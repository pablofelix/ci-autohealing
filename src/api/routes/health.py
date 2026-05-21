"""Health check endpoint (unauthenticated)."""

from fastapi import APIRouter

from repositories.repository_factory import get_pool

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check():
    """Database connectivity and pool status."""
    pool = get_pool()
    try:
        with pool.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        return {"status": "unhealthy", "database": str(e)}
