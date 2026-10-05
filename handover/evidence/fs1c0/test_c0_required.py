"""FS-1C0 required behaviour, as it stood before the fix (evidence; run
explicitly: PYTHONPATH=$PWD:$PWD/tests pytest -p conftest handover/evidence/fs1c0/test_c0_required.py).
Fails on the pre-fix revision; the suite copy is tests/test_fs1c0_named_test_oracle.py."""
import pytest
from _named_test_oracle_harness import FORGING, scenario

from kriya.workflow.requirements import RequirementOutcome


@pytest.mark.parametrize("name", FORGING)
def test_a_wrong_candidate_never_closes_the_requirement(tmp_path, name):
    [attempt], outcome, _ = scenario(tmp_path, name)
    assert attempt["closed"] is False and outcome is RequirementOutcome.UNVERIFIED


def test_controls(tmp_path):
    [attempt], outcome, _ = scenario(tmp_path / "c", "correct")
    assert attempt["closed"] is True and outcome is RequirementOutcome.CLOSED_BY_EVIDENCE
    [attempt], outcome, _ = scenario(tmp_path / "w", "wrong")
    assert attempt["closed"] is False and outcome is RequirementOutcome.UNVERIFIED
