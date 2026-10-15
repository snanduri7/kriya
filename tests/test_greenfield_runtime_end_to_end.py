"""Demo V2, controlled Greenfield end to end (Phase 22): Demo 00's exact goal through the REAL controller, workflow and launch layer, with a scripted model and the REAL decision functions
a desktop uses (``approval.decide`` is what ``kriya launch-decision`` calls; the host/native-confirmation half is covered in ui/standalone/test/runtime_command_host.test.ts).

Chain proved here: runtime detector true -> four units preserved -> three source changes, each held ONCE as a typed CHANGE review with files -> s4 (verification-only) never asks an
empty source approval -> a typed RUNTIME-COMMAND review -> decided with its own kind and the fingerprint -> the exact reviewed commands execute -> recorded steps -> ApplicationOutcome
EXECUTED with the printed output -> the run succeeds. Stubbed: the model transport, the toolchain gates and the process execution itself (``run_app_sequence``: no Maven in a unit test);
the argv handed to the executor is compared with the argv the person reviewed.
"""
import threading
import time

import _enforce_unit_harness as harness
import pytest
from test_greenfield_runtime_verification_dropped import GOAL, JUDGE, MAIN, POM, TEST, _plan
from test_launch_approval import LID, _session

import kriya.control.launch_approval as approval
from kriya.control import launch as launch_module
from kriya.control.persistence import load_launch_record
from kriya.kup.application_outcome import STATUS_EXECUTED, application_outcome
from kriya.workflow.runtime_command_approval import TypedApprovalCallback

UNITS = {"s1": (POM, "<project/>\n"),
         "s2": (MAIN, "package com.example;\npublic class HelloKriya {\n  public static void main(String[] a) { System.out.println(\"Hello from Kriya\"); }\n}\n"),
         "s3": (TEST, "package com.example;\npublic class HelloKriyaTest {}\n")}


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(approval, "POLL_SECONDS", 0.01)


class Person:
    """The desktop's human + host, as a thread: it watches the launch record the way the cockpit does and answers each pending review with ``approval.decide`` and the review's OWN kind
    and identity (exactly what ``kriya launch-decision`` receives). ``plan`` maps a review kind to ('approve'|'reject', seconds to think first)."""

    def __init__(self, canonical, plan=None):
        self.canonical, self.plan, self.seen, self.log = canonical, plan or {}, [], []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(10)

    def _loop(self):
        decided = set()
        while not self._stop.is_set():
            record = load_launch_record(self.canonical, LID) or {}
            pending = record.get("pending_approval")
            if isinstance(pending, dict) and pending.get("status") == "PENDING" and pending["review_id"] not in decided:
                decided.add(pending["review_id"])
                self.seen.append(pending)
                choice, think = self.plan.get(pending["kind"], ("approve", 0.0))
                self.log.append(("pending", pending["kind"]))
                time.sleep(think)
                self.log.append(("deciding", pending["kind"]))
                approval.decide(self.canonical, LID, pending["review_id"], pending["candidate_digest"], choice, pending["kind"])
            time.sleep(0.01)


def _callback():
    return TypedApprovalCallback(lambda files, reason: launch_module.request_change_approval(files, reason), lambda subject: launch_module.request_runtime_command_approval(subject))


def _launch_on(opened, plan, person_cls=Person):
    """`on_workspace` hook: open the launch session on the engine's own folder (the real arrangement: one folder, one launch, the run lock held under the launch id) and start the person."""
    def hook(workspace):
        canonical, session = _session(workspace)
        opened["canonical"], opened["session"] = canonical, session
        opened["person"] = person_cls(canonical, plan)
        opened["person"].start()
    return hook


def _go(tmp_path, plan=None, judge=JUDGE, person_cls=Person):
    opened = {}
    executed_at = []
    try:
        run = harness.run_enforce(tmp_path, plan=_plan(), units=UNITS, goal=GOAL, run_verification=True, judge_reply=judge, app_output="Hello from Kriya\n", callback=_callback(),
                                  record_steps=True, run_id=LID, on_workspace=_launch_on(opened, plan, person_cls),
                                  on_execute=lambda commands: executed_at.append((time.monotonic(), opened["person"].log[:])),
                                  on_build=lambda: opened["person"].log[:])
    finally:
        if "person" in opened:
            opened["person"].stop()
        if "session" in opened:
            opened["session"].finish(0, None, None)
    return run, opened["person"], opened["canonical"], executed_at


def test_demo_00_runs_the_whole_chain_with_one_human_decision_per_subject(tmp_path):
    run, person, canonical, executed_at = _go(tmp_path)

    # the person was asked exactly four times, in this order, each with its own subject: three source changes (each with files) and then the runtime commands
    assert [p["kind"] for p in person.seen] == ["change_approval", "change_approval", "change_approval", "runtime_command"]
    assert [[f["filepath"] for f in p["files"]] for p in person.seen[:3]] == [[POM], [MAIN], [TEST]]                 # ONE source approval each; never an empty one
    assert all("files" not in p for p in person.seen[3:])                                                          # the runtime review is not represented as files
    runtime = person.seen[3]
    assert runtime["unit_id"] == "s4"                                                                                # the verification-only unit owns the runtime obligation
    assert runtime["status"] == "PENDING" and runtime["candidate_digest"] == runtime["command_fingerprint"]
    assert runtime["working_directory_kind"] == "ISOLATED_WORKTREE" and runtime["input_channel"] == "none" and runtime["stdin_present"] is False

    # the commands that executed are EXACTLY the commands the person reviewed, once, and only after the decision
    assert run.executed == [runtime["commands"]], (run.executed, runtime["commands"])
    [(_when, log_at_execution)] = executed_at
    assert log_at_execution[-2:] == [("pending", "runtime_command"), ("deciding", "runtime_command")]                # nothing ran before the person decided

    # the approval is durable and bound; the run succeeded with real recorded execution evidence
    final = load_launch_record(canonical, LID)["pending_approval"]
    assert final["kind"] == "runtime_command" and final["status"] == "APPROVED"
    assert run.result.legacy_result["status"] == "success"
    assert {r.subtask_id: r.status.value for r in run.result.subtask_results} == {"s1": "completed", "s2": "completed", "s3": "completed", "s4": "completed"}

    # ApplicationOutcome: EXECUTED, exit 0, the printed output, from the executor's recorded steps (nothing invented)
    outcome = application_outcome(run.gate_outcomes)
    assert outcome["status"] == STATUS_EXECUTED and outcome["succeeded"] is True and outcome["exit_code"] == 0
    assert outcome["commands"] == [" ".join(c) for c in runtime["commands"]]
    assert "Hello from Kriya" in outcome["stdout"]["text"] and outcome["stderr"]["text"] == ""


def test_gap_b_structured_plan_builds_and_tests_each_unit_only_after_that_unit_s_source_was_approved(tmp_path):
    """The same HITL ordering on the structured plan (the generic owner is run_attempt/run_generation_workflow, shared by every unit): each unit's build/tests start after the
    person decided THAT unit's source review; the runtime review (the verification-only unit) comes last and nothing executes before its decision."""
    run, person, _canonical, _executed_at = _go(tmp_path)
    assert run.builds, "the build/test gates ran"
    for _gate, log_at_build in run.builds:
        approved = [entry for entry in log_at_build if entry == ("deciding", "change_approval")]
        assert approved, f"a build/test gate ran before any source review was decided: {log_at_build}"
    first_log = run.builds[0][1]
    assert first_log[-2:] == [("pending", "change_approval"), ("deciding", "change_approval")]       # the first build ran right after the first unit's source decision
    assert [p["kind"] for p in person.seen][-1] == "runtime_command"


def test_reject_is_the_explicit_refusal_nothing_executes_and_the_run_does_not_claim_success(tmp_path):
    run, person, canonical, executed_at = _go(tmp_path, plan={"runtime_command": ("reject", 0.0)})

    assert [p["kind"] for p in person.seen][-1] == "runtime_command"
    assert run.executed == [] and executed_at == []
    assert load_launch_record(canonical, LID)["pending_approval"]["status"] == "REJECTED"
    # MEASURED on the real path: the declined required check is a recorded verification-infrastructure failure, never a pass
    assert any(g.get("type") == "verification_infrastructure_failure" and str(g.get("output")).startswith("REQUIRED_RUNTIME_VERIFICATION_DECLINED:") and g.get("success") is False for g in run.gate_outcomes)
    assert application_outcome(run.gate_outcomes)["blocked_reason_code"] == "REQUIRED_RUNTIME_VERIFICATION_DECLINED"   # the UI projection names the refusal
    assert run.result.legacy_result["status"] != "success"
    assert application_outcome(run.gate_outcomes)["status"] != STATUS_EXECUTED


def test_CHARACTERISATION_gap_c_the_structured_plan_leaves_the_same_requirement_ledger_pending(tmp_path):
    """Pair of tests/test_greenfield_direct_plan_runtime.py::test_CHARACTERISATION_gap_c_*: the SAME evidence (executed, graded PASS, run success) on the structured plan leaves the same
    pending / NOT_YET_VERIFIED ledger and no requirements_verified. MEASURED 2026-10-15; the brief's premise that this shape closes the ledger is refuted."""
    run, _person, _canonical, _executed_at = _go(tmp_path)
    assert run.result.legacy_result["status"] == "success" and application_outcome(run.gate_outcomes)["status"] == STATUS_EXECUTED
    requirements = run.result.legacy_result["requirements"]
    assert requirements["outcomes"] == {"REQ-1": "pending", "REQ-2": "pending"}
    assert {v["reason_code"] for v in requirements["verdicts"].values()} == {"NOT_YET_VERIFIED"}
    assert requirements["evidence"] == {} and run.result.legacy_result.get("requirements_verified") is None


def test_a_pending_runtime_review_waits_for_the_person_and_nothing_runs_while_it_waits(tmp_path):
    """Native Cancel is 'no decision yet': the review stays PENDING and the executor keeps waiting. Here the person simply takes their time."""
    run, person, canonical, executed_at = _go(tmp_path, plan={"runtime_command": ("approve", 0.4)})

    pending_index = person.log.index(("pending", "runtime_command"))
    deciding_index = person.log.index(("deciding", "runtime_command"))
    assert pending_index < deciding_index
    [(_when, log_at_execution)] = executed_at
    assert ("deciding", "runtime_command") in log_at_execution                                                       # execution happened only after the decision
    assert run.executed and run.result.legacy_result["status"] == "success"


def test_a_decision_of_the_wrong_kind_cannot_approve_the_runtime_review(tmp_path):
    """The change-review action cannot approve a runtime-command review; the review stays pending and the run waits (here the person then decides correctly)."""
    refusals = []

    class Confused(Person):
        def _loop(self):
            decided = set()
            while not self._stop.is_set():
                pending = (load_launch_record(self.canonical, LID) or {}).get("pending_approval")
                if isinstance(pending, dict) and pending.get("status") == "PENDING" and pending["review_id"] not in decided:
                    decided.add(pending["review_id"])
                    self.seen.append(pending)
                    self.log.append(("pending", pending["kind"]))
                    if pending["kind"] == "runtime_command":
                        try:
                            approval.decide(self.canonical, LID, pending["review_id"], pending["candidate_digest"], "approve", "change_approval")
                        except approval.ApprovalRefused as refused:
                            refusals.append(refused.code)
                        refusals.append((load_launch_record(self.canonical, LID) or {})["pending_approval"]["status"])
                    self.log.append(("deciding", pending["kind"]))
                    approval.decide(self.canonical, LID, pending["review_id"], pending["candidate_digest"], "approve", pending["kind"])
                time.sleep(0.01)

    run, _person, _canonical, _executed_at = _go(tmp_path, person_cls=Confused)
    assert refusals == ["WRONG_REVIEW_KIND", "PENDING"]
    assert run.executed and run.result.legacy_result["status"] == "success"


def test_without_a_launch_the_terminal_behaviour_is_unchanged(tmp_path):
    """Outside a desktop launch no review is recorded; the legacy boolean callback is still bound to the subject it was shown (a model-derived command still needs an answer)."""
    asked = []

    def terminal(files, reason):
        asked.append((len(files), "Allow Kriya to execute" in reason))
        return True

    run = harness.run_enforce(tmp_path, plan=_plan(), units=UNITS, goal=GOAL, run_verification=True, judge_reply=JUDGE, app_output="Hello from Kriya\n", callback=terminal)
    assert [a for a in asked if a[0] == 0] == [(0, True)]
    assert run.executed and run.result.legacy_result["status"] == "success"
