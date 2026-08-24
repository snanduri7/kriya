"""Git worktree sandbox lifecycle for the Developer + Quality Gates retry loop - create/reset/sync/remove. Extracted from kriya/workflow/workflow.py (2026-08-11 modularization)."""

import asyncio
import difflib
import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from kriya.policy.enforcement import enforce_hard_invariants
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import ActionRequest, ActionType

logger = logging.getLogger(__name__)

# MA4.8 (control-plane implementation plan) - audit-only, module-level since
# this file has no class/instance to hold it (same pattern as
# kriya/workflow/edit_safety.py's MA4.5 integration and kriya/tools/web.py's
# MA4.6 one). See _audit_git_write below.
_execution_policy = ExecutionPolicy()


def _audit_git_write(command: List[str], workspace_path: str) -> None:
    """MA4.8 - ExecutionPolicy consultation, mirroring kriya/core/llm.py's
    _audit_llm_network_access (MA4.3), kriya/tools/validate.py's
    _audit_run_command (MA4.4/4.7), kriya/workflow/edit_safety.py's
    _audit_write_file (MA4.5), and kriya/tools/web.py's
    _audit_network_access (MA4.6): the decision is logged, never branched
    on for ALLOW/ALLOW_SANDBOXED/REQUIRE_APPROVAL/most DENY reasons - those
    can never affect whether the real git command actually runs.

    MA7.3 (2026-08-24): kriya.policy.enforcement.enforce_hard_invariants
    now really raises PolicyDeniedError for a small, fixed set of DENY
    reason_codes (force-push, protected-ref mutation, git config/remote
    mutation) - the same narrow, explicitly-authorized real-enforcement
    pattern as kriya/policy/filesystem.py's AuthorizedFileWriter. A
    PolicyDeniedError propagates out of this function (this function's own
    caller - the bootstrap-commit try/except below - is what actually
    stops the real git command from running); every other exception
    (a broken/misconfigured policy engine, not a real denial) is still
    caught and logged here, exactly as before.

    This is the ONE real GIT_WRITE call this file (or anywhere else in
    Kriya's pipeline - confirmed via a full-codebase grep before
    implementing MA4.8) performs today: create_git_worktree's own
    `git commit --allow-empty` bootstrap for a zero-commit repo, below.
    Real signal: this specific invocation falls through
    kriya/policy/execution.py's _check_git_destructive to its ordinary-write
    GIT_WRITE_REQUIRES_APPROVAL backstop, never one of MA7.3's hard-enforced
    reason_codes - GitTool (plugins/core_tools) has no push/branch-delete/
    config/remote code path at all, so this enforcement is real defense-in-
    depth for a future change here, not the closure of a currently-live gap."""
    try:
        result = enforce_hard_invariants(
            _execution_policy,
            ActionRequest(action_type=ActionType.GIT_WRITE, command=tuple(command), workspace_path=workspace_path),
        )
        logger.debug(
            "MA4 policy audit: GIT_WRITE '%s' -> %s (%s)",
            " ".join(command), result.decision.value, result.reason_code,
        )
    except PolicyDeniedError:
        raise
    except Exception as e:
        logger.debug("MA4 policy audit call failed (ignored, audit-only): %s", e)


def _sync_uncommitted_changes_into_worktree(repo_path: str, worktree_path: str) -> None:
    """After create_git_worktree resets the sandbox to a clean git HEAD checkout, copy
    over any uncommitted changes (modified tracked files, new untracked files) from the
    real workspace so the Developer's sandbox reflects what's actually on disk, not just
    git history. Without this, a project with any uncommitted work - the normal state of
    an in-progress feature branch - looks to the sandbox exactly like a completely fresh
    checkout of HEAD: every uncommitted file the goal expects to already exist (and any
    goal asking to "preserve"/"extend" existing work) silently vanishes from compilation,
    producing confusing "package does not exist" failures that have nothing to do with
    the actual generated code. Confirmed live: an additive goal building on a previous
    run's uncommitted output failed every retry attempt this way, with pom.xml (never
    touched by the Developer, since the goal said to leave it as-is) simply absent from
    the sandbox because it was never git-committed.

    Excludes .kriya/ (checkpoints, this very worktree) via the same pathspec already used
    for workspace-fingerprint dirty checks, and deleted-in-working-tree files are removed
    from the worktree rather than left as a stale HEAD-only copy.

    `--untracked-files=all` is required, not the default mode: a directory that is
    ENTIRELY untracked (no committed content anywhere under it) gets collapsed by git
    into one line (`?? src/`) under the default `normal` mode, rather than one line per
    file - which silently dropped the whole directory here, since the per-line copy loop
    below treats a directory entry as a no-op (`if os.path.isdir(src): continue`) and
    never recurses into it. Confirmed live: a from-scratch `src/` tree (regenerated by a
    prior run but never git-committed) never reached the worktree at all, producing a
    "package does not exist" compile failure that looked like a model mistake but was
    purely this sync gap."""
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all", "--", ".", ":!.kriya"],
            cwd=repo_path, capture_output=True, text=True,
        )
        if status.returncode != 0 or not status.stdout.strip():
            return
        for line in status.stdout.splitlines():
            if len(line) < 4:
                continue
            code, rest = line[:2], line[3:]
            # Renames: "R  old -> new" - treat as delete-old + copy-new.
            if code.startswith("R") and " -> " in rest:
                old_rel, rest = rest.split(" -> ", 1)
                old_abs = os.path.join(worktree_path, old_rel.strip().strip('"'))
                if os.path.exists(old_abs):
                    os.remove(old_abs)
            rel = rest.strip().strip('"')
            if not rel or rel.startswith(".kriya"):
                continue
            src = os.path.join(repo_path, rel)
            dst = os.path.join(worktree_path, rel)
            if code.strip() == "D" or not os.path.exists(src):
                if os.path.exists(dst):
                    try:
                        os.remove(dst)
                    except IsADirectoryError:
                        shutil.rmtree(dst, ignore_errors=True)
                continue
            if os.path.isdir(src):
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
    except Exception as e:
        logger.warning(f"Failed to sync uncommitted changes into worktree sandbox (non-fatal, sandbox may be missing uncommitted work): {e}")


def _resolve_repo_head(repo_path: str) -> Optional[str]:
    """Resolves repo_path's actual current HEAD commit SHA. Needed because a
    worktree created with `git worktree add --detach` gets its own fixed HEAD
    pointer at creation time, not a moving ref to repo_path's branch - so
    `git checkout -f HEAD` run *inside* that worktree resolves against its own
    frozen pointer and is a no-op, never advancing to match new commits landed
    on repo_path since. Returns None if resolution fails (e.g. an empty repo
    with no commits yet), in which case callers fall back to the old "HEAD"
    behavior rather than crash."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_path, capture_output=True, text=True,
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception as e:
        logger.debug(f"Failed to resolve HEAD for '{repo_path}': {e}")
    return None


def create_git_worktree(repo_path: str) -> str:
    # 1. Quick pre-check: Is this a git repository?
    try:
        res = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=repo_path, capture_output=True, text=True)
        if res.returncode != 0:
            raise ValueError("Not a git repository")
    except Exception as e:
        raise ValueError(f"Directory is not a git repository: {e}") from e

    # 1b. `git worktree add --detach` needs a commit-ish to detach at - a repo
    # with zero commits has no HEAD and this fails with exit 128, silently
    # falling back to the unisolated real workspace (see the "Falling back to
    # default workspace" warning at this function's only caller) for every
    # milestone until something else happens to create a first commit.
    # Confirmed live, 2026-08-22 (ignite_qpid_protocol): a fresh repo lost
    # worktree isolation on milestones 1 AND 2 this way - isolation only
    # started working on milestone 3, once an unrelated skill-verification
    # auto-commit had incidentally created the repo's first commit. An empty
    # initial commit is enough to give worktree creation something to detach
    # at, and is safe: it adds no files, so it can't collide with or hide any
    # real content the target repo already has.
    if _resolve_repo_head(repo_path) is None:
        try:
            bootstrap_commit_command = [
                "git", "commit", "--allow-empty", "-m", "Kriya: initial commit (empty) to enable worktree isolation",
            ]
            _audit_git_write(bootstrap_commit_command, repo_path)
            subprocess.run(
                bootstrap_commit_command,
                cwd=repo_path, check=True, capture_output=True,
            )
        except Exception as e:
            logger.warning(
                f"Repo at '{repo_path}' has no commits yet and creating an initial empty commit "
                f"failed ({e}) - worktree creation will likely fail and fall back to the unisolated workspace."
            )

    worktree_path = os.path.join(repo_path, ".kriya", "worktree")
    os.makedirs(os.path.dirname(worktree_path), exist_ok=True)

    # 2. Prune any stale/orphaned worktree records in git administrative data
    try:
        subprocess.run(["git", "worktree", "prune"], cwd=repo_path, capture_output=True)
    except Exception as e:
        logger.debug(f"git worktree prune failed (non-fatal): {e}")

    worktree_registered = False
    try:
        res = subprocess.run(["git", "worktree", "list"], cwd=repo_path, capture_output=True, text=True)
        if worktree_path in res.stdout:
            worktree_registered = True
    except Exception as e:
        logger.debug(f"git worktree list failed, assuming worktree is not registered: {e}")

    if not worktree_registered:
        if os.path.exists(worktree_path):
            shutil.rmtree(worktree_path, ignore_errors=True)
        subprocess.run(["git", "worktree", "add", "--detach", worktree_path], cwd=repo_path, check=True, capture_output=True)
    else:
        # Recreate the directory physically if it was deleted but still registered
        if not os.path.exists(worktree_path):
            try:
                subprocess.run(["git", "worktree", "prune"], cwd=repo_path, capture_output=True)
            except Exception as e:
                logger.debug(f"git worktree prune failed (non-fatal): {e}")
            subprocess.run(["git", "worktree", "add", "--detach", worktree_path], cwd=repo_path, check=True, capture_output=True)
        else:
            # Reset but preserve target/ and other build directories. "HEAD" here
            # must be resolved against repo_path, not checked out literally inside
            # the worktree - see _resolve_repo_head for why.
            target = _resolve_repo_head(repo_path)
            subprocess.run(["git", "checkout", "-f", target or "HEAD"], cwd=worktree_path, check=True, capture_output=True)
            subprocess.run(["git", "clean", "-fd"], cwd=worktree_path, check=True, capture_output=True)

    # A worktree only ever reflects git HEAD - it knows nothing about uncommitted
    # changes in the real workspace, which is the normal state of an in-progress
    # project. Copy those over now so the sandbox matches what's actually on disk.
    _sync_uncommitted_changes_into_worktree(repo_path, worktree_path)

    return worktree_path


_SNAPSHOT_FALLBACK_IGNORED_DIRS = {
    ".git", ".kriya", "__pycache__", "node_modules", "target", ".venv", "venv",
}


def _snapshot_all_files_without_git(worktree_path: str) -> Optional[set]:
    """Plain filesystem-walk fallback for snapshot_untracked_files() when
    worktree_path isn't a git repository at all (git status itself fails) -
    exactly the case create_git_worktree() already falls back to operating
    directly on the real workspace for (see its own "Falling back to default
    workspace" warning). Without this, snapshot_untracked_files() returned
    None and clean_untracked_files_since() silently no-op'd, so the runtime-
    state-leak bug that pair of functions exists to close (see
    clean_untracked_files_since's own docstring) reproduced identically in a
    non-git workspace - confirmed live, 2026-08-21 (milestone_task_cli): task
    IDs climbed 1->5 across retry attempts with the exact same signature as
    the original python_task_tracker incident this mechanism was built for.

    There is no tracked/untracked distinction without git, so this returns
    every file currently on disk under worktree_path - correct for this
    caller's purpose regardless: clean_untracked_files_since() only ever acts
    on paths present in a LATER snapshot but absent from this one, i.e. files
    a run just created, never anything that already existed. Ignores the same
    directories _list_workspace_files() in kriya/workflow/milestones.py
    already treats as noise/build-cache, so compile artifacts aren't misread
    as runtime state and wiped between attempts."""
    try:
        files = set()
        for root, dirs, filenames in os.walk(worktree_path):
            dirs[:] = [d for d in dirs if d not in _SNAPSHOT_FALLBACK_IGNORED_DIRS]
            for fn in filenames:
                files.add(os.path.relpath(os.path.join(root, fn), worktree_path))
        return files
    except OSError as e:
        logger.debug(f"Failed to snapshot files without git in '{worktree_path}' (non-fatal): {e}")
        return None


def snapshot_untracked_files(worktree_path: str) -> Optional[set]:
    """Returns the set of currently-untracked file paths (relative to
    worktree_path) - the same `git status --porcelain --untracked-files=all`
    source _sync_uncommitted_changes_into_worktree already uses, so a
    directory that's entirely untracked is enumerated file-by-file rather
    than collapsed into one `?? dir/` line. Falls back to
    _snapshot_all_files_without_git() (see its own docstring) when git itself
    isn't usable in this worktree, rather than returning None and leaving the
    caller unable to clean up at all. Still returns None (not an empty set)
    if even that fallback fails, so a caller can distinguish "definitely no
    untracked files" from "couldn't tell" and skip cleanup rather than risk
    deleting something real off an unreliable read.

    Paired with clean_untracked_files_since() below - see that function's own
    docstring for the real incident this closes (Runtime Verification's
    generated-app runtime state, e.g. a JSON store, leaking across retry
    attempts inside the same reused worktree)."""
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=worktree_path, capture_output=True, text=True,
        )
        if res.returncode != 0:
            return _snapshot_all_files_without_git(worktree_path)
    except Exception as e:
        logger.debug(f"Failed to snapshot untracked files in '{worktree_path}' via git (non-fatal): {e}")
        return _snapshot_all_files_without_git(worktree_path)
    files = set()
    for line in res.stdout.splitlines():
        if line.startswith("??"):
            files.add(line[3:].strip().strip('"'))
    return files


def clean_untracked_files_since(worktree_path: str, baseline: Optional[set]) -> None:
    """Removes any file that became untracked in the worktree SINCE `baseline`
    was snapshotted (via snapshot_untracked_files, taken immediately before
    the just-finished operation) - i.e. exactly what that operation created,
    nothing that already existed beforehand.

    Added 2026-08-17 after the corpus survey's dig into `run_verification`
    found a real, previously-undiscovered mechanism bug: run_app_sequence()
    deliberately runs each verification command in the SAME worktree
    directory so state one step creates (a JSON file, a database) is visible
    to the next step WITHIN one attempt - correct and necessary for a
    goal like "add a task, then list it". But the worktree itself is reused
    across an ENTIRE run's retry attempts (create_git_worktree's own
    docstring: "so compile caches... survive between retries"), and nothing
    ever cleaned up what a PRIOR attempt's own verification run wrote to
    disk. Confirmed live (python_task_tracker, runs b-6/b-7, 2026-08-16):
    attempt 1's generated code was actually CORRECT (task added as id 1,
    marked done, correctly excluded from the final pending list) - grade()
    hallucinated a failure ("Task 1 should still be listed as completed",
    not anything the goal actually asked for), triggering a retry. From
    there, EVERY subsequent attempt's verification ran against the SAME
    tasks.json the previous attempt's run had already written to - task
    IDs kept climbing (1,2 -> ... -> 5,6 by attempt 7) and `done 1` failed
    every time because task 1 had already been consumed/renumbered by an
    earlier attempt's leftover state, producing a cascade of "the done
    command doesn't work" failures that had nothing to do with the
    generated code, which never changed in the way that mattered.

    Snapshotting immediately before run_app_sequence() and cleaning up
    immediately after (regardless of pass/fail) means every attempt's
    verification always starts from the same "freshly compiled, never run"
    baseline - compile-time build caches (target/, node_modules/, __pycache__/)
    are untouched since they already existed in `baseline` by the time this
    runs (compile always happens earlier in the same attempt), only files
    the run itself just created get removed. A None baseline (snapshot
    failed) or None/empty diff is a silent no-op, not an error - this is a
    hygiene improvement, never a gate that can itself fail the run."""
    if baseline is None:
        return
    current = snapshot_untracked_files(worktree_path)
    if current is None:
        return
    for rel in current - baseline:
        full = os.path.join(worktree_path, rel)
        try:
            if os.path.isdir(full):
                shutil.rmtree(full, ignore_errors=True)
            elif os.path.exists(full):
                os.remove(full)
        except OSError as e:
            logger.debug(f"Failed to clean up runtime artifact '{rel}' after verification run (non-fatal): {e}")


def remove_git_worktree(repo_path: str, worktree_path: str) -> None:
    """Despite the name, this resets the worktree back to repo_path's current
    commit rather than truly deleting it - the worktree is deliberately reused
    across runs so compile caches inside it (target/, node_modules/, etc.)
    survive between retries and between separate generate/fix invocations. See
    _resolve_repo_head for why "HEAD" can't be checked out literally here -
    without it, this "cleanup" was a no-op that left the worktree permanently
    frozen at whatever commit existed the first time it was ever created,
    silently hiding every commit made since from any future run that doesn't
    itself rewrite the affected files."""
    if os.path.exists(worktree_path):
        try:
            target = _resolve_repo_head(repo_path)
            subprocess.run(["git", "checkout", "-f", target or "HEAD"], cwd=worktree_path, capture_output=True)
            subprocess.run(["git", "clean", "-fd"], cwd=worktree_path, capture_output=True)
        except Exception as e:
            logger.debug(f"Failed to clean up worktree at '{worktree_path}' (non-fatal): {e}")
