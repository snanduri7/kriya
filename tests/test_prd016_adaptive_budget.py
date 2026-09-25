"""PRD-016 / CTX-001 adaptive budget at the model boundary.

The configured window (num_ctx) and max_tokens are preferred values. Under
the adaptive policy a request that does not fit is sent with the smallest
larger context tier that is qualified for the exact runtime (a current
qualification record at that num_ctx passing context_capacity and the
model's role cases) or - while no qualification data exists for it -
declared safe by the operator; output grows only for a grounded
expectation. Every expansion is recorded and reaches the run trace."""
import asyncio
import json
import sqlite3
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kriya.config import AppConfig
from kriya.config.authority import FieldClassification, classify_field
from kriya.core import model_qualification as mq
from kriya.core import model_runtime
from kriya.core import token_budget as tb
from kriya.core.completion import CompletionStatus
from kriya.core.llm import LLMClient
from kriya.core.model_runtime import ModelRuntimeFingerprint

MODEL = "qwen3-coder:30b"


def _response(content="ok", finish_reason="stop"):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = content
    response.choices[0].message.reasoning = None
    response.choices[0].message.tool_calls = None
    response.choices[0].finish_reason = finish_reason
    response.usage = MagicMock(prompt_tokens=40, completion_tokens=2)
    return response


def _exact_ollama(monkeypatch, *, model_context_length=262144, provider="ollama", artifact="sha256:abc"):
    """Probe stand-in: an exact runtime whose served window is the num_ctx
    the request configures (as Ollama reports it)."""
    base = ModelRuntimeFingerprint(
        alias=MODEL, endpoint="http://localhost:11434/v1", provider=provider, provider_version="0.34.2",
        artifact_digest=artifact, tokenizer_digest="sha256:tok", model_context_length=model_context_length,
    )

    def probe(**kw):
        return replace(base, alias=kw["model"], configured_context_window=kw["configured_context"],
                       effective_context_window=kw["configured_context"], kriya_protocol=kw["kriya_protocol"])

    monkeypatch.setattr(model_runtime, "probe_model_runtime", probe)


def _cfg(*, declared=(), mode="adaptive", max_tokens=4096, max_context=None, max_output=None):
    cfg = AppConfig()
    cfg.llm.model = MODEL
    cfg.llm.extra_body = {"options": {"num_ctx": 32768}}
    cfg.llm.context_window = 32768
    cfg.llm.max_tokens = max_tokens
    cfg.llm_chain = []
    cfg.llm.context_policy.mode = mode
    cfg.llm.context_policy.declared_safe_context_tiers = list(declared)
    cfg.llm.context_policy.max_context_tokens = max_context
    cfg.llm.context_policy.max_output_tokens = max_output
    return cfg


def _prompt_of(tokens):
    """A user prompt the default bound counts as about ``tokens``."""
    return "x" * int(tb.DEFAULT_BYTES_PER_TOKEN * tokens)


def _qualify_tier(cfg, size, *, statuses=None):
    """A qualification record for MODEL at num_ctx ``size``."""
    tier_cfg = mq.qualification_config(cfg, MODEL, size)
    tier_cfg.llm.context_policy.mode = cfg.llm.context_policy.mode
    runtime = model_runtime.resolve_configured_model_runtime(tier_cfg, MODEL)
    results = [mq.CaseResult(c, (statuses or {}).get(c, mq.PASS)) for c in mq.CAPABILITIES]
    mq.save_record(mq.build_record(runtime, results))
    return runtime


async def _send(llm, user, **kw):
    create = AsyncMock(return_value=_response())
    with patch.object(llm.client.chat.completions, "create", new=create):
        result = await llm.complete_result("system", user, **kw)
    return result, create


# --- context tiers at the LLM boundary ----------------------------------------------------------

@pytest.mark.asyncio
async def test_a_declared_tier_is_used_only_when_the_prompt_needs_it(monkeypatch):
    _exact_ollama(monkeypatch)
    llm = LLMClient(_cfg(declared=[65536]))
    small, create = await _send(llm, _prompt_of(8000))
    assert small.budget["selected_context_window"] == 32768 and not small.budget["context_expanded"]
    assert create.call_args[1]["extra_body"]["options"]["num_ctx"] == 32768
    assert llm.budget_expansions == []

    large, create = await _send(llm, _prompt_of(41000))
    sent = create.call_args[1]
    assert sent["extra_body"]["options"]["num_ctx"] == 65536
    assert sent["max_tokens"] == 4096
    assert large.budget["selected_context_window"] == 65536
    assert large.budget["qualification_source"] == tb.TIER_SOURCE_OPERATOR_DECLARED
    # The configured request options are never changed.
    assert llm.config.llm.extra_body["options"]["num_ctx"] == 32768
    # The call is attributed to the tier's own runtime fingerprint.
    assert large.runtime_fingerprint != small.runtime_fingerprint
    [event] = llm.budget_expansions
    for key in ("preferred_context_window", "selected_context_window", "preferred_output_tokens",
                "selected_output_tokens", "required_prompt_tokens", "expected_output_tokens", "reason",
                "qualification_source", "hard_context_ceiling", "runtime_fingerprint", "elapsed_seconds"):
        assert key in event, key
    assert event["selected_context_window"] == 65536 and event["reason"] == "prompt_exceeds_preferred_window"


@pytest.mark.asyncio
async def test_a_qualification_record_makes_a_tier_available_without_a_declaration(monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg()
    _qualify_tier(cfg, 65536)
    llm = LLMClient(cfg)
    result, create = await _send(llm, _prompt_of(41000))
    assert create.call_args[1]["extra_body"]["options"]["num_ctx"] == 65536
    assert result.budget["qualification_source"] == tb.TIER_SOURCE_QUALIFICATION_RECORD


@pytest.mark.asyncio
async def test_a_failed_capacity_probe_overrides_the_operator_declaration(monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg(declared=[65536])
    _qualify_tier(cfg, 65536, statuses={"context_capacity": mq.FAIL})
    llm = LLMClient(cfg)
    create = AsyncMock(return_value=_response())
    with patch.object(llm.client.chat.completions, "create", new=create):
        with pytest.raises(tb.ContextBudgetUnsatisfiableError) as refused:
            await llm.complete_result("system", _prompt_of(41000))
    create.assert_not_called()
    assert "65536: NOT_QUALIFIED" in refused.value.decision.tier_note


@pytest.mark.asyncio
async def test_a_tier_above_the_model_trained_length_is_never_selected(monkeypatch):
    """128K declared, but the model reports a 65536-token trained length."""
    _exact_ollama(monkeypatch, model_context_length=65536)
    llm = LLMClient(_cfg(declared=[131072]))
    create = AsyncMock(return_value=_response())
    with patch.object(llm.client.chat.completions, "create", new=create):
        with pytest.raises(tb.ContextBudgetUnsatisfiableError) as refused:
            await llm.complete_result("system", _prompt_of(41000))
    create.assert_not_called()
    assert "131072: above the ceiling 65536" in refused.value.decision.tier_note


@pytest.mark.asyncio
async def test_the_smallest_sufficient_tier_is_chosen(monkeypatch):
    _exact_ollama(monkeypatch)
    llm = LLMClient(_cfg(declared=[131072, 65536]))
    result, create = await _send(llm, _prompt_of(41000))
    assert create.call_args[1]["extra_body"]["options"]["num_ctx"] == 65536
    bigger, create = await _send(llm, _prompt_of(100000))
    assert create.call_args[1]["extra_body"]["options"]["num_ctx"] == 131072


@pytest.mark.asyncio
async def test_the_policy_hard_ceiling_bounds_the_tiers(monkeypatch):
    _exact_ollama(monkeypatch)
    llm = LLMClient(_cfg(declared=[65536, 131072], max_context=65536))
    with patch.object(llm.client.chat.completions, "create", new=AsyncMock(return_value=_response())):
        with pytest.raises(tb.ContextBudgetUnsatisfiableError):
            await llm.complete_result("system", _prompt_of(100000))


@pytest.mark.asyncio
async def test_strict_policy_never_expands(monkeypatch):
    _exact_ollama(monkeypatch)
    llm = LLMClient(_cfg(declared=[65536], mode="strict"))
    create = AsyncMock(return_value=_response())
    with patch.object(llm.client.chat.completions, "create", new=create):
        with pytest.raises(tb.ContextBudgetUnsatisfiableError):
            await llm.complete_result("system", _prompt_of(41000))
    create.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,artifact", [("openai-compatible", "sha256:abc"), ("ollama", "unavailable")])
async def test_a_runtime_that_cannot_switch_windows_behaves_like_strict(monkeypatch, provider, artifact):
    _exact_ollama(monkeypatch, provider=provider, artifact=artifact)
    llm = LLMClient(_cfg(declared=[65536]))
    with patch.object(llm.client.chat.completions, "create", new=AsyncMock(return_value=_response())):
        with pytest.raises(tb.ContextBudgetUnsatisfiableError) as refused:
            await llm.complete_result("system", _prompt_of(41000))
    assert "exact Ollama runtime" in refused.value.decision.tier_note


# --- output ---------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_grounded_expectation_enlarges_the_output_budget(monkeypatch):
    _exact_ollama(monkeypatch)
    llm = LLMClient(_cfg(max_tokens=4096))
    expected = tb.OutputExpectation(9000, "full-file rewrite of Big.java (~7500 tokens)")
    result, create = await _send(llm, _prompt_of(2000), expected_output=expected)
    assert create.call_args[1]["max_tokens"] == 9000
    assert result.budget["output_expanded"] and result.budget["output_grounding"].startswith("full-file")
    [event] = llm.budget_expansions
    assert event["reason"] == "grounded_output_exceeds_preferred_output" and event["selected_output_tokens"] == 9000


@pytest.mark.asyncio
async def test_without_grounding_the_output_never_grows(monkeypatch):
    _exact_ollama(monkeypatch)
    llm = LLMClient(_cfg(max_tokens=4096, declared=[65536]))
    result, create = await _send(llm, _prompt_of(2000))
    assert create.call_args[1]["max_tokens"] == 4096 and llm.budget_expansions == []


@pytest.mark.asyncio
async def test_an_expectation_above_the_hard_output_ceiling_is_refused_before_inference(monkeypatch):
    _exact_ollama(monkeypatch)
    llm = LLMClient(_cfg(max_tokens=4096, max_output=8192))
    create = AsyncMock(return_value=_response())
    with patch.object(llm.client.chat.completions, "create", new=create):
        with pytest.raises(tb.OutputBudgetUnsatisfiableError) as refused:
            await llm.complete_result("system", "u", expected_output=tb.OutputExpectation(20000, "rewrite"))
    create.assert_not_called()
    assert refused.value.reason_code == tb.OUTPUT_BUDGET_UNSATISFIABLE


@pytest.mark.asyncio
async def test_the_empty_content_retry_is_capped_by_the_hard_output_ceiling_and_recorded(monkeypatch):
    _exact_ollama(monkeypatch)
    llm = LLMClient(_cfg(max_tokens=1024, max_output=4096))
    create = AsyncMock(side_effect=[_response(""), _response('{"ok": true}')])
    with patch.object(llm.client.chat.completions, "create", new=create):
        result = await llm.complete_result("system", "u", json_mode=True)
    assert [c[1]["max_tokens"] for c in create.call_args_list] == [1024, 4096]
    assert result.protocol["empty_content_floor_retry"]
    assert [e["reason"] for e in llm.budget_expansions] == ["empty_content_floor_retry"]


@pytest.mark.asyncio
async def test_a_truncated_answer_after_expansion_is_still_truncated(monkeypatch):
    _exact_ollama(monkeypatch)
    llm = LLMClient(_cfg(declared=[65536]))
    create = AsyncMock(return_value=_response("partial", finish_reason="length"))
    with patch.object(llm.client.chat.completions, "create", new=create):
        result = await llm.complete_result("system", _prompt_of(41000))
    assert result.status is CompletionStatus.OUTPUT_TRUNCATED
    assert result.budget["context_expanded"]


# --- qualification of a tier ------------------------------------------------------------------------

def test_a_tier_needs_the_capacity_probe_and_every_role_case_of_the_model():
    cfg = _cfg()
    required = mq.context_tier_requirements(cfg, MODEL)
    assert "context_capacity" in required
    assert set(mq.required_capabilities(cfg, "developer", MODEL)) <= set(required)


def test_qualification_config_sets_the_tier_on_the_binding_and_runs_strict():
    cfg = _cfg(declared=[65536])
    tier = mq.qualification_config(cfg, MODEL, 65536)
    assert tier.llm.extra_body["options"]["num_ctx"] == 65536 and tier.llm.extra_body["options"]
    assert tier.llm.context_window == 65536 and tier.llm.context_policy.mode == "strict"
    assert cfg.llm.extra_body["options"]["num_ctx"] == 32768  # the original is untouched


def test_recorded_tiers_are_discovered_for_the_same_artifact_only(monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _cfg()
    _qualify_tier(cfg, 65536)
    preferred = model_runtime.resolve_configured_model_runtime(cfg, MODEL)
    assert mq.recorded_context_sizes(preferred) == [65536]
    other = replace(preferred, artifact_digest="sha256:other")
    assert mq.recorded_context_sizes(other) == []


def _capacity_client(*, recall_head=True, fill=0.97, usage=True):
    """Fake endpoint: prompt tokens proportional to the filler, answer
    recalls the markers found in the request."""
    per_unit = 12

    async def create(model, messages, max_tokens, temperature, extra_body):
        text = "".join(m["content"] for m in messages)
        units = text.count(mq._CAPACITY_UNIT)
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].finish_reason = "stop"
        if max_tokens == 1:
            tokens = 20 + units * per_unit
        else:
            tokens = int(extra_body["options"]["num_ctx"] * fill)
            head = messages[0]["content"].split("is ")[1].split(".")[0]
            tail = messages[1]["content"].split("second code is ")[1].split(".")[0]
            response.choices[0].message.content = f"{head if recall_head else 'unknown'} {tail}"
        response.usage = MagicMock(prompt_tokens=tokens if usage else None)
        return response

    client = MagicMock()
    client.chat.completions.create = create
    llm = MagicMock()
    llm.client = client
    return llm


def _capacity(llm, window=65536):
    return asyncio.run(mq.case_context_capacity(
        llm, MODEL, {"context_window": window, "extra_body": {"options": {"num_ctx": window}}},
    ))


def test_context_capacity_passes_when_a_near_window_prompt_keeps_both_markers():
    result = _capacity(_capacity_client())
    assert result.status == mq.PASS, result.evidence
    assert result.evidence["first_marker_recalled"] and result.evidence["fill_ratio"] > 0.9


def test_context_capacity_fails_when_the_front_of_the_prompt_is_dropped():
    result = _capacity(_capacity_client(recall_head=False))
    assert result.status == mq.FAIL and result.evidence["first_marker_recalled"] is False


def test_context_capacity_fails_when_the_window_is_not_actually_filled():
    assert _capacity(_capacity_client(fill=0.5)).status == mq.FAIL


def test_context_capacity_is_unavailable_without_usage_or_a_known_window():
    assert _capacity(_capacity_client(usage=False)).status == mq.UNAVAILABLE
    assert asyncio.run(mq.case_context_capacity(MagicMock(), MODEL, {})).status == mq.UNAVAILABLE


def test_model_qualify_passes_the_context_window(monkeypatch, tmp_path):
    from click.testing import CliRunner

    from kriya.cli import main

    seen = {}

    async def fake_run(cfg, model, **kw):
        seen.update(kw)
        return {"summary": {}, "measured_limits": {}, "fingerprint_digest": "d", "cases": [],
                "fingerprint": {}}

    monkeypatch.setattr(mq, "run_qualification", fake_run)
    monkeypatch.setattr(mq, "save_record", lambda record, workspace_root=None: str(tmp_path / "r.json"))
    config = tmp_path / "k.yaml"
    config.write_text("{}\n")
    result = CliRunner().invoke(main, ["--config", str(config), "model", "qualify", "--context-window", "65536"])
    assert result.exit_code == 0, result.output
    assert seen["context_window"] == 65536 and "num_ctx 65536" in result.output


def test_the_context_policy_is_security_authority():
    assert classify_field("llm", "context_policy") is FieldClassification.SECURITY_AUTHORITY


def test_an_unknown_policy_mode_is_rejected():
    from pydantic import ValidationError

    from kriya.config.config import ContextPolicyConfig

    with pytest.raises(ValidationError):
        ContextPolicyConfig(mode="unbounded")


# --- the evidence reaches the run trace --------------------------------------------------------------

def test_an_expansion_during_a_real_run_is_persisted_in_the_trace(tmp_path, monkeypatch):
    """End to end: the Developer's prompt does not fit its preferred window,
    a declared tier is selected for exactly those calls (num_ctx sent), and
    a model.budget_expansion event lands in traces.db."""
    import subprocess

    from kriya.config.config import AgentModelConfig, LLMConfig
    from kriya.core.kernel import Kernel
    from kriya.core.state_paths import trace_db_path
    from kriya.workflow.workflow import WorkflowEngine

    _exact_ollama(monkeypatch)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@e"], ["config", "user.name", "T"]):
        subprocess.run(["git", *args], cwd=workspace, check=True)
    (workspace / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "s"], cwd=workspace, check=True)

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.llm.model = "developer-small-window"
    cfg.llm.extra_body = {"options": {"num_ctx": 1536}}
    cfg.llm.context_window = 1536
    cfg.llm.max_tokens = 256
    cfg.llm.context_policy.declared_safe_context_tiers = [32768]
    cfg.llm_chain = []
    for role in ("planner", "architect", "reviewer", "run_verifier", "skill_gap", "spec_compliance"):
        setattr(cfg.agent_llms, role, AgentModelConfig(llm=LLMConfig(model="helper", context_window=32768)))

    developer_num_ctx = []
    llm = LLMClient(cfg)

    async def request_once(client, model, system_prompt, user_prompt, temperature, max_tokens, extra_body,
                           *args, **kwargs):
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        if model == "developer-small-window":
            developer_num_ctx.append(((extra_body or {}).get("options") or {}).get("num_ctx"))
        content = ('["calc.py"]' if "File List Planner" in first else "Step 1: add sub to calc.py"
                   if "Planner Agent" in first else "Review: Approved")
        if model == "developer-small-window" and "File List Planner" not in first:
            content = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": 10, "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}

    llm._request_once = request_once
    engine = WorkflowEngine(Kernel(config=cfg), llm)
    engine.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})
    asyncio.run(engine.run_generation_workflow(goal="add sub(a, b) to calc.py", workspace_path=str(workspace)))

    assert 32768 in developer_num_ctx, developer_num_ctx
    with sqlite3.connect(trace_db_path(cfg)) as db:
        rows = [json.loads(r[0]) for r in db.execute("SELECT run_events FROM runs WHERE run_events IS NOT NULL").fetchall()]
    events = [e for run in rows for e in run if e.get("kind") == "model.budget_expansion"]
    assert events, "the expansion must be persisted in the run trace"
    details = events[0]["details"]
    assert details["selected_context_window"] == 32768 and details["preferred_context_window"] == 1536
    assert details["qualification_source"] == tb.TIER_SOURCE_OPERATOR_DECLARED


# --- grounded Developer output and the lower-output protocol fallback --------------------------------

def _attempt_ctx(tmp_path, cfg, developer):
    from kriya.core.kernel import Kernel
    from kriya.workflow.attempt import AttemptContext
    from kriya.workflow.migration import resolve_migration_resolution

    return AttemptContext(
        goal="Rename add to plus in calc.py", plan="Step 1: rename", design="Design: rename",
        workspace_path=str(tmp_path), worktree_path=str(tmp_path), architect_files=["calc.py"],
        resume_state=None, run_id="run", skills_prompt="", learned_rag_context="", matched_files=[],
        related_files=[], ecosystem_invariant_block="", resource_lifecycle_block="",
        verification_contract_block="", recovery_contract_block="", required_files_prompt_block="",
        required_dependencies_prompt_block="", expected_files_upfront=["calc.py"],
        architect_basename_to_path={"calc.py": "calc.py"}, chain=[], targeted_max_retries=3,
        stream_callback=None, approval_callback=None, active_skills=[], active_skill_rules_snapshot={},
        developer=developer, run_verifier=AsyncMock(), spec_compliance=AsyncMock(), skill_engine=MagicMock(),
        kernel=Kernel(config=cfg), max_retries=4, web_lookup_query_callback=None,
        approve_web_lookup=AsyncMock(return_value=False),
        migration_resolution=resolve_migration_resolution("Rename add to plus in calc.py", str(tmp_path)),
    )


def _big_calc(tmp_path):
    source = "".join(f"def add{i}(a, b):\n    return a + b + {i}\n\n\n" for i in range(120))
    (tmp_path / "calc.py").write_text(source, encoding="utf-8")
    return source


def _developer_run(tmp_path, monkeypatch, *, max_output, protocol="small_native_tools"):
    from kriya.agents.agent import DeveloperAgent
    from kriya.workflow.attempt import _run_developer_generation
    from kriya.workflow.operations import CodeOperation
    from kriya.workflow.state import GenerationState

    _exact_ollama(monkeypatch)
    source = _big_calc(tmp_path)
    cfg = _cfg(max_tokens=1024, max_output=max_output)
    cfg.llm.capabilities.preferred_edit_protocol = protocol
    llm = LLMClient(cfg)
    requests = []

    async def request_once(client, model, system_prompt, user_prompt, temperature, max_tokens, *a, **k):
        requests.append({"system": system_prompt, "max_tokens": max_tokens})
        body = ("FIX ANALYSIS: rename.\nSEARCH:\ndef add0(a, b):\nREPLACE:\ndef plus0(a, b):\n"
                if "MODE: REPAIR." in system_prompt else source.replace("def add0", "def plus0"))
        return {"content": body, "reasoning_chars": 0, "prompt_tokens": 10, "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}

    llm._request_once = request_once
    ctx = _attempt_ctx(tmp_path, cfg, DeveloperAgent("developer", llm))
    state = GenerationState()
    state.attempt_number = 1
    files = asyncio.run(_run_developer_generation(
        state, ctx, task_description="Rename add0 to plus0", design_context="Design: rename",
        existing_code_context=f"=== File: calc.py ===\n{source}", known_target_files=["calc.py"],
        operation_by_file={"calc.py": CodeOperation.REPAIR_WITH_FULL_FILE},
        default_operation=CodeOperation.REPAIR_WITH_FULL_FILE,
    ))
    return files, requests, state


def test_a_full_file_rewrite_is_budgeted_for_the_file_it_rewrites(tmp_path, monkeypatch):
    files, requests, state = _developer_run(tmp_path, monkeypatch, max_output=None)
    # The grounded expectation (the file is ~2.8K tokens by the default
    # bound) enlarges max_tokens above the configured 1024.
    assert requests[0]["max_tokens"] > 1024
    assert "MODE: REPAIR_WITH_FULL_FILE" in requests[0]["system"]
    kinds = [e.kind for e in state.run_events]
    assert "model.budget_expansion" in kinds
    assert files and "plus0" in files[0]["content"]


def test_a_rewrite_that_cannot_fit_falls_back_to_an_anchored_patch(tmp_path, monkeypatch):
    files, requests, state = _developer_run(tmp_path, monkeypatch, max_output=1024)
    # No full-file request was ever sent: the refusal came before inference,
    # and the one request made asks for a patch.
    assert len(requests) == 1 and "MODE: REPAIR." in requests[0]["system"]
    fallback = [e for e in state.run_events if e.kind == "model.output_budget_protocol_fallback"]
    assert fallback and fallback[0].details["file"] == "calc.py"
    assert fallback[0].details["reason_code"] == tb.OUTPUT_BUDGET_UNSATISFIABLE
    assert files and files[0].get("edits")


def test_a_full_file_only_model_keeps_the_typed_output_refusal(tmp_path, monkeypatch):
    with pytest.raises(tb.OutputBudgetUnsatisfiableError) as refused:
        _developer_run(tmp_path, monkeypatch, max_output=1024, protocol="full_file")
    assert refused.value.filepath == "calc.py"


def test_an_output_refusal_is_a_typed_resource_failure():
    from kriya.workflow.failure_reporting import FailureCategory, categorize_failure

    assert categorize_failure("output_budget_unsatisfiable") is FailureCategory.RESOURCE


def test_a_budget_refusal_is_never_retried_as_one_larger_single_stage_request(monkeypatch):
    from kriya.agents.agent import DeveloperAgent

    _exact_ollama(monkeypatch)
    llm = LLMClient(_cfg())
    developer = DeveloperAgent("developer", llm)

    async def refuse(*args, **kwargs):
        raise tb.ContextBudgetUnsatisfiableError(tb.plan_dispatch(
            messages=[{"role": "user", "content": "x"}], requested_max_tokens=1, context_window=None,
            window_source="unknown"))

    developer._resolve_step1_file_list = AsyncMock(return_value=([{"filepath": "a.py"}], "model"))
    developer._fill_missing_content = refuse
    single_stage = AsyncMock()
    llm.complete = single_stage
    with pytest.raises(tb.ContextBudgetUnsatisfiableError):
        asyncio.run(developer.run_generation("task", "design", "context"))
    single_stage.assert_not_called()


def test_the_resume_fingerprint_binds_the_offered_context_tiers(monkeypatch):
    """A tier qualified (or its qualification revoked) after a checkpoint
    changes what a resumed request would be sent with: resume must not
    silently continue under a different window set."""
    from kriya.workflow.resume_fingerprints import model_runtime_resume_fingerprint

    _exact_ollama(monkeypatch)
    cfg = _cfg()
    before = model_runtime_resume_fingerprint(cfg)
    assert before.basis == "model-runtime"
    _qualify_tier(cfg, 65536)
    after = model_runtime_resume_fingerprint(cfg)
    assert after.value != before.value
    cfg.llm.context_policy.mode = "strict"
    assert model_runtime_resume_fingerprint(cfg).value != after.value


def _sibling_run(monkeypatch, *, design_tokens=0):
    from kriya.agents.agent import DeveloperAgent

    _exact_ollama(monkeypatch)
    cfg = _cfg(max_tokens=1024)
    cfg.llm.extra_body = {"options": {"num_ctx": 8192}}
    cfg.llm.context_window = 8192
    llm = LLMClient(cfg)
    prompts = []
    body = "x = 1\n" * int(4000 * tb.DEFAULT_BYTES_PER_TOKEN / 6)  # ~4000 counted tokens per file

    async def request_once(client, model, system_prompt, user_prompt, *a, **k):
        prompts.append(user_prompt)
        return {"content": body, "reasoning_chars": 0, "prompt_tokens": 10, "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}

    llm._request_once = request_once
    files = asyncio.run(DeveloperAgent("developer", llm).run_generation(
        "task", "design " + "d" * int(design_tokens * tb.DEFAULT_BYTES_PER_TOKEN), "context",
        known_target_files=["a.py", "b.py", "c.py"], sibling_content_budget=10 ** 6,
    ))
    return files, prompts, llm


def test_optional_sibling_contents_are_dropped_before_a_context_refusal(monkeypatch):
    """Fallback 1 (reduce optional context): c.py's prompt with a.py and
    b.py in full does not fit 8192; it is sent once more with their names
    only, and the reduction is recorded."""
    from kriya.workflow.state import GenerationState

    files, prompts, llm = _sibling_run(monkeypatch)
    assert len(files) == 3 and len(prompts) == 3
    assert "=== Already-Written File This Batch" in prompts[1]  # b.py still sees a.py in full
    assert "contents omitted - context budget; filenames only): a.py, b.py" in prompts[2]
    assert "=== Already-Written File This Batch (for cross-file" not in prompts[2]
    state = GenerationState()
    state.drain_budget_expansions(llm)
    reduced = [e for e in state.run_events if e.kind == "model.optional_context_reduced"]
    assert reduced and reduced[0].details["file"] == "c.py"


def test_a_prompt_that_does_not_fit_even_without_optional_context_is_refused(monkeypatch):
    with pytest.raises(tb.ContextBudgetUnsatisfiableError) as refused:
        _sibling_run(monkeypatch, design_tokens=7500)
    assert refused.value.filepath == "a.py"


def test_context_capacity_refuses_a_non_local_endpoint():
    result = asyncio.run(mq.case_context_capacity(
        _capacity_client(), MODEL,
        {"context_window": 8192, "base_url": "https://api.example.com/v1", "extra_body": {}},
    ))
    assert result.status == mq.UNAVAILABLE and "local endpoint" in result.evidence["reason"]


def test_context_capacity_uses_the_model_binding_client_when_one_is_given():
    used = []
    probe = _capacity_client()

    def factory(timeout):
        used.append(timeout)
        return probe

    result = asyncio.run(mq.case_context_capacity(
        MagicMock(), MODEL,
        {"context_window": 8192, "base_url": "http://localhost:11434/v1", "client_factory": factory,
         "extra_body": {"options": {"num_ctx": 8192}}},
    ))
    assert used and result.status == mq.PASS
