"""The process working directory (full modularization D3): CONTEXT is its one owner (DEPENDENCY-RULES §2: ``os.getcwd()`` only at the
edge - ADAPTER-CLI, ADAPTER-KUP - and in CONTEXT).

* ``current_directory()``: ``os.getcwd()``. Configuration discovery (``kriya/config/resolution.py::resolve_config_state``) reads the
  directory a terminal ``kriya`` command was started in through it: that IS the workspace of a local CLI request.
* ``WORKING_DIRECTORY_LOCK`` and ``entered(directory)``: resolving a FOLDER's configuration the way a run started in that folder would,
  by temporarily entering it, is a process-global effect, so every in-process user of it (``ConfigService.resolve`` for a request's
  workspace, the KUP skill/tool catalog that also discovers ``skills/`` beside the configuration) holds this ONE lock while inside. The
  lock moved here from ``kriya/kup/execution.py`` (``_CWD_LOCK``, which stays its alias) in D3. It is not reentrant: ``entered`` requires
  the caller to hold it.

Why the folder is entered instead of resolved with an explicit root (MEASURED, D3): the resolution's own cwd reads could take the root as
a parameter with identical results (``canonical_workspace`` == ``os.getcwd()`` after ``os.chdir`` of it, including case variants on a
case-insensitive filesystem), but a RELATIVE ``KRIYA_TRUST_FILE`` (and any relative path an approval or trust lookup realpaths) resolves
against the process working directory inside ``load_config`` - with the folder entered it lands inside the folder and is refused as a
trust path inside the workspace (tests/golden/config/kup_config_forms.json, ``trust_relative``); without it, it would resolve against the
KUP process's own directory. Removing the chdir is therefore a behaviour change for that input: it belongs to the slice that makes
resolution take ``RequestContext.workspace`` explicitly (O1), not to a PRESERVE slice.
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Iterator

WORKING_DIRECTORY_LOCK = threading.Lock()


def current_directory() -> str:
    """``os.getcwd()``."""
    return os.getcwd()


@contextmanager
def entered(directory: str) -> Iterator[None]:
    """Run the body with ``directory`` as the process working directory, then return to the previous one. The caller holds
    ``WORKING_DIRECTORY_LOCK`` for the whole body."""
    previous = os.getcwd()
    os.chdir(directory)
    try:
        yield
    finally:
        os.chdir(previous)


__all__ = ["WORKING_DIRECTORY_LOCK", "current_directory", "entered"]
