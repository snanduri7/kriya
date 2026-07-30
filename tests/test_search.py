from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kriya.tools.search import search_web


def _mock_response(json_body):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=json_body)
    return resp


@pytest.mark.asyncio
async def test_search_web_returns_results():
    body = {"results": [
        {"title": "Qpid JMS Docs", "url": "https://qpid.apache.org/releases/qpid-jms-2.10.0/", "content": "Official docs."},
        {"title": "Other", "url": "https://example.com/other", "content": "Unrelated."},
    ]}
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_mock_response(body))):
        results = await search_web("qpid-jms 2.10.0", "http://localhost:8080", top_k=1)

    assert len(results) == 1
    assert results[0]["url"] == "https://qpid.apache.org/releases/qpid-jms-2.10.0/"
    assert results[0]["title"] == "Qpid JMS Docs"

@pytest.mark.asyncio
async def test_search_web_returns_empty_without_base_url():
    results = await search_web("anything", "", top_k=1)
    assert results == []

@pytest.mark.asyncio
async def test_search_web_handles_request_failure():
    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=Exception("connection refused"))):
        results = await search_web("qpid-jms", "http://localhost:8080")
    assert results == []

@pytest.mark.asyncio
async def test_search_web_handles_malformed_response():
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_mock_response({"not_results": []}))):
        results = await search_web("qpid-jms", "http://localhost:8080")
    assert results == []

@pytest.mark.asyncio
async def test_search_web_skips_results_without_url():
    body = {"results": [{"title": "No URL", "content": "..."}]}
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_mock_response(body))):
        results = await search_web("qpid-jms", "http://localhost:8080")
    assert results == []
