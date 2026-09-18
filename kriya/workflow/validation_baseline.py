"""VAL-001 brownfield validation baselining (2026-09-18).

Investigation (docs/assurance/VAL_001_GRAPHIFY_G1.md section 16) found Kriya
had no way to distinguish a genuine candidate-caused test regression from a
pre-existing failure already present in a real brownfield repository before
Kriya ever touched it - the full-regression gate (kriya/workflow/workflow.py)
treated ANY failure as disqualifying, implicitly assuming the repository was
fully green before generation started. For a real repo Kriya didn't author
(exactly VAL-001's whole premise), that assumption is unverified and, in
general, false.

This module is the PRE/POST comparison primitive: capture a `ValidationBaseline`
from the pristine, authoritative workspace BEFORE the first Developer call,
then classify an equivalent POST-candidate validation result against it. It
is a pure, deterministic comparison library - it runs no subprocess, calls no
model, and makes no authority/gating decision of its own; kriya/workflow/
workflow.py (the one caller) decides what to DO with a classification.

Architecture, reused rather than duplicated:
  - `kriya/workflow/checkpoint.py::compute_workspace_content_hash()` (STATE-001)
    is reused AS-IS for `workspace_revision` - already the correct "pristine
    working-tree content, immune to mtime, correct for renames" identity, and
    already the established checkpoint-compatibility primitive.
  - `kriya/tools/validate.py::PolymorphicValidator.run_compile_check()`/
    `run_tests()` are reused AS-IS for execution - this module only wraps
    their EXISTING `{"success": bool, "output": str}` return shape into a
    structured, comparable `ValidationOutcome`. No new subprocess/validator
    execution path is introduced.
  - `kriya/workflow/edit_safety.py::content_revision()` is reused for the
    final fingerprint hash - the same sha256 primitive already used
    throughout this codebase for content identity.

Two-level comparison (see LEVEL1_IMPLEMENTED/LEVEL2_IMPLEMENTED in the
investigation's own RETURN contract):
  LEVEL 1 (mandatory, works for every validator/ecosystem PolymorphicValidator
    already supports): compares whole-invocation execution_status + a
    normalized failure_fingerprint of the raw output. Needs nothing beyond
    what run_compile_check()/run_tests() already return today.
  LEVEL 2 (optional, additive, "incrementally reliable" per the design
    review): when a structured per-test adapter can confidently parse the
    raw output (currently: pytest's default "short test summary info"
    section), per-test-id classification becomes available too. Absence of
    a Level 2 adapter for a given ecosystem is not a defect - Level 1 alone
    is sufficient for correct NEW_FAILURE/PRE_EXISTING_FAILURE/
    CHANGED_FAILURE blocking at the whole-invocation granularity.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from kriya.workflow.edit_safety import content_revision


# --- Data model --------------------------------------------------------------

@dataclass(frozen=True)
class ValidationInvocation:
    """What was run, frozen once and reused byte-for-byte PRE and POST -
    the "PRE and POST validation command/selection identities are frozen
    and equivalent" invariant, made an explicit, comparable value rather
    than an implicit assumption."""

    command_identity: str
    selection_identity: str
    environment_fingerprint: Optional[str] = None

    def matches(self, other: "ValidationInvocation") -> bool:
        """Command/selection identity equality - the caller-visible check
        behind PRE/POST command-identity-mismatch rejection. Environment
        fingerprint is compared only when BOTH sides have one (a validator
        that never sets it is not thereby "incompatible with itself")."""
        if self.command_identity != other.command_identity:
            return False
        if self.selection_identity != other.selection_identity:
            return False
        if self.environment_fingerprint is not None and other.environment_fingerprint is not None:
            return self.environment_fingerprint == other.environment_fingerprint
        return True


class TestStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    SKIP = "skip"


@dataclass(frozen=True)
class TestOutcome:
    test_id: str
    status: TestStatus
    failure_fingerprint: Optional[str] = None


@dataclass(frozen=True)
class ValidationOutcome:
    """Wraps PolymorphicValidator's own existing {"success": bool, "output":
    str} return shape - never a competing execution path. `execution_status`
    distinguishes "the validator genuinely ran and produced a real pass/fail
    verdict" (`"completed"`) from "the validator itself could not run at all"
    (`"environment_failure"` - dependency install failure, timeout, etc.) -
    the latter is what feeds BASELINE_INDETERMINATE, never silently folded
    into an ordinary failure."""

    execution_status: str  # "completed" | "environment_failure"
    success: bool
    failure_fingerprint: Optional[str]
    evidence_ref: Optional[str] = None
    test_outcomes: Optional[Tuple[TestOutcome, ...]] = None
    # Level 2 adapters that can only reliably enumerate FAILING/ERRORING test
    # identities (not an exhaustive PASS list - see pytest_adapter's own
    # docstring) still surface aggregate collected/passed/failed/skipped
    # counts as a cross-check signal, honestly distinct from per-test
    # identity coverage.
    aggregate_counts: Optional[Dict[str, int]] = None


@dataclass(frozen=True)
class ValidationBaseline:
    """The authoritative PRE-candidate record. `workspace_revision` MUST be
    computed against the pristine workspace_path, never a worktree/sandbox
    path - see this module's own capture_validation_baseline() docstring for
    where that boundary is enforced. `status="baseline_indeterminate"` means
    this object carries no trustworthy PRE evidence at all - callers must
    never treat an indeterminate baseline as "assume green"."""

    workspace_revision: Optional[str]
    run_id: str
    invocation: ValidationInvocation
    captured_at: float
    outcome: Optional[ValidationOutcome]
    status: str = "captured"  # "captured" | "baseline_indeterminate"
    indeterminate_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Checkpoint/trace-safe serialization - raw validator output is
        NEVER embedded here (only evidence_ref, a pointer), matching this
        package's own established "hash/reference, never raw content in
        state/checkpoint payloads" convention (CTX-001-P1-C3's own
        search_text_hash precedent)."""
        outcome_dict = None
        if self.outcome is not None:
            outcome_dict = {
                "execution_status": self.outcome.execution_status,
                "success": self.outcome.success,
                "failure_fingerprint": self.outcome.failure_fingerprint,
                "evidence_ref": self.outcome.evidence_ref,
                "aggregate_counts": self.outcome.aggregate_counts,
                "test_outcomes": (
                    [
                        {"test_id": t.test_id, "status": t.status.value, "failure_fingerprint": t.failure_fingerprint}
                        for t in self.outcome.test_outcomes
                    ]
                    if self.outcome.test_outcomes is not None else None
                ),
            }
        return {
            "workspace_revision": self.workspace_revision,
            "run_id": self.run_id,
            "invocation": {
                "command_identity": self.invocation.command_identity,
                "selection_identity": self.invocation.selection_identity,
                "environment_fingerprint": self.invocation.environment_fingerprint,
            },
            "captured_at": self.captured_at,
            "outcome": outcome_dict,
            "status": self.status,
            "indeterminate_reason": self.indeterminate_reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ValidationBaseline":
        outcome = None
        raw_outcome = data.get("outcome")
        if raw_outcome is not None:
            raw_tests = raw_outcome.get("test_outcomes")
            test_outcomes = None
            if raw_tests is not None:
                test_outcomes = tuple(
                    TestOutcome(
                        test_id=t["test_id"], status=TestStatus(t["status"]),
                        failure_fingerprint=t.get("failure_fingerprint"),
                    )
                    for t in raw_tests
                )
            outcome = ValidationOutcome(
                execution_status=raw_outcome["execution_status"],
                success=raw_outcome["success"],
                failure_fingerprint=raw_outcome.get("failure_fingerprint"),
                evidence_ref=raw_outcome.get("evidence_ref"),
                test_outcomes=test_outcomes,
                aggregate_counts=raw_outcome.get("aggregate_counts"),
            )
        raw_invocation = data["invocation"]
        return cls(
            workspace_revision=data.get("workspace_revision"),
            run_id=data["run_id"],
            invocation=ValidationInvocation(
                command_identity=raw_invocation["command_identity"],
                selection_identity=raw_invocation["selection_identity"],
                environment_fingerprint=raw_invocation.get("environment_fingerprint"),
            ),
            captured_at=data["captured_at"],
            outcome=outcome,
            status=data.get("status", "captured"),
            indeterminate_reason=data.get("indeterminate_reason"),
        )


# --- Failure fingerprint normalization ---------------------------------------

# Deliberately narrow: strips only tokens that are volatile ACROSS two
# otherwise-identical runs of the SAME failure (temp paths, timestamps,
# durations, memory addresses, PIDs) - never line numbers or exception
# messages, which are semantically material ("retain meaningful failure
# location/message signature" - do not over-normalize distinct failures
# into equality).
_VOLATILE_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"/private/var/folders/\S+"),
    re.compile(r"/var/folders/\S+"),
    re.compile(r"/tmp/\S+"),
    re.compile(r"\b[a-zA-Z]:\\Users\\[^\\]+\\AppData\\Local\\Temp\\\S+"),
    re.compile(r"\btmp[a-zA-Z0-9_]{6,}\b"),
    re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d+)?"),
    re.compile(r"\bin \d+\.\d+s\b"),
    re.compile(r"\b\d+\.\d+ ?(?:s|sec|seconds)\b"),
    re.compile(r"0x[0-9a-fA-F]{4,}"),
    re.compile(r"\bpid=?\d+\b", re.IGNORECASE),
    re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),
)
_PLACEHOLDER = "<volatile>"


def normalize_failure_text(raw: str) -> str:
    """Deterministic normalization - strips volatile noise, retains every
    semantically material token (test identity, exception/error category,
    file:line, message text). Two invocations of the exact same real
    failure normalize identically; two genuinely different failures never
    collapse to the same normalized text merely because both happen to
    reference, say, a temp path or a timestamp."""
    text = raw or ""
    for pattern in _VOLATILE_PATTERNS:
        text = pattern.sub(_PLACEHOLDER, text)
    return text


def compute_failure_fingerprint(raw: str) -> str:
    """The stable, comparable identity of a failure's own text - normalize
    then hash via this codebase's one shared content-hash primitive
    (edit_safety.content_revision), never a second hashing scheme."""
    return content_revision(normalize_failure_text(raw))


# --- Level 2 structured adapter: pytest --------------------------------------
#
# Deliberately incremental, not universal (invariant 10: "do not require
# universal per-test parsing for initial correctness"). Reliably extracts
# FAILING/ERRORING test identities from pytest's own default "short test
# summary info" section (`FAILED <nodeid> - <reason>` / `ERROR <nodeid> -
# <reason>` lines, present in pytest's default output without requiring any
# extra flag) - this is exactly what NEW_FAILURE/CHANGED_FAILURE detection
# needs (a test that just failed necessarily appears here). It does NOT
# claim to enumerate every individual PASSING test id (pytest's default,
# non-verbose output gives only an aggregate pass COUNT, not per-test
# identity) - that limitation is surfaced honestly via `aggregate_counts`
# rather than fabricated per-test PASS entries.

_PYTEST_SUMMARY_LINE_RE = re.compile(
    r"^(FAILED|ERROR)\s+(\S+?)(?:\s+-\s+(.*))?$", re.MULTILINE,
)
_PYTEST_FINAL_SUMMARY_RE = re.compile(
    r"=+ ("
    r"(?:\d+ \w+(?:, )?)+"
    r") in [\d.]+s"
)
_PYTEST_COUNT_RE = re.compile(r"(\d+) (passed|failed|error|errors|skipped|xfailed|xpassed)")


def looks_like_pytest_output(raw: str) -> bool:
    return bool(raw) and ("pytest" in raw.lower() or "test session starts" in raw or bool(_PYTEST_FINAL_SUMMARY_RE.search(raw or "")))


def parse_pytest_structured_outcomes(raw: str) -> Optional[Tuple[Tuple[TestOutcome, ...], Dict[str, int]]]:
    """Returns (test_outcomes, aggregate_counts) for FAILED/ERROR tests found
    in pytest's default short-summary section, plus whatever aggregate
    pass/fail/skip counts the final summary line reports - or None if `raw`
    doesn't look like pytest output at all (caller falls back to Level 1
    only, never fabricates a guess)."""
    if not looks_like_pytest_output(raw):
        return None

    outcomes: List[TestOutcome] = []
    seen_ids = set()
    for kind, test_id, reason in _PYTEST_SUMMARY_LINE_RE.findall(raw):
        if test_id in seen_ids:
            continue
        seen_ids.add(test_id)
        status = TestStatus.FAIL if kind == "FAILED" else TestStatus.ERROR
        fingerprint = compute_failure_fingerprint(reason or "")
        outcomes.append(TestOutcome(test_id=test_id, status=status, failure_fingerprint=fingerprint))

    counts: Dict[str, int] = {}
    match = _PYTEST_FINAL_SUMMARY_RE.search(raw)
    if match:
        for count, label in _PYTEST_COUNT_RE.findall(match.group(0)):
            key = {"errors": "error", "error": "error"}.get(label, label)
            counts[key] = counts.get(key, 0) + int(count)

    return tuple(outcomes), counts


# --- Building a ValidationOutcome from an existing validator result ---------

def build_validation_outcome(
    raw_result: Dict[str, Any], *, evidence_ref: Optional[str] = None,
) -> ValidationOutcome:
    """Wraps PolymorphicValidator.run_compile_check()/run_tests()'s own
    EXISTING {"success": bool, "output": str} return shape - the ONLY
    validator-execution contract this module depends on (invariant 9:
    "existing validator callers remain compatible" - this function is
    purely additive, called AFTER an existing call site's own unchanged
    validator invocation, never replacing it)."""
    success = bool(raw_result.get("success"))
    output = raw_result.get("output") or ""
    fingerprint = None if success else compute_failure_fingerprint(output)
    test_outcomes = None
    aggregate_counts = None
    parsed = parse_pytest_structured_outcomes(output)
    if parsed is not None:
        # Deliberately NOT collapsed to None when the tuple is empty - an
        # empty-but-successfully-parsed tuple means "pytest output was
        # recognized and reported ZERO failures", a real, meaningful
        # structured result (see classify_level2_delta's own
        # pre_was_fully_passing parameter, which depends on being able to
        # tell "confirmed zero failures" apart from "could not parse at
        # all"). Only a genuine parse failure (parsed is None) leaves
        # test_outcomes as None.
        test_outcomes, aggregate_counts = parsed
    return ValidationOutcome(
        execution_status="completed",
        success=success,
        failure_fingerprint=fingerprint,
        evidence_ref=evidence_ref,
        test_outcomes=test_outcomes,
        aggregate_counts=aggregate_counts or None,
    )


def environment_failure_outcome(reason: str) -> ValidationOutcome:
    """The validator itself could not produce a trustworthy verdict at
    all - distinct from an ordinary test failure. Feeds baseline_
    indeterminate / INFRASTRUCTURE_ENVIRONMENT_FAILURE, never silently
    treated as an ordinary pass or fail."""
    return ValidationOutcome(
        execution_status="environment_failure", success=False,
        failure_fingerprint=compute_failure_fingerprint(reason),
    )


# --- Baseline capture --------------------------------------------------------

def capture_validation_baseline(
    *, workspace_revision: Optional[str], run_id: str, invocation: ValidationInvocation,
    raw_result: Optional[Dict[str, Any]], evidence_ref: Optional[str] = None,
    indeterminate_reason: Optional[str] = None, now: Optional[float] = None,
) -> ValidationBaseline:
    """Pure construction - the CALLER (kriya/workflow/workflow.py) is
    responsible for actually invoking PolymorphicValidator against the
    pristine workspace_path (never a worktree/sandbox path - invariant 1/2)
    and passing the resulting raw_result here. `workspace_revision=None` or
    `raw_result=None` (validator itself failed to execute) both produce
    status="baseline_indeterminate" - this function never guesses a
    trustworthy baseline into existence from incomplete inputs."""
    captured_at = now if now is not None else time.time()
    if workspace_revision is None:
        return ValidationBaseline(
            workspace_revision=None, run_id=run_id, invocation=invocation,
            captured_at=captured_at, outcome=None, status="baseline_indeterminate",
            indeterminate_reason=indeterminate_reason or "workspace_revision could not be computed",
        )
    if raw_result is None:
        return ValidationBaseline(
            workspace_revision=workspace_revision, run_id=run_id, invocation=invocation,
            captured_at=captured_at, outcome=None, status="baseline_indeterminate",
            indeterminate_reason=indeterminate_reason or "validator did not produce a result",
        )
    outcome = build_validation_outcome(raw_result, evidence_ref=evidence_ref)
    return ValidationBaseline(
        workspace_revision=workspace_revision, run_id=run_id, invocation=invocation,
        captured_at=captured_at, outcome=outcome, status="captured",
    )


def is_baseline_reusable(
    baseline: Optional[ValidationBaseline], *, current_workspace_revision: Optional[str],
    invocation: ValidationInvocation,
) -> bool:
    """Retry/resume reuse gate (invariant 8, and the CHECKPOINT/RESUME
    requirement): a baseline is reusable ONLY when the pristine workspace
    revision is byte-identical AND the invocation (command/selection/
    environment) matches exactly. The candidate/sandbox's own revision is
    NEVER an input here - reusability is decided purely from the pristine
    workspace's own identity, matching invariant "Candidate/sandbox
    revision MUST NOT become the cache key for pristine baseline." """
    if baseline is None or baseline.status != "captured":
        return False
    if current_workspace_revision is None or baseline.workspace_revision != current_workspace_revision:
        return False
    return baseline.invocation.matches(invocation)


# --- Delta classification ----------------------------------------------------

class DeltaClassification(str, Enum):
    UNCHANGED_PASS = "UNCHANGED_PASS"
    PRE_EXISTING_FAILURE = "PRE_EXISTING_FAILURE"
    RESOLVED_FAILURE = "RESOLVED_FAILURE"
    NEW_FAILURE = "NEW_FAILURE"
    CHANGED_FAILURE = "CHANGED_FAILURE"
    NEWLY_SKIPPED_OR_NOT_EXECUTED = "NEWLY_SKIPPED_OR_NOT_EXECUTED"
    INFRASTRUCTURE_ENVIRONMENT_FAILURE = "INFRASTRUCTURE_ENVIRONMENT_FAILURE"
    NOT_COMPARABLE = "NOT_COMPARABLE"


# Conservative-by-default blocking set - "fail conservatively" for anything
# resembling an unexplained disappearance or environment problem, per the
# FAILURE FINGERPRINT / TWO-LEVEL COMPARISON section's own explicit
# instruction. NOT_COMPARABLE (a genuinely new test) is deliberately
# excluded - a new test's own pass/fail is judged by Kriya's ordinary
# quality gates elsewhere, never blocked by baseline comparison itself.
TERMINAL_BLOCKING_CLASSIFICATIONS = frozenset({
    DeltaClassification.NEW_FAILURE,
    DeltaClassification.CHANGED_FAILURE,
    DeltaClassification.NEWLY_SKIPPED_OR_NOT_EXECUTED,
    DeltaClassification.INFRASTRUCTURE_ENVIRONMENT_FAILURE,
})


@dataclass(frozen=True)
class Level1Delta:
    classification: DeltaClassification
    pre_fingerprint: Optional[str]
    post_fingerprint: Optional[str]


def classify_level1_delta(pre: ValidationOutcome, post: ValidationOutcome) -> Level1Delta:
    """Whole-invocation comparison - always available (needs only
    execution_status/success/failure_fingerprint, which build_validation_
    outcome() always populates for any ecosystem)."""
    if pre.execution_status != "completed" or post.execution_status != "completed":
        return Level1Delta(DeltaClassification.INFRASTRUCTURE_ENVIRONMENT_FAILURE, pre.failure_fingerprint, post.failure_fingerprint)
    if pre.success and post.success:
        return Level1Delta(DeltaClassification.UNCHANGED_PASS, None, None)
    if pre.success and not post.success:
        return Level1Delta(DeltaClassification.NEW_FAILURE, None, post.failure_fingerprint)
    if not pre.success and post.success:
        return Level1Delta(DeltaClassification.RESOLVED_FAILURE, pre.failure_fingerprint, None)
    # Both failed - the critical rule: same failure text only if the
    # NORMALIZED fingerprint actually matches; otherwise CHANGED_FAILURE,
    # never a raw-textual-equality shortcut over ambiguous evidence.
    if pre.failure_fingerprint == post.failure_fingerprint:
        return Level1Delta(DeltaClassification.PRE_EXISTING_FAILURE, pre.failure_fingerprint, post.failure_fingerprint)
    return Level1Delta(DeltaClassification.CHANGED_FAILURE, pre.failure_fingerprint, post.failure_fingerprint)


def classify_level2_delta(
    pre_outcomes: Sequence[TestOutcome], post_outcomes: Sequence[TestOutcome],
    *, pre_was_fully_passing: bool = False,
) -> Dict[str, DeltaClassification]:
    """Per-test-id comparison, when structured outcomes are available on
    BOTH sides. Only ever covers the test ids either side actually reports
    (FAILED/ERROR for the default pytest adapter - see that adapter's own
    docstring for why an exhaustive PASS enumeration isn't attempted) - a
    test id present in neither dict is simply not covered by this
    function; aggregate_counts-based cross-checking (see classify_
    baseline_delta) is what surfaces a silent disappearance at the
    aggregate level.

    `pre_was_fully_passing` (default False, the conservative/honest
    default): when the CALLER already knows PRE's whole invocation
    succeeded (ValidationOutcome.success is True), an "absent from
    pre_by_id" test_id is NOT actually ambiguous - a fully-passing PRE run
    means every test passed, so a test now failing in POST that isn't in
    the (necessarily empty) PRE failed-list is a real NEW_FAILURE, not an
    indistinguishable "maybe new, maybe was passing" case. Without this
    context (pre_was_fully_passing=False, PRE itself had SOME failures),
    a test absent from pre_by_id genuinely cannot be told apart from a
    brand-new test using only the FAILED/ERROR-only adapter this module
    ships - NOT_COMPARABLE remains the honest answer for that case."""
    pre_by_id = {o.test_id: o for o in pre_outcomes}
    post_by_id = {o.test_id: o for o in post_outcomes}
    all_ids = set(pre_by_id) | set(post_by_id)
    result: Dict[str, DeltaClassification] = {}
    for test_id in sorted(all_ids):
        pre = pre_by_id.get(test_id)
        post = post_by_id.get(test_id)
        if pre is None:
            if pre_was_fully_passing and post is not None and post.status in (TestStatus.FAIL, TestStatus.ERROR):
                result[test_id] = DeltaClassification.NEW_FAILURE
            else:
                result[test_id] = DeltaClassification.NOT_COMPARABLE
            continue
        if post is None:
            # A test that PRE-existed (in the failed/error list we have
            # identities for) and is now simply absent from POST's own
            # failed/error list. If it's not reported as failed anymore,
            # it either passed (RESOLVED) or vanished (can't distinguish
            # from this per-id view alone) - conservatively defer to
            # NEWLY_SKIPPED_OR_NOT_EXECUTED only when aggregate counts
            # (handled by the caller) corroborate a real drop; here, a
            # bare disappearance from the failing set is treated as
            # RESOLVED_FAILURE (the common, benign case), with the
            # aggregate-count cross-check in classify_baseline_delta
            # responsible for catching a genuine silent vanish.
            result[test_id] = DeltaClassification.RESOLVED_FAILURE
            continue
        if pre.status == TestStatus.SKIP or post.status == TestStatus.SKIP:
            if pre.status != TestStatus.SKIP and post.status == TestStatus.SKIP:
                result[test_id] = DeltaClassification.NEWLY_SKIPPED_OR_NOT_EXECUTED
                continue
        if pre.status in (TestStatus.FAIL, TestStatus.ERROR) and post.status in (TestStatus.FAIL, TestStatus.ERROR):
            if pre.failure_fingerprint == post.failure_fingerprint:
                result[test_id] = DeltaClassification.PRE_EXISTING_FAILURE
            else:
                result[test_id] = DeltaClassification.CHANGED_FAILURE
            continue
        if pre.status in (TestStatus.FAIL, TestStatus.ERROR) and post.status == TestStatus.PASS:
            result[test_id] = DeltaClassification.RESOLVED_FAILURE
            continue
        if pre.status == TestStatus.PASS and post.status in (TestStatus.FAIL, TestStatus.ERROR):
            result[test_id] = DeltaClassification.NEW_FAILURE
            continue
        result[test_id] = DeltaClassification.UNCHANGED_PASS
    return result


@dataclass(frozen=True)
class BaselineDeltaResult:
    """The full comparison result the caller (workflow.py) actually acts
    on. `blocking` is the single boolean decision point - True whenever
    ANY classification (level1 or any level2 entry) is in
    TERMINAL_BLOCKING_CLASSIFICATIONS."""

    level1: Level1Delta
    level2: Dict[str, DeltaClassification]
    aggregate_drop_detected: bool
    blocking: bool
    blocking_reasons: Tuple[str, ...]


def classify_baseline_delta(baseline: ValidationBaseline, post: ValidationOutcome) -> BaselineDeltaResult:
    """The one entry point kriya/workflow/workflow.py calls. Requires
    baseline.status == "captured" and baseline.outcome is not None - a
    caller must check baseline-indeterminate BEFORE calling this (see
    FAILURE_BEHAVIOR: indeterminate is a distinct terminal outcome, never
    routed through delta comparison at all)."""
    if baseline.status != "captured" or baseline.outcome is None:
        raise ValueError("classify_baseline_delta requires a captured baseline with a real outcome")
    pre = baseline.outcome
    level1 = classify_level1_delta(pre, post)

    level2: Dict[str, DeltaClassification] = {}
    # `is not None` (never bare truthiness) - an empty-but-successfully-
    # parsed tuple (pytest ran, confirmed zero failures) must still enable
    # Level 2 comparison; only a genuine parse failure (None) skips it.
    if pre.test_outcomes is not None and post.test_outcomes is not None:
        level2 = classify_level2_delta(
            pre.test_outcomes, post.test_outcomes, pre_was_fully_passing=pre.success,
        )

    aggregate_drop = False
    if pre.aggregate_counts and post.aggregate_counts:
        pre_total = sum(pre.aggregate_counts.values())
        post_total = sum(post.aggregate_counts.values())
        pre_failing = pre.aggregate_counts.get("failed", 0) + pre.aggregate_counts.get("error", 0)
        post_failing = post.aggregate_counts.get("failed", 0) + post.aggregate_counts.get("error", 0)
        # A drop in TOTAL collected/reported tests that isn't explained by
        # an equal-or-greater rise in failing tests is a silent
        # disappearance signal - conservative, aggregate-level detection
        # for exactly the case classify_level2_delta's own per-id view
        # can't see (a test that stops being collected/reported at all).
        # total_drop and failure_rise are both "positive means worse" -
        # flag only when more tests vanished than can be accounted for by
        # tests that simply moved from passing into the failing count.
        total_drop = pre_total - post_total
        failure_rise = post_failing - pre_failing
        if total_drop > 0 and total_drop > max(0, failure_rise):
            aggregate_drop = True

    reasons: List[str] = []
    blocking = False
    if level1.classification in TERMINAL_BLOCKING_CLASSIFICATIONS:
        blocking = True
        reasons.append(f"level1:{level1.classification.value}")
    for test_id, classification in level2.items():
        if classification in TERMINAL_BLOCKING_CLASSIFICATIONS:
            blocking = True
            reasons.append(f"level2:{test_id}:{classification.value}")
    if aggregate_drop:
        blocking = True
        reasons.append("aggregate_count_drop")

    return BaselineDeltaResult(
        level1=level1, level2=level2, aggregate_drop_detected=aggregate_drop,
        blocking=blocking, blocking_reasons=tuple(reasons),
    )


# --- Pre-mutation orchestration ---------------------------------------------
#
# `capture_brownfield_baselines()` is the ONE function kriya/workflow/
# workflow.py calls, kept deliberately thin at that call site (this is where
# the real capture/reuse/resume/hard-stop logic actually lives, unit-testable
# without a real git repo or a real PolymorphicValidator subprocess - both
# are injected as plain callables, never imported by this module). Ordering
# is mechanically testable: this function must be called, and must return,
# BEFORE workflow.py creates the sandbox worktree or calls the Developer -
# the caller enforces that by construction (this function has no side effect
# reaching into worktree/candidate state at all - it only reads
# workspace_path via the injected callables).

@dataclass(frozen=True)
class BrownfieldBaselineCaptureResult:
    targeted: Optional[ValidationBaseline]
    full_regression: Optional[ValidationBaseline]
    # Set only when full_regression_policy=="required" and that baseline
    # came back indeterminate - the caller must return this as an explicit
    # terminal result (FAILURE_BEHAVIOR: never silently proceed assuming
    # green) rather than continue toward the Developer.
    hard_stop_reason: Optional[str] = None


def _capture_single_baseline(
    *, run_id: str, selection_identity: str, target_test: Optional[str],
    run_validator: Callable[[Optional[str]], Dict[str, Any]],
    compute_revision: Callable[[], Optional[str]],
) -> ValidationBaseline:
    invocation = ValidationInvocation(
        command_identity="polymorphic_validator.run_tests", selection_identity=selection_identity,
    )
    workspace_revision = compute_revision()
    if workspace_revision is None:
        return capture_validation_baseline(
            workspace_revision=None, run_id=run_id, invocation=invocation, raw_result=None,
            indeterminate_reason="pristine workspace_revision could not be computed "
                                  "(not a git repository, or HEAD does not resolve)",
        )
    try:
        raw_result = run_validator(target_test)
    except Exception as validator_ex:
        return capture_validation_baseline(
            workspace_revision=workspace_revision, run_id=run_id, invocation=invocation,
            raw_result=None, indeterminate_reason=f"validator raised: {validator_ex}",
        )
    if raw_result is None:
        return capture_validation_baseline(
            workspace_revision=workspace_revision, run_id=run_id, invocation=invocation,
            raw_result=None, indeterminate_reason="validator returned no result",
        )
    return capture_validation_baseline(
        workspace_revision=workspace_revision, run_id=run_id, invocation=invocation, raw_result=raw_result,
    )


def _reuse_or_capture(
    *, run_id: str, selection_identity: str, target_test: Optional[str],
    resume_baseline: Optional[Dict[str, Any]],
    run_validator: Callable[[Optional[str]], Dict[str, Any]],
    compute_revision: Callable[[], Optional[str]],
) -> ValidationBaseline:
    invocation = ValidationInvocation(
        command_identity="polymorphic_validator.run_tests", selection_identity=selection_identity,
    )
    if resume_baseline is not None:
        try:
            candidate = ValidationBaseline.from_dict(resume_baseline)
            if is_baseline_reusable(
                candidate, current_workspace_revision=compute_revision(), invocation=invocation,
            ):
                return candidate
        except Exception:
            pass  # malformed/legacy checkpoint payload - fall through to a fresh capture, never raise
    return _capture_single_baseline(
        run_id=run_id, selection_identity=selection_identity, target_test=target_test,
        run_validator=run_validator, compute_revision=compute_revision,
    )


def capture_brownfield_baselines(
    *, run_id: str,
    target_test: Optional[str],
    full_regression_policy: str,
    run_validator: Callable[[Optional[str]], Dict[str, Any]],
    compute_revision: Callable[[], Optional[str]],
    resume_baseline_targeted: Optional[Dict[str, Any]] = None,
    resume_baseline_full_regression: Optional[Dict[str, Any]] = None,
) -> BrownfieldBaselineCaptureResult:
    """`run_validator(target_test) -> {"success": bool, "output": str}` and
    `compute_revision() -> Optional[str]` are the caller's own thin wrappers
    around PolymorphicValidator.run_tests()/compute_workspace_content_hash()
    (both reused as-is, never reimplemented here) - injected so this
    function (and everything it calls) stays free of subprocess/git
    execution and fully deterministic to unit-test.

    Captures a TARGETED baseline whenever `target_test` is set (regardless
    of `full_regression_policy`) and a FULL-REGRESSION baseline only when
    `full_regression_policy == "required"` - "disabled"/"auto" (today,
    "auto" behaves as "disabled" - see AutonomyConfig's own field
    docstring) capture neither, a complete no-op matching current
    behavior exactly.

    Resume/reuse: a checkpointed baseline dict is reused (never re-run)
    when its own pristine workspace_revision and invocation both still
    match current reality - repository drift transparently invalidates it
    and triggers a fresh capture, never a silent reuse across drift."""
    targeted = None
    if target_test is not None:
        targeted = _reuse_or_capture(
            run_id=run_id, selection_identity=f"target_test:{target_test}", target_test=target_test,
            resume_baseline=resume_baseline_targeted,
            run_validator=run_validator, compute_revision=compute_revision,
        )

    full_regression = None
    hard_stop_reason = None
    if full_regression_policy == "required":
        full_regression = _reuse_or_capture(
            run_id=run_id, selection_identity="full_suite", target_test=None,
            resume_baseline=resume_baseline_full_regression,
            run_validator=run_validator, compute_revision=compute_revision,
        )
        if full_regression.status == "baseline_indeterminate":
            hard_stop_reason = (
                "brownfield_full_regression_baseline_policy is 'required' but a trustworthy "
                f"PRE-mutation baseline could not be established: {full_regression.indeterminate_reason}"
            )

    return BrownfieldBaselineCaptureResult(
        targeted=targeted, full_regression=full_regression, hard_stop_reason=hard_stop_reason,
    )
