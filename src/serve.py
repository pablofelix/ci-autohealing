"""Unified server entry point.

Usage:
    python -m serve --mcp          # MCP stdio (Claude Desktop)
    python -m serve --mcp-sse      # MCP SSE (remote agents)
    python -m serve --api           # REST API on port 8000
    python -m serve --api --mcp-sse # Both in same process
"""

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="Konflux CI Monitoring Server")
    parser.add_argument("--mcp", action="store_true", help="MCP server (stdio transport)")
    parser.add_argument("--mcp-sse", action="store_true", help="MCP server (SSE transport)")
    parser.add_argument("--api", action="store_true", help="REST API server")
    parser.add_argument("--port", type=int, default=8000, help="API port (default: 8000)")
    parser.add_argument("--host", default="0.0.0.0", help="API host (default: 0.0.0.0)")
    args = parser.parse_args()

    if not any([args.mcp, args.mcp_sse, args.api]):
        parser.print_help()
        sys.exit(1)

    if args.mcp:
        from mcp_server import mcp
        mcp.run(transport="stdio")
        return

    if args.mcp_sse and not args.api:
        from mcp_server import mcp
        mcp.run(transport="sse")
        return

    if args.api and not args.mcp_sse:
        import uvicorn

        from api import create_app
        app = create_app()
        uvicorn.run(app, host=args.host, port=args.port)
        return

    if args.api and args.mcp_sse:
        import uvicorn

        from api import create_app
        from mcp_server import mcp

        app = create_app()
        mcp_app = mcp.http_app(transport="sse")
        app.mount("/mcp", mcp_app)

        uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
