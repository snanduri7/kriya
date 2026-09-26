"""Graph RAG retrieval for the generation pipeline (extracted for PRD-027).

This is the exact retrieval ``WorkflowEngine.run_generation_workflow`` runs:
1. a hybrid vector + lexical query over the code index (``LocalVectorStore.query_hybrid``);
2. pre-plan member grounding of each hit's controlled chunk header;
3. dependency-graph neighbourhood expansion from the expansion seeds'
   own symbols (``select_expansion_seeds``: corroborated hits only when
   both retrieval legs produced evidence);
4. the token-budgeted context build (``build_code_context_package``).
It was moved here verbatim from workflow.py so that the context-recall
certification suite (``kriya/workflow/context_certification.py``) measures
the production code path rather than a re-implementation. The one addition
is the returned ``ContextPackage``: the structured tiers and omissions
behind the (unchanged) rendered context string.

Nothing here is an authority. Member names parsed from hits are candidates,
split into ``verified_grounding`` (resolves to exactly one real current
member) and ``hypothesis_candidates``. See workflow.py's PRE-PLAN
GROUNDING stage and ``AttemptContext.retrieval_member_hints`` for how those
buckets are consumed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from kriya.workflow.context_budget import RetrievalLimits, build_code_context_package

# PRD027-PRECISION-001: why the graph walk started from the files it did.
EXPANSION_CORROBORATED = "CORROBORATED_EXPANSION_SEED"
EXPANSION_EMBEDDING_ONLY = "EMBEDDING_ONLY_EXPANSION_SEED"
EXPANSION_LEXICAL_ONLY = "LEXICAL_ONLY_EXPANSION_SEED"
EXPANSION_NO_CORROBORATED = "NO_CORROBORATED_EXPANSION_SEED"
EXPANSION_NO_VALID_EVIDENCE = "NO_VALID_RETRIEVAL_EVIDENCE"
EXPANSION_PROVENANCE_UNAVAILABLE = "EXPANSION_SEED_PROVENANCE_UNAVAILABLE"
EXPANSION_SEED_REASON_CODES = (
    EXPANSION_CORROBORATED, EXPANSION_EMBEDDING_ONLY, EXPANSION_LEXICAL_ONLY,
    EXPANSION_NO_CORROBORATED, EXPANSION_NO_VALID_EVIDENCE, EXPANSION_PROVENANCE_UNAVAILABLE,
)
_LEG_FIELDS = ("vector_rank", "lexical_rank", "vector_valid_hits", "lexical_valid_hits")


def select_expansion_seeds(hits: Sequence[Dict[str, Any]], top_k: int) -> Tuple[List[str], str]:
    """Which search hits may seed the dependency-graph walk, and why.

    Search hits themselves are never removed; this decides only graph
    expansion authority. E is the embedding leg's valid top_k, L the
    lexical leg's (``LocalVectorStore.query_hybrid`` annotates every hit):
    - E and L both non-empty: only hits in E and L seed; with none, no hit
      seeds (NO_CORROBORATED_EXPANSION_SEED) - disagreement alone never
      fans out, and no similarity threshold is invented to break it;
    - only one leg produced valid hits: that leg's top_k hits seed, so a
      genuinely embedding-only or lexical-only query keeps its expansion;
    - neither: nothing seeds.
    A hit without leg provenance cannot be classified, so nothing seeds.
    Returns (seed files in hit order, reason code)."""
    if not hits:
        return [], EXPANSION_NO_VALID_EVIDENCE
    if any(field not in hit for hit in hits for field in _LEG_FIELDS):
        return [], EXPANSION_PROVENANCE_UNAVAILABLE

    def within(rank: Any) -> bool:
        return rank is not None and rank <= top_k

    vector_valid = hits[0]["vector_valid_hits"] > 0
    lexical_valid = hits[0]["lexical_valid_hits"] > 0
    if vector_valid and lexical_valid:
        seeds = [hit for hit in hits if within(hit["vector_rank"]) and within(hit["lexical_rank"])]
        reason = EXPANSION_CORROBORATED if seeds else EXPANSION_NO_CORROBORATED
    elif vector_valid:
        seeds, reason = [hit for hit in hits if within(hit["vector_rank"])], EXPANSION_EMBEDDING_ONLY
    elif lexical_valid:
        seeds, reason = [hit for hit in hits if within(hit["lexical_rank"])], EXPANSION_LEXICAL_ONLY
    else:
        seeds, reason = [], EXPANSION_NO_VALID_EVIDENCE
    files = list(dict.fromkeys(hit["filepath"] for hit in seeds if hit.get("filepath")))
    return files, reason


@dataclass
class GraphRetrievalResult:
    matched: bool = False
    matched_files: List[str] = field(default_factory=list)
    related_files: List[str] = field(default_factory=list)
    file_scores: Dict[str, float] = field(default_factory=dict)
    graph_rag_context: str = ""
    context_package: Optional[Any] = None
    retrieved_chunks: List[Dict[str, Any]] = field(default_factory=list)
    retrieval_member_hints: Dict[str, List[str]] = field(default_factory=dict)
    verified_grounding: Dict[str, List[str]] = field(default_factory=dict)
    hypothesis_candidates: Dict[str, List[str]] = field(default_factory=dict)
    # PRD027-PRECISION-001: the files the graph walk started from and why.
    expansion_seed_files: List[str] = field(default_factory=list)
    expansion_seed_reason: Optional[str] = None


async def retrieve_graph_context(
    goal: str,
    workspace_path: str,
    *,
    embed_client: Any,
    vector_store: Any,
    dependency_graph_path: Optional[str],
    limits: RetrievalLimits,
    embedding_model: str,
    budget_limit: Callable[[], int],
) -> GraphRetrievalResult:
    """Retrieve and assemble the Graph RAG context for ``goal``.
    ``budget_limit`` is evaluated only when there are hits to assemble, as it
    was inline. The dependency graph is opened only in that case too."""
    from kriya.workflow.context_source import (
        CurrentSourceResolver,
        parse_controlled_chunk_header_name,
        resolve_verified_grounding_member_id,
    )

    result = GraphRetrievalResult()
    query_emb = await embed_client.get_embedding(goal, is_query=True)
    # The index is keyed by (model, dimension): pass the real query
    # dimension, never query_hybrid's 768 default - a non-768 embedding
    # model otherwise silently degraded code retrieval to lexical-only
    # (found by the PRD-027 certification suite).
    matches = vector_store.query_hybrid(
        goal, query_emb, top_k=limits.top_k, model_name=embedding_model, dimensions=len(query_emb),
    )
    good_matches = [m for m in matches if m.get("score", 0.0) > 0.0]
    grounding_resolver = CurrentSourceResolver(workspace_path, None)
    for m in good_matches:
        result.retrieved_chunks.append({
            "filepath": m.get("filepath", "unknown"),
            "score": m.get("score", 0.0),
            "text": m.get("text", "")[:300] + "..." if len(m.get("text", "")) > 300 else m.get("text", "")
        })
        # CTX-001 P1 C2: parse (never trust) a candidate member/class name
        # from the hit's controlled "Method: X"/"Class: X" chunk header.
        fp = m.get("filepath")
        if fp:
            candidate_name = parse_controlled_chunk_header_name(m.get("text", ""))
            if candidate_name:
                names = result.retrieval_member_hints.setdefault(fp, [])
                if candidate_name not in names:
                    names.append(candidate_name)

                resolved = grounding_resolver.resolve(fp)
                verified_member_id = (
                    resolve_verified_grounding_member_id(fp, resolved.content, candidate_name)
                    if resolved.exists else None
                )
                if verified_member_id is not None:
                    verified_list = result.verified_grounding.setdefault(fp, [])
                    if verified_member_id not in verified_list:
                        verified_list.append(verified_member_id)
                else:
                    hyp_list = result.hypothesis_candidates.setdefault(fp, [])
                    if candidate_name not in hyp_list:
                        hyp_list.append(candidate_name)

    if not good_matches:
        return result
    matched_files_list = list(dict.fromkeys([m["filepath"] for m in good_matches if "filepath" in m]))
    related_files_set = set()
    # Matched-file relevance: the best (max) hybrid RRF score across that
    # file's own matched chunks.
    file_scores: Dict[str, float] = {}
    for m in good_matches:
        fp = m.get("filepath")
        if fp:
            file_scores[fp] = max(file_scores.get(fp, 0.0), m.get("score", 0.0))

    # PRD027-PRECISION-001: only corroborated hits (or a single leg's own
    # hits when the other leg found nothing) seed the walk; every matched
    # file is still packaged below.
    seed_files, result.expansion_seed_reason = select_expansion_seeds(good_matches, limits.top_k)
    result.expansion_seed_files = seed_files
    if seed_files and dependency_graph_path and os.path.exists(dependency_graph_path):
        from kriya.analyzer.graph import DependencyGraph
        graph = DependencyGraph(dependency_graph_path)

        # Real symbols this file's own parse produced, not a filename-stem
        # guess - falls back to the stem only when the file has no indexed
        # symbols at all (e.g. a matched YAML/config file).
        seed_symbols = []
        for f in seed_files:
            symbols = graph.get_symbols_for_file(f)
            seed_symbols.extend(symbols or [os.path.splitext(os.path.basename(f))[0]])
            # PRD-027: the file itself is a node too - calls/imports are
            # recorded with the calling file as their source, so a matched
            # file's own dependencies are reachable only from its path.
            seed_symbols.append(f)
        neighbors = graph.get_neighborhood(
            seed_symbols, max_hops=limits.max_hops,
            max_results=limits.max_neighborhood_results,
        )
        for n in neighbors:
            fp = n.get("filepath")
            if fp and fp not in matched_files_list:
                related_files_set.add(fp)
                file_scores[fp] = max(file_scores.get(fp, 0.0), n.get("score", 0.0))

    result.matched = True
    result.matched_files = matched_files_list
    result.related_files = list(related_files_set)
    result.file_scores = file_scores
    result.graph_rag_context, result.context_package = build_code_context_package(
        result.matched_files, result.related_files, workspace_path, budget_limit(), file_scores=file_scores,
    )
    return result
