"""ATTRIBUTION-MODEL-CLAIM-HARD-STOP-001 (K-C; Scenario-A run 6 consumer trace, owner ruling 2026-11-11, CONFIRMED P1).

Root invariant: a MODEL_CLAIMED / judge / self-diagnosis attribution is advisory evidence and never makes an
authoritative stop decision by itself.

MEASURED before the fix (kc_probe_model_claimed_attribution_stops_unit.jsonl): in a mutating (ALLOWLIST) unit an
attribution of tier triage (confidence high OR low), judge or self_diagnosis that named only a file outside the unit's
write scope ended the unit with ``NO_AUTHORIZED_REPAIR_TARGET: this failure is grounded to [...]``. In run 6 the triage
call named ProtocolParser.java with confidence "high" and was wrong.

Now: only a DETERMINISTIC_ATTRIBUTION_TIERS attribution may make that stop. A model-claimed out-of-scope file is dropped
from the targets (never a target, never write scope), kept as a rejected target, and the unit keeps its ordinary retry.
Deterministic evidence keeps its own typed stop (the plan-scope escalation), unchanged.

``handle_attempt_failure`` through the real attempt-evidence store; only ``attribute_failure`` is scripted, except where
a test says it runs the real one.
"""
import ast
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from test_lr_r1_m1_reason_codes import _recorded
from test_workflow import _minimal_attempt_ctx

from kriya.policy.filesystem import WriteScopeMode
from kriya.workflow import retry_strategy
from kriya.workflow.attribution import DETERMINISTIC_ATTRIBUTION_TIERS, AttributionResult
from kriya.workflow.diagnosis_codes import NO_AUTHORIZED_REPAIR_TARGET
from kriya.workflow.failure import Failure, QualityGateFailure
from kriya.workflow.retry_strategy import handle_attempt_failure
from kriya.workflow.state import GenerationState

APP = "src/main/java/com/example/App.java"                 # the unit's own file (run 6: s5)
PARSER = "src/main/java/com/example/ProtocolParser.java"   # another unit's file (run 6: the triage's wrong answer)
RUNTIME_OUTPUT = "[RESULT] protocolVersion=1, equals=false\n[VERIFICATION] FAIL: Protocols are not equal"
MODEL_TIERS = [("triage", "high"), ("triage", "low"), ("triage", "medium"), ("judge", "medium"), ("judge", "high"), ("self_diagnosis", "high")]


def _unit(tmp_path, *, scope=(APP,)):
    state = GenerationState()
    state.attempt_number = 1
    state.last_attempt_mode = "full_set"
    state.all_files_written = set(scope)
    ctx = _minimal_attempt_ctx(tmp_path, max_retries=4)
    ctx.allowed_write_relpaths = list(scope)
    ctx.write_scope_mode = WriteScopeMode.ALLOWLIST
    return state, ctx


def _failure(failure_type="run_verification"):
    return QualityGateFailure(Failure(type=failure_type, message="[VERIFICATION] FAIL: Protocols are not equal", raw_output=RUNTIME_OUTPUT))


def _handle(tmp_path, monkeypatch, run_id, state, ctx, exc, attribution):
    async def body():
        with patch("kriya.workflow.retry_strategy.attribute_failure", AsyncMock(return_value=attribution)):
            return await handle_attempt_failure(state, ctx, exc)
    return _recorded(tmp_path, monkeypatch, run_id, body)


# ---- a model claim alone never stops the unit ----

@pytest.mark.parametrize("tier,confidence", MODEL_TIERS)
def test_a_model_claimed_out_of_scope_file_never_stops_the_unit_whatever_its_confidence(tmp_path, monkeypatch, tier, confidence):
    assert tier not in DETERMINISTIC_ATTRIBUTION_TIERS
    state, ctx = _unit(tmp_path)
    attribution = AttributionResult(tier=tier, files=[PARSER], confidence=confidence, reasoning="the model's claim")
    should_break, decisions = _handle(tmp_path, monkeypatch, f"run-{tier}-{confidence}", state, ctx, _failure(), attribution)
    assert should_break is False                                   # the ordinary retry path goes on
    assert state.environment_failure is None and state.stop_reason_evidence is None
    assert state.plan_scope_conflict is None                       # no owner routing from a model claim either
    assert decisions[-1]["stop_reason_code"] is None and decisions[-1]["retry"] is True and decisions[-1]["stop_loop"] is False
    assert decisions[-1]["action"] == "full_set"                   # from the remaining evidence: the unit's own files
    # the claim is discarded as a target and kept as advisory evidence
    assert state.last_implicated_files is None
    assert state.rejected_generation_targets == [PARSER]
    assert state.last_failure.likely_files == [PARSER] and state.last_failure.attribution_tier == tier
    assert ctx.allowed_write_relpaths == [APP] and ctx.write_scope_mode == WriteScopeMode.ALLOWLIST   # scope not widened


def test_the_measured_run6_shape_no_longer_reports_a_model_claim_as_grounded(tmp_path, monkeypatch):
    """Negative control on the exact pre-fix reproducer (the K-C probe's first row)."""
    state, ctx = _unit(tmp_path)
    attribution = AttributionResult(tier="triage", files=[PARSER], confidence="high", reasoning="ProtocolParser.decode has a critical bug")
    should_break, decisions = _handle(tmp_path, monkeypatch, "run-run6-shape", state, ctx, _failure(), attribution)
    assert should_break is False
    assert NO_AUTHORIZED_REPAIR_TARGET not in str(state.environment_failure) and "grounded to" not in str(state.environment_failure)
    assert all(decision.get("stop_reason_code") != NO_AUTHORIZED_REPAIR_TARGET for decision in decisions)


def test_a_mixed_model_claim_keeps_only_the_in_scope_file_as_a_target_and_widens_nothing(tmp_path, monkeypatch):
    state, ctx = _unit(tmp_path)
    attribution = AttributionResult(tier="triage", files=[APP, PARSER], confidence="high", reasoning="both")
    should_break, decisions = _handle(tmp_path, monkeypatch, "run-mixed", state, ctx, _failure(), attribution)
    assert should_break is False and state.environment_failure is None and state.plan_scope_conflict is None
    assert state.last_implicated_files == [APP]                    # the out-of-scope file is never a target
    assert state.rejected_generation_targets == [PARSER]
    assert decisions[-1]["action"] == "targeted" and ctx.allowed_write_relpaths == [APP]


def test_no_attribution_at_all_keeps_the_ordinary_full_set_retry(tmp_path, monkeypatch):
    state, ctx = _unit(tmp_path)
    attribution = AttributionResult(tier="full_set", files=[], confidence="low", reasoning="nothing")
    should_break, decisions = _handle(tmp_path, monkeypatch, "run-full-set", state, ctx, _failure(), attribution)
    assert should_break is False and state.environment_failure is None and state.plan_scope_conflict is None
    assert state.last_implicated_files is None and state.rejected_generation_targets == []
    assert decisions[-1]["action"] == "full_set" and decisions[-1]["retry"] is True


# ---- deterministic evidence keeps its authority ----

@pytest.mark.parametrize("tier", sorted(DETERMINISTIC_ATTRIBUTION_TIERS))
def test_a_deterministic_out_of_scope_attribution_still_hard_stops_the_unit_typed(tmp_path, monkeypatch, tier):
    """The genuine authoritative 'no legal target in this unit' stop: the plan-scope escalation, unchanged."""
    state, ctx = _unit(tmp_path)
    attribution = AttributionResult(tier=tier, files=[PARSER], confidence="high", reasoning="deterministic evidence")
    should_break, decisions = _handle(tmp_path, monkeypatch, f"run-det-{tier}", state, ctx, _failure("compile"), attribution)
    assert should_break is True
    conflict = state.plan_scope_conflict
    assert conflict["reason_code"] == "PLAN_SCOPE_REVISION_REQUIRED" and conflict["classification"] == "PLAN_SCOPE_DEFECT"
    assert conflict["required_files"] == [PARSER] and conflict["allowed_files"] == [APP] and conflict["attribution_tier"] == tier
    assert decisions[-1]["stop_loop"] is True and decisions[-1]["retry"] is False
    assert decisions[-1]["plan_scope_conflict"]["required_files"] == [PARSER]
    assert state.last_implicated_files is None and ctx.allowed_write_relpaths == [APP]


def test_confidence_is_never_what_separates_a_stop_from_a_retry(tmp_path, monkeypatch):
    """The same file, the same confidence word: the tier alone decides (a locator may be 'medium', a triage 'high')."""
    outcomes = {}
    for tier in ("triage", "subtask_dependency"):
        (tmp_path / tier).mkdir()
        state, ctx = _unit(tmp_path / tier)
        attribution = AttributionResult(tier=tier, files=[PARSER], confidence="high", reasoning="r")
        outcomes[tier] = _handle(tmp_path / tier, monkeypatch, f"run-conf-{tier}", state, ctx, _failure("compile"), attribution)[0]
    assert outcomes == {"triage": False, "subtask_dependency": True}


def test_a_model_claim_beside_a_deterministic_locator_follows_the_locator(tmp_path, monkeypatch):
    """The REAL attribute_failure: the failure carries a model-supplied likely file (judge evidence) AND the compiler's own
    file:line for another file. The deterministic locator is the attribution; the stop names its file, not the claim's."""
    protocol = "src/main/java/com/example/Protocol.java"
    state, ctx = _unit(tmp_path)
    ctx.established_files = [PARSER, protocol]
    for path in (APP, PARSER, protocol):
        target = Path(ctx.worktree_path) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("package com.example;\n", encoding="utf-8")
    javac = f"[ERROR] /kriya/workspace/{PARSER}:[30,44] cannot find symbol\n  symbol: class Missing\n[INFO] 1 error"
    exc = QualityGateFailure(Failure(type="compile", message="COMPILATION ERROR", raw_output=javac, likely_files=[protocol]))

    async def body():
        return await handle_attempt_failure(state, ctx, exc)
    should_break, decisions = _recorded(tmp_path, monkeypatch, "run-corroborated", body)
    assert state.last_attribution.tier == "locator" and state.last_attribution.files == [PARSER]
    assert should_break is True and state.plan_scope_conflict["required_files"] == [PARSER]
    assert protocol not in state.plan_scope_conflict["required_files"]
    assert state.plan_scope_conflict["attribution_tier"] == "locator"
    assert decisions[-1]["stop_loop"] is True and decisions[-1]["plan_scope_conflict"]["required_files"] == [PARSER]


# ---- the gate itself ----

def test_the_hard_stop_admission_reads_the_deterministic_tier_set_and_no_confidence():
    """Static pin: the one branch that sets NO_AUTHORIZED_REPAIR_TARGET tests the tier against the shared set, and the
    function never compares an attribution's confidence."""
    source = inspect.getsource(retry_strategy)

    def names_the_code(node):
        return any(isinstance(inner, ast.Name) and inner.id == "NO_AUTHORIZED_REPAIR_TARGET" for inner in ast.walk(node))

    # the innermost `if` whose body assigns the stop: no nested `if` inside it names the code as well
    stops = [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.If) and any(names_the_code(stmt) for stmt in node.body)
             and not any(isinstance(inner, ast.If) and inner is not node and names_the_code(inner) for inner in ast.walk(node))]
    assert len(stops) == 1
    condition = ast.unparse(stops[0].test)
    assert "attribution.tier in DETERMINISTIC_ATTRIBUTION_TIERS" in condition
    assert "state.plan_scope_conflict is None" in condition and "confidence" not in condition
    handler = inspect.getsource(retry_strategy.handle_attempt_failure)
    assert ".confidence ==" not in handler and ".confidence in" not in handler and "confidence >=" not in handler
