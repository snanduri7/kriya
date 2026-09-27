"""PRD-032 defect (found by the chaos harness, C08/D01): on the direct and
milestone path a verified candidate whose terminal commit did not commit was
re-raised into the generic attempt-failure path. The Developer was asked
again (a retry that could never succeed: the base is stale, the commit
evidence is settled, or the guard refused), and the run ended with a wrong
category (quality_gates_exhausted / no_progress).

Fixed: a non-committed terminal commit is a deterministic stop,
``failure_category`` ``workspace_commit_failed``, with the enforce terminal's
``workspace_commit_failure`` payload. These tests run the real pipeline with
a scripted model (tests/_chaos_harness.py).
"""
from pathlib import Path

from _chaos_harness import (
    CALC,
    CALC_WITH_SUB,
    TEST_SUB,
    ChaosRuntime,
    RuntimeRegistration,
    audit_run_records,
    benign_roles,
    chaos_config,
    chaos_engine,
    git_workspace,
    inject_after_static_analysis_gate,
    run_direct,
    static_analysis_config,
)
from _fake_static_analysis import FakeRegistration

from kriya.cli import _print_workspace_commit_failure
from kriya.static_analysis import model as sa_model

GOAL = "add sub to calc.py"
USER_EDIT = CALC + "# edited by the user while Kriya was verifying\n"


def _runtime(developer=CALC_WITH_SUB):
    return ChaosRuntime(lambda role, request: developer if role == "developer" else benign_roles(role, request))


def _commit_stop_evidence(result, runtime):
    assert result["quality_gates_passed"] is False
    assert result["failure_category"] == "workspace_commit_failed"
    assert result["environment_failure"].startswith("WORKSPACE_COMMIT_NOT_COMPLETED: ")
    # One Developer request: the stop is deterministic, never a retry.
    assert runtime.count("developer") == 1
    assert [entry["failure_type"] for entry in result["failure_report"]][-1] == "workspace_commit"
    return result["workspace_commit_failure"]


def test_a_concurrent_edit_before_the_commit_stops_with_the_conflict(tmp_path, monkeypatch):
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB})
    inject_after_static_analysis_gate(monkeypatch, lambda result: Path(workspace, "calc.py").write_text(USER_EDIT))
    runtime = _runtime()
    with RuntimeRegistration(runtime):
        result = run_direct(chaos_engine(chaos_config()), GOAL, workspace)
    failure = _commit_stop_evidence(result, runtime)
    assert failure["reason_code"] == "WORKSPACE_REVISION_CONFLICT"
    assert failure["workspace_state"] == "UNCHANGED" and failure["commit_transaction_id"]
    assert Path(workspace, "calc.py").read_text() == USER_EDIT
    audit = audit_run_records(workspace)
    assert audit.lifecycles == ("FAILURE",) and set(audit.commit_results) <= {"NOT_COMMITTED", "ROLLED_BACK"}


def test_refused_static_analysis_evidence_stops_without_a_retry(tmp_path, monkeypatch):
    workspace = git_workspace(tmp_path, {"calc.py": CALC})
    runtime = _runtime()
    with FakeRegistration() as fake:
        def change_rule_pack(result):
            assert result.permits_commit
            # The commit guard probes a fresh provider instance (built from
            # the registration's knobs), as production re-probes the scanner.
            fake.knobs.rule_pack_digest = "pack-digest-2"

        inject_after_static_analysis_gate(monkeypatch, change_rule_pack)
        with RuntimeRegistration(runtime):
            result = run_direct(chaos_engine(static_analysis_config()), GOAL, workspace)
    failure = _commit_stop_evidence(result, runtime)
    assert failure["reason_code"] == sa_model.STATIC_ANALYSIS_EVIDENCE_STALE
    assert Path(workspace, "calc.py").read_text() == CALC


def test_the_payload_is_absent_on_success_and_on_an_ordinary_gate_failure(tmp_path):
    succeeded = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB}, name="ok")
    with RuntimeRegistration(_runtime()):
        ok = run_direct(chaos_engine(chaos_config()), GOAL, succeeded)
    assert ok["quality_gates_passed"] is True and ok["workspace_commit_failure"] is None
    failing = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB}, name="bad")
    with RuntimeRegistration(_runtime("[]")):
        bad = run_direct(chaos_engine(chaos_config()), GOAL, failing)
    assert bad["quality_gates_passed"] is False and bad["failure_category"] != "workspace_commit_failed"
    assert bad["workspace_commit_failure"] is None


def test_the_cli_names_the_stop_and_the_recovery_step(capsys):
    _print_workspace_commit_failure({"failure_category": "workspace_commit_failed",
                                     "environment_failure": "WORKSPACE_COMMIT_NOT_COMPLETED: X",
                                     "workspace_commit_failure": {"workspace_state": "UNCERTAIN"}})
    uncertain = capsys.readouterr().out
    _print_workspace_commit_failure({"failure_category": "workspace_commit_failed",
                                     "environment_failure": "WORKSPACE_COMMIT_NOT_COMPLETED: Y",
                                     "workspace_commit_failure": {"workspace_state": "UNCHANGED"}})
    unchanged = capsys.readouterr().out
    _print_workspace_commit_failure({"failure_category": "quality_gates_exhausted"})
    assert "[WORKSPACE COMMIT NOT COMPLETED]" in uncertain and "kriya runs recover" in uncertain
    assert "[WORKSPACE COMMIT NOT COMPLETED]" in unchanged and "workspace is unchanged" in unchanged
    assert capsys.readouterr().out == ""
