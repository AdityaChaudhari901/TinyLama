#!/bin/bash
set -e

echo "=========================================="
echo "Starting deployment at $(date)"
echo "Working directory: $(pwd)"
echo "Python version: $(python3 --version)"
echo "Files in /app: $(ls -la /app)"
echo "=========================================="

echo "Starting Ollama server..."
OLLAMA_HOST=127.0.0.1:11434 ollama serve &

# Wait for Ollama to be ready (reduced from 30 to 15 iterations)
echo "Waiting for Ollama server to start..."
for i in {1..15}; do
  if curl -s http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    echo "✅ Ollama server is ready!"
    break
  fi
  echo "Waiting for Ollama... ($i/15)"
  sleep 2
done

# Remove any models that are NOT qwen2.5:1.5b
echo "Removing any non-qwen models..."
for model in $(ollama list 2>/dev/null | awk 'NR>1 {print $1}'); do
  if [[ "$model" != *"qwen2.5:1.5b"* ]]; then
    echo "🗑️  Removing $model..."
    ollama rm "$model" 2>/dev/null || true
  fi
done

# Remove any model that is NOT qwen2.5 to keep storage clean
echo "Removing any non-qwen2.5 models..."
ollama list | awk 'NR>1 {print $1}' | grep -v "^qwen2.5" | xargs -r -I{} ollama rm {} 2>/dev/null || true

# Model is pre-pulled at build time, just verify it exists
echo "Verifying model $MODEL..."
if ollama list | grep -q "$MODEL"; then
  echo "✅ Model $MODEL is ready!"
else
  echo "⚠️  Model not found in image, pulling now (this may take a few minutes)..."
  ollama pull $MODEL
fi

echo "Starting FastAPI app on port $PORT..."
echo "Environment: PORT=$PORT, MODEL=$MODEL"
echo "Checking if dist folder exists: $(ls -la /app/dist 2>&1 || echo 'dist not found')"
echo "About to start uvicorn..."
exec python3 -m uvicorn app:app --host 0.0.0.0 --port $PORT --log-level info
