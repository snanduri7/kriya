"""LR-R1-P5 deterministic reproducer: a cross-subtask integration obligation
reported VIOLATED after every subtask passed (CAGC-v2 live instances:
python-symbol-valuechain B r2 and spring-xml-pettypes-cache A r1, both ending
TERMINAL_OBLIGATIONS_UNSATISFIED; valuechain B r2 is the decisive UNKNOWN of
the PROTOCOL_v2 rule-3 result).

Investigation: handover/LR_R1_P5_INTEGRATION_OBLIGATION_INVESTIGATION.md.
First written to pin the defective behaviour (investigation branch, 4283539);
flipped to the fixed behaviour before the P5 fix was implemented. The defect:
workflow_controller._evaluate_integration_obligations builds the consumer's
evidence only from the files the consumer subtask WROTE in this run
(established_file_context is filled from call_result["files"]). A consumer
that legitimately writes nothing - a verified no-change unit (valuechain B
r2's s2), or a verification-only unit with no planned files (spring-xml A
r1's s3) - therefore has empty evidence, every producer is "missing", and
the obligation is VIOLATED even when the consumer's unchanged file already
references the producer.

Fixed behaviour (LR-R1-P5): with the run's provenance (which subtask
established which artifact, at which bytes) the check reads the consumer's
CURRENT planned artifacts (written this run or unchanged on disk), requires
each provider artifact to have been established by a declared producer and to
be unchanged in the workspace, and judges a consumer with no planned artifact
(verification-only) by those provider artifacts plus its own declared
verification - which has passed when the check runs. The reference criterion
itself is unchanged: a consumer whose content does not reference the
producer is still VIOLATED.
"""
import logging

import test_enforce_verified_no_change as harness

from kriya.workflow.obligations import (
    ObligationAuthority,
    ObligationKind,
    ObligationLedger,
    ObligationRecord,
    ObligationStatus,
)
from kriya.workflow.plan_schema import EngineeringPlan
from kriya.workflow.workflow_controller import EstablishedProvenance, _evaluate_integration_obligations

SERVICE, CONTROLLER = harness.SERVICE, harness.CONTROLLER
_TWO_UNIT_PLAN = harness._plan


def _plan_with_integration(criterion):
    plan = _TWO_UNIT_PLAN(criterion).model_dump()
    plan["integration_relationships"] = [{
        "id": "ir1", "kind": "uses", "producer_subtask_ids": ["s1"], "consumer_subtask_ids": ["s2"],
        "participating_artifacts": [],
        "relationship_statement": "shop/controller.py uses the cached find_pet_types from shop/service.py.",
    }]
    return EngineeringPlan.model_validate(plan)


def _pending_ledger():
    ledger = ObligationLedger()
    ledger.record(ObligationRecord(
        id="plan.integration.ir1", kind=ObligationKind.CROSS_SUBTASK_INTEGRATION, status=ObligationStatus.PENDING,
        authority=ObligationAuthority.DETERMINISTIC, description="ir1", source="plan_validation",
        revision=0, terminal_required=True,
    ))
    return ledger


def _workspace(tmp_path, files):
    for path, text in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text)
    return tmp_path


def test_a_consumer_that_wrote_nothing_is_judged_on_its_current_artifact(tmp_path):
    """The consumer's unchanged file references the producer: SATISFIED.
    Negative control: the same unwritten consumer whose file does NOT
    reference the producer stays VIOLATED (the criterion is unchanged)."""
    plan = _plan_with_integration(harness.TOOL_CRITERION)
    workspace = _workspace(tmp_path / "ok", {SERVICE: harness.SERVICE_SRC, CONTROLLER: harness.CONTROLLER_SRC})
    provenance = EstablishedProvenance(str(workspace))
    provenance.record(SERVICE, "s1")
    ledger = _pending_ledger()
    _evaluate_integration_obligations(plan, ledger, "s2", {SERVICE: harness.SERVICE_SRC}, 1, provenance=provenance)
    record = ledger.current("plan.integration.ir1")
    assert record.status == ObligationStatus.SATISFIED
    assert record.evidence["missing_producer_references"] == []
    assert record.evidence["consumer_evidence"]["sources"] == {CONTROLLER: "current_workspace"}

    unrelated = _workspace(tmp_path / "bad", {SERVICE: harness.SERVICE_SRC, CONTROLLER: "def populate():\n    return ()\n"})
    provenance = EstablishedProvenance(str(unrelated))
    provenance.record(SERVICE, "s1")
    ledger = _pending_ledger()
    _evaluate_integration_obligations(plan, ledger, "s2", {SERVICE: harness.SERVICE_SRC}, 1, provenance=provenance)
    assert ledger.current("plan.integration.ir1").status == ObligationStatus.VIOLATED


def _verification_consumer_plan():
    return EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}],
        "acceptance_criteria": [],
        "subtasks": [
            {"id": "s1", "description": "configure the cache", "execution_method": "model",
             "planned_files": [{"path": "config/tools-config.xml", "action": "modify"}],
             "provides": ["cache-configuration"], "relevant_global_invariant_ids": ["gi1"],
             "verification": [{"type": "tool", "tool_name": "compile", "description": "compile"}]},
            {"id": "s2", "description": "run the regression suite", "execution_method": "model",
             "execution_role": "verification", "planned_files": [], "depends_on": ["s1"],
             "requires": ["cache-configuration"], "relevant_global_invariant_ids": ["gi1"],
             "verification": [{"type": "tool", "tool_name": "test", "description": "run the tests"}]},
        ],
        "integration_relationships": [{
            "id": "ir1", "kind": "configures", "producer_subtask_ids": ["s1"], "consumer_subtask_ids": ["s2"],
            "participating_artifacts": [], "relationship_statement": "the cache configuration is verified",
        }],
    })


def test_a_verification_only_consumer_is_judged_on_the_provider_artifact_and_its_verification(tmp_path):
    """The spring-xml A r1 shape: the consumer owns no artifact. The provider
    artifact established by s1 and still present, plus s2's own passed
    verification, is the relationship's evidence."""
    plan = _verification_consumer_plan()
    workspace = _workspace(tmp_path, {"config/tools-config.xml": "<beans/>"})
    provenance = EstablishedProvenance(str(workspace))
    provenance.record("config/tools-config.xml", "s1")
    ledger = _pending_ledger()
    _evaluate_integration_obligations(plan, ledger, "s2", {"config/tools-config.xml": "<beans/>"}, 1,
                                      provenance=provenance)
    record = ledger.current("plan.integration.ir1")
    assert record.status == ObligationStatus.SATISFIED
    assert record.evidence["evaluation"] == "verification_only_consumer"
    assert record.evidence["consumer_evidence"]["verification"] == ["tool:test"]


def test_end_to_end_a_verified_no_change_consumer_satisfies_the_integration_obligation(tmp_path, monkeypatch, caplog):
    """The valuechain B r2 shape through the real enforce controller: s1
    changes the service, s2 (planned against the controller, which already
    calls the service) is verified NO CHANGE. Fixed: the obligation is judged
    on the controller's current content and is SATISFIED; the run is no
    longer stopped by it."""
    caplog.set_level(logging.INFO, logger="kriya.workflow.workflow_controller")
    monkeypatch.setattr(harness, "_plan", _plan_with_integration)
    workspace, _events, result = harness._enforce(tmp_path, harness.TOOL_CRITERION)

    status = {r.subtask_id: r.status.value for r in result.subtask_results}
    assert status == {"s1": "completed", "s2": "completed"}
    assert "INTEGRATION_OBLIGATION_SATISFIED id=plan.integration.ir1 consumer=s2 missing=[]" in caplog.text
    assert "INTEGRATION_OBLIGATION_VIOLATED" not in caplog.text
    assert "plan.integration.ir1" not in "\n".join(
        line for line in caplog.text.splitlines() if "TERMINAL OBLIGATIONS UNSATISFIED" in line)
