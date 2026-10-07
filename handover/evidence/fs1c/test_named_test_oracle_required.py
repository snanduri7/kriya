"""FS-1C required behaviour (evidence; run explicitly:
PYTHONPATH=$PWD:$PWD/tests pytest -p conftest handover/evidence/fs1c/test_named_test_oracle_required.py).
Fails until the named-test closure guards its oracle's dependencies."""
from test_fs1c_named_test_oracle_dependency import close_with_candidate

from kriya.workflow.requirements import RequirementOutcome


def test_a_candidate_changed_oracle_dependency_never_closes_a_requirement(tmp_path):
    attempt, outcome = close_with_candidate(tmp_path, conftest=True)
    assert attempt["closed"] is False and outcome is RequirementOutcome.UNVERIFIED
