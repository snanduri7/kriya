"""A cross-process mutual-exclusion lock for the few test resources that are genuinely machine-wide (CERT-SHARED-PORT-001).

MEASURED (c3 follow-up broad regression): demo-v2's scenario-03 stub listens on the FIXED port 18767 (``demo-v2/fixtures/controlled-repair/fixture.env``, baked into the operator
config by prepare_demo.sh). Two test processes that start it at once both report success and one of them has no stub (``stub_ctl.sh status`` exits 1): 6 of 6 trials of two
concurrent pytest processes failed ``test_idle_check_sees_a_running_stub`` once, and 12 of 12 raced ``start`` pairs left a loser. The operator scripts are the owner's demo and keep their
port; the TESTS serialise on it instead - across xdist workers, static shards and a developer running two pytest sessions at once.

The lock file lives in ``KRIYA_CERT_LOCK_DIR`` when set (certify.sh exports one directory shared by every shard) and otherwise in ``/tmp`` (machine-wide; TMPDIR is per process).
"""
from __future__ import annotations

import contextlib
import fcntl
import os
import tempfile
from pathlib import Path


def _machine_wide_directory() -> str:
    """A machine-wide resource needs a machine-wide lock: TMPDIR differs per process (certify.sh gives every shard its own), ``/tmp`` does not."""
    return "/tmp" if os.path.isdir("/tmp") and os.access("/tmp", os.W_OK) else tempfile.gettempdir()


def lock_path(name: str) -> Path:
    return Path(os.environ.get("KRIYA_CERT_LOCK_DIR") or _machine_wide_directory()) / f"kriya-test-lock-{name}.lock"


@contextlib.contextmanager
def shared_resource_lock(name: str):
    """Hold an exclusive advisory lock named ``name`` for the duration of the block (blocks until it is free; released with the process if it dies)."""
    path = lock_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield path
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
