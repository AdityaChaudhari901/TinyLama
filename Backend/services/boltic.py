"""Boltic Tables API client — fetch and persist product records."""
import logging
import os

import httpx

logger = logging.getLogger(__name__)

_API_BASE  = "https://api.boltic.fynd.com/asia-south1/service/panel/boltic-tables/v1/tables"
_PAGE_SIZE = 100


def _headers() -> dict:
    return {
        "x-boltic-token": os.getenv("BOLTIC_TOKEN", ""),
        "Content-Type": "application/json",
    }


async def fetch_all(client: httpx.AsyncClient, table_id: str) -> list[dict]:
    """Fetch all records from a Boltic table, paginating automatically."""
    url     = f"{_API_BASE}/{table_id}/records/list"
    records: list[dict] = []
    page_no = 1

    while True:
        try:
            r = await client.post(
                url,
                headers=_headers(),
                json={
                    "page": {"page_no": page_no, "page_size": _PAGE_SIZE},
                    "sort": [{"field": "created_at", "direction": "asc"}],
                },
                timeout=30.0,
            )
            r.raise_for_status()
            body  = r.json()
            data  = body.get("data", [])
            batch = data if isinstance(data, list) else (data.get("list") or data.get("records") or [])
            if not batch:
                break
            records.extend(batch)
            if len(batch) < _PAGE_SIZE:
                break
            page_no += 1
        except Exception as e:
            logger.error("[boltic] fetch failed (table=%s page=%d): %s", table_id, page_no, e)
            break

    return records


async def create_product(client: httpx.AsyncClient, record: dict) -> bool:
    """Persist a product record to Boltic. Returns True on success."""
    table_id   = os.getenv("BOLTIC_PRODUCTS_TABLE", "")
    url        = f"{_API_BASE}/{table_id}/records"
    auto_fields = {"id", "created_at", "updated_at"}
    payload    = {k: v for k, v in record.items() if k not in auto_fields}

    try:
        r = await client.post(url, headers=_headers(), json=payload, timeout=15.0)
        r.raise_for_status()
        body = r.json()
        if body.get("error"):
            logger.warning("[boltic] record rejected for %s: %s", record.get("title"), body["error"])
            return False
        return True
    except Exception as e:
        logger.warning("[boltic] failed to write product %s: %s", record.get("title"), e)
        return False
