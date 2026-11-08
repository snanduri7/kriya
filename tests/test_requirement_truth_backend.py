"""REQUIREMENT TRUTH (owner sign-off 2026-11-08, section 9): the typed FINAL requirement status is derived by the backend from ``requirement_outcomes`` alone, travels on the launch record's ``ended`` bounded and redacted, and
is never recomputed by the desktop. ALL_VERIFIED / OPEN_REQUIREMENTS / CONTRADICTED; NOT_A_CLAIM and DISPOSITIONED statements are not mandatory requirements and are left out of the counts."""
import pytest

from kriya.control.launch import _requirement_truth_field, _runtime_observation_field
from kriya.workflow.requirements import (
    REQUIREMENT_TRUTH_ALL_VERIFIED,
    REQUIREMENT_TRUTH_CONTRADICTED,
    REQUIREMENT_TRUTH_MAX_UNRESOLVED,
    REQUIREMENT_TRUTH_OPEN,
    derive_requirements,
    requirement_truth,
)
from kriya.workflow.requirements import (
    RequirementOutcome as O,
)


def _set(n):
    return derive_requirements("\n".join(f"Requirement number {i} must hold." for i in range(1, n + 1)) + "\n")


def _truth(*outcomes):
    reqs = _set(len(outcomes))
    return requirement_truth({r.id: o for r, o in zip(reqs.requirements, outcomes, strict=True)}, reqs)


@pytest.mark.parametrize("outcomes,status,verified,total", [
    ((O.CLOSED_BY_EVIDENCE, O.CLOSED_BY_EVIDENCE), REQUIREMENT_TRUTH_ALL_VERIFIED, 2, 2),
    ((O.CLOSED_BY_EVIDENCE, O.HUMAN_ACCEPTED), REQUIREMENT_TRUTH_ALL_VERIFIED, 2, 2),
    ((O.CLOSED_BY_EVIDENCE, O.UNVERIFIED), REQUIREMENT_TRUTH_OPEN, 1, 2),
    ((O.UNKNOWN, O.PENDING), REQUIREMENT_TRUTH_OPEN, 0, 2),
    ((O.CLOSED_BY_EVIDENCE, O.VIOLATED, O.UNVERIFIED), REQUIREMENT_TRUTH_CONTRADICTED, 1, 3),
    ((O.CLOSED_BY_EVIDENCE, O.NOT_A_CLAIM, O.DISPOSITIONED), REQUIREMENT_TRUTH_ALL_VERIFIED, 1, 1),
    ((O.NOT_A_CLAIM,), REQUIREMENT_TRUTH_OPEN, 0, 0),             # nothing mandatory: never an affirmative "all verified"
])
def test_status_is_derived_from_the_outcomes_alone(outcomes, status, verified, total):
    truth = _truth(*outcomes)
    assert (truth["status"], truth["verified"], truth["total"]) == (status, verified, total)


def test_unresolved_lists_the_open_requirements_in_order_with_their_outcome_and_is_bounded():
    truth = _truth(O.CLOSED_BY_EVIDENCE, O.UNVERIFIED, O.VIOLATED)
    assert [(u["id"], u["outcome"]) for u in truth["unresolved"]] == [("REQ-2", "unverified"), ("REQ-3", "violated")]
    assert len(_truth(*([O.UNVERIFIED] * 20))["unresolved"]) == REQUIREMENT_TRUTH_MAX_UNRESOLVED


def test_a_requirement_missing_from_the_outcomes_is_open_never_verified():
    reqs = _set(2)
    truth = requirement_truth({"REQ-1": O.CLOSED_BY_EVIDENCE}, reqs)
    assert truth["status"] == REQUIREMENT_TRUTH_OPEN and truth["verified"] == 1


def test_the_launch_record_copies_the_truth_bounded_and_redacted():
    token = "ghp_" + "a" * 36
    result = {"requirement_truth": {"status": "OPEN_REQUIREMENTS", "verified": 1, "total": 3,
                                    "unresolved": [{"id": "REQ-2", "text": f"print {token} " + "x" * 500, "outcome": "unverified"}] + [
                                        {"id": f"REQ-{i}", "text": "t", "outcome": "unverified"} for i in range(3, 30)]}}
    field = _requirement_truth_field(result)["requirement_truth"]
    assert field["status"] == "OPEN_REQUIREMENTS" and (field["verified"], field["total"]) == (1, 3)
    assert token not in field["unresolved"][0]["text"] and len(field["unresolved"][0]["text"]) <= 240 and len(field["unresolved"]) == 8


@pytest.mark.parametrize("bad", [None, {}, {"status": "GREEN", "verified": 1, "total": 1}, {"status": "ALL_VERIFIED", "verified": "1", "total": 1},
                                 {"status": "ALL_VERIFIED", "verified": True, "total": 1}, "ALL_VERIFIED"])
def test_a_malformed_or_absent_truth_is_not_copied(bad):
    assert _requirement_truth_field({"requirement_truth": bad}) == {}
    assert _requirement_truth_field({}) == {}


def test_the_launch_record_copies_the_observed_output_redacted_and_capped():
    token = "ghp_" + "b" * 36
    field = _runtime_observation_field({"runtime_observation": {"command": "python3 hello.py", "exit_status": 0, "stdout": f"{token}\n" + "y" * 3000,
                                                               "truncated": False, "stdout_lost": True, "attempt": 1}})["runtime_observation"]
    assert token not in field["stdout"] and len(field["stdout"]) <= 2000 and field["truncated"] is True and field["stdout_lost"] is True
    assert field["command"] == "python3 hello.py" and field["exit_status"] == 0
    assert _runtime_observation_field({"runtime_observation": {"command": "x"}}) == {} and _runtime_observation_field({}) == {}
