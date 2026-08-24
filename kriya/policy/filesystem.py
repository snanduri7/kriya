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
from dataclasses import dataclass
from typing import Dict, Iterable, Sequence, Tuple

from kriya.policy.errors import PolicyDeniedError
from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import ActionRequest, ActionType, PolicyDecision, PolicyResult
from kriya.workflow.edit_safety import (
    StagedFileWrite,
    commit_revision_grounded_batch,
    commit_revision_grounded_file,
)

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


def _canonical(path: str) -> str:
    return os.path.realpath(os.path.expanduser(path))


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
    return any(
        canonical_target == root or canonical_target.startswith(root + os.sep)
        for root in scope.writable_roots
    )


class AuthorizedFileWriter:
    """The one authorized entry point for a workflow/self-correction-
    originated content write once a workspace/worktree root is known. See
    module docstring for why this really enforces rather than just
    auditing."""

    def __init__(self, workspace_root: str, extra_writable_roots: Sequence[str] = ()) -> None:
        self._scope = make_workspace_scope(workspace_root, extra_writable_roots)
        # A dedicated ExecutionPolicy instance with the NARROWER
        # enforcement-only sensitive-path pattern set (see module
        # docstring) - MA4.15's own additive sensitive_path_patterns
        # constructor parameter, used here for exactly the purpose it was
        # added for. Deliberately NOT the shared, audit-only default -
        # this instance's verdicts are real, so its patterns must be
        # precise, not just a useful logged signal.
        self._execution_policy = ExecutionPolicy(sensitive_path_patterns=_ENFORCEMENT_SENSITIVE_PATH_PATTERNS)

    def authorize(self, target_path: str) -> PolicyResult:
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
        return self._execution_policy.evaluate(ActionRequest(
            action_type=ActionType.WRITE_FILE, target=target_path, workspace_path=self._scope.writable_roots[0],
        ))

    def _raise_if_denied(self, target_path: str) -> None:
        result = self.authorize(target_path)
        if result.decision == PolicyDecision.DENY:
            raise PolicyDeniedError(
                request=ActionRequest(action_type=ActionType.WRITE_FILE, target=target_path),
                result=result,
            )

    def commit_file(self, full_path: str, content: str, expected_revision: str) -> str:
        """Authorizes, then delegates to edit_safety.py's unmodified
        commit_revision_grounded_file - nothing is written if this raises."""

        self._raise_if_denied(full_path)
        return commit_revision_grounded_file(full_path, content, expected_revision=expected_revision)

    def commit_batch(self, writes: Iterable[StagedFileWrite]) -> Dict[str, str]:
        """Every item is authorized BEFORE any write in the batch happens -
        one denied target aborts the whole batch, mirroring
        commit_revision_grounded_batch's own existing all-or-nothing
        contract for revision conflicts."""

        materialized = list(writes)
        for item in materialized:
            self._raise_if_denied(item.target_path)
        return commit_revision_grounded_batch(materialized)
