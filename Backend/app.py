import os
import re
import uuid
import math
import asyncio
import httpx
import logging
import time
import json
from contextlib import asynccontextmanager
from pathlib import Path
import csv
import io
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, field_validator
import uvicorn
from dotenv import load_dotenv

load_dotenv()  # loads Backend/.env (or .env in cwd) into os.environ

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
PORT                = int(os.getenv("PORT", "8080"))
OPENROUTER_API_KEY  = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_URL      = "https://openrouter.ai/api/v1/chat/completions"
EMBEDDINGS_URL      = "https://openrouter.ai/api/v1/embeddings"
MODEL               = os.getenv("MODEL", "openai/gpt-4o-mini")
EMBEDDINGS_MODEL    = os.getenv("EMBEDDINGS_MODEL", "openai/text-embedding-3-small")
DEFAULT_TEMPERATURE = float(os.getenv("DEFAULT_TEMPERATURE", "0.45"))
DEFAULT_MAX_TOKENS  = int(os.getenv("DEFAULT_MAX_TOKENS", "2048"))
MAX_INPUT_LENGTH    = int(os.getenv("MAX_INPUT_LENGTH", "2000"))
MAX_HISTORY_CHARS   = int(os.getenv("MAX_HISTORY_CHARS", "40000"))
APP_SITE_URL        = os.getenv("APP_SITE_URL", "https://fynd-ai.app")
APP_TITLE           = os.getenv("APP_TITLE", "Fynd AI")
RAG_SIMILARITY_THRESHOLD = float(os.getenv("RAG_SIMILARITY_THRESHOLD", "0.50"))
RAG_TOP_K           = int(os.getenv("RAG_TOP_K", "5"))

# ── Boltic Table config ───────────────────────────────────────────────────────
BOLTIC_TOKEN         = os.getenv("BOLTIC_TOKEN", "")
BOLTIC_API_BASE      = "https://api.boltic.fynd.com/asia-south1/service/panel/boltic-tables/v1/tables"
BOLTIC_PRODUCTS_TABLE      = os.getenv("BOLTIC_PRODUCTS_TABLE", "735364bf-48e3-4723-b549-e3e123562d2a")
BOLTIC_RECS_TABLE          = os.getenv("BOLTIC_RECS_TABLE", "db2b84be-744e-4e16-a369-d3d7d17b2ec4")
BOLTIC_PAGE_SIZE     = 100  # Boltic API max rows per page

ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:5173,http://localhost:5174,http://localhost:4173"
    ).split(",")
    if o.strip()
]

# ── Input validation ──────────────────────────────────────────────────────────
def validate_input(text: str) -> str:
    if not text or not text.strip():
        raise ValueError("Input cannot be empty")
    if len(text) > MAX_INPUT_LENGTH:
        raise ValueError(f"Input too long. Max {MAX_INPUT_LENGTH} characters")
    return text.strip()

# ── Context window management ─────────────────────────────────────────────────
def trim_to_context(messages: list[dict], max_chars: int = MAX_HISTORY_CHARS) -> list[dict]:
    """
    Keep as many recent messages as fit within max_chars.
    Always includes the last message. Never cuts a message mid-content.
    Preserves user/assistant turn pairs — drops the older user msg if its
    assistant reply can't fit, keeping the history coherent.
    """
    total, result = 0, []
    for msg in reversed(messages):
        msg_len = len(msg.get("content", ""))
        if total + msg_len > max_chars and result:
            break
        total += msg_len
        result.insert(0, msg)
    if not result and messages:
        result = [messages[-1]]
    return result

# ── AI Personality ────────────────────────────────────────────────────────────
SYSTEM_PERSONALITY = os.getenv(
    "AI_PERSONALITY",
    "You are Fynd AI, a product shopping assistant for the Fynd catalog.\n"
    "STRICT RULES — follow every rule exactly, no exceptions:\n"
    "1. Greetings/small talk (e.g. 'hi', 'thanks'): reply briefly WITHOUT calling any tools.\n"
    "2. ANY product question: you MUST call search_products. Never skip this step.\n"
    "3. Use ONLY data returned by tools. NEVER use your training knowledge about products, brands, specs, or prices.\n"
    "4. If search_products returns found=false OR returns products that do not match the request: "
    "respond EXACTLY with 'I don't have any products matching that in our catalog.' — nothing more.\n"
    "5. NEVER mention, describe, or recommend any product not present in the tool result. "
    "Do not name brands (Apple, Samsung, etc.) or models that were not returned by the tool.\n"
    "6. Price filter: when user says 'under X' or 'below X', pass max_price=X to search_products. "
    "Show ONLY products within that range.\n"
    "7. Similar/recommended products: call get_recommendations with the product_id from the search result. "
    "If no product_id is available, say you need a specific product first. "
    "If same_category_found=false in the result, say 'No similar [category] in our catalog — here are related products you might like:' before listing them.\n"
    "8. Format: list each product with name, price, and 2-3 key features. Be concise."
)

def _resolve_temperature(value: float | None) -> float:
    return DEFAULT_TEMPERATURE if value is None else value

# ── OpenRouter helpers ────────────────────────────────────────────────────────
def _headers() -> dict:
    return {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": APP_SITE_URL,
        "X-Title": APP_TITLE,
        "Content-Type": "application/json",
    }

# ── In-memory product vector store ───────────────────────────────────────────
# Each entry: {id, title, text, embedding: list[float], metadata, source}
_product_store: list[dict] = []
# Secondary P-ID index: "P001" → UUID (assigned by load order from Boltic)
_pid_index: dict[str, str] = {}

def _boltic_headers() -> dict:
    return {"x-boltic-token": BOLTIC_TOKEN, "Content-Type": "application/json"}


async def _boltic_fetch_all(table_id: str) -> list[dict]:
    """
    Fetch all records from a Boltic table using the POST /records/list endpoint.
    Paginates automatically until all rows are retrieved.
    """
    url     = f"{BOLTIC_API_BASE}/{table_id}/records/list"
    records: list[dict] = []
    page_no = 1
    while True:
        try:
            r = await _http_client.post(
                url,
                headers=_boltic_headers(),
                json={
                    "page": {"page_no": page_no, "page_size": BOLTIC_PAGE_SIZE},
                    "sort": [{"field": "created_at", "direction": "asc"}],
                },
                timeout=30.0,
            )
            r.raise_for_status()
            body  = r.json()
            # Response shape: {data: [...records...]}
            data  = body.get("data", [])
            batch = data if isinstance(data, list) else (data.get("list") or data.get("records") or [])
            if not batch:
                break
            records.extend(batch)
            # Stop when fewer rows than page_size — we've hit the last page
            if len(batch) < BOLTIC_PAGE_SIZE:
                break
            page_no += 1
        except Exception as e:
            logger.error("[boltic] fetch failed (table=%s page=%d): %s", table_id, page_no, e)
            break
    return records


async def _load_from_boltic():
    """
    Load products + recommendations from Boltic tables into memory.
    Falls back gracefully if tables are empty or unreachable.
    """
    global _product_store, _pid_index

    logger.info("[boltic] Loading products from table %s …", BOLTIC_PRODUCTS_TABLE)
    raw_products = await _boltic_fetch_all(BOLTIC_PRODUCTS_TABLE)

    if not raw_products:
        logger.warning("[boltic] Products table is empty or unreachable — knowledge base will be empty")
        return

    products = []
    for row in raw_products:
        emb = row.get("embedding")
        # Boltic Vector column can return:
        #   - list[float]  → native (already correct)
        #   - str          → JSON array "[-0.1, 0.2, ...]" or CSV "-0.1,0.2,..."
        if isinstance(emb, str):
            try:
                emb = json.loads(emb)          # handles "[-0.1, 0.2, ...]"
            except Exception:
                try:
                    emb = [float(x) for x in emb.strip("[]").split(",") if x.strip()]
                except Exception:
                    emb = None
        if not isinstance(emb, list) or not emb:
            continue  # skip rows without a valid embedding

        text_parts = [row.get("title", "")]
        for field in ("brand", "category", "description", "features"):
            val = row.get(field, "")
            if val:
                text_parts.append(str(val))

        products.append({
            "id":        row.get("id") or row.get("product_id", ""),
            "title":     row.get("title", ""),
            "text":      "\n".join(text_parts),
            "embedding": emb,
            "metadata": {
                "brand":        row.get("brand", ""),
                "category":     row.get("category", ""),
                "price":        row.get("price", ""),
                "rating":       row.get("rating", ""),
                "features":     row.get("features", []),
                "availability": row.get("availability", "In Stock"),
            },
            "source": "boltic",
        })

    _product_store = products
    # Assign P-IDs based on load order (sorted by created_at asc from Boltic).
    # This makes P001 == first product, P002 == second, etc. — matching the
    # seeded recommendations table which was generated with the same ordering.
    _pid_index = {f"P{str(i + 1).zfill(3)}": p["id"] for i, p in enumerate(products)}
    logger.info("[boltic] Loaded %d products (P001–P%03d)", len(_product_store), len(_product_store))


async def _boltic_create_product(record: dict) -> bool:
    """Write a single product record to Boltic Products table."""
    url = f"{BOLTIC_API_BASE}/{BOLTIC_PRODUCTS_TABLE}/records"
    # id, created_at, updated_at are auto-generated by Boltic
    auto_fields = {"id", "created_at", "updated_at"}
    payload = {k: v for k, v in record.items() if k not in auto_fields}
    try:
        r = await _http_client.post(
            url,
            headers=_boltic_headers(),
            json=payload,
            timeout=15.0,
        )
        r.raise_for_status()
        body = r.json()
        if body.get("error"):
            logger.warning("[boltic] record rejected for %s: %s", record.get("title"), body["error"])
            return False
        return True
    except Exception as e:
        logger.warning("[boltic] failed to write product %s: %s", record.get("title"), e)
        return False

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot    = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0

async def _embed(text: str) -> list[float]:
    """Embed text using OpenRouter text-embedding-3-small."""
    r = await _http_client.post(
        EMBEDDINGS_URL,
        headers=_headers(),
        json={"model": EMBEDDINGS_MODEL, "input": text},
    )
    r.raise_for_status()
    return r.json()["data"][0]["embedding"]

def _parse_price(val) -> float | None:
    """Normalize price strings like '$149.99', '₹249', '1,299' to float."""
    if val is None:
        return None
    cleaned = re.sub(r'[^\d.]', '', str(val).strip())
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _vector_search(query_embedding: list[float], top_n: int | None = None) -> list[dict]:
    """Cosine similarity search over in-memory product store."""
    if not _product_store:
        return []
    n = top_n or RAG_TOP_K
    scored = [
        (_cosine_similarity(query_embedding, p["embedding"]), p)
        for p in _product_store
    ]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        p for score, p in scored[:n]
        if score >= RAG_SIMILARITY_THRESHOLD
    ]


def _deduplicate(products: list[dict]) -> list[dict]:
    """Remove duplicate products by normalized title (keep first occurrence)."""
    seen, out = set(), []
    for p in products:
        key = re.sub(r'\s+', ' ', p["title"].lower().strip())
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out

def _keyword_search(query: str) -> list[dict]:
    """
    Keyword fallback — used when vector search returns no results.
    Matches word stems against title + category + brand to handle plurals
    and morphological variants (e.g. 'consoles' finds 'console',
    'shoes' finds 'shoe'). Requires ALL query words to match so that
    broad single-word hits (e.g. 'gaming' matching a mouse) don't appear.
    """
    # Strip trailing 's'/'es' for simple stemming
    def stem(w: str) -> str:
        if w.endswith("es") and len(w) > 4:
            return w[:-2]
        if w.endswith("s") and len(w) > 3:
            return w[:-1]
        return w

    words = [stem(w) for w in query.lower().split() if len(w) > 2]
    if not words:
        return []

    seen, results = set(), []
    for p in _product_store:
        meta       = p.get("metadata", {})
        searchable = f"{p['title']} {meta.get('brand', '')} {meta.get('category', '')}".lower()
        # ALL words must match (AND logic) to avoid off-topic single-word hits
        if all(w in searchable for w in words) and p["id"] not in seen:
            seen.add(p["id"])
            results.append(p)
        if len(results) >= RAG_TOP_K:
            break
    return results

# ── Tool 1: Search knowledge base (RAG) ──────────────────────────────────────
async def search_products(
    query: str,
    max_price: float | None = None,
    min_price: float | None = None,
    _precomputed_embedding: list[float] | None = None,  # injected by speculative embed
) -> dict:
    """
    Search the product knowledge base using semantic similarity.
    Supports optional price range filtering.
    When price filter is active, scans the full catalog by price first,
    then re-ranks by semantic relevance to the query.
    _precomputed_embedding: skip the embed() call if already computed in parallel.
    """
    logger.info(f"[tool:search_products] query={query!r} max_price={max_price} min_price={min_price}")
    try:
        price_filter = max_price is not None or min_price is not None

        if price_filter:
            # Scan ALL products for price match — no similarity threshold applied
            price_matched = []
            for p in _product_store:
                price = _parse_price(p.get("metadata", {}).get("price"))
                if price is None:
                    continue
                if max_price is not None and price > max_price:
                    continue
                if min_price is not None and price < min_price:
                    continue
                price_matched.append(p)

            # Re-rank by semantic similarity to the query (if any products found)
            if price_matched and query.strip():
                q_emb = _precomputed_embedding or await _embed(query)
                scored = [
                    (_cosine_similarity(q_emb, p["embedding"]), p)
                    for p in price_matched
                ]
                scored.sort(key=lambda x: x[0], reverse=True)
                results = [p for _, p in scored]
            else:
                results = price_matched

        else:
            # Normal semantic search with threshold
            q_emb = _precomputed_embedding or await _embed(query)
            results = _vector_search(q_emb)

            # Only fall back to keyword search when vector search returns nothing.
            # Merging keyword results unconditionally caused off-topic products to
            # appear (e.g. a gaming mouse showing up in "gaming console" results).
            if not results:
                logger.info("[tool:search_products] vector miss — trying keyword fallback")
                results = _keyword_search(query)

        # Deduplicate by title and cap
        results = _deduplicate(results)[:RAG_TOP_K]

        if results:
            products = [
                {
                    "id":       p["id"],
                    "title":    p["title"],
                    "details":  p["text"],
                    "metadata": p.get("metadata", {}),
                    "source":   p.get("source", "knowledge_base"),
                }
                for p in results
            ]
            logger.info(f"[tool:search_products] found {len(products)} results")
            return {"found": True, "products": products, "source": "knowledge_base"}

        logger.info("[tool:search_products] no results found")
        return {"found": False, "products": [], "source": "knowledge_base"}

    except Exception as e:
        logger.error(f"[tool:search_products] error: {e}")
        return {"found": False, "error": str(e)}



# ── Tool 2: RAG-based recommendations ────────────────────────────────────────
async def get_recommendations(product_id: str) -> dict:
    """
    Find similar products by embedding similarity — RAG-powered.

    Uses the target product's own embedding as the query vector and runs
    cosine similarity against every other product in the store.
    Works for any product in the catalog; no pre-computed table needed.
    """
    logger.info(f"[tool:get_recommendations] product_id={product_id!r}")

    # Resolve UUID → P-ID or P-ID → UUID so we can find the product either way
    resolved_uuid = _pid_index.get(product_id) or product_id
    source_product = next((p for p in _product_store if p["id"] == resolved_uuid), None)

    if not source_product or not source_product.get("embedding"):
        logger.info(f"[tool:get_recommendations] product not found: {product_id!r}")
        return {"found": False, "product_id": product_id, "recommendations": []}

    query_embedding  = source_product["embedding"]
    source_category  = source_product.get("metadata", {}).get("category", "").lower().strip()

    # Score every other product by cosine similarity to the source product
    scored = [
        (_cosine_similarity(query_embedding, p["embedding"]), p)
        for p in _product_store
        if p["id"] != resolved_uuid and p.get("embedding")
    ]
    scored.sort(key=lambda x: x[0], reverse=True)

    # Prefer same-category products first, then fill with best cross-category.
    # No minimum threshold — for recommendations the user always wants results;
    # niche categories (e.g. only one gaming console) would return nothing otherwise.
    same_cat  = [(s, p) for s, p in scored if p.get("metadata", {}).get("category", "").lower().strip() == source_category]
    cross_cat = [(s, p) for s, p in scored if p.get("metadata", {}).get("category", "").lower().strip() != source_category]

    # Fill up to RAG_TOP_K: same-category first, then cross-category
    combined = same_cat[:RAG_TOP_K] + cross_cat[: max(0, RAG_TOP_K - len(same_cat))]
    combined = combined[:RAG_TOP_K]

    enriched = [
        {
            "product_id":    p["id"],
            "title":         p["title"],
            "score":         round(score, 4),
            "rank":          rank,
            "same_category": p.get("metadata", {}).get("category", "").lower().strip() == source_category,
            "metadata":      p.get("metadata", {}),
        }
        for rank, (score, p) in enumerate(combined, start=1)
    ]

    has_same_cat = any(r["same_category"] for r in enriched)
    logger.info(
        f"[tool:get_recommendations] '{source_product['title']}' → "
        f"{len(enriched)} products (same_category={has_same_cat})"
    )
    return {
        "found":         True,
        "product_id":    product_id,
        "source_title":  source_product["title"],
        "same_category_found": has_same_cat,
        "recommendations":     enriched,
    }


# ── Tool definitions (sent to LLM) ───────────────────────────────────────────
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": (
                "Search for products in the knowledge base using semantic similarity. "
                "Supports optional price range filtering. "
                "When the user specifies a price limit (e.g. 'under $50', 'below 500'), "
                "always pass max_price. Returns matching products if found."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language product search query, e.g. 'running shoes' or 'Samsung phones'",
                    },
                    "max_price": {
                        "type": "number",
                        "description": "Maximum price (numeric, no currency symbol). Use when user says 'under X', 'below X', 'less than X'.",
                    },
                    "min_price": {
                        "type": "number",
                        "description": "Minimum price (numeric). Use when user says 'above X', 'more than X'.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recommendations",
            "description": (
                "Get pre-computed product recommendations similar to a specific product. "
                "Use this when the user asks for 'similar products', 'recommendations', "
                "'what else should I buy', or 'show me more like this'. "
                "Requires a product_id (e.g. 'P001'). Call search_products first if you "
                "don't have the product_id yet."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": "string",
                        "description": "The product ID to get recommendations for, e.g. 'P001'",
                    }
                },
                "required": ["product_id"],
            },
        },
    },
]

TOOL_MAP = {
    "search_products":     search_products,
    "get_recommendations": get_recommendations,
}

# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(_: FastAPI):
    global _http_client
    _http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=5.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )
    if not OPENROUTER_API_KEY:
        logger.warning("OPENROUTER_API_KEY is not set")
    else:
        logger.info("OpenRouter ready | chat=%s | embed=%s", MODEL, EMBEDDINGS_MODEL)
    await _load_from_boltic()
    yield
    if _http_client:
        await _http_client.aclose()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)

_http_client: httpx.AsyncClient | None = None

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

def _update_ttft(ms: float):
    if _metrics["ttft_samples"] == 0:
        _metrics["avg_ttft_ms"] = ms
    else:
        _metrics["avg_ttft_ms"] = _EMA_ALPHA * ms + (1 - _EMA_ALPHA) * _metrics["avg_ttft_ms"]
    _metrics["ttft_samples"] += 1


# ── Validation error handler ──────────────────────────────────────────────────
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Request, exc: RequestValidationError):
    errors = exc.errors()
    if errors:
        msg = str(errors[0].get("msg", "Validation error"))
        if msg.startswith("Value error, "):
            msg = msg.replace("Value error, ", "", 1)
        return JSONResponse(status_code=400, content={"error": msg})
    return JSONResponse(status_code=422, content={"error": "Invalid input"})


# ── Request models ────────────────────────────────────────────────────────────
class ChatMessage(BaseModel):
    role: str
    content: str


class AskIn(BaseModel):
    messages:    list[ChatMessage]
    temperature: float | None = DEFAULT_TEMPERATURE
    personality: str | None   = None
    use_tools:   bool          = True   # set False to skip tool calling

    @field_validator('messages')
    @classmethod
    def validate_messages(cls, v):
        if not v:
            raise ValueError("Messages cannot be empty")
        last_user = next((m for m in reversed(v) if m.role == 'user'), None)
        if last_user:
            validate_input(last_user.content)
        return v


class GenerateIn(BaseModel):
    prompt:      str
    temperature: float | None = DEFAULT_TEMPERATURE

    @field_validator('prompt')
    @classmethod
    def validate_prompt(cls, v):
        return validate_input(v)


class DocumentIn(BaseModel):
    text:  str
    title: str | None = None

    @field_validator('text')
    @classmethod
    def validate_text(cls, v):
        if not v or not v.strip():
            raise ValueError("Document text cannot be empty")
        if len(v) > 10_000:
            raise ValueError("Document too long. Max 10,000 characters")
        return v.strip()


# ── Health / Metrics ──────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {
        "ok":          bool(OPENROUTER_API_KEY),
        "model":       MODEL,
        "embed_model": EMBEDDINGS_MODEL,
        "provider":    "openrouter",
        "model_ready": bool(OPENROUTER_API_KEY),
        "kb_size":     len(_product_store),
    }


@app.get("/metrics")
async def metrics():
    return {**_metrics, "model": MODEL, "kb_size": len(_product_store)}


# ── Knowledge base CRUD ───────────────────────────────────────────────────────
@app.post("/documents")
async def add_document(payload: DocumentIn):
    """Manually add a product/document to the knowledge base."""
    doc_id    = uuid.uuid4().hex
    embedding = await _embed(payload.text)
    doc = {
        "id":         doc_id,
        "title":      payload.title or f"Document {len(_product_store) + 1}",
        "text":       payload.text,
        "embedding":  embedding,
        "metadata":   {},
        "source":     "manual",
        "created_at": int(time.time()),
    }
    _product_store.append(doc)
    logger.info(f"[kb] manual add: {doc['title']}")
    return {
        "id":      doc_id,
        "title":   doc["title"],
        "snippet": payload.text[:120] + ("…" if len(payload.text) > 120 else ""),
    }


@app.get("/documents")
async def list_documents():
    return [
        {
            "id":         d["id"],
            "title":      d["title"],
            "snippet":    d["text"][:120] + ("…" if len(d["text"]) > 120 else ""),
            "source":     d.get("source", "manual"),
            "created_at": d.get("created_at"),
        }
        for d in _product_store
    ]


@app.delete("/documents/{doc_id}")
async def delete_document(doc_id: str):
    global _product_store
    before = len(_product_store)
    _product_store = [d for d in _product_store if d["id"] != doc_id]
    if len(_product_store) == before:
        raise HTTPException(404, detail="Document not found")
    return {"ok": True}


# ── File upload → embed → knowledge base ─────────────────────────────────────
def _parse_upload(content: bytes, filename: str) -> list[dict]:
    """Parse CSV or Excel file and return list of row dicts."""
    ext = Path(filename).suffix.lower()

    if ext in (".xlsx", ".xls"):
        import openpyxl
        wb  = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws  = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        headers = [str(h).strip().lower() if h else "" for h in rows[0]]
        return [
            {headers[i]: (str(v).strip() if v is not None else "") for i, v in enumerate(row)}
            for row in rows[1:]
            if any(v is not None for v in row)
        ]
    else:
        text    = content.decode("utf-8-sig", errors="replace")
        reader  = csv.DictReader(io.StringIO(text))
        return [{k.strip().lower(): (v.strip() if v else "") for k, v in row.items()} for row in reader]


def _row_to_product_text(row: dict) -> tuple[str, str]:
    """Convert a row dict to (title, full_text) for embedding."""
    title    = row.get("title") or row.get("name") or row.get("product") or "Unknown Product"
    brand    = row.get("brand") or row.get("manufacturer") or ""
    category = row.get("category") or row.get("type") or ""
    price    = row.get("price") or ""
    rating   = row.get("rating") or ""
    desc     = row.get("description") or row.get("desc") or row.get("details") or ""
    features = row.get("features") or row.get("specs") or ""

    parts = [f"{title}"]
    if brand:    parts.append(f"by {brand}")
    if category: parts.append(f"Category: {category}")
    if price:    parts.append(f"Price: {price}")
    if rating:   parts.append(f"Rating: {rating}/5")
    if desc:     parts.append(desc)
    if features: parts.append(f"Features: {features}")

    return title, "\n".join(parts)


@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """
    Upload a CSV or Excel file of products.
    Each row is embedded and added to the knowledge base.
    Returns a streaming JSON-lines response so the client can show progress.
    """
    if not OPENROUTER_API_KEY:
        raise HTTPException(503, detail="OpenRouter API key not configured.")

    allowed = {".csv", ".xlsx", ".xls"}
    ext     = Path(file.filename or "").suffix.lower()
    if ext not in allowed:
        raise HTTPException(400, detail=f"Unsupported file type '{ext}'. Upload a CSV or Excel file.")

    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(400, detail="File too large. Max 5 MB.")

    try:
        rows = _parse_upload(content, file.filename)
    except Exception as e:
        raise HTTPException(400, detail=f"Could not parse file: {e}")

    if not rows:
        raise HTTPException(400, detail="File is empty or has no data rows.")
    if len(rows) > 500:
        raise HTTPException(400, detail="Too many rows. Max 500 per upload.")

    async def stream_progress():
        added, skipped = 0, 0
        total = len(rows)

        yield json.dumps({"status": "started", "total": total}) + "\n"

        for i, row in enumerate(rows):
            title, text = _row_to_product_text(row)
            if not text.strip():
                skipped += 1
                continue
            try:
                embedding = await _embed(text)
                doc = {
                    "id":         uuid.uuid4().hex,
                    "title":      title,
                    "text":       text,
                    "embedding":  embedding,
                    "metadata":   {
                        "brand":    row.get("brand", ""),
                        "category": row.get("category", ""),
                        "price":    row.get("price", ""),
                        "rating":   row.get("rating", ""),
                        "features": row.get("features", "").split(";"),
                        "availability": row.get("availability", "In Stock"),
                    },
                    "source":     "upload",
                    "created_at": int(time.time()),
                }
                _product_store.append(doc)

                # Write to Boltic (non-blocking — failure doesn't abort the upload)
                boltic_record = {
                    "id":           doc["id"],
                    "title":        title,
                    "brand":        row.get("brand", ""),
                    "category":     row.get("category", ""),
                    "price":        row.get("price", ""),
                    "rating":       row.get("rating", ""),
                    "description":  row.get("description") or row.get("desc") or "",
                    "features":     row.get("features", ""),
                    "availability": row.get("availability", "In Stock"),
                    "embedding":    embedding,
                    "created_at":   doc["created_at"],
                }
                await _boltic_create_product(boltic_record)

                added += 1
                yield json.dumps({"status": "progress", "done": i + 1, "total": total, "title": title}) + "\n"
            except Exception as e:
                skipped += 1
                logger.warning(f"[upload] failed to embed row {i}: {e}")
                yield json.dumps({"status": "progress", "done": i + 1, "total": total, "title": title, "error": str(e)}) + "\n"

        yield json.dumps({"status": "done", "added": added, "skipped": skipped, "total": total}) + "\n"

    return StreamingResponse(stream_progress(), media_type="application/x-ndjson")


# ── /ask/stream — tool-calling RAG pipeline ───────────────────────────────────
@app.post("/ask/stream")
async def ask_stream(request: Request, payload: AskIn):
    """
    Multi-turn streaming chat with tool calling.

    Flow:
      1. Send message + tools to LLM (non-streaming, fast ~200ms)
      2. If LLM calls search_products → search local KB
         If LLM calls fetch_from_fynd  → call Fynd API (+ auto-cache result)
         If LLM answers directly       → skip tools
      3. Stream final answer back to client

    SSE events:
      {"type": "tool_call",  "tool": "search_products", "query": "..."}
      {"type": "tool_result","tool": "search_products", "found": true}
      {"type": "token",      "content": "..."}
      {"type": "done",       "ttft_ms": 123}
      {"type": "error",      "message": "..."}
    """
    req_id = uuid.uuid4().hex[:8]
    logger.info(f"[{req_id}] Stream: {len(payload.messages)} msgs | tools={payload.use_tools}")
    _metrics["requests_total"] += 1

    if not OPENROUTER_API_KEY:
        async def no_key():
            yield f"data: {json.dumps({'type': 'error', 'message': 'OpenRouter API key not configured.'})}\n\n"
        return StreamingResponse(no_key(), media_type="text/event-stream")

    personality = payload.personality or SYSTEM_PERSONALITY
    history     = trim_to_context([{"role": m.role, "content": m.content} for m in payload.messages])
    messages    = [{"role": "system", "content": personality}] + history
    temperature = _resolve_temperature(payload.temperature)

    async def event_stream():
        request_start = time.monotonic()

        try:
            # ── Turn 1: tool decision (non-streaming, fast) ───────────────────
            tool_messages = list(messages)  # copy

            # Speculative embedding: start embedding the user's last message NOW,
            # in parallel with the Turn-1 tool-decision call. If the LLM decides
            # to call search_products the embedding is already done — saving one
            # sequential round-trip (~200–400 ms). Discarded if not needed.
            last_user_content = next(
                (m["content"] for m in reversed(tool_messages) if m["role"] == "user"), ""
            )
            speculative_embed_task: asyncio.Task | None = None
            if payload.use_tools and last_user_content:
                speculative_embed_task = asyncio.create_task(_embed(last_user_content))

            # Small random jitter to spread concurrent requests on OpenRouter
            await asyncio.sleep(0.005 + 0.015 * (hash(req_id) % 10) / 10)

            if payload.use_tools:
                # Multi-round tool loop — allows search → recommend in sequence
                for _round in range(3):  # max 3 rounds to prevent infinite loops
                    r1 = await _http_client.post(
                        OPENROUTER_URL,
                        headers=_headers(),
                        json={
                            "model":       MODEL,
                            "messages":    tool_messages,
                            "tools":       TOOLS,
                            "tool_choice": "auto",
                            "temperature": temperature,
                            "max_tokens":  128,  # tool call JSON ≈ 50 tokens; 128 is plenty
                            "stream":      False,
                        },
                    )
                    if r1.status_code == 401:
                        yield f"data: {json.dumps({'type': 'error', 'message': 'Invalid API key.'})}\n\n"
                        return
                    if r1.status_code == 429:
                        # Retry once after a short back-off before giving up
                        if _round == 0:
                            logger.warning(f"[{req_id}] Turn-1 rate limited — retrying in 2s")
                            await asyncio.sleep(2)
                            continue
                        yield f"data: {json.dumps({'type': 'error', 'message': 'Rate limit exceeded. Please try again in a moment.'})}\n\n"
                        return
                    if r1.status_code != 200:
                        try:
                            err_body = r1.json()
                            err_msg  = err_body.get("error", {}).get("message") or f"Model error ({r1.status_code})."
                        except Exception:
                            err_msg  = f"Model error ({r1.status_code})."
                        logger.error(f"[{req_id}] Turn-1 error {r1.status_code}: {err_msg}")
                        yield f"data: {json.dumps({'type': 'error', 'message': err_msg})}\n\n"
                        return
                    choice = r1.json()["choices"][0]

                    if choice.get("finish_reason") != "tool_calls":
                        if _round == 0:
                            _metrics["tool_calls_direct"] += 1
                        break  # LLM is done calling tools — proceed to stream

                    tool_calls = choice["message"].get("tool_calls", [])
                    tool_messages.append(choice["message"])

                    for tc in tool_calls:
                        fn_name = tc["function"]["name"]
                        fn_args = json.loads(tc["function"]["arguments"])
                        arg     = fn_args.get("product_id") or fn_args.get("query", "")

                        logger.info(f"[{req_id}] tool call (round {_round+1}): {fn_name}({arg!r})")
                        yield f"data: {json.dumps({'type': 'tool_call', 'tool': fn_name, 'query': arg})}\n\n"

                        # Inject the speculative embedding on the first search_products call
                        # so it doesn't need to make another embed API call.
                        if fn_name == "search_products" and speculative_embed_task is not None:
                            try:
                                precomputed = await speculative_embed_task
                                speculative_embed_task = None  # consume once
                                fn_args["_precomputed_embedding"] = precomputed
                                logger.info(f"[{req_id}] speculative embed hit — skipped embed round-trip")
                            except Exception as e:
                                logger.warning(f"[{req_id}] speculative embed failed: {e}")
                                speculative_embed_task = None

                        result = await TOOL_MAP[fn_name](**fn_args) if fn_name in TOOL_MAP else {"error": f"Unknown tool: {fn_name}"}

                        if fn_name == "search_products":
                            _metrics["tool_calls_rag"] += 1
                        elif fn_name == "get_recommendations":
                            _metrics["tool_calls_recs"] += 1

                        yield f"data: {json.dumps({'type': 'tool_result', 'tool': fn_name, 'found': result.get('found', False)})}\n\n"

                        tool_messages.append({
                            "role":         "tool",
                            "tool_call_id": tc["id"],
                            "content":      json.dumps(result),
                        })
            else:
                _metrics["tool_calls_direct"] += 1
                tool_messages = messages
                # No tool was called — cancel the speculative embed to avoid wasting the call
                if speculative_embed_task is not None and not speculative_embed_task.done():
                    speculative_embed_task.cancel()

            # ── Turn 2: stream final answer ───────────────────────────────────
            first_token = True

            async with _http_client.stream(
                "POST", OPENROUTER_URL,
                headers=_headers(),
                json={
                    "model":       MODEL,
                    "messages":    tool_messages,
                    "temperature": temperature,
                    "max_tokens":  DEFAULT_MAX_TOKENS,
                    "stream":      True,
                },
            ) as r2:
                if r2.status_code == 401:
                    yield f"data: {json.dumps({'type': 'error', 'message': 'Invalid API key.'})}\n\n"
                    return
                if r2.status_code == 429:
                    yield f"data: {json.dumps({'type': 'error', 'message': 'Rate limit exceeded.'})}\n\n"
                    return
                if r2.status_code != 200:
                    yield f"data: {json.dumps({'type': 'error', 'message': f'Model error ({r2.status_code}).'})}\n\n"
                    return

                async for line in r2.aiter_lines():
                    if await request.is_disconnected():
                        _metrics["requests_cancelled"] += 1
                        return

                    if not line.startswith("data: "):
                        continue
                    raw = line[6:].strip()
                    if raw == "[DONE]":
                        break

                    try:
                        chunk = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    delta   = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    finish  = chunk.get("choices", [{}])[0].get("finish_reason")

                    if content:
                        if first_token:
                            first_token = False
                            _update_ttft((time.monotonic() - request_start) * 1000)
                        _metrics["total_tokens_streamed"] += 1
                        yield f"data: {json.dumps({'type': 'token', 'content': content})}\n\n"

                    if finish:
                        ttft_ms = round((time.monotonic() - request_start) * 1000)
                        yield f"data: {json.dumps({'type': 'done', 'ttft_ms': ttft_ms})}\n\n"
                        return

            ttft_ms = round((time.monotonic() - request_start) * 1000)
            yield f"data: {json.dumps({'type': 'done', 'ttft_ms': ttft_ms})}\n\n"

        except httpx.RequestError as e:
            logger.error(f"[{req_id}] connection error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': 'Cannot connect to model service.'})}\n\n"
        except Exception as e:
            logger.error(f"[{req_id}] unexpected error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': 'An unexpected error occurred.'})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":       "keep-alive",
        },
    )


# ── /generate (non-streaming, no tools) ──────────────────────────────────────
@app.post("/generate")
async def generate(payload: GenerateIn):
    req_id = uuid.uuid4().hex[:8]
    _metrics["requests_total"] += 1

    if not OPENROUTER_API_KEY:
        raise HTTPException(503, detail="OpenRouter API key not configured.")

    try:
        r = await _http_client.post(
            OPENROUTER_URL,
            headers=_headers(),
            json={
                "model":       MODEL,
                "messages":    [{"role": "user", "content": payload.prompt}],
                "temperature": _resolve_temperature(payload.temperature),
                "max_tokens":  DEFAULT_MAX_TOKENS,
                "stream":      False,
            },
        )
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"].strip()
        return {"response": text or "No response.", "model": MODEL}
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            raise HTTPException(503, detail="Invalid API key.")
        if e.response.status_code == 429:
            raise HTTPException(429, detail="Rate limit exceeded.")
        raise HTTPException(503, detail="Model service temporarily unavailable.")
    except httpx.RequestError as e:
        logger.error(f"[{req_id}] connection error: {e}")
        raise HTTPException(503, detail="Cannot connect to model service.")


# ── Static frontend ───────────────────────────────────────────────────────────
_dist = Path(__file__).parent / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="assets")

    @app.get("/", include_in_schema=False)
    async def serve_root():
        return FileResponse(_dist / "index.html")

    @app.get("/{filename}", include_in_schema=False)
    async def serve_static_files(filename: str):
        file_path = _dist / filename
        if file_path.is_file() and file_path.name != "index.html":
            return FileResponse(file_path)
        return FileResponse(_dist / "index.html")


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=PORT, reload=False)
