"""PRD-008 S2/S3: resume fingerprints, the reused-artifact dependency
matrix, the single resume comparison path, and stage-precise invalidation."""

import os
import subprocess
from unittest.mock import AsyncMock, patch

import pytest

from kriya.config import AppConfig
from kriya.config.config import LLMConfig
from kriya.control.persistence import save_control_state
from kriya.control.retention import prune_run_state
from kriya.control.run_coordinator import begin_mutating_run
from kriya.control.run_record import RunRecord
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
    ARTIFACT_STAGE,
    CANDIDATE_HASH_KEY,
    CONFIG_FIELD_OWNERS,
    DERIVATION_CHAIN,
    EFFECTIVE_LEDGER_KEY,
    FINGERPRINT_NAMES,
    STAGE_ORDER,
    UNAVAILABLE,
    Fingerprint,
    FingerprintStatus,
    apply_resume_invalidation,
    authority_context_fingerprint,
    candidate_integrity_problem,
    candidate_snapshot_digest,
    compare_resume_fingerprints,
    fingerprint_block,
    generation_resume_fingerprints,
    invalidated_stages_for,
    ledger_fingerprint,
    restore_effective_ledger,
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
    candidate = {"stage": "candidate_gates_passed", "plan": "p", "design": "d", "gate_outcomes": []}
    assert {"candidate", "candidate_gate_outcomes"} <= reused_artifacts_for_checkpoint(candidate)
    # S3: gate outcomes a resume discarded (None) are not offered again.
    discarded = dict(candidate, gate_outcomes=None)
    assert "candidate" in reused_artifacts_for_checkpoint(discarded)
    assert "candidate_gate_outcomes" not in reused_artifacts_for_checkpoint(discarded)


def test_toolchain_is_not_applicable_to_a_plan_resume_but_blocks_candidate_reuse():
    fingerprints = dict(SAME, toolchain=Fingerprint.unavailable("PRD-011"))
    plan = compare_resume_fingerprints(
        fingerprint_block(fingerprints), fingerprints,
        reused_artifacts_for_checkpoint({"stage": "plan", "plan": "p"}),
    )
    assert _statuses(plan)["toolchain"] is FingerprintStatus.NOT_APPLICABLE
    candidate = compare_resume_fingerprints(
        fingerprint_block(fingerprints), fingerprints,
        reused_artifacts_for_checkpoint({"stage": "candidate_gates_passed", "plan": "p", "gate_outcomes": []}),
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
    # Per-role model overrides (agent_llms.<role>.llm) default to None, so
    # check the owned leaves against a config where every role sets one.
    config = AppConfig()
    for role in config.agent_llms.model_dump():
        getattr(config.agent_llms, role).llm = LLMConfig()
    dump = config.model_dump()
    for path in CONFIG_FIELD_OWNERS:
        node = dump
        for key in path:
            assert isinstance(node, dict) and key in node, f"stale ownership entry {path}"
            node = node[key]


def test_only_model_identity_leaves_leave_config():
    # Per-role and fallback knobs other than identity must stay in config.
    config = AppConfig()
    config.agent_llms.planner.llm = LLMConfig(model="m", max_tokens=17)
    owned = split_config_by_owner(config.model_dump())
    assert owned["model_runtime"]["agent_llms.planner.llm.model"] == "m"
    assert owned["config"]["agent_llms"]["planner"]["llm"]["max_tokens"] == 17
    assert "llm_chain" in owned["config"]


def test_config_split_partitions_every_leaf_exactly_once():
    config = AppConfig()
    config.agent_llms.reviewer.llm = LLMConfig()
    dump = config.model_dump()
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


def test_authority_context_sorts_regions_that_differ_only_by_a_missing_successor():
    # Sorting raw tuples raised TypeError (None vs str) for such a pair.
    common = {"relpath": "A.java", "region_type": RegionType.METHOD_BODY, "member_key": "m"}
    regions = [
        AuthorizedSemanticRegion(successor_key="n", **common),
        AuthorizedSemanticRegion(successor_key=None, **common),
    ]
    fingerprint = authority_context_fingerprint(
        authorized_semantic_regions=regions, allowed_write_relpaths=None,
        write_scope_mode=None, protected_source_file=None,
    )
    assert fingerprint == authority_context_fingerprint(
        authorized_semantic_regions=list(reversed(regions)), allowed_write_relpaths=None,
        write_scope_mode=None, protected_source_file=None,
    )


def test_input_ledger_is_fixed_at_entry_while_effective_grows(git_repo):
    ledger = ObligationLedger()
    entry = ledger_fingerprint(ledger)
    ledger.record(_grown_ledger().current("goal.requirement"))  # the run grows the SAME object
    saved = generation_resume_fingerprints(
        _config(), str(git_repo), goal=GOAL, obligation_ledger=ledger,
        effective_obligation_ledger=ledger, input_obligation_fingerprint=entry,
    )
    assert saved["input_obligation_ledger"] == entry
    assert saved["effective_obligation_ledger"] != entry


def test_kriya_runtime_ignores_dotfiles_and_bytecode(tmp_path, monkeypatch):
    import kriya
    from kriya.workflow import resume_fingerprints as module

    package = tmp_path / "kriya"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "a.py").write_text("x = 1\n")
    monkeypatch.setattr(kriya, "__file__", str(package / "__init__.py"))
    module.kriya_runtime_fingerprint.cache_clear()
    try:
        before = module.kriya_runtime_fingerprint()
        (package / ".DS_Store").write_bytes(b"finder")
        (package / "__pycache__").mkdir()
        (package / "__pycache__" / "a.cpython.pyc").write_bytes(b"\0")
        module.kriya_runtime_fingerprint.cache_clear()
        assert module.kriya_runtime_fingerprint() == before
        (package / "a.py").write_text("x = 2\n")
        module.kriya_runtime_fingerprint.cache_clear()
        assert module.kriya_runtime_fingerprint() != before
    finally:
        module.kriya_runtime_fingerprint.cache_clear()


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


# ---------------------------------------------------------------- S3: stage-precise invalidation

_FILES = {"math.py": "def add(a, b):\n    return a + b\n"}


def _checkpoint(stage):
    """One checkpoint of each kind, carrying what the workflow saves."""
    data = {
        "stage": stage, "plan": "P",
        "validation_baseline_targeted": {"status": "captured"},
        "validation_baseline_full_regression": None,
    }
    if stage in ("design", "candidate_gates_passed", "developer_success"):
        data.update(design="D", architect_files=["math.py"])
    if stage in ("candidate_gates_passed", "developer_success"):
        data.update(
            final_files=dict(_FILES), original_files={}, gate_outcomes=[{"type": "compile", "success": True}],
            model_hops=["m"], **{
                CANDIDATE_HASH_KEY: candidate_snapshot_digest(_FILES),
                EFFECTIVE_LEDGER_KEY: ObligationLedger().to_snapshot(),
            },
        )
    return data


@pytest.mark.parametrize("invalidated, reused, stage", [
    ((), {"knowledge_clearance", "plan", "design", "candidate", "candidate_gate_outcomes", "validation_baselines"},
     "candidate_gates_passed"),
    (("verification",), {"knowledge_clearance", "plan", "design", "candidate"}, "candidate_gates_passed"),
    (("candidate", "verification"), {"knowledge_clearance", "plan", "design"}, "design"),
    (("planning", "candidate", "verification"), {"knowledge_clearance"}, "context"),
    # model_protocol is off the derivation chain: nothing downstream drops.
    (("model_protocol",),
     {"knowledge_clearance", "plan", "design", "candidate", "candidate_gate_outcomes", "validation_baselines"},
     "candidate_gates_passed"),
])
def test_invalidation_keeps_the_longest_valid_prefix(invalidated, reused, stage):
    plan = apply_resume_invalidation("ckpt", _checkpoint("candidate_gates_passed"), invalidated)
    assert plan.reused == reused
    assert plan.state["stage"] == stage
    # The returned state offers exactly what the plan reuses, nothing more.
    assert reused_artifacts_for_checkpoint(plan.state) == plan.reused
    for artifact in plan.offered - plan.reused:
        assert artifact not in reused_artifacts_for_checkpoint(plan.state)


def test_context_invalidation_reuses_nothing():
    plan = apply_resume_invalidation("ckpt", _checkpoint("candidate_gates_passed"), STAGE_ORDER)
    assert not plan.resumes and plan.state is None and plan.reused == frozenset()
    assert plan.to_dict()["discarded"] == sorted(plan.offered)


def test_reuse_flags_come_from_the_plan_never_from_checkpoint_content():
    forged = dict(_checkpoint("candidate_gates_passed"), skip_candidate_gates=True, reuse_candidate=True)
    plan = apply_resume_invalidation("ckpt", forged, ("verification",))
    assert plan.reuse_candidate is True
    assert plan.skip_candidate_gates is False
    plan = apply_resume_invalidation("ckpt", forged, ("candidate", "verification"))
    assert plan.reuse_candidate is False and plan.skip_candidate_gates is False


@pytest.mark.parametrize("kind", ["plan", "design", "candidate_gates_passed"])
@pytest.mark.parametrize("changed", FINGERPRINT_NAMES)
def test_every_surviving_artifact_has_only_matching_dependencies(kind, changed):
    """Invariant: after one fingerprint changes, everything a resume still
    reuses depends only on MATCH/NOT_APPLICABLE fingerprints, and nothing
    survives whose upstream artifact on the derivation chain was dropped."""
    checkpoint = dict(_checkpoint(kind), **{RESUME_FINGERPRINTS_KEY: fingerprint_block(SAME)})
    current = dict(SAME, **{changed: Fingerprint("different", "b")})
    result = validate_resume_against_reality(checkpoint, "/unused", current_resume_fingerprints=current)
    plan = apply_resume_invalidation("ckpt", checkpoint, result.invalidated_stages)
    statuses = {item.name: item.status for item in result.fingerprint_comparisons}
    for artifact in plan.reused:
        for dependency in ARTIFACT_DEPENDENCIES[artifact]:
            assert statuses[dependency] in (FingerprintStatus.MATCH, FingerprintStatus.NOT_APPLICABLE), (
                artifact, dependency,
            )
    dropped_stages = {ARTIFACT_STAGE[artifact] for artifact in plan.offered - plan.reused}
    for artifact in plan.reused:
        stage = ARTIFACT_STAGE[artifact]
        if stage in DERIVATION_CHAIN:
            upstream = DERIVATION_CHAIN[:DERIVATION_CHAIN.index(stage)]
            assert not dropped_stages & set(upstream), (artifact, dropped_stages)


def test_model_runtime_change_never_discards_a_candidate():
    checkpoint = dict(_checkpoint("candidate_gates_passed"), **{RESUME_FINGERPRINTS_KEY: fingerprint_block(SAME)})
    current = dict(SAME, model_runtime=Fingerprint("other-model", "b"))
    result = validate_resume_against_reality(checkpoint, "/unused", current_resume_fingerprints=current)
    plan = apply_resume_invalidation("ckpt", checkpoint, result.invalidated_stages)
    assert plan.reuse_candidate and plan.skip_candidate_gates


# ---------------------------------------------------------------- S3: candidate integrity

def test_candidate_integrity_accepts_only_an_exact_complete_snapshot():
    good = _checkpoint("candidate_gates_passed")
    assert candidate_integrity_problem(good) is None
    assert "digest" in candidate_integrity_problem(dict(good, **{CANDIDATE_HASH_KEY: None}))
    tampered = dict(good, final_files={"math.py": "import os\n"})
    assert "do not match" in candidate_integrity_problem(tampered)
    assert "no well-formed" in candidate_integrity_problem(dict(good, final_files=None))


def test_a_tampered_candidate_is_dropped_but_its_plan_is_kept():
    checkpoint = dict(
        _checkpoint("candidate_gates_passed"),
        final_files={"math.py": "import os\n"},
        **{RESUME_FINGERPRINTS_KEY: fingerprint_block(SAME)},
    )
    result = validate_resume_against_reality(checkpoint, "/unused", current_resume_fingerprints=SAME)
    assert [item.fingerprint for item in result.decisions] == ["candidate_integrity"]
    assert RESUME_INVALIDATION_MATRIX["candidate_integrity"][1] == ("candidate", "verification")
    plan = apply_resume_invalidation("ckpt", checkpoint, result.invalidated_stages)
    assert plan.reused == {"knowledge_clearance", "plan", "design"}
    assert plan.state["final_files"] is None


# ---------------------------------------------------------------- S3: effective ledger snapshot

def _rich_ledger():
    ledger = ObligationLedger()
    common = {"kind": ObligationKind.PRESERVED_REFERENCE, "authority": ObligationAuthority.DETERMINISTIC,
              "description": "keep Foo.bar", "source": "test"}
    ledger.record(ObligationRecord(
        id="ref.foo", status=ObligationStatus.SATISFIED, revision=1,
        evidence={"paths": ("a.java", "b.java"), "nested": {"lines": [1, 2], "kind": ObligationKind.PRESERVED_REFERENCE}},
        repair_scope=("a.java",), terminal_required=True, **common,
    ))
    ledger.record(ObligationRecord(id="ref.foo", status=ObligationStatus.VIOLATED, revision="s2", **common))
    return ledger


def test_ledger_snapshot_round_trips_to_the_same_fingerprint():
    ledger = _rich_ledger()
    restored = ObligationLedger.from_snapshot(ledger.to_snapshot())
    assert restored.fingerprint() == ledger.fingerprint()
    assert ledger_fingerprint(restored) == ledger_fingerprint(ledger)
    assert restored.current("ref.foo").status is ObligationStatus.VIOLATED
    assert restored.history("ref.foo")[0].repair_scope == ("a.java",)
    assert len(restored.regressions) == 1  # rebuilt by replay


def test_restore_from_keeps_the_callers_ledger_object():
    shared = ObligationLedger()
    identity = id(shared)
    shared.restore_from(ObligationLedger.from_snapshot(_rich_ledger().to_snapshot()))
    assert id(shared) == identity
    assert shared.fingerprint() == _rich_ledger().fingerprint()


@pytest.mark.parametrize("snapshot", [
    {"schema_version": 99, "history": []},
    {"schema_version": 1},
    {"schema_version": 1, "history": [["x", [{"id": "x", "kind": "not-a-kind"}]]]},
    {"schema_version": 1, "history": [["other-id", [{
        "id": "x", "kind": "preserved_reference", "status": "satisfied", "authority": "deterministic",
        "description": "d", "source": "s",
    }]]]},
])
def test_malformed_ledger_snapshots_are_rejected(snapshot):
    with pytest.raises(ValueError):
        ObligationLedger.from_snapshot(snapshot)


def test_a_missing_ledger_snapshot_is_never_an_empty_ledger():
    ledger, problem = restore_effective_ledger({})
    assert ledger is None and "no effective obligation ledger" in problem


# ---------------------------------------------------------------- S3: durable decision records

def test_run_record_accepts_a_resume_decision_and_old_records_still_load():
    record = RunRecord.new("run-1", "ws", None, None).annotate(resume_decision={"reused": ["plan"]})
    assert RunRecord.from_dict(record.to_dict()).resume_decision == {"reused": ["plan"]}
    legacy = record.to_dict()
    legacy.pop("resume_decision")
    assert RunRecord.from_dict(legacy).resume_decision is None


def test_control_state_completion_scope_round_trips_and_defaults_to_unrecorded():
    state = ControlState(schema_version=CURRENT_SCHEMA_VERSION, run_id="r", subtask_completion_scope="workspace")
    assert ControlState.from_dict(state.to_dict()).subtask_completion_scope == "workspace"
    legacy = state.to_dict()
    legacy.pop("subtask_completion_scope")
    assert ControlState.from_dict(legacy).subtask_completion_scope is None
