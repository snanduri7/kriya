"""Test doubles for the EMBEDDING-CONTRACT-001 surface (never a live model).

- ``fake_fingerprint``: a deterministic EmbeddingFingerprint.
- ``StaticEmbedder``: a fixed-vector embedder with the full contract
  surface (embed, get_embedding, get_embeddings, fingerprint).
- ``seed_index``: publishes documents into a LocalVectorStore under an
  identity, the way index_repository() does (a legacy add_document row has
  no identity, so a fingerprinted query never treats it as current).
"""
from typing import Iterable, List, Optional, Sequence, Tuple

from kriya.memory.embedding import (
    PREPROCESSING_VERSION,
    SEGMENTATION_VERSION,
    EmbeddedSegment,
    EmbeddingFingerprint,
)

TEST_SERVED_CONTEXT = 2048


def fake_fingerprint(dimension: int, model: str = "test-embed") -> EmbeddingFingerprint:
    return EmbeddingFingerprint(
        model=model, model_digest=f"test-digest-{model}", adapter="test-embedder/1", dimension=dimension,
        served_context=TEST_SERVED_CONTEXT, prefix_policy="none", preprocessing_version=PREPROCESSING_VERSION,
        segmentation_version=SEGMENTATION_VERSION,
    )


class StaticEmbedder:
    """Every text embeds to the same vector."""

    admission_misses = 0

    def __init__(self, vector: Sequence[float], model: str = "test-embed") -> None:
        self.vector = list(vector)
        self.model = model

    async def fingerprint(self) -> EmbeddingFingerprint:
        return fake_fingerprint(len(self.vector), self.model)

    async def embed(self, texts: Sequence[str], *, is_query: bool = False,
                    deadline: Optional[float] = None) -> List[List[float]]:
        del is_query, deadline
        return [list(self.vector) for _ in texts]

    async def get_embedding(self, text: str, is_query: bool = False, deadline: Optional[float] = None) -> List[float]:
        return (await self.embed([text], is_query=is_query, deadline=deadline))[0]

    async def get_embeddings(self, texts: Sequence[str], is_query: bool = False,
                             deadline: Optional[float] = None) -> List[List[float]]:
        return await self.embed(texts, is_query=is_query, deadline=deadline)


def seed_index(store, documents: Iterable[Tuple[str, str, Sequence[float]]],
               fingerprint: Optional[EmbeddingFingerprint] = None) -> EmbeddingFingerprint:
    """Publish (filepath, text, vector) documents as current vectors under
    ``fingerprint`` (by default the fake one for their dimension)."""
    documents = list(documents)
    fingerprint = fingerprint or fake_fingerprint(len(documents[0][2]))
    if store.active_fingerprint() != fingerprint.digest:
        store.reset_index(fingerprint)
    by_file = {}
    for filepath, text, vector in documents:
        by_file.setdefault(filepath, []).append((text, list(vector)))
    for filepath, items in by_file.items():
        store.publish_file(
            filepath, [EmbeddedSegment(index, 0, 1, 1, text, vector) for index, (text, vector) in enumerate(items)],
            source_digest=f"test-{filepath}", fingerprint=fingerprint.digest,
        )
    return fingerprint


def native_embed_response(dimension: int = 384):
    """A side_effect for a patched httpx.AsyncClient.post answering the native
    /api/embed shape: one vector per input."""
    from unittest.mock import MagicMock

    def respond(*_args, json=None, **_kwargs):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"embeddings": [[0.1] * dimension for _ in (json or {}).get("input", [""])]}
        return response
    return respond
