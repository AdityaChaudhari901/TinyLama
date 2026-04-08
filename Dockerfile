# ============================================================
# Stage 1: Build Frontend
# ============================================================
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend

COPY Frontend/package*.json ./
RUN npm install --legacy-peer-deps

COPY Frontend/ ./
ENV VITE_API_URL=""
RUN npm run build

# ============================================================
# Stage 2: Runtime
# ollama/ollama:latest (Ubuntu 22.04) already has the ollama binary.
# ============================================================
FROM ollama/ollama:latest
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        python3 \
        python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies
COPY Backend/requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# Application code
COPY Backend/app.py .

# Built React frontend served by FastAPI as static files
COPY --from=frontend-builder /app/frontend/dist ./dist

# Runtime environment defaults (all overridable via boltic.yaml env block)
ENV PORT=8080 \
    MODEL=gemma3:4b \
    OLLAMA_HOST=127.0.0.1:11434 \
    OLLAMA_MODELS=/app/.ollama/models \
    PYTHONUNBUFFERED=1

EXPOSE 8080

# Pull the Ollama model into the image at build time so instances do not
# download it again during startup.
RUN bash -lc 'set -euo pipefail; \
    mkdir -p "$OLLAMA_MODELS"; \
    ollama serve >/tmp/ollama-build.log 2>&1 & \
    OLLAMA_PID=$!; \
    cleanup() { \
        kill "$OLLAMA_PID" >/dev/null 2>&1 || true; \
        wait "$OLLAMA_PID" >/dev/null 2>&1 || true; \
    }; \
    trap cleanup EXIT; \
    for i in $(seq 1 60); do \
        if curl -sf http://127.0.0.1:11434/api/tags >/dev/null; then \
            break; \
        fi; \
        sleep 2; \
    done; \
    curl -sf http://127.0.0.1:11434/api/tags >/dev/null; \
    ollama pull "$MODEL"'

HEALTHCHECK --interval=30s --timeout=15s --start-period=300s --retries=5 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

COPY start.sh .
RUN chmod +x start.sh

# Override the default ollama ENTRYPOINT so our script runs directly
ENTRYPOINT []
CMD ["./start.sh"]
