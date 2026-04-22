"""
Chat endpoints: /ask/stream (SSE streaming with tool calling) and /generate.
Owns the tool definitions, tool functions, and SYSTEM_PERSONALITY.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import traceback
import uuid

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator

from services import openrouter as or_client
from services.catalog import _cosine
from routers.admin import _metrics, update_ttft

logger = logging.getLogger(__name__)
router = APIRouter()


def _safe_json(obj) -> str:
    """Serialize obj to JSON, coercing any non-serializable value to str."""
    def _sanitize(v):
        if isinstance(v, dict):
            return {k: _sanitize(val) for k, val in v.items()}
        if isinstance(v, list):
            return [_sanitize(i) for i in v]
        if isinstance(v, (str, int, float, bool)) or v is None:
            return v
        return str(v)
    try:
        return json.dumps(obj)
    except (TypeError, ValueError):
        return json.dumps(_sanitize(obj))

# ── Prompt ─────────────────────────────────────────────────────────────────────
SYSTEM_PERSONALITY = os.getenv(
    "AI_PERSONALITY",
    "You are Fynd AI, a friendly shopping assistant for the Fynd product catalog. "
    "Help users find, explore, and compare products conversationally.\n\n"
    "When showing products, always include: title, brand, price, rating, availability, "
    "full description, and all features — never truncate. "
    "Only use data from tool results; never invent product details. "
    "For recommendations, use get_recommendations with the product_id field from search results. "
    "Be helpful, natural, and conversational.",
)

# ── Tool schema ────────────────────────────────────────────────────────────────
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": (
                "Search the product catalog. Call this for any product-related question — "
                "browsing, finding, comparing, or filtering products. "
                "Always pass brand and/or category when the user mentions them, regardless of phrasing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language search query, e.g. 'running shoes', 'wireless headphones'.",
                    },
                    "brand": {
                        "type": "string",
                        "description": (
                            "Brand name to filter by. Pass whenever the user mentions a brand — "
                            "Nike, Adidas, Apple, Sony, Samsung, Dell, New Balance, etc. "
                            "Works for any phrasing: 'Nike shoes', 'show all Nike', 'only Adidas'."
                        ),
                    },
                    "category": {
                        "type": "string",
                        "description": (
                            "Product category to filter by. Normalize synonyms: "
                            "phones/mobile → Smartphones, shoes/sneakers/trainers → Shoes, "
                            "earphones/earbuds → Headphones, laptop/notebook → Laptops."
                        ),
                    },
                    "max_price": {
                        "type": "number",
                        "description": "Max price. Pass for: 'under X', 'below X', 'max X', 'budget', 'cheap'.",
                    },
                    "min_price": {
                        "type": "number",
                        "description": "Min price. Pass for: 'above X', 'over X', 'premium', 'expensive'.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Results to return (default 5, max 20). Increase for 'show all' or specific counts.",
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
                "Find products similar to a specific product using embedding similarity. "
                "Use for: 'similar products', 'recommendations', 'what else', 'show me more like this'. "
                "Use the product_id field returned by search_products. "
                "Call search_products first if you don't have a product_id yet."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": "string",
                        "description": "The product_id field from a search_products result (format: P001, P002, …).",
                    }
                },
                "required": ["product_id"],
            },
        },
    },
]

# ── Tool implementations ───────────────────────────────────────────────────────

async def _search_products(
    request: Request,
    query: str,
    max_price: float | None = None,
    min_price: float | None = None,
    brand: str | None = None,
    category: str | None = None,
    top_k: int | None = None,
    _precomputed_embedding: list[float] | None = None,
) -> dict:
    logger.info("[tool:search_products] query=%r brand=%s category=%s max_price=%s", query, brand, category, max_price)
    catalog = request.app.state.catalog
    http    = request.app.state.http_client
    top_k_n = min(top_k or int(os.getenv("RAG_TOP_K", "5")), 20)
    has_filter = bool(brand or category or max_price is not None or min_price is not None)

    try:
        pool = catalog.filter_pool(brand=brand, category=category, max_price=max_price, min_price=min_price)

        # If category filter wiped the pool, retry without it — category names in the
        # catalog may not match LLM-normalized names (e.g. "Sneakers" vs "Shoes").
        # Vector search will still surface the right products semantically.
        effective_has_filter = has_filter
        if not pool and category:
            pool = catalog.filter_pool(brand=brand, category=None, max_price=max_price, min_price=min_price)
            effective_has_filter = bool(brand or max_price is not None or min_price is not None)
            if pool:
                logger.info("[tool:search_products] category=%r matched nothing — falling back to vector search", category)

        if not pool:
            return {"found": False, "products": [], "source": "knowledge_base"}

        q_emb = _precomputed_embedding or await or_client.embed(http, query)

        if effective_has_filter:
            results = catalog.filtered_search(q_emb, pool=pool, top_n=top_k_n)
        else:
            results = catalog.vector_search(q_emb, top_n=top_k_n)
            if not results:
                results = catalog.keyword_search(query)[:top_k_n]

        results = catalog.deduplicate(results)[:top_k_n]
        if not results:
            return {"found": False, "products": [], "source": "knowledge_base"}

        products = [p.to_search_result(catalog.pid_reverse) for p in results]
        logger.info("[tool:search_products] found %d results", len(products))
        return {"found": True, "products": products, "source": "knowledge_base"}

    except Exception as e:
        logger.error("[tool:search_products] error: %s", e)
        return {"found": False, "error": str(e)}


async def _get_recommendations(request: Request, product_id: str) -> dict:
    logger.info("[tool:get_recommendations] product_id=%r", product_id)
    catalog = request.app.state.catalog
    source  = catalog.get_by_id(product_id)

    if not source:
        return {"found": False, "product_id": product_id, "recommendations": []}

    # ── Phase 2: read pre-computed recommendations from Boltic recs table ────
    enriched = catalog.get_precomputed_recs(product_id)

    if enriched is not None:
        logger.info("[tool:get_recommendations] '%s' → %d pre-computed recs", source.title, len(enriched))
    else:
        # ── Fallback: live cosine similarity (for products uploaded after seeding) ──
        logger.info("[tool:get_recommendations] '%s' — no pre-computed recs, running live cosine sim", source.title)
        top_k      = int(os.getenv("RAG_TOP_K", "5"))
        source_cat = source.metadata.category.lower().strip()
        scored = sorted(
            ((_cosine(source.embedding, p.embedding), p) for p in catalog.products if p.id != source.id and p.embedding),
            key=lambda x: x[0], reverse=True,
        )
        same_cat  = [(s, p) for s, p in scored if p.metadata.category.lower().strip() == source_cat]
        cross_cat = [(s, p) for s, p in scored if p.metadata.category.lower().strip() != source_cat and s >= 0.60]
        combined  = (same_cat[:top_k] + cross_cat[: max(0, top_k - len(same_cat))])[:top_k]
        enriched  = [p.to_recommendation(score, rank, source_cat) for rank, (score, p) in enumerate(combined, 1)]

    has_same_cat = any(r["same_category"] for r in enriched)
    return {
        "found":               True,
        "product_id":          product_id,
        "source_title":        source.title,
        "same_category_found": has_same_cat,
        "recommendations":     enriched,
    }


def _make_tool_map(request: Request) -> dict:
    return {
        "search_products":     lambda **kw: _search_products(request, **kw),
        "get_recommendations": lambda **kw: _get_recommendations(request, **kw),
    }


# ── Request models ─────────────────────────────────────────────────────────────

def _validate_input(text: str) -> str:
    max_len = int(os.getenv("MAX_INPUT_LENGTH", "2000"))
    if not text or not text.strip():
        raise ValueError("Input cannot be empty")
    if len(text) > max_len:
        raise ValueError(f"Input too long. Max {max_len} characters")
    return text.strip()


def _trim_to_context(messages: list[dict]) -> list[dict]:
    max_chars = int(os.getenv("MAX_HISTORY_CHARS", "40000"))
    total, result = 0, []
    for msg in reversed(messages):
        msg_len = len(msg.get("content", ""))
        if total + msg_len > max_chars and result:
            break
        total += msg_len
        result.insert(0, msg)
    return result or (messages[-1:] if messages else [])


class ChatMessage(BaseModel):
    role:    str
    content: str


class AskIn(BaseModel):
    messages:    list[ChatMessage]
    temperature: float | None = None
    personality: str | None   = None
    use_tools:   bool          = True

    @field_validator("messages")
    @classmethod
    def validate_messages(cls, v):
        if not v:
            raise ValueError("Messages cannot be empty")
        last_user = next((m for m in reversed(v) if m.role == "user"), None)
        if last_user:
            _validate_input(last_user.content)
        return v


class GenerateIn(BaseModel):
    prompt:      str
    temperature: float | None = None

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, v):
        return _validate_input(v)


# ── Streaming chat ─────────────────────────────────────────────────────────────

@router.post("/ask/stream")
async def ask_stream(request: Request, payload: AskIn):
    req_id = uuid.uuid4().hex[:8]
    _metrics["requests_total"] += 1

    if not os.getenv("OPENROUTER_API_KEY"):
        async def no_key():
            yield f"data: {json.dumps({'type': 'error', 'message': 'OpenRouter API key not configured.'})}\n\n"
        return StreamingResponse(no_key(), media_type="text/event-stream")

    personality = payload.personality or SYSTEM_PERSONALITY
    history     = _trim_to_context([{"role": m.role, "content": m.content} for m in payload.messages])
    messages    = [{"role": "system", "content": personality}] + history
    temperature = payload.temperature or float(os.getenv("DEFAULT_TEMPERATURE", "0.45"))
    http        = request.app.state.http_client
    tool_map    = _make_tool_map(request)

    async def event_stream():
        request_start = time.monotonic()
        try:
            tool_messages = list(messages)

            # Speculative embedding — fired in parallel with Turn-1 tool decision
            last_user_msg = next((m["content"] for m in reversed(tool_messages) if m["role"] == "user"), "")
            spec_embed: asyncio.Task | None = None
            if payload.use_tools and last_user_msg:
                spec_embed = asyncio.create_task(or_client.embed(http, last_user_msg))

            await asyncio.sleep(0.005 + 0.015 * (hash(req_id) % 10) / 10)

            if payload.use_tools:
                for _round in range(3):
                    r1 = await or_client.chat(
                        http,
                        tool_messages,
                        temperature=temperature,
                        max_tokens=512,
                        tools=TOOLS,
                        tool_choice="auto",
                        stream=False,
                    )
                    if r1.status_code == 401:
                        yield f"data: {json.dumps({'type': 'error', 'message': 'Invalid API key.'})}\n\n"; return
                    if r1.status_code == 429:
                        if _round == 0:
                            await asyncio.sleep(2); continue
                        yield f"data: {json.dumps({'type': 'error', 'message': 'Rate limit exceeded.'})}\n\n"; return
                    if r1.status_code != 200:
                        try:   err = r1.json().get("error", {}).get("message") or f"Model error ({r1.status_code})."
                        except: err = f"Model error ({r1.status_code})."
                        yield f"data: {json.dumps({'type': 'error', 'message': err})}\n\n"; return

                    choice = r1.json()["choices"][0]
                    if choice.get("finish_reason") != "tool_calls":
                        if _round == 0:
                            _metrics["tool_calls_direct"] += 1
                        break

                    tool_calls = choice["message"].get("tool_calls", [])
                    tool_messages.append(choice["message"])

                    for tc in tool_calls:
                        fn_name = tc["function"]["name"]
                        try:
                            fn_args = json.loads(tc["function"]["arguments"])
                        except json.JSONDecodeError as e:
                            logger.warning("[%s] malformed tool args for %s: %s", req_id, fn_name, e)
                            continue
                        arg     = fn_args.get("product_id") or fn_args.get("query", "")

                        logger.info("[%s] tool call (round %d): %s(%r)", req_id, _round + 1, fn_name, arg)
                        yield f"data: {json.dumps({'type': 'tool_call', 'tool': fn_name, 'query': arg})}\n\n"

                        if fn_name == "search_products" and spec_embed is not None:
                            try:
                                fn_args["_precomputed_embedding"] = await spec_embed
                                spec_embed = None
                            except Exception as e:
                                logger.warning("[%s] speculative embed failed: %s", req_id, e)
                                spec_embed = None

                        fn = tool_map.get(fn_name)
                        result = await fn(**fn_args) if fn else {"error": f"Unknown tool: {fn_name}"}

                        if fn_name == "search_products":      _metrics["tool_calls_rag"] += 1
                        elif fn_name == "get_recommendations": _metrics["tool_calls_recs"] += 1

                        yield f"data: {json.dumps({'type': 'tool_result', 'tool': fn_name, 'found': result.get('found', False)})}\n\n"
                        tool_messages.append({"role": "tool", "tool_call_id": tc["id"], "content": _safe_json(result)})
            else:
                _metrics["tool_calls_direct"] += 1
                tool_messages = messages
                if spec_embed and not spec_embed.done():
                    spec_embed.cancel()

            # Turn 2: stream final answer
            first_token = True
            async with http.stream(
                "POST", "https://openrouter.ai/api/v1/chat/completions",
                headers=or_client._headers(),
                json={
                    "model":       os.getenv("MODEL", "openai/gpt-4o-mini"),
                    "messages":    tool_messages,
                    "temperature": temperature,
                    "max_tokens":  int(os.getenv("DEFAULT_MAX_TOKENS", "2048")),
                    "stream":      True,
                },
            ) as r2:
                if r2.status_code in (401, 429, ):
                    msg = "Invalid API key." if r2.status_code == 401 else "Rate limit exceeded."
                    yield f"data: {json.dumps({'type': 'error', 'message': msg})}\n\n"; return
                if r2.status_code != 200:
                    yield f"data: {json.dumps({'type': 'error', 'message': f'Model error ({r2.status_code}).'})}\n\n"; return

                async for line in r2.aiter_lines():
                    if await request.is_disconnected():
                        _metrics["requests_cancelled"] += 1; return
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        chunk = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta   = choices[0].get("delta", {})
                    content = delta.get("content", "")
                    finish  = choices[0].get("finish_reason")
                    if content:
                        if first_token:
                            first_token = False
                            update_ttft((time.monotonic() - request_start) * 1000)
                        _metrics["total_tokens_streamed"] += 1
                        yield f"data: {json.dumps({'type': 'token', 'content': content})}\n\n"
                    if finish:
                        yield f"data: {json.dumps({'type': 'done', 'ttft_ms': round((time.monotonic() - request_start) * 1000)})}\n\n"
                        return

            yield f"data: {json.dumps({'type': 'done', 'ttft_ms': round((time.monotonic() - request_start) * 1000)})}\n\n"

        except httpx.RequestError as e:
            logger.error("[%s] connection error: %s", req_id, e)
            yield f"data: {json.dumps({'type': 'error', 'message': 'Cannot connect to model service.'})}\n\n"
        except Exception as e:
            logger.error("[%s] unexpected error: %s\n%s", req_id, e, traceback.format_exc())
            yield f"data: {json.dumps({'type': 'error', 'message': 'An unexpected error occurred.'})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


# ── Simple non-streaming completion ───────────────────────────────────────────

@router.post("/generate")
async def generate(payload: GenerateIn, request: Request):
    _metrics["requests_total"] += 1
    if not os.getenv("OPENROUTER_API_KEY"):
        from fastapi import HTTPException
        raise HTTPException(503, detail="OpenRouter API key not configured.")

    http = request.app.state.http_client
    try:
        r = await or_client.chat(
            http,
            [{"role": "user", "content": payload.prompt}],
            temperature=payload.temperature,
            stream=False,
        )
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"].strip()
        return {"response": text or "No response.", "model": os.getenv("MODEL")}
    except httpx.HTTPStatusError as e:
        from fastapi import HTTPException
        if e.response.status_code == 401: raise HTTPException(503, detail="Invalid API key.")
        if e.response.status_code == 429: raise HTTPException(429, detail="Rate limit exceeded.")
        raise HTTPException(503, detail="Model service temporarily unavailable.")
    except httpx.RequestError:
        from fastapi import HTTPException
        raise HTTPException(503, detail="Cannot connect to model service.")


