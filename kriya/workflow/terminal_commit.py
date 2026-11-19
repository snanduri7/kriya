"""The one real-workspace commit seam for a verified candidate (PRD-004/005/007).

Both terminal commit sites - the enforce controller's plan-level candidate
and the generation workflow's per-run sandbox - go through this module, so
they share one set of guarantees:

* the candidate's exact verified bytes and file mode are what gets committed
  (never a text-decoded copy);
* when an active run owns the target workspace, durable commit intent is
  recorded in its RunRecord BEFORE the first workspace byte changes, and a
  commit whose intent cannot be persisted is refused;
* every outcome settles the run's commit cycle from the commit evidence -
  COMMITTED, ROLLED_BACK/NOT_COMMITTED (workspace unchanged), or UNCERTAIN;
* PRD-029: the ContractRegistry transition the candidate implies is part
  of the same transaction. It is derived before intent, and its digests go
  into the cycle. It is staged before the first source byte, promoted only
  after the source commit, and the cycle is settled COMMITTED only once the
  promotion succeeded. A promotion failure after the bytes landed settles
  UNCERTAIN (CONTRACT_REGISTRY_TRANSITION_INCOMPLETE), so `kriya runs
  recover` completes it, never a silent stale registry;
* PRD-031A: static-analysis evidence is required by the current
  configuration, never by the caller. Every commit takes a
  StaticAnalysisCommitGuard (kriya/static_analysis/service.py::commit_guard)
  and is refused before any intent when that evidence is missing, does not
  permit the commit, or is stale (candidate, base, scope, provider runtime,
  rule packs or settings changed after the scan).

Callers map the returned TerminalCommitOutcome into their own result shape.
"""

from __future__ import annotations

import difflib
import os
import stat
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from kriya.control.run_coordinator import begin_run_commit, candidate_workspace_root, settle_run_commit
from kriya.control.run_record import (
    COMMIT_COMMITTED,
    COMMIT_NOT_COMMITTED,
    COMMIT_ROLLED_BACK,
    COMMIT_UNCERTAIN,
)
from kriya.policy.approval import MutationApproval, mutation_approval_refusal
from kriya.policy.filesystem import ControlObjectIdentities, is_trusted_control_path
from kriya.repository.bound_tree import NO_GIT_REPOSITORY, bound_tree_paths, workspace_root_class
from kriya.repository.commit import (  # M1: owned by kriya/repository/commit.py, re-exported (PARTIAL_FACADES); Removed in P1
    CandidateMaterializationError as CandidateMaterializationError,
)
from kriya.static_analysis.service import StaticAnalysisCommitGuard
from kriya.workflow.edit_safety import (
    BatchCommitError,
    CommitEvidence,
    CommitState,
    FileRevisionConflict,
    StagedFileWrite,
    UncertainCommitError,
    candidate_digest,
    commit_revision_grounded_batch,
    commit_state_for_transaction,
)
from kriya.workflow.exception_masking import find_masking_against_workspace
from kriya.workflow.file_integrity import display_text, file_raw_digest, raw_digest
from kriya.workflow.file_resolution import (
    ENTRYPOINT_MISMATCH,
    ENTRYPOINT_PENDING,
    ENTRYPOINT_UNVERIFIED,
    ENTRYPOINT_VERIFIED,
    EntrypointIdentity,
    exec_main_class_identity,
)
from kriya.workflow.verification_binding import (
    CandidateVerificationBinding,
    VerifiedCandidateStale,
    binding_refusal,
)
from kriya.workflow.verification_binding import (  # S7: the approval subject's derivation, re-exported for the gate and the enforce terminal
    bind_candidate as bind_candidate,
)

VERIFIED_COMMIT_DIGEST_MISMATCH = "VERIFIED_COMMIT_DIGEST_MISMATCH"


class MutationApprovalRefused(RuntimeError):
    """S7: the batch about to be committed is not covered by the approval decision it was handed."""


def batch_diffs(writes: Iterable[StagedFileWrite], workspace_path: str) -> List[Dict[str, str]]:
    """The change a batch makes to ``workspace_path`` as the approval surface shows it: one unified diff per path, the new side from the
    batch's own bytes (never a re-read of the candidate), the old side from the file the batch is grounded on. Display only: the approval
    is bound to the batch's digest, and the commit checks each base revision itself."""
    diffs: List[Dict[str, str]] = []
    for write in sorted(writes, key=lambda item: item.target_path):
        relpath = os.path.relpath(write.target_path, workspace_path)
        try:
            with open(write.base_path or write.target_path, "rb") as handle:
                before = display_text(handle.read())
        except FileNotFoundError:
            before = ""
        after = "" if write.delete else write.content
        diffs.append({"filepath": relpath, "content": "".join(difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True), fromfile=f"a/{relpath}", tofile=f"b/{relpath}"))})
    return diffs


def committed_digest_mismatch(writes: Iterable[StagedFileWrite]) -> Optional[str]:
    """FILE-INTEGRITY-CONTRACT-001: the destination bytes after the commit,
    re-read from disk, against the verified candidate bytes; the first
    difference, or None when every path holds exactly what was verified."""
    for write in writes:
        if write.delete:
            if os.path.lexists(write.target_path):
                return f"{write.target_path}: verified as deleted but still present after commit"
            continue
        expected = raw_digest(
            write.content_bytes if write.content_bytes is not None else write.content.encode("utf-8")
        )
        actual = file_raw_digest(write.target_path)
        if actual != expected:
            return (f"{write.target_path}: committed bytes {actual[:12]} differ from the "
                    f"verified candidate {expected[:12]}")
    return None


class StaticAnalysisCommitRefused(RuntimeError):
    """PRD-031A: the commit guard refused the static-analysis evidence."""


class SemanticSafetyRefused(RuntimeError):
    """BACKEND-READINESS-V2: the batch about to be committed newly masks Error/Throwable."""


SEMANTIC_SAFETY_REFUSED = "SEMANTIC_SAFETY_REFUSED"
SEMANTIC_SAFETY_INDETERMINATE = "SEMANTIC_SAFETY_INDETERMINATE"


def semantic_safety_refusal(writes: List[StagedFileWrite], workspace_path: str) -> Optional[Tuple[str, str]]:
    """(reason code, detail) when the exact batch about to reach the workspace must not be committed; None when it may.
    THE central invariant: both commit boundaries (the enforce terminal commit and the direct terminal apply) call
    ``commit_terminal_candidate``, so no candidate, whatever route produced its bytes and whichever earlier gate saw it,
    reaches the workspace without this check passing. Fail closed: an error while judging refuses."""
    try:
        findings = find_masking_against_workspace(writes)
    except Exception as error:
        return SEMANTIC_SAFETY_INDETERMINATE, f"{type(error).__name__}: {error}"
    if findings:
        return SEMANTIC_SAFETY_REFUSED, "; ".join(
            f"{os.path.relpath(target, workspace_path)}: {found}" for target, found in findings)
    return None


class EntrypointIdentityRefused(RuntimeError):
    """JAVA-ENTRYPOINT-IDENTITY-001: the batch would leave the real workspace with a Maven entry point that names no real main class."""


ENTRYPOINT_IDENTITY_REFUSED = "ENTRYPOINT_IDENTITY_REFUSED"
ENTRYPOINT_IDENTITY_INDETERMINATE = "ENTRYPOINT_IDENTITY_INDETERMINATE"
_ENTRYPOINT_POM = "pom.xml"


def _entrypoint_identity_of(pom_text: Optional[str], java_paths: Iterable[str], read: Callable[[str], Optional[str]],
                            java_main_classes: Optional[Callable[[Dict[str, str]], Dict[str, str]]]) -> Optional[EntrypointIdentity]:
    """The identity of one tree state (file_resolution.exec_main_class_identity); None when the state has no pom.xml. A pom that
    declares no single literal <exec.mainClass> is UNVERIFIED without the detector ever being needed; any other pom needs it."""
    if pom_text is None:
        return None
    declared = exec_main_class_identity(pom_text, {})
    if declared.status == ENTRYPOINT_UNVERIFIED:
        return declared
    if java_main_classes is None:
        raise RuntimeError("no Java main-class detector was supplied for a pom.xml that declares <exec.mainClass>")
    sources = {path: text for path, text in ((path, read(path)) for path in sorted(java_paths)) if text is not None}
    return exec_main_class_identity(pom_text, dict(java_main_classes(sources)))


def entrypoint_identity_refusal(
    writes: List[StagedFileWrite], workspace_path: str,
    java_main_classes: Optional[Callable[[Dict[str, str]], Dict[str, str]]],
) -> Optional[Tuple[str, str]]:
    """(reason code, detail) when the batch must not reach the REAL workspace because it would newly leave ``pom.xml``'s
    ``<exec.mainClass>`` naming a class that is not the application's one main class; None when it may.

    Which commits it judges is decided here, from the run's own lease, never by the caller: a destination the active run
    authorized as its isolated candidate (control.run_coordinator.candidate_workspace_root, the enforce plan worktree) is an
    intermediate apply and is not judged (a plan may hold a mismatch between the unit that writes the class and the unit that
    owns the pom); EVERY other destination is judged, including one with no active run. A real workspace can never be declared
    a candidate: candidate_workspace_root answers None for the run's own workspace whatever was authorized.

    The policy is the exception-masking one (semantic_safety_refusal): only a batch that writes or deletes pom.xml or a .java
    file is judged; the state after the batch (the canonical bound tree of the workspace - what git tracks or leaves visible,
    repository.bound_tree.bound_tree_paths - with the batch's additions and deletions applied) is compared with the workspace as
    it is. Refused: a MISMATCH after the batch that was not the identical mismatch before it; and a VERIFIED entry point the
    batch leaves with no main class at all (PENDING: the class or its main method was removed under an unchanged
    configuration). A project that never had a verified entry point may stay PENDING (greenfield, a library, an incomplete
    project), and UNVERIFIED is never refused; nothing here records any state as verified. Fail closed: an applicable check that
    cannot be made (no detector, a detector or membership error) refuses. Nothing is ever rewritten here.

    Scope, stated plainly: the ROOT pom.xml and a single literal ``<exec.mainClass>`` property. A multi-module build, a main class
    configured on a plugin, and a property that is itself a reference are not judged here (UNVERIFIED or not applicable)."""
    try:
        if candidate_workspace_root(workspace_path) is not None:
            return None
        changed: Dict[str, Optional[str]] = {}
        for write in writes:
            relpath = os.path.relpath(write.target_path, workspace_path).replace(os.sep, "/")
            changed[relpath] = None if write.delete else write.content
        if not any(path == _ENTRYPOINT_POM or path.endswith(".java") for path in changed):
            return None

        # a workspace with no repository at all is admitted only when empty (bound_tree.admit_workspace_for_mutation): it has no members
        members = set() if workspace_root_class(workspace_path) == NO_GIT_REPOSITORY else set(bound_tree_paths(workspace_path))

        def baseline(relpath: str) -> Optional[str]:
            # only a member of the bound tree is workspace content: a git-ignored pom.xml on disk is in no candidate and decides nothing
            full_path = os.path.join(workspace_path, relpath)
            if relpath not in members or not os.path.isfile(full_path):
                return None
            with open(full_path, "r", encoding="utf-8", errors="replace") as handle:
                return handle.read()

        def candidate(relpath: str) -> Optional[str]:
            return changed[relpath] if relpath in changed else baseline(relpath)

        java_before = {path for path in members if path.endswith(".java")}
        java_after = {path for path in java_before | set(changed) if path.endswith(".java") and changed.get(path, "") is not None}
        after = _entrypoint_identity_of(candidate(_ENTRYPOINT_POM), java_after, candidate, java_main_classes)
        if after is None or after.status not in (ENTRYPOINT_MISMATCH, ENTRYPOINT_PENDING):
            return None
        before = _entrypoint_identity_of(baseline(_ENTRYPOINT_POM), java_before, baseline, java_main_classes)
        if after.status == ENTRYPOINT_PENDING:
            if before is None or before.status != ENTRYPOINT_VERIFIED:
                return None
            return ENTRYPOINT_IDENTITY_REFUSED, (
                f"{_ENTRYPOINT_POM}: <exec.mainClass> is {after.configured!r}, but this change leaves the application with no class that "
                f"has a main method; before it the entry point was {before.actual!r} (declared in {before.source_file})")
        if before is not None and before.status == ENTRYPOINT_MISMATCH and (
                before.configured, before.actual, before.source_file) == (after.configured, after.actual, after.source_file):
            return None
    except Exception as error:
        return ENTRYPOINT_IDENTITY_INDETERMINATE, f"{type(error).__name__}: {error}"
    return ENTRYPOINT_IDENTITY_REFUSED, f"{_ENTRYPOINT_POM}: <exec.mainClass> is {after.configured!r}, but {after.actual_description()}"


@dataclass(frozen=True)
class CandidateFile:
    """One approved path of a verified candidate and the base it was built on."""

    relpath: str
    expected_base_revision: str
    # None = revision-only identity (callers that cannot tell an empty
    # existing file from a missing one).
    expected_base_exists: Optional[bool] = None
    delete: bool = False


def _base_mode(target_path: str, expected_base_exists: Optional[bool]) -> Optional[int]:
    """The permission bits of the EXISTING workspace file a candidate edit is based on, or None when there is no such file to preserve (CANDIDATE-MODE-
    DRIFT-FROM-CHECKOUT-001). The base file's mode is authoritative: the candidate sandbox's checkout mode is a git/umask artifact (git records only 644/755,
    and ``core.fileMode=false`` drops the executable bit), never user intent. A target that is missing, though declared to exist, or is not a regular file
    returns None here and is refused by the commit (existence conflict / link or directory refusal); an unreadable one fails closed."""
    if expected_base_exists is False:
        return None
    try:
        info = os.lstat(target_path)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise CandidateMaterializationError(
            f"Cannot read the base file's mode for {target_path!r}: {error}"
        ) from error
    return stat.S_IMODE(info.st_mode) if stat.S_ISREG(info.st_mode) else None


def materialize_candidate(
    candidate_root: str, workspace_root: str, files: Iterable[CandidateFile],
) -> List[StagedFileWrite]:
    """Read the candidate's exact bytes into a guarded batch. An existing file keeps its BASE file's mode (read from the workspace); only a new file
    takes the candidate's mode.

    Reads only the candidate and the base file's mode; the real workspace is
    written later, by the revision-grounded commit, which checks each recorded
    base revision and the base mode."""
    writes: List[StagedFileWrite] = []
    identities = ControlObjectIdentities((workspace_root,))  # OI-164: one control-object index for this commit
    for item in files:
        candidate_path = os.path.join(candidate_root, item.relpath)
        target_path = os.path.join(workspace_root, item.relpath)
        if is_trusted_control_path(workspace_root, target_path, identities=identities):
            # PLAT-039: the commit boundary's own backstop to the candidate
            # writer's refusal - a candidate never commits into .git/.kriya.
            raise CandidateMaterializationError(
                f"Verified candidate names a trusted control path {item.relpath!r}; refusing to commit it"
            )
        if item.delete:
            if os.path.lexists(candidate_path):
                raise CandidateMaterializationError(
                    f"Verified candidate did not delete approved file {item.relpath!r}"
                )
            writes.append(StagedFileWrite(
                target_path=target_path, content="", base_path=target_path,
                expected_base_revision=item.expected_base_revision, delete=True,
                expected_base_exists=True,
            ))
            continue
        if os.path.islink(candidate_path):
            raise CandidateMaterializationError(
                f"Verified candidate file {item.relpath!r} is a symbolic link; Kriya never "
                "commits a link as a regular file"
            )
        try:
            with open(candidate_path, "rb") as handle:
                candidate_bytes = handle.read()
            candidate_mode = os.lstat(candidate_path).st_mode & 0o7777
        except OSError as error:
            raise CandidateMaterializationError(
                f"Verified candidate is missing approved file {item.relpath!r}: {error}"
            ) from error
        base_mode = _base_mode(target_path, item.expected_base_exists)
        writes.append(StagedFileWrite(
            target_path=target_path, content=display_text(candidate_bytes), base_path=target_path,
            expected_base_revision=item.expected_base_revision,
            expected_base_exists=item.expected_base_exists,
            content_bytes=candidate_bytes, mode=candidate_mode if base_mode is None else base_mode,
            expected_base_mode=base_mode,
        ))
    return writes


@dataclass
class TerminalCommitOutcome:
    committed: bool
    # "COMMITTED" | "UNCHANGED" | "UNCERTAIN" - the real workspace, not the run.
    workspace_state: str
    commit_result: str
    transaction_id: str
    reason_code: Optional[str] = None
    evidence: Optional[CommitEvidence] = None
    error: Optional[BaseException] = None
    # Record persistence failures AFTER the workspace outcome was decided.
    record_errors: List[Dict[str, str]] = field(default_factory=list)
    # PRD-029: the committed ContractRegistry transition (RunRecord cycle
    # intent), when this commit changed a contract.
    contract_registry: Optional[Dict[str, Any]] = None

    def failure_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "reason_code": self.reason_code, "workspace_state": self.workspace_state,
            "error": str(self.error) if self.error is not None else None,
            "commit_transaction_id": self.transaction_id,
        }
        if self.workspace_state == "UNCHANGED":
            payload["commit_result"] = self.commit_result
        return payload


def _settled_state(workspace_path: str, transaction_id: str) -> str:
    """The cycle result the durable commit evidence proves."""
    try:
        state = commit_state_for_transaction(workspace_path, transaction_id)
    except Exception:
        return COMMIT_UNCERTAIN
    if state == CommitState.COMMITTED:
        return COMMIT_COMMITTED
    if state == CommitState.ROLLED_BACK:
        return COMMIT_ROLLED_BACK
    if state == CommitState.NOT_STARTED:
        return COMMIT_NOT_COMMITTED
    return COMMIT_UNCERTAIN


def commit_terminal_candidate(
    writes: List[StagedFileWrite], *, workspace_path: str, transaction_id: str,
    static_analysis: StaticAnalysisCommitGuard,
    verified_candidate: Optional[CandidateVerificationBinding],
    approval: Optional[MutationApproval],
    run_id: Optional[str] = None,
    evidence: Optional[Dict[str, Any]] = None,
    contract_transition: Optional[Callable[[str], Optional[Any]]] = None,
    java_main_classes: Optional[Callable[[Dict[str, str]], Dict[str, str]]] = None,
) -> TerminalCommitOutcome:
    """Commit a materialized, verified candidate into the real workspace.

    ``approval`` (S7, C-03) is the approval decision for THIS batch (kriya/policy/approval.py::MutationApproval). Its subject is recomputed
    from ``writes`` as the last check before the durable intent: a missing decision, one bound to another batch, another run or another
    root, a required approval that was not granted, or one that does not authorize applying, refuses the commit with nothing written.

    ``verified_candidate`` is the binding taken when terminal verification
    started (kriya/workflow/verification_binding.py). It is recomputed from
    ``writes`` first, before anything else: a missing or different binding
    refuses the commit with nothing written, whatever static analysis says.

    ``contract_transition`` (PRD-029) builds the ContractRegistry transition
    from the candidate hash (contract_lifecycle.derive_contract_transition);
    a refusal or an unreadable registry refuses the commit before any
    intent. Controlled commit outcomes are returned, never raised. Any other
    exception settles the cycle from the durable evidence and propagates.
    """
    from kriya.control.contracts import ContractRegistryCorruptError
    from kriya.control.persistence import (
        discard_pending_contract_registry,
        promote_pending_contract_registry,
        stage_pending_contract_registry,
    )
    from kriya.workflow.contract_lifecycle import (
        CONTRACT_REGISTRY_STAGING_FAILED,
        CONTRACT_REGISTRY_TRANSITION_INCOMPLETE,
        ContractTransitionRefused,
    )

    binding_refused = binding_refusal(verified_candidate, writes, workspace_path)
    if binding_refused is not None:
        reason_code, detail = binding_refused
        return TerminalCommitOutcome(
            committed=False, workspace_state="UNCHANGED", commit_result=COMMIT_NOT_COMMITTED,
            transaction_id=transaction_id, reason_code=reason_code, error=VerifiedCandidateStale(detail),
        )
    if not writes:
        return TerminalCommitOutcome(
            committed=True, workspace_state="UNCHANGED", commit_result="NO_CHANGES",
            transaction_id=transaction_id,
        )
    unsafe = semantic_safety_refusal(writes, workspace_path)
    if unsafe is not None:
        reason_code, detail = unsafe
        return TerminalCommitOutcome(
            committed=False, workspace_state="UNCHANGED", commit_result=COMMIT_NOT_COMMITTED,
            transaction_id=transaction_id, reason_code=reason_code, error=SemanticSafetyRefused(detail),
        )
    # JAVA-ENTRYPOINT-IDENTITY-001: the final-content identity of the Maven entry point, judged for every destination the run did not
    # authorize as its isolated candidate; before static analysis, the durable intent and the first workspace byte.
    mismatched = entrypoint_identity_refusal(writes, workspace_path, java_main_classes)
    if mismatched is not None:
        reason_code, detail = mismatched
        return TerminalCommitOutcome(
            committed=False, workspace_state="UNCHANGED", commit_result=COMMIT_NOT_COMMITTED,
            transaction_id=transaction_id, reason_code=reason_code, error=EntrypointIdentityRefused(detail),
        )
    refusal = static_analysis.verify(writes, workspace_path)
    if refusal is not None:
        return TerminalCommitOutcome(
            committed=False, workspace_state="UNCHANGED", commit_result=COMMIT_NOT_COMMITTED,
            transaction_id=transaction_id, reason_code=refusal.reason_code,
            error=StaticAnalysisCommitRefused(refusal.detail),
        )
    static_evidence_id = static_analysis.evidence_id()
    if static_evidence_id is not None:
        evidence = dict(evidence or {})
        evidence["verification_evidence_ids"] = [*evidence.get("verification_evidence_ids", []), static_evidence_id]
    candidate_hash = candidate_digest(writes, workspace_path)
    transition = None
    if contract_transition is not None:
        try:
            transition = contract_transition(candidate_hash=candidate_hash)
        except (ContractTransitionRefused, ContractRegistryCorruptError) as error:
            return TerminalCommitOutcome(
                committed=False, workspace_state="UNCHANGED", commit_result=COMMIT_NOT_COMMITTED,
                transaction_id=transaction_id, reason_code=error.reason_code, error=error,
            )
    # S7: the final application authority. Recomputed from the batch about to be written, after every other refusal and immediately before
    # the durable intent, so nothing between an approval and the first workspace byte can change what is applied under it.
    approval_refused = mutation_approval_refusal(
        approval, subject_digest=bind_candidate(writes, workspace_path).digest, root=os.path.realpath(workspace_path), run_id=run_id)
    if approval_refused is not None:
        reason_code, detail = approval_refused
        return TerminalCommitOutcome(
            committed=False, workspace_state="UNCHANGED", commit_result=COMMIT_NOT_COMMITTED,
            transaction_id=transaction_id, reason_code=reason_code, error=MutationApprovalRefused(detail),
        )
    try:
        run = begin_run_commit(
            workspace_path, transaction_id, candidate_hash=candidate_hash,
            contract_registry=transition.intent() if transition is not None else None,
            approval=approval.record(),
            **(evidence or {}),
        )
    except Exception as error:
        # Without durable intent a crash inside the commit could never be
        # recognized later, so the commit must not start.
        return TerminalCommitOutcome(
            committed=False, workspace_state="UNCHANGED", commit_result=COMMIT_NOT_COMMITTED,
            transaction_id=transaction_id, reason_code="RUN_RECORD_INTENT_NOT_PERSISTED",
            error=error,
        )

    def settle(result: str, outcome: TerminalCommitOutcome) -> TerminalCommitOutcome:
        record_error = settle_run_commit(run, result)
        if record_error is not None:
            outcome.record_errors.append({
                "operation": f"run_record_commit_{result.lower()}", "error": record_error,
            })
        # A cycle proven not to have committed leaves nothing to promote.
        if transition is not None and result in (COMMIT_ROLLED_BACK, COMMIT_NOT_COMMITTED):
            discard_pending_contract_registry(workspace_path, transaction_id)
        return outcome

    if transition is not None:
        try:
            stage_pending_contract_registry(workspace_path, transaction_id, transition.after_payload)
        except Exception as error:
            return settle(COMMIT_NOT_COMMITTED, TerminalCommitOutcome(
                committed=False, workspace_state="UNCHANGED", commit_result=COMMIT_NOT_COMMITTED,
                transaction_id=transaction_id, reason_code=CONTRACT_REGISTRY_STAGING_FAILED, error=error,
            ))

    try:
        result = commit_revision_grounded_batch(
            writes, workspace_path=workspace_path, transaction_id=transaction_id,
        )
    except UncertainCommitError as error:
        return settle(COMMIT_UNCERTAIN, TerminalCommitOutcome(
            committed=False, workspace_state="UNCERTAIN", commit_result=COMMIT_UNCERTAIN,
            transaction_id=transaction_id, reason_code="WORKSPACE_COMMIT_UNCERTAIN", error=error,
        ))
    except (FileRevisionConflict, BatchCommitError) as error:
        settled = _settled_state(workspace_path, transaction_id)
        if settled == COMMIT_UNCERTAIN:
            return settle(COMMIT_UNCERTAIN, TerminalCommitOutcome(
                committed=False, workspace_state="UNCERTAIN", commit_result=COMMIT_UNCERTAIN,
                transaction_id=transaction_id, reason_code="WORKSPACE_COMMIT_UNCERTAIN",
                error=error,
            ))
        conflict = isinstance(error, FileRevisionConflict) or isinstance(
            error.__cause__, FileRevisionConflict,
        )
        return settle(settled, TerminalCommitOutcome(
            committed=False, workspace_state="UNCHANGED", commit_result=settled,
            transaction_id=transaction_id,
            reason_code="WORKSPACE_REVISION_CONFLICT" if conflict else "WORKSPACE_COMMIT_FAILED",
            error=error,
        ))
    except BaseException:
        # Unexpected (including KeyboardInterrupt mid-commit): settle what
        # the evidence proves - IN_PROGRESS becomes UNCERTAIN - then raise.
        settled = _settled_state(workspace_path, transaction_id)
        settle_run_commit(run, settled)
        if transition is not None and settled in (COMMIT_ROLLED_BACK, COMMIT_NOT_COMMITTED):
            discard_pending_contract_registry(workspace_path, transaction_id)
        raise

    mismatch = committed_digest_mismatch(writes)
    if mismatch is not None:
        return settle(COMMIT_UNCERTAIN, TerminalCommitOutcome(
            committed=False, workspace_state="UNCERTAIN", commit_result=COMMIT_UNCERTAIN,
            transaction_id=transaction_id, reason_code=VERIFIED_COMMIT_DIGEST_MISMATCH,
            evidence=result.evidence, error=BatchCommitError(mismatch),
        ))

    if transition is not None:
        try:
            promote_pending_contract_registry(
                workspace_path, transaction_id,
                before_digest=transition.before_digest, after_digest=transition.after_digest,
            )
        except Exception as error:
            # The source bytes are committed; the registry is not. Never a
            # silent stale registry: the cycle stays open for recovery.
            return settle(COMMIT_UNCERTAIN, TerminalCommitOutcome(
                committed=False, workspace_state="UNCERTAIN", commit_result=COMMIT_UNCERTAIN,
                transaction_id=transaction_id, reason_code=CONTRACT_REGISTRY_TRANSITION_INCOMPLETE,
                evidence=result.evidence, error=error,
            ))
    return settle(COMMIT_COMMITTED, TerminalCommitOutcome(
        committed=True, workspace_state="COMMITTED", commit_result=COMMIT_COMMITTED,
        transaction_id=transaction_id, evidence=result.evidence,
        contract_registry=transition.intent() if transition is not None else None,
    ))
