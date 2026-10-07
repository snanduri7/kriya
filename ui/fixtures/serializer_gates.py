#!/usr/bin/env python
"""Deterministic gate_outcomes fixture in the shapes Kriya's OWN writers record, read back through the KUP adapter.

Writer inventory (TRACED 2026-10-04; every site persists through TraceLogger.log_run(gate_outcomes=...) as JSON text):
  * kriya/workflow/failure.py::Failure.to_gate_outcome - EVERY failed gate (attempt.py, workflow.py, retry_strategy.py,
    verification_coordinator.py): attempt, type, success=False, output, mode, likely_files, file_locations,
    failed_content, attempted_edits, self_correction_attempt, attribution_tier/_confidence/_reasoning/_kind,
    subtask_id, plan_id, milestone_id, planned_files, verification_target, authoritative_files; some sites .update()
    commands/steps (runtime), graded_by/deterministic_result/runtime_disposition/verifier_evidence (graded runtime),
    managed_service_outcome (managed service).
  * successful gates are dict literals: attempt, type, success=True, output, plus **execution_evidence(result)
    (kriya/tools/validate.py: toolchain_identity, egress, resources, runtime_artifacts when present) and per-site
    fields: self_corrected/self_correction_turns/self_correction_transcript (compile, targeted_test, run_verification);
    selection_fallback (test); graded_by/commands/steps/deterministic_result + runtime_evidence_outcome_fields(grade)
    (run_verification); deferred_to_future_owner (regression_test, workflow.py); status/reason_code/
    arbitrated_contradictions/planner_only_requirements (goal_spec_compliance).
  * kriya/workflow/attempt.py test_selection: attempt, type="test_selection", success=False, output, target_test,
    recovered_by - a non-Failure unsuccessful record.
  * kriya/workflow/verification_coordinator.py::VerificationCoordinator._gate: attempt, type, success=result.success,
    output, **execution_evidence(result).
Common to every writer: attempt (int), type (str), success (bool), output (str). No writer records gate, passed, name.

The records below are produced by those constructors (Failure.to_gate_outcome, execution_evidence,
runtime_evidence_outcome_fields, EgressDecision.to_dict, VerificationCoordinator._gate) and, for the literal-dict
writers, copied key for key from the cited source lines; then logged, acquired and read back with the real KUP code.
Run from ui/ with the checkout's interpreter: `../.newvenv/bin/python fixtures/serializer_gates.py` (or
`npm run fixtures:serializer`); `--check` fails when the committed file differs. Fixture only: temporary roots, no
protected store, no model, no network, no bytecode written.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import Any, Dict, List

sys.dont_write_bytecode = True

from kriya.core.trace import TraceLogger  # noqa: E402 - after dont_write_bytecode, like the CLI
from kriya.kup.acquire import acquire_snapshot  # noqa: E402
from kriya.kup.inspect import history_detail  # noqa: E402
from kriya.policy.egress import EgressCapability, EgressDecision  # noqa: E402
from kriya.tools.validate import execution_evidence  # noqa: E402
from kriya.workflow.failure import Failure, FileLocation  # noqa: E402
from kriya.workflow.verification_coordinator import VerificationCoordinator  # noqa: E402
from kriya.workflow.verifier_evidence import runtime_evidence_outcome_fields  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "serializer", "gate_outcomes.json")
RUN_ID = "run-serializer-gates"
COMPILE_ERROR = "src/mod1/a.py:12:5: error: name 'audit' is not defined\n1 error\n"
TEST_OUTPUT = "============================= 3 passed in 0.41s =============================\n"
TEST_FAILURE = "FAILED tests/test_mod1.py::test_run - AssertionError: expected 1\n1 failed, 2 passed in 0.52s\n"
EGRESS = EgressDecision(channel="compile", capability=EgressCapability.DENIED, allowed=False,
                        authority_source="autonomy.shell_network", destinations=(), reason="contained compile gate runs without network").to_dict()


def real_gate_outcomes() -> List[Dict[str, Any]]:
    # 1. attempt.py ~8797 family: a failed compile gate, Failure.to_gate_outcome
    compile_failure = Failure(type="compile", message=f"COMPILATION FAILURE:\n{COMPILE_ERROR}", raw_output=COMPILE_ERROR, attempt=1,
                              likely_files=["src/mod1/a.py"], file_locations=[FileLocation("src/mod1/a.py", 12, 5)])
    # 2. attempt.py 8800: a successful compile with the contained toolchain's execution evidence
    compile_ok = {"attempt": 2, "type": "compile", "success": True, "output": "", **execution_evidence({"success": True, "output": "", "egress": EGRESS})}
    # 3. attempt.py 8839: targeted selection collected zero tests (non-Failure unsuccessful record)
    selection = {"attempt": 2, "type": "test_selection", "success": False, "output": "collected 0 items\n", "target_test": "tests/test_mod1.py::test_run", "recovered_by": "full_suite"}
    # 4. attempt.py 8864: the full suite after the selection fallback
    test_fallback = {"attempt": 2, "type": "test", "success": True, "output": TEST_OUTPUT, "selection_fallback": True, **execution_evidence({"success": True, "output": TEST_OUTPUT, "egress": EGRESS})}
    # 5. attempt.py 8907: a targeted test that the self-correction micro-loop resolved
    targeted_self_corrected = {"attempt": 2, "type": "targeted_test", "success": True, "output": TEST_OUTPUT, "self_corrected": True, "self_correction_turns": 2,
                               "self_correction_transcript": [{"turn": 1, "tool": "inspect_member", "result": "ok"}, {"turn": 2, "tool": "repair_with_patch", "result": "resolved"}]}
    # 6. attempt.py 3945 family: a runtime verification failure, Failure.to_gate_outcome + commands/steps
    rv_failure = Failure(type="run_verification", message="RUNTIME VERIFICATION FAILURE: process exited 1\n\nCaptured output:\nTraceback ...\n",
                         raw_output="Traceback (most recent call last): ...\n", attempt=2, likely_files=["src/mod1/a.py"]).to_gate_outcome()
    rv_failure.update({"commands": [["python", "-m", "mod1"]], "steps": [{"command": ["python", "-m", "mod1"], "exit_code": 1, "duration_ms": 412}]})
    # 7. attempt.py 5553: a graded runtime verification that passed (process_exit authority -> deterministic_result "PASS")
    grade = {"passed": True, "reasoning": "exit 0 and the contract markers matched", "disposition": "PASS", "evidence_packages": [{"kind": "stdout", "bytes": 212}]}
    rv_ok = {"attempt": 3, "type": "run_verification", "success": True, "output": "mod1 started\n[Grader reasoning]: " + grade["reasoning"],
             "graded_by": "process_exit", "commands": [["python", "-m", "mod1"]], "steps": [{"command": ["python", "-m", "mod1"], "exit_code": 0, "duration_ms": 388}],
             "deterministic_result": "PASS", **runtime_evidence_outcome_fields(grade), **execution_evidence({"success": True, "egress": EGRESS, "runtime_artifacts": ["mod1.log"]})}
    # 8. workflow.py 5036: a full regression deferred to a future owner
    regression = {"attempt": 3, "type": "regression_test", "success": True, "output": TEST_OUTPUT, "deferred_to_future_owner": "subtask-7", **execution_evidence({"success": True, "egress": EGRESS})}
    # 9. attempt.py 10210: spec compliance that could not run (success True with status UNAVAILABLE and a reason_code)
    spec = {"attempt": 3, "type": "goal_spec_compliance", "success": True, "output": "spec compliance verifier unavailable: request refused", "status": "UNAVAILABLE", "reason_code": "VERIFIER_REQUEST_REFUSED"}
    # 10. verification_coordinator.py::_gate through the real class
    recorded: List[Dict[str, Any]] = []
    coordinator = VerificationCoordinator(validator=None, record_gate_outcome=recorded.append, run_runtime_verification=None)
    coordinator._gate("test", {"success": False, "output": TEST_FAILURE, "egress": EGRESS}, 3)  # pylint: disable=protected-access
    return [compile_failure.to_gate_outcome(), compile_ok, selection, test_fallback, targeted_self_corrected, rv_failure, rv_ok, regression, spec, recorded[0]]


def through_the_adapter(gates: List[Dict[str, Any]]) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        store = os.path.join(tmp, "state", "traces.db")
        os.makedirs(os.path.dirname(store))
        TraceLogger(store).log_run(run_id=RUN_ID, goal="serializer gate fixture", duration_sec=61.23, attempts=3, status="FAILED",
                                   files_modified=["src/mod1/a.py"], gate_outcomes=gates)
        snapshot = acquire_snapshot(store, os.path.join(tmp, "state", "kup-snapshots"))["snapshot"]
        return history_detail(snapshot, RUN_ID)["gate_outcomes"]


def main() -> int:
    gates = real_gate_outcomes()
    section = through_the_adapter(gates)
    assert section["data"] == json.loads(json.dumps(gates)), "the adapter must return the writers' records verbatim"
    for record in section["data"]:
        assert isinstance(record["attempt"], int) and isinstance(record["type"], str) and isinstance(record["success"], bool) and isinstance(record["output"], str)
    doc = {
        "generated_by": "ui/fixtures/serializer_gates.py: Failure.to_gate_outcome / execution_evidence / runtime_evidence_outcome_fields / "
                        "EgressDecision.to_dict / VerificationCoordinator._gate + the cited literal writers -> TraceLogger.log_run -> "
                        "kriya.kup.acquire.acquire_snapshot -> kriya.kup.inspect.history_detail",
        "common_keys": ["attempt", "type", "success", "output"],
        "writers": [
            "failure.py::Failure.to_gate_outcome (every failed gate; some sites add commands/steps, graded_by/deterministic_result, managed_service_outcome)",
            "attempt.py 8800 compile success (+execution_evidence)", "attempt.py 8839 test_selection (success False, target_test, recovered_by)",
            "attempt.py 8864 test after selection fallback (selection_fallback, +execution_evidence)",
            "attempt.py 8907 targeted_test self-corrected (self_corrected, self_correction_turns, self_correction_transcript)",
            "attempt.py 3945 run_verification failure (+commands, steps)",
            "attempt.py 5553 run_verification graded success (graded_by, commands, steps, deterministic_result, runtime_disposition, verifier_evidence, +execution_evidence)",
            "workflow.py 5036 regression_test deferred (deferred_to_future_owner, +execution_evidence)",
            "attempt.py 10210 goal_spec_compliance (status, reason_code, arbitrated_contradictions, planner_only_requirements)",
            "verification_coordinator.py::VerificationCoordinator._gate (success from the result, +execution_evidence)",
        ],
        "section": {"availability": section["availability"], "provenance": section["provenance"], "reason": section["reason"]},
        "gate_outcomes": section["data"],
    }
    text = json.dumps(doc, indent=1, ensure_ascii=False, sort_keys=True) + "\n"
    if "--check" in sys.argv:
        committed = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
        if committed != text:
            print(f"{OUT} differs from a fresh generation; run fixtures/serializer_gates.py", file=sys.stderr)
            return 1
        print(f"{OUT} is current ({len(doc['gate_outcomes'])} gate records)")
        return 0
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"wrote {OUT} ({len(doc['gate_outcomes'])} gate records through the adapter)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
