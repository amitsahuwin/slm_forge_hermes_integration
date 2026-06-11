"""Module entry point for the SLM-Forge MCP server.

Examples:
    # stdio (Claude Desktop, Claude Code CLI defaults)
    python -m mcp_server

    # SSE/HTTP (Cursor, web clients, Docker)
    python -m mcp_server --http --port 8765

Environment overrides:
    SLM_FORGE_API_URL   Base URL of the API (default http://localhost:8000)
    SLM_FORGE_MCP_PORT  Default port for --http if not given on the CLI
    SLM_FORGE_MCP_HOST  Default host for --http (default 0.0.0.0)
"""
from __future__ import annotations

import argparse
import os
import sys

from mcp_server.server import run_http, run_stdio


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m mcp_server",
        description="SLM-Forge MCP server (stdio or SSE/HTTP).",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Run the SSE/HTTP transport instead of stdio.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("SLM_FORGE_MCP_PORT", "8765")),
        help="Port for the SSE/HTTP transport (default 8765).",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=os.environ.get("SLM_FORGE_MCP_HOST", "0.0.0.0"),
        help="Host for the SSE/HTTP transport (default 0.0.0.0).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.http:
        run_http(port=args.port, host=args.host)
    else:
        run_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
