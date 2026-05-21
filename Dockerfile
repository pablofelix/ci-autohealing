FROM python:3.11-slim AS builder

WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends \
        libkrb5-dev gcc && \
    rm -rf /var/lib/apt/lists/*
COPY src/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /install /usr/local
COPY src/ .
COPY db/ /app/db/

RUN useradd --uid 1001 --no-create-home appuser
USER 1001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

ENTRYPOINT ["python", "-m", "serve", "--api", "--mcp-sse", "--host", "0.0.0.0", "--port", "8000"]
