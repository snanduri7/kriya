"""Measures how often a model's own FIX ANALYSIS correctly diagnoses a bug, but
the accompanying SEARCH/REPLACE edit fails to implement it - a real, repeated
pattern found live 2026-08-07/08 (see README.md for the two incidents this
spike's fixtures are drawn from). Real LLM calls throughout, no mocks.

Deliberately meant to be launched by a human in their own terminal, not by an
AI assistant polling it turn by turn from inside a chat session - same posture
as spikes/eval_harness/ (see its own README for the reasoning).

Usage:
    .venv/bin/python spikes/fix_alignment/run_alignment_test.py \\
        --model qwen3-coder:30b --repetitions 10

    # Just one fixture, just the baseline condition, fewer reps while iterating:
    .venv/bin/python spikes/fix_alignment/run_alignment_test.py \\
        --fixtures buffer_capacity --conditions baseline --repetitions 3
"""
import argparse
import asyncio
import datetime
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
from fixtures import FIXTURES  # noqa: E402

from kriya.agents.agent import DeveloperAgent  # noqa: E402
from kriya.config import AppConfig  # noqa: E402
from kriya.core.llm import LLMClient  # noqa: E402
from kriya.workflow.workflow import apply_anchored_edits, _build_error_source_context  # noqa: E402

# Imports the exact same text now wired to always-on in
# kriya/workflow/workflow.py's retry loop (2026-08-10, once this spike's first
# real batch supported it) rather than keeping a separate copy - so a future
# re-run of this spike always measures whatever text production actually
# ships, not a stale snapshot of it. This IS the independent variable this
# spike measures; the baseline condition passes "" (no-op).
CONDITIONS = {"baseline": "", "nudge": DeveloperAgent.SELF_CONSISTENCY_NUDGE}


def _build_existing_code_context(fixture) -> str:
    return f"File: {fixture.filepath}\n```java\n{fixture.buggy_content}\n```\n"


async def run_one(llm: LLMClient, dev: DeveloperAgent, fixture, extra_fix_instruction: str, source_snippet: str, existing_code_context: str) -> dict:
    """Runs ONE real completion for ONE fixture/condition and scores it."""
    real_complete = llm.complete
    raw_holder = {}

    async def spying_complete(*args, **kwargs):
        text = await real_complete(*args, **kwargs)
        raw_holder["raw"] = text
        return text

    llm.complete = spying_complete
    try:
        await dev.run_generation(
            task_description=fixture.task_description,
            design_context=fixture.design_context,
            existing_code_context=existing_code_context,
            known_target_files=[fixture.filepath],
            prior_error_context=fixture.error_context,
            implicated_files=[fixture.filepath],
            error_source_context={fixture.filepath: source_snippet},
            extra_fix_instruction=extra_fix_instruction,
        )
    finally:
        llm.complete = real_complete

    raw = raw_holder.get("raw", "")
    analysis, edits, _content = DeveloperAgent._split_fix_analysis_edit(raw)

    result = {
        "analysis": analysis or "",
        "diagnosis_correct": fixture.diagnosis_check(analysis or ""),
        "edit_returned": bool(edits),
        "apply_ok": False,
        "apply_error": None,
        "execution_correct": False,
        "raw_response": raw,
    }
    if edits:
        try:
            final_content = apply_anchored_edits(fixture.buggy_content, edits, existing_code_context)
            result["apply_ok"] = True
            result["execution_correct"] = fixture.success_check(final_content)
            result["final_content"] = final_content
        except ValueError as ex:
            result["apply_error"] = str(ex)
    return result


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=os.environ.get("KRIYA_LIVE_LLM_MODEL", "qwen3-coder:30b"))
    parser.add_argument("--base-url", default=os.environ.get("KRIYA_LIVE_BASE_URL", "http://localhost:11434/v1"))
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument("--conditions", default="baseline,nudge", help="Comma-separated subset of: baseline,nudge")
    parser.add_argument("--fixtures", default=None, help="Comma-separated fixture ids to run (default: all)")
    parser.add_argument("--output", default=None, help="Path for the JSON results file (default: timestamped file under runs/)")
    args = parser.parse_args()

    conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]
    for c in conditions:
        if c not in CONDITIONS:
            print(f"Unknown condition '{c}'. Known: {list(CONDITIONS)}", file=sys.stderr)
            return 1

    fixtures = FIXTURES
    if args.fixtures:
        wanted = {f.strip() for f in args.fixtures.split(",")}
        fixtures = [f for f in fixtures if f.id in wanted]
        if not fixtures:
            print(f"No fixtures matched {args.fixtures}. Known: {[f.id for f in FIXTURES]}", file=sys.stderr)
            return 1

    cfg = AppConfig()
    cfg.llm.model = args.model
    cfg.llm.base_url = args.base_url
    cfg.llm.provider = "openai"
    cfg.llm.api_key = "local-key"
    llm = LLMClient(cfg)
    dev = DeveloperAgent("developer", llm)

    # Real _build_error_source_context() call per fixture, against the fixture's
    # actual content on disk (a temp file) - byte-perfect match to what a real
    # Kriya retry would show, no manual line-window arithmetic to get wrong.
    snippets = {}
    existing_contexts = {}
    with tempfile.TemporaryDirectory() as tmpdir:
        for fixture in fixtures:
            full_path = os.path.join(tmpdir, fixture.filepath)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w") as fh:
                fh.write(fixture.buggy_content)
            ctx = _build_error_source_context(tmpdir, fixture.error_context, known_files=[fixture.filepath])
            snippets[fixture.id] = ctx.get(fixture.filepath, "")
            existing_contexts[fixture.id] = _build_existing_code_context(fixture)

    total_calls = len(fixtures) * len(conditions) * args.repetitions
    print(f"Fix-alignment spike: {len(fixtures)} fixture(s) x {len(conditions)} condition(s) x {args.repetitions} repetition(s) = {total_calls} real LLM calls")
    print(f"Model: {args.model} | Base URL: {args.base_url}\n")

    all_results: dict = {}
    done = 0
    for fixture in fixtures:
        all_results[fixture.id] = {}
        for condition in conditions:
            condition_results = []
            for i in range(args.repetitions):
                done += 1
                print(f"[{done}/{total_calls}] fixture={fixture.id} condition={condition} rep={i + 1}/{args.repetitions}...", end=" ", flush=True)
                res = await run_one(
                    llm, dev, fixture, CONDITIONS[condition],
                    snippets[fixture.id], existing_contexts[fixture.id],
                )
                condition_results.append(res)
                diag_tag = "DIAG-OK" if res["diagnosis_correct"] else "diag-no"
                if not res["edit_returned"]:
                    exec_tag = "no-edit"
                elif res["apply_error"]:
                    exec_tag = "apply-fail"
                elif res["execution_correct"]:
                    exec_tag = "FIX-OK"
                else:
                    exec_tag = "fix-no"
                print(f"{diag_tag} / {exec_tag}", flush=True)
            all_results[fixture.id][condition] = condition_results

    print("\n" + "=" * 92)
    print("SUMMARY")
    print("=" * 92)
    print(f"{'fixture':<20} {'condition':<10} {'diagnosis-correct':>18} {'execution-correct':>18} {'diag-ok,exec-no':>17}")
    for fixture in fixtures:
        for condition in conditions:
            rows = all_results[fixture.id][condition]
            n = len(rows)
            diag_ok = sum(1 for r in rows if r["diagnosis_correct"])
            exec_ok = sum(1 for r in rows if r["execution_correct"])
            mismatch = sum(1 for r in rows if r["diagnosis_correct"] and not r["execution_correct"])
            print(f"{fixture.id:<20} {condition:<10} {f'{diag_ok}/{n}':>18} {f'{exec_ok}/{n}':>18} {f'{mismatch}/{n}':>17}")
    print("\n'diag-ok,exec-no' is the exact phenomenon this spike measures: the model")
    print("correctly named the root cause in words, but the edit didn't implement it.")

    output_path = args.output or os.path.join(
        os.path.dirname(__file__), "runs", datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".json"
    )
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nFull results (including every raw response) written to: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
