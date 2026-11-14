"""Generated-verifier semantic authority (VERIFIER-SEMANTIC-AUTHORITY-001, WS3-C, owner rulings 2026-11-14).

The ONE owner of this one policy: which run-authored test sources are generated-verifier subjects, whether their expectation has semantic authority, what a
person's decision permits, and whether a failure of an accepted verifier may select a production owner. The loop seams transport facts to it and obey the
typed decision it returns; none of them recomputes this policy. It is not a general workflow-policy registry.

Kriya has no typed "verifier" role: a model-written test is an ordinary planned file of an implementation unit, and a file NAME is never evidence here. A
generated-verifier subject is derived only from three facts:

1. the structured test-execution report of the gate (``kriya/tools/test_execution.py``): which test identities executed, and with what status;
2. the structural parser (``kriya.code_intel.parsing.parse_file``): an executed identity belongs to a source file when it equals the ``lookup_key`` of a
   callable that file declares (the report's identity is documented as that same key);
3. the unit's write journal: the file is in the run's written set, which holds only paths whose staged bytes differ from the captured originals (created,
   or modified) - an unchanged pre-existing test is never a subject merely because it ran.

One subject per such source file. No deterministic authority can establish what a newly written verifier SHOULD expect (the acceptance oracle, the
human-bound acceptance and the named-test oracle all judge operator-supplied or base-revision tests), so the authority is a person's
``verifier_semantic_review`` decision (``kriya/policy/approval.py``): unattended, or with no decision, the answer is needs-review.

``expectation_valid`` is authority for the exact verifier content under the exact contract (``semantic_authority_fingerprint``: no review phase, no failure
identity), so it survives the verifier going from failing to passing and a later different failure of the same bytes. It never selects a production owner:
that needs typed failure + a deterministic locator to a non-verifier workspace artifact + ONE owner in the validated plan (``production_owner_authority``).
``expectation_invalid`` spends the subject's one regeneration (run + unit + path). ``cannot_decide`` and no decision establish nothing.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from kriya.code_intel.parsing import is_structural_path, parse_file
from kriya.policy.approval import (
    VerifierSemanticAllowance,
    VerifierSemanticDecision,
    VerifierSemanticSubject,
    ask_verifier_semantic,
    verifier_semantic_matches,
)

logger = logging.getLogger(__name__)

# The structured report's own vocabulary (kriya/tools/test_execution.py: COMPLETE / FAILED / ERROR), as the report dict carries it.
_REPORT_COMPLETE = "COMPLETE"
_FAILING_STATUSES = frozenset({"failed", "error"})
# The phase fact of a failed JVM gate (kriya/tools/test_execution.py, JVM-TEST-PHASE-001) that names no executed case at all.
_TEST_SOURCE_COMPILE_FAILURE = "TEST_SOURCE_COMPILE_FAILURE"

# The typed failure a unit stops on when a generated verifier's semantics have no authority, and the prefix of its message (the unit's terminal category
# keys on the prefix, like every other deterministic stop).
VERIFIER_SEMANTIC_STOP_TYPE = "verifier_semantic_authority_required"
VERIFIER_SEMANTIC_AUTHORITY_REQUIRED = "VERIFIER_SEMANTIC_AUTHORITY_REQUIRED"

# Why a subject is not settled (closed table).
REASON_UNATTENDED = "VERIFIER_SEMANTIC_UNATTENDED"
REASON_NO_DECISION = "VERIFIER_SEMANTIC_NO_DECISION"
REASON_CANNOT_DECIDE = "VERIFIER_SEMANTIC_CANNOT_DECIDE"
REASON_DECISION_STALE = "VERIFIER_SEMANTIC_DECISION_STALE"
REASON_REGENERATION_SPENT = "VERIFIER_SEMANTIC_REGENERATION_ALREADY_USED"
REASON_SUBJECT_AMBIGUOUS = "VERIFIER_SEMANTIC_SUBJECT_AMBIGUOUS"
REASON_SUBJECT_UNREADABLE = "VERIFIER_SEMANTIC_SUBJECT_UNREADABLE"
REASON_TEST_SOURCE_COMPILE = "VERIFIER_SEMANTIC_TEST_SOURCE_COMPILE_WITHOUT_SUBJECT"
REASON_NO_PRODUCTION_OWNER = "VERIFIER_SEMANTIC_NO_PRODUCTION_OWNER_AUTHORITY"
REVIEW_REASON_CODES = (REASON_UNATTENDED, REASON_NO_DECISION, REASON_CANNOT_DECIDE, REASON_DECISION_STALE, REASON_REGENERATION_SPENT,
                       REASON_SUBJECT_AMBIGUOUS, REASON_SUBJECT_UNREADABLE, REASON_TEST_SOURCE_COMPILE, REASON_NO_PRODUCTION_OWNER)

# Why a production owner was or was not selected after an accepted verifier failed (closed table).
OWNER_SELECTED = "PRODUCTION_OWNER_SELECTED"
OWNER_NO_EXECUTED_FAILURE = "PRODUCTION_OWNER_NO_EXECUTED_FAILURE"
OWNER_LOCATOR_ABSENT = "PRODUCTION_OWNER_LOCATOR_ABSENT"
OWNER_LOCATOR_VERIFIER_ONLY = "PRODUCTION_OWNER_LOCATOR_VERIFIER_ONLY"
OWNER_LOCATOR_EXTERNAL = "PRODUCTION_OWNER_LOCATOR_NOT_A_WORKSPACE_ARTIFACT"
OWNER_UNOWNED = "PRODUCTION_OWNER_ARTIFACT_HAS_NO_PLAN_OWNER"
OWNER_AMBIGUOUS = "PRODUCTION_OWNER_AMBIGUOUS"
OWNER_CONFLICTING = "PRODUCTION_OWNER_CONFLICTING_EVIDENCE"
OWNER_REASON_CODES = (OWNER_SELECTED, OWNER_NO_EXECUTED_FAILURE, OWNER_LOCATOR_ABSENT, OWNER_LOCATOR_VERIFIER_ONLY, OWNER_LOCATOR_EXTERNAL, OWNER_UNOWNED,
                      OWNER_AMBIGUOUS, OWNER_CONFLICTING)


# ----------------------------------------------------------------------------- subject derivation

@dataclass(frozen=True)
class ExecutedVerifier:
    """One run-authored source file with at least one executed test identity mapped to it."""

    path: str                                  # canonical workspace-relative path
    sha256: str                                # hex digest of the exact bytes the gate ran
    source: str
    identities: Tuple[str, ...]                # executed identities this file declares
    failed_identities: Tuple[str, ...]         # those reported failed or errored


@dataclass(frozen=True)
class VerifierDerivation:
    """What one gate's structured report says about run-authored verifier sources. ``complete`` False = the report establishes nothing (no subject).
    ``ambiguous``: executed identities declared by more than one run-authored file (never assigned to either). ``unreadable``: run-authored structural
    files whose bytes could not be read, decoded or cleanly parsed, so what they declare is unknown."""

    verifiers: Tuple[ExecutedVerifier, ...] = ()
    complete: bool = False
    ambiguous: Tuple[str, ...] = ()
    unreadable: Tuple[str, ...] = ()

    @property
    def failing(self) -> Tuple[ExecutedVerifier, ...]:
        return tuple(verifier for verifier in self.verifiers if verifier.failed_identities)

    @property
    def undetermined(self) -> bool:
        """An executed identity could belong to a run-authored file that cannot be told apart or read: no subject may be invented, and none ruled out."""
        return bool(self.ambiguous or self.unreadable)


def normalize_path(path: str) -> str:
    """The canonical workspace-relative spelling: forward slashes, no leading ``./`` (a leading dot of a name such as ``.kriya`` is kept)."""
    normalized = (path or "").replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def derive_executed_verifiers(report: Optional[Mapping[str, Any]], authored: Mapping[str, Optional[bytes]]) -> VerifierDerivation:
    """``report``: the gate result's ``test_execution`` dict. ``authored``: {workspace-relative path: the exact current bytes} for every path the unit's
    write journal holds (None = unreadable now). Only a COMPLETE report establishes anything. No file name, suffix or directory is consulted."""
    if not isinstance(report, Mapping) or report.get("completeness") != _REPORT_COMPLETE:
        return VerifierDerivation()
    cases = [case for case in report.get("test_cases") or () if isinstance(case, Mapping) and isinstance(case.get("identity"), str)]
    executed = {case["identity"] for case in cases}
    failed = {case["identity"] for case in cases if case.get("status") in _FAILING_STATUSES}
    declared: Dict[str, List[str]] = {}
    sources: Dict[str, Tuple[bytes, str]] = {}
    unreadable: List[str] = []
    for raw_path in sorted(authored):
        path = normalize_path(raw_path)
        if not is_structural_path(path):
            continue                                   # not a source the parser models: it declares no test callable Kriya can map
        data = authored[raw_path]
        try:
            if data is None:
                raise ValueError("unreadable")
            text = data.decode("utf-8")
            structure = parse_file(path, data)
            if getattr(structure, "error_count", 0):
                raise ValueError("parse errors")
        except Exception:  # what the file declares is unknown: reported, never guessed
            unreadable.append(path)
            continue
        sources[path] = (data, text)
        for symbol in structure.symbols:
            if symbol.is_callable and symbol.lookup_key in executed:
                declared.setdefault(symbol.lookup_key, []).append(path)
    ambiguous = sorted(identity for identity, paths in declared.items() if len(set(paths)) > 1)
    by_file: Dict[str, List[str]] = {}
    for identity, paths in declared.items():
        if len(set(paths)) == 1:
            by_file.setdefault(paths[0], []).append(identity)
    verifiers = tuple(
        ExecutedVerifier(path=path, sha256=hashlib.sha256(sources[path][0]).hexdigest(), source=sources[path][1],
                         identities=tuple(sorted(identities)), failed_identities=tuple(sorted(i for i in identities if i in failed)))
        for path, identities in sorted(by_file.items()))
    return VerifierDerivation(verifiers=verifiers, complete=True, ambiguous=tuple(ambiguous), unreadable=tuple(unreadable))


def is_test_source_compile_without_subject(test_result: Any) -> bool:
    """True when the gate's typed phase record says main compilation succeeded and test-source compilation did not: nothing executed, so no exact
    verifier subject exists and no authority proves whose defect it is (no ordinary retry, no verifier repair, no production reopen from this fact)."""
    phase = test_result.get("test_phase") if isinstance(test_result, Mapping) else None
    return isinstance(phase, Mapping) and phase.get("phase_class") == _TEST_SOURCE_COMPILE_FAILURE


# ----------------------------------------------------------------------------- semantic authority and the next action

class Action(str, Enum):
    PROCEED = "proceed"                          # the subject's expectation has semantic authority (expectation_valid for these exact bytes)
    ASK = "ask"                                  # attended, no authority yet: put the semantic review to the person
    REGENERATE_VERIFIER = "regenerate_verifier"  # expectation_invalid: the subject's ONE regeneration, of exactly its own source path
    NEEDS_REVIEW = "needs_review"                # nothing may be changed; the unit stops for review


@dataclass(frozen=True)
class SubjectDecision:
    action: Action
    path: str
    reason_code: Optional[str] = None            # set for NEEDS_REVIEW (REVIEW_REASON_CODES)
    detail: str = ""


class VerifierSemanticLedger:
    """The run's semantic-authority state (memory only; the durable evidence is the review record itself). ``expectation_valid`` decisions are kept by the
    semantic-authority fingerprint they were made for; the regeneration allowance is per subject. One per run, shared by every unit of the run."""

    def __init__(self) -> None:
        self._valid: Dict[str, str] = {}         # semantic_authority_fingerprint -> the review fingerprint whose decision established it
        self.allowance = VerifierSemanticAllowance()

    def expectation_valid(self, subject: VerifierSemanticSubject) -> bool:
        return subject.semantic_authority_fingerprint in self._valid

    def before_asking(self, subject: VerifierSemanticSubject, *, attended: bool) -> SubjectDecision:
        path = _subject_path(subject)
        if self.expectation_valid(subject):
            return SubjectDecision(Action.PROCEED, path)
        if not attended:
            return SubjectDecision(Action.NEEDS_REVIEW, path, REASON_UNATTENDED,
                                   "no deterministic authority establishes what a generated verifier should expect, and nobody can be asked")
        return SubjectDecision(Action.ASK, path)

    def after_decision(self, subject_now: VerifierSemanticSubject, decision: Any) -> SubjectDecision:
        """Apply a person's answer to the subject AS IT IS NOW (the caller recomputes it after the wait): a decision bound to another review is stale."""
        path = _subject_path(subject_now)
        if not isinstance(decision, VerifierSemanticDecision) or not decision.decided:
            reason = getattr(decision, "no_decision_reason", None) or "no decision"
            return SubjectDecision(Action.NEEDS_REVIEW, path, REASON_NO_DECISION, str(reason))
        if not verifier_semantic_matches(decision, subject_now):
            return SubjectDecision(Action.NEEDS_REVIEW, path, REASON_DECISION_STALE, "a binding of the review changed while it was being decided")
        if decision.valid:
            self._valid[subject_now.semantic_authority_fingerprint] = decision.fingerprint or ""
            return SubjectDecision(Action.PROCEED, path)
        if decision.invalid:
            if self.allowance.take_regeneration(decision, subject_now):
                return SubjectDecision(Action.REGENERATE_VERIFIER, path)
            return SubjectDecision(Action.NEEDS_REVIEW, path, REASON_REGENERATION_SPENT,
                                   "this verifier was already regenerated once in this run; there is no second regeneration")
        return SubjectDecision(Action.NEEDS_REVIEW, path, REASON_CANNOT_DECIDE, "the person recorded that they cannot decide")

    async def settle(self, subject_now: Callable[[], Optional[VerifierSemanticSubject]], callback: Any, *, attended: bool) -> SubjectDecision:
        """The whole decision for one subject: existing authority, else (attended) one semantic review, applied to the subject recomputed after the wait.
        ``subject_now`` returns None when the subject no longer exists as shown."""
        subject = subject_now()
        if subject is None:
            return SubjectDecision(Action.NEEDS_REVIEW, "", REASON_DECISION_STALE, "the verifier under review is no longer the one the gate ran")
        first = self.before_asking(subject, attended=attended)
        if first.action is not Action.ASK:
            return first
        decision = await ask_verifier_semantic(callback, subject)
        current = subject_now()
        if current is None:
            return SubjectDecision(Action.NEEDS_REVIEW, _subject_path(subject), REASON_DECISION_STALE,
                                   "the verifier under review changed while it was being decided")
        return self.after_decision(current, decision)


def _subject_path(subject: VerifierSemanticSubject) -> str:
    return subject.verifier_files[0][0] if subject.verifier_files else ""


def needs_review_message(decision: SubjectDecision) -> str:
    """The typed stop's message: the prefix the terminal category keys on, the closed reason code, the verifier path."""
    return (f"{VERIFIER_SEMANTIC_AUTHORITY_REQUIRED}: {decision.reason_code}: the generated verifier {decision.path or '(no exact subject)'} has no "
            f"semantic authority ({decision.detail}). A generated verifier's pass or failure is candidate evidence, never the truth about the contract; "
            "nothing was changed and nothing was applied. The run stops for review.")


# ----------------------------------------------------------------------------- production owner after expectation_valid

@dataclass(frozen=True)
class OwnerAuthority:
    """Whether a failure of an ACCEPTED verifier may select a production repair owner. ``owner_id`` is set only for OWNER_SELECTED."""

    reason_code: str
    owner_id: Optional[str] = None
    files: Tuple[str, ...] = ()

    @property
    def selected(self) -> bool:
        return self.reason_code == OWNER_SELECTED and self.owner_id is not None


def production_owner_authority(
    *, executed_failure: bool, located_paths: Sequence[str], verifier_paths: Iterable[str],
    is_workspace_artifact: Callable[[str], bool], owners_of: Callable[[str], Sequence[str]], conflicting_owner_ids: Iterable[str] = (),
) -> OwnerAuthority:
    """The stricter predicate of the generated-verifier path (WS3-C ruling 3). A locator alone is grounding, never owner authority. A production owner is
    selected only when: typed evidence proves an executed verifier failure; the deterministic locator names at least one concrete workspace artifact that
    is not a verifier; every such artifact maps through the validated plan's ownership to the SAME single owner; and no other deterministic attribution
    evidence names a different owner. Anything else selects nobody.

    ``located_paths``: workspace-relative paths the deterministic locator parsed from the gate's actual runtime/compiler evidence (never a model's
    diagnosis, never a file-name guess). ``owners_of(path)``: the plan unit ids that own it. ``conflicting_owner_ids``: owners named by other
    deterministic attribution evidence."""
    if not executed_failure:
        return OwnerAuthority(OWNER_NO_EXECUTED_FAILURE)
    located = [normalize_path(path) for path in located_paths if path]
    if not located:
        return OwnerAuthority(OWNER_LOCATOR_ABSENT)
    verifiers = {normalize_path(path) for path in verifier_paths}
    production = sorted({path for path in located if path not in verifiers})
    if not production:
        return OwnerAuthority(OWNER_LOCATOR_VERIFIER_ONLY)
    artifacts = [path for path in production if is_workspace_artifact(path)]
    if not artifacts:
        return OwnerAuthority(OWNER_LOCATOR_EXTERNAL)
    owners: Dict[str, List[str]] = {}
    for path in artifacts:
        found = sorted(set(owners_of(path) or ()))
        if not found:
            return OwnerAuthority(OWNER_UNOWNED, files=(path,))
        if len(found) > 1:
            return OwnerAuthority(OWNER_AMBIGUOUS, files=(path,))
        owners.setdefault(found[0], []).append(path)
    if len(owners) != 1:
        return OwnerAuthority(OWNER_AMBIGUOUS, files=tuple(artifacts))
    owner_id = next(iter(owners))
    if any(other != owner_id for other in conflicting_owner_ids):
        return OwnerAuthority(OWNER_CONFLICTING, files=tuple(artifacts))
    return OwnerAuthority(OWNER_SELECTED, owner_id=owner_id, files=tuple(owners[owner_id]))


__all__ = [
    "Action", "ExecutedVerifier", "OwnerAuthority", "SubjectDecision", "VerifierDerivation", "VerifierSemanticLedger", "derive_executed_verifiers",
    "needs_review_message", "normalize_path", "production_owner_authority", "is_test_source_compile_without_subject",
    "VERIFIER_SEMANTIC_AUTHORITY_REQUIRED", "VERIFIER_SEMANTIC_STOP_TYPE", "REVIEW_REASON_CODES", "OWNER_REASON_CODES",
]
