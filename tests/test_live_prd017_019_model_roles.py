"""PRD-017..019 live verification against two real local models.

- PRD-017: primary ``KRIYA_LIVE_LLM_MODEL`` (default qwen3-coder:30b) and a
  fallback ``KRIYA_LIVE_FALLBACK_MODEL`` (default qwen3.5:9B) declared with
  materially different capabilities (8192 window, 1024 output tokens, no JSON
  mode, whole-file edits). One bounded Developer request is sent on the
  fallback: the transition is recorded against both real exact fingerprints
  and the request sent carries the fallback's own num_ctx, max_tokens and no
  JSON mode.
- PRD-018: a same-model configuration (every role on the primary) and an
  override (the verifier roles on the fallback model, with
  model_policy.independent_roles): the doctor row identifies the real exact
  runtimes, WARN (shared) versus PASS (required and met).
- PRD-019: a routing plan over both runtimes for a small stage matrix
  (reviewer, spec_compliance, planner) is computed twice and is identical;
  every candidate's rejection or selection is recorded. No model is
  downloaded; unqualified runtimes are never selected.

Windows stay at 8192 tokens to bound hardware load; both models must already
be pulled.

Run:
    KRIYA_BATCH4_EVIDENCE_DIR=handover/evidence/BATCH4/user-live \\
    KRIYA_LIVE_BASE_URL=http://localhost:11434/v1 KRIYA_LIVE_LLM_MODEL=qwen3-coder:30b \\
    KRIYA_LIVE_FALLBACK_MODEL=qwen3.5:9B \\
    .venv/bin/pytest -m live_model -ra -s tests/test_live_prd017_019_model_roles.py
"""
import asyncio
import json
import os
from types import SimpleNamespace

import pytest

from kriya.config.config import AgentModelConfig, FallbackModelConfig, LLMConfig, ModelCapabilities, load_config
from kriya.core import model_qualification as mq
from kriya.core import model_routing as mr
from kriya.core.model_runtime import clear_model_runtime_cache, resolve_configured_model_runtime

pytestmark = pytest.mark.live_model

EVIDENCE_DIR = os.environ.get("KRIYA_BATCH4_EVIDENCE_DIR")
BASE_URL = os.environ.get("KRIYA_LIVE_BASE_URL", "http://localhost:11434/v1")
PRIMARY = os.environ.get("KRIYA_LIVE_LLM_MODEL", "qwen3-coder:30b")
FALLBACK = os.environ.get("KRIYA_LIVE_FALLBACK_MODEL", "qwen3.5:9B")
VERIFIER_ROLES = ("run_verifier", "spec_compliance")


def _evidence(name, payload):
    if EVIDENCE_DIR:
        os.makedirs(EVIDENCE_DIR, exist_ok=True)
        with open(os.path.join(EVIDENCE_DIR, name), "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True, default=str)


def _fallback_binding():
    return FallbackModelConfig(
        model=FALLBACK, base_url=BASE_URL, max_tokens=1024, context_window=8192,
        extra_body={"options": {"num_ctx": 8192}},
        capabilities=ModelCapabilities(native_tool_calls=False, json_mode=False, reliable_multiline_json=False,
                                       streaming=False, preferred_edit_protocol="full_file"),
    )


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv(mq.QUALIFICATION_HOME_ENV, str(tmp_path / "qualifications"))
    operator = tmp_path / "operator.yaml"
    operator.write_text("{}\n", encoding="utf-8")
    config = load_config(str(operator))
    config.llm.base_url = BASE_URL
    config.llm.model = PRIMARY
    config.llm.api_key = os.environ.get("KRIYA_LIVE_API_KEY", "local-key")
    config.llm.extra_body = {"options": {"num_ctx": 8192}}
    config.llm.context_window = 8192
    config.llm.max_tokens = 2048
    config.llm_chain = [_fallback_binding()]
    clear_model_runtime_cache()
    return config


def test_prd017_a_real_fallback_hop_is_recomputed_for_the_fallback_runtime(cfg, tmp_path):
    from kriya.agents.agent import DeveloperAgent
    from kriya.core.kernel import Kernel
    from kriya.core.llm import LLMClient
    from kriya.workflow.attempt import _run_developer_generation
    from kriya.workflow.state import GenerationState

    llm = LLMClient(cfg)
    sent = []
    original = llm._request_once

    async def recording(client, model, system_prompt, user_prompt, temperature, max_tokens, extra_body,
                        response_format, *args, **kwargs):
        sent.append({"model": model, "max_tokens": max_tokens, "response_format": response_format,
                     "num_ctx": ((extra_body or {}).get("options") or {}).get("num_ctx")})
        return await original(client, model, system_prompt, user_prompt, temperature, max_tokens, extra_body,
                              response_format, *args, **kwargs)

    llm._request_once = recording
    ctx = SimpleNamespace(kernel=Kernel(config=cfg), developer=DeveloperAgent("developer", llm),
                          expected_files_upfront=["hello.py"], chain=list(cfg.llm_chain),
                          worktree_path=str(tmp_path), workspace_path=str(tmp_path))
    state = GenerationState()
    fallback = cfg.llm_chain[0]
    state.attempt_number = 1
    files = asyncio.run(_run_developer_generation(
        state, ctx, task_description="Create hello.py with a function hello() that returns 'hi'.",
        design_context="One file, hello.py.", existing_code_context="", known_target_files=["hello.py"],
        model_override=fallback.model, base_url_override=fallback.base_url, api_key_override=fallback.api_key,
        extra_body_override=fallback.extra_body,
    ))

    transition = next(e for e in state.run_events if e.kind == "model.transition").details
    _evidence("prd017-transition.json", {"transition": transition, "requests": sent, "files": files})
    assert transition["to"]["model"] == FALLBACK and transition["to"]["runtime_exact"], transition["to"]
    assert transition["fallback"] is True
    assert sent and all(r["model"] == FALLBACK for r in sent)
    assert all(r["num_ctx"] == 8192 and r["max_tokens"] == 1024 and r["response_format"] is None for r in sent)
    assert files and "def hello" in (files[0].get("content") or "")


def test_prd018_doctor_identifies_shared_and_independent_real_runtimes(cfg, tmp_path):
    from kriya.production_doctor import CheckStatus, _check_role_independence, _Context

    shared = _check_role_independence(_Context(cfg=cfg, workspace=str(tmp_path)))
    for role in VERIFIER_ROLES:
        setattr(cfg.agent_llms, role, AgentModelConfig(llm=LLMConfig(
            model=FALLBACK, base_url=BASE_URL, extra_body={"options": {"num_ctx": 8192}}, context_window=8192)))
    cfg.model_policy.independent_roles = list(VERIFIER_ROLES)
    independent = _check_role_independence(_Context(cfg=cfg, workspace=str(tmp_path)))
    _evidence("prd018-role-independence.json", {"same_model": shared.evidence, "override": independent.evidence})

    assert (shared.status, shared.required) == (CheckStatus.WARN, False)
    assert all(info["runtime_exact"] for info in shared.evidence["roles"].values()), shared.evidence
    assert len(shared.evidence["groups"]) == 1
    assert (independent.status, independent.required) == (CheckStatus.PASS, True), independent.evidence
    developer = independent.evidence["roles"]["developer"]["runtime_digest"]
    assert all(independent.evidence["roles"][role]["runtime_digest"] != developer for role in VERIFIER_ROLES)


def test_prd019_routing_over_two_real_runtimes_is_deterministic(cfg, tmp_path):
    routing = cfg.model_policy.routing
    routing.mode = "evidence"
    routing.table_path = str(tmp_path / "table.json")
    # Declared capabilities: an undeclared profile is the conservative one (no JSON mode), which the JSON roles
    # would reject before any evidence is weighed.
    routing.candidates = [
        FallbackModelConfig(model=PRIMARY, base_url=BASE_URL, extra_body={"options": {"num_ctx": 8192}},
                            context_window=8192, capabilities=ModelCapabilities(json_mode=True)),
        FallbackModelConfig(model=FALLBACK, base_url=BASE_URL, extra_body={"options": {"num_ctx": 8192}},
                            context_window=8192, capabilities=ModelCapabilities(json_mode=True)),
    ]
    routing.roles = {role: [FALLBACK, PRIMARY] for role in ("reviewer", "spec_compliance", "planner")}
    # Qualify the fallback for the reviewer route only (a record, not a live campaign).
    placed = mr.place_candidate(cfg, "reviewer", routing.candidates[1])
    runtime = resolve_configured_model_runtime(placed, FALLBACK)
    assert runtime.exact, runtime.to_dict()
    mq.save_record(mq.build_record(runtime, [mq.CaseResult(c, mq.PASS) for c in mq.CAPABILITIES]))

    first = mr.plan_routes(cfg).to_events()
    clear_model_runtime_cache()
    second = mr.plan_routes(cfg).to_events()
    _evidence("prd019-routes.json", {"first": first, "second": second})
    assert first == second
    decisions = {event["role"]: event for event in first}
    assert (decisions["reviewer"]["model"], decisions["reviewer"]["source"]) == (FALLBACK, "evidence")
    for role in ("spec_compliance", "planner"):
        assert decisions[role]["source"] == "configured_default"  # nothing qualified for these routes
        assert all(row["reasons"] for row in decisions[role]["rejected"])
