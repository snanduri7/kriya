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
  runtime_review     -> records a candidate and a passing gate, then reaches the REAL runtime-command approval path (kriya.control.launch.request_runtime_command_approval, the same call
                        the generate callback makes for the executor) and waits for the human's decision: approve -> the commands are RECORDED as executed (a run_verification gate outcome
                        with steps) and the run succeeds; reject -> nothing is recorded as executed and the run FAILS (the real required-verification-declined outcome)
                        script keys: "runtime_commands" [[argv]...], "runtime_criterion", "runtime_stdout"
  greenfield_flow    -> the Demo 00 shape in two decisions: the REAL change-approval path (one source review), then the REAL runtime-command approval path (the verification-only step), each waiting
                        for its own human decision; approve both -> the run succeeds with the commands recorded as executed; script key "tests": n records n test cases on the test gate
  Optional script keys (PRODUCT-UX-COMPLETION screenshots and smokes; all opt-in, absent = the behaviour above):
    "diffs": [{"path": "...", "diff": "<unified diff text>"}]   the change the review is about (and the candidate/trace records follow it) instead of the synthetic one-line diff
    "compiled_goal": true   the trace row of the unit carries the COMPILED "=== Authoritative Goal ===" prompt and names a terminal "<id>.enforce" row that holds the original request
    "rich_events": true     records the run events a real run leaves (generation.completed, candidate_gates.passed, approval.decision, terminal_regression.passed, attempt.passed)
    "gate_outcomes": [...]  the gate outcomes stored on the trace row (e.g. a recorded runtime execution with steps)
    "post_approval_ms": n   how long the run keeps working after an approval (so a screenshot can see "Finishing")
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
DIFFS = SCRIPT.get("diffs") or []


def _changes_of_diffs(diffs):
    out = []
    for d in diffs:
        lines = d["diff"].splitlines()
        out.append({"path": d["path"], "kind": "added" if any(line.startswith("@@ -0,0 ") for line in lines) else "modified",
                    "lines_added": sum(1 for line in lines if line.startswith("+") and not line.startswith("+++")), "lines_removed": sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))})
    return out


CHANGES = SCRIPT.get("changes") or (_changes_of_diffs(DIFFS) if DIFFS else [{"path": "src/clamp.py", "kind": "added", "lines_added": 3, "lines_removed": 0}])
STEP = float(SCRIPT.get("step_ms", 300)) / 1000.0
OUTCOME = SCRIPT.get("outcome", "success")
MARKER = re.compile(r"\[controlled:(success|failure|crash|die|approval|review|runtime_review|greenfield_flow)\]")


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
            for change in CHANGES:
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
            gate_payload = {"gate": gate_name, "success": passed, "stage": "targeted", "exit_code": 0 if passed else 1}
            if SCRIPT.get("tests") is not None and gate_name == "test":   # the structured test summary the recorder keeps (cases, status counts)
                gate_payload["test_execution"] = {"cases": int(SCRIPT["tests"]), "status_counts": {"passed": int(SCRIPT["tests"])}}
            scope.emit("gate.result", gate_payload)
            if OUTCOME == "crash":
                raise RuntimeError("scripted crash inside the run")
            if OUTCOME == "die":
                os.kill(os.getpid(), signal.SIGKILL)
            if OUTCOME == "hold":
                release = SCRIPT.get("release")
                while release and not os.path.exists(release):
                    time.sleep(0.05)
            approved = None
            runtime_decision = None
            runtime_commands = SCRIPT.get("runtime_commands") or [["mvn", "-q", "package"], ["java", "-cp", "target/classes", "com.example.App"]]
            if OUTCOME == "greenfield_flow" and passed:
                from kriya.control.launch import request_change_approval
                files = [{"filepath": c["path"], "content": f"--- a/{c['path']}\n+++ b/{c['path']}\n@@ -0,0 +1,3 @@\n+public class HelloKriya {{\n+  public static void main(String[] a) {{ System.out.println(\"Hello from Kriya\"); }}\n+}}\n"} for c in CHANGES]
                awaiting = request_change_approval(files, SCRIPT.get("review_reason") or "Human-in-the-loop review policy\n\n=== Automated Code Review ===\nThe application prints the requested greeting and includes a unit test.")
                approved = asyncio.run(awaiting) if awaiting is not None else False
            if (OUTCOME == "runtime_review" or (OUTCOME == "greenfield_flow" and approved is True)) and passed:
                from kriya.control.launch import request_runtime_command_approval
                from kriya.workflow.runtime_command_approval import build_subject
                subject = build_subject(unit_id="s4", criterion=SCRIPT.get("runtime_criterion") or 'The application prints "Hello from Kriya" and exits with status 0.',
                                        working_directory=os.path.join(os.getcwd(), ".kriya", "worktrees", "candidate-1"), commands=runtime_commands, input_channel="none",
                                        stdin_payload=None, reason="Allow Kriya to execute these command(s)?")
                awaiting = request_runtime_command_approval(subject)
                decision = asyncio.run(awaiting) if awaiting is not None else None
                runtime_decision = bool(decision is not None and decision.approved)
                if runtime_decision and SCRIPT.get("post_approval_ms"):
                    time.sleep(float(SCRIPT["post_approval_ms"]) / 1000.0)
            if OUTCOME == "review" and passed:
                from kriya.control.launch import request_change_approval
                scripted = CHANGES
                files = ([{"filepath": d["path"], "content": d["diff"]} for d in DIFFS] if DIFFS
                         else [{"filepath": c["path"], "content": f"--- a/{c['path']}\n+++ b/{c['path']}\n@@ -1 +1 @@\n-old\n+new\n"} for c in scripted])
                awaiting = request_change_approval(files, SCRIPT.get("review_reason") or "Human-in-the-loop review policy\n\n=== Automated Code Review ===\nThe change implements the goal.")
                approved = asyncio.run(awaiting) if awaiting is not None else False
                if approved and SCRIPT.get("post_approval_ms"):
                    time.sleep(float(SCRIPT["post_approval_ms"]) / 1000.0)
        pointer = scope.store_pointer()
        success = (OUTCOME in ("success", "hold") and passed) or (approved is True and OUTCOME != "greenfield_flow") or runtime_decision is True
        rejected = (OUTCOME == "review" or (OUTCOME == "greenfield_flow" and runtime_decision is None)) and passed and not approved
        status = "success" if success else "human_rejected" if rejected else "failure"
        events = [{"kind": "evidence.attempt_store", "attempt": 0, "source": "workflow.run_generation_workflow", "authority": "auxiliary",
                   "message": "attempt evidence store", "failure_type": None, "operation": None, "details": pointer, "created_at": started}]
        if SCRIPT.get("rich_events"):
            def event(kind, at, **details):
                return {"kind": kind, "attempt": 1, "source": "workflow", "authority": "authoritative", "message": kind, "failure_type": None, "operation": None, "details": details, "created_at": at}
            now = time.time()
            events += [event("attempt.started", started + 0.5), event("generation.completed", started + 1.0, file_count=len(CHANGES)),
                       event("candidate_gates.passed", started + 1.5, passed=passed, terminal=False)]
            if OUTCOME == "review" and passed:
                events.append(event("approval.decision", now - 0.2, outcome="approved" if approved else "rejected", triggers=["human_in_the_loop"]))
            if success:
                events += [event("terminal_regression.passed", now - 0.1, passed=True, terminal=True), event("attempt.passed", now, passed=True, applied=True)]
        gates = SCRIPT.get("gate_outcomes")
        if runtime_decision is True:  # what the executor records when the approved commands ran: one step per command (argv, exit code, stdout, stderr)
            gates = (gates or []) + [{"type": "run_verification", "success": True, "attempt": 1, "steps": [
                {"command": list(c), "exit_code": 0, "stdout": SCRIPT.get("runtime_stdout", "Hello from Kriya\n"), "stderr": "", "timed_out": False} for c in runtime_commands]}]
        logger = TraceLogger(os.path.join(resolve_state_directory(cfg)[0], "traces.db"))
        modified = [c["path"] for c in CHANGES] if success else []
        common = dict(duration_sec=time.time() - started, attempts=1, status=status, files_modified=modified, failure_category=None if success else status if rejected else "quality_gate_failed")
        if SCRIPT.get("compiled_goal"):
            enforce_id = f"{trace_id}.enforce"
            compiled = ("=== Authoritative Goal ===\nThis is the real, unmediated user request - the source of truth for what is actually required.\n\n" + goal
                        + "\n\n=== Planned Implementation ===\nPlanner detail the user never wrote.")
            logger.log_run(run_id=trace_id, goal=compiled, run_events=events, gate_outcomes=gates, enforce_run_id=enforce_id, **common)
            logger.log_run(run_id=enforce_id, goal=goal, run_events=[], **common)
        else:
            logger.log_run(run_id=trace_id, goal=goal, run_events=events, gate_outcomes=gates, **common)
        if success:
            _complete_successful_run(context)
    if rejected:
        # the workflow's own abort result: no status, a checkpoint-style run_id (NOT the trace row id)
        output.result = {"files": [], "quality_gates_passed": False, "review": "reviewed", "run_id": "20200101T000000-0badc0de"}
        sys.exit(1)
    output.result = {"status": status, "quality_gates_passed": success, "run_id": trace_id, "files": [c["path"] for c in CHANGES] if success else [],
                     **({} if success else {"failure_category": "quality_gate_failed"})}
    sys.exit(0 if success else 1)


cli._generate_impl = controlled_generate

if __name__ == "__main__":
    cli.main()
