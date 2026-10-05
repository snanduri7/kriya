"""LR-R1-P5 deterministic reproducer: a cross-subtask integration obligation
reported VIOLATED after every subtask passed (CAGC-v2 live instances:
python-symbol-valuechain B r2 and spring-xml-pettypes-cache A r1, both ending
TERMINAL_OBLIGATIONS_UNSATISFIED; valuechain B r2 is the decisive UNKNOWN of
the PROTOCOL_v2 rule-3 result).

Investigation: handover/LR_R1_P5_INTEGRATION_OBLIGATION_INVESTIGATION.md.
Pins the CURRENT behaviour (no fix here). The mechanism:
workflow_controller._evaluate_integration_obligations builds the consumer's
evidence only from the files the consumer subtask WROTE in this run
(established_file_context is filled from call_result["files"]). A consumer
that legitimately writes nothing - a verified no-change unit (valuechain B
r2's s2), or a verification-only unit with no planned files (spring-xml A
r1's s3) - therefore has empty evidence, every producer is "missing", and
the obligation is VIOLATED even when the consumer's unchanged file already
references the producer.
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
from kriya.workflow.workflow_controller import _evaluate_integration_obligations

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


def test_the_check_reads_only_what_the_consumer_wrote():
    """Unit-level: the consumer's unchanged file references the producer
    ("shop.service"), but a consumer that wrote nothing is judged on empty
    evidence. Negative control: the same reference, written by the consumer,
    satisfies the obligation."""
    plan = _plan_with_integration(harness.TOOL_CRITERION)
    assert "service" in harness.CONTROLLER_SRC.replace("shop.service", "shop . service").split()

    unwritten = _pending_ledger()
    _evaluate_integration_obligations(plan, unwritten, "s2", {SERVICE: harness.SERVICE_SRC}, 1)
    record = unwritten.current("plan.integration.ir1")
    assert record.status == ObligationStatus.VIOLATED
    assert record.evidence["missing_producer_references"] == [SERVICE]

    written = _pending_ledger()
    _evaluate_integration_obligations(plan, written, "s2",
                                      {SERVICE: harness.SERVICE_SRC, CONTROLLER: harness.CONTROLLER_SRC}, 1)
    assert written.current("plan.integration.ir1").status == ObligationStatus.SATISFIED


def test_a_consumer_with_no_planned_files_can_never_satisfy_the_obligation():
    """The spring-xml A r1 shape: the consumer is a verification-only unit
    (planned_files: []) - there is no consumer content to hold any reference."""
    plan = EngineeringPlan.model_validate({
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
    ledger = _pending_ledger()
    _evaluate_integration_obligations(plan, ledger, "s2", {"config/tools-config.xml": "<beans/>"}, 1)
    record = ledger.current("plan.integration.ir1")
    assert record.status == ObligationStatus.VIOLATED
    assert record.evidence["missing_producer_references"] == ["config/tools-config.xml"]


def test_end_to_end_a_verified_no_change_consumer_ends_terminal_obligations_unsatisfied(tmp_path, monkeypatch, caplog):
    """The valuechain B r2 shape through the real enforce controller: s1
    changes the service, s2 (planned against the controller, which already
    calls the service) is verified NO CHANGE - both subtasks complete, and the
    run still ends on the integration obligation."""
    caplog.set_level(logging.INFO, logger="kriya.workflow.workflow_controller")
    monkeypatch.setattr(harness, "_plan", _plan_with_integration)
    workspace, _events, result = harness._enforce(tmp_path, harness.TOOL_CRITERION)

    status = {r.subtask_id: r.status.value for r in result.subtask_results}
    assert status == {"s1": "completed", "s2": "completed"}
    assert "INTEGRATION_OBLIGATION_VIOLATED id=plan.integration.ir1 consumer=s2 missing=['shop/service.py']" \
        in caplog.text
    assert "TERMINAL OBLIGATIONS UNSATISFIED" in caplog.text and "plan.integration.ir1" in caplog.text
    assert result.legacy_result["status"] != "success"
    # Nothing applied (as in the live run).
    assert (workspace / SERVICE).read_text() == harness.UNCACHED_SERVICE
