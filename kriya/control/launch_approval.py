"""Human review of a change inside a desktop launch (GUI final; D2 M2).

Kriya's change-approval gate is an in-process callback (``approval_callback``): returning True continues the workflow into its regression gates and
the apply step; a DECLINED answer (a person said no) ends it as ``human_rejected`` and an UNAVAILABLE one (nobody decided) as ``approval_required``
(``kriya.policy.approval.ApprovalOutcome``). That callback stays the ONE continuation point. A desktop launch has no terminal, so
instead of prompting, the callback of ``kriya generate --launch-id`` (kriya/cli.py ``on_approval``):

1. records a PENDING review in the launch record - durable, bound to the launch id, the workspace identity, the exact candidate (a digest over the
   unified diffs the gate was given) and the reviewer text it was given - then offers it to the host over the launch's PRIVATE review channel
   (kriya/control/review_channel.py: an inherited socket, a per-review capability that exists only in this process's memory and in that frame);
2. waits for the host's ``decision`` frame, which carries the review identity and the capability. The waiter validates everything itself (launch, review,
   kind, digest, capability in constant time, expiry, one-shot) and answers with a ``result`` frame; a wrong, missing, replayed or cross-review decision is
   refused without consuming the live capability;
3. records the decision in the launch record BEFORE returning, so the workflow can only continue after the approval is durable. The persisted decision
   file is AUDIT OUTPUT ONLY: it is written after the decision was accepted and is never read as approval authority.

Fail closed everywhere: a launch without a channel (the review is not even offered), a channel that failed closed, a stale or foreign decision, an
expired review, a host that vanished (end of file) - none of them approves. A durable ``reject`` by the person is DECLINED (``human_rejected``); an
expired, abandoned or unrecordable review is UNAVAILABLE (``approval_required``): nobody said no. Nothing here approves anything by itself, passes ``-y``
or changes the autonomy mode; the human decision is the only input.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from kriya.control.persistence import (
    save_launch_review_decision,
)
from kriya.control.review_channel import (
    OUTCOME_DECIDED,
    OUTCOME_EOF,
    OUTCOME_EXPIRED,
    ChannelUnavailable,
)
from kriya.foundation.redaction import redact_text, redact_value
from kriya.policy import (
    approval as _owner,  # the typed ApprovalOutcome and its reason codes (used here, not re-exported)
)

# Moved to AUTHORITY's approval owner (kriya/policy/approval.py) in slice C3, re-exported here as themselves for this module's importers. Removed in P1.
from kriya.policy.approval import KIND_CHANGE as KIND_CHANGE
from kriya.policy.approval import KIND_RUNTIME as KIND_RUNTIME
from kriya.policy.approval import KINDS as KINDS
from kriya.policy.approval import REFUSAL_CODES as REFUSAL_CODES
from kriya.policy.approval import REVIEW_SECTION_HEADER as REVIEW_SECTION_HEADER
from kriya.policy.approval import RUNTIME_COMMAND_REVIEW as RUNTIME_COMMAND_REVIEW
from kriya.policy.approval import WORKING_DIRECTORY_KIND as WORKING_DIRECTORY_KIND
from kriya.policy.approval import ApprovalRefused as ApprovalRefused
from kriya.policy.approval import RuntimeCommandReviewPort as RuntimeCommandReviewPort
from kriya.policy.approval import _sha as _sha
from kriya.policy.approval import candidate_digest as candidate_digest
from kriya.policy.approval import split_reason as split_reason

logger = logging.getLogger(__name__)

REVIEW_ID_RE = re.compile(r"^ra-[0-9a-f]{24}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
DECISIONS = _owner.BINARY_DECISIONS   # the binary kinds' words; validity is per kind (kriya.policy.approval.DECISIONS_BY_KIND)
STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED, STATUS_EXPIRED = "PENDING", "APPROVED", "REJECTED", "EXPIRED"
# VERIFIER-SEMANTIC-REVIEW-001: the one decided status of a verifier SEMANTIC review. It has no polarity: the semantic word itself is the authoritative
# ``decision.decision`` field. APPROVED / REJECTED keep exactly their meaning and are never written for that kind.
STATUS_DECIDED = "DECIDED"
APPROVAL_WAIT_SECONDS = 3600.0   # a review nobody answers ends UNAVAILABLE (approval_required), never left holding the workspace forever
REASON_MAX_CHARS = 60000
HISTORY_MAX = 20

CRITERION_MAX_CHARS = 500


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds")


def review_identity(launch_id: str, workspace_identity_value: str, sequence: int, digest: str, reason: str) -> str:
    material = "\0".join((launch_id, workspace_identity_value, str(sequence), digest, _sha(reason)))
    return "ra-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _diff_counts(content: str) -> Dict[str, int]:
    added = removed = 0
    for line in content.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return {"lines_added": added, "lines_removed": removed}


def build_pending(launch_id: str, workspace_identity_value: str, sequence: int, files: List[Dict[str, str]], reason: str) -> Dict[str, Any]:
    digest = candidate_digest(files)
    requested = _now()
    # S20r (OBS-003, I-9): the STORED reason is display text, redacted BEFORE it is cut; the review identity (``review_id``) and
    # ``reason_sha256`` stay bound to the EXACT reason bytes (OI-146), and ``reason_truncated`` describes the exact reason.
    reason_text = redact_text(reason)[:REASON_MAX_CHARS]
    return {
        "review_id": review_identity(launch_id, workspace_identity_value, sequence, digest, reason), "sequence": sequence, "kind": "change_approval",
        "status": STATUS_PENDING, "requested_at": _stamp(requested), "expires_at": _stamp(requested + timedelta(seconds=APPROVAL_WAIT_SECONDS)),
        "candidate_digest": digest,
        "files": [{"filepath": str(f.get("filepath", "")), "diff_sha256": _sha(str(f.get("content", ""))), **_diff_counts(str(f.get("content", "")))} for f in files],
        "reason": reason_text, "reason_truncated": len(reason) > REASON_MAX_CHARS, "reason_sha256": _sha(reason),
        "decision": None,
    }


def build_pending_runtime(launch_id: str, workspace_identity_value: str, sequence: int, subject: Any,
                          review: RuntimeCommandReviewPort = RUNTIME_COMMAND_REVIEW) -> Optional[Dict[str, Any]]:
    """The pending RUNTIME-COMMAND review: the exact resolved commands, in order, the directory kind and the input channel, bound by ``candidate_digest`` (= the subject's command
    fingerprint). None - the review is not offered - when the commands cannot be shown faithfully: a secret scrub that would alter any argument means what the person would read is not
    what would run. Secret stdin/environment values never enter the record: only ``stdin_present`` / the stdin digest and environment NAMES do. ``review`` is the subject's
    fingerprint/reviewability owner (AUTHORITY's ``kriya.policy.approval``; slice C3: a parameter, not a lazy import of the kernel-side module)."""
    commands = [list(c) for c in subject.commands]
    # S2b: each command is read WHOLE (``--token`` then its value is a secret pair; neither element alone is one). S2d: the
    # same comparison covers a positional secret of a supported command's grammar (``mysql -p<pw>``); it is the rule of
    # ``kriya.policy.approval.commands_carry_secret`` (not imported here: this module re-exports only its moved names).
    if not review.reviewable(subject) or any(redact_value(c) != c for c in commands):
        return None
    digest = review.fingerprint_of(subject.working_directory, subject.commands, subject.input_channel, subject.stdin_sha256)
    requested = _now()
    return {
        "review_id": review_identity(launch_id, workspace_identity_value, sequence, digest, subject.reason), "sequence": sequence, "kind": KIND_RUNTIME,
        "status": STATUS_PENDING, "requested_at": _stamp(requested), "expires_at": _stamp(requested + timedelta(seconds=APPROVAL_WAIT_SECONDS)),
        "candidate_digest": digest, "command_fingerprint": digest, "unit_id": subject.unit_id,
        "criterion": redact_text(subject.criterion)[:CRITERION_MAX_CHARS], "commands": commands,
        "working_directory": subject.working_directory, "working_directory_kind": WORKING_DIRECTORY_KIND, "input_channel": subject.input_channel,
        "stdin_present": subject.stdin_sha256 is not None, "stdin_sha256": subject.stdin_sha256, "environment_names": list(subject.environment_names),
        "reason_sha256": _sha(subject.reason), "decision": None,
    }


OUTCOME_MAX_CHARS = 12000


def build_pending_verifier_repair(launch_id: str, workspace_identity_value: str, sequence: int, subject: Any) -> Optional[Dict[str, Any]]:
    """The pending VERIFIER-DEFECT REPAIR AUTHORIZATION review (owner rulings 2026-11-13; a third subject): the exact verifier source and digest, the canonical
    contract text and digest, both deterministic gate outcomes and every binding, bound by ``candidate_digest`` (= the subject's fingerprint). None - the review is
    not offered - when the subject cannot be shown exactly (``verifier_repair_reviewable``: a text the redaction owner would alter is not the bytes the digest
    binds). The two gate outcomes are display text (redacted, bounded); their identities are in the fingerprint. The owner's pre-repair BYTES are never here."""
    if not _owner.verifier_repair_reviewable(subject):
        return None
    digest = subject.fingerprint
    requested = _now()
    sources = dict(subject.verifier_sources)
    return {
        "review_id": review_identity(launch_id, workspace_identity_value, sequence, digest, _owner.VERIFIER_REPAIR_QUESTION), "sequence": sequence,
        "kind": _owner.KIND_VERIFIER_REPAIR, "status": STATUS_PENDING, "requested_at": _stamp(requested),
        "expires_at": _stamp(requested + timedelta(seconds=APPROVAL_WAIT_SECONDS)),
        "candidate_digest": digest, "authorization_fingerprint": digest,
        "question": _owner.VERIFIER_REPAIR_QUESTION, "notice": _owner.VERIFIER_REPAIR_NOTICE,
        "verifier_unit_id": subject.verifier_unit_id, "owner_unit_id": subject.owner_unit_id,
        "bindings": {
            "run_id": subject.run_id, "plan_hash": subject.plan_hash, "recovery_plan_id": subject.recovery_plan_id,
            "recovery_generation": subject.recovery_generation, "goal_digest": subject.goal_digest,
            "verification_contract_digest": subject.verification_contract_digest,
            "pre_repair_failure_identity": subject.pre_repair_failure_identity, "replay_failure_identity": subject.replay_failure_identity,
        },
        "verifier_files": [{"path": path, "sha256": sha, "source": sources[path]} for path, sha in subject.verifier_files],
        "contract_text": subject.contract_text, "contract_digest": subject.goal_digest,
        "pre_repair_owner_files": [{"path": path, "sha256": sha} for path, sha in subject.pre_repair_owner_files],
        "repaired_owner_files": [{"path": path, "sha256": sha} for path, sha in subject.repaired_owner_files],
        "pre_repair_outcome": redact_text(subject.pre_repair_outcome)[:OUTCOME_MAX_CHARS],
        "replay_outcome": redact_text(subject.replay_outcome)[:OUTCOME_MAX_CHARS], "replay_failure_type": subject.replay_failure_type,
        "decision": None,
    }


async def await_verifier_repair_decision(session: Any, review_id: str, fingerprint: str) -> Any:
    """The verifier-repair authorization's wait: the same durable, fail-closed decision as a change review, answered with the fingerprint the authorization is
    bound to. Anything but a durable approval (a rejection, an expiry, a host that went away, a decision that could not be recorded) is "not authorized"."""
    outcome = await await_decision(session, review_id, fingerprint)
    return _owner.VerifierRepairDecision(fingerprint if outcome.approved else None)


def build_pending_verifier_semantic(launch_id: str, workspace_identity_value: str, sequence: int, subject: Any) -> Optional[Dict[str, Any]]:
    """The pending VERIFIER SEMANTIC REVIEW (VERIFIER-SEMANTIC-REVIEW-001, a fourth subject): the exact verifier source and digests, the canonical contract text
    and digest, the unit's plan-declared criteria and invariants, the review phase and every binding, bound by ``candidate_digest`` (= the subject's
    fingerprint). ``decisions`` lists the only words this review accepts. None - not offered - when the subject cannot be shown exactly."""
    if not _owner.verifier_semantic_reviewable(subject):
        return None
    digest = subject.fingerprint
    requested = _now()
    sources = dict(subject.verifier_sources)
    return {
        "review_id": review_identity(launch_id, workspace_identity_value, sequence, digest, _owner.VERIFIER_SEMANTIC_QUESTION), "sequence": sequence,
        "kind": _owner.KIND_VERIFIER_SEMANTIC, "status": STATUS_PENDING, "requested_at": _stamp(requested),
        "expires_at": _stamp(requested + timedelta(seconds=APPROVAL_WAIT_SECONDS)),
        "candidate_digest": digest, "semantic_fingerprint": digest,
        # WS3-C ruling 1: what an expectation_valid decision on this review is authority FOR (no phase, no failure identity). Carried in the one durable
        # review record; there is no second store.
        "semantic_authority_fingerprint": subject.semantic_authority_fingerprint,
        "question": _owner.VERIFIER_SEMANTIC_QUESTION, "notice": _owner.VERIFIER_SEMANTIC_NOTICE,
        "decisions": list(_owner.VERIFIER_SEMANTIC_DECISIONS),
        "verifier_unit_id": subject.verifier_unit_id, "review_phase": subject.review_phase,
        "bindings": {
            "run_id": subject.run_id, "plan_hash": subject.plan_hash, "goal_digest": subject.goal_digest,
            "verification_contract_digest": subject.verification_contract_digest, "failure_identity": subject.failure_identity,
        },
        "verifier_files": [{"path": path, "sha256": sha, "source": sources[path]} for path, sha in subject.verifier_files],
        "contract_text": subject.contract_text, "contract_digest": subject.goal_digest,
        "acceptance_criteria": [{"id": item_id, "text": text} for item_id, text in subject.acceptance_criteria],
        "invariants": [{"id": item_id, "text": text} for item_id, text in subject.invariants],
        "outcome": redact_text(subject.outcome)[:OUTCOME_MAX_CHARS],
        "decision": None,
    }


async def await_verifier_semantic_decision(session: Any, review_id: str, fingerprint: str) -> Any:
    """The verifier semantic review's wait, over the SAME durable, fail-closed waiter as every other review (capability, staleness, expiry, durable-before-
    resume, audit document). A durably recorded word of the closed table is the decision, bound to the fingerprint; everything else - expiry, the host gone,
    the channel unavailable, a decision that could not be recorded - is NO decision with its reason code, never ``cannot_decide``."""
    recorded = await _await_recorded(session, review_id, fingerprint, _semantic_status)
    if isinstance(recorded, str) and recorded in _owner.VERIFIER_SEMANTIC_DECISIONS:
        return _owner.VerifierSemanticDecision.decided_as(recorded, fingerprint)
    reason = recorded.reason_code if isinstance(recorded, _owner.ApprovalOutcome) else _owner.APPROVAL_RESULT_UNRECOGNIZED
    return _owner.VerifierSemanticDecision.none(reason)


def _binary_status(decision: str) -> str:
    return STATUS_APPROVED if decision == _owner.DECISION_APPROVE else STATUS_REJECTED


def _semantic_status(_decision: str) -> str:
    return STATUS_DECIDED


async def await_decision(session: Any, review_id: str, digest: str) -> _owner.ApprovalOutcome:
    """Block (cooperatively) until the review is decided, expired or abandoned. APPROVED only for a durable approval; DECLINED only for a durable rejection by the person;
    UNAVAILABLE (with its reason code) when nobody decided - the review expired, the host went away, the wait was interrupted - or the decision could not be made durable."""
    recorded = await _await_recorded(session, review_id, digest, _binary_status)
    if isinstance(recorded, _owner.ApprovalOutcome):
        return recorded
    return _owner.ApprovalOutcome.approve() if recorded == _owner.DECISION_APPROVE else _owner.ApprovalOutcome.decline()


async def _await_recorded(session: Any, review_id: str, digest: str, status_of: Any) -> Any:
    """The one wait every review kind uses: the durably recorded decision WORD, or the UNAVAILABLE ApprovalOutcome that says why nobody decided."""
    try:
        return await _wait(session, review_id, digest, status_of)
    except BaseException:  # the terminal's own cancellation (SIGINT -> KeyboardInterrupt) or a cancelled task: the review ends undecided, and the record says so
        if getattr(session, "review_channel", None) is not None:
            session.review_channel.abandon()  # its capability is forgotten with it
        session.resolve_review(review_id, STATUS_EXPIRED, {"decision": None, "decided_at": _stamp(_now()), "source": "interrupted"})
        raise


async def await_runtime_decision(session: Any, review_id: str, fingerprint: str, review: RuntimeCommandReviewPort = RUNTIME_COMMAND_REVIEW) -> Any:
    """The runtime-command review's wait: the same durable, fail-closed decision as a change review, answered with the fingerprint the approval is bound to (as
    ``review``'s typed decision: a ``RuntimeCommandDecision`` by default)."""
    outcome = await await_decision(session, review_id, fingerprint)
    return review.decision(fingerprint if outcome.approved else None)


def _unavailable(session: Any, review_id: str, source: str, reason_code: str) -> _owner.ApprovalOutcome:
    """The review ended with nobody having decided it: recorded as expired (the record never keeps claiming it waits), answered UNAVAILABLE with its reason."""
    session.resolve_review(review_id, STATUS_EXPIRED, {"decision": None, "decided_at": _stamp(_now()), "source": source})
    return _owner.ApprovalOutcome.unavailable_because(reason_code)


def _write_audit_decision(session: Any, review_id: str, digest: str, decision: str, decided_at: str) -> None:
    """The persisted decision document: OUTPUT ONLY, written after the waiter accepted the decision, for the audit trail. Nothing ever reads it as authority; a
    document someone planted at its name is simply not overwritten (the write-once link fails) and changes nothing."""
    payload = {"schema": 1, "launch_id": session.launch_id, "review_id": review_id, "candidate_digest": digest, "workspace_identity": session.record["workspace"]["identity"],
               "decision": decision, "decided_at": decided_at, "source": "review-channel"}
    try:
        save_launch_review_decision(session.workspace, session.launch_id, review_id, payload)
    except Exception as error:  # audit output: its failure is logged, it never alters the decision
        logger.warning("Launch %s: audit decision record not written (%s)", session.launch_id, type(error).__name__)


async def _wait(session: Any, review_id: str, digest: str, status_of: Any = _binary_status) -> Any:
    # The expiry is the WALL-CLOCK instant Kriya recorded, not a monotonic timer that stops while the machine sleeps. The decision arrives ONLY over the private
    # review channel; a launch without one has no way to be approved (the review was not offered in the first place - this is the guard for a channel that went away).
    channel = session.review_channel
    if channel is None:
        return _unavailable(session, review_id, "no-channel", _owner.APPROVAL_CHANNEL_UNAVAILABLE)
    pending = session.record.get("pending_approval") or {}
    expires = datetime.fromisoformat(str(pending.get("expires_at")))
    try:
        await channel.offer(launch_id=session.launch_id, review_id=review_id, kind=pending.get("kind", KIND_CHANGE), candidate_digest=digest)
    except ChannelUnavailable:
        return _unavailable(session, review_id, "channel-unavailable", _owner.APPROVAL_CHANNEL_UNAVAILABLE)
    outcome = await channel.await_decision(expires)
    if outcome.kind == OUTCOME_DECIDED:
        decision = str(outcome.decision)
        if not _owner.decision_valid_for_kind(pending.get("kind", KIND_CHANGE), decision):
            # The channel already refuses a word of another kind; this is the waiter's own guard: such a word is never recorded and never a rejection.
            return _unavailable(session, review_id, "wrong-decision-for-kind", _owner.APPROVAL_RESULT_UNRECOGNIZED)
        decided_at = _stamp(_now())
        _write_audit_decision(session, review_id, digest, decision, decided_at)
        # DURABLE BEFORE RESUME: the record holds the decision before the workflow is allowed to continue; if that cannot be written, nothing is applied
        if session.resolve_review(review_id, status_of(decision), {"decision": decision, "decided_at": decided_at, "source": "review-channel"}):
            return decision
        return _owner.ApprovalOutcome.unavailable_because(_owner.APPROVAL_DECISION_NOT_RECORDED)
    if outcome.kind == OUTCOME_EXPIRED:
        return _unavailable(session, review_id, "timeout", _owner.APPROVAL_REVIEW_EXPIRED)
    if outcome.kind == OUTCOME_EOF:
        return _unavailable(session, review_id, "host-gone", _owner.APPROVAL_REVIEW_EXPIRED)
    return _unavailable(session, review_id, "channel-failed", _owner.APPROVAL_CHANNEL_UNAVAILABLE)  # the channel failed closed (a protocol violation)
