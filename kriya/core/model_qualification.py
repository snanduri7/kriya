"""PRD-014: exact local model runtime qualification.

A qualification record is evidence that ONE exact runtime (a PRD-013
``ModelRuntimeFingerprint`` digest), called with ONE set of inference
settings (``kriya/core/inference_settings.py``: temperature, reasoning flag,
reasoning_effort, sampling options and every other ``extra_body`` field
except the per-call ``num_ctx``), passed the protocol cases Kriya uses with
it, under one Kriya protocol-adapter version and one qualification policy
version. It is never inferred from a model's name or benchmark reputation.
Records are keyed by the qualification identity (runtime digest + settings
digest, MODEL-QUAL-IDENTITY-001), so a record qualified with
``reasoning_effort: none`` never qualifies the same runtime called without it.

Functional evidence (every case but ``ENVIRONMENT_DEPENDENT_CAPABILITIES``)
belongs to the runtime + settings. Capacity evidence (``context_capacity``)
also depends on the machine serving the runtime, so a record keeps it per
execution-environment digest (``environment_evidence``,
kriya/core/execution_environment.py) and it counts only in that exact
environment: a 64K FAIL on one machine never blocks, and a 64K PASS on
another never qualifies, a different one. Re-qualifying in a new environment
adds its evidence next to the others'.

- Every case yields PASS, FAIL or UNAVAILABLE with its evidence. UNAVAILABLE
  is never PASS.
- A runtime that is not ``exact`` (artifact digest and provider version
  known) cannot be qualified at all.
- A record is STALE when the runtime fingerprint, the inference settings,
  the protocol adapter version or the policy version differs from the
  current one; a stale record qualifies nothing and its measured limits are
  not used. A policy-/2 record (keyed by the runtime digest alone, from
  before inference settings were part of the identity) is still found and
  reported STALE, never MISSING, and is never silently reinterpreted.
- Records live OUTSIDE any workspace (``~/.kriya/qualifications`` by
  default, ``KRIYA_QUALIFICATION_HOME`` to override), like SEC-009 and
  TOOL-002 approvals, so a repository can never ship its own qualification.
- Production roles require the capabilities Kriya will actually use with
  them (``required_capabilities``); ``kriya doctor --production`` blocks a
  role whose exact runtime lacks a current PASS for any of them.

- A larger context window (PRD-016 adaptive budget tier) is a different
  runtime input, so a different fingerprint: ``kriya model qualify
  --context-window N`` qualifies it, and it counts as a qualified tier only
  when that record is current and passes ``context_capacity`` (a real
  near-window request whose first and last markers both survive) plus
  every case the model's roles require.

Offline fixture conformance (``model_capabilities.validate_tool_call_sample``
and the fixture tests of this module's evaluators) stays separate from live
qualification, which only ``run_qualification`` against a real endpoint
produces.
"""
from __future__ import annotations

import ast
import asyncio
import json
import math
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

from kriya.core.execution_environment import (
    ENVIRONMENT_DEPENDENT_CAPABILITIES,
    ExecutionEnvironment,
    environment_for_fingerprint,
)
from kriya.core.inference_settings import InferenceSettings, qualification_identity
from kriya.core.model_runtime import MODEL_PROTOCOL_ADAPTER_VERSION, ModelRuntimeFingerprint

# /3 (MODEL-QUAL-IDENTITY-001): records are keyed by runtime + inference
# settings; every /2 record is STALE and must be re-qualified.
QUALIFICATION_POLICY_VERSION = "kriya-qualification/3"
QUALIFICATION_SCHEMA_VERSION = 2
QUALIFICATION_HOME_ENV = "KRIYA_QUALIFICATION_HOME"

PASS, FAIL, UNAVAILABLE = "PASS", "FAIL", "UNAVAILABLE"

QUALIFIED = "QUALIFIED"
NOT_QUALIFIED = "NOT_QUALIFIED"
STALE = "STALE"
MISSING = "MISSING"
NOT_EXACT = "RUNTIME_NOT_EXACT"

# Safety margin applied to the smallest measured bytes-per-token ratio.
BYTES_PER_TOKEN_MARGIN = 0.9

CAPABILITIES: Tuple[str, ...] = (
    "plain_completion",
    "finish_reason_stop",
    "structured_json",
    "multiline_json",
    "native_tool_calls",
    "multiple_tool_calls",
    "tool_argument_integrity",
    "streaming_assembly",
    "output_truncation",
    "reasoning_behavior",
    "full_file_raw_content",
    "anchored_edit_protocol",
    "malformed_output_recovery",
    "timeout_semantics",
    "cancellation_semantics",
    "endpoint_error_semantics",
    "endpoint_restart_semantics",
    "tokenizer_measurement",
    "context_capacity",
)

# A larger context tier additionally needs a passing near-window probe.
CONTEXT_TIER_REQUIREMENTS: Tuple[str, ...] = ("context_capacity",)

_BASE_REQUIREMENTS = ("plain_completion", "finish_reason_stop", "output_truncation", "reasoning_behavior",
                      "endpoint_error_semantics")
_ROLE_REQUIREMENTS: Dict[str, Tuple[str, ...]] = {
    "developer": ("full_file_raw_content", "anchored_edit_protocol", "malformed_output_recovery"),
    "planner": ("malformed_output_recovery",),
    "architect": (),
    "reviewer": (),
    "run_verifier": (),
    "skill_gap": (),
    "spec_compliance": (),
}
ROLES: Tuple[str, ...] = tuple(_ROLE_REQUIREMENTS)


class QualificationError(RuntimeError):
    pass


class QualificationPathInsideWorkspaceError(QualificationError):
    pass


@dataclass
class CaseResult:
    capability: str
    status: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    measured: Dict[str, Any] = field(default_factory=dict)
    elapsed_seconds: float = 0.0


def required_capabilities(config: Any, role: str, model: str) -> Tuple[str, ...]:
    """What Kriya will use with ``model`` in ``role``: the base protocol
    cases, the role's own, and every protocol the resolved capability
    profile enables (native tools, JSON mode, multi-line JSON, streaming)."""
    from kriya.core.model_capabilities import capabilities_for_model

    caps = capabilities_for_model(config, model)
    required = list(_BASE_REQUIREMENTS) + list(_ROLE_REQUIREMENTS.get(role, ()))
    if caps.native_tool_calls:
        required += ["native_tool_calls", "multiple_tool_calls", "tool_argument_integrity"]
    if caps.json_mode:
        required.append("structured_json")
    if caps.reliable_multiline_json:
        required.append("multiline_json")
    if caps.streaming:
        required.append("streaming_assembly")
    return tuple(dict.fromkeys(required))


def role_models(config: Any) -> Dict[str, List[str]]:
    """Every model a production role can call: its own binding (agent_llms
    or the primary llm) and its escalation chain. The Developer uses the
    primary llm and the top-level llm_chain."""
    result: Dict[str, List[str]] = {"developer": [config.llm.model, *[c.model for c in config.llm_chain]]}
    for role in ROLES:
        if role == "developer":
            continue
        role_cfg = getattr(config.agent_llms, role, None)
        primary = role_cfg.llm.model if role_cfg is not None and role_cfg.llm is not None else config.llm.model
        chain = [c.model for c in role_cfg.llm_chain] if role_cfg is not None else []
        result[role] = [primary, *chain]
    return {role: list(dict.fromkeys(models)) for role, models in result.items()}


def context_tier_requirements(config: Any, model: str) -> Tuple[str, ...]:
    """What a larger context tier of ``model`` must pass: every case any role
    that calls ``model`` requires, plus the near-window capacity probe."""
    required: List[str] = []
    for role, models in role_models(config).items():
        if any(m.casefold() == model.casefold() for m in models):
            required.extend(required_capabilities(config, role, model))
    if not required:
        required.extend(_BASE_REQUIREMENTS)
    return tuple(dict.fromkeys([*required, *CONTEXT_TIER_REQUIREMENTS]))


def _stored_records() -> Iterable[Dict[str, Any]]:
    home = qualification_home()
    try:
        names = sorted(os.listdir(home))
    except OSError:
        return
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(home, name), encoding="utf-8") as stream:
                record = json.load(stream)
        except (OSError, ValueError):
            continue
        if isinstance(record, dict):
            yield record


def recorded_context_sizes(fingerprint: ModelRuntimeFingerprint, settings: InferenceSettings) -> List[int]:
    """Context windows other than ``fingerprint``'s own that have a
    qualification record for the same served artifact at the same endpoint
    under the same inference settings (candidates only: each is re-verified
    against its own current fingerprint before use)."""
    sizes = set()
    for record in _stored_records():
        recorded = record.get("fingerprint") if isinstance(record.get("fingerprint"), dict) else {}
        size = recorded.get("configured_context_window")
        if (isinstance(size, int) and size != fingerprint.configured_context_window
                and record.get("inference_settings_digest") == settings.digest
                and recorded.get("artifact_digest") == fingerprint.artifact_digest
                and recorded.get("endpoint") == fingerprint.endpoint
                and str(recorded.get("alias", "")).casefold() == fingerprint.alias.casefold()):
            sizes.add(size)
    return sorted(sizes)


def runtime_has_records(runtime_digest: str) -> bool:
    """Whether any qualification record, under any inference settings or
    policy version, exists for this exact runtime. An operator-declared tier
    stands in for qualification only while there is no such data at all."""
    return any(record.get("fingerprint_digest") == runtime_digest for record in _stored_records())


@dataclass(frozen=True)
class ContextTierOffer:
    """PRD-016: the larger context windows a request to one runtime may be
    sent with, the hard context ceiling, why anything was excluded, and the
    evidence of each considered size (bound into the resume fingerprint)."""
    tiers: Tuple[Any, ...]
    ceiling: Optional[int]
    note: str
    evidence: Tuple[Dict[str, Any], ...] = ()


def offered_context_tiers(config: Any, model: str, fingerprint: ModelRuntimeFingerprint, policy: Any, *,
                          base_url: str, api_key: str, settings: InferenceSettings) -> ContextTierOffer:
    """A tier is offered when this exact runtime at that num_ctx, called with
    ``settings``, has a current qualification record passing every case the
    model's roles need plus context_capacity, or - while no qualification
    data exists for that runtime at all, under any settings or policy - when
    the operator declared it safe; a NOT_QUALIFIED or STALE record, or a
    record under other inference settings, overrides a declaration. Never above the policy ceiling or the model's
    trained length. The window can only be chosen per request on an exact
    runtime whose adapter takes it per request
    (``model_runtime.supports_per_request_context_window``); anywhere else
    the adaptive policy behaves like strict and says so."""
    from kriya.core.model_runtime import resolve_model_runtime, supports_per_request_context_window
    from kriya.core.token_budget import (
        POLICY_ADAPTIVE,
        TIER_SOURCE_OPERATOR_DECLARED,
        TIER_SOURCE_QUALIFICATION_RECORD,
        ContextTier,
    )

    limits = [value for value in (policy.max_context_tokens, fingerprint.model_context_length) if value]
    ceiling = min(limits) if limits else None
    if policy.mode != POLICY_ADAPTIVE:
        return ContextTierOffer((), ceiling, "strict policy: the preferred window is the limit")
    declared = set(policy.declared_safe_context_tiers)
    sizes = set(declared)
    if fingerprint.exact:
        sizes |= set(recorded_context_sizes(fingerprint, settings))
    if not sizes:
        return ContextTierOffer((), ceiling, "")
    if not supports_per_request_context_window(fingerprint):
        return ContextTierOffer((), ceiling, "no larger tier: the context window can only be chosen per request "
                                             "on an exact Ollama runtime")
    preferred = fingerprint.effective_context_window or 0
    tiers, notes, evidence = [], [], []
    for size in sorted(sizes):
        if size <= preferred:
            continue
        if ceiling is not None and size > ceiling:
            notes.append(f"{size}: above the ceiling {ceiling}")
            evidence.append({"tokens": size, "status": "above_ceiling"})
            continue
        tier_runtime = resolve_model_runtime(
            base_url=base_url, model=model, api_key=api_key, egress_policy=config.autonomy.egress_policy,
            configured_context=size, kriya_protocol=fingerprint.kriya_protocol, config=config,
        )
        assessment = assess(tier_runtime, context_tier_requirements(config, model), settings=settings)
        source = None
        if assessment.status == QUALIFIED:
            source = TIER_SOURCE_QUALIFICATION_RECORD
        elif assessment.status == MISSING and size in declared and not runtime_has_records(tier_runtime.digest):
            source = TIER_SOURCE_OPERATOR_DECLARED
        elif assessment.status == MISSING and size in declared:
            notes.append(f"{size}: {MISSING} for these inference settings (qualified under other settings)")
        else:
            notes.append(f"{size}: {assessment.status}")
        if source:
            tiers.append(ContextTier(size, source))
        evidence.append({"tokens": size, "status": assessment.status, "source": source,
                         "runtime_fingerprint": tier_runtime.digest if tier_runtime.exact else None})
    return ContextTierOffer(tuple(tiers), ceiling, "; ".join(notes), tuple(evidence))


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------

def qualification_home() -> str:
    configured = os.environ.get(QUALIFICATION_HOME_ENV)
    home = os.path.expanduser(configured) if configured else os.path.join(os.path.expanduser("~"), ".kriya", "qualifications")
    return os.path.realpath(home)


def _refuse_inside_workspace(path: str, workspace_root: Optional[str]) -> None:
    if not workspace_root:
        return
    real = os.path.realpath(path)
    root = os.path.realpath(workspace_root)
    if real == root or real.startswith(root + os.sep):
        raise QualificationPathInsideWorkspaceError(
            f"qualification store {path!r} resolves inside the workspace {workspace_root!r}; a qualification "
            "must live outside anything a repository can populate."
        )


def record_path(record_key: str, workspace_root: Optional[str] = None) -> str:
    """``<home>/<key>.json``; the key is a qualification identity (or, for a
    policy-/2 record, the runtime digest it was keyed by)."""
    home = qualification_home()
    _refuse_inside_workspace(home, workspace_root)
    return os.path.join(home, f"{record_key}.json")


def _same_qualified_identity(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    return all(a.get(key) == b.get(key) for key in (
        "qualification_identity", "fingerprint_digest", "inference_settings_digest", "adapter_version",
        "policy_version", "schema_version"))


def save_record(record: Dict[str, Any], workspace_root: Optional[str] = None) -> str:
    """Save ``record``. Capacity evidence other execution environments
    recorded for the same identity (and policy) is kept alongside it."""
    path = record_path(record["qualification_identity"], workspace_root)
    existing = _read_record(record["qualification_identity"], workspace_root)
    if existing is not None and _same_qualified_identity(existing, record):
        merged = dict(existing.get("environment_evidence") or {})
        merged.update(record.get("environment_evidence") or {})
        record = {**record, "environment_evidence": merged}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as stream:
        json.dump(record, stream, indent=2, sort_keys=True)
    os.replace(tmp, path)
    return path


def _read_record(record_key: str, workspace_root: Optional[str]) -> Optional[Dict[str, Any]]:
    try:
        with open(record_path(record_key, workspace_root), encoding="utf-8") as stream:
            record = json.load(stream)
    except (OSError, ValueError, QualificationPathInsideWorkspaceError):
        return None
    return record if isinstance(record, dict) else None


def load_record(runtime_digest: str, settings: InferenceSettings,
                workspace_root: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The record for this runtime under these inference settings; else a
    policy-/2 record keyed by the runtime digest alone (which is STALE)."""
    return (_read_record(qualification_identity(runtime_digest, settings), workspace_root)
            or _read_record(runtime_digest, workspace_root))


# --------------------------------------------------------------------------
# Assessment
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class QualificationAssessment:
    status: str
    fingerprint_digest: Optional[str]
    missing: Tuple[str, ...] = ()
    failed: Tuple[str, ...] = ()
    reasons: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def record_is_current(record: Optional[Dict[str, Any]], fingerprint: ModelRuntimeFingerprint,
                      settings: InferenceSettings) -> Tuple[bool, List[str]]:
    if record is None:
        return False, ["no qualification record for this exact runtime and inference settings"]
    reasons = []
    if record.get("fingerprint_digest") != fingerprint.digest:
        reasons.append("the runtime fingerprint changed since qualification")
    if record.get("inference_settings_digest") != settings.digest:
        reasons.append(
            "the inference settings differ from the qualified ones"
            if record.get("inference_settings_digest")
            else "the record predates inference-settings identity (re-qualify)"
        )
    if record.get("adapter_version") != MODEL_PROTOCOL_ADAPTER_VERSION:
        reasons.append(
            f"the Kriya protocol adapter changed ({record.get('adapter_version')} -> {MODEL_PROTOCOL_ADAPTER_VERSION})"
        )
    if record.get("policy_version") != QUALIFICATION_POLICY_VERSION:
        reasons.append(
            f"the qualification policy changed ({record.get('policy_version')} -> {QUALIFICATION_POLICY_VERSION})"
        )
    if record.get("schema_version") != QUALIFICATION_SCHEMA_VERSION:
        reasons.append("the qualification record schema changed")
    return not reasons, reasons


def environment_case_statuses(record: Dict[str, Any], environment: ExecutionEnvironment) -> Dict[str, Any]:
    """The environment-dependent case statuses ``record`` holds for exactly
    ``environment`` ({} unless it is exact and was evaluated there)."""
    if not environment.exact:
        return {}
    entry = (record.get("environment_evidence") or {}).get(environment.digest) or {}
    return {case.get("capability"): case.get("status") for case in entry.get("cases", [])
            if case.get("capability") in ENVIRONMENT_DEPENDENT_CAPABILITIES}


def assess(fingerprint: ModelRuntimeFingerprint, required: Iterable[str], *, settings: InferenceSettings,
           record: Optional[Dict[str, Any]] = None, workspace_root: Optional[str] = None,
           environment: Optional[ExecutionEnvironment] = None) -> QualificationAssessment:
    """``settings``: what the role sends this runtime
    (``inference_settings.role_inference_settings``); a record qualified
    under other settings does not count. ``environment`` (default: the one
    serving the runtime's endpoint) selects the capacity evidence that
    counts; evidence from any other environment never does."""
    required = tuple(required)
    if not fingerprint.exact:
        return QualificationAssessment(
            NOT_EXACT, None, required, (),
            (f"runtime identity is not exact (missing {', '.join(fingerprint.missing_components) or 'components'})",),
        )
    if record is None:
        record = load_record(fingerprint.digest, settings, workspace_root)
    if record is None:
        return QualificationAssessment(MISSING, fingerprint.digest, required, (),
                                       ("no qualification record for this exact runtime and inference settings",))
    current, reasons = record_is_current(record, fingerprint, settings)
    if not current:
        return QualificationAssessment(STALE, fingerprint.digest, required, (), tuple(reasons))
    statuses = {case.get("capability"): case.get("status") for case in record.get("cases", [])
                if case.get("capability") not in ENVIRONMENT_DEPENDENT_CAPABILITIES}
    environment = environment if environment is not None else environment_for_fingerprint(fingerprint)
    statuses.update(environment_case_statuses(record, environment))
    failed = tuple(cap for cap in required if statuses.get(cap) == FAIL)
    missing = tuple(cap for cap in required if statuses.get(cap) not in (PASS, FAIL))

    def missing_reason(cap: str) -> str:
        if statuses.get(cap):
            return f"{cap}: {statuses[cap]}"
        if cap in ENVIRONMENT_DEPENDENT_CAPABILITIES:
            where = ("the execution environment is not observable" if not environment.exact
                     else f"not evaluated in this execution environment ({environment.digest[:19]})")
            return f"{cap}: {where}"
        return f"{cap}: not run"

    if failed or missing:
        return QualificationAssessment(
            NOT_QUALIFIED, fingerprint.digest, missing, failed,
            tuple([f"{cap}: FAIL" for cap in failed] + [missing_reason(cap) for cap in missing]),
        )
    return QualificationAssessment(QUALIFIED, fingerprint.digest)


# Measured limits that describe the runtime's tokenizer, not its sampling:
# the same for every inference identity of one runtime.
RUNTIME_SCOPED_LIMITS = ("bytes_per_token_floor", "non_ascii_bytes_per_token_floor")


def _runtime_current(record: Dict[str, Any], fingerprint: ModelRuntimeFingerprint) -> bool:
    """Current for this runtime under the current policy, whatever its settings."""
    return (record.get("fingerprint_digest") == fingerprint.digest
            and record.get("adapter_version") == MODEL_PROTOCOL_ADAPTER_VERSION
            and record.get("policy_version") == QUALIFICATION_POLICY_VERSION
            and record.get("schema_version") == QUALIFICATION_SCHEMA_VERSION
            and bool(record.get("inference_settings_digest")))


def measured_limits_for(fingerprint: ModelRuntimeFingerprint, config: Any = None, *,
                        settings: InferenceSettings) -> Dict[str, Any]:
    """Measured limits for this exact runtime. Identity-scoped limits (e.g.
    reasoning_tokens_max) come only from a CURRENT record under these
    inference settings. Tokenizer floors (RUNTIME_SCOPED_LIMITS) are a
    property of the runtime: the most conservative value over every current
    record of the runtime, whatever its settings, so a prompt sized under
    one identity (allocation, the Developer's) is counted the same way when
    it is dispatched under another (a retry_temperature retry, a Reviewer
    temperature). A policy-/2 record never contributes."""
    if not fingerprint.exact:
        return {}
    record = load_record(fingerprint.digest, settings)
    current, _ = record_is_current(record, fingerprint, settings)
    limits = dict(record.get("measured_limits") or {}) if current and record else {}
    for key in RUNTIME_SCOPED_LIMITS:
        limits.pop(key, None)
        values = [
            stored["measured_limits"][key] for stored in _stored_records()
            if _runtime_current(stored, fingerprint)
            and isinstance((stored.get("measured_limits") or {}).get(key), (int, float))
        ]
        if values:
            limits[key] = min(values)
    return limits


# --------------------------------------------------------------------------
# Cases
# --------------------------------------------------------------------------

CaseFn = Callable[[Any, str, Dict[str, Any]], Awaitable[CaseResult]]

_WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "City name"}},
            "required": ["city"],
        },
    },
}
_NOTE_TOOL = {
    "type": "function",
    "function": {
        "name": "save_note",
        "description": "Save a note exactly as given.",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "The exact note text"}},
            "required": ["text"],
        },
    },
}
NOTE_TEXT = 'Line one "quoted" \\ backslash\nLine two: tab\tand unicode café – 東京 ✓ {braces} [brackets]'

TOKENIZER_CORPORA: Dict[str, str] = {
    "java": (
        "package com.example.orders;\n\nimport java.util.List;\nimport java.util.stream.Collectors;\n\n"
        "public final class OrderService {\n    private final OrderRepository repository;\n\n"
        "    public OrderService(OrderRepository repository) { this.repository = repository; }\n\n"
        "    public List<OrderDto> openOrders(long customerId) {\n"
        "        return repository.findByCustomerId(customerId).stream()\n"
        "            .filter(o -> o.getStatus() == Status.OPEN)\n"
        "            .map(OrderDto::from)\n            .collect(Collectors.toList());\n    }\n}\n"
    ),
    "python": (
        "from __future__ import annotations\n\nimport dataclasses\nfrom typing import Iterable\n\n\n"
        "@dataclasses.dataclass(frozen=True)\nclass Invoice:\n    number: str\n    lines: tuple[float, ...]\n\n"
        "    def total(self) -> float:\n        return round(sum(self.lines), 2)\n\n\n"
        "def overdue(invoices: Iterable[Invoice], limit: float = 1e3) -> list[str]:\n"
        "    return [i.number for i in invoices if i.total() > limit]\n"
    ),
    "xml": (
        '<?xml version="1.0" encoding="UTF-8"?>\n<project xmlns="http://maven.apache.org/POM/4.0.0">\n'
        "  <modelVersion>4.0.0</modelVersion>\n  <groupId>com.example</groupId>\n"
        "  <artifactId>orders</artifactId>\n  <version>1.2.3-SNAPSHOT</version>\n  <dependencies>\n"
        "    <dependency><groupId>org.junit.jupiter</groupId><artifactId>junit-jupiter</artifactId>"
        "<version>5.10.2</version><scope>test</scope></dependency>\n  </dependencies>\n</project>\n"
    ),
    "json": json.dumps({
        "files": [{"filepath": "src/app.py", "content": "print('hi')\n", "edits": []}],
        "meta": {"ids": [101, 202, 303], "ok": True, "ratio": 0.125, "tags": ["a-b", "c_d", "e.f"]},
    }, indent=2),
    "unicode": (
        "Grüße aus Köln – naïve café. Привет, мир! 你好，世界。こんにちは世界。안녕하세요. "
        "مرحبا بالعالم. שלום עולם. Γειά σου Κόσμε. ✓ ★ → ∑ ∞ ≠ 😀 🚀 🎉\n"
    ),
    "stacktrace": (
        'Exception in thread "main" java.lang.IllegalStateException: order 42 is closed\n'
        "\tat com.example.orders.OrderService.close(OrderService.java:88)\n"
        "\tat com.example.orders.OrderController.lambda$close$3(OrderController.java:41)\n"
        "Traceback (most recent call last):\n  File \"/srv/app/main.py\", line 12, in <module>\n"
        "    main()\n  File \"/srv/app/main.py\", line 9, in main\n    raise KeyError('customer_id')\n"
        "KeyError: 'customer_id'\n"
    ),
}


def _case(capability: str) -> Callable[[CaseFn], CaseFn]:
    def wrap(fn: CaseFn) -> CaseFn:
        async def run(llm: Any, model: str, ctx: Dict[str, Any]) -> CaseResult:
            started = time.monotonic()
            try:
                result = await fn(llm, model, ctx)
            except Exception as error:  # a crashing case is a FAIL with its evidence, never a PASS
                result = CaseResult(capability, FAIL, {"error": f"{type(error).__name__}: {error}"[:500]})
            result.capability = capability
            result.elapsed_seconds = round(time.monotonic() - started, 3)
            return result
        run.capability = capability  # type: ignore[attr-defined]
        return run
    return wrap


def _completion_evidence(result: Any) -> Dict[str, Any]:
    return result.to_telemetry() if hasattr(result, "to_telemetry") else {}


@_case("plain_completion")
async def case_plain_completion(llm, model, ctx):
    r = await llm.complete_result("You are a terse assistant.", "Reply with exactly one word: READY",
                                  model_override=model, max_tokens_override=64)
    ok = r.status.value == "OK" and "".join(ch for ch in r.content if ch.isalpha()).upper() == "READY"
    return CaseResult("", PASS if ok else FAIL, {"content": r.content[:200], **_completion_evidence(r)})


@_case("finish_reason_stop")
async def case_finish_reason_stop(llm, model, ctx):
    r = await llm.complete_result("You are a terse assistant.", "Say hello in one short sentence.",
                                  model_override=model, max_tokens_override=256)
    ok = r.status.value == "OK" and r.finish_reason == "stop"
    return CaseResult("", PASS if ok else FAIL, {"finish_reason": r.finish_reason, **_completion_evidence(r)})


@_case("structured_json")
async def case_structured_json(llm, model, ctx):
    r = await llm.complete_result(
        "You output JSON only.",
        'Return a JSON object with exactly these keys and values: "status" set to "ok", "count" set to 3.',
        json_mode=True, model_override=model, max_tokens_override=256,
    )
    try:
        parsed = json.loads(r.content)
    except (ValueError, TypeError):
        parsed = None
    ok = r.status.value == "OK" and isinstance(parsed, dict) and parsed.get("status") == "ok" and parsed.get("count") == 3
    return CaseResult("", PASS if ok else FAIL, {"parsed": parsed, **_completion_evidence(r)})


_MULTILINE_EXPECTED = 'def greet(name):\n    message = f"Hello, {name}!"\n    return message\n'


@_case("multiline_json")
async def case_multiline_json(llm, model, ctx):
    r = await llm.complete_result(
        "You output JSON only.",
        "Return a JSON object with key \"filepath\" set to \"greet.py\" and key \"content\" set to this exact "
        "three-line Python file (keep the newlines, indentation and quotes exactly):\n\n" + _MULTILINE_EXPECTED,
        json_mode=True, model_override=model, max_tokens_override=512,
    )
    try:
        parsed = json.loads(r.content)
    except (ValueError, TypeError):
        parsed = None
    content = parsed.get("content") if isinstance(parsed, dict) else None
    ok = r.status.value == "OK" and isinstance(content, str) and content.strip() == _MULTILINE_EXPECTED.strip()
    return CaseResult("", PASS if ok else FAIL, {"content": content, **_completion_evidence(r)})


def _tools_disabled(ctx: Dict[str, Any]) -> Optional[CaseResult]:
    if not ctx.get("native_tool_calls_enabled", True):
        return CaseResult("", UNAVAILABLE, {"reason": "the capability profile disables native tool calls; not measured"})
    return None


@_case("native_tool_calls")
async def case_native_tool_calls(llm, model, ctx):
    if (skip := _tools_disabled(ctx)) is not None:
        return skip
    r = await llm.complete_with_tools_result(
        [{"role": "user", "content": "What is the weather in Paris? Use the tool."}], [_WEATHER_TOOL],
        model_override=model, max_tokens_override=256,
    )
    calls = [c for c in r.tool_calls if c.get("name") == "get_weather"]
    ok = (r.status.value == "OK" and len(calls) == 1 and calls[0].get("source") == "native"
          and str(calls[0].get("arguments", {}).get("city", "")).lower() == "paris")
    return CaseResult("", PASS if ok else FAIL, {"tool_calls": r.tool_calls, **_completion_evidence(r)})


@_case("multiple_tool_calls")
async def case_multiple_tool_calls(llm, model, ctx):
    if (skip := _tools_disabled(ctx)) is not None:
        return skip
    r = await llm.complete_with_tools_result(
        [{"role": "user", "content": "Get the weather for Paris and for Tokyo. Call the tool once per city, "
                                     "both in this single reply."}],
        [_WEATHER_TOOL], model_override=model, max_tokens_override=512,
    )
    cities = sorted(str(c.get("arguments", {}).get("city", "")).lower() for c in r.tool_calls
                    if c.get("name") == "get_weather")
    ok = r.status.value == "OK" and cities == ["paris", "tokyo"]
    return CaseResult("", PASS if ok else FAIL, {"cities": cities, **_completion_evidence(r)})


@_case("tool_argument_integrity")
async def case_tool_argument_integrity(llm, model, ctx):
    if (skip := _tools_disabled(ctx)) is not None:
        return skip
    r = await llm.complete_with_tools_result(
        [{"role": "user", "content": "Save this note with the save_note tool, character for character:\n"
                                     + NOTE_TEXT}],
        [_NOTE_TOOL], model_override=model, max_tokens_override=512,
    )
    texts = [c.get("arguments", {}).get("text") for c in r.tool_calls if c.get("name") == "save_note"]
    ok = r.status.value == "OK" and len(texts) == 1 and texts[0] == NOTE_TEXT
    return CaseResult("", PASS if ok else FAIL,
                      {"received": texts, "expected": NOTE_TEXT, **_completion_evidence(r)},
                      {"verified_tool_argument_chars": len(NOTE_TEXT)} if ok else {})


@_case("streaming_assembly")
async def case_streaming_assembly(llm, model, ctx):
    deltas: List[str] = []
    r = await llm.complete_result("You are a terse assistant.",
                                  "Write the numbers 1 to 20 separated by single spaces and nothing else.",
                                  stream_callback=deltas.append, model_override=model, max_tokens_override=256)
    from kriya.core.completion import split_reasoning

    assembled, _ = split_reasoning("".join(deltas), anywhere=True)
    expected = " ".join(str(n) for n in range(1, 21))
    ok = (r.status.value == "OK" and len(deltas) > 1 and assembled == r.content
          and " ".join(r.content.split()) == expected)
    return CaseResult("", PASS if ok else FAIL,
                      {"delta_count": len(deltas), "assembled_matches_result": assembled == r.content,
                       **_completion_evidence(r)})


@_case("output_truncation")
async def case_output_truncation(llm, model, ctx):
    r = await llm.complete_result("You are a helpful assistant.",
                                  "Count from 1 to 400, one number per line.",
                                  model_override=model, max_tokens_override=16, reasoning_override=False)
    ok = r.status.value == "OUTPUT_TRUNCATED" and r.finish_reason == "length"
    return CaseResult("", PASS if ok else FAIL, {"finish_reason": r.finish_reason, **_completion_evidence(r)})


@_case("reasoning_behavior")
async def case_reasoning_behavior(llm, model, ctx):
    r = await llm.complete_result("You are a careful assistant.",
                                  "A train leaves at 09:40 and arrives at 11:05. How many minutes is the trip? "
                                  "Answer with the number only.",
                                  model_override=model, max_tokens_override=2048)
    visible_clean = "<think>" not in r.content and "</think>" not in r.content
    ok = r.status.value == "OK" and visible_clean and "85" in r.content
    measured = {"reasoning_observed": r.reasoning_present}
    if r.reasoning_present and r.completion_tokens:
        measured["reasoning_tokens_observed"] = r.completion_tokens
    return CaseResult("", PASS if ok else FAIL,
                      {"content": r.content[:200], "visible_content_has_think_tags": not visible_clean,
                       **_completion_evidence(r)}, measured)


@_case("full_file_raw_content")
async def case_full_file_raw_content(llm, model, ctx):
    from kriya.agents.agent import DeveloperAgent

    r = await llm.complete_result(
        "You are a senior software engineer. You write complete, correct source files.",
        "Please generate the complete, correct file content for: 'slug.py'\n"
        "It must define slugify(text: str) -> str that lowercases text, replaces runs of non-alphanumeric "
        "characters with a single '-', and strips leading/trailing '-'.\n"
        "Return ONLY the content of 'slug.py' - nothing before it, nothing after it, no other file.",
        model_override=model, max_tokens_override=1024,
    )
    content = DeveloperAgent.sanitize_generated_content(r.content, filepath="slug.py") or ""
    # Model output is never executed on the host: the check is structural.
    defines = False
    try:
        tree = ast.parse(content)
        parses = True
        defines = any(isinstance(node, ast.FunctionDef) and node.name == "slugify" for node in ast.walk(tree))
    except SyntaxError:
        parses = False
    ok = r.status.value == "OK" and parses and defines
    return CaseResult("", PASS if ok else FAIL,
                      {"parses": parses, "defines_slugify": defines,
                       "raw_had_fence": r.content.lstrip().startswith("```"), **_completion_evidence(r)})


_EDIT_SOURCE = "def total(prices):\n    result = 0\n    for p in prices:\n        result += p\n    return result\n"


@_case("anchored_edit_protocol")
async def case_anchored_edit_protocol(llm, model, ctx):
    from kriya.agents.agent import DeveloperAgent
    from kriya.workflow.edit_safety import apply_anchored_edits

    r = await llm.complete_result(
        "You are a senior software engineer.",
        f"=== calc.py ===\n{_EDIT_SOURCE}\n"
        "Task: total() must ignore negative prices.\n"
        "Before writing any code, write a line \"FIX ANALYSIS:\" with one sentence. Then write the line "
        "\"SEARCH:\" followed by the exact original code (copied verbatim from calc.py above) that needs to "
        "change, then the line \"REPLACE:\" followed by the corrected code - include only the lines that "
        "need to change plus the minimum context to identify them, not the whole file.",
        model_override=model, max_tokens_override=1024,
    )
    analysis, edits, _ = DeveloperAgent._split_fix_analysis_edit(r.content)
    applied = None
    if edits:
        try:
            applied = apply_anchored_edits(_EDIT_SOURCE, edits, _EDIT_SOURCE)
        except Exception as error:
            applied = None
            ctx.setdefault("notes", []).append(str(error))
    # Structural check only (model output is never executed on the host):
    # the edit applied exactly once, the result parses, still defines
    # total(), and changed the loop.
    changed = False
    if applied:
        try:
            tree = ast.parse(applied)
            changed = applied != _EDIT_SOURCE and any(
                isinstance(node, ast.FunctionDef) and node.name == "total" for node in ast.walk(tree)
            )
        except SyntaxError:
            changed = False
    ok = r.status.value == "OK" and bool(analysis) and bool(edits) and changed
    return CaseResult("", PASS if ok else FAIL,
                      {"analysis": bool(analysis), "edit_count": len(edits or []), "applied": applied is not None,
                       "applied_parses_and_changed": changed, **_completion_evidence(r)})


@_case("malformed_output_recovery")
async def case_malformed_output_recovery(llm, model, ctx):
    """Without JSON mode, models often wrap JSON in prose or fences; Kriya's
    extraction must recover the value, and the normalized status must not
    call the wrapped answer valid structured output."""
    from kriya.agents.agent import DeveloperAgent

    r = await llm.complete_result(
        "You are a helpful assistant.",
        "First write one sentence of explanation, then a JSON array of the three strings \"a.py\", \"b.py\" "
        "and \"c.py\" inside a ```json fenced block.",
        model_override=model, max_tokens_override=512,
    )
    try:
        value = DeveloperAgent._extract_json_value(r.content)
    except Exception:
        value = None
    ok = r.status.value == "OK" and value == ["a.py", "b.py", "c.py"]
    return CaseResult("", PASS if ok else FAIL, {"recovered": value, **_completion_evidence(r)})


@_case("timeout_semantics")
async def case_timeout_semantics(llm, model, ctx):
    probe = ctx["client_factory"](timeout=0.001)
    r = await probe.complete_result("You are a helpful assistant.", "Write a long story about a lighthouse.",
                                    model_override=model, max_tokens_override=512)
    ok = r.status.value == "TIMEOUT" and r.error is not None
    return CaseResult("", PASS if ok else FAIL, {"status": r.status.value, "backend_error": r.backend_error})


@_case("cancellation_semantics")
async def case_cancellation_semantics(llm, model, ctx):
    first_delta = asyncio.Event()

    def on_delta(_text: str) -> None:
        first_delta.set()

    task = asyncio.ensure_future(llm.complete_result(
        "You are a helpful assistant.", "Write a 600-word story about a lighthouse keeper.",
        stream_callback=on_delta, model_override=model, max_tokens_override=1024,
    ))
    try:
        await asyncio.wait_for(first_delta.wait(), timeout=ctx.get("first_delta_timeout", 120))
    except asyncio.TimeoutError:
        task.cancel()
        return CaseResult("", FAIL, {"reason": "no streamed output before cancelling"})
    task.cancel()
    cancelled_raised = False
    started = time.monotonic()
    try:
        await task
    except asyncio.CancelledError:
        cancelled_raised = True
    settle_seconds = round(time.monotonic() - started, 3)
    recorded = getattr(llm.last_completion, "status", None)
    healthy = await llm.complete_result("You are a terse assistant.", "Reply with exactly one word: READY",
                                        model_override=model, max_tokens_override=64)
    ok = (cancelled_raised and getattr(recorded, "value", None) == "CANCELLED" and settle_seconds < 10
          and healthy.status.value == "OK")
    return CaseResult("", PASS if ok else FAIL,
                      {"cancelled_raised": cancelled_raised, "recorded_status": getattr(recorded, "value", None),
                       "settle_seconds": settle_seconds, "endpoint_healthy_after": healthy.status.value})


@_case("endpoint_error_semantics")
async def case_endpoint_error_semantics(llm, model, ctx):
    r = await llm.complete_result("You are a helpful assistant.", "Hello",
                                  model_override="kriya-qualification-no-such-model:0", max_tokens_override=16)
    ok = r.status.value == "BACKEND_ERROR" and r.error is not None and r.content == ""
    return CaseResult("", PASS if ok else FAIL, {"status": r.status.value, "backend_error": r.backend_error})


@_case("endpoint_restart_semantics")
async def case_endpoint_restart_semantics(llm, model, ctx):
    return CaseResult("", UNAVAILABLE, {
        "reason": "not exercised live: restarting the operator's model server is out of scope for an automatic "
                  "qualification; connection-refused classification is covered by fixture tests",
    })


@_case("tokenizer_measurement")
async def case_tokenizer_measurement(llm, model, ctx):
    """Real prompt-token usage per content class. ASCII bytes per token comes
    from the ASCII probes; non-ASCII bytes per token from the Unicode probe
    after its ASCII part is charged at the ASCII rate. Both are floors (the
    template's own tokens are included, which only makes them smaller)."""
    reported: Dict[str, Any] = {}
    ascii_ratios: Dict[str, float] = {}
    for name, text in TOKENIZER_CORPORA.items():
        r = await llm.complete_result("", text, model_override=model, max_tokens_override=1, reasoning_override=False)
        tokens = r.prompt_tokens if not r.tokens_estimated else None
        reported[name] = tokens
        if tokens and name != "unicode":
            ascii_ratios[name] = len(text.encode("utf-8")) / tokens
    if any(value is None for value in reported.values()):
        return CaseResult("", UNAVAILABLE, {"reason": "the endpoint did not report prompt token usage",
                                            "reported_prompt_tokens": reported})
    ascii_floor = min(ascii_ratios.values())
    unicode_text = TOKENIZER_CORPORA["unicode"]
    ascii_part = sum(1 for ch in unicode_text if ord(ch) < 128)
    non_ascii_bytes = len(unicode_text.encode("utf-8")) - ascii_part
    non_ascii_tokens = max(1.0, reported["unicode"] - ascii_part / ascii_floor)
    non_ascii_ratio = non_ascii_bytes / non_ascii_tokens
    return CaseResult("", PASS, {
        "ascii_bytes_per_token": {k: round(v, 4) for k, v in ascii_ratios.items()},
        "non_ascii_bytes_per_token": round(non_ascii_ratio, 4),
        "reported_prompt_tokens": reported,
    }, {
        "bytes_per_token_floor": round(ascii_floor * BYTES_PER_TOKEN_MARGIN, 4),
        "non_ascii_bytes_per_token_floor": round(non_ascii_ratio * BYTES_PER_TOKEN_MARGIN, 4),
    })


_CAPACITY_UNIT = "alpha beta gamma delta epsilon zeta eta theta iota kappa. "
# Room left for the reply and the chat template around the filler.
_CAPACITY_HEADROOM_TOKENS = 384


@_case("context_capacity")
async def case_context_capacity(llm, model, ctx):
    """A near-window request actually fits the served window: the filler's
    real token rate is measured on two small probes, a prompt of about
    (window - headroom) real tokens is sent with a marker in the system
    message and another at the end, and both must come back. A server that
    silently drops the front of an over-long prompt loses the first marker.
    The request goes straight to the endpoint (this probes the server, not
    Kriya's own dispatch estimate) with the qualification binding's num_ctx."""
    import secrets

    from kriya.core.llm import is_local_url

    window = ctx.get("context_window")
    if not window:
        return CaseResult("", UNAVAILABLE, {"reason": "the served context window (num_ctx) is not known"})
    base_url = ctx.get("base_url")
    if base_url and not is_local_url(base_url):
        # This case talks to the endpoint directly (not through LLMClient's
        # egress check), so it refuses a non-local endpoint itself.
        return CaseResult("", UNAVAILABLE, {"reason": "context capacity is only probed on a local endpoint",
                                            "endpoint": base_url})
    # The model's own endpoint and key (the timeout case's client factory),
    # not necessarily the primary binding's.
    factory = ctx.get("client_factory")
    client = factory(600.0).client if factory is not None else llm.client
    extra_body = ctx.get("extra_body") or None

    async def send(messages, max_tokens):
        return await client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens, temperature=0.0, extra_body=extra_body,
        )

    def prompt_tokens(response) -> Optional[int]:
        usage = getattr(response, "usage", None)
        value = getattr(usage, "prompt_tokens", None)
        return value if isinstance(value, int) and value > 0 else None

    small = prompt_tokens(await send([{"role": "user", "content": _CAPACITY_UNIT * 40}], 1))
    large = prompt_tokens(await send([{"role": "user", "content": _CAPACITY_UNIT * 80}], 1))
    if not small or not large or large <= small:
        return CaseResult("", UNAVAILABLE, {"reason": "the endpoint did not report prompt token usage",
                                            "probe_prompt_tokens": [small, large]})
    per_unit = (large - small) / 40
    fixed = small - 40 * per_unit
    target = int(window) - _CAPACITY_HEADROOM_TOKENS
    units = max(1, int((target - fixed) / per_unit))
    head, tail = secrets.token_hex(4), secrets.token_hex(4)
    response = await send([
        {"role": "system", "content": f"The first code is {head}. Remember it."},
        {"role": "user", "content": _CAPACITY_UNIT * units
         + f"\nThe second code is {tail}. Reply with the first code, then the second code, separated by one "
           "space, and nothing else."},
    ], 64)
    reported = prompt_tokens(response)
    choice = response.choices[0]
    content = str(getattr(choice.message, "content", "") or "")
    evidence = {
        # This probe measures the server's window, so it is sent at
        # temperature 0.0 whatever the qualified inference settings say.
        "temperature": 0.0,
        "context_window": int(window), "target_prompt_tokens": target, "reported_prompt_tokens": reported,
        "fill_ratio": round(reported / int(window), 4) if reported else None,
        "first_marker_recalled": head in content, "last_marker_recalled": tail in content,
        "finish_reason": getattr(choice, "finish_reason", None),
    }
    if reported is None:
        return CaseResult("", UNAVAILABLE, {"reason": "the endpoint did not report prompt token usage", **evidence})
    ok = (head in content and tail in content and reported >= int(0.9 * target) and reported <= int(window))
    return CaseResult("", PASS if ok else FAIL, evidence)


ALL_CASES: Tuple[CaseFn, ...] = (
    case_plain_completion, case_finish_reason_stop, case_structured_json, case_multiline_json,
    case_native_tool_calls, case_multiple_tool_calls, case_tool_argument_integrity, case_streaming_assembly,
    case_output_truncation, case_reasoning_behavior, case_full_file_raw_content, case_anchored_edit_protocol,
    case_malformed_output_recovery, case_timeout_semantics, case_cancellation_semantics,
    case_endpoint_error_semantics, case_endpoint_restart_semantics, case_tokenizer_measurement,
    case_context_capacity,
)
assert tuple(case.capability for case in ALL_CASES) == CAPABILITIES  # type: ignore[attr-defined]


def measured_limits(cases: List[CaseResult]) -> Dict[str, Any]:
    limits: Dict[str, Any] = {}
    for case in cases:
        if case.status != PASS:
            continue
        for key, value in case.measured.items():
            limits[key] = value
    if "reasoning_tokens_observed" in limits:
        limits["reasoning_tokens_max"] = int(math.ceil(limits.pop("reasoning_tokens_observed") * 1.5))
    return limits


async def run_qualification(
    config: Any,
    model: Optional[str] = None,
    *,
    llm: Any = None,
    client_factory: Optional[Callable[..., Any]] = None,
    fingerprint: Optional[ModelRuntimeFingerprint] = None,
    only: Optional[Iterable[str]] = None,
    progress: Optional[Callable[[CaseResult], None]] = None,
    context_window: Optional[int] = None,
    settings: Optional[InferenceSettings] = None,
) -> Dict[str, Any]:
    """Run the protocol cases against the configured endpoint for one exact
    runtime and return the record (the caller saves it).

    ``settings`` are the inference settings qualified (default: what the
    Developer sends ``model`` with, ``role_inference_settings``); every case
    is sent with them (temperature, reasoning flag and extra_body) and the
    record is keyed by them. ``context_window`` qualifies the model at that
    num_ctx instead of its configured one (a PRD-016 context tier). Every
    case is sent with a strict budget policy, so a case is never itself sent
    with a different window."""
    from kriya.core.inference_settings import role_inference_settings
    from kriya.core.llm import LLMClient
    from kriya.core.model_capabilities import capabilities_for_model
    from kriya.core.model_runtime import _binding_for, configured_context_window, resolve_configured_model_runtime

    model = model or config.llm.model
    settings = settings or role_inference_settings(config, "developer", model)
    config = qualification_config(config, model, context_window, settings=settings)
    fingerprint = fingerprint or resolve_configured_model_runtime(config, model, fresh=True)
    if not fingerprint.exact:
        raise QualificationError(
            f"{NOT_EXACT}: {model!r} cannot be qualified: the runtime does not report "
            f"{', '.join(c for c in ('artifact_digest', 'provider_version') if c in fingerprint.missing_components)} "
            f"({'; '.join(fingerprint.probe_errors) or 'no native metadata'})"
        )
    llm = llm or LLMClient(config)

    def default_factory(timeout: float) -> Any:
        from openai import AsyncOpenAI

        binding = _binding_for(config, model)
        probe = LLMClient(config)
        probe.client = AsyncOpenAI(api_key=binding.get("api_key") or config.llm.api_key,
                                   base_url=binding.get("base_url") or config.llm.base_url,
                                   timeout=timeout, max_retries=0)
        return probe

    ctx: Dict[str, Any] = {
        "native_tool_calls_enabled": capabilities_for_model(config, model).native_tool_calls,
        "client_factory": client_factory or default_factory,
        "extra_body": config.llm.extra_body,
        "base_url": (_binding_for(config, model).get("base_url") or config.llm.base_url),
        "context_window": fingerprint.effective_context_window or configured_context_window(config.llm.extra_body),
    }
    wanted = set(only) if only else None
    results: List[CaseResult] = []
    for case in ALL_CASES:
        if wanted is not None and case.capability not in wanted:  # type: ignore[attr-defined]
            continue
        result = await case(llm, model, ctx)
        results.append(result)
        if progress is not None:
            progress(result)
    return build_record(fingerprint, results, settings=settings,
                        environment=environment_for_fingerprint(fingerprint))


def qualification_config(config: Any, model: str, context_window: Optional[int] = None, *,
                         settings: Optional[InferenceSettings] = None) -> Any:
    """A copy of ``config`` for qualifying ``model``: optionally at another
    num_ctx (set on the model's own binding, which is what its fingerprint
    is built from), every request carrying ``settings`` (the temperature,
    reasoning flag and extra_body qualified; LLMClient otherwise sends the
    primary binding's), and a strict budget policy so no case is itself sent
    with a different window."""
    from kriya.core.model_runtime import binding_object, configured_context_window, with_context_window

    copy = config.model_copy(deep=True)
    binding = binding_object(copy, model) or copy.llm
    if settings is not None:
        # Keep the binding's own context window: it is the runtime input, not a setting.
        window = configured_context_window(getattr(binding, "extra_body", None))
        extra_body = settings.extra_body
        binding.extra_body = with_context_window(extra_body, window) if window is not None else extra_body
        binding.reasoning = settings.reasoning
        if settings.temperature is not None and hasattr(binding, "temperature"):
            binding.temperature = settings.temperature
        copy.llm.temperature = settings.temperature if settings.temperature is not None else copy.llm.temperature
        copy.llm.reasoning = settings.reasoning
    if context_window is not None:
        binding.extra_body = with_context_window(getattr(binding, "extra_body", None), context_window)
        binding.context_window = int(context_window)
    if binding is not copy.llm:
        copy.llm.extra_body = dict(getattr(binding, "extra_body", None) or {})
    for policy_owner in (binding, copy.llm):
        policy = getattr(policy_owner, "context_policy", None)
        if policy is not None:
            policy.mode = "strict"
    return copy


def build_record(fingerprint: ModelRuntimeFingerprint, results: List[CaseResult], *,
                 settings: InferenceSettings, environment: Optional[ExecutionEnvironment] = None) -> Dict[str, Any]:
    """The record of one qualification run. Functional cases go to
    ``cases``; environment-dependent ones to ``environment_evidence`` under
    the digest of the environment that ran them (``environment``, default
    the one serving the fingerprint's endpoint)."""
    from kriya import __version__ as kriya_version

    environment = environment if environment is not None else environment_for_fingerprint(fingerprint)
    counts = {status: sum(1 for r in results if r.status == status) for status in (PASS, FAIL, UNAVAILABLE)}
    functional = [r for r in results if r.capability not in ENVIRONMENT_DEPENDENT_CAPABILITIES]
    dependent = [r for r in results if r.capability in ENVIRONMENT_DEPENDENT_CAPABILITIES]
    qualified_at = datetime.now(timezone.utc).isoformat()
    evidence = ({environment.digest: {"environment": environment.to_dict(), "qualified_at": qualified_at,
                                      "cases": [asdict(r) for r in dependent]}} if dependent else {})
    return {
        "schema_version": QUALIFICATION_SCHEMA_VERSION,
        "policy_version": QUALIFICATION_POLICY_VERSION,
        "adapter_version": MODEL_PROTOCOL_ADAPTER_VERSION,
        "kriya_version": kriya_version,
        "qualification_identity": qualification_identity(fingerprint.digest, settings),
        "fingerprint_digest": fingerprint.digest,
        "fingerprint": fingerprint.to_dict(),
        "inference_settings_digest": settings.digest,
        # output_ceiling inside is metadata (the configured max_tokens), never identity.
        "inference_settings": settings.to_dict(),
        "qualified_at": qualified_at,
        # The environment this run was served from (capacity evidence below
        # is keyed by its digest; functional cases hold in any environment).
        "environment": environment.to_dict(),
        "cases": [asdict(r) for r in functional],
        "environment_evidence": evidence,
        "measured_limits": measured_limits(results),
        "summary": counts,
    }


__all__ = [
    "ALL_CASES", "CAPABILITIES", "CaseResult", "FAIL", "MISSING", "NOT_EXACT", "NOT_QUALIFIED", "PASS",
    "QUALIFICATION_HOME_ENV", "QUALIFICATION_POLICY_VERSION", "QUALIFIED", "QualificationAssessment",
    "QualificationError", "QualificationPathInsideWorkspaceError", "ROLES", "STALE", "TOKENIZER_CORPORA",
    "CONTEXT_TIER_REQUIREMENTS", "ContextTierOffer", "UNAVAILABLE", "offered_context_tiers", "assess", "build_record", "context_tier_requirements",
    "load_record", "measured_limits", "measured_limits_for", "qualification_config", "qualification_home",
    "record_is_current", "record_path", "recorded_context_sizes", "required_capabilities", "role_models",
    "runtime_has_records", "environment_case_statuses",
    "run_qualification", "save_record",
]
