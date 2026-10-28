"""R3 (Wave-2 final closure, owner decision 2026-10-28): the dependency cache a gate mounts is EPHEMERAL, never a persistent writable directory.

Why. A verification gate runs candidate-controlled code (a test, a build, the plugins and extensions its POM names). The Maven local repository and the Gradle
user home it was given used to be ONE persistent per-workspace directory mounted read-write into every gate (and into the registry-scoped acquisition phase,
which runs the same goals). MEASURED at f3b7d39: a candidate test rewrote the cached ``junit-4.13.2.jar`` and a LATER gate, with an honest failing candidate,
reported BUILD SUCCESS; the sidecar checksum no longer matched and nothing looked. The writer and every consumer were in one trust class, so the cache was an
unbounded channel from any earlier candidate (also a rejected one) to the verdict of the next gate, the baseline and the authority oracle.

What a gate gets instead. Each outermost verification scope (a gate, a ``verification_session``, an authority-bundle run) is given a FRESH, empty,
run-local dependency directory under ``<state>/gate-caches/``, writable by that scope's containers only (offline execution and the registry-scoped acquisition
that precedes it share it, so the offline retry finds what the acquisition fetched), and removed when the scope ends. Nothing a candidate writes there flows
back into any other scope. No persistent dependency material is mounted into candidate-controlled execution at all: Kriya has no acquisition that runs no
candidate code (a POM names build extensions and plugin coordinates; even ``dependency:go-offline`` loads them), so there is no safe promotion seam, and
whatever the pre-R3 persistent directories hold was written by candidate code and is unprovenanced. The accepted cost is reduced cache reuse.

The Python project venv is the same surface (``<worktree>/.kriya/venv``): the pip-install step runs candidate-declared install and build hooks with the
venv WRITABLE, and the venv used to be reused across attempts. MEASURED at ebd022a: a hook in attempt 1 planted a pytest plugin that made attempt 2's
honest failing test pass. The venv now has the lifetime of the verification scope that builds it: ``discard_project_venv`` removes it when the
outermost scope opens (anything an earlier run or scope left is unprovenanced) and when it closes.

The structural guard. ``is_ephemeral_cache`` is what the OCI backend consults (``containment_oci._require_ephemeral_writable_caches``) before any docker
resource exists: a profile that mounts a dependency cache WRITABLE must name a directory this module created (a Kriya-owned marker in the unit directory
above it, which is never mounted). Anything else is the typed refusal ``PERSISTENT_DEPENDENCY_CACHE_WRITABLE``; a gate that asks for a cache outside a
scope gets ``DEPENDENCY_CACHE_SCOPE_REQUIRED`` (both ``kriya.tools.containment.DependencyCacheError``). Never a fallback. This module imports nothing of
Kriya's but the ``.kriya`` directory-name constant (a leaf), so the OCI backend and the validator can both use it without an import cycle.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile

from kriya.config.environment import KRIYA_DIRECTORY_NAME

logger = logging.getLogger(__name__)

CACHES_DIRECTORY = "gate-caches"
PROJECT_VENV = (KRIYA_DIRECTORY_NAME, "venv")
EPHEMERAL_MARKER = ".kriya-ephemeral-dependency-cache"
_UNIT_PREFIX = "cache-"


def caches_directory(state_root: str) -> str:
    """Where ephemeral dependency layers live: under the Kriya state root, never inside a worktree or an owner workspace."""
    return os.path.join(os.path.realpath(state_root), CACHES_DIRECTORY)


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _remove_tree(path: str) -> None:
    for directory, _dirnames, _filenames in os.walk(path):
        os.chmod(directory, 0o700)  # a build may have made its own directories read-only; they are still Kriya's to remove
    shutil.rmtree(path, ignore_errors=True)


def _sweep_dead_units(base: str) -> None:
    """Units a crashed process left behind (its pid is in the unit name and the marker). A live process's unit is never touched."""
    try:
        names = os.listdir(base)
    except OSError:
        return
    for name in names:
        if not name.startswith(_UNIT_PREFIX):
            continue
        try:
            pid = int(name[len(_UNIT_PREFIX):].split("-", 1)[0])
        except ValueError:
            continue
        if not _process_alive(pid):
            _remove_tree(os.path.join(base, name))


def discard_project_venv(workspace_path: str) -> bool:
    """Remove the project venv of ``workspace_path`` (never its target when it is a link: the link itself goes). True when nothing is left there."""
    venv = os.path.join(workspace_path, *PROJECT_VENV)
    if os.path.islink(venv):
        os.unlink(venv)
    elif os.path.lexists(venv):
        _remove_tree(venv)
    return not os.path.lexists(venv)


def create_ephemeral_cache(state_root: str, tool: str) -> str:
    """A fresh, empty dependency directory for ``tool`` (``maven``, ``gradle``), owned by one scope. Returns the directory to mount. An ``OSError`` is the caller's
    (the validator turns it into the typed refusal)."""
    base = caches_directory(state_root)
    os.makedirs(base, mode=0o700, exist_ok=True)
    _sweep_dead_units(base)
    unit = tempfile.mkdtemp(prefix=f"{_UNIT_PREFIX}{os.getpid()}-", dir=base)
    with open(os.path.join(unit, EPHEMERAL_MARKER), "w", encoding="utf-8") as marker:
        json.dump({"pid": os.getpid(), "tool": tool}, marker)
    cache = os.path.join(unit, tool)
    os.mkdir(cache, 0o700)
    return os.path.realpath(cache)


def remove_ephemeral_cache(path: str) -> None:
    """Remove the unit holding ``path``. Cleanup is never part of a gate's truth: a failure is logged and the directory is left for the next sweep."""
    unit = os.path.dirname(os.path.realpath(path))
    if not os.path.isfile(os.path.join(unit, EPHEMERAL_MARKER)):
        logger.warning("Refusing to remove %s: it is not an ephemeral dependency cache unit", unit)
        return
    try:
        _remove_tree(unit)
    except Exception as error:  # noqa: BLE001 - cleanup must not turn a verdict into an error; reported, not hidden
        logger.warning("Ephemeral dependency cache %s could not be removed (%s); it is left for the next sweep", unit, error)


def is_ephemeral_cache(path: str) -> bool:
    """Whether ``path`` is a dependency directory this module created: a real directory (never a link) directly inside a unit that carries the marker
    and sits in ``gate-caches``. The marker is in the unit, which no container mounts."""
    if os.path.islink(path) or not os.path.isdir(path):
        return False
    unit = os.path.dirname(os.path.realpath(path))
    return (os.path.basename(os.path.dirname(unit)) == CACHES_DIRECTORY
            and os.path.basename(unit).startswith(_UNIT_PREFIX)
            and os.path.isfile(os.path.join(unit, EPHEMERAL_MARKER)))
