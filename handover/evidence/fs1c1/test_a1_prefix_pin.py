"""FS-1C1 PRE-FIX PIN (evidence; run on the pre-fix revision 7e4de32:
PYTHONPATH=$PWD:$PWD/tests pytest -p conftest handover/evidence/fs1c1/test_a1_prefix_pin.py).
The live A1 false success, replayed: C0 closes the whole compound requirement."""
from _fs1c1_a1_specimen import outcomes, production_blocking, terminal_requirements

from kriya.workflow.requirements import RequirementOutcome


def test_a1_today_c0_closes_the_whole_compound_requirement_and_the_run_may_succeed():
    reqs, ledger, _ = terminal_requirements()
    assert outcomes(reqs, ledger) == {"REQ-1": RequirementOutcome.CLOSED_BY_EVIDENCE,
                                      "REQ-2": RequirementOutcome.CLOSED_BY_EVIDENCE}
    assert production_blocking(reqs, ledger) == []        # nothing blocks: SUCCESS, COMMIT, APPLY
