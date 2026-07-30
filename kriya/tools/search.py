import logging
from typing import Dict, List

import httpx

logger = logging.getLogger(__name__)


async def search_web(query: str, base_url: str, top_k: int = 1) -> List[Dict[str, str]]:
    """Issues a query against a configured SearXNG-compatible search endpoint
    (`search.base_url`) and returns up to `top_k` results as {title, url, snippet}.

    Callers MUST only ever pass `query` strings produced by a bounded, code-level
    extraction (e.g. `extract_library_versions`) - never raw goal/design/code text.
    This function has no way to enforce that itself, but it's the reason live
    lookup's query construction lives in a single, auditable place in workflow.py
    rather than being left to model judgment."""
    if not base_url:
        return []
    try:
        params = {"q": query, "format": "json"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{base_url.rstrip('/')}/search", params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning(f"Live lookup search failed for query '{query}': {e}")
        return []

    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return []

    out = []
    for r in results[:top_k]:
        if not isinstance(r, dict) or not r.get("url"):
            continue
        out.append({
            "title": r.get("title", ""),
            "url": r["url"],
            "snippet": r.get("content", ""),
        })
    return out
