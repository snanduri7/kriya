"""PRD-019: evidence-based model routing (opt-in, ``model_policy.routing``).

Routing picks, per role, one runtime from the operator's configured
candidates. It is deterministic and uses only these inputs:

- the role's required capabilities and protocol needs (a role that parses
  JSON needs a JSON-mode profile);
- the role's safe context need (never a smaller window than the role's
  configured binding, unless the operator sets ``min_context_window``);
- the qualification status of the candidate's exact runtime for that role;
- Kriya's own measured outcomes for that role on that exact runtime
  (PRD-018 metrics, aggregated between runs by ``kriya model metrics
  --aggregate`` into a table; never updated during a run).

A model's name, parameter size or a model's own judgment is never an input.
A candidate is eligible only when its runtime is exactly identified and
QUALIFIED for the role; a better historical score never makes an
unqualified runtime eligible. An explicit role binding
(``agent_llms.<role>.llm``) wins whenever it is qualified. Ties break by
operator order, then alias. With no eligible candidate the role keeps its
configured binding and the reason is recorded. ``frozen`` mode replays a
recorded route table: every role gets exactly its frozen runtime, or the
run is refused.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from kriya.core.role_metrics import METRICS_TABLE_VERSION, metrics_digest

# The routing table is the PRD-018 metrics table (role_metrics.aggregate_role_metrics).
ROUTING_TABLE_VERSION = METRICS_TABLE_VERSION
ROUTE_FROZEN_MISMATCH = "ROUTE_FROZEN_MISMATCH"
ROUTE_RESUME_MISMATCH = "ROUTE_RESUME_MISMATCH"
ROUTING_CANDIDATE_ALIAS_CONFLICT = "ROUTING_CANDIDATE_ALIAS_CONFLICT"
ROUTING_TABLE_INVALID = "ROUTING_TABLE_INVALID"

# Roles whose responses Kriya parses as JSON (agent.py call_with_escalation
# with json_mode=True): a candidate for them needs a JSON-mode profile.
ROLE_PROTOCOL_NEEDS: Dict[str, Tuple[str, ...]] = {
    "reviewer": ("json_mode",),
    "run_verifier": ("json_mode",),
    "spec_compliance": ("json_mode",),
    "skill_gap": ("json_mode",),
}


class RoutingError(RuntimeError):
    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class CandidateEvidence:
    """What routing knows about one candidate runtime for one role."""

    model: str
    order: int
    runtime_digest: Optional[str]
    runtime_exact: bool
    qualification: str
    qualification_reasons: Tuple[str, ...]
    json_mode: bool
    native_tool_calls: bool
    context_window: Optional[int]
    explicit: bool = False
    metrics: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class RouteDecision:
    role: str
    mode: str
    model: Optional[str]
    runtime_digest: Optional[str]
    source: str
    reason: str
    candidates: Tuple[Dict[str, Any], ...] = ()
    rejected: Tuple[Dict[str, Any], ...] = ()
    table_digest: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["candidates"] = list(self.candidates)
        data["rejected"] = list(self.rejected)
        return data


@dataclass(frozen=True)
class MetricScore:
    measured: bool
    success_rate: float = 0.0
    failure_rate: float = 0.0
    mean_latency: float = 0.0
    calls: int = 0


def score_metrics(role: str, row: Optional[Dict[str, Any]], min_calls: int) -> MetricScore:
    """A candidate's measured score for ``role``: the Developer by the share
    of its attempts that passed the deterministic gates, other roles by the
    share of calls with no protocol or schema failure. Below ``min_calls``
    calls it is unmeasured (ranked after every measured candidate)."""
    if not row or int(row.get("calls", 0)) < max(1, min_calls):
        return MetricScore(False, calls=int((row or {}).get("calls", 0)))
    calls = int(row["calls"])
    failures = int(row.get("protocol_failures", 0)) + int(row.get("schema_failures", 0))
    failure_rate = min(1.0, failures / calls)
    if role == "developer" and int(row.get("attempts", 0)) > 0:
        success = int(row.get("attempts_passed", 0)) / int(row["attempts"])
    else:
        success = 1.0 - failure_rate
    return MetricScore(True, round(success, 6), round(failure_rate, 6),
                       round(float(row.get("latency_seconds", 0.0)) / calls, 6), calls)


def rejection_reasons(role: str, candidate: CandidateEvidence, *, min_context_window: Optional[int]) -> List[str]:
    from kriya.core.model_qualification import QUALIFIED

    reasons: List[str] = []
    if not candidate.runtime_exact:
        reasons.append("runtime identity is not exact")
    if candidate.qualification != QUALIFIED:
        reasons.append(f"not qualified for {role} ({candidate.qualification}"
                       + (f": {'; '.join(candidate.qualification_reasons)}" if candidate.qualification_reasons else "")
                       + ")")
    for need in ROLE_PROTOCOL_NEEDS.get(role, ()):
        if not getattr(candidate, need):
            reasons.append(f"{role} needs {need}, which the capability profile does not have")
    if min_context_window and (candidate.context_window or 0) < min_context_window:
        reasons.append(f"context window {candidate.context_window} is below the role's need of {min_context_window}")
    return reasons


def choose_route(role: str, candidates: Sequence[CandidateEvidence], *, min_calls: int,
                 min_context_window: Optional[int], table_digest: Optional[str],
                 default_model: str) -> RouteDecision:
    """The route for ``role`` (evidence mode). Pure and deterministic."""
    considered, rejected, eligible = [], [], []
    for candidate in candidates:
        reasons = rejection_reasons(role, candidate, min_context_window=min_context_window)
        score = score_metrics(role, candidate.metrics, min_calls)
        row = {"model": candidate.model, "runtime_digest": candidate.runtime_digest, "explicit": candidate.explicit,
               "qualification": candidate.qualification, "score": asdict(score)}
        considered.append(row)
        if reasons:
            rejected.append({**row, "reasons": reasons})
        else:
            eligible.append((candidate, score))
    explicit = [item for item in eligible if item[0].explicit]
    if explicit:
        chosen, score = explicit[0]
        return RouteDecision(role, "evidence", chosen.model, chosen.runtime_digest, "explicit_override",
                             "the explicit role binding is qualified", tuple(considered), tuple(rejected),
                             table_digest)
    if not eligible:
        return RouteDecision(role, "evidence", default_model, None, "configured_default",
                             "no candidate is eligible; the role keeps its configured binding",
                             tuple(considered), tuple(rejected), table_digest)
    ranked = sorted(eligible, key=lambda item: (
        not item[1].measured, -item[1].success_rate, item[1].failure_rate, item[1].mean_latency,
        item[0].order, item[0].model,
    ))
    chosen, score = ranked[0]
    if score.measured:
        reason = (f"highest measured {'first-pass success' if role == 'developer' else 'clean-call'} rate "
                  f"{score.success_rate:.3f} over {score.calls} calls among eligible candidates")
    else:
        reason = "no eligible candidate has enough measured calls; first eligible candidate in operator order"
    return RouteDecision(role, "evidence", chosen.model, chosen.runtime_digest, "evidence", reason,
                         tuple(considered), tuple(rejected), table_digest)


def frozen_route(role: str, frozen: Dict[str, Any], candidates: Sequence[CandidateEvidence], *,
                 table_digest: Optional[str], reason_code: str = ROUTE_FROZEN_MISMATCH,
                 label: str = "frozen") -> RouteDecision:
    """Frozen mode: exactly the recorded runtime, still qualified, or a
    typed refusal. Resume replays a checkpoint's routes the same way
    (``resumed_route``)."""
    from kriya.core.model_qualification import QUALIFIED

    entry = (frozen.get("routes") or {}).get(role)
    if entry is None:
        raise RoutingError(reason_code, f"the {label} route table has no route for {role}")
    match = next((c for c in candidates if c.model == entry.get("model")), None)
    if match is None:
        raise RoutingError(reason_code,
                           f"{role}: {label} model {entry.get('model')} is no longer a configured candidate")
    if not match.runtime_exact or match.runtime_digest != entry.get("runtime_digest"):
        raise RoutingError(reason_code,
                           f"{role}: {match.model} is now runtime {match.runtime_digest or 'unavailable'}, "
                           f"{label} as {entry.get('runtime_digest')}")
    if match.qualification != QUALIFIED:
        raise RoutingError(reason_code, f"{role}: {label} runtime {match.model} is {match.qualification}")
    return RouteDecision(role, "frozen", match.model, match.runtime_digest, "frozen_table",
                         "replayed from the frozen route table", table_digest=table_digest)


def resumed_route(role: str, saved: Dict[str, Any], candidates: Sequence[CandidateEvidence], *,
                  default_model: str) -> RouteDecision:
    """Resume: the route the checkpoint's run used for ``role``, if it still
    holds (the same model on the same exact, still QUALIFIED runtime; a role
    that kept its configured binding keeps it), or ROUTE_RESUME_MISMATCH.
    The current metrics table is not consulted."""
    entry = (saved.get("routes") or {}).get(role)
    if entry is None:
        raise RoutingError(ROUTE_RESUME_MISMATCH, f"the resumed run recorded no route for {role}")
    if entry.get("source") == "configured_default":
        if entry.get("model") != default_model:
            raise RoutingError(ROUTE_RESUME_MISMATCH,
                               f"{role}: the resumed run kept {entry.get('model')}, now configured {default_model}")
        return RouteDecision(role, "resume", default_model, None, "configured_default",
                             "kept the configured binding, as the resumed run did")
    replay = frozen_route(role, saved, candidates, table_digest=saved.get("table_digest"),
                          reason_code=ROUTE_RESUME_MISMATCH, label="the resumed run's")
    return RouteDecision(role, "resume", replay.model, replay.runtime_digest, entry.get("source") or "evidence",
                         "reused the resumed run's route", table_digest=saved.get("table_digest"))


def resume_routes_from(plan: Optional["RoutingPlan"]) -> Optional[Dict[str, Any]]:
    """What a checkpoint records so a resume reuses the run's routes: each
    routed role's model, exact runtime and source (None without routing)."""
    if plan is None or not plan.decisions:
        return None
    return {
        "version": FROZEN_ROUTES_VERSION,
        "routes": {role: {"model": d.model, "runtime_digest": d.runtime_digest, "source": d.source}
                   for role, d in sorted(plan.decisions.items())},
        "table_digest": plan.table_digest,
    }


# --- the between-run metrics table ---------------------------------------------------------------

def table_digest(table: Dict[str, Any]) -> str:
    return metrics_digest(table)


def load_table(path: str) -> Dict[str, Any]:
    """A routing table or frozen route table; a missing file is an empty
    table, a corrupt or tampered one is refused."""
    if not os.path.exists(path):
        return {"version": ROUTING_TABLE_VERSION, "runs": [], "rows": [], "digest": None}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            table = json.load(handle)
    except (OSError, ValueError) as error:
        raise RoutingError(ROUTING_TABLE_INVALID, f"{path} cannot be read: {error}") from error
    if table.get("version") != ROUTING_TABLE_VERSION or table.get("digest") != table_digest(table):
        raise RoutingError(ROUTING_TABLE_INVALID, f"{path} is not a valid Kriya routing table (version or digest)")
    return table


def write_table(path: str, table: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(table, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)


def metrics_row(table: Dict[str, Any], role: str, runtime_digest: Optional[str]) -> Optional[Dict[str, Any]]:
    if not runtime_digest:
        return None
    return next((row for row in table.get("rows", [])
                 if row.get("role") == role and row.get("runtime_digest") == runtime_digest), None)


@dataclass
class RoutingPlan:
    decisions: Dict[str, RouteDecision] = field(default_factory=dict)
    table_digest: Optional[str] = None

    def to_events(self) -> List[Dict[str, Any]]:
        return [self.decisions[role].to_dict() for role in sorted(self.decisions)]


# --- planning and applying routes ---------------------------------------------------------------

ROUTING_MODES = ("off", "evidence", "frozen")
FROZEN_ROUTES_VERSION = 1


ROUTING_TABLE_FILENAME = "model_routing_table.json"


def routing_table_path(config: Any) -> str:
    """Where the between-run metrics table routing reads lives:
    ``model_policy.routing.table_path``, else the state directory."""
    from kriya.core.state_paths import resolve_state_directory

    configured = config.model_policy.routing.table_path
    return configured or os.path.join(resolve_state_directory(config)[0], ROUTING_TABLE_FILENAME)


def place_candidate(config: Any, role: str, candidate: Any) -> Any:
    """A copy of ``config`` with ``candidate`` (a FallbackModelConfig-shaped
    binding) as ``role``'s own model: the primary ``llm`` for the Developer,
    ``agent_llms.<role>.llm`` otherwise. An unset ``max_tokens`` is the shared
    DEFAULT_OUTPUT_TOKENS (as for any binding), never the primary's own
    value; other unset fields keep the primary's. The runtime identity of a model depends on where it
    is bound (its capability-profile provenance), so a candidate is always
    assessed, qualified and run in this placement."""
    from kriya.config.config import AgentModelConfig
    from kriya.core.model_runtime import binding_output_tokens

    placed = config.model_copy(deep=True)
    update = {name: getattr(candidate, name) for name in type(candidate).model_fields
              if getattr(candidate, name) is not None}
    update["max_tokens"] = binding_output_tokens(config, candidate)
    binding = placed.llm.model_copy(update=update, deep=True)
    if role == "developer":
        placed.llm = binding
    else:
        role_cfg = getattr(placed.agent_llms, role, None) or AgentModelConfig()
        role_cfg = role_cfg.model_copy(update={"llm": binding}, deep=True)
        setattr(placed.agent_llms, role, role_cfg)
    return placed


def role_binding(config: Any, role: str) -> Any:
    if role == "developer":
        return config.llm
    role_cfg = getattr(config.agent_llms, role, None)
    return role_cfg.llm if role_cfg is not None and role_cfg.llm is not None else config.llm


def _served_window(fingerprint: Any, binding: Any) -> Optional[int]:
    return getattr(fingerprint, "effective_context_window", None) or getattr(binding, "context_window", None)


def candidate_evidence(config: Any, role: str, model: str, *, order: int, explicit: bool,
                       table: Dict[str, Any]) -> CandidateEvidence:
    """Evidence for ``model`` bound as ``role`` in ``config`` (already placed)."""
    from kriya.core.model_capabilities import capabilities_for_model
    from kriya.core.model_qualification import assess, required_capabilities
    from kriya.core.model_runtime import resolve_configured_model_runtime

    binding = role_binding(config, role)
    caps = capabilities_for_model(config, model)
    try:
        fingerprint = resolve_configured_model_runtime(
            config, model, base_url=binding.base_url, api_key=binding.api_key, extra_body=binding.extra_body or {},
        )
        assessment = assess(fingerprint, required_capabilities(config, role, model))
        exact, digest = bool(fingerprint.exact), fingerprint.digest if fingerprint.exact else None
        status, reasons = assessment.status, tuple(assessment.reasons)
        window = _served_window(fingerprint, binding)
    except Exception as error:  # an unreachable runtime is simply not eligible
        exact, digest, status, reasons, window = False, None, "UNAVAILABLE", (str(error),), binding.context_window
    return CandidateEvidence(
        model=model, order=order, runtime_digest=digest, runtime_exact=exact, qualification=status,
        qualification_reasons=reasons, json_mode=bool(caps.json_mode), native_tool_calls=bool(caps.native_tool_calls),
        context_window=window, explicit=explicit, metrics=metrics_row(table, role, digest),
    )


def plan_routes(config: Any, *, mode: Optional[str] = None,
                resume_routes: Optional[Dict[str, Any]] = None) -> RoutingPlan:
    """The route of every role listed in ``model_policy.routing.roles``.
    ``mode`` overrides the configured mode (``kriya model routes`` shows the
    evidence decision even while routing is off). ``resume_routes`` (the
    routes a resumed checkpoint's run used, ``resume_routes_from``) makes
    evidence routing sticky across the resume: each role gets exactly its
    saved route or the resume is refused (ROUTE_RESUME_MISMATCH)."""
    from kriya.config.config import routing_alias_conflicts
    from kriya.core.model_runtime import resolve_configured_model_runtime

    routing = config.model_policy.routing
    mode = mode or routing.mode
    plan = RoutingPlan()
    if mode == "off" or not routing.roles:
        return plan
    conflicts = routing_alias_conflicts(config)  # also a config error; checked again for configs built in code
    if conflicts:
        raise RoutingError(ROUTING_CANDIDATE_ALIAS_CONFLICT, "; ".join(conflicts))
    resuming = resume_routes is not None and mode == "evidence"
    # A resume never consults the current metrics table (its routes are the run's).
    table = ({"rows": [], "digest": resume_routes.get("table_digest")} if resuming
             else load_table(routing_table_path(config)))
    plan.table_digest = table.get("digest")
    frozen = load_frozen_routes(routing.frozen_routes_path) if mode == "frozen" else None
    by_alias = {candidate.model: candidate for candidate in routing.candidates}
    if resuming and set(resume_routes.get("routes") or {}) != set(routing.roles):
        raise RoutingError(ROUTE_RESUME_MISMATCH,
                           f"the resumed run routed {sorted(resume_routes.get('routes') or {})}, "
                           f"the configuration routes {sorted(routing.roles)}")
    for role, aliases in sorted(routing.roles.items()):
        default = role_binding(config, role)
        evidence: List[CandidateEvidence] = []
        role_cfg = getattr(config.agent_llms, role, None) if role != "developer" else None
        if role_cfg is not None and role_cfg.llm is not None:
            evidence.append(candidate_evidence(config, role, role_cfg.llm.model, order=-1, explicit=True,
                                               table=table))
        for order, alias in enumerate(aliases):
            placed = place_candidate(config, role, by_alias[alias])
            evidence.append(candidate_evidence(placed, role, alias, order=order, explicit=False, table=table))
        if frozen is not None:
            plan.decisions[role] = frozen_route(role, frozen, evidence, table_digest=frozen.get("digest"))
            continue
        if resuming:
            plan.decisions[role] = resumed_route(role, resume_routes, evidence, default_model=default.model)
            continue
        need = routing.min_context_window
        if need is None:
            try:
                need = _served_window(resolve_configured_model_runtime(config, default.model), default)
            except Exception:
                need = default.context_window
        plan.decisions[role] = choose_route(role, evidence, min_calls=routing.min_calls, min_context_window=need,
                                            table_digest=plan.table_digest, default_model=default.model)
    return plan


def apply_routes(config: Any, plan: RoutingPlan) -> Any:
    """``config`` with every routed role bound to its chosen candidate (a
    copy; roles that keep their binding are unchanged)."""
    by_alias = {candidate.model: candidate for candidate in config.model_policy.routing.candidates}
    routed = config
    for role, decision in sorted(plan.decisions.items()):
        if decision.source in ("evidence", "frozen_table") and decision.model in by_alias:
            routed = place_candidate(routed, role, by_alias[decision.model])
    return routed


def frozen_routes_from(plan: RoutingPlan) -> Dict[str, Any]:
    """The frozen route table of ``plan``: each role's chosen model and exact
    runtime (a role left on its configured binding cannot be frozen)."""
    routes = {}
    for role, decision in sorted(plan.decisions.items()):
        if not decision.runtime_digest:
            raise RoutingError(ROUTE_FROZEN_MISMATCH,
                               f"{role} has no exactly identified routed runtime to freeze ({decision.reason})")
        routes[role] = {"model": decision.model, "runtime_digest": decision.runtime_digest}
    body = {"version": FROZEN_ROUTES_VERSION, "routes": routes, "table_digest": plan.table_digest}
    body["digest"] = metrics_digest(body)
    return body


def load_frozen_routes(path: Optional[str]) -> Dict[str, Any]:
    if not path or not os.path.exists(path):
        raise RoutingError(ROUTE_FROZEN_MISMATCH, f"frozen routing needs a frozen route table; {path!r} does not exist")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            table = json.load(handle)
    except (OSError, ValueError) as error:
        raise RoutingError(ROUTING_TABLE_INVALID, f"{path} cannot be read: {error}") from error
    if table.get("version") != FROZEN_ROUTES_VERSION or table.get("digest") != metrics_digest(table):
        raise RoutingError(ROUTING_TABLE_INVALID, f"{path} is not a valid Kriya frozen route table")
    return table
