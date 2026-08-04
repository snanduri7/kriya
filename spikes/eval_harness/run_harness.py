"""Unattended eval-harness runner. Drives the real `kriya generate` CLI (not
mocks) once per goal in goals.py, against a real local LLM/embedding
endpoint, and leaves a single shared traces.db (kriya/core/trace.py) behind
for report.py to aggregate by failure_category.

Deliberately meant to be launched by a human in their own terminal, not by
an AI assistant polling it turn by turn from inside a chat session - see
README.md. Fully unattended (`-y` throughout, matching kriya/cli.py's
on_approval/on_skill_gap/on_web_lookup auto-skip behavior under -y); no
prompt ever blocks waiting for input.

Usage:
    .venv/bin/python spikes/eval_harness/run_harness.py \\
        --model qwen3-coder:30b --embed-model embeddinggemma:latest

    # Iterate on just one goal while developing:
    .venv/bin/python spikes/eval_harness/run_harness.py --goal-id python_greeter
"""
import argparse
import datetime
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml
from goals import GOALS

LIVE_LLM_MODEL = os.environ.get("KRIYA_LIVE_LLM_MODEL", "qwen3-coder:30b")
LIVE_EMBED_MODEL = os.environ.get("KRIYA_LIVE_EMBED_MODEL", "embeddinggemma:latest")
LIVE_BASE_URL = os.environ.get("KRIYA_LIVE_BASE_URL", "http://localhost:11434/v1")

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))


def _init_git_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "eval-harness@kriya.local"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Kriya Eval Harness"], cwd=path, check=True)
    (path / "README.md").write_text("eval harness scratch project\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=path, check=True)


def _write_config(workspace_path, shared_logs_dir, model, embed_model, base_url):
    # paths.skills/memory stay relative (resolved against this config file's own
    # directory, i.e. per-goal-isolated - see CLAUDE.md's config-resolution note)
    # so one goal's skill/RAG state can never leak into another's. paths.logs is
    # deliberately an ABSOLUTE path shared across every goal in the batch, so the
    # whole batch lands in one traces.db that report.py can read as a unit.
    config = {
        "llm": {
            "provider": "openai", "model": model, "base_url": base_url,
            "temperature": 0.2, "api_key": "local-key",
        },
        "embedding": {"model": embed_model, "base_url": base_url},
        "autonomy": {
            "mode": "guardrails",
            "run_verification_enabled": True,
            "web_lookup_enabled": True,
            "web_lookup_auto_approve": True,
        },
        "paths": {"skills": "./skills", "memory": "./memory", "logs": shared_logs_dir},
    }
    (workspace_path / "kriya.yaml").write_text(yaml.dump(config))
    (workspace_path / "skills").mkdir(exist_ok=True)


def _kriya_executable():
    candidate = os.path.join(os.path.dirname(sys.executable), "kriya")
    return candidate if os.path.exists(candidate) else "kriya"


def _run_goal(goal, workspace_path, timeout):
    args = [_kriya_executable(), "--config", "kriya.yaml", "generate", goal.text, "-y", *goal.extra_args]
    try:
        result = subprocess.run(
            args, cwd=workspace_path, capture_output=True, text=True, timeout=timeout,
        )
        return {
            "goal_id": goal.id, "returncode": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr, "timed_out": False,
        }
    except subprocess.TimeoutExpired as ex:
        return {
            "goal_id": goal.id, "returncode": None,
            "stdout": (ex.stdout or b"").decode("utf-8", errors="replace") if isinstance(ex.stdout, bytes) else (ex.stdout or ""),
            "stderr": (ex.stderr or b"").decode("utf-8", errors="replace") if isinstance(ex.stderr, bytes) else (ex.stderr or ""),
            "timed_out": True,
        }
    except Exception as ex:
        # A crash launching the subprocess itself (not a Kriya-internal failure)
        # must not abort the rest of the batch - record it and move on.
        return {
            "goal_id": goal.id, "returncode": None,
            "stdout": "", "stderr": f"Harness failed to launch this goal: {ex}", "timed_out": False,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=LIVE_LLM_MODEL)
    parser.add_argument("--embed-model", default=LIVE_EMBED_MODEL)
    parser.add_argument("--base-url", default=LIVE_BASE_URL)
    parser.add_argument("--goal-id", action="append", default=None,
                         help="Run only these goal id(s) instead of the full set. Repeatable.")
    parser.add_argument("--timeout-per-goal", type=int, default=1200,
                         help="Seconds before a single goal's `kriya generate` is killed and recorded as timed_out.")
    parser.add_argument("--batch-dir", default=None,
                         help="Where to put this batch's workspaces/logs/summary. Defaults to a timestamped "
                              "directory under spikes/eval_harness/runs/.")
    args = parser.parse_args()

    goals = GOALS if not args.goal_id else [g for g in GOALS if g.id in args.goal_id]
    if not goals:
        print(f"No goals matched --goal-id {args.goal_id}. Known ids: {[g.id for g in GOALS]}", file=sys.stderr)
        sys.exit(1)

    batch_ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    batch_dir = os.path.abspath(args.batch_dir or os.path.join(HARNESS_DIR, "runs", batch_ts))
    logs_dir = os.path.join(batch_dir, "logs")
    os.makedirs(logs_dir, exist_ok=True)

    print(f"Eval harness batch: {batch_dir}")
    print(f"Model: {args.model} | Embed: {args.embed_model} | Base URL: {args.base_url}")
    print(f"Goals: {[g.id for g in goals]}\n")

    summary_lines = [
        f"Eval harness batch {batch_ts}",
        f"Model: {args.model} | Embed: {args.embed_model} | Base URL: {args.base_url}",
        f"traces.db: {os.path.join(logs_dir, 'traces.db')}",
        "",
    ]

    for goal in goals:
        goal_dir = Path(os.path.join(batch_dir, "workspaces", goal.id))
        goal_dir.mkdir(parents=True, exist_ok=True)
        _init_git_repo(goal_dir)
        _write_config(goal_dir, logs_dir, args.model, args.embed_model, args.base_url)

        print(f"--- Running goal '{goal.id}' (timeout {args.timeout_per_goal}s) ---")
        start = time.time()
        result = _run_goal(goal, goal_dir, args.timeout_per_goal)
        duration = time.time() - start

        goal_log_path = os.path.join(logs_dir, f"{goal.id}.stdout.log")
        with open(goal_log_path, "w", encoding="utf-8") as fh:
            fh.write(result["stdout"])
            if result["stderr"]:
                fh.write("\n=== stderr ===\n")
                fh.write(result["stderr"])

        if result["timed_out"]:
            outcome = f"TIMED OUT after {args.timeout_per_goal}s"
        elif result["returncode"] == 0:
            outcome = "CLI exited 0"
        else:
            outcome = f"CLI exited {result['returncode']}"

        print(f"    {outcome} ({duration:.1f}s) - full output: {goal_log_path}")
        summary_lines.append(f"[{goal.id}] {outcome} - {duration:.1f}s - log: {goal_log_path}")

    summary_path = os.path.join(batch_dir, "summary.txt")
    with open(summary_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(summary_lines) + "\n")

    print(f"\nBatch complete. Summary: {summary_path}")
    print(f"Run the report with:\n  .venv/bin/python spikes/eval_harness/report.py --logs-dir {logs_dir}")


if __name__ == "__main__":
    main()
