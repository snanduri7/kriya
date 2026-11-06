"""Atomic write of one file (full modularization, slice M1): ``atomic_write_file`` and ``commit_revision_grounded_file``.

The invariant (``atomic_write_file``'s docstring, the 2026-08-16 SIGKILL incident): the file on disk is always EITHER
the complete old content OR the complete new content. The bytes are written to a temp file beside the target and
moved over it with ``os.replace``; a write that dies half way leaves the target untouched. The faults are injected
at the os level (``builtins.open`` / ``os.replace``), so these tests hold wherever the mechanism lives.
"""
from __future__ import annotations

import builtins
import os
from pathlib import Path

import pytest

from kriya.repository.atomic_write import DurableWriteError
from kriya.workflow import edit_safety as es
from tests._repository_mechanism_harness import Recorder

OLD = b"complete old content\n" * 64


class _HalfWriter:
    """A file handle that writes half of what it is given, flushes it to disk, then fails (a full disk mid-write)."""

    def __init__(self, handle):
        self._handle = handle

    def write(self, data):
        self._handle.write(data[: len(data) // 2])
        self._handle.flush()
        raise OSError(28, "No space left on device")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._handle.close()
        return False

    def __getattr__(self, name):
        return getattr(self._handle, name)


@pytest.fixture
def half_writes(monkeypatch, tmp_path):
    real_open = builtins.open
    opened = []

    def failing_open(file, mode="r", *args, **kwargs):
        handle = real_open(file, mode, *args, **kwargs)
        if isinstance(file, (str, os.PathLike)) and str(file).startswith(str(tmp_path)) and "w" in mode:
            opened.append(str(file))
            return _HalfWriter(handle)
        return handle

    monkeypatch.setattr(builtins, "open", failing_open)
    return opened


@pytest.mark.parametrize("call", ["atomic_write_file", "atomic_write_file_bytes", "commit_revision_grounded_file"])
def test_partial_write_never_visible(tmp_path, half_writes, call):
    target = tmp_path / "target.txt"
    target.write_bytes(OLD)
    new = "complete new content\n" * 64
    with pytest.raises(OSError, match="No space left on device"):
        if call == "atomic_write_file":
            es.atomic_write_file(str(target), new)
        elif call == "atomic_write_file_bytes":
            es.atomic_write_file(str(target), "ignored", content_bytes=new.encode("utf-8"))
        else:
            es.commit_revision_grounded_file(str(target), new, es.read_file_revision(str(target)))
    assert half_writes, "the fault was never injected: the test would pass vacuously"
    assert all(path != str(target) for path in half_writes), "the target itself was opened for writing"
    assert target.read_bytes() == OLD


def test_the_temp_file_lives_beside_the_target_and_replaces_it(tmp_path):
    (tmp_path / "sub").mkdir()
    target = tmp_path / "sub" / "target.txt"
    target.write_bytes(OLD)
    with Recorder(str(tmp_path)) as recorder:
        es.atomic_write_file(str(target), "new\n")
    calls = recorder.calls()
    # WAVE8 OI-03: the write is the durable sequence (temp beside the target -> write -> fsync -> replace -> fsync of the directory)
    temp = "<root>/sub/target.txt.kriya-durable-<pid>-12345678"
    assert calls[0] == ["open", temp, "wb"] and ["replace", temp, "<root>/sub/target.txt"] in calls
    assert calls.index(["replace", temp, "<root>/sub/target.txt"]) > 0
    assert target.read_bytes() == b"new\n" and sorted(os.listdir(tmp_path / "sub")) == ["target.txt"]


def test_a_failed_replace_leaves_the_target_untouched_and_no_temp_file(tmp_path):
    """WAVE8 OI-04 (was the characterization of ATOMIC-WRITE-TEMP-LEFTOVER-001): the temp file IS removed when the replace fails, the failure is
    a typed DurableWriteError (an OSError) naming the step, and the target is untouched."""
    target = tmp_path / "target.txt"
    target.write_bytes(OLD)

    def fault(op, args):
        return OSError("replace failed") if op == "replace" else None

    with pytest.raises(DurableWriteError, match="replace failed") as caught, Recorder(str(tmp_path), fault=fault):
        es.atomic_write_file(str(target), "new\n")
    assert caught.value.step == "replace" and isinstance(caught.value, OSError)
    assert target.read_bytes() == OLD
    assert sorted(os.listdir(tmp_path)) == ["target.txt"]


def test_a_grounded_write_keeps_the_existing_mode_and_returns_the_written_bytes_revision(tmp_path):
    target = tmp_path / "run.sh"
    target.write_bytes(b"#!/bin/sh\n")
    os.chmod(target, 0o751)
    revision = es.commit_revision_grounded_file(str(target), "text view", es.read_file_revision(str(target)),
                                                content_bytes=b"#!/bin/sh\r\necho\r\n")
    assert target.read_bytes() == b"#!/bin/sh\r\necho\r\n" and os.stat(target).st_mode & 0o7777 == 0o751
    assert revision == es.read_file_revision(str(target))


def test_a_stale_grounded_write_never_touches_the_target(tmp_path):
    target = tmp_path / "f.txt"
    target.write_bytes(OLD)
    with pytest.raises(es.FileRevisionConflict, match="Refusing stale write"):
        es.commit_revision_grounded_file(str(target), "new\n", es.content_revision("something else\n"))
    assert target.read_bytes() == OLD and sorted(os.listdir(tmp_path)) == ["f.txt"]


def test_a_symbolic_link_is_never_replaced_by_a_regular_file(tmp_path):
    real = tmp_path / "real.txt"
    real.write_bytes(OLD)
    link = Path(tmp_path / "link.txt")
    os.symlink("real.txt", link)
    with pytest.raises(es.FileRevisionConflict, match="symbolic link"):
        es.commit_revision_grounded_file(str(link), "new\n", es.read_file_revision(str(link)))
    assert os.path.islink(link) and real.read_bytes() == OLD
