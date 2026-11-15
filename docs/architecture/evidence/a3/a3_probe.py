"""Slice A3 probe (model-free, read-only against the baseline code; writes only inside a temporary directory).

Run: PYTHONPATH=<closure worktree> PYTHONDONTWRITEBYTECODE=1 KRIYA_STATE_DIR=<tmp> KRIYA_LOG_DIR=<tmp> KRIYA_MODEL_RUNTIME_PROBE=0 \
     <python> docs/architecture/evidence/a3/a3_probe.py

Each probe prints what it measured. A probe that cannot be set up prints PROBE_SETUP_FAILED with the error and measures nothing
(it is then not evidence for anything).
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import traceback
from types import SimpleNamespace


def section(name: str) -> None:
    print(f"\n=== {name}")


def guarded(name: str, body) -> None:
    section(name)
    try:
        body()
    except Exception as error:  # a probe that cannot run is reported, never read as a result
        print(f"PROBE_SETUP_FAILED {type(error).__name__}: {error}")
        traceback.print_exc(limit=3)


# ---------------------------------------------------------------- P1: the unit pass predicate folds in "final review performed"
def p1() -> None:
    from kriya.workflow.outcome import terminal_failure_category
    from kriya.workflow.state import GenerationState

    def state(**over):
        base = dict(candidate_gates_succeeded=True, terminal_regression_succeeded=True, overall_attempt_succeeded=True,
                    quality_gates_succeeded=True, api_contract_recovery=None, final_review_refusal=None,
                    environment_failure=None, plan_scope_conflict=None, no_progress_terminated=False)
        base.update(over)
        return SimpleNamespace(**base)

    applied = state()
    refused = state(final_review_refusal={"reason_code": "INFERENCE_DEADLINE_EXCEEDED", "detail": "probe"})
    print("applied, review performed  -> final_workflow_quality_passed =", GenerationState.final_workflow_quality_passed(applied))
    print("applied, review refused    -> final_workflow_quality_passed =", GenerationState.final_workflow_quality_passed(refused))
    print("applied, review refused    -> terminal_failure_category     =", terminal_failure_category(refused))


# ---------------------------------------------------------------- P2: the same unit result read by the two executors
def p2() -> None:
    from kriya.workflow import enforce_loop, plan_executor

    result = {"quality_gates_passed": False, "candidate_gates_passed": True, "terminal_regression_passed": True,
              "overall_attempt_passed": True, "failure_category": "final_review_refused", "files": ["a.py"],
              "final_review_refusal": {"reason_code": "INFERENCE_DEADLINE_EXCEEDED", "detail": "probe", "candidate_applied": True,
                                       "committed_work_units": None, "rolled_back": False}}
    print("plan_executor._passed(result)                 =", plan_executor._passed(result))
    print("enforce_loop.enforce_unit_gates_accepted      =", enforce_loop.enforce_unit_gates_accepted(result))
    print("enforce_loop.advisory_review_refusal is None  =", enforce_loop.advisory_review_refusal(result) is None)
    print("enforce_loop.committed_files(result)          =", enforce_loop.committed_files(result))


# ---------------------------------------------------------------- P3: a two-unit milestone plan whose second unit's review was refused
def p3() -> None:
    from kriya.workflow import plan_executor
    from kriya.workflow.execution_plan import ExecutionPlan, PlanSourceKind, WorkUnit, WorkUnitRole

    calls = []

    class Driver(plan_executor.PlanDriver):
        async def run_unit(self, plan, unit, invocation, *, resume, resume_id):
            calls.append(("run_unit", unit.id))
            if unit.id == "m1":
                return {"quality_gates_passed": True, "files": ["one.py"]}
            return {"quality_gates_passed": False, "failure_category": "final_review_refused", "files": ["two.py"],
                    "final_review_refusal": {"reason_code": "INFERENCE_DEADLINE_EXCEEDED", "candidate_applied": True, "rolled_back": False}}

        def retry_failed_unit(self, unit, result):
            calls.append(("retry_failed_unit", unit.id))
            return False

        async def unit_failed(self, unit, result):
            calls.append(("unit_failed", unit.id))
            return {"status": "milestone_failed", "milestone_id": unit.id, "result": result}

        async def complete_unit(self, unit, result):
            calls.append(("complete_unit", unit.id))
            return None

    units = (
        WorkUnit(id="m1", goal="first", definition_digest="d1"),
        WorkUnit(id="m2", goal="second", definition_digest="d2", depends_on=("m1",)),
        WorkUnit(id="m3", goal="third", definition_digest="d3", depends_on=("m2",)),
    )
    try:
        plan = ExecutionPlan(plan_id="probe-plan", source_kind=PlanSourceKind.MILESTONE, work_units=units)
    except Exception as first:
        print("three PRIMARY units refused by plan validation:", type(first).__name__, str(first)[:300])
        units = units[:2] + (WorkUnit(id="integration", goal="integrate", definition_digest="d3", role=WorkUnitRole.INTEGRATION,
                                      depends_on=("m1", "m2")),)
        plan = ExecutionPlan(plan_id="probe-plan", source_kind=PlanSourceKind.MILESTONE, work_units=units)
    with tempfile.TemporaryDirectory() as workspace:
        result = asyncio.run(plan_executor._execute(plan, Driver(), workspace, False, None, set(), set()))
    print("driver calls:", calls)
    print("plan result status:", result.get("status"), "| quality_gates_passed:", result.get("quality_gates_passed"))
    print("work_unit_states:", {k: (v.get("status"), v.get("reason_codes")) for k, v in (result.get("work_unit_states") or {}).items()})
    print("committed_work_units key present:", "committed_work_units" in result, "| value:", result.get("committed_work_units"))


# ---------------------------------------------------------------- P4: commit eligibility and requirement truth are separate facts
def p4() -> None:
    from kriya.workflow.obligations import ObligationLedger
    from kriya.workflow.requirements import (
        blocking_requirements,
        derive_requirements,
        requirement_outcomes,
        requirement_truth,
        seed_requirement_obligations,
    )
    from kriya.workflow.terminal_gate_service import TerminalGateReport, requirements_fully_verified

    report = TerminalGateReport(ran=True, requirements_verified=False)
    print("TerminalGateReport(ran=True, requirements_verified=False).commit_eligible =", report.commit_eligible)

    requirements = derive_requirements("Add a function slugify(text) that lowercases the text and replaces spaces with hyphens.")
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, requirements)
    outcomes = requirement_outcomes(ledger, requirements)
    print("requirements:", [r.id for r in requirements.requirements], "| outcomes (no verdict recorded):", {k: v.value for k, v in outcomes.items()})
    print("requirement_truth:", requirement_truth(outcomes, requirements)["status"])
    print("requirements_fully_verified:", requirements_fully_verified(outcomes.values(), None))
    for unknown, unverified in (("record", "record"), ("block", "record"), ("record", "block"), ("block", "block")):
        blocking = blocking_requirements(ledger, requirements, unknown_policy=unknown, unverified_policy=unverified)
        print(f"blocking_requirements(unknown={unknown}, unverified={unverified}) ->", [(r.id, o.value) for r, o in blocking])


# ---------------------------------------------------------------- P5: what the approval.decision record carries
def p5() -> None:
    from kriya.control.launch_approval import build_pending
    from kriya.workflow.workflow import _record_approval_decision

    events = []
    state = SimpleNamespace(attempt_number=1, record_event=events.append)
    _record_approval_decision(state, "approved", ["human_in_the_loop"])
    _record_approval_decision(state, "unavailable", ["diff_size"], reason_code="APPROVAL_REVIEW_EXPIRED")
    for event in events:
        print("approval.decision details keys:", sorted(event.details), "| kind:", event.kind, "| authority:", getattr(event.authority, "value", event.authority))
    pending = build_pending("20261115T000000-deadbeef", "ws-identity", 1, [{"filepath": "a.py", "content": "+x = 1\n"}], "Human-in-the-loop review policy")
    print("launch pending review keys:", sorted(pending))
    print("launch pending review carries candidate_digest:", pending["candidate_digest"][:19], "...")


# ---------------------------------------------------------------- P6: the digests that each name "the candidate"
def p6() -> None:
    import difflib

    from kriya.policy.approval import candidate_digest as approval_diff_digest
    from kriya.repository.commit import candidate_digest as commit_candidate_hash
    from kriya.repository.commit import content_revision
    from kriya.repository.snapshot import raw_digest
    from kriya.workflow.resume_fingerprints import candidate_snapshot_digest
    from kriya.workflow.terminal_commit import CandidateFile, materialize_candidate
    from kriya.workflow.verification_binding import bind_candidate, binding_refusal

    with tempfile.TemporaryDirectory() as root:
        workspace, sandbox = os.path.join(root, "ws"), os.path.join(root, "sandbox")
        os.makedirs(workspace)
        os.makedirs(sandbox)
        text = "x = 1\n"
        with open(os.path.join(sandbox, "a.py"), "w", encoding="utf-8") as handle:
            handle.write(text)
        writes = materialize_candidate(sandbox, workspace, [CandidateFile(relpath="a.py", expected_base_revision=content_revision(""))])
        binding = bind_candidate(writes, workspace)
        diff = "".join(difflib.unified_diff([], text.splitlines(keepends=True), fromfile="a/a.py", tofile="b/a.py"))
        values = {
            "approval diff digest (policy.approval.candidate_digest, launch record + decision file)": approval_diff_digest([{"filepath": "a.py", "content": diff}]),
            "verification binding digest (verification_binding.bind_candidate, memory only)": binding.digest,
            "commit candidate hash (repository.commit.candidate_digest, RunRecord cycle + commit evidence)": commit_candidate_hash(writes, workspace),
            "checkpoint candidate digest (resume_fingerprints.candidate_snapshot_digest, checkpoint)": candidate_snapshot_digest({"a.py": text}),
            "per-file staged digest (state.candidate_digests, memory only)": raw_digest(text.encode("utf-8")),
        }
        for name, value in values.items():
            print(f"{value}  <- {name}")
        print("distinct values:", len(set(values.values())), "of", len(values))
        # the commit guard: same bytes pass, changed bytes refuse, a missing binding refuses
        print("binding_refusal(same batch)    =", binding_refusal(binding, writes, workspace))
        with open(os.path.join(sandbox, "a.py"), "w", encoding="utf-8") as handle:
            handle.write("x = 2\n")
        changed = materialize_candidate(sandbox, workspace, [CandidateFile(relpath="a.py", expected_base_revision=content_revision(""))])
        refusal = binding_refusal(binding, changed, workspace)
        print("binding_refusal(changed bytes) =", refusal[0] if refusal else None)
        refusal = binding_refusal(None, changed, workspace)
        print("binding_refusal(no binding)    =", refusal[0] if refusal else None)


# ---------------------------------------------------------------- P7: which approval answers approve
def p7() -> None:
    from kriya.policy.approval import (
        ApprovalOutcome,
        classify_approval_answer,
        defer_execution_until_source_review,
        stale,
    )

    for answer in (True, False, None, "yes", 1, ApprovalOutcome.approve()):
        outcome = classify_approval_answer(answer)
        print(f"classify_approval_answer({answer!r}) -> {outcome.decision.value} / {outcome.reason_code}")
    for mode in ("human-in-the-loop", "guardrails", "autonomous"):   # "autonomous" is not a mode: the reader must refuse it
        try:
            print(f"defer_execution_until_source_review(enabled=True, mode={mode!r}, wrote_source={{'a.py'}}) =",
                  defer_execution_until_source_review(True, mode, {"a.py"}))
        except Exception as error:
            print(f"mode {mode!r}: {type(error).__name__}: {error}")
    print("defer(enabled=True, human-in-the-loop, wrote nothing) =", defer_execution_until_source_review(True, "human-in-the-loop", set()))
    print("stale(None) =", stale(None), "| stale(lambda: True) =", stale(lambda: True), "| stale(lambda: False) =", stale(lambda: False))


if __name__ == "__main__":
    for label, probe in (("P1 unit pass predicate and the refused final review", p1),
                         ("P2 one unit result, two executors", p2),
                         ("P3 milestone plan, second unit's final review refused after apply", p3),
                         ("P4 commit eligibility vs requirement truth", p4),
                         ("P5 approval.decision record content", p5),
                         ("P6 five digests of one candidate, and the commit guard", p6),
                         ("P7 approval answer classification, held execution, staleness", p7)):
        guarded(label, probe)
