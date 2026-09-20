"""S2 - Large File Depth probe.

Needs no embedding endpoint at all: skeletonization/build_code_context are
pure deterministic functions of real source text. For each (line_count,
placement) combination, measures: skeletonization time by tier (full/
skeleton/signatures), whether the RELEVANT member's real body survives at
each tier (a deterministic string-containment check against a known sentinel
line - real ground truth, not an LLM judgment), and at what build_code_context
token budget the file gets pushed down to a tier that elides the member body.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixtures  # noqa: E402
from kriya.workflow.context_budget import build_code_context, estimate_tokens, skeletonize_code  # noqa: E402

RELEVANT_BODY_SENTINEL = "subtotal = sum(item.unit_price"
RELEVANT_METHOD_SIGNATURE = "def calculate_total(self, items: List[LineItem]) -> float:"
INTERFACE_METHOD_SIGNATURE = "def calculate_total_contract(self, items: List[LineItem]) -> float:"

BUDGET_BANDS = [8000, 16000, 32000, 128000]


def analyze_one(lines: int, placement: str) -> dict:
    content = fixtures.build_large_file(lines, placement)
    actual_lines = len(content.splitlines())
    filepath = f"s2/large_{lines}_{placement}.py"

    tier_results = {}
    for tier in ("full", "skeleton", "signatures"):
        t0 = time.perf_counter()
        skel = skeletonize_code(content, filepath, tier)
        elapsed = time.perf_counter() - t0
        tier_results[tier] = {
            "elapsed_s": elapsed,
            "output_tokens_est": estimate_tokens(skel),
            "output_lines": len(skel.splitlines()),
            "body_survives": RELEVANT_BODY_SENTINEL in skel,
            "signature_survives": RELEVANT_METHOD_SIGNATURE in skel,
            "interface_signature_survives": (
                INTERFACE_METHOD_SIGNATURE in skel if placement == "far_apart" else None
            ),
        }

    # Budget-pressure sweep via the real build_code_context allocator, using
    # a synthetic single-file "matched" set (workspace_path is a temp dir
    # containing just this one file, written fresh per call).
    import tempfile
    tmp = tempfile.mkdtemp(prefix="ctx001_s2_")
    full_path = os.path.join(tmp, filepath)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as fh:
        fh.write(content)

    budget_results = {}
    for budget in BUDGET_BANDS:
        ctx = build_code_context([filepath], [], tmp, budget)
        budget_results[str(budget)] = {
            "output_tokens_est": estimate_tokens(ctx),
            "body_survives": RELEVANT_BODY_SENTINEL in ctx,
            "signature_survives": RELEVANT_METHOD_SIGNATURE in ctx,
        }
    import shutil
    shutil.rmtree(tmp)

    return {
        "requested_lines": lines,
        "actual_lines": actual_lines,
        "placement": placement,
        "tiers": tier_results,
        "budget_sweep": budget_results,
    }


def main() -> None:
    results = []
    for lines in fixtures.S2_LINE_BANDS:
        for placement in fixtures.S2_PLACEMENTS:
            print(f"Analyzing lines={lines} placement={placement}...", flush=True)
            results.append(analyze_one(lines, placement))

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", "s2_large_file_depth.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)

    # Compact summary
    summary = []
    for r in results:
        summary.append({
            "lines": r["actual_lines"],
            "placement": r["placement"],
            "full_time_s": round(r["tiers"]["full"]["elapsed_s"], 5),
            "skeleton_time_s": round(r["tiers"]["skeleton"]["elapsed_s"], 5),
            "signatures_time_s": round(r["tiers"]["signatures"]["elapsed_s"], 5),
            "body_survives_full": r["tiers"]["full"]["body_survives"],
            "body_survives_skeleton": r["tiers"]["skeleton"]["body_survives"],
            "body_survives_signatures": r["tiers"]["signatures"]["body_survives"],
            "sig_survives_signatures": r["tiers"]["signatures"]["signature_survives"],
            "8k_body_survives": r["budget_sweep"]["8000"]["body_survives"],
            "16k_body_survives": r["budget_sweep"]["16000"]["body_survives"],
            "32k_body_survives": r["budget_sweep"]["32000"]["body_survives"],
        })
    print(json.dumps(summary, indent=2))
    print(f"\nFull results written to {out_path}")


if __name__ == "__main__":
    main()
