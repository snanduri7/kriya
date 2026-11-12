"""Verifier replay of an owner recovery (ENFORCE-OWNER-RECOVERY-DEPENDENT-VERIFIER-REGENERATED-001, owner rulings 2026-11-12, Scenario-A run 7).

MEASURED in run 20261112T150652-e9c5502b: a consumer unit (the JUnit test) failed inside an upstream owner's file; the loop reopened the owner;
the owner's repair passed its own gates (a compile, and a tests gate that ran zero tests); the loop then invoked the consumer again as an ordinary
unit, so the model wrote a NEW test file to find out whether the repair worked. Three such cycles used the run's budget. The one artifact that had
disproved the owner - the consumer's refused candidate - was discarded with the failed unit.

This module is the transport for replaying that artifact instead:

- ``RefusedCandidate``: what a failed unit keeps of its own staged candidate just before its sandbox is removed (exact text of every written file,
  the raw digest candidate authority already recorded for it, the failing gate's identity).
- ``ReplayHandle``: the enforce loop's run-local, immutable binding of a refused candidate to the identities that make it valid: run, plan hash,
  consumer unit, goal digest, verification-contract digest, path set and byte digests.

It is transport and identity, never a candidate authority: nothing here writes a file. The bytes are materialized by the consumer unit's own
attempt, through the write path model output takes (``attempt.run_attempt``, the PRD-008 candidate-reuse seam), and the unit's normal review,
approval, gates and commit decide everything. Nothing is persisted: the attempt-evidence store stays control-flow inert (invariant I-2), and a
restarted process simply regenerates the consumer.

VALIDITY is decided from existing canonical identities only (``ReplayHandle.refusal``). An upstream owner changing its IMPLEMENTATION is not a
reason to discard the verifier - checking that implementation is what the verifier is for - and a replay that FAILS, in whatever way, is evidence
against the owner's repair, never a licence to rewrite the verifier: no authoritative contract change, the existing verifier stays valid.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from kriya.repository.snapshot import raw_digest

# Why a handle was not replayed (the consumer is then regenerated through the Developer, as before).
REPLAY_PLAN_CHANGED = "VERIFIER_REPLAY_PLAN_CHANGED"
REPLAY_GOAL_CHANGED = "VERIFIER_REPLAY_GOAL_CHANGED"
REPLAY_CONTRACT_CHANGED = "VERIFIER_REPLAY_VERIFICATION_CONTRACT_CHANGED"
REPLAY_RUN_CHANGED = "VERIFIER_REPLAY_RUN_CHANGED"
REPLAY_UNIT_CHANGED = "VERIFIER_REPLAY_UNIT_CHANGED"
REPLAY_OUT_OF_SCOPE = "VERIFIER_REPLAY_FILE_OUTSIDE_CONSUMER_SCOPE"
REPLAY_BYTES_MISMATCH = "VERIFIER_REPLAY_BYTES_MISMATCH"
REPLAY_EMPTY = "VERIFIER_REPLAY_NOTHING_CARRIED"
REPLAY_REFUSAL_CODES = (REPLAY_PLAN_CHANGED, REPLAY_GOAL_CHANGED, REPLAY_CONTRACT_CHANGED, REPLAY_RUN_CHANGED, REPLAY_UNIT_CHANGED,
                        REPLAY_OUT_OF_SCOPE, REPLAY_BYTES_MISMATCH, REPLAY_EMPTY)

# The typed stop of a replay whose gates failed: the unit has no authority to rewrite the carried verifier, so no further attempt can change it.
VERIFIER_REPLAY_FAILED = "VERIFIER_REPLAY_FAILED_OWNER_REPAIR_NOT_VERIFIED"

# What the consumer is told when it runs after an owner repair. The owner is NOT verified at this point: only its own gates accepted it.
DEPENDENT_VERIFICATION_PENDING_CONTEXT = (
    "--- upstream owner repair locally accepted; dependent verification pending ---\n"
    "Upstream owner(s) {owners} changed their files and passed their OWN gates. Nothing has verified that repair against this unit's "
    "verification yet: this run of the unit is that check. Run this unit against the updated workspace."
)


@dataclass(frozen=True)
class CarriedFile:
    """One file of a refused candidate: its exact text and the raw digest candidate authority recorded when it was staged."""

    path: str
    content: str
    raw_sha256: str

    def intact(self) -> bool:
        return raw_digest(self.content.encode("utf-8")) == self.raw_sha256


@dataclass(frozen=True)
class RefusedCandidate:
    """A failed unit's own staged candidate, kept in memory when its sandbox is removed. ``binding_digest`` is the candidate binding the unit's
    verification had taken over its base (None when none was taken); ``failure_type`` / ``failure_digest`` identify the gate failure that refused it."""

    files: Tuple[CarriedFile, ...]
    binding_digest: Optional[str]
    failure_type: Optional[str]
    failure_digest: Optional[str]

    @property
    def paths(self) -> Tuple[str, ...]:
        return tuple(file.path for file in self.files)


def failure_identity(failure_type: Optional[str], raw_output: Optional[str]) -> Optional[str]:
    """The identity of one deterministic gate failure: its type and the gate's own output. Never model text."""
    if failure_type is None and not raw_output:
        return None
    return hashlib.sha256(f"{failure_type or ''}\n{raw_output or ''}".encode("utf-8", errors="replace")).hexdigest()


def capture_refused_candidate(state: Any, worktree_path: str) -> Optional[RefusedCandidate]:
    """The refused candidate of a unit that is stopping, read from its sandbox before the sandbox is removed. None unless EVERY written file is
    captured exactly (strict UTF-8, no newline translation: the PRD-008 rule for a reusable candidate) and its bytes still have the raw digest
    candidate authority recorded when it staged them. A deletion, a file that is gone, or any difference yields None: nothing partial is carried."""
    written = sorted(getattr(state, "all_files_written", None) or ())
    if not written:
        return None
    files: List[CarriedFile] = []
    for relpath in written:
        staged_digest = (getattr(state, "candidate_digests", None) or {}).get(relpath)
        if not staged_digest:
            return None
        try:
            with open(os.path.join(worktree_path, relpath), "rb") as handle:
                data = handle.read()
            content = data.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        if raw_digest(data) != staged_digest:
            return None
        files.append(CarriedFile(relpath, content, staged_digest))
    last_failure = getattr(state, "last_failure", None)
    binding = getattr(state, "verified_candidate_binding", None)
    return RefusedCandidate(
        files=tuple(files), binding_digest=getattr(binding, "digest", None),
        failure_type=getattr(last_failure, "type", None),
        failure_digest=failure_identity(getattr(last_failure, "type", None), getattr(last_failure, "raw_output", None)),
    )


def goal_digest(goal: str) -> str:
    return hashlib.sha256((goal or "").encode("utf-8", errors="replace")).hexdigest()


@dataclass(frozen=True)
class ReplayHandle:
    """A refused consumer candidate bound to the run, the plan and the contract it was produced under. Run-local: built by the enforce loop for
    the ACTIVE recovery episode and dropped when that episode settles or is exhausted."""

    run_id: str
    plan_hash: str
    unit_id: str
    goal_digest: str
    verification_contract_digest: Optional[str]
    candidate: RefusedCandidate

    def refusal(
        self, *, run_id: str, plan_hash: str, unit_id: str, goal_digest: str, verification_contract_digest: Optional[str],
        planned_paths: Iterable[str],
    ) -> Optional[str]:
        """None when the handle may be replayed NOW, else the reason code. Existing canonical identities only: the run, the validated plan's
        hash (it changes with every plan, scope or integration-relationship revision), the goal, the sealed verification contract, the consumer
        unit, its current planned scope, and the carried bytes themselves. Upstream file CONTENT is deliberately not an input."""
        if self.run_id != run_id:
            return REPLAY_RUN_CHANGED
        if self.plan_hash != plan_hash:
            return REPLAY_PLAN_CHANGED
        if self.goal_digest != goal_digest:
            return REPLAY_GOAL_CHANGED
        if self.verification_contract_digest != verification_contract_digest:
            return REPLAY_CONTRACT_CHANGED
        if self.unit_id != unit_id:
            return REPLAY_UNIT_CHANGED
        if not self.candidate.files:
            return REPLAY_EMPTY
        if not set(self.candidate.paths) <= set(planned_paths):
            return REPLAY_OUT_OF_SCOPE
        if not all(file.intact() for file in self.candidate.files):
            return REPLAY_BYTES_MISMATCH
        return None

    def generated_files(self) -> List[Dict[str, str]]:
        """The carried files in the shape the attempt's write path takes for model output."""
        return [{"filepath": file.path, "content": file.content} for file in self.candidate.files]

    def expected_digests(self) -> Dict[str, str]:
        return {file.path: file.raw_sha256 for file in self.candidate.files}


def staged_bytes_mismatch(handle: ReplayHandle, staged_digests: Mapping[str, Optional[str]], written: Iterable[str]) -> List[str]:
    """Paths whose staged bytes are not the carried ones, plus any path written that was not carried or carried and not written."""
    expected = handle.expected_digests()
    wrong = sorted(path for path, digest in expected.items() if staged_digests.get(path) != digest)
    return wrong + sorted(set(written) ^ set(expected))


def replay_facts_block(
    *, owner_id: str, repair_changed: Optional[bool], repair_digests: Mapping[str, Optional[str]], verifier_paths: Iterable[str],
    replayed: bool, previous_failure: Optional[str], current_failure: Optional[str],
) -> str:
    """The deterministic facts of the last dependent verification, for the next owner invocation. Candidate comparison and gate identities
    only: nothing here is model reasoning and nothing is remembered beyond this run."""
    same = previous_failure is not None and previous_failure == current_failure
    lines = [
        "--- dependent verification of your previous repair (deterministic facts) ---",
        f"Owner: {owner_id}. Your previous repair "
        + ("changed the required file(s)." if repair_changed else "left the required file(s) byte-identical." if repair_changed is False
           else "was not compared.")
        + " Its own gates accepted it; the dependent verification did NOT.",
        "Repair candidate digest(s): " + (", ".join(f"{path}={digest}" for path, digest in sorted(repair_digests.items())) or "none"),
        ("The dependent verifier was the SAME artifact as before (" + ", ".join(sorted(verifier_paths)) + "), replayed byte for byte: its "
         "result is about your change, not about a new test." if replayed else
         "The dependent verifier was generated again for this check."),
        f"Previous dependent failure identity: {previous_failure or 'none'}",
        f"Current dependent failure identity:  {current_failure or 'none'}",
        ("The failure is the SAME as before your repair: the repair did not change the failing behaviour." if same else
         "The failure CHANGED after your repair: the evidence below is the current one and replaces the earlier one."),
    ]
    return "\n".join(lines)
