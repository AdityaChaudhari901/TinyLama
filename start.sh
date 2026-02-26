#!/bin/bash
set -e

echo "Starting Ollama server..."
OLLAMA_HOST=127.0.0.1:11434 ollama serve &

# Detached subshell to pull the model asynchronously so uvicorn binds instantly
(
  echo "Waiting for Ollama to be ready..."
  sleep 5
  echo "Pulling model $MODEL in the background..."
  ollama pull $MODEL
) &

echo "Starting FastAPI app on port 8080..."
exec uvicorn app:app --host 0.0.0.0 --port 8080
