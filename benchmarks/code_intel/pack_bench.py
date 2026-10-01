"""Member packing benchmark: is the gold member's exact body in the context?

    python benchmarks/code_intel/pack_bench.py <loc_bench --out json> [--budget 2000]

For each mined loc-N case (gold = the member a real commit changed), at HEAD:
- ``file_packing``: the pre-R1 per-file packer ``build_code_context`` with the
  gold file as the matched file and the given token budget;
- ``member_packing``: ``CodeIntelligenceService.build_context`` (T0..T2) with
  the same budget.
Reports how often the gold member's exact body text is present, and tokens
(len//4, the packer's own estimator). No model calls.
"""
import argparse
import json
import os
import statistics
import sys
import tempfile

from kriya.code_intel.service import CodeIntelligenceService, discover_source_files
from kriya.workflow.context_budget import build_code_context, estimate_tokens


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("results")
    parser.add_argument("--budget", type=int, default=2000)
    args = parser.parse_args(argv)
    data = json.load(open(args.results))
    repo = data["report"]["repo"]
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        service = CodeIntelligenceService(repo, os.path.join(tmp, "ci.db"))
        service.refresh(discover_source_files(repo))
        for case in data["cases"]:
            if case["kind"] != "mined":
                continue
            gold = case["gold_ids"][0]
            member = service.get_member(gold)
            if member is None:
                continue
            body = member.text
            old = build_code_context([member.path], [], repo, args.budget)
            package = service.build_context(gold, budget_tokens=args.budget, count_tokens=estimate_tokens)
            new = package.render()
            with open(os.path.join(repo, member.path), encoding="utf-8", errors="replace") as handle:
                file_tokens = estimate_tokens(handle.read())
            rows.append({"old_present": body in old, "new_present": body in new, "old_tokens": estimate_tokens(old),
                         "new_tokens": estimate_tokens(new), "file_tokens": file_tokens,
                         "big_file": file_tokens > args.budget, "over_budget": package.over_budget})
        service.close()
    n = len(rows)
    big = [r for r in rows if r["big_file"]]
    report = {
        "repo": repo, "budget": args.budget, "cases": n, "files_over_budget": len(big),
        "gold_body_present_file_packing": round(sum(r["old_present"] for r in rows) / n, 3),
        "gold_body_present_member_packing": round(sum(r["new_present"] for r in rows) / n, 3),
        "gold_body_present_file_packing_big_files": round(sum(r["old_present"] for r in big) / len(big), 3) if big else None,
        "gold_body_present_member_packing_big_files": round(sum(r["new_present"] for r in big) / len(big), 3) if big else None,
        "median_tokens_file_packing": statistics.median(r["old_tokens"] for r in rows),
        "median_tokens_member_packing": statistics.median(r["new_tokens"] for r in rows),
        "median_whole_file_tokens": statistics.median(r["file_tokens"] for r in rows),
        "t0_over_budget": sum(r["over_budget"] for r in rows),
    }
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
