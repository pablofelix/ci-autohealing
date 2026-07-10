"""MCP tools for the RHOAI System Map.

All tools query the Neo4j graph via map.backend.graph. The live-status tool
also fetches real-time CI/CD data from the IC API via LiveStatusService.
"""

import asyncio
import logging
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, TypeVar

from map.mcp_server import mcp

logger = logging.getLogger(__name__)

T = TypeVar("T")


def async_tool(sync_func: Callable[..., T]) -> Callable[..., "asyncio.Future[T]"]:
    @wraps(sync_func)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: sync_func(*args, **kwargs))
    return wrapper


def _graph():
    from map.backend import graph
    return graph


# ---------------------------------------------------------------------------
# Graph query tools
# ---------------------------------------------------------------------------


@mcp.tool()
@async_tool
def get_map_graph() -> Dict:
    """Get the full RHOAI infrastructure topology (nodes + edges).

    Returns all nodes and relationships in the System Map graph. Each node
    includes its type (Component, Application, Pipeline, etc.), properties,
    and any detected infrastructure gaps.

    Use this for a complete picture of the CI/CD infrastructure. For targeted
    lookups, prefer search_map_nodes or get_map_node instead.
    """
    g = _graph()
    raw_nodes = g.get_all_nodes()
    raw_edges = g.get_all_edges()
    gaps = g.get_gaps()

    gap_map: dict[str, list] = {}
    for gap in gaps:
        gap_map.setdefault(gap["node_id"], []).append(gap)

    nodes = []
    for n in raw_nodes:
        props = n.get("props", {})
        node_gaps = gap_map.get(n["id"], [])
        nodes.append({
            "id": n["id"],
            "type": n["type"],
            "name": props.get("name", n["id"]),
            "description": props.get("description", ""),
            "gaps": node_gaps,
            "properties": {
                k: v for k, v in props.items()
                if k not in ("id", "name", "description", "_source", "_seeded_at")
            },
        })

    edges = [
        {"source": e["source"], "target": e["target"], "label": e["label"]}
        for e in raw_edges
    ]

    return {
        "nodes": nodes,
        "edges": edges,
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "total_gaps": sum(len(n["gaps"]) for n in nodes),
    }


@mcp.tool()
@async_tool
def search_map_nodes(
    query: str,
    type: Optional[str] = None,
) -> Dict:
    """Search infrastructure nodes by name, description, or ID.

    Args:
        query: Search text (matched against node name, description, and ID).
        type: Optional filter by node type. Valid types: Application, Component,
              Repository, Pipeline, TektonTask, Workflow, Automation, ECPolicy.

    Returns matching nodes with their type, name, and description.
    """
    results = _graph().search_nodes(query, node_type=type)
    return {"results": results, "count": len(results), "query": query}


@mcp.tool()
@async_tool
def get_map_node(node_id: str) -> Dict:
    """Get detailed info about a specific infrastructure node.

    Args:
        node_id: Node identifier (e.g., 'comp-odh-dashboard', 'app-rhoai-v3-5',
                 'pipeline-docker-build'). Use search_map_nodes to find IDs.

    Returns the node's properties, all neighbors (connected nodes), and any
    infrastructure gaps detected for this node.
    """
    g = _graph()
    detail = g.get_node_detail(node_id)
    if not detail:
        return {"error": f"Node '{node_id}' not found"}

    internal = {"_source", "_seeded_at"}
    if "props" in detail:
        detail["props"] = {k: v for k, v in detail["props"].items() if k not in internal}

    gaps = [gap for gap in g.get_gaps() if gap["node_id"] == node_id]
    return {**detail, "gaps": gaps}


@mcp.tool()
@async_tool
def find_map_path(from_id: str, to_id: str) -> Dict:
    """Find the shortest path between two infrastructure nodes.

    Args:
        from_id: Source node ID (e.g., 'comp-odh-dashboard').
        to_id: Target node ID (e.g., 'pipeline-docker-build').

    Returns the sequence of nodes and edges forming the shortest path.
    Useful for understanding how components relate to pipelines, policies,
    and automation systems.
    """
    path = _graph().find_path(from_id, to_id)
    if not path:
        return {"error": f"No path found between '{from_id}' and '{to_id}'"}
    return path


@mcp.tool()
@async_tool
def get_map_gaps() -> Dict:
    """Detect infrastructure gaps and misconfigurations in the graph.

    Checks for:
    - Components without a build pipeline
    - Components not linked to any Application
    - Applications without EC policy validation
    - EC policies missing stage or prod environment
    - Workflows not linked to any Repository

    Returns issues sorted by severity with suggested fixes.
    """
    gaps = _graph().get_gaps()
    by_type: dict[str, list] = {}
    for g in gaps:
        by_type.setdefault(g["type"], []).append(g)
    return {
        "gaps": gaps,
        "count": len(gaps),
        "by_type": {k: len(v) for k, v in by_type.items()},
    }


@mcp.tool()
@async_tool
def get_map_stats() -> Dict:
    """Get node and edge counts by type in the infrastructure graph.

    Returns a summary of how many nodes exist per type (Component, Pipeline,
    etc.) and how many relationships exist per type (CONTAINS, BUILDS_WITH,
    etc.). Useful for understanding the graph's scope and coverage.
    """
    return _graph().get_stats()


@mcp.tool()
@async_tool
def get_map_live_status(application: str = "rhoai-v3-5") -> Dict:
    """Get live CI/CD status for components in the System Map.

    Fetches real-time data from the IC API:
    - Per-component health scores (healthy/degraded/failing)
    - Build failure alerts
    - Blocking conforma violations
    - Application-level release readiness verdict

    Args:
        application: RHOAI version to query (default: rhoai-v3-5).

    Returns node statuses with colors, an activity feed of recent events,
    and whether the IC API is currently reachable.
    """
    from map.backend.live_status_service import LiveStatusService
    try:
        service = LiveStatusService()
        return service.get_live_status(application)
    except Exception as exc:
        logger.warning("Live status unavailable: %s", exc)
        return {
            "application": application,
            "nodes": [],
            "activity": [],
            "ic_available": False,
            "last_updated": None,
        }
