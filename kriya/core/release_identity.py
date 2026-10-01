"""Release certification identity (ARCH-PLATFORM-001, PLAT-RELEASE-IDENTITY-001).

A production/release certification is evidence about one exact
combination of Kriya code and platform mechanism. This module records that
combination and decides whether a recorded certification still describes
the current host:

- the Kriya git revision (and whether the checkout was dirty);
- the PlatformServices provider identities and every capability's
  ENFORCED/ADVISORY/UNAVAILABLE status;
- the containment backend (configured name, and the OCI runtime version
  when it is OCI);
- the environment identity (OS family, architecture, Python version).

Any material difference makes the recorded certification STALE (re-run
required). It is certification evidence only: it never authorizes anything
and never changes the model qualification fingerprint.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
from typing import Any, Dict, List, Mapping, Optional

from kriya.platform.host_properties import UNAVAILABLE, os_and_architecture
from kriya.platform.services import platform_services

RELEASE_IDENTITY_VERSION = 1
CURRENT = "CURRENT"
STALE = "STALE"
_COMMAND_TIMEOUT_SECONDS = 10.0
_SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _command(argv: List[str], cwd: Optional[str] = None) -> Optional[str]:
    if shutil.which(argv[0]) is None:
        return None
    try:
        completed = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=_COMMAND_TIMEOUT_SECONDS,
                                   check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def kriya_revision(source_root: str = _SOURCE_ROOT) -> Dict[str, Any]:
    """The git revision of the Kriya source, or UNAVAILABLE outside a checkout."""
    if not os.path.exists(os.path.join(source_root, ".git")):
        return {"revision": UNAVAILABLE, "dirty": None}
    revision = _command(["git", "rev-parse", "HEAD"], cwd=source_root)
    status = _command(["git", "status", "--porcelain", "--untracked-files=no"], cwd=source_root)
    return {"revision": revision or UNAVAILABLE, "dirty": None if status is None else bool(status)}


def containment_identity(backend: str) -> Dict[str, Any]:
    runtime = _command(["docker", "version", "--format", "{{.Server.Version}}"]) if backend == "oci" else None
    return {"backend": backend, "runtime_version": runtime or (UNAVAILABLE if backend == "oci" else None)}


def environment_identity() -> Dict[str, Any]:
    return {**os_and_architecture(), "python": platform.python_version()}


def release_identity(containment_backend: str, *, source_root: str = _SOURCE_ROOT) -> Dict[str, Any]:
    """The identity a release certification binds; ``digest`` covers every
    material field."""
    services = platform_services().identity()
    material = {
        "version": RELEASE_IDENTITY_VERSION,
        "kriya": kriya_revision(source_root),
        "platform": {"family": services["family"], "providers": services["providers"],
                     "capabilities": sorted([row["capability"], row["status"], row["provider"]]
                                            for row in services["capabilities"])},
        "containment": containment_identity(containment_backend),
        "environment": environment_identity(),
    }
    return {**material, "digest": _digest(material)}


def _digest(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":"))
                          .encode("utf-8")).hexdigest()


def _changes(recorded: Any, current: Any, path: str) -> List[str]:
    if isinstance(recorded, Mapping) and isinstance(current, Mapping):
        return [change for key in sorted(set(recorded) | set(current)) if key != "digest"
                for change in _changes(recorded.get(key), current.get(key), f"{path}.{key}" if path else key)]
    return [] if recorded == current else [path]


def compare_release_identity(recorded: Mapping[str, Any], current: Mapping[str, Any]) -> Dict[str, Any]:
    """CURRENT when nothing material changed; STALE (re-run required) with
    every changed field otherwise. A record that is not a release identity,
    or was made dirty or without a revision, is never CURRENT."""
    changes = _changes(dict(recorded), dict(current), "")
    if recorded.get("digest") != _digest({k: v for k, v in recorded.items() if k != "digest"}):
        changes.append("digest")
    kriya = recorded.get("kriya") or {}
    if kriya.get("revision") in (None, UNAVAILABLE) or kriya.get("dirty") is not False:
        changes.append("kriya.unpinned")
    return {"status": CURRENT if not changes else STALE, "changes": sorted(set(changes))}
