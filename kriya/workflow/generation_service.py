"""The Developer request assembly and generation of one attempt (full modularization W4, E6).

Moved verbatim from ``kriya/workflow/attempt.py``:

- ``_run_developer_generation`` / ``_run_developer_generation_as_developer``: every Developer generation (attributed to
  the developer role, PRD-018): the model the call goes to and its request profile (``_enter_developer_model``,
  PRD-017), the qualified retry identity (``_require_qualified_retry_identity``, MODEL-EVIDENCE-HARDENING-001), the
  generation time budget, the opt-in read-only investigation pre-pass (``_maybe_run_developer_investigation``,
  DEV-INV-001), the edit capabilities, the request fit (DEVELOPER-PROMPT-FIT-001), the grounded output expectations
  (PRD-016) and the anchored-patch retry when a whole file cannot fit, and the call's timing and prompt-composition
  record;
- the request profile of a Developer call (``_developer_request_profile``) and the llm_chain entry it goes to
  (``_chain_binding``);
- the optional Developer sections (``_developer_optional_sections``: the planned files' current source,
  ``_planned_source_context``, the graph context and the fenced learned reference);
- the attempt-1 known-target package (``_target_package_with_goal_member_fallback``,
  ``_target_package_with_window_reserve``).

``kriya/workflow/attempt.py`` keeps every one of these names (a forwarder that builds ``GenerationDeps`` at call time,
or a re-export) so a test patch on ``kriya.workflow.attempt`` keeps intercepting the moved code: every collaborator the
bodies looked up in attempt.py's globals arrives in ``GenerationDeps``, resolved through ``kriya.workflow.attempt``
when the forwarder runs (``deps._enter_developer_model`` IS ``kriya.workflow.attempt._enter_developer_model``, and so
on, including ``logger`` and ``time``). Collaborators from the kernel's all-import cycle (``context_budget``,
``authority_escalation``, ``edit_capability``, ``prompt_composition``, ``model_transition``, ``investigation``,
``graph_retrieval``, ``memory.*``, the PROVIDER modules ``core.role_metrics``/``model_capabilities``/
``model_qualification``/``token_budget``) arrive the same way; the bodies' function-local imports of them are loaders
(``deps.load_*``), defined in attempt.py and called where the bodies imported them. This module never imports
``kriya.workflow.attempt``, ``workflow``, ``workflow_controller`` or ``kriya.cli``, nor any all-import-cycle module.

``RunState`` is the attempt's ``kriya.workflow.state.GenerationState`` and ``AttemptCtx`` its
``kriya.workflow.attempt.AttemptContext``; both are spelled ``Any`` (kriya/workflow/stage_context.py says why the
state module is not imported; AttemptContext lives in attempt.py, OI-46).
"""
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from kriya.core.attempt_evidence import scope as attempt_evidence_scope
from kriya.workflow.failure import Failure, QualityGateFailure
from kriya.workflow.run_events import EventAuthority, RunEvent

RunState = Any    # kriya.workflow.state.GenerationState
AttemptCtx = Any  # kriya.workflow.attempt.AttemptContext


@dataclass(frozen=True)
class GenerationDeps:
    """What the moved generation code may use, resolved through ``kriya.workflow.attempt`` by
    ``attempt._generation_deps()`` when a forwarder runs. Every field is required: a builder that omits one fails at
    construction, never at the first use."""

    logger: Any                               # kriya.workflow.attempt.logger
    time: Any                                 # kriya.workflow.attempt.time (the module: a patch on it is honoured)
    # context_budget / authority_escalation / edit_capability / prompt_composition / core.token_budget
    DeveloperRequestFit: Any
    OptionalSection: Any
    allocation_window: Callable[..., Any]
    investigation_evidence_char_budget: Callable[..., Any]
    build_code_context: Callable[..., Any]
    fenced_reference_section: Callable[..., Any]
    REASON_MINIMUM_AUTHORITY_UNFIT: str
    _reserve_graph_context_budget: Callable[..., Any]
    build_known_target_context: Callable[..., Any]
    grant_member_hints: Callable[..., Any]
    exact_window_reserve: Callable[..., Any]
    OutputBudgetUnsatisfiableError: Any
    prompt_composition: Callable[..., Any]
    # attempt.py's own names (forwarders of the moved functions included: a patch on any of them is honoured)
    _chain_binding: Callable[..., Any]
    _dependency_graph_db_path: Callable[..., Any]
    _escalation_authorized_paths: Callable[..., Any]
    _preserve_member_exact_precision: Callable[..., Any]
    _record_authority_expansions: Callable[..., Any]
    _resolve_known_target_member_hints: Callable[..., Any]
    _resolve_retry_member_hints: Callable[..., Any]
    _filtered_candidates: Callable[..., Any]
    _planned_source_context: Callable[..., Any]
    _goal_named_member_hints: Callable[..., Any]
    _target_package_with_window_reserve: Callable[..., Any]
    _code_intelligence_for: Callable[..., Any]
    _decide_edit_capabilities: Callable[..., Any]
    _ensure_generation_time_budget: Callable[..., Any]
    _enter_developer_model: Callable[..., Any]
    _grounded_output_expectations: Callable[..., Any]
    _lower_output_protocol_retry: Callable[..., Any]
    _maybe_run_developer_investigation: Callable[..., Any]
    _require_qualified_retry_identity: Callable[..., Any]
    _run_developer_generation_as_developer: Callable[..., Any]
    _developer_request_profile: Callable[..., Any]
    _patch_required_files: Callable[..., Any]
    _substitute_for_required_patch: Callable[..., Any]
    # the bodies' function-local imports, done where they were done
    load_model_role: Callable[[], Any]
    load_resolve_request_profile: Callable[[], Any]
    load_model_entry: Callable[[], Tuple[Any, Any]]
    load_retry_identity: Callable[[], Tuple[Any, Any]]
    load_resolve_model_capability_profile: Callable[[], Any]
    load_investigation: Callable[[], Tuple[Any, Any, Any]]
    load_investigation_search: Callable[[], Tuple[Any, Any, Any, Any]]


PLANNED_SOURCE_HEADER = "=== Planned File Current Source (this subtask's approved files) ==="
# The initial rendering keeps every planned file whole whenever it can; the
# request fit (DEVELOPER-PROMPT-FIT-001) is what decides the real room.
_PLANNED_SOURCE_UNBOUNDED_TOKENS = 1 << 40


async def _maybe_run_developer_investigation(
    deps: GenerationDeps, state: RunState, ctx: AttemptCtx, kwargs: Dict[str, Any], active_model: str,
) -> None:
    """DEV-INV-001 (2026-09-19): opt-in (default OFF via autonomy.
    developer_investigation_enabled) bounded, read-only pre-pass that lets
    the Developer request additional repository evidence BEFORE this
    attempt's real generation call - see kriya/workflow/investigation.py for
    the full turn-loop/protocol/governance machinery this only wires up.

    Mutates only kwargs["existing_code_context"] (appending rendered
    evidence text, the same way retrieval-sourced Graph RAG context is
    already appended in workflow.py) and, for a genuinely member-exact
    result only, state.known_target_context_items (via the SAME
    _preserve_member_exact_precision() merge D1's own retry-projection path
    already uses below - never a second, independently-invented merge rule)
    - never touches kwargs otherwise, never calls ctx.developer.run_generation
    itself, never writes to disk. Flag OFF (the default): returns
    immediately before any of this, byte-identical to pre-DEV-INV-001
    behavior.

    Available for every brownfield attempt (every call through
    _run_developer_generation, every attempt/retry) when enabled - the one
    exception is a workspace with no dependency-graph index at all yet
    (this module's own `_dependency_graph_db_path` existence check, the
    same operational "has this workspace ever been indexed" signal
    workflow.py's own auto_index_missing_dependency_graph gate already
    uses): with no index, find_symbol/find_callers/search_code can only
    ever report "nothing found," so running the loop at all would just
    burn turns for no benefit - retains existing (no-investigation) flow
    for that case rather than activating on tier/is_exact signals, per the
    task's own explicit instruction not to gate on those.

    Also computes known_target_member_hints (2026-09-19) - which member_id
    is CURRENTLY believed relevant, per declared target path - and passes
    it to run_investigation_loop so its own mutation-readiness check can
    require member-level relevance, not merely "some exact member in the
    right file" (see that function's own docstring)."""
    autonomy_cfg = ctx.kernel.config.autonomy
    if not autonomy_cfg.developer_investigation_enabled:
        return
    db_path = deps._dependency_graph_db_path(ctx)
    if not os.path.exists(db_path):
        return
    configured_max = max(0, autonomy_cfg.developer_investigation_max_turns)
    used_so_far = state.investigation_turns_used_by_attempt.get(state.attempt_number, 0)
    remaining = configured_max - used_so_far
    if remaining <= 0:
        return

    resolve_model_capability_profile = deps.load_resolve_model_capability_profile()
    InvestigationDependencies, render_investigation_evidence, run_investigation_loop = deps.load_investigation()

    capability_profile = resolve_model_capability_profile(ctx.kernel.config, active_model)

    async def _search_code(query: str) -> List[Dict[str, Any]]:
        vector_index_path = os.path.join(ctx.kernel.config.paths.memory, "vector_index.db")
        if not os.path.exists(vector_index_path):
            return []
        from types import SimpleNamespace

        configured_client, run_deadline, LocalVectorStore, semantic_query_embedding = deps.load_investigation_search()

        store = LocalVectorStore(vector_index_path)
        try:
            # EMBEDDING-CONTRACT-001: an unavailable semantic leg is recorded,
            # and only the lexical leg answers - never a zero-vector search.
            availability = SimpleNamespace(semantic_unavailable=None)
            query_emb, fingerprint = await semantic_query_embedding(
                configured_client(ctx.kernel.config), store, query, availability,
                run_deadline(ctx.kernel.config),
            )
            if availability.semantic_unavailable:
                state.record_event(RunEvent(
                    kind="retrieval.semantic_unavailable", attempt=state.attempt_number,
                    source="investigation.search_code", authority=EventAuthority.ADVISORY,
                    message=f"semantic search unavailable ({availability.semantic_unavailable}); lexical only",
                    details={"reason_code": availability.semantic_unavailable},
                ))
            return store.query_hybrid(
                query, query_emb, top_k=5, model_name=ctx.kernel.config.embedding.model,
                dimensions=len(query_emb) if query_emb else 0, fingerprint=fingerprint,
            )
        finally:
            store.close()

    investigation_deps = InvestigationDependencies(
        workspace_path=ctx.workspace_path, worktree_path=ctx.worktree_path,
        dependency_graph_db_path=db_path, search_code=_search_code,
        source_cache=ctx.source_cache,
    )
    # MUTATION_RELEVANCE_GATE for readiness (2026-09-19, VAL-001 G1 follow-
    # up): "an exact member exists in the right file" is NOT the same claim
    # as "an exact member exists for the region this attempt actually
    # cares about" - G1's own add_node/walk_calls shape proved that
    # distinction matters. run_investigation_loop's own mutation-readiness
    # check needs to know which member_id(s), if any, are CURRENTLY
    # believed relevant for each declared target - reuses the SAME two
    # already-proven resolvers this module's own retry-context preparation
    # already calls (_resolve_known_target_member_hints for SOURCE 1/
    # Graph-RAG grounding, _resolve_retry_member_hints for SOURCE 2/3
    # failure-grounded escalation), never a new resolver or data model.
    # Both are safe to call unconditionally: _resolve_retry_member_hints
    # itself returns {} with no state.last_failure yet (attempt 1).
    known_target_files_list = list(kwargs.get("known_target_files") or [])
    known_target_member_hints: Dict[str, List[str]] = {}
    for hints_source in (
        deps._resolve_known_target_member_hints(ctx, known_target_files_list),
        deps._resolve_retry_member_hints(ctx, state, known_target_files_list),
    ):
        for path, member_ids in hints_source.items():
            existing = known_target_member_hints.setdefault(path, [])
            for member_id in member_ids:
                if member_id not in existing:
                    existing.append(member_id)
    result = await run_investigation_loop(
        llm=ctx.developer.llm, capabilities=capability_profile.capabilities, deps=investigation_deps,
        task_description=str(kwargs.get("task_description") or ""),
        design_context=str(kwargs.get("design_context") or ""),
        existing_code_context=str(kwargs.get("existing_code_context") or ""),
        max_turns=remaining,
        known_target_files=kwargs.get("known_target_files"),
        known_target_member_hints=known_target_member_hints,
        model_override=kwargs.get("model_override"),
        base_url_override=kwargs.get("base_url_override"),
        api_key_override=kwargs.get("api_key_override"),
        extra_body_override=kwargs.get("extra_body_override"),
        attempt_number=state.attempt_number,
        # DEVELOPER-AUX-LOOP-PROMPT-FIT-001: the same fit the Developer
        # request gets - the binding it goes to and the optional sections
        # placed in existing_code_context.
        request_fit=deps.DeveloperRequestFit(
            ctx.kernel.config, deps._chain_binding(ctx, kwargs.get("model_override")),
            kwargs.get("optional_sections") or (),
        ),
    )
    state.investigation_turns_used_by_attempt[state.attempt_number] = used_so_far + result.turns_used
    for event in result.events:
        state.record_event(event)
    if not result.evidence:
        return
    # PRD-028: investigation evidence is always shown (read-only context),
    # but a member becomes edit authority only through a GRANTED expansion.
    member_items = {item.path: item for item in result.evidence if item.tier == "member_exact" and item.member_id}
    _granted, expansions = deps.grant_member_hints(
        {path: [item.member_id] for path, item in member_items.items()},
        workspace_path=ctx.workspace_path, worktree_path=ctx.worktree_path,
        authorized_paths=deps._escalation_authorized_paths(ctx, list(kwargs.get("known_target_files") or [])),
        evidence={"source": "developer_investigation", "attempt": state.attempt_number},
    )
    deps._record_authority_expansions(state, expansions)
    for path in _granted:
        state.known_target_context_items[path] = deps._preserve_member_exact_precision(
            state, path, member_items[path],
        )
    rendered = render_investigation_evidence(
        result.evidence,
        char_budget=deps.investigation_evidence_char_budget(
            deps.allocation_window(ctx.kernel.config, deps._chain_binding(ctx, kwargs.get("model_override"))),
        ),
    )
    if rendered:
        kwargs["existing_code_context"] = str(kwargs.get("existing_code_context") or "") + rendered
        # DEVELOPER-PROMPT-FIT-001: optional evidence, re-rendered smaller
        # when the request does not fit.
        kwargs["optional_sections"] = tuple(kwargs.get("optional_sections") or ()) + (deps.OptionalSection(
            "investigation", rendered,
            lambda budget: render_investigation_evidence(result.evidence, char_budget=budget * 4),
        ),)


def _developer_request_profile(deps: GenerationDeps, ctx: AttemptCtx, model_override: Optional[str]) -> Any:
    """PRD-017: the request profile of a Developer call with ``model_override``
    (None = the primary)."""
    resolve_request_profile = deps.load_resolve_request_profile()

    binding = deps._chain_binding(ctx, model_override) or ctx.kernel.config.llm
    return resolve_request_profile(ctx.kernel.config, binding)


def _enter_developer_model(deps: GenerationDeps, state: RunState, ctx: AttemptCtx, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """PRD-017: resolve the request profile of the model this Developer call
    goes to and return the call's kwargs. A fallback that cannot serve this
    call is replaced by the next configured one that can
    (_substitute_for_required_patch), or the attempt ends with the typed
    FALLBACK_MODEL_INCOMPATIBLE failure before any request (terminal). A
    change of profile from the previous call is recorded field by field
    (the model.transition run event)."""
    fallback_incompatibilities, profile_changes = deps.load_model_entry()

    model_override = kwargs.get("model_override")
    profile = deps._developer_request_profile(ctx, model_override)
    is_fallback = deps._chain_binding(ctx, model_override) is not None
    reasons = fallback_incompatibilities(
        ctx.kernel.config, profile, patch_required_files=deps._patch_required_files(state, ctx, kwargs),
    ) if is_fallback else []
    requested_model = profile.model
    requested_digest = profile.digest
    try:
        if reasons:
            kwargs, profile = deps._substitute_for_required_patch(state, ctx, kwargs, profile, reasons)
    except BaseException:
        attempt_evidence_scope.record_fallback_decision({
            "phase": "call", "fallback": is_fallback, "requested": requested_model,
            "requested_profile_digest": requested_digest, "requested_rejection": list(reasons),
            "selected": None, "profile_digest": None})
        raise
    attempt_evidence_scope.record_fallback_decision({
        "phase": "call", "fallback": is_fallback, "requested": requested_model,
        "requested_profile_digest": requested_digest, "requested_rejection": list(reasons),
        "selected": profile.model, "profile_digest": profile.digest})
    previous = state.last_developer_request_profile
    if previous is None or previous.digest != profile.digest:
        changes = profile_changes(previous, profile)
        state.record_event(RunEvent(
            kind="model.transition",
            attempt=state.attempt_number,
            source="attempt._run_developer_generation",
            authority=EventAuthority.ADVISORY,
            message=(
                f"Developer request profile for {profile.model}"
                + ("" if previous is None else f" (was {previous.model}): {', '.join(sorted(changes))} changed")
            ),
            details={
                "initial": previous is None,
                "fallback": is_fallback,
                "from": previous.to_dict() if previous is not None else None,
                "to": profile.to_dict(),
                "changes": changes,
            },
        ))
    state.last_developer_request_profile = profile
    state.last_developer_call_attempt = state.attempt_number
    # The model this attempt's generation actually went to (read by the
    # attempt's own bookkeeping and the next attempt's triage).
    state.last_model_override = kwargs.get("model_override")
    state.last_base_url_override = kwargs.get("base_url_override")
    state.last_api_key_override = kwargs.get("api_key_override")
    state.last_extra_body_override = kwargs.get("extra_body_override")
    return kwargs


def _require_qualified_retry_identity(deps: GenerationDeps, state: RunState, ctx: AttemptCtx,
                                      kwargs: Dict[str, Any]) -> None:
    """MODEL-EVIDENCE-HARDENING-001: a Developer retry carrying a
    retry_temperature that differs from the called model's own temperature
    executes a distinct inference identity. Under the production runtime
    profile that exact identity must be QUALIFIED; otherwise the attempt ends
    with the typed terminal RETRY_INFERENCE_IDENTITY_NOT_QUALIFIED before any
    request - it never runs under the normal identity's qualification.
    Outside production it is recorded (the profile), never refused."""
    QUALIFIED, RETRY_INFERENCE_IDENTITY_NOT_QUALIFIED = deps.load_retry_identity()

    profile = state.last_developer_request_profile
    config = ctx.kernel.config
    if (kwargs.get("retry_temperature") is None or profile is None or profile.retry_qualification is None
            or profile.retry_qualification == QUALIFIED or getattr(config, "runtime_profile", None) != "production"):
        return
    message = (f"{RETRY_INFERENCE_IDENTITY_NOT_QUALIFIED}: the production runtime profile requires the Developer "
               f"retry identity of {profile.model} (retry_temperature {config.llm.retry_temperature}, inference "
               f"settings {profile.retry_inference_settings_digest}) to be QUALIFIED; it is "
               f"{profile.retry_qualification}. Qualify it with `kriya model qualify --model {profile.model}`; "
               "nothing was sent to the model.")
    details = {"reason_code": RETRY_INFERENCE_IDENTITY_NOT_QUALIFIED, "model": profile.model,
               "retry_temperature": config.llm.retry_temperature,
               "retry_inference_settings_digest": profile.retry_inference_settings_digest,
               "retry_qualification": profile.retry_qualification}
    state.record_event(RunEvent(
        kind="model.retry_identity_not_qualified", attempt=state.attempt_number,
        source="attempt._run_developer_generation", authority=EventAuthority.ADVISORY,
        message=message, details=details,
    ))
    deps.logger.error(message)
    raise QualityGateFailure(Failure(
        type="retry_identity_not_qualified", message=message, raw_output=message, source="orchestrator",
        attempt=state.attempt_number, diagnostics=details,
    ))


def _chain_binding(ctx: AttemptCtx, model_override: Optional[str]) -> Any:
    """The llm_chain entry a Developer call with ``model_override`` goes to,
    or None for the primary model."""
    if not model_override:
        return None
    return next((fallback for fallback in ctx.chain if fallback.model == model_override), None)


def _planned_source_context(
    deps: GenerationDeps, ctx: AttemptCtx, exclude: Any, budget: int = _PLANNED_SOURCE_UNBOUNDED_TOKENS,
) -> str:
    """GRAPHIFY-OVERSIZE-REQUEST-001: the current source of a bounded enforce
    subtask's planned files (ctx.planned_source_files), through
    build_code_context's own tiers (full -> skeleton -> signatures, anything
    left over named as omitted) instead of verbatim mandatory text. A path
    the attempt already represents through a more authoritative producer
    (``exclude``: attempt-1 known targets, files written this run) is left
    out, never shown twice. Registered first among the optional Developer
    sections, so it stays whole whenever the request fits and degrades
    rather than making the request unsendable."""
    paths = deps._filtered_candidates(ctx.planned_source_files, exclude)
    if not paths:
        return ""
    body = deps.build_code_context(paths, [], ctx.worktree_path, budget, cache=ctx.source_cache)
    return f"\n\n{PLANNED_SOURCE_HEADER}\n{body}" if body else ""


def _developer_optional_sections(
    deps: GenerationDeps, ctx: AttemptCtx, graph_context: str, graph_exclude: Any, learned_reference: str,
    planned_source: str = "",
) -> Tuple[Any, ...]:
    """The optional sections an attempt branch placed in its Developer
    context (DEVELOPER-PROMPT-FIT-001): the planned files' current source
    and the graph context, each rebuilt smaller from the same candidates when
    the request does not fit, and the fenced learned reference, trimmed at
    whole entries."""
    sections: List[Any] = []
    if planned_source:
        sections.append(deps.OptionalSection(
            "planned_source", planned_source, lambda budget: deps._planned_source_context(ctx, graph_exclude, budget)))
    if graph_context:
        sections.append(deps.OptionalSection("graph_context", graph_context, lambda budget: deps.build_code_context(
            deps._filtered_candidates(ctx.matched_files, graph_exclude),
            deps._filtered_candidates(ctx.related_files, graph_exclude),
            ctx.worktree_path, budget, cache=ctx.source_cache,
        )))
    reference = deps.fenced_reference_section(learned_reference)
    if reference is not None:
        sections.append(reference)
    return tuple(sections)


def _target_package_with_goal_member_fallback(
    deps: GenerationDeps, ctx: AttemptCtx, paths: List[str], limit: int, prompt_window: int, member_hints: Dict[str, List[str]],
) -> Tuple[str, Any, Optional[Dict[str, Any]]]:
    """KNOWN-TARGET-GOAL-NAMED-MEMBER-HINT-001: the attempt-1 package, rebuilt
    once with the goal's own named members of a target as member hints when -
    and only when - that target's whole source was omitted as
    minimum_authority_unfit (its minimum unit, the whole file, exceeds the
    protected room: P4-T2's Element.java). A target that fits whole, or that
    already has a hint, is never demoted to a member-only view. Returns
    (rendered, package, fallback evidence or None)."""
    rendered, package = deps._target_package_with_window_reserve(ctx, paths, limit, prompt_window, member_hints)
    unfit = sorted({
        entry["path"] for entry in package.omitted
        if entry.get("reason") == deps.REASON_MINIMUM_AUTHORITY_UNFIT and entry["path"] not in member_hints
    })
    if not unfit:
        return rendered, package, None
    goal_hints = {path: names for path, names in deps._goal_named_member_hints(ctx, unfit).items() if names}
    if not goal_hints:
        return rendered, package, None
    merged = {**member_hints, **goal_hints}
    rendered, package = deps._target_package_with_window_reserve(ctx, paths, limit, prompt_window, merged)
    return rendered, package, {"unfit_targets": unfit, "goal_member_hints": goal_hints,
                               "tiers": [{"path": item.path, "member_id": item.member_id, "tier": item.tier}
                                         for item in package.relevant_files]}


def _target_package_with_window_reserve(
    deps: GenerationDeps, ctx: AttemptCtx, paths: List[str], limit: int, prompt_window: int, member_hints: Dict[str, List[str]],
) -> Tuple[str, Any]:
    """build_known_target_context at its full budget; when a target is not
    shown whole and exact there, exact windows may be added to the request
    (CONTEXT-EDIT-PROTOCOL-001), so it is rebuilt with their reserve held
    back and the windows never push the request past its capacity."""
    # Code Intelligence R1 slice 2: T0 (a grounded member's exact current
    # body) has its own room, computed without skills, learned reference or
    # optional context - none of them can evict it.
    exact_member_budget = deps._reserve_graph_context_budget(prompt_window, ctx.design, ctx.plan)
    service = deps._code_intelligence_for(ctx) if member_hints else None
    try:
        rendered, package = deps.build_known_target_context(
            paths, ctx.workspace_path, ctx.worktree_path, limit, member_hints=member_hints, cache=ctx.source_cache,
            exact_member_budget=exact_member_budget, code_intelligence=service,
        )
        shown_whole = {item.path for item in package.relevant_files if item.tier == "full" and item.is_exact}
        if set(paths) <= shown_whole:
            return rendered, package
        return deps.build_known_target_context(
            paths, ctx.workspace_path, ctx.worktree_path, max(0, limit - deps.exact_window_reserve(prompt_window)),
            member_hints=member_hints, cache=ctx.source_cache, exact_member_budget=exact_member_budget,
            code_intelligence=service,
        )
    finally:
        if service is not None:
            service.close()


async def _run_developer_generation(
    deps: GenerationDeps, state: RunState, ctx: AttemptCtx, **kwargs,
) -> List[Dict[str, str]]:
    """Every Developer generation goes through here; its model calls are
    attributed to the developer role (PRD-018)."""
    model_role = deps.load_model_role()

    with model_role("developer"):
        return await deps._run_developer_generation_as_developer(state, ctx, **kwargs)


async def _run_developer_generation_as_developer(
    deps: GenerationDeps, state: RunState, ctx: AttemptCtx, **kwargs,
) -> List[Dict[str, str]]:
    targets = kwargs.get("known_target_files")
    file_count = len(targets or ctx.expected_files_upfront or state.all_files_written or [None])
    kwargs = deps._enter_developer_model(state, ctx, kwargs)
    deps._require_qualified_retry_identity(state, ctx, kwargs)
    active_model = kwargs.get("model_override") or ctx.kernel.config.llm.model
    deps._ensure_generation_time_budget(
        state, ctx, file_count=file_count, active_model=active_model,
    )
    await deps._maybe_run_developer_investigation(state, ctx, kwargs, active_model)
    capabilities = deps._decide_edit_capabilities(state, ctx, kwargs)
    # DEVELOPER-PROMPT-FIT-001: every request is fitted into the capacity of
    # the binding it is sent to, its optional sections shrinking first.
    request_fit = kwargs["request_fit"] = deps.DeveloperRequestFit(
        ctx.kernel.config, deps._chain_binding(ctx, kwargs.get("model_override")),
        kwargs.pop("optional_sections", None) or (),
    )
    if "expected_output_by_file" not in kwargs:
        # A target whose capability excludes the full file is always asked
        # for an anchored patch, to which the Developer applies no
        # expectation (CONTEXT-EDIT-PROTOCOL-001).
        kwargs["expected_output_by_file"] = deps._grounded_output_expectations(
            ctx, kwargs.get("known_target_files"), deps._chain_binding(ctx, kwargs.get("model_override")),
        )
    started = deps.time.monotonic()
    succeeded = False
    try:
        try:
            result = await ctx.developer.run_generation(**kwargs)
        except deps.OutputBudgetUnsatisfiableError as refusal:
            retry_kwargs = deps._lower_output_protocol_retry(state, ctx, kwargs, refusal, active_model)
            if retry_kwargs is None:
                raise
            attempt_evidence_scope.record_authority_transition(
                "output_budget_protocol_fallback",
                {path: getattr(op, "value", op) for path, op in (retry_kwargs.get("operation_by_file") or {}).items()})
            result = await ctx.developer.run_generation(**retry_kwargs)
        succeeded = True
        return result
    finally:
        # P3-A: what this invocation really sent, for the anchor check.
        for path in capabilities:
            state.edit_capability_sent[path] = tuple(request_fit.fitted)
        state.drain_budget_expansions(getattr(ctx.developer, "llm", None))
        duration = deps.time.monotonic() - started
        # R1 Deliverable 5 (2026-09-08) - observational only, read AFTER the
        # await above already returned/raised; never influences kwargs,
        # result, or control flow. ctx.developer.llm.last_call_metrics
        # reflects the LAST underlying LLMClient.complete() call
        # run_generation() made - for the common single-shot batch-JSON
        # path this is the whole attempt's real usage; for the iterative
        # per-file fallback path (kriya/agents/agent.py, one completion per
        # filepath) it is only the FINAL file's usage, not a sum across all
        # of them - documented as a known undercount in
        # docs/assurance/KRIYA_PERFORMANCE_TELEMETRY.md rather than silently
        # presented as exact.
        call_metrics = getattr(ctx.developer, "llm", None)
        last_call_metrics = getattr(call_metrics, "last_call_metrics", None) if call_metrics else None
        state.generation_timings.append({
            "duration_seconds": duration,
            "file_count": file_count,
            "succeeded": succeeded,
            "model": active_model,
            "prompt_tokens": (last_call_metrics or {}).get("prompt_tokens"),
            "completion_tokens": (last_call_metrics or {}).get("completion_tokens"),
            "tokens_estimated": (last_call_metrics or {}).get("tokens_estimated"),
            # VAL-001 G1-R3 (2026-09-18): same observational-only posture as
            # every other field here - None whenever the provider/SDK never
            # reported one (LLMClient.complete()'s own last_call_metrics
            # already carries that same honest None, never a fabricated
            # "stop").
            "finish_reason": (last_call_metrics or {}).get("finish_reason"),
            # PRD-013/015: the exact runtime the call went to and its
            # normalized protocol outcome (secret-free).
            "runtime_fingerprint": (last_call_metrics or {}).get("runtime_fingerprint"),
            "protocol_status": (last_call_metrics or {}).get("protocol_status"),
        })
        # Code Intelligence R1 slice 2: what this request spent its prompt on
        # (T0 member/header, sibling signatures, T1-T3, skills) and the
        # provider's measured prefill time - observational only.
        # Inside this finally block it must never raise over the call's own
        # outcome: it reads only plain strings and mappings.
        completion = (last_call_metrics or {}).get("completion")
        code_context = kwargs.get("existing_code_context")
        skills_text = getattr(ctx, "skills_prompt", "")
        state.record_event(RunEvent(
            kind="developer.prompt_composition", attempt=state.attempt_number, source="developer",
            authority=EventAuthority.ADVISORY,
            message="Developer prompt composition (estimated per section; provider-reported total).",
            details=deps.prompt_composition(
                code_context if isinstance(code_context, str) else "",
                skills_text if isinstance(skills_text, str) else "",
                prompt_tokens_reported=(last_call_metrics or {}).get("prompt_tokens"),
                provider_metadata=(completion.get("provider_metadata") if isinstance(completion, dict) else None),
                prefix_reuse=(completion.get("prefix_reuse") if isinstance(completion, dict) else None),
            ),
        ))
        state.record_event(RunEvent(
            kind="generation.completed" if succeeded else "generation.failed",
            attempt=state.attempt_number,
            source="developer",
            authority=EventAuthority.ADVISORY,
            message=(
                f"Developer generation {'completed' if succeeded else 'failed'} "
                f"for {file_count} file(s) in {duration:.2f}s."
            ),
            details={
                "duration_seconds": duration,
                "file_count": file_count,
                "model": active_model,
                "model_use": (last_call_metrics or {}).get("completion"),
            },
        ))


