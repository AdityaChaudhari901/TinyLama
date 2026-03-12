#!/bin/bash
# Do NOT use set -e — we must always reach the uvicorn exec at the end,
# even if Ollama is slow or model verification fails on a tight 1-CPU instance.

echo "=========================================="
echo "Starting deployment at $(date)"
echo "Working directory: $(pwd)"
echo "Python version: $(python3 --version)"
echo "Files in /app: $(ls -la /app)"
echo "=========================================="

echo "Starting Ollama server..."
OLLAMA_HOST=127.0.0.1:11434 ollama serve &

# Wait up to 150 s (50 × 3 s) for Ollama to be ready.
# With only 1 CPU, startup can take well over 30 s — the old 30 s
# timeout caused the script to exit before uvicorn ever launched.
echo "Waiting for Ollama server to start (up to 150 s)..."
OLLAMA_READY=false
for i in $(seq 1 50); do
  if curl -s http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    echo "✅ Ollama server is ready after ${i} attempts (~$((i*3)) s)!"
    OLLAMA_READY=true
    break
  fi
  echo "Waiting for Ollama... ($i/50)"
  sleep 3
done

if [ "$OLLAMA_READY" = "false" ]; then
  echo "⚠️  Ollama did not respond in 150 s — continuing anyway so FastAPI can start."
fi

# Remove any models that are NOT qwen2.5:1.5b (best-effort, never fatal)
echo "Removing any non-qwen models..."
for model in $(ollama list 2>/dev/null | awk 'NR>1 {print $1}'); do
  if [[ "$model" != *"qwen2.5:1.5b"* ]]; then
    echo "🗑️  Removing $model..."
    ollama rm "$model" 2>/dev/null || true
  fi
done

# Verify the pre-pulled model; attempt a pull only as a last resort.
# The || true ensures the script never exits here even on 1-CPU machines.
echo "Verifying model $MODEL..."
if ollama list 2>/dev/null | grep -q "$MODEL"; then
  echo "✅ Model $MODEL is ready!"
else
  echo "⚠️  Model not found — attempting pull (may be slow on 1 CPU)..."
  ollama pull "$MODEL" 2>/dev/null || echo "⚠️  Pull failed — FastAPI will surface a 503 until the model is available."
fi

echo "Starting FastAPI app on port $PORT..."
echo "Environment: PORT=$PORT, MODEL=$MODEL"
echo "Checking if dist folder exists: $(ls -la /app/dist 2>&1 || echo 'dist not found')"
echo "About to start uvicorn..."
exec python3 -m uvicorn app:app --host 0.0.0.0 --port $PORT --log-level info
