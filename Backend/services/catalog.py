"""
CatalogService — in-memory vector store with atomic reload.

Concurrency model:
  - asyncio.Lock ensures only one reload runs at a time.
  - Snapshot replacement (_store = new_list) is atomic in CPython (GIL).
  - Multiple concurrent reads are safe; writes (reload/add/delete) use the lock.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import uuid

import httpx

from models import Product, ProductMetadata
from services import boltic as boltic_client

logger = logging.getLogger(__name__)


class CatalogService:
    def __init__(self) -> None:
        self._store: list[Product] = []
        self._pid_index: dict[str, str] = {}    # P001 → uuid
        self._pid_reverse: dict[str, str] = {}  # uuid → P001
        self._recs_index: dict[str, list[dict]] = {}  # P001 → [{recommended_product_id, score, rank}]
        self._lock = asyncio.Lock()

    # ── Public read API (no lock needed — snapshot reads are safe) ─────────────

    @property
    def size(self) -> int:
        return len(self._store)

    @property
    def products(self) -> list[Product]:
        return self._store

    def get_by_id(self, product_id: str) -> Product | None:
        """Resolve P-ID (P001) or raw UUID to a Product."""
        resolved = self._pid_index.get(product_id.strip().upper()) or product_id.strip()
        return next((p for p in self._store if p.id == resolved), None)

    @property
    def pid_reverse(self) -> dict[str, str]:
        return self._pid_reverse

    # ── Mutation API (uses lock) ───────────────────────────────────────────────

    async def reload(self, http_client: httpx.AsyncClient) -> int:
        """Fetch products from Boltic and atomically replace the catalog."""
        table_id = os.getenv("BOLTIC_PRODUCTS_TABLE", "")
        logger.info("[catalog] reloading from Boltic table %s …", table_id)

        raw = await boltic_client.fetch_all(http_client, table_id)
        if not raw:
            logger.warning("[catalog] Boltic returned no products — keeping existing catalog")
            return self.size

        products = [p for row in raw if (p := self._parse_row(row)) is not None]
        pid_index   = {f"P{str(i + 1).zfill(3)}": p.id for i, p in enumerate(products)}
        pid_reverse = {v: k for k, v in pid_index.items()}

        async with self._lock:
            self._store       = products
            self._pid_index   = pid_index
            self._pid_reverse = pid_reverse

        logger.info("[catalog] loaded %d products (P001–P%03d)", len(products), len(products))
        return len(products)

    async def load_recs(self, http_client: httpx.AsyncClient) -> int:
        """Load pre-computed recommendations from Boltic recs table into memory."""
        table_id = os.getenv("BOLTIC_RECS_TABLE", "")
        if not table_id:
            logger.warning("[catalog] BOLTIC_RECS_TABLE not set — skipping recs load")
            return 0

        raw = await boltic_client.fetch_all(http_client, table_id)
        if not raw:
            logger.warning("[catalog] recs table is empty — recommendations will fall back to live cosine sim")
            return 0

        index: dict[str, list[dict]] = {}
        for row in raw:
            pid = (row.get("product_id") or "").strip().upper()
            if not pid:
                continue
            index.setdefault(pid, []).append({
                "recommended_product_id": (row.get("recommended_product_id") or "").strip().upper(),
                "score": float(row.get("score") or 0),
                "rank":  int(row.get("rank") or 0),
            })

        # Sort each product's recs by rank ascending
        for pid in index:
            index[pid].sort(key=lambda r: r["rank"])

        async with self._lock:
            self._recs_index = index

        logger.info("[catalog] loaded pre-computed recs for %d products", len(index))
        return len(index)

    def get_precomputed_recs(self, product_id: str) -> list[dict] | None:
        """
        Return pre-computed recommendations for a product.
        Accepts P-ID (P001) or raw UUID. Returns None if no pre-computed recs exist.
        Each entry includes full product metadata resolved from the in-memory store.
        """
        # Normalize to P-ID
        pid = self._pid_reverse.get(product_id.strip(), product_id.strip().upper())

        recs = self._recs_index.get(pid)
        if not recs:
            return None

        source = self.get_by_id(pid)
        source_cat = source.metadata.category.lower().strip() if source else ""

        enriched = []
        for rec in recs:
            rec_pid = rec["recommended_product_id"]
            product = self.get_by_id(rec_pid)
            if not product:
                continue
            enriched.append({
                "product_id":    rec_pid,
                "title":         product.title,
                "score":         rec["score"],
                "rank":          rec["rank"],
                "same_category": product.metadata.category.lower().strip() == source_cat,
                "metadata":      product.metadata.to_dict(),
            })

        return enriched if enriched else None

    async def add(self, product: Product) -> None:
        async with self._lock:
            self._store.append(product)

    async def remove(self, product_id: str) -> bool:
        async with self._lock:
            before = len(self._store)
            self._store = [p for p in self._store if p.id != product_id]
            return len(self._store) < before

    # ── Search ─────────────────────────────────────────────────────────────────

    def vector_search(
        self,
        query_embedding: list[float],
        *,
        top_n: int | None = None,
        threshold: float | None = None,
    ) -> list[Product]:
        if not self._store:
            return []
        threshold = threshold if threshold is not None else float(os.getenv("RAG_SIMILARITY_THRESHOLD", "0.50"))
        n = top_n or int(os.getenv("RAG_TOP_K", "5"))
        scored = sorted(
            ((_cosine(query_embedding, p.embedding), p) for p in self._store if p.embedding),
            key=lambda x: x[0],
            reverse=True,
        )
        return [p for score, p in scored[:n] if score >= threshold]

    def filtered_search(
        self,
        query_embedding: list[float],
        *,
        pool: list[Product],
        top_n: int,
    ) -> list[Product]:
        """Vector search within an already-filtered pool; no threshold applied."""
        scored = sorted(
            ((_cosine(query_embedding, p.embedding), p) for p in pool if p.embedding),
            key=lambda x: x[0],
            reverse=True,
        )
        return [p for _, p in scored[:top_n]]

    def keyword_search(self, query: str) -> list[Product]:
        """Fallback full-text search. Filters query to catalog-relevant words."""
        def stem(w: str) -> str:
            if w.endswith("es") and len(w) > 4:
                return w[:-2]
            if w.endswith("s") and len(w) > 3:
                return w[:-1]
            return w

        stemmed = [stem(w) for w in query.lower().split() if len(w) > 2]
        if not stemmed:
            return []

        catalog_text = " ".join(
            f"{p.title} {p.metadata.brand} {p.metadata.category}".lower()
            for p in self._store
        )
        words = [w for w in stemmed if w in catalog_text] or stemmed

        top_k = int(os.getenv("RAG_TOP_K", "5"))
        seen, results = set(), []
        for p in self._store:
            searchable = f"{p.title} {p.metadata.brand} {p.metadata.category}".lower()
            if all(w in searchable for w in words) and p.id not in seen:
                seen.add(p.id)
                results.append(p)
            if len(results) >= top_k:
                break
        return results

    def filter_pool(
        self,
        *,
        brand: str | None = None,
        category: str | None = None,
        max_price: float | None = None,
        min_price: float | None = None,
    ) -> list[Product]:
        """Return the subset of the catalog matching all active filters."""
        out = []
        for p in self._store:
            m = p.metadata
            if brand and brand.lower() not in m.brand.lower():
                continue
            if category and category.lower() not in m.category.lower():
                continue
            if max_price is not None:
                price = _parse_price(m.price)
                if price is None or price > max_price:
                    continue
            if min_price is not None:
                price = _parse_price(m.price)
                if price is None or price < min_price:
                    continue
            out.append(p)
        return out

    def deduplicate(self, products: list[Product]) -> list[Product]:
        seen, out = set(), []
        for p in products:
            key = re.sub(r"\s+", " ", p.title.lower().strip())
            if key not in seen:
                seen.add(key)
                out.append(p)
        return out

    # ── Private helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _parse_row(row: dict) -> Product | None:
        emb = row.get("embedding")
        if isinstance(emb, str):
            try:
                emb = json.loads(emb)
            except Exception:
                try:
                    emb = [float(x) for x in emb.strip("[]").split(",") if x.strip()]
                except Exception:
                    emb = None
        if not isinstance(emb, list) or not emb:
            return None

        text_parts = [row.get("title", "")]
        for field in ("brand", "category", "description", "features"):
            val = row.get(field, "")
            if val:
                text_parts.append(str(val))

        return Product(
            id=row.get("id") or row.get("product_id", "") or uuid.uuid4().hex,
            title=row.get("title", ""),
            text="\n".join(text_parts),
            embedding=emb,
            metadata=ProductMetadata.from_row(row),
            source="boltic",
        )


# ── Standalone helpers ─────────────────────────────────────────────────────────

def _cosine(a: list[float], b: list[float]) -> float:
    dot    = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def _parse_price(val) -> float | None:
    if val is None:
        return None
    cleaned = re.sub(r"[^\d.]", "", str(val).strip())
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None
