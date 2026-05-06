"""Entry point for python -m konflux_mcp."""

from .server import mcp

if __name__ == "__main__":
    mcp.run()
