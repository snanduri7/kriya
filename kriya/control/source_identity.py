"""Trusted source identity of Kriya's OWN checkout (RUN-SOURCE-IDENTITY-001, owner ruling 2026-11-11).

The ONE implementation that reads which Kriya source is executing: the git revision and tracked-tree dirty flag of the
checkout Kriya runs from, or the build identity embedded in an installed wheel. It is not a repository reader: a
target workspace, a candidate or any user repository is never a legal argument, and every such folder is read under the
untrusted-repository policy of ``kriya.repository.git`` at its own entry.

Two entries, one implementation:

- ``read_kriya_source_identity(source_root=None)``: the release / certification side. ``source_root`` names a Kriya
  source or release-candidate checkout supplied by that authority (``kriya.core.release_identity`` forwards it); a
  refusal is raised typed.
- ``run_source_identity()``: the run side, no parameter. ``begin_mutating_run`` calls it once per top-level run and
  puts the value on the ``RunContext``; a typed refusal becomes a typed unavailable value, never a failed run and
  never a guessed identity.

INSTALLATION STATE (S19 P1b, owner decision B; tests/test_s19p1b_residuals.py INSTALLATION_STATE): Kriya's own source
checkout is operator-managed application infrastructure, so the content-filter judgment is NOT applied to its `status`
(which can run a content filter) - whoever can write that checkout's configuration can already write Kriya's code. This
is the only production site that passes ``content_filters=False``. The trust is the ROLE of this call, never the path:
the same folder opened as a target workspace gets the untrusted-repository policy at every target entry.

That exemption is only the content-filter policy. The W3-2 `attr.tree` rule is not role-based: an effective `attr.tree`
(any scope, the environment this call's git runs under included) redirects the attributes that decide the `dirty` flag,
so the typed `RepositoryContentFilterRefused` (GIT_ATTR_TREE_CONFIGURED) is raised BEFORE either git call and no
identity is returned (RELEASE-IDENTITY-ATTR-TREE-UNINSPECTED-001, owner decision 2026-10-30).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Mapping, Optional

from kriya.config.environment import child_environment
from kriya.platform import git_argv, git_environment
from kriya.repository.git import RepositoryContentFilterRefused, refuse_repository_content_filters

SOURCE_CHECKOUT = "source_checkout"   # read from the git checkout Kriya runs from
EMBEDDED = "embedded"                 # no checkout: the build identity KRIYA-VERSION-001 embedded in the wheel
UNAVAILABLE = "unavailable"           # neither, or git could not name HEAD, or the read was refused (``reason_code``)
_COMMAND_TIMEOUT_SECONDS = 10.0
_SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@dataclass(frozen=True)
class SourceIdentity:
    """``revision``: the exact git HEAD (or the embedded commit), None when unavailable. ``dirty``: the measured
    tracked-tree flag, None when it was not measured. ``reason_code``: the typed refusal that made it unavailable."""

    revision: Optional[str]
    dirty: Optional[bool]
    provenance: str
    reason_code: Optional[str] = None


def _command(argv: List[str], cwd: str, env: Mapping[str, str]) -> Optional[str]:
    """One git query of the source checkout: a ``git_argv`` argv, no shell, the canonical git environment, bounded."""
    if shutil.which(argv[0]) is None:
        return None
    try:
        completed = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=_COMMAND_TIMEOUT_SECONDS,
                                   check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def read_kriya_source_identity(source_root: Optional[str] = None) -> SourceIdentity:
    """The identity of the Kriya source at ``source_root`` (default: the checkout this module is imported from). Only a
    root that IS a checkout is asked: a directory nested in someone else's repository never borrows that repository's
    HEAD. Raises ``RepositoryContentFilterRefused`` for an effective ``attr.tree``."""
    root = _SOURCE_ROOT if source_root is None else source_root
    if not os.path.exists(os.path.join(root, ".git")):
        from kriya.foundation.build_info import UNKNOWN as UNKNOWN_BUILD
        from kriya.foundation.build_info import embedded_build_info

        embedded = embedded_build_info()
        if embedded["commit"] != UNKNOWN_BUILD:
            return SourceIdentity(embedded["commit"], embedded["dirty"], EMBEDDED)
        return SourceIdentity(None, None, UNAVAILABLE)
    git_env = git_environment(child_environment())
    refuse_repository_content_filters(root, env=git_env, timeout=_COMMAND_TIMEOUT_SECONDS, content_filters=False)
    revision = _command(git_argv(["git", "rev-parse", "HEAD"]), cwd=root, env=git_env)
    status = _command(git_argv(["git", "status", "--porcelain", "--untracked-files=no"]), cwd=root, env=git_env)
    return SourceIdentity(revision or None, None if status is None else bool(status), SOURCE_CHECKOUT if revision else UNAVAILABLE)


def run_source_identity() -> SourceIdentity:
    """The identity of the Kriya that executes this run. No parameter: a run never names the source root. Only the
    typed self-identity refusal is absorbed (as an unavailable value carrying its reason code); any other error is a
    defect and propagates."""
    try:
        return read_kriya_source_identity()
    except RepositoryContentFilterRefused as refusal:
        return SourceIdentity(None, None, UNAVAILABLE, refusal.reason_code)
