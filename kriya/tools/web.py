import ipaddress
import logging
import re
import socket
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# The URL fetch_url_text() fetches always comes from an untrusted external
# source (a live web-search result via search_web(), or a redirect target
# reached from one) - Kriya doesn't control what that URL points at. Without
# these bounds, a compromised/misconfigured search backend or a crafted
# redirect chain could make Kriya's own process fetch an internal service or
# a cloud metadata endpoint (e.g. 169.254.169.254) and feed the response
# into an LLM prompt as "reference material," or a malicious/huge response
# body could exhaust memory (the request timeout below only bounds time, not
# bytes). Found live, 2026-08-12 (SME architecture review).
_MAX_REDIRECTS = 5
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MB - generous for a reference page's readable text


def _is_safe_external_url(url: str) -> bool:
    """SSRF guard for fetch_url_text(): requires an http(s) scheme and every
    DNS-resolved address for the hostname to be a real public address - fails
    closed (rejects) on any resolution failure or non-public address.

    Deliberately the OPPOSITE polarity of is_local_url() (kriya/core/llm.py):
    that function exists to ALLOW local/private addresses, since it gates
    Kriya's own LLM calls under a local_only egress policy where local IS the
    trusted target. This function exists to BLOCK local/private addresses,
    since it gates an outbound fetch of untrusted EXTERNAL content - here,
    local/private is exactly the class of target an SSRF attempt is trying
    to reach."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        addr_info = socket.getaddrinfo(hostname, None)
        for _family, _, _, _, sockaddr in addr_info:
            ip_obj = ipaddress.ip_address(sockaddr[0])
            if (
                ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local
                or ip_obj.is_reserved or ip_obj.is_multicast or ip_obj.is_unspecified
            ):
                return False
        return True
    except Exception as e:
        logger.debug(f"_is_safe_external_url check failed for '{url}', treating as unsafe (fail closed): {e}")
        return False


async def fetch_url_text(url: str, quiet_on_failure: bool = False) -> str:
    """Fetch raw HTML from a URL and extract clean, readable text.

    Manually follows redirects (rather than httpx's own follow_redirects)
    specifically to re-run the SSRF guard against EACH hop's target, not
    just the original URL - a redirect chain that starts at a safe external
    URL and ends at an internal one would otherwise bypass the guard
    entirely. Streams the response body and enforces _MAX_RESPONSE_BYTES
    against actual bytes read, not just a (spoofable, or absent under
    chunked encoding) Content-Length header.

    quiet_on_failure: found live, 2026-08-16 - a failed fetch always logged
    at ERROR with a full traceback (exc_info=True), correct for the 3
    call sites where the caller fetches ONE deliberate, user-specified URL
    (kriya learn, workflow.py's two "user-supplied reference URL" sites) and
    a failure there IS worth surfacing loudly. But kriya/workflow/
    live_lookup.py tries a whole LIST of search-result candidates and moves
    on the moment one fails - there, a single candidate 403ing (e.g.
    mvnrepository.com blocking scraper-looking User-Agents, unrelated to
    Kriya) is routine and already re-logged at DEBUG by that caller right
    after catching this function's re-raised exception - the loud
    ERROR+traceback from in here had already been written by that point
    regardless, alarming to read for something that isn't a problem. Default
    False (unchanged behavior for every existing caller) - only
    live_lookup.py's candidate loop opts in."""
    if not _is_safe_external_url(url):
        raise ValueError(f"Refusing to fetch '{url}': not a safe external http(s) URL.")
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        current_url = url
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            for _ in range(_MAX_REDIRECTS):
                async with client.stream("GET", current_url, headers=headers) as resp:
                    if resp.is_redirect:
                        location = resp.headers.get("location")
                        if not location:
                            resp.raise_for_status()
                            break
                        next_url = str(httpx.URL(current_url).join(location))
                        if not _is_safe_external_url(next_url):
                            raise ValueError(f"Refusing to follow redirect to '{next_url}': not a safe external http(s) URL.")
                        current_url = next_url
                        continue

                    resp.raise_for_status()
                    content_length = resp.headers.get("content-length")
                    if content_length and int(content_length) > _MAX_RESPONSE_BYTES:
                        raise ValueError(f"Response from '{current_url}' exceeds the {_MAX_RESPONSE_BYTES}-byte fetch limit.")

                    chunks = []
                    total = 0
                    async for chunk in resp.aiter_bytes():
                        total += len(chunk)
                        if total > _MAX_RESPONSE_BYTES:
                            raise ValueError(f"Response from '{current_url}' exceeded the {_MAX_RESPONSE_BYTES}-byte fetch limit while streaming.")
                        chunks.append(chunk)
                    html = b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")
                    break
            else:
                raise ValueError(f"Too many redirects (>{_MAX_REDIRECTS}) while fetching '{url}'.")

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
        if quiet_on_failure:
            logger.debug(f"Failed to fetch content from URL '{url}': {e}")
        else:
            logger.error(f"Failed to fetch content from URL '{url}': {e}", exc_info=True)
        raise ValueError(f"HTTP fetch failed for {url}: {e}") from e
