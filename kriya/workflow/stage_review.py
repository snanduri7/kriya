"""Stage 5 of a work unit: the Reviewer (full modularization W4, E4).

Moved verbatim from ``WorkflowEngine.run_generation_workflow`` (``kriya/workflow/workflow.py``):

- ``pre_approval_review``: the REAL Reviewer work of an escalated run, done at the Pre-Apply Human Approval Gate
  (Stage 6 SME review, Finding 1) so the person sees the completed report before deciding; a failed request is
  recorded (``review.pre_approval_unattached``, PRE-APPROVAL-REVIEW-REFUSAL-001) and the approval proceeds without it;
- ``final_review``: stage 5 proper, after the Developer loop: it reuses the pre-approval report when one exists,
  otherwise reviews the applied (or, after exhausted gates, the rejected) candidate, and ends a refused or failed
  review request as the typed ``final_review_refusal`` with its ``review.refused`` event (PROMPT-BUDGET-FIT-001C,
  FINAL-REVIEW-BACKEND-ERROR-001; the reason-code mapping ``_final_review_error_code`` and the ``FINAL_REVIEW_*``
  codes stay on the facade, whose provider imports are allowlisted for it alone).

Collaborators arrive in ``StageDeps`` (``kriya/workflow/stage_context.py``), the run-local names in ``StageContext``;
this module never imports ``kriya.workflow.workflow``.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Tuple

from kriya.workflow.run_events import EventAuthority, RunEvent
from kriya.workflow.stage_context import StageContext, StageDeps


async def pre_approval_review(
    ctx: StageContext, deps: StageDeps, *, worktree_file_contents: Dict[str, str], escalation_reason: str,
) -> str:
    """The Reviewer's report on the candidate the person is asked to approve, attached to ``escalation_reason`` (or the
    reason no report is attached); returns the extended escalation reason."""
    # The stage's run-local names (StageContext) and injected collaborators (StageDeps), bound under the names the
    # body used inside run_generation_workflow; the body below is that method's code, moved verbatim.
    state, goal, stream_callback = ctx.state, ctx.goal, ctx.stream_callback
    resolved_obligation_ledger = ctx.resolved_obligation_ledger
    logger, reviewer, _log_phase_banner = deps.logger, deps.reviewer, deps._log_phase_banner
    review_requests, build_reviewer_verified_evidence = deps.review_requests, deps.build_reviewer_verified_evidence
    ownership_review_evidence, REVIEW_SECTION_HEADER = deps.ownership_review_evidence, deps.REVIEW_SECTION_HEADER
    _record_review_fit, _review_refit_recorder = deps._record_review_fit, deps._review_refit_recorder

    # The REAL Reviewer work for an escalated run happens HERE, not at
    # the later "5. Reviewer" section below (which, for this exact
    # path, only reuses state.pre_approval_review and does no new
    # work - see its own comment). Banner placed at the LATER section
    # unconditionally was found live to fire only after the human had
    # already been shown the review and approved it, misleadingly
    # announcing "Review" as just starting when it had already
    # finished minutes earlier.
    _log_phase_banner("REVIEW")
    try:
        verified_evidence = build_reviewer_verified_evidence(state.gate_outcomes) + \
            ownership_review_evidence(resolved_obligation_ledger, state.all_files_written)
        review_header = f"Goal: {goal}\n{verified_evidence}\nFiles generated:\n"
        # PROMPT-BUDGET-FIT-001B: batches get the room this
        # request leaves after its system prompt and header,
        # refitted for each role candidate that is called.
        review_batches, _, review_fit = review_requests(
            deps.config, reviewer,
            [(fp, worktree_file_contents[fp]) for fp in sorted(state.all_files_written)],
            reviewer.system_prompt, review_header,
            on_refit=_review_refit_recorder(state, "pre_approval", deps.config),
        )
        _record_review_fit(state, "pre_approval", review_fit)
        # The completed report is attached to the approval context
        # below, where the human must see it before deciding. Streaming
        # the same tokens first creates a second presentation of one
        # artifact. Keep a progress signal, but deliver the body once.
        if stream_callback:
            stream_callback(
                "Review", "Preparing automated code review for approval...\n",
            )
        review_parts = []
        for i, batch_prompts in enumerate(review_batches, 1):
            label = "" if len(review_batches) == 1 else f"\n=== Batch {i}/{len(review_batches)} ===\n"
            _reviewer_started = time.monotonic()
            review_text = await reviewer.run(
                batch_prompts.first(), stream_callback=None,
                temperature_override=deps.config.llm.reviewer_temperature,
                candidate_prompt=batch_prompts,
            )
            # R1 Deliverable 5 - observational only, one
            # entry per review batch (a multi-batch review
            # makes more than one real LLM call).
            state.reviewer_calls += 1
            state.reviewer_llm_seconds += time.monotonic() - _reviewer_started
            review_parts.append(label + review_text)
        state.pre_approval_review = "\n".join(review_parts)
        escalation_reason += f"\n\n{REVIEW_SECTION_HEADER}\n{state.pre_approval_review}"
    except Exception as ex:
        logger.warning(f"Pre-approval Reviewer call failed, proceeding without it: {ex}")
        # PRE-APPROVAL-REVIEW-REFUSAL-001 (PRD-033): the approver
        # and the trace both see that no review is attached, and why.
        unattached_reason = getattr(ex, "reason_code", None) or type(ex).__name__
        state.record_event(RunEvent(
            kind="review.pre_approval_unattached", attempt=state.attempt_number,
            source="workflow.pre_approval_review", authority=EventAuthority.ADVISORY,
            message="the pre-approval review was not performed; approval proceeds without it",
            details={"reason_code": unattached_reason},
        ))
        escalation_reason += (
            f"\n\n{REVIEW_SECTION_HEADER}\nNOT ATTACHED: the review request failed "
            f"({unattached_reason}). Review the diff without it."
        )

    return escalation_reason


async def final_review(ctx: StageContext, deps: StageDeps) -> Any:
    """Stage 5: the run's review text (the reused pre-approval report, the review of the applied candidate, the
    rejected-candidate diagnostic, or the refusal note), after the step callback saw it."""
    state, goal, workspace_path = ctx.state, ctx.goal, ctx.workspace_path
    step_callback, stream_callback = ctx.step_callback, ctx.stream_callback
    resolved_obligation_ledger = ctx.resolved_obligation_ledger
    logger, reviewer, _log_phase_banner = deps.logger, deps.reviewer, deps._log_phase_banner
    review_requests, build_reviewer_verified_evidence = deps.review_requests, deps.build_reviewer_verified_evidence
    build_candidate_diff_context, ownership_review_evidence = deps.build_candidate_diff_context, deps.ownership_review_evidence
    _record_review_fit, _review_refit_recorder = deps._record_review_fit, deps._review_refit_recorder
    ContextBudgetUnsatisfiableError, InferenceDeadlineError = deps.ContextBudgetUnsatisfiableError, deps.InferenceDeadlineError
    _final_review_error_code = deps._final_review_error_code

    # 5. Reviewer
    if state.pre_approval_review is not None:
        # Stage 6 SME review, Finding 1: already ran at the
        # Pre-Apply Human Approval Gate, against the exact same final content - a
        # second call here would be a redundant LLM round-trip for an identical
        # answer. step_callback still fires below so anything consuming the "Review"
        # step in pipeline order sees it at the position it expects. No banner here
        # - it already fired at the pre-approval gate above, where the real work
        # happened; repeating it here would misleadingly announce "Review" as just
        # starting when it in fact finished (and was already shown to the human)
        # earlier in this same run.
        logger.info(
            "Reusing pre-approval Reviewer report; no second Reviewer call required."
        )
        review = state.pre_approval_review
    else:
        _log_phase_banner("REVIEW")
        logger.info("Reviewer Agent evaluating results...")
        if state.final_attempt_contents:
            goal_header = (
                f"Goal: {goal}\n\n"
                "NOTE: Quality gates did not pass within the retry budget - these files were "
                "NOT applied to the workspace and only reflect the last (failing) attempt.\n"
                f"Last quality gate error:\n{state.error_context}\n\nFiles from the failing attempt:\n"
            )
        else:
            goal_header = (
                f"Goal: {goal}\n{build_reviewer_verified_evidence(state.gate_outcomes)}"
                f"{ownership_review_evidence(resolved_obligation_ledger, state.all_files_written)}"
                "\nFiles generated:\n"
            )

        file_contents_for_review: List[Tuple[str, str]] = []
        for filepath in sorted(state.all_files_written):
            if filepath in state.final_attempt_contents:
                file_contents_for_review.append((filepath, state.final_attempt_contents[filepath]))
                continue
            full_path = os.path.join(workspace_path, filepath)
            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    file_contents_for_review.append((filepath, f.read()))
            except Exception as e:
                logger.debug(f"Failed to read '{full_path}' for reviewer prompt: {e}")

        # Reviewer mutation-evidence priority (2026-09-20): a real live
        # incident - a large brownfield file's own candidate diff sat
        # deep past where build_review_batches's own from-the-front
        # token-budget truncation reached, so the Reviewer was shown
        # thousands of lines of untouched code and never the actual
        # changed region, then correctly (but unhelpfully) rejected the
        # candidate for "insufficient evidence." Prepended to goal_header
        # so it reaches the Reviewer in EVERY batch, ahead of the
        # (possibly-truncated) full-file content - see build_candidate_
        # diff_context()'s own docstring for the full incident and why
        # this is additive, never a replacement for the existing
        # full-file block. "" (no candidate files changed relative to
        # their own original content - e.g. every written file is
        # brand new) is a complete no-op, byte-identical to before this
        # fix.
        candidate_diff_context = build_candidate_diff_context(
            state.all_original_contents, dict(file_contents_for_review),
        )
        goal_header = candidate_diff_context + goal_header

        # Stage 6 SME review, Finding 2: previously concatenated every file's full
        # raw content with no token-budget check at all - the exact silent-
        # truncation-from-the-front failure mode the standalone `kriya review` CLI
        # command was already fixed for (see kriya/workflow/review_context.py).
        # PROMPT-BUDGET-FIT-001B: batches get the room this request
        # leaves after the system prompt actually sent and the header
        # (goal, candidate diff, gate and ownership evidence).
        # Authoritative disposition override (2026-09-11, Demo-01 Run A
        # finding): state.final_attempt_contents is populated ONLY on the
        # terminal-failure paths (retry_strategy.py's scope-conflict and
        # budget-exhausted branches) where this run will not succeed -
        # the exact same, already-established signal goal_header above
        # keys off of. Passing a disposition-aware system_prompt_override
        # here, rather than relying solely on the plain-text NOTE already
        # in goal_header, is what stops the Reviewer from writing "How to
        # Run" instructions for a candidate that was never applied.
        reviewer_system_prompt_override = (
            reviewer.rejected_candidate_system_prompt(
                state.error_context or "Quality gates did not pass within the retry budget."
            )
            if state.final_attempt_contents else None
        )
        review_batches, _, review_fit = review_requests(
            deps.config, reviewer, file_contents_for_review,
            reviewer_system_prompt_override or reviewer.system_prompt, goal_header,
            on_refit=_review_refit_recorder(state, "final", deps.config),
        )
        _record_review_fit(state, "final", review_fit)
        # Demo-01 Finding 3 follow-up (2026-09-11): a rejected/unapplied
        # candidate's review must never stream raw, unfiltered LLM
        # tokens live to the user - extract_rejected_candidate_
        # diagnostic() below only ever filters the FINAL joined text
        # (review_text), so leaving live streaming on here would still
        # leak the exact unfiltered "How to Run"/success-language
        # content Finding 3 already closed for the final output, just
        # earlier, token by token. Suppressed entirely for this case
        # (not filtered after the fact - the raw tokens are simply
        # never sent to stream_callback at all); the accepted-candidate
        # path is completely unaffected.
        reviewer_stream = (
            (lambda token: stream_callback("Review", token))
            if stream_callback and not state.final_attempt_contents else None
        )
        review_parts = []
        for i, batch_prompts in enumerate(review_batches, 1):
            label = "" if len(review_batches) == 1 else f"\n=== Batch {i}/{len(review_batches)} ===\n"
            _reviewer_started = time.monotonic()
            try:
                review_text = await reviewer.run(
                    batch_prompts.first(), stream_callback=reviewer_stream,
                    temperature_override=deps.config.llm.reviewer_temperature,
                    system_prompt_override=reviewer_system_prompt_override,
                    candidate_prompt=batch_prompts,
                )
            except (ContextBudgetUnsatisfiableError, InferenceDeadlineError) as refusal:
                # PROMPT-BUDGET-FIT-001C: PRD-016 refused the request
                # before inference (or, PROVIDER-CONTRACT-001A, the run's
                # generation deadline refused or stopped it). The run ends
                # as a typed non-success that keeps what already happened
                # (the candidate may be applied and committed); nothing is
                # retried or rolled back.
                state.final_review_refusal = {
                    "reason_code": refusal.reason_code, "detail": str(refusal),
                    "batch": i, "batches": len(review_batches),
                }
                state.record_event(RunEvent(
                    kind="review.refused", attempt=state.attempt_number, source="workflow",
                    authority=EventAuthority.AUTHORITATIVE,
                    message=("the final review request was stopped by the run's generation deadline"
                             if isinstance(refusal, InferenceDeadlineError)
                             else "the final review request was refused before inference"),
                    details=dict(state.final_review_refusal),
                ))
                logger.error(f"Final review not performed: {refusal}")
                review_parts.append(label + f"Final review not performed: {refusal}")
                break
            except Exception as error:
                # FINAL-REVIEW-BACKEND-ERROR-001: any other failure of the
                # review request (a provider/transport error, a protocol
                # error, an unexpected exception) once the candidate is
                # applied and committed ends the run the same typed way
                # (final_review_refused, naming the committed state) with
                # the error's own reason code (FINAL_REVIEW_BACKEND_ERROR
                # when it carries none) and type - never a raw exception
                # out of run_generation_workflow over work already in the
                # workspace. Nothing is retried or rolled back. A coding
                # error here still surfaces: its type and message are the
                # recorded detail, and the run is a non-success.
                code, message = _final_review_error_code(error)
                state.final_review_refusal = {
                    "reason_code": code,
                    "detail": f"{type(error).__name__}: {error}", "exception_type": type(error).__name__,
                    "batch": i, "batches": len(review_batches),
                }
                state.record_event(RunEvent(
                    kind="review.refused", attempt=state.attempt_number, source="workflow",
                    authority=EventAuthority.AUTHORITATIVE, message=message,
                    details=dict(state.final_review_refusal),
                ))
                logger.error(f"Final review not performed: {type(error).__name__}: {error}")
                review_parts.append(label + f"Final review not performed: {type(error).__name__}: {error}")
                break
            # Demo-01 Finding 3 (2026-09-11): the structural enforcement
            # boundary itself - applied per-batch (not to the joined
            # final text) so one batch's non-compliance with the marker
            # contract doesn't discard another batch's compliant
            # diagnostic content. Streamed tokens above (reviewer_stream)
            # are real-time progress display and are not retroactively
            # filtered - this governs the FINAL text that reaches
            # res["review"]/the CLI, which is what Finding 3 is actually
            # about.
            if state.final_attempt_contents:
                review_text = reviewer.extract_rejected_candidate_diagnostic(review_text)
            # R1 Deliverable 5 - observational only, same posture as the
            # pre-approval reviewer wrapper above.
            state.reviewer_calls += 1
            state.reviewer_llm_seconds += time.monotonic() - _reviewer_started
            review_parts.append(label + review_text)
        review = "\n".join(review_parts)
    if step_callback:
        step_callback("Review", review)

    return review


__all__ = ["final_review", "pre_approval_review"]
