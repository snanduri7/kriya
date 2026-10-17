"""The llm_chain fallback decisions of a Developer attempt (full modularization W4, E6; PRD-017, LR-R1-P1).

Moved verbatim (no decision change) from ``kriya/workflow/attempt.py``:

- ``_select_developer_fallback``: the configured fallback an attempt escalates to, chosen before its prompt is built
  (configured order kept; a fallback proven unable to serve the run is skipped with its reasons; a fallback that cannot
  serve this attempt's patch-only targets is skipped for this attempt; the typed terminal failure when none remains);
- ``_substitute_for_required_patch``: at the call, the next configured fallback that can return what the attempt may
  write, when the one the call goes to cannot;
- their records: ``_record_fallback_selection`` (the model.fallback_selection run event),
  ``_raise_fallback_incompatible`` (the model.fallback_incompatible event and the typed FALLBACK_MODEL_INCOMPATIBLE
  failure) and ``_fallback_rejection`` (one skipped fallback's record).

``kriya/workflow/attempt.py`` keeps every name (forwarders that build ``FallbackDeps`` at call time; a re-export of
``_fallback_rejection``), so the bodies reach attempt.py's names - the request profile, the chain binding, the
completeness gate's patch-only files, each other - through ``kriya.workflow.attempt`` exactly as before. The
model_transition names the bodies imported function-locally (all-import cycle) are loaders called where they were
imported; ``resolve_fallback_model`` (attribution, all-import cycle) and ``logger`` arrive the same way. This module
never imports ``kriya.workflow.attempt``, ``workflow``, ``workflow_controller``, ``kriya.cli`` or an all-import-cycle
module. ``RunState``/``AttemptCtx`` are spelled ``Any`` (kriya/workflow/generation_service.py says why).
"""
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from kriya.core.attempt_evidence import scope as attempt_evidence_scope
from kriya.workflow.failure import Failure, QualityGateFailure
from kriya.workflow.run_events import EventAuthority, RunEvent

RunState = Any    # kriya.workflow.state.GenerationState
AttemptCtx = Any  # kriya.workflow.attempt.AttemptContext


@dataclass(frozen=True)
class FallbackDeps:
    """What the moved fallback decisions may use, resolved through ``kriya.workflow.attempt`` by
    ``attempt._fallback_deps()`` when a forwarder runs. Every field is required."""

    logger: Any                                   # kriya.workflow.attempt.logger
    resolve_fallback_model: Callable[..., Any]    # kriya.workflow.attribution (all-import cycle)
    _developer_request_profile: Callable[..., Any]
    _chain_binding: Callable[..., Any]
    _patch_required_files: Callable[..., Any]
    _fallback_rejection: Callable[..., Any]
    _record_fallback_selection: Callable[..., Any]
    _raise_fallback_incompatible: Callable[..., Any]
    # the bodies' function-local imports of kriya.workflow.model_transition, done where they were done
    load_fallback_model_incompatible: Callable[[], str]
    load_fallback_selection: Callable[[], Tuple[Any, Any, Any]]
    load_fallback_incompatibilities: Callable[[], Any]


def _fallback_rejection(profile: Any, reasons: List[str]) -> Dict[str, Any]:
    return {"model": profile.model, "reasons": list(reasons), "profile_digest": profile.digest}


def _record_fallback_selection(deps: FallbackDeps, state: RunState, *, phase: str, requested: str, selected: Optional[str],
                               rejected: List[Dict[str, Any]], primary_route: bool = False) -> None:
    """PRD-017: why configured fallbacks were skipped and which one serves
    the attempt (the model.fallback_selection run event)."""
    FALLBACK_MODEL_INCOMPATIBLE = deps.load_fallback_model_incompatible()

    state.record_event(RunEvent(
        kind="model.fallback_selection",
        attempt=state.attempt_number,
        source="attempt.fallback_selection",
        authority=EventAuthority.ADVISORY,
        message=(
            f"Fallback {requested} skipped ({', '.join(r['model'] for r in rejected)} incompatible); "
            + (f"{selected} selected" if selected
               else "the full-set attempt stays on the primary" if primary_route
               else "no configured fallback remains")
        ),
        details={"phase": phase, "requested": requested, "selected": selected, "rejected": rejected,
                 "reason_code": FALLBACK_MODEL_INCOMPATIBLE, "primary_route": primary_route},
    ))


def _raise_fallback_incompatible(deps: FallbackDeps, state: RunState, requested: str, rejected: List[Dict[str, Any]]) -> None:
    """The terminal typed failure: no remaining configured fallback can
    serve this attempt; every skipped fallback's reasons are carried."""
    FALLBACK_MODEL_INCOMPATIBLE = deps.load_fallback_model_incompatible()

    detail = "; ".join(f"{r['model']}: {'; '.join(r['reasons'])}" for r in rejected)
    message = (f"{FALLBACK_MODEL_INCOMPATIBLE}: no remaining configured fallback from {requested} can serve "
               f"this attempt - {detail}")
    reasons = [reason for r in rejected for reason in r["reasons"]]
    state.record_event(RunEvent(
        kind="model.fallback_incompatible",
        attempt=state.attempt_number,
        source="attempt.fallback_selection",
        authority=EventAuthority.ADVISORY,
        message=message,
        details={"reason_code": FALLBACK_MODEL_INCOMPATIBLE, "model": requested, "reasons": reasons,
                 "rejected": rejected},
    ))
    deps.logger.error(message)
    raise QualityGateFailure(Failure(
        type="fallback_incompatible",
        message=message,
        raw_output=message,
        source="orchestrator",
        attempt=state.attempt_number,
        diagnostics={"reason_code": FALLBACK_MODEL_INCOMPATIBLE, "model": requested, "reasons": reasons,
                     "rejected": rejected},
    ))


def _select_developer_fallback(deps: FallbackDeps, state: RunState, ctx: AttemptCtx, retry_count: int, *,
                              targets: Iterable[str] = (), allow_primary: bool = False) -> Any:
    """PRD-017: the llm_chain fallback this attempt escalates to, chosen
    before its prompt is built (the prompt is sized for the chosen model's
    window). The configured order is kept (resolve_fallback_model); a
    fallback proven unable to serve the run - a failed Developer
    qualification case, the production profile without QUALIFIED, no
    prompt room - is skipped with its reasons (never re-evaluated or sent a
    request), and the next configured one is taken. None for the primary;
    the typed terminal failure when no configured fallback remains.

    LR-R1-P1 Option (b): a fallback that cannot serve THIS attempt's
    ``targets`` because one is patch-only for it (whole-file representation
    impossible in its request, model_transition.patch_only_files) and it
    returns whole files only is skipped for this attempt (not a run-wide
    verdict). When every remaining fallback was skipped for that reason
    alone and ``allow_primary`` (the full-set route), the attempt stays on
    the primary (None) instead of ending; a run-wide rejection among them
    keeps PRD-017's terminal semantics."""
    existing_target_sizes, fallback_incompatibilities, patch_only_files = deps.load_fallback_selection()

    if retry_count <= 0 or not ctx.chain:
        return None
    start = min(retry_count - 1, len(ctx.chain) - 1)
    requested = ctx.chain[start]
    sizes = existing_target_sizes(ctx.worktree_path, targets)
    newly_rejected: List[Dict[str, Any]] = []
    patch_rejected: Dict[str, Dict[str, Any]] = {}
    for binding in ctx.chain[start:]:
        if binding.model in state.incompatible_fallbacks:
            continue
        profile = deps._developer_request_profile(ctx, binding.model)
        reasons = fallback_incompatibilities(ctx.kernel.config, profile)
        if reasons:
            state.incompatible_fallbacks[binding.model] = reasons
            newly_rejected.append(deps._fallback_rejection(profile, reasons))
            continue
        files = patch_only_files(ctx.kernel.config, binding, sizes)[0] if sizes else ()
        patch_reasons = fallback_incompatibilities(ctx.kernel.config, profile, patch_required_files=files) \
            if files else []
        if not patch_reasons:
            break
        patch_rejected[binding.model] = {**deps._fallback_rejection(profile, patch_reasons),
                                         "patch_only_files": list(files)}
    skipped = {*state.incompatible_fallbacks, *patch_rejected}
    selected = deps.resolve_fallback_model(retry_count, ctx.chain, skipped)
    attempt_evidence_scope.record_fallback_decision({
        "phase": "escalation", "retry_count": retry_count, "requested": requested.model,
        "selected": selected.model if selected is not None else None,
        "newly_rejected": newly_rejected, "previously_rejected": sorted(
            model for model in state.incompatible_fallbacks if model not in {r["model"] for r in newly_rejected}),
        "patch_rejected": list(patch_rejected.values()),
    })
    if selected is requested:
        return selected
    evaluated_now = {item["model"]: item for item in newly_rejected}
    rejected: Dict[str, Dict[str, Any]] = {}
    for binding in ctx.chain[start:]:
        if binding.model in patch_rejected:
            rejected[binding.model] = patch_rejected[binding.model]
        elif binding.model in state.incompatible_fallbacks and binding.model not in rejected:
            rejected[binding.model] = evaluated_now.get(binding.model) or {
                "model": binding.model, "reasons": list(state.incompatible_fallbacks[binding.model]),
            }
    rejected = list(rejected.values())
    stays_on_primary = selected is None and allow_primary and all(r["model"] in patch_rejected for r in rejected)
    deps._record_fallback_selection(state, phase="escalation", requested=requested.model,
                               selected=selected.model if selected is not None else None, rejected=rejected,
                               primary_route=stays_on_primary)
    if selected is None and not stays_on_primary:
        deps._raise_fallback_incompatible(state, requested.model, rejected)
    return selected


def _substitute_for_required_patch(deps: FallbackDeps, state: RunState, ctx: AttemptCtx, kwargs: Dict[str, Any],
                                   profile: Any, reasons: List[str]) -> Tuple[Dict[str, Any], Any]:
    """PRD-017: the fallback this call goes to cannot return what the
    attempt may write (an anchored patch the completeness gate requires, a
    fact of the prompt already built). The next configured fallback after
    it that can (skipping those already proven incompatible) serves the
    call instead - no request is sent to the incompatible one; the typed
    terminal failure when none remains. The prompt was sized for the
    requested fallback's window; PRD-016's dispatch check still refuses it
    before inference if it does not fit the substitute's."""
    fallback_incompatibilities = deps.load_fallback_incompatibilities()

    current = deps._chain_binding(ctx, kwargs.get("model_override"))
    patch_files = deps._patch_required_files(state, ctx, kwargs)
    rejected = [deps._fallback_rejection(profile, reasons)]
    index = next(i for i, binding in enumerate(ctx.chain) if binding is current)
    for binding in ctx.chain[index + 1:]:
        if binding.model in state.incompatible_fallbacks:
            rejected.append({"model": binding.model, "reasons": list(state.incompatible_fallbacks[binding.model])})
            continue
        candidate = deps._developer_request_profile(ctx, binding.model)
        candidate_reasons = fallback_incompatibilities(ctx.kernel.config, candidate, patch_required_files=patch_files)
        if candidate_reasons:
            rejected.append(deps._fallback_rejection(candidate, candidate_reasons))
            continue
        deps._record_fallback_selection(state, phase="call", requested=current.model, selected=binding.model,
                                   rejected=rejected)
        if state.model_hops and state.model_hops[-1] == current.model:
            state.model_hops[-1] = binding.model
        substituted = {**kwargs, "model_override": binding.model, "base_url_override": binding.base_url,
                       "api_key_override": binding.api_key, "extra_body_override": binding.extra_body}
        return substituted, candidate
    deps._record_fallback_selection(state, phase="call", requested=current.model, selected=None, rejected=rejected)
    deps._raise_fallback_incompatible(state, current.model, rejected)
    raise AssertionError("unreachable")


