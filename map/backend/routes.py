"""Map API routes — standalone, no IC imports (except optional live-status)."""

import logging

from fastapi import APIRouter, HTTPException, Query

from . import graph

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/map", tags=["map"])


@router.get("/nodes")
def list_nodes(type: str | None = Query(None, description="Filter by node type")):
    """All nodes in the graph, optionally filtered by type."""
    nodes = graph.get_all_nodes()
    if type:
        nodes = [n for n in nodes if n["type"] == type]
    return {"nodes": nodes, "count": len(nodes)}


@router.get("/edges")
def list_edges():
    """All relationships in the graph."""
    edges = graph.get_all_edges()
    return {"edges": edges, "count": len(edges)}


@router.get("/graph")
def full_graph():
    """Full graph (nodes + edges) in React Flow format."""
    raw_nodes = graph.get_all_nodes()
    raw_edges = graph.get_all_edges()
    gaps = graph.get_gaps()

    gap_map: dict[str, list] = {}
    for g in gaps:
        gap_map.setdefault(g["node_id"], []).append(g)

    rf_nodes = []
    for n in raw_nodes:
        props = n.get("props", {})
        node_gaps = gap_map.get(n["id"], [])
        rf_nodes.append({
            "id": n["id"],
            "type": n["type"],
            "data": {
                "label": props.get("name", n["id"]),
                "description": props.get("description", ""),
                "nodeType": n["type"],
                "gaps": node_gaps,
                "hasGaps": len(node_gaps) > 0,
                **{k: v for k, v in props.items() if k not in ("id", "name", "description", "_source", "_seeded_at")},
            },
            "position": {"x": 0, "y": 0},
        })

    rf_edges = []
    for e in raw_edges:
        rf_edges.append({
            "id": f"{e['source']}-{e['label']}-{e['target']}",
            "source": e["source"],
            "target": e["target"],
            "label": e["label"],
            "type": "smoothstep",
            "animated": e["label"] in ("NUDGES", "TRIGGERS"),
        })

    return {"nodes": rf_nodes, "edges": rf_edges}


INTERNAL_PROPS = {"_source", "_seeded_at"}


@router.get("/node/{node_id}")
def node_detail(node_id: str):
    """Detailed view of a single node with its neighbors."""
    detail = graph.get_node_detail(node_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")

    if "props" in detail:
        detail["props"] = {k: v for k, v in detail["props"].items() if k not in INTERNAL_PROPS}

    gaps = graph.get_gaps()
    node_gaps = [g for g in gaps if g["node_id"] == node_id]

    return {**detail, "gaps": node_gaps}


@router.get("/search")
def search(q: str = Query(..., min_length=1), type: str | None = Query(None)):
    """Search nodes by name, description, or ID."""
    results = graph.search_nodes(q, node_type=type)
    return {"results": results, "count": len(results), "query": q}


@router.get("/path/{from_id}/{to_id}")
def shortest_path(from_id: str, to_id: str):
    """Find shortest path between two nodes."""
    path = graph.find_path(from_id, to_id)
    if not path:
        raise HTTPException(
            status_code=404,
            detail=f"No path found between '{from_id}' and '{to_id}'",
        )
    return path


@router.get("/stats")
def stats():
    """Node and edge counts by type."""
    return graph.get_stats()


@router.get("/gaps")
def gaps():
    """Infrastructure gaps and limitations detected in the graph."""
    all_gaps = graph.get_gaps()
    by_type: dict[str, list] = {}
    for g in all_gaps:
        by_type.setdefault(g["type"], []).append(g)
    return {
        "gaps": all_gaps,
        "count": len(all_gaps),
        "by_type": {k: len(v) for k, v in by_type.items()},
    }


@router.get("/live-status")
def live_status(application: str = Query("rhoai-v3-5", description="Application name")):
    """Live CI/CD status overlay from IC API."""
    try:
        from .live_status_service import LiveStatusService
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
