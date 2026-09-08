"""Explicit state for run_generation_workflow()'s Developer + Quality Gates
retry loop - replaces ~25 mutable local variables previously threaded through
the loop via closures. Extracted 2026-08-11 (Opportunity 2, Slice 1): the
loop's control flow and every read/write site are unchanged, only the
storage moved from bare names to attributes on this object - this is what
makes the next slices (an isolable attempt executor and retry-decision
function) possible to unit-test without invoking the whole method.
"""
from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from kriya.workflow.architectural_choice import CandidateArchitecturalChange
from kriya.workflow.run_events import EventAuthority, FailureLedger, RunEvent
from kriya.workflow.evidence import EvidenceRecord
from kriya.workflow.edit_safety import content_revision
from kriya.workflow.triage import EngineeringRoute
from kriya.workflow.process_profile import ProcessProfile
from kriya.workflow.repair_contract import RepairContract


class APIContractRecoveryPhase(str, Enum):
    DETECTED = "DETECTED"
    RESTORE_PUBLIC_CONTRACT = "RESTORE_PUBLIC_CONTRACT"
    REPAIR_BEHAVIOR = "REPAIR_BEHAVIOR"
    AWAIT_TERMINAL_SUCCESS = "AWAIT_TERMINAL_SUCCESS"
    COMPLETE = "COMPLETE"


class RecoveryPhaseAdvanced(Exception):
    """Internal non-failure control flow for a verified recovery transition."""

    def __init__(self, source: str, target: str) -> None:
        self.source = source
        self.target = target
        super().__init__(f"{source} -> {target}")


@dataclass
class APIContractRecovery:
    """Authoritative brownfield API recovery lifecycle.

    This object—not retry prompts or the latest failure—owns recovery scope
    and legal transitions. Diagnostics may be serialized and rehydrated, but
    callers cannot silently skip owner restoration or terminal verification.
    """

    violations: List[Dict[str, Any]]
    protected_evidence_files: Tuple[str, ...]
    file_roles: Dict[str, str]
    phase: APIContractRecoveryPhase = APIContractRecoveryPhase.DETECTED
    transition_history: List[str] = field(
        default_factory=lambda: [APIContractRecoveryPhase.DETECTED.value]
    )

    @classmethod
    def detected(
        cls, violations: List[Dict[str, Any]], protected_evidence_files,
        file_roles: Dict[str, str],
    ) -> "APIContractRecovery":
        return cls(
            violations=list(violations),
            protected_evidence_files=tuple(sorted(set(protected_evidence_files))),
            file_roles=dict(file_roles),
        )

    @classmethod
    def from_diagnostics(cls, value: Dict[str, Any]) -> "APIContractRecovery":
        recovery = cls.detected(
            list(value.get("violations", [])),
            value.get("protected_evidence_files", []),
            value.get("file_roles", {}),
        )
        phase = APIContractRecoveryPhase(
            value.get("phase", APIContractRecoveryPhase.DETECTED.value)
        )
        recovery.phase = phase
        recovery.transition_history = list(
            value.get("transition_history", [phase.value])
        )
        return recovery

    @property
    def owner_files(self) -> List[str]:
        return sorted({item["owner"] for item in self.violations})

    def _transition(
        self, expected: APIContractRecoveryPhase, target: APIContractRecoveryPhase,
    ) -> None:
        if self.phase is not expected:
            raise ValueError(
                f"illegal API contract recovery transition: {self.phase.value} -> "
                f"{target.value}; expected {expected.value}"
            )
        self.phase = target
        self.transition_history.append(target.value)

    def begin_restoration(self) -> None:
        self._transition(
            APIContractRecoveryPhase.DETECTED,
            APIContractRecoveryPhase.RESTORE_PUBLIC_CONTRACT,
        )

    def owner_contract_restored(self) -> None:
        self._transition(
            APIContractRecoveryPhase.RESTORE_PUBLIC_CONTRACT,
            APIContractRecoveryPhase.REPAIR_BEHAVIOR,
        )

    def candidate_gates_passed(self) -> None:
        if self.phase is APIContractRecoveryPhase.REPAIR_BEHAVIOR:
            self._transition(
                APIContractRecoveryPhase.REPAIR_BEHAVIOR,
                APIContractRecoveryPhase.AWAIT_TERMINAL_SUCCESS,
            )

    def terminal_succeeded(self) -> None:
        if self.phase is APIContractRecoveryPhase.REPAIR_BEHAVIOR:
            self.candidate_gates_passed()
        self._transition(
            APIContractRecoveryPhase.AWAIT_TERMINAL_SUCCESS,
            APIContractRecoveryPhase.COMPLETE,
        )

    def to_diagnostics(self) -> Dict[str, Any]:
        return {
            "mode": "API_CONTRACT_RECOVERY",
            "phase": self.phase.value,
            "violations": self.violations,
            "protected_evidence_files": list(self.protected_evidence_files),
            "file_roles": self.file_roles,
            "transition_history": list(self.transition_history),
        }

    # Read-only mapping compatibility for deterministic inspection helpers.
    def get(self, key: str, default=None):
        return self.to_diagnostics().get(key, default)

    def __getitem__(self, key: str):
        return self.to_diagnostics()[key]


@dataclass
class RetryBudgets:
    """The counters that govern which attempt mode comes next - grouped
    separately because they're read/written together as a unit by the
    retry-decision logic, unlike the rest of GenerationState's fields."""

    # Full-set retry attempt counter, bounded by max_retries (max(4, 1+len(chain))).
    retry_count: int = 0
    # Failure-scoped budget for targeted (single/few-file) retries. It resets
    # when authoritative validator evidence identifies a genuinely different
    # failure family, while attempt_number supplies the run-wide ceiling. It is
    # deliberately
    # NOT folded into retry_count, which governs the full-file-set path and its
    # model-escalation chain. Targeted attempts always use the primary model
    # (never escalate - a measured 19-43s Ollama model-swap cost made "swap on
    # every targeted attempt" a bad trade). Exhausting this scoped budget falls
    # through to the full-set path's own budget/escalation, unchanged.
    targeted_retry_count: int = 0
    # API contract restoration is an authoritative state machine, not an
    # ordinary targeted compiler/test repair.  Keep its budget separate so
    # logs and exhaustion never produce nonsensical values such as 4/3.
    api_contract_recovery_count: int = 0
    # A single, one-shot opportunity per authoritative failure family to try a
    # TARGETED fix on the fallback model
    # before escalating all the way to a full-set regeneration (found live,
    # 2026-08-10, ignite_qpid_protocol): a full-set escalation already pays a
    # model-swap cost, so trying one targeted fix on that same fallback model
    # first spends a swap cost that was coming anyway on a much cheaper shot.
    # Deliberately its own flag, not folded into targeted_retry_count (primary
    # model only, never escalate) or retry_count (the full-set path's own
    # counter). A new failure family clears it; the global attempt ceiling still
    # bounds the run.
    fallback_targeted_attempted: bool = False
    # Set when a primary-model targeted response returns advisory NO CHANGE
    # against a target backed by a deterministic file:line locator.  The
    # authoritative locator remains the scope, but retrying the same model's
    # rejected opinion is low-value; consume the existing one-shot fallback
    # targeted attempt next.  This is evidence-based and stack-neutral (no
    # filenames/failure-type allowlist), and is cleared as that attempt starts.
    fallback_targeted_requested: bool = False
    # Failure signature for which a dependency-closure-scoped full-set repair
    # has already been attempted. A different validator failure receives its
    # own one narrow closure attempt even when earlier failures consumed global
    # full-set retries; the same failure broadens after that one shot.
    scoped_full_set_failure_signature: Optional[Tuple[str, Any]] = None
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
    # Consecutive anchor mismatches per authorized file. After the first
    # mismatch, the next attempt changes only the edit protocol—never the file
    # scope—to a full-file repair, avoiding repeated skeleton/anchor failures.
    anchor_failure_counts: Dict[str, int] = field(default_factory=dict)


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
    generation_started_monotonic: float = field(default_factory=time.monotonic)
    # Completed/failed Developer calls used to refine the conservative configured
    # per-file estimate without persisting prompt or proprietary source content.
    # R1 Deliverable 5 (2026-09-08): each dict also carries prompt_tokens/
    # completion_tokens/tokens_estimated when the Developer's own LLM call
    # exposed them (see _run_developer_generation's own capture site,
    # kriya/workflow/attempt.py) - additive keys, nothing existing removed or
    # renamed, so any prior reader of this list (there were none outside
    # generation_metrics() itself) is unaffected.
    generation_timings: List[Dict[str, Any]] = field(default_factory=list)
    # R1 Deliverable 5 - observational only, never read by the retry loop
    # itself (same posture as generation_timings above). One entry per
    # deterministic validator/build/test invocation this run
    # (PolymorphicValidator.run_compile_check/run_tests/run_app_sequence),
    # captured by the same try/finally timing pattern _run_developer_
    # generation already uses for Developer calls - kind ("compile"/"test"/
    # "run_verification"/"managed_service_verification"), duration_seconds,
    # success - never the raw compiler/test output blob (that already lives
    # in gate_outcomes/logs); this list exists purely for timing/count
    # aggregation. Covers only the PRIMARY compile gate and the two main
    # runtime-verification call sites in attempt.py - see docs/assurance/
    # KRIYA_PERFORMANCE_TELEMETRY.md for the exact coverage boundary
    # (several secondary/fallback validator call sites are not separately
    # timed, a documented limitation rather than a silent gap).
    validator_timings: List[Dict[str, Any]] = field(default_factory=list)
    # R1 Deliverable 5 - Planner/Architect/Reviewer LLM-call telemetry.
    # These three stages run their own single-shot agent calls (not the
    # Developer retry loop's own per-attempt generation_timings above).
    # GenerationState is constructed BEFORE the Planner/Architect calls in
    # run_generation_workflow (state = GenerationState(...) precedes both),
    # so these fields are simply incremented directly at each call site -
    # no local-variable-then-backfill indirection needed. Never read or
    # branched on by the retry loop itself - observational only, same
    # posture as every other field in this comment block.
    planner_llm_seconds: float = 0.0
    planner_calls: int = 0
    architect_llm_seconds: float = 0.0
    architect_calls: int = 0
    reviewer_llm_seconds: float = 0.0
    reviewer_calls: int = 0
    # R1 Deliverable 5 - retry-amplification observability (the P7 attempt-1
    # efficiency-finding class of question). Incremented at the exact call
    # site in handle_attempt_failure() (kriya/workflow/retry_strategy.py)
    # that consults evaluate_candidate_independent_failure() -
    # candidate_independent_diagnostic_invocations counts every consultation
    # (cache hit or not); baseline_replay_count counts only the subset that
    # actually reached a real baseline replay subprocess call (proven, not
    # assumed: every code path in evaluate_candidate_independent_failure()
    # that calls store.record() necessarily called replay_deterministic_
    # verification_against_baseline() first, and every path that does NOT
    # call replay returns before ever calling store.record() - so comparing
    # the store's own record count before/after each call is an exact count,
    # not a heuristic).
    candidate_independent_diagnostic_invocations: int = 0
    baseline_replay_count: int = 0
    error_context: str = ""
    # MA1.3/MA1.4 of the control-plane implementation plan (kriya/workflow/
    # triage.py) - the shadow-mode classification computed once, before this
    # object is even constructed (see run_generation_workflow's own MA1.3
    # comment), carried here purely so generation_metrics() below has one
    # place to serialize it into telemetry. Nothing in the retry loop reads
    # or writes this - it is NOT becoming the general control-plane state
    # object (see triage.py's own module docstring); it is one optional,
    # write-once field, same shape as last_failure being carried for
    # downstream reporting rather than loop control.
    engineering_route: Optional[EngineeringRoute] = None
    # MA2.6b - the ProcessProfile resolved alongside engineering_route (see
    # WorkflowControlContext, kriya/workflow/control_context.py), carried
    # here purely for generation_metrics() to serialize - same
    # telemetry-only, write-once posture as engineering_route above. Kept
    # as its own field rather than storing the whole WorkflowControlContext
    # object, so this stays a plain, JSON-serializable value like every
    # other GenerationState field, not a second copy of engineering_route
    # nested inside a wrapper.
    process_profile: Optional[ProcessProfile] = None
    # The active canonical typed failure behind repair prompts. Advisory and
    # auxiliary events remain in run_events/gate_outcomes but cannot replace an
    # existing authoritative failure here. Retry prompts project this object
    # into bounded, revision-labelled evidence instead of relying on an
    # unbounded string concatenation of errors and complete files.
    last_failure: Optional[Any] = None
    files_written: List[Dict[str, str]] = field(default_factory=list)
    all_files_written: Set[str] = field(default_factory=set)
    all_original_contents: Dict[str, str] = field(default_factory=dict)
    # P9-R1 (P9/PRV-08, 2026-09-08): the most recent content `files` (this
    # attempt's own Developer/deterministic-restore response) carried for
    # each path, updated on EVERY attempt regardless of that attempt's own
    # gate outcome - a real, legitimate edit to one file must survive even
    # when the SAME batch is rejected for an unrelated reason in a
    # different file. Consumed only while state.api_contract_recovery is
    # active (kriya/workflow/attempt.py's own run_attempt(), just before the
    # brownfield ownership check) to fold an untouched-this-round expected
    # file's own last real content back into a recovery attempt's narrowed
    # `files` list, so the generic completeness check doesn't mistake a
    # deliberately narrowed recovery round for the Developer silently
    # under-delivering. Never read to fabricate content for a file that was
    # never actually generated in any attempt (get() returns None; the
    # caller does not fall back to baseline).
    last_candidate_contents: Dict[str, str] = field(default_factory=dict)
    # Revisions that passed the real compile gate. A later candidate invalidates
    # only changed files and their manifest dependents; unrelated validated files
    # remain stable across targeted/dependency-scoped retries.
    validated_file_revisions: Dict[str, str] = field(default_factory=dict)
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
    last_extra_body_override: Optional[Dict[str, Any]] = None
    # The file(s) extract_implicated_files() found in the MOST RECENT failure -
    # re-evaluated after every failure, not fixed at the first one, so a
    # targeted attempt against a different file (a new error surfaced by fixing
    # the last one) is still eligible. None whenever the last failure named no
    # known file, or scoping is disabled (goes to the full-set path).
    last_implicated_files: Optional[List[str]] = None
    # Correctness Continuity Part B (PRV-06, 2026-08-29): filepaths dropped
    # from a generation/edit attempt because they fell outside
    # ctx.allowed_write_relpaths under WriteScopeMode.ALLOWLIST - recorded
    # by retry_strategy.py (pre-prompt narrowing of last_implicated_files)
    # and attempt.py (pre-apply_anchored_edits rejection in the per-file
    # write loop). Pure observability/diagnostics, accumulates across the
    # whole run - never read or branched on by the retry loop itself, same
    # posture as planner_reuse_used_attempt1 above.
    rejected_generation_targets: List[str] = field(default_factory=list)
    # PRV-17 (2026-09-03): counts attempts aborted by a PolicyDeniedError
    # (FILE_OUTSIDE_VALIDATED_SUBTASK_SCOPE) that
    # _failure_from_validated_scope_denial() (kriya/workflow/retry_strategy.py)
    # could NOT convert into a plan_scope_conflict, because the target names
    # no real existing production owner to hand recovery off to (a
    # hallucinated new path, or a test file) - unlike
    # rejected_generation_targets above, this one IS read and branched on:
    # a live incident burned 4 full generation cycles because each such
    # denial was treated as an ordinary retryable failure and the Developer
    # was simply asked to try again, proposing a DIFFERENT illegal target
    # each time. handle_attempt_failure() gives the first occurrence a
    # normal retry chance, then stops the subtask deterministically
    # (state.environment_failure) the second time - there is no legitimate
    # owner for retrying to discover, so continuing cannot produce a
    # different, legal outcome.
    unrecoverable_scope_denial_count: int = 0
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
    # (failure_signature, files, attempt_number) from the MOST RECENT
    # attempt's own FIX ANALYSIS text, when it named a DIFFERENT known file
    # than the one it was attached to (extract_self_diagnosed_files(),
    # kriya/workflow/attribution.py) - paired with the failure signature
    # that attempt was RESPONDING to AND the attempt number that produced
    # it, so retry_strategy.py only trusts it as the explanation for THAT
    # SAME attempt's own outcome (both signature and attempt number must
    # match - see retry_strategy.py's consumption site and attempt.py's
    # capture site for the PRV-05 run 7, 2026-08-28 incident this attempt-
    # number gate closes: signature equality alone let a stale, several-
    # attempts-old diagnosis get replayed against unrelated later failures
    # that merely collapsed to the same signature). None whenever the most
    # recent attempt produced no analysis text, or its analysis didn't
    # diverge from what it was asked to fix.
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
    cached_run_verification_basis_hash: Optional[str] = None
    last_failed_workspace_hash: Optional[str] = None
    last_progress_failure_signature: Optional[Tuple[str, Any]] = None
    last_progress_stage: Optional[str] = None
    last_progress_files: Tuple[str, ...] = ()
    last_progress_action: Optional[str] = None
    last_progress_classification: Optional[str] = None
    consecutive_no_progress_attempts: int = 0
    no_progress_terminated: bool = False
    # Current attempt's three distinct verification/application boundaries.
    candidate_gates_succeeded: bool = False
    terminal_regression_succeeded: bool = False
    overall_attempt_succeeded: bool = False
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
    # Set when grounded failure attribution identifies a required repair
    # file outside an authoritative caller-provided write allowlist. This
    # is a plan/scope conflict, not another code-generation retry target.
    plan_scope_conflict: Optional[Dict[str, Any]] = None
    # Sticky authoritative recovery contract established by the deterministic
    # brownfield API gate. Unlike last_failure/error_context, this survives
    # later compiler/test failures until every removed signature is restored.
    api_contract_recovery: Optional[APIContractRecovery] = None
    # MA8 (spec §35-38): every occurrence, this run, of an EXISTING
    # brownfield/duplicate-ownership detector (find_brownfield_test_
    # redirections/find_brownfield_public_api_changes) flagging the same
    # candidate-introduced file - see kriya/workflow/architectural_choice.py.
    # A single occurrence is ordinary quality-gate failure/retry; recurrence
    # is what confirms the underlying architectural choice itself is wrong.
    architectural_changes: List[CandidateArchitecturalChange] = field(default_factory=list)
    # MA9 (2026-08-29, PRV-06 Bucket A forensic finding): the run's current
    # coordinated-repair transaction, if any - see kriya/workflow/
    # repair_contract.py's own module docstring for why this exists and why
    # it's deliberately in-memory/per-run-sticky only, not checkpointed.
    # None (the default) means no coordinated repair is active - every
    # existing single-file targeted-retry call site is completely unchanged
    # whenever this stays None, which is every run except one with a live,
    # unambiguously-evidenced PROCESS_BOUNDARY_COMPATIBILITY violation.
    repair_contract: Optional[RepairContract] = None

    def record_event(self, event: RunEvent) -> None:
        self.run_events.append(event)
        if event.failure_type:
            self.failure_ledger.record(event)

    def final_workflow_quality_passed(self) -> bool:
        """Return terminal workflow truth, never historical-attempt truth."""
        return bool(
            self.candidate_gates_succeeded
            and self.terminal_regression_succeeded
            and self.overall_attempt_succeeded
            and self.quality_gates_succeeded
            and self.api_contract_recovery is None
        )

    def generation_metrics(self, total_wall_seconds: Optional[float] = None) -> Dict[str, Any]:
        """Content-free operational telemetry safe to persist in local traces.

        total_wall_seconds (R1 Deliverable 5, 2026-09-08): the caller's own
        time.monotonic() - self.generation_started_monotonic measurement,
        threaded in rather than computed here - this method must stay a pure
        read of already-recorded fields (called more than once per run in
        some paths, e.g. mid-run checkpoint logging), never a fresh "now"
        sample of its own that would disagree between two calls.

        See docs/assurance/KRIYA_PERFORMANCE_TELEMETRY.md for the full field
        reference, units, and inclusive/exclusive timing semantics - the
        summary here: llm_wall_seconds/validator_wall_seconds/
        total_wall_seconds are INCLUSIVE of each other (a validator call can
        happen while wall-clock time that also counts toward total_wall_
        seconds elapses) - they do not sum exactly to total_wall_seconds,
        and must never be presented as though they do.
        """
        # timing.get(key, 0) would NOT catch a present key whose value is
        # None (dict.get's default only ever applies to a MISSING key) -
        # prompt_tokens/completion_tokens are deliberately set to None (not
        # omitted) when unavailable (see _run_developer_generation's own
        # capture site), so `or 0` is required here, not a defensive
        # nicety. developer_tokens_available reports how many attempts
        # actually had real data, so a low/zero sum is never misread as
        # "no tokens used" when it may really mean "not measured".
        developer_prompt_tokens = sum(
            int(timing.get("prompt_tokens") or 0) for timing in self.generation_timings
        )
        developer_completion_tokens = sum(
            int(timing.get("completion_tokens") or 0) for timing in self.generation_timings
        )
        developer_tokens_available = sum(
            1 for timing in self.generation_timings if timing.get("prompt_tokens") is not None
        )
        developer_llm_seconds = sum(
            float(timing.get("duration_seconds") or 0) for timing in self.generation_timings
        )
        llm_calls = (
            len(self.generation_timings) + self.planner_calls
            + self.architect_calls + self.reviewer_calls
        )
        llm_wall_seconds = (
            developer_llm_seconds + self.planner_llm_seconds
            + self.architect_llm_seconds + self.reviewer_llm_seconds
        )
        metrics: Dict[str, Any] = {
            "calls": len(self.generation_timings),
            "successful_calls": sum(
                1 for timing in self.generation_timings if timing.get("succeeded")
            ),
            "duration_seconds": developer_llm_seconds,
            "files_requested": sum(
                int(timing.get("file_count", 0)) for timing in self.generation_timings
            ),
            "operation_fallbacks": sum(
                1 for event in self.run_events if event.kind == "operation.fallback"
            ),
            "validation_invalidations": sum(
                1 for event in self.run_events if event.kind == "validation.invalidated"
            ),
            "validated_files": len(self.validated_file_revisions),
            # --- R1 Deliverable 5 additions below - all additive, nothing
            # above this line changed in name, meaning, or value. ---
            "total_wall_seconds": total_wall_seconds,
            "terminal_status": (
                "success" if self.final_workflow_quality_passed()
                else ("environment_failure" if self.environment_failure else "failed")
            ),
            "llm": {
                "calls": llm_calls,
                "wall_seconds": llm_wall_seconds,
                "developer_calls": len(self.generation_timings),
                "developer_wall_seconds": developer_llm_seconds,
                "developer_prompt_tokens": developer_prompt_tokens,
                "developer_completion_tokens": developer_completion_tokens,
                "developer_tokens_available_for": developer_tokens_available,
                "planner_calls": self.planner_calls,
                "planner_wall_seconds": self.planner_llm_seconds,
                "architect_calls": self.architect_calls,
                "architect_wall_seconds": self.architect_llm_seconds,
                "reviewer_calls": self.reviewer_calls,
                "reviewer_wall_seconds": self.reviewer_llm_seconds,
            },
            "validators": {
                "invocations": len(self.validator_timings),
                "wall_seconds": sum(
                    float(t.get("duration_seconds", 0)) for t in self.validator_timings
                ),
                "by_kind": {
                    kind: sum(1 for t in self.validator_timings if t.get("kind") == kind)
                    for kind in sorted({t.get("kind") for t in self.validator_timings if t.get("kind")})
                },
            },
            "retry": {
                "full_set_attempts": self.budgets.retry_count,
                "targeted_attempts": self.budgets.targeted_retry_count,
                "unrecoverable_scope_denials": self.unrecoverable_scope_denial_count,
                "candidate_independent_diagnostic_invocations": (
                    self.candidate_independent_diagnostic_invocations
                ),
                "baseline_replay_count": self.baseline_replay_count,
            },
        }
        if self.engineering_route is not None:
            # MA1.4 - fold the shadow classification into the SAME dict
            # (generation_metrics) that already flows to every
            # trace_logger.log_run() call site, rather than adding a new
            # column/parameter to each one individually - see this field's
            # own comment above and the control-plane plan's own "extend
            # generation_metrics first, not ten SQL columns" guidance.
            metrics["engineering_route"] = self.engineering_route.to_dict()
        if self.process_profile is not None:
            # MA2.6b - observational only. Recording this tells a human/future
            # analysis what tier WOULD have applied and, for HEAVY, that its
            # extended checks aren't real yet - it does NOT mean any of
            # PolymorphicValidator/Quality Gates actually ran differently for
            # this attempt. See ProcessProfile.to_dict()'s own docstring.
            metrics["process_profile"] = self.process_profile.to_dict()
        return metrics

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
                # Full failed source already exists once in the compatibility
                # gate outcome. Store revisions here to avoid doubling trace DB
                # size while preserving a canonical identity link.
                "failed_content_revisions": {
                    path: content_revision(content)
                    for path, content in failure.failed_content.items()
                },
                "attempted_edits": list(failure.attempted_edits),
            },
        ))
        return event
