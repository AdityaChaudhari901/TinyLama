"""Product catalog upload: POST /upload (CSV / Excel)."""
import csv
import io
import json
import logging
import os
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader

from models import Product, ProductMetadata
from services import boltic as boltic_client
from services import openrouter as or_client

logger = logging.getLogger(__name__)
router = APIRouter()

_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)


def _require_upload_key(key: str | None = Depends(_key_header)) -> None:
    required = os.getenv("ADMIN_API_KEY", "")
    if required and key != required:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Key header.")


def _parse_file(content: bytes, filename: str) -> list[dict]:
    ext = Path(filename).suffix.lower()
    if ext in (".xlsx", ".xls"):
        import openpyxl
        wb   = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws   = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        headers = [str(h).strip().lower() if h else "" for h in rows[0]]
        return [
            {headers[i]: (str(v).strip() if v is not None else "") for i, v in enumerate(row)}
            for row in rows[1:]
            if any(v is not None for v in row)
        ]
    text   = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    return [{k.strip().lower(): (v.strip() if v else "") for k, v in row.items()} for row in reader]


def _row_to_text(row: dict) -> tuple[str, str]:
    title    = row.get("title") or row.get("name") or row.get("product") or "Unknown Product"
    brand    = row.get("brand") or row.get("manufacturer") or ""
    category = row.get("category") or row.get("type") or ""
    price    = row.get("price") or ""
    rating   = row.get("rating") or ""
    desc     = row.get("description") or row.get("desc") or row.get("details") or ""
    features = row.get("features") or row.get("specs") or ""

    parts = [title]
    if brand:    parts.append(f"by {brand}")
    if category: parts.append(f"Category: {category}")
    if price:    parts.append(f"Price: {price}")
    if rating:   parts.append(f"Rating: {rating}/5")
    if desc:     parts.append(desc)
    if features: parts.append(f"Features: {features}")
    return title, "\n".join(parts)


@router.post("/upload", dependencies=[Depends(_require_upload_key)])
async def upload_file(request: Request, file: UploadFile = File(...)):
    if not os.getenv("OPENROUTER_API_KEY"):
        raise HTTPException(503, detail="OpenRouter API key not configured.")

    ext = Path(file.filename or "").suffix.lower()
    if ext not in {".csv", ".xlsx", ".xls"}:
        raise HTTPException(400, detail=f"Unsupported file type '{ext}'. Upload CSV or Excel.")

    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(400, detail="File too large. Max 5 MB.")

    try:
        rows = _parse_file(content, file.filename)
    except Exception as e:
        raise HTTPException(400, detail=f"Could not parse file: {e}")

    if not rows:
        raise HTTPException(400, detail="File is empty or has no data rows.")
    if len(rows) > 500:
        raise HTTPException(400, detail="Too many rows. Max 500 per upload.")

    catalog = request.app.state.catalog
    http    = request.app.state.http_client

    async def stream_progress():
        added, skipped = 0, 0
        total = len(rows)
        yield json.dumps({"status": "started", "total": total}) + "\n"

        for i, row in enumerate(rows):
            title, text = _row_to_text(row)
            if not text.strip():
                skipped += 1
                continue
            try:
                embedding = await or_client.embed(http, text)
                product = Product(
                    id=uuid.uuid4().hex,
                    title=title,
                    text=text,
                    embedding=embedding,
                    metadata=ProductMetadata(
                        brand=row.get("brand", ""),
                        category=row.get("category", ""),
                        price=row.get("price", ""),
                        rating=row.get("rating", ""),
                        description=row.get("description") or row.get("desc") or "",
                        features=row.get("features", "").split(";"),
                        availability=row.get("availability", "In Stock"),
                    ),
                    source="upload",
                )
                await catalog.add(product)

                boltic_record = {
                    "id":           product.id,
                    "title":        title,
                    "brand":        product.metadata.brand,
                    "category":     product.metadata.category,
                    "price":        product.metadata.price,
                    "rating":       product.metadata.rating,
                    "description":  product.metadata.description,
                    "features":     row.get("features", ""),
                    "availability": product.metadata.availability,
                    "embedding":    embedding,
                    "created_at":   int(time.time()),
                }
                await boltic_client.create_product(http, boltic_record)

                added += 1
                yield json.dumps({"status": "progress", "done": i + 1, "total": total, "title": title}) + "\n"
            except Exception as e:
                skipped += 1
                logger.warning("[upload] row %d failed: %s", i, e)
                yield json.dumps({"status": "progress", "done": i + 1, "total": total, "title": title, "error": str(e)}) + "\n"

        yield json.dumps({"status": "done", "added": added, "skipped": skipped, "total": total}) + "\n"

    return StreamingResponse(stream_progress(), media_type="application/x-ndjson")
