"""GR-R0 NEGATIVE-MODEL-AUTHORITY: LLM output can authorize nothing, in either
direction. A verifier's "missing" verdict is a MODEL_CLAIMED assessment: it may
inform diagnostics and keeps a run from succeeding without stronger evidence,
but it never outranks trusted deterministic or human-bound evidence and never
establishes VIOLATED on its own.

Measured before the fix (this file, no model: the verdict is injected through
the B2-a ledger seam): an exact requirement whose B2 acceptance passed, and a
GENERAL one HUMAN_ACCEPTED through B3, both stayed VIOLATED because the
recorded model verdict was "missing" - requirement_outcomes applied trusted
closure only to an UNVERIFIED/SATISFIED verdict."""
import dataclasses

import pytest
from _b2a_fixtures import CALC_ACCEPTANCE, CALC_GOAL
from test_b2a_acceptance_oracle import _ledger
from test_b3_human_acceptance import GENERAL_GOAL, _acceptance, _approval, _close, _repo

from kriya.workflow.obligations import ObligationStatus
from kriya.workflow.requirements import (
    VERIFIER_REPORTED_MISSING,
    RequirementOutcome,
    blocking_requirements,
    record_requirement_closure,
    requirement_obligation_id,
    requirement_outcomes,
    requirement_verdict_details,
)

PRODUCTION = {"unknown_policy": "block", "unverified_policy": "block"}
PERMISSIVE = {"unknown_policy": "record", "unverified_policy": "record"}
SATISFIED, MISSING = RequirementOutcome.SATISFIED, RequirementOutcome.VIOLATED  # the model's two verdicts


def _outcome(ledger, reqs):
    return requirement_outcomes(ledger, reqs)["REQ-1"]


def _blocked(ledger, reqs, policy):
    return [(r.id, o) for r, o in blocking_requirements(ledger, reqs, **policy)]


def test_exact_b2_pass_outranks_a_model_negative(tmp_path):
    root, _ = _repo(tmp_path)
    reqs, ledger = _ledger(CALC_GOAL, verdict=MISSING)
    _close(ledger, reqs, _acceptance(tmp_path, goal=CALC_GOAL, source=CALC_ACCEPTANCE), root)
    assert _outcome(ledger, reqs) is RequirementOutcome.CLOSED_BY_EVIDENCE
    assert not _blocked(ledger, reqs, PRODUCTION)


def test_general_b3_human_acceptance_outranks_a_model_negative(tmp_path):
    root, base = _repo(tmp_path)
    acceptance = _acceptance(tmp_path)
    approval = _approval(tmp_path, GENERAL_GOAL, acceptance, base, root)
    reqs, ledger = _ledger(GENERAL_GOAL, verdict=MISSING)
    _close(ledger, reqs, acceptance, root, approval=approval, base=base)
    assert _outcome(ledger, reqs) is RequirementOutcome.HUMAN_ACCEPTED  # never CLOSED_BY_EVIDENCE
    assert not _blocked(ledger, reqs, PRODUCTION)


@pytest.mark.parametrize("approved", [False, True])
def test_a_failing_trusted_case_is_violated_whatever_the_model_says(tmp_path, approved):
    root, base = _repo(tmp_path, correct=False)
    acceptance = _acceptance(tmp_path)
    approval = _approval(tmp_path, GENERAL_GOAL, acceptance, base, root) if approved else None
    reqs, ledger = _ledger(GENERAL_GOAL, verdict=SATISFIED)
    _close(ledger, reqs, acceptance, root, approval=approval, base=base)
    assert _outcome(ledger, reqs) is RequirementOutcome.VIOLATED
    assert _blocked(ledger, reqs, PERMISSIVE) == [("REQ-1", RequirementOutcome.VIOLATED)]


@pytest.mark.parametrize("verdict", [SATISFIED, MISSING])
def test_without_trusted_evidence_either_model_verdict_is_unverified(verdict):
    reqs, ledger = _ledger(GENERAL_GOAL, verdict=verdict)
    assert _outcome(ledger, reqs) is RequirementOutcome.UNVERIFIED
    assert _blocked(ledger, reqs, PRODUCTION) == [("REQ-1", RequirementOutcome.UNVERIFIED)]


def test_a_model_negative_alone_still_keeps_success_closed_under_a_permissive_policy():
    """Fail closed: "missing" without stronger evidence is not VIOLATED, and it
    is not success either - it blocks whatever the unverified policy."""
    reqs, ledger = _ledger(GENERAL_GOAL, verdict=MISSING)
    assert _blocked(ledger, reqs, PERMISSIVE) == [("REQ-1", RequirementOutcome.UNVERIFIED)]
    satisfied_reqs, satisfied_ledger = _ledger(GENERAL_GOAL, verdict=SATISFIED)
    assert not _blocked(satisfied_ledger, satisfied_reqs, PERMISSIVE)  # unchanged: policy decides


def test_deterministic_counter_evidence_is_violated_whatever_the_model_says():
    reqs, ledger = _ledger(GENERAL_GOAL, verdict=SATISFIED)
    record_requirement_closure(ledger, reqs, "REQ-1", evidence_id="cand", method="mutation_scope",
                               detail={}, source="test", revision=1, violated=True)
    assert _outcome(ledger, reqs) is RequirementOutcome.VIOLATED
    assert _blocked(ledger, reqs, PERMISSIVE) == [("REQ-1", RequirementOutcome.VIOLATED)]


def test_the_model_negative_is_kept_as_advisory_provenance():
    reqs, ledger = _ledger(GENERAL_GOAL, verdict=MISSING)
    details = requirement_verdict_details(ledger, reqs)["REQ-1"]
    assert details["model_outcome"] == "violated" and details["evidence_class"] == "MODEL_CLAIMED"
    assert details["reason_code"] == VERIFIER_REPORTED_MISSING
    assert details["outcome"] == "unverified"


def test_a_pre_fix_violated_verdict_record_read_back_is_a_model_claim_too(tmp_path):
    """A verdict record written before GR-R0 (outcome "violated", e.g. read back
    on resume) is still only the verifier's claim: trusted closure outranks it,
    and without closure it blocks as UNVERIFIED, never VIOLATED."""
    reqs, ledger = _ledger(GENERAL_GOAL, verdict=MISSING)
    record = ledger.current(requirement_obligation_id("REQ-1"))
    ledger.record(dataclasses.replace(record, status=ObligationStatus.VIOLATED,
                                      evidence={**record.evidence, "outcome": "violated"}))
    assert _outcome(ledger, reqs) is RequirementOutcome.UNVERIFIED
    assert _blocked(ledger, reqs, PERMISSIVE) == [("REQ-1", RequirementOutcome.UNVERIFIED)]
    root, base = _repo(tmp_path)
    acceptance = _acceptance(tmp_path)
    _close(ledger, reqs, acceptance, root, approval=_approval(tmp_path, GENERAL_GOAL, acceptance, base, root),
           base=base)
    assert _outcome(ledger, reqs) is RequirementOutcome.HUMAN_ACCEPTED
