"""TEST-HARNESS-SESSION-BASE-PATHNAME-REUSE-001: a pytest invocation's base directory pathname never recurs across invocations.

MEASURED 2026-11-11: pytest numbers its base "highest existing + 1" and removes the base of a fully passing session, so consecutive
invocations were handed the same pathname (three in a row on pytest-6539); the container sessions that were refused as a whole
(a 120-test sequence 40/40; 52 of 327 at -n 8) ran on such a recycled name. That reuse being the CAUSE of those refusals is a
hypothesis the real-Docker qualification decides; what is pinned here is only the allocation: a unique root per invocation
(tests/conftest.py::_use_a_session_unique_base), every xdist worker beneath it, nothing left behind by a passing session, and no
reuse after deletion.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import conftest
import pytest

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
SAMPLE = TESTS / "certification" / "sample_session_base.py"
UNIQUE = re.compile(r"/kriya-pytest-[^/]+/pytest-of-[^/]+/pytest-\d+(?:/popen-gw\d+)?$")


def _invoke(tmp_path, record, *args):
    """One real pytest invocation of the sample with this repository's conftest; its temp root is <tmp_path>/t."""
    environ = {key: value for key, value in os.environ.items() if key not in ("PYTEST_DEBUG_TEMPROOT", "PYTEST_XDIST_WORKER", "PYTEST_XDIST_WORKER_COUNT")}
    environ.update(TMPDIR=str(tmp_path / "t"), KRIYA_SAMPLE_SESSION_BASE_OUT=str(record), KRIYA_CERTIFICATION="")
    done = subprocess.run([sys.executable, "-m", "pytest", str(SAMPLE), "-p", "no:cacheprovider", *args], cwd=str(ROOT), capture_output=True, text=True,
                          timeout=300, env=environ)
    assert done.returncode == 0 and "6 passed" in done.stdout, done.stdout[-1500:] + done.stderr[-500:]
    rows = [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]
    record.unlink()
    return rows, done.stdout


def test_consecutive_invocations_get_different_base_pathnames_although_each_base_was_deleted(tmp_path):
    (tmp_path / "t").mkdir()
    record = tmp_path / "bases.jsonl"
    bases = []
    for _ in range(3):
        rows, stdout = _invoke(tmp_path, record)
        (base,) = {row["base"] for row in rows}
        assert UNIQUE.search(base) and os.path.realpath(base).startswith(os.path.realpath(str(tmp_path / "t")) + os.sep), base
        assert f"kriya session base: {base}" in stdout                                  # the evidence line of a non-quiet run
        assert not os.path.exists(base), "the passing invocation's base is gone before the next one is allocated"
        assert not list((tmp_path / "t").glob("kriya-pytest-*")), "a passing invocation leaves no unique root behind"
        assert {row["inherited_temproot"] for row in rows} == {None}, "the temp-root knob must not leak into what a test starts"
        bases.append(base)
    assert len(set(bases)) == 3, bases
    # the old shape for comparison: the numbered leaf alone is the same every time - the uniqueness is in the root
    assert {os.path.basename(base) for base in bases} == {"pytest-0"}
    assert len({base.split("/kriya-pytest-")[-1].split("/")[0] for base in bases}) == 3   # [-1]: this test's own tmp_path is under a unique root too


def test_every_xdist_worker_is_rooted_beneath_the_one_unique_session_base(tmp_path):
    (tmp_path / "t").mkdir()
    rows, _stdout = _invoke(tmp_path, tmp_path / "bases.jsonl", "-n", "3", "--dist", "loadgroup")
    workers = {row["worker"]: row["base"] for row in rows}
    assert len(workers) >= 2 and "main" not in workers, workers
    controller_bases = {os.path.dirname(base) for base in workers.values()}
    assert len(controller_bases) == 1, workers                                           # one session base ...
    (controller,) = controller_bases
    assert UNIQUE.search(controller) and {os.path.basename(base) for base in workers.values()} == {f"popen-{name}" for name in workers}
    assert all(row["tmp_path"].startswith(row["base"] + os.sep) for row in rows)          # ... and every test directory under its worker's
    assert not list((tmp_path / "t").glob("kriya-pytest-*"))


def test_this_sessions_own_base_is_under_a_unique_root(tmp_path_factory, request):
    base = str(tmp_path_factory.getbasetemp())
    if request.config.option.basetemp is not None and not hasattr(request.config, "workerinput"):
        pytest.skip("an explicit --basetemp is the caller's own pathname")
    assert UNIQUE.search(base), base


def test_the_root_name_is_not_derived_from_what_exists(tmp_path, monkeypatch):
    seen = []
    for _ in range(25):
        root = conftest._allocate_session_root(str(tmp_path))
        assert os.path.dirname(root) == str(tmp_path) and os.path.basename(root).startswith(conftest.SESSION_ROOT_PREFIX) and os.listdir(root) == []
        seen.append(root)
        os.rmdir(root)                                               # deleted: nothing of it is left to count from
        assert os.listdir(tmp_path) == []
    assert len(set(seen)) == 25
    assert not any(re.fullmatch(r"\d+", os.path.basename(root)[len(conftest.SESSION_ROOT_PREFIX):]) for root in seen)   # no counter
    calls = []
    real = conftest.tempfile.mkdtemp
    monkeypatch.setattr(conftest.tempfile, "mkdtemp", lambda **kwargs: calls.append(kwargs) or real(**kwargs))
    conftest._allocate_session_root(str(tmp_path))
    assert calls == [{"prefix": conftest.SESSION_ROOT_PREFIX, "dir": str(tmp_path)}]      # the standard uniqueness facility, nothing home-made


def test_a_kept_base_keeps_its_root_and_an_empty_one_is_released(tmp_path):
    kept = Path(conftest._allocate_session_root(str(tmp_path)))
    (kept / "pytest-of-someone" / "pytest-0" / "test_failed0").mkdir(parents=True)
    conftest._release_session_root(str(kept))
    assert kept.is_dir()                                             # a failed test's directories stay for diagnosis
    empty = Path(conftest._allocate_session_root(str(tmp_path)))
    (empty / "pytest-of-someone").mkdir()                            # what pytest leaves after removing a passing session's base
    conftest._release_session_root(str(empty))
    assert not empty.exists()
    conftest._release_session_root(None)                             # an invocation that allocated none


def test_only_old_roots_of_this_harness_are_swept(tmp_path):
    now = time.time()
    old, fresh, foreign = tmp_path / "kriya-pytest-old", tmp_path / "kriya-pytest-fresh", tmp_path / "someone-elses-old"
    for path in (old, fresh, foreign):
        (path / "pytest-of-x" / "pytest-0").mkdir(parents=True)
    (tmp_path / "kriya-pytest-file").write_text("not a directory")
    for path in (old, foreign, tmp_path / "kriya-pytest-file"):
        os.utime(path, (now - 10 * 24 * 3600, now - 10 * 24 * 3600))
    assert conftest._sweep_stale_session_roots(str(tmp_path), now) == [str(old)]
    assert not old.exists() and fresh.is_dir() and foreign.is_dir() and (tmp_path / "kriya-pytest-file").is_file()
    assert conftest._sweep_stale_session_roots(str(tmp_path / "missing"), now) == []


def test_no_production_module_knows_the_harness_temp_root():
    """The mechanism is pytest's own temp-root knob set by tests/conftest.py; Kriya's runtime reads neither it nor the root's name."""
    for root in ("kriya", "plugins"):
        for path in (ROOT / root).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "PYTEST_DEBUG_TEMPROOT" not in text and conftest.SESSION_ROOT_PREFIX not in text, path
