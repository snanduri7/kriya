"""TEST-HARNESS-STATE-PATHNAME-REUSE-001: a per-test directory's pathname is never handed out twice in one pytest session.

MEASURED 2026-11-11 (real Docker, serial): a state tree deleted right after a contained gate and recreated under the SAME pathname is
refused by the container backend as a bind source (29 of 30; alternating blocks 9 of 10 and 8 of 10), the same pathname deleted only
before its next use is not (0 of 20), and a never-reused pathname is not (0 of 50). The harness produced that shape itself:
``tests/conftest.py::_per_test_dir`` used ``tmp_path_factory.mktemp(name)`` ("highest existing number + 1") and released the directory
of a passing test at teardown, so the released NUMBER - the exact pathname - was handed to a later test (MEASURED: 30 distinct state
directory names for 90 tests).

Invariant: once a test-state pathname may have been exposed to a container bind, that exact pathname is not used again in the session.
"""
import shutil

import conftest
import pytest


@pytest.fixture
def serials(monkeypatch):
    """The allocator's session counters, isolated for a control that uses its own base directory."""
    monkeypatch.setattr(conftest, "_PER_TEST_DIR_SERIALS", {})


def test_a_released_pathname_is_never_handed_out_again(tmp_path, serials):
    seen = []
    for _ in range(40):
        path = conftest._session_unique_dir(tmp_path, "kriya-state")
        assert path.is_dir() and path.parent == tmp_path
        seen.append(path)
        shutil.rmtree(path)                                        # released: nothing with a number is left in the base directory
        assert not any(tmp_path.iterdir())
    assert len(set(seen)) == 40
    assert [path.name for path in seen] == [f"kriya-state{n}" for n in range(40)]     # the name form is unchanged; the number only grows


def test_pytests_own_numbering_does_reuse_a_released_pathname(tmp_path_factory):
    """Validity control: the old allocation really produced the reuse (so the control above is not vacuous)."""
    first = tmp_path_factory.mktemp("reuse-validity")
    shutil.rmtree(first)
    second = tmp_path_factory.mktemp("reuse-validity")
    shutil.rmtree(second)
    assert second == first


def test_each_prefix_counts_on_its_own_and_an_existing_directory_is_skipped_not_shared(tmp_path, serials):
    assert conftest._session_unique_dir(tmp_path, "kriya-state").name == "kriya-state0"
    assert conftest._session_unique_dir(tmp_path, "kriya-authority").name == "kriya-authority0"
    (tmp_path / "kriya-state1").mkdir()                             # someone else's directory (a kept failed test's, a direct mktemp)
    (tmp_path / "kriya-state1" / "theirs").write_text("x")
    allocated = conftest._session_unique_dir(tmp_path, "kriya-state")
    assert allocated.name == "kriya-state2" and not any(allocated.iterdir())
    shutil.rmtree(tmp_path / "kriya-state1")
    assert conftest._session_unique_dir(tmp_path, "kriya-state").name == "kriya-state3"      # ... and its freed name is not picked up either


def test_the_fixture_path_releases_on_pass_keeps_on_failure_and_never_reuses(tmp_path, serials):
    """Through ``_per_test_dir`` itself, with the finalizer run as pytest would."""
    class _Factory:
        def getbasetemp(self):
            return tmp_path

    class _Request:
        def __init__(self, failed):
            self.node, self.finalizers = type("Node", (), {"stash": {conftest._TEST_FAILED: failed}})(), []

        def addfinalizer(self, function):
            self.finalizers.append(function)

    paths = []
    for failed in (False, False, True, False):
        request = _Request(failed)
        path = conftest._per_test_dir(request, _Factory(), "kriya-state")
        paths.append(path)
        (path / "state.json").write_text("{}")
        for finalizer in request.finalizers:
            finalizer()
        assert path.exists() is failed                              # a passed test leaves nothing, a failed one keeps its directory
    assert len(set(paths)) == 4 and [p.name for p in paths] == ["kriya-state0", "kriya-state1", "kriya-state2", "kriya-state3"]


def test_this_sessions_real_state_directories_are_distinct_pathnames(scratch_dir, request):
    """The real fixtures of this very session: every allocation is a new pathname, also right after a release."""
    import os

    from kriya.core.state_paths import ENV_STATE_DIR

    first = scratch_dir("kriya-state")
    shutil.rmtree(first)
    second = scratch_dir("kriya-state")
    shutil.rmtree(second)
    third = scratch_dir("kriya-state")
    mine = os.environ[ENV_STATE_DIR]
    assert len({str(first), str(second), str(third), mine}) == 4
    assert os.path.dirname(mine) == str(first.parent) and os.path.basename(mine).startswith("kriya-state")


def test_every_per_test_isolation_directory_comes_from_the_one_allocator():
    """No fixture hands out a numbered per-test directory through ``tmp_path_factory.mktemp`` again (the callers allowlist of
    tests/test_cert_tmp_growth_001.py pins the session / module scoped ones)."""
    import ast
    from pathlib import Path

    tree = ast.parse(Path(conftest.__file__).read_text(encoding="utf-8"))
    allocator = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_per_test_dir")
    calls = [ast.unparse(node.func) for node in ast.walk(allocator) if isinstance(node, ast.Call)]
    assert "_session_unique_dir" in calls and "tmp_path_factory.mktemp" not in calls, calls
    users = sorted({ast.unparse(node.args[2]) for node in ast.walk(tree) if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_per_test_dir"})
    assert users == ["'kriya-authority'", "'kriya-certifications'", "'kriya-qualifications'", "'kriya-state'", "name"], users
