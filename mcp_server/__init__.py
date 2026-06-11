"""SLM-Forge MCP server package.

Exposes the local SLM-Forge fine-tuning lab's capabilities to MCP clients
(Claude Desktop, Cursor, Claude Code CLI) by proxying through the existing
FastAPI surface at http://localhost:8000.

Entry points:
    python -m mcp_server                       # stdio transport (Claude Desktop)
    python -m mcp_server --http --port 8765    # SSE/HTTP transport (web)
"""
from __future__ import annotations

__all__ = ["__version__"]
__version__ = "0.1.0"
