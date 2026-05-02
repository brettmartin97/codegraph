FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CODEGRAPH_HOST=0.0.0.0 \
    CODEGRAPH_PORT=8811

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY docs ./docs

RUN pip install --no-cache-dir -e '.[full]'

EXPOSE 8811

# Default: REST API (for docker-compose / browser access)
# Override CMD with: python -m codegraph_mcp.server.mcp_server  for stdio MCP
CMD ["uvicorn", "codegraph_mcp.server.rest_api:api", "--host", "0.0.0.0", "--port", "8811"]
