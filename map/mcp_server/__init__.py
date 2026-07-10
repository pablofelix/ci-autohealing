"""RHOAI System Map MCP Server.

Exposes the Neo4j-backed infrastructure graph as MCP tools for Claude Code
and agents. Enables querying topology, searching nodes, finding paths,
detecting gaps, and checking live CI/CD status from conversations.

Usage:
    python -m map.mcp_server              # stdio (Claude Desktop)
"""

from fastmcp import FastMCP

mcp = FastMCP("RHOAI System Map")

import map.mcp_server.tools  # noqa: E402, F401
