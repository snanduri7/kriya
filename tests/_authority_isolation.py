"""Test bootstrap: the pytest process never has the operator's real authority home as its ``KRIYA_AUTHORITY_HOME``.

One session-scoped autouse fixture (registered in ``tests/conftest.py``) runs before any per-test fixture:

1. it resolves the REAL authority home (the value the operator configured when pytest started, else ``~/.kriya/authority`` from the account database)
   and takes a read-only STAT snapshot of it (path, mode, size, mtime - file contents are never read);
2. it points ``KRIYA_AUTHORITY_HOME`` at a session sandbox, directly in ``os.environ`` (not through a per-test monkeypatch), so the value a per-test
   ``monkeypatch.setenv`` saves - and a ``monkeypatch.undo()`` restores - is the sandbox, never the operator's home; child processes inherit it;
3. at session end it restores the original environment and FAILS the session if the real home changed. It only ever reads: it never deletes, restores
   or writes anything under the real home.

Production authority code is untouched: nothing here is a test-mode branch of ``authority_home()``.
"""
from __future__ import annotations

import os
import pwd
from typing import Dict, Optional, Tuple

import pytest

AUTHORITY_HOME = "KRIYA_AUTHORITY_HOME"
Entry = Tuple[int, int, int]  # (st_mode, st_size, st_mtime_ns)
Snapshot = Optional[Dict[str, Entry]]  # None: the home does not exist
_REAL_HOME_AT_START: Optional[str] = None


def session_real_home() -> Optional[str]:
    """The operator's real authority home as resolved when this session started (None before the fixture ran)."""
    return _REAL_HOME_AT_START


def real_authority_home(configured: Optional[str]) -> str:
    """The home the operator would really use: the value configured when pytest started, else the account database's ``~/.kriya/authority``
    ($HOME is deliberately not consulted: a test must not be able to redirect what this guard watches)."""
    if configured:
        return os.path.realpath(os.path.expanduser(configured))
    return os.path.realpath(os.path.join(pwd.getpwuid(os.getuid()).pw_dir, ".kriya", "authority"))


def snapshot(home: str) -> Snapshot:
    """Read-only: ``lstat`` of the home and everything beneath it (symlinks are not followed). Contents are never opened."""
    if not os.path.lexists(home):
        return None
    entries: Dict[str, Entry] = {}

    def record(path: str) -> None:
        try:
            info = os.lstat(path)
        except FileNotFoundError:  # vanished between listing and stat: recorded as absent, which the comparison reports
            return
        entries[os.path.relpath(path, home)] = (info.st_mode, info.st_size, info.st_mtime_ns)

    record(home)
    for directory, directories, files in os.walk(home, followlinks=False):
        for name in directories + files:
            record(os.path.join(directory, name))
    return entries


def changes(before: Snapshot, after: Snapshot) -> Tuple[str, ...]:
    if before is None and after is None:
        return ()
    if before is None:
        return (f"the home was created ({len(after or {})} entries)",)
    if after is None:
        return ("the home was removed",)
    found = [f"added: {name}" for name in sorted(set(after) - set(before))]
    found += [f"removed: {name}" for name in sorted(set(before) - set(after))]
    found += [f"modified: {name}" for name in sorted(name for name in set(before) & set(after) if before[name] != after[name])]
    return tuple(found)


@pytest.fixture(scope="session", autouse=True)
def _authority_home_isolation(tmp_path_factory):
    configured = os.environ.get(AUTHORITY_HOME)
    global _REAL_HOME_AT_START  # noqa: PLW0603 - the one place the session records what it is protecting
    real_home = _REAL_HOME_AT_START = real_authority_home(configured)
    before = snapshot(real_home)
    os.environ[AUTHORITY_HOME] = str(tmp_path_factory.mktemp("kriya-authority-session"))
    try:
        yield
    finally:
        if configured is None:
            os.environ.pop(AUTHORITY_HOME, None)
        else:
            os.environ[AUTHORITY_HOME] = configured
    found = changes(before, snapshot(real_home))
    if found:
        shown = "; ".join(found[:10]) + (f"; ... and {len(found) - 10} more" if len(found) > 10 else "")
        pytest.fail(
            f"the real authority home {real_home} changed during the test session ({shown}). A test wrote outside its sandbox; nothing was "
            "deleted or restored here. If you used Kriya yourself while the session ran, re-run to confirm.",
            pytrace=False,
        )
