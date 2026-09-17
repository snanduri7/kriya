"""S1 - Repository Breadth probe.

Relevant engineering code (the billing core, 5 files) held constant.
Unrelated repository breadth increased across bands: small/medium/large/
very_large. For each band, measures analyze() cost, index_repository() cold
cost, vector query_hybrid() retrieval cost + precision (does the real target
file rank in the top-k against filler?), and graph get_neighborhood() cost.
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

TARGET_QUERY = "apply regional surcharge to invoice tax calculation"
SEED_SYMBOLS = ["StandardInvoiceCalculator", "InvoiceCalculator"]


async def run_band(name: str, filler_count: int) -> dict:
    tmp = tempfile.mkdtemp(prefix=f"ctx001_s1_{name}_")
    root = os.path.join(tmp, "repo")
    mem = os.path.join(tmp, "memory")
    skl = os.path.join(tmp, "skills")
    fixtures.build_s1_fixture(root, band_filler_count=filler_count)

    analyze_r = await probes.run_analyze(root)
    index_r = await probes.run_index_repository(root, mem, skills_dir=skl)
    vector_r = probes.run_vector_query(mem, TARGET_QUERY, top_k=10)
    graph_r = probes.run_graph_neighborhood(mem, SEED_SYMBOLS)

    target_rank = None
    for i, m in enumerate(vector_r["matches"], 1):
        if m["filepath"].startswith("core/billing/"):
            target_rank = i
            break

    ctx_r = probes.run_build_code_context(
        ["core/billing/invoice_impl.py"], ["core/billing/invoice_interface.py"], root, 5000,
    )

    result = {
        "band": name,
        "filler_files": filler_count,
        "total_files": analyze_r["total_files_indexed"],
        "analyze": {k: v for k, v in analyze_r.items() if k != "languages"},
        "index_cold": {k: v for k, v in index_r.items() if k != "db_counts"},
        "index_db_counts": index_r["db_counts"],
        "vector_query": {
            "elapsed_s": vector_r["elapsed_s"],
            "total_indexed_chunks_scanned": vector_r["total_indexed_chunks_scanned"],
            "billing_file_top_rank_of_10": target_rank,
        },
        "graph_neighborhood": {
            "elapsed_s": graph_r["elapsed_s"],
            "results_count": graph_r["results_count"],
        },
        "build_code_context": {
            "elapsed_s": ctx_r["elapsed_s"],
            "output_tokens_est": ctx_r["output_tokens_est"],
        },
    }
    shutil.rmtree(tmp)
    return result


async def main() -> None:
    bands = []
    for name, filler_count in fixtures.S1_BANDS:
        print(f"Running band '{name}' ({filler_count} filler files)...", flush=True)
        bands.append(await run_band(name, filler_count))

    # Scaling ratios relative to the smallest band.
    base = bands[0]
    scaling = []
    for b in bands[1:]:
        file_ratio = b["total_files"] / max(1, base["total_files"])
        scaling.append({
            "band": b["band"],
            "files_x": round(file_ratio, 2),
            "analyze_time_x": round(b["analyze"]["elapsed_s"] / max(1e-9, base["analyze"]["elapsed_s"]), 2),
            "analyze_dirs_walked_x": round(b["analyze"]["dirs_walked"] / max(1, base["analyze"]["dirs_walked"]), 2),
            "index_cold_time_x": round(b["index_cold"]["elapsed_s"] / max(1e-9, base["index_cold"]["elapsed_s"]), 2),
            "vector_query_time_x": round(b["vector_query"]["elapsed_s"] / max(1e-9, base["vector_query"]["elapsed_s"]), 2),
            "vector_chunks_scanned_x": round(
                b["vector_query"]["total_indexed_chunks_scanned"] / max(1, base["vector_query"]["total_indexed_chunks_scanned"]), 2
            ),
            "build_code_context_time_x": round(
                b["build_code_context"]["elapsed_s"] / max(1e-9, base["build_code_context"]["elapsed_s"]), 2
            ),
            "build_code_context_tokens_x": round(
                b["build_code_context"]["output_tokens_est"] / max(1, base["build_code_context"]["output_tokens_est"]), 2
            ),
        })

    out = {"bands": bands, "scaling_relative_to_small": scaling}
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", "s1_repository_breadth.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)

    print(json.dumps(scaling, indent=2))
    print(f"\nFull results written to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
