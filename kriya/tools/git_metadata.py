"""OD-1 (BACKEND-FINAL-CLOSURE-005): sanitized, read-only Git metadata for
contained builds that deterministically require it.

Measured need (BACKEND-READINESS-004, T3 JavaHamcrest): ``gradle/versioning.gradle``
runs ``git describe --tags`` at configuration time; Kriya presents every
candidate tree as "no repository" (the dangling-gitfile mask, the authority
export without ``.git``), so the build fails before any task runs
(GRADLE-GIT-AT-CONFIGURATION-BOUNDARY-001).

Owner security boundary (2026-10-08): export only the minimum metadata and
object graph the measured operations need - ``git describe``, ``git rev-parse``,
baseline ancestry and the reachable tag/ref metadata. Concretely:

- COMMIT objects reachable from the baseline revision and the TAG objects of
  the annotated tags reachable from it. No TREE or BLOB objects, ever: the
  export is deliberately not ``git fsck``-clean, because historical source
  content needs its own demonstrated necessity and authority (an operation
  that walks trees, such as ``git describe --dirty`` or ``git diff``, fails
  inside the container instead of silently widening exposure).
- Refs: ``HEAD`` -> ``refs/heads/kriya-base`` -> the baseline revision, plus
  ``refs/tags/*`` reachable from it. No other branches or tags.
- Nothing mutable or ambient: no remotes, credentials, hooks, reflogs, stash,
  alternates, index or templates; a fresh ``config`` written by Kriya.
- Bound to the workspace identity and the exact baseline revision, content-
  addressed (``digest``), stored under the Kriya state root, mounted read-only
  by the OCI backend (kriya/tools/containment_oci.py); stale or wrong-workspace
  metadata fails closed with a typed reason (``GitMetadataError``); without a
  valid bound export the existing no-Git masking stays the fallback.
- One config kill switch: ``autonomy.git_metadata_export`` (SEC-009:
  SECURITY_AUTHORITY, a repository can never flip it).

The host-side git commands here are read-only queries of the operator's own
repository (``rev-parse``, ``rev-list``, ``for-each-ref``, ``pack-objects``),
the same class of query kriya/workflow/worktree.py already runs; kriya/tools/
never imports kriya/workflow/, so the helper lives here.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from kriya.tools.containment import ContainmentSetupError, GitMetadataMount

GIT_METADATA_FORMAT = "kriya.git_metadata/1"
GIT_METADATA_DIR = "git-metadata"
GIT_METADATA_POLICY = "commits-and-tags-only"
# Where the OCI backend mounts the sanitized repository when the workspace's
# own ``.git`` is a worktree gitfile (kriya/tools/containment_oci.py); the
# Kriya-owned gitfile below points here.
CONTAINER_GIT_METADATA = "/kriya/gitmeta"
BASE_REF = "refs/heads/kriya-base"

GIT_METADATA_STALE = "GIT_METADATA_STALE"
GIT_METADATA_WORKSPACE_MISMATCH = "GIT_METADATA_WORKSPACE_MISMATCH"
GIT_METADATA_CORRUPT = "GIT_METADATA_CORRUPT"
GIT_METADATA_EXPORT_FAILED = "GIT_METADATA_EXPORT_FAILED"

_GIT_TIMEOUT_SECONDS = 180
_SHA = re.compile(r"^[0-9a-f]{40}$")
# A fresh config: the only keys the export may carry.
_CONFIG = ("[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = false\n\tlogallrefupdates = false\n")
_FORBIDDEN_CONFIG_PREFIXES = ("remote.", "credential.", "url.", "include.", "includeif.", "core.hookspath",
                              "core.sshcommand", "core.gitproxy", "core.fsmonitor", "core.alternaterefscommand",
                              "core.pager", "core.editor", "diff.", "merge.", "filter.", "uploadpack.", "receive.",
                              "alias.", "http.", "protocol.", "safe.")
_FORBIDDEN_ENTRIES = ("hooks", "logs", "index", "description", "info", "worktrees", "modules", "packed-refs.lock",
                      "COMMIT_EDITMSG", "FETCH_HEAD", "ORIG_HEAD", "shallow")


class GitMetadataError(ContainmentSetupError):
    """A bound Git metadata export cannot be used: typed, never a silent fallback."""

    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {message}")


@dataclass(frozen=True)
class GitMetadataExport:
    root: str
    repo_dir: str
    gitfile_path: str
    base_revision: str
    identity_path: str
    digest: str
    object_count: int
    refs: Dict[str, str]

    def mount(self) -> GitMetadataMount:
        return GitMetadataMount(repo_dir=self.repo_dir, gitfile_path=self.gitfile_path,
                                base_revision=self.base_revision, digest=self.digest)


def _git(cwd: str, *args: str, input_bytes: Optional[bytes] = None) -> bytes:
    completed = subprocess.run(["git", *args], cwd=cwd, input=input_bytes, capture_output=True, check=True,
                               timeout=_GIT_TIMEOUT_SECONDS)
    return completed.stdout


def _git_text(cwd: str, *args: str) -> str:
    return _git(cwd, *args).decode("utf-8", errors="replace")


def workspace_head(path: str) -> Optional[str]:
    """The commit a checkout (a repository or a worktree) is at, None when
    ``path`` is not inside a git checkout or has no commit."""
    if not os.path.isdir(path):
        return None
    try:
        head = _git_text(path, "rev-parse", "--verify", "HEAD^{commit}").strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    return head if _SHA.match(head) else None


def workspace_identity_key(identity_path: str) -> str:
    """The export's workspace key: the identity path's realpath, hashed (the
    same keying the dependency cache uses)."""
    return hashlib.sha256(os.path.realpath(identity_path).encode("utf-8")).hexdigest()[:16]


def export_root(state_root: str, identity_path: str, base_revision: str) -> str:
    return os.path.join(os.path.realpath(state_root), GIT_METADATA_DIR, workspace_identity_key(identity_path),
                        base_revision)


def _reachable_tags(repo_path: str, base: str) -> List[Tuple[str, str, str]]:
    """(refname, object sha, object type) of every tag whose commit is reachable from ``base``."""
    out = _git_text(repo_path, "for-each-ref", "--merged", base,
                    "--format=%(refname)%00%(objectname)%00%(objecttype)", "refs/tags")
    tags: List[Tuple[str, str, str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        refname, sha, kind = line.split("\0")
        tags.append((refname, sha, kind))
    return tags


def _export_digest(repo_dir: str) -> str:
    """Content address of the export: HEAD, every ref and every object (sha
    and type) - computed from the repository, never trusted from a file."""
    head = open(os.path.join(repo_dir, "HEAD"), "r", encoding="utf-8").read()
    refs = sorted(_git_text(repo_dir, "for-each-ref", "--format=%(refname) %(objectname)").splitlines())
    objects = sorted(_git_text(repo_dir, "cat-file", "--batch-all-objects",
                               "--batch-check=%(objectname) %(objecttype)").splitlines())
    payload = json.dumps({"format": GIT_METADATA_FORMAT, "policy": GIT_METADATA_POLICY, "head": head,
                          "refs": refs, "objects": objects}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _policy_violations(repo_dir: str) -> List[str]:
    """Everything in the export that the boundary forbids: stray entries,
    alternates, forbidden config keys, any non-commit/tag object."""
    violations: List[str] = []
    for entry in _FORBIDDEN_ENTRIES:
        if os.path.lexists(os.path.join(repo_dir, entry)):
            violations.append(f"entry {entry!r} present")
    if os.path.lexists(os.path.join(repo_dir, "objects", "info", "alternates")):
        violations.append("objects/info/alternates present")
    try:
        config = _git_text(repo_dir, "config", "--list", "--file", os.path.join(repo_dir, "config"))
    except subprocess.CalledProcessError as error:
        return violations + [f"config unreadable: {error.stderr.decode(errors='replace').strip()}"]
    for line in config.splitlines():
        key = line.split("=", 1)[0].strip().lower()
        if key.startswith(_FORBIDDEN_CONFIG_PREFIXES):
            violations.append(f"config key {key!r}")
    objects = _git_text(repo_dir, "cat-file", "--batch-all-objects", "--batch-check=%(objectname) %(objecttype)")
    for line in objects.splitlines():
        if line.split()[-1] not in ("commit", "tag"):
            violations.append(f"object {line.strip()} is neither a commit nor a tag")
            break
    return violations


def _write_ref(repo_dir: str, refname: str, sha: str) -> None:
    target = os.path.join(repo_dir, *refname.split("/"))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(sha + "\n")


def _manifest_path(root: str) -> str:
    return os.path.join(root, "manifest.json")


def _load_export(root: str) -> GitMetadataExport:
    try:
        with open(_manifest_path(root), "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError) as error:
        raise GitMetadataError(GIT_METADATA_CORRUPT, f"manifest unreadable at {root}: {error}") from error
    if manifest.get("format") != GIT_METADATA_FORMAT or manifest.get("policy") != GIT_METADATA_POLICY:
        raise GitMetadataError(GIT_METADATA_CORRUPT, f"unexpected manifest format/policy at {root}")
    try:
        return GitMetadataExport(
            root=root, repo_dir=os.path.join(root, "repo"), gitfile_path=os.path.join(root, "gitfile"),
            base_revision=str(manifest["base_revision"]), identity_path=str(manifest["identity_path"]),
            digest=str(manifest["digest"]), object_count=int(manifest["object_count"]),
            refs=dict(manifest["refs"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise GitMetadataError(GIT_METADATA_CORRUPT, f"manifest incomplete at {root}: {error}") from error


def verify_git_metadata(root: str, *, identity_path: str, base_revision: str) -> GitMetadataExport:
    """The export at ``root``, if it binds exactly to ``identity_path`` and
    ``base_revision`` and is byte-for-byte what Kriya exported (digest, HEAD,
    gitfile, policy); every other state is a typed refusal."""
    export = _load_export(root)
    if export.identity_path != os.path.realpath(identity_path):
        raise GitMetadataError(GIT_METADATA_WORKSPACE_MISMATCH,
                               f"export at {root} was bound to {export.identity_path}, not {os.path.realpath(identity_path)}")
    if export.base_revision != base_revision:
        raise GitMetadataError(GIT_METADATA_STALE,
                               f"export at {root} binds base {export.base_revision[:12]}, the workspace is at {base_revision[:12]}")
    if not os.path.isdir(export.repo_dir):
        raise GitMetadataError(GIT_METADATA_CORRUPT, f"sanitized repository missing at {export.repo_dir}")
    try:
        head = _git_text(export.repo_dir, "rev-parse", "--verify", "HEAD^{commit}").strip()
        digest = _export_digest(export.repo_dir)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as error:
        raise GitMetadataError(GIT_METADATA_CORRUPT, f"sanitized repository unreadable at {export.repo_dir}: {error}") from error
    if head != base_revision or digest != export.digest:
        raise GitMetadataError(GIT_METADATA_CORRUPT, f"export at {root} does not match its manifest (HEAD {head[:12]}, digest {digest[:12]})")
    try:
        with open(export.gitfile_path, "r", encoding="utf-8") as handle:
            gitfile = handle.read()
    except OSError as error:
        raise GitMetadataError(GIT_METADATA_CORRUPT, f"gitfile unreadable at {export.gitfile_path}: {error}") from error
    if gitfile != f"gitdir: {CONTAINER_GIT_METADATA}\n":
        raise GitMetadataError(GIT_METADATA_CORRUPT, f"gitfile at {export.gitfile_path} was altered")
    violations = _policy_violations(export.repo_dir)
    if violations:
        raise GitMetadataError(GIT_METADATA_CORRUPT, f"export at {root} violates the metadata boundary: " + "; ".join(violations))
    return export


def export_git_metadata(repo_path: str, state_root: str, *, identity_path: str) -> GitMetadataExport:
    """Build (or reuse, after verification) the sanitized export of the
    checkout at ``repo_path`` for its current HEAD, bound to ``identity_path``.
    Pure given the repository's objects and refs: the same baseline yields
    the same digest."""
    base = workspace_head(repo_path)
    if base is None:
        raise GitMetadataError(GIT_METADATA_EXPORT_FAILED, f"{repo_path} is not a git checkout with a commit")
    root = export_root(state_root, identity_path, base)
    if os.path.isdir(root):
        return verify_git_metadata(root, identity_path=identity_path, base_revision=base)
    parent = os.path.dirname(root)
    os.makedirs(parent, exist_ok=True)
    build = tempfile.mkdtemp(prefix=".build-", dir=parent)
    try:
        repo_dir = os.path.join(build, "repo")
        template = os.path.join(build, "template")
        os.makedirs(template)
        _git(build, "init", "-q", "--bare", f"--template={template}", repo_dir)
        shutil.rmtree(template)
        for entry in _FORBIDDEN_ENTRIES:  # whatever this git version's init created beyond objects/refs
            stray = os.path.join(repo_dir, entry)
            if os.path.isdir(stray) and not os.path.islink(stray):
                shutil.rmtree(stray)
            elif os.path.lexists(stray):
                os.remove(stray)
        commits = _git_text(repo_path, "rev-list", base).split()
        tags = _reachable_tags(repo_path, base)
        objects = commits + [sha for _ref, sha, kind in tags if kind == "tag"]
        pack = _git(repo_path, "pack-objects", "--stdout", "-q", input_bytes=("\n".join(objects) + "\n").encode("utf-8"))
        _git(repo_dir, "unpack-objects", "-q", input_bytes=pack)
        with open(os.path.join(repo_dir, "config"), "w", encoding="utf-8") as handle:
            handle.write(_CONFIG)
        refs: Dict[str, str] = {BASE_REF: base}
        _write_ref(repo_dir, BASE_REF, base)
        for refname, sha, _kind in tags:
            _write_ref(repo_dir, refname, sha)
            refs[refname] = sha
        with open(os.path.join(repo_dir, "HEAD"), "w", encoding="utf-8") as handle:
            handle.write(f"ref: {BASE_REF}\n")
        # The commit graph must be complete: every ancestor is present, nothing else is.
        exported = _git_text(repo_dir, "rev-list", base).split()
        if sorted(exported) != sorted(commits):
            raise GitMetadataError(GIT_METADATA_EXPORT_FAILED,
                                   f"commit graph incomplete: {len(exported)} of {len(commits)} commits exported")
        violations = _policy_violations(repo_dir)
        if violations:
            raise GitMetadataError(GIT_METADATA_EXPORT_FAILED, "export violates the metadata boundary: " + "; ".join(violations))
        with open(os.path.join(build, "gitfile"), "w", encoding="utf-8") as handle:
            handle.write(f"gitdir: {CONTAINER_GIT_METADATA}\n")
        digest = _export_digest(repo_dir)
        manifest = {
            "format": GIT_METADATA_FORMAT, "policy": GIT_METADATA_POLICY, "base_revision": base,
            "identity_path": os.path.realpath(identity_path), "workspace_key": workspace_identity_key(identity_path),
            "source_checkout": os.path.realpath(repo_path), "container_gitdir": CONTAINER_GIT_METADATA,
            "refs": refs, "object_count": len(objects), "digest": digest,
            "excluded": ["trees", "blobs", "unreachable refs", "remotes", "credentials", "hooks", "reflogs", "stash",
                         "alternates", "index"],
        }
        with open(_manifest_path(build), "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=1, sort_keys=True)
        try:
            os.rename(build, root)
        except OSError:
            if not os.path.isdir(root):  # not a concurrent writer: a real failure
                raise
            shutil.rmtree(build, ignore_errors=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as error:
        shutil.rmtree(build, ignore_errors=True)
        detail = error.stderr.decode(errors="replace").strip() if isinstance(error, subprocess.CalledProcessError) else str(error)
        raise GitMetadataError(GIT_METADATA_EXPORT_FAILED, f"could not export the Git metadata of {repo_path}: {detail}") from error
    except GitMetadataError:
        shutil.rmtree(build, ignore_errors=True)
        raise
    return verify_git_metadata(root, identity_path=identity_path, base_revision=base)


def bound_git_metadata(*, mounted_workspace: str, original_workspace: Optional[str], state_root: str,
                       enabled: bool) -> Optional[GitMetadataMount]:
    """The read-only metadata mount for a contained run of ``mounted_workspace``
    (the tree the container sees), or None when the switch is off or neither
    the mounted tree nor ``original_workspace`` (the run's checkout, when the
    mounted tree is a Kriya-owned copy without ``.git``) is a git checkout -
    a non-git project is unaffected. The export binds to the original
    workspace's identity and the checkout's exact HEAD; a mounted checkout at
    another revision than the original is STALE (typed refusal)."""
    if not enabled:
        return None
    identity = original_workspace or mounted_workspace
    mounted_head = workspace_head(mounted_workspace)
    source = mounted_workspace if mounted_head is not None else original_workspace
    if source is None:
        return None
    base = mounted_head if mounted_head is not None else workspace_head(source)
    if base is None:
        return None
    if original_workspace and mounted_head is not None:
        original_head = workspace_head(original_workspace)
        if original_head is not None and original_head != mounted_head:
            raise GitMetadataError(GIT_METADATA_STALE,
                                   f"the mounted checkout is at {mounted_head[:12]}, the run's workspace at {original_head[:12]}")
    return export_git_metadata(source, state_root, identity_path=identity).mount()
