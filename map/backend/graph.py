"""Neo4j connection and Cypher queries for the system map.

Standalone — no IC imports. All graph operations go through this module.
"""

import logging
import os
import threading
from contextlib import contextmanager

logger = logging.getLogger(__name__)

_driver = None
_driver_lock = threading.Lock()


def get_driver():
    global _driver
    if _driver is not None:
        return _driver
    with _driver_lock:
        if _driver is not None:
            return _driver
        from neo4j import GraphDatabase
        uri = os.environ.get("SLK_NEO4J_URI", "bolt://localhost:7687")
        user = os.environ.get("SLK_NEO4J_USER", "neo4j")
        password = os.environ.get("SLK_NEO4J_PASSWORD", "")
        if not password:
            raise RuntimeError("SLK_NEO4J_PASSWORD not set")
        _driver = GraphDatabase.driver(uri, auth=(user, password))
        _driver.verify_connectivity()
        logger.info("Connected to Neo4j at %s", uri)
        return _driver


def close():
    global _driver
    if _driver:
        _driver.close()
        _driver = None


@contextmanager
def session():
    driver = get_driver()
    s = driver.session()
    try:
        yield s
    finally:
        s.close()


# ─── Queries ───────────────────────────────────────────────────────────────────

def get_all_nodes():
    """Return all nodes with labels and properties for React Flow."""
    with session() as s:
        result = s.run(
            "MATCH (n) "
            "WHERE NOT n:_Schema "
            "RETURN n.id AS id, labels(n)[0] AS type, properties(n) AS props "
            "ORDER BY labels(n)[0], n.id"
        )
        return [dict(r) for r in result]


def get_all_edges():
    """Return all relationships for React Flow."""
    with session() as s:
        result = s.run(
            "MATCH (a)-[r]->(b) "
            "RETURN a.id AS source, b.id AS target, type(r) AS label, "
            "properties(r) AS props"
        )
        return [dict(r) for r in result]


def get_node_detail(node_id: str):
    """Return a single node with its neighbors."""
    with session() as s:
        node_result = s.run(
            "MATCH (n {id: $id}) "
            "RETURN n.id AS id, labels(n)[0] AS type, properties(n) AS props",
            id=node_id,
        )
        node = node_result.single()
        if not node:
            return None

        neighbors_result = s.run(
            "MATCH (n {id: $id})-[r]-(m) "
            "RETURN m.id AS id, labels(m)[0] AS type, m.name AS name, "
            "type(r) AS relationship, "
            "CASE WHEN startNode(r) = n THEN 'outgoing' ELSE 'incoming' END AS direction",
            id=node_id,
        )
        neighbors = [dict(r) for r in neighbors_result]

        return {
            **dict(node),
            "neighbors": neighbors,
        }


def search_nodes(query: str, node_type: str | None = None):
    """Full-text search across node names, descriptions, IDs."""
    pattern = f"(?i).*{query}.*"
    with session() as s:
        if node_type:
            result = s.run(
                "MATCH (n) "
                "WHERE (n.name =~ $pattern OR n.id =~ $pattern OR n.description =~ $pattern) "
                "AND labels(n)[0] = $node_type "
                "RETURN n.id AS id, labels(n)[0] AS type, n.name AS name, "
                "n.description AS description "
                "ORDER BY CASE WHEN n.name =~ $pattern THEN 0 ELSE 1 END, n.name "
                "LIMIT 50",
                pattern=pattern,
                node_type=node_type,
            )
        else:
            result = s.run(
                "MATCH (n) "
                "WHERE (n.name =~ $pattern OR n.id =~ $pattern OR n.description =~ $pattern) "
                "RETURN n.id AS id, labels(n)[0] AS type, n.name AS name, "
                "n.description AS description "
                "ORDER BY CASE WHEN n.name =~ $pattern THEN 0 ELSE 1 END, n.name "
                "LIMIT 50",
                pattern=pattern,
            )
        return [dict(r) for r in result]


def find_path(from_id: str, to_id: str):
    """Find shortest path between two nodes."""
    with session() as s:
        result = s.run(
            "MATCH p = shortestPath((a {id: $from_id})-[*..10]-(b {id: $to_id})) "
            "RETURN [n IN nodes(p) | {id: n.id, type: labels(n)[0], name: n.name}] AS nodes, "
            "[r IN relationships(p) | {source: startNode(r).id, target: endNode(r).id, "
            "label: type(r)}] AS edges",
            from_id=from_id, to_id=to_id,
        )
        rec = result.single()
        if not rec:
            return None
        return {"nodes": rec["nodes"], "edges": rec["edges"]}


def get_stats():
    """Return node and edge counts by type."""
    with session() as s:
        nodes_result = s.run(
            "MATCH (n) RETURN labels(n)[0] AS type, count(n) AS count ORDER BY count DESC"
        )
        edges_result = s.run(
            "MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS count ORDER BY count DESC"
        )
        return {
            "nodes": [dict(r) for r in nodes_result],
            "edges": [dict(r) for r in edges_result],
        }


def get_gaps():
    """Detect infrastructure gaps and limitations.

    Returns a list of issues found in the graph topology.
    """
    gaps = []
    with session() as s:
        # Components without build pipeline (only flag static-topology nodes)
        result = s.run(
            "MATCH (c:Component) WHERE NOT (c)-[:BUILDS_WITH]->(:Pipeline) "
            "AND c._source = 'seed' "
            "RETURN c.id AS id, c.name AS name"
        )
        for r in result:
            gaps.append({
                "node_id": r["id"],
                "node_name": r["name"],
                "type": "missing_pipeline",
                "severity": "warning",
                "message": "Component has no build pipeline linked",
            })

        # Components not in any application
        result = s.run(
            "MATCH (c:Component) WHERE NOT (c)<-[:CONTAINS]-(:Application) "
            "RETURN c.id AS id, c.name AS name"
        )
        for r in result:
            gaps.append({
                "node_id": r["id"],
                "node_name": r["name"],
                "type": "orphan_component",
                "severity": "error",
                "message": "Component not linked to any Application",
            })

        # Applications without EC policy validation
        result = s.run(
            "MATCH (a:Application) WHERE NOT (:ECPolicy)-[:VALIDATES]->(a) "
            "RETURN a.id AS id, a.name AS name"
        )
        for r in result:
            gaps.append({
                "node_id": r["id"],
                "node_name": r["name"],
                "type": "no_ec_policy",
                "severity": "warning",
                "message": "Application has no EC policy validating it",
            })

        # EC policies without stage+prod pair
        result = s.run(
            "MATCH (p:ECPolicy) "
            "WITH p.scope AS scope, collect(p.environment) AS envs "
            "WHERE size(envs) < 2 "
            "RETURN scope, envs"
        )
        for r in result:
            gaps.append({
                "node_id": f"scope-{r['scope']}",
                "node_name": r["scope"],
                "type": "incomplete_policy_pair",
                "severity": "warning",
                "message": f"EC policy scope '{r['scope']}' only has environments: {r['envs']} (expected stage + prod)",
            })

        # Workflows not linked to any repository
        result = s.run(
            "MATCH (w:Workflow) WHERE NOT (w)<-[:CONTAINS]-(:Repository) "
            "RETURN w.id AS id, w.name AS name"
        )
        for r in result:
            gaps.append({
                "node_id": r["id"],
                "node_name": r["name"],
                "type": "orphan_workflow",
                "severity": "warning",
                "message": "Workflow not linked to any Repository",
            })

    return gaps


INTERNAL_PROPS = {"_source", "_seeded_at"}


def build_gap_map(gaps: list[dict]) -> dict[str, list]:
    """Group gaps by node_id for efficient lookup."""
    gap_map: dict[str, list] = {}
    for g in gaps:
        gap_map.setdefault(g["node_id"], []).append(g)
    return gap_map


def filter_props(props: dict) -> dict:
    """Remove internal metadata properties from a node's properties."""
    return {k: v for k, v in props.items() if k not in INTERNAL_PROPS}


def get_impact(node_id: str, max_depth: int = 5, direction: str = "downstream"):
    """Trace impact from a node through the graph.

    Args:
        node_id: Starting node ID.
        max_depth: Maximum hops to trace (1-10, default 5).
        direction: 'downstream' follows outgoing edges, 'upstream' follows incoming.
    """
    max_depth = max(1, min(max_depth, 10))

    max_results = 500

    if direction == "upstream":
        cypher = (
            "MATCH (start {id: $id}) "
            "MATCH path = (affected)-[r*1.." + str(max_depth) + "]->(start) "
            "WITH affected, nodes(path) AS path_nodes, relationships(path) AS path_rels, length(path) AS depth "
            "RETURN DISTINCT affected.id AS id, labels(affected)[0] AS type, "
            "affected.name AS name, depth, "
            "[r IN path_rels | type(r)] AS via_relationships "
            "ORDER BY depth, type, name "
            "LIMIT " + str(max_results)
        )
    else:
        cypher = (
            "MATCH (start {id: $id}) "
            "MATCH path = (start)-[r*1.." + str(max_depth) + "]->(affected) "
            "WITH affected, nodes(path) AS path_nodes, relationships(path) AS path_rels, length(path) AS depth "
            "RETURN DISTINCT affected.id AS id, labels(affected)[0] AS type, "
            "affected.name AS name, depth, "
            "[r IN path_rels | type(r)] AS via_relationships "
            "ORDER BY depth, type, name "
            "LIMIT " + str(max_results)
        )

    with session() as s:
        # Check start node exists
        check = s.run("MATCH (n {id: $id}) RETURN n.id AS id", id=node_id)
        if not check.single():
            return None

        result = s.run(cypher, id=node_id)
        records = [dict(r) for r in result]

    # Group by depth
    by_depth = {}
    for r in records:
        d = r["depth"]
        by_depth.setdefault(d, []).append({
            "id": r["id"],
            "type": r["type"],
            "name": r["name"],
            "via": r["via_relationships"],
        })

    # Group by type
    by_type = {}
    for r in records:
        by_type.setdefault(r["type"], []).append(r["id"])

    return {
        "source": node_id,
        "direction": direction,
        "max_depth": max_depth,
        "total_affected": len(records),
        "by_depth": by_depth,
        "by_type": {k: len(v) for k, v in by_type.items()},
        "affected": [{"id": r["id"], "type": r["type"], "name": r["name"], "depth": r["depth"]} for r in records],
    }
