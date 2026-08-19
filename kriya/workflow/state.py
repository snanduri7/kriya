"""Explicit state for run_generation_workflow()'s Developer + Quality Gates
retry loop - replaces ~25 mutable local variables previously threaded through
the loop via closures. Extracted 2026-08-11 (Opportunity 2, Slice 1): the
loop's control flow and every read/write site are unchanged, only the
storage moved from bare names to attributes on this object - this is what
makes the next slices (an isolable attempt executor and retry-decision
function) possible to unit-test without invoking the whole method.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from kriya.workflow.run_events import EventAuthority, FailureLedger, RunEvent
from kriya.workflow.evidence import EvidenceRecord


@dataclass
class RetryBudgets:
    """The counters that govern which attempt mode comes next - grouped
    separately because they're read/written together as a unit by the
    retry-decision logic, unlike the rest of GenerationState's fields."""

    # Full-set retry attempt counter, bounded by max_retries (max(4, 1+len(chain))).
    retry_count: int = 0
    # Independent budget for targeted (single/few-file) retries - deliberately
    # NOT folded into retry_count, which governs the full-file-set path and its
    # model-escalation chain. Targeted attempts always use the primary model
    # (never escalate - a measured 19-43s Ollama model-swap cost made "swap on
    # every targeted attempt" a bad trade). Exhausting this budget falls
    # through to the full-set path's own budget/escalation, unchanged.
    targeted_retry_count: int = 0
    # A single, one-shot opportunity to try a TARGETED fix on the fallback model
    # before escalating all the way to a full-set regeneration (found live,
    # 2026-08-10, ignite_qpid_protocol): a full-set escalation already pays a
    # model-swap cost, so trying one targeted fix on that same fallback model
    # first spends a swap cost that was coming anyway on a much cheaper shot.
    # Deliberately its own flag, not folded into targeted_retry_count (primary
    # model only, never escalate) or retry_count (the full-set path's own
    # counter).
    fallback_targeted_attempted: bool = False
    # (fail_type, signature) of the previous attempt's failure, so a REPEATED
    # failure (the model isn't self-correcting) can be distinguished from a
    # normal first-time failure - only a repeat is eligible for error-triggered
    # live lookup.
    last_failure_signature: Optional[Tuple[str, Any]] = None
    # How many independent candidates kriya/workflow/best_of_n.py discarded before
    # this run's winning (or final) attempt. Unlike retry_count, this is NEVER reset
    # between candidates - it's a running total across the whole run, since it exists
    # specifically to answer "was this actually hard-won" for the heuristics in
    # workflow.py that read retry_count for that purpose, after best_of_n.py resets
    # retry_count back to 0 for each fresh independent candidate.
    best_of_n_candidates_tried: int = 0
    # {filepath: consecutive diagnosis_mismatch rejections for that file},
    # added 2026-08-17 for the bounded-veto policy (attempt.py's
    # _diagnosis_mismatch_bypass_reason): find_edits_ignoring_own_diagnosis
    # is a fuzzy prose-vs-diff heuristic, proven repeatedly this session to
    # have real false-positive shapes (5 found and closed via signals, a 6th
    # via a bypass, see docs/design.md §7.29/§7.31) - no matter how many more
    # are found and fixed, the heuristic can never be proven complete. This
    # counter is the safety net UNDERNEATH all of that: once a file's edit
    # has been rejected by this specific check once, it is NEVER rejected by
    # it again in the same run, regardless of fail_type or whether a cheap
    # re-check exists - the real downstream compile/test/verification gates
    # decide instead. Reset to 0 whenever an attempt writes that file WITHOUT
    # this check flagging it, so an unrelated, later mismatch on the same
    # file still gets its own first (bounded) veto.
    diagnosis_mismatch_veto_counts: Dict[str, int] = field(default_factory=dict)


@dataclass
class GenerationState:
    """Everything the Developer + Quality Gates retry loop reads or writes
    across iterations. Constructed once per run_generation_workflow() call,
    right before the loop starts."""

    # Unified attempt counter for gate_outcomes/logging only - retry_count and
    # targeted_retry_count are the actual budget counters, but a single
    # chronological attempt number reads far more sensibly in the trace than
    # two counters that don't both advance on every iteration.
    attempt_number: int = 0
    error_context: str = ""
    files_written: List[Dict[str, str]] = field(default_factory=list)
    all_files_written: Set[str] = field(default_factory=set)
    all_original_contents: Dict[str, str] = field(default_factory=dict)
    # Captures the last attempt's file contents before worktree cleanup, so the
    # Reviewer stage has something to review even when quality gates never
    # passed (those files never get copied to workspace_path - only ever lived
    # in the worktree, which gets git-clean'd on failure).
    final_attempt_contents: Dict[str, str] = field(default_factory=dict)
    # Which retry mode the most recent run_attempt() call actually used -
    # "targeted"/"missing_files"/"fallback_targeted"/"full_set". Set at the
    # very start of run_attempt(), from the same derivation the caller needs
    # afterward (for logging and retry-budget accounting) - recomputing the
    # same booleans from state AFTER the attempt returns/raises would be
    # wrong, since fallback_targeted_attempted is deliberately flipped True
    # as the first action inside the fallback_targeted branch itself.
    last_attempt_mode: Optional[str] = None
    # Whether attempt 1 reused the Planner's own over-delivered code blocks
    # verbatim (extract_planner_code_blocks(), attempt.py) instead of a fresh
    # Developer generation call - None until attempt 1's full-set branch
    # actually runs (never reassigned after, since that branch only executes
    # once per run: gated on state.budgets.retry_count == 0). Recorded purely
    # for observability - added 2026-08-16 specifically to make "does
    # Planner-reuse correlate with more first-attempt failures than fresh
    # Developer generation" an answerable-from-data question (an external
    # review raised this as a real hypothesis, evidenced by two of that same
    # day's live incidents both tracing back to reused Planner content) rather
    # than something argued from a handful of anecdotes - never read or
    # branched on anywhere in the retry loop itself.
    planner_reuse_used_attempt1: Optional[bool] = None
    # The model/endpoint override the most recent run_attempt() call actually
    # used (None means the primary model) - the caller needs these afterward
    # to gate lesson extraction on "this successful attempt used a non-primary
    # model" and to run that extraction on the SAME model that resolved the
    # issue, not whatever the primary model is.
    last_model_override: Optional[str] = None
    last_base_url_override: Optional[str] = None
    last_api_key_override: Optional[str] = None
    # The file(s) extract_implicated_files() found in the MOST RECENT failure -
    # re-evaluated after every failure, not fixed at the first one, so a
    # targeted attempt against a different file (a new error surfaced by fixing
    # the last one) is still eligible. None whenever the last failure named no
    # known file, or scoping is disabled (goes to the full-set path).
    last_implicated_files: Optional[List[str]] = None
    # The file(s) the completeness check (extract_expected_files vs. what got
    # written) found missing after the MOST RECENT attempt. Mutually exclusive
    # with last_implicated_files - an IncompleteGenerationError sets this and
    # clears last_implicated_files (nothing to implicate: the file was never
    # written), any other failure clears this and re-evaluates
    # last_implicated_files as before.
    last_missing_files: Optional[List[str]] = None
    # The full AttributionResult (kriya/workflow/attribution.py) behind the
    # MOST RECENT last_implicated_files - which tier produced it
    # ("locator"/"judge"/"triage"/"full_set") and how confident that tier
    # was. last_implicated_files/last_missing_files stay the source of truth
    # for retry-mode decisions (unchanged downstream contract); this is
    # purely for observability (persisted onto the Failure that triggered it,
    # see attribution_tier/attribution_confidence/attribution_reasoning in
    # kriya/workflow/failure.py) and for a future caller that wants the
    # ranking/reasoning, not just the winning file list.
    last_attribution: Optional[Any] = None
    # (failure_signature, files) from the MOST RECENT attempt's own FIX
    # ANALYSIS text, when it named a DIFFERENT known file than the one it
    # was attached to (extract_self_diagnosed_files(), kriya/workflow/
    # attribution.py) - paired with the failure signature that attempt was
    # RESPONDING to, so retry_strategy.py can only trust it on a CONFIRMED
    # repeat of that exact failure, never on a genuinely new/unrelated one.
    # None whenever the most recent attempt produced no analysis text, or
    # its analysis didn't diverge from what it was asked to fix.
    last_self_diagnosis: Optional[Any] = None
    # {filepath: source-line snippet} for the MOST RECENT failure's error
    # location(s) - empty whenever the last failure's error text named no
    # javac-style file:[line,col] locator, or before any failure has happened.
    last_error_source_context: Dict[str, str] = field(default_factory=dict)
    # Tracks the human-in-the-loop confirmation for judgment-triggered (not
    # goal-text-explicit) runtime verification, so it's asked at most once per
    # generation run rather than on every retry attempt.
    run_verification_confirmed: bool = False
    run_verification_declined: bool = False
    # Caches RunVerifierAgent.judge()'s result across retry attempts within
    # this run - the goal/design driving "should we run this, and how" don't
    # change between retries, so repeating the LLM call only bought wasted
    # latency, not a different answer.
    cached_run_verification_judgment: Optional[Dict[str, Any]] = None
    # Set True only right before the success-path `break` - retry_count alone
    # can no longer indicate success/failure now that a run can succeed via a
    # targeted attempt after the full-set budget was already exhausted.
    quality_gates_succeeded: bool = False
    # Set from classify_environment_failure() on the most recent failed
    # attempt - a non-None value short-circuits the retry loop, since no amount
    # of code regeneration can ever fix a JVM crashing during its own startup
    # or a missing build/run tool binary.
    environment_failure: Optional[str] = None
    # Toolchain preflight (_check_java_toolchain_mismatch) runs at most once per
    # generation run, the first time a PolymorphicValidator confirms the stack
    # is 'java' - toolchain_checked gates that, toolchain_warning persists into
    # the final result regardless of pass/fail.
    toolchain_checked: bool = False
    toolchain_warning: Optional[str] = None
    # See _resolve_java_home_override for when this gets set - threaded into
    # every PolymorphicValidator construction so a detected, goal-relevant JDK
    # mismatch actually gets corrected for real subprocess calls.
    java_home_override: Optional[str] = None
    # One jdtls process for this whole generation run (lazily started on first
    # real need, kept alive across retries, shut down at run end) - None until
    # first used, and stays None permanently (no repeated start attempts) if
    # jdtls isn't found or fails to start.
    jdtls_client: Optional[Any] = None
    # Set once, the first time jdtls is found on PATH but fails to start -
    # distinct from jdtls simply not being installed (expected, silent).
    jdtls_unavailable: bool = False
    lsp_warning: Optional[str] = None
    gate_outcomes: List[Dict[str, Any]] = field(default_factory=list)
    # Canonical append-only runtime facts. gate_outcomes stays as a backwards-
    # compatible trace projection while callers migrate to this event stream.
    run_events: List[RunEvent] = field(default_factory=list)
    failure_ledger: FailureLedger = field(default_factory=FailureLedger)
    evidence_records: List[EvidenceRecord] = field(default_factory=list)
    model_hops: List[Dict[str, Any]] = field(default_factory=list)
    budgets: RetryBudgets = field(default_factory=RetryBudgets)
    # Set when the Pre-Apply Human Approval Gate runs the Reviewer early (so its
    # verdict can inform the human's actual approve/reject decision, instead of
    # only appearing afterward when the decision - and the file copy - are
    # already final) - the later "5. Reviewer" stage reuses this instead of
    # running a second, redundant LLM call against identical content. None
    # whenever no human-approval escalation happened this run (the common
    # autonomous-mode path), or the run never reached that gate at all.
    pre_approval_review: Optional[str] = None

    def record_event(self, event: RunEvent) -> None:
        self.run_events.append(event)
        if event.failure_type:
            self.failure_ledger.record(event)

    def record_failure(self, failure: Any, *, operation: Optional[str] = None) -> RunEvent:
        try:
            authority = EventAuthority(failure.authority)
        except (ValueError, TypeError):
            authority = EventAuthority.AUTHORITATIVE
        event = RunEvent(
            kind="failure.recorded",
            attempt=failure.attempt or self.attempt_number,
            source=failure.source,
            authority=authority,
            message=failure.message,
            failure_type=failure.type,
            operation=operation,
            details={"likely_files": list(failure.likely_files)},
        )
        self.record_event(event)
        self.evidence_records.append(EvidenceRecord(
            kind="failure",
            source=failure.source,
            attempt=failure.attempt or self.attempt_number,
            payload={
                "type": failure.type,
                "message": failure.message,
                "raw_output": failure.raw_output,
                "likely_files": list(failure.likely_files),
                "failed_content": dict(failure.failed_content),
                "attempted_edits": list(failure.attempted_edits),
            },
        ))
        return event
