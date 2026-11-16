"""The one read/write contract for knowledge taught with ``kriya learn``
(KNOWLEDGE-READPATH-001).

Store: ``<paths.memory>/web_knowledge.db``, table ``learned_knowledge``
(text, embedding, model_name, dimensions, provenance_url, fetch_date). ``learn``
writes it through ``LocalVectorStore.add_learned_knowledge``; every consumer
reads it through ``retrieve_learned_references`` below. It is a separate,
lower-trust namespace: the code index (``vector_index.db``) never holds it and
code retrieval never reads it.

Scope (C-05, S3): a PROJECT store. ``learn`` takes a URL, a local file or free
text, and a row records no scope and no approval, so nothing proves a taught
chunk is meant for every project. The store is therefore the bound project's
(kriya/memory/project_store.py: stamped with the project identity when
``learn`` claims it; a reader is handed it only when the stamp is this
project's). What was taught in one project is not shown to a model working in
another, under the per-project default and under an explicit ``paths.memory``
two projects share alike. Making a chunk available to every project is a
governed promotion, which does not exist yet (S4).

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

import sqlite3
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from kriya.core.llm import EgressViolationError
from kriya.memory.project_store import claim_project_knowledge_store, project_store_path, project_store_status
from kriya.memory.vector import LocalVectorStore

LEARNED_KNOWLEDGE_DB = "web_knowledge.db"
# How many learned chunks one request may show, and the cosine similarity a
# chunk needs to be shown at all.
LEARNED_REFERENCE_TOP_K = 5
LEARNED_REFERENCE_MIN_SCORE = 0.4


def learned_knowledge_db_path(cfg: Any) -> Optional[str]:
    """READ side: this project's learned-knowledge store, or None when it has none (nothing was taught here, or the file there is another
    project's or cannot be attributed: reported once, never read). A configuration with no project identity raises
    (``PROJECT_IDENTITY_MISSING``)."""
    return project_store_path(cfg, LEARNED_KNOWLEDGE_DB)


def claim_learned_knowledge_store(cfg: Any) -> str:
    """WRITE side, for ``kriya learn`` only: this project's learned-knowledge store, created and stamped when absent. Raises
    ``ProjectStateError`` for a store of another project or an unattributable one, which is left exactly as it was."""
    return claim_project_knowledge_store(cfg, LEARNED_KNOWLEDGE_DB)


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
    if not query.strip():
        return LearnedRetrieval()
    path, refusal = project_store_status(cfg, LEARNED_KNOWLEDGE_DB)
    if refusal is not None:
        # C-05: a store is there but it is not this project's (another project's, unattributable, unreadable). Nothing is read from it, and
        # the operator is told why, exactly as for any other store that cannot be used.
        return LearnedRetrieval(unavailable_reason=f"the store cannot be read for this project [{refusal.reason_code}]: {refusal}")
    if path is None:
        return LearnedRetrieval()
    from kriya.memory.embedding import EmbeddingError, configured_client, run_deadline

    try:
        embedding = await configured_client(cfg).get_embedding(query, is_query=True, deadline=run_deadline(cfg))
    except (EgressViolationError, EmbeddingError) as exc:
        # EMBEDDING-CONTRACT-001: a typed reason, never a zero-vector query.
        return LearnedRetrieval(unavailable_reason=str(exc))
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
