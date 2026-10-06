"""FS-1C1 required behaviour on the A1 specimen (evidence; fails on 7e4de32, passes after FS-1C1:
PYTHONPATH=$PWD:$PWD/tests pytest -p conftest handover/evidence/fs1c1/test_a1_required.py)."""
from _fs1c1_a1_specimen import outcomes, production_blocking, terminal_requirements

from kriya.workflow.requirements import RequirementOutcome


def test_a1_c0_closes_only_regression_preservation_and_the_run_cannot_succeed():
    reqs, ledger, _ = terminal_requirements()
    assert outcomes(reqs, ledger) == {"REQ-1": RequirementOutcome.UNVERIFIED,
                                      "REQ-2": RequirementOutcome.CLOSED_BY_EVIDENCE}
    assert production_blocking(reqs, ledger) == ["REQ-1"]  # no SUCCESS, no COMMIT, no APPLY
