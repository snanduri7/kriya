"""CTX-001 P0 - Live Retrieval Validation. USER-RUN ONLY - the agent that
prepared this script did not execute it (embedding calls are live network
calls; CTX-001 P0's own constraints forbid the agent from making any).

Scope, stated precisely per the task that requested this script: this
validates ONE thing only - whether query_hybrid()'s REAL vector half ranks
the real, known-relevant file (core/billing/invoice_impl.py) usefully against
increasing amounts of real, unrelated repository content, using the actual
configured embedding endpoint. It does NOT validate Developer coding
quality, F8 model behavior, MODEL-001, end-to-end CTX correctness, or
context-budget correctness - see CTX_001_P0_CURRENT_STATE.md for those.

Reuses fixtures.py's S1 fixture generator and CORE_GROUND_TRUTH verbatim -
no fixture logic is duplicated here. The SAME query
(fixtures.CORE_GROUND_TRUTH["goal"]) is used for every band, unmodified.

SAFETY (verify with `python3 run_live_retrieval_validation.py --safety-check`,
which touches no network and makes no embedding call):
  - The only network-capable calls in this script's own execution path are
    OllamaEmbeddingClient.get_embedding()/get_embeddings() (embedding
    endpoint only). Grep confirms: no import of ConventionsExtractorAgent,
    LLMClient, or any agent/chat class anywhere in this file.
  - index_repository() (kriya/analyzer/analyzer.py:624) has exactly one
    completion/chat LLM call site of its own - the auto-skill-generation
    block, gated on `if not os.path.exists(auto_skill_dir)`
    (analyzer.py:895). This script pre-creates that exact directory before
    calling index_repository(), for every band, so that branch is always
    skipped via the production code's own existing condition - not a patch
    to kriya/.
  - cfg.llm.base_url is additionally pointed at a deliberately unreachable
    address, as defense in depth, so even an untraced future completion
    call site would fail fast rather than silently reach a real endpoint.
  - embedding.base_url/embedding.model are left as whatever the user's real
    kriya.yaml (or the packaged default) configures - deliberately NOT
    overridden, since the whole point of this script is to exercise the
    actual configured embedding endpoint.
"""
import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from typing import Any, Dict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, REPO_ROOT)

import fixtures  # noqa: E402 - existing S1 fixture generator, reused verbatim

UNREACHABLE_LLM_BASE_URL = "http://127.0.0.1:1/v1"
GROUND_TRUTH_FILE = "core/billing/invoice_impl.py"


def repo_slug_for(root: str) -> str:
    """Identical derivation to RepositoryAnalyzer.index_repository()'s own
    repo_slug (kriya/analyzer/analyzer.py:889-891) - must match exactly for
    the auto-skill-directory pre-creation below to actually land on the
    same path that function's own `if not os.path.exists(auto_skill_dir)`
    gate checks."""
    return os.path.basename(os.path.abspath(root)).lower().strip(".") or "root"


def build_cfg(memory_dir: str, skills_dir: str, repo_root: str):
    """Real, user-configured embedding settings (via load_config(), falling
    back to packaged defaults if that fails for any reason - e.g. this
    script's own CWD/authority context - since paths.memory/skills are
    overridden either way). llm.base_url is forced unreachable regardless -
    this script must never be able to reach a real completion endpoint."""
    try:
        from kriya.config.config import load_config
        cfg = load_config()
    except Exception:
        from kriya.config.config import AppConfig
        cfg = AppConfig()

    cfg.paths.memory = memory_dir
    cfg.paths.skills = skills_dir
    cfg.llm.base_url = UNREACHABLE_LLM_BASE_URL

    os.makedirs(memory_dir, exist_ok=True)
    os.makedirs(skills_dir, exist_ok=True)
    slug = repo_slug_for(repo_root)
    auto_skill_dir = os.path.join(skills_dir, f"auto-{slug}")
    os.makedirs(auto_skill_dir, exist_ok=True)
    return cfg, auto_skill_dir


def safety_check() -> bool:
    """Static, zero-network, zero-embedding self-check - safe for the agent
    (or anyone) to run without violating CTX-001 P0's live-call prohibition.
    Confirms: (1) no chat/completion agent class is imported anywhere in
    this file's own source, (2) build_cfg() actually forces llm.base_url
    unreachable, (3) build_cfg() actually pre-creates the exact auto-skill
    directory index_repository()'s own LLM-call gate checks."""
    ok = True

    # AST-based import/call detection, not naive substring search: a naive
    # substring check on this file's own source would false-positive on
    # this very function's own forbidden-name list and this module's
    # docstring, which both necessarily CONTAIN those names as text.
    # Checking actual `import`/`ImportFrom`/`Attribute`/`Call` nodes avoids
    # that self-reference entirely and is the more rigorous check anyway.
    import ast as _ast

    forbidden_names = {
        "ConventionsExtractorAgent", "LLMClient", "PlannerAgent", "ArchitectAgent",
        "DeveloperAgent", "ReviewerAgent", "MilestonePlannerAgent", "SkillGapAgent",
        "SpecComplianceAgent", "RunVerifierAgent",
    }
    with open(os.path.abspath(__file__), "r", encoding="utf-8") as fh:
        own_source = fh.read()
    tree = _ast.parse(own_source, filename=os.path.abspath(__file__))
    found_forbidden = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            for alias in node.names:
                if alias.name.split(".")[-1] in forbidden_names:
                    found_forbidden.add(alias.name)
        elif isinstance(node, _ast.ImportFrom):
            for alias in node.names:
                if alias.name in forbidden_names:
                    found_forbidden.add(f"{node.module}.{alias.name}")
        elif isinstance(node, _ast.Name) and node.id in forbidden_names:
            found_forbidden.add(node.id)
        elif isinstance(node, _ast.Attribute) and node.attr == "complete":
            found_forbidden.add("<something>.complete(...) call")

    if found_forbidden:
        print(f"SAFETY CHECK FAILED: forbidden import/reference(s) present in script AST: {sorted(found_forbidden)}")
        ok = False
    else:
        print("SAFETY CHECK: AST scan found zero imports/references to any chat/completion "
              "agent class or .complete(...) call anywhere in this script. PASS")

    tmp = tempfile.mkdtemp(prefix="ctx001_live_safety_")
    try:
        root = os.path.join(tmp, "repo")
        os.makedirs(root, exist_ok=True)
        cfg, auto_skill_dir = build_cfg(os.path.join(tmp, "memory"), os.path.join(tmp, "skills"), root)
        if cfg.llm.base_url != UNREACHABLE_LLM_BASE_URL:
            print(f"SAFETY CHECK FAILED: cfg.llm.base_url is {cfg.llm.base_url!r}, expected unreachable sentinel")
            ok = False
        else:
            print(f"SAFETY CHECK: cfg.llm.base_url forced unreachable ({cfg.llm.base_url}). PASS")
        if not os.path.isdir(auto_skill_dir):
            print(f"SAFETY CHECK FAILED: auto_skill_dir {auto_skill_dir!r} was not created")
            ok = False
        else:
            print(f"SAFETY CHECK: auto-skill directory pre-created at {auto_skill_dir} "
                  "(index_repository()'s own LLM-call gate will see it as already-existing "
                  "and skip that branch). PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nSAFETY CHECK OVERALL: {'PASS' if ok else 'FAIL'}")
    return ok


async def run_band(name: str, filler_count: int) -> Dict[str, Any]:
    from kriya.analyzer.analyzer import RepositoryAnalyzer
    from kriya.memory.vector import LocalVectorStore, OllamaEmbeddingClient

    tmp = tempfile.mkdtemp(prefix=f"ctx001_live_{name}_")
    root = os.path.join(tmp, "repo")
    mem = os.path.join(tmp, "memory")
    skl = os.path.join(tmp, "skills")
    fixtures.build_s1_fixture(root, band_filler_count=filler_count)

    cfg, auto_skill_dir = build_cfg(mem, skl, root)
    assert os.path.isdir(auto_skill_dir), "auto-skill directory must pre-exist before indexing"

    analyzer = RepositoryAnalyzer(root)
    t0 = time.perf_counter()
    await analyzer.index_repository(cfg)
    index_elapsed = time.perf_counter() - t0

    store = LocalVectorStore(os.path.join(mem, "vector_index.db"))
    total_chunks = store.conn.execute("SELECT COUNT(*) FROM vector_chunks").fetchone()[0]

    client = OllamaEmbeddingClient(base_url=cfg.embedding.base_url, model=cfg.embedding.model)
    query_text = fixtures.CORE_GROUND_TRUTH["goal"]

    t0 = time.perf_counter()
    query_emb = await client.get_embedding(query_text, is_query=True)
    matches = store.query_hybrid(query_text, query_emb, top_k=10, model_name=cfg.embedding.model)
    retrieval_elapsed = time.perf_counter() - t0

    store.close()

    rank = None
    score = None
    for i, m in enumerate(matches, 1):
        if m.get("filepath") == GROUND_TRUTH_FILE:
            rank = i
            score = m.get("score")
            break

    total_files = analyzer.analyze().project_structure.get("total_files_indexed")
    shutil.rmtree(tmp, ignore_errors=True)

    return {
        "band": name,
        "total_repository_files": total_files,
        "indexed_vector_chunks": total_chunks,
        "embedding_model": cfg.embedding.model,
        "embedding_base_url": cfg.embedding.base_url,
        "index_elapsed_s": index_elapsed,
        "retrieval_elapsed_s": retrieval_elapsed,
        "relevant_file_rank": rank,
        "relevant_file_score": score,
        "top5": bool(rank is not None and rank <= 5),
        "top10": bool(rank is not None and rank <= 10),
        "top10_matches": [
            {"filepath": m.get("filepath"), "score": m.get("score")} for m in matches
        ],
    }


async def main() -> None:
    print(f"Query (unmodified across all bands): {fixtures.CORE_GROUND_TRUTH['goal']!r}")
    print(f"Ground-truth relevant file: {GROUND_TRUTH_FILE}\n")

    bands = []
    for name, filler_count in fixtures.S1_BANDS:
        print(f"Running band '{name}' ({filler_count} filler files) - real embedding calls in flight...", flush=True)
        result = await run_band(name, filler_count)
        bands.append(result)
        print(
            f"  -> files={result['total_repository_files']} chunks={result['indexed_vector_chunks']} "
            f"rank={result['relevant_file_rank']} score={result['relevant_file_score']} "
            f"top5={result['top5']} top10={result['top10']} "
            f"index_s={result['index_elapsed_s']:.2f} retrieval_s={result['retrieval_elapsed_s']:.4f}",
            flush=True,
        )

    ranks = [b["relevant_file_rank"] for b in bands]
    rank_stability = {b["band"]: b["relevant_file_rank"] for b in bands}

    # Degradation is judged primarily on material rank/top-K loss, not raw
    # score drift (score distributions shift with corpus size regardless of
    # retrieval quality - a smaller absolute score at a larger band is
    # expected and NOT itself evidence of degradation).
    numeric_ranks = [r for r in ranks if r is not None]
    lost_top5 = any(b["top5"] is False and i > 0 and bands[i - 1]["top5"] is True for i, b in enumerate(bands))
    lost_top10 = any(b["top10"] is False and i > 0 and bands[i - 1]["top10"] is True for i, b in enumerate(bands))
    never_found_at_all = len(numeric_ranks) == 0
    rank_materially_worsened = (
        len(numeric_ranks) >= 2 and (max(numeric_ranks) - min(numeric_ranks)) >= 5
    )
    degradation = bool(lost_top5 or lost_top10 or never_found_at_all or rank_materially_worsened)

    summary = {
        "query": fixtures.CORE_GROUND_TRUTH["goal"],
        "ground_truth_file": GROUND_TRUTH_FILE,
        "bands": bands,
        "rank_stability": rank_stability,
        "retrieval_precision_degradation": degradation,
        "degradation_basis": {
            "lost_top5_between_consecutive_bands": lost_top5,
            "lost_top10_between_consecutive_bands": lost_top10,
            "never_found_in_top10_any_band": never_found_at_all,
            "rank_range_spread_ge_5": rank_materially_worsened,
        },
        "scope_note": (
            "This validates real embedding retrieval ranking only. It does NOT validate "
            "Developer coding quality, F8 model behavior, MODEL-001, end-to-end CTX "
            "correctness, or context-budget correctness."
        ),
    }

    out_path = os.path.join(SCRIPT_DIR, "results", "live_retrieval_validation.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    print("\n=== RANK_STABILITY (60 -> 260 -> 760 -> 1510) ===")
    print(json.dumps(rank_stability, indent=2))
    print(f"\nRETRIEVAL_PRECISION_DEGRADATION: {'YES' if degradation else 'NO'}")
    print(json.dumps(summary["degradation_basis"], indent=2))
    print(f"\nFull results written to {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--safety-check", action="store_true",
        help="Run only the static, zero-network safety self-check and exit (no embedding calls).",
    )
    args = parser.parse_args()

    if args.safety_check:
        passed = safety_check()
        sys.exit(0 if passed else 1)

    asyncio.run(main())
