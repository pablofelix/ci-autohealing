"""MCP server entry point.

FastMCP server that exposes Konflux CI monitoring tools.
"""

from .tools import mcp

# Export the FastMCP instance
__all__ = ["mcp"]


if __name__ == "__main__":
    # Run the MCP server
    mcp.run()
