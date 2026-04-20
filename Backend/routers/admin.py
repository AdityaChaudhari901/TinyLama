"""Admin and observability routes: /health, /metrics, /admin/reload."""
import os
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import APIKeyHeader

logger = logging.getLogger(__name__)
router = APIRouter()

_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)


def _require_admin_key(key: str | None = Depends(_key_header)) -> None:
    """If ADMIN_API_KEY env is set, requests must supply it in X-Admin-Key header."""
    required = os.getenv("ADMIN_API_KEY", "")
    if required and key != required:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Key header.")


_metrics: dict = {
    "requests_total":        0,
    "tool_calls_rag":        0,
    "tool_calls_recs":       0,
    "tool_calls_direct":     0,
    "requests_cancelled":    0,
    "avg_ttft_ms":           0.0,
    "ttft_samples":          0,
    "total_tokens_streamed": 0,
}
_EMA_ALPHA = 0.1


def update_ttft(ms: float) -> None:
    if _metrics["ttft_samples"] == 0:
        _metrics["avg_ttft_ms"] = ms
    else:
        _metrics["avg_ttft_ms"] = _EMA_ALPHA * ms + (1 - _EMA_ALPHA) * _metrics["avg_ttft_ms"]
    _metrics["ttft_samples"] += 1


@router.get("/health")
async def health(request: Request):
    catalog = request.app.state.catalog
    return {
        "ok":          bool(os.getenv("OPENROUTER_API_KEY")),
        "model":       os.getenv("MODEL"),
        "embed_model": os.getenv("EMBEDDINGS_MODEL"),
        "provider":    "openrouter",
        "model_ready": bool(os.getenv("OPENROUTER_API_KEY")),
        "kb_size":     catalog.size,
    }


@router.get("/metrics")
async def metrics(request: Request):
    return {**_metrics, "model": os.getenv("MODEL"), "kb_size": request.app.state.catalog.size}


@router.post("/admin/reload", dependencies=[Depends(_require_admin_key)])
async def admin_reload(request: Request):
    """Force-reload the knowledge base from Boltic for this worker."""
    catalog = request.app.state.catalog
    http    = request.app.state.http_client
    before  = catalog.size
    after   = await catalog.reload(http)
    await catalog.load_recs(http)
    return {"ok": True, "before": before, "after": after}
