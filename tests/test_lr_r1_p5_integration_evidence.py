"""LR-R1-P5: a cross-subtask integration obligation is judged on the artifacts
that can legitimately satisfy it (workflow_controller._integration_evidence).
Case 1 is the reproducer (tests/test_lr_r1_p5_integration_obligation_reproducer.py)."""
import ast
import os

import pytest
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

SERVICE, CONTROLLER, OTHER, REPO = harness.SERVICE, harness.CONTROLLER, "shop/other.py", "shop/repository.py"
USES_BOTH = "from shop.service import find_pet_types\nfrom shop.repository import load\n"


def _sub(sid, paths, **extra):
    return {"id": sid, "description": sid, "execution_method": "model",
            "planned_files": [{"path": p, "action": "modify"} for p in paths],
            "relevant_global_invariant_ids": ["gi1"],
            "verification": [{"type": "tool", "tool_name": "test", "description": "run the tests"}], **extra}


def _plan(subtasks, producers, consumers, participating=()):
    return EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}],
        "acceptance_criteria": [], "subtasks": subtasks,
        "integration_relationships": [{"id": "ir1", "kind": "uses", "producer_subtask_ids": producers,
                                       "consumer_subtask_ids": consumers,
                                       "participating_artifacts": list(participating),
                                       "relationship_statement": "the consumer uses the provider"}]})


def _ledger():
    ledger = ObligationLedger()
    ledger.record(ObligationRecord(
        id="plan.integration.ir1", kind=ObligationKind.CROSS_SUBTASK_INTEGRATION, status=ObligationStatus.PENDING,
        authority=ObligationAuthority.DETERMINISTIC, description="ir1", source="plan_validation", revision=0,
        terminal_required=True))
    return ledger


def _workspace(tmp_path, files):
    for path, text in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text)
    return EstablishedProvenance(str(tmp_path))


def _judge(plan, provenance, consumer="s2", established=None):
    ledger = _ledger()
    _evaluate_integration_obligations(plan, ledger, consumer, dict(established or {}), 1, provenance=provenance)
    return ledger.current("plan.integration.ir1")


def _two_unit():
    return _plan([_sub("s1", [SERVICE]), _sub("s2", [CONTROLLER], depends_on=["s1"])], ["s1"], ["s2"])


# -- the provider side ---------------------------------------------------------------------------------


def test_case2_a_provider_artifact_never_established_fails_even_if_the_consumer_references_it(tmp_path):
    provenance = _workspace(tmp_path, {SERVICE: harness.SERVICE_SRC, CONTROLLER: harness.CONTROLLER_SRC})
    record = _judge(_two_unit(), provenance)   # s1 recorded nothing
    assert record.status == ObligationStatus.VIOLATED
    assert record.evidence["provider_evidence"][SERVICE] == {"state": "not_established", "established_by": None}
    assert record.evidence["missing_producer_references"] == [SERVICE]


def test_case3_the_provider_writing_another_artifact_does_not_satisfy_the_required_one(tmp_path):
    plan = _plan([_sub("s1", [SERVICE, OTHER]), _sub("s2", [CONTROLLER], depends_on=["s1"])], ["s1"], ["s2"],
                 participating=[SERVICE, CONTROLLER])
    provenance = _workspace(tmp_path, {SERVICE: harness.SERVICE_SRC, OTHER: "x = 1\n",
                                       CONTROLLER: harness.CONTROLLER_SRC})
    provenance.record(OTHER, "s1")   # the provider established something - not the required artifact
    record = _judge(plan, provenance)
    assert record.status == ObligationStatus.VIOLATED
    assert list(record.evidence["provider_evidence"]) == [SERVICE]
    assert record.evidence["provider_evidence"][SERVICE]["state"] == "not_established"


def test_case7_a_non_provider_writing_the_same_path_does_not_count(tmp_path):
    provenance = _workspace(tmp_path, {SERVICE: harness.SERVICE_SRC, CONTROLLER: harness.CONTROLLER_SRC})
    provenance.record(SERVICE, "s9")   # an unrelated sibling
    record = _judge(_two_unit(), provenance)
    assert record.status == ObligationStatus.VIOLATED
    assert record.evidence["provider_evidence"][SERVICE] == {"state": "established_by_non_provider",
                                                            "established_by": "s9"}


@pytest.mark.parametrize("change", ["removed", "rewritten"])
def test_case8_a_provider_artifact_invalidated_after_its_local_pass_fails(tmp_path, change):
    provenance = _workspace(tmp_path, {SERVICE: harness.SERVICE_SRC, CONTROLLER: harness.CONTROLLER_SRC})
    provenance.record(SERVICE, "s1")   # s1 passed locally with these bytes
    if change == "removed":
        os.remove(tmp_path / SERVICE)
    else:
        (tmp_path / SERVICE).write_text("def find_pet_types():\n    return ()\n")
    record = _judge(_two_unit(), provenance)
    assert record.status == ObligationStatus.VIOLATED
    assert record.evidence["provider_evidence"][SERVICE]["state"] == "invalidated"


def test_case6_only_the_relationships_providers_count(tmp_path):
    plan = _plan([_sub("s1", [SERVICE]), _sub("s3", [REPO]), _sub("s4", [OTHER]),
                  _sub("s2", [CONTROLLER], depends_on=["s1", "s3"])], ["s1", "s3"], ["s2"])
    provenance = _workspace(tmp_path, {SERVICE: harness.SERVICE_SRC, REPO: "def load():\n    pass\n",
                                       OTHER: "x = 1\n", CONTROLLER: USES_BOTH})
    for path, sid in ((SERVICE, "s1"), (REPO, "s3"), (OTHER, "s4")):
        provenance.record(path, sid)
    record = _judge(plan, provenance)
    assert record.status == ObligationStatus.SATISFIED
    assert sorted(record.evidence["provider_evidence"]) == [REPO, SERVICE]   # s4's artifact is not evidence
    (tmp_path / CONTROLLER).write_text("from shop.service import find_pet_types\n")   # references one only
    provenance2 = EstablishedProvenance(str(tmp_path))
    for path, sid in ((SERVICE, "s1"), (REPO, "s3")):
        provenance2.record(path, sid)
    record = _judge(plan, provenance2)
    assert record.status == ObligationStatus.VIOLATED and record.evidence["missing_producer_references"] == [REPO]


# -- the consumer side ---------------------------------------------------------------------------------


def test_case4_a_no_change_consumer_is_judged_on_its_current_file_not_auto_passed(tmp_path):
    provenance = _workspace(tmp_path, {SERVICE: harness.SERVICE_SRC, CONTROLLER: harness.CONTROLLER_SRC})
    provenance.record(SERVICE, "s1")
    record = _judge(_two_unit(), provenance, established={SERVICE: harness.SERVICE_SRC})
    assert record.status == ObligationStatus.SATISFIED
    assert record.evidence["consumer_evidence"]["sources"] == {CONTROLLER: "current_workspace"}
    # Not an automatic pass: the same no-change consumer with an invalid provider artifact fails.
    (tmp_path / SERVICE).write_text("changed later\n")
    assert _judge(_two_unit(), provenance).status == ObligationStatus.VIOLATED


def _verification_plan(verification=True):
    consumer = {"id": "s2", "description": "verify", "execution_method": "model", "execution_role": "verification",
                "planned_files": [], "depends_on": ["s1"], "relevant_global_invariant_ids": ["gi1"],
                "verification": [{"type": "tool", "tool_name": "test", "description": "run the tests"}]}
    plan = _plan([_sub("s1", [SERVICE]), consumer], ["s1"], ["s2"])
    if not verification:
        plan.subtask_by_id("s2").verification.clear()
    return plan


def test_case5_a_verification_only_consumer_needs_a_valid_provider_artifact(tmp_path):
    provenance = _workspace(tmp_path, {SERVICE: harness.SERVICE_SRC})
    assert _judge(_verification_plan(), provenance).status == ObligationStatus.VIOLATED   # not established
    provenance.record(SERVICE, "s1")
    record = _judge(_verification_plan(), provenance)
    assert record.status == ObligationStatus.SATISFIED
    assert record.evidence["evaluation"] == "verification_only_consumer"


def test_case5_a_consumer_with_no_artifact_and_no_verification_fails_closed(tmp_path):
    provenance = _workspace(tmp_path, {SERVICE: harness.SERVICE_SRC})
    provenance.record(SERVICE, "s1")
    record = _judge(_verification_plan(verification=False), provenance)
    assert record.status == ObligationStatus.VIOLATED
    assert "no declared verification" in record.evidence["failure_reason"]


def test_a_consumer_written_this_run_reads_its_written_content(tmp_path):
    provenance = _workspace(tmp_path, {SERVICE: harness.SERVICE_SRC, CONTROLLER: harness.CONTROLLER_SRC})
    provenance.record(SERVICE, "s1")
    provenance.record(CONTROLLER, "s2")
    record = _judge(_two_unit(), provenance, established={CONTROLLER: harness.CONTROLLER_SRC})
    assert record.status == ObligationStatus.SATISFIED
    assert record.evidence["consumer_evidence"]["sources"] == {CONTROLLER: "written_this_run"}


def test_a_consumer_that_does_not_reference_the_provider_still_fails(tmp_path):
    provenance = _workspace(tmp_path, {SERVICE: harness.SERVICE_SRC, CONTROLLER: "def f():\n    return 1\n"})
    provenance.record(SERVICE, "s1")
    record = _judge(_two_unit(), provenance)
    assert record.status == ObligationStatus.VIOLATED
    assert record.evidence["missing_producer_references"] == [SERVICE]
    assert "not referenced by the consumer" in record.evidence["failure_reason"]


# -- unchanged paths -----------------------------------------------------------------------------------


def test_case9_a_consumer_that_is_its_own_provider_behaves_as_before(tmp_path):
    plan = _plan([_sub("s1", [SERVICE])], ["s1"], ["s1"])
    provenance = _workspace(tmp_path, {SERVICE: harness.SERVICE_SRC})
    provenance.record(SERVICE, "s1")
    for content in (harness.SERVICE_SRC, "# service\n" + harness.SERVICE_SRC):
        (tmp_path / SERVICE).write_text(content)
        provenance.record(SERVICE, "s1")
        before, after = _ledger(), _ledger()
        _evaluate_integration_obligations(plan, before, "s1", {SERVICE: content}, 1)   # pre-P5 semantics
        _evaluate_integration_obligations(plan, after, "s1", {SERVICE: content}, 1, provenance=provenance)
        assert after.current("plan.integration.ir1").status == before.current("plan.integration.ir1").status


def test_case10_a_plan_without_integration_relationships_is_untouched(tmp_path):
    plan = EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}],
        "acceptance_criteria": [], "subtasks": [_sub("s1", [SERVICE])]})
    ledger = ObligationLedger()
    other = ObligationRecord(id="plan.something", kind=ObligationKind.CROSS_SUBTASK_INTEGRATION,
                             status=ObligationStatus.PENDING, authority=ObligationAuthority.DETERMINISTIC,
                             description="d", source="s", revision=0, terminal_required=True)
    ledger.record(other)
    _evaluate_integration_obligations(plan, ledger, "s1", {SERVICE: "x"}, 1,
                                      provenance=_workspace(tmp_path, {SERVICE: "x"}))
    assert ledger.current("plan.something") == other


def test_every_production_call_passes_the_runs_provenance():
    source = open(os.path.join(os.path.dirname(__file__), "..", "kriya", "workflow", "workflow_controller.py"),
                  encoding="utf-8").read()
    calls = [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call)
             and getattr(node.func, "id", None) == "_evaluate_integration_obligations"]
    assert len(calls) == 2
    assert all(any(k.arg == "provenance" and getattr(k.value, "id", None) == "established_provenance"
                   for k in call.keywords) for call in calls)
    assert source.count("established_provenance.record(") == 3   # completion, resume, owner recovery


# -- M1: Q9 names the decision and its evidence --------------------------------------------------------


def test_q9_explains_the_integration_decision(tmp_path, monkeypatch):
    from kriya.core.attempt_evidence import scope
    from kriya.core.attempt_evidence.explain import explain_run
    from kriya.core.state_paths import ENV_STATE_DIR
    from tests._strict_doubles import strict_config

    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(ENV_STATE_DIR, str(state))

    class _Context:
        run_id = "run-p5"
    provenance = _workspace(tmp_path / "ws", {SERVICE: harness.SERVICE_SRC, CONTROLLER: "def f():\n    pass\n"})
    provenance.record(SERVICE, "s1")
    with scope.run_scope(_Context()):
        scope.ensure_store(strict_config())
        _evaluate_integration_obligations(_two_unit(), _ledger(), "s2", {}, 1, provenance=provenance)
        scope.close_run(_Context(), lambda: None)
    [decision] = explain_run(str(state), "run-p5")["Q9"]["integration_obligations"]
    assert decision["obligation_id"] == "plan.integration.ir1" and decision["status"] == "violated"
    assert decision["provider_evidence"] == {SERVICE: {"state": "valid", "established_by": "s1"}}
    assert decision["consumer_evidence"]["sources"] == {CONTROLLER: "current_workspace"}
    assert decision["missing_producer_references"] == [SERVICE]
    assert "not referenced by the consumer" in decision["failure_reason"]


def test_another_subtasks_written_reference_is_not_the_consumers_evidence(tmp_path):
    """Not a union of every write: a sibling's established file references
    the provider, the consumer's own artifact does not - VIOLATED."""
    provenance = _workspace(tmp_path, {SERVICE: harness.SERVICE_SRC, CONTROLLER: "def f():\n    return 1\n",
                                       OTHER: "from shop.service import find_pet_types\n"})
    provenance.record(SERVICE, "s1")
    provenance.record(OTHER, "s4")
    record = _judge(_two_unit(), provenance, established={SERVICE: harness.SERVICE_SRC,
                                                          OTHER: "from shop.service import find_pet_types\n"})
    assert record.status == ObligationStatus.VIOLATED
    assert record.evidence["consumer_evidence"]["paths"] == [CONTROLLER]
