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
from kriya.workflow.edit_safety import apply_anchored_edits, find_edits_ignoring_own_diagnosis, find_edits_ignoring_reported_line, find_structural_corruption
from kriya.workflow.failure import Failure, FileLocation, QualityGateFailure
from kriya.workflow.failure_grounding import _build_quality_gate_failure
from kriya.workflow.file_resolution import IncompleteGenerationError, _resolve_run_command, extract_planner_code_blocks, extract_target_test, find_missing_expected_files, normalize_written_filepath
from kriya.workflow.context_budget import _reserve_graph_context_budget, build_code_context
from kriya.workflow.retry_prompts import _build_full_set_retry_prompt, _build_missing_files_retry_prompt, _build_targeted_retry_prompt
from kriya.workflow.skill_extraction import _skill_verification_context
from kriya.workflow.state import GenerationState
from kriya.workflow.static_checks import run_static_checks
from kriya.workflow.attribution import extract_self_diagnosed_files, resolve_fallback_model
from kriya.workflow.toolchain import _check_java_toolchain_mismatch, _pin_exec_plugin_executable_to_resolved_jdk, _resolve_java_home_override, _strip_jdk_incompatible_jvm_flags
from kriya.workflow.verification_contract import extract_contract_verdict

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
        )
    else:
        # Re-run context budget allocator dynamically for escalated model context window size
        current_limit = _reserve_graph_context_budget(
            ctx.kernel.config.llm.context_window, ctx.skills_prompt, ctx.learned_rag_context
        )
        model_override = None
        base_url_override = None
        api_key_override = None

        fallback = resolve_fallback_model(state.budgets.retry_count, ctx.chain)
        if fallback is not None:
            model_override = fallback.model
            base_url_override = fallback.base_url
            api_key_override = fallback.api_key
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
    self_diagnosed = extract_self_diagnosed_files(files, list(state.all_files_written))
    if self_diagnosed:
        state.last_self_diagnosis = (state.budgets.last_failure_signature, self_diagnosed)

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

            # Layer 1 pre-flight check (see find_edits_ignoring_reported_line's
            # own docstring): only meaningful when this attempt is itself
            # responding to a real prior error with a locatable line for this
            # file - error_context is "" on a clean first attempt, where
            # extract_error_source_locations() would find nothing anyway, but
            # the explicit guard avoids the line-matching work entirely there.
            if state.error_context:
                ignored_lines = find_edits_ignoring_reported_line(
                    orig_text, edits, filepath, state.error_context
                )
                if ignored_lines:
                    lines_desc = ", ".join(str(n) for n in sorted(ignored_lines))
                    # Preserve the ORIGINAL error text (with its javac
                    # file:[line,col] locator) inside the message, not just a
                    # paraphrase - error_context becomes the NEXT attempt's
                    # error_context via `error_context = raw_error_context`
                    # below, and both extract_error_source_locations() (this
                    # same check, on a possible next attempt) and
                    # _build_error_source_context() (the source-snippet shown
                    # in the prompt) depend on that locator still being present
                    # verbatim - a paraphrase-only message would silently lose
                    # source-line grounding from here on.
                    failure = Failure(
                        type="unaddressed_error_location",
                        message=(
                            f"UNADDRESSED ERROR LOCATION in {filepath}: your previous edit's "
                            f"search block included line(s) {lines_desc} of the error below, but "
                            f"left that exact line unchanged in its replace text - applying it "
                            f"would leave the identical error in place. If your fix genuinely "
                            f"doesn't require changing line(s) {lines_desc} itself (e.g. you fixed "
                            f"a type declaration elsewhere instead), don't include it in your "
                            f"search block at all; otherwise, you MUST change that exact line.\n\n"
                            f"Original error:\n{state.error_context}"
                        ),
                        raw_output=f"search block ignored reported line(s) {lines_desc}",
                        file_locations=[
                            FileLocation(filepath=filepath, line=n) for n in sorted(ignored_lines)
                        ],
                        likely_files=[filepath],
                        failed_content={filepath: orig_text},
                        attempted_edits=edits,
                        attempt=state.attempt_number,
                    )
                    state.gate_outcomes.append(failure.to_gate_outcome())
                    raise QualityGateFailure(failure)

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

            with open(full_path, "w", encoding="utf-8") as f:
                f.write(new_content)
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

            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

        state.files_written.append(filepath)
        state.all_files_written.add(filepath)
        logger.info(f"Wrote generated/edited file to sandbox: {filepath}")

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
        # rule for, before the expensive compile+run cycle rather than after it. Same
        # philosophy/wiring shape as find_structural_corruption() above: a direct
        # Failure(...) construction (no compiler output exists yet to ground against).
        static_violation = run_static_checks(ctx.worktree_path, state.all_files_written)
        if static_violation:
            failure = Failure(
                type="static_rule_violation",
                message=f"STATIC RULE VIOLATION: {static_violation}",
                raw_output=static_violation,
                likely_files=list(state.all_files_written),
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
                failure = _build_quality_gate_failure(
                    "targeted_test", f"TARGETED TEST FAILURE:\n{test_res['output']}",
                    test_res.get("output", ""), ctx.worktree_path, state.all_files_written, state.attempt_number,
                )
                state.gate_outcomes.append(failure.to_gate_outcome())
                raise QualityGateFailure(failure)
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
                state.cached_run_verification_judgment = await ctx.run_verifier.judge(
                    goal=ctx.goal,
                    design=ctx.design,
                    files_written=list(state.all_files_written),
                    build_file_content=pom_content_for_judge,
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
                    run_res = validator.run_app_sequence(
                        resolved_run_commands,
                        timeout=autonomy_cfg_rv.run_verification_timeout_seconds,
                    )
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
                        contract_verdict = extract_contract_verdict(run_res["output"])
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
                        grade = {"passed": False, "reasoning": f"One or more steps failed (final step exit code {run_res['returncode']})."}
                    else:
                        contract_verdict = extract_contract_verdict(run_res["output"])
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
