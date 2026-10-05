"""LR-R1-M1.8a: candidate.change, gate.result and obligations.snapshot
(design §3.3; tests T8 and the gate part of T6).

Through the real direct pipeline (only the runtime port scripted): the
candidate each attempt's gates verify is recorded with its base and
candidate raw digests and both byte versions, and applying the recorded
diff to the base reproduces the candidate exactly; every validator gate
invocation is recorded after it ran; every attempt closes with the ledger
snapshot. The terminal gates are recorded from the report the commit
decision reads.
"""
import os
import subprocess

import pytest
from _chaos_harness import (
    CALC,
    CALC_WITH_SUB,
    TEST_SUB,
    ChaosRuntime,
    RuntimeRegistration,
    benign_roles,
    chaos_config,
    chaos_engine,
    git_workspace,
    run_direct,
)

from kriya.core.attempt_evidence import reader, scope
from kriya.core.state_paths import ENV_STATE_DIR
from kriya.workflow.file_integrity import raw_digest


def _run(tmp_path, monkeypatch, developer, base=CALC):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(ENV_STATE_DIR, str(state))
    workspace = git_workspace(tmp_path, {"calc.py": base, "test_calc.py": TEST_SUB})
    runtime = ChaosRuntime(lambda role, request: developer(request) if role == "developer"
                           else benign_roles(role, request))
    with RuntimeRegistration(runtime):
        run_direct(chaos_engine(chaos_config()), "add sub to calc.py", workspace)
    [run_id] = reader.list_runs(str(state))
    run = reader.open_run(str(state), run_id)
    return workspace, run, list(run.records())


def _git_apply(tmp_path, relpath, before, diff):
    """Apply a recorded diff with git itself (not our own patch logic)."""
    root = tmp_path / "apply"
    root.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    target = root / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(before)
    patch = root / "change.diff"
    patch.write_bytes(diff)
    subprocess.run(["git", "apply", "--unidiff-zero", "change.diff"], cwd=root, check=True)
    return target.read_bytes()


def test_candidate_change_binds_digests_and_its_diff_reproduces_the_candidate(tmp_path, monkeypatch):
    workspace, run, records = _run(tmp_path, monkeypatch, lambda request: CALC_WITH_SUB)
    changes = [r for r in records if r["kind"] == "candidate.change"]
    assert changes, "the candidate verified by the gates must be recorded"
    change = changes[-1]
    payload = change["payload"]
    assert payload["path"] == "calc.py" and payload["decision"] == "STAGED"
    before = run.blob(change["blobs"]["before"])
    after = run.blob(change["blobs"]["after"])
    assert before == CALC.encode() and after == CALC_WITH_SUB.encode()
    assert payload["before_digest"] == raw_digest(before) and payload["after_digest"] == raw_digest(after)
    assert payload["created"] is False and payload["deleted"] is False and payload["text_diff"] is True
    assert payload["lines_added"] > 0
    assert _git_apply(tmp_path, "calc.py", before, run.blob(change["blobs"]["diff"])) == after
    # The candidate change precedes every gate of its attempt.
    gates = [r for r in records if r["kind"] == "gate.result" and r["attempt_number"] == change["attempt_number"]]
    assert gates and all(g["seq"] > change["seq"] for g in gates)
    # The workspace received exactly the recorded candidate.
    with open(os.path.join(str(workspace), "calc.py"), "rb") as handle:
        assert raw_digest(handle.read()) == payload["after_digest"]


def test_a_file_without_final_newline_still_diffs_exactly(tmp_path, monkeypatch):
    """A whole-file replacement keeps the existing file's final-newline
    convention (file_integrity.keep_final_newline_state); a base without one
    gives a candidate without one, and the recorded diff carries git's
    no-newline marker so it still applies exactly."""
    from _protocol_responses import sentinel

    unterminated = sentinel("calc.py", content=CALC_WITH_SUB.rstrip("\n"))
    _workspace, run, records = _run(tmp_path, monkeypatch, lambda request: unterminated, base=CALC.rstrip("\n"))
    change = [r for r in records if r["kind"] == "candidate.change"][-1]
    before, after = run.blob(change["blobs"]["before"]), run.blob(change["blobs"]["after"])
    assert not before.endswith(b"\n") and not after.endswith(b"\n")
    diff = run.blob(change["blobs"]["diff"])
    assert b"\\ No newline at end of file" in diff
    assert _git_apply(tmp_path, "calc.py", before, diff) == after


def test_every_validator_gate_is_recorded_after_it_ran_with_its_output(tmp_path, monkeypatch):
    _result, run, records = _run(tmp_path, monkeypatch, lambda request: CALC_WITH_SUB)
    gates = [r for r in records if r["kind"] == "gate.result" and r["payload"]["stage"] == "validator"]
    assert gates
    names = {g["payload"]["gate"] for g in gates}
    assert names, names
    for gate in gates:
        assert gate["payload"]["success"] in (True, False)
        assert gate["payload"]["error_type"] is None and gate["payload"]["duration_seconds"] >= 0
        if gate["payload"]["output_bytes"]:
            assert len(run.blob(gate["blobs"]["output"])) == gate["payload"]["output_bytes"]


def test_every_attempt_closes_with_an_obligations_snapshot(tmp_path, monkeypatch):
    _result, _run_store, records = _run(tmp_path, monkeypatch, lambda request: CALC_WITH_SUB)
    attempts = {r["attempt_number"] for r in records if r["kind"] == "attempt.closed"}
    snapshots = [r for r in records if r["kind"] == "obligations.snapshot"]
    assert attempts and {s["attempt_number"] for s in snapshots} == attempts
    for snapshot in snapshots:
        closed = next(r for r in records if r["kind"] == "attempt.closed"
                      and r["attempt_number"] == snapshot["attempt_number"])
        assert snapshot["seq"] < closed["seq"]
        assert isinstance(snapshot["payload"]["ledger_revision"], int)
        assert snapshot["payload"]["ledger_digest"]


# -- unit level ------------------------------------------------------------------------------

class _Context:
    def __init__(self, run_id):
        self.run_id = run_id


def _store(tmp_path, monkeypatch, run_id):
    from tests._strict_doubles import strict_config

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    monkeypatch.setenv(ENV_STATE_DIR, str(state))
    return state, strict_config


def test_a_raising_gate_is_recorded_and_the_exception_still_propagates(tmp_path, monkeypatch):
    from kriya.tools.validate import _verification_gate

    state, strict_config = _store(tmp_path, monkeypatch, "run-gate-raise")

    class _Validator:
        tree_binding = None
        _gate = None

        @_verification_gate("compile")
        def compile(self):
            raise RuntimeError("gate exploded")

    with scope.run_scope(_Context("run-gate-raise")):
        scope.ensure_store(strict_config())
        with pytest.raises(RuntimeError, match="gate exploded"):
            _Validator().compile()
        scope._RUN.get().writer.seal("test")
    [gate] = [r for r in reader.open_run(str(state), "run-gate-raise").records() if r["kind"] == "gate.result"]
    assert gate["payload"]["gate"] == "compile" and gate["payload"]["error_type"] == "RuntimeError"
    assert gate["payload"]["success"] is None


def test_oversized_gate_output_is_capped_with_the_full_digest(tmp_path, monkeypatch):
    from kriya.core.attempt_evidence import model
    from kriya.tools.validate import _verification_gate

    state, strict_config = _store(tmp_path, monkeypatch, "run-gate-cap")
    monkeypatch.setattr(scope, "GATE_OUTPUT_CAP_BYTES", 100)
    output = "x" * 250

    class _Validator:
        tree_binding = None
        _gate = None

        @_verification_gate("tests")
        def tests(self):
            return {"success": False, "output": output, "exit_code": 1}

    with scope.run_scope(_Context("run-gate-cap")):
        scope.ensure_store(strict_config())
        assert _Validator().tests()["output"] == output        # the gate's own result is untouched
        scope._RUN.get().writer.seal("test")
    run = reader.open_run(str(state), "run-gate-cap")
    [gate] = [r for r in run.records() if r["kind"] == "gate.result"]
    payload = gate["payload"]
    assert payload["truncated"] is True and payload["output_bytes"] == 250 and payload["exit_code"] == 1
    assert payload["full_digest"] == model.digest(output.encode())
    assert run.blob(gate["blobs"]["output"]) == b"x" * 100


def test_terminal_gates_are_recorded_from_the_report_the_service_returns(tmp_path, monkeypatch):
    """Through TerminalGateService.run itself: one gate.result per terminal
    gate, failing exactly where the report the commit decision reads fails."""
    from test_prd030_terminal_services import GATES, _request, _run, _validators, _violated_terminal_obligation

    from kriya.workflow.obligations import ObligationLedger

    state, strict_config = _store(tmp_path, monkeypatch, "run-terminal")
    ledger = ObligationLedger()
    _violated_terminal_obligation(ledger)
    with scope.run_scope(_Context("run-terminal")):
        scope.ensure_store(strict_config())
        report, _events = _run(_request(tmp_path, ledger=ledger), _validators())
        scope._RUN.get().writer.seal("test")
    assert report.terminal_obligation_gap and not report.commit_eligible
    run = reader.open_run(str(state), "run-terminal")
    recorded = [r for r in run.records() if r["kind"] == "gate.result"]
    assert [r["payload"]["gate"] for r in recorded] == GATES
    gates = {r["payload"]["gate"]: r for r in recorded}
    failed = gates["terminal_obligations"]
    assert failed["payload"]["success"] is False and failed["payload"]["commit_eligible"] is False
    assert run.blob(failed["blobs"]["output"]).decode() == report.terminal_obligation_gap
    assert all(g["payload"]["success"] for name, g in gates.items() if name != "terminal_obligations")
