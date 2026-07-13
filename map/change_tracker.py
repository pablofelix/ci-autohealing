"""Track changes to the System Map graph over time.

Stores change events in PostgreSQL for temporal queries.
Compares Neo4j graph snapshots to detect additions, removals, and modifications.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class GraphChange:
    """A single change event in the graph."""

    def __init__(self, change_type: str, entity_type: str, entity_id: str,
                 entity_name: str = "", details: dict | None = None):
        self.change_type = change_type  # added, removed, modified
        self.entity_type = entity_type  # node or relationship
        self.entity_id = entity_id
        self.entity_name = entity_name
        self.details = details or {}
        self.timestamp = datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        return {
            "change_type": self.change_type,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "entity_name": self.entity_name,
            "details": self.details,
            "timestamp": self.timestamp.isoformat(),
        }


class ChangeTracker:
    """Detects and records graph changes by comparing snapshots."""

    def __init__(self, neo4j_driver, pg_conn_factory=None):
        """
        Args:
            neo4j_driver: Neo4j driver for reading current graph state.
            pg_conn_factory: Callable returning a psycopg2 connection.
                If None, changes are tracked in memory only.
        """
        self.neo4j_driver = neo4j_driver
        self.pg_conn_factory = pg_conn_factory
        self._memory_changes: list[dict] = []

    def ensure_table(self):
        """Create the graph_changes table if it doesn't exist."""
        if not self.pg_conn_factory:
            return

        conn = self.pg_conn_factory()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS graph_changes (
                        id SERIAL PRIMARY KEY,
                        timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        change_type VARCHAR(20) NOT NULL,
                        entity_type VARCHAR(20) NOT NULL,
                        entity_id VARCHAR(255) NOT NULL,
                        entity_name VARCHAR(255) DEFAULT '',
                        node_label VARCHAR(100) DEFAULT '',
                        details JSONB DEFAULT '{}',
                        snapshot_id VARCHAR(100) DEFAULT ''
                    );
                    CREATE INDEX IF NOT EXISTS idx_graph_changes_timestamp
                        ON graph_changes (timestamp DESC);
                    CREATE INDEX IF NOT EXISTS idx_graph_changes_entity
                        ON graph_changes (entity_id);
                    CREATE INDEX IF NOT EXISTS idx_graph_changes_type
                        ON graph_changes (change_type);
                """)
            conn.commit()
        finally:
            conn.close()

    def _take_snapshot(self) -> dict:
        """Take a snapshot of the current Neo4j graph state.

        Returns dict with nodes (dict of node ID -> info) and
        edges (set of (source, rel_type, target) tuples).
        """
        nodes = {}
        edges = set()

        with self.neo4j_driver.session() as s:
            result = s.run(
                "MATCH (n) WHERE NOT n:_Schema "
                "RETURN n.id AS id, labels(n)[0] AS label, n.name AS name, "
                "properties(n) AS props"
            )
            for r in result:
                props = dict(r["props"])
                # Remove metadata that changes every seed
                props.pop("_seeded_at", None)
                props.pop("_source", None)
                nodes[r["id"]] = {
                    "label": r["label"],
                    "name": r["name"] or r["id"],
                    "props": props,
                }

            result = s.run(
                "MATCH (a)-[r]->(b) "
                "RETURN a.id AS source, type(r) AS rel_type, b.id AS target"
            )
            for r in result:
                edges.add((r["source"], r["rel_type"], r["target"]))

        return {"nodes": nodes, "edges": edges}

    def diff_snapshots(self, old: dict, new: dict) -> list[GraphChange]:
        """Compare two snapshots and return list of changes."""
        changes = []

        old_ids = set(old["nodes"].keys())
        new_ids = set(new["nodes"].keys())

        # Added nodes
        for nid in sorted(new_ids - old_ids):
            node = new["nodes"][nid]
            changes.append(GraphChange(
                change_type="added",
                entity_type="node",
                entity_id=nid,
                entity_name=node["name"],
                details={"label": node["label"]},
            ))

        # Removed nodes
        for nid in sorted(old_ids - new_ids):
            node = old["nodes"][nid]
            changes.append(GraphChange(
                change_type="removed",
                entity_type="node",
                entity_id=nid,
                entity_name=node["name"],
                details={"label": node["label"]},
            ))

        # Modified nodes (property changes)
        for nid in sorted(old_ids & new_ids):
            old_props = old["nodes"][nid]["props"]
            new_props = new["nodes"][nid]["props"]
            if old_props != new_props:
                changed_keys = {
                    k for k in set(old_props) | set(new_props)
                    if old_props.get(k) != new_props.get(k)
                }
                changes.append(GraphChange(
                    change_type="modified",
                    entity_type="node",
                    entity_id=nid,
                    entity_name=new["nodes"][nid]["name"],
                    details={"changed_properties": sorted(changed_keys)},
                ))

        old_edges = old["edges"]
        new_edges = new["edges"]

        # Added edges
        for edge in sorted(new_edges - old_edges):
            changes.append(GraphChange(
                change_type="added",
                entity_type="relationship",
                entity_id=f"{edge[0]}-[{edge[1]}]->{edge[2]}",
                entity_name=edge[1],
                details={"source": edge[0], "rel_type": edge[1], "target": edge[2]},
            ))

        # Removed edges
        for edge in sorted(old_edges - new_edges):
            changes.append(GraphChange(
                change_type="removed",
                entity_type="relationship",
                entity_id=f"{edge[0]}-[{edge[1]}]->{edge[2]}",
                entity_name=edge[1],
                details={"source": edge[0], "rel_type": edge[1], "target": edge[2]},
            ))

        return changes

    def record_changes(self, changes: list[GraphChange], snapshot_id: str = ""):
        """Persist changes to PostgreSQL (or memory if no PG connection)."""
        if not changes:
            return

        if not self.pg_conn_factory:
            self._memory_changes.extend([c.to_dict() for c in changes])
            logger.info("Recorded %d changes in memory", len(changes))
            return

        conn = self.pg_conn_factory()
        try:
            with conn.cursor() as cur:
                for c in changes:
                    cur.execute(
                        "INSERT INTO graph_changes "
                        "(timestamp, change_type, entity_type, entity_id, entity_name, "
                        "node_label, details, snapshot_id) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)",
                        (
                            c.timestamp, c.change_type, c.entity_type,
                            c.entity_id, c.entity_name,
                            c.details.get("label", ""),
                            json.dumps(c.details),
                            snapshot_id,
                        ),
                    )
            conn.commit()
            logger.info("Recorded %d changes to PostgreSQL", len(changes))
        finally:
            conn.close()

    def get_changes(self, since: Optional[datetime] = None,
                    entity_id: Optional[str] = None,
                    change_type: Optional[str] = None,
                    limit: int = 100) -> list[dict]:
        """Query change history from PostgreSQL or memory."""
        if not self.pg_conn_factory:
            results = list(self._memory_changes)
            if since:
                results = [
                    c for c in results
                    if datetime.fromisoformat(c["timestamp"]) >= since
                ]
            if entity_id:
                results = [c for c in results if entity_id in c["entity_id"]]
            if change_type:
                results = [c for c in results if c["change_type"] == change_type]
            return results[-limit:]

        conn = self.pg_conn_factory()
        try:
            conditions = []
            params: list = []

            if since:
                conditions.append("timestamp >= %s")
                params.append(since)
            if entity_id:
                conditions.append("entity_id LIKE %s")
                params.append(f"%{entity_id}%")
            if change_type:
                conditions.append("change_type = %s")
                params.append(change_type)

            where = ""
            if conditions:
                where = "WHERE " + " AND ".join(conditions)

            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT id, timestamp, change_type, entity_type, entity_id, "
                    f"entity_name, node_label, details, snapshot_id "
                    f"FROM graph_changes {where} "
                    f"ORDER BY timestamp DESC LIMIT %s",
                    params + [limit],
                )
                columns = [d[0] for d in cur.description]
                rows = cur.fetchall()

            return [
                {
                    col: (val.isoformat() if isinstance(val, datetime) else val)
                    for col, val in zip(columns, row)
                }
                for row in rows
            ]
        finally:
            conn.close()

    def run_diff(self, previous_snapshot: dict | None = None,
                 snapshot_id: str = "") -> dict:
        """Take a new snapshot, diff against previous, record changes.

        If no previous snapshot is given, takes a snapshot and returns it
        (no diff on first run).
        """
        current = self._take_snapshot()

        if previous_snapshot is None:
            return {
                "first_run": True,
                "snapshot": current,
                "node_count": len(current["nodes"]),
                "edge_count": len(current["edges"]),
            }

        changes = self.diff_snapshots(previous_snapshot, current)
        self.record_changes(changes, snapshot_id=snapshot_id)

        return {
            "first_run": False,
            "snapshot": current,
            "changes": [c.to_dict() for c in changes],
            "summary": {
                "added": sum(1 for c in changes if c.change_type == "added"),
                "removed": sum(1 for c in changes if c.change_type == "removed"),
                "modified": sum(1 for c in changes if c.change_type == "modified"),
                "total": len(changes),
            },
        }
