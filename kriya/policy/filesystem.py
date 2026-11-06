"""Authorized filesystem writes - MA4.16 of the control-plane implementation
plan (see kriya/policy/__init__.py for MA4's overall principle). Closes the
gap flagged since MA4.5 in kriya/workflow/edit_safety.py's atomic_write_file
docstring and kriya/policy/execution.py's own _check_filesystem docstring:
atomic_write_file is a pure path-in/bytes-in primitive with no workspace-
root context, so ExecutionPolicy's stage 2 containment rule could never
fire for either of Kriya's two real content-write call sites
(kriya/workflow/attempt.py, kriya/workflow/self_correction.py).

DELIBERATE, EXPLICITLY-AUTHORIZED EXCEPTION TO MA4's AUDIT-ONLY MANDATE:
every other MA4.1-4.15 integration is audit-only by design - a policy
decision is computed and logged but never gates real behavior, because a
false positive there (e.g. MA4.4's command allowlist denying Kriya's own
normal toolchain) would break legitimate autonomous operation. This module
is different, by the explicit direction of the user who set that mandate
in the first place (2026-08-24): a write escaping its authorized workspace
root, or landing on a real credential-store file, is never a legitimate
Kriya action a human needs to judge - it is always either a bug or an
attack, so failing loudly here carries none of the false-positive risk
that kept MA4's general policy engine audit-only. AuthorizedFileWriter
therefore REALLY enforces (raises PolicyDeniedError), and is the first
real-enforcement code path anywhere in MA4.

Layering (each piece keeps exactly one job):
  Workflow / self-correction code (knows the real workspace/worktree root)
    -> AuthorizedFileWriter.commit_file / commit_batch (this module)
         -> resolves the target canonically (symlinks included) and
            consults ExecutionPolicy for containment + sensitive-path
         -> on DENY: raises PolicyDeniedError, nothing is written
         -> on ALLOW: delegates to edit_safety.py's existing
            commit_revision_grounded_file / commit_revision_grounded_batch
              -> revision-conflict-safe atomic_write_file (unchanged,
                 still policy-unaware, still a pure primitive - this
                 module does not modify it or edit_safety.py's revision
                 logic at all)

Sensitive-path patterns here are DELIBERATELY NARROWER than ExecutionPolicy's
own default set (kriya/policy/execution.py's
_DEFAULT_SENSITIVE_PATH_PATTERN_STRINGS, still used unchanged by every
audit-only call site - llm.py, validate.py, edit_safety.py's own audit
call, web.py, worktree.py, workflow.py). That default's `credentials`/
`secrets`/`password` patterns are bare, unanchored substrings - harmless
for a logged signal, but too broad for a real, silent DENY: they would
block a perfectly legitimate generated file like `password_validator.py`
or `credentials_service.py`. This module's own patterns require those
three to look like an actual credential-store file (a path component that
IS "secrets"/"credentials"/"password", optionally with an extension - a
literal `secrets.json`, `credentials.yaml`, `password.txt` - not any
filename that merely contains the word as a substring), plus common
private-key file shapes (`id_rsa` and siblings, `.pem`, `.key`). Confirmed
with the user directly before implementing (2026-08-24) rather than
silently narrowing or silently reusing the broad set.
"""

import os
import stat
from dataclasses import dataclass
from enum import Enum
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

from kriya.config.environment import expand_user
from kriya.platform.filesystem_semantics import (
    LinkSharing,
    PathIdentity,
    PathRelation,
    fold_name,
    link_sharing,
    object_key,
    path_identity,
    path_relation,
)
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import ActionRequest, ActionType, PolicyDecision, PolicyResult
from kriya.repository.commit import (
    StagedFileWrite,
    commit_revision_grounded_batch,
    commit_revision_grounded_file,
)


class WriteScopeMode(str, Enum):
    """Explicit write-scope policy for one AuthorizedFileWriter instance -
    makes the previously-overloaded meaning of an empty `allowed_relpaths`
    unambiguous. Found live, PRV-05 (2026-08-28): a verification-only
    subtask (kriya/workflow/plan_schema.py::ExecutionRole.VERIFICATION)
    passed allowed_relpaths=() intending "write nothing", but this class's
    own enforcement only activated `if self._allowed_relpaths and ...` - an
    empty collection was silently treated as "no restriction" instead,
    because that is ALSO what every ordinary top-level `kriya generate` call
    means when it passes the same empty collection (it never scopes writes
    at all). One falsy value cannot honestly carry both meanings.

    UNRESTRICTED: allowed_relpaths ignored - existing top-level generation
        behavior, unchanged.
    ALLOWLIST: writes permitted only to paths in allowed_relpaths.
    DENY_ALL: no persistent write is permitted at all, regardless of
        allowed_relpaths - enforced here, in the writer itself, not merely
        implied by an empty planned_files list or a Planner-side validation
        rule. A bug anywhere upstream (Developer, retry/self-correction
        recovery loop) that somehow still requests a write cannot grant
        repository mutation once this mode is set - the real security
        boundary, not a convenience check."""

    UNRESTRICTED = "unrestricted"
    ALLOWLIST = "allowlist"
    DENY_ALL = "deny_all"

_ENFORCEMENT_SENSITIVE_PATH_PATTERNS: Tuple[str, ...] = (
    r"(^|/)\.ssh(/|$)",
    r"(^|/)\.aws(/|$)",
    r"(^|/)\.kube(/|$)",
    r"(^|/)\.gnupg(/|$)",
    r"(^|/)\.env(\.[A-Za-z0-9_.-]+)?$",
    r"(^|/)id_(rsa|dsa|ecdsa|ed25519)$",
    r"\.pem$",
    r"\.key$",
    r"(^|/)credentials(\.[A-Za-z0-9]+)?($|/)",
    r"(^|/)secrets?(\.[A-Za-z0-9]+)?($|/)",
    r"(^|/)passwords?(\.[A-Za-z0-9]+)?($|/)",
)


# PLAT-039: repository metadata and Kriya's own control state. Candidate
# (model-directed) write authority never reaches them, under any spelling;
# Kriya's own stores write `.kriya/` through kriya/control/control_store.py.
TRUSTED_CONTROL_DIRECTORIES: Tuple[str, ...] = (".git", ".kriya")
# S18d (OI-150 c): the file that marks a Kriya scoped-snapshot candidate (kriya/workflow/worktree.py writes it and
# imports the name from here - one definition). Kriya control state like .kriya: never a candidate write target, never
# a planned read's source.
SCOPED_SNAPSHOT_SENTINEL = ".kriya-scoped-snapshot"
# Every name the PLAT-039 rule protects, at any depth and under any spelling.
TRUSTED_CONTROL_NAMES: Tuple[str, ...] = TRUSTED_CONTROL_DIRECTORIES + (SCOPED_SNAPSHOT_SENTINEL,)


_FOLDED_CONTROL_NAMES = frozenset(fold_name(name) for name in TRUSTED_CONTROL_NAMES)
# The one reason code for every refusal of a trusted control path (a candidate write, a planned read, and - OI-164 - a
# sandbox copy of another name for a control object): one cause, one code.
TRUSTED_CONTROL_PATH_DENIED = "TRUSTED_CONTROL_PATH_DENIED"


def is_trusted_control_name(name: str) -> bool:
    """Whether one path component is a name the PLAT-039 rule protects (any spelling)."""
    return fold_name(name) in _FOLDED_CONTROL_NAMES


def names_trusted_control_path(relative_path: str) -> bool:
    """Whether a path RELATIVE to a root names a trusted control path by its components alone (any component, as git's
    own verify_path does: a nested .git is as live as the top-level one) - the name half of
    ``is_trusted_control_path``. A path leaving the root (a leading ``..``) names none of that root's."""
    parts = os.path.normpath(relative_path).split(os.sep)
    return parts[0] != os.pardir and any(is_trusted_control_name(part) for part in parts)


class ControlObjectIdentities:
    """OI-164: the filesystem identities (``object_key``: device + inode) of every object at a trusted control path of
    ``roots`` - everything beneath a `.git`/`.kriya` directory and every `.git` gitfile and scoped-snapshot sentinel, at
    any depth and under any spelling (the names ``is_trusted_control_path`` protects) - so that ANOTHER name for one of
    them (a hard link) is recognised as that object. Only a non-directory with more than one link can have another
    name (``link_sharing``), so a target with one link never builds the index.

    Bounded to ONE operation (one planned read, listing or search; one writer decision): built lazily on the first
    shared target and dropped with this object - never cached across operations, views, candidates or workspaces, so
    a link or control object created after one operation is judged afresh by the next.

    Several roots (a planned read's owner workspace and candidate): each root's objects are collected, and a directory
    that is another of the roots is left to that root's own walk - a run's candidate lives under the owner's `.kriya`,
    yet its source is not control state (the innermost-root rule of plugins/core_tools._is_control_path). Symlinks are
    not followed, except a control-NAMED symlink directly in a root, which ``path_relation`` resolves (everything it
    reaches is control state); deeper, the name rule judges a symlink by where it resolves, so it is not followed. The
    walk is in name order (deterministic). Any directory or entry that cannot be read or has no usable identity makes
    the index incomplete, and every shared target then counts as a control object (fail closed)."""

    def __init__(self, roots: Sequence[str]) -> None:
        self._roots: Tuple[str, ...] = tuple(roots)
        self._keys: Optional[FrozenSet[Tuple[int, int]]] = None
        self._complete = True

    def aliases_control_object(self, target: str) -> bool:
        """Whether ``target`` (symlinks followed) is another name for a control object of the roots. A target whose
        identity cannot be established counts as one (fail closed)."""
        sharing, key = link_sharing(target)
        if sharing is LinkSharing.SINGLE:
            return False
        if sharing is LinkSharing.UNKNOWN:
            return True
        if self._keys is None:
            self._keys = self._collect()
        return not self._complete or key in self._keys

    def _collect(self) -> FrozenSet[Tuple[int, int]]:
        root_keys: Dict[Tuple[int, int], str] = {}
        for root in self._roots:
            try:
                key = object_key(os.stat(root))
            except FileNotFoundError:  # nothing exists beneath a missing root
                continue
            except (OSError, ValueError):
                key = None
            if key is None:
                self._complete = False
                return frozenset()
            root_keys[key] = root
        keys: Set[Tuple[int, int]] = set()
        visited: Dict[Tuple[int, int], bool] = {}
        for root_key, root in root_keys.items():
            if not self._walk(root, root_key, frozenset(root_keys) - {root_key}, keys, visited):
                self._complete = False
                return frozenset()
        return frozenset(keys)

    @staticmethod
    def _walk(root: str, root_key: Tuple[int, int], other_roots: FrozenSet[Tuple[int, int]],
              keys: Set[Tuple[int, int]], visited: Dict[Tuple[int, int], bool]) -> bool:
        """Collect into ``keys``; False when the tree cannot be read completely."""
        stack: List[Tuple[str, Tuple[int, int], bool]] = [(root, root_key, False)]
        while stack:
            directory, directory_key, inside_control = stack.pop()
            # A directory is walked once, or twice when it is first reached as ordinary and then through a
            # control-named symlink (its objects are then control objects too, as path_relation judges them).
            if directory_key in visited and (visited[directory_key] or not inside_control):
                continue
            visited[directory_key] = inside_control
            try:
                with os.scandir(directory) as iterator:
                    entries = sorted(iterator, key=lambda entry: entry.name)
            except FileNotFoundError:
                continue  # removed while walking: nothing beneath it to alias
            except (OSError, ValueError):
                return False
            for entry in entries:
                control = inside_control or is_trusted_control_name(entry.name)
                try:
                    # Outside a control tree only directories matter (they may hold a nested control directory); the
                    # entry type comes from the directory listing itself where the host provides it.
                    if not control and not entry.is_dir(follow_symlinks=False):
                        continue
                    # A control-NAMED entry directly in the root is resolved, as path_relation resolves it; nothing
                    # else is followed.
                    follow = control and not inside_control and directory_key == root_key
                    result = os.stat(entry.path) if follow else os.lstat(entry.path)
                except FileNotFoundError:
                    continue
                except (OSError, ValueError):
                    return False  # an unreadable entry may be, or hide, a control object
                key = object_key(result)
                if key is None:
                    return False
                if stat.S_ISDIR(result.st_mode):
                    if key not in other_roots:
                        stack.append((entry.path, key, control))
                elif control:
                    keys.add(key)
        return True


def is_trusted_control_path(root: str, target: str, *, identities: Optional[ControlObjectIdentities] = None) -> bool:
    """Whether ``target`` is, or lies beneath, a trusted control directory
    of ``root``. By filesystem identity, so symlink aliases and (on an
    insensitive filesystem) case/normalization variants count; and by name,
    case- and normalization-insensitively on every host, because a `.GIT/`
    written on a case-sensitive filesystem becomes the real `.git/` once the
    repository is checked out on an insensitive one; at any depth. A
    location that cannot be established counts as trusted (fail closed).

    OI-164: also when ``target`` is another name (a hard link) for an object
    at such a path - judged by ``identities`` (the operation's index over
    its roots), else by a fresh index over ``root`` alone."""
    if any(path_relation(os.path.join(root, name), target) is not PathRelation.OUTSIDE
           for name in TRUSTED_CONTROL_NAMES):
        return True
    try:
        if names_trusted_control_path(os.path.relpath(os.path.realpath(target), os.path.realpath(root))):
            return True
    except ValueError:  # another drive: not beneath root
        pass
    return (identities if identities is not None else ControlObjectIdentities((root,))).aliases_control_object(target)


def trusted_control_path_denial(target_path: str) -> PolicyResult:
    """The one DENY a candidate write to a trusted control path gets, from
    the writer and from any earlier gate that refuses it before touching
    the filesystem."""
    return PolicyResult(
        decision=PolicyDecision.DENY,
        reason_code=TRUSTED_CONTROL_PATH_DENIED,
        explanation=(
            f"'{target_path}' is repository metadata or Kriya control state "
            f"({', '.join(TRUSTED_CONTROL_DIRECTORIES)}); candidate writes never reach it."
        ),
        matched_rule="filesystem.authorized_writer.trusted_control_path",
    )


def trusted_control_path_read_denial(target_path: str) -> PolicyResult:
    """S18d (OI-150 c): the one DENY a model-authored read of a trusted control path gets - repository metadata and
    Kriya control state are never reported as source (the same reason code as the write refusal; one cause, one
    code)."""
    return PolicyResult(
        decision=PolicyDecision.DENY,
        reason_code=TRUSTED_CONTROL_PATH_DENIED,
        explanation=(
            f"'{target_path}' is repository metadata or Kriya control state "
            f"({', '.join(TRUSTED_CONTROL_NAMES)}); a planned read never reports it as source."
        ),
        matched_rule="filesystem.planned_read.trusted_control_path",
    )


def _canonical(path: str) -> str:
    return os.path.realpath(expand_user(path))


def normalize_workspace_relpath(path: str) -> str:
    """Canonical STRING identity for a workspace-relative path, independent
    of trailing-slash/'./' formatting - 'customers_project',
    'customers_project/', and './customers_project' must all compare equal
    wherever a planned/authorized relpath is checked against a Developer-
    generated target. Pure `os.path.normpath` string normalization, never
    touches the filesystem or resolves symlinks (unlike `_canonical` above,
    which is only for a real, already-on-disk path) - safe to call on a
    CREATE target that doesn't exist yet.

    PRV-17 (2026-09-03): this exact mismatch (plan-declared
    'customers_project/' vs. Developer-reported 'customers_project')
    tripped FILE_OUTSIDE_VALIDATED_SUBTASK_SCOPE even though both names the
    same directory - AuthorizedFileWriter.__init__/.authorize() below
    already normalized both sides via a bare inline `os.path.normpath`, but
    kriya/workflow/attempt.py's EARLIER raw-string pre-write gate did not.
    Both call sites now share this one helper so the two enforcement layers
    can never disagree about path identity again."""

    return os.path.normpath(path) if path else path


@dataclass(frozen=True)
class FilesystemScope:
    """The explicit, closed set of canonical roots a write may land under.
    A real, first-class type rather than a bare string/list, per the
    design this was scoped from - a future caller (e.g. a `.kriya/`
    internal-state writer) can be granted a DIFFERENT scope than a
    workspace-content writer without either one silently trusting the
    other's roots. `writable_roots` are always already canonicalized
    (symlinks resolved) by make_workspace_scope - never raw caller input."""

    writable_roots: Tuple[str, ...]


def make_workspace_scope(workspace_root: str, extra_writable_roots: Sequence[str] = ()) -> FilesystemScope:
    canonical_roots = tuple(dict.fromkeys(_canonical(r) for r in (workspace_root, *extra_writable_roots)))
    return FilesystemScope(writable_roots=canonical_roots)


def is_within_scope(scope: FilesystemScope, target_path: str) -> bool:
    """Canonical-path containment (os.path.realpath, symlinks resolved) -
    never a lexical str.startswith() against the raw target. The `+
    os.sep` suffix guard is what keeps a sibling directory with a shared
    prefix (e.g. '/repo' vs '/repo-evil') from being wrongly treated as
    contained."""

    canonical_target = _canonical(target_path)
    return any(path_relation(root, canonical_target) is PathRelation.WITHIN for root in scope.writable_roots)


class AuthorizedFileWriter:
    """The one authorized entry point for a workflow/self-correction-
    originated content write once a workspace/worktree root is known. See
    module docstring for why this really enforces rather than just
    auditing."""

    def __init__(
        self, workspace_root: str, extra_writable_roots: Sequence[str] = (),
        protected_relpaths: Sequence[str] = (),
        allowed_relpaths: Sequence[str] = (),
        write_scope_mode: Optional[WriteScopeMode] = None,
    ) -> None:
        self._scope = make_workspace_scope(workspace_root, extra_writable_roots)
        # A dedicated ExecutionPolicy instance with the NARROWER
        # enforcement-only sensitive-path pattern set (see module
        # docstring) - MA4.15's own additive sensitive_path_patterns
        # constructor parameter, used here for exactly the purpose it was
        # added for. Deliberately NOT the shared, audit-only default -
        # this instance's verdicts are real, so its patterns must be
        # precise, not just a useful logged signal.
        self._execution_policy = ExecutionPolicy(sensitive_path_patterns=_ENFORCEMENT_SENSITIVE_PATH_PATTERNS)
        # protected_relpaths (2026-08-25): per-run dynamic denials, distinct
        # from the fixed regex patterns above - a real, live-confirmed
        # incident (ignite_qpid_protocol) showed a generated subtask can
        # target the literal goal-source file (the path given via `kriya
        # generate --file <path>`) as an ordinary output file. That subtask's
        # content happened to be Kriya's own JSON planning-artifact shape
        # (the model, asked to "create the goal file", drafted content that
        # looked like its own planning format) - Quality Gates has no way to
        # catch this (a markdown/text file has no compile gate, and nothing
        # about the content itself is invalid), and it got applied straight
        # to the real workspace, silently destroying the real goal text that
        # drives this AND every future run against the same workspace/file.
        # No fixed regex could have caught this in advance - the protected
        # path is only known at call time, from the actual CLI invocation.
        self._workspace_root = _canonical(workspace_root)
        self._protected_relpaths = tuple(
            normalize_workspace_relpath(p) for p in protected_relpaths if p
        )
        self._allowed_relpaths = frozenset(
            normalize_workspace_relpath(p) for p in allowed_relpaths if p
        )
        # Backward-compatible inference when the caller doesn't pass
        # write_scope_mode explicitly: every call site written before this
        # mode existed either passed a non-empty allowlist (ALLOWLIST,
        # unchanged) or nothing at all (UNRESTRICTED, unchanged) - so every
        # existing caller's behavior is preserved byte-for-byte without
        # needing to be touched. A caller that needs DENY_ALL (or wants
        # ALLOWLIST/UNRESTRICTED stated explicitly rather than inferred)
        # passes write_scope_mode directly.
        if write_scope_mode is None:
            write_scope_mode = (
                WriteScopeMode.ALLOWLIST if self._allowed_relpaths else WriteScopeMode.UNRESTRICTED
            )
        self._write_scope_mode = write_scope_mode

    def authorize(self, target_path: str, *, identities: Optional[ControlObjectIdentities] = None) -> PolicyResult:
        """``identities`` (OI-164): the calling operation's control-object index over this writer's workspace root (one
        per batch); a fresh one per call when omitted."""
        if not is_within_scope(self._scope, target_path):
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason_code="PATH_OUTSIDE_AUTHORIZED_WRITABLE_ROOTS",
                explanation=(
                    f"'{target_path}' resolves (canonically, symlinks included) outside every "
                    f"authorized writable root: {self._scope.writable_roots}."
                ),
                matched_rule="filesystem.authorized_writer.outside_scope",
            )
        if is_trusted_control_path(self._workspace_root, target_path, identities=identities):
            return trusted_control_path_denial(target_path)
        if self._write_scope_mode == WriteScopeMode.DENY_ALL:
            # Unconditional - no persistent write is permitted in this mode,
            # regardless of allowed_relpaths/protected_relpaths content. The
            # real security boundary for a verification-only execution
            # context (kriya/workflow/plan_schema.py::ExecutionRole.
            # VERIFICATION): even a bug in the Developer or a retry/self-
            # correction recovery loop that somehow still requests a write
            # cannot grant repository mutation once this mode is set.
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason_code="WRITE_SCOPE_DENY_ALL",
                explanation=(
                    f"'{target_path}' cannot be written - this execution context is "
                    "write_scope_mode=DENY_ALL (a non-mutating verification-only context); no "
                    "persistent repository write is permitted."
                ),
                matched_rule="filesystem.authorized_writer.deny_all",
            )
        target_relpath = None
        if self._protected_relpaths or self._write_scope_mode == WriteScopeMode.ALLOWLIST:
            try:
                target_relpath = os.path.normpath(os.path.relpath(_canonical(target_path), self._workspace_root))
            except ValueError:
                target_relpath = None
        if self._write_scope_mode == WriteScopeMode.ALLOWLIST and target_relpath not in self._allowed_relpaths:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason_code="FILE_OUTSIDE_VALIDATED_SUBTASK_SCOPE",
                explanation=(
                    f"'{target_path}' is outside the validated subtask's allowed modification "
                    f"scope: {sorted(self._allowed_relpaths)!r}."
                ),
                matched_rule="filesystem.authorized_writer.validated_subtask_scope",
            )
        if self._protected_relpaths:
            if target_relpath in self._protected_relpaths or self._aliases_protected_path(target_path):
                return PolicyResult(
                    decision=PolicyDecision.DENY,
                    reason_code="GOAL_SOURCE_FILE_PROTECTED",
                    explanation=(
                        f"'{target_path}' is the goal file supplied via --file for this run - a "
                        "generated subtask targeting Kriya's own goal-source file is never "
                        "intentional and would silently corrupt the instructions driving this "
                        "and every future run against this workspace."
                    ),
                    matched_rule="filesystem.authorized_writer.protected_goal_source_file",
                )
        return self._execution_policy.evaluate(ActionRequest(
            action_type=ActionType.WRITE_FILE, target=target_path, workspace_path=self._scope.writable_roots[0],
        ))

    def _aliases_protected_path(self, target_path: str) -> bool:
        """PLAT-001: the target names a protected file under another
        spelling - a case or Unicode-normalization variant on an
        insensitive filesystem, a symlink or a hard link - by filesystem
        identity, never by string. Identity that cannot be established
        counts as an alias (fail closed)."""
        return any(
            path_identity(os.path.join(self._workspace_root, relpath), target_path) is not PathIdentity.DIFFERENT
            for relpath in self._protected_relpaths
        )

    def _raise_if_denied(self, target_path: str, identities: Optional[ControlObjectIdentities] = None) -> None:
        result = self.authorize(target_path, identities=identities)
        # POL-001: fail-closed on REQUIRE_APPROVAL too, not just DENY. No
        # stage this instance's evaluate() can reach currently produces
        # REQUIRE_APPROVAL for a WRITE_FILE request built by authorize()
        # above (it never sets process_profile/engineering_route, and
        # _check_filesystem never emits REQUIRE_APPROVAL for WRITE_FILE) -
        # verified, not assumed - so this is a defensive tightening with no
        # behavior change for any real caller today, not a fix for an
        # observed silent-allow. This writer has no approval_callback of
        # its own to ask a human, so a hypothetical future REQUIRE_APPROVAL
        # verdict here must never fall through as if it were ALLOW.
        if result.decision != PolicyDecision.ALLOW and result.decision != PolicyDecision.ALLOW_SANDBOXED:
            raise PolicyDeniedError(
                request=ActionRequest(action_type=ActionType.WRITE_FILE, target=target_path),
                result=result,
            )

    def commit_file(
        self, full_path: str, content: str, expected_revision: str, *, content_bytes: Optional[bytes] = None,
    ) -> str:
        """Authorizes, then delegates to edit_safety.py's
        commit_revision_grounded_file - nothing is written if this raises.
        ``content_bytes`` are the exact bytes to write when given."""

        self._raise_if_denied(full_path)
        return commit_revision_grounded_file(
            full_path, content, expected_revision=expected_revision,
            workspace_path=self._scope.writable_roots[0], content_bytes=content_bytes,
        )

    def commit_batch(self, writes: Iterable[StagedFileWrite]) -> Dict[str, str]:
        """Every item is authorized BEFORE any write in the batch happens -
        one denied target aborts the whole batch, mirroring
        commit_revision_grounded_batch's own existing all-or-nothing
        contract for revision conflicts."""

        materialized = list(writes)
        identities = ControlObjectIdentities((self._workspace_root,))  # OI-164: one index for the whole batch
        for item in materialized:
            self._raise_if_denied(item.target_path, identities)
        return commit_revision_grounded_batch(
            materialized, workspace_path=self._scope.writable_roots[0],
        )


HARD_LINK_OUTSIDE_READABLE_ROOTS = "HARD_LINK_OUTSIDE_AUTHORIZED_READABLE_ROOTS"


class ReadableRootLinkCensus:
    """WAVE8 OI-170: whether EVERY name of a multi-link file lies inside the readable roots.

    Path containment (``is_within_scope``) judges where a NAME is. A hard link inside a root to a file whose other name is outside every root passes it, and a
    read of that name then returns the outside object's bytes (MEASURED, OI-170). The identity of the object is the only thing that can tell: a regular file
    reached through a name inside the roots has ``st_nlink`` names in all; if the roots hold fewer than ``st_nlink`` of them, at least one name is outside
    every root. The accounting is by ``(st_dev, st_ino)`` (``object_key``), and only a non-directory with more than one link ever pays for it - a single-link file,
    a directory and a missing path are decided by ``os.stat`` alone, so ordinary trees (including hard-linked ones whose names all lie inside the roots) are
    unaffected.

    The census is one walk of the roots, built lazily on the first multi-link target and kept for this instance only: callers build an ``AuthorizedFileReader``
    per operation (a read, a search, a planned diff), so a link made after one operation is judged afresh by the next. Each directory is walked once (a candidate
    worktree lives under the owner's ``.kriya``: its names are counted once, not once per root); symlinks are never followed (a symlink is not a name of the
    object, ``os.stat`` of the target already followed it). A directory or entry that cannot be read, or an identity the host does not provide, makes the
    census incomplete and every multi-link target then fails closed (not accounted for) - the same posture as ``ControlObjectIdentities``.

    It is NOT a second containment engine: it answers one identity question the canonical reader asks after its own path containment decided, and it never
    sees the write side."""

    def __init__(self, roots: Sequence[str]) -> None:
        self._roots: Tuple[str, ...] = tuple(roots)
        self._counts: Optional[Dict[Tuple[int, int], int]] = None
        self._counted = False

    def accounts_for_every_name(self, target: str) -> bool:
        """True when ``target`` (symlinks followed, as a read would) is not a multi-link file, or when the roots hold all of its names. False when a name is
        unaccounted for or the identity cannot be established (fail closed)."""
        try:
            result = os.stat(target)
        except (FileNotFoundError, NotADirectoryError):
            return True  # nothing to read: the read itself fails
        except (OSError, ValueError):
            return False
        if stat.S_ISDIR(result.st_mode) or result.st_nlink <= 1:
            return True
        key = object_key(result)
        if key is None:
            return False
        if not self._counted:
            self._counts = self._count()
            self._counted = True
        if self._counts is None:
            return False
        return self._counts.get(key, 0) >= result.st_nlink

    def _count(self) -> Optional[Dict[Tuple[int, int], int]]:
        """Names per multi-link object across the roots; None when the walk could not be completed."""
        counts: Dict[Tuple[int, int], int] = {}
        visited: Set[Tuple[int, int]] = set()
        stack: List[str] = []
        for root in self._roots:
            try:
                root_key = object_key(os.stat(root))
            except FileNotFoundError:
                continue
            except (OSError, ValueError):
                return None
            if root_key is None:
                return None
            if root_key not in visited:
                visited.add(root_key)
                stack.append(root)
        while stack:
            directory = stack.pop()
            try:
                with os.scandir(directory) as iterator:
                    entries = list(iterator)
            except FileNotFoundError:
                continue  # removed while walking: no names beneath it
            except (OSError, ValueError):
                return None
            for entry in entries:
                try:
                    info = entry.stat(follow_symlinks=False)
                    is_directory = stat.S_ISDIR(info.st_mode)
                except FileNotFoundError:
                    continue
                except (OSError, ValueError):
                    return None
                if not is_directory and info.st_nlink <= 1:  # lstat: a symlink is never a directory here (never followed) and its own inode is not any file's key
                    continue
                key = object_key(info)
                if key is None:
                    return None
                if is_directory:
                    if key not in visited:
                        visited.add(key)
                        stack.append(entry.path)
                else:
                    counts[key] = counts.get(key, 0) + 1
        return counts


class AuthorizedFileReader:
    """DEV-INV-001: the read-side sibling of AuthorizedFileWriter above -
    same composition (make_workspace_scope/is_within_scope for canonical,
    symlink-resolved containment + a dedicated ExecutionPolicy instance for
    the sensitive-path check), same real-enforcement posture (raises
    PolicyDeniedError, never audit-only), for ActionType.READ_FILE instead
    of WRITE_FILE.

    This closes the gap the DEV-INV-001 architecture review flagged:
    ExecutionPolicy._check_filesystem already governs READ_FILE and
    WRITE_FILE symmetrically (kriya/policy/execution.py), but no real
    call site ever built a READ_FILE ActionRequest with a real
    workspace_path - so the containment/sensitive-path rule never actually
    ran for a read. This class is that missing call site, reused by every
    DEV-INV-001 investigation resolver that touches path-backed content
    (kriya/workflow/investigation.py) - never a second, independently
    invented read-authority mechanism.

    Uses the SAME narrower _ENFORCEMENT_SENSITIVE_PATH_PATTERNS set
    AuthorizedFileWriter uses, not ExecutionPolicy's broader default list -
    this is also real enforcement (a false-positive DENY here blocks a
    legitimate investigation read, not just a logged signal), and the
    broader default's bare `credentials`/`secrets`/`password` substrings
    would incorrectly block reading an ordinarily-named business file like
    `credentials_service.py` - the exact false-positive AuthorizedFileWriter
    was already narrowed to avoid, for the identical reason.

    `FilesystemScope.writable_roots` is reused as-is for the readable-root
    list (a naming artifact from the write-side class it was built for, not
    worth renaming and touching that closed, already-hardened path)."""

    def __init__(self, workspace_root: str, extra_readable_roots: Sequence[str] = ()) -> None:
        self._scope = make_workspace_scope(workspace_root, extra_readable_roots)
        self._execution_policy = ExecutionPolicy(sensitive_path_patterns=_ENFORCEMENT_SENSITIVE_PATH_PATTERNS)
        self._link_census = ReadableRootLinkCensus(self._scope.writable_roots)  # WAVE8 OI-170: per instance, i.e. per operation

    def authorize(self, target_path: str) -> PolicyResult:
        if not is_within_scope(self._scope, target_path):
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason_code="PATH_OUTSIDE_AUTHORIZED_READABLE_ROOTS",
                explanation=(
                    f"'{target_path}' resolves (canonically, symlinks included) outside every "
                    f"authorized readable root: {self._scope.writable_roots}."
                ),
                matched_rule="filesystem.authorized_reader.outside_scope",
            )
        if not self._link_census.accounts_for_every_name(target_path):
            # WAVE8 OI-170: the NAME is inside a root, the OBJECT also has a name that is not (or its names cannot be counted): a hard link must not widen
            # read authority past the roots. The explanation never names the other names (they are unknown) or the object's content.
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason_code=HARD_LINK_OUTSIDE_READABLE_ROOTS,
                explanation=(
                    f"'{target_path}' is one of several names (hard links) of a file, and not every name lies inside an authorized readable root "
                    f"({self._scope.writable_roots}), or its names could not be counted: a read through it would reach an object outside them."
                ),
                matched_rule="filesystem.authorized_reader.hard_link_outside_scope",
            )
        # Deliberately omits workspace_path here, unlike AuthorizedFileWriter's
        # otherwise-identical delegation - is_within_scope() above is already
        # the authoritative, MULTI-root containment decision (workspace_path
        # PLUS every extra_readable_root, e.g. a run's worktree_path - see
        # kriya/workflow/investigation.py's own _reader() construction).
        # ExecutionPolicy._check_filesystem's own containment rule only ever
        # checks a SINGLE workspace_path value; passing self._scope.
        # writable_roots[0] here would re-deny a target legitimately inside
        # a second/third readable root (found live: a worktree_path target,
        # correctly allowed by is_within_scope, was then re-rejected as
        # PATH_OUTSIDE_WORKSPACE_DENIED by this exact single-root re-check).
        # Omitting workspace_path makes _check_filesystem run ONLY its
        # sensitive-path rule (still real, still unconditional) and fall
        # through to evaluate()'s own default-ALLOW backstop for READ_FILE -
        # correct, since containment was already conclusively decided above.
        return self._execution_policy.evaluate(ActionRequest(
            action_type=ActionType.READ_FILE, target=target_path,
        ))

    def raise_if_denied(self, target_path: str) -> None:
        """Public (unlike AuthorizedFileWriter's private _raise_if_denied) -
        every DEV-INV-001 resolver calls this directly before touching disk
        or returning a path in a result; there is no commit_file()-style
        wrapping primitive to call it from internally on the read side."""
        result = self.authorize(target_path)
        if result.decision != PolicyDecision.ALLOW and result.decision != PolicyDecision.ALLOW_SANDBOXED:
            raise PolicyDeniedError(
                request=ActionRequest(action_type=ActionType.READ_FILE, target=target_path),
                result=result,
            )
