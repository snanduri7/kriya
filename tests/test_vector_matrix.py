"""E-06: the vector query reads one contiguous float32 matrix per index
generation instead of deserializing every row of every query.

The matrix must answer exactly what the per-row scan answered, and must never
outlive the rows it was built from: every vector write (publish, stale mark,
removal, reset, legacy add) bumps the index generation in its own
transaction, including writes through another connection."""
import numpy as np
import pytest
from _fake_embedding import fake_fingerprint, seed_index

from kriya.memory import vector as vector_module
from kriya.memory.embedding import EmbeddedSegment
from kriya.memory.vector import LocalVectorStore


@pytest.fixture(autouse=True)
def _fresh_cache():
    vector_module._MATRIX_CACHE.clear()
    yield
    vector_module._MATRIX_CACHE.clear()


def _per_row_reference(store, query, fingerprint, top_k):
    """The pre-E-06 algorithm: every current row of the identity, cosine
    against the query, best first."""
    rows = store.conn.execute(
        "SELECT filepath, chunk_index, embedding FROM vector_chunks WHERE is_current = 1 AND fingerprint = ?",
        (fingerprint,)).fetchall()
    q = np.asarray(query, dtype=np.float32)
    scored = []
    for filepath, chunk_index, blob in rows:
        v = np.frombuffer(blob, dtype=np.float32)
        scored.append((-float(v @ q / (np.linalg.norm(v) * np.linalg.norm(q))), filepath, chunk_index))
    return [(f, c) for _, f, c in sorted(scored)[:top_k]]


def _random_index(store, rows=300, dim=16, seed=3):
    rng = np.random.default_rng(seed)
    docs = [(f"src/F{i // 10}.java", f"text {i}", rng.standard_normal(dim).tolist()) for i in range(rows)]
    return seed_index(store, docs, fake_fingerprint(dim))


def test_the_matrix_ranking_equals_the_per_row_scan(tmp_path):
    store = LocalVectorStore(str(tmp_path / "v.db"))
    fingerprint = _random_index(store)
    rng = np.random.default_rng(9)
    for _ in range(5):
        query = rng.standard_normal(16).tolist()
        hits = store.query(query, top_k=20, fingerprint=fingerprint.digest)
        assert [(h["filepath"], h["chunk_index"]) for h in hits] == _per_row_reference(
            store, query, fingerprint.digest, 20)
        assert all(h["text"] and "span_start" in h for h in hits)
        assert [h["score"] for h in hits] == sorted((h["score"] for h in hits), reverse=True)
    store.close()


def test_an_unchanged_index_is_read_once(tmp_path):
    store = LocalVectorStore(str(tmp_path / "v.db"))
    fingerprint = _random_index(store)
    first = store.current_matrix(fingerprint.digest, 16)
    assert store.current_matrix(fingerprint.digest, 16) is first
    store.close()


@pytest.mark.parametrize("write", ["publish", "stale", "remove", "reset", "other_connection"])
def test_every_write_invalidates_the_matrix(tmp_path, write):
    path = str(tmp_path / "v.db")
    store = LocalVectorStore(path)
    fingerprint = seed_index(store, [("A.java", "a", [1.0, 0.0]), ("B.java", "b", [0.0, 1.0])], fake_fingerprint(2))
    assert {h["filepath"] for h in store.query([1.0, 0.2], top_k=5, fingerprint=fingerprint.digest)} == {
        "A.java", "B.java"}
    if write == "publish":
        store.publish_file("A.java", [EmbeddedSegment(0, 0, 1, 1, "a2", [0.0, -1.0])], source_digest="x",
                           fingerprint=fingerprint.digest)
        expected = {("A.java", -0.196), ("B.java", 0.196)}
    elif write == "stale":
        store.mark_stale("A.java")
        expected = {("B.java", 0.196)}
    elif write == "remove":
        store.remove_file("A.java")
        expected = {("B.java", 0.196)}
    elif write == "reset":
        store.reset_index(fingerprint)
        expected = set()
    else:
        other = LocalVectorStore(path)
        other.remove_file("B.java")
        other.close()
        expected = {("A.java", 0.981)}
    hits = store.query([1.0, 0.2], top_k=5, fingerprint=fingerprint.digest)
    assert {(h["filepath"], round(h["score"], 3)) for h in hits} == expected
    store.close()


def test_another_identity_or_dimension_never_reads_the_cached_matrix(tmp_path):
    store = LocalVectorStore(str(tmp_path / "v.db"))
    fingerprint = seed_index(store, [("A.java", "a", [1.0, 0.0])], fake_fingerprint(2, "model-a"))
    assert store.query([1.0, 0.0], fingerprint=fingerprint.digest)
    assert store.query([1.0, 0.0], fingerprint=fake_fingerprint(2, "model-b").digest) == []
    assert store.query([1.0, 0.0, 0.0], fingerprint=fingerprint.digest) == []
    store.close()


def test_hits_carry_the_indexed_span(tmp_path):
    store = LocalVectorStore(str(tmp_path / "v.db"))
    fingerprint = fake_fingerprint(2)
    store.reset_index(fingerprint)
    store.publish_file("A.java", [EmbeddedSegment(0, 0, 10, 24, "m", [1.0, 0.0])], source_digest="x",
                       fingerprint=fingerprint.digest)
    [hit] = store.query([1.0, 0.0], fingerprint=fingerprint.digest)
    assert (hit["span_start"], hit["span_end"], hit["text"]) == (10, 24, "m")
    store.close()
