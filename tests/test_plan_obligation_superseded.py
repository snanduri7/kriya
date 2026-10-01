"""PLAN-OBLIGATION-SUPERSEDED-001: a terminal plan obligation belongs to the
plan draft that recorded it.

PRD-036 rc6 canary run 2: the Planner's first draft declared that s1
`requires driver_repository_access` with no provider (VIOLATED,
terminal-required); the repaired draft dropped the requirement and
validated, s1 completed and every gate passed, and the terminal
obligations check still failed the run on the rejected draft's record.
"""
import pytest

from kriya.workflow.obligations import (
    ObligationAuthority,
    ObligationKind,
    ObligationLedger,
    ObligationRecord,
    ObligationStatus,
)
from kriya.workflow.plan_schema import (
    EngineeringPlan,
    ExecutionMethod,
    FileAction,
    GlobalInvariant,
    IntegrationRelationship,
    IntegrationRelationshipKind,
    PlannedFile,
    Subtask,
)
from kriya.workflow.plan_validation import validate_plan
from kriya.workflow.triage import ChangeKind

REQUIRES_ID = "plan.subtask.s1.requires.driver_repository_access"


def _subtask(sid="s1", path="Service.java", **overrides):
    values = dict(id=sid, description=f"write {path}", execution_method=ExecutionMethod.MODEL,
                  planned_files=[PlannedFile(path=path, action=FileAction.CREATE)])
    values.update(overrides)
    return Subtask(**values)


def _plan(*subtasks, **overrides):
    return EngineeringPlan(plan_id="p1", kind=ChangeKind.TASK, subtasks=list(subtasks), **overrides)


async def _validate(plan, tmp_path, ledger, revision):
    return await validate_plan(plan, workspace_path=str(tmp_path), obligation_ledger=ledger, revision=revision)


@pytest.mark.asyncio
async def test_a_requirement_dropped_by_the_repaired_draft_no_longer_blocks_the_terminal_gate(tmp_path):
    ledger = ObligationLedger()
    rejected = await _validate(_plan(_subtask(requires=["driver_repository_access"])), tmp_path, ledger, 0)
    assert "SUBTASK_REQUIREMENT_UNPROVIDED" in rejected.reason_codes
    assert [r.id for r in ledger.unresolved_terminal_obligations()] == [REQUIRES_ID]

    accepted = await _validate(_plan(_subtask()), tmp_path, ledger, 1)

    assert accepted.valid, accepted.errors
    assert ledger.unresolved_terminal_obligations() == []
    history = ledger.history(REQUIRES_ID)
    assert [(r.status, r.terminal_required, r.revision) for r in history] == [
        (ObligationStatus.VIOLATED, True, 0), (ObligationStatus.VIOLATED, False, 1),
    ]
    assert history[-1].evidence["superseded_between_revisions"] is True
    assert ledger.regressions == []


@pytest.mark.asyncio
async def test_a_draft_that_plans_the_element_again_makes_it_terminal_again(tmp_path):
    ledger = ObligationLedger()
    await _validate(_plan(_subtask(requires=["driver_repository_access"])), tmp_path, ledger, 0)
    await _validate(_plan(_subtask()), tmp_path, ledger, 1)
    await _validate(_plan(_subtask(requires=["driver_repository_access"])), tmp_path, ledger, 2)

    assert [r.id for r in ledger.unresolved_terminal_obligations()] == [REQUIRES_ID]
    assert ledger.current(REQUIRES_ID).terminal_required is True


@pytest.mark.asyncio
async def test_a_dropped_unknown_invariant_reference_is_retired_too(tmp_path):
    ledger = ObligationLedger()
    invariants = [GlobalInvariant(id="gi1", statement="the service stays stateless")]
    await _validate(_plan(_subtask(relevant_global_invariant_ids=["gi9"]), global_invariants=invariants),
                    tmp_path, ledger, 0)
    assert [r.id for r in ledger.unresolved_terminal_obligations()] == ["plan.subtask.s1.invariant_ref.gi9"]

    await _validate(_plan(_subtask(relevant_global_invariant_ids=["gi1"]), global_invariants=invariants),
                    tmp_path, ledger, 1)

    assert ledger.unresolved_terminal_obligations() == []


def _integration(rel_id="r1"):
    return IntegrationRelationship(
        id=rel_id, kind=IntegrationRelationshipKind.USES,
        producer_subtask_ids=["s3"], consumer_subtask_ids=["s2"],
        relationship_statement="App.java must use InMemoryService.java",
    )


def _integration_subtasks():
    return (_subtask("s2", "App.java", depends_on=["s3"]), _subtask("s3", "InMemoryService.java"))


@pytest.mark.asyncio
async def test_a_still_planned_integration_stays_pending_and_terminal_across_drafts(tmp_path):
    """Seeded once, never re-recorded: it must not read as superseded while
    the relationship is still planned."""
    ledger = ObligationLedger()
    await _validate(_plan(*_integration_subtasks(), integration_relationships=[_integration()]), tmp_path, ledger, 0)
    await _validate(_plan(*_integration_subtasks(), integration_relationships=[_integration()]), tmp_path, ledger, 1)

    rec = ledger.current("plan.integration.r1")
    assert (rec.status, rec.terminal_required) == (ObligationStatus.PENDING, True)
    assert len(ledger.history("plan.integration.r1")) == 1


@pytest.mark.asyncio
async def test_a_dropped_integration_is_retired(tmp_path):
    ledger = ObligationLedger()
    await _validate(_plan(*_integration_subtasks(), integration_relationships=[_integration()]), tmp_path, ledger, 0)
    await _validate(_plan(*_integration_subtasks()), tmp_path, ledger, 1)

    rec = ledger.current("plan.integration.r1")
    assert (rec.status, rec.terminal_required) == (ObligationStatus.PENDING, False)
    assert ledger.unresolved_terminal_obligations() == []


@pytest.mark.asyncio
async def test_obligations_from_other_sources_are_never_retired_by_plan_validation(tmp_path):
    ledger = ObligationLedger()
    ledger.record(ObligationRecord(
        id="recovery.s1.owner", kind=ObligationKind.CROSS_SUBTASK_INTEGRATION,
        status=ObligationStatus.VIOLATED, authority=ObligationAuthority.DETERMINISTIC,
        description="owner recovery", source="workflow_controller.owner_recovery",
        terminal_required=True,
    ))
    await _validate(_plan(_subtask()), tmp_path, ledger, 0)

    assert [r.id for r in ledger.unresolved_terminal_obligations()] == ["recovery.s1.owner"]


@pytest.mark.asyncio
async def test_a_satisfied_requirement_dropped_later_still_raises_its_regression(tmp_path):
    """The existing SATISFIED->dropped regression pass is unchanged."""
    ledger = ObligationLedger()
    provider = _subtask("s0", "Repo.java", provides=["driver_repository_access"])
    await _validate(_plan(provider, _subtask(requires=["driver_repository_access"], depends_on=["s0"])),
                    tmp_path, ledger, 0)
    assert ledger.current(REQUIRES_ID).status == ObligationStatus.SATISFIED

    await _validate(_plan(provider, _subtask(depends_on=["s0"])), tmp_path, ledger, 1)

    assert [r.obligation_id for r in ledger.regressions] == [REQUIRES_ID]
    assert ledger.current(REQUIRES_ID).terminal_required is False


@pytest.mark.asyncio
async def test_a_satisfied_record_of_a_dropped_element_is_left_untouched(tmp_path):
    """Only unresolved records are retired: a settled one keeps its single
    record (the repair loop's must-preserve lines read those)."""
    ledger = ObligationLedger()
    await _validate(_plan(_subtask(path="Service.java")), tmp_path, ledger, 0)
    ownership = "plan.file.Service.java.ownership"
    assert ledger.current(ownership).status == ObligationStatus.SATISFIED

    await _validate(_plan(_subtask(path="Other.java")), tmp_path, ledger, 1)

    assert len(ledger.history(ownership)) == 1
    assert ledger.current(ownership).terminal_required is True
