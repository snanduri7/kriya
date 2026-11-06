"""D1 slice 1: the strict durable-write path at kriya/repository/atomic_write.py - temp write -> file fsync -> atomic replace -> parent-directory fsync, every step required.

The faults are injected at the real OS calls (`open`, `os.fsync`, `os.replace`) so the production code path is what fails; the ordering is observed, not assumed."""
import errno
import os
import stat

import pytest

from kriya.repository import atomic_write
from kriya.repository.atomic_write import (
    DURABLE_WRITE_STEPS,
    DurableWriteError,
    atomic_write_file,
    durable_atomic_write_bytes,
)


@pytest.fixture
def target(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("OLD")
    return path


def _leftovers(tmp_path):
    return sorted(p.name for p in tmp_path.iterdir() if "kriya-durable" in p.name or "kriya-tmp" in p.name)


class _Trace:
    """Records the order of the real file fsync, replace and directory fsync."""

    def __init__(self, monkeypatch, *, fail_fsync_call=None, fail_replace=False):
        self.events = []
        self.fsync_calls = 0
        real_fsync, real_replace = os.fsync, os.replace

        def fsync(fd):
            self.fsync_calls += 1
            kind = "dir_fsync" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file_fsync"
            self.events.append(kind)
            if self.fsync_calls == fail_fsync_call:
                raise OSError(errno.EIO, "injected fsync failure")
            return real_fsync(fd)

        def replace(src, dst):
            self.events.append("replace")
            if fail_replace:
                raise OSError(errno.EXDEV, "injected replace failure")
            return real_replace(src, dst)

        monkeypatch.setattr(os, "fsync", fsync)
        monkeypatch.setattr(os, "replace", replace)


def test_the_durable_write_fsyncs_the_file_before_the_replace_and_the_directory_after_it(tmp_path, target, monkeypatch):
    trace = _Trace(monkeypatch)
    durable_atomic_write_bytes(str(target), b"NEW")
    assert trace.events == ["file_fsync", "replace", "dir_fsync"], trace.events
    assert target.read_bytes() == b"NEW" and _leftovers(tmp_path) == []


def test_the_mode_is_applied_before_the_replace_so_it_is_covered_by_the_same_fsync(tmp_path, target):
    target.chmod(0o640)
    atomic_write_file(str(target), "NEW", durable=True, mode=0o640)
    assert stat.S_IMODE(target.stat().st_mode) == 0o640 and target.read_text() == "NEW"


@pytest.mark.parametrize("step", DURABLE_WRITE_STEPS)
def test_each_step_failure_fails_the_transition_with_that_step_named(tmp_path, target, monkeypatch, step):
    if step == "temp_write":
        real_open = open

        def failing_open(path, mode="r", *args, **kwargs):
            if "kriya-durable" in str(path):
                raise OSError(errno.ENOSPC, "injected temp write failure")
            return real_open(path, mode, *args, **kwargs)
        monkeypatch.setattr(atomic_write, "open", failing_open, raising=False)
    elif step == "file_fsync":
        _Trace(monkeypatch, fail_fsync_call=1)
    elif step == "replace":
        _Trace(monkeypatch, fail_replace=True)
    else:
        _Trace(monkeypatch, fail_fsync_call=2)
    with pytest.raises(DurableWriteError) as caught:
        durable_atomic_write_bytes(str(target), b"NEW")
    assert caught.value.step == step and isinstance(caught.value.__cause__, OSError)
    assert _leftovers(tmp_path) == [], "a temp file survived a failed transition"
    if step != "directory_fsync":
        assert target.read_bytes() == b"OLD", "the previous content must be intact when the replace never happened"


def test_a_directory_fsync_failure_is_never_swallowed_but_the_default_best_effort_path_still_is(tmp_path, monkeypatch):
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError(errno.EIO, "x")) if stat.S_ISDIR(os.fstat(fd).st_mode) else real_fsync(fd))
    atomic_write._fsync_directory(str(tmp_path))                      # default: unchanged best-effort behaviour (negative control)
    with pytest.raises(OSError):
        atomic_write._fsync_directory(str(tmp_path), strict=True)     # strict: propagates
    with pytest.raises(OSError):
        atomic_write._fsync_directory(str(tmp_path / "missing"), strict=True)


def test_the_default_writer_is_durable_it_performs_the_full_sequence(tmp_path, target, monkeypatch):
    """WAVE8 OI-03 (was ``..._is_unchanged_it_performs_no_fsync_at_all``, which PINNED the defect): the default ``atomic_write_file`` is the durable path."""
    trace = _Trace(monkeypatch)
    atomic_write_file(str(target), "NEW")
    assert trace.events == ["file_fsync", "replace", "dir_fsync"] and target.read_text() == "NEW"
    assert _leftovers(tmp_path) == []


def test_the_default_writer_failure_contract_is_typed_and_leaves_no_temp(tmp_path, target, monkeypatch):
    _Trace(monkeypatch, fail_fsync_call=1)
    with pytest.raises(DurableWriteError) as caught:
        atomic_write_file(str(target), "NEW")
    assert caught.value.step == "file_fsync" and target.read_text() == "OLD" and _leftovers(tmp_path) == []


def test_the_control_state_save_chain_is_durable_by_default_and_with_the_flag(tmp_path, monkeypatch):
    import subprocess

    from kriya.control.persistence import control_state_path, load_control_state, save_control_state
    from kriya.control.state import ControlState
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    state = ControlState(schema_version=1, run_id="r1")
    trace = _Trace(monkeypatch)
    save_control_state(str(tmp_path), state)                       # WAVE8 OI-03: the default is the full durable sequence too
    assert trace.events == ["file_fsync", "replace", "dir_fsync"]
    trace.events.clear()
    save_control_state(str(tmp_path), state, durable=True)         # D1: the full durable sequence
    assert trace.events == ["file_fsync", "replace", "dir_fsync"]
    assert load_control_state(str(tmp_path)).run_id == "r1"
    _Trace(monkeypatch, fail_fsync_call=1)
    with pytest.raises(DurableWriteError) as caught:
        save_control_state(str(tmp_path), state, durable=True)
    assert caught.value.step == "file_fsync" and os.path.isfile(control_state_path(str(tmp_path)))
