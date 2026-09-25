"""PRD-014: exact local model runtime qualification.

A qualification record is evidence that ONE exact runtime (a PRD-013
``ModelRuntimeFingerprint`` digest) passed the protocol cases Kriya uses with
it, under one Kriya protocol-adapter version and one qualification policy
version. It is never inferred from a model's name or benchmark reputation.

- Every case yields PASS, FAIL or UNAVAILABLE with its evidence. UNAVAILABLE
  is never PASS.
- A runtime that is not ``exact`` (artifact digest and provider version
  known) cannot be qualified at all.
- A record is STALE when the runtime fingerprint, the protocol adapter
  version or the policy version differs from the current one; a stale
  record qualifies nothing and its measured limits are not used.
- Records live OUTSIDE any workspace (``~/.kriya/qualifications`` by
  default, ``KRIYA_QUALIFICATION_HOME`` to override), like SEC-009 and
  TOOL-002 approvals, so a repository can never ship its own qualification.
- Production roles require the capabilities Kriya will actually use with
  them (``required_capabilities``); ``kriya doctor --production`` blocks a
  role whose exact runtime lacks a current PASS for any of them.

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

from kriya.core.model_runtime import MODEL_PROTOCOL_ADAPTER_VERSION, ModelRuntimeFingerprint

QUALIFICATION_POLICY_VERSION = "kriya-qualification/1"
QUALIFICATION_SCHEMA_VERSION = 1
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
)

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


def record_path(fingerprint_digest: str, workspace_root: Optional[str] = None) -> str:
    home = qualification_home()
    _refuse_inside_workspace(home, workspace_root)
    return os.path.join(home, f"{fingerprint_digest}.json")


def save_record(record: Dict[str, Any], workspace_root: Optional[str] = None) -> str:
    path = record_path(record["fingerprint_digest"], workspace_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as stream:
        json.dump(record, stream, indent=2, sort_keys=True)
    os.replace(tmp, path)
    return path


def load_record(fingerprint_digest: str, workspace_root: Optional[str] = None) -> Optional[Dict[str, Any]]:
    try:
        with open(record_path(fingerprint_digest, workspace_root), encoding="utf-8") as stream:
            record = json.load(stream)
    except (OSError, ValueError, QualificationPathInsideWorkspaceError):
        return None
    return record if isinstance(record, dict) else None


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


def record_is_current(record: Optional[Dict[str, Any]], fingerprint: ModelRuntimeFingerprint) -> Tuple[bool, List[str]]:
    if record is None:
        return False, ["no qualification record for this exact runtime"]
    reasons = []
    if record.get("fingerprint_digest") != fingerprint.digest:
        reasons.append("the runtime fingerprint changed since qualification")
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


def assess(fingerprint: ModelRuntimeFingerprint, required: Iterable[str],
           record: Optional[Dict[str, Any]] = None, workspace_root: Optional[str] = None) -> QualificationAssessment:
    required = tuple(required)
    if not fingerprint.exact:
        return QualificationAssessment(
            NOT_EXACT, None, required, (),
            (f"runtime identity is not exact (missing {', '.join(fingerprint.missing_components) or 'components'})",),
        )
    if record is None:
        record = load_record(fingerprint.digest, workspace_root)
    if record is None:
        return QualificationAssessment(MISSING, fingerprint.digest, required, (),
                                       ("no qualification record for this exact runtime",))
    current, reasons = record_is_current(record, fingerprint)
    if not current:
        return QualificationAssessment(STALE, fingerprint.digest, required, (), tuple(reasons))
    statuses = {case.get("capability"): case.get("status") for case in record.get("cases", [])}
    failed = tuple(cap for cap in required if statuses.get(cap) == FAIL)
    missing = tuple(cap for cap in required if statuses.get(cap) not in (PASS, FAIL))
    if failed or missing:
        return QualificationAssessment(
            NOT_QUALIFIED, fingerprint.digest, missing, failed,
            tuple(
                [f"{cap}: FAIL" for cap in failed]
                + [f"{cap}: {statuses.get(cap) or 'not run'}" for cap in missing]
            ),
        )
    return QualificationAssessment(QUALIFIED, fingerprint.digest)


def measured_limits_for(fingerprint: ModelRuntimeFingerprint, config: Any = None) -> Dict[str, Any]:
    """Measured limits from a CURRENT record for this exact runtime, else {}."""
    if not fingerprint.exact:
        return {}
    record = load_record(fingerprint.digest)
    current, _ = record_is_current(record, fingerprint)
    return dict(record.get("measured_limits") or {}) if current and record else {}


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


ALL_CASES: Tuple[CaseFn, ...] = (
    case_plain_completion, case_finish_reason_stop, case_structured_json, case_multiline_json,
    case_native_tool_calls, case_multiple_tool_calls, case_tool_argument_integrity, case_streaming_assembly,
    case_output_truncation, case_reasoning_behavior, case_full_file_raw_content, case_anchored_edit_protocol,
    case_malformed_output_recovery, case_timeout_semantics, case_cancellation_semantics,
    case_endpoint_error_semantics, case_endpoint_restart_semantics, case_tokenizer_measurement,
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
) -> Dict[str, Any]:
    """Run the protocol cases against the configured endpoint for one exact
    runtime and return the record (the caller saves it)."""
    from kriya.core.llm import LLMClient
    from kriya.core.model_capabilities import capabilities_for_model
    from kriya.core.model_runtime import resolve_configured_model_runtime

    model = model or config.llm.model
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

        from kriya.core.model_runtime import _binding_for

        binding = _binding_for(config, model)
        probe = LLMClient(config)
        probe.client = AsyncOpenAI(api_key=binding.get("api_key") or config.llm.api_key,
                                   base_url=binding.get("base_url") or config.llm.base_url,
                                   timeout=timeout, max_retries=0)
        return probe

    ctx: Dict[str, Any] = {
        "native_tool_calls_enabled": capabilities_for_model(config, model).native_tool_calls,
        "client_factory": client_factory or default_factory,
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
    return build_record(fingerprint, results)


def build_record(fingerprint: ModelRuntimeFingerprint, results: List[CaseResult]) -> Dict[str, Any]:
    from kriya import __version__ as kriya_version

    counts = {status: sum(1 for r in results if r.status == status) for status in (PASS, FAIL, UNAVAILABLE)}
    return {
        "schema_version": QUALIFICATION_SCHEMA_VERSION,
        "policy_version": QUALIFICATION_POLICY_VERSION,
        "adapter_version": MODEL_PROTOCOL_ADAPTER_VERSION,
        "kriya_version": kriya_version,
        "fingerprint_digest": fingerprint.digest,
        "fingerprint": fingerprint.to_dict(),
        "qualified_at": datetime.now(timezone.utc).isoformat(),
        "cases": [asdict(r) for r in results],
        "measured_limits": measured_limits(results),
        "summary": counts,
    }


__all__ = [
    "ALL_CASES", "CAPABILITIES", "CaseResult", "FAIL", "MISSING", "NOT_EXACT", "NOT_QUALIFIED", "PASS",
    "QUALIFICATION_HOME_ENV", "QUALIFICATION_POLICY_VERSION", "QUALIFIED", "QualificationAssessment",
    "QualificationError", "QualificationPathInsideWorkspaceError", "ROLES", "STALE", "TOKENIZER_CORPORA",
    "UNAVAILABLE", "assess", "build_record", "load_record", "measured_limits", "measured_limits_for",
    "qualification_home", "record_is_current", "record_path", "required_capabilities", "role_models",
    "run_qualification", "save_record",
]
