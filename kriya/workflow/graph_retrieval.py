"""Graph RAG retrieval for the generation pipeline (extracted for PRD-027).

This is the exact retrieval ``WorkflowEngine.run_generation_workflow`` runs:
1. a hybrid vector + lexical query over the code index (``LocalVectorStore.query_hybrid``);
2. pre-plan member grounding of each hit's controlled chunk header;
3. dependency-graph neighbourhood expansion from the matched files' own symbols;
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
from typing import Any, Callable, Dict, List, Optional

from kriya.workflow.context_budget import RetrievalLimits, build_code_context_package


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
    matches = vector_store.query_hybrid(goal, query_emb, top_k=limits.top_k, model_name=embedding_model)
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

    if dependency_graph_path and os.path.exists(dependency_graph_path):
        from kriya.analyzer.graph import DependencyGraph
        graph = DependencyGraph(dependency_graph_path)

        # Real symbols this file's own parse produced, not a filename-stem
        # guess - falls back to the stem only when the file has no indexed
        # symbols at all (e.g. a matched YAML/config file).
        seed_symbols = []
        for f in matched_files_list:
            symbols = graph.get_symbols_for_file(f)
            seed_symbols.extend(symbols or [os.path.splitext(os.path.basename(f))[0]])
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
