"""Auto-seed the System Map from the live Konflux cluster via IC API.

All data comes through IC HTTP endpoints — no direct K8s access.
"""

import logging
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

import os
IC_BASE_URL = os.environ.get("IC_API_URL", "http://localhost:8000").rstrip("/") + "/api/v1"


class ClusterSeeder:
    """Populates Neo4j from live cluster state via IC API."""

    def __init__(self, driver, ic_url: str = IC_BASE_URL):
        self.driver = driver
        self.ic_url = ic_url.rstrip("/")
        self._session = requests.Session()
        self._session.timeout = 30

    def _api(self, path: str, params: dict | None = None) -> dict | list | None:
        """Call IC API endpoint. Returns None on failure."""
        try:
            resp = self._session.get(f"{self.ic_url}{path}", params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning("IC API %s failed: %s", path, e)
            return None

    def _merge_node(self, tx, label: str, data: dict):
        """MERGE a node by id, SET properties + metadata."""
        props = {k: v for k, v in data.items() if v is not None}
        tx.run(
            f"MERGE (n:{label} {{id: $id}}) "
            "SET n += $props, n._source = 'cluster', n._seeded_at = datetime()",
            id=data["id"], props=props,
        )

    def _merge_relationship(self, tx, from_label, from_id, rel_type, to_label, to_id, props=None):
        """MERGE a relationship between two nodes."""
        query = (
            f"MATCH (a:{from_label} {{id: $from_id}}) "
            f"MATCH (b:{to_label} {{id: $to_id}}) "
            f"MERGE (a)-[r:{rel_type}]->(b)"
        )
        if props:
            query += " SET r += $props"
        params = {"from_id": from_id, "to_id": to_id}
        if props:
            params["props"] = props
        tx.run(query, **params)

    def check_ic_health(self) -> bool:
        """Verify IC API is available."""
        try:
            base = self.ic_url.split("/api/v1")[0]
            resp = self._session.get(f"{base}/health", timeout=5)
            data = resp.json()
            return data.get("status") in ("ok", "healthy")
        except Exception:
            return False

    def seed_applications(self) -> list[str]:
        """Fetch applications from IC and create Application nodes."""
        data = self._api("/applications")
        if not data:
            logger.warning("No applications from IC API")
            return []

        apps = data if isinstance(data, list) else data.get("applications", [])
        seeded = []
        with self.driver.session() as s:
            for app in apps:
                app_id = f"app-{app['name']}" if isinstance(app, dict) else f"app-{app}"
                name = app["name"] if isinstance(app, dict) else app
                s.execute_write(
                    lambda tx, aid=app_id, n=name: self._merge_node(tx, "Application", {
                        "id": aid, "name": n, "description": f"Konflux Application: {n}",
                    })
                )
                seeded.append(app_id)

        logger.info("Seeded %d applications", len(seeded))
        return seeded

    def seed_components(self, application: str = "rhoai-v3-5") -> list[str]:
        """Fetch components from IC and create Component nodes + relationships."""
        data = self._api(f"/applications/{application}/health")
        if not data:
            logger.warning("No components for %s", application)
            return []

        components = data if isinstance(data, list) else data.get("components", [])
        seeded = []
        app_id = f"app-{application}"

        with self.driver.session() as s:
            for comp in components:
                if isinstance(comp, str):
                    comp = {"name": comp}
                name = comp.get("name") or comp.get("component_name", "")
                if not name:
                    continue
                comp_id = f"comp-{name}"
                node_data = {
                    "id": comp_id,
                    "name": name,
                    "repo": comp.get("repo", ""),
                    "branch": comp.get("branch", ""),
                    "container_image": comp.get("container_image", ""),
                }
                s.execute_write(lambda tx, d=node_data: self._merge_node(tx, "Component", d))
                s.execute_write(
                    lambda tx, cid=comp_id, aid=app_id: self._merge_relationship(
                        tx, "Application", aid, "CONTAINS", "Component", cid
                    )
                )
                seeded.append(comp_id)

        logger.info("Seeded %d components for %s", len(seeded), application)
        return seeded

    def seed_nudge_chains(self, component_ids: list[str]) -> int:
        """Infer NUDGES relationships from naming convention.

        Pattern: comp-X -> comp-X-bundle -> comp-*-fbc-fragment
        """
        id_set = set(component_ids)
        nudge_count = 0

        with self.driver.session() as s:
            for cid in component_ids:
                bundle_id = f"{cid}-bundle"
                if bundle_id in id_set:
                    s.execute_write(
                        lambda tx, f=cid, t=bundle_id: self._merge_relationship(
                            tx, "Component", f, "NUDGES", "Component", t
                        )
                    )
                    nudge_count += 1

            # FBC fragments: any component ending in -fbc-fragment gets nudged by bundles
            fbc_ids = [c for c in component_ids if c.endswith("-fbc-fragment")]
            bundle_ids = [c for c in component_ids if c.endswith("-bundle")]
            for fbc_id in fbc_ids:
                for bundle_id in bundle_ids:
                    s.execute_write(
                        lambda tx, f=bundle_id, t=fbc_id: self._merge_relationship(
                            tx, "Component", f, "NUDGES", "Component", t
                        )
                    )
                    nudge_count += 1

        logger.info("Created %d NUDGES relationships", nudge_count)
        return nudge_count

    def detect_drift(self, application: str = "rhoai-v3-5") -> dict:
        """Compare current graph against cluster state to find drift.

        Returns nodes that exist in graph but not cluster (stale), and
        nodes in cluster but not graph (missing).
        """
        cluster_components = self._api(f"/applications/{application}/health")
        if cluster_components is None:
            return {"error": "Could not reach IC API", "stale": [], "missing": []}

        if isinstance(cluster_components, dict):
            cluster_components = cluster_components.get("components", [])

        cluster_ids = set()
        for c in cluster_components:
            name = (c.get("name") or c.get("component_name", "")) if isinstance(c, dict) else c
            cluster_ids.add(f"comp-{name}")

        with self.driver.session() as s:
            result = s.run("MATCH (c:Component) RETURN c.id AS id")
            graph_ids = {r["id"] for r in result}

        stale = sorted(graph_ids - cluster_ids)
        missing = sorted(cluster_ids - graph_ids)

        return {
            "graph_count": len(graph_ids),
            "cluster_count": len(cluster_ids),
            "in_sync": len(graph_ids & cluster_ids),
            "stale": stale,
            "missing": missing,
            "drift_detected": bool(stale or missing),
        }

    def seed_all(self, application: str = "rhoai-v3-5") -> dict:
        """Run full auto-seed: applications, components, nudges."""
        if not self.check_ic_health():
            return {"error": "IC API not available", "seeded": False}

        apps = self.seed_applications()
        comps = self.seed_components(application)
        nudges = self.seed_nudge_chains(comps)

        return {
            "seeded": True,
            "applications": len(apps),
            "components": len(comps),
            "nudge_relationships": nudges,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
