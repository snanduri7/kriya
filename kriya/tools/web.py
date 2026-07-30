import logging
import re

import httpx

logger = logging.getLogger(__name__)

async def fetch_url_text(url: str) -> str:
    """Fetch raw HTML from a URL and extract clean, readable text."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            html = resp.text
            
            # 1. Remove script blocks
            html = re.sub(r'<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>', ' ', html, flags=re.IGNORECASE|re.DOTALL)
            
            # 2. Remove style blocks
            html = re.sub(r'<style\b[^<]*(?:(?!<\/style>)<[^<]*)*<\/style>', ' ', html, flags=re.IGNORECASE|re.DOTALL)
            
            # 3. Strip general HTML tags
            text = re.sub(r'<[^>]+>', ' ', html)
            
            # 4. Clean up spacing and HTML entities
            text = re.sub(r'\s+', ' ', text)
            text = text.replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
            
            return text.strip()
    except Exception as e:
        logger.error(f"Failed to fetch content from URL '{url}': {e}", exc_info=True)
        raise ValueError(f"HTTP fetch failed for {url}: {e}") from e
