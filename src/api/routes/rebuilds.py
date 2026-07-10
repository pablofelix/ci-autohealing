"""Component rebuild endpoint — triggers a fresh Konflux build."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from shared_config import NAMESPACE

router = APIRouter(tags=["rebuilds"])


class RebuildRequest(BaseModel):
    namespace: str = Field(default="", description="K8s namespace override")


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
