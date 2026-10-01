"""Git worktree sandbox lifecycle for the Developer + Quality Gates retry loop - create/reset/sync/remove. Extracted from kriya/workflow/workflow.py (2026-08-11 modularization)."""

import hashlib
import logging
import os
import re
import shutil
import subprocess
from typing import List, Optional, Tuple

from kriya.policy.enforcement import enforce_hard_invariants
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import ActionRequest, ActionType
from kriya.workflow.file_integrity import WORKTREE_CONTENT_MISMATCH, WORKTREE_SYNC_FAILED, file_raw_digest

logger = logging.getLogger(__name__)

# SEC-001-P1 (2026-09-11): every Kriya-internal git invocation that can
# trigger a repository-defined hook (checkout, worktree add - which
# implicitly checks out - and commit) gets this prepended. A target repo
# (adversarial or merely pre-existing) can define .git/hooks/post-checkout,
# pre-commit, post-commit, etc. - none of Kriya's own control-plane git
# operations are supposed to execute repository-controlled code as a side
# effect of managing the sandbox worktree, and none did intentionally, but
# nothing previously suppressed them either (SEC-001 execution-surface
# inventory finding). `core.hooksPath=/dev/null` is a real git mechanism
# (not a Kriya invention) - git looks up hooks under this path and finds
# nothing, so every hook type is suppressed uniformly rather than
# enumerating one --no-verify-style flag per hook type (--no-verify only
# covers pre-commit/commit-msg, not post-checkout/post-commit). Read-only
# plumbing (status/rev-parse/worktree list/worktree prune) is untouched -
# it cannot trigger a hook and this stays a minimal, targeted diff.
_HOOKS_DISABLED = ["-c", "core.hooksPath=/dev/null"]

# WORKTREE-CANONICAL-ROOT-001: where Kriya-managed worktrees live. A workspace's
# own reusable worktree is <workspace>/.kriya/worktree (or the scoped snapshot
# <workspace>/.kriya/scoped-worktree). A worktree for an isolated candidate
# workspace the active run authorized (the enforce plan worktree, in which the
# subtask engine runs) is a sibling rooted at the run's canonical workspace:
# <workspace>/.kriya/worktrees/<name>, never inside the candidate. No
# Kriya-managed worktree is ever a descendant of another one.
WORKSPACE_WORKTREE = (".kriya", "worktree")
WORKSPACE_SCOPED_WORKTREE = (".kriya", "scoped-worktree")
CANDIDATE_WORKTREES = (".kriya", "worktrees")


class NestedWorktreeError(RuntimeError):
    """A Kriya-managed worktree would be created inside another one."""


def managed_worktree_ancestor(path: str) -> Optional[str]:
    """The Kriya-managed worktree location ``path`` lies strictly inside, or
    None. A managed location itself is not inside one."""
    parts = os.path.realpath(path).split(os.sep)
    for index, part in enumerate(parts[:-1]):
        if part != ".kriya":
            continue
        following = parts[index + 1]
        if following in (WORKSPACE_WORKTREE[1], WORKSPACE_SCOPED_WORKTREE[1]):
            end = index + 2
        elif following == CANDIDATE_WORKTREES[1] and index + 2 < len(parts):
            end = index + 3
        else:
            continue
        if end < len(parts):
            return os.sep.join(parts[:end])
    return None


def managed_worktree_path(repo_path: str, *, scoped: bool = False) -> str:
    """Where the worktree for ``repo_path`` lives. It is rooted at the
    canonical workspace: ``repo_path`` itself, or, for a candidate workspace
    the active run authorized, that run's workspace (supplied by the
    RunCoordinator, never inferred from the working directory). The candidate's
    name is derived from its path relative to that root, so a resumed run
    resolves the same worktree. Raises NestedWorktreeError rather than place
    one managed worktree inside another."""
    from kriya.control.run_coordinator import candidate_workspace_root

    canonical = os.path.realpath(repo_path)
    root = candidate_workspace_root(canonical)
    if root is None:
        path = os.path.join(canonical, *(WORKSPACE_SCOPED_WORKTREE if scoped else WORKSPACE_WORKTREE))
    else:
        relative = os.path.relpath(canonical, root)
        hint = re.sub(r"[^A-Za-z0-9]+", "-", relative).strip("-")[:40] or "workspace"
        digest = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:8]
        path = os.path.join(root, *CANDIDATE_WORKTREES, f"{'scoped-' if scoped else ''}candidate-{hint}-{digest}")
    ancestor = managed_worktree_ancestor(path)
    if ancestor is not None:
        raise NestedWorktreeError(
            f"refusing a Kriya-managed worktree at {path!r}: it would be inside the Kriya-managed worktree "
            f"{ancestor!r}. A worktree for a candidate workspace is rooted at the run's canonical workspace; "
            "this path is not a candidate the active run authorized."
        )
    return path


def _registered_worktrees(repo_path: str) -> List[str]:
    res = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=repo_path, capture_output=True, text=True)
    return [os.path.realpath(line[len("worktree "):]) for line in res.stdout.splitlines() if line.startswith("worktree ")]


def _remove_legacy_nested_worktrees(repo_path: str, registered: List[str]) -> None:
    """Remove what the pre-WORKTREE-CANONICAL-ROOT-001 layout left behind.

    Kriya now refuses to create a managed worktree inside another one, so a
    registered worktree of this repository that lies inside one of this
    repository's own registered managed worktrees (the old subtask worktree
    ``<ws>/.kriya/worktree/.kriya/worktree``) was left by an earlier version.
    Resetting the outer worktree never removes it: ``git clean`` skips a
    directory holding a ``.git`` file. The main worktree, and anything whose
    managed ancestor is not a worktree of this repository, is never touched.
    A locked one is removed too (``--force`` twice). Innermost first: removing
    an outer one first can delete an inner one's ``.git`` file while it stays
    registered, which git then refuses to remove. A removal that fails
    raises, and both callers fail closed."""
    registered_set = set(registered)
    nested = [path for path in registered if managed_worktree_ancestor(path) in registered_set]
    for path in sorted(nested, key=len, reverse=True):
        logger.warning("Removing a nested Kriya worktree left by an earlier layout: %s", path)
        subprocess.run(["git", "worktree", "remove", "--force", "--force", path],
                       cwd=repo_path, check=True, capture_output=True)


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
        entries = git_status_entries(repo_path, untracked="all", pathspec=(".", ":!.kriya"))
    except Exception as error:
        raise WorktreeSyncError(WORKTREE_SYNC_FAILED, f"git status failed: {error}") from error
    synced: List[str] = []
    try:
        for _code, rel, original in entries:
            if rel == ".kriya" or rel.startswith(".kriya/"):
                continue
            if original and not os.path.lexists(os.path.join(repo_path, original)):
                _remove_path(os.path.join(worktree_path, original))
                synced.append(original)
            src = os.path.join(repo_path, rel)
            dst = os.path.join(worktree_path, rel)
            if not os.path.lexists(src):
                _remove_path(dst)
                synced.append(rel)
                continue
            if os.path.isdir(src) and not os.path.islink(src):
                continue  # an untracked nested repository/submodule directory
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.lexists(dst) and (os.path.islink(dst) or os.path.isdir(dst) or os.path.islink(src)):
                _remove_path(dst)
            if os.path.islink(src):
                os.symlink(os.readlink(src), dst)
            else:
                shutil.copy2(src, dst, follow_symlinks=False)
            synced.append(rel)
    except Exception as error:
        raise WorktreeSyncError(WORKTREE_SYNC_FAILED, f"copying uncommitted work failed: {error}") from error
    mismatched = [rel for rel in synced if _path_identity(os.path.join(repo_path, rel))
                  != _path_identity(os.path.join(worktree_path, rel))]
    if mismatched:
        raise WorktreeSyncError(
            WORKTREE_CONTENT_MISMATCH,
            f"the sandbox does not hold the workspace's bytes after sync: {', '.join(mismatched[:10])}",
        )


def repository_content_paths(workspace_path: str) -> List[str]:
    """The repository's own content under ``workspace_path``: every path Git
    tracks there (``git ls-files -z``, relative to the workspace - also for a
    workspace nested inside an enclosing repository), Kriya's ``.kriya/``
    state excluded. Untracked output (target/, build/, caches) is never
    repository content. A workspace that is not a Git work tree has none (a
    real run never gets here: create_git_worktree bootstraps Git first); any
    other failure to list it raises (fail closed)."""
    inside = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=workspace_path,
                            capture_output=True, text=True)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return []
    result = subprocess.run(["git", "ls-files", "-z"], cwd=workspace_path, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"git ls-files failed: {os.fsdecode(result.stderr).strip() or result.returncode}")
    return [path for path in (os.fsdecode(item) for item in result.stdout.split(b"\0") if item)
            if path != ".kriya" and not path.startswith(".kriya/")]


class WorktreeSyncError(RuntimeError):
    """FILE-INTEGRITY-CONTRACT-001: the sandbox could not be made to hold the
    workspace's uncommitted work exactly; nothing is verified against it."""

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code


def git_status_entries(
    repo_path: str, *, untracked: str = "all", pathspec: Tuple[str, ...] = (),
) -> List[Tuple[str, str, Optional[str]]]:
    """``git status --porcelain=v1 -z`` as (XY code, path, original path of a
    rename/copy). NUL-delimited, so spaces, tabs, newlines and non-UTF-8
    bytes in names reach the caller exactly (never Git's quoted display
    form). Raises on a failed ``git status``."""
    command = ["git", "status", "--porcelain=v1", "-z", f"--untracked-files={untracked}"]
    if pathspec:
        command += ["--", *pathspec]
    result = subprocess.run(command, cwd=repo_path, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(os.fsdecode(result.stderr).strip() or f"exit {result.returncode}")
    records = result.stdout.split(b"\0")
    entries: List[Tuple[str, str, Optional[str]]] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4 or record[2:3] != b" ":
            raise RuntimeError(f"unparseable git status record {record!r}")
        code, path = record[:2].decode("ascii"), os.fsdecode(record[3:])
        original = None
        if code[0] in "RC":
            if index >= len(records) or not records[index]:
                raise RuntimeError(f"rename/copy record without its original path: {record!r}")
            original = os.fsdecode(records[index])
            index += 1
        entries.append((code, path, original))
    return entries


def _remove_path(path: str) -> None:
    if os.path.islink(path) or os.path.isfile(path):
        os.remove(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)


def _path_identity(path: str) -> Optional[Tuple[str, str]]:
    """What a path is, byte-exactly: absent, a link (its target) or a file
    (its raw digest). Directories are not synced and compare as themselves."""
    if not os.path.lexists(path):
        return None
    if os.path.islink(path):
        return ("link", os.readlink(path))
    if os.path.isdir(path):
        return ("dir", "")
    return ("file", file_raw_digest(path))


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


_SCOPED_SNAPSHOT_SENTINEL = ".kriya-scoped-snapshot"
_SCOPED_SNAPSHOT_IGNORED = {".git", ".kriya"}


def _bootstrap_greenfield_repository(workspace_path: str) -> None:
    """Initialize a truly non-Git workspace before any model-originated write.

    Nested workspaces never reach this helper: Git discovery succeeds for
    them and create_git_worktree keeps using its scoped-snapshot boundary.
    Command-local identity avoids mutating repository/global configuration.
    Any failure propagates so sandboxed generation fails closed.
    """
    init_command = ["git", *_HOOKS_DISABLED, "init"]
    _audit_git_write(init_command, workspace_path)
    initialized = subprocess.run(
        init_command, cwd=workspace_path, capture_output=True, text=True,
    )
    if initialized.returncode != 0:
        raise RuntimeError(
            f"greenfield Git initialization failed: {initialized.stderr.strip() or initialized.stdout.strip()}"
        )

    info_dir = os.path.join(workspace_path, ".git", "info")
    os.makedirs(info_dir, exist_ok=True)
    exclude_path = os.path.join(info_dir, "exclude")
    existing_excludes = ""
    try:
        with open(exclude_path, "r", encoding="utf-8") as handle:
            existing_excludes = handle.read()
    except FileNotFoundError:
        pass
    if ".kriya/" not in existing_excludes.splitlines():
        with open(exclude_path, "a", encoding="utf-8") as handle:
            if existing_excludes and not existing_excludes.endswith("\n"):
                handle.write("\n")
            handle.write(".kriya/\n")

    commit_command = [
        "git", *_HOOKS_DISABLED, "-c", "user.name=Kriya", "-c", "user.email=kriya@local",
        "commit", "--allow-empty", "-m", "Kriya: initial commit to enable isolation",
    ]
    _audit_git_write(commit_command, workspace_path)
    committed = subprocess.run(
        commit_command, cwd=workspace_path, capture_output=True, text=True,
    )
    if committed.returncode != 0:
        raise RuntimeError(
            f"greenfield Git bootstrap commit failed: {committed.stderr.strip() or committed.stdout.strip()}"
        )


def _resolved_git_toplevel(workspace_path: str) -> Optional[str]:
    """Return Git's owning worktree root for ``workspace_path``, if any."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=workspace_path, capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return os.path.realpath(result.stdout.strip())
    except Exception as exc:
        logger.debug("Failed to resolve Git top-level for '%s': %s", workspace_path, exc)
    return None


def _create_scoped_snapshot_sandbox(workspace_path: str, sandbox_path: str) -> str:
    """Isolate a requested directory that is merely nested inside another repo.

    A Git worktree always checks out the owning repository's complete root. If
    ``workspace_path`` is only an untracked/ordinary directory below that root,
    using ``git worktree add`` silently imports the enclosing project's files
    and build markers into Quality Gates. A filesystem snapshot preserves the
    caller's explicit workspace boundary while retaining the same apply-back
    contract used by the normal sandbox.
    """
    if os.path.exists(sandbox_path):
        shutil.rmtree(sandbox_path)
    os.makedirs(sandbox_path, exist_ok=True)

    for entry in os.listdir(workspace_path):
        if entry in _SCOPED_SNAPSHOT_IGNORED:
            continue
        source = os.path.join(workspace_path, entry)
        destination = os.path.join(sandbox_path, entry)
        if os.path.isdir(source) and not os.path.islink(source):
            shutil.copytree(source, destination, symlinks=True)
        elif os.path.islink(source):
            os.symlink(os.readlink(source), destination)
        else:
            shutil.copy2(source, destination)

    # Stop Git commands issued from inside the snapshot from walking upward
    # and rediscovering the enclosing repository we deliberately excluded.
    # Build the small on-disk shape of an empty repository directly. This is a
    # real Git discovery boundary (`rev-parse --show-toplevel` resolves to the
    # snapshot), but does not depend on spawning `git init`; validation tests
    # and embedders may replace subprocess/Popen globally while testing build
    # commands. No commit or application-repository mutation is involved.
    git_dir = os.path.join(sandbox_path, ".git")
    os.makedirs(os.path.join(git_dir, "objects", "info"), exist_ok=True)
    os.makedirs(os.path.join(git_dir, "objects", "pack"), exist_ok=True)
    os.makedirs(os.path.join(git_dir, "refs", "heads"), exist_ok=True)
    os.makedirs(os.path.join(git_dir, "refs", "tags"), exist_ok=True)
    with open(os.path.join(git_dir, "HEAD"), "w", encoding="utf-8") as head:
        head.write("ref: refs/heads/main\n")
    with open(os.path.join(git_dir, "config"), "w", encoding="utf-8") as config:
        config.write(
            "[core]\n"
            "\trepositoryformatversion = 0\n"
            "\tfilemode = true\n"
            "\tbare = false\n"
            "\tlogallrefupdates = true\n"
        )
    with open(os.path.join(git_dir, "description"), "w", encoding="utf-8") as description:
        description.write("Kriya workspace-scoped snapshot sandbox\n")
    with open(os.path.join(sandbox_path, _SCOPED_SNAPSHOT_SENTINEL), "w", encoding="utf-8") as marker:
        marker.write("workspace-scoped sandbox; not an enclosing-repository checkout\n")
    return sandbox_path


def git_read_lines(cwd: str, *args: str) -> List[str]:
    """A read-only git query (``rev-parse``, ``ls-tree``, ``diff --name-only``,
    ``ls-files``) in ``cwd``: its non-empty output lines. Raises on failure,
    so a caller that needs the answer can fail closed."""
    completed = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True, timeout=60)
    return [line for line in completed.stdout.splitlines() if line.strip()]


def create_git_worktree(repo_path: str) -> str:
    # 1. Quick pre-check: Is this a git repository?
    try:
        res = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=repo_path, capture_output=True, text=True)
        if res.returncode != 0:
            logger.info(
                "Workspace '%s' is not a Git repository; initializing the "
                "greenfield repository before creating its isolated worktree.", repo_path,
            )
            _bootstrap_greenfield_repository(os.path.realpath(repo_path))
    except Exception as e:
        if not os.path.isdir(repo_path):
            raise ValueError(f"Workspace directory is unavailable: {e}") from e
        raise RuntimeError(f"Git repository detection/bootstrap failed for {repo_path!r}: {e}") from e

    # `git rev-parse --is-inside-work-tree` is true for every ordinary
    # directory nested below a repository. That does not make the directory
    # itself the requested project's repository root. Checking out the owning
    # repository here contaminated a from-scratch Maven workspace with Kriya's
    # parent requirements.txt/pyproject.toml, causing the ecosystem invariant
    # to reject pom.xml indefinitely. Preserve the explicit workspace boundary.
    workspace_realpath = os.path.realpath(repo_path)
    git_toplevel = _resolved_git_toplevel(repo_path)
    if git_toplevel is not None and git_toplevel != workspace_realpath:
        logger.info(
            "Workspace '%s' is nested inside enclosing Git repository '%s'; "
            "using a workspace-scoped snapshot sandbox.",
            workspace_realpath, git_toplevel,
        )
        return _create_scoped_snapshot_sandbox(workspace_realpath, managed_worktree_path(repo_path, scoped=True))

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
                "git", *_HOOKS_DISABLED, "-c", "user.name=Kriya", "-c", "user.email=kriya@local",
                "commit", "--allow-empty", "-m", "Kriya: initial commit (empty) to enable worktree isolation",
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

    worktree_path = managed_worktree_path(repo_path)
    os.makedirs(os.path.dirname(worktree_path), exist_ok=True)

    # 2. Prune any stale/orphaned worktree records in git administrative data
    try:
        subprocess.run(["git", "worktree", "prune"], cwd=repo_path, capture_output=True)
    except Exception as e:
        logger.debug(f"git worktree prune failed (non-fatal): {e}")

    try:
        registered = _registered_worktrees(repo_path)
    except Exception as e:
        logger.debug(f"git worktree list failed, assuming worktree is not registered: {e}")
        registered = []
    _remove_legacy_nested_worktrees(repo_path, registered)
    # The managed path is never nested (managed_worktree_path refuses), so the
    # removal above cannot change whether it is registered. An exact path match: <ws>/.kriya/worktree is a string prefix of
    # every <ws>/.kriya/worktrees/<name>, so a substring test is wrong.
    worktree_registered = os.path.realpath(worktree_path) in registered

    if not worktree_registered:
        if os.path.exists(worktree_path):
            shutil.rmtree(worktree_path, ignore_errors=True)
        subprocess.run(["git", *_HOOKS_DISABLED, "worktree", "add", "--detach", worktree_path], cwd=repo_path, check=True, capture_output=True)
    else:
        # Recreate the directory physically if it was deleted but still registered
        if not os.path.exists(worktree_path):
            try:
                subprocess.run(["git", "worktree", "prune"], cwd=repo_path, capture_output=True)
            except Exception as e:
                logger.debug(f"git worktree prune failed (non-fatal): {e}")
            subprocess.run(["git", *_HOOKS_DISABLED, "worktree", "add", "--detach", worktree_path], cwd=repo_path, check=True, capture_output=True)
        else:
            # Reset but preserve target/ and other build directories. "HEAD" here
            # must be resolved against repo_path, not checked out literally inside
            # the worktree - see _resolve_repo_head for why.
            target = _resolve_repo_head(repo_path)
            subprocess.run(["git", *_HOOKS_DISABLED, "checkout", "-f", target or "HEAD"], cwd=worktree_path, check=True, capture_output=True)
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
        entries = git_status_entries(worktree_path, untracked="all")
    except Exception as e:
        logger.debug(f"Failed to snapshot untracked files in '{worktree_path}' via git (non-fatal): {e}")
        return _snapshot_all_files_without_git(worktree_path)
    return {path for code, path, _original in entries if code == "??"}


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


def _is_scoped_snapshot(worktree_path: str) -> bool:
    """A Kriya scoped snapshot sandbox at a managed location (never an
    arbitrary directory that happens to carry the sentinel)."""
    path = os.path.realpath(worktree_path)
    parent, name = os.path.split(path)
    at_managed_location = (
        (name == WORKSPACE_SCOPED_WORKTREE[1] and os.path.basename(parent) == ".kriya")
        or (name.startswith("scoped-candidate-") and parent.endswith(os.path.join(*CANDIDATE_WORKTREES)))
    )
    return at_managed_location and os.path.exists(os.path.join(path, _SCOPED_SNAPSHOT_SENTINEL))


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
    if _is_scoped_snapshot(worktree_path):
        try:
            shutil.rmtree(worktree_path)
        except OSError as e:
            logger.debug(f"Failed to clean up scoped snapshot at '{worktree_path}' (non-fatal): {e}")
        return

    if os.path.exists(worktree_path):
        try:
            target = _resolve_repo_head(repo_path)
            subprocess.run(["git", *_HOOKS_DISABLED, "checkout", "-f", target or "HEAD"], cwd=worktree_path, capture_output=True)
            subprocess.run(["git", "clean", "-fd"], cwd=worktree_path, capture_output=True)
        except Exception as e:
            logger.debug(f"Failed to clean up worktree at '{worktree_path}' (non-fatal): {e}")
