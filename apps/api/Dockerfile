FROM python:3.12-slim

WORKDIR /app

# uv for fast Python dep management
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir uv

# Lock file is required — make ensure-lock guarantees it exists before build
COPY pyproject.toml uv.lock /app/

# Install dependencies (no trainer extra — MLX is macOS-only; trainer runs on host)
# `chat` extra brings in langgraph + langchain-ollama so /api/v1/chat works.
# `research` extra brings in ddgs so R&D web grounding works out of the box.
RUN uv sync --extra ingest --extra chat --extra research --no-install-project

# Copy source
COPY apps /app/apps
COPY packages /app/packages

ENV PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
