"""S4 - Context Pressure probe (multi-file allocation, complementary to S2's
single-large-file tier sweep). Uses the CORE fixture's 4 real files as
"matched" plus 3 synthetic ~800-line "related" filler files, at 8K/16K/32K
budgets, both WITHOUT file_scores (categorical degradation - related files
degrade before any matched file) and WITH file_scores (score-aware
degradation - lowest-scored file degrades first regardless of matched/
related category). Demonstrates whether a correctly-identified HIGH-RELEVANCE
matched file can still lose its body to an unrelated LOW-RELEVANCE file
crowding the budget, under each allocation mode.
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixtures  # noqa: E402

from kriya.workflow.context_budget import build_code_context, estimate_tokens  # noqa: E402

BUDGETS = [8000, 16000, 32000]
SENTINEL = "subtotal = sum(item.unit_price"


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="ctx001_s4_")
    fixtures.write_core(tmp)
    # 3 related-but-irrelevant large files (~800 lines each, real structural content).
    for i, placement in enumerate(["near_start", "near_end", "far_apart"]):
        content = fixtures.build_large_file(800, placement)
        rel = f"related_large_{i}.py"
        full = os.path.join(tmp, rel)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)

    matched = ["core/billing/invoice_impl.py", "core/billing/invoice_interface.py"]
    related = ["related_large_0.py", "related_large_1.py", "related_large_2.py"]

    results = {"no_scores": {}, "with_scores_target_low": {}, "with_scores_target_high": {}}

    for budget in BUDGETS:
        ctx = build_code_context(matched, related, tmp, budget)
        results["no_scores"][str(budget)] = {
            "output_tokens_est": estimate_tokens(ctx),
            "target_body_survives": SENTINEL in ctx,
        }

    # Score-aware: target file scored LOWEST among all 5 - should degrade FIRST
    # even though it's in "matched", ahead of the unrelated "related" files.
    low_scores = {
        "core/billing/invoice_impl.py": 0.01,
        "core/billing/invoice_interface.py": 0.02,
        "related_large_0.py": 0.9,
        "related_large_1.py": 0.8,
        "related_large_2.py": 0.7,
    }
    for budget in BUDGETS:
        ctx = build_code_context(matched, related, tmp, budget, file_scores=low_scores)
        results["with_scores_target_low"][str(budget)] = {
            "output_tokens_est": estimate_tokens(ctx),
            "target_body_survives": SENTINEL in ctx,
        }

    # Score-aware: target file scored HIGHEST - should be the LAST to degrade.
    high_scores = {
        "core/billing/invoice_impl.py": 0.99,
        "core/billing/invoice_interface.py": 0.95,
        "related_large_0.py": 0.1,
        "related_large_1.py": 0.2,
        "related_large_2.py": 0.15,
    }
    for budget in BUDGETS:
        ctx = build_code_context(matched, related, tmp, budget, file_scores=high_scores)
        results["with_scores_target_high"][str(budget)] = {
            "output_tokens_est": estimate_tokens(ctx),
            "target_body_survives": SENTINEL in ctx,
        }

    shutil.rmtree(tmp)

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", "s4_context_pressure.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    print(json.dumps(results, indent=2))
    print(f"\nFull results written to {out_path}")


if __name__ == "__main__":
    main()
