"""Konflux MCP Server.

Model Context Protocol server for Konflux CI monitoring.
Exposes tools for AI agents to query build failures and Conforma violations.
"""

from .server import mcp

__version__ = "0.1.0"
__all__ = ["mcp"]
