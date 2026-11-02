"""PRD-034 performance probe plugin (loaded with ``-p _perf_probe_plugin`` by scripts/certification_perf_probe.py).

It measures the two quantities behind the c3-run1 crawl (CERT-TMP-GROWTH-001): how much wall time the process spends listing directories
(``kriya.platform.filesystem_semantics.canonical_spelling`` lists every ancestor of a path, so each call costs O(entries in those directories)), and
whether pytest's basetemp keeps growing while the tests pass. Both are written to ``$PERF_PROBE_OUT`` at exit. It changes no behaviour.
"""
from __future__ import annotations

import atexit
import json
import os
import time

import pytest

_listing = {"calls": 0, "seconds": 0.0, "max_entries": 0, "worst_path": ""}
_samples = []  # (tests completed, entries directly under pytest's basetemp)
_state = {"done": 0, "base": None}

_real_listdir = os.listdir
_real_scandir = os.scandir


def _timed_listdir(path="."):
    started = time.perf_counter()
    entries = _real_listdir(path)
    _listing["calls"] += 1
    _listing["seconds"] += time.perf_counter() - started
    if len(entries) > _listing["max_entries"]:
        _listing.update(max_entries=len(entries), worst_path=str(path))
    return entries


class _TimedScandir:
    def __init__(self, path):
        self._started = time.perf_counter()
        self._iterator = _real_scandir(path)
        self._count = 0
        self._path = str(path)

    def __iter__(self):
        return self

    def __next__(self):
        try:
            entry = next(self._iterator)
        except StopIteration:
            self._finish()
            raise
        self._count += 1
        return entry

    def _finish(self):
        _listing["calls"] += 1
        _listing["seconds"] += time.perf_counter() - self._started
        if self._count > _listing["max_entries"]:
            _listing.update(max_entries=self._count, worst_path=self._path)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._iterator.close()
        return False

    def close(self):
        self._iterator.close()


os.listdir = _timed_listdir
os.scandir = _TimedScandir


@pytest.fixture(scope="session", autouse=True)
def _probe_basetemp(tmp_path_factory):
    _state["base"] = str(tmp_path_factory.getbasetemp())
    yield


def pytest_runtest_logreport(report):
    if report.when != "teardown":
        return
    _state["done"] += 1
    base = _state["base"]
    if base and os.path.isdir(base):
        _samples.append((_state["done"], len(_real_listdir(base))))


@atexit.register
def _write():
    target = os.environ.get("PERF_PROBE_OUT")
    if not target:
        return
    with open(target, "w", encoding="utf-8") as handle:
        json.dump({"listing": _listing, "basetemp_samples": _samples}, handle)
