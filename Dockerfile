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

# NOTE: We do NOT run `ollama pull` here.
# Kaniko (the Boltic build runner) OOM-kills any layer that snapshots a 17 GB
# model file. Instead, start.sh pulls the model at container start time and
# keeps it in the persistent volume across restarts.
# start.sh waits up to 150 s for Ollama + the pull before launching FastAPI.

HEALTHCHECK --interval=30s --timeout=15s --start-period=300s --retries=5 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

COPY start.sh .
RUN chmod +x start.sh

# Override the default ollama ENTRYPOINT so our script runs directly
ENTRYPOINT []
CMD ["./start.sh"]
