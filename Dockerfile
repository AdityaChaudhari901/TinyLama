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
# ollama/ollama:latest (Ubuntu 22.04) already has the ollama
# binary — no curl/install.sh needed.
# ============================================================
FROM ollama/ollama:latest
ENV DEBIAN_FRONTEND=noninteractive

# Install python3, pip; use --break-system-packages to bypass
# PEP 668 (Ubuntu marks its Python as "externally managed")
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy & install Python deps directly — packages land in
# Ubuntu's dist-packages (not site-packages) so imports work
COPY Backend/requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# Application code
COPY Backend/app.py .

# Built React frontend served by FastAPI as static files
COPY --from=frontend-builder /app/frontend/dist ./dist

# Runtime environment
ENV PORT=8080 \
    MODEL=qwen2.5:1.5b \
    OLLAMA_HOST=127.0.0.1:11434 \
    PYTHONUNBUFFERED=1


EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=15s --start-period=120s --retries=5 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

# Pre-pull the model at build time so container starts immediately
# (qwen2.5:1.5b is ~1GB — pulling at runtime causes health deadline failures)
RUN ollama serve & \
    pid=$! && \
    sleep 8 && \
    ollama pull qwen2.5:1.5b && \
    kill $pid && \
    wait $pid 2>/dev/null || true

# Startup script
COPY start.sh .
RUN chmod +x start.sh

# Override the default ollama ENTRYPOINT so our script runs directly
ENTRYPOINT []

CMD ["./start.sh"]