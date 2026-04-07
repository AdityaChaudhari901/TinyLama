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
OLLAMA_PID=$!

# ── 2. Wait for Ollama API to respond (up to 150 s) ──────────────────────────
echo "Waiting for Ollama to be ready (up to 150 s)..."
for i in $(seq 1 50); do
    if curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
        echo "Ollama ready after ~$((i*3)) s"
        break
    fi
    sleep 3
done

# ── 3. Start FastAPI immediately so Nomad health checks pass ──────────────────
# The /health endpoint returns ok:true regardless of model state, so Nomad
# marks the allocation healthy within seconds. Model pull happens in background.
echo "Starting FastAPI on port $PORT..."
python3 -m uvicorn app:app --host 0.0.0.0 --port "$PORT" --log-level info &
UVICORN_PID=$!

# ── 4. Pull model in the background ──────────────────────────────────────────
echo "Pulling model $MODEL in background..."
(
    if ollama list 2>/dev/null | grep -q "$MODEL"; then
        echo "Model $MODEL already present — skipping pull."
    else
        echo "Model $MODEL not found — pulling now..."
        for attempt in 1 2 3; do
            ollama pull "$MODEL" && echo "Model pull succeeded." && break
            echo "Pull attempt $attempt failed — retrying in 10 s..."
            sleep 10
        done
    fi
) &

# ── 5. Wait for either process to exit (keeps script alive) ──────────────────
wait $UVICORN_PID
