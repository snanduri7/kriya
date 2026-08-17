"""Developer-stage eval: runs Planner -> Architect -> DeveloperAgent's Step 1
file-list query against a real local model for each goal in goals.py,
independent of per-file content generation and Quality Gates - seconds per
goal instead of run_harness.py's full cycle. Reuses goals.py's SHARED fixture
set, same as architect_eval.py, so a regression here is directly comparable
against a full-pipeline regression on the identical goal.

What this measures that the full pipeline can't isolate on its own: which of
DeveloperAgent._resolve_step1_file_list()'s three paths resolves the file
list -
  "contract" - the validated {"files": [...]} schema (kriya/agents/
               contracts.py, the same one ArchitectAgent.run_with_file_list()
               uses) matched on the first completion.
  "fallback" - the older, more permissive _extract_json_value()/
               _normalize_file_entries() extraction recovered it instead.
  "none"     - neither worked; a real run would degrade to the much more
               expensive single-stage generation call.
This is the exact mechanism generalized from Architect to Developer
(2026-08-07) - see docs/design.md sec 2.4. A run here also reports how much
Developer's own file list overlaps with Architect's (a basic consistency
signal, not a correctness claim - Developer legitimately narrowing or
slightly adjusting the set isn't necessarily wrong).

What this deliberately does NOT do: call _fill_missing_content() (per-file
content generation - a much larger, per-file completion cost this eval isn't
measuring) or build the RAG/skill-matching/convention context real generation
runs assemble - only RepositoryAnalyzer's own repo_context, same
simplification architect_eval.py already makes and for the same reason.
Also deliberately does NOT simulate an actual quality-gate failure/retry
context (task_description here is the same clean "Goal + Plan" shape a first
attempt would use) - the schema-validation mechanism being measured doesn't
depend on why known_target_files was omitted, only on whether the model's
raw response to the file-list prompt validates.

Usage:
    .venv/bin/python spikes/eval_harness/developer_eval.py \\
        --model qwen3-coder:30b

    # Iterate on just one goal while developing:
    .venv/bin/python spikes/eval_harness/developer_eval.py --goal-id python_task_tracker

Run this yourself, in your own terminal - same reasoning as run_harness.py.
"""
import argparse
import asyncio
import datetime
import os
import subprocess
import sys
import time
from pathlib import Path

from goals import GOALS

from kriya.agents.agent import ArchitectAgent, DeveloperAgent, PlannerAgent
from kriya.analyzer.analyzer import RepositoryAnalyzer
from kriya.config import AppConfig
from kriya.core.llm import LLMClient

LIVE_LLM_MODEL = os.environ.get("KRIYA_LIVE_LLM_MODEL", "qwen3-coder:30b")
LIVE_BASE_URL = os.environ.get("KRIYA_LIVE_BASE_URL", "http://localhost:11434/v1")

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))


def _init_git_repo(path: Path) -> None:
    # See run_harness.py's own _init_git_repo docstring for the incident this
    # closes: re-running against an existing workspace whose .kriya/worktree
    # already exists made `git add -A` pick up that real git worktree as an
    # "embedded git repository", printing git's own submodule-confusion
    # warning on every resumed run. .gitignore-ing .kriya/ before the first
    # add, and --allow-empty on the commit (a resumed run may have nothing
    # new to stage once README.md/.gitignore are already committed), closes
    # it the same way in all three eval scripts that share this pattern.
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "eval-harness@kriya.local"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Kriya Developer Eval"], cwd=path, check=True)
    (path / "README.md").write_text("developer eval scratch project\n")
    gitignore_path = path / ".gitignore"
    if not gitignore_path.exists():
        gitignore_path.write_text(".kriya/\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "initial"], cwd=path, check=True)


async def _eval_one_goal(
    goal, planner: PlannerAgent, architect: ArchitectAgent, developer: DeveloperAgent, batch_dir: Path
) -> dict:
    workspace = batch_dir / goal.id
    workspace.mkdir(parents=True, exist_ok=True)
    _init_git_repo(workspace)

    repo_context = RepositoryAnalyzer(str(workspace)).analyze().model_dump_json(indent=2)

    start = time.time()
    plan_prompt = f"Goal: {goal.text}\n\nWorkspace Context:\n{repo_context}"
    plan = await planner.run(plan_prompt)

    design_prompt = f"Plan:\n{plan}\n\nWorkspace Context:\n{repo_context}"
    design, architect_files = await architect.run_with_file_list(design_prompt)

    task_description = f"Goal: {goal.text}\nPlan: {plan}"
    file_entries, source = await developer._resolve_step1_file_list(task_description, design)
    duration = time.time() - start

    (workspace / "plan.txt").write_text(plan)
    (workspace / "design.txt").write_text(design)

    developer_files = [e["filepath"] for e in file_entries] if file_entries else None
    overlap = None
    if developer_files is not None and architect_files:
        overlap = len(set(developer_files) & set(architect_files)) / len(set(architect_files))

    return {
        "goal_id": goal.id,
        "duration_sec": duration,
        "source": source,
        "architect_files": architect_files,
        "developer_files": developer_files,
        "overlap_with_architect": overlap,
    }


async def _main_async(goals, model: str, base_url: str, batch_dir: Path) -> list:
    cfg = AppConfig()
    cfg.llm.model = model
    cfg.llm.base_url = base_url
    llm = LLMClient(cfg)
    planner = PlannerAgent("planner", llm)
    architect = ArchitectAgent("architect", llm)
    developer = DeveloperAgent("developer", llm)

    results = []
    for goal in goals:
        print(f"--- {goal.id} ---")
        result = await _eval_one_goal(goal, planner, architect, developer, batch_dir)
        results.append(result)
        overlap_note = (
            f", {result['overlap_with_architect']:.0%} overlap w/ Architect's list"
            if result["overlap_with_architect"] is not None else ""
        )
        print(f"  source={result['source']}{overlap_note} ({result['duration_sec']:.1f}s)")
        if result["developer_files"]:
            for f in result["developer_files"]:
                print(f"    - {f}")
        print()
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=LIVE_LLM_MODEL)
    parser.add_argument("--base-url", default=LIVE_BASE_URL)
    parser.add_argument("--goal-id", action="append", default=None,
                         help="Run only these goal id(s) instead of the full set. Repeatable.")
    parser.add_argument("--batch-dir", default=None,
                         help="Where to put this batch's per-goal scratch repos/plan/design output. "
                              "Defaults to a timestamped directory under spikes/eval_harness/stage_runs/.")
    args = parser.parse_args()

    goals = GOALS if not args.goal_id else [g for g in GOALS if g.id in args.goal_id]
    if not goals:
        print(f"No goals matched --goal-id {args.goal_id}. Known ids: {[g.id for g in GOALS]}", file=sys.stderr)
        sys.exit(1)

    batch_ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    batch_dir = Path(os.path.abspath(args.batch_dir or os.path.join(HARNESS_DIR, "stage_runs", batch_ts)))
    batch_dir.mkdir(parents=True, exist_ok=True)

    print(f"Developer-stage eval batch: {batch_dir}")
    print(f"Model: {args.model} | Base URL: {args.base_url}")
    print(f"Goals: {[g.id for g in goals]}\n")

    results = asyncio.run(_main_async(goals, args.model, args.base_url, batch_dir))

    contract_count = sum(1 for r in results if r["source"] == "contract")
    fallback_count = sum(1 for r in results if r["source"] == "fallback")
    none_count = sum(1 for r in results if r["source"] == "none")
    print("=== Summary ===")
    print(f"Contract validated: {contract_count}/{len(results)}")
    print(f"Fell back to older extraction: {fallback_count}/{len(results)}")
    print(f"Neither worked (single-stage needed): {none_count}/{len(results)}")
    for r in results:
        if r["source"] != "contract":
            print(f"  needs attention: {r['goal_id']} (source={r['source']})")


if __name__ == "__main__":
    main()
