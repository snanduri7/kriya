"""FS-1C0 pre-fix pins (evidence; run explicitly on the pre-fix revision:
PYTHONPATH=$PWD:$PWD/tests pytest -p conftest handover/evidence/fs1c0/test_c0_prefix_pins.py).
Every forging scenario closes the requirement today (a false success); the
two controls behave. Superseded by tests/test_fs1c0_named_test_oracle.py."""
import pytest
from _named_test_oracle_harness import FORGING, scenario

from kriya.workflow.requirements import RequirementOutcome


@pytest.mark.parametrize("name", FORGING)
def test_a_wrong_candidate_closes_the_requirement_today(tmp_path, name):
    [attempt], outcome, _ = scenario(tmp_path, name)
    assert attempt["closed"] is True and outcome is RequirementOutcome.CLOSED_BY_EVIDENCE


def test_controls(tmp_path):
    [attempt], outcome, _ = scenario(tmp_path / "c", "correct")
    assert attempt["closed"] is True and outcome is RequirementOutcome.CLOSED_BY_EVIDENCE
    [attempt], outcome, _ = scenario(tmp_path / "w", "wrong")
    assert attempt["closed"] is False and outcome is RequirementOutcome.UNVERIFIED
