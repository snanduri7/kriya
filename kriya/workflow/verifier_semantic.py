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
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from kriya.code_intel.service import CandidateOverlay
from kriya.policy.approval import (
    REVIEW_PHASE_FAILING,
    REVIEW_PHASE_PASSING,
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
REASON_REGENERATION_UNAVAILABLE = "VERIFIER_SEMANTIC_REGENERATION_UNAVAILABLE"          # no exact-path, candidate-preserving regeneration here
REASON_REGENERATION_NOT_ADMITTED = "VERIFIER_SEMANTIC_REGENERATION_NOT_ADMITTED"        # the retry policy admits no targeted attempt for it
REASON_REGENERATION_NOT_EXECUTED = "VERIFIER_SEMANTIC_REGENERATION_NOT_EXECUTED"        # the one answer produced no new executed verifier
REASON_NO_PRODUCTION_OWNER = "VERIFIER_SEMANTIC_NO_PRODUCTION_OWNER_AUTHORITY"
REASON_FULL_REGENERATION_REFUSED = "VERIFIER_SEMANTIC_FULL_REGENERATION_REFUSED"      # the next attempt would regenerate a verifier that may not change
REASON_RATIONALE_REQUIRED = "VERIFIER_SEMANTIC_RATIONALE_REQUIRED"                      # expectation_invalid without what is invalid and why: no blind regeneration
REVIEW_REASON_CODES = (REASON_UNATTENDED, REASON_NO_DECISION, REASON_CANNOT_DECIDE, REASON_DECISION_STALE, REASON_REGENERATION_SPENT,
                       REASON_SUBJECT_AMBIGUOUS, REASON_SUBJECT_UNREADABLE, REASON_REGENERATION_UNAVAILABLE, REASON_REGENERATION_NOT_ADMITTED,
                       REASON_REGENERATION_NOT_EXECUTED, REASON_NO_PRODUCTION_OWNER, REASON_FULL_REGENERATION_REFUSED, REASON_RATIONALE_REQUIRED)

# Why a production owner was or was not selected after an accepted verifier failed (closed table).
OWNER_SELECTED = "PRODUCTION_OWNER_SELECTED"
OWNER_NO_EXECUTED_FAILURE = "PRODUCTION_OWNER_NO_EXECUTED_FAILURE"
OWNER_LOCATOR_ABSENT = "PRODUCTION_OWNER_LOCATOR_ABSENT"
OWNER_LOCATOR_AMBIGUOUS = "PRODUCTION_OWNER_LOCATOR_AMBIGUOUS"       # a locator matches more than one workspace member: it identifies none
OWNER_LOCATOR_VERIFIER_ONLY = "PRODUCTION_OWNER_LOCATOR_VERIFIER_ONLY"
OWNER_LOCATOR_EXTERNAL = "PRODUCTION_OWNER_LOCATOR_NOT_A_WORKSPACE_ARTIFACT"
OWNER_UNOWNED = "PRODUCTION_OWNER_ARTIFACT_HAS_NO_PLAN_OWNER"
OWNER_AMBIGUOUS = "PRODUCTION_OWNER_AMBIGUOUS"
OWNER_CONFLICTING = "PRODUCTION_OWNER_CONFLICTING_EVIDENCE"
OWNER_REASON_CODES = (OWNER_SELECTED, OWNER_NO_EXECUTED_FAILURE, OWNER_LOCATOR_ABSENT, OWNER_LOCATOR_AMBIGUOUS, OWNER_LOCATOR_VERIFIER_ONLY, OWNER_LOCATOR_EXTERNAL, OWNER_UNOWNED,
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
    files whose bytes could not be read, decoded or cleanly parsed, so what they declare is unknown. ``unmapped`` (S5, GAP-A2-02): executed identities
    that map to no run-authored file exactly and that a run-authored file may nevertheless declare (``_may_declare``); "no mapped file" is not the
    fact "the run did not write this test"."""

    verifiers: Tuple[ExecutedVerifier, ...] = ()
    complete: bool = False
    ambiguous: Tuple[str, ...] = ()
    unreadable: Tuple[str, ...] = ()
    unmapped: Tuple[str, ...] = ()

    @property
    def failing(self) -> Tuple[ExecutedVerifier, ...]:
        return tuple(verifier for verifier in self.verifiers if verifier.failed_identities)

    @property
    def undetermined(self) -> bool:
        """An executed identity could belong to a run-authored file that cannot be told apart or read: no subject may be invented, and none ruled out."""
        return bool(self.ambiguous or self.unreadable or self.unmapped)


def _dotted_tail(longer: str, shorter: str) -> bool:
    """``shorter`` is ``longer`` minus leading dotted segments (a runner rooted above or below the structural module root)."""
    return longer != shorter and longer.endswith("." + shorter)


def _may_declare(identity: str, callable_keys: Iterable[str], opaque_paths: Iterable[str]) -> bool:
    """Could a run-authored file declare the executed ``identity`` although no callable of it carries that exact identity? Two measured causes
    (A2 probes P5, P7): (1) the runner and the structural parser root the module differently (a ``src/`` layout), so one name is a dotted tail of
    the other; (2) the file is in a language with no structural grammar, so nothing it declares is known, and its path spelled as a dotted name
    ends with the identity's leading segments. Either makes the provenance UNKNOWN; it never makes the file a subject."""
    if any(_dotted_tail(identity, key) or _dotted_tail(key, identity) for key in callable_keys):
        return True
    segments = identity.split(".")
    for path in opaque_paths:
        module = os.path.splitext(path)[0].split("/")
        if any(segments[:size] == module[-size:] for size in range(1, min(len(segments) - 1, len(module)) + 1)):
            return True
    return False


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
    callable_keys: List[str] = []   # every callable a parsed run-authored file declares, executed under that exact identity or not
    opaque_paths: List[str] = []    # run-authored paths the structural parser has no grammar for
    for raw_path in sorted(authored):
        path = normalize_path(raw_path)
        data = authored[raw_path]
        try:
            # RETRIEVAL's public seam (kriya.code_intel.service): an in-memory overlay of this one file, parsed by the structural parser. Nothing
            # is indexed or written. One overlay per file, so a file the parser cannot handle never hides what another declares. A path the
            # parser has no grammar for gets no structure: it declares no test callable Kriya can map. Empty bytes stand in for an unreadable
            # file only to ask that question.
            structure = CandidateOverlay({path: data if data is not None else b""}).structures.get(path)
            if structure is None:
                opaque_paths.append(path)
                continue
            if data is None:
                raise ValueError("unreadable")
            text = data.decode("utf-8")
            if getattr(structure, "error_count", 0):
                raise ValueError("parse errors")
        except Exception:  # what the file declares is unknown: reported, never guessed
            unreadable.append(path)
            continue
        sources[path] = (data, text)
        for symbol in structure.symbols:
            if symbol.is_callable:
                callable_keys.append(symbol.lookup_key)
            if symbol.is_callable and symbol.lookup_key in executed:
                declared.setdefault(symbol.lookup_key, []).append(path)
    ambiguous = sorted(identity for identity, paths in declared.items() if len(set(paths)) > 1)
    # S5 (GAP-A2-02): an executed identity with no exact mapping is NOT thereby a pre-existing test. Fail closed where a run-authored file may
    # declare it; an identity no run-authored file can account for stays what it was (a repository test: the historical path).
    unmapped = sorted(identity for identity in executed - set(declared) if _may_declare(identity, callable_keys, opaque_paths))
    by_file: Dict[str, List[str]] = {}
    for identity, paths in declared.items():
        if len(set(paths)) == 1:
            by_file.setdefault(paths[0], []).append(identity)
    verifiers = tuple(
        ExecutedVerifier(path=path, sha256=hashlib.sha256(sources[path][0]).hexdigest(), source=sources[path][1],
                         identities=tuple(sorted(identities)), failed_identities=tuple(sorted(i for i in identities if i in failed)))
        for path, identities in sorted(by_file.items()))
    return VerifierDerivation(verifiers=verifiers, complete=True, ambiguous=tuple(ambiguous), unreadable=tuple(unreadable), unmapped=tuple(unmapped))


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
    rationale: Optional[str] = None              # set for REGENERATE_VERIFIER: the person's bounded rationale (VERIFIER-REGENERATION-BLIND-001)


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
            # VERIFIER-REGENERATION-BLIND-001: a regeneration with nothing to correct is not performed. Decided BEFORE the allowance is taken, so an
            # interface that cannot say what is invalid spends nothing; nothing is synthesized in its place.
            if not decision.rationale:
                return SubjectDecision(Action.NEEDS_REVIEW, path, REASON_RATIONALE_REQUIRED,
                                       "the expectation was declared invalid without saying what is invalid and why; a regeneration with nothing to "
                                       "correct is not performed")
            if self.allowance.take_regeneration(decision, subject_now):
                return SubjectDecision(Action.REGENERATE_VERIFIER, path, rationale=decision.rationale)
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
    return (f"{VERIFIER_SEMANTIC_AUTHORITY_REQUIRED}: {decision.reason_code}: generated verifier {decision.path or '(no exact subject)'}: "
            f"{decision.detail}. A generated verifier's pass or failure is candidate evidence, never the truth about the contract; "
            "nothing further was changed and nothing was applied. The run stops for review.")


# ----------------------------------------------------------------------------- production owner after expectation_valid

@dataclass(frozen=True)
class OwnerAuthority:
    """Whether a failure of an ACCEPTED verifier may select a production repair owner. ``owner_id`` is set only for OWNER_SELECTED."""

    reason_code: str
    owner_id: Optional[str] = None
    files: Tuple[str, ...] = ()
    # VERIFIER-FAILURE-OWNER-CORROBORATION-001: True when the gate's locator named only the verifier and the owner was selected from
    # corroborated source and plan evidence instead. Such a selection is bounded repair ELIGIBILITY, never a located root cause.
    corroborated: bool = False

    @property
    def selected(self) -> bool:
        return self.reason_code == OWNER_SELECTED and self.owner_id is not None


def production_owner_authority(
    *, executed_failure: bool, located_paths: Sequence[str], verifier_paths: Iterable[str],
    is_workspace_artifact: Callable[[str], bool], owners_of: Callable[[str], Sequence[str]], conflicting_owner_ids: Iterable[str] = (),
    ambiguous_locators: Iterable[str] = (), corroborated_paths: Sequence[str] = (),
) -> OwnerAuthority:
    """The stricter predicate of the generated-verifier path (WS3-C ruling 3). A locator alone is grounding, never owner authority. A production owner is
    selected only when: typed evidence proves an executed verifier failure; the deterministic locator names at least one concrete workspace artifact that
    is not a verifier; every such artifact maps through the validated plan's ownership to the SAME single owner; and no other deterministic attribution
    evidence names a different owner. Anything else selects nobody.

    ``located_paths``: CANONICAL workspace-relative member paths the gate's own locators UNIQUELY identify (``failure_grounding.
    resolve_unique_locator_artifacts``: never a model's diagnosis, never every file that shares a basename). ``ambiguous_locators``: locators that
    match more than one workspace member; one is enough to select nobody, because what the failure points at is then not known. ``owners_of(path)``: the plan unit ids that own it. ``conflicting_owner_ids``: owners named by other
    deterministic attribution evidence."""
    if not executed_failure:
        return OwnerAuthority(OWNER_NO_EXECUTED_FAILURE)
    if any(True for _locator in ambiguous_locators):
        return OwnerAuthority(OWNER_LOCATOR_AMBIGUOUS)
    located = [normalize_path(path) for path in located_paths if path]
    if not located:
        return OwnerAuthority(OWNER_LOCATOR_ABSENT)
    verifiers = {normalize_path(path) for path in verifier_paths}
    production = sorted({path for path in located if path not in verifiers})
    corroborated = False
    if not production:
        # VERIFIER-FAILURE-OWNER-CORROBORATION-001 (Stage C run 20261119T174636-1a504074; owner ruling 2026-11-19).
        # OLD ASSUMPTION: a production owner can be selected only from a locator that names a production artifact. NEW EVIDENCE: the
        # commonest failure of a valid test is an assertion on a returned value, whose stack names the test alone; in the sealed run
        # a person had confirmed the expectation, the defect was real, and the run had no repair path. REQUIRED CORRECTION: when the
        # locator names ONLY the accepted verifier, the caller's corroborated artifacts stand in for the missing locator - production
        # files the verifier's source calls into AND that the validated plan puts in a direct dependency of the verifier's unit.
        # Neither evidence alone selects anyone (a dependency the verifier does not call, or a call into a file that is no direct
        # dependency, corroborates nothing), and everything below is unchanged: the artifacts must be workspace members with exactly
        # ONE plan owner between them, with no conflicting evidence; anything else selects nobody. What is selected is the bounded
        # repair candidate, not a proven root cause: the unchanged verifier, run again after the repair, is the judge.
        production = sorted({normalize_path(path) for path in corroborated_paths if path} - verifiers)
        if not production:
            return OwnerAuthority(OWNER_LOCATOR_VERIFIER_ONLY)
        corroborated = True
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
    return OwnerAuthority(OWNER_SELECTED, owner_id=owner_id, files=tuple(owners[owner_id]), corroborated=corroborated)


# ----------------------------------------------------------------------------- one unit invocation (what the loop seams call)

# The failure a unit's pass seam raises when a person declares a PASSING generated verifier invalid: the candidate is not accepted and the retry loop's
# failure seam starts the verifier's one regeneration. It is not a stop.
REGENERATE_FAILURE_TYPE = "verifier_semantic_expectation_invalid"
_OUTCOME_MAX_CHARS = 4000


@dataclass(frozen=True)
class GateRuling:
    """What a seam must do. ``stop``: the needs-review decision (the unit stops typed; nothing may be changed). ``regenerate``: the verifier path whose
    ONE regeneration is the next generation. ``owner``: after expectation_valid, the strict production-owner result (its files are the only repair
    targets). All None/empty = the gate involved no generated-verifier subject that needs anything: the seam's historical behaviour is unchanged."""

    stop: Optional[SubjectDecision] = None
    regenerate: Optional[str] = None
    owner: Optional[OwnerAuthority] = None
    rationale: Optional[str] = None      # with ``regenerate``: the person's rationale for exactly that verifier's bytes (evidence, shown in the ruling event)

    @property
    def unchanged(self) -> bool:
        return self.stop is None and self.regenerate is None and self.owner is None

    @property
    def stop_message(self) -> Optional[str]:
        return needs_review_message(self.stop) if self.stop is not None else None


@dataclass(frozen=True)
class VerifierRegenerationRequest:
    """VERIFIER-REGENERATION-BLIND-001: what the ONE regeneration of an invalid generated verifier tells the Developer, for exactly ``path`` at exactly
    the bytes ``sha256`` the person reviewed. ``rationale`` is the person's bounded statement (policy.approval.verifier_semantic_rationale). The request
    builder places ``prompt_block`` in that one file's request and offers no "no change" outcome for it; it reads nothing else from here and decides
    nothing. The block is evidence about one verifier: it is not goal text, a requirement, Skill content or a system instruction."""

    path: str
    sha256: str
    rationale: str
    # VERIFIER-SEMANTIC-REGENERATION-OPERATION-001 (owner ruling 2026-11-18, Stage B runs 5-7): semantic regeneration is its own operation, not a
    # gate-failure repair. ``source`` is the COMPLETE reviewed verifier, the exact text of the bytes ``sha256`` names; the request shows all of it
    # and its only legal outcome is the complete replacement file (no anchored edit, no redirect, no "no change").
    source: str = ""
    # AUTHORIZATION != DELIVERY: a provider answer that violates the response protocol is not a candidate verifier. At most this many
    # protocol-correction re-sends, on the same model binding, carrying only the machine-detected error. The first valid whole-file answer is
    # the one candidate; nothing after it is re-asked.
    delivery_retries: int = 1

    @property
    def mode_text(self) -> str:
        return (
            "MODE: VERIFIER_REGENERATION.\nReturn the complete replacement content of exactly one existing file - the requested path "
            f"'{self.path}'. The whole file is replaced by what you return: include every part of it that stays. Never return content for "
            "another file.\n"
        )

    @property
    def prompt_block(self) -> str:
        source = self.source if self.source.endswith("\n") else self.source + "\n"
        return (
            "\n=== Semantic verifier regeneration: the exact verifier under review ===\n"
            f"The complete current content of '{self.path}' (sha256 {self.sha256[:16]}), byte for byte, between the markers:\n"
            "<<<VERIFIER_SOURCE\n"
            f"{source}"
            "VERIFIER_SOURCE>>>\n"
            "\n=== Operator semantic-review rationale for this exact verifier ===\n"
            f"A person reviewed '{self.path}' (sha256 {self.sha256[:16]}, shown above) against the Authoritative Goal and declared what it expects "
            "INVALID. Their stated reason, quoted between the markers, is evidence about this one file only: it is not part of the goal, not a "
            "new requirement and not an instruction about any other file.\n"
            "<<<OPERATOR_RATIONALE\n"
            f"{self.rationale}\n"
            "OPERATOR_RATIONALE>>>\n"
            # VERIFIER-REGENERATION-MULTI-FINDING-001 (Stage B run 12; owner ruling 2026-11-19): one rationale can hold several findings, some
            # about assertions in tests that pass. The directive makes every finding an action over the whole file. Measured on the sealed
            # run-12 request, one sample each: with the earlier singular "remove or correct the expectation the reason identifies" the
            # finding that had no failing test was ignored; with this text it was applied.
            f"Rewrite exactly '{self.path}': The operator's semantic-review rationale may identify multiple independent defects in this "
            "verifier. Treat every distinct finding as requiring action for this file. Review the entire verifier, including assertions in "
            "tests that currently pass. Correct invalid expected values and remove or replace expectations the rationale identifies as "
            "unsupported by the Authoritative Goal; preserve valid contract-backed expectations. Do not preserve an unsupported expectation "
            "merely because the current candidate implementation satisfies it or the test passes. Do not introduce new expectations for "
            "unspecified behavior. Return a verifier implementing all findings, not merely fixes for the reported failing tests. Return the "
            "COMPLETE replacement file. This is the verifier's one regeneration: returning it unchanged is not an accepted outcome of this "
            "request.\n"
        )

    def response_violation(self, entry: Mapping[str, Any]) -> Optional[str]:
        """Why a Developer answer is not a candidate verifier under this operation's response protocol (None = it is one: a complete file and
        nothing else). The text is the machine-detected error only; it is what a protocol-correction re-send carries."""
        if entry.get("protocol_error"):
            return f"the response did not follow the response protocol: {entry['protocol_error']}"
        if entry.get("edits"):
            return (f"the response was an anchored EDIT; this request accepts only one complete <<<KRIYA:FILE path=\"{self.path}\">>> block "
                    "holding the whole replacement file")
        if entry.get("content") is None:
            return (f"the response returned no file content (a \"no change\" answer is not an outcome of this request); it accepts only one "
                    f"complete <<<KRIYA:FILE path=\"{self.path}\">>> block holding the whole replacement file")
        return None

    def correction_block(self, violation: str) -> str:
        return (
            "\n=== Response protocol correction ===\n"
            f"Your previous response to this same request was not accepted, for this machine-detected reason only: {violation}\n"
            f"Answer the same request again, following the RESPONSE PROTOCOL: one complete file block for '{self.path}'.\n"
        )


class UnitSemantics:
    """The generated-verifier semantic state of ONE unit invocation, over the run's one ledger. The loop seams hand it facts (the test gates' result
    dicts, the unit's authored bytes, the gate failure) and obey the ``GateRuling`` it returns; they hold no policy of their own.

    ``regenerable``: the invocation runs under a validated plan with an exact allow-list write scope, so the write scope of one attempt can be the one
    verifier path while the rest of the candidate stays in the tree read-only. A direct run has neither: expectation_invalid is needs-review there."""

    def __init__(self, *, ledger: VerifierSemanticLedger, run_id: str, plan_hash: str, unit_id: str, goal: str,
                 verification_contract_digest: Optional[str] = None, acceptance_criteria: Sequence[Tuple[str, str]] = (),
                 invariants: Sequence[Tuple[str, str]] = (), regenerable: bool = False, attended: bool = False, callback: Any = None,
                 read_bytes: Optional[Callable[[str], Optional[bytes]]] = None) -> None:
        self.ledger, self.run_id, self.plan_hash, self.unit_id, self.goal = ledger, run_id, plan_hash, unit_id, goal
        self.verification_contract_digest = verification_contract_digest
        self.acceptance_criteria, self.invariants = tuple(acceptance_criteria), tuple(invariants)
        self.regenerable, self.attended, self.callback, self.read_bytes = regenerable, attended, callback, read_bytes
        self.results: List[Mapping[str, Any]] = []           # the test gates' result dicts of the CURRENT attempt, as the gates returned them
        self.denied: set = set()                             # verifier paths no later generation of this invocation may write
        self.pending: Optional[Tuple[str, str]] = None       # (path, sha256 of the invalid bytes): its one regeneration is the NEXT generation
        self.regenerating: Optional[Tuple[str, str]] = None  # the same pair once that generation started, until its outcome is judged
        self.rationales: Dict[Tuple[str, str], str] = {}     # (path, sha256 of the invalid bytes) -> the person's rationale for exactly those bytes
        # VERIFIER-SEMANTIC-REGENERATION-OPERATION-001: delivery and candidate production are counted apart from each other (and from the work
        # grant and the provider requests, which have their own owners).
        self.delivery_retries: int = 0                       # protocol-correction re-sends of the regeneration request
        self.candidates: int = 0                             # valid whole-file answers received: at most one per authorization

    # -- facts in
    def observe(self, result: Any) -> None:
        if isinstance(result, Mapping):
            self.results.append(result)

    def _authored(self, written: Iterable[str]) -> Dict[str, Optional[bytes]]:
        authored: Dict[str, Optional[bytes]] = {}
        for path in written:
            try:
                authored[path] = self.read_bytes(path) if self.read_bytes is not None else None
            except Exception:  # unreadable now: reported by the derivation, never guessed
                authored[path] = None
        return authored

    def _derive(self, written: Iterable[str], results: Optional[Sequence[Mapping[str, Any]]] = None) -> Tuple[List[ExecutedVerifier], List[str]]:
        """(every run-authored executed verifier, why the provenance of an executed case is unknown) over the attempt's gate results. A verifier
        reported by several gates is one entry; it is failing when any gate reported one of its identities failed."""
        authored = self._authored(written)
        merged: Dict[str, ExecutedVerifier] = {}
        unknown: List[str] = []
        for result in (self.results if results is None else results):
            derivation = derive_executed_verifiers(result.get("test_execution"), authored)
            if derivation.complete and any(isinstance(case, Mapping) for case in (result.get("test_execution") or {}).get("test_cases") or ()):
                unknown.extend(f"ambiguous:{identity}" for identity in derivation.ambiguous)
                unknown.extend(f"unreadable:{path}" for path in derivation.unreadable)
                unknown.extend(f"unmapped:{identity}" for identity in derivation.unmapped)
            for verifier in derivation.verifiers:
                known = merged.get(verifier.path)
                merged[verifier.path] = verifier if known is None else ExecutedVerifier(
                    path=verifier.path, sha256=verifier.sha256, source=verifier.source,
                    identities=tuple(sorted(set(known.identities) | set(verifier.identities))),
                    failed_identities=tuple(sorted(set(known.failed_identities) | set(verifier.failed_identities))))
        return [merged[path] for path in sorted(merged)], sorted(set(unknown))

    def _subject(self, verifier: ExecutedVerifier, outcome: str) -> VerifierSemanticSubject:
        failing = bool(verifier.failed_identities)
        return VerifierSemanticSubject(
            run_id=self.run_id, plan_hash=self.plan_hash, verifier_unit_id=self.unit_id, verifier_files=((verifier.path, verifier.sha256),),
            goal_digest=hashlib.sha256((self.goal or "").encode("utf-8", errors="replace")).hexdigest(),
            verification_contract_digest=self.verification_contract_digest, acceptance_criteria=self.acceptance_criteria, invariants=self.invariants,
            review_phase=REVIEW_PHASE_FAILING if failing else REVIEW_PHASE_PASSING,
            failure_identity=(hashlib.sha256("\n".join(verifier.failed_identities).encode("utf-8")).hexdigest() if failing else None),
            contract_text=self.goal, verifier_sources=((verifier.path, verifier.source),), outcome=(outcome or "")[:_OUTCOME_MAX_CHARS])

    def _stop(self, reason_code: str, path: str, detail: str) -> GateRuling:
        return GateRuling(stop=SubjectDecision(Action.NEEDS_REVIEW, path, reason_code, detail))

    def _judge_regeneration(self, verifiers: Sequence[ExecutedVerifier], *, final: bool) -> Optional[GateRuling]:
        """The one regeneration answer was given (or the attempt ended without it). The path is write-denied from here on, whatever happened: there
        is no second regeneration. It must have produced NEW bytes that a test gate executed; ``final`` False = a later gate of this attempt may
        still execute it."""
        if self.regenerating is None:
            return None
        path, old_sha = self.regenerating
        self.denied.add(path)
        now = next((verifier for verifier in verifiers if verifier.path == path), None)
        if now is not None and now.sha256 != old_sha:
            self.regenerating = None
            return None
        if not final and now is None:
            return None
        self.regenerating = None
        return self._stop(REASON_REGENERATION_NOT_EXECUTED, path,
                          "its one regeneration did not produce a new verifier that a test gate executed; there is no second regeneration")

    async def _settle(self, verifier: ExecutedVerifier, outcome: str) -> SubjectDecision:
        def subject_now() -> Optional[VerifierSemanticSubject]:
            try:
                data = self.read_bytes(verifier.path) if self.read_bytes is not None else None
            except Exception:
                data = None
            if data is None or hashlib.sha256(data).hexdigest() != verifier.sha256:
                return None
            return self._subject(verifier, outcome)
        return await self.ledger.settle(subject_now, self.callback, attended=self.attended)

    def _regeneration(self, decision: SubjectDecision, verifier: ExecutedVerifier) -> GateRuling:
        """expectation_invalid: the ledger has spent the subject's one regeneration. It can only be performed where the write scope of one generation
        can be exactly this path with the candidate kept; anywhere else nothing is regenerated."""
        if not self.regenerable:
            self.denied.add(verifier.path)
            return self._stop(REASON_REGENERATION_UNAVAILABLE, decision.path,
                              "the expectation was declared invalid, and this run has no exact-path regeneration that keeps the candidate")
        self.pending = (verifier.path, verifier.sha256)
        if decision.rationale:
            self.rationales[self.pending] = decision.rationale
        return GateRuling(regenerate=verifier.path, rationale=decision.rationale)

    # -- the failure seam (before the ordinary retry may choose targets)
    async def on_gate_failure(self, *, written: Iterable[str], failure_type: str, outcome: str,
                              locate: Callable[[], Tuple[Sequence[str], Sequence[str]]],
                              is_workspace_artifact: Callable[[str], bool], owners_of: Callable[[str], Sequence[str]],
                              corroborate: Optional[Callable[[Sequence[str]], Sequence[str]]] = None) -> GateRuling:
        """``locate``: the gate's locators as (uniquely resolved canonical member paths, ambiguous locators); called only when an accepted
        verifier's failure needs a production owner. ``corroborate`` (VERIFIER-FAILURE-OWNER-CORROBORATION-001): the accepted FAILING
        verifier paths -> the production artifacts both the verifier's source and the validated plan point at; asked only when the
        locators name nothing but verifiers, and absent (None) it corroborates nothing."""
        written = list(written)
        if failure_type == REGENERATE_FAILURE_TYPE and self.pending is not None:
            # decided at the pass seam: the retry loop only has to start it
            return GateRuling(regenerate=self.pending[0], rationale=self.rationales.get(self.pending))
        verifiers, unknown = self._derive(written)
        judged = self._judge_regeneration(verifiers, final=True)
        if judged is not None:
            return judged
        if unknown:
            return self._stop(REASON_SUBJECT_UNREADABLE if unknown[0].startswith("unreadable:") else REASON_SUBJECT_AMBIGUOUS, "",
                              "a test gate executed cases, and whether they belong to a source this run wrote cannot be resolved ("
                              + ", ".join(unknown[:5]) + ")")
        failing = [verifier for verifier in verifiers if verifier.failed_identities]
        if not failing:
            return GateRuling()
        for verifier in failing:
            decision = await self._settle(verifier, outcome)
            if decision.action is Action.NEEDS_REVIEW:
                return GateRuling(stop=decision)
            if decision.action is Action.REGENERATE_VERIFIER:
                return self._regeneration(decision, verifier)
            self.denied.add(verifier.path)                           # expectation_valid: these exact bytes stay as they are
        located_paths, ambiguous_locators = locate()
        verifier_paths = {normalize_path(verifier.path) for verifier in verifiers}
        located = [normalize_path(path) for path in located_paths if path]
        corroborated_paths: Sequence[str] = ()
        if corroborate is not None and located and not list(ambiguous_locators) and all(path in verifier_paths for path in located):
            corroborated_paths = tuple(corroborate([verifier.path for verifier in failing]))
        owner = production_owner_authority(
            executed_failure=True, located_paths=located_paths, verifier_paths=[verifier.path for verifier in verifiers],
            is_workspace_artifact=is_workspace_artifact, owners_of=owners_of, ambiguous_locators=ambiguous_locators,
            corroborated_paths=corroborated_paths)
        if not owner.selected:
            return self._stop(REASON_NO_PRODUCTION_OWNER, failing[0].path,
                              f"the expectation is valid, and no production owner has authority to be repaired from this failure: {owner.reason_code}")
        return GateRuling(owner=owner)

    # -- the pass seams (before terminal regression; after it, before apply)
    async def on_gates_passed(self, *, written: Iterable[str], final: bool) -> GateRuling:
        written = list(written)
        verifiers, unknown = self._derive(written)
        judged = self._judge_regeneration(verifiers, final=final)
        if judged is not None:
            return judged
        if unknown:
            return self._stop(REASON_SUBJECT_UNREADABLE if unknown[0].startswith("unreadable:") else REASON_SUBJECT_AMBIGUOUS, "",
                              "a test gate executed cases, and whether they belong to a source this run wrote cannot be resolved ("
                              + ", ".join(unknown[:5]) + ")")
        for verifier in verifiers:
            decision = await self._settle(verifier, "")
            if decision.action is Action.NEEDS_REVIEW:
                return GateRuling(stop=decision)
            if decision.action is Action.REGENERATE_VERIFIER:
                return self._regeneration(decision, verifier)
        return GateRuling()

    # -- the side routes
    def autonomous_rewrite_permitted(self, written: Iterable[str], result: Any) -> bool:
        """May the targeted-test self-correction loop rewrite files from this failed test result? Never while a regeneration answer is being
        judged, and never when the result identifies a failing run-authored verifier or leaves an executed case's provenance unknown: that
        decision is the failure seam's. (The compile gate's loop needs no guard: a semantic decision exists only in an attended run, whose
        executing gates run behind the source review with every self-correction loop off.)"""
        if self.regenerating is not None or self.pending is not None:
            return False
        if not isinstance(result, Mapping):
            return True
        verifiers, unknown = self._derive(written, [result])
        return not unknown and not any(verifier.failed_identities for verifier in verifiers)

    def unscoped_owner_refusal(self, ruling: GateRuling) -> GateRuling:
        """A selected production owner in an invocation whose write scope is not an allow-list: the accepted verifier could not be
        write-denied while production is repaired, so nobody is repaired."""
        files = ", ".join(ruling.owner.files) if ruling.owner is not None else ""
        return self._stop(REASON_NO_PRODUCTION_OWNER, "", f"the expectation is valid, and this run has no exact write scope under which {files} "
                          "could be repaired with the verifier write-denied")

    def regeneration_admitted(self, admitted: bool) -> Optional[GateRuling]:
        """After the failed attempt was charged: the retry policy's own decision for the next attempt. A pending regeneration that is not admitted
        as a targeted attempt is never forced (no budget is reset): the path is closed and the unit stops for review."""
        if self.pending is None or admitted:
            return None
        path, _sha = self.pending
        self.pending = None
        self.denied.add(path)
        return self._stop(REASON_REGENERATION_NOT_ADMITTED, path,
                          "the expectation was declared invalid, and the retry policy admits no targeted attempt to regenerate it")

    def regeneration_not_dispatched(self) -> Optional[str]:
        """VERIFIER-REGENERATION-SPENT-BY-REFUSAL-001: the attempt that started the one regeneration ended before any provider saw its request (the
        caller's fact: a pre-provider refusal of the model candidate). Nothing was regenerated, so there is nothing to judge: the SAME (path, bytes)
        is pending again for the next attempt, and the person is not asked again. Only while the verifier is still the bytes the decision was made
        for; otherwise nothing is carried and the in-flight regeneration is left for the existing judgment. Returns the carried path, else None.
        A regeneration a provider answered is never returned here: it is judged by ``_judge_regeneration`` and stays the only one."""
        if self.regenerating is None:
            return None
        path, sha = self.regenerating
        try:
            data = self.read_bytes(path) if self.read_bytes is not None else None
        except Exception:  # unreadable now: not the bound subject, never guessed
            data = None
        if data is None or hashlib.sha256(data).hexdigest() != sha:
            return None
        self.pending, self.regenerating = self.regenerating, None
        return path

    def regeneration_request(self, targets: Optional[Iterable[str]]) -> Optional[VerifierRegenerationRequest]:
        """VERIFIER-REGENERATION-BLIND-001: what the generation that IS the in-flight regeneration tells the Developer - only when that generation's
        targets are exactly the regenerating verifier, and only with the rationale recorded for exactly its (path, bytes). Any other generation of
        the unit (another file, a full-set attempt, the investigation, a later repair) gets None: no generic request carries the rationale."""
        if self.regenerating is None:
            return None
        path, sha = self.regenerating
        if [normalize_path(target) for target in (targets or ())] != [path]:
            return None
        rationale = self.rationales.get((path, sha))
        if not rationale:
            return None
        # the COMPLETE exact verifier, read now and held to the reviewed bytes: anything else is not the subject of the decision
        try:
            data = self.read_bytes(path) if self.read_bytes is not None else None
            source = data.decode("utf-8") if data is not None and hashlib.sha256(data).hexdigest() == sha else None
        except Exception:  # unreadable or not text now: never guessed
            source = None
        return VerifierRegenerationRequest(path, sha, rationale, source) if source else None

    def regeneration_in_flight(self, targets: Optional[Iterable[str]]) -> bool:
        """Is a generation with these targets the in-flight regeneration? (True with no ``regeneration_request`` = its exact subject cannot be
        shown: the caller must not send a generic repair in its place.)"""
        return self.regenerating is not None and [normalize_path(target) for target in (targets or ())] == [self.regenerating[0]]

    def note_delivery(self, entry: Mapping[str, Any]) -> Dict[str, Any]:
        """Count what one regeneration answer was, from the Developer's own result entry; returns the evidence of it."""
        retry = entry.get("regeneration_delivery_retry")
        if retry:
            self.delivery_retries += 1
        produced = entry.get("content") is not None and not entry.get("protocol_error") and not entry.get("edits")
        if produced:
            self.candidates += 1
        return {"delivery_retry": dict(retry) if retry else None, "candidate_produced": produced,
                "delivery_retries": self.delivery_retries, "candidates": self.candidates,
                "candidate_sha256": hashlib.sha256(entry["content"].encode("utf-8")).hexdigest() if produced else None}

    def full_regeneration_refusal(self) -> GateRuling:
        """A verifier of this invocation may no longer be written (accepted, or its one regeneration is used), and the retry policy's next
        attempt would regenerate the unit's whole file set. That is neither a strict production-owner repair nor the one regeneration."""
        path = sorted(self.denied)[0] if self.denied else ""
        return self._stop(REASON_FULL_REGENERATION_REFUSED, path,
                          "the retry policy's next attempt would regenerate the whole unit, including a verifier that may not be changed")

    # -- the write scope of ONE attempt
    def attempt_scope(self, ctx: Any, allowlist_mode: Any) -> "_AttemptScope":
        return _AttemptScope(self, ctx, allowlist_mode)


@dataclass(frozen=True)
class RunSemantics:
    """What the run's controller hands every unit invocation: the run's ONE ledger and the run-level bindings of a semantic review."""

    ledger: VerifierSemanticLedger
    plan_hash: str
    verification_contract_digest: Optional[str]
    goal: str


NO_STRUCTURED_PLAN = "NO_STRUCTURED_PLAN"   # the plan binding of a run that has no validated plan (a direct run): it owns no file and regenerates nothing


def unit_semantics(run: Optional[RunSemantics], *, run_id: str, unit_id: Optional[str], goal: str, structured_plan: Any, allowlist_scope: bool,
                   attended: bool, callback: Any, read_bytes: Callable[[str], Optional[bytes]]) -> UnitSemantics:
    """The semantic state of one unit invocation. Under a controller (``run``) with a validated plan and an allow-list scope the unit can regenerate
    one verifier path in place; a direct run gets a ledger of its own, no plan binding and no regeneration."""
    criteria: List[Tuple[str, str]] = []
    invariants: List[Tuple[str, str]] = []
    subtask = next((item for item in getattr(structured_plan, "subtasks", None) or () if item.id == unit_id), None)
    if subtask is not None:
        wanted_criteria, wanted_invariants = set(subtask.acceptance_criteria_ids), set(subtask.relevant_global_invariant_ids)
        criteria = [(item.id, item.description) for item in structured_plan.acceptance_criteria if item.id in wanted_criteria]
        invariants = [(item.id, item.statement) for item in structured_plan.global_invariants if item.id in wanted_invariants]
    return UnitSemantics(
        ledger=run.ledger if run is not None else VerifierSemanticLedger(), run_id=run_id,
        plan_hash=run.plan_hash if run is not None else NO_STRUCTURED_PLAN, unit_id=unit_id or run_id,
        goal=run.goal if run is not None else goal, verification_contract_digest=run.verification_contract_digest if run is not None else None,
        acceptance_criteria=criteria, invariants=invariants,
        regenerable=bool(run is not None and subtask is not None and allowlist_scope), attended=attended, callback=callback, read_bytes=read_bytes)


class _AttemptScope:
    """The write scope of exactly one attempt, restored on every exit (return, failure, timeout, cancellation). A pending regeneration makes the
    scope exactly its verifier path; otherwise an allow-list scope loses the write-denied verifier paths. The attempt context's own scope is never
    left changed, so no later attempt, unit or recovery inherits it."""

    def __init__(self, unit: UnitSemantics, ctx: Any, allowlist_mode: Any) -> None:
        self.unit, self.ctx, self.allowlist_mode = unit, ctx, allowlist_mode
        self.saved: Optional[Tuple[List[str], Any]] = None

    def __enter__(self) -> "_AttemptScope":
        unit, ctx = self.unit, self.ctx
        self.saved = (ctx.allowed_write_relpaths, ctx.write_scope_mode)
        unit.results = []
        if unit.pending is not None:
            unit.regenerating, unit.pending = unit.pending, None
            ctx.allowed_write_relpaths, ctx.write_scope_mode = [unit.regenerating[0]], self.allowlist_mode
        elif unit.denied and ctx.write_scope_mode == self.allowlist_mode:
            ctx.allowed_write_relpaths = [path for path in self.saved[0] if normalize_path(path) not in unit.denied]
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.ctx.allowed_write_relpaths, self.ctx.write_scope_mode = self.saved


__all__ = [
    "Action", "ExecutedVerifier", "OwnerAuthority", "SubjectDecision", "VerifierDerivation", "VerifierSemanticLedger", "derive_executed_verifiers",
    "needs_review_message", "normalize_path", "production_owner_authority", "UnitSemantics", "GateRuling", "REGENERATE_FAILURE_TYPE",
    "VerifierRegenerationRequest", "REASON_RATIONALE_REQUIRED",
    "VERIFIER_SEMANTIC_AUTHORITY_REQUIRED", "VERIFIER_SEMANTIC_STOP_TYPE", "REVIEW_REASON_CODES", "OWNER_REASON_CODES",
]
