"""Sequential, first-attempt-only Best-of-N: tries up to N independent full-set
candidates for the very first generation attempt of a run, before falling into
the existing reactive retry loop (kriya/workflow/workflow.py's `while` loop).

Deliberately sequential, never parallel - a goal that binds real, fixed
resources (an embedded broker's AMQP port, an Ignite node's discovery/comm
ports) would have two candidates' generated apps port-conflict with each
other under real parallel execution, independent of hardware; local model
serving (Ollama) doesn't meaningfully parallelize multiple requests against
one loaded model either, so "parallel" would only add queueing overhead for
no benefit. Peak resource usage at any moment is identical to a normal
`generate` call today - the only cost is added wall-clock in the bounded
worst case (every candidate fails).

Targets a specific, evidenced failure shape: the SAME goal, with the SAME
skill rules already in context, sometimes produces a compliant first attempt
and sometimes doesn't (confirmed live, 2026-08-12, ignite_qpid_protocol - one
attempt correctly used Apache Ignite's Method B, a differently-sampled attempt
mixed Method A and B despite the exact rule against it already being in the
active skill's rules.txt). Best-of-N gives the model a second, third,
independent roll at the SAME starting point rather than waiting for that
mistake to surface as a real, expensive compile/run failure and reactively
patching it - one candidate needs to succeed, not the first one.
"""
import logging

from kriya.workflow.repair_contract import RepairContractStatus
from kriya.workflow.state import RecoveryPhaseAdvanced

logger = logging.getLogger(__name__)


class BestOfNFailureRecorded(Exception):
    """A candidate failure best-of-N already recorded through
    handle_attempt_failure, handed to the retry loop when no further
    independent candidate follows (STATE-BEST-OF-N-HANDOFF-001). The loop
    acts on ``stop_loop`` and must not record the failure a second time:
    that would charge its budget, ledger it and classify its progress twice."""

    def __init__(self, failure: Exception, *, stop_loop: bool) -> None:
        super().__init__(str(failure))
        self.failure = failure
        self.stop_loop = stop_loop


def _authoritative_repair_active(state) -> bool:
    """Repair state the next attempt must honour (sticky API contract
    recovery, an active MA9 repair contract). A reset candidate is an
    ungrounded full-set attempt, so it cannot start while either is active:
    that state belongs to the main loop."""
    contract = state.repair_contract
    return state.api_contract_recovery is not None or (
        contract is not None and contract.status == RepairContractStatus.ACTIVE
    )


def reset_state_for_independent_candidate(state) -> None:
    """Resets exactly the fields that make attempt.py's full-set branch treat this
    as a fresh, ungrounded attempt (the sole gate is state.error_context being
    falsy - see kriya/workflow/retry_prompts.py's "=== Previous Error to Fix ==="
    section) plus the fields that would otherwise leak a discarded candidate's
    content into the next one's prompt or trigger unrelated retry machinery.

    Explicitly does NOT reset:
    - state.attempt_number / state.gate_outcomes / state.model_hops - trace
      continuity, so every candidate's outcome (including discarded ones) stays
      visible in the full run trace, not silently thrown away.
    - state.budgets.targeted_retry_count / fallback_targeted_attempted - moot,
      since a reset candidate always re-enters the full-set branch regardless.
    - toolchain_checked / toolchain_warning / java_home_override / jdtls_client /
      jdtls_unavailable / lsp_warning - real environment facts about this
      machine, not specific to any one candidate.
    - run_verification_confirmed / run_verification_declined /
      cached_run_verification_judgment - their own field docstrings say these
      deliberately persist across retry attempts within a run (a human's
      approval to execute the generated app shouldn't be re-asked per candidate).
    - state.budgets.best_of_n_candidates_tried - accumulates across the whole
      run by design, never resets here (see its own docstring in state.py).
    """
    state.error_context = ""
    state.last_failure = None
    state.last_implicated_files = None
    state.last_missing_files = None
    state.last_error_source_context = {}
    state.all_files_written = set()
    state.all_original_contents = {}
    state.validated_file_revisions = {}
    state.files_written = []
    state.budgets.retry_count = 0
    # Closes a real gap found during design validation: leaving this set would
    # make a second, genuinely independent candidate that happens to fail with
    # the same signature as the just-discarded one look like a "repeat failure"
    # to handle_attempt_failure's live-lookup escalation gate - firing a real
    # (possibly interactive, network-calling) lookup for a candidate that's
    # about to be thrown away anyway.
    state.budgets.last_failure_signature = None
    state.budgets.seen_failure_signatures = set()
    state.budgets.scoped_full_set_failure_signature = None
    state.budgets.fallback_targeted_requested = False
    state.budgets.anchor_failure_counts = {}
    # Same reasoning as last_failure_signature above: a fresh, independent
    # candidate must not be treated as a "no new evidence" repeat of
    # whatever the just-discarded candidate's own last attempt showed.
    state.budgets.last_retry_evidence_fingerprint = None
    state.last_failed_workspace_hash = None
    state.last_progress_failure_signature = None
    state.last_progress_stage = None
    state.last_progress_files = ()
    state.last_progress_action = None
    state.last_progress_classification = None
    state.consecutive_no_progress_attempts = 0
    state.no_progress_terminated = False


async def run_attempt_with_best_of_n(state, attempt_ctx, n: int) -> None:
    """Tries up to `n` independent full-set candidates. Returns normally the
    instant one passes - indistinguishable to the caller from a single
    successful run_attempt() call, so every one of workflow.py's existing
    post-success steps (checkpoint, human approval, apply-to-workspace,
    regression suite, lesson extraction) runs completely unchanged.

    A candidate failure that ends sampling early (the loop must stop, the
    budgets are spent, authoritative repair state became active, or no fresh
    sandbox is available) was already recorded here, so it reaches the loop
    as BestOfNFailureRecorded and is never recorded twice.

    If every candidate fails, re-raises the LAST one's exception un-swallowed,
    so the existing `except Exception as e: if await handle_attempt_failure(...):
    break` right after the call site in workflow.py handles it exactly as it
    already handles today's single-attempt failure - no new failure-handling
    path, no duplicated logic.

    Only ever called for the very first attempt of a run (state.attempt_number
    == 0 going in, checked by the caller) - a resumed checkpoint's first
    run_attempt() call short-circuits and returns success on iteration 0 here,
    so Best-of-N never actually re-attempts a resumed run.
    """
    from kriya.workflow.attempt import run_attempt
    from kriya.workflow.retry_policy import decide_for_state
    from kriya.workflow.retry_strategy import handle_attempt_failure
    from kriya.workflow.worktree import create_git_worktree

    for i in range(n):
        try:
            await run_attempt(state, attempt_ctx)
            return
        except RecoveryPhaseAdvanced:
            # A verified recovery transition, never a candidate failure: the
            # retry loop's own handler records it.
            raise
        except Exception as e:
            if i == n - 1:
                raise
            should_stop = await handle_attempt_failure(state, attempt_ctx, e)
            # The failure is recorded from here on: every exit below hands
            # the loop that record, never the raw exception.
            if should_stop or _authoritative_repair_active(state) or not decide_for_state(
                state, max_retries=attempt_ctx.max_retries,
                targeted_max_retries=attempt_ctx.targeted_max_retries,
                has_fallback_model=bool(attempt_ctx.chain),
            ).should_continue:
                raise BestOfNFailureRecorded(e, stop_loop=should_stop) from e
            # The fresh sandbox comes first: the state is reset for an
            # independent candidate only once that candidate can run.
            try:
                create_git_worktree(attempt_ctx.workspace_path)
            except Exception as reset_error:
                # No clean sandbox for another candidate: stop sampling. The
                # loop continues from the recorded failure, in its own
                # worktree, exactly as after an ordinary failed attempt.
                logger.warning(f"Best-of-N: worktree reset failed, no further candidate: {reset_error!r}")
                raise BestOfNFailureRecorded(e, stop_loop=False) from e
            state.budgets.best_of_n_candidates_tried += 1
            reset_state_for_independent_candidate(state)
            logger.info(
                f"Best-of-N: candidate {i + 1}/{n} failed, trying an independent "
                f"candidate {i + 2}/{n}."
            )
