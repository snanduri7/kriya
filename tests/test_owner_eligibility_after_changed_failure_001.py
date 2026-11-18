"""OWNER-ELIGIBILITY-AFTER-CHANGED-FAILURE-001 (Stage B run 8, 20261118T161538-f3d8b246).

MEASURED: s5 (App.java) repaired the missing `igniteNode` bean; the s7 rerun then failed on `equals=false`; s5's stage state was
needs_review, so the verification-owner reopen skipped it and tried s3, s4 and s2 (all byte-identical) until the time budget ran out.

These check the two pure decisions the loop now reads (who stays eligible, and what the resolver is offered) against the run 8 plan and the
real resolver. They do NOT run the enforce loop: its wiring of these two calls is not exercised here.
"""
import pytest

from kriya.workflow.enforce_loop import changed_failure_reopen_owners, verification_owner_reopen_ids
from kriya.workflow.plan_revision import nearest_mutating_upstream_owner
from kriya.workflow.plan_schema import EngineeringPlan
from kriya.workflow.verifier_replay import failure_identity

MISSING_BEAN = failure_identity("run_verification", "[VERIFICATION] FAIL: Exception occurred - No bean named 'igniteNode' available")
NOT_EQUAL = failure_identity("run_verification", "[RESULT] ... equals=false\n[VERIFICATION] FAIL: Protocols are not equal after round-trip")


def _plan() -> EngineeringPlan:
    """The run 8 dependency shape: s7 <- s5 <- {s3, s4}; s3 <- s2 <- s1; s4 <- s1; s6 <- s3. s7 and s8 are verification-only."""
    def unit(unit_id, depends_on, path=None):
        return {"id": unit_id, "description": unit_id, "execution_method": "model", "depends_on": depends_on,
                "planned_files": [{"path": path, "action": "create"}] if path else [],
                "relevant_global_invariant_ids": ["gi1"], "verification": [{"type": "tool", "tool_name": "test", "description": "t"}]}

    return EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}], "acceptance_criteria": [],
        "subtasks": [unit("s1", [], "pom.xml"), unit("s2", ["s1"], "Protocol.java"), unit("s3", ["s2"], "ProtocolParser.java"),
                     unit("s4", ["s1"], "ignite-config.xml"), unit("s5", ["s3", "s4"], "App.java"),
                     unit("s6", ["s3"], "ProtocolParserTest.java"), unit("s7", ["s5"]), unit("s8", ["s6"])]})


def _order(plan, unit_id, eligible):
    """The owners the reopen walk yields, in order (each considered once, as the loop does)."""
    unit, considered, order = plan.subtask_by_id(unit_id), [], []
    while True:
        owner = nearest_mutating_upstream_owner(plan, unit, [stage for stage in eligible if stage not in considered])
        if owner is None:
            return order
        considered.append(owner.id)
        order.append(owner.id)


RERAN = {"owner_id": "s5", "owner_reran": True}
STATES = {"s1": "completed", "s2": "completed", "s3": "completed", "s4": "completed", "s5": "needs_review", "s6": "completed"}


def test_run8_the_owner_whose_repair_changed_the_failure_is_first_in_the_reopen_order_and_stays_needs_review():
    plan = _plan()
    before = _order(plan, "s7", verification_owner_reopen_ids(STATES, (), "completed"))
    assert before == ["s3", "s4", "s2", "s1"], "the measured run 8 order: s5 skipped"
    owners = changed_failure_reopen_owners([RERAN], MISSING_BEAN, NOT_EQUAL)
    assert owners == ["s5"]
    eligible = verification_owner_reopen_ids(STATES, owners, "completed")
    assert _order(plan, "s7", eligible) == ["s5", "s3", "s4", "s2", "s1"], "the resolver's own order, with s5 first"
    assert STATES["s5"] == "needs_review", "eligibility only: the stage state is not touched"


@pytest.mark.parametrize("previous, current", [
    (MISSING_BEAN, MISSING_BEAN),       # the same deterministic failure before and after the repair
    (None, NOT_EQUAL), (MISSING_BEAN, None), (None, None),   # either identity unknown
])
def test_the_same_failure_or_an_unknown_identity_keeps_the_owner_excluded(previous, current):
    assert changed_failure_reopen_owners([RERAN], previous, current) == []
    eligible = verification_owner_reopen_ids(STATES, changed_failure_reopen_owners([RERAN], previous, current), "completed")
    assert _order(_plan(), "s7", eligible) == ["s3", "s4", "s2", "s1"]


def test_only_an_owner_that_actually_reran_qualifies():
    records = [{"owner_id": "s5", "owner_reran": False}, {"owner_id": "s4", "owner_reran": True}, {"owner_id": "s3"}]
    assert changed_failure_reopen_owners(records, MISSING_BEAN, NOT_EQUAL) == ["s4"]
    assert changed_failure_reopen_owners([], MISSING_BEAN, NOT_EQUAL) == []


def test_an_unrelated_needs_review_owner_stays_ineligible():
    states = {**STATES, "s3": "needs_review"}            # s3 is needs_review for some other reason; only s5 qualified
    eligible = verification_owner_reopen_ids(states, ["s5"], "completed")
    assert "s3" not in eligible and _order(_plan(), "s7", eligible) == ["s5", "s4", "s2", "s1"]


def test_the_set_offered_to_one_verification_unit_says_nothing_about_another():
    """s5 qualified for s7. s8 (<- s6 <- s3) does not have s5 among its ancestors: offered the same ids, the resolver never yields it there;
    and without the qualification (what the loop offers s8: its own, empty set) the ids are the completed stages only."""
    plan = _plan()
    with_s5 = verification_owner_reopen_ids(STATES, ["s5"], "completed")
    assert _order(plan, "s8", with_s5) == ["s6", "s3", "s2", "s1"]
    assert verification_owner_reopen_ids(STATES, (), "completed") == ["s1", "s2", "s3", "s4", "s6"]


def test_the_order_among_otherwise_eligible_owners_is_the_resolvers_own():
    plan = _plan()
    everything_completed = {**STATES, "s5": "completed"}
    assert _order(plan, "s7", verification_owner_reopen_ids(everything_completed, (), "completed")) == ["s5", "s3", "s4", "s2", "s1"]
    assert _order(plan, "s7", verification_owner_reopen_ids(STATES, ["s5"], "completed")) == ["s5", "s3", "s4", "s2", "s1"]
    assert verification_owner_reopen_ids(everything_completed, ["s5"], "completed").count("s5") == 1
