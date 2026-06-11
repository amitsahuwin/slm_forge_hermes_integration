# SLM-Forge MCP server — standalone container.
#
# Runs the SSE/HTTP variant of the MCP server alongside the API container so
# web clients (Cursor over HTTP, or the in-browser Claude inspector) can talk
# to it. Claude Desktop / Claude Code stdio installs do NOT use this image —
# they spawn `python -m mcp_server` directly on the host.

FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir uv

# Lock file is required — `make ensure-lock` keeps it in sync with pyproject.
COPY pyproject.toml uv.lock /app/

# Install only the MCP extra plus the runtime deps used by the proxy
# (httpx, pydantic). No trainer/MLX/torch needed — this image is a thin
# HTTP shim over the API.
RUN uv sync --extra mcp --no-install-project

COPY mcp_server /app/mcp_server

ENV PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SLM_FORGE_API_URL=http://slm-forge-api:8000 \
    SLM_FORGE_MCP_HOST=0.0.0.0 \
    SLM_FORGE_MCP_PORT=8765

EXPOSE 8765

CMD ["uv", "run", "python", "-m", "mcp_server", "--http", "--port", "8765"]
