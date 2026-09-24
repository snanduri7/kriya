"""PRD-008 S2: resume fingerprints, the reused-artifact dependency matrix,
and the single resume comparison path."""

import os
import subprocess
from unittest.mock import AsyncMock, patch

import pytest

from kriya.config import AppConfig
from kriya.control.persistence import save_control_state
from kriya.control.retention import prune_run_state
from kriya.control.run_coordinator import begin_mutating_run
from kriya.control.state import CURRENT_SCHEMA_VERSION, ControlState
from kriya.core import LLMClient
from kriya.core.kernel import Kernel
from kriya.workflow.checkpoint import (
    RESUME_INVALIDATION_MATRIX,
    ResumeStatus,
    load_checkpoint,
    save_checkpoint,
    validate_resume_against_reality,
)
from kriya.workflow.obligations import (
    ObligationAuthority,
    ObligationKind,
    ObligationLedger,
    ObligationRecord,
    ObligationStatus,
)
from kriya.workflow.resume_fingerprints import (
    ARTIFACT_DEPENDENCIES,
    CONFIG_FIELD_OWNERS,
    FINGERPRINT_NAMES,
    STAGE_ORDER,
    UNAVAILABLE,
    Fingerprint,
    FingerprintStatus,
    authority_context_fingerprint,
    compare_resume_fingerprints,
    fingerprint_block,
    generation_resume_fingerprints,
    invalidated_stages_for,
    ledger_fingerprint,
    reused_artifacts_for_checkpoint,
    skills_fingerprint,
    split_config_by_owner,
)
from kriya.workflow.resume_fingerprints import (
    CHECKPOINT_KEY as RESUME_FINGERPRINTS_KEY,
)
from kriya.workflow.semantic_region_authority import AuthorizedSemanticRegion, RegionType
from kriya.workflow.workflow import WorkflowEngine

GOAL = "Create math library"
SAME = {name: Fingerprint("v", "b") for name in FINGERPRINT_NAMES}


@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)
    return tmp_path


def _config():
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    return cfg


def _grown_ledger():
    ledger = ObligationLedger()
    ledger.record(ObligationRecord(
        id="goal.requirement", kind=ObligationKind.PLAN_STRUCTURAL_VALIDITY,
        status=ObligationStatus.SATISFIED, authority=ObligationAuthority.DETERMINISTIC,
        description="d", source="test", revision=0,
    ))
    return ledger


def _statuses(comparisons):
    return {item.name: item.status for item in comparisons}


# ---------------------------------------------------------------- statuses

def test_not_applicable_comes_only_from_the_matrix():
    # Only knowledge clearance is reused: its dependencies are compared,
    # every other fingerprint is NOT_APPLICABLE - even though the caller
    # supplied no current value for any of them.
    comparisons = compare_resume_fingerprints(fingerprint_block(SAME), {}, {"knowledge_clearance"})
    statuses = _statuses(comparisons)
    applicable = ARTIFACT_DEPENDENCIES["knowledge_clearance"]
    for name in FINGERPRINT_NAMES:
        if name in applicable:
            # A caller omitting an applicable value never makes it NOT_APPLICABLE.
            assert statuses[name] is FingerprintStatus.UNVERIFIED
        else:
            assert statuses[name] is FingerprintStatus.NOT_APPLICABLE


@pytest.mark.parametrize("side", ["stored", "current"])
def test_unavailable_on_either_side_is_unverified_never_a_match(side):
    unavailable = dict(SAME, workspace=Fingerprint.unavailable("no git"))
    stored, current = (unavailable, SAME) if side == "stored" else (SAME, unavailable)
    statuses = _statuses(compare_resume_fingerprints(
        fingerprint_block(stored), current, {"plan"},
    ))
    assert statuses["workspace"] is FingerprintStatus.UNVERIFIED


def test_unavailable_to_unavailable_is_still_unverified():
    both = dict(SAME, toolchain=Fingerprint.unavailable("PRD-011"))
    statuses = _statuses(compare_resume_fingerprints(
        fingerprint_block(both), both, {"candidate_gate_outcomes"},
    ))
    assert statuses["toolchain"] is FingerprintStatus.UNVERIFIED


def test_different_basis_is_unverified():
    current = dict(SAME, config=Fingerprint("v", "other-basis"))
    statuses = _statuses(compare_resume_fingerprints(fingerprint_block(SAME), current, {"plan"}))
    assert statuses["config"] is FingerprintStatus.UNVERIFIED


def test_match_and_changed():
    current = dict(SAME, goal=Fingerprint("w", "b"))
    statuses = _statuses(compare_resume_fingerprints(fingerprint_block(SAME), current, {"plan"}))
    assert statuses["goal"] is FingerprintStatus.CHANGED
    assert statuses["config"] is FingerprintStatus.MATCH


@pytest.mark.parametrize("block", [None, "junk", {"schema_version": 99, "fingerprints": {}}])
def test_missing_or_unknown_block_makes_every_applicable_fingerprint_unverified(block):
    comparisons = compare_resume_fingerprints(block, SAME, {"plan"})
    for item in comparisons:
        if item.name in ARTIFACT_DEPENDENCIES["plan"]:
            assert item.status is FingerprintStatus.UNVERIFIED


def test_unknown_artifact_is_rejected():
    with pytest.raises(ValueError):
        compare_resume_fingerprints(fingerprint_block(SAME), SAME, {"no_such_artifact"})


def test_fingerprint_serialization_round_trips_unavailable():
    unavailable = Fingerprint.unavailable("why")
    assert unavailable.to_dict() == {"value": UNAVAILABLE, "basis": "why"}
    assert Fingerprint.from_dict(unavailable.to_dict()) == unavailable
    assert Fingerprint.from_dict({"value": 3}) is None


# ---------------------------------------------------------------- matrix

def test_every_fingerprint_is_a_dependency_of_some_artifact_and_in_the_resume_matrix():
    for name in FINGERPRINT_NAMES:
        assert invalidated_stages_for(name), name
        assert RESUME_INVALIDATION_MATRIX[name][1] == invalidated_stages_for(name)


def test_model_runtime_invalidates_only_model_protocol():
    assert invalidated_stages_for("model_runtime") == ("model_protocol",)
    # No checkpoint reuses model-protocol state today.
    for stage in ("plan", "design", "candidate_gates_passed", "developer_success"):
        assert "model_protocol_state" not in reused_artifacts_for_checkpoint(
            {"stage": stage, "plan": "p", "design": "d"},
        )


def test_kriya_runtime_change_invalidates_every_stage():
    assert invalidated_stages_for("kriya_runtime") == STAGE_ORDER
    assert all("kriya_runtime" in deps for deps in ARTIFACT_DEPENDENCIES.values())


def test_reused_artifacts_follow_values_not_key_presence():
    # _save_stage_checkpoint always writes the baseline keys, usually None.
    plan_stage = {
        "stage": "plan", "plan": "p",
        "validation_baseline_targeted": None, "validation_baseline_full_regression": None,
    }
    assert reused_artifacts_for_checkpoint(plan_stage) == {"knowledge_clearance", "plan"}
    with_baseline = dict(plan_stage, validation_baseline_targeted={"passed": True})
    assert "validation_baselines" in reused_artifacts_for_checkpoint(with_baseline)
    candidate = {"stage": "candidate_gates_passed", "plan": "p", "design": "d"}
    assert {"candidate", "candidate_gate_outcomes"} <= reused_artifacts_for_checkpoint(candidate)


def test_toolchain_is_not_applicable_to_a_plan_resume_but_blocks_candidate_reuse():
    fingerprints = dict(SAME, toolchain=Fingerprint.unavailable("PRD-011"))
    plan = compare_resume_fingerprints(
        fingerprint_block(fingerprints), fingerprints,
        reused_artifacts_for_checkpoint({"stage": "plan", "plan": "p"}),
    )
    assert _statuses(plan)["toolchain"] is FingerprintStatus.NOT_APPLICABLE
    candidate = compare_resume_fingerprints(
        fingerprint_block(fingerprints), fingerprints,
        reused_artifacts_for_checkpoint({"stage": "candidate_gates_passed", "plan": "p"}),
    )
    assert _statuses(candidate)["toolchain"] is FingerprintStatus.UNVERIFIED


# ---------------------------------------------------------------- config ownership

def _leaves(value, prefix=()):
    if isinstance(value, dict) and value:
        for key, child in value.items():
            yield from _leaves(child, prefix + (key,))
    else:
        yield prefix


def test_every_owned_config_path_exists():
    dump = AppConfig().model_dump()
    for path in CONFIG_FIELD_OWNERS:
        node = dump
        for key in path:
            assert isinstance(node, dict) and key in node, f"stale ownership entry {path}"
            node = node[key]


def test_config_split_partitions_every_leaf_exactly_once():
    dump = AppConfig().model_dump()
    owned = split_config_by_owner(dump)
    remainder_leaves = set(_leaves(owned["config"]))
    owned_paths = set(CONFIG_FIELD_OWNERS)
    for leaf in _leaves(dump):
        in_owned = any(leaf[:len(path)] == path for path in owned_paths)
        assert in_owned != (leaf in remainder_leaves), leaf


def test_an_unlisted_config_field_stays_in_config():
    dump = AppConfig().model_dump()
    dump["autonomy"]["some_future_field"] = 1
    assert split_config_by_owner(dump)["config"]["autonomy"]["some_future_field"] == 1


def test_model_identity_change_leaves_config_fingerprint_alone_but_temperature_does_not(git_repo):
    base = generation_resume_fingerprints(_config(), str(git_repo), goal=GOAL)
    model = _config()
    model.llm.model = "another-model"
    assert generation_resume_fingerprints(model, str(git_repo), goal=GOAL)["config"] == base["config"]
    temperature = _config()
    temperature.llm.temperature = 0.01
    assert generation_resume_fingerprints(temperature, str(git_repo), goal=GOAL)["config"] != base["config"]


def test_containment_and_verification_changes_are_their_own_fingerprints(git_repo):
    base = generation_resume_fingerprints(_config(), str(git_repo), goal=GOAL)
    contained = _config()
    contained.autonomy.containment_backend = "oci"
    changed = generation_resume_fingerprints(contained, str(git_repo), goal=GOAL)
    assert changed["containment"] != base["containment"] and changed["config"] == base["config"]
    verified = _config()
    verified.autonomy.run_verification_enabled = True
    changed = generation_resume_fingerprints(verified, str(git_repo), goal=GOAL)
    assert changed["verification_policy"] != base["verification_policy"]
    assert changed["config"] == base["config"]


def test_model_runtime_and_toolchain_are_declared_unavailable(git_repo):
    fingerprints = generation_resume_fingerprints(_config(), str(git_repo), goal=GOAL)
    assert not fingerprints["model_runtime"].available and "PRD-013" in fingerprints["model_runtime"].basis
    assert not fingerprints["toolchain"].available and "PRD-011" in fingerprints["toolchain"].basis
    assert set(fingerprints) == set(FINGERPRINT_NAMES)


# ---------------------------------------------------------------- individual fingerprints

def test_skills_fingerprint_tracks_rule_content_and_ignores_staged_rules(tmp_path):
    skill = tmp_path / "skills" / "java"
    skill.mkdir(parents=True)
    (skill / "rules.txt").write_text("rule one\n")
    before = skills_fingerprint([str(tmp_path / "skills")])
    (skill / "staged_rules.txt").write_text("proposed\n")
    (skill / "__pycache__").mkdir()
    (skill / "__pycache__" / "x.pyc").write_bytes(b"\0")
    assert skills_fingerprint([str(tmp_path / "skills")]) == before
    (skill / "rules.txt").write_text("rule two\n")
    assert skills_fingerprint([str(tmp_path / "skills")]) != before
    assert not skills_fingerprint(None).available


def test_authority_context_is_order_independent_and_ignores_audit_source():
    a = AuthorizedSemanticRegion(relpath="A.java", region_type=RegionType.METHOD_BODY, member_key="m", source="p1")
    b = AuthorizedSemanticRegion(relpath="B.java", region_type=RegionType.METHOD_BODY, member_key="n")
    relabelled = AuthorizedSemanticRegion(relpath="A.java", region_type=RegionType.METHOD_BODY, member_key="m", source="p2")
    common = {"allowed_write_relpaths": ["x", "y"], "write_scope_mode": None, "protected_source_file": None}
    one = authority_context_fingerprint(authorized_semantic_regions=[a, b], **common)
    assert one == authority_context_fingerprint(authorized_semantic_regions=[b, relabelled], **common)
    assert one != authority_context_fingerprint(authorized_semantic_regions=[a], **common)
    assert one != authority_context_fingerprint(
        authorized_semantic_regions=[a, b], **dict(common, allowed_write_relpaths=["x"]),
    )


def test_no_ledger_fingerprints_like_an_empty_ledger_and_growth_changes_it():
    ledger = ObligationLedger()
    assert ledger_fingerprint(None) == ledger_fingerprint(ledger)
    grown = ledger_fingerprint(_grown_ledger())
    assert grown.available and grown != ledger_fingerprint(ledger)


def test_effective_ledger_grown_by_the_prior_run_blocks_candidate_reuse(git_repo):
    saved = generation_resume_fingerprints(
        _config(), str(git_repo), goal=GOAL, effective_obligation_ledger=_grown_ledger(),
    )
    resumed = generation_resume_fingerprints(_config(), str(git_repo), goal=GOAL)
    statuses = _statuses(compare_resume_fingerprints(
        fingerprint_block(saved), resumed, {"candidate"},
    ))
    assert statuses["effective_obligation_ledger"] is FingerprintStatus.CHANGED
    assert statuses["input_obligation_ledger"] is FingerprintStatus.MATCH


# ---------------------------------------------------------------- validator

def test_missing_referenced_run_record_invalidates_everything():
    result = validate_resume_against_reality(
        {RESUME_FINGERPRINTS_KEY: fingerprint_block(SAME)}, "/unused",
        current_resume_fingerprints=SAME, reused_artifacts=set(ARTIFACT_DEPENDENCIES),
        run_record_missing="gone-run",
    )
    assert result.status is ResumeStatus.NEEDS_REVIEW
    [decision] = result.decisions
    assert decision.fingerprint == "run_record_provenance"
    assert result.invalidated_stages == STAGE_ORDER


# ---------------------------------------------------------------- workflow integration

def _seed(tmp_path, cfg, run_id, stage, fingerprint_inputs=None, goal=GOAL, **extra):
    save_checkpoint(str(tmp_path), run_id, {
        "stage": stage,
        RESUME_FINGERPRINTS_KEY: fingerprint_block(generation_resume_fingerprints(
            cfg, str(tmp_path), goal=goal, **(fingerprint_inputs or {}),
        )),
        **extra,
    })


def _fresh_run_llm(cfg):
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write math.py",
        '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]',
        "Review: Approved",
    ])
    return llm


@pytest.mark.asyncio
async def test_workflow_saves_every_fingerprint_with_each_checkpoint(git_repo):
    cfg = _config()
    saved = []
    real_save = save_checkpoint

    def spy(workspace_path, run_id, data):
        saved.append(dict(data))
        return real_save(workspace_path, run_id, data)

    with patch("kriya.workflow.workflow.save_checkpoint", side_effect=spy):
        result = await WorkflowEngine(Kernel(config=cfg), _fresh_run_llm(cfg)).run_generation_workflow(
            goal=GOAL, workspace_path=str(git_repo),
        )
    assert result["quality_gates_passed"] is True
    assert {entry["stage"] for entry in saved} >= {"plan", "design", "candidate_gates_passed"}
    for entry in saved:
        block = entry[RESUME_FINGERPRINTS_KEY]
        assert set(block["fingerprints"]) == set(FINGERPRINT_NAMES)
        assert block["fingerprints"]["toolchain"]["value"] == UNAVAILABLE


@pytest.mark.asyncio
async def test_workflow_resumes_a_plan_checkpoint_when_every_applicable_fingerprint_matches(git_repo):
    cfg = _config()
    _seed(git_repo, cfg, "ckpt-plan", "plan", plan="Step 1 (from checkpoint)")
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Design: Write math.py",
        '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]',
        "Review: Approved",
    ])
    result = await WorkflowEngine(Kernel(config=cfg), llm).run_generation_workflow(
        goal=GOAL, workspace_path=str(git_repo), resume=True,
    )
    assert result["plan"] == "Step 1 (from checkpoint)"
    assert llm.complete.await_count == 3


@pytest.mark.asyncio
async def test_workflow_does_not_resume_a_legacy_checkpoint_without_fingerprints(git_repo):
    cfg = _config()
    save_checkpoint(str(git_repo), "legacy", {"stage": "plan", "plan": "Stale plan"})
    llm = _fresh_run_llm(cfg)
    result = await WorkflowEngine(Kernel(config=cfg), llm).run_generation_workflow(
        goal=GOAL, workspace_path=str(git_repo), resume=True,
    )
    assert result["plan"] == "Step 1: Write code"
    assert llm.complete.await_count == 4


@pytest.mark.asyncio
async def test_workflow_does_not_resume_after_a_skill_changed(git_repo, tmp_path_factory):
    cfg = _config()
    skills = tmp_path_factory.mktemp("skills")
    (skills / "demo").mkdir()
    (skills / "demo" / "rules.txt").write_text("before\n")
    cfg.paths.skills = str(skills)
    cfg.skills.load_global = False
    cfg.skills.load_cwd = False
    _seed(git_repo, cfg, "ckpt-plan", "plan", plan="Stale plan")
    (skills / "demo" / "rules.txt").write_text("after\n")
    llm = _fresh_run_llm(cfg)
    result = await WorkflowEngine(Kernel(config=cfg), llm).run_generation_workflow(
        goal=GOAL, workspace_path=str(git_repo), resume=True,
    )
    assert result["plan"] == "Step 1: Write code"
    assert llm.complete.await_count == 4


@pytest.mark.asyncio
async def test_workflow_does_not_resume_under_a_different_semantic_region_boundary(git_repo):
    # Before PRD-008 only a DROPPED boundary blocked resume; a changed one
    # resumed silently. authority_context catches both.
    cfg = _config()
    saved_with = [AuthorizedSemanticRegion(relpath="A.java", region_type=RegionType.METHOD_BODY, member_key="m")]
    resumed_with = [AuthorizedSemanticRegion(relpath="B.java", region_type=RegionType.METHOD_BODY, member_key="m")]
    _seed(git_repo, cfg, "ckpt-plan", "plan", plan="Stale plan",
          fingerprint_inputs={"authorized_semantic_regions": saved_with})
    llm = _fresh_run_llm(cfg)
    result = await WorkflowEngine(Kernel(config=cfg), llm).run_generation_workflow(
        goal=GOAL, workspace_path=str(git_repo), resume=True,
        authorized_semantic_regions=resumed_with,
    )
    assert result["plan"] == "Step 1: Write code"
    assert llm.complete.await_count == 4


@pytest.mark.asyncio
async def test_workflow_does_not_resume_when_the_referenced_run_record_is_gone(git_repo):
    # Before PRD-008 a missing record skipped the commit-state check
    # entirely (fail open).
    cfg = _config()
    _seed(git_repo, cfg, "ckpt-plan", "plan", plan="Stale plan",
          _run_record={"classification": "derived", "run_id": "deleted-run", "revision": 3})
    assert load_checkpoint(str(git_repo), "ckpt-plan")["_run_record"]["run_id"] == "deleted-run"
    llm = _fresh_run_llm(cfg)
    with patch("kriya.workflow.workflow.logger") as workflow_logger:
        result = await WorkflowEngine(Kernel(config=cfg), llm).run_generation_workflow(
            goal=GOAL, workspace_path=str(git_repo), resume=True,
        )
    assert result["plan"] == "Step 1: Write code"
    assert llm.complete.await_count == 4
    warnings = " ".join(str(call.args[0]) for call in workflow_logger.warning.call_args_list)
    assert "run_record_provenance" in warnings and "deleted-run" in warnings


# ---------------------------------------------------------------- retention

def test_retention_protects_the_run_the_control_state_references(tmp_path):
    with begin_mutating_run(str(tmp_path), run_id="state-owner"):
        save_control_state(str(tmp_path), ControlState(
            schema_version=CURRENT_SCHEMA_VERSION, run_id="state-owner",
        ))
    for index in range(3):
        with begin_mutating_run(str(tmp_path), run_id=f"later-{index}"):
            pass
    report = prune_run_state(str(tmp_path), keep_terminal_runs=0)
    assert "state-owner" in report.protected_run_ids
    assert "state-owner" not in report.pruned_run_ids
    assert os.path.exists(os.path.join(str(tmp_path), ".kriya", "control", "runs", "state-owner.json"))
