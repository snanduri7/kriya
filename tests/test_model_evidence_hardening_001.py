"""MODEL-EVIDENCE-HARDENING-001: the three telemetry defects MODEL-EVAL-001
exposed (MODEL-QUAL-IDENTITY-001 has its own file).

1. Pre-planning failure telemetry: model calls made before a run fails in
   planning reach a durable traces.db row, exactly once, with the failure.
2. Structured plan outcomes: every Planner response has a typed outcome
   (valid / malformed output / structured plan validation failure / policy
   rejection / other), and a model fault counts against the Planner.
3. Requirement verdicts: every verdict carries a normalized reason code, its
   evidence, the verifier identity and the judged candidate; UNKNOWN is never
   unexplained."""
import json
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _strict_doubles import strict_config, strict_kernel

from kriya.agents.agent import SpecComplianceAgent
from kriya.config import AppConfig
from kriya.core import role_metrics as rm
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.core.model_routing import score_metrics
from kriya.core.state_paths import trace_db_path
from kriya.core.token_budget import ContextBudgetUnsatisfiableError
from kriya.workflow import planner_repair as pr
from kriya.workflow import requirements as rq
from kriya.workflow.obligations import ObligationLedger
from kriya.workflow.planner_repair import STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS
from kriya.workflow.run_trace import write_outcome_trace
from kriya.workflow.triage import (
    ChangeKind,
    EngineeringRoute,
    ExecutionWeight,
    ImpactVector,
    RiskClass,
)
from kriya.workflow.workflow import WorkflowEngine
from kriya.workflow.workflow_controller import WorkflowController, _verify_original_requirements

REPO = Path(__file__).resolve().parents[1]
PLANNER_MODEL = "qwen3.6:35b-a3b-q4_K_M"
CHAIN_MODEL = "qwen3.8:27b"
DIGEST = "d" * 64


def _metrics_llm(model=PLANNER_MODEL):
    return SimpleNamespace(role_metrics=rm.RoleMetrics(), last_completion=None, model=model)


def _call(llm, *, model=PLANNER_MODEL, role="planner", digest=DIGEST, latency=2.5):
    llm.role_metrics.record_call(model=model, runtime_digest=digest, runtime_exact=True, status="OK",
                                 latency_seconds=latency, prompt_tokens=1200, completion_tokens=300,
                                 tokens_estimated=False, role=role)
    llm.last_completion = SimpleNamespace(model=model)


def _rows(trace_db, run_id=None):
    with sqlite3.connect(trace_db) as db:
        query = "SELECT run_id, status, failure_category, run_events FROM runs"
        rows = db.execute(query + (" WHERE run_id = ?" if run_id else "") + " ORDER BY rowid",
                          (run_id,) if run_id else ()).fetchall()
    return [(rid, status, category, json.loads(events or "[]")) for rid, status, category, events in rows]


def _events(events, kind):
    return [event for event in events if event["kind"] == kind]


# ============================================================ structured plan outcomes

@pytest.mark.parametrize(("codes", "valid", "expected"), [
    ([], True, rm.STRUCTURED_VALID),
    (["STRUCTURED_PLAN_PARSE_FAILED"], False, rm.STRUCTURED_MALFORMED),
    (["STRUCTURED_PLAN_SCHEMA_INVALID"], False, rm.STRUCTURED_VALIDATION_FAILURE),
    (["SUBTASK_DEPENDENCY_CYCLE"], False, rm.STRUCTURED_VALIDATION_FAILURE),
    (["EXTENSION_POINT_REQUIRED"], False, rm.STRUCTURED_POLICY_REJECTED),
    (["MISSING_GROUNDED_PRODUCTION_ARTIFACT"], False, rm.STRUCTURED_POLICY_REJECTED),
    (["PLAN_VALIDATION_FAILED"], False, rm.STRUCTURED_OTHER_FAILURE),
    (["A_CODE_NOBODY_MAPPED"], False, rm.STRUCTURED_OTHER_FAILURE),
    # Precedence: a model fault wins over a policy rejection in the same response.
    (["EXTENSION_POINT_REQUIRED", "DUPLICATE_SUBTASK_ID"], False, rm.STRUCTURED_VALIDATION_FAILURE),
    (["STRUCTURED_PLAN_PARSE_FAILED", "STRUCTURED_PLAN_SCHEMA_INVALID"], False, rm.STRUCTURED_MALFORMED),
], ids=["valid", "malformed_json", "schema_invalid", "invalid_structure", "policy_route", "policy_grounded",
        "other_typed", "unmapped", "mixed_invalid_wins", "mixed_malformed_wins"])
def test_planner_outcomes_are_typed(codes, valid, expected):
    assert pr.classify_planner_outcome(codes, valid=valid) == expected


def test_the_model_eval_run3_failure_is_negative_planner_evidence():
    """qwen3.6 run 3: two VERIFICATION_EVIDENCE_PATH_MISSING responses and one
    STRUCTURED_PLAN_SCHEMA_INVALID. Each is a structured plan validation
    failure, so all three count against the Planner's reliability."""
    llm = _metrics_llm()
    for codes in (["VERIFICATION_EVIDENCE_PATH_MISSING"], ["VERIFICATION_EVIDENCE_PATH_MISSING"],
                  ["STRUCTURED_PLAN_SCHEMA_INVALID"]):
        _call(llm)
        pr.record_planner_outcome(SimpleNamespace(llm=llm, name="planner"),
                                  pr.classify_planner_outcome(codes, valid=False))
    (row,) = llm.role_metrics.take_unreported()
    assert row["structured_validation_failures"] == 3 and row["schema_failures"] == 3
    assert score_metrics("planner", row, min_calls=1).failure_rate == 1.0


def test_a_policy_rejection_is_recorded_but_is_not_a_model_fault():
    llm = _metrics_llm()
    _call(llm)
    pr.record_planner_outcome(SimpleNamespace(llm=llm, name="planner"), rm.STRUCTURED_POLICY_REJECTED)
    (row,) = llm.role_metrics.take_unreported()
    assert row["structured_policy_rejections"] == 1 and row["schema_failures"] == 0
    assert score_metrics("planner", row, min_calls=1).failure_rate == 0.0


def test_every_planner_rejection_code_is_classified():
    """Tripwire: a reason code a Planner response can be rejected with that no
    class names fails here (it would silently count as other_failure)."""
    classified = (pr.PLANNER_MALFORMED_OUTPUT_CODES | pr.PLANNER_VALIDATION_FAILURE_CODES
                  | pr.PLANNER_POLICY_REJECTION_CODES | pr.PLANNER_OTHER_FAILURE_CODES)
    emitted = set(re.findall(r'reason_codes\.append\("([A-Z_]+)"\)',
                             (REPO / "kriya/workflow/plan_validation.py").read_text()))
    emitted |= set(re.findall(r'reason_codes=\["([A-Z_]+)"\]',
                              (REPO / "kriya/workflow/planner_validation.py").read_text()))
    source = (REPO / "kriya/workflow/workflow_controller.py").read_text()
    loop = source[source.index("        while True:\n            errors: List[str] = []"):
                  source.index('raise _UnsafeStructuredPlan(\n                    "structured plan remained unsafe')]
    # Per-response codes only (the loop's own ``reason_codes``); the
    # run-level terminal codes go on ``final_reason_codes``.
    emitted |= set(re.findall(r'\breason_codes\.append\("([A-Z_]+)"\)', loop))
    emitted |= {code for issue in ("x", "failed schema validation", "execution_method=tool but no tool_name")
                for code in pr.classify_structured_plan_parse_issue(issue)}
    assert emitted and emitted <= classified, sorted(emitted - classified)


def test_the_four_classes_do_not_overlap():
    classes = (pr.PLANNER_MALFORMED_OUTPUT_CODES, pr.PLANNER_VALIDATION_FAILURE_CODES,
               pr.PLANNER_POLICY_REJECTION_CODES, pr.PLANNER_OTHER_FAILURE_CODES)
    assert sum(len(c) for c in classes) == len(set().union(*classes))


@pytest.mark.parametrize(("classification", "expected"), [
    ("complete", rm.STRUCTURED_VALID),
    ("incomplete_truncated", rm.STRUCTURED_MALFORMED),
    ("schema_invalid", rm.STRUCTURED_VALIDATION_FAILURE),
    ("unauthorized_path", rm.STRUCTURED_VALIDATION_FAILURE),
    ("something_new", rm.STRUCTURED_OTHER_FAILURE),
])
def test_legacy_completeness_classifications_are_typed(classification, expected):
    assert pr.planner_outcome_for_completeness(classification) == expected


def test_an_outcome_is_charged_to_the_model_that_answered():
    """Escalation can answer from a chain model: its runtime row carries the
    outcome, not the role's primary."""
    llm = _metrics_llm()
    _call(llm)
    _call(llm, model=CHAIN_MODEL, digest="c" * 64)
    assert pr.record_planner_outcome(SimpleNamespace(llm=llm, name="planner"),
                                     rm.STRUCTURED_MALFORMED) == CHAIN_MODEL
    rows = {row["model"]: row for row in llm.role_metrics.take_unreported()}
    assert rows[CHAIN_MODEL]["structured_malformed"] == 1 and rows[CHAIN_MODEL]["runtime_digest"] == "c" * 64
    assert rows[PLANNER_MODEL]["structured_malformed"] == 0


def test_no_metrics_means_nothing_is_recorded():
    assert pr.record_planner_outcome(MagicMock(), rm.STRUCTURED_VALID) is None


def test_structured_counters_reach_the_routing_table_and_old_rows_read_zero():
    llm = _metrics_llm()
    _call(llm)
    llm.role_metrics.record_structured_outcome(model=PLANNER_MODEL, outcome=rm.STRUCTURED_VALIDATION_FAILURE,
                                               role="planner")
    fresh = llm.role_metrics.take_unreported()
    old = [{"role": "planner", "model": PLANNER_MODEL, "runtime_digest": DIGEST, "runtime_exact": True,
            "calls": 2, "latency_seconds": 1.0}]  # a row written before these counters existed
    (row,) = rm.aggregate_role_metrics([("run-a", fresh), ("run-b", old)])["rows"]
    assert row["calls"] == 3 and row["structured_validation_failures"] == 1 and row["schema_failures"] == 1
    assert row["structured_policy_rejections"] == 0


def _route():
    return EngineeringRoute(
        kind=ChangeKind.TASK, impact=ImpactVector(), initial_risk_class=RiskClass.LOW,
        current_risk_class=RiskClass.LOW, max_observed_risk_class=RiskClass.LOW,
        execution_weight=ExecutionWeight.LIGHT,
    )


@pytest.mark.asyncio
async def test_enforce_records_one_outcome_per_planner_response_and_writes_the_failure(tmp_path):
    """The real enforce planning loop: three schema-invalid responses (initial
    + two repairs). Each is charged as a structured validation failure, and
    the planning failure reaches the enforce trace writer."""
    llm = _metrics_llm()

    async def planner_run(*args, **kwargs):
        _call(llm)
        return "plan text"

    we = MagicMock()
    we.kernel = None
    we.engineering_triage.classify = AsyncMock(return_value=_route())
    we.planner = SimpleNamespace(run=AsyncMock(side_effect=planner_run), llm=llm, name="planner")
    written = []
    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output",
               return_value=(None, "structured plan JSON block failed schema validation: bad")), \
            patch.object(WorkflowController, "_write_enforce_trace",
                         lambda self, run_id, goal, result, started: written.append(result)):
        result = await WorkflowController(we).execute("goal", str(tmp_path), migration_mode="enforce")

    assert result.legacy_result["failure_type"] == "PLANNING_ERROR"
    (row,) = llm.role_metrics.take_unreported()
    assert row["calls"] == 1 + STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS
    assert row["structured_validation_failures"] == row["calls"] and row["schema_failures"] == row["calls"]
    assert [r["status"] for r in written] == ["needs_review"] and written[0]["failure_type"] == "PLANNING_ERROR"


# ============================================================ pre-planning failure telemetry

def _controller_with_trace(cfg, llm):
    return WorkflowController(SimpleNamespace(kernel=strict_kernel(cfg), developer=SimpleNamespace(llm=llm)))


def test_a_planning_failure_writes_its_calls_status_and_reason_exactly_once():
    cfg = strict_config()
    llm = _metrics_llm()
    for _ in range(3):
        _call(llm)
    failure = {"status": "needs_review", "failure_type": "PLANNING_ERROR", "files": [],
               "reason_codes": ["STRUCTURED_PLAN_SCHEMA_INVALID", "STRUCTURED_PLAN_REPAIR_EXHAUSTED"],
               "plan_repair_attempts": 2, "error": "structured plan remained unsafe"}
    assert _controller_with_trace(cfg, llm)._write_enforce_trace("run-7", "goal", failure, 0.0)

    ((run_id, status, category, events),) = _rows(trace_db_path(cfg))
    assert (run_id, status, category) == ("run-7.enforce", "needs_review", "PLANNING_ERROR")
    (planning,) = _events(events, "planning.failed")
    assert planning["details"]["reason_codes"] == failure["reason_codes"]
    (metrics,) = _events(events, "model.role_metrics")
    (row,) = metrics["details"]["rows"]
    assert (row["role"], row["model"], row["runtime_digest"], row["runtime_exact"]) == (
        "planner", PLANNER_MODEL, DIGEST, True)
    assert (row["calls"], row["prompt_tokens"], row["completion_tokens"]) == (3, 3600, 900)
    assert row["latency_seconds"] == 7.5
    # The routing table sees the calls once...
    (table_row,) = rm.aggregate_role_metrics(rm.runs_with_role_metrics(trace_db_path(cfg)))["rows"]
    assert table_row["calls"] == 3
    # ...and a later normal run's row does not report them again.
    _call(llm, role="developer", model="qwen3-coder:30b", digest="e" * 64)
    write_outcome_trace(trace_db_path(cfg), run_id="run-8", goal="goal", status="success", llm=llm, source="test")
    later = _events(_rows(trace_db_path(cfg), "run-8")[0][3], "model.role_metrics")[0]["details"]["rows"]
    assert [r["role"] for r in later] == ["developer"]
    totals = rm.aggregate_role_metrics(rm.runs_with_role_metrics(trace_db_path(cfg)))["rows"]
    assert {r["role"]: r["calls"] for r in totals} == {"planner": 3, "developer": 1}


def test_the_enforce_row_never_claims_success_it_did_not_have_and_is_not_a_run_record(tmp_path):
    cfg = strict_config()
    _controller_with_trace(cfg, _metrics_llm())._write_enforce_trace(
        "run-9", "goal", {"status": "needs_review", "failure_type": "PLANNING_ERROR"}, 0.0)
    ((_, status, _, _),) = _rows(trace_db_path(cfg))
    assert status == "needs_review"
    assert not list(tmp_path.rglob("run_record*"))


def test_an_enforce_run_without_a_kernel_writes_nothing():
    assert WorkflowController(SimpleNamespace(kernel=None))._write_enforce_trace("r", "g", {}, 0.0) is False


def test_a_trace_write_failure_is_reported_not_raised(tmp_path, caplog):
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    llm = _metrics_llm()
    _call(llm)
    assert write_outcome_trace(str(blocked / "traces.db"), run_id="r", goal="g", status="needs_review",
                               llm=llm, source="test") is False
    assert "Failed to write the test trace row" in caplog.text


@pytest.mark.asyncio
async def test_a_legacy_planner_rejection_row_carries_the_planner_calls_and_outcomes(tmp_path):
    """The direct path: three schema-invalid Planner responses end the run
    with planner_output_schema_invalid; its trace row now carries the role
    metrics, with each response's typed outcome."""
    from test_planner_robust_001_repair import _tool_subtask_missing_toolname_plan

    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    bad_plan = _tool_subtask_missing_toolname_plan(subtask_tool_name=None)

    async def complete(*args, **kwargs):
        llm.role_metrics.record_call(model=cfg.llm.model, runtime_digest=DIGEST, runtime_exact=True,
                                     status="OK", latency_seconds=1.0, prompt_tokens=10, completion_tokens=5,
                                     tokens_estimated=False)
        return bad_plan

    llm.complete = AsyncMock(side_effect=complete)
    we = WorkflowEngine(kernel, llm)
    result = await we.run_generation_workflow(goal="Fix the bug", workspace_path=str(tmp_path))

    assert result["status"] == "planner_output_schema_invalid"
    ((_, status, _, events),) = _rows(trace_db_path(cfg), result["run_id"])
    assert status == "planner_output_schema_invalid"
    rows = [r for e in _events(events, "model.role_metrics") for r in e["details"]["rows"]]
    (planner,) = [r for r in rows if r["role"] == "planner"]
    assert planner["calls"] == 1 + STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS
    assert planner["structured_validation_failures"] == planner["calls"]


@pytest.mark.asyncio
async def test_milestone_planning_reports_its_calls_whatever_the_outcome(tmp_path):
    from kriya.workflow.milestones import plan_milestones

    llm = _metrics_llm()

    async def malformed(prompt, stream_callback=None):
        _call(llm, role="milestone_planner")
        return "not a list", None

    planner = SimpleNamespace(run_with_milestone_list=malformed, llm=llm)
    trace_db = str(tmp_path / "traces.db")
    state, error = await plan_milestones(planner, "goal", str(tmp_path), trace_db=trace_db)
    assert state is None and error
    ((run_id, status, category, events),) = _rows(trace_db)
    assert run_id.endswith(".milestone-plan") and status == "milestone_plan_malformed_output"
    assert category == "milestone_plan_malformed_output"
    (metrics,) = _events(events, "model.role_metrics")
    assert metrics["details"]["rows"][0]["role"] == "milestone_planner"


# ============================================================ requirement verdict diagnostics

GOAL = "Add a DEFAULT_NAME constant.\n- Make the module more robust\n- Keep greet() unchanged\n"


def _reqs():
    return rq.derive_requirements(GOAL)


@pytest.mark.parametrize(("result", "reason"), [
    ({"status": "unknown", "reasoning": "Check call failed: 500", "failure_reason_code": rq.VERIFIER_CALL_FAILED},
     rq.VERIFIER_CALL_FAILED),
    ({"status": "unknown", "reasoning": "Check call failed: budget",
      "failure_reason_code": rq.VERIFIER_REQUEST_REFUSED}, rq.VERIFIER_REQUEST_REFUSED),
    ({"status": "unknown", "reasoning": "Response could not be parsed"}, rq.MALFORMED_VERIFIER_RESULT),
    ({"compliant": True, "requirement_verdicts": "REQ-1 ok"}, rq.MALFORMED_VERIFIER_RESULT),
    ({"compliant": True}, rq.MODEL_RETURNED_NO_VERDICT),
    ({"compliant": True, "requirement_verdicts": []}, rq.MODEL_RETURNED_NO_VERDICT),
], ids=["call_failed", "request_refused", "unparseable", "verdicts_not_a_list", "no_verdicts_key", "empty_list"])
def test_every_unknown_says_why(result, reason):
    ledger = ObligationLedger()
    verdicts, _, missing_reason, missing_detail = rq.verifier_result_verdicts(result, _reqs())
    rq.record_requirement_verdicts(ledger, _reqs(), verdicts, revision="terminal", evidence_fingerprint="fp",
                                   source="test", missing_reason=missing_reason, missing_detail=missing_detail)
    details = rq.requirement_verdict_details(ledger, _reqs())
    assert {d["outcome"] for d in details.values()} == {"unknown"}
    assert {d["reason_code"] for d in details.values()} == {reason}
    assert all(d["detail"] for d in details.values())


def test_malformed_no_verdict_and_genuine_insufficient_evidence_are_distinguishable():
    reqs = _reqs()
    result = {"compliant": True, "verifier": {"model": "qwen3-coder:30b", "runtime_fingerprint": DIGEST,
                                              "runtime_exact": True},
              "requirement_verdicts": [
                  {"id": "REQ-1", "verdict": "satisfied", "evidence": "greeting.py DEFAULT_NAME"},
                  {"id": "REQ-2", "verdict": "unverifiable", "evidence": "robustness is behavioural"},
                  {"id": "REQ-3", "verdict": "probably"},
              ]}
    ledger = ObligationLedger()
    verdicts, findings, missing_reason, missing_detail = rq.verifier_result_verdicts(result, reqs)
    rq.record_requirement_verdicts(ledger, reqs, verdicts, revision=2, evidence_fingerprint="cand-1",
                                   source="test", missing_reason=missing_reason, missing_detail=missing_detail,
                                   verifier=result["verifier"])
    details = rq.requirement_verdict_details(ledger, reqs)
    assert (details["REQ-1"]["outcome"], details["REQ-1"]["reason_code"]) == ("satisfied", rq.VERIFIER_CONFIRMED)
    assert (details["REQ-2"]["outcome"], details["REQ-2"]["reason_code"]) == (
        "unverified", rq.INSUFFICIENT_CODE_EVIDENCE)
    assert details["REQ-2"]["detail"] == "robustness is behavioural"
    assert (details["REQ-3"]["outcome"], details["REQ-3"]["reason_code"]) == ("unknown", rq.MALFORMED_VERIFIER_RESULT)
    for detail in details.values():
        assert detail["verifier"]["runtime_fingerprint"] == DIGEST
        assert (detail["evidence_id"], detail["revision"]) == ("cand-1", 2)
    assert findings


def test_a_missing_claim_about_general_prose_and_a_suppressed_claim_say_so():
    reqs = _reqs()
    verdicts, _ = rq.parse_requirement_verdicts([{"id": "REQ-2", "verdict": "missing"}], reqs)
    assert verdicts["REQ-2"] == (rq.RequirementOutcome.UNVERIFIED, "", rq.MISSING_CLAIM_NOT_CONCRETE)
    ledger = ObligationLedger()
    rq.record_requirement_verdicts(ledger, reqs, {"REQ-1": (rq.RequirementOutcome.UNVERIFIED, "suppressed",
                                                            rq.CLAIM_CONTRADICTS_STRONGER_AUTHORITY)},
                                   revision=1, evidence_fingerprint="fp", source="test", only=["REQ-1"])
    assert rq.requirement_verdict_details(ledger, reqs)["REQ-1"]["reason_code"] == (
        rq.CLAIM_CONTRADICTS_STRONGER_AUTHORITY)


def test_a_requirement_without_any_verdict_is_pending_not_yet_verified():
    details = rq.requirement_verdict_details(ObligationLedger(), _reqs())
    assert {(d["outcome"], d["reason_code"]) for d in details.values()} == {("pending", rq.NOT_YET_VERIFIED)}


def test_a_two_element_verdict_still_gets_its_outcomes_default_reason():
    ledger = ObligationLedger()
    rq.record_requirement_verdicts(ledger, _reqs(), {"REQ-1": (rq.RequirementOutcome.VIOLATED, "absent")},
                                   revision=1, evidence_fingerprint="fp", source="test", only=["REQ-1"])
    assert rq.requirement_verdict_details(ledger, _reqs())["REQ-1"]["reason_code"] == rq.VERIFIER_REPORTED_MISSING


def _spec_agent():
    agent = SpecComplianceAgent("spec_compliance", MagicMock())
    agent.llm = SimpleNamespace(last_completion=SimpleNamespace(
        model="qwen3-coder:30b", runtime_fingerprint=DIGEST, runtime_fingerprint_exact=True))
    return agent


def _refusal():
    decision = SimpleNamespace(prompt_tokens=90000, counting_method="default", min_output_tokens=256,
                               reasoning_allowance=0, context_window=32768, window_source="served_num_ctx",
                               context_expanded=False)
    return ContextBudgetUnsatisfiableError(decision)


@pytest.mark.asyncio
@pytest.mark.parametrize(("behaviour", "reason"), [
    (RuntimeError("Error code: 500"), rq.VERIFIER_CALL_FAILED),
    ("refused", rq.VERIFIER_REQUEST_REFUSED),
    ("not json {", rq.MALFORMED_VERIFIER_RESULT),
    ('["a list"]', rq.MALFORMED_VERIFIER_RESULT),
], ids=["call_failed", "budget_refused", "unparseable", "not_an_object"])
async def test_the_verifier_names_why_it_has_no_verdict_and_who_judged(behaviour, reason):
    agent = _spec_agent()

    async def escalation(*args, **kwargs):
        if behaviour == "refused":
            raise _refusal()
        if isinstance(behaviour, Exception):
            raise behaviour
        return behaviour

    with patch("kriya.agents.agent.call_with_escalation", new=escalation):
        result = await agent.check("goal", ["a.py"], {"a.py": "x = 1"}, requirements=_reqs())
    assert result["status"] == "unknown" and result["compliant"] is True  # fail-open kept
    assert result["failure_reason_code"] == reason
    assert result["verifier"] == {"model": "qwen3-coder:30b", "runtime_fingerprint": DIGEST, "runtime_exact": True}


@pytest.mark.asyncio
async def test_a_normal_verdict_names_the_verifier():
    agent = _spec_agent()

    async def escalation(*args, **kwargs):
        return json.dumps({"compliant": True, "reasoning": "ok", "missing_requirements": [],
                           "requirement_verdicts": [{"id": "REQ-1", "verdict": "satisfied"}]})

    with patch("kriya.agents.agent.call_with_escalation", new=escalation):
        result = await agent.check("goal", ["a.py"], {"a.py": "DEFAULT_NAME = 1"}, requirements=_reqs())
    assert "status" not in result and result["verifier"]["runtime_fingerprint"] == DIGEST


@pytest.mark.asyncio
async def test_enforce_verdict_reasons_survive_into_the_ledger_result_and_trace(tmp_path):
    """The enforce terminal verifier with an all-UNKNOWN answer (the
    MODEL-EVAL-001 qwen3.6 run-1 shape): each requirement's reason survives
    into the result's requirement verdicts and the enforce trace row."""
    (tmp_path / "a.py").write_text("DEFAULT_NAME = 1\n")
    reqs = _reqs()
    ledger = ObligationLedger()
    spec = SimpleNamespace(check=AsyncMock(return_value={
        "compliant": True, "reasoning": "looks fine", "missing_requirements": [],
        "verifier": {"model": "qwen3.6:35b-a3b-q4_K_M", "runtime_fingerprint": DIGEST, "runtime_exact": True}}))
    await _verify_original_requirements(spec, reqs, GOAL, str(tmp_path), ["a.py"], ledger)
    verdicts = rq.requirement_verdict_details(ledger, reqs)
    assert {(v["outcome"], v["reason_code"]) for v in verdicts.values()} == {
        ("unknown", rq.MODEL_RETURNED_NO_VERDICT)}
    assert all(v["verifier"]["model"] == "qwen3.6:35b-a3b-q4_K_M" and v["evidence_id"] for v in verdicts.values())

    cfg = strict_config()
    result = {"status": "needs_review", "files": ["a.py"], "reason_codes": ["REQUIREMENTS_UNRESOLVED"],
              "requirements": {**reqs.to_dict(), "outcomes": {rid: "unknown" for rid in reqs.ids},
                               "verdicts": verdicts}}
    _controller_with_trace(cfg, _metrics_llm())._write_enforce_trace("run-1", GOAL, result, 0.0)
    ((_, _, _, events),) = _rows(trace_db_path(cfg))
    (event,) = _events(events, "requirement.verdicts")
    assert {v["reason_code"] for v in event["details"]["verdicts"].values()} == {rq.MODEL_RETURNED_NO_VERDICT}
    assert "REQ-1=unknown(MODEL_RETURNED_NO_VERDICT)" in event["message"]


def test_direct_path_verdict_event_carries_the_reasons():
    from kriya.workflow.attempt import _record_original_requirement_verdicts

    reqs = _reqs()
    events = []
    state = SimpleNamespace(attempt_number=1, record_event=events.append)
    ctx = SimpleNamespace(requirement_set=reqs, obligation_ledger=ObligationLedger())
    _record_original_requirement_verdicts(state, ctx, {
        "status": "unknown", "reasoning": "Check call failed: timeout",
        "failure_reason_code": rq.VERIFIER_CALL_FAILED, "verifier": {"model": "m"}}, "cand-1")
    (event,) = events
    assert {v["reason_code"] for v in event.details["verdicts"].values()} == {rq.VERIFIER_CALL_FAILED}
    assert all(v["detail"] == "Check call failed: timeout" for v in event.details["verdicts"].values())
