"""CTX-001 P0 instrumented probes over REAL, UNMODIFIED kriya/ production code.

Nothing in kriya/ is edited. The only thing monkeypatched is the network call
inside OllamaEmbeddingClient.get_embedding (so index_repository() can run
end-to-end deterministically, with no live embedding endpoint) and a counting
wrapper around builtins.open (read-only instrumentation, delegates to the
real open()). Everything else - os.walk, gitignore filtering, mtime/hash
caching, AST/regex symbol extraction, chunking, skeletonization, RRF fusion,
build_code_context's budget allocator - runs exactly as it does in production.
"""
import builtins
import contextlib
import hashlib
import os
import sqlite3
import struct
import sys
import time
from typing import Any, Dict, List, Optional

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

from kriya.analyzer.analyzer import RepositoryAnalyzer  # noqa: E402
from kriya.analyzer.graph import DependencyGraph  # noqa: E402
from kriya.config.config import AppConfig  # noqa: E402
from kriya.memory.vector import LocalVectorStore, OllamaEmbeddingClient  # noqa: E402
from kriya.workflow.context_budget import build_code_context, estimate_tokens  # noqa: E402

FAKE_DIM = 64


def _deterministic_vector(text: str, dim: int = FAKE_DIM) -> List[float]:
    """A fast, seeded pseudo-embedding - same text always maps to the same
    vector, different text maps to a different vector, with no network call
    and no dependence on a real embedding model. Sufficient for measuring
    RETRIEVAL cost (query_hybrid's O(N) scan, RRF fusion, ranking) which is
    purely a function of vector shape/count, not embedding quality - it is
    NOT a substitute for measuring retrieval PRECISION against a real model,
    which needs the live validation step handed to the user."""
    h = hashlib.sha256(text.encode("utf-8")).digest()
    vals = []
    for i in range(dim):
        byte_pair = h[(i * 2) % len(h): (i * 2) % len(h) + 2]
        if len(byte_pair) < 2:
            byte_pair = byte_pair + b"\x00"
        v = struct.unpack(">H", byte_pair)[0]
        vals.append((v / 65535.0) * 2 - 1)
    return vals


@contextlib.contextmanager
def patched_embedding_client():
    """Patches BOTH get_embedding (singular - used for the one-shot dimension
    probe and query-time embedding) AND get_embeddings (plural, batched - the
    method index_repository()'s real per-chunk indexing loop actually calls).
    Missing the plural form was a real bug caught while building this harness:
    the singular-only patch left every chunk's embedding going through the
    REAL httpx.AsyncClient().post() call in get_embeddings, i.e. a genuine
    network attempt to base_url on every indexing run - exactly what CTX-001
    P0 is required to never do, regardless of whether an Ollama instance
    happens to be reachable on this machine. Both methods are restored in
    the finally block; __init__ is also restored, not left patched."""
    async def fake_get_embedding(self, text, client=None, is_query=False):
        return _deterministic_vector(text, self.detected_dimensions or FAKE_DIM)

    async def fake_get_embeddings(self, texts, is_query=False):
        if not texts:
            return []
        return [_deterministic_vector(t, self.detected_dimensions or FAKE_DIM) for t in texts]

    original_get_embedding = OllamaEmbeddingClient.get_embedding
    original_get_embeddings = OllamaEmbeddingClient.get_embeddings
    original_init = OllamaEmbeddingClient.__init__
    OllamaEmbeddingClient.__init__ = _patched_init
    OllamaEmbeddingClient.get_embedding = fake_get_embedding
    OllamaEmbeddingClient.get_embeddings = fake_get_embeddings
    try:
        yield
    finally:
        OllamaEmbeddingClient.get_embedding = original_get_embedding
        OllamaEmbeddingClient.get_embeddings = original_get_embeddings
        OllamaEmbeddingClient.__init__ = original_init


def _patched_init(self, base_url: str, model: str) -> None:
    # Deliberately ignores the caller-supplied base_url - CTX-001 P0 must
    # make zero real network attempts, so this never even constructs a
    # reachable-looking address.
    self.base_url = "http://127.0.0.1:1"
    self.model = model
    self.detected_dimensions = FAKE_DIM


@contextlib.contextmanager
def counting_open(scoped_root: str):
    counters = {"opens": 0, "bytes_read": 0}
    real_open = builtins.open
    scoped_root_abs = os.path.abspath(scoped_root)

    def wrapped_open(file, mode="r", *args, **kwargs):
        try:
            path_str = os.fspath(file)
        except TypeError:
            path_str = None
        if path_str and os.path.abspath(path_str).startswith(scoped_root_abs) and "r" in mode:
            counters["opens"] += 1
        fh = real_open(file, mode, *args, **kwargs)
        return fh

    builtins.open = wrapped_open
    try:
        yield counters
    finally:
        builtins.open = real_open


@contextlib.contextmanager
def counting_walk(scoped_root: str):
    """os.walk never calls open() - analyze()'s cost is dominated by directory
    traversal + per-entry os.path.splitext/stat work, not file content reads
    (only 2 of its 4 sub-steps open any files at all). Counts every (dir,
    file) entry os.walk yields under scoped_root, as a proxy for that
    traversal cost, independent of and additive to counting_open's own
    file-content-read count."""
    counters = {"dirs_walked": 0, "file_entries_seen": 0}
    real_walk = os.walk
    scoped_root_abs = os.path.abspath(scoped_root)

    def wrapped_walk(top, *args, **kwargs):
        top_abs = os.path.abspath(top)
        for dirpath, dirnames, filenames in real_walk(top, *args, **kwargs):
            if os.path.abspath(dirpath).startswith(scoped_root_abs) or top_abs.startswith(scoped_root_abs):
                counters["dirs_walked"] += 1
                counters["file_entries_seen"] += len(filenames)
            yield dirpath, dirnames, filenames

    os.walk = wrapped_walk
    try:
        yield counters
    finally:
        os.walk = real_walk


def make_cfg(memory_dir: str, skills_dir: str, repo_root: str) -> AppConfig:
    """CTX-001 P0 must make zero live LLM calls. index_repository() has exactly
    one LLM call site (analyzer.py's unconditional "Auto-Generate Codebase
    Conventions Skill" step, gated only on `not os.path.exists(auto_skill_dir)`)
    - pre-creating that directory is the production-respecting way to take that
    branch, not a patch to kriya/ itself. llm.base_url is ALSO pointed at a
    guaranteed-unreachable port as defense-in-depth, so this stays true even if
    a future code path adds a second LLM call site this investigation didn't
    trace."""
    cfg = AppConfig()
    cfg.paths.memory = memory_dir
    cfg.paths.skills = skills_dir
    cfg.embedding.model = "nomic-embed-text:latest"
    cfg.embedding.base_url = "http://127.0.0.1:1/v1"  # deliberately unreachable - defense in depth
    cfg.llm.base_url = "http://127.0.0.1:1/v1"  # deliberately unreachable - defense in depth
    os.makedirs(memory_dir, exist_ok=True)
    os.makedirs(skills_dir, exist_ok=True)
    repo_slug = os.path.basename(os.path.abspath(repo_root)).lower().strip(".") or "root"
    auto_skill_dir = os.path.join(skills_dir, f"auto-{repo_slug}")
    os.makedirs(auto_skill_dir, exist_ok=True)
    return cfg


def db_counts(memory_dir: str) -> Dict[str, int]:
    result = {"symbols": 0, "relations": 0, "files_graph": 0, "vector_chunks": 0, "files_vector": 0}
    graph_db = os.path.join(memory_dir, "dependency_graph.db")
    if os.path.exists(graph_db):
        conn = sqlite3.connect(graph_db)
        try:
            for table, key in (("symbols", "symbols"), ("relations", "relations"), ("files", "files_graph")):
                try:
                    result[key] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                except sqlite3.OperationalError:
                    pass
        finally:
            conn.close()
    vec_db = os.path.join(memory_dir, "vector_index.db")
    if os.path.exists(vec_db):
        conn = sqlite3.connect(vec_db)
        try:
            for table, key in (("vector_chunks", "vector_chunks"), ("file_metadata", "files_vector")):
                try:
                    result[key] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                except sqlite3.OperationalError:
                    pass
        finally:
            conn.close()
    return result


async def run_analyze(root: str) -> Dict[str, Any]:
    with counting_open(root) as counters, counting_walk(root) as walk_counters:
        t0 = time.perf_counter()
        analyzer = RepositoryAnalyzer(root)
        model = analyzer.analyze()
        elapsed = time.perf_counter() - t0
    return {
        "elapsed_s": elapsed,
        "opens": counters["opens"],
        "dirs_walked": walk_counters["dirs_walked"],
        "file_entries_seen": walk_counters["file_entries_seen"],
        "total_files_indexed": model.project_structure.get("total_files_indexed"),
        "languages": model.languages,
    }


async def run_index_repository(root: str, memory_dir: str, skills_dir: Optional[str] = None, changed: bool = False, force: bool = False) -> Dict[str, Any]:
    cfg = make_cfg(memory_dir, skills_dir or os.path.join(memory_dir, "..", "skills"), root)
    with patched_embedding_client(), counting_open(root) as counters, counting_walk(root) as walk_counters:
        t0 = time.perf_counter()
        analyzer = RepositoryAnalyzer(root)
        await analyzer.index_repository(cfg, changed=changed, force=force)
        elapsed = time.perf_counter() - t0
    return {
        "elapsed_s": elapsed,
        "opens": counters["opens"],
        "dirs_walked": walk_counters["dirs_walked"],
        "file_entries_seen": walk_counters["file_entries_seen"],
        "db_counts": db_counts(memory_dir),
    }


def run_vector_query(memory_dir: str, query_text: str, top_k: int = 5) -> Dict[str, Any]:
    vec_db = os.path.join(memory_dir, "vector_index.db")
    store = LocalVectorStore(vec_db)
    try:
        q_emb = _deterministic_vector(query_text)
        t0 = time.perf_counter()
        matches = store.query_hybrid(query_text, q_emb, top_k=top_k, model_name="nomic-embed-text:latest", dimensions=FAKE_DIM)
        elapsed = time.perf_counter() - t0
        total_rows = store.conn.execute("SELECT COUNT(*) FROM vector_chunks").fetchone()[0]
    finally:
        store.close()
    return {"elapsed_s": elapsed, "total_indexed_chunks_scanned": total_rows, "matches_returned": len(matches), "matches": matches}


def run_graph_neighborhood(memory_dir: str, seed_symbols: List[str], max_hops: int = 2, max_results: int = 30) -> Dict[str, Any]:
    graph_db = os.path.join(memory_dir, "dependency_graph.db")
    graph = DependencyGraph(graph_db)
    try:
        t0 = time.perf_counter()
        results = graph.get_neighborhood(seed_symbols, max_hops=max_hops, max_results=max_results)
        elapsed = time.perf_counter() - t0
    finally:
        graph.close()
    return {"elapsed_s": elapsed, "results_count": len(results), "results": results}


def run_build_code_context(matched_files: List[str], related_files: List[str], root: str, budget_tokens: int) -> Dict[str, Any]:
    with counting_open(root) as counters:
        t0 = time.perf_counter()
        ctx = build_code_context(matched_files, related_files, root, budget_tokens)
        elapsed = time.perf_counter() - t0
    return {
        "elapsed_s": elapsed,
        "opens": counters["opens"],
        "output_tokens_est": estimate_tokens(ctx),
        "output_chars": len(ctx),
        "context": ctx,
    }
