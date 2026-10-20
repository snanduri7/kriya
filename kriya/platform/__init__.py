"""Platform mechanism behind explicit ports (ARCH-PLATFORM-001).

Kriya core (workflow, policy, control, config) owns policy; this package
owns operating-system mechanism. Core modules ask a port what it can do
and how it did it; an adapter never grants authority. See
handover/PLATFORM_ARCHITECTURE.md.

It also owns how Kriya invokes git (``git_argv``, S19 / OI-141): one rule
every Kriya git spawn goes through, in the one layer every Kriya module may
import (PLATFORM, L0) without a new dependency edge.
"""
import os
from typing import List, Sequence

# S19 (OI-141): global options every git invocation Kriya spawns carries, so a repository's own configuration
# (.git/config, what it includes, config.worktree, a submodule's config - all MEASURED executing through Kriya's own
# calls, handover/MODULARIZATION/S19-PREFLIGHT.md) cannot run a command on the host through them:
# - core.fsmonitor: a repository-supplied command run on every index refresh (status, add, diff, ls-files, checkout);
# - core.hooksPath=/dev/null: no repository hook (post-index-change, post-checkout, reference-transaction, commit hooks);
# - commit.gpgSign=false: Kriya's own commits never sign, so no repository gpg.program runs (owner question Q2; the
#   fail-closed choice).
# Command-line options outrank every configuration file and reach child gits (submodules) through
# GIT_CONFIG_PARAMETERS. Content-filter drivers (filter.<x>.clean|smudge|process) are NOT neutralised here: blanking
# them moves Kriya's content/evidence hashes (owner question Q1, held).
GIT_INVOCATION_OPTIONS = ("-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgSign=false")


def git_argv(argv: Sequence[str]) -> List[str]:
    """The argv Kriya spawns for the git invocation ``argv``: a NEW list with ``GIT_INVOCATION_OPTIONS`` inserted after
    the executable (global options precede the subcommand). ``argv[0]`` is ``git`` or a path to it (a resolved
    ``shutil.which`` / PATH candidate); anything else is refused. Apply it at the spawn, after any policy audit, so the
    audited argv (and the POL-001-P3 exact-prefix recognizers) never change."""
    command = list(argv)
    if not command or os.path.basename(str(command[0])) != "git":
        raise ValueError(f"git_argv() needs a git invocation, got {command[:1]!r}")
    return [command[0], *GIT_INVOCATION_OPTIONS, *command[1:]]
