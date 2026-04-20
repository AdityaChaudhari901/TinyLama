"""OpenRouter API client — embeddings and chat completions."""
import logging
import os

import httpx

logger = logging.getLogger(__name__)

_CHAT_URL   = "https://openrouter.ai/api/v1/chat/completions"
_EMBED_URL  = "https://openrouter.ai/api/v1/embeddings"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY', '')}",
        "HTTP-Referer":  os.getenv("APP_SITE_URL", "https://fynd-ai.app"),
        "X-Title":       os.getenv("APP_TITLE", "Fynd AI"),
        "Content-Type":  "application/json",
    }


async def embed(client: httpx.AsyncClient, text: str) -> list[float]:
    """Return embedding vector for text."""
    r = await client.post(
        _EMBED_URL,
        headers=_headers(),
        json={"model": os.getenv("EMBEDDINGS_MODEL", "openai/text-embedding-3-small"), "input": text},
    )
    r.raise_for_status()
    return r.json()["data"][0]["embedding"]


async def chat(
    client: httpx.AsyncClient,
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    tools: list[dict] | None = None,
    tool_choice: str = "auto",
    stream: bool = False,
) -> httpx.Response:
    """Send a chat completion request. Returns the raw httpx Response."""
    payload: dict = {
        "model":       model or os.getenv("MODEL", "openai/gpt-4o-mini"),
        "messages":    messages,
        "temperature": temperature or float(os.getenv("DEFAULT_TEMPERATURE", "0.45")),
        "max_tokens":  max_tokens or int(os.getenv("DEFAULT_MAX_TOKENS", "2048")),
        "stream":      stream,
    }
    if tools:
        payload["tools"]       = tools
        payload["tool_choice"] = tool_choice

    return await client.post(_CHAT_URL, headers=_headers(), json=payload)
