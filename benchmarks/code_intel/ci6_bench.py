"""CI-6 live measurement: the ambiguity-only localization call on loc-N.

    python benchmarks/code_intel/ci6_bench.py --config OPERATOR.yaml --cases loc.json --index MEMORY_DIR \
        --embed-cache FILE --repo REPO [--limit 40] [--out results.json]

Replays loc-N cases (``loc_bench.py --out``) through the production fused
localization and the production CI-6 step (kriya.workflow.localization_decision)
with the configured, qualified Developer model. Reports, per category, how
many cases needed the model (deterministically ambiguous), and on those the
top-1 accuracy before and after the decision, the prompt tokens and the wall
time. A live-model benchmark: run it deliberately; nothing in the suite runs it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time

from kriya.code_intel import locate as loc
from kriya.code_intel.service import CodeIntelligenceService, discover_source_files
from kriya.config.config import load_config
from kriya.core.llm import LLMClient
from kriya.memory.vector import LocalVectorStore
from kriya.workflow import graph_retrieval as gr
from kriya.workflow import localization_decision as ld


async def run(args) -> dict:
    data = json.load(open(args.cases))
    cases = data["cases"]
    embeddings = json.load(open(args.embed_cache))
    cfg = load_config(args.config)
    llm = LLMClient(cfg)
    store = LocalVectorStore(os.path.join(args.index, "vector_index.db"))
    fingerprint = store.active_fingerprint()
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        service = CodeIntelligenceService(args.repo, os.path.join(tmp, "ci.db"))
        service.refresh(discover_source_files(args.repo))
        ambiguous_seen = 0
        for case in cases:
            semantic = store.query(embeddings[case["goal"]], top_k=loc.SEMANTIC_CHUNKS, fingerprint=fingerprint)
            candidates = gr.fused_localization(service, case["goal"], semantic)
            separation = gr.separation(candidates)
            gold = set(case["gold_ids"])
            row = {"id": case["id"], "category": case["category"], "kind": case["kind"],
                   "clear": ld.is_clear(separation),
                   "top1_before": bool(candidates) and candidates[0].symbol_id in gold,
                   "gold_shown": any(c.symbol_id in gold for c in candidates[:ld.MAX_CANDIDATES])}
            if not row["clear"] and len(candidates) > 1 and ambiguous_seen < args.limit:
                ambiguous_seen += 1
                started = time.monotonic()
                outcome = await ld.decide(llm, case["goal"], candidates, separation, current=lambda i: True)
                row.update(called=outcome.called, reason=outcome.reason_code, seconds=time.monotonic() - started,
                           prompt_tokens=outcome.prompt_tokens)
                after = ld.apply_decision(candidates, outcome.decision) if outcome.decision else candidates
                row["top1_after"] = bool(after) and after[0].symbol_id in gold
                row["any_target_gold"] = bool(outcome.decision) and bool(
                    set(outcome.decision.target_symbol_ids) & gold)
            rows.append(row)
        service.close()
    store.close()
    await llm.aclose()
    return summarize(rows)


def summarize(rows) -> dict:
    def group(name, subset):
        called = [r for r in subset if r.get("called")]
        return {
            "n": len(subset),
            "ambiguous (needs the model)": round(sum(1 for r in subset if not r["clear"]) / max(1, len(subset)), 3),
            "measured_calls": len(called),
            "top1_before_on_called": round(sum(r["top1_before"] for r in called) / max(1, len(called)), 3),
            "top1_after_on_called": round(sum(r["top1_after"] for r in called) / max(1, len(called)), 3),
            "gold_among_shown_on_called": round(sum(r["gold_shown"] for r in called) / max(1, len(called)), 3),
            "median_prompt_tokens": statistics.median([r["prompt_tokens"] or 0 for r in called]) if called else None,
            "median_seconds": round(statistics.median([r["seconds"] for r in called]), 2) if called else None,
            "p95_seconds": round(sorted(r["seconds"] for r in called)[int(0.95 * (len(called) - 1))], 2)
            if called else None,
            "reasons": {k: sum(1 for r in called if r["reason"] == k) for k in ld.REASON_CODES
                        if any(r.get("reason") == k for r in called)},
        }
    out = {"all": group("all", rows)}
    for category in sorted({r["kind"] if r["kind"] == "synthetic" else r["category"] for r in rows}):
        out[category] = group(category, [r for r in rows if (r["kind"] if r["kind"] == "synthetic"
                                                             else r["category"]) == category])
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in ("--config", "--cases", "--index", "--embed-cache", "--repo"):
        parser.add_argument(name, required=True)
    parser.add_argument("--limit", type=int, default=40, help="ambiguous cases sent to the model")
    parser.add_argument("--out")
    args = parser.parse_args()
    args.repo = os.path.abspath(os.path.expanduser(args.repo))
    report = asyncio.run(run(args))
    print(json.dumps(report, indent=1))
    if args.out:
        json.dump(report, open(args.out, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
