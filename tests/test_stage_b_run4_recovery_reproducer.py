"""Stage B run 4 (20261118T111000-acc9061c), scripted and model-free, through the real enforce controller:

    verification-only unit fails  ->  the plan's owner is reopened with the gate's evidence
    ->  the primary refuses the reopened Developer request before any provider request (CONTEXT_BUDGET_UNSATISFIABLE)
    ->  the fallback is resolved and admitted (or refused: MODEL_BINDING_NOT_QUALIFIED)
    ->  no candidate is offered the same request twice.

MEASURED in the live run: five attempts of the reopened owner, no Developer request sent, five retry charges, "all
applicable retry budgets are exhausted". Here the three counters are pinned separately: work grants (S8, one per
attempt, kept), provider requests, retry-family charges (MODEL-CANDIDATE-UNAVAILABLE-001).

The refusals are raised by the real refusal types at the client's own pre-provider hooks (``_dispatch_budget`` /
``_admit``, both before ``_request_once``), for the reopened Developer request only; everything after that - the
Developer agent, the attempt, the recovery coordinator, the retry policy, fallback selection, the owner fold and the
verification rerun - is the product's.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from _protocol_responses import sentinel
from test_backend_final_closure_005_verification_owner_reopen import (
    BASE_SOURCE,
    FAILING,
    FIXED_SOURCE,
    PASSING,
    SERVICE,
    WRONG_SOURCE,
    _git,
    _plan,
)

import kriya.workflow.attempt as attempt_module
import kriya.workflow.retry_strategy as retry_strategy_module
from kriya.config import AppConfig
from kriya.config.config import FallbackModelConfig
from kriya.core import model_admission as admission
from kriya.core import model_runtime
from kriya.core import token_budget as tb
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow.state import GenerationState
from kriya.workflow.workflow import WorkflowEngine

PRIMARY, FALLBACK = "dev-model", "fallback-model"
RECOVERY = "VERIFICATION FAILURE RECOVERY"


def _context_refusal():
    with pytest.raises(tb.ContextBudgetUnsatisfiableError) as refused:   # run 4's arithmetic: 31601 + 1024 + 256 > 32768
        tb.plan_dispatch(
            messages=[{"role": "system", "content": "s" * 22}, {"role": "user", "content": "u" * 78899}],
            requested_max_tokens=16384, context_window=32768, window_source="served_observed")
    return refused.value


def _admission_refusal(model):
    return admission.ModelAdmissionError(
        f"{admission.MODEL_BINDING_NOT_QUALIFIED}: the production runtime profile dispatches only a QUALIFIED identity; "
        f"{model} as called by developer is MISSING. Nothing was sent to the model.",
        {"reason_code": admission.MODEL_BINDING_NOT_QUALIFIED, "role": "developer", "model": model, "status": "MISSING"})


def _run(tmp_path, *, fallback_admitted: bool):
    from kriya.workflow.workflow_controller import WorkflowController

    model_runtime.clear_model_runtime_cache()
    cfg = AppConfig()
    cfg.llm.model = PRIMARY
    cfg.llm.context_window = 32768
    cfg.llm.extra_body = {}
    # The run 4 fallback shape: its window declared once, provider-neutrally, with no extra_body.
    cfg.llm_chain = [FallbackModelConfig(model=FALLBACK, base_url=cfg.llm.base_url, context_window=32768,
                                         capabilities=cfg.llm.capabilities.model_copy())]
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.spec_compliance_enabled = False
    cfg.paths.skills = str(tmp_path / "skills")
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.logging.file_enabled = False
    cfg.logging.run_file_enabled = False
    workspace = tmp_path / "ws"
    (workspace / "shop").mkdir(parents=True)
    (workspace / "shop/__init__.py").write_text("")
    (workspace / SERVICE).write_text(BASE_SOURCE)
    _git(workspace, "init", "-q")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-qm", "base")

    active = {}
    seen = {"provider": [], "refused": [], "grants": [], "charges": [], "events": [], "test_gates": []}

    def _reopened_developer(messages):
        text = "\n".join(str(message.get("content") or "") for message in messages)
        return "Developer Agent" in text.splitlines()[0] and RECOVERY in text

    real_budget, real_admit = LLMClient._dispatch_budget, LLMClient._admit

    def dispatch_budget(self, *, model, messages, **kwargs):
        if model == PRIMARY and _reopened_developer(messages):
            refusal = _context_refusal()
            refusal.model = model            # as LLMClient._dispatch_budget names the refused candidate
            seen["refused"].append((model, refusal.reason_code))
            raise refusal
        return real_budget(self, model=model, messages=messages, **kwargs)

    def admit(self, model, fingerprint, settings):
        if model == FALLBACK and not fallback_admitted and active.get("reopened"):
            seen["refused"].append((model, admission.MODEL_BINDING_NOT_QUALIFIED))
            raise _admission_refusal(model)
        return real_admit(self, model, fingerprint, settings)

    async def transport(llm, client, model, system_prompt, user_prompt, *args, **kwargs):
        del llm, client, args, kwargs
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        if "File List Planner" in first:
            content = '{"files": ["%s"]}' % SERVICE
        elif "Developer Agent" in first:
            reopened = RECOVERY in ((system_prompt or "") + (user_prompt or ""))
            seen["provider"].append((active.get("id"), model, "reopened" if reopened else "planned"))
            content = sentinel(SERVICE, analysis="introduce PAGE_SIZE.", content=FIXED_SOURCE if reopened else WRONG_SOURCE)
        else:
            content = "Review: Approved"
        tokens = len(((system_prompt or "") + (user_prompt or "")).encode()) * 2 // 7
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": tokens, "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}

    real_workflow = WorkflowEngine.run_generation_workflow

    async def run_unit(self, *args, **kwargs):
        active["id"] = kwargs.get("current_subtask_id")
        active["reopened"] = "role=verification_owner_recovery" in str(kwargs.get("execution_scope", ""))
        try:
            return await real_workflow(self, *args, **kwargs)
        finally:
            active["reopened"] = False

    def run_tests(self, *_args, **_kwargs):
        import os

        seen["test_gates"].append(active.get("id"))
        if active.get("id") != "s2":
            return PASSING
        current = open(os.path.join(self.workspace_path, SERVICE), encoding="utf-8").read()
        return PASSING if current == FIXED_SOURCE else FAILING

    real_record, real_grant, real_charge = (GenerationState.record_event, attempt_module.grant_developer_attempt,
                                            retry_strategy_module.charge_failed_attempt)

    def record(state, event):
        seen["events"].append((active.get("id"), bool(active.get("reopened")), event))
        return real_record(state, event)

    def grant(required, unit):
        if active.get("reopened"):
            seen["grants"].append(unit)
        return real_grant(required, unit)

    def charge(budgets, **kwargs):
        if active.get("reopened"):
            seen["charges"].append(kwargs.get("candidate_unavailable"))
        return real_charge(budgets, **kwargs)

    engine = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    engine.planner.run = AsyncMock(return_value="structured plan")
    with patch.object(LLMClient, "_request_once", new=transport), \
         patch.object(LLMClient, "_dispatch_budget", new=dispatch_budget), \
         patch.object(LLMClient, "_admit", new=admit), \
         patch.object(GenerationState, "record_event", new=record), \
         patch.object(WorkflowEngine, "run_generation_workflow", new=run_unit), \
         patch.object(attempt_module, "grant_developer_attempt", new=grant), \
         patch.object(retry_strategy_module, "charge_failed_attempt", new=charge), \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", new=lambda *_a, **_k: {"success": True, "output": "ok"}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", new=run_tests), \
         patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(object(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", return_value=_plan()):
        result = asyncio.run(WorkflowController(engine).execute("Make the page size configurable", str(workspace),
                                                                migration_mode="enforce"))
    return workspace, result, seen


def _reopened_events(seen, kind):
    return [event for _unit, reopened, event in seen["events"] if reopened and event.kind == kind]


@pytest.mark.usefixtures("requirements_closed")   # S6 (C-02): this test is not about requirement truth; see tests/_requirement_truth.py
def test_run4_sequence_recovers_on_the_fallback_after_one_uncharged_primary_refusal(tmp_path):
    workspace, result, seen = _run(tmp_path, fallback_admitted=True)
    status = {r.subtask_id: r.status.value for r in result.subtask_results}
    assert status == {"s1": "completed", "s2": "completed"}, (status, result.legacy_result.get("environment_failure"))
    assert (workspace / SERVICE).read_text() == FIXED_SOURCE
    assert result.legacy_result["status"] == "success"
    # verification-only failure -> the plan's owner reopened -> verification rerun on the repaired tree
    assert seen["test_gates"][:2] == ["s1", "s2"] and seen["test_gates"].count("s2") >= 2
    # the primary refused the reopened request exactly once, before the provider; it was never asked again
    assert seen["refused"] == [(PRIMARY, tb.CONTEXT_BUDGET_UNSATISFIABLE)]
    # provider requests of the reopened owner: one, on the fallback
    assert [(model, kind) for unit, model, kind in seen["provider"] if kind == "reopened"] == [(FALLBACK, "reopened")]
    assert [(unit, model) for unit, model, kind in seen["provider"] if kind == "planned"] == [("s1", PRIMARY)]
    # work grants: one per attempt of the reopened owner (the refused attempt keeps its grant)
    assert len(seen["grants"]) == 2
    # retry-family charges: none for the refusal, and the fallback's attempt succeeded (no failure to charge)
    assert seen["charges"] == ["primary"]
    unavailable = [e.details for e in _reopened_events(seen, "model.candidate_unavailable")]
    assert [(d["model"], d["reason_code"], d["terminal"]) for d in unavailable] == [
        (PRIMARY, tb.CONTEXT_BUDGET_UNSATISFIABLE, False)]
    assert len(_reopened_events(seen, "attempt.started")) == 2


def test_run4_sequence_as_measured_stops_typed_after_one_refusal_per_candidate(tmp_path):
    workspace, result, seen = _run(tmp_path, fallback_admitted=False)
    status = {r.subtask_id: r.status.value for r in result.subtask_results}
    assert status == {"s1": "completed", "s2": "failed"}
    assert (workspace / SERVICE).read_text() == BASE_SOURCE and result.legacy_result["status"] == "failed"
    # one refused ATTEMPT per candidate (an attempt makes more than one call: the file list, then the generation)
    assert list(dict.fromkeys(seen["refused"])) == [
        (PRIMARY, tb.CONTEXT_BUDGET_UNSATISFIABLE), (FALLBACK, admission.MODEL_BINDING_NOT_QUALIFIED)]
    assert seen["refused"].count((PRIMARY, tb.CONTEXT_BUDGET_UNSATISFIABLE)) == 1
    assert [entry for entry in seen["provider"] if entry[2] == "reopened"] == []      # provider requests: 0
    assert len(seen["grants"]) == 2                                                   # work grants: 2 (it was 5)
    assert seen["charges"] == ["primary", "fallback"]                                 # retry-family charges: 0
    assert len(_reopened_events(seen, "attempt.started")) == 2                        # two attempts, not five
    unavailable = [e.details for e in _reopened_events(seen, "model.candidate_unavailable")]
    assert [(d["model"], d["terminal"]) for d in unavailable] == [(PRIMARY, False), (FALLBACK, True)]
    assert seen["test_gates"].count("s2") == 1                                        # the failure stands; no rerun

