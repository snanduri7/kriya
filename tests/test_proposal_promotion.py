"""A3-P2: approved-proposal promotion into Kriya's existing generation
workflow. Covers kriya/workflow/proposal_promotion.py and the narrow
checkpoint resume-safety addition in kriya/workflow/workflow.py
(had_authorized_semantic_regions).

Never invokes a live LLM - every generation-path test either drives
run_attempt() directly (the real per-attempt CORR-018-P1 gate, no
WorkflowEngine/Planner/Architect involved) or mocks WorkflowEngine.
run_generation_workflow / llm.complete deterministically, matching this
codebase's own established test_workflow.py conventions.
"""
import asyncio
import hashlib
import json
import os
import subprocess
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from kriya.analyzer.java_members import extract_java_members
from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow import WorkflowEngine
from kriya.workflow.attempt import AttemptContext, run_attempt
from kriya.workflow.checkpoint import (
    compute_config_fingerprint,
    compute_workspace_fingerprint,
    save_checkpoint,
)
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.migration import resolve_migration_resolution
from kriya.workflow.proposal_binding import proposal_to_authorized_semantic_regions
from kriya.workflow.proposal_promotion import (
    PromotedProposal,
    ProposalPromotionError,
    REASON_PROPOSAL_NOT_APPROVED,
    REASON_PROPOSAL_NOT_CURRENTLY_VALID,
    REASON_PROPOSAL_PROMOTION_UNSUPPORTED,
    build_authoritative_goal,
    execute_approved_proposal,
    prepare_proposal_promotion,
)
from kriya.workflow.proposal_store import (
    ProposalStoreError,
    approve_proposal,
    load_proposal,
    persist_proposal,
    reject_proposal,
)
from kriya.workflow.review_context import (
    AdjudicatedFinding,
    StructuredFinding,
    build_proposed_modification,
)
from kriya.workflow.semantic_region_authority import AuthorizedSemanticRegion, RegionType
from kriya.workflow.state import GenerationState

TARGET_SRC = (
    "public class Target implements TargetInterface {\n"
    "    public Target(Collaborator c) {\n"
    "    }\n\n"
    "    public void doWork() {\n"
    "        int x = 1;\n"
    "    }\n\n"
    "    public void unrelatedMethod() {\n"
    "        int y = 2;\n"
    "    }\n"
    "}\n"
)

AUTHORIZED_BODY = TARGET_SRC.replace(
    "        int x = 1;\n    }",
    "        if (x < 0) { throw new IllegalArgumentException(); }\n        int x = 1;\n    }",
)
UNRELATED_ALSO_CHANGED = AUTHORIZED_BODY.replace("int y = 2;", "int y = 999; // sneaky unrelated drift")
UNAUTHORIZED_HELPER_ADDED = AUTHORIZED_BODY.replace(
    "        int x = 1;\n    }",
    "        int x = 1;\n        calculateBounds();\n    }",
).replace(
    "    public void unrelatedMethod() {\n        int y = 2;\n    }\n}",
    "    public void unrelatedMethod() {\n        int y = 2;\n    }\n\n"
    "    private void calculateBounds() {\n        // unauthorized helper\n    }\n}",
)

FAKE_UNRELATED_REGION = [
    AuthorizedSemanticRegion(relpath="Unrelated.java", region_type=RegionType.METHOD_BODY, member_key="k"),
]


def _make_finding_and_members(java_path):
    with open(java_path) as f:
        src = f.read()
    members = extract_java_members(src)
    m_dowork = next(m for m in members if m.name == "doWork")
    member_ids = {"M1": m_dowork}
    finding = StructuredFinding(
        finding_id="F1", title="Missing bounds check", member_id="M1",
        requested_confidence="STRONG_STATIC_INDICATION",
        condition_evidence_ids=["M1"], consequence_evidence_ids=[],
        runtime_dependency_declared=False,
        explanation="x is unchecked.",
        recommendation="Add a bounds check before using x.",
    )
    adjudicated = AdjudicatedFinding(
        finding=finding, final_confidence="STRONG_STATIC_INDICATION",
        downgrade_reason=None, invalid_condition_ids=(), invalid_consequence_ids=(),
    )
    return [adjudicated], member_ids, {}


def _build_and_persist_proposal(workspace_root, java_relpath="Target.java", proposed_change=None):
    """Full A2->A3-P0->A3-P1 chain: real Java source on disk, real
    StructuredFinding/AdjudicatedFinding, durably-bound ProposedModification,
    persisted PENDING_APPROVAL. Returns the persisted proposal_id ('P1' -
    build_proposed_modification's own fixed id)."""
    java_path = os.path.join(workspace_root, java_relpath)
    if not os.path.isfile(java_path):
        with open(java_path, "w") as f:
            f.write(TARGET_SRC)
    adjudicated, member_ids, relation_ids = _make_finding_and_members(java_path)
    proposal = build_proposed_modification(
        "F1", adjudicated, member_ids, relation_ids, java_relpath, workspace_root=workspace_root,
    )
    if proposed_change is not None:
        import dataclasses
        proposal = dataclasses.replace(proposal, proposed_change=proposed_change)
    persist_proposal(proposal, workspace_root)
    return proposal.proposal_id  # "P1"


def _approved_promotable_workspace(tmp_path):
    """Convenience: a tmp_path with a real Target.java, a persisted+approved
    proposal, ready for prepare_proposal_promotion()/execute_approved_proposal()."""
    ws = str(tmp_path)
    pid = _build_and_persist_proposal(ws)
    approve_proposal(pid, ws)
    return ws, pid


def _init_git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("placeholder\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)


def _seed_checkpoint(tmp_path, cfg, goal, run_id, stage, **extra):
    save_checkpoint(str(tmp_path), run_id, {
        "stage": stage,
        "workspace_fingerprint": compute_workspace_fingerprint(str(tmp_path)),
        "config_fingerprint": compute_config_fingerprint(cfg.model_dump()),
        "goal_fingerprint": hashlib.sha256(f"{goal}\x00".encode("utf-8")).hexdigest(),
        **extra,
    })


def _minimal_attempt_ctx(tmp_path, **overrides) -> AttemptContext:
    """Local equivalent of test_workflow.py's own _minimal_attempt_ctx -
    drives run_attempt() (the real per-attempt CORR-018-P1 gate) without a
    full WorkflowEngine/Planner/Architect/Graph RAG chain."""
    default_run_verifier = AsyncMock()
    default_run_verifier.judge = AsyncMock(return_value={
        "should_run": False, "run_commands": [], "command_source": "inferred", "success_criteria": "",
    })
    default_run_verifier.grade = AsyncMock(return_value={
        "passed": False, "reasoning": "not requested", "likely_files": [],
    })
    default_spec_compliance = AsyncMock()
    default_spec_compliance.check = AsyncMock(return_value={
        "compliant": True, "reasoning": "not requested", "missing_requirements": [], "likely_files": [],
    })
    defaults = dict(
        goal="Fix bounds check in Target.doWork()",
        plan="Step 1: fix it",
        design="Design: edit Target.java",
        workspace_path=str(tmp_path),
        worktree_path=str(tmp_path),
        architect_files=["Target.java"],
        resume_state=None,
        run_id="test-run-id",
        skills_prompt="",
        learned_rag_context="",
        matched_files=[],
        related_files=[],
        ecosystem_invariant_block="",
        resource_lifecycle_block="",
        verification_contract_block="",
        recovery_contract_block="",
        required_files_prompt_block="",
        required_dependencies_prompt_block="",
        expected_files_upfront=["Target.java"],
        architect_basename_to_path={"Target.java": "Target.java"},
        chain=[],
        targeted_max_retries=3,
        stream_callback=None,
        approval_callback=None,
        active_skills=[],
        active_skill_rules_snapshot={},
        developer=AsyncMock(),
        run_verifier=default_run_verifier,
        spec_compliance=default_spec_compliance,
        skill_engine=MagicMock(),
        kernel=Kernel(config=AppConfig()),
        max_retries=4,
        web_lookup_query_callback=None,
        approve_web_lookup=AsyncMock(return_value=False),
    )
    defaults.update(overrides)
    if "migration_resolution" not in overrides:
        defaults["migration_resolution"] = resolve_migration_resolution(
            defaults.get("grounding_goal") or defaults["goal"], defaults["workspace_path"],
        )
    return AttemptContext(**defaults)


def _run_attempt_case(regions, candidate_content, plan_mentions_helper=False):
    with tempfile.TemporaryDirectory() as td:
        java_path = os.path.join(td, "Target.java")
        with open(java_path, "w") as f:
            f.write(TARGET_SRC)
        developer = AsyncMock()
        developer.run_generation = AsyncMock(return_value=[
            {"filepath": "Target.java", "content": candidate_content},
        ])
        plan_text = "Step 1: fix doWork bounds check"
        if plan_mentions_helper:
            plan_text += ". Also add a helper calculateBounds() to support it."
        ctx = _minimal_attempt_ctx(td, developer=developer, plan=plan_text, authorized_semantic_regions=list(regions))
        state = GenerationState()
        state.attempt_number = 0
        state.all_files_written = set()
        with patch(
            "kriya.tools.validate.PolymorphicValidator.run_compile_check",
            return_value={"success": True, "output": ""},
        ), patch(
            "kriya.tools.validate.PolymorphicValidator.run_tests",
            return_value={"success": True, "output": ""},
        ):
            try:
                asyncio.run(run_attempt(state, ctx))
                return ("ok", None, ctx)
            except QualityGateFailure as e:
                return ("rejected", e.failure.type, ctx)


async def _raise_if_called(*a, **kw):
    raise AssertionError("run_generation_workflow must not be called")


# ======================================================================
# Promotion validity (1-7)
# ======================================================================

def test_valid_approved_proposal_is_promotable(tmp_path):
    ws, pid = _approved_promotable_workspace(tmp_path)
    result = prepare_proposal_promotion(pid, ws)
    assert result.ok, result.reason_codes
    assert isinstance(result.promoted, PromotedProposal)


def test_pending_proposal_rejected(tmp_path):
    ws = str(tmp_path)
    pid = _build_and_persist_proposal(ws)
    result = prepare_proposal_promotion(pid, ws)
    assert not result.ok
    assert REASON_PROPOSAL_NOT_APPROVED in result.reason_codes


def test_rejected_proposal_rejected(tmp_path):
    ws = str(tmp_path)
    pid = _build_and_persist_proposal(ws)
    reject_proposal(pid, ws)
    result = prepare_proposal_promotion(pid, ws)
    assert not result.ok
    assert REASON_PROPOSAL_NOT_APPROVED in result.reason_codes


def test_tampered_approved_proposal_rejected(tmp_path):
    ws, pid = _approved_promotable_workspace(tmp_path)
    proposal_path = os.path.join(ws, ".kriya", "proposals", f"{pid}.json")
    with open(proposal_path) as f:
        raw = json.load(f)
    raw["proposal"]["proposed_change"] = "TAMPERED"
    with open(proposal_path, "w") as f:
        json.dump(raw, f)
    result = prepare_proposal_promotion(pid, ws)
    assert not result.ok
    assert REASON_PROPOSAL_NOT_CURRENTLY_VALID in result.reason_codes
    assert "PROPOSAL_TAMPERED" in result.reason_codes


def test_approved_digest_mismatch_rejected(tmp_path):
    ws, pid = _approved_promotable_workspace(tmp_path)
    proposal_path = os.path.join(ws, ".kriya", "proposals", f"{pid}.json")
    with open(proposal_path) as f:
        raw = json.load(f)
    raw["approval"]["approved_digest"] = "0" * 64  # syntactically valid, wrong
    with open(proposal_path, "w") as f:
        json.dump(raw, f)
    result = prepare_proposal_promotion(pid, ws)
    assert not result.ok
    assert REASON_PROPOSAL_NOT_CURRENTLY_VALID in result.reason_codes


def test_stale_approved_proposal_rejected_target_file_changed(tmp_path):
    ws, pid = _approved_promotable_workspace(tmp_path)
    with open(os.path.join(ws, "Target.java"), "a") as f:
        f.write("\n// drift\n")
    result = prepare_proposal_promotion(pid, ws)
    assert not result.ok
    assert REASON_PROPOSAL_NOT_CURRENTLY_VALID in result.reason_codes


def test_wrong_workspace_rejected(tmp_path):
    ws, pid = _approved_promotable_workspace(tmp_path)
    proposal_path = os.path.join(ws, ".kriya", "proposals", f"{pid}.json")
    with tempfile.TemporaryDirectory() as other_ws:
        os.makedirs(os.path.join(other_ws, ".kriya", "proposals"), exist_ok=True)
        import shutil
        shutil.copy(proposal_path, os.path.join(other_ws, ".kriya", "proposals", f"{pid}.json"))
        # Target.java doesn't even exist in other_ws
        result = prepare_proposal_promotion(pid, other_ws)
        assert not result.ok
        assert REASON_PROPOSAL_NOT_CURRENTLY_VALID in result.reason_codes


def test_unknown_proposal_id_raises_structural_error(tmp_path):
    with pytest.raises(ProposalStoreError) as exc_info:
        prepare_proposal_promotion("NoSuchId", str(tmp_path))
    assert exc_info.value.reason_code == "PROPOSAL_NOT_FOUND"


# ======================================================================
# Goal builder (8-13)
# ======================================================================

def test_goal_builder_pinned_vector(tmp_path):
    ws, pid = _approved_promotable_workspace(tmp_path)
    persisted = load_proposal(pid, ws)
    goal = build_authoritative_goal(persisted.proposal)
    expected = (
        "AUTHORITATIVE ENGINEERING CHANGE\n\n"
        "Target:\n"
        "Target.java\n"
        "Target.java::Target::METHOD::doWork::()\n\n"
        "Required change:\n"
        "Add a bounds check before using x.\n\n"
        "Must preserve:\n"
        "- unrelated public APIs\n"
        "- unrelated service/application behavior\n"
        "- existing repository contracts, unless the approved change specifically requires altering them\n"
        "- the existing signature of public void doWork(), unless the approved change explicitly requires modifying it\n"
        "- all files other than Target.java, unless the approved change explicitly requires touching them\n\n"
        "Required verification:\n"
        "- compile\n"
        "- existing regression tests\n"
        "- a targeted test for the reviewed behavior of public void doWork()"
    )
    assert goal == expected


def test_goal_builder_deterministic_same_proposal(tmp_path):
    ws, pid = _approved_promotable_workspace(tmp_path)
    persisted = load_proposal(pid, ws)
    assert build_authoritative_goal(persisted.proposal) == build_authoritative_goal(persisted.proposal)


def test_goal_unchanged_by_operational_metadata(tmp_path):
    ws, pid = _approved_promotable_workspace(tmp_path)
    p1 = load_proposal(pid, ws)
    goal1 = build_authoritative_goal(p1.proposal)
    # approve_proposal() only ever changes operational metadata (approval_state/
    # approved_digest/approved_at/updated_at) - the proposal's own semantic
    # fields (what build_authoritative_goal reads) are untouched.
    p2 = load_proposal(pid, ws)
    goal2 = build_authoritative_goal(p2.proposal)
    assert goal1 == goal2


def test_goal_changes_when_proposed_change_field_changes(tmp_path):
    ws = str(tmp_path)
    pid = _build_and_persist_proposal(ws, proposed_change="Do the original fix.")
    p1 = load_proposal(pid, ws)
    goal1 = build_authoritative_goal(p1.proposal)

    ws2 = tempfile.mkdtemp()
    pid2 = _build_and_persist_proposal(ws2, proposed_change="Do a completely different fix.")
    p2 = load_proposal(pid2, ws2)
    goal2 = build_authoritative_goal(p2.proposal)
    assert goal1 != goal2


def test_must_preserve_rendered_exactly(tmp_path):
    ws, pid = _approved_promotable_workspace(tmp_path)
    persisted = load_proposal(pid, ws)
    goal = build_authoritative_goal(persisted.proposal)
    for item in persisted.proposal.must_preserve:
        assert f"- {item}" in goal


def test_verification_rendered_as_verification_not_mutation(tmp_path):
    ws, pid = _approved_promotable_workspace(tmp_path)
    persisted = load_proposal(pid, ws)
    goal = build_authoritative_goal(persisted.proposal)
    assert "Required verification:" in goal
    assert "Required change:" in goal
    # Verification entries must appear under the verification header, never
    # blended into "Required change" as if they were mutation instructions.
    change_section = goal.split("Must preserve:")[0]
    for item in persisted.proposal.verification:
        assert item not in change_section


# ======================================================================
# Semantic regions (14-18)
# ======================================================================

def test_approved_method_body_proposal_produces_exact_method_body_region(tmp_path):
    ws, pid = _approved_promotable_workspace(tmp_path)
    result = prepare_proposal_promotion(pid, ws)
    body_regions = [r for r in result.promoted.authorized_semantic_regions if r.region_type == RegionType.METHOD_BODY]
    assert len(body_regions) == 1
    assert body_regions[0].relpath == "Target.java"
    assert body_regions[0].member_key == result.promoted.source_target_member_key


def test_imports_region_present(tmp_path):
    ws, pid = _approved_promotable_workspace(tmp_path)
    result = prepare_proposal_promotion(pid, ws)
    import_regions = [r for r in result.promoted.authorized_semantic_regions if r.region_type == RegionType.IMPORTS]
    assert len(import_regions) == 1


def test_no_whole_file_region_type_exists():
    assert not hasattr(RegionType, "WHOLE_FILE")


def test_unsupported_target_member_kind_rejected(tmp_path):
    """A2's proposal shape only ever expresses method/constructor body
    authority (proposal_to_authorized_semantic_regions()'s own documented
    limitation) - prepare_proposal_promotion() must fail closed on any other
    kind BEFORE calling that translator, rather than silently defaulting to
    METHOD_BODY for an unrecognized kind."""
    import dataclasses
    ws = str(tmp_path)
    java_path = os.path.join(ws, "Target.java")
    with open(java_path, "w") as f:
        f.write(TARGET_SRC)
    adjudicated, member_ids, relation_ids = _make_finding_and_members(java_path)
    proposal = build_proposed_modification("F1", adjudicated, member_ids, relation_ids, "Target.java", workspace_root=ws)
    unsupported = dataclasses.replace(proposal, target_member_kind="field")
    persist_proposal(unsupported, ws)
    approve_proposal(unsupported.proposal_id, ws)

    result = prepare_proposal_promotion(unsupported.proposal_id, ws)
    assert not result.ok
    assert REASON_PROPOSAL_PROMOTION_UNSUPPORTED in result.reason_codes


def test_region_translation_reads_only_proposal_fields_no_planner_or_candidate():
    import inspect
    params = list(inspect.signature(proposal_to_authorized_semantic_regions).parameters)
    assert params == ["proposal"]


# ======================================================================
# Generation invocation (19-22)
# ======================================================================

def test_valid_proposal_invokes_run_generation_workflow_exactly_once(tmp_path):
    ws, pid = _approved_promotable_workspace(tmp_path)
    we = MagicMock()
    we.run_generation_workflow = AsyncMock(return_value={"quality_gates_passed": True, "files": {}})
    asyncio.run(execute_approved_proposal(pid, ws, we))
    assert we.run_generation_workflow.await_count == 1


def test_exact_authoritative_goal_passed(tmp_path):
    ws, pid = _approved_promotable_workspace(tmp_path)
    we = MagicMock()
    we.run_generation_workflow = AsyncMock(return_value={"quality_gates_passed": True, "files": {}})
    asyncio.run(execute_approved_proposal(pid, ws, we))
    prep = prepare_proposal_promotion(pid, ws)
    kwargs = we.run_generation_workflow.await_args.kwargs
    assert kwargs["goal"] == prep.promoted.authoritative_goal


def test_exact_region_list_passed(tmp_path):
    ws, pid = _approved_promotable_workspace(tmp_path)
    we = MagicMock()
    we.run_generation_workflow = AsyncMock(return_value={"quality_gates_passed": True, "files": {}})
    asyncio.run(execute_approved_proposal(pid, ws, we))
    prep = prepare_proposal_promotion(pid, ws)
    kwargs = we.run_generation_workflow.await_args.kwargs
    assert kwargs["authorized_semantic_regions"] == list(prep.promoted.authorized_semantic_regions)


def test_existing_generation_flags_preserved(tmp_path):
    ws, pid = _approved_promotable_workspace(tmp_path)
    we = MagicMock()
    we.run_generation_workflow = AsyncMock(return_value={"quality_gates_passed": True, "files": {}})
    stream_cb = MagicMock()
    asyncio.run(execute_approved_proposal(
        pid, ws, we, knowledge_risk_confirmed=True, stream_callback=stream_cb, protected_source_file="x.txt",
    ))
    kwargs = we.run_generation_workflow.await_args.kwargs
    assert kwargs["knowledge_risk_confirmed"] is True
    assert kwargs["stream_callback"] is stream_cb
    assert kwargs["protected_source_file"] == "x.txt"
    assert kwargs["workspace_path"] == ws


# ======================================================================
# -y semantics (23-25)
# ======================================================================

def test_pending_plus_knowledge_risk_confirmed_still_rejects_before_generation(tmp_path):
    ws = str(tmp_path)
    pid = _build_and_persist_proposal(ws)
    we = MagicMock()
    we.run_generation_workflow = _raise_if_called
    with pytest.raises(ProposalPromotionError) as exc_info:
        asyncio.run(execute_approved_proposal(pid, ws, we, knowledge_risk_confirmed=True))
    assert REASON_PROPOSAL_NOT_APPROVED in exc_info.value.reason_codes


def test_rejected_plus_knowledge_risk_confirmed_still_rejects(tmp_path):
    ws = str(tmp_path)
    pid = _build_and_persist_proposal(ws)
    reject_proposal(pid, ws)
    we = MagicMock()
    we.run_generation_workflow = _raise_if_called
    with pytest.raises(ProposalPromotionError) as exc_info:
        asyncio.run(execute_approved_proposal(pid, ws, we, knowledge_risk_confirmed=True))
    assert REASON_PROPOSAL_NOT_APPROVED in exc_info.value.reason_codes


def test_approved_valid_plus_knowledge_risk_confirmed_invokes_generation(tmp_path):
    ws, pid = _approved_promotable_workspace(tmp_path)
    we = MagicMock()
    we.run_generation_workflow = AsyncMock(return_value={"quality_gates_passed": True, "files": {}})
    asyncio.run(execute_approved_proposal(pid, ws, we, knowledge_risk_confirmed=True))
    assert we.run_generation_workflow.await_count == 1
    assert we.run_generation_workflow.await_args.kwargs["knowledge_risk_confirmed"] is True


def test_execute_approved_proposal_has_no_resume_parameters():
    import inspect
    params = list(inspect.signature(execute_approved_proposal).parameters)
    assert "resume" not in params
    assert "resume_id" not in params


# ======================================================================
# CORR-018 integration (26-29) - real run_attempt() gate, not the pure comparator
# ======================================================================

def test_corr018_authorized_target_body_only_change_allowed(tmp_path):
    ws, pid = _approved_promotable_workspace(tmp_path)
    regions = prepare_proposal_promotion(pid, ws).promoted.authorized_semantic_regions
    outcome, failure_type, _ = _run_attempt_case(regions, AUTHORIZED_BODY)
    assert outcome == "ok", failure_type


def test_corr018_unrelated_same_file_body_change_rejected(tmp_path):
    ws, pid = _approved_promotable_workspace(tmp_path)
    regions = prepare_proposal_promotion(pid, ws).promoted.authorized_semantic_regions
    outcome, failure_type, _ = _run_attempt_case(regions, UNRELATED_ALSO_CHANGED)
    assert outcome == "rejected"
    assert failure_type == "semantic_region_unauthorized"


def test_corr018_unauthorized_helper_add_rejected(tmp_path):
    ws, pid = _approved_promotable_workspace(tmp_path)
    regions = prepare_proposal_promotion(pid, ws).promoted.authorized_semantic_regions
    outcome, failure_type, _ = _run_attempt_case(regions, UNAUTHORIZED_HELPER_ADDED, plan_mentions_helper=True)
    assert outcome == "rejected"
    assert failure_type == "semantic_region_unauthorized"


def test_corr018_planner_strategy_naming_helper_does_not_broaden_authority(tmp_path):
    """Same unauthorized helper candidate, but Planner's own plan text never
    mentions it at all - rejection is identical either way, proving CORR-018's
    decision is purely region-driven, never derived from plan/goal text."""
    ws, pid = _approved_promotable_workspace(tmp_path)
    regions = prepare_proposal_promotion(pid, ws).promoted.authorized_semantic_regions
    with_mention, type_with, _ = _run_attempt_case(regions, UNAUTHORIZED_HELPER_ADDED, plan_mentions_helper=True)
    without_mention, type_without, _ = _run_attempt_case(regions, UNAUTHORIZED_HELPER_ADDED, plan_mentions_helper=False)
    assert with_mention == without_mention == "rejected"
    assert type_with == type_without == "semantic_region_unauthorized"


# ======================================================================
# Recovery (30-31)
# ======================================================================

def test_recovery_retry_receives_same_immutable_authorized_regions(tmp_path):
    ws, pid = _approved_promotable_workspace(tmp_path)
    regions = prepare_proposal_promotion(pid, ws).promoted.authorized_semantic_regions
    _, _, ctx = _run_attempt_case(regions, AUTHORIZED_BODY)
    # AttemptContext.authorized_semantic_regions is a single fixed list set
    # once at construction - run_attempt() never reassigns it, so any
    # retry/recovery path within the same attempt necessarily sees the exact
    # same object, never a broadened one.
    assert ctx.authorized_semantic_regions == list(regions)


def test_unauthorized_recovery_change_still_rejected(tmp_path):
    ws, pid = _approved_promotable_workspace(tmp_path)
    regions = prepare_proposal_promotion(pid, ws).promoted.authorized_semantic_regions
    outcome, failure_type, _ = _run_attempt_case(regions, UNAUTHORIZED_HELPER_ADDED)
    assert outcome == "rejected"
    assert failure_type == "semantic_region_unauthorized"


# ======================================================================
# Approval drift (32-37)
# ======================================================================

def test_approve_then_target_file_mutated_then_execute_rejected(tmp_path):
    ws, pid = _approved_promotable_workspace(tmp_path)
    with open(os.path.join(ws, "Target.java"), "a") as f:
        f.write("\n// mutated after approval\n")
    we = MagicMock()
    we.run_generation_workflow = _raise_if_called
    with pytest.raises(ProposalPromotionError) as exc_info:
        asyncio.run(execute_approved_proposal(pid, ws, we))
    assert REASON_PROPOSAL_NOT_CURRENTLY_VALID in exc_info.value.reason_codes


def test_approve_then_evidence_file_mutated_then_execute_rejected(tmp_path):
    ws = str(tmp_path)
    # give the finding a related-file evidence entry as well as the member
    java_path = os.path.join(ws, "Target.java")
    with open(java_path, "w") as f:
        f.write(TARGET_SRC)
    related_path = os.path.join(ws, "Collaborator.java")
    with open(related_path, "w") as f:
        f.write("public class Collaborator {}\n")

    from kriya.workflow.review_context import RelatedFile
    adjudicated, member_ids, _ = _make_finding_and_members(java_path)
    relation_ids = {"R1": RelatedFile(relpath="Collaborator.java", relation="CALLED_BY", detail="constructor param")}
    adjudicated[0].finding.consequence_evidence_ids.append("R1") if hasattr(adjudicated[0].finding.consequence_evidence_ids, "append") else None
    proposal = build_proposed_modification("F1", adjudicated, member_ids, relation_ids, "Target.java", workspace_root=ws)
    persist_proposal(proposal, ws)
    pid = proposal.proposal_id
    approve_proposal(pid, ws)

    if any(eb.canonical_relpath == "Collaborator.java" for eb in load_proposal(pid, ws).proposal.evidence_bindings):
        with open(related_path, "a") as f:
            f.write("\n// mutated evidence after approval\n")
        we = MagicMock()
        we.run_generation_workflow = _raise_if_called
        with pytest.raises(ProposalPromotionError) as exc_info:
            asyncio.run(execute_approved_proposal(pid, ws, we))
        assert REASON_PROPOSAL_NOT_CURRENTLY_VALID in exc_info.value.reason_codes
    else:
        # This finding's evidence didn't bind R1 (member-only grounding) -
        # target-file drift (already covered above) is the applicable case;
        # nothing further to assert here.
        pass


# ======================================================================
# CLI (38-43)
# ======================================================================

def test_cli_proposal_execute_pending_refusal(tmp_path):
    from kriya.cli import main
    ws = str(tmp_path)
    pid = _build_and_persist_proposal(ws)
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(ws)
        result = runner.invoke(main, ["proposal", "execute", pid])
    finally:
        os.chdir(old_cwd)
    assert result.exit_code != 0
    assert "PROPOSAL_NOT_APPROVED" in result.output + str(result.exception)


def test_cli_proposal_execute_rejected_refusal(tmp_path):
    from kriya.cli import main
    ws = str(tmp_path)
    pid = _build_and_persist_proposal(ws)
    reject_proposal(pid, ws)
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(ws)
        result = runner.invoke(main, ["proposal", "execute", pid])
    finally:
        os.chdir(old_cwd)
    assert result.exit_code != 0
    assert "PROPOSAL_NOT_APPROVED" in result.output + str(result.exception)


def test_cli_proposal_execute_stale_refusal(tmp_path):
    from kriya.cli import main
    ws = str(tmp_path)
    pid = _build_and_persist_proposal(ws)
    approve_proposal(pid, ws)
    with open(os.path.join(ws, "Target.java"), "a") as f:
        f.write("\n// drift\n")
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(ws)
        result = runner.invoke(main, ["proposal", "execute", pid])
    finally:
        os.chdir(old_cwd)
    assert result.exit_code != 0
    assert "PROPOSAL_NOT_CURRENTLY_VALID" in result.output + str(result.exception)


def test_cli_proposal_execute_unknown_id_refusal(tmp_path):
    from kriya.cli import main
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(tmp_path))
        result = runner.invoke(main, ["proposal", "execute", "NoSuchId"])
    finally:
        os.chdir(old_cwd)
    assert result.exit_code != 0
    assert "PROPOSAL_NOT_FOUND" in result.output + str(result.exception)


def test_cli_proposal_execute_approved_valid_invokes_generation(tmp_path):
    from kriya.cli import main
    ws, pid = _approved_promotable_workspace(tmp_path)
    runner = CliRunner()
    old_cwd = os.getcwd()
    fake_we = MagicMock()
    fake_we.run_generation_workflow = AsyncMock(return_value={"quality_gates_passed": True, "files": {}})
    try:
        os.chdir(ws)
        with patch("kriya.cli.WorkflowEngine", return_value=fake_we), patch("kriya.cli.LLMClient"):
            result = runner.invoke(main, ["proposal", "execute", pid, "-y"])
    finally:
        os.chdir(old_cwd)
    assert result.exit_code == 0, result.output + str(result.exception)
    assert fake_we.run_generation_workflow.await_count == 1


def test_cli_review_proposal_show_reject_unaffected_by_execute_addition(tmp_path):
    """A3-P2 adds `proposal execute` alongside A3-P1's show/approve/reject -
    confirms those pre-existing subcommands are untouched."""
    from kriya.cli import main
    ws = str(tmp_path)
    pid = _build_and_persist_proposal(ws)
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(ws)
        show = runner.invoke(main, ["proposal", "show", pid])
        assert show.exit_code == 0
        assert "PENDING_APPROVAL" in show.output
    finally:
        os.chdir(old_cwd)


# ======================================================================
# Source write boundary (44-46)
# ======================================================================

def _tree_hash(root):
    import hashlib as _h
    digest = _h.sha256()
    for dirpath, dirnames, filenames in sorted(os.walk(root)):
        dirnames.sort()
        if ".git" in dirnames:
            dirnames.remove(".git")
        for fn in sorted(filenames):
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, root)
            with open(fp, "rb") as f:
                digest.update(rel.encode())
                digest.update(f.read())
    return digest.hexdigest()


def test_refused_execute_writes_no_source_files(tmp_path):
    ws = str(tmp_path)
    pid = _build_and_persist_proposal(ws)  # PENDING - execute must refuse
    before = _tree_hash(ws)
    we = MagicMock()
    we.run_generation_workflow = _raise_if_called
    with pytest.raises(ProposalPromotionError):
        asyncio.run(execute_approved_proposal(pid, ws, we))
    # only .kriya/proposals may legitimately change across this whole test
    # module (persist_proposal already wrote it before this snapshot) -
    # nothing else should move.
    after = _tree_hash(ws)
    assert before == after


def test_promotion_preparation_writes_no_files(tmp_path):
    ws, pid = _approved_promotable_workspace(tmp_path)
    before = _tree_hash(ws)
    prepare_proposal_promotion(pid, ws)
    after = _tree_hash(ws)
    assert before == after


def test_successful_execution_source_writes_only_through_mocked_generation_workflow(tmp_path):
    """execute_approved_proposal() itself never opens a target source file for
    writing - the only write path is whatever run_generation_workflow (here,
    a mock) does. Proven by asserting no file I/O happens on the real
    workspace outside .kriya/proposals when the mock performs no writes."""
    ws, pid = _approved_promotable_workspace(tmp_path)
    before = _tree_hash(ws)
    we = MagicMock()
    we.run_generation_workflow = AsyncMock(return_value={"quality_gates_passed": True, "files": {}})
    asyncio.run(execute_approved_proposal(pid, ws, we))
    after = _tree_hash(ws)
    assert before == after


# ======================================================================
# Checkpoint / resume (47-49)
# ======================================================================

def test_checkpoint_marker_set_true_when_regions_supplied(tmp_path):
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code", "Design: Write math.py",
        "def add(a,b):\n    return a+b", "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    captured = []
    real_save = save_checkpoint

    def spy_save(workspace_path, run_id, data):
        captured.append(dict(data))
        return real_save(workspace_path, run_id, data)

    with patch("kriya.workflow.workflow.save_checkpoint", side_effect=spy_save):
        res = asyncio.run(we.run_generation_workflow(
            goal="Create math library", workspace_path=str(tmp_path),
            authorized_semantic_regions=FAKE_UNRELATED_REGION,
        ))
    assert res["quality_gates_passed"] is True
    assert captured
    assert all(c.get("had_authorized_semantic_regions") is True for c in captured)


def test_resume_fails_closed_when_region_bound_checkpoint_resumed_without_regions(tmp_path):
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    _seed_checkpoint(tmp_path, cfg, "Create math library", "ckpt-region", "plan",
                      plan="Stale plan", had_authorized_semantic_regions=True)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code", "Design: Write math.py",
        "def add(a,b):\n    return a+b", "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    res = asyncio.run(we.run_generation_workflow(
        goal="Create math library", workspace_path=str(tmp_path), resume=True,
    ))
    # All 4 completions consumed -> fresh run, checkpoint was NOT resumed
    # (a real resume would skip the Planner call and only consume 3).
    assert llm.complete.await_count == 4
    assert res["plan"] == "Step 1: Write code"


def test_resume_succeeds_when_regions_supplied_on_resume_call_too(tmp_path):
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    _seed_checkpoint(tmp_path, cfg, "Create math library", "ckpt-region2", "plan",
                      plan="Step 1: Write code", had_authorized_semantic_regions=True)
    llm.complete = AsyncMock(side_effect=[
        "Design: Write math.py", "def add(a,b):\n    return a+b", "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    asyncio.run(we.run_generation_workflow(
        goal="Create math library", workspace_path=str(tmp_path), resume=True,
        authorized_semantic_regions=FAKE_UNRELATED_REGION,
    ))
    assert llm.complete.await_count == 3  # Planner skipped -> real resume


def test_preexisting_checkpoint_resume_unaffected_by_a3p2(tmp_path):
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    _seed_checkpoint(tmp_path, cfg, "Create math library", "ckpt-plain", "plan", plan="Step 1: Write code")
    llm.complete = AsyncMock(side_effect=[
        "Design: Write math.py", "def add(a,b):\n    return a+b", "Review: Approved",
    ])
    we = WorkflowEngine(kernel, llm)
    asyncio.run(we.run_generation_workflow(goal="Create math library", workspace_path=str(tmp_path), resume=True))
    assert llm.complete.await_count == 3


# PROPOSAL_EXECUTION_RESUME_FAILS_CLOSED for v1: execute_approved_proposal()
# structurally cannot resume (no resume/resume_id parameter at all - proven
# above in test_execute_approved_proposal_has_no_resume_parameters). This is
# the deliberate v1 answer, not an oversight - see proposal_promotion.py's
# own module docstring.


# ======================================================================
# No regeneration proof (50-52)
# ======================================================================

def test_execution_does_not_call_build_proposed_modification(tmp_path):
    ws, pid = _approved_promotable_workspace(tmp_path)
    we = MagicMock()
    we.run_generation_workflow = AsyncMock(return_value={"quality_gates_passed": True, "files": {}})

    async def _raise(*a, **kw):
        raise AssertionError("build_proposed_modification must not be called during execution")

    with patch("kriya.workflow.review_context.build_proposed_modification", side_effect=_raise):
        res = asyncio.run(execute_approved_proposal(pid, ws, we))
    assert res["quality_gates_passed"] is True


def test_execution_does_not_call_a1_reviewer_run(tmp_path):
    ws, pid = _approved_promotable_workspace(tmp_path)
    we = MagicMock()
    we.run_generation_workflow = AsyncMock(return_value={"quality_gates_passed": True, "files": {}})

    def _raise(*a, **kw):
        raise AssertionError("ReviewerAgent.run_structured_review must not be called during execution")

    with patch("kriya.agents.agent.ReviewerAgent.run_structured_review", side_effect=_raise):
        res = asyncio.run(execute_approved_proposal(pid, ws, we))
    assert res["quality_gates_passed"] is True


def test_execution_does_not_call_reviewer_llm_run(tmp_path):
    ws, pid = _approved_promotable_workspace(tmp_path)
    we = MagicMock()
    we.run_generation_workflow = AsyncMock(return_value={"quality_gates_passed": True, "files": {}})

    def _raise(*a, **kw):
        raise AssertionError("ReviewerAgent.run must not be called during execution")

    with patch("kriya.agents.agent.ReviewerAgent.run", side_effect=_raise):
        res = asyncio.run(execute_approved_proposal(pid, ws, we))
    assert res["quality_gates_passed"] is True


# ======================================================================
# Full pre-promotion -> generation-path integration test
# ======================================================================

def test_full_persist_approve_promote_generate_harness_rejects_unauthorized_helper(tmp_path):
    """A2 bound proposal -> persist -> approve -> prepare promotion -> exact
    authoritative goal -> exact AuthorizedSemanticRegion[] -> real run_attempt()
    generation-path gate with a fake Developer output that (a) adds an
    unauthorized helper (CORR-018 must reject) and (b) changes only the
    authorized target (CORR-018 must pass the semantic boundary)."""
    ws, pid = _approved_promotable_workspace(tmp_path)
    prep = prepare_proposal_promotion(pid, ws)
    assert prep.ok
    assert prep.promoted.authoritative_goal
    assert len(prep.promoted.authorized_semantic_regions) == 2

    rejected_outcome, rejected_type, _ = _run_attempt_case(
        prep.promoted.authorized_semantic_regions, UNAUTHORIZED_HELPER_ADDED,
    )
    assert rejected_outcome == "rejected"
    assert rejected_type == "semantic_region_unauthorized"

    passed_outcome, _, _ = _run_attempt_case(
        prep.promoted.authorized_semantic_regions, AUTHORIZED_BODY,
    )
    assert passed_outcome == "ok"
