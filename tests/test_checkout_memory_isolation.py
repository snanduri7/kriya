"""OI-45 permanent control: the files that open the relative default ``./memory`` never reach the checkout's.

CLAIM (E14, 030b75c + ea4118e): the 19 test files MEASURED to open/create ``./memory/dependency_graph.db`` and ``knowledge_cache.db``
through AppConfig-default configs run each test from a fresh working directory holding an EMPTY ``./memory``
(``tests/conftest.py::_checkout_memory_isolated``). The claim was proven once by an out-of-tree sandbox audit and the per-test
tripwire was removed as an own bug, so nothing in the repository failed if the fixture were dropped. Here:

- the set of files is pinned (changing it is a conscious edit, like the other tier lists; OI-71 is the 49 files NOT yet in it);
- the real fixture function is driven for every pinned name and for an unlisted one: listed -> chdir into a fresh cwd with an empty
  ``./memory``; unlisted -> untouched;
- a sentinel inside a listed file (tests/test_generation_service.py) proves the fixture is actually autouse-wired for that file."""
from pathlib import Path
from types import SimpleNamespace

import conftest
import pytest

PINNED = frozenset({
    "test_sm_reserved_fallback.py", "test_sm_attempt_metrics.py", "test_sm_best_of_n.py", "test_sm_failure_families.py",
    "test_sm_budget_transitions.py", "test_prd032_chaos_static_analysis.py", "test_prd032_chaos_runtime.py",
    "test_prd032_chaos_commit.py", "test_prd032_terminal_commit_stop.py", "test_prd008_resume_fingerprints.py",
    "test_prd008a_resume_convergence.py", "test_candidate_verification_binding.py", "test_workflow_recovery_handback.py",
    "test_provider_contract_001a.py", "test_failure_signature_run_noise.py", "test_prd026_retry_progress.py",
    "test_prd017_fallback_transition.py", "test_generation_service.py", "test_repair_orchestrator.py",
})
TESTS = Path(__file__).resolve().parent


def test_the_isolated_file_set_is_the_audited_one():
    assert conftest.CHECKOUT_MEMORY_RESOLVING_FILES == PINNED


@pytest.mark.parametrize("name", sorted(PINNED))
def test_every_listed_file_exists_exactly_once_so_the_name_key_cannot_go_stale(name):
    assert len([path for path in TESTS.rglob(name) if "__pycache__" not in path.parts]) == 1


def _drive(name, tmp_path_factory, monkeypatch):
    """Run the fixture's own body for a test file called ``name`` and return the working directory it leaves."""
    body = conftest._checkout_memory_isolated.__wrapped__                       # pylint: disable=protected-access
    generator = body(SimpleNamespace(path=Path(name)), tmp_path_factory, monkeypatch)
    next(generator)
    return Path.cwd().resolve()


@pytest.mark.parametrize("name", sorted(PINNED))
def test_a_listed_file_gets_a_fresh_cwd_with_an_empty_memory(name, tmp_path_factory, monkeypatch):
    before = Path.cwd().resolve()
    cwd = _drive(name, tmp_path_factory, monkeypatch)
    assert cwd != before and (cwd / "memory").is_dir() and not any((cwd / "memory").iterdir())


def test_an_unlisted_file_keeps_its_working_directory(tmp_path_factory, monkeypatch):
    before = Path.cwd().resolve()
    assert _drive("test_something_else.py", tmp_path_factory, monkeypatch) == before


def test_the_fixture_is_autouse():
    """The property the sentinel in test_generation_service.py exercises end to end, stated at the declaration as well."""
    assert conftest._checkout_memory_isolated._fixture_function_marker.autouse is True      # pylint: disable=protected-access

