#!/bin/bash
# Do NOT use set -e — we must always reach the uvicorn exec at the end.

echo "=========================================="
echo "Starting deployment at $(date)"
echo "MODEL=$MODEL  PORT=$PORT"
echo "OLLAMA_NUM_PARALLEL=$OLLAMA_NUM_PARALLEL  OLLAMA_NUM_THREADS=$OLLAMA_NUM_THREADS"
echo "=========================================="

# ── 1. Start Ollama server ────────────────────────────────────────────────────
echo "Starting Ollama server..."
OLLAMA_HOST=127.0.0.1:11434 ollama serve &

# ── 2. Wait for Ollama API to respond (up to 150 s) ──────────────────────────
echo "Waiting for Ollama to be ready (up to 150 s)..."
OLLAMA_READY=false
for i in $(seq 1 50); do
    if curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
        echo "Ollama ready after ~$((i*3)) s"
        OLLAMA_READY=true
        break
    fi
    sleep 3
done

if [ "$OLLAMA_READY" = "false" ]; then
    echo "WARNING: Ollama did not respond in 150 s — continuing anyway."
fi

# ── 3. Verify the model that was baked into the image is present ──────────────
# The model is pulled at build time so this should always succeed.
# The pull below is a last-resort fallback in case the image layer was dropped.
echo "Verifying model $MODEL..."
if ollama list 2>/dev/null | grep -q "$MODEL"; then
    echo "Model $MODEL is ready."
else
    echo "WARNING: Model $MODEL not found in image — attempting pull..."
    for attempt in 1 2 3; do
        ollama pull "$MODEL" && echo "Pull succeeded." && break
        echo "Attempt $attempt failed — retrying in 10 s..."
        sleep 10
    done
fi

# ── 4. Launch FastAPI ─────────────────────────────────────────────────────────
echo "Starting FastAPI on port $PORT..."
exec python3 -m uvicorn app:app --host 0.0.0.0 --port "$PORT" --log-level info
