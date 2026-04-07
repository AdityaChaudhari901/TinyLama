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
    MODEL=gemma3:27b \
    OLLAMA_HOST=127.0.0.1:11434 \
    PYTHONUNBUFFERED=1

EXPOSE 8080

# Pull model at build time so the container starts instantly with no cold pull.
# Requires the build environment to have sufficient disk (>20 GB).
# OLLAMA_NO_KEYGEN prevents SSH identity keys from being baked into the image.
RUN OLLAMA_NO_KEYGEN=true ollama serve & \
    pid=$! && \
    echo "Waiting for Ollama to be ready..." && \
    for i in $(seq 1 60); do \
        curl -s http://127.0.0.1:11434/api/tags > /dev/null 2>&1 && echo "Ollama ready after ${i}s" && break || sleep 1; \
    done && \
    ollama pull gemma3:27b && \
    kill $pid && \
    wait $pid 2>/dev/null || true && \
    rm -f /root/.ollama/id_ed25519 /root/.ollama/id_ed25519.pub

HEALTHCHECK --interval=30s --timeout=15s --start-period=300s --retries=5 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

COPY start.sh .
RUN chmod +x start.sh

# Override the default ollama ENTRYPOINT so our script runs directly
ENTRYPOINT []
CMD ["./start.sh"]
