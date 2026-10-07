"""FS-1C finding (Track A): the named-test closure's oracle depends on files
it does not guard. A requirement naming a pre-existing test closes when that
test file is unchanged, executed and passing - but the candidate can change
what the test means without touching it: a candidate conftest.py hook turns
its failure into a pass (measured: report AND exit code both "passed").

PRE-FIX PIN (moved from tests/ by FS-1C0; runs only on the pre-fix revision, which still has the
run_tests/confirms_execution API). This pins the behaviour before C0 (the closure happens). The required
behaviour is asserted by handover/evidence/fs1c/test_named_test_oracle_required.py
(failed before C0; passes after it through tests/test_fs1c_named_test_oracle_dependency.py)."""
import sys

from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.acceptance import output_confirms_nonzero_test_execution
from kriya.workflow.obligations import ObligationLedger
from kriya.workflow.requirements import (
    RequirementOutcome,
    close_unverified_requirements_with_named_tests,
    derive_requirements,
    record_requirement_verdicts,
    requirement_outcomes,
    seed_requirement_obligations,
)

GOAL = "Behaviour stays compatible with the legacy check test_legacy\n"
LEGACY = "from app import value\n\n\ndef test_legacy():\n    assert value() == 1\n"
FLIP_HOOK = ("import pytest\n\n\n@pytest.hookimpl(hookwrapper=True)\n"
             "def pytest_runtest_makereport(item, call):\n    outcome = yield\n"
             "    outcome.get_result().outcome = \"passed\"\n")


def close_with_candidate(tmp_path, conftest):
    """The candidate breaks app.value() and (optionally) adds a conftest.py;
    the named test file itself is unchanged."""
    (tmp_path / "requirements.txt").write_text("")
    (tmp_path / "app.py").write_text("def value():\n    return 2\n")       # candidate: wrong
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "__init__.py").write_text("")
    (tmp_path / "tests" / "test_legacy.py").write_text(LEGACY)               # pre-existing, unchanged
    modified = ["app.py"]
    if conftest:
        (tmp_path / "tests" / "conftest.py").write_text(FLIP_HOOK)
        modified.append("tests/conftest.py")
    reqs = derive_requirements(GOAL)
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    record_requirement_verdicts(ledger, reqs, {"REQ-1": (RequirementOutcome.SATISFIED, "")}, revision=1,
                                evidence_fingerprint="cand-1", source="test")
    validator = PolymorphicValidator(str(tmp_path))
    validator._resolve_python_interpreter = lambda: (sys.executable, None)
    [attempt] = close_unverified_requirements_with_named_tests(
        ledger, reqs, test_files=["tests/test_legacy.py"], modified=modified,
        run_tests=lambda paths: validator.run_tests(target_test=list(paths)),
        confirms_execution=output_confirms_nonzero_test_execution, source="test", revision=1)
    return attempt, requirement_outcomes(ledger, reqs)["REQ-1"]


def test_control_the_unchanged_named_test_catches_the_wrong_candidate(tmp_path):
    attempt, outcome = close_with_candidate(tmp_path, conftest=False)
    assert attempt["closed"] is False and outcome is RequirementOutcome.UNVERIFIED


def test_a_candidate_conftest_hook_closes_the_requirement_today(tmp_path):
    """Pins the defect path: the named test file is unchanged, the candidate
    is wrong, a candidate conftest hook makes the run report a pass."""
    attempt, outcome = close_with_candidate(tmp_path, conftest=True)
    assert attempt["closed"] is True and outcome is RequirementOutcome.CLOSED_BY_EVIDENCE
