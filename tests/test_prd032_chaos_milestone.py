"""PRD-032 (Wave 7): milestone-path chaos scenarios (deterministic).

Hostility enters at the one seam that owns each failure (never a mocked ``llm.complete``): the model is the harness's
role-scripted runtime port, the real milestone engine and controller run, and the invariants are the harness's
(typed outcome, exact tree change set, no false PASS, an auditable RunRecord).
"""
import asyncio
from pathlib import Path

from _chaos_harness import (
    ChaosRuntime,
    RuntimeRegistration,
    audit_run_records,
    benign_roles,
    chaos,
    chaos_config,
    chaos_engine,
    git_workspace,
)

from kriya.control.persistence import load_artifact_registry, scan_run_records
from kriya.workflow import milestones as milestones_module
from kriya.workflow.milestones import load_or_resume_milestone_run_state
from kriya.workflow.workflow_controller import WorkflowController

CALC = "def add(a, b):\n    return a + b\n"
WITH_MUL = CALC + "\n\ndef mul(a, b):\n    return a * b\n"
WITH_DIV = WITH_MUL + "\n\ndef div(a, b):\n    return a / b\n"
PYPROJECT = '[project]\nname = "myapp"\nversion = "0.1.0"\n'
GROUP = "grp-d11"
INTEGRATION = 3


def _plan():
    return {"group_id": GROUP, "original_goal": "Add mul and div to calc.py.", "milestones": [
        {"id": "M1", "goal": "Add mul(a, b) to calc.py.", "success_criterion": "mul works", "depends_on": []},
        {"id": "M2", "goal": "Add div(a, b) to calc.py.", "success_criterion": "div works", "depends_on": ["M1"]},
    ]}


def _responder(workspace: Path):
    """benign roles; the Developer extends calc.py one step beyond what the REAL workspace holds."""
    def respond(role, request):
        if role == "developer":
            return WITH_DIV if "def mul" in (workspace / "calc.py").read_text() else WITH_MUL
        return benign_roles(role, request)
    return respond


def _execute_milestones(workspace: Path):
    runtime = ChaosRuntime(_responder(workspace))
    with RuntimeRegistration(runtime):
        state = load_or_resume_milestone_run_state(str(workspace), _plan())
        result = asyncio.run(WorkflowController(chaos_engine(chaos_config())).execute_milestones(state, str(workspace)))
    return runtime, result.legacy_result, state


def _commit_cycles(workspace: Path):
    """[(RunRecord lifecycle, cycle)] for every commit cycle of every RunRecord, oldest first."""
    records = sorted(scan_run_records(str(workspace)).records, key=lambda r: r.created_at)
    return [(record.lifecycle_state.value, cycle) for record in records for cycle in record.commits]


def _committed_transactions(workspace: Path):
    commits = workspace / ".kriya" / "control" / "commits"
    return sorted(path.stem for path in commits.glob("*.json")) if commits.is_dir() else []


@chaos("D11")
def test_a_failure_between_the_source_commit_and_the_registry_settlement_is_explicitly_unsettled_then_recovers(
        chaos_case, tmp_path, monkeypatch):
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "pyproject.toml": PYPROJECT})
    real_save = milestones_module.save_artifact_registry
    calls = {"n": 0}

    def flaky_save(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("injected registry publication failure after the source commit")
        return real_save(*args, **kwargs)

    monkeypatch.setattr(milestones_module, "save_artifact_registry", flaky_save)
    chaos_case.arm()

    # --- the fault: the source commit succeeded, the registry publication failed -------------------------------------
    runtime, legacy, state = _execute_milestones(workspace)
    assert legacy["status"] == "artifact_registry_settlement_uncertain"          # explicit uncertainty, not a clean failure
    assert legacy["reason_codes"] == ["ARTIFACT_REGISTRY_SETTLEMENT_UNCERTAIN"]
    assert legacy["workspace_state"] == "COMMITTED_REGISTRY_UNSETTLED"
    assert legacy["quality_gates_passed"] is False                                # never a false PASS
    assert state.completed_milestone_ids == []                                    # the milestone is NOT complete
    assert runtime.roles.count("developer") == 1                                  # the dependent M2 did not execute
    assert (workspace / "calc.py").read_text() == WITH_MUL                        # workspace truth: M1's source IS committed
    assert (workspace / "pyproject.toml").read_text() == PYPROJECT
    assert load_artifact_registry(str(workspace)).all_records() == ()             # no stale or partial authoritative registry
    audit = audit_run_records(workspace, allow_committed=True)                    # no SUCCESS record; the commit cycle is settled
    assert audit.lifecycles == ("FAILURE",) and audit.commit_results == ("COMMITTED",)
    [(lifecycle, cycle)] = _commit_cycles(workspace)
    assert cycle["work_unit"]["milestone_id"] == "M1" and cycle["intent"] == "APPLY_VERIFIED_CANDIDATE"
    unsettled_transaction = cycle["transaction_id"]
    assert _committed_transactions(workspace) == [unsettled_transaction]          # RunRecord truth == commit evidence == workspace

    # --- recovery: resume reconstructs M1 from the durable commit evidence and settles it ------------------------------
    recovery_runtime, recovered, recovered_state = _execute_milestones(workspace)
    assert recovered["status"] == "success" and recovered_state.completed_milestone_ids == ["M1", "M2"]
    assert recovery_runtime.roles.count("developer") == 2                          # M2 and the integration unit only: M1 was NOT redone
    assert (workspace / "calc.py").read_text() == WITH_DIV
    proofs = recovered_state.completion_proofs
    assert proofs["M1"]["completion_origin"] != "run"                              # settled by reconstruction, not re-executed
    assert proofs["M1"]["transaction_ids"][-1] == unsettled_transaction            # bound to the ORIGINAL committed transaction
    registry = load_artifact_registry(str(workspace))
    assert sorted(record.milestone_id for record in registry.all_records()) == ["M1", "M2"]   # one record each: no duplicates
    [m1_record] = registry.resolve_for_milestone("M1")
    assert m1_record.resolved_at_commit == unsettled_transaction
    assert m1_record.coordinates == {"name": "myapp", "version": "0.1.0"}
    [m2_record] = registry.resolve_for_milestone("M2")
    assert m2_record.resolved_at_commit == proofs["M2"]["transaction_ids"][-1]

    # --- idempotency: settling the same committed transaction again changes nothing ---------------------------------
    assert milestones_module.settle_artifact_registry(str(workspace), "M1", unsettled_transaction, None, registry) is None
    again = load_artifact_registry(str(workspace))
    def rows(reg):
        return sorted((r.to_dict() for r in reg.all_records()), key=lambda row: row["milestone_id"])

    assert rows(again) == rows(registry)                                         # same records (order is not part of the state)
    assert len(again.resolve_for_milestone("M1")) == 1 and len(again.all_records()) == 2   # a retried settlement never duplicates

    # --- truth agreement after recovery: every commit evidence is claimed by exactly one COMMITTED RunRecord cycle --------
    final_audit = audit_run_records(workspace, allow_success=True, allow_committed=True)
    claimed = sorted(cycle["transaction_id"] for _lifecycle, cycle in _commit_cycles(workspace) if cycle["result"] == "COMMITTED")
    assert claimed == _committed_transactions(workspace)
    assert "SUCCESS" in final_audit.lifecycles and final_audit.lifecycles[0] == "FAILURE"   # the earlier failure stays as history
    chaos_case.assert_tree(allowed={"ws/calc.py"}, required={"ws/calc.py"})
    chaos_case.observe(
        "artifact_registry_settlement_uncertain_then_recovered", reason_code="ARTIFACT_REGISTRY_SETTLEMENT_UNCERTAIN",
        developer_requests_before_recovery=1, developer_requests_in_recovery=2, records_after_recovery=2,
        committed_transactions=len(claimed), **{"lifecycles_after_recovery": list(final_audit.lifecycles)},
    )
