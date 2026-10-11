"""A Kriya command whose WORKFLOW BODY is scripted - TEST INFRASTRUCTURE for the desktop Start Run path (GUI M6). It is never shipped or enabled by the product.

Everything around the body is the real Kriya: the click command (`kriya generate --json --launch-id ...`), the preflight, the launch record, the run
coordinator (workspace lock, RunRecord, run id), the attempt-evidence recorder (a real, chained, sealed store with the workspace identity), the trace
logger and the typed result. Only `_generate_impl` - the planning/generation/verification pipeline that would call a model - is replaced by a
script, so the cockpit's execution path can be driven deterministically without any model. Other commands (`traces`, `evidence`, `kup-stdio`, ...)
are the real ones, so reads see exactly the durable records a real run would leave.

A goal containing [controlled:success|failure|crash|die|approval|review] overrides the outcome for that run.
Script (JSON in KRIYA_CONTROLLED_SCRIPT): {"outcome": "success|failure|crash|die|hold|approval", "step_ms": 300, "release": "<path>"}
  success / failure  -> a run with unit, attempt, model, change and gate records; success seals RunRecord SUCCESS, failure seals FAILURE
  crash              -> raises inside the run (RunRecord FAILURE, no trace row)
  die                -> SIGKILL after the records: nothing is sealed (the launch stays RUNNING, the lock is released by the OS)
  approval           -> records the human decision the workflow reached (Kriya's own recorder), then stops: nothing is approved
  hold               -> records, then waits until the `release` file exists (to observe a run in progress), then succeeds
  Optional script keys (the hardening matrix): "changes": [{"path": "...", "kind": "added|modified|deleted", "lines_added": n, "lines_removed": n}] (default: one added
                        src/clamp.py) and "gate": "pass|compile_fail|test_fail|regression_fail" (a failing gate makes the run FAIL, and a `review` run then ends before any review,
                        as the workflow's safe failure does).
  review             -> records a candidate and a passing gate, then reaches the REAL change-approval path (kriya.control.launch.request_change_approval, the same
                        call the generate callback makes) and waits for the human's decision: approve -> the run succeeds; reject -> the run ends human_rejected,
                        exactly as the workflow's abort does (its result carries a checkpoint-style run_id that is NOT the trace row id)
"""
import asyncio
import json
import os
import re
import signal
import sys
import time
import uuid

import click

import kriya.cli as cli
from kriya.control.run_coordinator import _complete_successful_run, begin_mutating_run, mark_run_stage
from kriya.control.run_record import RunLifecycle
from kriya.core.attempt_evidence import scope
from kriya.core.state_paths import resolve_state_directory
from kriya.core.trace import TraceLogger

SCRIPT = json.loads(os.environ.get("KRIYA_CONTROLLED_SCRIPT", "{}"))
STEP = float(SCRIPT.get("step_ms", 300)) / 1000.0
OUTCOME = SCRIPT.get("outcome", "success")
MARKER = re.compile(r"\[controlled:(success|failure|crash|die|approval|review)\]")


def controlled_generate(ctx, goal, file, yes, knowledge_policy, ack, resume, resume_id, json_output, milestones, output, **kwargs):
    global OUTCOME
    marker = MARKER.search(goal)  # a goal may choose its own scripted outcome (screenshot scenarios with one executable)
    if marker:
        OUTCOME = marker.group(1)
    cfg = ctx.obj["config"]
    started = time.time()
    trace_id = uuid.uuid4().hex[:8]
    with begin_mutating_run(os.getcwd()) as context:
        mark_run_stage(context.workspace_path, RunLifecycle.RUNNING)
        if OUTCOME == "approval":
            # the real workflow reaches a human decision: the CLI callback records it, then its prompt finds no terminal (click.Abort)
            from kriya.control.launch import note_approval_required
            note_approval_required("change_approval", "the proposed change touches a sensitive path", {"files": [".github/workflows/ci.yml"]})
            raise click.exceptions.Abort()
        with scope.unit_scope(cfg, "direct", "direct", {"goal_chars": len(goal)}):
            time.sleep(STEP)
            scope.emit("attempt.opened", {"mode": "direct", "operation": "generate"})
            time.sleep(STEP)
            scope.emit("model.request", {"role": "developer"})
            scope.emit("model.result", {"finish_reason": "stop"})
            time.sleep(STEP)
            for change in SCRIPT.get("changes") or [{"path": "src/clamp.py", "kind": "added", "lines_added": 3, "lines_removed": 0}]:
                kind = change.get("kind", "added")
                payload = {"decision": "STAGED", "path": change["path"], "created": kind == "added", "deleted": kind == "deleted", "unchanged": False,
                           "lines_added": change.get("lines_added", 1), "lines_removed": change.get("lines_removed", 0), "text_diff": True}
                if kind != "added":
                    payload["before_digest"] = "sha256:" + "a" * 64
                if kind != "deleted":
                    payload["after_digest"] = "sha256:" + "b" * 64
                scope.emit("candidate.change", payload, content={"diff": "+x\n-y\n"} if kind != "deleted" else {"before": "gone\n"})
            time.sleep(STEP)
            gate_script = SCRIPT.get("gate", "pass")
            passed = OUTCOME != "failure" and gate_script == "pass"
            gate_name = {"compile_fail": "compile", "test_fail": "test", "regression_fail": "regression_test"}.get(gate_script, "test")
            scope.emit("gate.result", {"gate": gate_name, "success": passed, "stage": "targeted", "exit_code": 0 if passed else 1})
            if OUTCOME == "crash":
                raise RuntimeError("scripted crash inside the run")
            if OUTCOME == "die":
                os.kill(os.getpid(), signal.SIGKILL)
            if OUTCOME == "hold":
                release = SCRIPT.get("release")
                while release and not os.path.exists(release):
                    time.sleep(0.05)
            approved = None
            if OUTCOME == "review" and passed:
                from kriya.control.launch import request_change_approval
                scripted = SCRIPT.get("changes") or [{"path": "src/clamp.py", "kind": "added", "lines_added": 3, "lines_removed": 0}]
                awaiting = request_change_approval(
                    [{"filepath": c["path"], "content": f"--- a/{c['path']}\n+++ b/{c['path']}\n@@ -1 +1 @@\n-old\n+new\n"} for c in scripted],
                    "Human-in-the-loop review policy\n\n=== Automated Code Review ===\nThe change implements the goal.")
                approved = asyncio.run(awaiting) if awaiting is not None else False
        pointer = scope.store_pointer()
        success = (OUTCOME in ("success", "hold") and passed) or approved is True
        rejected = OUTCOME == "review" and passed and not approved
        status = "success" if success else "human_rejected" if rejected else "failure"
        events = [{"kind": "evidence.attempt_store", "attempt": 0, "source": "workflow.run_generation_workflow", "authority": "auxiliary",
                   "message": "attempt evidence store", "failure_type": None, "operation": None, "details": pointer, "created_at": started}]
        TraceLogger(os.path.join(resolve_state_directory(cfg)[0], "traces.db")).log_run(
            run_id=trace_id, goal=goal, duration_sec=time.time() - started, attempts=1, status=status, files_modified=["src/clamp.py"] if success else [],
            failure_category=None if success else status if rejected else "quality_gate_failed", run_events=events)
        if success:
            _complete_successful_run(context)
    if rejected:
        # the workflow's own abort result: no status, a checkpoint-style run_id (NOT the trace row id)
        output.result = {"files": [], "quality_gates_passed": False, "review": "reviewed", "run_id": "20200101T000000-0badc0de"}
        sys.exit(1)
    output.result = {"status": status, "quality_gates_passed": success, "run_id": trace_id, "files": ["src/clamp.py"] if success else [],
                     **({} if success else {"failure_category": "quality_gate_failed"})}
    sys.exit(0 if success else 1)


cli._generate_impl = controlled_generate

if __name__ == "__main__":
    cli.main()
