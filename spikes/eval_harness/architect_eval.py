"""Architect-stage-only eval: runs Planner -> Architect against a real local
model for each goal in goals.py, independent of Developer/Quality Gates -
seconds per goal instead of run_harness.py's full compile/test/run-
verification cycle (minutes, and only ever indirect evidence about the
Architect specifically). Reuses goals.py's SHARED fixture set rather than
duplicating it, so a regression here is directly comparable against a
full-pipeline regression on the identical goal.

What this measures that the full pipeline can't isolate on its own:
  1. Does ArchitectAgent.run_with_file_list()'s structured JSON file list
     validate on the first completion (kriya/agents/contracts.py), or does
     it fall back to the older heuristic regex extraction?
  2. For a goal whose text establishes Maven/Gradle/Bundler, does the file
     list actually include that stack's manifest? (The exact failure class
     _detect_missing_build_manifest() exists to catch reactively, AFTER a
     wasted compile attempt - this catches it proactively, before any
     Developer call happens at all.)

What this deliberately does NOT do: build the RAG/skill-matching/convention
context real generation runs assemble (kriya/workflow/workflow.py's
convention_prompt) - only RepositoryAnalyzer's own repo_context, which is
cheap and realistic for these goals' fresh, empty repos. Skills mostly
affect content QUALITY, not the file-list schema mechanism itself; omitting
them keeps this eval fast and focused on what it's actually testing. A
result here is a signal about the Architect stage specifically, not a
substitute for a full-pipeline batch.

Usage:
    .venv/bin/python spikes/eval_harness/architect_eval.py \\
        --model qwen3-coder:30b

    # Iterate on just one goal while developing:
    .venv/bin/python spikes/eval_harness/architect_eval.py --goal-id python_task_tracker

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

from kriya.agents.agent import ArchitectAgent, PlannerAgent
from kriya.analyzer.analyzer import RepositoryAnalyzer
from kriya.config import AppConfig
from kriya.core.llm import LLMClient

LIVE_LLM_MODEL = os.environ.get("KRIYA_LIVE_LLM_MODEL", "qwen3-coder:30b")
LIVE_BASE_URL = os.environ.get("KRIYA_LIVE_BASE_URL", "http://localhost:11434/v1")

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))

# (goal-text keyword, expected file-path suffix) - a goal establishing one of
# these build systems structurally requires the matching manifest somewhere
# in the file list, the exact failure class _detect_missing_build_manifest()
# (kriya/workflow/workflow.py) exists to catch reactively after a wasted
# compile attempt. Deliberately a small, explicit table for confirmed cases,
# not a general "infer required files" system - same scoping discipline as
# _JDK_INCOMPATIBLE_JVM_FLAGS.
_MANIFEST_EXPECTATIONS = [
    ("maven", "pom.xml"),
    ("gradle", "build.gradle"),
    ("gemfile", "Gemfile"),
]


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
    subprocess.run(["git", "config", "user.name", "Kriya Architect Eval"], cwd=path, check=True)
    (path / "README.md").write_text("architect eval scratch project\n")
    gitignore_path = path / ".gitignore"
    if not gitignore_path.exists():
        gitignore_path.write_text(".kriya/\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "initial"], cwd=path, check=True)


def _expected_manifest(goal_text: str) -> str | None:
    lower = goal_text.lower()
    for keyword, manifest in _MANIFEST_EXPECTATIONS:
        if keyword in lower:
            return manifest
    return None


async def _eval_one_goal(goal, planner: PlannerAgent, architect: ArchitectAgent, batch_dir: Path) -> dict:
    workspace = batch_dir / goal.id
    workspace.mkdir(parents=True, exist_ok=True)
    _init_git_repo(workspace)

    repo_context = RepositoryAnalyzer(str(workspace)).analyze().model_dump_json(indent=2)

    start = time.time()
    plan_prompt = f"Goal: {goal.text}\n\nWorkspace Context:\n{repo_context}"
    plan = await planner.run(plan_prompt)

    design_prompt = f"Plan:\n{plan}\n\nWorkspace Context:\n{repo_context}"
    design, files = await architect.run_with_file_list(design_prompt)
    duration = time.time() - start

    (workspace / "plan.txt").write_text(plan)
    (workspace / "design.txt").write_text(design)

    expected_manifest = _expected_manifest(goal.text)
    manifest_present = (
        expected_manifest is None
        or files is not None and any(f.endswith(expected_manifest) for f in files)
    )

    return {
        "goal_id": goal.id,
        "duration_sec": duration,
        "structured": files is not None,
        "files": files,
        "expected_manifest": expected_manifest,
        "manifest_present": manifest_present,
    }


async def _main_async(goals, model: str, base_url: str, batch_dir: Path) -> list:
    cfg = AppConfig()
    cfg.llm.model = model
    cfg.llm.base_url = base_url
    llm = LLMClient(cfg)
    planner = PlannerAgent("planner", llm)
    architect = ArchitectAgent("architect", llm)

    results = []
    for goal in goals:
        print(f"--- {goal.id} ---")
        result = await _eval_one_goal(goal, planner, architect, batch_dir)
        results.append(result)
        status = "STRUCTURED" if result["structured"] else "FELL BACK TO HEURISTIC"
        manifest_note = ""
        if result["expected_manifest"] is not None:
            manifest_note = (
                f", manifest {'OK' if result['manifest_present'] else 'MISSING: ' + result['expected_manifest']}"
            )
        print(f"  {status}{manifest_note} ({result['duration_sec']:.1f}s)")
        if result["files"]:
            for f in result["files"]:
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

    print(f"Architect-stage eval batch: {batch_dir}")
    print(f"Model: {args.model} | Base URL: {args.base_url}")
    print(f"Goals: {[g.id for g in goals]}\n")

    results = asyncio.run(_main_async(goals, args.model, args.base_url, batch_dir))

    structured_count = sum(1 for r in results if r["structured"])
    manifest_ok_count = sum(1 for r in results if r["manifest_present"])
    print("=== Summary ===")
    print(f"Structured file list: {structured_count}/{len(results)}")
    print(f"Manifest expectations met: {manifest_ok_count}/{len(results)}")
    for r in results:
        if not r["structured"] or not r["manifest_present"]:
            print(f"  needs attention: {r['goal_id']}")


if __name__ == "__main__":
    main()
