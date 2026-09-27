"""The one read/write contract for knowledge taught with ``kriya learn``
(KNOWLEDGE-READPATH-001).

Store: ``<paths.memory>/web_knowledge.db``, table ``learned_knowledge``
(text, embedding, model_name, dimensions, provenance_url, fetch_date). ``learn``
writes it through ``LocalVectorStore.add_learned_knowledge``; every consumer
reads it through ``retrieve_learned_references`` below. It is a separate,
lower-trust namespace: the code index (``vector_index.db``) never holds it and
code retrieval never reads it.

Trust: the result is reference data. Callers show it to a model only through
``fence_untrusted_reference`` and never join it to a goal, so it can never
become requirement, mutation-scope, contract, expected-exit, approval,
promotion or resume authority (AUTH-GOAL-CONTAMINATION-001). The query is the
user's own words - the ``ask`` question, the ``generate`` goal, a milestone
plan's original goal - never retrieved or model-written text.

Failure: a store that cannot be read, a query that cannot be embedded, or an
embedding endpoint the egress policy refuses contributes nothing (no partial
read) and says why in ``unavailable_reason``; rows that cannot be decoded or
were embedded by another model are excluded and counted. The run goes on
without reference material - it is never an input to a correctness decision.
"""

import os
import sqlite3
from dataclasses import dataclass
from typing import Any, List, Tuple

from kriya.core.llm import EgressViolationError
from kriya.memory.vector import LocalVectorStore, OllamaEmbeddingClient

LEARNED_KNOWLEDGE_DB = "web_knowledge.db"
# How many learned chunks one request may show, and the cosine similarity a
# chunk needs to be shown at all.
LEARNED_REFERENCE_TOP_K = 5
LEARNED_REFERENCE_MIN_SCORE = 0.4


def learned_knowledge_db_path(cfg: Any) -> str:
    """The learned-knowledge store ``kriya learn`` writes and every reader reads."""
    return os.path.join(cfg.paths.memory, LEARNED_KNOWLEDGE_DB)


@dataclass(frozen=True)
class LearnedReference:
    text: str
    provenance_url: str
    fetch_date: str
    score: float


@dataclass(frozen=True)
class LearnedRetrieval:
    references: Tuple[LearnedReference, ...] = ()
    malformed_rows: int = 0
    other_embedding_rows: int = 0
    unavailable_reason: str = ""

    def render(self) -> str:
        """The references with their provenance, unfenced; the caller fences."""
        return "".join(
            f"\n[Source: {ref.provenance_url or 'unknown'} (Fetched: {ref.fetch_date or 'unknown'})]\n{ref.text}\n"
            for ref in self.references
        )

    def warnings(self) -> List[str]:
        """What the operator should know about this retrieval, if anything."""
        notes = []
        if self.unavailable_reason:
            notes.append(f"Learned knowledge not used: {self.unavailable_reason}.")
        if self.malformed_rows:
            notes.append(f"Learned knowledge: {self.malformed_rows} unreadable stored chunk(s) were skipped.")
        if self.other_embedding_rows:
            notes.append(
                f"Learned knowledge: {self.other_embedding_rows} chunk(s) were embedded by a different "
                "embedding model and are not searchable; re-run `kriya learn` for them."
            )
        return notes


async def retrieve_learned_references(cfg: Any, query: str) -> LearnedRetrieval:
    """The learned chunks relevant to ``query`` (the user's own words).
    Empty, with no reason, when nothing was ever learned."""
    path = learned_knowledge_db_path(cfg)
    if not query.strip() or not os.path.exists(path):
        return LearnedRetrieval()
    client = OllamaEmbeddingClient(
        base_url=cfg.embedding.base_url,
        model=cfg.embedding.model,
        egress_policy=cfg.autonomy.egress_policy,
    )
    try:
        embedding = await client.get_embedding(query, is_query=True)
    except EgressViolationError as exc:
        return LearnedRetrieval(unavailable_reason=str(exc))
    if not any(embedding):
        return LearnedRetrieval(unavailable_reason="the embedding endpoint returned no usable query vector")
    try:
        store = LocalVectorStore(path)
        try:
            found = store.query_learned_knowledge(
                embedding, top_k=LEARNED_REFERENCE_TOP_K,
                model_name=cfg.embedding.model, dimensions=len(embedding),
            )
        finally:
            store.close()
    except sqlite3.Error as exc:
        return LearnedRetrieval(unavailable_reason=f"the store {path} cannot be read ({exc})")
    return LearnedRetrieval(
        references=tuple(
            LearnedReference(match["text"], match["provenance_url"] or "", match["fetch_date"] or "", match["score"])
            for match in found.matches if match["score"] > LEARNED_REFERENCE_MIN_SCORE
        ),
        malformed_rows=found.malformed_rows,
        other_embedding_rows=found.other_embedding_rows,
    )
