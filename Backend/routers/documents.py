"""Knowledge base CRUD: /documents."""
import uuid
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator

from services import openrouter as or_client

logger = logging.getLogger(__name__)
router = APIRouter()


class DocumentIn(BaseModel):
    text:  str
    title: str | None = None

    @field_validator("text")
    @classmethod
    def validate_text(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Document text cannot be empty")
        if len(v) > 10_000:
            raise ValueError("Document too long. Max 10,000 characters")
        return v.strip()


@router.post("/documents")
async def add_document(payload: DocumentIn, request: Request):
    catalog    = request.app.state.catalog
    http       = request.app.state.http_client
    doc_id     = uuid.uuid4().hex
    embedding  = await or_client.embed(http, payload.text)

    from models import Product, ProductMetadata
    product = Product(
        id=doc_id,
        title=payload.title or f"Document {catalog.size + 1}",
        text=payload.text,
        embedding=embedding,
        metadata=ProductMetadata(),
        source="manual",
    )
    await catalog.add(product)
    logger.info("[kb] manual add: %s", product.title)
    return {
        "id":      doc_id,
        "title":   product.title,
        "snippet": payload.text[:120] + ("…" if len(payload.text) > 120 else ""),
    }


@router.get("/documents")
async def list_documents(request: Request):
    return [
        {
            "id":      p.id,
            "title":   p.title,
            "snippet": p.text[:120] + ("…" if len(p.text) > 120 else ""),
            "source":  p.source,
        }
        for p in request.app.state.catalog.products
    ]


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, request: Request):
    removed = await request.app.state.catalog.remove(doc_id)
    if not removed:
        raise HTTPException(404, detail="Document not found")
    return {"ok": True}
