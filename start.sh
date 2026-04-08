#!/bin/bash
# Do NOT use set -e — we must always reach the uvicorn exec at the end.

echo "=========================================="
echo "Starting deployment at $(date)"
echo "MODEL=$MODEL  PORT=$PORT"
echo "OLLAMA_NUM_PARALLEL=$OLLAMA_NUM_PARALLEL  OLLAMA_NUM_THREADS=$OLLAMA_NUM_THREADS"
echo "=========================================="

# ── 1. Apply Ollama config (boltic does not inject env vars reliably) ─────────
export OLLAMA_HOST=127.0.0.1:11434
export OLLAMA_MODELS=${OLLAMA_MODELS:-/app/.ollama/models}
export OLLAMA_NUM_PARALLEL=${OLLAMA_NUM_PARALLEL:-2}
export OLLAMA_NUM_THREADS=${OLLAMA_NUM_THREADS:-6}
export OLLAMA_KEEP_ALIVE=${OLLAMA_KEEP_ALIVE:--1}
export OLLAMA_FLASH_ATTENTION=${OLLAMA_FLASH_ATTENTION:-1}
export OLLAMA_KV_CACHE_TYPE=${OLLAMA_KV_CACHE_TYPE:-q8_0}
export OLLAMA_MAX_LOADED_MODELS=${OLLAMA_MAX_LOADED_MODELS:-1}
export OLLAMA_LLM_LIBRARY=${OLLAMA_LLM_LIBRARY:-cpu}
export GOMEMLIMIT=${GOMEMLIMIT:-24576MiB}

echo "Ollama config: parallel=$OLLAMA_NUM_PARALLEL threads=$OLLAMA_NUM_THREADS keep_alive=$OLLAMA_KEEP_ALIVE flash_attn=$OLLAMA_FLASH_ATTENTION"

# ── 2. Start Ollama server ────────────────────────────────────────────────────
echo "Starting Ollama server..."
ollama serve &
OLLAMA_PID=$!

# ── 3. Wait for Ollama API to respond (up to 150 s) ──────────────────────────
echo "Waiting for Ollama to be ready (up to 150 s)..."
for i in $(seq 1 50); do
    if curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
        echo "Ollama ready after ~$((i*3)) s"
        break
    fi
    sleep 3
done

# ── 4. Verify the image already contains the configured model ─────────────────
if ! ollama list 2>/dev/null | grep -q "$MODEL"; then
    echo "Bundled model $MODEL was not found in OLLAMA_MODELS=$OLLAMA_MODELS"
    exit 1
fi

# ── 5. Start FastAPI after Ollama is healthy ──────────────────────────────────
echo "Starting FastAPI on port $PORT..."
python3 -m uvicorn app:app --host 0.0.0.0 --port "$PORT" --log-level info &
UVICORN_PID=$!

# ── 6. Warm up the bundled model in the background ────────────────────────────
echo "Warming up bundled model $MODEL..."
(
    ollama run "$MODEL" "hi" --nowordwrap 2>/dev/null || true
    echo "Model $MODEL is warm and ready."
) &

# ── 7. Wait for either process to exit (keeps script alive) ──────────────────
wait $UVICORN_PID
