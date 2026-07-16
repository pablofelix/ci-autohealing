"""Component rebuild endpoint — triggers a fresh Konflux build."""

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from shared_config import NAMESPACE

logger = logging.getLogger(__name__)
router = APIRouter(tags=["rebuilds"])


class RebuildRequest(BaseModel):
    namespace: str = Field(default="", description="K8s namespace override")


class BatchRebuildRequest(BaseModel):
    components: List[str] = Field(..., description="Component names to rebuild")
    namespace: str = Field(default="", description="K8s namespace override")
    triage_group: Optional[int] = Field(
        default=None, description="Triage group ID — rebuilds all components in the group")


@router.post("/applications/{application}/rebuild/{component}")
def trigger_rebuild(application: str, component: str, req: RebuildRequest | None = None):
    """Trigger a fresh build for a component by annotating the Component CR."""
    from clients.kubernetes import KubernetesClient

    ns = (req.namespace if req and req.namespace else None) or NAMESPACE
    if not ns:
        raise HTTPException(
            status_code=400,
            detail="No namespace configured. Set NAMESPACE env var.",
        )

    try:
        k8s = KubernetesClient(namespace=ns)
        k8s.trigger_rebuild(component, namespace=ns)
        return {
            "status": "triggered",
            "component": component,
            "namespace": ns,
            "application": application,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"K8s API error: {exc}")


def _k8s_client(namespace=None):
    from clients.kubernetes import KubernetesClient
    return KubernetesClient(namespace=namespace or NAMESPACE)


def _resolve_triage_components(triage_group_id, application):
    """Look up component names from a triage group."""
    from repositories.connection import DatabaseConnection
    from repositories.triage_repository import TriageRepository
    db = DatabaseConnection.from_env()
    repo = TriageRepository(db)
    item = repo.get_item(triage_group_id)
    if not item:
        return []
    components = item.get('components', [])
    if isinstance(components, str):
        return [components]
    return list(components)


@router.post("/applications/{application}/rebuilds/batch")
def batch_rebuild(application: str, req: BatchRebuildRequest):
    """Trigger rebuilds for multiple components at once.

    Accepts a list of component names, or a triage_group ID to rebuild
    all components in that group. Returns per-component results.
    """
    ns = req.namespace or NAMESPACE
    if not ns:
        raise HTTPException(
            status_code=400,
            detail="No namespace configured. Set NAMESPACE env var.",
        )

    components = list(req.components) if req.components else []
    if req.triage_group and not components:
        components = _resolve_triage_components(req.triage_group, application)
        if not components:
            raise HTTPException(
                status_code=404,
                detail=f"Triage group {req.triage_group} not found or has no components",
            )

    if not components:
        raise HTTPException(status_code=400, detail="No components specified")

    k8s = _k8s_client(ns)
    results = []
    for comp in components:
        try:
            k8s.trigger_rebuild(comp, namespace=ns)
            results.append({
                'component': comp,
                'status': 'triggered',
            })
            logger.info("Batch rebuild triggered for %s", comp)
        except Exception as exc:
            results.append({
                'component': comp,
                'status': 'failed',
                'error': str(exc),
            })
            logger.warning("Batch rebuild failed for %s: %s", comp, exc)

    triggered = sum(1 for r in results if r['status'] == 'triggered')
    failed = sum(1 for r in results if r['status'] == 'failed')
    return {
        'application': application,
        'namespace': ns,
        'total': len(results),
        'triggered': triggered,
        'failed': failed,
        'results': results,
    }
