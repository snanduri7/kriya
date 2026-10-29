"""ARTIFACT-REGISTRY-MALFORMED-READ-AS-EMPTY-001 (Wave 2 Step 6): a malformed artifact registry is not an absent one.

MEASURED at f076a44 (step6_repro.py in the Step-6 evidence directory; seven corruption forms): ``load_artifact_registry`` returned an EMPTY registry for a file that existed but could not be
read, so ARTIFACT_DRIFT detection answered ``[]`` where the healthy registry answered ``[drifted]``, the next save (milestones, the enforce post-commit persistence) replaced the malformed
bytes with a one-record registry and no copy, and a whole enforce run reported ``success`` over it. Now the loader raises ``ArtifactRegistryCorruptError`` (the twin of PRD-029's
``ContractRegistryCorruptError``), leaves the bytes exactly as they are, and every caller has an explicit policy:

* milestone runs and the controller's milestone path: ``needs_review`` / ARTIFACT_REGISTRY_CORRUPT, nothing runs, nothing is overwritten;
* enforce runs: the run raises the same typed error at its start (parity with a corrupt contract registry), before any Developer work;
* the terminal gate: the ``artifact_registry`` gate fails with the reason;
* the post-commit persistence of derived artifacts: the commit STANDS (it is never invalidated retroactively), the failure is reported in ``post_commit_persistence_errors`` with its reason code
  and the malformed bytes are untouched;
* the control state a run leaves behind hashes an unreadable registry to its reason code, so a resume invalidates instead of the finalization raising;
* ``doctor`` reports it as an error, not as ``[VALID]``.
"""
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _strict_doubles import strict_engine
from click.testing import CliRunner

sys.path.insert(0, os.path.dirname(__file__))
from test_e12_enforce_characterization import _PYPROJECT, _enforce, _enforce_engine, _plan_of, _unit  # noqa: E402
from test_milestones import mkv2  # noqa: E402

from kriya.cli import main  # noqa: E402
from kriya.context.workspace_identity import WorkspaceOwnershipError  # noqa: E402
from kriya.control.artifacts import (  # noqa: E402
    ARTIFACT_REGISTRY_CORRUPT,
    ArtifactRecord,
    ArtifactRegistry,
    ArtifactRegistryCorruptError,
)
from kriya.control.persistence import (  # noqa: E402
    artifact_registry_path,
    load_artifact_registry,
    save_artifact_registry,
)
from kriya.workflow.milestones import MilestoneRunState, run_milestones  # noqa: E402

C = "kriya.workflow.workflow_controller"


def _record(milestone_id):
    return ArtifactRecord(milestone_id=milestone_id, ecosystem="python", kind="package", module_path=None, coordinates={"name": f"pkg-{milestone_id}"}, packaging="wheel")


def _healthy(workspace):
    registry = ArtifactRegistry()
    registry.record(_record("m1"))
    registry.record(_record("m2"))
    save_artifact_registry(str(workspace), registry)
    return artifact_registry_path(str(workspace))


# Every malformed form; the callable receives the healthy file's text and returns the bytes to leave on disk (the ownership stamp is kept except where the form is not an object).
CORRUPTIONS = {
    "truncated_json": lambda good: good[: len(good) // 2].encode(),
    "wrong_shape_records": lambda good: json.dumps({**json.loads(good), "artifacts": {"m1": ["not-a-record"]}}).encode(),
    "missing_required_field": lambda good: json.dumps({**json.loads(good), "artifacts": {"m1": [{"ecosystem": "python"}]}}).encode(),
    "artifacts_not_a_mapping": lambda good: json.dumps({**json.loads(good), "artifacts": [1, 2]}).encode(),
    "not_an_object": lambda good: b"[1, 2, 3]",
    "non_utf8_bytes": lambda good: b"\xff\xfe\x00garbage",
    "ascii_garbage": lambda good: b"this is not json at all",
}


def _corrupt(workspace, form):
    path = _healthy(workspace)
    good = open(path, encoding="utf-8").read()
    bad = CORRUPTIONS[form](good)
    with open(path, "wb") as handle:
        handle.write(bad)
    return path, bad


def _files(path):
    return sorted(os.listdir(os.path.dirname(path)))


# ----------------------------------------------------------------------------------------------------------------------------------------- the loader

def test_a_registry_that_was_never_saved_is_a_valid_empty_registry(tmp_path):
    assert load_artifact_registry(str(tmp_path)).all_records() == ()


def test_a_healthy_registry_round_trips_and_its_drift_is_detected(tmp_path):
    _healthy(tmp_path)
    loaded = load_artifact_registry(str(tmp_path))
    assert len(loaded.all_records()) == 2 and [r.drifted for r in loaded.validate(str(tmp_path), "m1")] == [True]   # the workspace has no pyproject: the recorded artifact drifted


@pytest.mark.parametrize("form", sorted(CORRUPTIONS))
def test_a_malformed_registry_raises_the_typed_error_and_its_bytes_are_left_exactly_as_they_are(tmp_path, form):
    path, bad = _corrupt(tmp_path, form)
    before = _files(path)
    with pytest.raises(ArtifactRegistryCorruptError) as raised:
        load_artifact_registry(str(tmp_path))
    assert raised.value.reason_code == ARTIFACT_REGISTRY_CORRUPT == "ARTIFACT_REGISTRY_CORRUPT" and raised.value.path == path
    assert path in str(raised.value) and raised.value.detail
    with open(path, "rb") as handle:
        assert handle.read() == bad                                                       # byte-identical: no rewrite, no repair
    assert _files(path) == before                                                         # and no temp / backup file appeared


@pytest.mark.parametrize("form", sorted(CORRUPTIONS))
def test_a_malformed_registry_is_never_empty_so_drift_cannot_silently_switch_off(tmp_path, form):
    """The original symptom: drift answered [] (no registry) where a healthy one answered [drifted]. Now there is no registry object to ask."""
    _corrupt(tmp_path, form)
    registry = None
    with pytest.raises(ArtifactRegistryCorruptError):
        registry = load_artifact_registry(str(tmp_path))
    assert registry is None


def test_the_next_save_cannot_overwrite_the_evidence_because_there_is_nothing_loaded_to_extend(tmp_path):
    """The destructive sequence of the original defect (load -> record -> save) now stops at the load."""
    path, bad = _corrupt(tmp_path, "ascii_garbage")
    with pytest.raises(ArtifactRegistryCorruptError):
        registry = load_artifact_registry(str(tmp_path))
        registry.record(_record("m3"))
        save_artifact_registry(str(tmp_path), registry)
    with open(path, "rb") as handle:
        assert handle.read() == bad


@pytest.mark.skipif(os.geteuid() == 0, reason="a permission-denied read cannot be provoked as root")
def test_an_unreadable_registry_file_is_the_typed_error_too_never_empty(tmp_path):
    path = _healthy(tmp_path)
    before = open(path, "rb").read()
    os.chmod(path, 0)
    try:
        with pytest.raises(ArtifactRegistryCorruptError) as raised:
            load_artifact_registry(str(tmp_path))
        assert "unreadable" in raised.value.detail
    finally:
        os.chmod(path, 0o600)
    assert open(path, "rb").read() == before


def test_a_workspace_ownership_refusal_still_propagates_as_it_always_did(tmp_path):
    path = _healthy(tmp_path)
    data = json.load(open(path, encoding="utf-8"))
    data.pop("_workspace", None)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    with pytest.raises(WorkspaceOwnershipError):
        load_artifact_registry(str(tmp_path))


# ---------------------------------------------------------------------------------------------------------------- milestone runs and the controller

@pytest.mark.asyncio
@pytest.mark.parametrize("form", ["truncated_json", "wrong_shape_records", "non_utf8_bytes"])
async def test_a_milestone_run_fails_closed_on_a_corrupt_artifact_registry_without_overwriting_it(tmp_path, form):
    path, bad = _corrupt(tmp_path, form)
    state = MilestoneRunState(group_id="grp", original_goal="orig", milestones=[mkv2("M1", goal="g1")])
    engine = strict_engine()
    engine.run_generation_workflow = AsyncMock(side_effect=AssertionError("no milestone may run"))
    engine.run_verifier = MagicMock()
    result = await run_milestones(engine, state, str(tmp_path))
    assert result["status"] == "needs_review" and result["quality_gates_passed"] is False
    assert result["reason_codes"] == [ARTIFACT_REGISTRY_CORRUPT] and result["persistence_store"] == "artifact_registry"
    assert ARTIFACT_REGISTRY_CORRUPT in result["persistence_error"]
    with open(path, "rb") as handle:
        assert handle.read() == bad


@pytest.mark.asyncio
async def test_the_controller_milestone_path_fails_closed_on_a_corrupt_artifact_registry(tmp_path):
    from test_workflow_controller import _milestone_run_state, _workflow_engine

    from kriya.workflow.workflow_controller import WorkflowController

    path, bad = _corrupt(tmp_path, "ascii_garbage")
    with patch("kriya.workflow.milestones.run_milestones", AsyncMock(side_effect=AssertionError("must not run"))):
        result = await WorkflowController(_workflow_engine()).execute_milestones(_milestone_run_state(), str(tmp_path))
    legacy = result.legacy_result
    assert legacy["status"] == "needs_review" and legacy["quality_gates_passed"] is False
    assert legacy["reason_codes"] == [ARTIFACT_REGISTRY_CORRUPT] and legacy["persistence_store"] == "artifact_registry"
    with open(path, "rb") as handle:
        assert handle.read() == bad


def test_the_reconstruction_load_is_the_same_fail_closed_policy(tmp_path):
    from kriya.workflow.milestones import MilestonePersistenceError, _live_artifact_registry

    path, bad = _corrupt(tmp_path, "truncated_json")
    with pytest.raises(MilestonePersistenceError) as raised:
        _live_artifact_registry(str(tmp_path), "M1")
    assert raised.value.reason_code == ARTIFACT_REGISTRY_CORRUPT and raised.value.store == "artifact_registry" and raised.value.milestone_id == "M1"
    with open(path, "rb") as handle:
        assert handle.read() == bad


def test_a_registry_that_became_unreadable_during_a_run_hashes_to_its_reason_code_so_a_resume_invalidates(tmp_path):
    from kriya.control.checkpoint import compute_registry_hash
    from kriya.workflow.workflow_controller import _artifact_registry_hash

    healthy = _artifact_registry_hash(str(tmp_path))
    assert healthy == compute_registry_hash(ArtifactRegistry().to_dict())                  # absent -> the empty registry's hash, unchanged behaviour
    _corrupt(tmp_path, "ascii_garbage")
    corrupt = _artifact_registry_hash(str(tmp_path))
    assert corrupt == ARTIFACT_REGISTRY_CORRUPT and corrupt != healthy                      # no raise after the run's work; no readable registry can have this hash


# ---------------------------------------------------------------------------------------------------------------------------------- enforce runs

@pytest.mark.asyncio
async def test_an_enforce_run_refuses_a_corrupt_artifact_registry_at_its_start_like_a_corrupt_contract_registry(tmp_path):
    """MEASURED at f076a44: this exact run reported success over the corrupt file and overwrote it at the post-commit save."""
    (tmp_path / "pyproject.toml").write_text(_PYPROJECT)
    path, bad = _corrupt(tmp_path, "ascii_garbage")
    engine = _enforce_engine(tmp_path)
    engine.run_generation_workflow = AsyncMock(side_effect=AssertionError("no Developer work before the refusal"))
    with pytest.raises(ArtifactRegistryCorruptError):
        await _enforce(engine, tmp_path, _plan_of(_unit("s1")))
    with open(path, "rb") as handle:
        assert handle.read() == bad


@pytest.mark.asyncio
async def test_a_registry_corrupted_after_the_commit_never_invalidates_the_commit_and_is_never_overwritten(tmp_path):
    """The post-commit rule: the authoritative commit stands. The derived artifacts could not be persisted, the malformed bytes are untouched, the failure travels with its reason code."""
    from kriya.workflow import workflow_controller as wc

    (tmp_path / "pyproject.toml").write_text(_PYPROJECT)
    path = artifact_registry_path(str(tmp_path))
    real_commit = wc.commit_verified_candidate
    garbage = b"corrupted after the source commit"

    def commit_then_corrupt(report, request):
        outcome = real_commit(report, request)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(garbage)
        return outcome
    with patch(f"{C}.commit_verified_candidate", side_effect=commit_then_corrupt):
        result, _ = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan_of(_unit("s1")))
    legacy = result.legacy_result
    assert legacy["status"] == "success" and legacy["quality_gates_passed"] is True            # the verification result and the commit are not rewritten
    errors = legacy["post_commit_persistence_errors"]
    entry = next(e for e in errors if e["operation"] == "save_artifact_registry")
    assert entry["reason_code"] == ARTIFACT_REGISTRY_CORRUPT and "ArtifactRegistryCorruptError" in entry["error"]
    assert "derived_artifacts" not in legacy
    with open(path, "rb") as handle:
        assert handle.read() == garbage


@pytest.mark.asyncio
async def test_the_terminal_gate_fails_on_an_unreadable_artifact_registry(tmp_path):
    (tmp_path / "pyproject.toml").write_text(_PYPROJECT)
    boom = ArtifactRegistryCorruptError("/x/artifacts.json", "unreadable: boom")
    with patch("kriya.workflow.terminal_gate_service.load_artifact_registry", side_effect=boom):
        result, _ = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan_of(_unit("s1")))
    legacy = result.legacy_result
    assert legacy["quality_gates_passed"] is False and legacy["status"] != "success"
    assert ARTIFACT_REGISTRY_CORRUPT in json.dumps(legacy, default=str)


# --------------------------------------------------------------------------------------------------------------------------------------- doctor

def test_doctor_reports_a_corrupt_artifact_registry_as_an_error_not_valid(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KRIYA_STATE_DIR", str(tmp_path / "state"))
    path, bad = _corrupt(tmp_path, "ascii_garbage")
    result = CliRunner().invoke(main, ["doctor"])
    assert ARTIFACT_REGISTRY_CORRUPT in result.output
    assert "ArtifactRegistry:" not in result.output and "record(s) [VALID]" not in result.output       # the "[VALID]" line is not printed for it
    with open(path, "rb") as handle:
        assert handle.read() == bad
