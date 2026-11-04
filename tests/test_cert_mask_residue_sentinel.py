"""CERT-TMP-MASK-RESIDUE-EXCLUSIVE-001: the recording-only residue sentinel (tests/_mask_residue_sentinel.py).

Contract under test: it names the test during which a ``kriya-authority-mask-*`` directory appeared and whether it survived to the end of the
session; it never removes, creates or alters anything, never raises on an unwritable record file, and is wired into the certification hooks only
when ``--certification-report`` is active. Each assertion executes (no conditional assertions) and each fails if the sentinel stops observing."""
from __future__ import annotations

import importlib
import json
import os
import stat
from types import SimpleNamespace

import pytest
from _mask_residue_sentinel import PREFIX, RECORD_FILE, MaskResidueSentinel


def _records(directory):
    with open(os.path.join(directory, RECORD_FILE), encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


@pytest.fixture(name="layout")
def _layout(tmp_path):
    root, out = tmp_path / "tmproot", tmp_path / "out"
    root.mkdir()
    out.mkdir()
    return SimpleNamespace(root=str(root), out=str(out), tick=iter(range(1000, 2000)))


def _sentinel(layout):
    return MaskResidueSentinel(layout.out, layout.root, clock=lambda: float(next(layout.tick)))


def test_a_directory_left_by_a_test_is_attributed_to_that_test_and_reported_present_at_finish(layout):
    sentinel = _sentinel(layout)
    sentinel.start("tests/test_a.py::test_leaves")
    leaked = os.path.join(layout.root, PREFIX + "abc123")
    os.mkdir(leaked, 0o700)                                                   # mkdtemp's mode, as the product creates it
    sentinel.end("tests/test_a.py::test_leaves")
    sentinel.start("tests/test_b.py::test_innocent")
    sentinel.end("tests/test_b.py::test_innocent")
    sentinel.finish()
    records = _records(layout.out)
    appeared = [r for r in records if r["event"] == "appeared"]
    final = [r for r in records if r["event"] == "final"]
    summary = [r for r in records if r["event"] == "summary"][0]
    assert [r["node"] for r in appeared] == ["tests/test_a.py::test_leaves"]      # the creator, not the next test
    assert appeared[0]["path"] == leaked and appeared[0]["empty"] is True and appeared[0]["mode"] == "0o700"
    assert [(r["node"], r["present_at_finish"]) for r in final] == [("tests/test_a.py::test_leaves", True)]
    assert summary["appeared"] == 1 and summary["present_at_finish"] == 1 and summary["write_errors"] == 0


def test_a_directory_created_and_removed_inside_one_test_is_not_recorded(layout):
    sentinel = _sentinel(layout)
    sentinel.start("tests/test_a.py::test_cleans_up")
    scratch = os.path.join(layout.root, PREFIX + "tidy")
    os.mkdir(scratch)
    os.rmdir(scratch)
    sentinel.end("tests/test_a.py::test_cleans_up")
    sentinel.finish()
    records = _records(layout.out)
    assert [r["event"] for r in records] == ["summary"]
    assert records[0]["appeared"] == 0 and records[0]["present_at_finish"] == 0


def test_a_directory_that_existed_before_the_test_and_other_prefixes_are_not_attributed(layout):
    os.mkdir(os.path.join(layout.root, PREFIX + "preexisting"))
    sentinel = _sentinel(layout)
    sentinel.start("tests/test_a.py::test_unrelated")
    os.mkdir(os.path.join(layout.root, "kriya-strict-config-xyz"))           # another family: CERT-TMP-STRICT-RESIDUAL-001's, not this sentinel's
    sentinel.end("tests/test_a.py::test_unrelated")
    sentinel.finish()
    assert [r["event"] for r in _records(layout.out)] == ["summary"]


def test_residue_a_later_test_cleans_up_is_reported_as_gone_at_finish(layout):
    sentinel = _sentinel(layout)
    sentinel.start("tests/test_a.py::test_transient")
    transient = os.path.join(layout.root, PREFIX + "later")
    os.mkdir(transient)
    sentinel.end("tests/test_a.py::test_transient")
    os.rmdir(transient)                                                       # some later test removed it
    sentinel.finish()
    final = [r for r in _records(layout.out) if r["event"] == "final"]
    assert [(r["node"], r["present_at_finish"]) for r in final] == [("tests/test_a.py::test_transient", False)]


def test_a_directory_is_recorded_once_even_if_it_stays_for_many_tests(layout):
    sentinel = _sentinel(layout)
    sentinel.start("t::first")
    os.mkdir(os.path.join(layout.root, PREFIX + "stays"))
    sentinel.end("t::first")
    for node in ("t::second", "t::third"):
        sentinel.start(node)
        sentinel.end(node)
    sentinel.finish()
    assert [r["node"] for r in _records(layout.out) if r["event"] == "appeared"] == ["t::first"]


def test_the_sentinel_only_observes_it_never_removes_or_alters_a_directory(layout):
    sentinel = _sentinel(layout)
    sentinel.start("t::leaves")
    leaked = os.path.join(layout.root, PREFIX + "keep")
    os.mkdir(leaked, 0o700)
    marker = os.path.join(leaked, "marker")
    with open(marker, "w", encoding="utf-8") as handle:
        handle.write("x")
    sentinel.end("t::leaves")
    sentinel.finish()
    assert os.path.isdir(leaked) and os.path.exists(marker)                  # untouched, content included
    assert stat.S_IMODE(os.stat(leaked).st_mode) == 0o700
    appeared = [r for r in _records(layout.out) if r["event"] == "appeared"][0]
    assert appeared["empty"] is False                                         # and it reports what it saw, not what it expected


def test_an_unwritable_record_file_is_counted_and_never_raises(layout):
    sentinel = MaskResidueSentinel(os.path.join(layout.out, "does", "not", "exist"), layout.root)
    sentinel.start("t::leaves")
    os.mkdir(os.path.join(layout.root, PREFIX + "x"))
    sentinel.end("t::leaves")                                                 # must not raise
    sentinel.finish()                                                         # must not raise
    assert sentinel.write_errors >= 2


def test_a_missing_temp_root_is_not_an_error(layout):
    sentinel = MaskResidueSentinel(layout.out, os.path.join(layout.root, "gone"))
    sentinel.start("t::x")
    sentinel.end("t::x")
    sentinel.finish()
    assert [r["event"] for r in _records(layout.out)] == ["summary"]


def test_the_conftest_hooks_drive_the_sentinel_only_for_a_teardown_report_and_are_inert_when_unset(layout):
    hooks = importlib.import_module("conftest")
    previous, previous_progress = hooks._MASK_SENTINEL[0], hooks._PROGRESS[0]  # pylint: disable=protected-access
    try:
        hooks._PROGRESS[0] = None                                             # pylint: disable=protected-access  # a live certification run's progress file must not see these fake reports
        hooks._MASK_SENTINEL[0] = None                                        # pylint: disable=protected-access
        hooks.pytest_runtest_logstart("t::inert", None)                        # no sentinel: nothing happens, nothing raises
        hooks.pytest_runtest_logreport(SimpleNamespace(when="teardown", nodeid="t::inert", failed=False, skipped=False, passed=True))
        hooks._MASK_SENTINEL[0] = _sentinel(layout)                           # pylint: disable=protected-access
        hooks.pytest_runtest_logstart("t::leaves", None)
        os.mkdir(os.path.join(layout.root, PREFIX + "viahooks"))
        hooks.pytest_runtest_logreport(SimpleNamespace(when="call", nodeid="t::leaves", failed=False, skipped=False, passed=True))
        assert not os.path.exists(os.path.join(layout.out, RECORD_FILE))      # a call-phase report is not the end of the test
        hooks.pytest_runtest_logreport(SimpleNamespace(when="teardown", nodeid="t::leaves", failed=False, skipped=False, passed=True))
        assert [r["node"] for r in _records(layout.out) if r["event"] == "appeared"] == ["t::leaves"]
    finally:
        hooks._MASK_SENTINEL[0], hooks._PROGRESS[0] = previous, previous_progress  # pylint: disable=protected-access
