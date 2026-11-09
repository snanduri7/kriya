"""The owner contract for the end of a gate view's life (GATE-VIEW-REMOVAL-NOT-IMMEDIATE-ON-DOCKER-DESKTOP-001, owner ruling 2026-11-09).

Immediate physical deletion is NOT part of the contract: MEASURED, right after a container that bind-mounted a view exits, the host's rmdir of the mount-point directories can fail for a
moment on Docker Desktop, and a sleep or retry to hide that is forbidden. What a finished gate owes is:

  1. logical ownership is released (the view is no longer open in this process),
  2. whatever physically remains is residue the next claim can sweep: a name of the current unique form owned by this process, never a name that any live view could be using,
  3. nothing about the origin tree changed (asserted by the callers, as before).

The deterministic proof of "a failed removal is logged and swept by the next claim, and the name is never reused" is in tests/test_gate_view_unique_bind_source_001.py; the real-Docker tests use
this so that they assert the same contract and do not depend on how quickly the host file share lets go.
"""
from __future__ import annotations

import os
import re

from kriya.tools import gate_view

_UNIQUE = re.compile(r"^view-([0-9a-f]{16})([0-9a-f]{8})([0-9a-f]{6})$")


def assert_view_released(view_root: str) -> None:
    """``view_root`` is not open in this process; if anything of it is still on disk it is a sweepable residue of this process."""
    real = os.path.realpath(view_root)
    with gate_view._ACTIVE_LOCK:                                     # noqa: SLF001 - the contract's observable: logical ownership
        still_open = real in gate_view._ACTIVE_ROOTS                 # noqa: SLF001
    assert not still_open, f"the view {view_root} is still open in this process after the gate finished"
    if os.path.lexists(view_root):
        match = _UNIQUE.match(os.path.basename(view_root))
        assert match is not None, f"residue {view_root} is not of the current unique form, so no claim would ever sweep it"
        assert int(match.group(2), 16) == os.getpid(), f"residue {view_root} is not owned by this process"


def assert_views_released(views_directory: str) -> None:
    """Every entry under the views directory satisfies :func:`assert_view_released` (an absent or empty directory trivially does)."""
    if not os.path.isdir(views_directory):
        return
    for name in sorted(os.listdir(views_directory)):
        assert_view_released(os.path.join(views_directory, name))
