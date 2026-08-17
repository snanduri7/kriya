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

# 2026-08-15: reverted from qwen3-coder:30b-a3b-q8_0 back to the plain qwen3-coder:30b
# tag (the pre-Q8 default) at the user's explicit request, after that night's live
# ignite_qpid_protocol run timed out at 2400s without resolving a real cross-file
# package-mismatch bug (see docs/kriya_backlog_and_lessons.md for the root cause and
# the DeveloperAgent fix that addresses it) - not itself evidence Q8 was the cause of
# that specific timeout, just the user's own call on which model to run primary.
LIVE_LLM_MODEL = os.environ.get("KRIYA_LIVE_LLM_MODEL", "qwen3-coder:30b")
LIVE_EMBED_MODEL = os.environ.get("KRIYA_LIVE_EMBED_MODEL", "embeddinggemma:latest")
LIVE_BASE_URL = os.environ.get("KRIYA_LIVE_BASE_URL", "http://localhost:11434/v1")
# Kriya's own packaged default_config.yaml ships qwen3.6:35b-a3b-q4_K_M
# (reasoning: false) as the sole llm_chain fallback (2026-08-15 - previously
# deepseek-r1:32b, reasoning: true, which was never actually pulled locally: a
# reasonable-looking default that was silently unreachable the whole time it was
# configured, confirmed via `ollama list`). A reasoning fallback is a real problem
# for an UNATTENDED harness in particular: confirmed live, 2026-08-06/07
# (python_task_tracker, ignite_qpid_person), that escalating to a reasoning model can
# single-handedly burn the harness's --timeout-per-goal budget - individual
# completions took 2-5+ minutes each, consistent with the already-cited spike finding
# (spikes/tool_call_developer/run_spike_reasoning_on_retry.py) that a reasoning model
# was 13x slower for zero correctness benefit on a fact-recall-class retry, and more
# recent, more direct think:true/think:false A/B data for THIS exact model
# (spikes/model_speed_poc/, 2026-08-15) found the same pattern 71-85x, not just 13x.
# qwen3.6:35b-a3b-q4_K_M is the same fast, non-reasoning fallback
# kriya-protocol-parser-app's own kriya.yaml independently chose and never hit this
# problem with - not a guess, a model already validated live in this exact fallback
# role, for this exact goal.
LIVE_FALLBACK_MODEL = os.environ.get("KRIYA_LIVE_FALLBACK_MODEL", "qwen3.6:35b-a3b-q4_K_M")
# Kriya's own kriya/config/config.py::SearchConfig deliberately makes
# autonomy.web_lookup_enabled and search.base_url two SEPARATE switches - "so
# a config merge/copy-paste can't silently enable outbound search" - but
# _write_config() below was only ever setting the first one. Confirmed live,
# 2026-08-07: every eval-harness run to date had web_lookup_enabled=true and
# web_lookup_auto_approve=true (both already hardcoded below) with NO
# search.base_url anywhere in this file or any root kriya.yaml, so the
# retry-loop live-lookup gate (`and self.kernel.config.search.base_url`) was
# always false and live lookup was silently inert the entire time, despite
# looking "on." http://localhost:8080 matches docs/user_guide.md Section
# 4.6's own documented convention for a self-hosted SearXNG instance - not a
# guess, the same address that section's own live testing was run against.
# Empty base_url (or no SearXNG actually reachable there) still degrades
# safely: search_web() no-ops on an empty base_url, and logs a WARNING and
# returns [] on a connection failure rather than raising - so setting this
# default never breaks a run that doesn't have SearXNG running, it just means
# live lookup won't find anything, the same as before this fix, just visibly
# (a WARNING in the log) instead of silently (the gate never firing at all).
LIVE_SEARCH_BASE_URL = os.environ.get("KRIYA_LIVE_SEARCH_BASE_URL", "http://localhost:8080")

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))


def _init_git_repo(path):
    # Called unconditionally on every invocation, including a --batch-dir
    # resume of an already-populated goal directory - not just a fresh one.
    # MUST no-op entirely once a repo already exists here - a real, more
    # serious bug than the embedded-repo warning this comment used to
    # describe (that half is still true and still worth knowing, kept
    # below): re-running `git add -A` + commit against a goal directory a
    # PRIOR successful run already wrote a generated app into swept that
    # leftover application code (source files, and for a Java goal even
    # compiled target/*.class bytecode) into a brand new "initial" commit,
    # which then becomes what create_git_worktree's worktree-reset logic
    # (kriya/workflow/worktree.py) checks the reused worktree out to -
    # so a "fresh" resumed run silently starts from the PREVIOUS run's
    # generated code, not a clean slate. Confirmed live (2026-08-17,
    # b-10p): ignite_qpid_person's second run's baseline commit contained
    # its own compiled .class files and Maven metadata from the first run;
    # django_healthcheck_gap's second run generated a different directory
    # layout (myapp/-prefixed) than its first run's root-level files, and
    # the two conflicting layouts coexisting on disk produced a cascade of
    # confusing failures (MISDIRECTED EDIT, a stray bare verification
    # marker in old leftover files, a Django ModuleNotFoundError) that had
    # nothing to do with the current model's actual output. The ORIGINAL
    # embedded-repo bug (a --batch-dir resume's `git add -A` picking up
    # .kriya/worktree - a real git worktree with its own .git file - as an
    # embedded repository, which git warns or, on some versions, hard-fails
    # exit 128 on) had been silently PREVENTING this deeper bug from
    # manifesting in most runs by crashing/warning before the harness ever
    # got this far - fixing only that symptom (an earlier, incomplete pass
    # at this function) let the harness proceed further and hit this one.
    if (path / ".git").exists():
        return
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "eval-harness@kriya.local"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Kriya Eval Harness"], cwd=path, check=True)
    (path / "README.md").write_text("eval harness scratch project\n")
    (path / ".gitignore").write_text(".kriya/\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=path, check=True)


def _write_config(workspace_path, shared_logs_dir, model, embed_model, base_url, fallback_model, search_base_url, self_correction, best_of_n, log_level):
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
        # Explicit, fast, non-reasoning fallback - see LIVE_FALLBACK_MODEL's own
        # comment for why this can't be left to inherit Kriya's packaged
        # default_config.yaml chain (deepseek-r1:32b, reasoning: true) here.
        "llm_chain": [
            {
                "model": fallback_model, "base_url": base_url,
                "reasoning": False, "context_window": 16384, "temperature": 0.2,
            },
        ],
        "embedding": {"model": embed_model, "base_url": base_url},
        "autonomy": {
            "mode": "guardrails",
            "run_verification_enabled": True,
            "web_lookup_enabled": True,
            "web_lookup_auto_approve": True,
            # Off by default (matches AutonomyConfig's own default) unless
            # --self-correction is passed - lets a batch be run twice, flag
            # on vs. off, for a real before/after comparison of the new
            # compile-gate micro-loop (kriya/workflow/self_correction.py)
            # against the same goal set, not just its mocked unit tests.
            "self_correction_loop_enabled": self_correction,
            # 1 by default (matches AutonomyConfig's own default, i.e. off) unless
            # --best-of-n is passed - same before/after comparison pattern as
            # --self-correction above, for kriya/workflow/best_of_n.py's sequential,
            # first-attempt-only independent-candidate retry.
            "best_of_n_first_attempt": best_of_n,
        },
        # The second, separate switch web_lookup_enabled alone doesn't turn on -
        # see LIVE_SEARCH_BASE_URL's own comment above for why this was missing
        # entirely until now.
        "search": {"base_url": search_base_url, "top_k": 3},
        "paths": {"skills": "./skills", "memory": "./memory", "logs": shared_logs_dir},
        # kriya/config/default_config.yaml's own packaged default is "INFO" - only
        # written here at all so --log-level DEBUG (see that flag's own help text)
        # can override it per batch; every prior batch got this by omission anyway,
        # so passing the same "INFO" back through when unset is a no-op, not a
        # behavior change.
        "logging": {"level": log_level},
    }
    (workspace_path / "kriya.yaml").write_text(yaml.dump(config))


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
    parser.add_argument("--fallback-model", default=LIVE_FALLBACK_MODEL,
                         help="Non-reasoning llm_chain fallback for every goal's kriya.yaml - "
                              "deliberately explicit, not inherited from Kriya's own packaged "
                              "default (a reasoning model), which was confirmed live to burn "
                              "--timeout-per-goal on its own.")
    parser.add_argument("--search-base-url", default=LIVE_SEARCH_BASE_URL,
                         help="SearXNG-compatible endpoint for autonomy.web_lookup_enabled's "
                              "error-triggered/skill-gap live lookup (kriya/tools/search.py). "
                              "Pass an empty string to leave live lookup configured-but-inert, "
                              "the same as every prior batch before this flag existed.")
    parser.add_argument("--goal-id", action="append", default=None,
                         help="Run only these goal id(s) instead of the full set. Repeatable.")
    parser.add_argument("--timeout-per-goal", type=int, default=1200,
                         help="Seconds before a single goal's `kriya generate` is killed and recorded as timed_out.")
    parser.add_argument("--batch-dir", default=None,
                         help="Where to put this batch's workspaces/logs/summary. Defaults to a timestamped "
                              "directory under spikes/eval_harness/runs/.")
    parser.add_argument("--self-correction", action="store_true",
                         help="Turn on autonomy.self_correction_loop_enabled for every goal in this batch "
                              "(default off, matching AutonomyConfig's own default) - run the same goal set "
                              "with and without this flag for a before/after comparison of the bounded "
                              "compile-gate self-correction micro-loop (kriya/workflow/self_correction.py).")
    parser.add_argument("--best-of-n", type=int, default=1, metavar="N",
                         help="Set autonomy.best_of_n_first_attempt for every goal in this batch (default 1, "
                              "matching AutonomyConfig's own default - i.e. off, today's exact single-attempt "
                              "behavior). N > 1 tries that many independent full-set candidates at the very "
                              "first attempt only before falling into the normal retry loop - run the same "
                              "goal set with and without this flag for a before/after comparison "
                              "(kriya/workflow/best_of_n.py). Deliberately sequential, never parallel - see "
                              "that module's own docstring for why.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                         help="logging.level for every goal's kriya.yaml (default INFO, matching Kriya's own "
                              "packaged default - unchanged batch behavior unless passed). DEBUG is verbose "
                              "(logs the full raw pre-parse Developer completion for every per-file generation "
                              "call, kriya/agents/agent.py) - meant for a targeted, single-goal investigative "
                              "run (pair with --goal-id), not left on for a full batch.")
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
    print(f"Model: {args.model} | Fallback: {args.fallback_model} | Embed: {args.embed_model} | Base URL: {args.base_url}")
    print(f"Search Base URL: {args.search_base_url or '(unset - live lookup configured-but-inert)'}")
    print(f"Self-correction loop: {'ON' if args.self_correction else 'off'}")
    print(f"Best-of-N (first attempt): {args.best_of_n}")
    print(f"Log level: {args.log_level}")
    print(f"Goals: {[g.id for g in goals]}\n")

    summary_lines = [
        f"Eval harness batch {batch_ts}",
        f"Model: {args.model} | Fallback: {args.fallback_model} | Embed: {args.embed_model} | Base URL: {args.base_url}",
        f"Search Base URL: {args.search_base_url or '(unset - live lookup configured-but-inert)'}",
        f"Self-correction loop: {'ON' if args.self_correction else 'off'}",
        f"Best-of-N (first attempt): {args.best_of_n}",
        f"Log level: {args.log_level}",
        f"traces.db: {os.path.join(logs_dir, 'traces.db')}",
        "",
    ]

    for goal in goals:
        goal_dir = Path(os.path.join(batch_dir, "workspaces", goal.id))
        goal_dir.mkdir(parents=True, exist_ok=True)
        _init_git_repo(goal_dir)
        _write_config(goal_dir, logs_dir, args.model, args.embed_model, args.base_url, args.fallback_model, args.search_base_url, args.self_correction, args.best_of_n, args.log_level)

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
