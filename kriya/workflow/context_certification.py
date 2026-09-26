"""PRD-027: context-recall certification.

This module measures whether Kriya's production retrieval
(``graph_retrieval.retrieve_graph_context`` over an index built by
``RepositoryAnalyzer.index_repository``) delivers the evidence a task needs,
at a useful precision tier. It does so independently of any model: no LLM
is ever called here.

For every case in ``context_recall_fixtures``, each golden item is a HIT,
or a MISS with a typed reason:
- NOT_RETRIEVED: neither the query nor the graph surfaced the file.
- BUDGET_EXHAUSTED: surfaced, but cut for the token budget.
- TIER_INSUFFICIENT: shown, but below the precision the item needs.
- SOURCE_UNAVAILABLE: surfaced but unreadable.
Precision is the share of packaged files that are golden or declared
acceptable, so indiscriminate retrieval cannot pass.

The class targets (``CLASS_RECALL_TARGETS``) and ``PRECISION_TARGET`` are
fixed and version-controlled; they are never derived from a measurement.

There are two embedders, and they certify different things:
- ``DeterministicHashingEmbedder`` (CI) proves the lexical, graph and budget
  mechanics only. Its reports are marked ``embedder: deterministic_hashing``
  and can never satisfy production certification.
- The configured embedding model (``kriya context certify``) produces the
  production record. It is bound to the exact embedding runtime and
  dimension, the retrieval limits, the context-tier policy, the
  index/chunker/retrieval implementation and the fixture suite - never to
  the chat model, which retrieval does not use - and it is stored outside
  the workspace under the state directory.
``doctor --production`` only reads that record (``certification_status``),
and never runs the benchmark.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import math
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from kriya.workflow.context_budget import RetrievalLimits
from kriya.workflow.context_recall_fixtures import (
    BUILD_METADATA,
    CONFIGURATION,
    CONTEXT_CLASSES,
    DIRECT_CALLER,
    INTERFACE_CONTRACT,
    ONE_HOP_DEPENDENCY,
    REPOSITORIES,
    SAME_CLASS_MEMBER,
    SIBLING_IMPLEMENTATION,
    TEST_PRECEDENT,
    TWO_HOP_DEPENDENCY,
    RecallCase,
    RecallRepository,
    fixtures_digest,
)

CERTIFICATION_SUITE_VERSION = "context-recall/1"
CERTIFICATION_RECORD_VERSION = 1

# Fixed, version-controlled targets (PRD-027 requirement 4). The evidence a
# brownfield edit cannot be correct without must always be found; wider
# context must be found at least half the time.
CLASS_RECALL_TARGETS: Dict[str, float] = {
    SAME_CLASS_MEMBER: 1.0,
    INTERFACE_CONTRACT: 1.0,
    DIRECT_CALLER: 1.0,
    ONE_HOP_DEPENDENCY: 1.0,
    SIBLING_IMPLEMENTATION: 0.5,
    TWO_HOP_DEPENDENCY: 0.5,
    TEST_PRECEDENT: 0.5,
    BUILD_METADATA: 0.5,
    CONFIGURATION: 0.5,
}
PRECISION_TARGET = 0.5

HIT = "HIT"
NOT_RETRIEVED = "NOT_RETRIEVED"
BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
TIER_INSUFFICIENT = "TIER_INSUFFICIENT"
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"

_TIER_RANK = {"signatures": 1, "skeleton": 2, "full": 3}

EMBEDDER_CONFIGURED = "configured"
EMBEDDER_DETERMINISTIC = "deterministic_hashing"

# Certification status (doctor).
STATUS_CERTIFIED = "CERTIFIED"
STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"
STATUS_MISSING = "MISSING"
STATUS_FAILED = "FAILED"
STATUS_UNAVAILABLE = "IDENTITY_UNAVAILABLE"

DEFAULT_RETRIEVAL_LIMITS = RetrievalLimits(top_k=5, max_hops=2, max_neighborhood_results=30)
# The version-controlled context-tier policy certification measures under:
# the graph-context share (PRD-016) of the packaged default 32K primary
# window. Production scales this with the chat model's served window; that
# budget input is PRD-016's, not a retrieval input (see
# certification_identity).
CERTIFICATION_GRAPH_BUDGET_TOKENS = 10644


class DeterministicHashingEmbedder:
    """A reproducible bag-of-identifier-words embedding for CI. camelCase and
    snake_case are split into words, and each word is feature-hashed into a
    signed dimension; the vector is L2-normalized. It carries no semantics a
    real model would learn, so it certifies retrieval mechanics only."""

    def __init__(self, dimensions: int = 256) -> None:
        self.dimensions = dimensions

    def _vector(self, text: str) -> List[float]:
        vector = [0.0] * self.dimensions
        for word in _words(text):
            digest = hashlib.sha256(word.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            vector[index] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    async def get_embedding(self, text: str, is_query: bool = False) -> List[float]:
        del is_query
        return self._vector(text)

    async def get_embeddings(self, texts: Sequence[str]) -> List[List[float]]:
        return [self._vector(text) for text in texts]


_WORD_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def _words(text: str) -> List[str]:
    return [word.lower() for word in _WORD_RE.findall(text) if len(word) > 1]


@dataclass(frozen=True)
class ItemOutcome:
    path: str
    context_class: str
    required_precision: str
    outcome: str
    tier: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path, "context_class": self.context_class,
            "required_precision": self.required_precision, "outcome": self.outcome, "tier": self.tier,
        }


@dataclass(frozen=True)
class CaseResult:
    repository: str
    case: str
    items: Tuple[ItemOutcome, ...]
    packaged_files: Tuple[str, ...]
    relevant_packaged: int

    @property
    def precision(self) -> float:
        return self.relevant_packaged / len(self.packaged_files) if self.packaged_files else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repository": self.repository, "case": self.case,
            "items": [item.to_dict() for item in self.items],
            "packaged_files": list(self.packaged_files), "precision": round(self.precision, 4),
        }


def score_case(repository: str, case: RecallCase, package: Any, retrieved_files: Sequence[str]) -> CaseResult:
    """Classify every golden item of ``case`` against the retrieval output.
    ``package`` is the ContextPackage the Developer would be shown (None when
    nothing matched); ``retrieved_files`` is every file retrieval surfaced
    before budgeting."""
    shown = {item.path: item.tier for item in (package.relevant_files if package is not None else ())}
    omitted = {entry["path"]: entry["reason"] for entry in (package.omitted if package is not None else ())
               if entry.get("reason") != "body_elided"}
    outcomes = []
    for golden in case.golden:
        tier = shown.get(golden.path)
        if tier is not None:
            outcome = HIT if _TIER_RANK.get(tier, 0) >= _TIER_RANK[golden.precision] else TIER_INSUFFICIENT
        elif omitted.get(golden.path) == "budget_exhausted":
            outcome = BUDGET_EXHAUSTED
        elif omitted.get(golden.path) == "source_unavailable":
            outcome = SOURCE_UNAVAILABLE
        else:
            outcome = NOT_RETRIEVED
        outcomes.append(ItemOutcome(golden.path, golden.context_class, golden.precision, outcome, tier))
    relevant = {item.path for item in case.golden} | set(case.acceptable)
    del retrieved_files  # surfaced-but-unshown files are already classified via omitted
    return CaseResult(
        repository=repository, case=case.name, items=tuple(outcomes),
        packaged_files=tuple(sorted(shown)), relevant_packaged=sum(1 for path in shown if path in relevant),
    )


@dataclass
class CertificationReport:
    identity: Dict[str, Any]
    cases: List[CaseResult] = field(default_factory=list)

    def class_recall(self) -> Dict[str, Dict[str, Any]]:
        summary: Dict[str, Dict[str, Any]] = {}
        for context_class in CONTEXT_CLASSES:
            items = [item for case in self.cases for item in case.items if item.context_class == context_class]
            hits = sum(1 for item in items if item.outcome == HIT)
            misses: Dict[str, int] = {}
            for item in items:
                if item.outcome != HIT:
                    misses[item.outcome] = misses.get(item.outcome, 0) + 1
            recall = hits / len(items) if items else 0.0
            summary[context_class] = {
                "golden": len(items), "hits": hits, "recall": round(recall, 4),
                "target": CLASS_RECALL_TARGETS[context_class], "misses": misses,
                "passed": bool(items) and recall >= CLASS_RECALL_TARGETS[context_class],
            }
        return summary

    def precision(self) -> float:
        packaged = sum(len(case.packaged_files) for case in self.cases)
        relevant = sum(case.relevant_packaged for case in self.cases)
        return relevant / packaged if packaged else 0.0

    def certified(self) -> bool:
        return (
            all(entry["passed"] for entry in self.class_recall().values())
            and self.precision() >= PRECISION_TARGET
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "record_version": CERTIFICATION_RECORD_VERSION,
            "identity": self.identity,
            "identity_digest": identity_digest(self.identity),
            "certified": self.certified(),
            "precision": round(self.precision(), 4),
            "precision_target": PRECISION_TARGET,
            "classes": self.class_recall(),
            "cases": [case.to_dict() for case in self.cases],
        }


def index_implementation_digest() -> str:
    """Source identity of everything between repository bytes and the
    Developer's context: chunking, graph indexing, the hybrid query,
    retrieval and context assembly. Changing any of them invalidates a
    stored certification."""
    from kriya.analyzer.analyzer import RepositoryAnalyzer, chunk_file_with_metadata_headers
    from kriya.analyzer.graph import DependencyGraph
    from kriya.memory.vector import LocalVectorStore, lexical_query_terms
    from kriya.workflow import graph_retrieval
    from kriya.workflow.context_budget import build_code_context_package

    # Both legs of the hybrid query and the whole graph_retrieval module
    # (retrieve_graph_context, the expansion-seed rule and its constants).
    sources = [
        inspect.getsource(obj) for obj in (
            chunk_file_with_metadata_headers, RepositoryAnalyzer.index_repository, DependencyGraph,
            LocalVectorStore.query, LocalVectorStore.query_lexical, lexical_query_terms,
            LocalVectorStore.query_hybrid, graph_retrieval, build_code_context_package,
        )
    ]
    return hashlib.sha256("\n".join(sources).encode("utf-8")).hexdigest()


def retrieval_policies(config: Any) -> Tuple[RetrievalLimits, ...]:
    """Every retrieval-limit policy a production run under ``config`` can
    use: the default, plus each context-depth variant when process profiles
    enforce context depth."""
    policies = [DEFAULT_RETRIEVAL_LIMITS]
    profiles = getattr(config, "process_profiles", None)
    if profiles is not None and profiles.enabled and profiles.enforce_context_depth:
        from kriya.workflow.context_budget import _RETRIEVAL_LIMITS_BY_DEPTH
        policies.extend(limits for limits in _RETRIEVAL_LIMITS_BY_DEPTH.values() if limits not in policies)
    return tuple(policies)


def embedding_runtime_identity(config: Any) -> str:
    """The exact embedding runtime digest (PRD-013 fingerprint), or
    "unavailable" when it cannot be proven. A model name is never used as
    identity."""
    from kriya.core.model_runtime import resolve_configured_model_runtime

    try:
        fingerprint = resolve_configured_model_runtime(
            config, config.embedding.model, base_url=config.embedding.base_url, api_key="", extra_body={},
        )
    except Exception:  # identity stays unproven; the caller fails closed on it
        return "unavailable"
    return fingerprint.digest if getattr(fingerprint, "exact", False) else "unavailable"


def indexed_embedding_dimensions(config: Any) -> Optional[int]:
    """The single embedding dimension the production code index holds for
    the configured embedding model, read from the index (no probe); None
    when it holds none, or more than one."""
    import sqlite3

    path = os.path.join(config.paths.memory, "vector_index.db")
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                "SELECT DISTINCT dimensions FROM vector_chunks WHERE model_name = ?", (config.embedding.model,),
            ).fetchall()
    except sqlite3.Error:
        return None
    return int(rows[0][0]) if len(rows) == 1 else None


def certification_identity(
    config: Any, *, embedder: str, embedding_runtime: str, embedding_dimensions: Optional[int],
) -> Dict[str, Any]:
    """Exactly the inputs that can change retrieval behaviour: the suite and
    fixtures, the index/chunker/graph/retrieval/assembly implementation,
    the exact embedding runtime and its dimension, the retrieval limits and
    the context-tier policy. The chat model is not an input: Graph RAG makes
    no chat inference (no query rewriting, scoring or selection by a chat
    model). Its only production influence is the token budget via its
    served window (PRD-016); certification pins the version-controlled
    reference budget instead, so a chat-model change or requalification
    never stales a certification."""
    from kriya import __version__

    return {
        "suite_version": CERTIFICATION_SUITE_VERSION,
        "kriya_version": __version__,
        "fixtures_digest": fixtures_digest(),
        "index_implementation": index_implementation_digest(),
        "embedder": embedder,
        "embedding_model": config.embedding.model,
        "embedding_runtime": embedding_runtime,
        "embedding_dimensions": embedding_dimensions,
        "retrieval_policies": [
            [limits.top_k, limits.max_hops, limits.max_neighborhood_results] for limits in retrieval_policies(config)
        ],
        "context_tier_policy": {
            "reference_graph_budget_tokens": CERTIFICATION_GRAPH_BUDGET_TOKENS,
            "tiers": list(_TIER_RANK),
        },
    }


def identity_digest(identity: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()


async def _index_repository(repository: RecallRepository, root: str, config: Any, embedder: Any) -> Any:
    from kriya.analyzer.analyzer import RepositoryAnalyzer

    for path, content in repository.files:
        full = os.path.join(root, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(content)
    repo_config = config.model_copy(deep=True)
    repo_config.paths.memory = os.path.join(root, ".kriya-cert-memory")
    repo_config.paths.skills = os.path.join(root, ".kriya-cert-skills")
    os.makedirs(repo_config.paths.memory, exist_ok=True)
    await RepositoryAnalyzer(root).index_repository(
        repo_config, force=True, embedding_client=embedder, generate_conventions_skill=False,
    )
    return repo_config


async def run_certification(
    config: Any, *, embedding_client: Any, embedder: str, embedding_runtime: str,
    repositories: Tuple[RecallRepository, ...] = REPOSITORIES,
) -> CertificationReport:
    """Index every fixture repository with ``embedding_client``, run
    production retrieval for every case under every production retrieval
    policy, and score the result. No model is called."""
    from kriya.memory.vector import LocalVectorStore
    from kriya.workflow.graph_retrieval import retrieve_graph_context

    dimensions = len(await embedding_client.get_embedding("kriya context certification", is_query=True))
    report = CertificationReport(identity=certification_identity(
        config, embedder=embedder, embedding_runtime=embedding_runtime, embedding_dimensions=dimensions,
    ))
    budget = CERTIFICATION_GRAPH_BUDGET_TOKENS
    with tempfile.TemporaryDirectory(prefix="kriya-context-cert-") as scratch:
        for repository in repositories:
            root = os.path.join(scratch, repository.name)
            repo_config = await _index_repository(repository, root, config, embedding_client)
            store = LocalVectorStore(os.path.join(repo_config.paths.memory, "vector_index.db"))
            try:
                for limits in retrieval_policies(config):
                    for case in repository.cases:
                        retrieval = await retrieve_graph_context(
                            case.goal, root, embed_client=embedding_client, vector_store=store,
                            dependency_graph_path=os.path.join(repo_config.paths.memory, "dependency_graph.db"),
                            limits=limits, embedding_model=config.embedding.model, budget_limit=lambda: budget,
                        )
                        report.cases.append(score_case(
                            repository.name, case, retrieval.context_package,
                            retrieval.matched_files + retrieval.related_files,
                        ))
            finally:
                store.close()
    return report


def certification_directory(config: Any) -> str:
    from kriya.core.state_paths import resolve_state_directory

    state_dir, _origin = resolve_state_directory(config)
    return os.path.join(state_dir, "context_certification")


def save_certification(config: Any, report: CertificationReport) -> str:
    """Persist ``report`` under the state directory (outside the workspace),
    keyed by its identity digest; returns the record path. Atomic."""
    data = report.to_dict()
    data["recorded_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    directory = certification_directory(config)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{data['identity_digest']}.json")
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
    os.replace(temporary, path)
    return path


def retrieval_applicable(config: Any) -> bool:
    """Graph RAG retrieval runs exactly when a code index exists at
    ``paths.memory`` (workflow.py's retrieval stage)."""
    return os.path.exists(os.path.join(config.paths.memory, "vector_index.db"))


def certification_status(config: Any) -> Tuple[str, str]:
    """(status, detail) for ``doctor --production``: reads a stored record,
    never runs the benchmark."""
    if not retrieval_applicable(config):
        return STATUS_NOT_APPLICABLE, "no code index at paths.memory - Graph RAG retrieval is not used"
    runtime = embedding_runtime_identity(config)
    if runtime == "unavailable":
        return STATUS_UNAVAILABLE, (
            f"embedding runtime identity of {config.embedding.model} cannot be proven - "
            "a certification cannot be matched to it"
        )
    dimensions = indexed_embedding_dimensions(config)
    if dimensions is None:
        return STATUS_FAILED, (
            f"the code index at paths.memory holds no single embedding dimension for {config.embedding.model} - "
            "re-index with `kriya analyze`"
        )
    identity = certification_identity(
        config, embedder=EMBEDDER_CONFIGURED, embedding_runtime=runtime, embedding_dimensions=dimensions,
    )
    path = os.path.join(certification_directory(config), f"{identity_digest(identity)}.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            record = json.load(handle)
    except FileNotFoundError:
        return STATUS_MISSING, (
            "no context-recall certification for this exact embedding runtime, retrieval policy, "
            "index implementation and suite - run `kriya context certify`"
        )
    except (OSError, ValueError) as error:
        return STATUS_FAILED, f"certification record unreadable ({type(error).__name__}): {path}"
    if record.get("identity") != identity or not record.get("certified"):
        failing = sorted(name for name, entry in (record.get("classes") or {}).items() if not entry.get("passed"))
        return STATUS_FAILED, (
            f"certification recorded but not passing (failing classes: {', '.join(failing) or 'none'}; "
            f"precision {record.get('precision')} vs {PRECISION_TARGET})"
        )
    return STATUS_CERTIFIED, f"certified {record.get('recorded_at')} ({os.path.basename(path)})"
