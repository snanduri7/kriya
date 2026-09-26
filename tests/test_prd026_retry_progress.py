"""PRD-026: universal retry progress invariant.

Proves, per retry family, which progress-vector dimensions justify another
attempt. A previously produced vector, including an A -> B -> A -> B
alternation, is never progress. SAMPLING_RESAMPLE is budgeted and never
counted as progress. At temperature 0 an identical-evidence retry is
refused. Also covers the terminal NO_PROGRESS result and CLI contract.
"""
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kriya.cli_output import no_progress_stop_message
from kriya.config import AppConfig, FallbackModelConfig
from kriya.core.kernel import Kernel
from kriya.workflow.attempt import AttemptContext, _prepare_retry_context
from kriya.workflow.failure import Failure, QualityGateFailure
from kriya.workflow.retry_policy import API_CONTRACT_RECOVERY_MAX_ATTEMPTS, RetryAction, decide_retry_action
from kriya.workflow.retry_progress import (
    NO_PROGRESS_TERMINAL_REASON,
    PROGRESS,
    REPEATED_VECTOR,
    RETRY_ACTION_MATERIAL_DELTA,
    SAMPLING_NOT_PERMITTED,
    SAMPLING_RESAMPLE,
    build_progress_vector,
    classify_progress,
    sampling_resample_permitted,
)
from kriya.workflow.retry_strategy import record_workspace_progress
from kriya.workflow.state import GenerationState

# --- the vector ------------------------------------------------------------


def _vector(**overrides):
    base = dict(
        failure_signature=("compile", "cannot find symbol"), workspace_hash="ws-A",
        implicated_files=["b.py", "a.py"], missing_files=[], evidence_fingerprint=("fp", 1),
        context_items={}, action="targeted", protocol=None, request_profile="primary@p1",
        plan_revision="plan-1", repair_contract=None, diagnostics=["X"],
    )
    base.update(overrides)
    return build_progress_vector(**base)


def test_vector_digest_is_stable_and_order_insensitive():
    assert _vector().digest() == _vector(implicated_files=["a.py", "b.py", "a.py"]).digest()
    assert _vector().digest() != _vector(workspace_hash="ws-B").digest()
    assert _vector().to_dict()["digest"] == _vector().digest()


def test_changed_dimensions_names_exactly_the_delta():
    assert _vector(workspace_hash="ws-B", action="full_set").changed_dimensions(_vector()) == (
        "workspace_hash", "action",
    )
    assert _vector().changed_dimensions(_vector()) == ()
    assert "failure_signature" in _vector().changed_dimensions(None)


def test_context_revisions_and_repair_contract_are_normalized():
    item = MagicMock(revision="r1", tier="member_exact", member_id="A.m")
    with_item = _vector(context_items={"x.py": item})
    assert with_item.context_revisions == (("x.py", "r1", "member_exact", "A.m"),)
    contract = MagicMock(id="c1", status=MagicMock(value="active"), active_group_id="g1",
                         participating_artifacts=("a.py",))
    assert _vector(repair_contract=contract).repair_contract_revision
    assert _vector(repair_contract=contract).digest() != _vector().digest()


# --- classification ----------------------------------------------------------


@pytest.mark.parametrize(("kwargs", "expected"), [
    (dict(already_seen=True, same_workspace=False, action_changed=True), (REPEATED_VECTOR, True)),
    (dict(already_seen=False, same_workspace=False, action_changed=False), (PROGRESS, False)),
    (dict(already_seen=False, same_workspace=True, action_changed=True), ("NO_PROGRESS", False)),
    (dict(already_seen=False, same_workspace=True, action_changed=False), ("NO_PROGRESS", True)),
])
def test_classify_progress(kwargs, expected):
    assert classify_progress(stage_regressed=False, repeated_action=False, **kwargs) == expected


def test_every_retrying_action_has_an_audited_material_delta():
    retrying = {action for action in RetryAction if not action.value.startswith("stop_")}
    assert set(RETRY_ACTION_MATERIAL_DELTA) == retrying
    vector_fields = set(_vector().to_dict()) - {"version", "digest"}
    for dims in RETRY_ACTION_MATERIAL_DELTA.values():
        assert dims and set(dims) <= vector_fields


def _record(state, vector, *, limit=3):
    return record_workspace_progress(
        state, vector.workspace_hash, limit,
        failure_signature=vector.failure_signature, stage="compile",
        files=vector.implicated_files, action=vector.action, vector=vector,
    )


# The dimension each family's own attempt characteristically changes.
_FAMILY_DELTA = {
    RetryAction.TARGETED: ("evidence_fingerprint", ("fp", 2)),
    RetryAction.MISSING_FILES: ("missing_files", ["new.py"]),
    RetryAction.FALLBACK_TARGETED: ("request_profile", "fallback@p9"),
    RetryAction.FULL_SET: ("workspace_hash", "ws-Z"),
    RetryAction.API_CONTRACT_RECOVERY: ("protocol", "restore_public_contract"),
}


@pytest.mark.parametrize("family", list(_FAMILY_DELTA))
def test_each_family_same_vector_is_not_progress_changed_material_dim_is(family):
    dim, changed_value = _FAMILY_DELTA[family]
    assert dim in RETRY_ACTION_MATERIAL_DELTA[family]
    state = GenerationState()
    first = _vector(action=family.value)
    _record(state, first)

    repeat = GenerationState()
    _record(repeat, first)
    _record(repeat, first)
    assert repeat.last_progress_classification == REPEATED_VECTOR
    assert repeat.consecutive_no_progress_attempts == 1

    moved = _vector(action=family.value, **{dim: changed_value})
    assert moved.digest() != first.digest()
    _record(state, moved)
    assert state.last_progress_classification != REPEATED_VECTOR


def test_alternating_cycle_is_not_progress_and_terminates():
    """A -> B -> A -> B: every step changes workspace and action, which the
    pre-PRD-026 pairwise rule always reset."""
    a = _vector(workspace_hash="ws-A", action="targeted")
    b = _vector(workspace_hash="ws-B", action="full_set")
    state = GenerationState()
    assert _record(state, a) and _record(state, b)
    assert state.consecutive_no_progress_attempts == 0
    assert _record(state, a) is True       # A again: seen
    assert state.last_progress_classification == REPEATED_VECTOR
    assert _record(state, b) is True       # B again: seen
    assert _record(state, a) is False      # third repeat reaches the limit
    assert state.no_progress_terminated is True
    assert state.no_progress_reason == NO_PROGRESS_TERMINAL_REASON
    kinds = [event.kind for event in state.run_events]
    assert kinds.count("retry.no_progress_terminal") == 1
    summary = state.retry_progress_summary()
    assert summary["terminal_reason"] == NO_PROGRESS_TERMINAL_REASON
    assert summary["distinct_vectors"] == 2
    assert summary["classification"] == REPEATED_VECTOR


def test_same_cycle_without_vectors_is_the_legacy_gap():
    """Documents the gap PRD-026 closes: the pairwise rule alone resets on
    every alternation and never terminates."""
    state = GenerationState()
    for _ in range(5):
        for workspace, action in (("ws-A", "targeted"), ("ws-B", "full_set")):
            assert record_workspace_progress(state, workspace, 3, action=action) is True
    assert state.consecutive_no_progress_attempts == 0


def test_identical_generated_bytes_cannot_reset_progress_by_switching_action():
    state = GenerationState()
    targeted = _vector(action="targeted")
    full_set = _vector(action="full_set")
    _record(state, targeted)
    _record(state, full_set)  # strategy transition on the same bytes: allowed once
    assert state.consecutive_no_progress_attempts == 0
    _record(state, targeted)  # back to the old action, same bytes: seen
    assert state.last_progress_classification == REPEATED_VECTOR
    assert state.consecutive_no_progress_attempts == 1


def test_progress_vector_event_reports_changed_dimensions():
    state = GenerationState()
    _record(state, _vector())
    _record(state, _vector(workspace_hash="ws-B"))
    event = [e for e in state.run_events if e.kind == "retry.progress_vector"][-1]
    assert event.details["changed_dimensions"] == ["workspace_hash"]
    assert event.details["classification"] == PROGRESS


def test_api_contract_recovery_keeps_its_hard_maximum():
    decision = decide_retry_action(
        retry_count=0, max_retries=4, targeted_retry_count=0, targeted_max_retries=3,
        has_implicated_files=True, has_missing_files=False, has_fallback_model=True,
        fallback_targeted_attempted=False, environment_failure=None,
        has_api_contract_recovery=True, api_contract_recovery_count=API_CONTRACT_RECOVERY_MAX_ATTEMPTS,
    )
    assert decision.action is RetryAction.STOP_EXHAUSTED


def test_api_contract_recovery_cycle_is_detected():
    restore = _vector(action="api_contract_recovery", protocol="restore_public_contract", workspace_hash="ws-R")
    repair = _vector(action="api_contract_recovery", protocol="repair_callers", workspace_hash="ws-C")
    state = GenerationState()
    _record(state, restore)
    _record(state, repair)
    _record(state, restore)
    assert state.last_progress_classification == REPEATED_VECTOR
    assert state.consecutive_no_progress_attempts == 1


# --- sampling allowance ------------------------------------------------------


@pytest.mark.parametrize(("mode", "temperature", "permitted"), [
    ("targeted", 0.7, True), ("full_set", 0.2, True), ("missing_files", None, True),
    ("fallback_targeted", 0.5, True), ("targeted", 0.0, False),
    ("api_contract_recovery", 0.7, False), (None, 0.7, False),
])
def test_sampling_resample_permitted(mode, temperature, permitted):
    assert sampling_resample_permitted(mode, temperature) is permitted


def _ctx(tmp_path, config):
    from kriya.workflow.migration import resolve_migration_resolution
    return AttemptContext(
        goal="Fix a narrow bug", plan="p", design="d", workspace_path=str(tmp_path),
        worktree_path=str(tmp_path), architect_files=["engine.py"], resume_state=None, run_id="r",
        skills_prompt="", learned_rag_context="", matched_files=[], related_files=[],
        ecosystem_invariant_block="", resource_lifecycle_block="", verification_contract_block="",
        recovery_contract_block="", required_files_prompt_block="", required_dependencies_prompt_block="",
        expected_files_upfront=["engine.py"], architect_basename_to_path={"engine.py": "engine.py"},
        chain=list(config.llm_chain), targeted_max_retries=3, stream_callback=None, approval_callback=None,
        active_skills=[], active_skill_rules_snapshot={}, developer=AsyncMock(), run_verifier=AsyncMock(),
        spec_compliance=AsyncMock(), skill_engine=MagicMock(), kernel=Kernel(config=config), max_retries=4,
        web_lookup_query_callback=None, approve_web_lookup=AsyncMock(return_value=False),
        migration_resolution=resolve_migration_resolution("Fix a narrow bug", str(tmp_path)),
    )


def _probabilistic_state():
    state = GenerationState()
    state.last_attempt_mode = "targeted"
    state.last_failure = Failure(type="compile", message="cannot find symbol", raw_output="cannot find symbol",
                                 likely_files=["engine.py"], attempt=2)
    state.budgets.last_failure_signature = ("compile", "cannot find symbol")
    return state


def _prepare(state, ctx, model="llama3"):
    return _prepare_retry_context(state, ctx, target_files=["engine.py"], prompt_window=16384, model_identity=model)


def test_identical_evidence_is_a_budgeted_sampling_resample_at_nonzero_temperature(tmp_path):
    (tmp_path / "engine.py").write_text("x = 1\n")
    config = AppConfig()
    config.llm.temperature = 0.7
    ctx, state = _ctx(tmp_path, config), _probabilistic_state()
    _prepare(state, ctx)
    _prepare(state, ctx)
    assert state.sampling_resamples == 1
    events = [e for e in state.run_events if e.kind == "retry.sampling_resample"]
    assert len(events) == 1 and events[0].details["reason_code"] == SAMPLING_RESAMPLE
    assert events[0].details["effective_temperature"] == 0.7
    # the resample never touched the progress state
    assert state.consecutive_no_progress_attempts == 0
    assert state.progress_vector_digests == {}


def test_identical_evidence_is_refused_at_temperature_zero(tmp_path):
    (tmp_path / "engine.py").write_text("x = 1\n")
    config = AppConfig()
    config.llm.temperature = 0.0
    ctx, state = _ctx(tmp_path, config), _probabilistic_state()
    _prepare(state, ctx)
    with pytest.raises(QualityGateFailure) as exc_info:
        _prepare(state, ctx)
    assert exc_info.value.failure.type == "no_progress_retry"
    assert exc_info.value.failure.diagnostics == {"reason_code": SAMPLING_NOT_PERMITTED}
    assert state.sampling_resamples == 0


def test_retry_temperature_overrides_the_binding_temperature(tmp_path):
    (tmp_path / "engine.py").write_text("x = 1\n")
    config = AppConfig()
    config.llm.temperature = 0.7
    config.llm.retry_temperature = 0.0
    ctx, state = _ctx(tmp_path, config), _probabilistic_state()
    _prepare(state, ctx)
    with pytest.raises(QualityGateFailure):
        _prepare(state, ctx)


def test_fallback_binding_temperature_is_used_for_a_fallback_retry(tmp_path):
    (tmp_path / "engine.py").write_text("x = 1\n")
    config = AppConfig()
    config.llm.temperature = 0.7
    config.llm_chain = [FallbackModelConfig(model="greedy-fallback", temperature=0.0)]
    ctx, state = _ctx(tmp_path, config), _probabilistic_state()
    state.last_attempt_mode = "fallback_targeted"
    _prepare(state, ctx, model="greedy-fallback")
    with pytest.raises(QualityGateFailure):
        _prepare(state, ctx, model="greedy-fallback")


def test_deterministic_evidence_cycle_is_blocked_against_any_earlier_attempt(tmp_path):
    """A -> B -> A evidence: the pre-PRD-026 gate compared only with the
    immediately preceding attempt, so the third call slipped through."""
    (tmp_path / "engine.py").write_text("x = 1\n")
    ctx = _ctx(tmp_path, AppConfig())
    state = GenerationState()
    state.last_failure = Failure(
        type="operation_contract", message="whole-file authority rejected", raw_output="rejected",
        likely_files=["engine.py"], attempt=3,
        diagnostics={"reason_code": "ACTUAL_MUTATION_SHAPE_AUTHORITY_REJECTED"},
    )
    state.budgets.last_failure_signature = ("operation_contract", "whole-file authority rejected")
    state.last_attempt_mode = "full_set"
    _prepare(state, ctx)
    state.last_attempt_mode = "targeted"
    _prepare(state, ctx)
    state.last_attempt_mode = "full_set"
    with pytest.raises(QualityGateFailure) as exc_info:
        _prepare(state, ctx)
    assert exc_info.value.failure.diagnostics == {"reason_code": "NO_PROGRESS_RETRY_EXHAUSTED"}


# --- result and CLI contract ---------------------------------------------------


def test_retry_progress_summary_on_a_run_without_retries():
    summary = GenerationState().retry_progress_summary()
    assert summary == {
        "classification": None, "consecutive_no_progress_attempts": 0, "no_progress_terminated": False,
        "terminal_reason": None, "distinct_vectors": 0, "last_vector_digest": None, "sampling_resamples": 0,
    }


def test_no_progress_cli_message_names_the_terminal_reason():
    state = GenerationState()
    vector = _vector()
    for _ in range(4):
        _record(state, replace(vector))
    message = no_progress_stop_message(state.retry_progress_summary())
    assert "[NO PROGRESS]" in message and NO_PROGRESS_TERMINAL_REASON in message
    assert "REPEATED_VECTOR" in message
    assert "[NO PROGRESS] RETRY_NO_PROGRESS_EXHAUSTED" in no_progress_stop_message(None)


def test_deterministic_diagnostics_are_a_material_dimension():
    assert _vector(diagnostics=["A"]).digest() != _vector(diagnostics=["B"]).digest()
    assert _vector(diagnostics=["A", None, ""]).diagnostics == ("A",)


@pytest.mark.asyncio
async def test_workflow_result_reports_terminal_no_progress(tmp_path):
    """End to end through run_generation_workflow: identical candidates
    failing identically end with failure_category ``no_progress`` and a
    ``retry_progress`` result block, not a generic budget exhaustion."""
    from unittest.mock import patch

    from kriya.core.llm import LLMClient
    from kriya.workflow.workflow import WorkflowEngine

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.skills = str(tmp_path / "skills")
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=["Step 1: Write code", "Design: Write math.py"] + ["Review: done"] * 5)
    we = WorkflowEngine(Kernel(config=cfg), llm)
    we.developer.run_generation = AsyncMock(
        return_value=[{"filepath": "math.py", "content": "def add(a,b):\n    return a+b\n"}],
    )
    with patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check",
        return_value={"success": False, "output": "Build error: dependency resolution failed."},
    ):
        res = await we.run_generation_workflow(goal="Create math library", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is False
    assert res["failure_category"] == "no_progress"
    progress = res["retry_progress"]
    assert progress["no_progress_terminated"] is True
    assert progress["terminal_reason"] == NO_PROGRESS_TERMINAL_REASON
    assert progress["classification"] == REPEATED_VECTOR
    assert progress["distinct_vectors"] >= 1 and progress["last_vector_digest"]
    # 1 new vector + 3 repeats reach the no-progress bound (limit 3).
    assert we.developer.run_generation.await_count == 4
    # The persisted trace carries the transition and terminal events.
    import json
    import sqlite3

    from kriya.core.state_paths import trace_db_path
    with sqlite3.connect(trace_db_path(cfg)) as db:
        (events_json,) = db.execute("SELECT run_events FROM runs ORDER BY rowid DESC").fetchone()
    kinds = [event["kind"] for event in json.loads(events_json)]
    assert kinds.count("retry.progress_vector") == 4
    assert "retry.strategy_transition" in kinds
    assert kinds.count("retry.no_progress_terminal") == 1


@pytest.mark.asyncio
async def test_workflow_success_result_carries_an_idle_retry_progress_block(tmp_path):
    from unittest.mock import patch

    from kriya.core.llm import LLMClient
    from kriya.workflow.workflow import WorkflowEngine

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.skills = str(tmp_path / "skills")
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=["Step 1: Write code", "Design: Write math.py"] + ["Review: done"] * 5)
    we = WorkflowEngine(Kernel(config=cfg), llm)
    we.developer.run_generation = AsyncMock(
        return_value=[{"filepath": "math.py", "content": "def add(a,b):\n    return a+b\n"}],
    )
    with patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check",
        return_value={"success": True, "output": "ok"},
    ):
        res = await we.run_generation_workflow(goal="Create math library", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    assert res["retry_progress"]["no_progress_terminated"] is False
    assert res["retry_progress"]["terminal_reason"] is None


def test_a_genuinely_new_vector_after_repeats_restarts_the_counter():
    state = GenerationState()
    stuck = _vector()
    for _ in range(3):
        _record(state, stuck, limit=5)
    assert state.consecutive_no_progress_attempts == 2
    _record(state, _vector(workspace_hash="ws-new"), limit=5)
    assert state.last_progress_classification == PROGRESS
    assert state.consecutive_no_progress_attempts == 0
