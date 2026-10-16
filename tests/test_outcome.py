"""Run outcome construction (full modularization E2): the terminal ``run_generation_workflow`` result and the run's
final traces.db row, on every terminal path, are what they were before the outcome/lifecycle code left
``workflow.py``.

Part 1 (characterization, written and green at d739de2 BEFORE any E2 production edit):

- every engine-driven E1 golden scenario's result section (``WorkflowResult`` fields, or the direct dict) equals
  its committed golden field by field (the byte comparison of the whole form stays in
  ``tests/state_machine/test_sm_kernel_golden.py``; this names the field that differs);
- every ``failure_category`` the terminal ladder can produce from an ``environment_failure`` stop, end to end: a
  real scripted run (``_kernel_golden._scripted``, engine entry) whose compile gate raises the orchestrator stop
  carrying that prefix, asserting the result dict and the final traces.db row. E1 lists static-analysis BLOCKED as
  an uncovered terminal path; its classification is one of these rows. The injection seam is
  ``PolymorphicValidator.run_compile_check`` (not a kernel module, so no kernel patch point is added).

Part 2 (E2, with the move): the moved builders (``outcome.terminal_failure_category``, ``failure_report_dicts``,
``final_review_refusal_payload``, ``completed_result``; ``run_lifecycle.open_run_state``, ``persist_run_trace``)
driven directly with a real ``GenerationState`` and a fully explicit ``StageDeps``, and the dynamic proof that the
moved code logs through the facade's patched ``kriya.workflow.workflow.logger``.
"""
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "state_machine"))
import _kernel_golden as kg  # noqa: E402 - needs the sys.path entry above  # pylint: disable=wrong-import-position

from kriya.core.provider_contract import PROVIDER_PROMPT_TRUNCATED  # noqa: E402
from kriya.core.state_paths import trace_db_path  # noqa: E402
from kriya.workflow.contract_lifecycle import CONTRACT_REGISTRY_CORRUPT  # noqa: E402
from kriya.workflow.failure import Failure, QualityGateFailure  # noqa: E402
from kriya.workflow.file_integrity import (  # noqa: E402
    MIXED_NEWLINE_UNSUPPORTED,
    VERIFICATION_GATE_MUTATED_TRACKED_FILES,
)
from kriya.workflow.requirements import REQUIREMENTS_UNRESOLVED  # noqa: E402

# The E1 scenarios whose terminal result the engine builds (the milestone ones drive a FakeEngine).
ENGINE_SCENARIOS = sorted(name for name in kg.SCENARIOS if not name.startswith("milestone_"))


@pytest.fixture(autouse=True)
def _fixed_git_identity(monkeypatch):
    for name, value in kg.GIT_ENV.items():
        monkeypatch.setenv(name, value)


def _golden(name: str) -> dict:
    return json.loads((kg.GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ENGINE_SCENARIOS)
def test_the_terminal_result_of_every_engine_path_equals_its_golden_field_by_field(name, tmp_path):
    captured = kg.capture(kg.SCENARIOS[name](tmp_path), tmp_path, name)
    produced = json.loads(kg.render({"result": captured["result"]}))["result"]
    golden = _golden(name)["result"]
    assert produced["type"] == golden["type"]
    assert produced.get("fields") == golden.get("fields")
    assert produced["legacy_result"]["keys"] == golden["legacy_result"]["keys"], name
    differing = sorted(key for key in golden["legacy_result"]["values"]
                       if produced["legacy_result"]["values"].get(key) != golden["legacy_result"]["values"][key])
    assert differing == [], f"{name}: result fields differ from the golden: {differing}"
    others = sorted(key for key in golden if key not in ("type", "fields", "legacy_result")
                    and produced.get(key) != golden[key])
    assert others == [], f"{name}: WorkflowResult fields differ from the golden: {others}"


# environment_failure prefix -> the result's failure_category (workflow.py's terminal ladder, read at d739de2).
CATEGORY_BY_STOP = {
    "UNAUTHORIZED_GENERATION_TARGET: injected": "unauthorized_generation_target",
    "NO_AUTHORIZED_REPAIR_TARGET: injected": "unauthorized_generation_target",
    "CANDIDATE_INDEPENDENT_DETERMINISTIC_FAILURE: injected": "candidate_independent_deterministic_failure",
    "GENERATION TIME BUDGET EXHAUSTED: injected": "generation_budget_exhausted",
    "CONTAINMENT_SETUP_FAILED: injected": "containment_setup_failed",
    "REGRESSION_UNATTRIBUTED: injected": "regression_unattributed",
    "FALLBACK_MODEL_INCOMPATIBLE: injected": "fallback_model_incompatible",
    "CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE: injected": "context_edit_protocol_unsatisfiable",
    f"{PROVIDER_PROMPT_TRUNCATED}: injected": "provider_contract_violation",
    "RETRY_INFERENCE_IDENTITY_NOT_QUALIFIED: injected": "retry_identity_not_qualified",
    f"{REQUIREMENTS_UNRESOLVED}: injected": "requirements_unresolved",
    f"{CONTRACT_REGISTRY_CORRUPT}: injected": "contract_registry_blocked",
    CONTRACT_REGISTRY_CORRUPT: "contract_registry_blocked",     # the exact-code form (no detail)
    "STATIC_ANALYSIS_BLOCKED: injected": "static_analysis_blocked",
    "STATIC_ANALYSIS_UNKNOWN: injected": "static_analysis_unknown",
    "STATIC_ANALYSIS_UNAVAILABLE: injected": "static_analysis_unavailable",
    "WORKSPACE_COMMIT_NOT_COMPLETED: injected": "workspace_commit_failed",
    f"{MIXED_NEWLINE_UNSUPPORTED}: injected": "file_integrity_unsupported",
    f"{VERIFICATION_GATE_MUTATED_TRACKED_FILES}: injected": "verification_tree_mutated",
    "TOOLCHAIN_MISSING: injected": "environment_failure",       # no typed prefix: the generic environment stop
}


def _final_row(cfg) -> dict:
    conn = sqlite3.connect(trace_db_path(cfg))
    conn.row_factory = sqlite3.Row
    try:
        return dict(conn.execute("SELECT * FROM runs ORDER BY timestamp DESC LIMIT 1").fetchone())
    finally:
        conn.close()


@pytest.mark.parametrize("stop", sorted(CATEGORY_BY_STOP))
def test_every_environment_stop_reaches_the_result_and_the_trace_row_as_its_category(stop, tmp_path):
    def gate_stops(*_args, **_kwargs):
        raise QualityGateFailure(Failure(type="time_budget_exhausted", source="orchestrator", message=stop, attempt=1))

    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", side_effect=gate_stops):
        outcome = kg._scripted(tmp_path, lambda model, n, text: kg.GOOD, controller=False)
    result = outcome.result
    category = CATEGORY_BY_STOP[stop]
    assert (result["failure_category"], result["environment_failure"], result["quality_gates_passed"]) == (
        category, stop, False)
    assert result["completion_kind"] is None and result["no_progress_reason"] is None
    assert result["last_failure"]["type"] == "time_budget_exhausted" and result["last_failure"]["message"] == stop
    assert "final_review_refusal" not in result
    assert sorted(result) == _golden("direct_success")["result"]["legacy_result"]["keys"]
    row = _final_row(outcome.cfg)
    assert (row["status"], row["failure_category"]) == ("failure", category)
    assert [entry["category"] for entry in json.loads(row["failure_report"] or "[]")] == [
        entry["category"] for entry in result["failure_report"]]
    kinds = [event["kind"] for event in json.loads(row["run_events"] or "[]")]
    assert kinds[:3] == ["egress.authority", "evidence.attempt_store", "model.runtime"], kinds


# ----------------------------------------------------------------------------- part 2 (E2): the moved builders
#
# kriya/workflow/outcome.py and run_lifecycle.py hold what workflow.py built inline until E2. The builders are
# driven with a real GenerationState and a StageDeps whose every field is set explicitly (no bare MagicMock: an
# attribute of a bare double is truthy and would silently take another branch).

def _state(**fields):
    from kriya.workflow.state import GenerationState

    state = GenerationState()
    for name, value in fields.items():
        setattr(state, name, value)
    return state


def _deps(**overrides):
    import logging

    from kriya.config import AppConfig
    from kriya.static_analysis.service import static_analysis_result_fields
    from kriya.workflow.stage_context import StageDeps
    from kriya.workflow.state import GenerationState
    from kriya.workflow.workflow import _build_required_verification_evidence, deterministic_gate_evidence

    async def runtime_details(_cfg):
        return {"fingerprint": "scripted"}

    fields = dict(
        logger=MagicMock(spec=logging.Logger), config=AppConfig(), new_state=GenerationState,
        egress_authority_details=lambda _cfg: {"channels": []}, primary_model_runtime_details=runtime_details,
        role_independence_details=lambda _cfg: {"roles": {}},
        trace_events=lambda state: [event.to_dict() for event in state.run_events],
        static_analysis_fields=static_analysis_result_fields,
        required_verification_evidence=_build_required_verification_evidence,
        deterministic_gate_evidence=deterministic_gate_evidence,
        wall_clock=lambda: 100.0, monotonic=lambda: 50.0,
    )
    fields.update(overrides)
    return StageDeps(**fields)


@pytest.mark.parametrize("stop", sorted(CATEGORY_BY_STOP))
def test_the_failure_category_ladder_names_every_environment_stop(stop):
    from kriya.workflow.outcome import terminal_failure_category

    assert terminal_failure_category(_state(environment_failure=stop)) == CATEGORY_BY_STOP[stop]


def test_the_failure_category_ladder_precedence():
    from kriya.workflow.outcome import terminal_failure_category

    refused = {"reason_code": "FINAL_REVIEW_BACKEND_ERROR", "detail": "d", "batch": 1, "batches": 1}
    conflict = {"files": ["a.py"]}
    stop = "STATIC_ANALYSIS_BLOCKED: x"
    assert terminal_failure_category(_state()) == "quality_gates_exhausted"
    assert terminal_failure_category(_state(no_progress_terminated=True)) == "no_progress"
    # an environment stop outranks the no-progress terminal; a plan-scope conflict outranks the stop; a refused
    # final review outranks everything (the candidate may already be committed)
    assert terminal_failure_category(_state(no_progress_terminated=True, environment_failure=stop)) == \
        "static_analysis_blocked"
    assert terminal_failure_category(_state(plan_scope_conflict=conflict, environment_failure=stop)) == \
        "plan_scope_revision_required"
    assert terminal_failure_category(_state(final_review_refusal=refused, plan_scope_conflict=conflict,
                                            environment_failure=stop)) == "final_review_refused"


def test_the_failure_report_maps_every_gate_outcome_in_order():
    from kriya.workflow.failure_reporting import build_failure_report_entry
    from kriya.workflow.outcome import failure_report_dicts

    outcomes = [{"type": "compile", "attribution_tier": "exact"}, {"type": "time_budget_exhausted"}, {}]
    expected = [build_failure_report_entry(o.get("type", ""), o.get("attribution_tier")) for o in outcomes]
    assert failure_report_dicts(_state(gate_outcomes=outcomes)) == [
        {"failure_type": e.failure_type, "category": e.category.value, "attribution_tier": e.attribution_tier}
        for e in expected]
    assert failure_report_dicts(_state()) == []


def test_the_final_review_refusal_payload_keeps_what_already_happened():
    import logging

    from kriya.workflow.outcome import final_review_refusal_payload

    refusal = {"reason_code": "CONTEXT_BUDGET_UNSATISFIABLE", "detail": "too big", "batch": 2, "batches": 3}
    state = _state(final_review_refusal=refusal, quality_gates_succeeded=True)
    log = MagicMock(spec=logging.Logger)
    with patch("kriya.control.run_coordinator.owning_run_committed_work_units", return_value=["u1"]) as owned:
        assert final_review_refusal_payload(state, "/ws", logger=log) == {
            **refusal, "candidate_applied": True, "committed_work_units": ["u1"], "rolled_back": False}
    owned.assert_called_once_with("/ws")
    log.warning.assert_not_called()
    with patch("kriya.control.run_coordinator.owning_run_committed_work_units", side_effect=OSError("no record")):
        payload = final_review_refusal_payload(_state(final_review_refusal=refusal), "/ws", logger=log)
    assert (payload["candidate_applied"], payload["committed_work_units"]) == (False, None)
    log.warning.assert_called_once_with("Committed work units unavailable: no record")


def _completed(state, deps, **overrides):
    from kriya.workflow.outcome import completed_result

    arguments = dict(quality_passed=False, failure_category="quality_gates_exhausted", failure_report_dicts=[],
                     plan="the plan", design="the design", review="the review", workspace_path="/ws", run_id="r1",
                     required_verification=None, unresolved_skill_gap_names=[], skill_staleness_warnings=[],
                     active_skill_manifest=[], requirement_set=None, resolved_obligation_ledger=None)
    arguments.update(overrides)
    return completed_result(deps, state, **arguments)


def test_the_completed_result_of_a_refused_final_review_carries_the_refusal():
    refusal = {"reason_code": "FINAL_REVIEW_BACKEND_ERROR", "detail": "RuntimeError: x", "batch": 1, "batches": 1}
    state = _state(final_review_refusal=refusal, quality_gates_succeeded=True)
    with patch("kriya.control.run_coordinator.owning_run_committed_work_units", return_value=None):
        result = _completed(state, _deps(), failure_category="final_review_refused")
    golden_keys = _golden("legacy_final_review_refused")["result"]["legacy_result"]["keys"]
    assert sorted(result) == sorted(set(golden_keys) - {"requirements"})   # no requirement set in this unit
    assert result["final_review_refusal"] == {**refusal, "candidate_applied": True, "committed_work_units": None,
                                              "rolled_back": False}
    assert (result["failure_category"], result["quality_gates_passed"]) == ("final_review_refused", False)


def test_the_completed_result_fields_on_a_failed_and_a_passed_run():
    from kriya.workflow.failure import Failure

    last = Failure(type="compile", source="validator", message="m" * 3000, raw_output="o" * 5000, attempt=2)
    failed = _state(environment_failure="TOOLCHAIN_MISSING: x", last_failure=last, no_progress_reason="R",
                    completion_kind="VERIFIED_NO_CHANGE", identical_rewrites={"b.py", "a.py"},
                    all_files_written=["a.py"])
    result = _completed(failed, _deps(), failure_category="environment_failure",
                        failure_report_dicts=[{"failure_type": "compile"}], unresolved_skill_gap_names=["y", "x", "y"])
    golden_keys = _golden("legacy_gates_exhausted")["result"]["legacy_result"]["keys"]
    assert sorted(result) == sorted(set(golden_keys) - {"requirements"})
    assert "final_review_refusal" not in result and "ownership_findings" not in result
    assert result["environment_failure"] == "TOOLCHAIN_MISSING: x" and result["no_progress_reason"] == "R"
    assert result["completion_kind"] is None and result["unchanged_files"] == []
    assert result["last_failure"] == {"type": "compile", "message": "m" * 2000, "raw_output": "o" * 4000}
    assert result["failure_report"] == [{"failure_type": "compile"}]
    assert result["unresolved_skill_gaps"] == ["x", "y"] and result["skill_staleness_warnings"] is None
    assert result["generation_metrics"]["total_wall_seconds"] == 50.0 - failed.generation_started_monotonic
    assert (result["plan"], result["design"], result["review"], result["run_id"], result["files"]) == (
        "the plan", "the design", "the review", "r1", ["a.py"])
    passed = _state(environment_failure="ignored on success", last_failure=last, no_progress_reason="R",
                    completion_kind="VERIFIED_NO_CHANGE", identical_rewrites={"b.py", "a.py"})
    result = _completed(passed, _deps(), quality_passed=True, failure_category=None)
    assert (result["quality_gates_passed"], result["environment_failure"], result["no_progress_reason"],
            result["last_failure"], result["completion_kind"], result["unchanged_files"]) == (
        True, None, None, None, "VERIFIED_NO_CHANGE", ["a.py", "b.py"])


def test_the_run_state_opens_with_its_identity_events_in_order_and_logs_a_missing_one():
    import asyncio

    from kriya.workflow.run_lifecycle import open_run_state

    def unavailable(_cfg):
        raise RuntimeError("egress table unreadable")

    deps = _deps(egress_authority_details=unavailable)
    state = asyncio.run(open_run_state(deps, error_context=None, engineering_route=None, process_profile=None))
    assert [event.kind for event in state.run_events] == [
        "evidence.attempt_store", "model.runtime", "model.role_independence"]
    assert state.error_context == ""
    deps.logger.warning.assert_called_once_with("Could not record the run's egress authority: egress table unreadable")
    complete = asyncio.run(open_run_state(_deps(), error_context="ctx", engineering_route=None, process_profile=None))
    assert [event.kind for event in complete.run_events][:2] == ["egress.authority", "evidence.attempt_store"]
    assert complete.run_events[0].details == {"channels": []} and complete.error_context == "ctx"


def test_the_terminal_trace_row_carries_the_outcome_and_a_trace_failure_is_logged(tmp_path):
    from kriya.workflow.run_lifecycle import RunTrace, TerminalTrace, TraceDetail, persist_run_trace

    deps = _deps()
    state = _state(all_files_written=["a.py"])
    trace = RunTrace(trace_id="t-1", goal="g", started_at=90.0, milestone_group_id="m", milestone_index=1,
                     milestone_total=2, enforce_run_id="e.enforce")
    with patch("kriya.control.trace.TraceLogger.log_run") as log_run:
        persist_run_trace(deps, state, trace, TraceDetail([], ["skill"], "prompt"), status="failure",
                          terminal=TerminalTrace("no_progress", [{"category": "x"}]), recorded_note=True)
        persist_run_trace(deps, state, trace, TraceDetail([], [], "prompt"), status="in_progress",
                          failure_note="pre-Reviewer")
    terminal, checkpoint = (call.kwargs for call in log_run.call_args_list)
    assert list(terminal) == [
        "run_id", "goal", "duration_sec", "attempts", "status", "files_modified", "retrieved_chunks", "active_skills",
        "prompt_rendered", "gate_outcomes", "model_hops", "failure_category", "failure_report", "milestone_group_id",
        "milestone_index", "milestone_total", "enforce_run_id", "run_events", "evidence_records", "generation_metrics"]
    assert (terminal["run_id"], terminal["duration_sec"], terminal["status"], terminal["failure_category"],
            terminal["failure_report"], terminal["files_modified"], terminal["enforce_run_id"]) == (
        "t-1", 10.0, "failure", "no_progress", [{"category": "x"}], ["a.py"], "e.enforce")
    assert "failure_category" not in checkpoint and "failure_report" not in checkpoint
    deps.logger.info.assert_called_once_with("Persistent run trace recorded: t-1")
    with patch("kriya.control.trace.TraceLogger.log_run", side_effect=OSError("disk full")):
        persist_run_trace(deps, state, trace, TraceDetail([], [], ""), status="failure", failure_note="pre-Reviewer")
    deps.logger.warning.assert_called_once_with("pre-Reviewer: disk full")


def test_the_trace_row_counts_the_candidates_best_of_n_discarded_as_attempts():
    """Orchestrator mutant (E2): `attempts=retry_count` alone survived every existing test, including all of test_workflow.py. retry_count is
    reset for each fresh Best-of-N candidate, so the row must add the discarded candidates or it understates the effort of the run."""
    from kriya.workflow.run_lifecycle import RunTrace, TraceDetail, persist_run_trace

    state = _state()
    state.budgets.retry_count, state.budgets.best_of_n_candidates_tried = 1, 2
    with patch("kriya.control.trace.TraceLogger.log_run") as log_run:
        persist_run_trace(_deps(), state, RunTrace("t-b", "g", 0.0), TraceDetail([], [], ""), status="failure")
    assert log_run.call_args.kwargs["attempts"] == 3


def test_the_moved_lifecycle_code_logs_through_the_engines_patched_logger(tmp_path):
    """The E2 guard for ``kriya.workflow.workflow.logger`` (2 suite patches): the facade hands its logger to the moved
    code at call time (``WorkflowEngine._stage_deps`` and the ``_record_run_exception`` forwarder), so a patch of the
    facade's logger still sees what the moved run-lifecycle code logs. E3: the same for the moved design stages
    (``WorkflowEngine._work_unit_deps``): the KnowledgeGuard stage's trace-write warning reaches the patched logger."""
    import asyncio
    import dataclasses

    from kriya.config import AppConfig
    from kriya.core.kernel import Kernel
    from kriya.core.llm import LLMClient
    from kriya.workflow import workflow as workflow_module
    from kriya.workflow.run_lifecycle import RunTrace, TraceDetail, persist_run_trace
    from kriya.workflow.stage_context import StageContext
    from kriya.workflow.stage_knowledge_guard import check_knowledge_guard

    cfg = AppConfig()
    cfg.knowledge.offline_mode = True        # a versioned library with no skill is a gap, decided without a network
    cfg.paths.skills, cfg.paths.memory = str(tmp_path / "skills"), str(tmp_path / "memory")
    engine = workflow_module.WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    with patch("kriya.workflow.workflow.logger") as facade_logger:
        deps = dataclasses.replace(engine._stage_deps(), trace_events=lambda _state: [])
        with patch("kriya.control.trace.TraceLogger.log_run", side_effect=OSError("disk full")):
            persist_run_trace(deps, _state(), RunTrace("t", "g", 0.0), TraceDetail([], [], ""), status="failure")
        workflow_module._record_run_exception(object(), {"trace_id": "t"}, RuntimeError("boom"))
        stage_deps = dataclasses.replace(engine._work_unit_deps(), trace_events=lambda _state: [])
        stage = StageContext(goal="Use mylib==1.2.3.", workspace_path=str(tmp_path), state=_state(), trace_id="t")
        with patch("kriya.control.trace.TraceLogger.log_run", side_effect=OSError("stage disk full")):
            stop = asyncio.run(check_knowledge_guard(stage, stage_deps))
    messages = [call.args[0] for call in facade_logger.warning.call_args_list]
    assert messages[0] == "Failed to write run trace: disk full"
    assert messages[1] == "No traces.db for the failed run %s: %s"
    assert stop["status"] == "knowledge_gap" and messages[2] == "Failed to write run trace: stage disk full"
