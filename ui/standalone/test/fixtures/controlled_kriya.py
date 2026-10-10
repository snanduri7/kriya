"""A Kriya command whose WORKFLOW BODY is scripted - TEST INFRASTRUCTURE for the desktop Start Run path (GUI M6). It is never shipped or enabled by the product.

Everything around the body is the real Kriya: the click command (`kriya generate --json --launch-id ...`), the preflight, the launch record, the run
coordinator (workspace lock, RunRecord, run id), the attempt-evidence recorder (a real, chained, sealed store with the workspace identity), the trace
logger and the typed result. Only `_generate_impl` - the planning/generation/verification pipeline that would call a model - is replaced by a
script, so the cockpit's execution path can be driven deterministically without any model. Other commands (`traces`, `evidence`, `kup-stdio`, ...)
are the real ones, so reads see exactly the durable records a real run would leave.

A goal containing [controlled:success|failure|crash|die|approval] overrides the outcome for that run.
Script (JSON in KRIYA_CONTROLLED_SCRIPT): {"outcome": "success|failure|crash|die|hold|approval", "step_ms": 300, "release": "<path>"}
  success / failure  -> a run with unit, attempt, model, change and gate records; success seals RunRecord SUCCESS, failure seals FAILURE
  crash              -> raises inside the run (RunRecord FAILURE, no trace row)
  die                -> SIGKILL after the records: nothing is sealed (the launch stays RUNNING, the lock is released by the OS)
  approval           -> records the human decision the workflow reached (Kriya's own recorder), then stops: nothing is approved
  hold               -> records, then waits until the `release` file exists (to observe a run in progress), then succeeds
"""
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
MARKER = re.compile(r"\[controlled:(success|failure|crash|die|approval)\]")


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
            scope.emit("candidate.change", {"decision": "STAGED", "path": "src/clamp.py", "created": True, "deleted": False, "unchanged": False,
                                            "lines_added": 3, "lines_removed": 0, "text_diff": True},
                       content={"after": "def clamp(v, lo, hi):\n    return max(lo, min(hi, v))\n", "diff": "+def clamp(v, lo, hi):\n+    return max(lo, min(hi, v))\n"})
            time.sleep(STEP)
            passed = OUTCOME != "failure"
            scope.emit("gate.result", {"gate": "test", "success": passed, "stage": "targeted", "exit_code": 0 if passed else 1})
            if OUTCOME == "crash":
                raise RuntimeError("scripted crash inside the run")
            if OUTCOME == "die":
                os.kill(os.getpid(), signal.SIGKILL)
            if OUTCOME == "hold":
                release = SCRIPT.get("release")
                while release and not os.path.exists(release):
                    time.sleep(0.05)
        pointer = scope.store_pointer()
        success = OUTCOME in ("success", "hold")
        status = "success" if success else "failure"
        events = [{"kind": "evidence.attempt_store", "attempt": 0, "source": "workflow.run_generation_workflow", "authority": "auxiliary",
                   "message": "attempt evidence store", "failure_type": None, "operation": None, "details": pointer, "created_at": started}]
        TraceLogger(os.path.join(resolve_state_directory(cfg)[0], "traces.db")).log_run(
            run_id=trace_id, goal=goal, duration_sec=time.time() - started, attempts=1, status=status, files_modified=["src/clamp.py"] if success else [],
            failure_category=None if success else "quality_gate_failed", run_events=events)
        if success:
            _complete_successful_run(context)
    output.result = {"status": status, "quality_gates_passed": success, "run_id": trace_id, "files": ["src/clamp.py"] if success else [],
                     **({} if success else {"failure_category": "quality_gate_failed"})}
    sys.exit(0 if success else 1)


cli._generate_impl = controlled_generate

if __name__ == "__main__":
    cli.main()
