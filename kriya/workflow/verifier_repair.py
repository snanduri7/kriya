"""One human-authorized repair of a verifier (ENFORCE-VERIFIER-REPLAY-TEST-DEFECT-NEVER-REPAIRED-001, owner rulings 2026-11-13, Scenario-A run 9).

MEASURED in run 20261113T060030-40a14267: the model-written test (V0) built an input the goal's contract rules out. Against the unrepaired owner (O0)
it failed inside the owner; against the repaired owner (O1) it failed on its own assertion; the replayed verifier may not be rewritten (verifier_replay),
so the owner was reopened again for a defect it did not have, until the budget ended the run.

Nothing deterministic in Kriya can say that a verifier's EXPECTATION is wrong: a gate failure is evidence against the code under test, and a locator,
a label, a stack frame, a file name or a model's reasoning is not authority. The authority is a person (``kriya.policy.approval.VerifierRepairSubject``):
in human-in-the-loop mode, once per run, the operator may decide that this exact verifier conflicts with the canonical contract. That decision buys ONE
Developer answer for the verifier's own files, and the answer (V1) is accepted only as a transaction this module guards:

- V1 stays STAGED in the unit's sandbox through its ordinary review, source approval and gates (which run against O1);
- before anything is applied, the same V1 bytes are verified against O0 in an ephemeral tree: the unit's sandbox copied byte for byte through the
  canonical bound-tree copy, with ONLY the captured owner paths replaced by their pre-repair bytes. V1 must be REJECTED there by a deterministic gate
  (a verifier that accepts the known-bad owner has been weakened) and only then is it applied, once, by the unit's normal terminal apply;
- any other outcome refuses the repair. Nothing is applied, no second answer is asked for, and the run stops for review: the verifier has been declared
  invalid by a person, so its failure is no longer evidence to send back to the owner.

``PreRepairOwner`` (O0) is captured by the enforce loop when it first reopens the owner, before the owner's files can change. It lives in memory only:
it is never persisted and never rebuilt from the attempt-evidence store, so a restarted process has none and no repair is offered.

Honest boundary: rejecting O0 proves V1 still detects the defect the owner had. It does not prove V1 kept every unrelated check. That is why the
repair exists only with a person's authorization, their separate source approval of V1, one answer, and the verifier's own write scope.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from kriya.platform.filesystem_semantics import PathRelation, path_relation
from kriya.repository.bound_tree import BoundTreeError, bound_tree_paths, copy_bound_paths
from kriya.repository.snapshot import raw_digest

# The execution role of the one authorized repair invocation, and the unit's typed stop when that invocation does not end in an applied V1.
VERIFIER_REPAIR_ROLE = "verifier_repair"
VERIFIER_REPAIR_FAILED = "VERIFIER_REPAIR_FAILED"
# The failure type of a refusal decided by this module (the gate outcome and the Failure the unit records).
VERIFIER_REPAIR_REFUSED_TYPE = "verifier_repair_refused"
# The run's failure category when an authorized repair did not produce an accepted verifier.
VERIFIER_REPAIR_FAILURE_CATEGORY = "verifier_repair_failed"

# Why V1 was refused before it was applied (closed table).
REFUSED_ACCEPTS_PRE_REPAIR_OWNER = "VERIFIER_REPAIR_REFUSED_ACCEPTS_PRE_REPAIR_OWNER"
REFUSED_UNCHANGED = "VERIFIER_REPAIR_REFUSED_VERIFIER_UNCHANGED"
REFUSED_OUT_OF_SCOPE = "VERIFIER_REPAIR_REFUSED_WRITE_OUTSIDE_VERIFIER"
REFUSED_COUNTERFACTUAL_UNAVAILABLE = "VERIFIER_REPAIR_REFUSED_COUNTERFACTUAL_UNAVAILABLE"
REFUSED_COUNTERFACTUAL_INDETERMINATE = "VERIFIER_REPAIR_REFUSED_COUNTERFACTUAL_INDETERMINATE"
REFUSED_SECOND_ANSWER = "VERIFIER_REPAIR_REFUSED_SECOND_DEVELOPER_ANSWER"
REFUSED_AUTHORIZATION_STALE = "VERIFIER_REPAIR_REFUSED_AUTHORIZATION_STALE"
REFUSAL_CODES = (REFUSED_ACCEPTS_PRE_REPAIR_OWNER, REFUSED_UNCHANGED, REFUSED_OUT_OF_SCOPE, REFUSED_COUNTERFACTUAL_UNAVAILABLE,
                 REFUSED_COUNTERFACTUAL_INDETERMINATE, REFUSED_SECOND_ANSWER, REFUSED_AUTHORIZATION_STALE)

# Why no authorization was requested (the enforce loop's log line; today's routing continues).
UNAVAILABLE_UNATTENDED = "VERIFIER_REPAIR_UNAVAILABLE_NOT_HUMAN_IN_THE_LOOP"
UNAVAILABLE_ALREADY_ASKED = "VERIFIER_REPAIR_UNAVAILABLE_ALREADY_ASKED_IN_THIS_RUN"
UNAVAILABLE_NO_PRE_REPAIR_OWNER = "VERIFIER_REPAIR_UNAVAILABLE_NO_PRE_REPAIR_OWNER"
UNAVAILABLE_BINDING_CHANGED = "VERIFIER_REPAIR_UNAVAILABLE_BINDING_CHANGED"
UNAVAILABLE_OWNER_UNCHANGED = "VERIFIER_REPAIR_UNAVAILABLE_OWNER_UNCHANGED"
UNAVAILABLE_NO_CHANNEL = "VERIFIER_REPAIR_UNAVAILABLE_NO_AUTHORIZATION_CHANNEL"
UNAVAILABLE_NOT_REVIEWABLE = "VERIFIER_REPAIR_UNAVAILABLE_NOT_REVIEWABLE"
UNAVAILABLE_CODES = (UNAVAILABLE_UNATTENDED, UNAVAILABLE_ALREADY_ASKED, UNAVAILABLE_NO_PRE_REPAIR_OWNER, UNAVAILABLE_BINDING_CHANGED,
                     UNAVAILABLE_OWNER_UNCHANGED, UNAVAILABLE_NO_CHANNEL, UNAVAILABLE_NOT_REVIEWABLE)

# The counterfactual gate verdicts (what the run's evidence keeps of V1 against O0).
OUTCOME_REJECTED, OUTCOME_ACCEPTED, OUTCOME_UNAVAILABLE, OUTCOME_INDETERMINATE = "rejected", "accepted", "unavailable", "indeterminate"

VERIFIER_REPAIR_CONTEXT = (
    "--- authorized verifier repair (one answer) ---\n"
    "The operator reviewed this unit's verifier against the canonical goal and decided that one of its expectations conflicts with it. This is the ONE "
    "repair of that verifier: only this unit's own file(s) may change, and there is no second answer.\n"
    "Correct the expectation that conflicts with the goal. Keep every check the goal requires: the repaired verifier is run against the upstream "
    "code as it was BEFORE its repair and must still fail there, and against the repaired upstream code, where it must pass.\n"
    "Deterministic gate output of the current verifier against the repaired upstream code ({failure_type}):\n{evidence}\n"
    "--- the current verifier (replace it in full) ---\n{sources}"
)


@dataclass(frozen=True)
class OwnerFile:
    """One owner file as it was before the owner's recovery: exact bytes, and the raw digest the journal authority reported for the same path."""

    path: str
    data: bytes
    raw_sha256: str

    def intact(self) -> bool:
        return raw_digest(self.data) == self.raw_sha256


@dataclass(frozen=True)
class PreRepairOwner:
    """O0: the implicated owner files of one recovery episode, captured before the owner's first reopen, bound to the identities it is valid under.
    Run-local and immutable. ``verifier_files`` is the path/digest set of the verifier candidate (V0) whose failure reopened the owner."""

    run_id: str
    plan_hash: str
    recovery_plan_id: str
    recovery_generation: int
    owner_id: str
    verifier_unit_id: str
    files: Tuple[OwnerFile, ...]
    verifier_files: Tuple[Tuple[str, str], ...]
    reopening_failure_identity: Optional[str]
    reopening_outcome: str

    @property
    def paths(self) -> Tuple[str, ...]:
        return tuple(file.path for file in self.files)

    def digests(self) -> Tuple[Tuple[str, str], ...]:
        return tuple((file.path, file.raw_sha256) for file in self.files)

    def refusal(self, *, run_id: str, plan_hash: str, owner_id: str, verifier_unit_id: str, verifier_files: Iterable[Tuple[str, str]]) -> Optional[str]:
        """None when this O0 may back a repair NOW. The run, the plan, the owner, the verifier unit and the verifier's exact bytes must be the ones it
        was captured under, and its own bytes must still have the digests the journal authority reported."""
        same = (self.run_id == run_id and self.plan_hash == plan_hash and self.owner_id == owner_id and self.verifier_unit_id == verifier_unit_id
                and tuple(sorted(self.verifier_files)) == tuple(sorted(verifier_files)))
        if not same or not self.files or not all(file.intact() for file in self.files):
            return UNAVAILABLE_BINDING_CHANGED
        return None


def capture_pre_repair_owner(
    *, workspace_path: str, paths: Sequence[str], journal_revision: Callable[[str], str], run_id: str, plan_hash: str, recovery_plan_id: str,
    recovery_generation: int, owner_id: str, verifier_unit_id: str, verifier_files: Iterable[Tuple[str, str]],
    reopening_failure_identity: Optional[str], reopening_outcome: str,
) -> Optional[PreRepairOwner]:
    """Read the owner's implicated files as they are NOW (before the recovery writes anything) and bind them. None - no handle, so no repair can be
    offered - unless every path is an existing regular file inside the workspace whose bytes have exactly the revision ``journal_revision`` (the
    repository's own file-revision authority) reports for it. Nothing partial is kept."""
    verifier = tuple(sorted(verifier_files))
    if not paths or not verifier:
        return None
    root = os.path.realpath(workspace_path)
    files: List[OwnerFile] = []
    for relpath in sorted(set(paths)):
        full = os.path.join(workspace_path, relpath)
        if os.path.islink(full) or not os.path.isfile(full) or path_relation(root, full) is not PathRelation.WITHIN:
            return None
        try:
            with open(full, "rb") as handle:
                data = handle.read()
        except OSError:
            return None
        digest = raw_digest(data)
        if digest != journal_revision(full):
            return None
        files.append(OwnerFile(relpath, data, digest))
    return PreRepairOwner(
        run_id=run_id, plan_hash=plan_hash, recovery_plan_id=recovery_plan_id, recovery_generation=recovery_generation, owner_id=owner_id,
        verifier_unit_id=verifier_unit_id, files=tuple(files), verifier_files=verifier, reopening_failure_identity=reopening_failure_identity,
        reopening_outcome=reopening_outcome,
    )


@dataclass(frozen=True)
class VerifierRepairTransaction:
    """What the one repair invocation carries into the unit: the O0 handle, the V0 digests its answer must differ from, and the stale-authorization
    check the enforce loop built from the operator's decision (consulted again immediately before the counterfactual and so before the apply)."""

    pre_repair: PreRepairOwner
    authorization_fingerprint: str
    still_authorized: Callable[[], bool]
    outcomes: List[Dict[str, Any]]            # appended by the unit: the O0 verdict, for the enforce loop's run evidence (never read for control)


class VerifierRepairRefused(Exception):
    """V1 is refused before anything is applied. ``code`` is one of REFUSAL_CODES."""

    def __init__(self, code: str, detail: str, outcome: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.outcome = outcome or {}


def staged_verifier(sandbox_path: str, written: Iterable[str], staged_digests: Mapping[str, Optional[str]], transaction: VerifierRepairTransaction) -> Dict[str, str]:
    """The staged V1 as {path: raw digest}, after the checks that need no gate: it wrote exactly verifier-owned paths (never the owner's), the sandbox
    holds the bytes candidate authority staged, and at least one byte differs from V0."""
    v0 = dict(transaction.pre_repair.verifier_files)
    paths = sorted(set(written))
    outside = [path for path in paths if path not in v0]
    if not paths or outside or set(paths) & set(transaction.pre_repair.paths):
        raise VerifierRepairRefused(REFUSED_OUT_OF_SCOPE, f"the repair wrote {outside or paths}; only the authorized verifier file(s) {sorted(v0)} may change")
    staged: Dict[str, str] = {}
    for relpath in paths:
        try:
            with open(os.path.join(sandbox_path, relpath), "rb") as handle:
                digest = raw_digest(handle.read())
        except OSError as error:
            raise VerifierRepairRefused(REFUSED_COUNTERFACTUAL_UNAVAILABLE, f"the staged verifier file {relpath} cannot be read ({type(error).__name__})") from error
        if digest != staged_digests.get(relpath):
            raise VerifierRepairRefused(REFUSED_COUNTERFACTUAL_UNAVAILABLE, f"the sandbox bytes of {relpath} are not the staged candidate's")
        staged[relpath] = digest
    if all(v0.get(path) == digest for path, digest in staged.items()):
        raise VerifierRepairRefused(REFUSED_UNCHANGED, "the answer left the verifier byte-identical to the one the operator declared invalid")
    return staged


def _file_digest(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as handle:
            return raw_digest(handle.read())
    except OSError:
        return None


def build_counterfactual_tree(sandbox_path: str, destination: str, pre_repair: PreRepairOwner, staged: Mapping[str, str]) -> List[str]:
    """Materialize the O0 verification tree in ``destination`` (an empty ephemeral directory the caller owns and removes): the sandbox's canonical bound
    tree copied and proven byte for byte, then ONLY ``pre_repair``'s paths overwritten with their captured bytes. Afterwards every member is checked:
    an owner path holds its O0 digest, a verifier path its staged V1 digest, and every other path the sandbox's current bytes. Returns the members.
    These are controller-owned writes into a throwaway tree; nothing here is, or can become, a repository mutation."""
    if not all(file.intact() for file in pre_repair.files):
        raise VerifierRepairRefused(REFUSED_COUNTERFACTUAL_UNAVAILABLE, "the captured pre-repair owner bytes no longer have their recorded digests")
    try:
        members = set(bound_tree_paths(sandbox_path))
        missing = sorted((set(pre_repair.paths) | set(staged)) - members)
        if missing:
            raise VerifierRepairRefused(REFUSED_COUNTERFACTUAL_UNAVAILABLE, f"{missing} is not part of the candidate tree")
        copy_bound_paths(sandbox_path, destination, members)
    except BoundTreeError as error:
        raise VerifierRepairRefused(REFUSED_COUNTERFACTUAL_UNAVAILABLE, f"the candidate tree could not be copied ({error.reason_code})") from error
    root = os.path.realpath(destination)
    for file in pre_repair.files:
        target = os.path.join(destination, file.path)
        if os.path.islink(target) or path_relation(root, target) is not PathRelation.WITHIN:
            raise VerifierRepairRefused(REFUSED_COUNTERFACTUAL_UNAVAILABLE, f"{file.path} does not resolve inside the verification tree")
        with open(target, "wb") as handle:        # the O0 overlay: an ephemeral verification tree only
            handle.write(file.data)
    expected = {**{path: None for path in members}, **dict(pre_repair.digests()), **dict(staged)}
    for relpath in sorted(members):
        full = os.path.join(destination, relpath)
        if os.path.islink(full) or os.path.isdir(full):
            continue                                  # a link or a submodule entry: copied and proven by the bound-tree copy, never overlaid
        wanted = expected[relpath] or _file_digest(os.path.join(sandbox_path, relpath))
        if _file_digest(full) != wanted:
            raise VerifierRepairRefused(REFUSED_COUNTERFACTUAL_UNAVAILABLE, f"{relpath} in the verification tree is not the expected byte sequence")
    return sorted(members)


async def verify_against_pre_repair_owner(
    transaction: VerifierRepairTransaction, *, sandbox_path: str, written: Iterable[str], staged_digests: Mapping[str, Optional[str]],
    run_gates: Callable[[str], Awaitable[Tuple[str, Dict[str, Any]]]], scratch_parent: Optional[str] = None,
) -> Dict[str, Any]:
    """The pre-apply guard of the repair transaction. ``run_gates(tree)`` runs the unit's applicable deterministic verifier gates on ``tree`` and answers
    ``(verdict, evidence)`` with a verdict from OUTCOME_*. Returns the recorded outcome when V1 is REJECTED on O0 (the only verdict that lets the unit
    apply); raises ``VerifierRepairRefused`` otherwise. The ephemeral tree is always removed."""
    if not transaction.still_authorized():
        raise VerifierRepairRefused(REFUSED_AUTHORIZATION_STALE, "a binding of the operator's authorization changed before the repaired verifier could be verified")
    staged = staged_verifier(sandbox_path, written, staged_digests, transaction)
    scratch = tempfile.mkdtemp(prefix="kriya-verifier-counterfactual-", dir=scratch_parent)
    tree = os.path.join(scratch, "tree")
    try:
        members = build_counterfactual_tree(sandbox_path, tree, transaction.pre_repair, staged)
        verdict, evidence = await run_gates(tree)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    outcome = {
        "against": "pre_repair_owner", "verdict": verdict, "verifier_files": dict(staged), "owner_files": dict(transaction.pre_repair.digests()),
        "tree_members": len(members), "authorization_fingerprint": transaction.authorization_fingerprint, **evidence,
    }
    transaction.outcomes.append(outcome)
    if verdict == OUTCOME_REJECTED:
        return outcome
    if verdict == OUTCOME_ACCEPTED:
        raise VerifierRepairRefused(
            REFUSED_ACCEPTS_PRE_REPAIR_OWNER,
            "the repaired verifier passes against the owner code as it was BEFORE the repair, so it no longer detects the defect that code had", outcome)
    if verdict == OUTCOME_UNAVAILABLE:
        raise VerifierRepairRefused(REFUSED_COUNTERFACTUAL_UNAVAILABLE, "the verification environment could not run the repaired verifier against the pre-repair owner", outcome)
    raise VerifierRepairRefused(REFUSED_COUNTERFACTUAL_INDETERMINATE, "no deterministic gate judged the repaired verifier against the pre-repair owner", outcome)
