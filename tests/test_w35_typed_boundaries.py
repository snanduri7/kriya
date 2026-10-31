"""W3-5 typed non-success run boundaries (signed 2026-10-30; handover/WAVE3_LEDGER_2026-10-30.md section 3).

Each section pins one registry row, reproducing the MEASURED failure mechanism (not a summary of it):

* ENFORCE-RUN-CORRUPT-REGISTRY-RAISES-INSTEAD-OF-NEEDS-REVIEW-001 - a corrupt CONTRACT or ARTIFACT registry in an enforce run.

The existing failed/non-success result contract is reused: no new status, no ``needs_review`` for parity with the milestone path, no
``recovery_command`` (its remedy is ``kriya runs recover``, wrong for a registry).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_e12_enforce_characterization import _PYPROJECT, _enforce, _enforce_engine, _plan_of, _unit  # noqa: E402

from kriya.control.artifacts import ARTIFACT_REGISTRY_CORRUPT, ArtifactRegistry  # noqa: E402
from kriya.control.contracts import CONTRACT_REGISTRY_CORRUPT  # noqa: E402
from kriya.control.persistence import (  # noqa: E402
    artifact_registry_path,
    contract_registry_path,
    save_artifact_registry,
)

# registry kind -> (path function, dedicated reason code, persistence_store). The contract registry is never saved by these tests: a corrupt file
# is simply written at its canonical path.
_KINDS = {
    "contract": (contract_registry_path, CONTRACT_REGISTRY_CORRUPT, "contract_registry"),
    "artifact": (artifact_registry_path, ARTIFACT_REGISTRY_CORRUPT, "artifact_registry"),
}
_FORMS = {"ascii_garbage": b"this is not json at all", "non_utf8_bytes": b"\xff\xfe\x00garbage", "not_an_object": b"[1, 2, 3]"}


def _write_corrupt(kind, workspace, form):
    path = _KINDS[kind][0](str(workspace))
    if kind == "artifact":
        save_artifact_registry(str(workspace), ArtifactRegistry())   # creates the directory and the ownership stamp the loader expects
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(_FORMS[form])
    return path, _FORMS[form]


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
@pytest.mark.parametrize("form", sorted(_FORMS))
@pytest.mark.parametrize("kind", sorted(_KINDS))
async def test_a_corrupt_registry_ends_an_enforce_run_as_the_existing_typed_failure(tmp_path, kind, form):
    (tmp_path / "pyproject.toml").write_text(_PYPROJECT)
    path, bad = _write_corrupt(kind, tmp_path, form)
    _path_fn, reason_code, store = _KINDS[kind]
    engine = _enforce_engine(tmp_path)
    result, calls = await _enforce(engine, tmp_path, _plan_of(_unit("s1")))
    legacy = result.legacy_result
    # the existing failed contract (the WorktreeSyncError shape), not needs_review, no new status
    assert legacy["status"] == "failure" and legacy["quality_gates_passed"] is False and legacy["files"] == []
    assert legacy["reason_codes"] == [reason_code] and legacy["failure_category"] == reason_code.lower()
    # recovery guidance rides the fields the milestone path already uses; recovery_command is NOT populated
    assert legacy["persistence_store"] == store and reason_code in legacy["persistence_error"] and os.path.basename(path) in legacy["persistence_error"]
    assert "recovery_command" not in legacy and legacy["run_id"]
    # nothing ran, nothing was overwritten
    assert calls == []   # the writer double records every Developer call
    with open(path, "rb") as handle:
        assert handle.read() == bad


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", sorted(_KINDS))
async def test_a_healthy_or_absent_registry_does_not_take_the_corruption_path(tmp_path, kind):
    """Negative control: the new handler is reached only by the typed corruption errors. An absent registry is a valid empty registry, and the enforce run proceeds to
    Developer work exactly as before (the writer double records its call)."""
    (tmp_path / "pyproject.toml").write_text(_PYPROJECT)
    engine = _enforce_engine(tmp_path)
    result, calls = await _enforce(engine, tmp_path, _plan_of(_unit("s1")))
    assert calls and "REGISTRY_CORRUPT" not in str(result.legacy_result.get("failure_type"))
    assert not any(code.endswith("_REGISTRY_CORRUPT") for code in result.legacy_result.get("reason_codes", []))


# ============================================================================================================================== STAGE-DEADLINE-RAW-ESCAPE-PLANNER-ARCHITECT-001
# MEASURED (W3-0, pre-fix): a REAL run deadline spent by a slow Planner or Architect call escaped run_generation_workflow as a raw InferenceDeadlineError
# (the CLI printed ``Workflow error: InferenceDeadlineError``), while the Developer loop and the final review already converted the same error. The technique
# below is the one that measured it: a runtime that sleeps for one role under a real 4 s generation budget.

import asyncio  # noqa: E402
import subprocess  # noqa: E402

from _chaos_harness import (  # noqa: E402
    CALC,
    CALC_WITH_SUB,
    TEST_SUB,
    ChaosRuntime,
    RuntimeRegistration,
    chaos_config,
    chaos_engine,
    git_workspace,
    role_of,
    run_direct,
)
from test_lr_r1_m1_t6_fixes import _always  # noqa: E402

from kriya.control.persistence import list_run_records  # noqa: E402
from kriya.core.state_paths import ENV_STATE_DIR  # noqa: E402

_FILES = {"calc.py": CALC, "test_calc.py": TEST_SUB}


# The budget must be spent INSIDE the slow role's own call (INFERENCE_DEADLINE_EXCEEDED), never before it (INFERENCE_DEADLINE_EXHAUSTED, llm.py:290/298). The first
# gate at c06596a measured the 4 s budget already spent before the Architect dispatch under -n 8 load (24 s test time), so the stricter pin failed on a wall-clock race.
# 15 s keeps a wide margin over the fast roles and stays under the 30 s sleep, so the slow role is still cut mid-call.
def _slow_role_run(tmp_path, monkeypatch, slow_role, budget_seconds=15):
    class Slow(ChaosRuntime):
        async def complete(self, client, request):
            role = role_of(request)
            self.seen_roles = getattr(self, "seen_roles", []) + [role]
            if role == slow_role:
                await asyncio.sleep(30)
            return await super().complete(client, request)

    cfg = chaos_config()
    cfg.autonomy.generation_time_budget_seconds = budget_seconds
    cfg.autonomy.generation_seconds_per_file_estimate = 0
    cfg.autonomy.generation_gate_reserve_seconds = 0
    cfg.paths.skills = str(tmp_path / "skills")
    cfg.paths.memory = str(tmp_path / "memory")
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(ENV_STATE_DIR, str(state))
    workspace = git_workspace(tmp_path, _FILES)
    runtime = Slow(_always(CALC_WITH_SUB))
    with RuntimeRegistration(runtime):
        result = run_direct(chaos_engine(cfg), "add sub to calc.py", workspace)     # before W3-5 this RAISED InferenceDeadlineError
    return result, runtime, workspace


@pytest.mark.parametrize("slow_role", ["planner", "architect"])
def test_a_deadline_spent_in_the_planner_or_architect_is_a_typed_failed_result(slow_role, tmp_path, monkeypatch):
    result, runtime, workspace = _slow_role_run(tmp_path, monkeypatch, slow_role)
    # the EXISTING failed contract, with the deadline's own typed reason code and the existing budget category - no new status
    assert result["status"] == "failure" and result["quality_gates_passed"] is False and result["files"] == []
    assert result["reason_codes"] == ["INFERENCE_DEADLINE_EXCEEDED"] and result["failure_category"] == "generation_budget_exhausted"
    assert result["error"].startswith("GENERATION TIME BUDGET EXHAUSTED: INFERENCE_DEADLINE_EXCEEDED")
    assert result["environment_failure"] == result["error"] and result["run_id"]       # the CLI's existing budget branch reads environment_failure
    # no invented retry, no candidate: exactly one call of the slow role, and nothing reached the Developer
    roles = runtime.seen_roles
    assert roles.count(slow_role) == 1 and "developer" not in roles
    if slow_role == "architect":
        assert roles.count("planner") == 1 and roles.index("planner") < roles.index("architect")     # the Planner finished: the stop is genuinely the Architect boundary
    # the run record settles FAILURE exactly as the raising path did, and the workspace is untouched (only Kriya's own state directory)
    assert [(r.lifecycle_state.value, r.terminal) for r in list_run_records(str(workspace))] == [("FAILURE", True)]
    status = subprocess.run(["git", "status", "--short"], cwd=workspace, capture_output=True, text=True, check=True).stdout.strip().splitlines()
    assert status == ["?? .kriya/"]


def test_a_run_without_a_deadline_does_not_take_the_design_stage_deadline_path(tmp_path, monkeypatch):
    """Negative control: the same run with a fast Planner and Architect reaches the Developer (no deadline stop, no budget category)."""
    result, runtime, _workspace = _slow_role_run(tmp_path, monkeypatch, "no-such-role", budget_seconds=600)     # a budget no loaded runner can spend (the 4 s one flaked under -n 8)
    assert "developer" in runtime.seen_roles
    assert result.get("failure_category") != "generation_budget_exhausted" and "INFERENCE_DEADLINE_EXCEEDED" not in result.get("reason_codes", [])


from unittest.mock import AsyncMock  # noqa: E402

from test_e14_pre_characterization import _enforce_engine as _planner_enforce_engine  # noqa: E402
from test_e14_pre_characterization import _planner_text, _trace_rows  # noqa: E402

from kriya.core.llm import InferenceDeadlineError  # noqa: E402
from kriya.workflow.workflow_controller import WorkflowController  # noqa: E402


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_deadline_spent_in_the_enforce_planner_call_is_the_same_typed_failed_result(tmp_path):
    """The enforce boundary of the same row (MEASURED in W3-5 before the fix: the Planner call stopped by the run deadline escaped
    ``WorkflowController.execute`` as a raw InferenceDeadlineError and the trace row was ``error``). One shared result builder, one Planner call, no generation."""
    we, cfg = _planner_enforce_engine(tmp_path, _planner_text("unique_ids"))
    we.planner.run = AsyncMock(side_effect=InferenceDeadlineError(
        "INFERENCE_DEADLINE_EXCEEDED", {"deadline_source": "run_generation_budget", "remaining_budget_at_dispatch_ms": 3000}))
    result = await WorkflowController(we).execute("Create a.py", str(tmp_path), migration_mode="enforce")
    legacy = result.legacy_result
    assert legacy["status"] == "failure" and legacy["quality_gates_passed"] is False and legacy["files"] == []
    assert legacy["reason_codes"] == ["INFERENCE_DEADLINE_EXCEEDED"] and legacy["failure_category"] == "generation_budget_exhausted"
    assert legacy["error"].startswith("GENERATION TIME BUDGET EXHAUSTED: INFERENCE_DEADLINE_EXCEEDED") and legacy["run_id"]
    assert we.planner.run.await_count == 1 and we.run_generation_workflow.await_count == 0
    assert [(r.lifecycle_state.value, r.terminal_status) for r in list_run_records(str(tmp_path))] == [("FAILURE", "FAILURE")]
    assert [row[1] for row in _trace_rows(cfg)] != ["error"]


# ============================================================================================================================== KERNEL-STAGE-STOP-NO-CATEGORY-001 (`kriya fix` KeyError)
# MEASURED (W3-0): `kriya fix` indexed ``res["quality_gates_passed"]`` while the design-stage stops (KnowledgeGuard, the three Planner rejections, the Architect
# rejection) return a result without that key (the stage-stop contract pinned by tests/golden/kernel and PATH_TABLE), so a typed refusal surfaced as
# ``Error executing fix workflow: KeyError`` (exit 1). The stop dicts below are the REAL producers' output (the kernel golden scenarios); the consumer is the REAL command.

from click.testing import CliRunner  # noqa: E402
from test_cli_smoke import _FAKE_GENERATE_RESULT, _mock_kernel, _mock_workflow_engine  # noqa: E402
from test_e14_pre_characterization import kg  # noqa: E402

from kriya.cli import main  # noqa: E402

_STAGE_STOPS = ["legacy_planner_output_incomplete", "legacy_planner_unauthorized_path", "legacy_planner_schema_invalid", "legacy_architect_file_list_rejected"]


def _fix_with_result(tmp_path, result):
    from unittest.mock import patch

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with patch("kriya.cli.WorkflowEngine", return_value=_mock_workflow_engine(result)), \
             patch("kriya.cli.Kernel", side_effect=_mock_kernel), \
             patch("kriya.cli.LLMClient", autospec=True):
            return runner.invoke(main, ["fix", "--error", "some compile error", "-y"])


def _real_stop(name, tmp_path):
    (tmp_path / "scenario").mkdir()
    outcome = kg.SCENARIOS[name](tmp_path / "scenario")
    legacy = outcome.result.legacy_result if hasattr(outcome.result, "legacy_result") else outcome.result
    assert "quality_gates_passed" not in legacy and legacy["status"] != "success"      # the producers' stage-stop contract (golden-pinned): the key is absent
    return legacy


@pytest.mark.parametrize("name", _STAGE_STOPS)
def test_kriya_fix_reports_a_design_stage_stop_as_a_typed_refusal_not_a_keyerror(name, tmp_path):
    stop = _real_stop(name, tmp_path)
    result = _fix_with_result(tmp_path, stop)
    assert "KeyError" not in result.output and "Error executing fix workflow" not in result.output
    assert result.exit_code == 1
    assert f"[STOPPED BEFORE GENERATION] {stop['status']}" in result.output and stop["reason"][:40] in result.output
    assert "Nothing was generated or applied" in result.output
    assert "[SUCCESS]" not in result.output and "still fail" not in result.output


def test_kriya_fix_reports_a_knowledge_gap_stop_as_blocked(tmp_path):
    stop = _real_stop("legacy_knowledge_gap", tmp_path)
    result = _fix_with_result(tmp_path, stop)
    assert "KeyError" not in result.output and result.exit_code == 3
    assert "[STOPPED BEFORE GENERATION] knowledge_gap" in result.output and "Nothing was generated or applied" in result.output
    assert "[SUCCESS]" not in result.output


@pytest.mark.parametrize("status,passed,tag", [("success", True, "[SUCCESS]"), ("failure", False, "[FAILURE]"), ("needs_review", False, "[FAILURE]")])
def test_kriya_fix_output_for_a_result_that_carries_quality_gates_passed_is_unchanged(tmp_path, status, passed, tag):
    """Negative controls: a result that carries ``quality_gates_passed`` - including every typed failure that ALSO has a ``status`` - takes the pre-existing
    success/failure branches, never the stage-stop one (so the guard on the key is load-bearing, not decorative)."""
    result = _fix_with_result(tmp_path, dict(_FAKE_GENERATE_RESULT, status=status, quality_gates_passed=passed, reason="would be printed by the stage-stop branch"))
    assert tag in result.output and "[STOPPED BEFORE GENERATION]" not in result.output


def test_kriya_fix_fails_closed_on_a_result_with_neither_a_status_nor_the_key(tmp_path):
    """A malformed result is a FAILURE report, not a KeyError and never a success."""
    malformed = {k: v for k, v in _FAKE_GENERATE_RESULT.items() if k not in ("status", "quality_gates_passed")}
    result = _fix_with_result(tmp_path, malformed)
    assert "KeyError" not in result.output and "[FAILURE]" in result.output and "[SUCCESS]" not in result.output
