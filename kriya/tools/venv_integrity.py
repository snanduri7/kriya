"""OI-142 / owner decision O4 (2026-10-23): the project venv (``<workspace>/.kriya/venv``) is a shared, reusable EXECUTION-ENVIRONMENT
INPUT of every Python gate. A gate runs candidate code with the operator's rights, so on the host it can write into that venv; a
later gate would then execute whatever it left there (a changed ``site-packages`` module, a ``.pth`` file, a planted ``.pyc``).
Host mode cannot make the venv read-only, so the venv is SEALED when Kriya has finished installing into it and VERIFIED before every
reuse: any difference from the sealed content is an unexpected mutation, and the caller discards the venv and creates it again
(never reuses it).

The seal is a manifest of the content identity (permission bits + raw SHA-256, link target, directory) of every path in the venv
except interpreter bytecode. Bytecode is a disposable cache the interpreter rewrites by itself - and a ``.pyc`` is exactly what a
gate could plant - so ``verify`` DELETES every ``__pycache__`` directory first: the next import compiles from the sealed source.
"""

from __future__ import annotations

import json
import os
import shutil
from typing import Dict, List, Optional

from kriya.repository.snapshot import _content_identity

_MANIFEST = ".kriya-integrity.json"
_BYTECODE = "__pycache__"


def _identities(venv_dir: str) -> Dict[str, Optional[str]]:
    found: Dict[str, Optional[str]] = {}
    for directory, dirnames, filenames in os.walk(venv_dir):
        dirnames[:] = [name for name in dirnames if name != _BYTECODE]
        relative = os.path.relpath(directory, venv_dir)
        for name in list(filenames) + [name for name in dirnames if os.path.islink(os.path.join(directory, name))]:
            relpath = os.path.normpath(os.path.join(relative, name))
            if relpath != _MANIFEST:
                found[relpath] = _content_identity(os.path.join(venv_dir, relpath))
    return found


def seal(venv_dir: str) -> None:
    """Record the venv's current content as the content it must have when it is next used."""
    _purge_bytecode(venv_dir)
    with open(os.path.join(venv_dir, _MANIFEST), "w", encoding="utf-8") as handle:
        json.dump({"files": _identities(venv_dir)}, handle, sort_keys=True)


def mismatches(venv_dir: str) -> List[str]:
    """The paths whose content is not the sealed content (changed, added or removed), after bytecode is removed. A venv with no readable
    seal - created before sealing existed, or never completed - has no content to trust: ``["<unsealed>"]``."""
    try:
        with open(os.path.join(venv_dir, _MANIFEST), "r", encoding="utf-8") as handle:
            sealed = json.load(handle).get("files")
    except (OSError, ValueError, AttributeError):
        return ["<unsealed>"]
    if not isinstance(sealed, dict):
        return ["<unsealed>"]
    _purge_bytecode(venv_dir)
    try:
        current = _identities(venv_dir)
    except OSError as error:
        return [f"<unreadable: {error}>"]
    return sorted(path for path in set(sealed) | set(current) if sealed.get(path) != current.get(path))


def _purge_bytecode(venv_dir: str) -> None:
    for directory, dirnames, _filenames in os.walk(venv_dir):
        for name in [name for name in dirnames if name == _BYTECODE]:
            shutil.rmtree(os.path.join(directory, name), ignore_errors=True)
        dirnames[:] = [name for name in dirnames if name != _BYTECODE]
