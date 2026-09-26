"""MODEL-EVIDENCE-HARDENING-001 final closure: the four residual gaps.

1. A model executes with its own effective settings, so
   qualified inference identity == executed inference identity.
2. Role metrics (and the routing evidence built from them) are keyed by
   inference identity, not by runtime alone.
3. A direct run that raises mid-planning keeps its model calls, once, with
   the exception.
4. Shadow and milestone Planner responses get the same typed outcomes; shadow
   evidence stays out of the Planner's routing bucket."""
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _strict_doubles import strict_config, strict_engine
from openai.resources.chat.completions import AsyncCompletions
from test_prd017_fallback_transition import FALLBACK, PRIMARY, _ctx, _exact_ollama, _fallback_kwargs, _response
from test_prd017_fallback_transition import _cfg as _prd017_cfg

from kriya.agents.agent import DeveloperAgent
from kriya.config import AppConfig
from kriya.core import model_qualification as mq
from kriya.core import model_routing as mr
from kriya.core import role_metrics as rm
from kriya.core.inference_settings import request_settings, role_inference_settings
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.core.model_runtime import resolve_configured_model_runtime
from kriya.core.state_paths import trace_db_path
from kriya.workflow.attempt import _run_developer_generation
from kriya.workflow.model_transition import resolve_request_profile
from kriya.workflow.state import GenerationState
from kriya.workflow.workflow import WorkflowEngine
from kriya.workflow.workflow_controller import SHADOW_PLANNER_ROLE, WorkflowController

FALLBACK_BODY = {"reasoning_effort": "none", "options": {"num_ctx": 8192, "top_p": 0.95, "top_k": 10, "seed": 7}}


def _route():
    from test_workflow_controller import _route as controller_route

    return controller_route()


def _cfg():
    cfg = _prd017_cfg(temperature=0.2, reasoning=True, extra_body=json.loads(json.dumps(FALLBACK_BODY)))
    cfg.llm.temperature = 0.7
    cfg.llm.extra_body = {"options": {"num_ctx": 32768, "top_p": 0.8, "top_k": 20}}
    return cfg


def _rows(trace_db, run_id):
    with sqlite3.connect(trace_db) as db:
        row = db.execute("SELECT status, failure_category, run_events FROM runs WHERE run_id = ?",
                         (run_id,)).fetchone()
    return None if row is None else (row[0], row[1], json.loads(row[2] or "[]"))


# ============================================================ 1. executed == qualified identity

@pytest.mark.asyncio
async def test_a_fallback_executes_with_its_own_settings_not_the_primarys(tmp_path, monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg()
    developer = DeveloperAgent("developer", LLMClient(cfg))
    ctx = _ctx(str(tmp_path), cfg, developer)
    state = GenerationState()
    create = AsyncMock(side_effect=[_response("class A {}\n"), _response("class A {}\n")])
    with patch.object(AsyncCompletions, "create", new=create):
        state.attempt_number = 1
        await _run_developer_generation(state, ctx, task_description="t", design_context="d",
                                        existing_code_context="", known_target_files=["A.java"])
        primary_digest = developer.llm.last_completion.inference_settings_digest
        state.attempt_number = 2
        await _run_developer_generation(state, ctx, known_target_files=["A.java"], **_fallback_kwargs(cfg))
        fallback_digest = developer.llm.last_completion.inference_settings_digest

    primary_sent, fallback_sent = (call.kwargs for call in create.call_args_list)
    assert primary_sent["temperature"] == 0.7 and primary_sent["extra_body"]["options"]["top_k"] == 20
    assert fallback_sent["temperature"] == 0.2
    assert fallback_sent["extra_body"] == FALLBACK_BODY  # reasoning_effort, top_p, top_k, seed: its own
    # The executed identity is the qualified identity, on both models.
    assert primary_digest == role_inference_settings(cfg, "developer", PRIMARY).digest
    assert fallback_digest == role_inference_settings(cfg, "developer", FALLBACK).digest
    assert request_settings(temperature=fallback_sent["temperature"], reasoning=True,
                            extra_body=fallback_sent["extra_body"]).digest == fallback_digest


@pytest.mark.asyncio
async def test_a_model_override_alone_never_inherits_the_primarys_sampling(tmp_path):
    cfg = _cfg()
    llm = LLMClient(cfg)
    create = AsyncMock(return_value=_response("ok"))
    with patch.object(AsyncCompletions, "create", new=create):
        await llm.complete("s", "u", model_override=FALLBACK)
    sent = create.call_args.kwargs
    assert sent["temperature"] == 0.2 and sent["extra_body"] == FALLBACK_BODY


def test_the_qualification_lookup_matches_the_executed_identity_and_follows_changes(monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg()
    fallback = cfg.llm_chain[0]
    runtime = resolve_configured_model_runtime(cfg, FALLBACK)
    executed = role_inference_settings(cfg, "developer", FALLBACK)
    mq.save_record(mq.build_record(runtime, [mq.CaseResult(c, mq.PASS) for c in mq.CAPABILITIES],
                                   settings=executed))
    assert resolve_request_profile(cfg, fallback).qualification == mq.QUALIFIED
    for change in ({"temperature": 0.4}, {"extra_body": {**FALLBACK_BODY, "reasoning_effort": "high"}}):
        changed = _cfg()
        for name, value in change.items():
            setattr(changed.llm_chain[0], name, value)
        assert resolve_request_profile(changed, changed.llm_chain[0]).qualification == mq.MISSING, change


# ============================================================ 2. identity-keyed metrics

@pytest.mark.asyncio
async def test_one_runtime_called_with_different_settings_has_separate_metric_buckets():
    cfg = AppConfig()
    llm = LLMClient(cfg)
    create = AsyncMock(return_value=_response("ok"))
    with patch.object(AsyncCompletions, "create", new=create):
        await llm.complete("s", "u", extra_body_override={"reasoning_effort": "none"})
        await llm.complete("s", "u", extra_body_override={"reasoning_effort": "none"})
        await llm.complete("s", "u")  # default reasoning
        await llm.complete("s", "u", temperature_override=0.1)
    rows = llm.role_metrics.take_unreported()
    assert len({row["runtime_digest"] for row in rows}) == 1
    assert sorted(row["calls"] for row in rows) == [1, 1, 2]
    assert len({row["inference_settings_digest"] for row in rows}) == 3


def test_identical_identities_aggregate_and_different_ones_never_merge():
    def row(settings, calls, schema):
        return {"role": "planner", "model": "qwen3.6", "runtime_digest": "d", "runtime_exact": True,
                "inference_settings_digest": settings, "calls": calls, "schema_failures": schema,
                "latency_seconds": 1.0}

    table = rm.aggregate_role_metrics([("r1", [row("none", 3, 0), row("default", 3, 3)]),
                                       ("r2", [row("none", 2, 0)])])
    by_identity = {r["inference_settings_digest"]: r for r in table["rows"]}
    assert (by_identity["none"]["calls"], by_identity["none"]["schema_failures"]) == (5, 0)
    assert (by_identity["default"]["calls"], by_identity["default"]["schema_failures"]) == (3, 3)
    assert mr.metrics_row(table, "planner", "d", "none")["schema_failures"] == 0
    assert mr.metrics_row(table, "planner", "d", "other") is None
    assert mr.metrics_row(table, "planner", "d", None) is None


def test_a_pre_identity_row_matches_no_candidate():
    table = rm.aggregate_role_metrics([("old", [{"role": "planner", "model": "m", "runtime_digest": "d",
                                                  "runtime_exact": True, "calls": 9}])])
    (only,) = table["rows"]
    assert only["inference_settings_digest"] == rm.UNAVAILABLE_SETTINGS
    assert mr.metrics_row(table, "planner", "d", "sha256:any") is None


def test_routing_never_ranks_on_another_identitys_metrics(tmp_path, monkeypatch):
    from test_prd019_model_routing import _exact_ollama as routing_probe
    from test_prd019_model_routing import _qualify_placed, _routed_settings, _routing_cfg

    routing_probe(monkeypatch)
    cfg = _routing_cfg(tmp_path)
    digest_a = _qualify_placed(cfg, "reviewer", "cand-a")
    digest_b = _qualify_placed(cfg, "reviewer", "cand-b")

    def table(settings_b):
        return rm.aggregate_role_metrics([("r1", [
            {"role": "reviewer", "model": "cand-a", "runtime_digest": digest_a, "runtime_exact": True,
             "inference_settings_digest": _routed_settings(cfg, "reviewer", "cand-a"), "calls": 20,
             "schema_failures": 8, "latency_seconds": 1.0},
            {"role": "reviewer", "model": "cand-b", "runtime_digest": digest_b, "runtime_exact": True,
             "inference_settings_digest": settings_b, "calls": 20, "schema_failures": 0, "latency_seconds": 1.0},
        ])])

    # cand-b's good record belongs to another identity: it is unmeasured, so
    # the measured (worse) cand-a still wins.
    mr.write_table(cfg.model_policy.routing.table_path, table("sha256:other-settings"))
    assert mr.plan_routes(cfg).decisions["reviewer"].model == "cand-a"
    mr.write_table(cfg.model_policy.routing.table_path, table(_routed_settings(cfg, "reviewer", "cand-b")))
    assert mr.plan_routes(cfg).decisions["reviewer"].model == "cand-b"


def test_an_attempt_outcome_is_charged_to_the_identity_that_generated_it():
    metrics = rm.RoleMetrics()
    metrics.record_call(model="m", runtime_digest="d", runtime_exact=True, status="OK", latency_seconds=1,
                        prompt_tokens=1, completion_tokens=1, tokens_estimated=False, role="developer",
                        inference_settings_digest="s1")
    metrics.record_attempt(role="developer", model="m", runtime_digest="d", runtime_exact=True,
                           attempt_number=1, passed=True)
    (row,) = metrics.take_unreported()
    assert (row["inference_settings_digest"], row["calls"], row["attempts_passed"]) == ("s1", 1, 1)


# ============================================================ 3. mid-planning exceptions

@pytest.mark.asyncio
async def test_an_exception_after_a_planner_call_keeps_the_call_and_the_reason(tmp_path):
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    llm = LLMClient(cfg)
    calls = []

    async def complete(*args, **kwargs):
        calls.append(kwargs)
        llm.role_metrics.record_call(model=cfg.llm.model, runtime_digest="d" * 64, runtime_exact=True,
                                     status="OK", latency_seconds=2.0, prompt_tokens=100, completion_tokens=50,
                                     tokens_estimated=False, inference_settings_digest="sha256:s")
        if len(calls) == 1:
            return "1. Update greeting.py to add a DEFAULT_NAME constant."
        raise RuntimeError("Architect backend went away")

    llm.complete = AsyncMock(side_effect=complete)
    we = WorkflowEngine(Kernel(config=cfg), llm)
    with pytest.raises(RuntimeError, match="Architect backend went away"):
        await we.run_generation_workflow(goal="Add DEFAULT_NAME", workspace_path=str(tmp_path),
                                         trace_id_override="exc-run")

    status, category, events = _rows(trace_db_path(cfg), "exc-run.exception")
    assert (status, category) == ("error", "RuntimeError")
    (exception,) = [e for e in events if e["kind"] == "run.exception"]
    assert "Architect backend went away" in exception["details"]["message"]
    rows = [r for e in events if e["kind"] == "model.role_metrics" for r in e["details"]["rows"]]
    assert sum(r["calls"] for r in rows) == len(calls) >= 2
    assert {r["role"] for r in rows} >= {"planner"}
    assert llm.role_metrics.take_unreported() == []  # counted exactly once
    assert _rows(trace_db_path(cfg), "exc-run") is None or _rows(trace_db_path(cfg), "exc-run")[0] != "success"


@pytest.mark.asyncio
async def test_an_enforce_run_that_raises_records_its_row_and_still_raises(tmp_path):
    written = []
    we = strict_engine(strict_config())
    we.kernel = None
    we.engineering_triage.classify = AsyncMock(return_value=_route())
    with patch.object(WorkflowController, "_run_structured_enforce", new=AsyncMock(side_effect=KeyError("bug"))), \
            patch.object(WorkflowController, "_write_enforce_trace",
                         lambda self, run_id, goal, result, started: written.append(result)):
        with pytest.raises(KeyError):
            await WorkflowController(we).execute("goal", str(tmp_path), migration_mode="enforce")
    assert [(r["status"], r["failure_type"]) for r in written] == [("error", "KeyError")]


# ============================================================ 4. shadow and milestone outcomes

def _metrics_llm():
    return SimpleNamespace(role_metrics=rm.RoleMetrics(), last_completion=None, model="qwen3.6")


def _charge(llm, role):
    llm.role_metrics.record_call(model="qwen3.6", runtime_digest="d" * 64, runtime_exact=True, status="OK",
                                 latency_seconds=1.0, prompt_tokens=10, completion_tokens=5, tokens_estimated=False,
                                 role=role, inference_settings_digest="sha256:s")
    llm.last_completion = SimpleNamespace(model="qwen3.6")


@pytest.mark.asyncio
@pytest.mark.parametrize(("parse", "outcome"), [
    ((None, "structured plan JSON block failed schema validation: bad"), "structured_validation_failures"),
    ((None, "no JSON at all"), "structured_malformed"),
])
async def test_shadow_planner_outcomes_are_typed_and_stay_out_of_the_planner_bucket(tmp_path, parse, outcome):
    llm = _metrics_llm()

    async def planner_run(prompt, **kwargs):
        _charge(llm, kwargs.get("metrics_role") or "planner")
        return "plan text"

    we = strict_engine(strict_config(autonomy={"spec_compliance_enabled": False}))
    we.kernel = None
    we.engineering_triage.classify = AsyncMock(return_value=_route())
    we.run_generation_workflow = AsyncMock(return_value={"status": "success", "run_id": "legacy"})
    we.planner = SimpleNamespace(run=AsyncMock(side_effect=planner_run), llm=llm, name="planner")
    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=parse):
        await WorkflowController(we).execute("goal", str(tmp_path), migration_mode="shadow")
    (row,) = llm.role_metrics.take_unreported()
    assert row["role"] == SHADOW_PLANNER_ROLE and row["calls"] == 1 and row[outcome] == 1


@pytest.mark.asyncio
async def test_a_policy_rejected_shadow_plan_is_distinct_from_a_malformed_one(tmp_path):
    from kriya.workflow.plan_validation import PlanValidationResult

    llm = _metrics_llm()

    async def planner_run(prompt, **kwargs):
        _charge(llm, kwargs.get("metrics_role") or "planner")
        return "plan text"

    we = strict_engine(strict_config(autonomy={"spec_compliance_enabled": False}))
    we.kernel = None
    we.engineering_triage.classify = AsyncMock(return_value=_route())
    we.run_generation_workflow = AsyncMock(return_value={"status": "success", "run_id": "legacy"})
    we.planner = SimpleNamespace(run=AsyncMock(side_effect=planner_run), llm=llm, name="planner")
    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(MagicMock(), None)), \
            patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output",
                  return_value=MagicMock()), \
            patch("kriya.workflow.workflow_controller.canonicalize_planned_file_actions",
                  side_effect=lambda plan, ws: (plan, [])), \
            patch("kriya.workflow.workflow_controller.record_plan_created"), \
            patch("kriya.workflow.workflow_controller.validate_plan", new=AsyncMock(return_value=PlanValidationResult(
                valid=False, errors=["route needs an extension point"], reason_codes=["EXTENSION_POINT_REQUIRED"]))):
        await WorkflowController(we).execute("goal", str(tmp_path), migration_mode="shadow")
    (row,) = llm.role_metrics.take_unreported()
    assert (row["structured_policy_rejections"], row["structured_malformed"], row["schema_failures"]) == (1, 0, 0)


def _milestone(mid, **extra):
    from kriya.agents.contracts import AcceptanceCriterion, MilestoneV2

    return MilestoneV2(id=mid, goal=f"do {mid}", acceptance=[AcceptanceCriterion(id=f"{mid}-A1", description="ok")],
                       **extra)


@pytest.mark.asyncio
@pytest.mark.parametrize(("responses", "counters"), [
    ([None], {"structured_malformed": 1}),
    ([[_milestone("M1"), _milestone("M1")], [_milestone("M1"), _milestone("M1")]],
     {"structured_validation_failures": 2}),
    ([[_milestone("M1"), _milestone("M1")], [_milestone("M1")]],
     {"structured_validation_failures": 1, "structured_valid": 1}),
], ids=["malformed", "validation_failures", "repaired"])
async def test_milestone_planner_responses_are_typed_once_each(tmp_path, responses, counters):
    from kriya.workflow.milestones import plan_milestones

    llm = _metrics_llm()
    queue = list(responses)

    async def run_with_milestone_list(prompt, stream_callback=None):
        _charge(llm, "milestone_planner")
        return "raw", queue.pop(0)

    planner = SimpleNamespace(run_with_milestone_list=run_with_milestone_list, llm=llm, name="milestone_planner")
    await plan_milestones(planner, "goal", str(tmp_path), max_planning_attempts=2)
    (row,) = llm.role_metrics.take_unreported()
    assert row["role"] == "milestone_planner" and row["calls"] == len(responses)
    for name, value in counters.items():
        assert row[name] == value, name
    outcomes = sum(row[n] for n in rm.STRUCTURED_OUTCOME_COUNTERS.values())
    assert outcomes == len(responses)  # one outcome per response, never double
