# Fynd AI — Application Documentation

## Overview

Fynd AI is a product shopping assistant built for the Fynd catalog. Users can search for products, get recommendations, and have natural conversations powered by GPT-4o-mini via OpenRouter. Products are stored in Boltic Tables with vector embeddings for semantic search.

---

## Architecture

```
Browser (React SPA)
       │
       │  SSE / HTTP
       ▼
FastAPI Backend (Python)
       │
       ├── OpenRouter API  ─── GPT-4o-mini (chat + tool calling)
       │                  ─── text-embedding-3-small (embeddings)
       │
       └── Boltic Tables
               ├── Products Table  (title, brand, category, price, rating, embedding)
               └── Recs Table      (pre-computed recommendations — currently unused)
```

**Deployment:** Docker container on Boltic Serverless (asia-south1)
- 8 CPU cores, 32 GB RAM
- 6 uvicorn workers (`WEB_CONCURRENCY=6`)
- URL: `https://llama-qwen-e5b9730c.serverless.boltic.app`

---

## Tech Stack

| Layer     | Technology                          |
|-----------|-------------------------------------|
| Frontend  | React 18, Vite, react-markdown      |
| Backend   | FastAPI, Python 3.11, uvicorn       |
| AI        | OpenRouter → GPT-4o-mini            |
| Embeddings| OpenRouter → text-embedding-3-small |
| Database  | Boltic Tables (vector store)        |
| Container | Docker (multi-stage build)          |
| Hosting   | Boltic Serverless                   |

---

## How It Works

### 1. Product Storage (Boltic Tables)

Products are stored in a Boltic Table with the following fields:

| Field         | Type   | Description                                      |
|---------------|--------|--------------------------------------------------|
| `id`          | UUID   | Auto-generated unique ID                         |
| `title`       | String | Product name                                     |
| `brand`       | String | Brand name                                       |
| `category`    | String | Product category                                 |
| `price`       | String | Price (e.g. `$49.99`)                            |
| `rating`      | String | Rating out of 5                                  |
| `description` | String | Product description                              |
| `features`    | String | Semicolon-separated feature list                 |
| `availability`| String | Stock status                                     |
| `embedding`   | Vector | 1536-dimensional float vector (text-embedding-3-small) |

On every server startup, all products are fetched from Boltic and loaded into **in-memory store** (`_product_store`). Each of the 6 workers independently loads the full catalog.

---

### 2. Vector Search (RAG)

When a user asks about products, the app uses Retrieval-Augmented Generation:

```
User query
    │
    ▼
Embed query with text-embedding-3-small
    │
    ▼
Cosine similarity against all product embeddings in memory
    │
    ├── Score ≥ 0.50 threshold → return top-5 results
    │
    └── No results → keyword fallback (title + brand + category match)
```

**Cosine similarity** is computed in pure Python:
```python
dot(a, b) / (norm(a) * norm(b))
```

**Speculative embedding** optimisation: the user's message is embedded in parallel with the Turn-1 tool-decision LLM call. If the LLM decides to call `search_products`, the embedding is already ready — saving one sequential round-trip (~200–400 ms).

---

### 3. Tool Calling Pipeline

The backend exposes two tools to the LLM:

#### `search_products`
Searches the product catalog using semantic similarity.

**Parameters:**
- `query` (required) — natural language search query
- `max_price` (optional) — filter by maximum price
- `min_price` (optional) — filter by minimum price

**Flow:**
1. Embed the query
2. Run cosine similarity search against `_product_store`
3. Apply price filter if provided (scans full catalog, then re-ranks by similarity)
4. Deduplicate by title
5. Return top-5 products

#### `get_recommendations`
Finds similar products using the source product's own embedding as the query vector.

**Parameters:**
- `product_id` (required) — P-ID (e.g. `P001`) or UUID

**Flow:**
1. Resolve product by ID
2. Use product's embedding as query vector
3. Score every other product by cosine similarity
4. Return same-category products first, then cross-category fill-up to top-5

---

### 4. Chat Flow (SSE Streaming)

```
POST /ask/stream
       │
       ├─ Turn 1 (non-streaming, fast ~200ms)
       │       Send messages + tools to GPT-4o-mini
       │       LLM decides: call tool OR answer directly
       │
       ├─ If tool call:
       │       Execute search_products / get_recommendations locally
       │       Append tool result to messages
       │       Repeat up to 3 rounds (allows search → recommend in sequence)
       │
       └─ Turn 2 (streaming)
               Stream final answer token-by-token to browser via SSE
```

**SSE event types:**

| Event type    | Description                              |
|---------------|------------------------------------------|
| `tool_call`   | A tool is being called (shows pill in UI)|
| `tool_result` | Tool returned results (updates pill)     |
| `token`       | A streamed text token                    |
| `done`        | Response complete, includes `ttft_ms`    |
| `error`       | An error occurred                        |

---

### 5. Context Window Management

Conversation history is trimmed to fit within `MAX_HISTORY_CHARS` (40,000 chars) by dropping oldest messages first, always preserving the last user message.

---

### 6. Frontend Architecture

```
App (state: convos, activeId, loading, personality)
 ├── Sidebar          — conversation history, grouped by date
 ├── Topbar           — title, export, settings button
 ├── Chat             — message list (Message components)
 │     └── Message
 │           ├── ToolPills  — shows which tools were called
 │           ├── Bubble     — markdown-rendered response
 │           └── Actions    — Copy button, ttft badge
 ├── Composer         — textarea, send/stop, file attach
 └── SettingsModal
       ├── Personality cards  — tone modifier (appended to system prompt)
       ├── Custom instructions — full custom system prompt override
       └── UploadPanel        — CSV/Excel product catalog import
```

**State persistence:** Conversations are saved to `localStorage` under key `ollama_conversations`. Streaming/loading messages are filtered out before saving.

**Token batching:** Incoming stream tokens are buffered and flushed to the UI every 50ms to avoid excessive React re-renders.

---

### 7. Product Upload

Users can upload a CSV or Excel file from the Settings modal:

1. File is parsed server-side (supports `.csv`, `.xlsx`, `.xls`)
2. Each row is converted to a text blob: `title + brand + category + price + rating + description + features`
3. Text is embedded via `text-embedding-3-small`
4. Product + embedding is added to `_product_store` (in memory)
5. Product is written to Boltic Products Table (persisted)
6. Progress is streamed back to the browser via NDJSON

**Limits:** 500 rows per upload, 5 MB max file size.

---

## API Reference

| Method | Path              | Description                              |
|--------|-------------------|------------------------------------------|
| GET    | `/health`         | Health check, returns model + kb_size    |
| GET    | `/metrics`        | Request counts, TTFT, token stats        |
| POST   | `/ask/stream`     | Main chat endpoint (SSE streaming)       |
| POST   | `/generate`       | Simple non-streaming completion          |
| GET    | `/documents`      | List all products in knowledge base      |
| POST   | `/documents`      | Manually add a document                  |
| DELETE | `/documents/{id}` | Remove a document                        |
| POST   | `/upload`         | Upload CSV/Excel catalog (NDJSON stream) |

---

## Environment Variables

| Variable               | Default                    | Description                          |
|------------------------|----------------------------|--------------------------------------|
| `OPENROUTER_API_KEY`   | —                          | **Required.** OpenRouter API key     |
| `MODEL`                | `openai/gpt-4o-mini`       | Chat model                           |
| `EMBEDDINGS_MODEL`     | `openai/text-embedding-3-small` | Embedding model                 |
| `PORT`                 | `8080`                     | Server port                          |
| `WEB_CONCURRENCY`      | `4`                        | Number of uvicorn workers            |
| `BOLTIC_TOKEN`         | —                          | Boltic API auth token                |
| `BOLTIC_PRODUCTS_TABLE`| —                          | Products table UUID                  |
| `BOLTIC_RECS_TABLE`    | —                          | Recommendations table UUID           |
| `RAG_SIMILARITY_THRESHOLD` | `0.50`                | Min cosine similarity for search     |
| `RAG_TOP_K`            | `5`                        | Max products returned per search     |
| `DEFAULT_TEMPERATURE`  | `0.45`                     | LLM temperature                      |
| `DEFAULT_MAX_TOKENS`   | `2048`                     | Max tokens per response              |
| `MAX_INPUT_LENGTH`     | `2000`                     | Max user input characters            |
| `MAX_HISTORY_CHARS`    | `40000`                    | Max conversation history characters  |
| `ALLOWED_ORIGINS`      | `http://localhost:5173,...` | CORS allowed origins                |

---

## Local Development

### Backend

```bash
cd Backend
pip install -r requirements.txt
# create Backend/.env with OPENROUTER_API_KEY etc.
uvicorn app:app --host 0.0.0.0 --port 8080 --reload
```

Health check: `curl http://localhost:8080/health`

### Frontend

```bash
cd Frontend
npm install
npm run dev   # starts at http://localhost:5173
```

The frontend proxies to `http://localhost:8080` by default (`VITE_API_URL` env var).

### Docker (full stack)

```bash
docker build -t fynd-ai .
docker run -p 8080:8080 \
  -e OPENROUTER_API_KEY=sk-or-... \
  -e BOLTIC_TOKEN=... \
  fynd-ai
```

---

## Deployment (Boltic Serverless)

The app deploys automatically on every push to the `boltic` git remote:

```bash
git push boltic main
```

Boltic reads `boltic.yaml` for resource config, builds the Docker image, and deploys to `asia-south1`.

**Environment variables** that contain secrets (`OPENROUTER_API_KEY`) must be set manually in the Boltic console under **Settings → Environment variables**.

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| In-memory vector store | No external vector DB dependency; 105 products × 1536 dims = ~2.5 MB, trivially fits in RAM |
| Speculative embedding | Embeds user query in parallel with Turn-1 LLM call, saving ~200–400 ms per request |
| Multi-worker uvicorn | 6 workers on 8 cores; each worker independently loads from Boltic on startup |
| Boltic as persistence | Products survive server restarts; all workers stay in sync via startup reload |
| SSE over WebSocket | Simpler infrastructure, works through any HTTP proxy, sufficient for one-way streaming |
| Pure Python cosine sim | Avoids numpy/scipy dependency; fast enough for 105–1000 products |
