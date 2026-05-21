"""Konflux CI Monitoring MCP Server.

Usage:
    python -m mcp_server              # stdio (Claude Desktop)
    python -m mcp_server --sse        # SSE (remote agents)
"""

from fastmcp import FastMCP

mcp = FastMCP("Konflux CI Monitoring")

_db_initialized = False


def _ensure_db():
    global _db_initialized
    if not _db_initialized:
        from config import CollectorConfig
        from repositories.repository_factory import init_pool
        cfg = CollectorConfig.from_env()
        init_pool(cfg.db, pool_size=10)
        _db_initialized = True


_ensure_db()

import mcp_server.tools  # noqa: E402, F401
