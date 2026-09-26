"""PRD-018: per-role, per-runtime model metrics.

Every agent shares one LLMClient, so a call is attributed to the role that
made it through ``model_role`` (a context variable the agents set around
their calls); a call made outside any role scope is recorded as
``unattributed``, never guessed. Each (role, model, exact runtime digest)
gets its own counters: calls, protocol failures (the normalized
CompletionResult status), schema failures (a caller rejecting a response
it could parse at the protocol level, e.g. JSON that fails the role's
contract), latency and token usage; the Developer also gets its attempt
outcomes (first-pass success and the retries its failed attempts
triggered).

Structured-output outcomes (MODEL-EVIDENCE-HARDENING-001) are typed, one per
response a caller validated (today: every Planner plan, initial and
repaired): valid, malformed output (JSON/protocol), structured plan
validation failure (parseable, but not a valid plan under Kriya's plan
contract), deterministic policy rejection (a valid plan a repository/route
policy refused) or another typed failure. Malformed output and a structured
plan validation failure are the model's fault and also count as schema
failures, which PRD-019 routing ranks on; a policy rejection does not.

These are observations for the operator and for PRD-019's between-run
aggregation. They never decide anything during the run, and a second
model's opinion is never counted as verification evidence: deterministic
gate outcomes stay the only authority on whether an attempt passed.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

UNATTRIBUTED = "unattributed"
UNAVAILABLE_RUNTIME = "unavailable"

# CompletionResult statuses that mean the model/backend did not return a
# usable answer at the protocol level.
PROTOCOL_FAILURE_STATUSES = frozenset({
    "EMPTY_CONTENT", "MALFORMED_STRUCTURED_OUTPUT", "OUTPUT_TRUNCATED", "BACKEND_ERROR", "TIMEOUT",
})

STRUCTURED_VALID = "valid"
STRUCTURED_MALFORMED = "malformed_output"
STRUCTURED_VALIDATION_FAILURE = "structured_validation_failure"
STRUCTURED_POLICY_REJECTED = "policy_rejected"
STRUCTURED_OTHER_FAILURE = "other_failure"
STRUCTURED_OUTCOME_COUNTERS: Dict[str, str] = {
    STRUCTURED_VALID: "structured_valid",
    STRUCTURED_MALFORMED: "structured_malformed",
    STRUCTURED_VALIDATION_FAILURE: "structured_validation_failures",
    STRUCTURED_POLICY_REJECTED: "structured_policy_rejections",
    STRUCTURED_OTHER_FAILURE: "structured_other_failures",
}
# Outcomes that are negative evidence about the model itself.
MODEL_FAULT_OUTCOMES = frozenset({STRUCTURED_MALFORMED, STRUCTURED_VALIDATION_FAILURE})

_ROLE: ContextVar[Optional[str]] = ContextVar("kriya_model_role", default=None)


@contextmanager
def model_role(role: Optional[str]) -> Iterator[None]:
    """Attribute every model call inside this scope to ``role``. An inner
    scope wins (the Developer's self-correction inside its attempt is the
    Developer's)."""
    if not role:
        yield
        return
    token = _ROLE.set(role)
    try:
        yield
    finally:
        _ROLE.reset(token)


def current_model_role() -> str:
    return _ROLE.get() or UNATTRIBUTED


@dataclass
class RoleRuntimeMetrics:
    role: str
    model: str
    runtime_digest: str
    runtime_exact: bool
    calls: int = 0
    protocol_failures: int = 0
    schema_failures: int = 0
    latency_seconds: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_token_calls: int = 0
    attempts: int = 0
    attempts_passed: int = 0
    first_pass_success: Optional[bool] = None
    retries_triggered: int = 0
    structured_valid: int = 0
    structured_malformed: int = 0
    structured_validation_failures: int = 0
    structured_policy_rejections: int = 0
    structured_other_failures: int = 0

    @property
    def key(self) -> Tuple[str, str, str]:
        return (self.role, self.model, self.runtime_digest)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["latency_seconds"] = round(self.latency_seconds, 3)
        return data


_COUNTERS = ("calls", "protocol_failures", "schema_failures", "prompt_tokens", "completion_tokens",
             "estimated_token_calls", "attempts", "attempts_passed", "retries_triggered",
             *STRUCTURED_OUTCOME_COUNTERS.values())


class RoleMetrics:
    """The counters of one LLMClient (one engine). ``take_unreported`` gives
    everything recorded since the previous report, so each run's trace row
    carries the calls made since the last row - including calls made
    before a unit run started (a controller's structured planning, milestone
    planning) - and nothing is reported twice."""

    def __init__(self) -> None:
        self._entries: Dict[Tuple[str, str, str], RoleRuntimeMetrics] = {}
        self._last_runtime: Dict[Tuple[str, str], Tuple[str, bool]] = {}
        self._reported: Dict[Tuple[str, str, str], RoleRuntimeMetrics] = {}

    def take_unreported(self) -> List[Dict[str, Any]]:
        """The rows recorded since the last call (see ``since``)."""
        rows = self.since(self._reported)
        self._reported = self.snapshot()
        return rows

    def _entry(self, role: str, model: str, digest: str, exact: bool) -> RoleRuntimeMetrics:
        key = (role, model, digest)
        entry = self._entries.get(key)
        if entry is None:
            entry = self._entries[key] = RoleRuntimeMetrics(role, model, digest, exact)
        return entry

    def record_call(self, *, model: str, runtime_digest: Optional[str], runtime_exact: bool, status: str,
                    latency_seconds: float, prompt_tokens: int, completion_tokens: int,
                    tokens_estimated: bool, role: Optional[str] = None) -> None:
        role = role or current_model_role()
        digest = runtime_digest if runtime_exact and runtime_digest else UNAVAILABLE_RUNTIME
        entry = self._entry(role, model, digest, bool(runtime_exact))
        entry.calls += 1
        if status in PROTOCOL_FAILURE_STATUSES:
            entry.protocol_failures += 1
        entry.latency_seconds += max(0.0, float(latency_seconds or 0.0))
        entry.prompt_tokens += int(prompt_tokens or 0)
        entry.completion_tokens += int(completion_tokens or 0)
        if tokens_estimated:
            entry.estimated_token_calls += 1
        self._last_runtime[(role, model)] = (digest, bool(runtime_exact))

    def record_schema_failure(self, *, model: str, role: Optional[str] = None) -> None:
        """A response the caller rejected against its role contract, charged
        to the runtime that produced it (the role's last call to ``model``)."""
        role = role or current_model_role()
        digest, exact = self._last_runtime.get((role, model), (UNAVAILABLE_RUNTIME, False))
        self._entry(role, model, digest, exact).schema_failures += 1

    def record_structured_outcome(self, *, model: str, outcome: str, role: Optional[str] = None) -> None:
        """One validated structured response's typed outcome, charged to the
        runtime that produced it (the role's last call to ``model``). A model
        fault (malformed output, structured validation failure) is also a
        schema failure."""
        counter = STRUCTURED_OUTCOME_COUNTERS[outcome]
        role = role or current_model_role()
        digest, exact = self._last_runtime.get((role, model), (UNAVAILABLE_RUNTIME, False))
        entry = self._entry(role, model, digest, exact)
        setattr(entry, counter, getattr(entry, counter) + 1)
        if outcome in MODEL_FAULT_OUTCOMES:
            entry.schema_failures += 1

    def record_attempt(self, *, role: str, model: str, runtime_digest: str, runtime_exact: bool,
                       attempt_number: int, passed: bool) -> None:
        """One attempt's deterministic gate outcome, charged to the runtime
        that generated its candidate. A failed attempt triggers a retry."""
        digest = runtime_digest if runtime_exact and runtime_digest else UNAVAILABLE_RUNTIME
        entry = self._entry(role, model, digest, bool(runtime_exact))
        entry.attempts += 1
        if passed:
            entry.attempts_passed += 1
        else:
            entry.retries_triggered += 1
        if attempt_number == 1:
            entry.first_pass_success = bool(passed)

    def snapshot(self) -> Dict[Tuple[str, str, str], RoleRuntimeMetrics]:
        return {key: RoleRuntimeMetrics(**asdict(entry)) for key, entry in self._entries.items()}

    def since(self, baseline: Optional[Dict[Tuple[str, str, str], RoleRuntimeMetrics]]) -> List[Dict[str, Any]]:
        """What was recorded after ``baseline`` (a ``snapshot()``), sorted by
        role, model and runtime; entries with nothing new are left out."""
        baseline = baseline or {}
        rows: List[Dict[str, Any]] = []
        for key in sorted(self._entries):
            entry = self._entries[key]
            before = baseline.get(key)
            delta = RoleRuntimeMetrics(**asdict(entry))
            if before is not None:
                for name in _COUNTERS:
                    setattr(delta, name, getattr(entry, name) - getattr(before, name))
                delta.latency_seconds = entry.latency_seconds - before.latency_seconds
                if before.first_pass_success is not None:
                    delta.first_pass_success = None
            if (delta.calls or delta.schema_failures or delta.attempts
                    or any(getattr(delta, name) for name in STRUCTURED_OUTCOME_COUNTERS.values())):
                rows.append(delta.to_dict())
        return rows


def shared_runtime_groups(role_runtimes: Dict[str, Tuple[str, str, bool]]) -> List[Dict[str, Any]]:
    """Roles grouped by the exact runtime they call ({role: (model, digest,
    exact)}). A runtime that is not exactly identified is grouped by model
    alias and marked unverified: two such roles may or may not share it."""
    groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for role in sorted(role_runtimes):
        model, digest, exact = role_runtimes[role]
        key = ("runtime", digest) if exact else ("alias", model.casefold())
        group = groups.setdefault(key, {"model": model, "runtime_digest": digest if exact else None,
                                         "identity": "exact" if exact else "unverified_alias", "roles": []})
        group["roles"].append(role)
    return [groups[key] for key in sorted(groups)]


ROLE_INDEPENDENCE_REQUIRED = "ROLE_INDEPENDENCE_REQUIRED"


def role_runtimes(config: Any, *, fresh: bool = False) -> Dict[str, Tuple[str, str, bool]]:
    """{role: (model, runtime digest, exact)} for each role's own binding
    (the Developer's is the primary llm). A runtime that cannot be resolved
    exactly is ("model", "unavailable", False)."""
    from kriya.core.model_qualification import role_models
    from kriya.core.model_runtime import resolve_configured_model_runtime

    resolved: Dict[str, Tuple[str, bool]] = {}
    result: Dict[str, Tuple[str, str, bool]] = {}
    for role, models in role_models(config).items():
        model = models[0]
        key = model.casefold()
        if key not in resolved:
            try:
                fingerprint = resolve_configured_model_runtime(config, model, fresh=fresh)
                resolved[key] = (fingerprint.digest, bool(fingerprint.exact))
            except Exception:  # an unreachable runtime is undeterminable, not shared or distinct
                resolved[key] = (UNAVAILABLE_RUNTIME, False)
        digest, exact = resolved[key]
        result[role] = (model, digest if exact else UNAVAILABLE_RUNTIME, exact)
    return result


def independence_violations(config: Any, runtimes: Dict[str, Tuple[str, str, bool]]) -> List[str]:
    """Why ``model_policy.independent_roles`` is not met ([] when it is, or
    when nothing is required). A required role must run on an exactly
    identified runtime distinct from the Developer's; a runtime that cannot
    be identified exactly cannot prove independence."""
    required = list(getattr(getattr(config, "model_policy", None), "independent_roles", []) or [])
    if not required:
        return []
    developer_model, developer_digest, developer_exact = runtimes["developer"]
    violations: List[str] = []
    if not developer_exact:
        violations.append(f"the Developer's runtime ({developer_model}) is not exactly identified")
    for role in required:
        model, digest, exact = runtimes[role]
        if not exact:
            violations.append(f"{role}: runtime of {model} is not exactly identified, so independence "
                              "from the Developer cannot be shown")
        elif developer_exact and digest == developer_digest:
            violations.append(f"{role}: shares the Developer's exact runtime ({model}, {digest[:12]})")
    return violations


METRICS_TABLE_VERSION = 1


def metrics_digest(table: Dict[str, Any]) -> str:
    import hashlib
    import json

    payload = {key: value for key, value in table.items() if key != "digest"}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def runs_with_role_metrics(trace_db: str) -> List[Tuple[str, List[Dict[str, Any]]]]:
    """[(run_id, model.role_metrics rows)] of every finished run in
    ``trace_db`` that recorded them (read-only)."""
    import json
    import os
    import sqlite3

    if not os.path.exists(trace_db):
        return []
    connection = sqlite3.connect(f"file:{trace_db}?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT run_id, run_events FROM runs ORDER BY run_id").fetchall()
    finally:
        connection.close()
    runs = []
    for run_id, events_json in rows:
        try:
            events = json.loads(events_json or "[]")
        except ValueError:
            continue
        metric_rows = [row for event in events if event.get("kind") == "model.role_metrics"
                       for row in (event.get("details") or {}).get("rows", [])]
        if metric_rows:
            runs.append((run_id, metric_rows))
    return runs


def aggregate_role_metrics(runs: Sequence[Tuple[str, Sequence[Dict[str, Any]]]]) -> Dict[str, Any]:
    """Deterministic aggregation of ``model.role_metrics`` rows from finished
    runs ([(run_id, rows)]) into the routing table: summed counters per
    (role, model, runtime digest), with the runs it covers."""
    totals: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    # Rows written before a counter existed read it as 0.
    counters = ("calls", "protocol_failures", "schema_failures", "prompt_tokens", "completion_tokens",
                "estimated_token_calls", "attempts", "attempts_passed", "retries_triggered",
                *STRUCTURED_OUTCOME_COUNTERS.values(), "first_pass_runs", "first_pass_successes")
    run_ids = []
    for run_id, rows in sorted(runs, key=lambda item: item[0]):
        run_ids.append(run_id)
        for row in rows:
            key = (row["role"], row["model"], row["runtime_digest"])
            total = totals.setdefault(key, {"role": key[0], "model": key[1], "runtime_digest": key[2],
                                            "runtime_exact": bool(row.get("runtime_exact")),
                                            "latency_seconds": 0.0, **{name: 0 for name in counters}})
            for name in counters[:-2]:
                total[name] += int(row.get(name, 0) or 0)
            total["latency_seconds"] = round(total["latency_seconds"] + float(row.get("latency_seconds", 0.0)), 3)
            if row.get("first_pass_success") is not None:
                total["first_pass_runs"] += 1
                total["first_pass_successes"] += int(bool(row["first_pass_success"]))
    rows = [totals[key] for key in sorted(totals)]
    body = {"version": METRICS_TABLE_VERSION, "runs": run_ids, "rows": rows}
    body["digest"] = metrics_digest(body)
    return body


