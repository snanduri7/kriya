"""ATOMIC-WRITE-NO-FSYNC-001 / OI-03 (and OI-04): every atomic write is crash-durable at the ONE canonical writer, and no caller carries its own
durability code.

Measured defect (2026-11-06): the default ``atomic_write_file`` did zero fsyncs (``test_the_default_writer_is_unchanged_it_performs_no_fsync_at_all``
pinned it) and left ``<target>.kriya-tmp-<pid>`` behind when the replace failed (OI-04); only call sites that passed ``durable=True`` got the
sequence. The faults are injected at the real OS calls, so the production path is what fails.

Platform contract (stated, not over-claimed): temp file beside the target -> write -> flush -> fsync(temp) -> atomic replace -> fsync(parent
directory) on POSIX; Windows has no directory fsync (the step is skipped there). macOS ``os.fsync`` is not ``F_FULLFSYNC``.
"""
import errno
import os
import stat
import subprocess

import pytest

from kriya.repository import atomic_write
from kriya.repository.atomic_write import DurableWriteError, atomic_write_file
from kriya.repository.commit import commit_revision_grounded_file, read_file_revision


class Trace:
    def __init__(self, monkeypatch, fail_at=None):
        self.events, self.fail_at, self.fsyncs = [], fail_at, 0
        real_fsync, real_replace = os.fsync, os.replace

        def fsync(fd):
            self.fsyncs += 1
            kind = "dir_fsync" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file_fsync"
            self.events.append(kind)
            if self.fail_at == kind:
                raise OSError(errno.EIO, f"injected {kind} failure")
            return real_fsync(fd)

        def replace(src, dst):
            self.events.append("replace")
            if self.fail_at == "replace":
                raise OSError(errno.EXDEV, "injected replace failure")
            return real_replace(src, dst)

        monkeypatch.setattr(os, "fsync", fsync)
        monkeypatch.setattr(os, "replace", replace)


def _no_temp(directory):
    return [name for name in os.listdir(directory) if ".kriya-" in name]


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


ORDER = ["file_fsync", "replace", "dir_fsync"]


def _entry_points(repo):
    target = repo / "plain.txt"
    target.write_text("OLD")
    state_target = repo / ".kriya" / "control" / "note.json"
    state_target.parent.mkdir(parents=True, exist_ok=True)
    state_target.write_text("{}")

    def grounded():
        commit_revision_grounded_file(str(target), "NEW", read_file_revision(str(target)))

    def control_file():
        from kriya.control.control_store import write_control_file

        write_control_file(str(repo), str(state_target), "{\"a\": 1}", expected_revision=read_file_revision(str(state_target)))

    def control_state():
        from kriya.control.persistence import save_control_state
        from kriya.control.state import ControlState

        save_control_state(str(repo), ControlState(schema_version=1, run_id="r1"))

    return {
        "atomic_write_file": lambda: atomic_write_file(str(target), "NEW"),
        "atomic_write_file(content_bytes)": lambda: atomic_write_file(str(target), "x", content_bytes=b"NEW"),
        "atomic_write_file(durable=False is ignored)": lambda: atomic_write_file(str(target), "NEW", durable=False),
        "commit_revision_grounded_file": grounded,
        "write_control_file": control_file,
        "save_control_state": control_state,
    }


@pytest.mark.parametrize("name", ["atomic_write_file", "atomic_write_file(content_bytes)", "atomic_write_file(durable=False is ignored)",
                                  "commit_revision_grounded_file", "write_control_file", "save_control_state"])
def test_every_entry_point_fsyncs_the_file_before_the_replace_and_the_directory_after(repo, monkeypatch, name):
    entry = _entry_points(repo)[name]
    trace = Trace(monkeypatch)
    entry()
    assert trace.events == ORDER, (name, trace.events)
    assert _no_temp(repo) == [] and _no_temp(repo / ".kriya" / "control") == []


@pytest.mark.parametrize("name", ["atomic_write_file", "commit_revision_grounded_file", "write_control_file", "save_control_state"])
@pytest.mark.parametrize("fail_at, original_intact", [("file_fsync", True), ("replace", True), ("dir_fsync", False)])
def test_a_failure_at_any_step_is_a_typed_fail_closed_error_with_no_temp_left(repo, monkeypatch, name, fail_at, original_intact):
    entries = _entry_points(repo)
    plain, state_file = repo / "plain.txt", repo / ".kriya" / "control" / "note.json"
    before = {p: p.read_bytes() for p in (plain, state_file)}
    Trace(monkeypatch, fail_at=fail_at)
    with pytest.raises(DurableWriteError) as caught:
        entries[name]()
    assert isinstance(caught.value, OSError) and caught.value.step == {"file_fsync": "file_fsync", "replace": "replace", "dir_fsync": "directory_fsync"}[fail_at]
    assert _no_temp(repo) == [] and _no_temp(repo / ".kriya" / "control") == [], "a temp file survived a failed transition (OI-04)"
    if original_intact:
        assert {p: p.read_bytes() for p in (plain, state_file)} == before, "the destination changed although the replace never happened"


def test_a_temp_write_failure_leaves_the_destination_intact_and_no_temp(repo, monkeypatch):
    target = repo / "plain.txt"
    target.write_text("OLD")
    real_open = open

    def failing_open(path, mode="r", *args, **kwargs):
        if ".kriya-durable" in str(path):
            raise OSError(errno.ENOSPC, "injected temp write failure")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(atomic_write, "open", failing_open, raising=False)
    with pytest.raises(DurableWriteError) as caught:
        atomic_write_file(str(target), "NEW")
    assert caught.value.step == "temp_write" and target.read_text() == "OLD" and _no_temp(repo) == []


def test_the_existing_mode_is_kept_and_set_before_the_replace(repo):
    target = repo / "run.sh"
    target.write_bytes(b"#!/bin/sh\n")
    os.chmod(target, 0o751)
    commit_revision_grounded_file(str(target), "text", read_file_revision(str(target)), content_bytes=b"#!/bin/sh\necho\n")
    assert stat.S_IMODE(target.stat().st_mode) == 0o751


def test_the_rollback_writer_also_fsyncs_the_directory(repo, monkeypatch):
    target = repo / "plain.txt"
    target.write_text("NEW")
    trace = Trace(monkeypatch)
    atomic_write._atomic_write_bytes(str(target), b"OLD")  # noqa: SLF001 - the rollback writer (commit.py restores originals with it)
    assert trace.events == ORDER and target.read_bytes() == b"OLD"


def test_windows_has_no_directory_fsync_and_the_step_is_skipped_there(tmp_path, monkeypatch):
    monkeypatch.setattr(atomic_write.os, "name", "nt")
    atomic_write._fsync_directory(str(tmp_path / "does-not-exist"), strict=True)  # noqa: SLF001 - would raise on POSIX


# --- mutations: restoring the defect must break these tests ---------------------------------------------------------------------
def test_mutation_a_non_durable_default_path_is_caught(repo, monkeypatch):
    """The OLD default (temp write + replace, no fsync, temp name ``.kriya-tmp-<pid>``) must fail the ordering contract."""
    def old_default(full_path, data, *, mode=None):
        tmp = f"{full_path}.kriya-tmp-{os.getpid()}"
        with open(tmp, "wb") as handle:
            handle.write(data)
        os.replace(tmp, full_path)

    monkeypatch.setattr(atomic_write, "durable_atomic_write_bytes", old_default)
    trace = Trace(monkeypatch)
    atomic_write_file(str(repo / "plain.txt"), "NEW")
    assert trace.events != ORDER, "the contract test would pass vacuously on the pre-fix writer"


def test_mutation_a_swallowed_directory_fsync_failure_is_caught(repo, monkeypatch):
    real = atomic_write._fsync_directory  # noqa: SLF001
    monkeypatch.setattr(atomic_write, "_fsync_directory", lambda path, strict=False: real(path, strict=False))
    Trace(monkeypatch, fail_at="dir_fsync")
    atomic_write_file(str(repo / "plain.txt"), "NEW")  # no error: exactly the defect the strict step prevents
    monkeypatch.undo()
    Trace(monkeypatch, fail_at="dir_fsync")
    with pytest.raises(DurableWriteError):
        atomic_write_file(str(repo / "plain.txt"), "NEW")
