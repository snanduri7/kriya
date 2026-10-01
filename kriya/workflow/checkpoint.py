"""Stage-level checkpoint/resume support for interrupted generate/fix runs.

Checkpoints are written incrementally to `.kriya/checkpoints/<run_id>.json` inside
the target workspace and deleted on normal completion - they only survive on disk
if the process is killed or crashes mid-run. Resume is opt-in only (an explicit
--resume/--resume-id CLI flag); there is no auto-detection from goal text. Drift is
checked strictly: any difference in the workspace git HEAD/dirty-state, the
resolved config, or the goal/error text invalidates the checkpoint entirely rather
than attempting a partial/best-effort resume.
"""
import hashlib
import json
import logging
import os
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from kriya.control.workspace_identity import WorkspaceOwnershipError, ownership_metadata, validate_ownership
from kriya.workflow.resume_fingerprints import (
    CHECKPOINT_KEY as RESUME_FINGERPRINTS_KEY,
)
from kriya.workflow.resume_fingerprints import (
    FINGERPRINT_NAMES,
    STAGE_ORDER,
    Fingerprint,
    FingerprintComparison,
    candidate_integrity_problem,
    compare_resume_fingerprints,
    invalidated_stages_for,
    reused_artifacts_for_checkpoint,
)

logger = logging.getLogger(__name__)

CHECKPOINT_DIR = os.path.join(".kriya", "checkpoints")


def _checkpoints_dir(workspace_path: str) -> str:
    return os.path.join(workspace_path, CHECKPOINT_DIR)


def checkpoint_path(workspace_path: str, run_id: str) -> str:
    return os.path.join(_checkpoints_dir(workspace_path), f"{run_id}.json")


def new_run_id() -> str:
    return time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]


def compute_workspace_fingerprint(workspace_path: str) -> Optional[str]:
    """Git HEAD SHA + dirty/clean marker. None if not a git repo (resume unavailable)."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=workspace_path, capture_output=True, text=True
        )
        if head.returncode != 0:
            return None
        # Exclude .kriya/ (checkpoints, worktree sandbox) from the dirty check -
        # writing a checkpoint file is itself an untracked change under .kriya/,
        # which would otherwise make every workspace look "dirty" the moment a
        # checkpoint is saved and permanently block resuming it.
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", ".", ":!.kriya"],
            cwd=workspace_path, capture_output=True, text=True
        )
        dirty = "dirty" if status.stdout.strip() else "clean"
        return f"{head.stdout.strip()}:{dirty}"
    except Exception as e:
        logger.debug(f"Failed to compute workspace fingerprint for '{workspace_path}': {e}")
        return None


def compute_workspace_content_hash(workspace_path: str) -> Optional[str]:
    """STATE-001 (2026-09-14) fix: a checkpoint-compatibility identity bound
    to the RELEVANT WORKING-TREE CONTENT actually present, not merely
    `compute_workspace_fingerprint()`'s HEAD+dirty-boolean (which cannot
    distinguish two different dirty contents at the same HEAD - reproduced
    and permanently characterized by `tests/test_workflow.py::
    test_checkpoint_workspace_fingerprint_cannot_distinguish_two_different_
    dirty_states`) nor `compute_tree_hash()`'s `HEAD^{tree}` (the COMMITTED
    tree only - independently reproduced to share the exact same blind spot
    for anything uncommitted). Neither of those two functions is modified by
    this fix - both keep their own honest, narrower, still-tested meaning
    (HEAD/dirty display; the literal committed tree object) - this is a
    THIRD, additive, stronger primitive, composed alongside them rather than
    conflated into either (Task 1's own "prefer composition over
    conflation").

    Mechanism: a SCRATCH git index (`GIT_INDEX_FILE` redirected to a private
    temp file for these subprocess calls only - the real repository index/
    HEAD/working tree are never touched, confirmed by direct reproduction:
    `git status`/`git add`/`git commit` run by a human or a concurrent
    process against the REAL index are structurally unaffected, since git's
    own index locking is per-index-file, not global), seeded from HEAD
    (`git read-tree HEAD`), then `git add -A -- . ':!.kriya'` stages the
    CURRENT on-disk content of every tracked-or-untracked-and-not-ignored
    path (deliberately excluding `.kriya/` - see the module docstring's own
    "checkpoint write must not self-invalidate" requirement; reusing the
    workspace's own real `.gitignore` semantics for everything else, rather
    than inventing a second ignore policy - target/build/dist/node_modules/
    venvs/caches already excluded by any real project's own `.gitignore`,
    exactly the existing exclusion this function reuses, never
    re-implements). `git write-tree` then produces a real, canonical,
    content-addressed git tree object hash of that scratch index - stable
    under whitespace-only path reformatting, immune to mtime (git blobs are
    pure content hashes), correct for renames/deletions (a real git tree
    diff, not a path-based heuristic), and correct for symlinks BY
    CONSTRUCTION (git tree entries encode file mode: `120000` for a symlink,
    with the blob holding the RAW TARGET STRING never a dereferenced/
    followed path - a symlink's target changing, or a symlink being
    replaced by a regular file, is therefore always a real tree-content
    difference; git never follows a symlink out of the workspace to hash
    whatever it points to, so this can never widen the read boundary beyond
    what git already scopes to the repository).

    Combined with `compute_base_commit()` (both folded into one sha256, so
    Invariant 1 - "same HEAD + different content = different identity" -
    holds unambiguously even in the vanishingly rare case two different
    HEAD commits happen to produce coincidentally identical scratch trees).

    Deliberate scope choice (Task 14): Kriya's OWN generation/validation
    code always reads WORKING-TREE bytes directly (`open(path).read()` -
    `edit_safety.py`'s `atomic_write_file`, `AuthorizedFileWriter`, every
    compile/test call in `kriya/tools/validate.py`) - it never reads from
    the git INDEX for anything content-relevant. `git add -A`'s own staging
    step above always stages CURRENT on-disk bytes regardless of prior
    staged/unstaged status, so this identity is staged-vs-unstaged
    AGNOSTIC by design: two workspaces with the same on-disk bytes but
    different index staging state get the IDENTICAL identity (matching what
    Kriya would actually read next), never a spurious mismatch over a
    distinction Kriya's own execution semantics do not observe.

    None (fail closed downstream, matching `compute_workspace_fingerprint`/
    `compute_tree_hash`'s own established shape) if not a git repository, if
    HEAD does not resolve (e.g. a repo with zero commits), or any step
    fails for any reason - the scratch index file is always removed in a
    `finally` block regardless of outcome."""
    tmp_index_path = os.path.join(
        tempfile.gettempdir(), f".kriya-scratch-index-{uuid.uuid4().hex}",
    )
    try:
        env = {**os.environ, "GIT_INDEX_FILE": tmp_index_path}
        seed = subprocess.run(
            ["git", "read-tree", "HEAD"], cwd=workspace_path, env=env, capture_output=True, text=True,
        )
        if seed.returncode != 0:
            return None
        add = subprocess.run(
            ["git", "add", "-A", "--", ".", ":!.kriya"],
            cwd=workspace_path, env=env, capture_output=True, text=True,
        )
        if add.returncode != 0:
            return None
        write = subprocess.run(
            ["git", "write-tree"], cwd=workspace_path, env=env, capture_output=True, text=True,
        )
        if write.returncode != 0:
            return None
        working_tree_hash = write.stdout.strip()
        base_commit = compute_base_commit(workspace_path)
        if base_commit is None:
            return None
        return hashlib.sha256(f"{base_commit}\x00{working_tree_hash}".encode("utf-8")).hexdigest()
    except Exception as e:
        logger.debug(f"Failed to compute workspace content hash for '{workspace_path}': {e}")
        return None
    finally:
        try:
            if os.path.exists(tmp_index_path):
                os.remove(tmp_index_path)
        except OSError:
            pass


def compute_config_fingerprint(config_dict: Dict[str, Any]) -> str:
    blob = json.dumps(config_dict, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def save_checkpoint(workspace_path: str, run_id: str, data: Dict[str, Any]) -> None:
    d = _checkpoints_dir(workspace_path)
    try:
        os.makedirs(d, exist_ok=True)
        path = checkpoint_path(workspace_path, run_id)
        tmp_path = path + ".tmp"
        payload = dict(data)
        payload["run_id"] = run_id
        payload["saved_at"] = time.time()
        payload["_workspace"] = ownership_metadata(workspace_path)
        from kriya.control.run_coordinator import current_run_context
        context = current_run_context()
        if context is not None and context.record_revision is not None:
            payload["_run_record"] = {
                "classification": "derived",
                "run_id": context.run_id,
                "revision": context.record_revision,
            }
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp_path, path)
        logger.info(f"Checkpoint saved (run_id={run_id}, stage={data.get('stage')}): {path}")
    except Exception as e:
        logger.warning(f"Failed to save checkpoint '{run_id}' (non-fatal, resume for this run won't be available): {e}")


def load_checkpoint(workspace_path: str, run_id: str) -> Optional[Dict[str, Any]]:
    path = checkpoint_path(workspace_path, run_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        validate_ownership(workspace_path, payload, path)
        return payload
    except WorkspaceOwnershipError:
        raise
    except Exception as e:
        logger.warning(f"Failed to load checkpoint '{path}': {e}")
        return None


def delete_checkpoint(workspace_path: str, run_id: str) -> None:
    path = checkpoint_path(workspace_path, run_id)
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        logger.debug(f"Failed to delete checkpoint '{path}' (non-fatal): {e}")


def list_checkpoints(workspace_path: str) -> List[Dict[str, Any]]:
    d = _checkpoints_dir(workspace_path)
    if not os.path.isdir(d):
        return []
    out = []
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".json"):
            data = load_checkpoint(workspace_path, fn[:-len(".json")])
            if data:
                out.append(data)
    return out


def find_latest_checkpoint(workspace_path: str) -> Optional[str]:
    checkpoints = list_checkpoints(workspace_path)
    if not checkpoints:
        return None
    checkpoints.sort(key=lambda c: c.get("saved_at", 0))
    return checkpoints[-1]["run_id"]


def list_checkpoint_run_references(workspace_path: str) -> List[str]:
    """Run ids whose RunRecord a saved checkpoint references (PRD-008
    retention protects them). A checkpoint that cannot be loaded is one
    resume will not use, so its reference does not need protecting."""
    d = _checkpoints_dir(workspace_path)
    if not os.path.isdir(d):
        return []
    references = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json"):
            continue
        try:
            data = load_checkpoint(workspace_path, fn[:-len(".json")])
        except WorkspaceOwnershipError:
            continue
        reference = (data or {}).get("_run_record")
        if isinstance(reference, dict) and reference.get("run_id"):
            references.append(str(reference["run_id"]))
    return references


# --- MA5.9: control-plane hash bundle + resume-vs-reality validation ---
#
# save_checkpoint() itself is untouched (still a plain Dict[str, Any] - "Do
# not require all fields for legacy checkpoints" is automatically true for a
# schemaless dict). compute_control_plane_hashes() below produces the
# additive hash fields section 29 calls for; a real caller merges its
# result into whatever dict it already passes to save_checkpoint(), e.g.
# `save_checkpoint(ws, run_id, {**existing_data, **compute_control_plane_hashes(...)})`.
# Nothing here changes run_generation_workflow()'s own existing resume/
# drift-check block (workspace/config/goal fingerprints) - this is a
# SEPARATE, additional validation layer a caller opts into, not a
# replacement of the one that already exists and is already load-bearing.

CONTROL_PLANE_CHECKPOINT_SCHEMA_VERSION = 2
# v1 -> v2 (STATE-001, 2026-09-14): added workspace_content_hash - a
# checkpoint saved under schema_version < 2 (or, equivalently, missing this
# key entirely) never had its working-tree content bound at all, only
# tree_hash's own HEAD^{tree} (committed-only) blind spot - such a
# checkpoint is deliberately treated as NOT safely resumable by
# validate_resume_against_reality() below, never silently accepted under
# the older, weaker guarantee (Task 10's own explicit instruction).


def compute_tree_hash(workspace_path: str) -> Optional[str]:
    """git's own tree object hash (`HEAD^{tree}`) - distinct from the
    commit SHA compute_base_commit returns: a tree hash changes only when
    real file CONTENT changes, so it stays stable across an amend/rebase
    that doesn't actually touch content, while still catching any real
    drift a bare commit-SHA comparison might miss in an unusual history
    rewrite. None if not a git repo or git is unavailable - same fail-
    closed shape compute_workspace_fingerprint above already uses."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=workspace_path, capture_output=True, text=True,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except Exception as e:
        logger.debug(f"Failed to compute tree hash for '{workspace_path}': {e}")
        return None


def compute_base_commit(workspace_path: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=workspace_path, capture_output=True, text=True,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except Exception as e:
        logger.debug(f"Failed to compute base commit for '{workspace_path}': {e}")
        return None


def compute_registry_hash(registry_dict: Dict[str, Any]) -> str:
    """Stable sha256 over a control-plane registry's own to_dict() -
    reused for both contract_hash and artifact_registry_hash below (each
    registry's to_dict() already has a well-defined, stable shape - see
    kriya/control/contracts.py::ContractRegistry.to_dict()/kriya/control/
    artifacts.py::ArtifactRegistry.to_dict())."""
    blob = json.dumps(registry_dict, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def compute_control_plane_hashes(
    workspace_path: str,
    control_state: Optional[Any] = None,
    contract_registry: Optional[Any] = None,
    artifact_registry: Optional[Any] = None,
    context_package: Optional[Any] = None,
    plan_hash: Optional[str] = None,
    verification_hash: Optional[str] = None,
    patch_hash: Optional[str] = None,
) -> Dict[str, Any]:
    """Bundles every MA5 checkpoint hash field (section 29) plus
    control_state_hash (needed for section 30's own "validate control-state
    hash" step, implied by but not itself named in section 29's field list)
    into one dict ready to merge into a checkpoint's own data. Every
    argument is optional and independently None-safe - a caller mid-run
    that doesn't yet have, say, a ContextPackage built simply omits
    context_package_hash from the result rather than the whole bundle
    failing. Types are accepted as Any (not kriya.control.state.ControlState
    etc. directly) so this module doesn't need a hard import dependency on
    kriya/control/ for a caller that only wants the git-derived fields."""

    return {
        "schema_version": CONTROL_PLANE_CHECKPOINT_SCHEMA_VERSION,
        "base_commit": compute_base_commit(workspace_path),
        "tree_hash": compute_tree_hash(workspace_path),
        # STATE-001 (2026-09-14): the real, working-tree-content-sensitive
        # identity - see compute_workspace_content_hash()'s own docstring.
        # Always computed (never conditionally, unlike the caller-supplied-
        # object fields below) - its own absence from a loaded checkpoint is
        # therefore always a legacy-schema signal, never "the caller didn't
        # have it yet this time".
        "workspace_content_hash": compute_workspace_content_hash(workspace_path),
        "patch_hash": patch_hash,
        "verification_hash": verification_hash,
        "plan_hash": plan_hash,
        "contract_hash": compute_registry_hash(contract_registry.to_dict()) if contract_registry is not None else None,
        "artifact_registry_hash": compute_registry_hash(artifact_registry.to_dict()) if artifact_registry is not None else None,
        "context_package_hash": context_package.package_hash if context_package is not None else None,
        "control_state_hash": control_state.content_hash() if control_state is not None else None,
    }


class ResumeStatus(str, Enum):
    OK = "ok"
    NEEDS_REVIEW = "needs_review"
    REFUSED = "refused"


class ResumeAction(str, Enum):
    INVALIDATE = "invalidate"
    REFUSE = "refuse"


RESUME_INVALIDATION_MATRIX = {
    "base_commit": (ResumeAction.INVALIDATE, ("context", "candidate", "verification")),
    "tree_hash": (ResumeAction.INVALIDATE, ("context", "candidate", "verification")),
    "workspace_content_hash": (ResumeAction.INVALIDATE, ("context", "candidate", "verification")),
    "control_state_hash": (ResumeAction.INVALIDATE, ("planning", "candidate", "verification")),
    "contract_hash": (ResumeAction.INVALIDATE, ("planning", "candidate", "verification")),
    "artifact_registry_hash": (ResumeAction.INVALIDATE, ("context", "candidate", "verification")),
    # PRD-008: the resume fingerprints. Stages are derived from the
    # reused-artifact dependency matrix (resume_fingerprints.py), never
    # restated here.
    **{name: (ResumeAction.INVALIDATE, invalidated_stages_for(name)) for name in FINGERPRINT_NAMES},
    # A checkpoint whose run record is gone cannot prove where it came
    # from; the workspace-wide commit gate already ran, so nothing unsafe
    # is pending, but nothing from this checkpoint is reused either.
    "run_record_provenance": (ResumeAction.INVALIDATE, STAGE_ORDER),
    # A checkpointed candidate that is incomplete, legacy (no digest) or
    # altered on disk is not rebuilt; the plan it came from may still be.
    "candidate_integrity": (ResumeAction.INVALIDATE, ("candidate", "verification")),
    "commit_state": (ResumeAction.REFUSE, ("mutation_authority", "candidate", "verification")),
}


@dataclass(frozen=True)
class ResumeDecision:
    fingerprint: str
    action: ResumeAction
    invalidated_stages: Tuple[str, ...]
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "action": self.action.value,
            "invalidated_stages": list(self.invalidated_stages),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ResumeValidationResult:
    status: ResumeStatus
    mismatches: Tuple[str, ...] = ()
    decisions: Tuple[ResumeDecision, ...] = ()
    fingerprint_comparisons: Tuple[FingerprintComparison, ...] = ()

    @property
    def invalidated_stages(self) -> Tuple[str, ...]:
        stages = {stage for item in self.decisions for stage in item.invalidated_stages}
        return tuple(stage for stage in STAGE_ORDER if stage in stages)


def validate_resume_against_reality(
    checkpoint_data: Dict[str, Any],
    workspace_path: str,
    control_state: Optional[Any] = None,
    contract_registry: Optional[Any] = None,
    artifact_registry: Optional[Any] = None,
    current_resume_fingerprints: Optional[Mapping[str, Fingerprint]] = None,
    reused_artifacts: Optional[Iterable[str]] = None,
    run_record: Optional[Any] = None,
    run_record_error: Optional[str] = None,
    run_record_missing: Optional[str] = None,
) -> ResumeValidationResult:
    """Section 30's flow: validate checkpoint commit -> validate tree hash
    -> validate control-state hash -> validate contract/artifact registry
    hashes -> NEEDS_REVIEW on any mismatch, never silently rebuilt.
    "Re-run required acceptance" and the actual resume/abort decision are
    the CALLER's job (this is a pure comparison, no side effects) - see
    this module's own docstring for why this is additive to, not a
    replacement of, run_generation_workflow()'s existing workspace/config/
    goal drift check.

    Each individual check only fires when BOTH the checkpoint stored that
    hash AND the caller supplied the matching live object to compare
    against - "do not require all fields for legacy checkpoints" (section
    29) means an old checkpoint or a caller that doesn't have, say, a
    ContractRegistry yet simply skips that one check, not a mismatch.

    ONE DELIBERATE EXCEPTION to that "skip if absent" shape (STATE-001,
    2026-09-14): `workspace_content_hash` is always computable whenever
    `base_commit`/`tree_hash` are (same `workspace_path`, no extra
    caller-supplied live object needed) - its absence from a checkpoint
    therefore never means "the caller didn't have it yet", it means the
    checkpoint was saved under `schema_version < 2`, before this identity
    existed at all, with only `tree_hash`'s own now-known-incomplete
    (committed-tree-only) drift signal. Task 10's own explicit instruction:
    such a checkpoint is not safely resumable and must fail closed with an
    explicit reason, never silently accepted under the weaker guarantee
    it was actually saved with.

    PRD-008 resume fingerprints: when the caller supplies
    ``current_resume_fingerprints`` the checkpoint's stored block is compared
    fingerprint by fingerprint (resume_fingerprints.py). Which fingerprints
    apply follows from the artifacts the resume reuses (``reused_artifacts``,
    or those derived from the checkpoint itself); an applicable fingerprint
    that is missing or UNAVAILABLE on either side is UNVERIFIED and
    invalidates like a change. The earlier flat-key comparison, which
    skipped any fingerprint absent on either side, is gone.

    ``run_record_missing`` names a run the checkpoint references whose
    record no longer exists: its provenance is unknown, so nothing from the
    checkpoint is reused."""

    mismatches: List[str] = []
    decisions: List[ResumeDecision] = []

    def mismatch(fingerprint: str, reason: str) -> None:
        mismatches.append(reason)
        action, stages = RESUME_INVALIDATION_MATRIX[fingerprint]
        decisions.append(ResumeDecision(fingerprint, action, stages, reason))

    stored_base_commit = checkpoint_data.get("base_commit")
    if stored_base_commit is not None:
        current = compute_base_commit(workspace_path)
        if current != stored_base_commit:
            mismatch("base_commit", f"base_commit: checkpoint={stored_base_commit!r} current={current!r}")

    stored_tree_hash = checkpoint_data.get("tree_hash")
    if stored_tree_hash is not None:
        current = compute_tree_hash(workspace_path)
        if current != stored_tree_hash:
            mismatch("tree_hash", f"tree_hash: checkpoint={stored_tree_hash!r} current={current!r}")

    # Only meaningful to require when the checkpoint is otherwise a real
    # control-plane checkpoint at all (i.e. it actually stored a
    # base_commit) - a caller with no git-derived fields whatsoever (an
    # unrelated dict shape reusing this same validator) has nothing to be
    # "legacy" about here; matches the pre-existing base_commit/tree_hash
    # gating pattern of only firing when the checkpoint clearly opted into
    # this control-plane hash bundle at all.
    if stored_base_commit is not None:
        stored_content_hash = checkpoint_data.get("workspace_content_hash")
        if stored_content_hash is None:
            mismatch("workspace_content_hash",
                "workspace_content_hash missing - checkpoint predates the "
                "working-tree-content identity check (schema_version < 2) "
                "and is not safely resumable"
            )
        else:
            current = compute_workspace_content_hash(workspace_path)
            if current is None:
                mismatch("workspace_content_hash",
                    "workspace_content_hash could not be recomputed for the current "
                    "workspace - failing closed rather than assuming unchanged"
                )
            elif current != stored_content_hash:
                mismatch("workspace_content_hash",
                    f"workspace_content_hash: checkpoint={stored_content_hash!r} current={current!r}"
                )

    stored_control_hash = checkpoint_data.get("control_state_hash")
    if stored_control_hash is not None and control_state is not None:
        current = control_state.content_hash()
        if current != stored_control_hash:
            mismatch("control_state_hash", "control_state_hash mismatch")

    stored_contract_hash = checkpoint_data.get("contract_hash")
    if stored_contract_hash is not None and contract_registry is not None:
        current = compute_registry_hash(contract_registry.to_dict())
        if current != stored_contract_hash:
            mismatch("contract_hash", "contract_hash mismatch")

    stored_artifact_hash = checkpoint_data.get("artifact_registry_hash")
    if stored_artifact_hash is not None and artifact_registry is not None:
        current = compute_registry_hash(artifact_registry.to_dict())
        if current != stored_artifact_hash:
            mismatch("artifact_registry_hash", "artifact_registry_hash mismatch")

    comparisons: Tuple[FingerprintComparison, ...] = ()
    if current_resume_fingerprints is not None:
        reused = frozenset(
            reused_artifacts if reused_artifacts is not None
            else reused_artifacts_for_checkpoint(checkpoint_data)
        )
        comparisons = compare_resume_fingerprints(
            checkpoint_data.get(RESUME_FINGERPRINTS_KEY), current_resume_fingerprints, reused,
        )
        for item in comparisons:
            if item.invalidates:
                mismatch(item.name, f"{item.name} {item.status.value}: {item.reason}")
        if "candidate" in reused:
            candidate_problem = candidate_integrity_problem(checkpoint_data)
            if candidate_problem is not None:
                mismatch("candidate_integrity", f"candidate_integrity: {candidate_problem}")

    if run_record_missing is not None:
        mismatch(
            "run_record_provenance",
            f"run_record_provenance: referenced run record {run_record_missing!r} no longer exists",
        )

    if run_record_error is not None:
        # A referenced record that exists but cannot be read may be the only
        # evidence of an interrupted commit (PRD-007): never resume past it.
        mismatch("commit_state", f"commit_state unknown: run record unreadable: {run_record_error}")
    if run_record is not None:
        lifecycle = getattr(getattr(run_record, "lifecycle_state", None), "value", None)
        commit_result = getattr(run_record, "commit_result", None)
        commit_intent = getattr(run_record, "commit_intent", None)
        if getattr(run_record, "commit_state_unknown", False) or lifecycle == "UNCERTAIN" or (
            commit_result == "UNCERTAIN"
        ) or (
            commit_intent is not None and commit_result is None
        ):
            mismatch(
                "commit_state",
                f"commit_state uncertain: lifecycle={lifecycle!r} result={commit_result!r}",
            )

    status = (
        ResumeStatus.REFUSED if any(item.action == ResumeAction.REFUSE for item in decisions)
        else ResumeStatus.NEEDS_REVIEW if mismatches else ResumeStatus.OK
    )
    return ResumeValidationResult(
        status=status, mismatches=tuple(mismatches), decisions=tuple(decisions),
        fingerprint_comparisons=comparisons,
    )
