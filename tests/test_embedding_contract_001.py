"""EMBEDDING-CONTRACT-001: the embedding side of the provider contract.

Measured on Ollama 0.34.4 + nomic-embed-text before the change: `/api/embed`
with `truncate: false` refuses an over-context input (HTTP 400 "the input
length exceeds the context length"), while `/api/embed` without it and
`/v1/embeddings` (Kriya's endpoint then) answer 200 from a silently truncated
prompt; and every embedding failure became an all-zero vector that was stored
and queried. These tests pin the contract: E1 no fake vectors, E2 no silent
truncation, E3 trustworthy identity, E4 transport/deadline rules, plus query
degradation and the doctor row."""
import asyncio
import dataclasses
import math
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from _fake_embedding import StaticEmbedder, fake_fingerprint, seed_index

from kriya.analyzer.analyzer import RepositoryAnalyzer
from kriya.config import AppConfig
from kriya.core.llm import INFERENCE_DEADLINE_EXHAUSTED, EgressViolationError, InferenceDeadlineError
from kriya.memory.embedding import (
    EMBEDDING_IDENTITY_CHANGED,
    EmbeddedSegment,
    EmbeddingInputTooLongError,
    EmbeddingMalformedResponseError,
    EmbeddingUnavailableError,
    OllamaEmbeddingClient,
    embed_chunks,
    native_root,
)
from kriya.memory.vector import LocalVectorStore
from kriya.workflow.context_budget import RetrievalLimits
from kriya.workflow.graph_retrieval import retrieve_graph_context

REFUSED = "the input length exceeds the context length"


def _client():
    return OllamaEmbeddingClient(base_url="http://localhost:11434/v1", model="nomic-embed-text:latest",
                                 egress_policy="local_only")


def _response(status=200, payload=None, text=""):
    response = MagicMock()
    response.status_code = status
    response.text = text
    response.json.return_value = payload if payload is not None else {}
    return response


class _Server:
    """A scripted /api/embed: refuses inputs longer than ``max_chars`` the
    way Ollama does with truncate:false, else one vector per input."""

    def __init__(self, max_chars=10_000, dimension=4, status_script=()):
        self.max_chars, self.dimension = max_chars, dimension
        self.status_script = list(status_script)
        self.requests = []

    async def post(self, url, json=None, **_kwargs):
        self.requests.append((url, json))
        if len(self.requests) > 20_000:  # an unbounded caller fails here instead of hanging
            raise AssertionError("embedding requests are not bounded")
        if self.status_script:
            outcome = self.status_script.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            if outcome != 200:
                return _response(outcome, text="server error")
        if any(len(text) > self.max_chars for text in json["input"]):
            return _response(400, text=f'{{"error":"{REFUSED}"}}')
        return _response(payload={"embeddings": [[1.0] + [0.5] * (self.dimension - 1) for _ in json["input"]]})


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- E2: one native endpoint, truncate:false

def test_requests_go_to_the_native_endpoint_with_truncate_false():
    server = _Server()
    with patch("httpx.AsyncClient.post", new=server.post):
        _run(_client().embed(["a", "b"]))
    [(url, body)] = server.requests
    assert url == "http://localhost:11434/api/embed" and body["truncate"] is False
    assert body["input"] == ["search_document: a", "search_document: b"]
    assert native_root("http://localhost:11434/v1/") == "http://localhost:11434"


def test_a_provider_refusal_is_input_too_long_and_never_a_legacy_fallback():
    server = _Server(max_chars=5)
    with patch("httpx.AsyncClient.post", new=server.post), pytest.raises(EmbeddingInputTooLongError):
        _run(_client().embed(["x" * 50]))
    assert [url for url, _ in server.requests] == ["http://localhost:11434/api/embed"]  # one request, one endpoint


def test_an_over_long_chunk_is_segmented_at_line_boundaries_keeping_its_identity():
    server = _Server(max_chars=60)
    client = _client()
    chunk = {"text": "\n".join(f"line {i:02d} of the chunk" for i in range(8)), "start": 10, "end": 17}
    with patch("httpx.AsyncClient.post", new=server.post):
        segments = _run(embed_chunks(client, [{"text": "short", "start": 1, "end": 1}, chunk], served_context=10_000))
    first, *rest = segments
    assert (first.parent_chunk, first.segment_index, first.text) == (0, 0, "short")
    assert [s.parent_chunk for s in rest] == [1] * len(rest) and len(rest) > 1
    assert [s.segment_index for s in rest] == list(range(len(rest)))
    assert all((s.start_line, s.end_line) == (10, 17) for s in rest)  # the parent's exact source span
    assert "".join(s.text.replace("\n", "") for s in rest) == chunk["text"].replace("\n", "")  # nothing dropped
    assert client.admission_misses >= 1  # the local estimate accepted it; the provider refused


def test_segmentation_is_deterministic():
    chunk = {"text": "\n".join(f"line {i:02d} of the chunk" for i in range(8)), "start": 1, "end": 8}

    def segment_once():
        with patch("httpx.AsyncClient.post", new=_Server(max_chars=60).post):
            return [(s.segment_index, s.text) for s in _run(embed_chunks(_client(), [chunk], served_context=10_000))]

    assert segment_once() == segment_once()


def test_an_estimated_over_long_chunk_is_split_before_any_request():
    server = _Server()
    client = _client()
    text = "\n".join("y" * 30 for _ in range(4))
    with patch("httpx.AsyncClient.post", new=server.post):
        segments = _run(embed_chunks(client, [{"text": text, "start": 1, "end": 4}], served_context=20))
    assert all(len(body["input"][0]) <= 20 * 2 + len("search_document: ") for _, body in server.requests)
    assert len(segments) == 4 and client.admission_misses == 0


def test_segmentation_progress_is_bounded():
    """Even a provider that refuses everything ends in a typed error, quickly
    (a bounded number of halvings, never a loop)."""
    work = embed_chunks(_client(), [{"text": "a single unsplittable line", "start": 1, "end": 1}],
                        served_context=10_000)
    with patch("httpx.AsyncClient.post", new=_Server(max_chars=0).post), \
         pytest.raises(EmbeddingInputTooLongError):
        _run(work)


# ---------------------------------------------------------------- E1: validated vectors, never a substitute

@pytest.mark.parametrize(("payload", "reason"), [
    ({"embeddings": [[0.1, 0.2]]}, "expected 2 embeddings"),  # count
    ({"embeddings": [[0.1, 0.2], [0.1]]}, "dimension"),
    ({"embeddings": [[0.1, math.nan], [0.1, 0.2]]}, "non-finite"),
    ({"embeddings": [[0.0, 0.0], [0.1, 0.2]]}, "zero norm"),
    ({"data": [{"embedding": [0.1, 0.2]}]}, "expected 2 embeddings"),  # the /v1 shape is not accepted
])
def test_every_response_is_validated(payload, reason):
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=_response(payload=payload))), \
         pytest.raises(EmbeddingMalformedResponseError, match=reason):
        _run(_client().embed(["a", "b"]))


def test_a_failed_call_is_a_typed_error_never_a_zero_vector():
    failing = AsyncMock(side_effect=httpx.ConnectError("down"))
    with patch("httpx.AsyncClient.post", new=failing), pytest.raises(EmbeddingUnavailableError):
        _run(_client().get_embedding("anything"))


def test_a_file_is_published_whole_or_its_old_vectors_stop_being_current(tmp_path):
    store = LocalVectorStore(str(tmp_path / "vector_index.db"))
    fingerprint = seed_index(store, [("A.java", "class A { old() }", [1.0, 0.0])])
    store.publish_file("A.java", [EmbeddedSegment(0, 0, 1, 1, "class A { new() }", [0.0, 1.0])],
                       source_digest="rev2", fingerprint=fingerprint.digest)
    rows = store.conn.execute("SELECT text, source_digest, generation, is_current FROM vector_chunks").fetchall()
    assert rows == [("class A { new() }", "rev2", 2, 1)]  # replaced in one transaction
    store.mark_stale("A.java")
    assert store.query([0.0, 1.0], fingerprint=fingerprint.digest) == []  # stored, never current
    assert store.query_lexical("new") == []  # nor its old text
    assert store.conn.execute("SELECT COUNT(*) FROM vector_chunks").fetchone()[0] == 1
    store.close()


# ---------------------------------------------------------------- E3: identity

def test_every_identity_field_changes_the_fingerprint():
    base = fake_fingerprint(768)
    for field in dataclasses.fields(base):
        value = getattr(base, field.name)
        changed = dataclasses.replace(base, **{field.name: value + 1 if isinstance(value, int) else value + "x"})
        assert changed.digest != base.digest, field.name


def test_vectors_of_another_identity_are_never_queried_as_current(tmp_path):
    store = LocalVectorStore(str(tmp_path / "vector_index.db"))
    seed_index(store, [("A.java", "class A", [1.0, 0.0])], fake_fingerprint(2, "model-a"))
    assert store.query([1.0, 0.0], fingerprint=fake_fingerprint(2, "model-a").digest)
    assert store.query([1.0, 0.0], fingerprint=fake_fingerprint(2, "model-b").digest) == []
    store.close()


@pytest.mark.asyncio
async def test_an_identity_change_rebuilds_the_index_under_the_served_identity(tmp_path):
    """No --force to remember: an index of another identity is dropped and
    re-embedded in full through the normal path; the two never coexist."""
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")
    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
    analyzer = RepositoryAnalyzer(str(tmp_path))
    first = await analyzer.index_repository(cfg, embedding_client=StaticEmbedder([1.0, 0.0], "model-a"),
                                            generate_conventions_skill=False)
    assert first.embedding_rebuilt_from is None and first.indexed == 2
    rebuilt = await analyzer.index_repository(cfg, embedding_client=StaticEmbedder([0.0, 1.0], "model-b"),
                                              generate_conventions_skill=False)
    assert rebuilt.fingerprint != first.fingerprint and rebuilt.embedding_rebuilt_from == first.fingerprint
    assert rebuilt.indexed == 2 and not rebuilt.failed  # every file, not only changed ones
    store = LocalVectorStore(os.path.join(cfg.paths.memory, "vector_index.db"))
    try:
        assert store.active_fingerprint() == rebuilt.fingerprint
        assert {r[0] for r in store.conn.execute("SELECT DISTINCT fingerprint FROM vector_chunks")} == {
            rebuilt.fingerprint}
        assert store.query([1.0, 0.0], fingerprint=first.fingerprint) == []
    finally:
        store.close()
    again = await analyzer.index_repository(cfg, embedding_client=StaticEmbedder([0.0, 1.0], "model-b"),
                                            generate_conventions_skill=False)
    assert again.embedding_rebuilt_from is None and again.indexed == 0  # now current: nothing re-embedded


@pytest.mark.asyncio
async def test_an_interrupted_identity_rebuild_never_leaves_a_mixed_index(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")
    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
    analyzer = RepositoryAnalyzer(str(tmp_path))
    first = await analyzer.index_repository(cfg, embedding_client=StaticEmbedder([1.0, 0.0], "model-a"),
                                            generate_conventions_skill=False)

    class FailsOnB(StaticEmbedder):
        async def embed(self, texts, *, is_query=False, deadline=None):
            if any("y = 2" in t for t in texts):
                raise EmbeddingUnavailableError("endpoint down")
            return await super().embed(texts, is_query=is_query, deadline=deadline)

    partial = await analyzer.index_repository(cfg, embedding_client=FailsOnB([0.0, 1.0], "model-b"),
                                              generate_conventions_skill=False)
    assert partial.embedding_rebuilt_from == first.fingerprint and list(partial.failed) == ["b.py"]
    store = LocalVectorStore(os.path.join(cfg.paths.memory, "vector_index.db"))
    try:
        rows = store.conn.execute("SELECT filepath, fingerprint FROM vector_chunks").fetchall()
        assert {fp for _, fp in rows} == {partial.fingerprint} and {p for p, _ in rows} == {"a.py"}
    finally:
        store.close()
    resumed = await analyzer.index_repository(cfg, embedding_client=StaticEmbedder([0.0, 1.0], "model-b"),
                                              generate_conventions_skill=False)
    assert resumed.embedding_rebuilt_from is None and resumed.indexed == 1 and not resumed.failed


@pytest.mark.asyncio
async def test_a_pre_contract_index_is_re_embedded_in_full(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
    os.makedirs(cfg.paths.memory)
    legacy = LocalVectorStore(os.path.join(cfg.paths.memory, "vector_index.db"))
    legacy.add_document("a.py", "x = 1", [0.0, 0.0], model_name="old", dimensions=2)  # an old zero vector
    legacy.file_metadata["a.py"] = {"mtime": os.path.getmtime(tmp_path / "a.py"), "hash": "x"}
    legacy.close()
    report = await RepositoryAnalyzer(str(tmp_path)).index_repository(
        cfg, embedding_client=StaticEmbedder([1.0, 0.0]), generate_conventions_skill=False)
    assert report.indexed == 1
    store = LocalVectorStore(os.path.join(cfg.paths.memory, "vector_index.db"))
    assert store.conn.execute("SELECT COUNT(*) FROM vector_chunks WHERE fingerprint IS NULL").fetchone()[0] == 0
    store.close()


@pytest.mark.real_embedding_identity
def test_the_fingerprint_is_measured_from_the_served_model():
    tags = {"models": [{"name": "nomic-embed-text:latest", "digest": "abc123"}]}
    show = {"model_info": {"nomic-bert.context_length": 2048}}

    async def get(_client, url, **_kwargs):
        assert url.endswith("/api/tags")
        return _response(payload=tags)

    async def post(_client, url, json=None, **_kwargs):
        if url.endswith("/api/show"):
            return _response(payload=show)
        return _response(payload={"embeddings": [[0.5, 0.5, 0.5] for _ in json["input"]]})

    with patch("httpx.AsyncClient.get", new=get), patch("httpx.AsyncClient.post", new=post):
        fingerprint = _run(_client().fingerprint())
    assert (fingerprint.model_digest, fingerprint.served_context, fingerprint.dimension) == ("abc123", 2048, 3)
    assert fingerprint.prefix_policy == "nomic-search-v1"


@pytest.mark.real_embedding_identity
def test_an_unserved_model_or_unknown_context_has_no_identity():
    async def get(_client, _url, **_kwargs):
        return _response(payload={"models": []})

    with patch("httpx.AsyncClient.get", new=get), \
         patch("httpx.AsyncClient.post", new=AsyncMock(return_value=_response(payload={}))), \
         pytest.raises(EmbeddingUnavailableError, match="not served"):
        _run(_client().fingerprint())


# ---------------------------------------------------------------- E4: retry and deadline rules

@pytest.mark.parametrize("transient", [httpx.ConnectError("blip"), 503])
def test_one_transient_failure_is_retried_once(transient):
    server = _Server(status_script=[transient])
    with patch("httpx.AsyncClient.post", new=server.post):
        assert _run(_client().get_embedding("a"))
    assert len(server.requests) == 2


def test_a_second_transient_failure_is_not_retried_again():
    server = _Server(status_script=[httpx.ConnectError("1"), httpx.ConnectError("2"), 200])
    with patch("httpx.AsyncClient.post", new=server.post), pytest.raises(EmbeddingUnavailableError):
        _run(_client().get_embedding("a"))
    assert len(server.requests) == 2


@pytest.mark.parametrize("response", [
    _response(400, text=f'{{"error":"{REFUSED}"}}'),  # input too long
    _response(payload={"embeddings": [[0.0, 0.0]]}),  # malformed
    _response(404, text="model not found"),
])
def test_permanent_failures_are_never_retried(response):
    post = AsyncMock(return_value=response)
    with patch("httpx.AsyncClient.post", new=post), pytest.raises(Exception):
        _run(_client().get_embedding("a"))
    assert post.await_count == 1


def test_a_security_refusal_makes_no_request():
    client = OllamaEmbeddingClient(base_url="https://embeddings.example.com/v1", model="m", egress_policy="local_only")
    post = AsyncMock()
    with patch("httpx.AsyncClient.post", new=post), pytest.raises(EgressViolationError):
        _run(client.get_embedding("code"))
    post.assert_not_awaited()


def test_an_exhausted_run_deadline_is_the_run_deadline_not_an_embedding_failure():
    post = AsyncMock()
    with patch("httpx.AsyncClient.post", new=post), pytest.raises(InferenceDeadlineError) as raised:
        _run(_client().get_embedding("a", deadline=time.monotonic() - 1))
    assert raised.value.reason_code == INFERENCE_DEADLINE_EXHAUSTED
    post.assert_not_awaited()


def test_the_transport_ignores_environment_proxies():
    seen = {}
    real_init = httpx.AsyncClient.__init__

    def init(self, *args, **kwargs):
        seen.update(kwargs)
        real_init(self, *args, **kwargs)

    with patch.object(httpx.AsyncClient, "__init__", init), \
         patch("httpx.AsyncClient.post", new=_Server().post):
        _run(_client().get_embedding("a"))
    assert seen["trust_env"] is False and seen["timeout"] is not None


# ---------------------------------------------------------------- query degradation

class _FailingEmbedder(StaticEmbedder):
    async def embed(self, texts, *, is_query=False, deadline=None):
        raise EmbeddingUnavailableError("embedding server down")


def _retrieve(tmp_path, embedder, store_model="test-embed"):
    (tmp_path / "Service.java").write_text("class Service { void charge() {} }\n")
    store = LocalVectorStore(str(tmp_path / "vector_index.db"))
    seed_index(store, [("Service.java", "class Service { void charge() {} }", [1.0, 0.0])],
               fake_fingerprint(2, store_model))
    try:
        return asyncio.run(retrieve_graph_context(
            "make Service charge", str(tmp_path), embed_client=embedder, vector_store=store,
            dependency_graph_path=None, limits=RetrievalLimits(top_k=3, max_hops=1, max_neighborhood_results=10),
            embedding_model="test-embed", budget_limit=lambda: 4000,
        ))
    finally:
        store.close()


def test_an_unavailable_query_embedding_is_recorded_and_lexical_retrieval_continues(tmp_path):
    result = _retrieve(tmp_path, _FailingEmbedder([1.0, 0.0]))
    assert result.semantic_unavailable == "EMBEDDING_UNAVAILABLE"
    assert result.matched_files == ["Service.java"]  # the lexical leg alone
    assert all(chunk["score"] > 0 for chunk in result.retrieved_chunks)


def test_an_index_of_another_identity_is_not_queried(tmp_path):
    result = _retrieve(tmp_path, StaticEmbedder([1.0, 0.0], "other-model"))
    assert result.semantic_unavailable == EMBEDDING_IDENTITY_CHANGED


def test_a_healthy_query_reports_no_degradation(tmp_path):
    assert _retrieve(tmp_path, StaticEmbedder([1.0, 0.0])).semantic_unavailable is None


# ---------------------------------------------------------------- doctor

@pytest.mark.parametrize(("refuses", "index_model", "status"), [
    (True, None, "PASS"),
    (False, None, "FAIL"),  # truncate:false not honoured
    (True, "other-model", "FAIL"),  # the index was built under another identity
])
def test_the_doctor_embedding_contract_row(tmp_path, refuses, index_model, status):
    from kriya.production_doctor import CheckStatus, _check_embedding

    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
    if index_model:
        os.makedirs(cfg.paths.memory)
        store = LocalVectorStore(os.path.join(cfg.paths.memory, "vector_index.db"))
        seed_index(store, [("a.py", "x", [1.0, 0.0])], fake_fingerprint(2, index_model))
        store.close()
    server = _Server(max_chars=10**9 if not refuses else 100, dimension=2)
    with patch("httpx.AsyncClient.post", new=server.post):
        check = _check_embedding(MagicMock(cfg=cfg))
    assert check.status is getattr(CheckStatus, status), check.evidence
    if status == "PASS":
        assert check.evidence["truncate_false_honoured"] is True and check.evidence["dimension"] == 2


def test_the_doctor_reports_an_unreachable_provider_as_unavailable(tmp_path):
    from kriya.production_doctor import CheckStatus, _check_embedding

    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=httpx.ConnectError("down"))):
        check = _check_embedding(MagicMock(cfg=cfg))
    assert check.status is CheckStatus.UNAVAILABLE
