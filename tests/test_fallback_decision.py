"""The llm_chain fallback decisions of a Developer attempt (full modularization W4, E6): characterization written and
green at dd8a980, BEFORE ``_select_developer_fallback`` and its family move out of ``kriya/workflow/attempt.py``.

Each case is a PRD-017 / LR-R1-P1 situation driven through the attempt module's names exactly as the attempt calls
them (``_select_developer_fallback`` at escalation, ``_run_developer_generation`` -> ``_enter_developer_model`` ->
``_substitute_for_required_patch`` at the call). The whole decision is pinned, not only the chosen model: the
selected binding or the typed failure (type, message, diagnostics), the run-wide rejections the state keeps
(``incompatible_fallbacks``), the model hops, the run events (model.fallback_selection / model.fallback_incompatible /
model.transition), every LR-R1-M1 fallback decision record, and which request profiles were resolved (a fallback
already proven incompatible is never evaluated again). The golden ``tests/golden/generation/fallback_decisions.json``
was WRITTEN from dd8a980 (``KRIYA_WRITE_GENERATION_GOLDEN=1``) and is only ever compared afterwards; tmp paths are
normalized.
"""
import asyncio
import json
import os
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from test_lr_r1_p1_capability_routing import PATCH, PATCH_CAPS, WHOLE, WHOLE_ONLY, _binding, _room, _write
from test_lr_r1_p1_capability_routing import _cfg as _routing_cfg
from test_prd017_fallback_transition import FALLBACK, SECOND, _ctx, _exact_ollama, _qualify, _two_fallback_cfg

from kriya.agents.agent import DeveloperAgent
from kriya.core import model_qualification as mq
from kriya.core.attempt_evidence import scope as attempt_evidence_scope
from kriya.core.llm import LLMClient
from kriya.core.provider_contract import DEFAULT_BYTES_PER_TOKEN_CEILING
from kriya.workflow import attempt
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.operations import CodeOperation
from kriya.workflow.state import GenerationState

GOLDEN = Path(__file__).resolve().parent / "golden" / "generation" / "fallback_decisions.json"
WRITE_ENV = "KRIYA_WRITE_GENERATION_GOLDEN"
SERVICE_SOURCE = "class Service {\n    void run() {}\n}\n"
SHOWN_SERVICE = f"=== File: Service.java ===\n{SERVICE_SOURCE}"
THIRD = "fallback-third:7b"   # a second whole-file-only fallback (a copy of FALLBACK's binding under another name)
_SECONDS = re.compile(r"\d+\.\d+s\b")   # the generation event's measured duration


def _norm(value, root):
    if isinstance(value, dict):
        return {str(k): _norm(v, root) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_norm(v, root) for v in value]
    if isinstance(value, str):
        return value.replace(root, "<tmp>")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if hasattr(value, "model") and hasattr(value, "base_url"):   # an llm_chain binding
        return {"binding": value.model, "base_url": value.base_url}
    return _norm(getattr(value, "value", repr(value)), root)


class _Observed:
    """Everything one decision leaves behind."""

    def __init__(self, tmp_path):
        self.root = str(tmp_path)
        self.decisions, self.profiles = [], []

    def run(self, call):
        real_decision = attempt_evidence_scope.record_fallback_decision
        real_profile = attempt._developer_request_profile

        def decision(payload):
            self.decisions.append(payload)
            return real_decision(payload)

        def profile(ctx, model_override):
            self.profiles.append(model_override)
            return real_profile(ctx, model_override)
        with patch.object(attempt_evidence_scope, "record_fallback_decision", new=decision), \
                patch.object(attempt, "_developer_request_profile", new=profile):
            try:
                return {"returned": call()}
            except QualityGateFailure as refusal:
                failure = refusal.failure
                return {"failure": {"type": failure.type, "message": failure.message, "source": failure.source,
                                    "attempt": failure.attempt, "diagnostics": failure.diagnostics}}

    def form(self, outcome, state):
        return _norm({
            **outcome,
            "incompatible_fallbacks": dict(state.incompatible_fallbacks),
            "model_hops": list(state.model_hops),
            "events": [{"kind": e.kind, "source": e.source, "attempt": e.attempt, "authority": e.authority,
                        "message": _SECONDS.sub("<t>s", e.message),
                        "details": {k: ("<t>" if k == "duration_seconds" else v) for k, v in (e.details or {}).items()}}
                       for e in state.run_events],
            "decisions": self.decisions,
            "profiles_resolved": self.profiles,
        }, self.root)


def _developer(cfg):
    return DeveloperAgent("developer", LLMClient(cfg))


def _big(tmp_path, cfg):
    return _write(tmp_path, "Big.java", (_room(cfg, cfg.llm_chain[0]) + 1) * int(DEFAULT_BYTES_PER_TOKEN_CEILING))


# ----------------------------------------------------------------------------- escalation (selection before the prompt)

def case_incompatible_first_is_skipped_then_remembered(tmp_path):
    """PRD-017: the first fallback failed a Developer case: skipped for the next configured one; asked again for the
    same rung, the remembered rejection is reused (no new evaluation) and the skip is recorded again."""
    cfg = _two_fallback_cfg()
    _qualify(cfg, FALLBACK, full_file_raw_content=mq.FAIL)
    ctx, state, seen = _ctx(str(tmp_path), cfg, _developer(cfg)), GenerationState(), _Observed(tmp_path)
    state.attempt_number = 2
    first = seen.run(lambda: attempt._select_developer_fallback(state, ctx, 1))
    second = seen.run(lambda: attempt._select_developer_fallback(state, ctx, 2))
    third = seen.run(lambda: attempt._select_developer_fallback(state, ctx, 1))
    return seen.form({"returned": [first, second, third]}, state)


def case_compatible_is_never_reordered(tmp_path):
    cfg = _two_fallback_cfg()
    _qualify(cfg, SECOND)
    ctx, state, seen = _ctx(str(tmp_path), cfg, _developer(cfg)), GenerationState(), _Observed(tmp_path)
    return seen.form(seen.run(lambda: attempt._select_developer_fallback(state, ctx, 1)), state)


def case_every_fallback_incompatible_is_terminal(tmp_path):
    cfg = _two_fallback_cfg()
    cfg.runtime_profile = "production"
    _qualify(cfg, FALLBACK, anchored_edit_protocol=mq.FAIL)
    ctx, state, seen = _ctx(str(tmp_path), cfg, _developer(cfg)), GenerationState(), _Observed(tmp_path)
    state.attempt_number = 2
    return seen.form(seen.run(lambda: attempt._select_developer_fallback(state, ctx, 1)), state)


def case_no_retry_and_past_the_chain(tmp_path):
    """retry_count 0 is the primary (nothing evaluated, nothing recorded); a retry count past the chain starts at its
    last rung."""
    cfg = _two_fallback_cfg()
    ctx, state, seen = _ctx(str(tmp_path), cfg, _developer(cfg)), GenerationState(), _Observed(tmp_path)
    primary = seen.run(lambda: attempt._select_developer_fallback(state, ctx, 0))
    late = seen.run(lambda: attempt._select_developer_fallback(state, ctx, 7))
    return seen.form({"returned": [primary, late]}, state)


def case_patch_only_targets_skip_for_this_attempt(tmp_path):
    """LR-R1-P1 Option (b): a whole-file-only fallback cannot serve a patch-only target: skipped for THIS attempt
    (never a run-wide verdict) for the next patch-capable one, on either route."""
    cfg = _routing_cfg(_binding(WHOLE, capabilities=WHOLE_ONLY), _binding(PATCH, capabilities=PATCH_CAPS))
    big = _big(tmp_path, cfg)
    ctx, state, seen = _ctx(str(tmp_path), cfg, _developer(cfg)), GenerationState(), _Observed(tmp_path)
    targeted = seen.run(lambda: attempt._select_developer_fallback(state, ctx, 1, targets=[big]))
    full_set = seen.run(lambda: attempt._select_developer_fallback(state, ctx, 2, targets=[big], allow_primary=True))
    return seen.form({"returned": [targeted, full_set]}, state)


def case_patch_only_with_only_whole_file_fallbacks(tmp_path):
    """The full-set route stays on the primary; the fallback-targeted route ends typed; a small target proves
    nothing."""
    cfg = _routing_cfg(_binding(WHOLE, capabilities=WHOLE_ONLY))
    big = _big(tmp_path, cfg)
    small = _write(tmp_path, "Small.java", 4000)
    ctx, seen = _ctx(str(tmp_path), cfg, _developer(cfg)), _Observed(tmp_path)
    forms = []
    for targets, allow_primary in (([big], True), ([big], False), ([small], True)):
        state = GenerationState()
        state.attempt_number = 3
        outcome = seen.run(lambda state=state, targets=targets, allow_primary=allow_primary:
                           attempt._select_developer_fallback(state, ctx, 1, targets=targets, allow_primary=allow_primary))
        forms.append(seen.form(outcome, state))
        seen.decisions, seen.profiles = [], []
    return {"routes": forms}


def case_run_wide_rejection_stays_terminal_on_the_full_set_route(tmp_path):
    cfg = _routing_cfg(_binding(PATCH, capabilities=PATCH_CAPS))
    _qualify(cfg, PATCH, full_file_raw_content=mq.FAIL)
    ctx, state, seen = _ctx(str(tmp_path), cfg, _developer(cfg)), GenerationState(), _Observed(tmp_path)
    return seen.form(seen.run(lambda: attempt._select_developer_fallback(state, ctx, 1, targets=[], allow_primary=True)),
                     state)


# ----------------------------------------------------------------------------- the call (substitution at the request)

def _call(tmp_path, cfg, state, *, rejected=()):
    (tmp_path / "Service.java").write_text(SERVICE_SOURCE)
    developer = _developer(cfg)
    developer.run_generation = AsyncMock(return_value=[{"filepath": "Service.java", "content": "x"}])
    ctx = _ctx(str(tmp_path), cfg, developer)
    for model in rejected:
        state.incompatible_fallbacks[model] = ["previously proven incompatible"]
    fallback = cfg.llm_chain[0]
    kwargs = dict(task_description="Fix it", design_context="Design", existing_code_context=SHOWN_SERVICE,
                  known_target_files=["Service.java"],
                  operation_by_file={"Service.java": CodeOperation.REPAIR_WITH_PATCH},
                  model_override=fallback.model, base_url_override=fallback.base_url,
                  api_key_override=fallback.api_key, extra_body_override=fallback.extra_body)
    return developer, ctx, kwargs


def case_required_patch_moves_the_call(tmp_path):
    """PRD-017 at the call: the first fallback returns whole files only and the attempt may only patch: the call goes
    to the next patch-capable fallback (its whole binding), the hop is rewritten, the request profile recorded."""
    cfg = _two_fallback_cfg()
    state, seen = GenerationState(), _Observed(tmp_path)
    state.attempt_number, state.model_hops = 2, [FALLBACK]
    developer, ctx, kwargs = _call(tmp_path, cfg, state)
    outcome = seen.run(lambda: asyncio.run(attempt._run_developer_generation(state, ctx, **kwargs)))
    sent = developer.run_generation.await_args.kwargs
    outcome["sent"] = {key: sent.get(key) for key in ("model_override", "base_url_override", "api_key_override",
                                                      "extra_body_override")}
    outcome["last"] = [state.last_model_override, state.last_base_url_override, state.last_api_key_override,
                       state.last_extra_body_override, state.last_developer_call_attempt,
                       getattr(state.last_developer_request_profile, "model", None)]
    return seen.form(outcome, state)


def case_required_patch_moves_the_call_to_the_substitutes_own_endpoint(tmp_path):
    """The substitute's whole binding is what the call is sent with: its own endpoint and key, not the requested
    fallback's."""
    cfg = _two_fallback_cfg(second={"base_url": "http://127.0.0.1:11435/v1", "api_key": "second-key"})
    state, seen = GenerationState(), _Observed(tmp_path)
    state.attempt_number = 2
    developer, ctx, kwargs = _call(tmp_path, cfg, state)
    outcome = seen.run(lambda: asyncio.run(attempt._run_developer_generation(state, ctx, **kwargs)))
    sent = developer.run_generation.await_args.kwargs
    outcome["sent"] = {key: sent.get(key) for key in ("model_override", "base_url_override", "api_key_override",
                                                      "extra_body_override")}
    outcome["last"] = [state.last_model_override, state.last_base_url_override, state.last_api_key_override]
    return seen.form(outcome, state)


def case_required_patch_skips_every_whole_file_fallback(tmp_path):
    """Two whole-file-only fallbacks before the patch-capable one: the call skips both (each with its own reasons)."""
    cfg = _two_fallback_cfg()
    cfg.llm_chain.insert(1, cfg.llm_chain[0].model_copy(update={"model": THIRD}))
    state, seen = GenerationState(), _Observed(tmp_path)
    state.attempt_number = 2
    developer, ctx, kwargs = _call(tmp_path, cfg, state)
    outcome = seen.run(lambda: asyncio.run(attempt._run_developer_generation(state, ctx, **kwargs)))
    outcome["sent_model"] = developer.run_generation.await_args.kwargs.get("model_override")
    return seen.form(outcome, state)


def case_required_patch_with_no_substitute_is_terminal(tmp_path):
    """The only patch-capable fallback after it is already proven incompatible: no request at all, the typed
    terminal failure carries every rejection."""
    cfg = _two_fallback_cfg()
    state, seen = GenerationState(), _Observed(tmp_path)
    state.attempt_number = 2
    developer, ctx, kwargs = _call(tmp_path, cfg, state, rejected=(SECOND,))
    outcome = seen.run(lambda: asyncio.run(attempt._run_developer_generation(state, ctx, **kwargs)))
    outcome["requests_sent"] = developer.run_generation.await_count
    outcome["last_profile"] = getattr(state.last_developer_request_profile, "model", None)
    return seen.form(outcome, state)


CASES = {name[len("case_"):]: fn for name, fn in sorted(globals().items()) if name.startswith("case_")}


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_fallback_decision_is_the_pre_move_decision(name, tmp_path, monkeypatch):
    _exact_ollama(monkeypatch)
    form = json.loads(json.dumps(CASES[name](tmp_path), sort_keys=True, default=str))
    golden = json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else {}
    if os.environ.get(WRITE_ENV):
        golden[name] = form
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(golden, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        pytest.skip(f"golden written for {name}")
    assert form == golden[name]


def test_the_cases_cover_every_decision_outcome():
    """The golden itself is checked for the outcomes it must contain, so a regenerated golden cannot silently lose
    one: a skip-for-the-next, a remembered rejection, the full-set primary route, a typed terminal at escalation and
    at the call, a substitution at the call (to the substitute's own endpoint, past every incapable fallback)."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert set(golden) == set(CASES)
    first, second, third = golden["incompatible_first_is_skipped_then_remembered"]["returned"]
    assert first["returned"]["binding"] == second["returned"]["binding"] == third["returned"]["binding"] == SECOND
    assert golden["incompatible_first_is_skipped_then_remembered"]["profiles_resolved"].count(FALLBACK) == 1
    assert golden["every_fallback_incompatible_is_terminal"]["failure"]["type"] == "fallback_incompatible"
    routes = golden["patch_only_with_only_whole_file_fallbacks"]["routes"]
    assert routes[0]["returned"] is None and routes[1]["failure"]["type"] == "fallback_incompatible"
    assert routes[2]["returned"]["binding"] == WHOLE
    assert golden["required_patch_moves_the_call"]["sent"]["model_override"] == SECOND
    assert golden["required_patch_moves_the_call_to_the_substitutes_own_endpoint"]["sent"]["base_url_override"] == \
        "http://127.0.0.1:11435/v1"
    assert golden["required_patch_skips_every_whole_file_fallback"]["sent_model"] == SECOND
    assert golden["required_patch_with_no_substitute_is_terminal"]["requests_sent"] == 0
    assert golden["no_retry_and_past_the_chain"]["returned"][0]["returned"] is None
