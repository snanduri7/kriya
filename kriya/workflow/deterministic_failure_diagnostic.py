"""Candidate-independent deterministic failure detection (PRV-17, 2026-09-08,
Production Validation P7 efficiency finding) - a small, DELIBERATELY separate
mechanism from MA8 (ObligationLedger, kriya/workflow/obligations.py), MA9's
cross-owner RECOVERY_NO_PROGRESS (kriya/workflow/workflow_controller.py, the
CROSS_OWNER_ARTIFACT_REQUIREMENT owner-recovery path), and
record_workspace_progress (kriya/workflow/retry_strategy.py, unmodified by
this module).

Live incident this closes (P7 attempt 1, ~78.6 minutes; the same run,
otherwise unchanged, later completed in ~15.4 minutes once the underlying
Kriya compile-check bug was fixed separately): 14 Developer attempts across
two independently-exhausted 7-attempt retry sequences, all rejected by the
byte-identical Kriya-synthesized message "Maven reported compilation
success, but zero .class files were actually produced..." - a defect in
Kriya's OWN compile validator (a genuine multi-module Maven reactor shape it
didn't yet understand), never in the generated Java, which was already
correct on attempt 1.

Root cause traced precisely (analysis-only investigation, same date):
record_workspace_progress's own "no progress" definition is byte-identity of
the regenerated workspace content - virtually never true for LLM-driven
full-set/targeted regeneration, so its PROGRESS branch fires unconditionally
regardless of whether the deterministic verifier's rejection reason
repeated. Removing that gate alone would not have helped either: the retry-
family's own mode (full-set/targeted/fallback_targeted) also changes on
most attempts, independent of the underlying failure signature, so even a
byte-identity-agnostic version of that same counter would rarely have
accumulated 3 consecutive matches in the actual traced timeline.

This module answers a NARROWER, orthogonal question instead: is the
DETERMINISTIC GATE ITSELF (not the retry loop's own progress bookkeeping)
going to keep failing identically regardless of what the candidate contains
- provable, cheaply, by replaying the SAME check against an isolated copy of
the pre-candidate baseline. If the baseline reproduces the identical
normalized failure, no further Developer regeneration can ever resolve it
(the defect is in the validator/build-configuration/environment, not the
code), and the run should stop with an explicit, correctly-labeled blocker
rather than burn further retries. If the baseline does NOT reproduce it, the
candidate remains a plausible cause and existing retry behavior is
completely unaffected - this module never terminates anything on that path.

Deliberately bounded, not a general "same error twice -> stop" rule (the
user's own explicitly named danger case): termination requires ALL of the
same normalized failure recurring after a MATERIALLY DIFFERENT candidate
(the workspace actually changed - ruling out the ordinary byte-identical
case record_workspace_progress already exists for), an ABSENT/insufficient
credible implicated file (a real per-file compile/test error that names a
specific offending file remains an ordinary Developer-repair target, handled
entirely by existing machinery - this module never even evaluates that
case), AND the isolated baseline replay itself independently reproducing
the identical failure. "No implicated files" is supporting evidence for
TRIGGERING the check only - by construction, it can never independently
conclude NON_CANDIDATE_CORRECTABLE; only a baseline replay that actually
reproduces the failure can do that."""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, Optional, Tuple

logger = logging.getLogger(__name__)


class DeterministicFailureCorrectability(str, Enum):
    UNKNOWN = "unknown"
    CANDIDATE_CORRECTABLE = "candidate_correctable"
    NON_CANDIDATE_CORRECTABLE = "non_candidate_correctable"


# Bounded to exactly the evidenced case (PolymorphicValidator.run_compile_
# check()) - not "every deterministic gate" speculatively. A gate whose
# replay could have side effects beyond a subprocess exit code/stdout
# (run_verification/run_verification_hung: starts a real managed service or
# long-running process) or that is itself LLM judgment (goal_spec_
# compliance) is deliberately excluded; extend only against a new live
# incident, matching this codebase's own established discipline for typed-
# failure-family additions (see kriya/workflow/obligations.py's own
# ObligationKind docstring for the identical discipline applied there).
_DETERMINISTIC_REPLAYABLE_FAIL_TYPES = frozenset({"compile"})

# Mirrors compute_effective_workspace_hash's own exclusion set (kriya/
# workflow/retry_strategy.py) so the baseline copy is the same kind of
# "real project content" comparison, minus control-plane/build-output
# directories that are either irrelevant or would make the copy itself
# expensive/misleading (a stale target/ from a PRIOR compile could mask
# whether ADD candidate change nothing).
_BASELINE_COPY_EXCLUDED_DIRS = frozenset({
    ".git", ".kriya", ".pytest_cache", "__pycache__", "node_modules",
    "target", "build", "dist",
})


# A store key is (subtask_id, failure_signature) - fail_type is already
# baked into failure_signature's own leading element (build_failure_
# signature() returns (failure_type, ...)), so the only piece worth adding
# explicitly is the subtask/workflow context: two DIFFERENT subtasks
# producing the byte-identical Kriya-synthesized message text must never
# silently inherit each other's baseline-replay conclusion just because the
# text collided - a scoping gap the user caught before this closed. None
# for subtask_id (a caller with no current_subtask_id, e.g. a plain Legacy
# run) still scopes correctly - it is simply one shared bucket for that
# caller, exactly like today's un-scoped behavior for every non-MA6-
# structured run.
DiagnosticKey = Tuple[Optional[str], Tuple[str, Any]]


@dataclass(frozen=True)
class DeterministicFailureDiagnosticRecord:
    """One conclusion. failure_signature is the SAME normalized signature
    build_failure_signature() already produces (kriya/workflow/
    failure_grounding.py) - never re-derived from raw error text, for the
    same reason obligations.py's own ObligationRecord.id docstring gives:
    stability across attempts is the whole point. subtask_id is carried
    alongside it (not folded into a single opaque key) so a caller can
    inspect which subtask this conclusion was actually proven for."""

    failure_signature: Tuple[str, Any]
    fail_type: str
    correctability: DeterministicFailureCorrectability
    baseline_output: str
    subtask_id: Optional[str] = None
    evidence: Dict[str, Any] = field(default_factory=dict)


class DeterministicFailureDiagnosticStore:
    """Per-WORKFLOW-RUN (not per-GenerationState, not per-subtask-attempt)
    store - created once by WorkflowController._run_structured_enforce
    alongside its own ObligationLedger, threaded unchanged through
    run_generation_workflow()/AttemptContext exactly the way obligation_
    ledger already is, so a plan-scope-recovery re-invocation (a BRAND NEW
    GenerationState/AttemptContext for the same subtask) never loses a
    conclusion already proven here - deliberately a separate object from
    ObligationLedger, not a new ObligationKind, so this mechanism carries
    zero coupling to MA8's regression-detection/authority-precedence
    semantics, which do not apply to this question at all.

    Keyed by (subtask_id, failure_signature) - see DiagnosticKey's own
    comment for why cross-subtask cache sharing would be a real
    contamination risk, not merely a naming nicety."""

    def __init__(self) -> None:
        self._records: Dict[DiagnosticKey, DeterministicFailureDiagnosticRecord] = {}

    def get(
        self, subtask_id: Optional[str], failure_signature: Tuple[str, Any],
    ) -> Optional[DeterministicFailureDiagnosticRecord]:
        return self._records.get((subtask_id, failure_signature))

    def record(self, rec: DeterministicFailureDiagnosticRecord) -> None:
        self._records[(rec.subtask_id, rec.failure_signature)] = rec


def _copy_baseline_workspace(source_workspace_path: str, dest_dir: str) -> None:
    """Copies real project content only - excludes control-plane/build-
    output directories, matching compute_effective_workspace_hash's own
    exclusion set. The destination is a fresh tempfile.mkdtemp() directory
    the caller owns and removes; this function never touches
    source_workspace_path itself (read-only os.walk + shutil.copy2)."""
    for root, dirs, files in os.walk(source_workspace_path):
        dirs[:] = sorted(name for name in dirs if name not in _BASELINE_COPY_EXCLUDED_DIRS)
        relative_root = os.path.relpath(root, source_workspace_path)
        dest_root = dest_dir if relative_root == "." else os.path.join(dest_dir, relative_root)
        os.makedirs(dest_root, exist_ok=True)
        for name in files:
            source_path = os.path.join(root, name)
            if os.path.islink(source_path) or not os.path.isfile(source_path):
                continue
            try:
                shutil.copy2(source_path, os.path.join(dest_root, name))
            except OSError:
                # A concurrently-changing file must not turn this diagnostic
                # replay itself into a new workflow failure - the same
                # tolerance compute_effective_workspace_hash already applies.
                continue


def replay_deterministic_verification_against_baseline(
    *, fail_type: str, authoritative_workspace_path: str, known_files: Iterable[str], autonomy_cfg: Any,
) -> Dict[str, Any]:
    """Replays the SAME deterministic check that produced `fail_type` against
    an ISOLATED COPY of authoritative_workspace_path - never the workspace
    itself, and never the candidate's own worktree. Returns the same
    {"success": bool, "output": str} shape PolymorphicValidator's own check
    methods return. The temp copy is always removed before returning,
    success or failure - nothing here can mutate or leave behind state in
    the authoritative workspace."""
    if fail_type not in _DETERMINISTIC_REPLAYABLE_FAIL_TYPES:
        raise ValueError(f"{fail_type!r} is not a replayable deterministic fail_type")
    from kriya.tools.validate import PolymorphicValidator

    temp_dir = tempfile.mkdtemp(prefix="kriya-baseline-replay-")
    try:
        _copy_baseline_workspace(authoritative_workspace_path, temp_dir)
        validator = PolymorphicValidator(
            temp_dir, original_workspace_path=temp_dir, autonomy_cfg=autonomy_cfg,
        )
        if fail_type == "compile":
            return validator.run_compile_check(sorted(set(known_files)))
        raise AssertionError(f"unreachable: {fail_type!r} passed the replayable-fail-type guard")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def evaluate_candidate_independent_failure(
    *,
    store: DeterministicFailureDiagnosticStore,
    fail_type: str,
    current_failure_signature: Tuple[str, Any],
    current_error_text: str,
    previous_failure_signature: Optional[Tuple[str, Any]],
    workspace_changed: bool,
    has_implicated_files: bool,
    authoritative_workspace_path: str,
    known_files: Iterable[str],
    autonomy_cfg: Any,
    subtask_id: Optional[str] = None,
) -> Optional[DeterministicFailureDiagnosticRecord]:
    """Orchestrates the full trigger-then-replay decision. Returns None
    whenever the trigger conditions are not met (nothing evaluated, no
    replay run, caller's existing retry behavior is completely untouched) -
    the caller (retry_strategy.py's handle_attempt_failure) is responsible
    for turning a returned NON_CANDIDATE_CORRECTABLE record into a
    terminal stop (via the existing state.environment_failure/
    STOP_ENVIRONMENT mechanism, never a new one - see that call site's own
    comment). A CANDIDATE_CORRECTABLE record is also returned so the caller
    can log it, but must never be treated as a reason to stop.

    current_error_text is the SAME raw text current_failure_signature was
    built from (retry_strategy.py's own raw_error_context, i.e. str(the
    raised exception)) - needed because the ORIGINAL failure's message is
    typically a wrapped form of the validator's raw output (e.g. attempt.py's
    own compile raise site: f"COMPILATION FAILURE:\\n{compile_res['output']}"),
    while a fresh baseline replay call only ever returns the validator's own
    raw, unwrapped output. Comparing build_failure_signature() of the two
    directly would (and, found live in this session's own direct
    verification before this fix, DID) almost always disagree even for the
    textually-identical underlying failure, since the wrapping prefix isn't
    itself part of the normalized digest either side computes independently.
    Substring containment (does current_error_text contain the baseline's
    own raw output) sidesteps needing to know or reconstruct any particular
    raise site's own wrapping convention.

    Trigger requires ALL of:
      1. fail_type is a bounded, safely-replayable deterministic gate.
      2. The SAME normalized failure_signature recurred (previous ==
         current) - a first occurrence never triggers a replay.
      3. workspace_changed is True - the candidate materially differs from
         the immediately prior attempt. A byte-identical repeat is exactly
         what record_workspace_progress's own no-progress counter already
         exists for; this mechanism is deliberately scoped to the gap that
         leaves open (different content, identical deterministic outcome).
      4. has_implicated_files is False - the failure names no credible
         repair target for the Developer. A real per-file error remains an
         ordinary repair target for existing machinery; this is supporting
         evidence for TRIGGERING only, per this module's own docstring - it
         never independently produces a NON_CANDIDATE_CORRECTABLE verdict.

    A cached record for this exact (subtask_id, failure_signature) pair
    (from an earlier attempt, possibly in a since-reset GenerationState/
    plan-scope-recovery cycle) is returned immediately without a second
    replay - a baseline replay is a real subprocess call and this mechanism
    is deliberately bounded to at most one per distinct pair per run.
    subtask_id scopes the cache so the byte-identical message occurring for
    a DIFFERENT subtask never inherits this subtask's own conclusion - two
    subtasks can share a failure_signature by coincidence without sharing a
    validation context."""
    cached = store.get(subtask_id, current_failure_signature)
    if cached is not None:
        return cached
    if fail_type not in _DETERMINISTIC_REPLAYABLE_FAIL_TYPES:
        return None
    same_failure_recurred = (
        previous_failure_signature is not None
        and current_failure_signature == previous_failure_signature
    )
    if not (same_failure_recurred and workspace_changed and not has_implicated_files):
        return None
    logger.info(
        "Candidate-independent deterministic failure check triggered for fail_type=%s "
        "subtask_id=%s (same normalized failure recurred after a materially different "
        "candidate, no credible implicated file) - replaying against an isolated "
        "baseline copy.",
        fail_type, subtask_id,
    )
    baseline_result = replay_deterministic_verification_against_baseline(
        fail_type=fail_type,
        authoritative_workspace_path=authoritative_workspace_path,
        known_files=known_files,
        autonomy_cfg=autonomy_cfg,
    )
    if baseline_result.get("success"):
        record = DeterministicFailureDiagnosticRecord(
            failure_signature=current_failure_signature,
            fail_type=fail_type,
            correctability=DeterministicFailureCorrectability.CANDIDATE_CORRECTABLE,
            baseline_output=baseline_result.get("output", ""),
            subtask_id=subtask_id,
            evidence={"baseline_reproduced_failure": False},
        )
    else:
        baseline_output = baseline_result.get("output", "")
        baseline_reproduced = bool(baseline_output) and baseline_output in current_error_text
        record = DeterministicFailureDiagnosticRecord(
            failure_signature=current_failure_signature,
            fail_type=fail_type,
            correctability=(
                DeterministicFailureCorrectability.NON_CANDIDATE_CORRECTABLE if baseline_reproduced
                else DeterministicFailureCorrectability.CANDIDATE_CORRECTABLE
            ),
            baseline_output=baseline_output,
            subtask_id=subtask_id,
            evidence={"baseline_reproduced_failure": baseline_reproduced},
        )
    store.record(record)
    return record
