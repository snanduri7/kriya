"""Graph RAG retrieval for the generation pipeline (extracted for PRD-027).

This is the exact retrieval ``WorkflowEngine.run_generation_workflow`` runs:
1. candidate localization. With a Code Intelligence structural index of the
   current parser identity (Code Intelligence R1 slice 2), one fused query
   (``fused_localization``): ``CodeIntelligenceService.locate`` over the
   workspace's CURRENT structure (index + in-memory overlay of drifted files)
   with every deterministic channel (qualified/simple symbol, path,
   file:line, stack frame, traceback, string literal, identifier BM25) plus
   the vector channel (the current-identity vector hits for the query
   embedding), fused by fixed tiers - exact evidence outranks similarity, and
   every hit carries its channels. The pre-R1 hybrid leg
   (``LocalVectorStore.query_hybrid``) remains a bounded fallback: whole when
   no structural index exists, and otherwise only for files the structural
   index does not cover (ranked below every structural hit, reported as
   ``legacy_only_files``). Without a structural index, step 1 is the hybrid
   query alone, exactly as before;
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

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from kriya.workflow.context_budget import RetrievalLimits, build_code_context_package, estimate_tokens

logger = logging.getLogger(__name__)

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


LOCALIZATION_CODE_INTELLIGENCE = "code_intelligence"
LOCALIZATION_LEGACY = "hybrid"
# Member-level candidates kept per query (the Planner map and member hints).
LOCALIZATION_LIMIT = 12


@dataclass(frozen=True)
class LocalizationCandidate:
    """One ranked localization candidate: WHERE, never what may be written
    (Developer context re-reads exact current bytes for any target)."""

    symbol_id: str
    path: str
    kind: str
    lookup_key: str
    signature: str
    score: float
    channels: Tuple[Tuple[str, float], ...]

    def to_dict(self) -> Dict[str, Any]:
        return {"symbol_id": self.symbol_id, "path": self.path, "kind": self.kind, "lookup_key": self.lookup_key,
                "score": self.score, "channels": [c for c, _ in self.channels]}


def render_localization_candidates(candidates: Sequence[LocalizationCandidate]) -> str:
    """The compact candidate map for the Planner: ranked symbol ids, path,
    kind, compact signature and the channels that found each - never a
    source body."""
    if not candidates:
        return ""
    lines = ["", "=== CODE INTELLIGENCE LOCALIZATION CANDIDATES (ranked; where the goal points, read-only, "
             "never write authority) ==="]
    for rank, candidate in enumerate(candidates, 1):
        signature = " ".join(candidate.signature.split())[:160]
        lines.append(f"{rank}. [{candidate.kind}] {candidate.lookup_key} - {candidate.path}"
                     f"{' - ' + signature if signature else ''} (via {', '.join(c for c, _ in candidate.channels)})"
                     f" id={candidate.symbol_id}")
    return "\n".join(lines) + "\n"


@dataclass
class GraphRetrievalResult:
    matched: bool = False
    matched_files: List[str] = field(default_factory=list)
    related_files: List[str] = field(default_factory=list)
    # evidence_scores(): direct hits in (1, 2], graph expansion in (0, 1].
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
    # EMBEDDING-CONTRACT-001: why the semantic leg did not run (a typed
    # embedding reason code); only the lexical leg then contributed.
    semantic_unavailable: Optional[str] = None
    # Code Intelligence R1 slice 2: which candidate generation answered
    # ("code_intelligence" or the legacy "hybrid"), the ranked member-level
    # candidates (localization context, never write authority), the files
    # only the legacy leg contributed, and the deterministic separation of
    # the top candidates.
    localization_source: str = LOCALIZATION_LEGACY
    localization: List["LocalizationCandidate"] = field(default_factory=list)
    legacy_only_files: List[str] = field(default_factory=list)
    separation: Optional[Dict[str, Any]] = None
    # symbol id -> member id in its file's CURRENT bytes (callable
    # candidates whose member resolved; the CI-6 decision's id check).
    current_member_ids: Dict[str, str] = field(default_factory=dict)

    def adopt_decision(self, target_symbol_ids: Sequence[str]) -> None:
        """CI-6: the chosen targets lead the candidates, their files lead the
        direct evidence and their members lead the member hints. Nothing is
        dropped or invented; only already-current candidates are moved."""
        chosen = [c for i in target_symbol_ids for c in self.localization if c.symbol_id == i]
        self.localization = chosen + [c for c in self.localization if c.symbol_id not in target_symbol_ids]
        top = max(self.file_scores.values(), default=0.0)
        for candidate in reversed(chosen):
            self.matched_files = [candidate.path] + [p for p in self.matched_files if p != candidate.path]
            self.file_scores[candidate.path] = max(self.file_scores.get(candidate.path, 0.0), top)
            member_id = self.current_member_ids.get(candidate.symbol_id)
            if member_id is None:
                continue
            for bucket in (self.retrieval_member_hints, self.verified_grounding):
                members = bucket.setdefault(candidate.path, [])
                bucket[candidate.path] = [member_id] + [m for m in members if m != member_id]
            self.related_files = [p for p in self.related_files if p != candidate.path]

    def rebuild_context(self, workspace_path: str, budget: int) -> Tuple[str, Any]:
        """The Graph RAG context within ``budget`` (allocator tokens): the
        compact localization candidate map (symbol ids, kinds, signatures,
        channels; never bodies) ahead of the file context, both inside the
        budget. The Planner/Architect request fit rebuilds through this too,
        so a rebuild never drops the map. Localization context only - every
        Developer target re-reads exact current bytes."""
        candidate_map = render_localization_candidates(self.localization)
        file_context, package = build_code_context_package(
            self.matched_files, self.related_files, workspace_path,
            max(0, budget - estimate_tokens(candidate_map)), file_scores=self.file_scores,
        )
        return candidate_map + file_context, package


# PRD027-SCORE-NORMALIZATION-001: the tier of direct query evidence. Each
# evidence class is normalized into (0, 1] by its own best score, so this
# offset - the upper bound of the lower tier - puts every direct hit above
# every graph-expanded file. It is the tier boundary, not a tuned weight.
DIRECT_EVIDENCE_TIER = 1.0


def evidence_scores(direct: Dict[str, float], expanded: Dict[str, float]) -> Dict[str, float]:
    """One comparable ranking over the two evidence classes
    (PRD027-SCORE-NORMALIZATION-001). A direct hit's hybrid RRF score (at
    most ~0.033) and a graph-expanded file's relation weight over hops
    (0.5-1.0) are different scales; comparing them raw ranked every expanded
    file above every file the query itself matched, so a binding budget
    shrank or dropped the direct evidence first.

    Each class is normalized by its own best score into (0, 1]: direct
    evidence then occupies (1, 2], graph expansion (0, 1]. Any direct hit
    outranks any expanded file, whatever the raw scales; the order within a
    class is exactly its raw order; a file in both classes is direct. The
    budget builders degrade and omit lowest score first, so graph expansion
    gives way before direct evidence and stays available whenever it fits."""
    def normalized(scores: Dict[str, float]) -> Dict[str, float]:
        top = max(scores.values(), default=0.0)
        return {path: (score / top if top > 0 else 0.0) for path, score in scores.items()}

    ranked = normalized(expanded)
    ranked.update({path: DIRECT_EVIDENCE_TIER + score for path, score in normalized(direct).items()})
    return ranked


async def semantic_query_embedding(
    embed_client: Any, vector_store: Any, query: str, result: Any, deadline: Optional[float] = None,
) -> Tuple[Optional[List[float]], Optional[str]]:
    """(query vector, fingerprint digest) for the semantic leg, or (None,
    fingerprint) with ``result.semantic_unavailable`` set to the typed reason
    when the embedding failed or the index was built under another identity:
    the lexical leg then runs alone and stale vectors are never queried
    (EMBEDDING-CONTRACT-001). An exhausted run deadline is not an embedding
    failure: it propagates."""
    from kriya.memory.embedding import EMBEDDING_IDENTITY_CHANGED, EmbeddingError

    try:
        fingerprint = (await embed_client.fingerprint()).digest
    except EmbeddingError as error:
        result.semantic_unavailable = error.reason_code
        logger.warning("Semantic retrieval unavailable (%s); lexical retrieval only.", error)
        return None, None
    if vector_store.active_fingerprint() != fingerprint:
        result.semantic_unavailable = EMBEDDING_IDENTITY_CHANGED
        logger.warning("Vector index identity differs from the served embedding model; lexical retrieval only "
                       "until 'kriya analyze' rebuilds it under the served identity.")
        return None, fingerprint
    try:
        return await embed_client.get_embedding(query, is_query=True, deadline=deadline), fingerprint
    except EmbeddingError as error:
        result.semantic_unavailable = error.reason_code
        logger.warning("Semantic retrieval unavailable (%s); lexical retrieval only.", error)
        return None, fingerprint


def select_fused_expansion_seeds(candidates: Sequence[LocalizationCandidate], top_k: int,
                                 legacy_hits: Sequence[Dict[str, Any]] = ()) -> Tuple[List[str], str]:
    """PRD027-PRECISION-001's rule over fused candidates plus the legacy
    leg's hits on files without structure (each carrying its own per-leg
    ranks from ``query_hybrid``). A candidate is corroborated when it rests
    on exact deterministic evidence or on agreement of the two similarity
    legs (identifier BM25 and vector); a legacy hit when both of its legs
    rank it within top_k. When both legs produced evidence anywhere, only
    corroborated items seed (none: nothing seeds - disagreement never fans
    out); when only one leg produced any, that leg's top_k seed (its own
    standing). Same reason codes as the legacy rule."""
    from kriya.code_intel import locate as loc

    top = list(candidates[:top_k])
    legacy = [hit for hit in legacy_hits if hit.get("filepath")]
    if not top and not legacy:
        return [], EXPANSION_NO_VALID_EVIDENCE

    def legs(candidate: LocalizationCandidate) -> set:
        names = {c for c, _ in candidate.channels}
        return {"vector"} & names | ({"lexical"} if names - {"vector"} else set())

    def legacy_legs(hit: Dict[str, Any]) -> set:
        found = set()
        if hit.get("vector_rank") is not None and hit["vector_rank"] <= top_k:
            found.add("vector")
        if hit.get("lexical_rank") is not None and hit["lexical_rank"] <= top_k:
            found.add("lexical")
        return found

    corroborated = [c.path for c in top if c.score >= loc.EXACT_EVIDENCE or legs(c) == {"vector", "lexical"}]
    corroborated += [hit["filepath"] for hit in legacy if legacy_legs(hit) == {"vector", "lexical"}]
    present = set().union(*(legs(c) for c in candidates), *(legacy_legs(hit) for hit in legacy))
    if corroborated:
        seeds, reason = corroborated, EXPANSION_CORROBORATED
    elif present == {"vector"}:
        seeds, reason = [c.path for c in top] + [h["filepath"] for h in legacy if "vector" in legacy_legs(h)], \
            EXPANSION_EMBEDDING_ONLY
    elif present == {"lexical"}:
        seeds, reason = [c.path for c in top] + [h["filepath"] for h in legacy if "lexical" in legacy_legs(h)], \
            EXPANSION_LEXICAL_ONLY
    elif present:
        seeds, reason = [], EXPANSION_NO_CORROBORATED
    else:
        seeds, reason = [], EXPANSION_NO_VALID_EVIDENCE
    return list(dict.fromkeys(seeds)), reason


def separation(candidates: Sequence[LocalizationCandidate]) -> Optional[Dict[str, Any]]:
    """Deterministic separation of the top candidate (no model): top1/top2
    scores, their margin, whether top1 rests on exact evidence, and how many
    channels agree on it. The CI-6 ambiguity decision reads only this."""
    from kriya.code_intel import locate as loc

    if not candidates:
        return None
    top1 = candidates[0]
    top2 = candidates[1].score if len(candidates) > 1 else 0.0
    return {"top1": top1.score, "top2": top2, "margin": round(top1.score - top2, 4),
            "exact": top1.score >= loc.EXACT_EVIDENCE, "channels": len(top1.channels),
            "top1_symbol_id": top1.symbol_id}


def open_code_intelligence(workspace_path: str, dependency_graph_path: Optional[str]) -> Optional[Any]:
    """The structural index's current view of ``workspace_path``, or None
    when there is no structural index of the current parser identity (the
    legacy hybrid leg then answers alone)."""
    if not dependency_graph_path or not os.path.exists(dependency_graph_path):
        return None
    from kriya.code_intel.service import CodeIntelligenceService

    service = CodeIntelligenceService(workspace_path, dependency_graph_path)
    if not service.has_baseline():
        service.close()
        return None
    return service


def fused_localization(service: Any, text: str, semantic_hits: Iterable[Dict[str, Any]],
                       limit: int = LOCALIZATION_LIMIT) -> List[LocalizationCandidate]:
    """One fused Code Intelligence query (deterministic channels + the
    vector channel), as ranked candidates with their signatures."""
    hits = service.locate(text, limit=limit, semantic=list(semantic_hits))
    symbols = {s.symbol_id: s for s in service._symbols_by_id([h.symbol_id for h in hits])}
    return [LocalizationCandidate(h.symbol_id, h.path, h.kind, h.lookup_key,
                                  symbols[h.symbol_id].signature_text if h.symbol_id in symbols else "",
                                  h.score, h.channels) for h in hits]


def member_id_for(candidate: LocalizationCandidate, namespace: str) -> str:
    """The member_hints/member_boundaries id of a structural candidate: its
    qualified key relative to the file's package/module."""
    prefix = f"{namespace}." if namespace else ""
    return candidate.lookup_key[len(prefix):] if prefix and candidate.lookup_key.startswith(prefix) else \
        candidate.lookup_key


def _code_intelligence_candidates(
    result: GraphRetrievalResult, service: Any, goal: str, legacy_matches: List[Dict[str, Any]], vector_store: Any,
    query_emb: Optional[List[float]], fingerprint: Optional[str], top_k: int,
) -> Tuple[Dict[str, float], List[str]]:
    """Fills ``result`` from one fused Code Intelligence query over the
    workspace's current structure; returns (direct file scores in rank
    order, expansion seed files). Member hints are the candidates' member
    ids, verified against the CURRENT bytes' member boundaries (verified vs
    hypothesis exactly as for the legacy header names)."""
    from kriya.code_intel import locate as loc
    from kriya.code_intel.model import CALLABLE_KINDS
    from kriya.workflow.context_source import boundaries_matching_member_id, member_boundaries_for

    view = service.current_view()
    semantic = (vector_store.query(query_emb, top_k=loc.SEMANTIC_CHUNKS, fingerprint=fingerprint)
                if query_emb and fingerprint else [])
    candidates = fused_localization(view, goal, semantic)
    result.localization_source = LOCALIZATION_CODE_INTELLIGENCE
    result.localization = candidates
    result.separation = separation(candidates)
    direct: Dict[str, float] = {}
    boundaries_by_path: Dict[str, Any] = {}
    for rank, candidate in enumerate(candidates):
        verified, data, structure, member_id = False, None, None, ""
        if candidate.kind in CALLABLE_KINDS:
            data = view.current_bytes(candidate.path)
            structure = view.current_structure(candidate.path)
            if data is not None and structure is not None:
                member_id = member_id_for(candidate, structure.namespace)
                if candidate.path not in boundaries_by_path:
                    boundaries_by_path[candidate.path] = member_boundaries_for(
                        candidate.path, data.decode("utf-8", "replace")) or []
                verified = bool(boundaries_matching_member_id(boundaries_by_path[candidate.path], member_id))
                if verified:
                    result.current_member_ids[candidate.symbol_id] = member_id
        # Like the legacy leg: the top_k hits (not top_k distinct files) are
        # the direct evidence; the rest stay localization candidates only.
        if rank >= top_k:
            continue
        direct.setdefault(candidate.path, candidate.score)
        result.retrieved_chunks.append({"filepath": candidate.path, "score": candidate.score,
                                        "text": f"{candidate.kind} {candidate.lookup_key}: {candidate.signature}"[:300],
                                        "channels": [c for c, _ in candidate.channels]})
        if candidate.kind not in CALLABLE_KINDS or data is None or structure is None:
            continue
        names = result.retrieval_member_hints.setdefault(candidate.path, [])
        if member_id not in names:
            names.append(member_id)
        bucket = result.verified_grounding if verified else result.hypothesis_candidates
        if member_id not in bucket.setdefault(candidate.path, []):
            bucket[candidate.path].append(member_id)
    # The legacy leg, bounded: only files the structural index does not
    # cover (build files, docs, other languages). Its hits are similarity
    # evidence (vector + BM25 RRF), so they rank in the similarity tier -
    # below any exact structural evidence, competing with BM25/vector hits.
    structural = view.structured_paths()
    legacy_scores: Dict[str, float] = {}
    for match in legacy_matches:
        path = match.get("filepath")
        if path and path not in structural:
            legacy_scores[path] = max(legacy_scores.get(path, 0.0), match.get("score", 0.0))
    legacy_only = list(legacy_scores)[:top_k]
    best_legacy = max(legacy_scores.values(), default=0.0)
    for path in legacy_only:
        score = loc.SIMILARITY_MAX * legacy_scores[path] / best_legacy if best_legacy > 0 else 0.0
        direct[path] = max(direct.get(path, 0.0), score)
    direct = dict(sorted(direct.items(), key=lambda item: -item[1]))
    result.legacy_only_files = legacy_only
    for match in legacy_matches:
        if match.get("filepath") in legacy_only:
            result.retrieved_chunks.append({"filepath": match["filepath"], "score": match.get("score", 0.0),
                                            "text": match.get("text", "")[:300], "channels": ["legacy_hybrid"]})
    seeds, result.expansion_seed_reason = select_fused_expansion_seeds(
        candidates, top_k, [m for m in legacy_matches if m.get("filepath") in legacy_only])
    return direct, seeds


async def localization_candidates(cfg: Any, workspace_path: str, text: str,
                                  limit: int = LOCALIZATION_LIMIT) -> List[LocalizationCandidate]:
    """The fused Code Intelligence candidates for ``text`` over the
    configured index (``paths.memory``) - the same query retrieval runs, for
    a caller that needs only localization (the enforce Planner's grounding).
    Empty without a structural index; the vector channel joins only when the
    vector index has the served embedding identity (EMBEDDING-CONTRACT-001).
    Localization context only, never write authority."""
    from kriya.code_intel import locate as loc

    memory = cfg.paths.memory
    service = open_code_intelligence(workspace_path, os.path.join(memory, "dependency_graph.db"))
    if service is None:
        return []
    try:
        semantic: List[Dict[str, Any]] = []
        vector_path = os.path.join(memory, "vector_index.db")
        if os.path.exists(vector_path):
            from kriya.memory.embedding import configured_client, run_deadline
            from kriya.memory.vector import LocalVectorStore

            store = LocalVectorStore(vector_path)
            try:
                query_emb, fingerprint = await semantic_query_embedding(
                    configured_client(cfg), store, text, GraphRetrievalResult(), run_deadline(cfg))
                if query_emb and fingerprint:
                    semantic = store.query(query_emb, top_k=loc.SEMANTIC_CHUNKS, fingerprint=fingerprint)
            finally:
                store.close()
        return fused_localization(service.current_view(), text, semantic, limit)
    finally:
        service.close()


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
    deadline: Optional[float] = None,
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
    query_emb, fingerprint = await semantic_query_embedding(embed_client, vector_store, goal, result, deadline)
    matches = vector_store.query_hybrid(
        goal, query_emb, top_k=limits.top_k, model_name=embedding_model,
        dimensions=len(query_emb) if query_emb else 0, fingerprint=fingerprint,
    )
    good_matches = [m for m in matches if m.get("score", 0.0) > 0.0]
    service = open_code_intelligence(workspace_path, dependency_graph_path)
    if service is not None:
        try:
            direct_scores, seed_files = _code_intelligence_candidates(
                result, service, goal, good_matches, vector_store, query_emb, fingerprint, limits.top_k)
        finally:
            service.close()
        if not direct_scores:
            return result
        matched_files_list = list(direct_scores)
    else:
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
        # Matched-file relevance: the best (max) hybrid RRF score across that
        # file's own matched chunks.
        direct_scores = {}
        for m in good_matches:
            fp = m.get("filepath")
            if fp:
                direct_scores[fp] = max(direct_scores.get(fp, 0.0), m.get("score", 0.0))
        # PRD027-PRECISION-001: only corroborated hits (or a single leg's own
        # hits when the other leg found nothing) seed the walk; every matched
        # file is still packaged below.
        seed_files, result.expansion_seed_reason = select_expansion_seeds(good_matches, limits.top_k)
    related_files_set = set()
    expanded_scores: Dict[str, float] = {}
    result.expansion_seed_files = seed_files
    if seed_files and dependency_graph_path and os.path.exists(dependency_graph_path):
        from kriya.analyzer.graph import DependencyGraph
        graph = DependencyGraph(dependency_graph_path)
        try:
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
        finally:
            graph.close()  # RESOURCE-SQLITE-CLOSE-001
        for n in neighbors:
            fp = n.get("filepath")
            if fp and fp not in matched_files_list:
                related_files_set.add(fp)
                expanded_scores[fp] = max(expanded_scores.get(fp, 0.0), n.get("score", 0.0))

    result.matched = True
    result.matched_files = matched_files_list
    result.related_files = list(related_files_set)
    result.file_scores = evidence_scores(direct_scores, expanded_scores)
    result.graph_rag_context, result.context_package = result.rebuild_context(workspace_path, budget_limit())
    return result
