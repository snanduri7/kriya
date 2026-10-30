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
from typing import Dict, List, Mapping, Sequence

# S19 (OI-141): global options every git invocation Kriya spawns carries, so a repository's own configuration
# (.git/config, what it includes, config.worktree, a submodule's config - all MEASURED executing through Kriya's own
# calls, handover/MODULARIZATION/S19-PREFLIGHT.md) cannot run a command on the host through them:
# - core.fsmonitor: a repository-supplied command run on every index refresh (status, add, diff, ls-files, checkout);
# - core.hooksPath=/dev/null: no repository hook (post-index-change, post-checkout, reference-transaction, commit hooks);
# - commit.gpgSign=false: Kriya's own commits never sign, so no repository gpg.program runs (owner question Q2; the
#   fail-closed choice);
# - log.showSignature=false (S19 P1e): a repository's log.showSignature=true made GitTool's `log` verify a commit's
#   (forged) gpgsig / mergetag signature through the repository's gpg.program, gpg.openpgp.program, gpg.ssh.program or
#   gpg.x509.program (MEASURED, git 2.54.0: every format fired); Kriya's other commit readers (rev-list,
#   for-each-ref, cat-file, blame, status, rev-parse) ran no signature program (MEASURED). No Kriya site runs merge or
#   pull (merge.verifySignatures; TRACED).
# - core.autocrlf=false, core.eol=lf, core.attributesFile=<null device> (W3-2, GIT-AUTOCRLF-HOST-CONFIG-CANDIDATE-BYTES-001): the
#   host's line-ending and attribute configuration has no authority over the bytes Kriya materializes (`reset --hard`,
#   `checkout`), stages (`add`, `read-tree` / `write-tree` checkpoint identity) or compares. MEASURED, git 2.54.0: host
#   `core.autocrlf=true` rewrote a green run's LF candidate to CRLF; host `core.eol=crlf` did so under a repository
#   `* text=auto`; a host attributes file `* text eol=crlf` did so under both pins above; and under host `true` a CRLF rewrite
#   of a working file left `read-tree` / `add -A` / `write-tree` producing the HEAD tree (evidence blind to the change).
#   Repository-owned `.gitattributes` and `$GIT_DIR/info/attributes` stay active: they are repository semantics, guarded by the
#   exact-byte materialization check (kriya.workflow.worktree), not host state.
# Command-line options outrank every configuration file and reach child gits (submodules) through
# GIT_CONFIG_PARAMETERS. MEASURED: they also outrank env-injected command-scope config (GIT_CONFIG_COUNT), so those pinned keys
# cannot be overridden by the host environment either. Content-filter drivers (filter.<x>.clean|smudge|process) are NOT
# neutralised here: blanking them moves Kriya's content/evidence hashes. Owner decision Q1 (2026-10-20): a repository-defined
# filter is REFUSED, typed, before the git call - kriya.repository.git.refuse_repository_content_filters (S19 Q1). `attr.tree`
# has no documented neutral value, so an effective one is REFUSED by the same inspector (GIT_ATTR_TREE_CONFIGURED), never pinned.
GIT_INVOCATION_OPTIONS = ("-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}", "-c", "commit.gpgSign=false",
                          "-c", "log.showSignature=false", "-c", "core.autocrlf=false", "-c", "core.eol=lf",
                          "-c", f"core.attributesFile={os.devnull}")

# The ENVIRONMENT half of the same authority (W3-2): what a Kriya-owned git process's environment may not inject.
# - GIT_ATTR_NOSYSTEM=1 FORCED: the system attributes file (<prefix>/etc/gitattributes) is reachable by no `-c`. Its influence on
#   checkout / index / evidence is TRACED from git's documented semantics; it is not behaviourally reproduced (no host file is
#   written to prove it), so the proof of its propagation is structural (tests/architecture/test_git_invocation_seam.py).
# - GIT_ATTR_SOURCE STRIPPED: an inherited value reads attributes from another tree-ish. MEASURED: 2 CRs under every argv pin
#   above, 0 without it. Kriya never sets it (the S19 inspector only reads the caller's).
# - GIT_LITERAL_PATHSPECS STRIPPED: turns off pathspec magic. MEASURED: `add -A -- . ':!.kriya' ...` fails fatally (the
#   checkpoint / milestone evidence hashes become None) and `status -- . ':!.kriya'` lists `.kriya/`. Kriya never sets it.
# Deliberately NOT touched (owner decisions 2026-10-30/31): GIT_CONFIG_* (the pinned keys above outrank them, MEASURED; S19 trusts
# command scope and OCI sets them for its container), HOME / XDG_CONFIG_HOME / GIT_CONFIG_GLOBAL|SYSTEM|NOSYSTEM, and the
# repository-selecting variables (GIT_DIR, GIT_WORK_TREE, ...: the S19 contract keeps the caller's own). GIT_GLOB_PATHSPECS and
# GIT_NOGLOB_PATHSPECS did not change membership (Kriya's default-magic pathspecs hold no wildcard); GIT_ICASE_PATHSPECS is UNKNOWN
# on a case-insensitive host (the discriminating fixture collapses) and is not sanitised without evidence.
GIT_FORCED_ENVIRONMENT: Mapping[str, str] = {"GIT_ATTR_NOSYSTEM": "1"}
GIT_STRIPPED_ENVIRONMENT = ("GIT_ATTR_SOURCE", "GIT_LITERAL_PATHSPECS")


def git_argv(argv: Sequence[str]) -> List[str]:
    """The argv Kriya spawns for the git invocation ``argv``: a NEW list with ``GIT_INVOCATION_OPTIONS`` inserted after
    the executable (global options precede the subcommand). ``argv[0]`` is ``git`` or a path to it (a resolved
    ``shutil.which`` / PATH candidate); anything else is refused. Apply it at the spawn, after any policy audit, so the
    audited argv (and the POL-001-P3 exact-prefix recognizers) never change."""
    command = list(argv)
    if not command or os.path.basename(str(command[0])) != "git":
        raise ValueError(f"git_argv() needs a git invocation, got {command[:1]!r}")
    return [command[0], *GIT_INVOCATION_OPTIONS, *command[1:]]


def git_environment(base: Mapping[str, str]) -> Dict[str, str]:
    """The environment Kriya spawns a git process with, derived from ``base`` (the caller's environment): a NEW mapping, ``base``
    never mutated. REQUIRED, never defaulted to the process environment: this layer is not an environment reader (PLATFORM is L0
    and ``kriya.config.environment`` is the one reader), so a caller passes ``child_environment()`` or its own mapping.
    Idempotent. A per-operation value Kriya owns (a scratch ``GIT_INDEX_FILE``) is overlaid AFTER this, so policy never
    overwrites it."""
    environment = {name: value for name, value in base.items() if name not in GIT_STRIPPED_ENVIRONMENT}
    environment.update(GIT_FORCED_ENVIRONMENT)
    return environment
