"""LR-R1-P1 deterministic reproducer: FALLBACK_MODEL_INCOMPATIBLE with the
live configuration (CAGC-v2 80-run census: terminal in 21/57 failures, all
four language views).

Investigation: handover/LR_R1_P1_FALLBACK_INCOMPATIBILITY_INVESTIGATION.md.
These tests pin the CURRENT behaviour (no fix is applied here). They
reproduce the live mechanism:

1. the Developer fallback is bound by its pinned, derived tag
   (``qwen3.6:35b-a3b-q4_K_M-kriya-620d4d5ce36a``) with no ``capabilities``
   block; the derived tag does not exact-match the known profile key
   (``qwen3.6:35b-a3b-q4_k_m``), so capability resolution falls to
   ``unverified_conservative_default`` (whole files only);
2. that resolution ignores the binding's own Developer qualification, which
   PASSes ``anchored_edit_protocol`` (measured on the live record);
3. a patch-only attempt (no complete current source shown, D1) that makes no
   progress triggers the strategy transition into ``fallback_targeted``; the
   fallback is refused only at the call: terminal FALLBACK_MODEL_INCOMPATIBLE
   with the live reason text.
"""
import json
import sqlite3

from _chaos_harness import (
    ChaosRuntime,
    RuntimeRegistration,
    benign_roles,
    chaos_config,
    chaos_engine,
    git_workspace,
    run_direct,
)
from _protocol_responses import sentinel
from test_prd017_fallback_transition import _exact_ollama, _qualify

from kriya.config import AppConfig
from kriya.config.config import FallbackModelConfig, ModelCapabilities
from kriya.core import model_qualification as mq
from kriya.core import model_runtime
from kriya.core.inference_settings import role_inference_settings
from kriya.core.model_capabilities import is_campaign_named_model, resolve_model_capability_profile
from kriya.core.state_paths import trace_db_path

LIVE_PRIMARY = "qwen3-coder:30b-kriya-e52213655394"
LIVE_FALLBACK = "qwen3.6:35b-a3b-q4_K_M-kriya-620d4d5ce36a"
# The capabilities block the live operator config (provider-contract-v5-production.yaml) gives the primary.
LIVE_PRIMARY_CAPABILITIES = ModelCapabilities(
    native_tool_calls=True, json_mode=True, reliable_multiline_json=False, streaming=True,
    max_tool_argument_chars=8192, preferred_edit_protocol="small_native_tools",
)
# The live fallback's Developer qualification (~/.kriya/qualifications/b0ed2c61...json, policy /8):
# 16 PASS, 4 UNAVAILABLE (native tool calls x3, endpoint restart), 0 FAIL.
LIVE_FALLBACK_QUALIFICATION = {
    "native_tool_calls": mq.UNAVAILABLE, "multiple_tool_calls": mq.UNAVAILABLE,
    "tool_argument_integrity": mq.UNAVAILABLE, "endpoint_restart_semantics": mq.UNAVAILABLE,
}


def _live_config() -> AppConfig:
    cfg = AppConfig()
    cfg.llm.model = LIVE_PRIMARY
    cfg.llm.capabilities = LIVE_PRIMARY_CAPABILITIES
    cfg.llm.context_window = 32768
    cfg.llm.extra_body = {"options": {"num_ctx": 32768}}
    cfg.llm_chain = [FallbackModelConfig(model=LIVE_FALLBACK, base_url=cfg.llm.base_url, context_window=32768,
                                         extra_body={"options": {"num_ctx": 32768}})]
    return cfg


def test_the_pinned_fallback_resolves_whole_file_only_although_its_qualification_passes_anchored_edits(
        monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _live_config()
    _qualify(cfg, LIVE_FALLBACK, **LIVE_FALLBACK_QUALIFICATION)

    primary = resolve_model_capability_profile(cfg, LIVE_PRIMARY)
    fallback = resolve_model_capability_profile(cfg, LIVE_FALLBACK)
    assert primary.source == "explicit_primary"
    assert primary.capabilities.preferred_edit_protocol == "small_native_tools"
    assert fallback.source == "unverified_conservative_default"
    assert fallback.capabilities.preferred_edit_protocol == "full_file"

    # The exact-name rule (MODEL-001): the base tag has a measured profile, the pinned derived tag does not.
    assert is_campaign_named_model("qwen3.6:35b-a3b-q4_K_M") and not is_campaign_named_model(LIVE_FALLBACK)

    # The same binding's own Developer qualification proves the anchored edit protocol.
    runtime = model_runtime.resolve_configured_model_runtime(cfg, LIVE_FALLBACK)
    record = mq.load_record(runtime.digest, role_inference_settings(cfg, "developer", LIVE_FALLBACK))
    statuses = {case["capability"]: case["status"] for case in record["cases"]}
    assert statuses["anchored_edit_protocol"] == mq.PASS
    assert statuses["full_file_raw_content"] == mq.PASS
    assert mq.required_capabilities(cfg, "developer", LIVE_FALLBACK) == (
        "plain_completion", "finish_reason_stop", "output_truncation", "reasoning_behavior",
        "endpoint_error_semantics", "over_context_refusal", "full_file_raw_content", "anchored_edit_protocol",
        "malformed_output_recovery", "streaming_assembly",
    )


def _module_too_large_for_the_window() -> str:
    helpers = "\n\n".join(f'def helper_{i}(value):\n    """Helper {i}."""\n    return value + {i}\n' for i in range(1500))
    return helpers + "\n\ndef value_chain(*args):\n    for value in args:\n        yield value\n"


def test_a_patch_only_run_with_the_live_fallback_ends_fallback_model_incompatible(tmp_path):
    """The live trajectory (e.g. python-error-invalidurl A r1/A r8, B r6/B r7):
    anchored edits that do not apply, ANCHOR_CONTEXT_NOT_ESCALATED refusals
    (no model call), a REPEATED_ACTION strategy transition that closes the
    primary's targeted budget and requests the fallback, and the fallback
    refused at the call. Difference from the live runs, stated: here the
    second attempt also reaches the model (two anchored-edit generations);
    live, the second attempt was already refused."""
    workspace = git_workspace(tmp_path, {
        "chain.py": _module_too_large_for_the_window(),
        "test_chain.py": "from chain import value_chain\n\n\ndef test_chain():\n    assert list(value_chain(1, 2)) == [1, 2]\n",
    })

    def responder(role, request):
        if role == "developer":
            return sentinel("chain.py", analysis="FIX ANALYSIS: skip None in value_chain",
                            edits=[("def value_chain(*args, missing):", "def value_chain(*args):")])
        return benign_roles(role, request, target="chain.py")

    runtime = ChaosRuntime(responder)
    cfg = chaos_config()
    cfg.llm_chain = [FallbackModelConfig(model=LIVE_FALLBACK, base_url=cfg.llm.base_url, context_window=8192,
                                         extra_body={}, inference_runtime="chaos")]
    with RuntimeRegistration(runtime):
        result = run_direct(chaos_engine(cfg),
                            "In chain.py, value_chain must skip None: change the loop `for value in args:` so a "
                            "None value is not yielded.", workspace)

    assert result["failure_category"] == "fallback_model_incompatible"
    with sqlite3.connect(trace_db_path(cfg)) as db:
        events = [e for (raw,) in db.execute("SELECT run_events FROM runs") for e in json.loads(raw)]
    modes = [e["details"]["mode"] for e in events if e["kind"] == "attempt.started"]
    failures = [e["details"]["failure_type"] for e in events if e["kind"] == "attempt.failed"]
    assert modes == ["full_set", "targeted", "targeted", "targeted", "fallback_targeted"]
    assert failures == ["anchored_edit", "anchored_edit", "no_progress_retry", "no_progress_retry",
                        "fallback_incompatible"]
    [transition] = [e["details"] for e in events if e["kind"] == "retry.strategy_transition"]
    assert transition["reason"] == "REPEATED_ACTION" and transition["fallback_targeted_requested"] is True
    [selection] = [e["details"] for e in events if e["kind"] == "model.fallback_selection"]
    assert selection["phase"] == "call" and selection["selected"] is None
    assert selection["rejected"][0]["reasons"] == [
        f"the attempt may only patch chain.py (no complete current source was shown), but {LIVE_FALLBACK}'s "
        "capability profile (unverified_conservative_default) returns whole files only (edit protocol full_file)"
    ]
    # Only the primary ever generated; the fallback was sent nothing.
    assert runtime.roles.count("developer") == 2
    assert all(r.model != LIVE_FALLBACK for r in runtime.requests)
