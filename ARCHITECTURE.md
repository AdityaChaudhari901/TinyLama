# Fynd AI — Architecture & Technical Reference

## Overview

Fynd AI is a production shopping assistant for the Fynd product catalog. Users chat naturally to find, filter, compare, and get recommendations for products. The system uses a **RAG (Retrieval-Augmented Generation)** pipeline: it embeds user queries, finds relevant products via cosine similarity, and feeds the results to an LLM to generate a conversational answer.

**Live URL:** https://llama-qwen-e5b9730c.serverless.boltic.app  
**Deployment:** Boltic Serverless (Docker, 8 CPU / 32 GB RAM)  
**LLM Provider:** OpenRouter → `openai/gpt-4o-mini`  
**Embedding Model:** OpenRouter → `openai/text-embedding-3-small` (1536-dim vectors)

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              USER'S BROWSER                                  │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │  React SPA  (Vite + React 18)                                          │ │
│  │                                                                        │ │
│  │  Sidebar          Chat Area             Settings Modal                 │ │
│  │  ├─ Conversation  ├─ Message list       ├─ Personality selector        │ │
│  │  │  history       │  (Markdown)         ├─ Custom system prompt        │ │
│  │  ├─ Search chats  ├─ Tool pills         └─ Upload Panel (CSV/Excel)    │ │
│  │  └─ New chat btn  │  (🔍 Searching…)                                   │ │
│  │                   ├─ TTFT badge                                        │ │
│  │                   ├─ Retry button                                      │ │
│  │                   └─ Export to Markdown                                │ │
│  └──────────────────────────┬───────────────────────────────────────────┘  │
│                             │  SSE stream / JSON                            │
└─────────────────────────────┼───────────────────────────────────────────────┘
                              │  POST /ask/stream
                              │  POST /upload
                              │  GET  /documents
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         FASTAPI APPLICATION                                  │
│                     (uvicorn, WEB_CONCURRENCY=6 workers)                     │
│                                                                              │
│  app.py ─ lifespan, CORS, static serving                                     │
│                                                                              │
│  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────────┐   │
│  │  routers/chat.py  │  │ routers/upload.py │  │ routers/documents.py  │   │
│  │                   │  │                   │  │                       │   │
│  │  POST /ask/stream │  │  POST /upload     │  │  POST   /documents    │   │
│  │  POST /generate   │  │  (CSV/Excel)      │  │  GET    /documents    │   │
│  │                   │  │  – parse file     │  │  DELETE /documents/id │   │
│  │  Turn 1:          │  │  – embed rows     │  └───────────────────────┘   │
│  │  Tool decision    │  │  – add to catalog │                               │
│  │  (gpt-4o-mini,    │  │  – persist Boltic │  ┌───────────────────────┐   │
│  │   max_tokens=512) │  │  – stream NDJSON  │  │  routers/admin.py     │   │
│  │                   │  └───────────────────┘  │                       │   │
│  │  Turn 2:          │                          │  GET  /health         │   │
│  │  Stream answer    │                          │  GET  /metrics        │   │
│  │  (SSE tokens)     │                          │  POST /admin/reload   │   │
│  │                   │                          │  (X-Admin-Key auth)   │   │
│  └────────┬──────────┘                          └───────────────────────┘   │
│           │                                                                  │
│           │  uses                                                            │
│           ▼                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                     services/catalog.py                               │   │
│  │                     CatalogService (in-memory vector store)           │   │
│  │                                                                       │   │
│  │  _store: list[Product]       ← atomic snapshot (GIL-safe)            │   │
│  │  _pid_index: P001 → uuid     ← stable short IDs for LLM              │   │
│  │  _recs_index: P001 → [recs]  ← pre-computed recommendations          │   │
│  │  _lock: asyncio.Lock         ← serialises reloads                    │   │
│  │                                                                       │   │
│  │  vector_search()   – cosine sim, threshold 0.50                      │   │
│  │  filtered_search() – cosine sim within filtered pool, no threshold   │   │
│  │  keyword_search()  – full-text, data-driven stop words               │   │
│  │  filter_pool()     – brand / category / price hard filter            │   │
│  │  get_precomputed_recs() – serves pre-seeded recommendations          │   │
│  └──────────────┬──────────────────────────────────────────────────────┘   │
│                 │                                                            │
│   ┌─────────────┴──────────────┐                                            │
│   │  services/openrouter.py    │   services/boltic.py                       │
│   │  embed(text) → [float]     │   fetch_all(table_id) → list[dict]         │
│   │  chat(messages) → Response │   create_product(record) → bool            │
│   └─────────────────────────── ┘                                            │
└──────────────────────────┬───────────────────────┬─────────────────────────┘
                           │                       │
                           ▼                       ▼
             ┌─────────────────────┐   ┌───────────────────────────┐
             │   OpenRouter API    │   │      Boltic Tables API    │
             │                     │   │                           │
             │  /embeddings        │   │  Products table           │
             │  → text-embedding   │   │  (embeddings + metadata)  │
             │    -3-small         │   │                           │
             │                     │   │  Recommendations table    │
             │  /chat/completions  │   │  (pre-computed recs)      │
             │  → gpt-4o-mini      │   └───────────────────────────┘
             └─────────────────────┘
```

---

## Request Lifecycle: "Show me Adidas shoes"

```
Browser                    FastAPI                  OpenRouter           Boltic
  │                           │                         │                  │
  │  POST /ask/stream          │                         │                  │
  │  {messages: [...]}         │                         │                  │
  ├──────────────────────────►│                         │                  │
  │                           │                         │                  │
  │                           │── embed("Adidas shoes") ►│                  │
  │                           │◄── [1536 floats] ────────│                  │
  │                           │  (speculative, parallel) │                  │
  │                           │                         │                  │
  │                           │── Turn 1 chat ──────────►│                  │
  │                           │   (tool_choice=auto,     │                  │
  │                           │    max_tokens=512)        │                  │
  │                           │◄── tool_call: ───────────│                  │
  │                           │    search_products(       │                  │
  │                           │      query="shoes",       │                  │
  │                           │      brand="Adidas")      │                  │
  │                           │                         │                  │
  │◄── {type:"tool_call"} ────│                         │                  │
  │    (🔍 Searching…)         │                         │                  │
  │                           │                         │                  │
  │                           │  filter_pool(brand=Adidas) → [5 products]  │
  │                           │  filtered_search(embedding, pool) → top 5  │
  │                           │                         │                  │
  │◄── {type:"tool_result"} ──│                         │                  │
  │    (found: true)           │                         │                  │
  │                           │                         │                  │
  │                           │── Turn 2 stream ────────►│                  │
  │                           │   (tool result in ctx)   │                  │
  │◄── {type:"token"} × N ────│◄── SSE tokens ───────────│                  │
  │◄── {type:"done"} ─────────│                         │                  │
```

---

## Two-Turn Tool Pipeline (chat.py)

Every `/ask/stream` request runs a **2-turn pipeline**:

### Turn 1 — Tool Decision (non-streaming, max 512 tokens)
The LLM decides whether to call a tool and extracts parameters. `tool_choice="auto"` lets it decide. Up to 3 rounds if the LLM wants to chain tools (e.g. search then recommend).

**Speculative embedding**: The user's query is embedded in parallel with Turn 1 so the result is ready by the time the tool call decision arrives — saving one network round trip.

### Turn 2 — Final Answer (streaming)
Tool results are injected into the message history. The LLM streams its markdown response back as SSE `token` events.

### Tool Schema
```
search_products(query, brand?, category?, max_price?, min_price?, top_k?)
  → { found, products: [{id, product_id, title, details, metadata}] }

get_recommendations(product_id)
  → { found, recommendations: [{product_id, title, score, rank, same_category}] }
```

The parameter descriptions in the schema carry the routing logic — e.g. "shoes/sneakers → Shoes", "only Adidas" → brand="Adidas". This is more reliable than system prompt rules.

---

## In-Memory Vector Store (catalog.py)

Products are loaded from Boltic into a Python list on startup and refreshed every 5 minutes.

### Search Strategy
```
User query
    │
    ├─ Has brand/category/price filter?
    │   YES → filter_pool() → filtered pool
    │          │
    │          ├─ Category matched nothing?
    │          │   YES → retry without category (LLM may use wrong synonym)
    │          │          → vector_search() on full/brand pool
    │          │
    │          └─ Category matched → filtered_search() (no threshold, within pool)
    │
    └─ NO filter → vector_search() (cosine sim ≥ 0.50 threshold)
                   │
                   └─ No results → keyword_search() fallback
                                   (dynamic stop words from catalog vocabulary)
```

### Cosine Similarity
Pure Python — no NumPy dependency. Fast enough for catalogs up to ~5,000 products on a single worker.

```python
dot / (|a| × |b|)   — computed with zip() and sum()
```

### Atomic Reload
```python
async with self._lock:
    self._store     = new_products   # GIL-safe snapshot swap
    self._pid_index = new_pid_index
```
Reads never lock — `_store = new_list` is atomic in CPython due to the GIL.

### P-ID System
Products get stable short IDs (`P001`, `P002`, …) for LLM context. Shorter than raw UUIDs, reduces token usage, and the LLM passes them back to `get_recommendations`.

---

## Pre-Computed Recommendations (Phase 2)

At startup, Fynd AI loads a separate Boltic table (`BOLTIC_RECS_TABLE`) containing pre-seeded similarity scores calculated offline (e.g. by your ML pipeline).

```
Boltic Recs Table row:
  product_id          → P001
  recommended_product_id → P007
  score               → 0.94
  rank                → 1
```

When `get_recommendations(P001)` is called:
1. Check `_recs_index[P001]` — if pre-computed entries exist, serve them directly (fast, no embedding needed).
2. If not found (new product uploaded after seeding), fall back to **live cosine similarity** across all catalog products.

---

## Data Models (models.py)

```python
@dataclass
class Product:
    id: str                    # UUID from Boltic
    title: str
    text: str                  # Concatenated searchable text (title + brand + desc + features)
    embedding: list[float]     # 1536-dim vector
    metadata: ProductMetadata
    source: str                # "boltic" | "manual" | "upload"

@dataclass
class ProductMetadata:
    brand: str
    category: str
    price: str
    rating: str
    description: str
    features: list[str] | str
    availability: str
```

---

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/ask/stream` | — | SSE chat with tool calling |
| `POST` | `/generate` | — | Simple non-streaming completion |
| `GET` | `/health` | — | Liveness + kb_size |
| `GET` | `/metrics` | — | Request counts, TTFT, token stats |
| `POST` | `/admin/reload` | `X-Admin-Key` | Force catalog reload |
| `POST` | `/upload` | `X-Admin-Key` | Upload CSV/Excel product catalog |
| `POST` | `/documents` | — | Add a single document to KB |
| `GET` | `/documents` | — | List all documents |
| `DELETE` | `/documents/:id` | — | Remove a document |
| `GET` | `/` | — | Serves React SPA |

---

## SSE Event Protocol

Frontend reads `POST /ask/stream` as a Server-Sent Events stream. Each line is `data: <JSON>\n\n`.

| Event type | Payload | Frontend action |
|------------|---------|-----------------|
| `tool_call` | `{tool, query}` | Show 🔍 pill with spinner |
| `tool_result` | `{tool, found}` | Update pill (green dot / grey dot) |
| `token` | `{content}` | Append to message, flush every 50ms |
| `done` | `{ttft_ms}` | Mark streaming complete, show TTFT badge |
| `error` | `{message}` | Show error bubble with Retry button |

---

## Upload Flow (upload.py)

```
Browser uploads CSV/Excel
        │
        ▼
_parse_file() → list[dict]        (handles .csv, .xlsx, .xls; strips BOM)
        │
        ▼
For each row (max 500):
  _row_to_text() → (title, searchable_text)
        │
        ├── embed(text) → [1536 floats]          OpenRouter API
        │
        ├── Product() → catalog.add()            in-memory add
        │
        └── boltic_client.create_product()       persist to Boltic
        │
        ▼
Stream NDJSON progress to frontend
  {"status": "progress", "done": N, "total": M, "title": "..."}
  {"status": "done", "added": N, "skipped": M}
```

---

## Frontend Architecture (App.jsx)

```
App
├── Sidebar
│   ├── Conversation list (grouped: Today / Yesterday / Past 7 days / Older)
│   ├── Search input (filters by title and message content)
│   └── Delete with confirm UI
│
├── Workspace
│   ├── Topbar (title, Export button, Settings button)
│   ├── Home screen (logo + suggested prompts)
│   └── Chat area
│       ├── Message list
│       │   └── Message
│       │       ├── ToolPills (tool_call events → running/found/miss states)
│       │       ├── Markdown renderer (react-markdown + remark-gfm)
│       │       ├── Streaming cursor
│       │       ├── Copy button
│       │       ├── Retry button (on error)
│       │       └── TTFT badge
│       ├── Scroll-to-bottom button
│       └── Composer
│           ├── Auto-resize textarea (max 200px)
│           ├── Attach button
│           ├── Char counter (warn at 1200, danger at 1800)
│           └── Send / Stop button
│
└── SettingsModal
    ├── Personality selector (helpful / creative / technical / casual / professional)
    ├── Custom system prompt textarea
    └── UploadPanel (CSV/Excel drag-drop with progress bar)
```

### State Management
Conversations are stored in `localStorage` (key: `ollama_conversations`, max 50). Messages with `loading` or `streaming` flags are stripped before saving. A 1-second debounce prevents excessive writes.

### Token Streaming
Tokens arrive as SSE events and are batched in a `ref` buffer, flushed to state every **50ms** via `setInterval`. This prevents React from re-rendering on every single token while keeping the UI visually smooth.

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENROUTER_API_KEY` | ✅ | API key for OpenRouter (LLM + embeddings) |
| `BOLTIC_TOKEN` | ✅ | Auth token for Boltic Tables API |
| `BOLTIC_PRODUCTS_TABLE` | ✅ | UUID of the products table in Boltic |
| `BOLTIC_RECS_TABLE` | ✅ | UUID of the recommendations table |
| `MODEL` | — | LLM model ID (default: `openai/gpt-4o-mini`) |
| `EMBEDDINGS_MODEL` | — | Embedding model ID (default: `openai/text-embedding-3-small`) |
| `PORT` | — | Server port (default: `8080`) |
| `WEB_CONCURRENCY` | — | Uvicorn worker count (default: `4`) |
| `DEFAULT_TEMPERATURE` | — | LLM temperature (default: `0.45`) |
| `DEFAULT_MAX_TOKENS` | — | Max tokens for Turn 2 answer (default: `2048`) |
| `MAX_INPUT_LENGTH` | — | Max user message chars (default: `2000`) |
| `MAX_HISTORY_CHARS` | — | Max conversation context chars (default: `40000`) |
| `RAG_TOP_K` | — | Default results to return (default: `5`) |
| `RAG_SIMILARITY_THRESHOLD` | — | Cosine sim cutoff for vector search (default: `0.50`) |
| `KB_RELOAD_INTERVAL` | — | Seconds between catalog reloads (default: `300`) |
| `ADMIN_API_KEY` | — | If set, protects `/admin/reload` and `/upload` |
| `AI_PERSONALITY` | — | Override default system prompt |
| `ALLOWED_ORIGINS` | — | CORS origins (default: localhost dev ports) |

---

## File Structure

```
├── Dockerfile               Multi-stage build (Node 20 Alpine → Python 3.11-slim)
├── boltic.yaml              Boltic Serverless deploy config (env vars, scaling)
├── start.sh                 Entrypoint — launches uvicorn with WEB_CONCURRENCY
│
├── Backend/
│   ├── app.py               FastAPI app: lifespan, CORS, static serving, router wiring
│   ├── models.py            Product + ProductMetadata dataclasses
│   │
│   ├── services/
│   │   ├── catalog.py       CatalogService — in-memory vector store, atomic reload
│   │   ├── openrouter.py    embed() and chat() wrappers for OpenRouter API
│   │   └── boltic.py        fetch_all() and create_product() for Boltic Tables
│   │
│   └── routers/
│       ├── chat.py          /ask/stream, /generate — tool pipeline, SSE streaming
│       ├── admin.py         /health, /metrics, /admin/reload
│       ├── documents.py     /documents CRUD
│       └── upload.py        /upload — CSV/Excel catalog ingestion
│
└── Frontend/
    ├── src/
    │   ├── App.jsx          Entire React SPA (single file)
    │   ├── App.css          All styles
    │   └── index.css        CSS variables, resets
    ├── index.html
    └── package.json         Vite + React 18 + ReactMarkdown + SyntaxHighlighter
```

---

## Deployment (Boltic Serverless)

```
git push boltic main
```

Boltic detects the push, builds the Docker image, and redeploys. The `boltic.yaml` drives:

- **Build**: `docker build -f Dockerfile .`
- **Runtime**: `./start.sh` → `uvicorn app:app --workers 6`
- **Scaling**: min=1 max=1 instance, AutoStop=false
- **Resources**: 8 CPU, 32 GB RAM
- **Health check**: `GET /health` every 30s

### Docker Build (2-stage)
```
Stage 1 — frontend-builder (node:20-alpine)
  npm install && npm run build → /app/frontend/dist

Stage 2 — runtime (python:3.11-slim)
  pip install requirements.txt
  COPY app.py, models.py, services/, routers/
  COPY --from=frontend-builder dist/ → ./dist/
  CMD ./start.sh
```

---

## Observability

### `/health`
```json
{
  "ok": true,
  "model": "openai/gpt-4o-mini",
  "embed_model": "openai/text-embedding-3-small",
  "provider": "openrouter",
  "model_ready": true,
  "kb_size": 47
}
```

### `/metrics`
```json
{
  "requests_total": 120,
  "tool_calls_rag": 95,
  "tool_calls_recs": 18,
  "tool_calls_direct": 7,
  "requests_cancelled": 2,
  "avg_ttft_ms": 1843.5,
  "ttft_samples": 118,
  "total_tokens_streamed": 42300,
  "model": "openai/gpt-4o-mini",
  "kb_size": 47
}
```

`avg_ttft_ms` is an exponential moving average (α=0.1) of time-to-first-token.

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| In-memory vector store (no Pinecone/Weaviate) | Catalog fits in RAM; eliminates external DB latency (~2–5ms vs ~50–100ms) |
| Two-turn pipeline (tool call + streaming) | Lets the LLM decide whether to search; avoids wasted embedding calls on conversational messages |
| Speculative embedding | Embeds in parallel with Turn 1 — eliminates one sequential round trip when a tool call is made |
| P-ID short IDs (P001…) | Shorter than UUIDs in the LLM context window; reduces token usage for tool calls |
| Tool schema carries routing logic | More reliable than system prompt rules — LLM follows schema descriptions more consistently |
| Category fallback (no strict filter) | Product categories in Boltic ("Sneakers") may not match LLM normalizations ("Shoes"); vector search as fallback ensures results surface |
| _safe_json serializer | Boltic fields may contain non-JSON-serializable types (datetime, custom objects); coerce to string rather than crash |
| asyncio.Lock for reloads | Prevents duplicate concurrent reloads without blocking reads (atomic snapshot swap is GIL-safe) |
| 50ms token flush batching | Prevents React re-renders on every SSE token while keeping streaming feel |
