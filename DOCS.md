# Fynd AI — How It Works (Step by Step)

---

## Step 1 — Server Starts Up

When the Docker container starts, `start.sh` launches **6 uvicorn worker processes** of the FastAPI app.

Each worker independently runs the startup sequence in `app.py`:

```
Worker boots
    │
    ├─ 1. Create a shared httpx.AsyncClient (100 max connections)
    │
    ├─ 2. Create a CatalogService (empty in-memory store)
    │
    ├─ 3. Fetch ALL products from Boltic Products Table
    │       → HTTP POST to Boltic Tables API, paginated 100 rows/page
    │       → Each row contains title, brand, category, price, description,
    │          features, availability, and a pre-computed 1536-dim embedding
    │       → Stored as typed Product objects in memory
    │       → P-IDs assigned: P001, P002, P003 … (short stable IDs for LLM)
    │
    ├─ 4. Fetch ALL rows from Boltic Recommendations Table
    │       → Pre-computed similarity scores seeded offline
    │       → Stored as {P001: [{recommended: P007, score: 0.94, rank: 1}, …]}
    │
    └─ 5. Start background task to repeat steps 3–4 every 5 minutes
```

After this, each worker holds the **entire product catalog in RAM** — no database call needed at query time.

---

## Step 2 — User Opens the App

The browser loads the **React SPA** served from FastAPI at `/`.

The frontend was compiled at Docker build time:
- Vite bundles React + all dependencies → `/dist/assets/index-*.js`
- FastAPI serves `/dist/index.html` for any unmatched route
- FastAPI serves `/dist/assets/*` for JS/CSS/fonts

The user sees the home screen with 4 **suggested prompts** and an empty chat.

---

## Step 3 — User Types a Message

Example: **"Show me Adidas shoes under $200"**

The frontend:
1. Adds the message to the conversation state
2. Adds a "loading" placeholder bot message (shows animated dots)
3. Sends `POST /ask/stream` with the full conversation history:

```json
{
  "messages": [
    { "role": "user", "content": "Show me Adidas shoes under $200" }
  ]
}
```

4. Opens the response as an **SSE (Server-Sent Events) stream**

---

## Step 4 — Backend Receives the Request

FastAPI routes it to `routers/chat.py → ask_stream()`.

The backend:
1. Builds the message list: `[system_prompt] + conversation_history`
2. Trims history to stay under 40,000 characters (drops oldest messages first)
3. Fires two things **in parallel** immediately:
   - **Turn 1**: Sends messages to GPT-4o-mini (non-streaming, tool_choice=auto)
   - **Speculative embed**: Embeds the user message using text-embedding-3-small

```
Both run at the same time ──────────────────────┐
                                                 │
OpenRouter /chat ──► GPT-4o-mini decides:        │
  "Should I call a tool?"                        │
                                                 │
OpenRouter /embeddings ──► [1536-dim vector]     │
  (ready by the time the LLM responds) ──────────┘
```

This parallelism saves ~300–500ms per request.

---

## Step 5 — LLM Decides to Call a Tool (Turn 1)

GPT-4o-mini reads the system prompt + conversation + tool schemas and returns a **tool call decision** (not a text answer):

```json
{
  "finish_reason": "tool_calls",
  "message": {
    "tool_calls": [{
      "function": {
        "name": "search_products",
        "arguments": "{\"query\": \"shoes\", \"brand\": \"Adidas\", \"max_price\": 200}"
      }
    }]
  }
}
```

The LLM extracted:
- `query = "shoes"` from the message
- `brand = "Adidas"` because the tool schema says "pass brand when user mentions one"
- `max_price = 200` because the tool schema says "pass for 'under X'"

The backend immediately streams a **tool_call event** to the browser:
```
data: {"type": "tool_call", "tool": "search_products", "query": "shoes"}
```

The frontend shows: **🔍 Searching catalog** (with a spinner)

---

## Step 6 — Tool Executes: search_products

The backend runs the tool locally — no extra LLM call needed:

```
search_products(query="shoes", brand="Adidas", max_price=200)
    │
    ├─ 1. filter_pool(brand="Adidas", max_price=200)
    │       → Scans in-memory catalog
    │       → Keeps only products where brand contains "adidas" (case-insensitive)
    │         AND price ≤ 200
    │       → Returns filtered pool [P002, P005, P011, …]
    │
    │       If pool is empty AND category was specified:
    │       → Retry without category (catalog may use "Sneakers" instead of "Shoes")
    │       → Falls back to vector search over brand/price filtered pool
    │
    ├─ 2. filtered_search(pre_computed_embedding, pool=filtered_pool, top_n=5)
    │       → Uses the speculative embedding from Step 4 (already computed!)
    │       → Runs cosine similarity:  dot(query_vec, product_vec) / (|a| × |b|)
    │       → Returns top 5 Adidas products sorted by semantic similarity
    │       → No similarity threshold applied (pool is already filtered)
    │
    ├─ 3. deduplicate(results)
    │       → Removes products with identical titles (normalised lowercase)
    │
    └─ 4. Build response:
            [{ id, product_id:"P002", title, details, metadata:{brand,price,…} }, …]
```

The backend streams a **tool_result event** to the browser:
```
data: {"type": "tool_result", "tool": "search_products", "found": true}
```

The frontend updates the pill: **🔍 Searching catalog ●** (green dot = results found)

---

## Step 7 — Tool Result Injected into Context (Turn 2)

The backend appends the tool result to the message history:

```
messages = [
  { role: "system",    content: "You are Fynd AI…" },
  { role: "user",      content: "Show me Adidas shoes under $200" },
  { role: "assistant", content: null, tool_calls: [{…}] },
  { role: "tool",      content: "{\"found\":true, \"products\":[{…5 products…}]}" }
]
```

Then sends this to GPT-4o-mini again — this time as a **streaming request**.

---

## Step 8 — LLM Streams the Final Answer (Turn 2)

GPT-4o-mini reads the tool results and generates a conversational response, streaming token by token.

Each token arrives as an SSE event:
```
data: {"type": "token", "content": "Here"}
data: {"type": "token", "content": " are"}
data: {"type": "token", "content": " some"}
…
data: {"type": "done", "ttft_ms": 1843}
```

---

## Step 9 — Frontend Renders the Streaming Response

The frontend processes SSE events as they arrive:

```
token event → buffered in tokenBufferRef (not rendered yet)
               ↓
every 50ms → setInterval flushes buffer to React state
               ↓
React re-renders with new text (smooth streaming effect)
```

Batching tokens every 50ms prevents React from re-rendering hundreds of times per second. Without this, typing would be janky on long responses.

The response renders as **Markdown** using `react-markdown`:
- Product names → bold headings
- Features → bullet lists  
- Prices → inline code or plain text

When `done` event arrives:
- Streaming cursor disappears
- **TTFT badge** appears (e.g. `1843ms`) — time from request to first token
- **Copy button** appears

---

## Step 10 — Conversation Saved to localStorage

After each response, the conversation is saved to `localStorage` with a 1-second debounce:

```javascript
key: "ollama_conversations"
value: [{
  id: "abc123",
  title: "Show me Adidas shoes under $200",   // ← first user message (truncated to 60 chars)
  messages: [...],
  createdAt: 1745123456789,
  updatedAt: 1745123498123
}]
```

Max 50 conversations stored. Loading/streaming messages stripped before saving.

---

## What Happens for "Show me similar products"

If the user asks for recommendations, the flow changes at **Step 5**:

```
LLM calls: get_recommendations(product_id="P002")
    │
    ├─ 1. Lookup P002 in catalog → source product
    │
    ├─ 2. Check _recs_index["P002"]
    │       → Has pre-computed recs from Boltic Recs Table?
    │
    │       YES → Return pre-seeded recommendations directly
    │              (fast, no embedding needed, scored offline by ML pipeline)
    │
    │       NO  → Live fallback:
    │              Run cosine sim of P002's embedding vs every other product
    │              Same-category products ranked first
    │              Cross-category fill-up if same-category < 5
    │
    └─ 3. Enrich each rec with full product metadata
```

---

## What Happens for "Show all shoes" (category mismatch case)

The LLM may normalise "shoes" → `category="Shoes"`. But if Boltic products are stored with `category="Sneakers"`, the filter returns zero results.

The backend handles this gracefully:

```
filter_pool(category="Shoes") → empty pool
    │
    └─ Retry: filter_pool(category=None)  ← drop category, keep brand/price filters
                │
                └─ vector_search(embedding_of_"shoes")
                        → cosine similarity finds shoe products semantically
                        → returns Sneakers, Running Shoes, etc.
```

---

## What Happens During File Upload

User uploads a CSV from the Settings modal:

```
POST /upload (multipart/form-data)
    │
    ├─ 1. Parse file → list of row dicts
    │       Supports: .csv (UTF-8), .xlsx, .xls
    │       Max: 500 rows, 5 MB
    │
    ├─ 2. For each row:
    │       Build text blob: "Nike Air Max by Nike | Category: Shoes | $150 | 4.5/5 | …"
    │       embed(text) → [1536 floats]          ← OpenRouter API call
    │       catalog.add(Product)                 ← added to in-memory store
    │       boltic.create_product(record)         ← persisted to Boltic Table
    │       yield {"status":"progress", "done":N, "total":M}
    │
    └─ 3. yield {"status":"done", "added":N, "skipped":M}

Browser streams NDJSON progress:
  → Shows "Embedding 3/10" with progress bar
  → Shows "✓ 10 products added"
```

Uploaded products are searchable immediately (added to in-memory catalog). They also survive server restarts because they were persisted to Boltic and will be reloaded at next startup.

---

## What Happens Every 5 Minutes (Background Reload)

The periodic reload task in `app.py` keeps the catalog fresh:

```
asyncio.sleep(300)  ← every 5 minutes
    │
    ├─ catalog.reload(http_client)
    │       → Fetch all products from Boltic
    │       → Build new list + new P-ID index
    │       → async with lock:
    │           self._store = new_list        ← atomic swap (GIL-safe)
    │           self._pid_index = new_index
    │
    └─ catalog.load_recs(http_client)
            → Re-fetch recommendations table
            → Atomic swap of _recs_index
```

While the reload runs, all reads continue uninterrupted — they see the previous `_store` snapshot until the swap completes.

---

## What Happens When You Call /admin/reload

Triggers an immediate catalog reload without waiting 5 minutes:

```
POST /admin/reload
  Header: X-Admin-Key: <ADMIN_API_KEY>
    │
    └─ Same as periodic reload, returns:
       {"ok": true, "before": 47, "after": 52}
```

Useful after adding products to Boltic directly (without the upload endpoint).

---

## Full Request Timeline

```
t=0ms     User sends "Show me Adidas shoes under $200"

t=5ms     Backend fires embed() and Turn 1 chat() in parallel

t=200ms   Embedding returns [1536 floats] ← ready and waiting

t=450ms   Turn 1 returns: tool_call search_products(brand=Adidas, max_price=200)
          → SSE: {"type":"tool_call"} → browser shows 🔍 spinner

t=452ms   filter_pool() + filtered_search() run in memory (<1ms)
          → SSE: {"type":"tool_result", "found":true} → browser shows green dot

t=453ms   Turn 2 streaming request sent to OpenRouter

t=900ms   First token arrives
          → SSE: {"type":"token", "content":"Here"} → browser starts rendering
          TTFT = 900ms

t=3200ms  Last token + done event
          → SSE: {"type":"done", "ttft_ms":900}
          → TTFT badge shows "900ms"
```

---

## Summary of All Components

| Component | File | Role |
|-----------|------|------|
| Entry point | `app.py` | Wires everything, runs lifespan startup, serves frontend |
| Chat pipeline | `routers/chat.py` | SSE streaming, tool calling, speculative embedding |
| Catalog engine | `services/catalog.py` | In-memory vector store, search, reload |
| LLM + embed | `services/openrouter.py` | HTTP calls to OpenRouter API |
| Data persistence | `services/boltic.py` | HTTP calls to Boltic Tables API |
| Admin routes | `routers/admin.py` | /health, /metrics, /admin/reload |
| Upload pipeline | `routers/upload.py` | CSV/Excel → embed → catalog → Boltic |
| Manual KB edit | `routers/documents.py` | Add/list/delete single documents |
| Data models | `models.py` | Product + ProductMetadata dataclasses |
| Frontend | `Frontend/src/App.jsx` | Entire React UI in one file |
| Deploy config | `boltic.yaml` | Env vars, resources, scaling |
| Container | `Dockerfile` | 2-stage: Node build → Python runtime |
