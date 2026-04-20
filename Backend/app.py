"""
Fynd AI — FastAPI application entry point.

Wires together services and routers. All business logic lives in:
  services/catalog.py   — in-memory vector store (CatalogService)
  services/boltic.py    — Boltic Tables client
  services/openrouter.py— OpenRouter client
  routers/chat.py       — /ask/stream, /generate + tool pipeline
  routers/admin.py      — /health, /metrics, /admin/reload
  routers/documents.py  — /documents CRUD
  routers/upload.py     — /upload
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()  # loads Backend/.env for local dev; no-op when vars are already set

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError

from services.catalog import CatalogService
from routers import admin, chat, documents, upload

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

_KB_RELOAD_INTERVAL = int(os.getenv("KB_RELOAD_INTERVAL", "300"))


async def _periodic_reload(app: FastAPI) -> None:
    while True:
        await asyncio.sleep(_KB_RELOAD_INTERVAL)
        try:
            await app.state.catalog.reload(app.state.http_client)
            await app.state.catalog.load_recs(app.state.http_client)
        except Exception as e:
            logger.error("[kb] periodic reload failed: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=5.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )
    app.state.catalog = CatalogService()

    if not os.getenv("OPENROUTER_API_KEY"):
        logger.warning("OPENROUTER_API_KEY is not set — model calls will fail")
    else:
        logger.info("OpenRouter ready | chat=%s | embed=%s", os.getenv("MODEL"), os.getenv("EMBEDDINGS_MODEL"))

    await app.state.catalog.reload(app.state.http_client)
    await app.state.catalog.load_recs(app.state.http_client)
    asyncio.create_task(_periodic_reload(app))

    yield

    await app.state.http_client.aclose()


_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:5173,http://localhost:5174,http://localhost:4173",
    ).split(",")
    if o.strip()
]

app = FastAPI(lifespan=lifespan, title="Fynd AI", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-Admin-Key"],
)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_req: Request, exc: RequestValidationError):
    errors = exc.errors()
    msg = str(errors[0].get("msg", "Validation error")) if errors else "Invalid input"
    if msg.startswith("Value error, "):
        msg = msg[len("Value error, "):]
    return JSONResponse(status_code=400, content={"error": msg})


app.include_router(admin.router)
app.include_router(chat.router)
app.include_router(documents.router)
app.include_router(upload.router)

# ── Static frontend (built React app) ─────────────────────────────────────────
_dist = Path(__file__).parent / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="assets")

    @app.get("/", include_in_schema=False)
    async def serve_root():
        return FileResponse(_dist / "index.html")

    @app.get("/{filename}", include_in_schema=False)
    async def serve_static(filename: str):
        fp = _dist / filename
        return FileResponse(fp) if fp.is_file() and fp.name != "index.html" else FileResponse(_dist / "index.html")


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8080")), reload=False)
