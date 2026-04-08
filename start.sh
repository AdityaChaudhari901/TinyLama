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
export OLLAMA_NUM_PARALLEL=${OLLAMA_NUM_PARALLEL:-1}
export OLLAMA_NUM_THREADS=${OLLAMA_NUM_THREADS:-4}
export OLLAMA_KEEP_ALIVE=${OLLAMA_KEEP_ALIVE:-0}
export OLLAMA_FLASH_ATTENTION=${OLLAMA_FLASH_ATTENTION:-1}
export OLLAMA_KV_CACHE_TYPE=${OLLAMA_KV_CACHE_TYPE:-q8_0}
export OLLAMA_MAX_LOADED_MODELS=${OLLAMA_MAX_LOADED_MODELS:-1}
export OLLAMA_LLM_LIBRARY=${OLLAMA_LLM_LIBRARY:-cpu}
export OLLAMA_USE_MMAP=${OLLAMA_USE_MMAP:-1}
export GOMEMLIMIT=${GOMEMLIMIT:-3500MiB}

echo "Ollama config: parallel=$OLLAMA_NUM_PARALLEL threads=$OLLAMA_NUM_THREADS keep_alive=$OLLAMA_KEEP_ALIVE flash_attn=$OLLAMA_FLASH_ATTENTION mmap=$OLLAMA_USE_MMAP"

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

# ── 4. Start FastAPI immediately so health checks can pass while the model
#      is restored or downloaded onto the persistent models volume. ───────────
echo "Starting FastAPI on port $PORT..."
python3 -m uvicorn app:app --host 0.0.0.0 --port "$PORT" --log-level info &
UVICORN_PID=$!

# ── 5. Restore the configured model onto the persistent volume if needed. ────
echo "Ensuring model $MODEL is available in OLLAMA_MODELS=$OLLAMA_MODELS..."
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

    echo "Warming up model $MODEL into RAM..."
    ollama run "$MODEL" "hi" --nowordwrap 2>/dev/null || true
    echo "Model $MODEL is warm and ready."
) &

# ── 6. Wait for either process to exit (keeps script alive) ──────────────────
wait $UVICORN_PID
