"""System Map server — standalone FastAPI app.

Run with: uvicorn map.backend.main:app --port 8080
Or:       python -m map.backend.main
"""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import graph
from .routes import router

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(levelname)s: %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app):
    yield
    graph.close()


app = FastAPI(
    title="RHOAI System Map",
    description="Interactive visual map of RHOAI CI/CD infrastructure",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/api/map/health")
def health():
    try:
        stats = graph.get_stats()
        total_nodes = sum(s["count"] for s in stats["nodes"])
        return {"status": "ok", "neo4j": "connected", "total_nodes": total_nodes}
    except Exception as e:
        return {"status": "degraded", "neo4j": "disconnected", "error": str(e)}


# Serve frontend static files if built
frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")


def run():
    import uvicorn
    port = int(os.environ.get("MAP_PORT", "8080"))
    uvicorn.run("map.backend.main:app", host="0.0.0.0", port=port, reload=True)


if __name__ == "__main__":
    run()
