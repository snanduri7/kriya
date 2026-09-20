"""S3 - Cross-Module Relationship probe.

Uses the CORE fixture's known caller -> implementation -> interface -> test
chain (ground truth in fixtures.CORE_GROUND_TRUTH). Two things are
deterministically checkable without any embedding:
  1. Does DependencyGraph's REAL symbol/relation extraction (AST for Python)
     actually represent each required relationship as a row in `relations`?
  2. Does the LEXICAL half of retrieval (query_lexical - real FTS5/LIKE
     matching on real chunk text, unaffected by fake embeddings) surface the
     real target files for a goal-shaped query, without loading unrelated
     filler modules?
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixtures  # noqa: E402
import probes  # noqa: E402
from kriya.analyzer.graph import DependencyGraph  # noqa: E402
from kriya.memory.vector import LocalVectorStore  # noqa: E402


async def main() -> None:
    tmp = tempfile.mkdtemp(prefix="ctx001_s3_")
    root = os.path.join(tmp, "repo")
    mem = os.path.join(tmp, "memory")
    skl = os.path.join(tmp, "skills")
    # Core fixture only, plus a moderate amount of unrelated breadth so
    # cross-module relationships have to be found IN a real repo shape, not
    # a trivial 5-file directory.
    fixtures.build_s1_fixture(root, band_filler_count=245)

    index_r = await probes.run_index_repository(root, mem, skills_dir=skl)

    graph = DependencyGraph(os.path.join(mem, "dependency_graph.db"))
    relationship_checks = []
    try:
        for source_file, target_file, kind in fixtures.CORE_GROUND_TRUTH["required_relationships"]:
            source_symbols = graph.get_symbols_for_file(source_file)
            target_symbols = graph.get_symbols_for_file(target_file)
            # A relation row exists whose source_file is the caller and whose
            # target name is one of the target file's own declared symbols.
            found = False
            cursor = graph.conn.cursor()
            cursor.execute(
                "SELECT DISTINCT target, type FROM relations WHERE source_file = ?",
                (source_file,),
            )
            rows = cursor.fetchall()
            targets_from_source = {r[0] for r in rows}
            found = bool(targets_from_source & set(target_symbols))
            relationship_checks.append({
                "source_file": source_file,
                "target_file": target_file,
                "kind": kind,
                "source_symbol_count": len(source_symbols),
                "target_symbol_count": len(target_symbols),
                "relation_row_found": found,
                "raw_relation_targets_from_source": sorted(targets_from_source),
            })
    finally:
        graph.close()

    # Lexical-only retrieval precision (real text matching, embedding-independent).
    store = LocalVectorStore(os.path.join(mem, "vector_index.db"))
    lexical_checks = []
    try:
        for query_text in [
            "StandardInvoiceCalculator calculate_total",
            "InvoiceCalculator abstract method",
            "CheckoutService checkout",
        ]:
            results = store.query_lexical(query_text, top_k=10)
            billing_hits = [r["filepath"] for r in results if r["filepath"].startswith("core/billing/")]
            filler_hits = [r["filepath"] for r in results if not r["filepath"].startswith("core/billing/")]
            lexical_checks.append({
                "query": query_text,
                "total_hits": len(results),
                "billing_hits": billing_hits,
                "filler_hits_count": len(filler_hits),
            })
    finally:
        store.close()

    out = {
        "ground_truth": fixtures.CORE_GROUND_TRUTH,
        "total_files": index_r["db_counts"]["files_graph"],
        "relationship_checks": relationship_checks,
        "lexical_retrieval_checks": lexical_checks,
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", "s3_cross_module.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)

    print(json.dumps({"relationship_checks": relationship_checks, "lexical_retrieval_checks": lexical_checks}, indent=2))
    print(f"\nFull results written to {out_path}")

    shutil.rmtree(tmp)


if __name__ == "__main__":
    asyncio.run(main())
