"""One retry-iteration's happy-path body for the Developer + Quality Gates
loop - Developer invocation, file writes, the completeness check, compile/
test gates, and the Runtime Verification gate. Extracted verbatim from
kriya/workflow/workflow.py's run_generation_workflow() (2026-08-11,
Opportunity 2 Slice 2): mutates the passed-in GenerationState in place and
either returns normally (Quality Gates passed) or raises the same exceptions
the inline code always did - QualityGateFailure or IncompleteGenerationError
- letting them propagate to the caller's own except block exactly as before.
Everything after Quality Gates pass (checkpoint save, human approval, apply-
to-workspace, lesson extraction, the full regression suite) deliberately
stays in workflow.py - out of scope for this slice.
"""
import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from kriya.agents.agent import DeveloperAgent
from kriya.core.kernel import Kernel
from kriya.workflow.edit_safety import (
    apply_anchored_edits, atomic_write_file, commit_revision_grounded_file,
    content_revision, find_structural_corruption,
)
from kriya.workflow.failure import Failure, FileLocation, QualityGateFailure
from kriya.workflow.failure_grounding import _build_quality_gate_failure
from kriya.workflow.file_resolution import IncompleteGenerationError, _resolve_run_command, downgrade_ungrounded_goal_explicit_commands, extract_planner_code_blocks, extract_target_test, find_missing_expected_files, normalize_written_filepath
from kriya.workflow.context_budget import (
    _reserve_graph_context_budget,
    _reserve_sibling_content_budget,
    build_code_context,
)
from kriya.workflow.retry_prompts import _build_full_set_retry_prompt, _build_missing_files_retry_prompt, _build_targeted_retry_prompt
from kriya.workflow.skill_extraction import _skill_verification_context
from kriya.workflow.state import GenerationState
from kriya.workflow.run_events import EventAuthority, RunEvent
from kriya.workflow.operations import all_results_are_no_change, operation_for_attempt
from kriya.workflow.static_checks import run_static_checks
from kriya.workflow.attribution import extract_self_diagnosed_files, find_edits_ignoring_own_diagnosis, find_edits_ignoring_reported_line, find_misdirected_edit_target, find_whole_response_no_op, resolve_fallback_model
from kriya.workflow.toolchain import _check_java_toolchain_mismatch, _pin_exec_plugin_executable_to_resolved_jdk, _resolve_java_home_override, _strip_jdk_incompatible_jvm_flags
from kriya.workflow.verification_contract import extract_contract_verdict, pass_verdict_is_grounded
from kriya.workflow.worktree import clean_untracked_files_since, snapshot_untracked_files

logger = logging.getLogger(__name__)


@dataclass
class AttemptContext:
    """The retry loop's read-only, loop-invariant closure captures - built
    once before the while loop starts (nothing in this range is reassigned
    across iterations), passed into run_attempt() (this module) and
    kriya.workflow.retry_strategy.handle_attempt_failure() alike - both
    operate on the same attempt/failure cycle, just its two different
    halves, so they share one context object rather than each inventing
    their own overlapping one."""

    goal: str
    plan: str
    design: str
    workspace_path: str
    worktree_path: str
    architect_files: List[str]
    resume_state: Optional[Dict[str, Any]]
    run_id: str
    skills_prompt: str
    learned_rag_context: str
    matched_files: Any
    related_files: Any
    ecosystem_invariant_block: str
    resource_lifecycle_block: str
    verification_contract_block: str
    required_files_prompt_block: str
    required_dependencies_prompt_block: str
    expected_files_upfront: List[str]
    architect_basename_to_path: Dict[str, str]
    chain: list
    targeted_max_retries: int
    stream_callback: Optional[Callable[[str, str], None]]
    approval_callback: Optional[Callable[[List[Dict[str, str]], str], Any]]
    active_skills: List[str]
    active_skill_rules_snapshot: Dict[str, Any]
    developer: DeveloperAgent
    run_verifier: Any
    skill_engine: Any
    kernel: Kernel
    # The next three fields are only read by handle_attempt_failure(), not
    # run_attempt() itself - kept on the same context object anyway (see the
    # class docstring) rather than a second, mostly-overlapping dataclass.
    max_retries: int
    web_lookup_query_callback: Optional[Callable[[List[str], str], Any]]
    # A bound method (WorkflowEngine._approve_web_lookup), not a free
    # function - already carries its own `self` reference, so it's just
    # another callable from this module's perspective.
    approve_web_lookup: Callable[..., Any]


def _extract_grounded_contract_verdict(
    output: str, worktree_path: str, files_written: List[str]
) -> Optional[Dict[str, Any]]:
    """Wraps extract_contract_verdict() with the independent grounding check
    from verification_contract.py::pass_verdict_is_grounded() - see that
    function's own docstring for the full reasoning (independent brutal
    review finding #4, 2026-08-15: a PASS marker is self-reported by the
    same model that wrote the implementation, with nothing else checking it
    really branches on anything). A single shared helper, not three copies
    of the same logic - used identically at all three of run_attempt()'s
    run_res-outcome branches (clean run / timed out / plain nonzero exit) so
    a PASS verdict's trust is checked consistently everywhere, not just
    wherever someone happened to add it first."""
    verdict = extract_contract_verdict(output)
    if verdict is None or not verdict["passed"]:
        return verdict
    files_content = []
    for filepath in files_written:
        full_path = os.path.join(worktree_path, filepath)
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                files_content.append(f.read())
        except Exception:
            continue
    if not pass_verdict_is_grounded(files_content):
        logger.info(
            "Runtime verification: a deterministic '[VERIFICATION] PASS' marker was found, but "
            "none of the written files contain a '[VERIFICATION] FAIL' string anywhere - the "
            "check doesn't look like it actually branches on anything. Not trusting it; falling "
            "back to LLM grading instead."
        )
        return None
    return verdict


def _diagnosis_mismatch_bypass_reason(
    state: GenerationState, ctx: "AttemptContext", filepath: str, candidate_content: str,
) -> Optional[str]:
    """Deterministic-validation-first override for the diagnosis-mismatch
    pre-flight check (find_edits_ignoring_own_diagnosis, kriya/workflow/
    attribution.py) - added 2026-08-17 after 5 independent false-positive
    prose shapes were found in that one check across a single night (cast-
    insertion, "instead of" ambiguity, self-referential filepath,
    verification-marker wrap, plus the original 2026-08-13 incident that
    motivated the check at all). Five bespoke signals in one function is a
    symptom of an unbounded problem, not a finite list of missing cases -
    verifying analysis PROSE against diff CONTENT via backtick-quote
    matching fights an inherently fuzzy correspondence that a new model
    response shape can always break differently. Rather than adding a 6th/
    7th/Nth signal for the next prose shape, this asks the real underlying
    question directly, two different ways depending on what's cheaply
    available:

    1. If this retry is responding to a static_rule_violation (a CHEAP,
       already-registered deterministic checker - kriya/workflow/
       static_checks.py), just re-run that SAME check against the proposed
       content. If the specific violation it originally flagged is gone,
       that's authoritative - no prose-matching needed at all. Confirmed
       live, 2026-08-17 (ignite_qpid_person, run b-10m): a correct fix
       wrapping a bare `[VERIFICATION] PASS` line in println(...) was
       rejected 3 times because the marker text was a substring of a
       larger search block, not signal (b)'s required whole-block match -
       re-running BareVerificationMarkerCheck against the candidate would
       have accepted it on the first try, with zero new signal needed.
    2. If this retry is responding to a `compile` failure, there is no
       equally-cheap re-check available - the only authoritative answer is
       the real compiler, which runs immediately after this check anyway.
       Rather than keep guessing from prose, this check is bypassed
       entirely for compile-triggered retries and the real compile gate
       decides. This is an evidence-backed tradeoff, not a guess: 3 of the
       5 confirmed false positives above (cast-insertion, "instead of",
       self-referential filepath) were ALL compile-triggered, each
       rejecting an edit that would have compiled successfully; by
       contrast, this check's own ORIGINAL motivating incident (a compile
       error recurring verbatim across 3 retries because an edit never
       implemented its own analysis, at ANY line -
       attribution-fix-validation-3, 2026-08-13) would cost at most one
       extra wasted compile cycle if this check were entirely absent - the
       SAME repeated-failure signal this retry loop already detects and
       escalates from independently (state.budgets.last_failure_signature),
       with no dependency on prose-matching at all. Given real compile
       subprocess cost against the confirmed cost of wrongly rejecting a
       correct fix (3-6 wasted retries EACH, tonight), the evidence favors
       letting the compiler decide.

    pom_semantic_validation joined the compile bypass 2026-08-17 (see the
    inline comment on that branch below) - a real compile gate always runs
    right after this check regardless, so it's the same tradeoff, not a
    separate case. Every OTHER failure type (misdirected_edit,
    run_verification, general_error, etc.) is left exactly as-is - no
    evidence yet that this check misfires for them, and this is deliberately
    not a blanket "disable the check" change. (unaddressed_error_location -
    find_edits_ignoring_reported_line, the "Layer 1" check just above this
    one's call site - got its OWN, separately-scoped deterministic-
    validation-first bypass the same night this docstring line was found to
    be stale; see that check's own inline comment in run_attempt() for why it
    didn't need this function's signature-based dispatch at all.)

    Returns a human-readable bypass reason (for logging/observability - the
    diagnosis text stays useful there, just never a blocking gate for these
    two cases) if the check should be skipped for this edit; None if the
    existing diagnosis-mismatch check should run and decide as before."""
    # Bounded-veto policy, checked before anything else and independent of
    # fail_type: this check can reject a given FILE'S edit at most once per
    # run. Found live, 2026-08-17 (ignite_qpid_person, run b-10o): the
    # pom_semantic_validation false positive above rejected the SAME correct
    # fix 4 CONSECUTIVE times before the model finally gave up and regenerated
    # from scratch on a fallback model - by which point the run had already
    # burned most of its wall-clock budget on one heuristic's repeated wrong
    # call. Every fail_type this check has EVER been extended to cover
    # (compile, static_rule_violation, pom_semantic_validation) was found
    # this same way: a real incident, after the fact. This is the safety net
    # for every fail_type NOT yet on that list, and for the next false-
    # positive shape this check will inevitably have that nobody has found
    # yet - a single wrongful rejection costs one retry; catching this
    # heuristic in a 4x-in-a-row loop costs the whole run.
    if state.budgets.diagnosis_mismatch_veto_counts.get(filepath, 0) >= 1:
        return (
            "this check already rejected an edit to this file once this run - "
            "per the bounded-veto policy, it never rejects the same file twice "
            "regardless of failure type; the real downstream gate decides instead"
        )
    sig = state.budgets.last_failure_signature
    if not sig:
        return None
    fail_type = sig[0]
    if fail_type in ("compile", "pom_semantic_validation", "targeted_test"):
        # targeted_test added 2026-08-17: run_tests(target_test=...) (kriya/
        # tools/validate.py) already scopes a targeted-test rerun to the ONE
        # test file extract_target_test() identified - not the whole
        # regression suite - and that real rerun happens immediately after
        # this per-file write loop regardless, in the SAME attempt (see the
        # targeted_test gate a few hundred lines below this check's own call
        # site). Same tradeoff as compile/pom_semantic_validation: bypassing
        # here costs nothing beyond what would happen anyway, and avoids
        # prose-matching a correct fix out from under a test that would have
        # passed.
        # pom_semantic_validation added 2026-08-17 after a live false positive
        # (ignite_qpid_person, run b-10o): a correct edit wrapped a bare
        # <dependency> fragment in a full, valid <project> POM structure -
        # exactly what its own analysis said - but the analysis quoted the
        # tag as `<project>` (no attributes) while the actual fix wrote
        # `<project xmlns="...">` (real Maven POMs always declare a
        # namespace), so a literal substring check for the bare quoted tag
        # never matched. Same tradeoff as compile, for the same reason: `mvn
        # validate` (the check that raised pom_semantic_validation) is a
        # cheap PRE-check specifically so a broken POM fails before the
        # expensive full `mvn compile` - but that real compile gate still
        # runs immediately after this per-file loop regardless, and would
        # catch a genuinely still-broken POM just as reliably, one step
        # later. No new prose-matching signal needed (the underlying gap is
        # XML-attribute-vs-bare-tag-name, not really about pom.xml
        # specifically - a 6th signal here would just be the next incident-
        # specific patch this function's whole redesign was meant to stop).
        return "this retry responds to a compile/POM-validation/targeted-test failure - deferring to the real gate instead of prose-matching"
    if fail_type == "static_rule_violation":
        from kriya.workflow.static_checks import run_static_checks
        still_violates = run_static_checks(
            ctx.worktree_path, state.all_files_written, overrides={filepath: candidate_content},
        )
        if not still_violates:
            return "the static check that originally flagged this file no longer flags the proposed content"
    return None


async def run_attempt(state: GenerationState, ctx: AttemptContext) -> None:
    """Runs one Developer + Quality Gates attempt. Mutates state in place
    (files_written, gate_outcomes, model_hops, run_verification_*, etc.).
    Raises QualityGateFailure or IncompleteGenerationError on any gate
    failure; returns normally when Quality Gates (including Runtime
    Verification) pass."""
    state.attempt_number += 1
    use_targeted = bool(state.last_implicated_files) and state.budgets.targeted_retry_count < ctx.targeted_max_retries
    use_missing_files = (
        not use_targeted and bool(state.last_missing_files) and state.budgets.targeted_retry_count < ctx.targeted_max_retries
    )
    # One-shot fallback-model targeted fix (see fallback_targeted_attempted's
    # own docstring above) - only eligible once the primary-model targeted
    # budget is exhausted (never competes with use_targeted/use_missing_files
    # for the same attempt) and only when there's still a real implicated-file
    # set and a fallback model to try it on.
    use_fallback_targeted = (
        not use_targeted and not use_missing_files
        and bool(state.last_implicated_files) and bool(ctx.chain) and not state.budgets.fallback_targeted_attempted
    )
    # Recorded now, not derived by the caller afterward - see the field's own
    # docstring in kriya/workflow/state.py for why that would be unsafe.
    state.last_attempt_mode = (
        "targeted" if use_targeted
        else "fallback_targeted" if use_fallback_targeted
        else "missing_files" if use_missing_files
        else "full_set"
    )
    attempt_operation = operation_for_attempt(
        state.last_attempt_mode, has_prior_failure=bool(state.error_context),
    )
    state.record_event(RunEvent(
        kind="attempt.started",
        attempt=state.attempt_number,
        source="workflow",
        authority=EventAuthority.ADVISORY,
        operation=attempt_operation.value,
        details={"mode": state.last_attempt_mode},
    ))
    # Needed unconditionally below (both the normal compile/test gate
    # path and the always-run full regression check use it) - imported
    # here rather than only inside the skippable gate block so a
    # resumed "developer_success" checkpoint iteration (which skips
    # that block entirely) still has it in scope.
    from kriya.tools.validate import PolymorphicValidator

    # A "developer_success" checkpoint means Developer generation + all
    # Quality Gates already passed once, before this process was
    # interrupted - only usable on the very first iteration of a resumed
    # run; any retry after that needs a real, fresh generation attempt.
    resuming_developer_stage = bool(
        ctx.resume_state and ctx.resume_state.get("stage") == "developer_success" and state.attempt_number == 1
    )

    if resuming_developer_stage:
        logger.info(f"Resuming checkpoint '{ctx.run_id}': using saved Developer output, skipping generation + Quality Gates.")
        files = [
            {"filepath": fp, "content": content}
            for fp, content in ctx.resume_state.get("final_files", {}).items()
        ]
        state.gate_outcomes = ctx.resume_state.get("gate_outcomes", state.gate_outcomes)
        state.model_hops = ctx.resume_state.get("model_hops", state.model_hops)
        model_override = None
        base_url_override = None
        api_key_override = None
    elif use_targeted:
        # Targeted retry: always the primary model, never escalated
        # (see the budget comment above) - so the context budget is
        # always the primary model's own window, not a fallback's.
        current_limit = _reserve_graph_context_budget(
            ctx.kernel.config.llm.context_window, ctx.skills_prompt, ctx.learned_rag_context
        )
        model_override = None
        base_url_override = None
        api_key_override = None

        current_graph_context = build_code_context(ctx.matched_files, ctx.related_files, ctx.workspace_path, current_limit)
        base_code_context = ctx.skills_prompt
        if current_graph_context:
            base_code_context += current_graph_context
        if ctx.learned_rag_context:
            base_code_context += ctx.learned_rag_context

        task_desc, active_code_context = _build_targeted_retry_prompt(
            ctx.goal, ctx.plan, state.error_context, state.last_implicated_files,
            state.all_files_written, ctx.worktree_path, base_code_context,
            ecosystem_invariant_block=ctx.ecosystem_invariant_block,
            resource_lifecycle_block=ctx.resource_lifecycle_block,
            verification_contract_block=ctx.verification_contract_block,
        )
        logger.info(f"Targeted retry {state.budgets.targeted_retry_count + 1}/{ctx.targeted_max_retries}: focusing on {', '.join(state.last_implicated_files)}.")

        state.model_hops.append(ctx.kernel.config.llm.model)

        dev_stream = (lambda token: ctx.stream_callback("Code Generation", token)) if ctx.stream_callback else None
        files = await ctx.developer.run_generation(
            task_description=task_desc,
            design_context=ctx.design,
            existing_code_context=active_code_context,
            stream_callback=dev_stream,
            model_override=model_override,
            base_url_override=base_url_override,
            api_key_override=api_key_override,
            known_target_files=state.last_implicated_files,
            prior_error_context=state.error_context or None,
            implicated_files=state.last_implicated_files,
            error_source_context=state.last_error_source_context or None,
            retry_temperature=ctx.kernel.config.llm.retry_temperature,
            extra_fix_instruction=DeveloperAgent.SELF_CONSISTENCY_NUDGE,
            files_with_current_content=state.all_files_written,
            sibling_content_budget=_reserve_sibling_content_budget(ctx.kernel.config.llm.context_window),
        )
    elif use_fallback_targeted:
        # One-shot targeted fix on the first fallback model (see
        # fallback_targeted_attempted's own docstring above) - same
        # narrow scope as a primary-model targeted retry (just the
        # implicated files, fix-analysis/anchored-edit preference),
        # just on a different model, before paying for a full-set
        # regeneration. Set the one-shot flag immediately, not after
        # a result is known, so a crash/exception mid-attempt can
        # never cause this to be retried in a loop.
        state.budgets.fallback_targeted_attempted = True
        fallback = ctx.chain[0]
        current_limit = _reserve_graph_context_budget(
            fallback.context_window, ctx.skills_prompt, ctx.learned_rag_context
        )
        model_override = fallback.model
        base_url_override = fallback.base_url
        api_key_override = fallback.api_key
        logger.info(
            f"Primary-model targeted retries exhausted - trying ONE targeted fix on "
            f"fallback model {model_override} before falling back to full-set regeneration."
        )

        current_graph_context = build_code_context(ctx.matched_files, ctx.related_files, ctx.workspace_path, current_limit)
        base_code_context = ctx.skills_prompt
        if current_graph_context:
            base_code_context += current_graph_context
        if ctx.learned_rag_context:
            base_code_context += ctx.learned_rag_context

        task_desc, active_code_context = _build_targeted_retry_prompt(
            ctx.goal, ctx.plan, state.error_context, state.last_implicated_files,
            state.all_files_written, ctx.worktree_path, base_code_context,
            ecosystem_invariant_block=ctx.ecosystem_invariant_block,
            resource_lifecycle_block=ctx.resource_lifecycle_block,
            verification_contract_block=ctx.verification_contract_block,
        )
        logger.info(f"Fallback-targeted retry: focusing on {', '.join(state.last_implicated_files)}.")

        state.model_hops.append(model_override)

        dev_stream = (lambda token: ctx.stream_callback("Code Generation", token)) if ctx.stream_callback else None
        files = await ctx.developer.run_generation(
            task_description=task_desc,
            design_context=ctx.design,
            existing_code_context=active_code_context,
            stream_callback=dev_stream,
            model_override=model_override,
            base_url_override=base_url_override,
            api_key_override=api_key_override,
            known_target_files=state.last_implicated_files,
            prior_error_context=state.error_context or None,
            implicated_files=state.last_implicated_files,
            error_source_context=state.last_error_source_context or None,
            retry_temperature=ctx.kernel.config.llm.retry_temperature,
            extra_fix_instruction=DeveloperAgent.SELF_CONSISTENCY_NUDGE,
            files_with_current_content=state.all_files_written,
            sibling_content_budget=_reserve_sibling_content_budget(fallback.context_window),
        )
    elif use_missing_files:
        # Missing-file recovery: same primary-model-only, non-escalating
        # budget as a targeted retry (see the comment on
        # last_missing_files above) - asks for exactly the file(s) the
        # completeness check found missing, instead of re-describing an
        # error or regenerating the whole file set.
        current_limit = _reserve_graph_context_budget(
            ctx.kernel.config.llm.context_window, ctx.skills_prompt, ctx.learned_rag_context
        )
        model_override = None
        base_url_override = None
        api_key_override = None

        current_graph_context = build_code_context(ctx.matched_files, ctx.related_files, ctx.workspace_path, current_limit)
        base_code_context = ctx.skills_prompt
        if current_graph_context:
            base_code_context += current_graph_context
        if ctx.learned_rag_context:
            base_code_context += ctx.learned_rag_context

        # last_missing_files (from find_missing_expected_files) is always
        # bare basenames (compared against written files by basename).
        # Resolve each to a real path via architect_basename_to_path (built
        # once from the Architect's already-resolved file list above) -
        # falls back to the bare basename itself for root-level files like
        # pom.xml, or if a basename genuinely isn't in the map. This lets
        # known_target_files be used safely: confirmed live (Qpid+Ignite
        # validation) that leaving this to the model's own file-list call -
        # even when explicitly told exactly which 1-4 files are missing -
        # reliably returns only ONE of them, silently dropping the rest and
        # burning the whole retry budget without ever recovering them.
        resolved_missing_files = [
            ctx.architect_basename_to_path.get(basename, basename) for basename in state.last_missing_files
        ]

        task_desc, active_code_context = _build_missing_files_retry_prompt(
            ctx.goal, ctx.plan, ctx.design, resolved_missing_files,
            state.all_files_written, ctx.worktree_path, base_code_context,
            ecosystem_invariant_block=ctx.ecosystem_invariant_block,
            resource_lifecycle_block=ctx.resource_lifecycle_block,
            verification_contract_block=ctx.verification_contract_block,
        )
        logger.info(f"Missing-file recovery retry {state.budgets.targeted_retry_count + 1}/{ctx.targeted_max_retries}: adding {', '.join(resolved_missing_files)}.")

        state.model_hops.append(ctx.kernel.config.llm.model)

        dev_stream = (lambda token: ctx.stream_callback("Code Generation", token)) if ctx.stream_callback else None
        files = await ctx.developer.run_generation(
            task_description=task_desc,
            design_context=ctx.design,
            existing_code_context=active_code_context,
            stream_callback=dev_stream,
            model_override=model_override,
            base_url_override=base_url_override,
            api_key_override=api_key_override,
            known_target_files=resolved_missing_files,
            sibling_content_budget=_reserve_sibling_content_budget(ctx.kernel.config.llm.context_window),
        )
    else:
        # Re-run context budget allocator dynamically for escalated model context window size
        current_limit = _reserve_graph_context_budget(
            ctx.kernel.config.llm.context_window, ctx.skills_prompt, ctx.learned_rag_context
        )
        model_override = None
        base_url_override = None
        api_key_override = None

        active_context_window = ctx.kernel.config.llm.context_window
        fallback = resolve_fallback_model(state.budgets.retry_count, ctx.chain)
        if fallback is not None:
            model_override = fallback.model
            base_url_override = fallback.base_url
            api_key_override = fallback.api_key
            active_context_window = fallback.context_window
            current_limit = _reserve_graph_context_budget(
                fallback.context_window, ctx.skills_prompt, ctx.learned_rag_context
            )
            logger.info(f"Escalating compilation attempt to fallback model: {model_override} (Limit: {current_limit} tokens)")

        current_graph_context = build_code_context(ctx.matched_files, ctx.related_files, ctx.workspace_path, current_limit)
        active_code_context = ctx.skills_prompt
        if current_graph_context:
            active_code_context += current_graph_context
        if ctx.learned_rag_context:
            active_code_context += ctx.learned_rag_context

        task_desc, active_code_context = _build_full_set_retry_prompt(
            ctx.goal, ctx.plan, state.error_context, ctx.required_files_prompt_block,
            state.all_files_written, ctx.worktree_path, active_code_context,
            ctx.required_dependencies_prompt_block,
            ecosystem_invariant_block=ctx.ecosystem_invariant_block,
            resource_lifecycle_block=ctx.resource_lifecycle_block,
            verification_contract_block=ctx.verification_contract_block,
        )

        # Track model hops
        state.model_hops.append(model_override or ctx.kernel.config.llm.model)

        # On the very first attempt only (never a full-set retry, which
        # already escalates through the fallback chain above and is
        # regenerating in response to a real compile/test/runtime error,
        # not a clean slate) - if the Architect's design yielded a
        # deterministic file manifest, use it directly instead of asking
        # the model to independently re-derive the same list. Confirmed
        # live: "INCOMPLETE GENERATION" (the design called for N files,
        # fewer were written) was one of the most common first-attempt
        # failures observed this session, each one costing a full extra
        # missing-file-recovery retry cycle - this prevents that failure
        # category outright on attempt 1 instead of only recovering from
        # it after the fact. Falls back to today's ask-the-model-for-a-
        # list behavior when the design didn't yield a usable list.
        # expected_files_upfront is already resolved to real paths by
        # this point (architect_files comes pre-resolved either from the
        # Architect's own structured JSON file list, or, in the fallback
        # case above, from _resolve_file_paths_from_design already) - no
        # separate resolution step needed here anymore.
        known_target_files = None
        if state.budgets.retry_count == 0 and ctx.expected_files_upfront:
            known_target_files = ctx.expected_files_upfront
        elif state.budgets.retry_count == 0 and state.last_implicated_files:
            # First full-set attempt reached via TARGETED-BUDGET EXHAUSTION,
            # not via this exact failure genuinely resisting narrow scoping -
            # found live, 2026-08-14 (spikes/eval_harness/runs/a-3, a-4,
            # ignite_qpid_protocol): targeted_retry_count/fallback_targeted_attempted
            # are single counters shared across the WHOLE run (kriya/workflow/
            # state.py's RetryBudgets), not per-failure. A run that spends its
            # entire targeted budget resolving one bug (a-4: 4 targeted attempts
            # + 1 fallback-targeted fixing an unclosed Ignite resource) has ZERO
            # scoped-retry runway left for the NEXT, completely different failure,
            # even when that new failure has a precise, high-confidence locator
            # (a-4: `Protocol.java:[17,5] variable dataLength might not have been
            # initialized`, a one-line javac error) that's never once been given a
            # targeted shot. Without this, that brand-new, trivially-scoped failure
            # falls straight into a full, unscoped "regenerate every file" walk on
            # a fallback model, chosen here purely by an unrelated earlier bug's
            # bad luck - confirmed live as the actual mechanism behind BOTH runs'
            # eventual 2400s timeout, not the fallback model's raw speed on its
            # own (glm-4.7-flash then pays one multi-minute completion PER FILE,
            # ~9 files, for a fix that only ever needed one).
            #
            # Fixed by reusing known_target_files here too - same mechanism
            # already used for expected_files_upfront just above, and already
            # trusted unconditionally (regardless of attribution confidence tier)
            # by every targeted/fallback_targeted branch above. Deliberately
            # gated to retry_count == 0 (the FIRST full-set attempt only, not
            # every one) so the existing "broaden to a clean full regeneration"
            # escape hatch is fully preserved for a failure that keeps recurring
            # despite already being given a scoped shot at THIS level too - only
            # the specific gap (a failure that's never once been targeted,
            # inheriting a spent budget from a different, already-resolved bug)
            # gets the cheaper, narrower first try.
            known_target_files = state.last_implicated_files
            logger.info(
                f"First full-set attempt after targeted-budget exhaustion, but the "
                f"current failure already has known implicated file(s) - scoping to "
                f"{', '.join(known_target_files)} instead of the full file set."
            )

        # PlannerAgent's own prompt never asks for full code, but models
        # routinely over-deliver it anyway in fenced blocks inside the plan
        # text - Architect explicitly discards it, and Developer previously
        # always regenerated every file from scratch regardless, paying a
        # full completion per file for work already done. On attempt 1 only
        # (never a retry - a plan that already led to a failure isn't a
        # trustworthy source for a fresh attempt), if the Planner's own text
        # already has usable code for EVERY expected file, use it directly
        # instead of asking Developer to redo it - still subject to the
        # exact same compile/test/Runtime-Verification gates as any other
        # attempt, so a wrong or incomplete Planner draft costs at most one
        # gate cycle before falling through to a real Developer generation
        # on the next attempt, the same downside a bad first Developer
        # attempt would already have. Deliberately all-or-nothing: a partial
        # match (some but not all expected files present) is NOT reused, to
        # avoid a third, harder-to-verify code path that mixes Planner and
        # Developer output for the same attempt.
        reused_files = None
        if state.budgets.retry_count == 0 and ctx.expected_files_upfront:
            planner_blocks = extract_planner_code_blocks(ctx.plan, ctx.expected_files_upfront)
            if set(planner_blocks.keys()) == set(ctx.expected_files_upfront):
                reused_files = [{"filepath": fp, "content": content} for fp, content in planner_blocks.items()]
                logger.info(
                    f"Planner's own plan already contains complete code for all "
                    f"{len(reused_files)} expected file(s) - reusing it directly instead of "
                    "a fresh Developer generation call, subject to the same Quality Gates "
                    "as any other attempt."
                )

        # Logged symmetrically on BOTH branches (previously only the reused
        # branch logged anything) so a run's log alone - via the same grep-
        # based analysis this session has used all day - can already answer
        # "did attempt 1 use Planner-reused content or fresh Developer
        # generation" without needing new tooling. See
        # state.planner_reuse_used_attempt1's own docstring for why this is
        # worth tracking at all: an external review raised, and two of the
        # same day's live incidents supported, the hypothesis that reused
        # Planner content correlates with more first-attempt failures than
        # fresh Developer generation - this makes that measurable from
        # ordinary run logs instead of argued from a handful of anecdotes.
        if state.budgets.retry_count == 0:
            state.planner_reuse_used_attempt1 = reused_files is not None
            if reused_files is None:
                logger.info(
                    "Attempt 1: Planner's plan did not contain usable code for every expected "
                    "file (or none was expected upfront) - using a fresh Developer generation "
                    "call for all files."
                )

        if reused_files is not None:
            files = reused_files
        else:
            # Generate code files
            dev_stream = (lambda token: ctx.stream_callback("Code Generation", token)) if ctx.stream_callback else None
            files = await ctx.developer.run_generation(
                task_description=task_desc,
                design_context=ctx.design,
                existing_code_context=active_code_context,
                stream_callback=dev_stream,
                model_override=model_override,
                base_url_override=base_url_override,
                api_key_override=api_key_override,
                known_target_files=known_target_files,
                prior_error_context=state.error_context or None,
                implicated_files=state.last_implicated_files,
                error_source_context=state.last_error_source_context or None,
                retry_temperature=ctx.kernel.config.llm.retry_temperature,
                extra_fix_instruction=DeveloperAgent.SELF_CONSISTENCY_NUDGE,
                files_with_current_content=state.all_files_written,
                sibling_content_budget=_reserve_sibling_content_budget(active_context_window),
            )

    # Recorded now, not derived by the caller afterward - see the fields'
    # own docstring in kriya/workflow/state.py. Every branch above sets all
    # three of these (to None for the primary model, or a fallback's values).
    state.last_model_override = model_override
    state.last_base_url_override = base_url_override
    state.last_api_key_override = api_key_override

    # Normalize filepaths before anything downstream uses them - the
    # Developer Agent occasionally returns an absolute path instead of a
    # relative one, which os.path.join(base, filepath) would silently
    # resolve to just `filepath` (discarding `base`) in every loop below.
    normalized_files = []
    for file_obj in files:
        raw_filepath = file_obj.get("filepath", "")
        normalized = normalize_written_filepath(raw_filepath, ctx.workspace_path)
        if normalized is None:
            logger.warning(f"Developer Agent returned an unusable filepath '{raw_filepath}' (absolute path outside the workspace, or empty) - skipping this file.")
            continue
        if normalized != raw_filepath:
            logger.info(f"Normalized Developer Agent filepath '{raw_filepath}' -> '{normalized}'.")
        file_obj["filepath"] = normalized
        normalized_files.append(file_obj)
    files = normalized_files

    # Captured here (before any write can raise) rather than after the write
    # loop below, so a self-diagnosis is never lost to an anchored-edit
    # exception on an unrelated file later in the same batch. Paired with
    # the failure signature THIS attempt was responding to - state.budgets.
    # last_failure_signature is still the PREVIOUS failure's signature at
    # this point (retry_strategy.py only overwrites it after the NEXT
    # failure is classified) - see kriya/workflow/attribution.py's
    # extract_self_diagnosed_files() and retry_strategy.py's signature-gated
    # consumption of this field.
    self_diagnosed = extract_self_diagnosed_files(files, sorted(state.all_files_written))
    state.last_self_diagnosis = (
        (state.budgets.last_failure_signature, self_diagnosed)
        if self_diagnosed else None
    )

    # "NO CHANGE NEEDED" is useful negative attribution evidence, not a
    # successful repair. In a targeted attempt, rerunning compile/tests/runtime
    # after every returned target was explicitly left untouched wastes an
    # expensive gate and routes the same failure back to the same file. Turn it
    # into a retry signal before any write or gate. If FIX ANALYSIS names a
    # different known file, redirect there; otherwise reject the old scope and
    # let attribute_failure() widen to the full set.
    all_targets_rejected = all_results_are_no_change(files)
    if state.last_attempt_mode in ("targeted", "fallback_targeted") and all_targets_rejected:
        state.record_event(RunEvent(
            kind="operation.no_change",
            attempt=state.attempt_number,
            source="developer",
            authority=EventAuthority.ADVISORY,
            operation="no_change_assessment",
            message="Every targeted result rejected the selected file scope.",
        ))
        returned_targets = [f.get("filepath", "") for f in files if f.get("filepath")]
        likely_files = list(self_diagnosed)
        evidence = "\n\n".join(
            f"{f.get('filepath', '(unknown file)')}: "
            f"{f.get('analysis') or '(no FIX ANALYSIS supplied)'}" for f in files
        )
        failure = Failure(
            type="attribution_rejected",
            message=(
                "TARGET ATTRIBUTION REJECTED: the Developer reported NO CHANGE NEEDED "
                f"for every targeted file ({', '.join(returned_targets)}). "
                + (
                    f"Its own analysis instead names: {', '.join(likely_files)}."
                    if likely_files else
                    "No grounded alternate file was named; widen the next attempt to the full file set."
                )
            ),
            raw_output=evidence,
            file_locations=[FileLocation(filepath=f) for f in likely_files],
            likely_files=likely_files,
            attempt=state.attempt_number,
        )
        state.gate_outcomes.append(failure.to_gate_outcome())
        raise QualityGateFailure(failure)

    # Read original file contents before overwriting (crucial for fallback mode diffs)
    for file_obj in files:
        filepath = file_obj.get("filepath", "")
        if not filepath:
            continue
        if filepath not in state.all_original_contents:
            actual_file = os.path.join(ctx.workspace_path, filepath)
            if os.path.exists(actual_file):
                with open(actual_file, "r", encoding="utf-8", errors="replace") as fh:
                    state.all_original_contents[filepath] = fh.read()
            else:
                state.all_original_contents[filepath] = ""

    # Write files to worktree sandbox
    state.files_written = []
    for file_obj in files:
        filepath = file_obj.get("filepath", "")
        content = file_obj.get("content", "")
        edits = file_obj.get("edits", [])
        analysis = file_obj.get("analysis")

        if not filepath:
            continue

        # Single choke point every content path (batch JSON, iterative
        # per-file, a full-set retry) converges through before a byte
        # reaches disk - closes a real gap the per-path fixes upstream
        # (DeveloperAgent.sanitize_generated_content) don't: a batch JSON
        # response's content/edits fields are consumed directly from
        # parsed JSON and never passed through any sanitization at all
        # before this point. Idempotent/harmless to re-apply to content
        # that already went through it upstream.
        if edits:
            edits = [
                {
                    **e,
                    "search": DeveloperAgent.sanitize_generated_content(e.get("search", "")),
                    "replace": DeveloperAgent.sanitize_generated_content(e.get("replace", "")),
                }
                for e in edits
            ]
        elif content is not None:
            content = DeveloperAgent.sanitize_generated_content(content)

        full_path = os.path.join(ctx.worktree_path, filepath)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        if edits:
            current_file_path = os.path.join(ctx.worktree_path, filepath)
            if not os.path.exists(current_file_path):
                current_file_path = os.path.join(ctx.workspace_path, filepath)

            orig_text = ""
            if os.path.exists(current_file_path):
                with open(current_file_path, "r", encoding="utf-8", errors="replace") as fh:
                    orig_text = fh.read()

            try:
                new_content = apply_anchored_edits(orig_text, edits, active_code_context)
            except ValueError as anchor_ex:
                # apply_anchored_edits() itself never receives a filepath, so its
                # raised ValueError never named one either - this failure class
                # always fell through to a blind full-set retry, unlike a compile
                # error (which self-names its file). filepath IS known right here,
                # in the caller's loop scope - capture it now instead of losing it.
                # orig_text (the real pre-edit content the SEARCH block was
                # supposed to match against) is already in memory - exactly what's
                # needed to debug an anchor mismatch, no disk re-read needed.
                # edits (the actual search/replace text that was attempted, already
                # sanitized) is captured alongside it as attempted_edits - together
                # both halves of "why didn't this match" are now persisted, not just
                # the generic "matched 0 times" message.
                #
                # Before accepting that generic explanation, check whether the
                # failed search block actually belongs to a DIFFERENT known file -
                # see find_misdirected_edit_target()'s own docstring for the real
                # incident (a-3, ignite_qpid_protocol, 2026-08-14) this closes: a
                # retry scoped to one file by a locator that could only ever name
                # the file where an explicit runtime check THREW, not the
                # different file whose method silently returned the wrong value,
                # produced a textbook-correct diagnosis whose edit could never
                # match the file it was constrained to. Reads every OTHER
                # already-written file straight from the worktree (nothing keeps
                # their content in memory this late in the per-file write loop).
                other_files: Dict[str, str] = {}
                for other_filepath in state.all_files_written:
                    if other_filepath == filepath:
                        continue
                    other_full_path = os.path.join(ctx.worktree_path, other_filepath)
                    try:
                        with open(other_full_path, "r", encoding="utf-8", errors="replace") as fh:
                            other_files[other_filepath] = fh.read()
                    except OSError:
                        continue

                misdirected_target = find_misdirected_edit_target(edits, orig_text, other_files)
                if misdirected_target:
                    # raw_output (not just message) carries both filenames - it's
                    # what to_gate_outcome() persists as "output" (raw_output or
                    # message) and what a future post-mortem reads from traces.db,
                    # same forensics goal as failed_content/attempted_edits below.
                    misdirected_explanation = (
                        f"the search block for {filepath} matched 0 times against "
                        f"{filepath}'s content, but was found instead inside {misdirected_target}"
                    )
                    failure = Failure(
                        type="misdirected_edit",
                        message=(
                            f"MISDIRECTED EDIT: {misdirected_explanation}. The fix you "
                            f"diagnosed likely belongs in {misdirected_target}, not {filepath} - "
                            f"target {misdirected_target} in your next edit (and {filepath} too, "
                            f"only if it genuinely also needs its own companion change)."
                        ),
                        raw_output=misdirected_explanation,
                        file_locations=[
                            FileLocation(filepath=filepath),
                            FileLocation(filepath=misdirected_target),
                        ],
                        likely_files=[filepath, misdirected_target],
                        failed_content={filepath: orig_text, misdirected_target: other_files[misdirected_target]},
                        attempted_edits=edits,
                        attempt=state.attempt_number,
                    )
                    state.gate_outcomes.append(failure.to_gate_outcome())
                    raise QualityGateFailure(failure) from anchor_ex

                failure = Failure(
                    type="anchored_edit",
                    message=f"ANCHORED EDIT FAILURE in {filepath}: {anchor_ex}",
                    raw_output=str(anchor_ex),
                    file_locations=[FileLocation(filepath=filepath)],
                    likely_files=[filepath],
                    failed_content={filepath: orig_text},
                    attempted_edits=edits,
                    attempt=state.attempt_number,
                )
                state.gate_outcomes.append(failure.to_gate_outcome())
                raise QualityGateFailure(failure) from anchor_ex

            # Layer 0 pre-flight check (see find_whole_response_no_op's own
            # docstring): purely structural, no analysis text or fail_type
            # dispatch needed - unlike Layers 1/2 below, a response whose
            # every edit is a byte-identical no-op is objectively useless
            # regardless of what it claims, so this is never bypassed and
            # never counts against the Layer 2 bounded veto (a different
            # check, a different question). apply_anchored_edits() above
            # already confirmed every search block matched real content -
            # this only fires when the edit(s) that matched changed nothing.
            if find_whole_response_no_op(edits):
                retry_files = list(self_diagnosed) or [filepath]
                failure = Failure(
                    type="no_op_edit",
                    message=(
                        f"NO-OP EDIT in {filepath}: every SEARCH/REPLACE pair in your response "
                        f"is byte-identical - this response changes nothing. If this file "
                        f"genuinely needs no change, write \"NO CHANGE NEEDED:\" instead of a "
                        f"SEARCH/REPLACE block; otherwise your REPLACE text must actually differ "
                        f"from your SEARCH text."
                    ),
                    raw_output="every edit in the response was a no-op (search == replace)",
                    file_locations=[FileLocation(filepath=f) for f in retry_files],
                    likely_files=retry_files,
                    failed_content={filepath: orig_text},
                    attempted_edits=edits,
                    attempt=state.attempt_number,
                )
                state.gate_outcomes.append(failure.to_gate_outcome())
                raise QualityGateFailure(failure)

            # Layer 1 pre-flight check (see find_edits_ignoring_reported_line's
            # own docstring): only meaningful when this attempt is itself
            # responding to a real prior error with a locatable line for this
            # file - error_context is "" on a clean first attempt, where
            # extract_error_source_locations() would find nothing anyway, but
            # the explicit guard avoids the line-matching work entirely there.
            #
            # Deterministic-validation-first (same principle as
            # _diagnosis_mismatch_bypass_reason, 2026-08-17): this check ONLY
            # ever fires when extract_error_source_locations() found a real
            # javac-style file:[line,col] locator for THIS file - i.e. it is
            # compile-scoped by construction, every single time, not just
            # sometimes (unlike diagnosis_mismatch, which fires for every
            # failure type). A real compile gate runs immediately after this
            # per-file write loop regardless, so it is always the cheaper,
            # authoritative answer to "does this edit actually still leave
            # the reported error in place" - no heuristic needed at all.
            # Confirmed both ways live via the corpus survey's replay of the
            # 2 DEBUG-capturable unaddressed_error_location occurrences
            # (b-10f, b-10n): b-10f was a FALSE positive - a companion edit
            # earlier in the same response fixed the bad import, which
            # transitively resolves the reported line's "cannot find symbol"
            # error too, but this check has no visibility into OTHER edits in
            # the same response and flagged the untouched (but now-harmless)
            # usage-site line anyway. b-10n was a genuine TRUE positive - the
            # model's own analysis said it would add a missing import and
            # never did - and bypassing here costs nothing there: the real
            # compile gate catches the exact same missing-import error one
            # step later, through the normal compile-failure retry path this
            # loop already handles well. Given every occurrence of this
            # check's own reported-line recurred identically across many
            # historical runs (10x/9x/6x for single line numbers - the same
            # small number of Ignite/Qpid goals hitting it over and over),
            # the false-positive cost of hard-blocking here was likely the
            # dominant driver of this failure category, not genuine misses.
            if state.error_context:
                ignored_lines = find_edits_ignoring_reported_line(
                    orig_text, edits, filepath, state.error_context
                )
                if ignored_lines:
                    lines_desc = ", ".join(str(n) for n in sorted(ignored_lines))
                    logger.info(
                        f"UNADDRESSED ERROR LOCATION pre-flight check bypassed for {filepath} "
                        f"(line(s) {lines_desc} of the reported error left unchanged): deferring "
                        f"to the real compiler instead of this heuristic, which cannot see whether "
                        f"a companion edit elsewhere in the same response already resolved it."
                    )

            # Structural corruption pre-flight check (see
            # find_structural_corruption's own docstring) - a cheap,
            # deterministic tripwire for the "obviously broken" shape
            # BOTH real corruption incidents this session actually had
            # (unbalanced braces from a folded-in duplicate class),
            # before the expensive compile gate spends itself
            # discovering the same thing.
            structural_problem = find_structural_corruption(filepath, new_content)
            if structural_problem:
                failure = Failure(
                    type="structural_corruption",
                    message=(
                        f"STRUCTURAL CORRUPTION in {filepath}: {structural_problem} "
                        f"This usually means an edit's replace text accidentally folded in "
                        f"extra, unrelated content (e.g. a redundant full-file dump appended "
                        f"after the intended change). Re-check your SEARCH/REPLACE blocks - "
                        f"the replace text for each pair should contain ONLY the corrected "
                        f"version of that pair's search text, nothing else."
                    ),
                    raw_output=structural_problem,
                    file_locations=[FileLocation(filepath=filepath)],
                    likely_files=[filepath],
                    failed_content={filepath: orig_text},
                    attempted_edits=edits,
                    attempt=state.attempt_number,
                )
                state.gate_outcomes.append(failure.to_gate_outcome())
                raise QualityGateFailure(failure)

            # Layer 2 pre-flight check (see find_edits_ignoring_own_diagnosis's own
            # docstring): the Layer 1 check above only catches an edit that left the
            # COMPILER's own reported line unchanged - it deliberately allows a fix
            # at a different, legitimate line (e.g. a declaration above the reported
            # use site). This catches the gap that leaves open: does the edit's own
            # content actually implement what its own FIX ANALYSIS just said, at ANY
            # line - found live, 2026-08-13, a real compile error recurred verbatim
            # across 3 targeted retries despite a textbook-correct analysis every time.
            diagnosis_mismatch = find_edits_ignoring_own_diagnosis(analysis, edits, None, orig_text)
            if diagnosis_mismatch:
                bypass_reason = _diagnosis_mismatch_bypass_reason(state, ctx, filepath, new_content)
                if bypass_reason:
                    logger.info(
                        f"DIAGNOSIS MISMATCH pre-flight check bypassed for {filepath}: {bypass_reason}."
                    )
                else:
                    # Counted here, at the actual rejection - see the bounded-
                    # veto policy's own comment in _diagnosis_mismatch_bypass_
                    # reason for why this exists.
                    state.budgets.diagnosis_mismatch_veto_counts[filepath] = (
                        state.budgets.diagnosis_mismatch_veto_counts.get(filepath, 0) + 1
                    )
                    failure = Failure(
                        type="diagnosis_mismatch",
                        message=(
                            f"DIAGNOSIS MISMATCH in {filepath}: {diagnosis_mismatch}. "
                            f"Make the exact change your own analysis described - not a "
                            f"different or partial change."
                        ),
                        raw_output=diagnosis_mismatch,
                        file_locations=[FileLocation(filepath=filepath)],
                        likely_files=[filepath],
                        failed_content={filepath: orig_text},
                        attempted_edits=edits,
                        attempt=state.attempt_number,
                    )
                    state.gate_outcomes.append(failure.to_gate_outcome())
                    raise QualityGateFailure(failure)
            else:
                # This file's edit looked consistent with its own analysis -
                # a clean slate, so an unrelated mismatch found later in the
                # run for this same file still gets its own bounded veto.
                state.budgets.diagnosis_mismatch_veto_counts.pop(filepath, None)

            commit_revision_grounded_file(
                full_path, new_content, expected_revision=content_revision(orig_text),
            )
        else:
            if content is None:
                continue
            structural_problem = find_structural_corruption(filepath, content)
            if structural_problem:
                failure = Failure(
                    type="structural_corruption",
                    message=f"STRUCTURAL CORRUPTION in {filepath}: {structural_problem}",
                    raw_output=structural_problem,
                    file_locations=[FileLocation(filepath=filepath)],
                    likely_files=[filepath],
                    failed_content={filepath: content},
                    attempt=state.attempt_number,
                )
                state.gate_outcomes.append(failure.to_gate_outcome())
                raise QualityGateFailure(failure)

            # Layer 2 pre-flight check - same rationale as the anchored-edit branch
            # above, applied to a full-file regeneration instead. Only reads the
            # file's current on-disk content when there's actually an analysis to
            # check against, to avoid the extra I/O on the common (no prior error)
            # case.
            if analysis:
                current_file_path = os.path.join(ctx.worktree_path, filepath)
                if not os.path.exists(current_file_path):
                    current_file_path = os.path.join(ctx.workspace_path, filepath)
                prior_content = ""
                if os.path.exists(current_file_path):
                    with open(current_file_path, "r", encoding="utf-8", errors="replace") as fh:
                        prior_content = fh.read()
                diagnosis_mismatch = find_edits_ignoring_own_diagnosis(analysis, None, content, prior_content)
                if diagnosis_mismatch:
                    bypass_reason = _diagnosis_mismatch_bypass_reason(state, ctx, filepath, content)
                    if bypass_reason:
                        logger.info(
                            f"DIAGNOSIS MISMATCH pre-flight check bypassed for {filepath}: {bypass_reason}."
                        )
                    else:
                        state.budgets.diagnosis_mismatch_veto_counts[filepath] = (
                            state.budgets.diagnosis_mismatch_veto_counts.get(filepath, 0) + 1
                        )
                        failure = Failure(
                            type="diagnosis_mismatch",
                            message=(
                                f"DIAGNOSIS MISMATCH in {filepath}: {diagnosis_mismatch}. "
                                f"Make the exact change your own analysis described - not a "
                                f"different or partial change."
                            ),
                            raw_output=diagnosis_mismatch,
                            file_locations=[FileLocation(filepath=filepath)],
                            likely_files=[filepath],
                            failed_content={filepath: prior_content},
                            attempt=state.attempt_number,
                        )
                        state.gate_outcomes.append(failure.to_gate_outcome())
                        raise QualityGateFailure(failure)
                else:
                    state.budgets.diagnosis_mismatch_veto_counts.pop(filepath, None)

            atomic_write_file(full_path, content)

        state.files_written.append(filepath)
        state.all_files_written.add(filepath)
        logger.info(f"Wrote generated/edited file to sandbox: {filepath}")

        if os.path.basename(filepath) == "pom.xml":
            # Cheap, semantic pre-check for pom.xml specifically - see
            # PolymorphicValidator.run_pom_validate()'s own docstring for the
            # real incident this closes (a well-formed-but-wrong-root-element
            # POM sailing straight past find_structural_corruption's XML
            # well-formedness check above, only caught by paying for the full
            # compile gate's own dependency resolution + javac invocation,
            # after every OTHER file in the batch had already been written
            # for nothing - nothing else in the project can possibly compile
            # without a usable POM). Checked here, immediately after the
            # write, not deferred to after the whole batch - pom.xml has no
            # cross-file dependency on sibling files (unlike a proactive
            # unresolved-symbol check on a .java file would), so there's no
            # "sibling not written yet" false-positive risk in checking it
            # this early, and this is exactly where the payoff is: the loop
            # stops right here, before any of the other files this attempt
            # would otherwise write are generated.
            #
            # Deliberately a fresh, minimal validator instance, not the one
            # built later for the real compile gate - "mvn validate" never
            # invokes javac or runs the application, so it doesn't need the
            # goal-specific JAVA_HOME override that compilation/execution
            # does (that override is resolved further below, after this
            # point in the loop, specifically to close a real JDK-version
            # mismatch gap for actual compilation - not applicable here).
            from kriya.tools.validate import PolymorphicValidator
            pom_validator = PolymorphicValidator(ctx.worktree_path, autonomy_cfg=ctx.kernel.config.autonomy)
            pom_validate_res = pom_validator.run_pom_validate()
            if not pom_validate_res["success"]:
                with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                    failed_pom_content = fh.read()
                failure = Failure(
                    type="pom_semantic_validation",
                    message=f"POM VALIDATION FAILED for {filepath}: {pom_validate_res['output']}",
                    raw_output=pom_validate_res["output"],
                    file_locations=[FileLocation(filepath=filepath)],
                    likely_files=[filepath],
                    failed_content={filepath: failed_pom_content},
                    attempt=state.attempt_number,
                )
                state.gate_outcomes.append(failure.to_gate_outcome())
                raise QualityGateFailure(failure)

    if not resuming_developer_stage:
        # Completeness Check: catch the Developer Agent silently under-delivering
        # (e.g. only writing pom.xml when the Architect's design called for 7 files).
        # A trivially-passing compile on a near-empty sandbox would otherwise report
        # PASSED and get applied to the workspace despite the goal not being met.
        # Sourced from architect_files (the structured file list, or its heuristic
        # fallback - see the Architect call above) rather than re-deriving via a
        # second independent regex pass over the design's prose.
        expected_files = {os.path.basename(f) for f in ctx.architect_files}
        missing_files = find_missing_expected_files(expected_files, state.all_files_written, goal=ctx.goal)
        if missing_files:
            raise IncompleteGenerationError(
                missing_files,
                "INCOMPLETE GENERATION: The design called for the following files, but "
                f"they were never written: {', '.join(missing_files)}. "
                f"You must generate ALL files listed in the Architect Design Guidelines, "
                f"not just a subset."
            )

        # Static pre-check: deterministic, no-LLM scan for known anti-patterns already
        # documented in active skill rules (e.g. mixing Ignite's two startup mechanisms,
        # an unclosed Ignition.start()) - catches a mistake the model already had the
        # rule for, before the expensive compile+run cycle rather than after it.
        static_violation = run_static_checks(ctx.worktree_path, state.all_files_written)
        if static_violation:
            # _build_quality_gate_failure() (not a bare Failure(...)), matching the
            # SAME construction every other Quality Gate type already uses (compile/
            # test/run_verification/regression_test) - its extract_implicated_files()
            # call scopes likely_files to whichever known file(s) the violation TEXT
            # actually names, instead of defaulting to state.all_files_written (every
            # file the run has ever written). Found live, 2026-08-13
            # (ignite_qpid_protocol): every StaticCheck's own violation message
            # already names its implicated file(s) by exact known relative path (see
            # static_checks.py) - broadcasting to the whole file set discarded that
            # precision, sending a targeted retry's per-file "explain the fix"
            # instruction to every unrelated file too. Confirmed as a real, non-
            # theoretical cost, not just a wasted-call inefficiency: one of those
            # broadcast files applied a genuinely unrelated edit (an unprompted
            # config section) that had nothing to do with the actual violation. A
            # violation whose text names no known file (a hypothetical future check
            # with no per-file locality) falls through to extract_implicated_files()'s
            # own empty-list fallback, same honest degrade-to-full-set behavior every
            # other gate type already has - never a regression from today's blind
            # broadcast, just no longer the ONLY behavior available.
            failure = _build_quality_gate_failure(
                type_="static_rule_violation",
                message=f"STATIC RULE VIOLATION: {static_violation}",
                raw_output=static_violation,
                worktree_path=ctx.worktree_path,
                known_files=state.all_files_written,
                attempt=state.attempt_number,
            )
            state.gate_outcomes.append(failure.to_gate_outcome())
            raise QualityGateFailure(failure)

        # Quality Gates: Polymorphic compile & test checks inside sandbox
        logger.info("Quality Gates: Running polymorphic compiler and test checks...")
        validator = PolymorphicValidator(
            ctx.worktree_path, original_workspace_path=ctx.workspace_path,
            autonomy_cfg=ctx.kernel.config.autonomy,
        )

        if not state.toolchain_checked:
            state.toolchain_checked = True
            state.toolchain_warning = _check_java_toolchain_mismatch(validator.stack)
            if state.toolchain_warning:
                logger.warning(f"Toolchain preflight: {state.toolchain_warning}")
            if validator.stack == "java":
                state.java_home_override = _resolve_java_home_override(ctx.goal)
                if state.java_home_override:
                    logger.warning(
                        "JVM toolchain enforcement: forcing Maven subprocess calls to "
                        f"run under JAVA_HOME={state.java_home_override} - the goal-stated Java "
                        "version doesn't match what 'mvn' resolves to by default here."
                    )
        # Constructed fresh above (a new validator every attempt) - re-apply
        # the one-time-resolved override every time, not just when it was
        # just computed.
        validator.java_home_override = state.java_home_override

        compile_res = validator.run_compile_check(list(state.all_files_written))
        if not compile_res["success"]:
            self_correction_result = None
            if ctx.kernel.config.autonomy.self_correction_loop_enabled:
                from kriya.workflow.self_correction import run_self_correction_loop
                logger.info(
                    "Compile gate failed - attempting bounded self-correction "
                    "micro-loop before raising QualityGateFailure."
                )
                self_correction_result = await run_self_correction_loop(
                    llm=ctx.developer.llm,
                    worktree_path=ctx.worktree_path,
                    validator=validator,
                    files_in_scope=list(state.all_files_written),
                    compile_error_output=compile_res["output"],
                    active_code_context=active_code_context,
                    max_turns=ctx.kernel.config.autonomy.self_correction_loop_max_turns,
                )
                for incident in getattr(self_correction_result, "incidents", []):
                    state.record_event(RunEvent(
                        kind="auxiliary.failed",
                        attempt=state.attempt_number,
                        source=incident["source"],
                        authority=EventAuthority.AUXILIARY,
                        message=incident["message"],
                        failure_type=incident["type"],
                        operation="repair_with_patch",
                    ))

            if self_correction_result and self_correction_result.resolved:
                logger.info(
                    "Self-correction micro-loop resolved the compile failure in "
                    f"{self_correction_result.turns_used} turn(s)."
                )
                state.gate_outcomes.append({
                    "attempt": state.attempt_number,
                    "type": "compile",
                    "success": True,
                    "output": self_correction_result.final_compile_output,
                    "self_corrected": True,
                    "self_correction_turns": self_correction_result.turns_used,
                    "self_correction_transcript": self_correction_result.transcript,
                })
            else:
                failure = _build_quality_gate_failure(
                    "compile", f"COMPILATION FAILURE:\n{compile_res['output']}",
                    compile_res.get("output", ""), ctx.worktree_path, state.all_files_written, state.attempt_number,
                )
                if self_correction_result is not None:
                    # The loop ran but didn't resolve it within budget - persist
                    # what it tried (its transcript) instead of silently
                    # discarding it, so a real exhaustion is diagnosable from
                    # gate_outcomes/traces.db afterward rather than only visible
                    # in the process's own (possibly-rotated) log file.
                    failure.self_correction_attempt = {
                        "turns_used": self_correction_result.turns_used,
                        "transcript": self_correction_result.transcript,
                        "final_compile_output": self_correction_result.final_compile_output,
                    }
                state.gate_outcomes.append(failure.to_gate_outcome())
                raise QualityGateFailure(failure)
        else:
            state.gate_outcomes.append({
                "attempt": state.attempt_number,
                "type": "compile",
                "success": True,
                "output": compile_res.get("output", "")
            })

        target_test = extract_target_test(state.error_context, list(state.all_files_written))
        if target_test:
            logger.info(f"Quality Gates: Running targeted tests: {target_test}")
            test_res = validator.run_tests(target_test=target_test)
            if not test_res["success"]:
                test_repair_result = None
                if ctx.kernel.config.autonomy.self_correction_loop_enabled:
                    from kriya.workflow.self_correction import run_repair_loop
                    test_repair_result = await run_repair_loop(
                        llm=ctx.developer.llm,
                        worktree_path=ctx.worktree_path,
                        validator=validator,
                        files_in_scope=list(state.all_files_written),
                        compile_error_output=test_res["output"],
                        active_code_context=active_code_context,
                        max_turns=ctx.kernel.config.autonomy.self_correction_loop_max_turns,
                        failure_type="targeted_test",
                        target_test=target_test,
                    )
                    for incident in getattr(test_repair_result, "incidents", []):
                        state.record_event(RunEvent(
                            kind="auxiliary.failed", attempt=state.attempt_number,
                            source=incident["source"], authority=EventAuthority.AUXILIARY,
                            message=incident["message"], failure_type=incident["type"],
                            operation="repair_with_patch",
                        ))
                if test_repair_result and test_repair_result.resolved:
                    state.gate_outcomes.append({
                        "attempt": state.attempt_number,
                        "type": "targeted_test",
                        "success": True,
                        "output": test_repair_result.final_compile_output,
                        "self_corrected": True,
                        "self_correction_turns": test_repair_result.turns_used,
                        "self_correction_transcript": test_repair_result.transcript,
                    })
                    test_res = {"success": True, "output": test_repair_result.final_compile_output}
                else:
                    failure = _build_quality_gate_failure(
                        "targeted_test", f"TARGETED TEST FAILURE:\n{test_res['output']}",
                        test_res.get("output", ""), ctx.worktree_path, state.all_files_written, state.attempt_number,
                    )
                    if test_repair_result is not None:
                        failure.self_correction_attempt = {
                            "turns_used": test_repair_result.turns_used,
                            "transcript": test_repair_result.transcript,
                            "final_validation_output": test_repair_result.final_compile_output,
                        }
                    state.gate_outcomes.append(failure.to_gate_outcome())
                    raise QualityGateFailure(failure)
            if not (test_repair_result and test_repair_result.resolved):
                state.gate_outcomes.append({
                "attempt": state.attempt_number,
                "type": "targeted_test",
                "success": True,
                "output": test_res.get("output", "")
                })
        else:
            test_written = any("test" in f.lower() or "spec" in f.lower() for f in state.all_files_written)
            if test_written:
                logger.info(f"Quality Gates: Executing tests for {validator.stack} stack...")
                test_res = validator.run_tests()
                if not test_res["success"]:
                    failure = _build_quality_gate_failure(
                        "test", f"TEST FAILURE:\n{test_res['output']}",
                        test_res.get("output", ""), ctx.worktree_path, state.all_files_written, state.attempt_number,
                    )
                    state.gate_outcomes.append(failure.to_gate_outcome())
                    raise QualityGateFailure(failure)
                state.gate_outcomes.append({
                    "attempt": state.attempt_number,
                    "type": "test",
                    "success": True,
                    "output": test_res.get("output", "")
                })

        # Quality Gates: Runtime Verification. Compiling and passing whatever tests
        # exist only proves the code is valid - it says nothing about whether it does
        # what the goal actually asked for, which matters most for goals with no test
        # suite at all. Judgment decides per-attempt whether this goal describes
        # self-terminating runtime behavior worth actually running and checking.
        autonomy_cfg_rv = ctx.kernel.config.autonomy
        if autonomy_cfg_rv.run_verification_enabled and not state.run_verification_declined:
            if state.cached_run_verification_judgment is None:
                pom_content_for_judge = None
                try:
                    with open(os.path.join(ctx.worktree_path, "pom.xml"), "r", encoding="utf-8") as f:
                        pom_content_for_judge = f.read()
                except Exception as e:
                    logger.debug(f"No pom.xml available for run-verification judgment: {e}")
                raw_judgment = await ctx.run_verifier.judge(
                    goal=ctx.goal,
                    design=ctx.design,
                    files_written=list(state.all_files_written),
                    build_file_content=pom_content_for_judge,
                )
                # Independent brutal review finding #2 (2026-08-15): don't trust
                # command_source="goal_explicit" as self-reported - verify it's
                # actually grounded in the goal text before it gets cached (and
                # before the approval-gate check below ever sees it), same
                # "never trust an LLM claim without an independent check" pattern
                # already applied to grade()'s likely_files. See
                # downgrade_ungrounded_goal_explicit_commands()'s own docstring
                # for the full reasoning.
                state.cached_run_verification_judgment = downgrade_ungrounded_goal_explicit_commands(
                    raw_judgment, ctx.goal
                )
            else:
                logger.debug("Reusing cached run-verification judgment from an earlier attempt in this run.")
            judgment = state.cached_run_verification_judgment
            if judgment["should_run"]:
                proceed_with_run = True
                if judgment["command_source"] == "inferred" and not state.run_verification_confirmed:
                    if autonomy_cfg_rv.mode == "human-in-the-loop":
                        commands_desc = "\n".join(
                            f"    {i}. {' '.join(cmd)}" for i, cmd in enumerate(judgment["run_commands"], 1)
                        )
                        confirm_reason = (
                            "Kriya judged that this goal describes runtime behavior compile/test "
                            "checks can't verify, and wants to actually run the generated app:\n"
                            f"  Command(s):\n{commands_desc}\n"
                            f"  Looking for: {judgment['success_criteria']}\n"
                            "Allow Kriya to execute these command(s) inside the sandboxed worktree?"
                        )
                        if ctx.approval_callback:
                            approved = ctx.approval_callback([], confirm_reason)
                            if asyncio.iscoroutine(approved):
                                approved = await approved
                            proceed_with_run = bool(approved)
                        else:
                            logger.warning("Runtime verification warrants human approval but no approval_callback is available. Proceeding under default policy.")
                    if not proceed_with_run:
                        state.run_verification_declined = True
                if proceed_with_run:
                    state.run_verification_confirmed = True
                    resolved_run_commands = [_resolve_run_command(cmd, ctx.worktree_path) for cmd in judgment["run_commands"]]
                    if resolved_run_commands != judgment["run_commands"]:
                        logger.info(
                            "One or more inferred run commands aren't resolvable as given here - "
                            "substituted Kriya's own interpreter/PATH-resolved equivalents."
                        )
                    jvm_flag_correction = _strip_jdk_incompatible_jvm_flags(ctx.worktree_path, state.java_home_override)
                    if jvm_flag_correction:
                        logger.warning(f"JVM flag preflight: {jvm_flag_correction}")
                        state.toolchain_warning = (
                            f"{state.toolchain_warning} {jvm_flag_correction}"
                            if state.toolchain_warning else jvm_flag_correction
                        )
                    exec_pin_correction = _pin_exec_plugin_executable_to_resolved_jdk(ctx.worktree_path, state.java_home_override)
                    if exec_pin_correction:
                        logger.warning(f"JVM executable preflight: {exec_pin_correction}")
                        state.toolchain_warning = (
                            f"{state.toolchain_warning} {exec_pin_correction}"
                            if state.toolchain_warning else exec_pin_correction
                        )
                    logger.info(
                        "Quality Gates: Running runtime verification: "
                        + " && ".join(" ".join(cmd) for cmd in resolved_run_commands)
                    )
                    # Snapshot/clean around the actual execution, not just once
                    # per attempt - see clean_untracked_files_since()'s own
                    # docstring for the real incident (python_task_tracker,
                    # b-6/b-7) this closes: the worktree is deliberately reused
                    # across retry attempts (compile caches), but nothing ever
                    # cleaned up runtime state a PRIOR attempt's own verification
                    # run wrote to disk (a JSON store, a database) - so attempt
                    # N's run started from attempt N-1's leftover state instead
                    # of a fresh one, producing task-ID drift and "not found"
                    # failures that had nothing to do with the generated code.
                    pre_run_untracked = snapshot_untracked_files(ctx.worktree_path)
                    run_res = validator.run_app_sequence(
                        resolved_run_commands,
                        timeout=autonomy_cfg_rv.run_verification_timeout_seconds,
                    )
                    clean_untracked_files_since(ctx.worktree_path, pre_run_untracked)
                    gate_type = "run_verification"
                    if run_res["timed_out"]:
                        # _run_cmd_with_timeout still reaps and captures whatever
                        # stdout/stderr the process produced before being killed (see
                        # kriya/tools/validate.py) - a forced kill does NOT mean nothing
                        # happened. Grading that captured output, same as a clean run,
                        # instead of short-circuiting straight to a flat "timed out"
                        # message, is what lets a genuinely-non-binary outcome surface.
                        # Confirmed live, 2026-08-04: a real Ignite/Qpid run printed its
                        # correct final "[RESULT]" output, then hung (an unclosed Ignite
                        # node's background threads kept the JVM alive) - the old flat
                        # message gave the retry loop zero signal, so every attempt kept
                        # trying timeout-tuning fixes that could never fix a genuine
                        # resource leak, burning the whole retry budget on the wrong
                        # class of change.
                        contract_verdict = _extract_grounded_contract_verdict(run_res["output"], ctx.worktree_path, list(state.all_files_written))
                        if contract_verdict is not None:
                            logger.info(
                                "Runtime verification: using deterministic verification-contract "
                                "marker instead of LLM grading (timed-out run)."
                            )
                            grade = contract_verdict
                        else:
                            grade = await ctx.run_verifier.grade(
                                goal=ctx.goal,
                                success_criteria=judgment["success_criteria"],
                                output=run_res["output"],
                                returncode=run_res["returncode"],
                                files_written=list(state.all_files_written),
                                timed_out=True,
                            )
                        timeout_s = autonomy_cfg_rv.run_verification_timeout_seconds
                        if grade["passed"]:
                            # The goal's described behavior WAS genuinely produced -
                            # this is a categorically different defect than "wrong
                            # behavior": a self-terminating entrypoint that doesn't
                            # terminate is still a real bug (still fails this gate,
                            # still needs a retry), but the fix is almost always the
                            # resource lifecycle (see RESOURCE_LIFECYCLE_HEADER above),
                            # not the application logic that already produced the
                            # correct result - pointing the retry there directly,
                            # rather than at a generic timeout message, is the entire
                            # point of grading the captured output instead of skipping
                            # straight to a synthetic failure.
                            gate_type = "run_verification_hung"
                            grade["reasoning"] = (
                                f"The goal's described output WAS produced correctly, but the "
                                f"process never exited on its own and had to be killed after "
                                f"{timeout_s}s. This is still a real defect, not a false alarm - "
                                "almost always an unclosed resource (a connection, broker client, "
                                "executor, or similar) keeping the process alive after all "
                                "application logic already finished. Fix the resource lifecycle "
                                f"(see Resource Lifecycle above), not the application logic, which "
                                f"already works. Grader's evidence: {grade['reasoning']}"
                            )
                        else:
                            grade["reasoning"] = (
                                f"Run timed out after {timeout_s}s, and the output captured before "
                                f"the forced kill does not show the goal was achieved either: "
                                f"{grade['reasoning']}"
                            )
                        # A hang is always disqualifying regardless of what the
                        # captured-output grade concluded - only the message/gate_type
                        # above differ based on it.
                        grade["passed"] = False
                    elif not run_res["success"]:
                        # A non-final step failing can still leave the LAST step's
                        # returncode at 0 (every command runs regardless of an
                        # earlier step's exit code) - success reflects the whole
                        # sequence, not just the last command, so check that instead.
                        #
                        # Independent-review finding (2026-08-15): this branch used to
                        # short-circuit straight to a synthetic "one or more steps
                        # failed" message, never checking extract_contract_verdict()
                        # or calling grade() at all - the ONLY one of the three outcome
                        # branches (clean run / timed out / plain nonzero exit) that
                        # skipped both. A plain nonzero exit with no hang is plausibly
                        # the single most common real failure shape, and it's exactly
                        # the case VERIFICATION_CONTRACT_HEADER's marker convention is
                        # for (an entrypoint that detects its own failure and exits
                        # nonzero after printing "[VERIFICATION] FAIL: <reason>") - that
                        # rich, deterministic diagnosis was being silently discarded in
                        # favor of a generic "exit code 1", and likely_files was always
                        # empty here, giving the retry loop zero file-attribution signal
                        # for this failure class specifically (the exact gap grade()
                        # exists to close - see its own docstring). Fixed by mirroring
                        # the clean-run branch below exactly: check the deterministic
                        # marker first, only fall back to the LLM grader if the
                        # generated program didn't comply with the contract.
                        contract_verdict = _extract_grounded_contract_verdict(run_res["output"], ctx.worktree_path, list(state.all_files_written))
                        if contract_verdict is not None:
                            logger.info(
                                "Runtime verification: using deterministic verification-contract "
                                "marker instead of LLM grading (non-zero exit, no hang)."
                            )
                            grade = contract_verdict
                        else:
                            grade = await ctx.run_verifier.grade(
                                goal=ctx.goal,
                                success_criteria=judgment["success_criteria"],
                                output=run_res["output"],
                                returncode=run_res["returncode"],
                                files_written=list(state.all_files_written),
                            )
                    else:
                        contract_verdict = _extract_grounded_contract_verdict(run_res["output"], ctx.worktree_path, list(state.all_files_written))
                        if contract_verdict is not None:
                            logger.info(
                                "Runtime verification: using deterministic verification-contract "
                                "marker instead of LLM grading."
                            )
                            grade = contract_verdict
                        else:
                            grade = await ctx.run_verifier.grade(
                                goal=ctx.goal,
                                success_criteria=judgment["success_criteria"],
                                output=run_res["output"],
                                returncode=run_res["returncode"],
                                files_written=list(state.all_files_written),
                            )
                    if not grade["passed"]:
                        # A compile error always names its own broken file
                        # (file:[line,col]) - a runtime failure's captured
                        # output (broker banners, SLF4J lines with no .java
                        # suffix) structurally never does. RunVerifierAgent.grade()'s
                        # already-validated likely_files (grade.get("likely_files"),
                        # absent on the two synthetic timed-out/step-failed grades
                        # built above, only present from a real grade() call) is
                        # passed straight into Failure.likely_files as
                        # extra_likely_files - no more stringify-into-the-message-
                        # then-re-derive-via-regex round-trip.
                        message = (
                            f"RUNTIME VERIFICATION FAILURE: {grade['reasoning']}"
                            f"\n\nCaptured output:\n{run_res['output']}"
                        )
                        # Append the grader's reasoning to what gets PERSISTED
                        # (Failure.raw_output -> to_gate_outcome()'s "output"
                        # field), mirroring the PASSED path a few lines below
                        # (which already does this) - found live, 2026-08-11
                        # (kriya-oneshot-protocol-ignite-qpid audit): without
                        # this, to_gate_outcome() prefers raw_output over
                        # message whenever raw_output is non-empty, so the
                        # grader's diagnosis (embedded in message, used only
                        # for the in-memory retry prompt) was silently absent
                        # from every persisted FAILED run_verification
                        # gate_outcome/traces.db record, even though it was
                        # computed and even though the identical PASSED case
                        # persists it - a real debugging/forensics asymmetry,
                        # confirmed directly against a real trace.
                        enriched_output = run_res["output"] + f"\n\n[Grader reasoning]: {grade['reasoning']}"
                        failure = _build_quality_gate_failure(
                            gate_type, message, enriched_output,
                            ctx.worktree_path, state.all_files_written, state.attempt_number,
                            extra_likely_files=grade.get("likely_files") or [],
                        )
                        state.gate_outcomes.append(failure.to_gate_outcome())
                        raise QualityGateFailure(failure)
                    state.gate_outcomes.append({
                        "attempt": state.attempt_number,
                        "type": "run_verification",
                        "success": True,
                        "output": run_res["output"] + f"\n\n[Grader reasoning]: {grade['reasoning']}",
                        # Only reachable via the clean-run branch above (the timed-out
                        # branch always forces grade["passed"] = False, so it can never
                        # reach here) - contract_verdict is guaranteed in scope. Makes
                        # deterministic-contract-vs-LLM-grader compliance queryable
                        # directly from traces.db instead of grepping raw stdout logs by
                        # hand, which is what diagnosing the underlying reliability gap
                        # required this session, repeatedly.
                        "graded_by": "contract" if contract_verdict is not None else "llm",
                    })
                    logger.info(f"Quality Gates: Runtime verification PASSED: {grade['reasoning']}")
                    # A passing real-world run is exactly the proof the
                    # skill-verification gap check is looking for - mark every
                    # skill that contributed to this generation as verified so
                    # future runs stop asking about it.
                    for active_skill_name in ctx.active_skills:
                        try:
                            active_skill_obj = ctx.skill_engine.get_skill(active_skill_name)
                            context = _skill_verification_context(active_skill_obj, ctx.goal)
                            ctx.skill_engine.mark_verified(active_skill_name, context=context)
                            # Also flip per-rule provenance for exactly the
                            # rules that were part of this skill when this
                            # run's context was built (the pre-retry-loop
                            # snapshot) - not whatever rules.txt contains now.
                            if active_skill_obj.source_path and active_skill_name in ctx.active_skill_rules_snapshot:
                                from kriya.skills.skill import mark_rules_verified
                                mark_rules_verified(active_skill_obj.source_path, ctx.active_skill_rules_snapshot[active_skill_name])
                        except Exception as ex:
                            logger.debug(f"Failed to mark skill '{active_skill_name}' verified: {ex}")

    # If we made it here, Quality Gates passed successfully!
    logger.info("Quality Gates check PASSED.")
