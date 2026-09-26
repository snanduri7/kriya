"""PRD-013..016 live verification against the real local model.

- PRD-013: capture the runtime fingerprint twice (stable); change one runtime
  input (the served context window) and prove the old qualification no
  longer applies.
- PRD-014: run the full qualification campaign on the configured model and
  hand the report over (``KRIYA_BATCH3_EVIDENCE_DIR``).
- PRD-015: JSON, tool-call and streaming requests: the normalized result is
  compared with the raw HTTP response of the same request shape.
- PRD-016: the default estimator against the real tokenizer's counts on each
  content class; a request near the served window is accepted and one that
  cannot fit is refused before dispatch; under the adaptive policy a request
  that needs more than the preferred 8192 is sent with a declared 16384-token
  tier (the only larger window this test ever uses) and the server really
  serves it. Windows are kept at 8192/16384 tokens to bound hardware load.

Run:
    KRIYA_BATCH3_EVIDENCE_DIR=handover/evidence/BATCH3/user-live \\
    KRIYA_LIVE_BASE_URL=http://localhost:11434/v1 KRIYA_LIVE_LLM_MODEL=qwen3-coder:30b \\
    .venv/bin/pytest -m live_model -ra -s tests/test_live_prd013_016_model_runtime.py
"""
import asyncio
import json
import os
import time
import urllib.request

import pytest

from kriya.config.config import load_config
from kriya.core import model_qualification as mq
from kriya.core import token_budget as tb
from kriya.core.completion import CompletionStatus
from kriya.core.llm import LLMClient
from kriya.core.model_runtime import clear_model_runtime_cache, resolve_configured_model_runtime

pytestmark = pytest.mark.live_model

EVIDENCE_DIR = os.environ.get("KRIYA_BATCH3_EVIDENCE_DIR")


def _evidence(name, payload):
    if EVIDENCE_DIR:
        os.makedirs(EVIDENCE_DIR, exist_ok=True)
        with open(os.path.join(EVIDENCE_DIR, name), "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True, default=str)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv(mq.QUALIFICATION_HOME_ENV, str(tmp_path / "qualifications"))
    operator = tmp_path / "operator.yaml"
    operator.write_text("{}\n", encoding="utf-8")
    config = load_config(str(operator))
    config.llm.base_url = os.environ.get("KRIYA_LIVE_BASE_URL", "http://localhost:11434/v1")
    config.llm.model = os.environ.get("KRIYA_LIVE_LLM_MODEL", "qwen3-coder:30b")
    config.llm.api_key = os.environ.get("KRIYA_LIVE_API_KEY", "local-key")
    config.llm.extra_body = {"options": {"num_ctx": 8192}}
    config.llm.context_window = 8192
    config.llm.max_tokens = 1024
    config.llm_chain = []
    clear_model_runtime_cache()
    return config


def test_prd013_fingerprint_is_stable_and_drift_invalidates_qualification(cfg):
    first = resolve_configured_model_runtime(cfg, fresh=True)
    second = resolve_configured_model_runtime(cfg, fresh=True)
    assert first.exact, first.to_dict()
    assert first.digest == second.digest

    from kriya.core.inference_settings import role_inference_settings

    settings = role_inference_settings(cfg, "developer", cfg.llm.model)
    record = mq.build_record(first, [mq.CaseResult(cap, mq.PASS) for cap in mq.CAPABILITIES], settings=settings)
    mq.save_record(record)
    assert mq.assess(first, ("plain_completion",), settings=settings).status == mq.QUALIFIED

    cfg.llm.extra_body = {"options": {"num_ctx": 4096}}
    drifted = resolve_configured_model_runtime(cfg, fresh=True)
    assert drifted.digest != first.digest
    assert mq.assess(drifted, ("plain_completion",), settings=settings).status == mq.MISSING
    current, reasons = mq.record_is_current(record, drifted, settings)
    assert not current and any("fingerprint changed" in reason for reason in reasons)
    _evidence("prd013-fingerprint.json", {"first": first.to_dict(), "second_digest": second.digest,
                                          "drifted": drifted.to_dict(), "stale_reasons": reasons})


def test_prd014_full_qualification_campaign_reports_every_case(cfg):
    record = asyncio.run(mq.run_qualification(cfg))
    path = mq.save_record(record)
    _evidence("prd014-qualification.json", record)
    statuses = {case["capability"]: case["status"] for case in record["cases"]}
    assert set(statuses) == set(mq.CAPABILITIES)
    assert set(statuses.values()) <= {mq.PASS, mq.FAIL, mq.UNAVAILABLE}
    assert statuses["endpoint_restart_semantics"] == mq.UNAVAILABLE
    # Protocol behaviour of the runtime itself (not model quality):
    for capability in ("plain_completion", "output_truncation", "endpoint_error_semantics",
                       "timeout_semantics", "cancellation_semantics", "tokenizer_measurement",
                       "context_capacity"):
        assert statuses[capability] == mq.PASS, (capability, record["cases"])
    assert record["measured_limits"]["bytes_per_token_floor"] > 0
    assert os.path.exists(path)
    from kriya.core.inference_settings import role_inference_settings

    developer = mq.assess(resolve_configured_model_runtime(cfg), mq.required_capabilities(cfg, "developer", cfg.llm.model),
                          settings=role_inference_settings(cfg, "developer", cfg.llm.model))
    _evidence("prd014-developer-assessment.json", developer.to_dict())
    assert developer.status in (mq.QUALIFIED, mq.NOT_QUALIFIED)


def _raw_chat(cfg, payload):
    request = urllib.request.Request(
        f"{cfg.llm.base_url.rstrip('/')}/chat/completions",
        data=json.dumps({"model": cfg.llm.model, **payload}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {cfg.llm.api_key}"},
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.loads(response.read().decode("utf-8"))


def test_prd015_normalized_result_matches_the_raw_response(cfg):
    llm = LLMClient(cfg)
    system, user = "You output JSON only.", 'Return {"status": "ok", "count": 3} exactly.'
    normalized = asyncio.run(llm.complete_result(system, user, json_mode=True, temperature_override=0.0,
                                                 max_tokens_override=128))
    raw = _raw_chat(cfg, {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                          "temperature": 0.0, "max_tokens": 128, "response_format": {"type": "json_object"},
                          "options": {"num_ctx": 8192}})
    raw_choice = raw["choices"][0]
    assert normalized.status is CompletionStatus.OK
    assert json.loads(normalized.content) == json.loads(raw_choice["message"]["content"])
    assert normalized.finish_reason == raw_choice["finish_reason"]
    assert normalized.prompt_tokens == raw["usage"]["prompt_tokens"]

    truncated = asyncio.run(llm.complete_result("You are helpful.", "Count from 1 to 300.",
                                                max_tokens_override=12, reasoning_override=False))
    assert truncated.status is CompletionStatus.OUTPUT_TRUNCATED

    deltas = []
    streamed = asyncio.run(llm.complete_result("You are terse.", "Write the numbers 1 to 10 separated by spaces.",
                                               stream_callback=deltas.append, max_tokens_override=64))
    assert streamed.status is CompletionStatus.OK and "".join(deltas).strip().endswith(streamed.content[-5:])

    evidence = {"json": normalized.to_telemetry(), "raw_finish_reason": raw_choice["finish_reason"],
                "truncated": truncated.to_telemetry(), "streamed": streamed.to_telemetry()}
    if cfg.llm.capabilities.native_tool_calls:
        tool = {"type": "function", "function": {"name": "get_weather", "parameters": {
            "type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}
        messages = [{"role": "user", "content": "What is the weather in Paris? Use the tool."}]
        tools_result = asyncio.run(llm.complete_with_tools_result(messages, [tool], temperature_override=0.0))
        raw_tools = _raw_chat(cfg, {"messages": messages, "tools": [tool], "temperature": 0.0,
                                    "options": {"num_ctx": 8192}})
        raw_names = [c["function"]["name"] for c in raw_tools["choices"][0]["message"].get("tool_calls") or []]
        assert [c["name"] for c in tools_result.tool_calls] == raw_names
        evidence["tools"] = tools_result.to_telemetry()
    _evidence("prd015-normalized.json", evidence)


def test_prd016_budget_against_the_real_tokenizer_and_the_served_window(cfg):
    llm = LLMClient(cfg)
    comparison = {}
    for name, text in mq.TOKENIZER_CORPORA.items():
        result = asyncio.run(llm.complete_result("", text, max_tokens_override=1, reasoning_override=False))
        # The same dispatch the budget check counts (messages + framing overhead).
        planned = tb.plan_dispatch(messages=[{"role": "system", "content": ""}, {"role": "user", "content": text}],
                                   requested_max_tokens=1, context_window=None, window_source="unknown")
        comparison[name] = {"estimated": planned.prompt_tokens, "reported": result.prompt_tokens,
                            "len_div_4": len(text) // 4}
    _evidence("prd016-estimator-vs-tokenizer.json", comparison)
    for name, row in comparison.items():
        # The default bound must never undercount the real tokenizer.
        assert row["estimated"] >= row["reported"], (name, row)

    # Near the window: predicted prompt ~ 8192 - 1024 min output - margin.
    near = "def f():\n    return 1\n" * int((8192 - 1024 - 400) * tb.DEFAULT_BYTES_PER_TOKEN / 24)
    accepted = asyncio.run(llm.complete_result("You are terse.", near + "\nReply OK.", max_tokens_override=1024))
    assert accepted.budget["satisfiable"] and accepted.status is not CompletionStatus.BACKEND_ERROR
    assert accepted.prompt_tokens <= 8192

    started = time.monotonic()
    with pytest.raises(tb.ContextBudgetUnsatisfiableError):
        asyncio.run(llm.complete_result("You are terse.", near * 3, max_tokens_override=1024))
    refused_in = time.monotonic() - started
    assert refused_in < 5, "an unsatisfiable request must be refused before any inference"
    _evidence("prd016-window.json", {"accepted_budget": accepted.budget, "accepted_prompt_tokens": accepted.prompt_tokens,
                                     "refused_seconds": round(refused_in, 3)})


def test_prd016_adaptive_policy_serves_a_declared_larger_tier(cfg):
    """Preferred 8192, declared-safe 16384: a ~10K-real-token prompt is sent with
    num_ctx 16384 and the server reports more prompt tokens than the
    preferred window holds; the expansion is recorded."""
    cfg.llm.context_policy.declared_safe_context_tiers = [16384]
    llm = LLMClient(cfg)
    # ~13K tokens by the default bound (no qualification in this test's
    # store): about 10K real tokens - more than 8192, less than 16384.
    prompt = "def f():\n    return 1\n" * int(13000 * tb.DEFAULT_BYTES_PER_TOKEN / 24)
    result = asyncio.run(llm.complete_result("You are terse.", prompt + "\nReply OK.", max_tokens_override=256))
    assert result.budget["context_expanded"] and result.budget["selected_context_window"] == 16384
    assert result.status is not CompletionStatus.BACKEND_ERROR
    assert result.prompt_tokens > 8192, "the server must actually have served the larger window"
    [event] = llm.budget_expansions
    _evidence("prd016-adaptive-tier.json", {"budget": result.budget, "prompt_tokens": result.prompt_tokens,
                                            "event": event})
