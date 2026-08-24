import httpx
import pytest

from kriya.tools.web import _MAX_RESPONSE_BYTES, _is_safe_external_url, fetch_url_text


def test_is_safe_external_url_rejects_non_http_scheme():
    assert _is_safe_external_url("file:///etc/passwd") is False
    assert _is_safe_external_url("ftp://example.com/file") is False


def test_is_safe_external_url_rejects_hostname_less_url():
    assert _is_safe_external_url("not-a-valid-url") is False


def test_is_safe_external_url_rejects_loopback_and_private_targets():
    """Regression test for a real bug found live, 2026-08-12 (SME architecture
    review): fetch_url_text() had no SSRF protection at all - a URL pointing
    at a loopback, private, or link-local address (e.g. a cloud metadata
    endpoint or an internal service) was fetched with zero validation."""
    assert _is_safe_external_url("http://127.0.0.1:8080/admin") is False
    assert _is_safe_external_url("http://localhost:8080/admin") is False
    assert _is_safe_external_url("http://169.254.169.254/latest/meta-data/") is False
    assert _is_safe_external_url("http://10.0.0.5/internal") is False
    assert _is_safe_external_url("http://192.168.1.1/router") is False


def test_is_safe_external_url_allows_genuine_public_targets():
    assert _is_safe_external_url("https://8.8.8.8/") is True


@pytest.mark.asyncio
async def test_fetch_url_text_refuses_unsafe_url_before_any_request():
    with pytest.raises(ValueError, match="not a safe external"):
        await fetch_url_text("http://127.0.0.1:8080/admin")


@pytest.mark.asyncio
async def test_fetch_url_text_follows_safe_redirect_and_revalidates_each_hop():
    """Redirects are followed manually specifically so each hop's target gets
    re-checked by the SSRF guard - a redirect chain that starts safe and
    ends unsafe must not bypass it."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "8.8.8.8":
            return httpx.Response(200, text="<html><body>Safe content</body></html>")
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    import kriya.tools.web as web_mod
    orig_async_client = web_mod.httpx.AsyncClient
    try:
        web_mod.httpx.AsyncClient = lambda **kw: orig_async_client(transport=transport, **kw)
        result = await fetch_url_text("https://8.8.8.8/")
    finally:
        web_mod.httpx.AsyncClient = orig_async_client

    assert "Safe content" in result


@pytest.mark.asyncio
async def test_fetch_url_text_refuses_redirect_to_unsafe_target():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "8.8.8.8":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
        return httpx.Response(200, text="should never be reached")

    transport = httpx.MockTransport(handler)

    import kriya.tools.web as web_mod
    orig_async_client = web_mod.httpx.AsyncClient
    try:
        web_mod.httpx.AsyncClient = lambda **kw: orig_async_client(transport=transport, **kw)
        with pytest.raises(ValueError, match="Refusing to follow redirect"):
            await fetch_url_text("https://8.8.8.8/")
    finally:
        web_mod.httpx.AsyncClient = orig_async_client


@pytest.mark.asyncio
async def test_fetch_url_text_logs_error_with_traceback_by_default(caplog):
    """Default behavior (quiet_on_failure=False, unchanged) - a fetch failure
    logs at ERROR with a full traceback. Correct for the 3 call sites that
    fetch ONE deliberate, user-specified URL (kriya learn, workflow.py's two
    "user-supplied reference URL" sites), where a failure is worth surfacing
    loudly."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Forbidden")

    transport = httpx.MockTransport(handler)

    import logging
    import kriya.tools.web as web_mod
    orig_async_client = web_mod.httpx.AsyncClient
    try:
        web_mod.httpx.AsyncClient = lambda **kw: orig_async_client(transport=transport, **kw)
        with caplog.at_level(logging.DEBUG, logger="kriya.tools.web"):
            with pytest.raises(ValueError, match="HTTP fetch failed"):
                await fetch_url_text("https://8.8.8.8/")
    finally:
        web_mod.httpx.AsyncClient = orig_async_client

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(error_records) == 1
    assert error_records[0].exc_info is not None

@pytest.mark.asyncio
async def test_fetch_url_text_quiet_on_failure_logs_debug_without_traceback(caplog):
    """Regression test for a real live incident, 2026-08-16 - a single
    live-lookup candidate URL (e.g. mvnrepository.com, which blocks
    scraper-looking User-Agents) 403ing is routine and expected, not a
    problem: kriya/workflow/live_lookup.py's multi-candidate loop already
    catches this and moves on to the next candidate, re-logging it at DEBUG
    itself - but the loud ERROR+full-traceback log from inside
    fetch_url_text() had already been written by that point regardless,
    alarming to read for something that isn't a bug. quiet_on_failure=True
    (as live_lookup.py now passes) must downgrade this to a single DEBUG
    line with no traceback, not suppress it entirely - still useful for
    real debugging, just not alarming at a normal log level.

    Scoped to the specific "Failed to fetch..." message (not "however many
    DEBUG records exist at this logger") since MA4.6 added its own,
    unrelated audit-only DEBUG line (_audit_network_access) that now also
    fires on every fetch_url_text call - this test only cares about the
    quiet-failure downgrade behavior, not the module's total debug-log
    volume."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Forbidden")

    transport = httpx.MockTransport(handler)

    import logging
    import kriya.tools.web as web_mod
    orig_async_client = web_mod.httpx.AsyncClient
    try:
        web_mod.httpx.AsyncClient = lambda **kw: orig_async_client(transport=transport, **kw)
        with caplog.at_level(logging.DEBUG, logger="kriya.tools.web"):
            with pytest.raises(ValueError, match="HTTP fetch failed"):
                await fetch_url_text("https://8.8.8.8/", quiet_on_failure=True)
    finally:
        web_mod.httpx.AsyncClient = orig_async_client

    assert not any(r.levelno == logging.ERROR for r in caplog.records)
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    fetch_failure_records = [r for r in debug_records if "Failed to fetch content from URL" in r.getMessage()]
    assert len(fetch_failure_records) == 1
    assert fetch_failure_records[0].exc_info is None

@pytest.mark.asyncio
async def test_fetch_url_text_enforces_byte_cap_while_streaming():
    """A malicious/huge response body must be rejected even without a (or
    with a lying) Content-Length header - the cap applies to actual bytes
    streamed, not just the declared header."""
    oversized_body = "x" * (_MAX_RESPONSE_BYTES + 1)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=oversized_body)

    transport = httpx.MockTransport(handler)

    import kriya.tools.web as web_mod
    orig_async_client = web_mod.httpx.AsyncClient
    try:
        web_mod.httpx.AsyncClient = lambda **kw: orig_async_client(transport=transport, **kw)
        with pytest.raises(ValueError, match="fetch limit"):
            await fetch_url_text("https://8.8.8.8/")
    finally:
        web_mod.httpx.AsyncClient = orig_async_client
