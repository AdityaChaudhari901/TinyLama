#!/bin/bash
echo "=========================================="
echo "Starting Fynd AI at $(date)"
echo "MODEL=$MODEL  PORT=$PORT  WORKERS=${WEB_CONCURRENCY:-4}"
echo "=========================================="

exec python3 -m uvicorn app:app \
    --host 0.0.0.0 \
    --port "${PORT:-8080}" \
    --workers "${WEB_CONCURRENCY:-4}" \
    --log-level info
