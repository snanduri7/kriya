"""S8 (C-07, C-08): the run's durable work ledger - one authority for consumed generation time, unit invocations, Developer attempts and retry-family history,
across units, retries and resumes.

Pre-fix reproducer (MEASURED at b6ac020, ``~/kriya-evidence/s8-b6ac020/prefix_budget_reset.txt``): a run consumed 1,300 s of generation time and stopped; the run
that resumed it started its generation clock at zero (nothing on the lease or on disk carried the consumption), and a repository configuration could set
``autonomy.generation_time_budget_seconds`` to 999,999 or ``null`` with no approval (it was REPOSITORY_SAFE at any value).

The lease-level tests drive the real coordinator (a real run lock, RunRecord and ledger document) with an injected clock; the pipeline tests run the real direct
and enforce pipelines with a scripted model.
"""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest
import yaml

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "state_machine"))
import _kernel_golden as kg  # noqa: E402
from _enforce_unit_harness import PASSING, run_enforce  # noqa: E402

from kriya.config import (
    authority,  # noqa: E402
    resolution,  # noqa: E402
)
from kriya.config import authority_approval as aa  # noqa: E402
from kriya.config.authority import ConfigAuthorityError  # noqa: E402
from kriya.config.config import load_config, resolve_config_state  # noqa: E402
from kriya.control import run_coordinator as rc  # noqa: E402
from kriya.control.persistence import list_run_records, load_run_record, work_ledger_path  # noqa: E402
from kriya.control.work_ledger import (  # noqa: E402
    UNIT_INVOCATION_CEILING,
    WORK_CEILING_EXHAUSTED,
    WORK_LEDGER_MISSING,
    WORK_LEDGER_NOT_PERSISTED,
    WORK_LEDGER_NOT_THIS_WORK,
    WORK_LEDGER_RESUME_REFUSED,
    WORK_LEDGER_UNREADABLE,
    WorkLedger,
    WorkLedgerError,
)
from kriya.workflow import retry_policy  # noqa: E402
from kriya.workflow.plan_schema import EngineeringPlan  # noqa: E402
from kriya.workflow.state import RetryBudgets  # noqa: E402


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr(rc, "_now", fake)
    return fake


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "README.md").write_text("base\n")
    for argv in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "t"], ["add", "-A"], ["commit", "-q", "-m", "initial"]):
        subprocess.run(["git", *argv], cwd=str(root), check=True)
    return str(root)


def _ledger(workspace: str) -> Dict[str, Any]:
    return json.loads(Path(work_ledger_path(workspace)).read_text())


def _elapsed() -> float:
    """What every reader of the run's generation clock sees as consumed."""
    return rc._now() - rc.claim_run_generation_clock()


# =========================================================================== the reproducer, inverted: a resume continues the consumed time

def test_a_resumed_run_continues_the_generation_time_the_earlier_run_consumed(workspace, clock):
    with rc.begin_mutating_run(workspace) as first:
        rc.claim_run_generation_clock()
        clock.now += 1200
        assert rc.grant_developer_attempt(60) is None
        clock.now += 100
    assert _ledger(workspace)["consumed_seconds"] == pytest.approx(1300) and _ledger(workspace)["open_grant"] is None
    clock.now += 86_400                                             # a day between the runs is not generation time
    with rc.begin_mutating_run(workspace) as resumed:
        assert rc.resume_work_ledger(first.run_id) is None
        assert _elapsed() == pytest.approx(1300), "pre-fix: 0.0 - the resumed run got a fresh budget"
        clock.now += 50
        assert _elapsed() == pytest.approx(1350)
    document = _ledger(workspace)
    assert document["runs"] == [first.run_id, resumed.run_id] and document["consumed_seconds"] == pytest.approx(1350)


def test_the_continuation_reaches_a_clock_that_was_already_running(workspace, clock):
    """A run that called a model before it learned what it resumes: the earlier consumption is added to its own, once, for every reader of the clock."""
    with rc.begin_mutating_run(workspace) as first:
        rc.claim_run_generation_clock()
        clock.now += 400
        assert rc.grant_developer_attempt(10) is None
    with rc.begin_mutating_run(workspace):
        rc.claim_run_generation_clock()
        clock.now += 30

        class _State:
            budget_started_monotonic = rc.claim_run_generation_clock()
        state = _State()
        rc.follow_run_generation_clock(state)
        assert rc.resume_work_ledger(first.run_id) is None
        assert _elapsed() == pytest.approx(430) and clock.now - state.budget_started_monotonic == pytest.approx(430)
        assert rc.resume_work_ledger(first.run_id) is None          # asked again (a nested invocation): nothing is added twice
        assert _elapsed() == pytest.approx(430)


def test_a_run_that_is_not_a_resume_starts_a_new_ledger(workspace, clock):
    with rc.begin_mutating_run(workspace) as first:
        rc.claim_run_generation_clock()
        clock.now += 900
        assert rc.grant_developer_attempt(10) is None
    with rc.begin_mutating_run(workspace) as second:
        assert _elapsed() == pytest.approx(0)
        assert rc.grant_developer_attempt(10) is None
    document = _ledger(workspace)
    assert document["runs"] == [second.run_id] and document["ledger_id"] == second.run_id and document["developer_attempts"] == 1
    assert first.run_id not in document["runs"]
    assert document["previous"] is None, "the run ended without resuming: the ledger it displaced is not kept for a later run to pick up"


def test_a_new_ledger_carries_the_one_it_displaced_until_the_run_decides(workspace, clock):
    """A run may grant work before it reaches its resume decision; that first write must not destroy what the decision would continue - even if the process
    dies in between (the third run below finds both)."""
    ledger_file = Path(work_ledger_path(workspace))
    with rc.begin_mutating_run(workspace) as first:
        rc.claim_run_generation_clock()
        clock.now += 700
        assert rc.grant_unit_invocation("s1") is None
    with rc.begin_mutating_run(workspace) as undecided:
        rc.claim_run_generation_clock()
        assert rc.grant_unit_invocation("s1") is None                # written under its own new ledger, before any resume decision
        clock.now += 20
        assert rc.grant_developer_attempt(30) is None
        on_disk = json.loads(ledger_file.read_text())
        assert on_disk["runs"] == [undecided.run_id] and on_disk["previous"]["runs"] == [first.run_id]
        at_crash = ledger_file.read_bytes()
    ledger_file.write_bytes(at_crash)                                # ... and the process died there, undecided
    with rc.begin_mutating_run(workspace) as third:
        assert rc.resume_work_ledger(first.run_id) is None
        # 700 s of the first run + the undecided run's 20 s and its open 30 s reservation; both runs' invocations
        assert _elapsed() == pytest.approx(750)
    document = _ledger(workspace)
    assert document["unit_invocations"] == {"s1": 2} and document["developer_attempts"] == 1
    assert document["runs"] == [first.run_id, undecided.run_id, third.run_id] and document["previous"] is None


# =========================================================================== grant before work; a crash restores nothing

def test_a_grant_is_durably_counted_before_the_work_starts(workspace, clock):
    with rc.begin_mutating_run(workspace):
        assert rc.grant_unit_invocation("s1") is None
        on_disk = _ledger(workspace)                                 # read while the "work" is still running
        assert on_disk["unit_invocations"] == {"s1": 1} and on_disk["open_grant"]["kind"] == "unit_invocation"
        with rc.work_unit_scope("s1"):
            assert rc.grant_developer_attempt(75) is None
            on_disk = _ledger(workspace)
            assert on_disk["developer_attempts"] == 1
            assert on_disk["open_grant"] == {"kind": "developer_attempt", "unit_id": "s1", "reserved_seconds": 75.0}
        assert _ledger(workspace)["open_grant"] is None, "the unit's scope settles its grant"


def test_a_crash_inside_granted_work_charges_the_reservation_and_keeps_the_count(workspace, clock):
    ledger_file = Path(work_ledger_path(workspace))
    with rc.begin_mutating_run(workspace) as first:
        rc.claim_run_generation_clock()
        clock.now += 500
        assert rc.grant_unit_invocation("s1") is None
        with rc.work_unit_scope("s1"):
            assert rc.grant_developer_attempt(90) is None
            at_crash = ledger_file.read_bytes()                      # what the disk holds if the process dies now
            clock.now += 40                                          # ... 40 s into the attempt
    ledger_file.write_bytes(at_crash)                                # the process died: nothing after the grant was written
    with rc.begin_mutating_run(workspace):
        assert rc.resume_work_ledger(first.run_id) is None
        # 500 s recorded at the grant + the 90 s the attempt reserved: never less than was recorded, never the attempt for free
        assert _elapsed() == pytest.approx(590)
    document = _ledger(workspace)
    assert document["developer_attempts"] == 1 and document["unit_invocations"] == {"s1": 1} and document["open_grant"] is None


def test_a_grant_that_cannot_be_written_is_refused_and_not_counted(workspace, clock, monkeypatch):
    with rc.begin_mutating_run(workspace):
        assert rc.grant_unit_invocation("s1") is None

        def unwritable(*_args, **_kwargs):
            raise OSError("disk full")
        monkeypatch.setattr(rc, "save_work_ledger", unwritable)
        refusal = rc.grant_unit_invocation("s1")
        assert refusal is not None and refusal.startswith(f"{WORK_LEDGER_NOT_PERSISTED}:")
        refusal = rc.grant_developer_attempt(10)
        assert refusal is not None and refusal.startswith(f"{WORK_LEDGER_NOT_PERSISTED}:")
        ledger = rc.current_run_context()._lease.work_ledger
        assert ledger.unit_invocations == {"s1": 1} and ledger.developer_attempts == 0
        monkeypatch.undo()


# =========================================================================== the unit ceiling survives everything

def test_the_eighth_invocation_of_a_unit_is_refused_and_a_resume_cannot_make_it_legal(workspace, clock):
    assert UNIT_INVOCATION_CEILING == 7
    with rc.begin_mutating_run(workspace) as first:
        for _ in range(UNIT_INVOCATION_CEILING):
            assert rc.grant_unit_invocation("s1") is None
        refusal = rc.grant_unit_invocation("s1")
        assert refusal is not None and refusal.startswith(f"{WORK_CEILING_EXHAUSTED}:")
        assert rc.grant_unit_invocation("s2") is None, "another planned unit has its own ceiling"
    with rc.begin_mutating_run(workspace) as resumed:
        assert rc.resume_work_ledger(first.run_id) is None
        refusal = rc.grant_unit_invocation("s1")
        assert refusal is not None and refusal.startswith(f"{WORK_CEILING_EXHAUSTED}:")
    assert _ledger(workspace)["unit_invocations"] == {"s1": 7, "s2": 1}
    # the terminal records name the ledger and its counts; the resumed one names its parent
    assert load_run_record(workspace, first.run_id).work_ledger == {
        "ledger_id": first.run_id, "resumed_from": None, "unit_invocations": {"s1": 7, "s2": 1}, "developer_attempts": 0}
    assert load_run_record(workspace, resumed.run_id).work_ledger["resumed_from"] == first.run_id


def test_work_a_run_granted_before_it_resumed_is_added_never_dropped(workspace, clock):
    with rc.begin_mutating_run(workspace) as first:
        for _ in range(5):
            assert rc.grant_unit_invocation("s1") is None
    with rc.begin_mutating_run(workspace):
        assert rc.grant_unit_invocation("s1") is None                # granted under this run's own ledger
        assert rc.resume_work_ledger(first.run_id) is None
        assert rc.grant_unit_invocation("s1") is None                # the seventh
        assert (rc.grant_unit_invocation("s1") or "").startswith(f"{WORK_CEILING_EXHAUSTED}:")


def test_a_unit_granted_before_the_continuation_took_it_past_its_ceiling_does_no_developer_work(workspace, clock):
    """A unit of a larger plan may be granted under the run's own ledger before another unit's checkpoint makes the run continue the earlier ledger: the count is
    kept (never dropped), and a unit that is past its ceiling afterwards is refused its Developer attempt."""
    with rc.begin_mutating_run(workspace) as first:
        for _ in range(UNIT_INVOCATION_CEILING):
            assert rc.grant_unit_invocation("s1") is None
    with rc.begin_mutating_run(workspace):
        assert rc.grant_unit_invocation("s1") is None                # the eighth, granted before the run knew what it continues
        assert rc.resume_work_ledger(first.run_id) is None
        assert _ledger(workspace)["unit_invocations"] == {"s1": UNIT_INVOCATION_CEILING + 1}
        assert (rc.grant_developer_attempt(10, "s1") or "").startswith(f"{WORK_CEILING_EXHAUSTED}:")
        assert _ledger(workspace)["developer_attempts"] == 0


# =========================================================================== a ledger that cannot be continued refuses the resume

def _earlier_run(workspace: str) -> str:
    with rc.begin_mutating_run(workspace) as first:
        rc.claim_run_generation_clock()
        assert rc.grant_unit_invocation("s1") is None
    return first.run_id


@pytest.mark.parametrize("damage, reason", [
    ("missing", WORK_LEDGER_MISSING), ("not_json", WORK_LEDGER_UNREADABLE), ("bad_count", WORK_LEDGER_UNREADABLE),
    ("bad_schema", WORK_LEDGER_UNREADABLE), ("other_work", WORK_LEDGER_NOT_THIS_WORK),
])
def test_a_missing_corrupt_or_foreign_ledger_refuses_the_resume_and_the_run_is_granted_nothing(workspace, clock, damage, reason):
    """Owner ruling 2026-11-17: a requested resume continues the authoritative ledger or fails closed. Pre-fix (MEASURED on the uncommitted S8 tree): the refusal
    code was returned, the run then worked under its own NEW ledger (``runs == [resumed]``) - a fresh hour, fresh attempts and an empty family history."""
    first_id = _earlier_run(workspace)
    path = Path(work_ledger_path(workspace))
    document = json.loads(path.read_text())
    if damage == "missing":
        path.unlink()
    elif damage == "not_json":
        path.write_text("{")
    else:
        document.update({"bad_count": {"unit_invocations": {"s1": -3}}, "bad_schema": {"schema_version": 99},
                         "other_work": {"runs": ["another-run"]}}[damage])
        path.write_text(json.dumps(document))
    found = path.read_bytes() if path.exists() else None
    with rc.begin_mutating_run(workspace):
        assert rc.resume_work_ledger(first_id) == reason
        assert rc.resume_work_ledger(first_id) == reason and rc.resume_work_ledger(None, parent_found=False) == reason, "one decision for the whole run"
        assert not rc.work_ledger_resumed()
        for refusal in (rc.grant_unit_invocation("s1"), rc.grant_developer_attempt(10.0, "s1"), rc.begin_generation_invocation()[1]):
            assert refusal is not None and refusal.startswith(f"{WORK_LEDGER_RESUME_REFUSED}:") and reason in refusal
        rc.record_unit_retry_state("s1", {"seen_failure_signatures": []})
    assert (path.read_bytes() if path.exists() else None) == found, "a refused resume writes no ledger: what was found is exactly what is left"


def test_a_refused_resume_puts_back_the_ledger_its_run_had_displaced(workspace, clock):
    """A run may grant before it reaches its resume decision (its new ledger carries the displaced one). When that decision is a refusal the earlier ledger is the
    workspace's ledger again, with this run's charge added - the earlier work loses nothing, and the refused run gets no further grant."""
    _earlier_run(workspace)
    before = _ledger(workspace)
    with rc.begin_mutating_run(workspace) as refused:
        assert rc.grant_unit_invocation("s1") is None
        assert _ledger(workspace)["runs"] == [refused.run_id]
        assert rc.resume_work_ledger("another-run") == WORK_LEDGER_NOT_THIS_WORK
        assert (rc.grant_unit_invocation("s1") or "").startswith(f"{WORK_LEDGER_RESUME_REFUSED}:")
    after = _ledger(workspace)
    assert after["ledger_id"] == before["ledger_id"] and after["runs"] == before["runs"] and after["previous"] is None
    assert after["unit_invocations"] == {"s1": before["unit_invocations"]["s1"] + 1}
    assert after["consumed_seconds"] >= before["consumed_seconds"]


def test_a_resume_with_no_earlier_work_to_reuse_still_continues_the_ledger(workspace, clock):
    """Lost work is not a refund: with nothing identified to reuse (no checkpoint, no ControlState) the resume continues what the workspace's ledger consumed; and
    earlier work that is then identified as another run's refuses the resume without leaving this run on the ledger."""
    with rc.begin_mutating_run(workspace) as first:
        rc.claim_run_generation_clock()
        clock.now += 900
        assert rc.grant_developer_attempt(10) is None
    with rc.begin_mutating_run(workspace) as resumed:
        assert rc.resume_work_ledger(None, parent_found=False) is None
        assert rc.resume_work_ledger(None, parent_found=False) is None, "asked again by a nested invocation: the run's one decision stands"
        assert _elapsed() == pytest.approx(900) and rc.work_ledger_resumed()
        assert rc.resume_work_ledger(first.run_id) is None, "the checkpoint read afterwards names a run of the continued ledger"
        assert _elapsed() == pytest.approx(900), "nothing is added twice"
    assert _ledger(workspace)["runs"] == [first.run_id, resumed.run_id] and _ledger(workspace)["developer_attempts"] == 1
    with rc.begin_mutating_run(workspace):
        assert rc.resume_work_ledger(None, parent_found=False) is None
        assert rc.resume_work_ledger("another-run") == WORK_LEDGER_NOT_THIS_WORK
        assert (rc.grant_developer_attempt(10) or "").startswith(f"{WORK_LEDGER_RESUME_REFUSED}:")
    document = _ledger(workspace)
    assert document["runs"] == [first.run_id, resumed.run_id] and document["developer_attempts"] == 1 and document["consumed_seconds"] >= 900


def test_a_ledger_document_is_all_or_nothing():
    good = WorkLedger.new("r1").to_dict()
    assert WorkLedger.from_dict(good).to_dict() == good
    for field, bad in (("consumed_seconds", -1), ("consumed_seconds", float("nan")), ("developer_attempts", True), ("runs", []),
                       ("unit_invocations", {"s1": "2"}), ("open_grant", {"kind": "anything"}), ("retry", {"s1": 3}), ("ledger_id", "")):
        with pytest.raises(WorkLedgerError):
            WorkLedger.from_dict({**good, field: bad})


# =========================================================================== retry-family continuity (C-07)

SIGNATURE_A = ("test", (("shop/a.py",), "AssertionError", ("assert a() == 1",)))
SIGNATURE_B = ("compile", (("shop/b.py",), "javac", ("cannot find symbol",), (("symbol", "method x()"),)))


def _charged() -> RetryBudgets:
    budgets = RetryBudgets()
    budgets.seen_failure_signatures = {SIGNATURE_A, SIGNATURE_B}
    budgets.last_failure_signature = SIGNATURE_B
    budgets.targeted_retry_count, budgets.api_contract_recovery_count = 3, 1
    budgets.fallback_targeted_attempted, budgets.fallback_targeted_requested = True, False
    return budgets


def test_a_units_next_invocation_continues_its_family_history_and_scoped_charges():
    recorded = json.loads(json.dumps(retry_policy.scoped_allowance_state(_charged())))     # through the ledger's JSON
    fresh = RetryBudgets()
    assert retry_policy.continue_scoped_allowance(fresh, recorded) is True
    assert fresh.seen_failure_signatures == {SIGNATURE_A, SIGNATURE_B} and fresh.inherited_failure_signature == SIGNATURE_B
    # the new invocation is NOT marked as already repairing: its own "previous attempt" is still none (a carried last_failure_signature made the first
    # attempt of a resumed direct run unsendable - CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE, found by the pipeline test below)
    assert fresh.last_failure_signature is None
    assert retry_policy.scoped_allowance_state(fresh)["last_failure_signature"] == recorded["last_failure_signature"], "re-recorded unchanged before any failure"
    assert (fresh.targeted_retry_count, fresh.api_contract_recovery_count, fresh.fallback_targeted_attempted) == (3, 1, True)
    assert fresh.retry_count == 0, "the full-set counter stays per invocation"
    # the family the unit already charged against is not new again; a genuinely different one still is
    assert retry_policy.observe_failure_family(fresh, SIGNATURE_B, SIGNATURE_A) is False
    assert retry_policy.observe_failure_family(fresh, SIGNATURE_A, ("test", "a third failure")) is True


def test_nothing_recorded_starts_as_before():
    fresh = RetryBudgets()
    assert retry_policy.continue_scoped_allowance(fresh, None) is False and fresh == RetryBudgets()


@pytest.mark.parametrize("damage", ["missing_key", "bad_counter", "unhashable"])
def test_an_unreadable_record_leaves_no_scoped_allowance_never_a_full_grant(damage):
    recorded = json.loads(json.dumps(retry_policy.scoped_allowance_state(_charged())))
    if damage == "missing_key":
        recorded.pop("seen_failure_signatures")
    elif damage == "bad_counter":
        recorded["targeted_retry_count"] = "3"
    else:
        recorded["last_failure_signature"] = {"l": ["a list is not a signature"]}
    fresh = RetryBudgets()
    assert retry_policy.continue_scoped_allowance(fresh, recorded) is True
    assert fresh.scoped_allowance_unaccounted is True and fresh.fallback_targeted_attempted is True

    class _State:
        budgets = fresh
        last_implicated_files, last_missing_files, api_contract_recovery = ["shop/a.py"], [], None
    inputs = retry_policy._state_inputs(_State(), max_retries=4, targeted_max_retries=3, has_fallback_model=False)
    assert inputs["targeted_retry_count"] >= inputs["targeted_max_retries"], "no targeted allowance is left"
    assert retry_policy.observe_failure_family(fresh, SIGNATURE_A, SIGNATURE_B) is False, "no family can be shown to be new"


def test_the_ledger_carries_a_units_retry_state_across_a_resume(workspace, clock):
    state = retry_policy.scoped_allowance_state(_charged())
    with rc.begin_mutating_run(workspace) as first:
        assert rc.grant_unit_invocation("s1") is None
        rc.record_unit_retry_state("s1", state)
    with rc.begin_mutating_run(workspace):
        assert rc.unit_retry_state("s1") is None, "a new run has no history"
        assert rc.resume_work_ledger(first.run_id) is None
        resumed = RetryBudgets()
        assert retry_policy.continue_scoped_allowance(resumed, rc.unit_retry_state("s1")) is True
        assert resumed.seen_failure_signatures == {SIGNATURE_A, SIGNATURE_B} and resumed.targeted_retry_count == 3


# =========================================================================== C-08: the budget value has one owner (P6, inverted)

@pytest.fixture
def _authority_home(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(tmp_path / "_authority_home"))
    monkeypatch.delenv("KRIYA_TRUST_FILE", raising=False)


def _repository_config(tmp_path, monkeypatch, value) -> None:
    ws = tmp_path / "repo"
    ws.mkdir()
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    (ws / "kriya.yaml").write_text(yaml.safe_dump({"autonomy": {"generation_time_budget_seconds": value}}))
    monkeypatch.chdir(ws)


def _denied() -> Any:
    try:
        load_config(None)
    except ConfigAuthorityError as error:
        return sorted(v.field_path for v in error.violations)
    return None


def test_the_ceiling_is_one_constant_shared_with_the_production_seal():
    assert authority.GENERATION_TIME_BUDGET_CEILING_SECONDS == 3600
    assert resolution.PRODUCTION_GENERATION_TIME_BUDGET_SECONDS is authority.GENERATION_TIME_BUDGET_CEILING_SECONDS
    assert authority._VALUE_BOUNDED_FIELDS[("autonomy", "generation_time_budget_seconds")] is authority.GENERATION_TIME_BUDGET_CEILING_SECONDS
    assert ("autonomy", "generation_time_budget_seconds") not in authority._REPOSITORY_SAFE_FIELDS
    assert authority.classify_field("autonomy", "generation_time_budget_seconds") is authority.FieldClassification.SECURITY_AUTHORITY


@pytest.mark.parametrize("value", [1, 2400, 3600])
def test_a_repository_may_lower_the_run_time_bound_without_approval(tmp_path, monkeypatch, _authority_home, value):
    assert authority.value_gated_field_classification("autonomy", "generation_time_budget_seconds", value) is authority.FieldClassification.REPOSITORY_SAFE
    _repository_config(tmp_path, monkeypatch, value)
    assert resolve_config_state().violations == [] and _denied() is None
    assert load_config(None).autonomy.generation_time_budget_seconds == value


@pytest.mark.parametrize("value", [3601, 999_999, None, 0, -1, True, "3600", 3600.0], ids=["3601", "999999", "null", "zero", "negative", "bool", "string", "float"])
def test_a_repository_cannot_raise_or_remove_the_run_time_bound(tmp_path, monkeypatch, _authority_home, value):
    assert authority.value_gated_field_classification("autonomy", "generation_time_budget_seconds", value) is authority.FieldClassification.SECURITY_AUTHORITY
    _repository_config(tmp_path, monkeypatch, value)
    assert _denied() == ["autonomy.generation_time_budget_seconds"], "pre-fix: None (no approval needed) for 999999 and null"


def test_the_operators_durable_approval_makes_a_raise_effective(tmp_path, monkeypatch, _authority_home):
    _repository_config(tmp_path, monkeypatch, 7200)
    state = resolve_config_state()
    aa.save_approval_artifact(aa.default_local_approval_path(state.workspace_root),
                              aa.build_approval_artifact(state.violations, state.config_dict, state.workspace_root))
    assert _denied() is None and load_config(None).autonomy.generation_time_budget_seconds == 7200


# =========================================================================== the direct pipeline: one ledger across a failed run and its resume

def _direct(tmp_path, workspace, developer, calls: List[str], *, resume: bool, configure=None, seen=None):
    from _scripted_run import engine

    from kriya.workflow.workflow_controller import WorkflowController

    eng, llm, _cfg = engine(chain=False, configure=configure)
    kg._role_script(llm, developer, calls)
    if seen is not None:   # every model call of the run, whatever the role
        scripted, seen["model_calls"] = llm.complete, 0

        async def counted(*args, **kwargs):
            seen["model_calls"] += 1
            return await scripted(*args, **kwargs)
        llm.complete = counted
    result = asyncio.run(WorkflowController(eng).execute(
        goal=kg.GOAL, workspace_path=workspace, approval_callback=MagicMock(return_value=True), migration_mode="legacy", resume=resume))
    return result.legacy_result if hasattr(result, "legacy_result") else result


def _direct_workspace(tmp_path) -> str:
    from _scripted_run import git_workspace

    return git_workspace(tmp_path, {"calc.py": kg.BASE, "test_calc.py": kg.TESTS})


def test_a_failed_direct_run_and_its_resume_share_one_ledger(tmp_path):
    with kg.isolated_working_directory(tmp_path):
        workspace = _direct_workspace(tmp_path)
        first_calls: List[str] = []
        first = _direct(tmp_path, workspace, lambda m, n, t: kg._oscillating(n), first_calls, resume=False)
        assert first.get("quality_gates_passed") is not True and first_calls
        after_first = _ledger(workspace)
        assert after_first["unit_invocations"] == {"direct": 1} and after_first["developer_attempts"] == len(first_calls)
        assert after_first["open_grant"] is None and after_first["consumed_seconds"] > 0
        # every charged failure recorded the unit's family history
        assert after_first["retry"]["direct"]["seen_failure_signatures"] and after_first["retry"]["direct"]["last_failure_signature"] is not None

        resumed_calls: List[str] = []
        _direct(tmp_path, workspace, lambda m, n, t: kg.GOOD, resumed_calls, resume=True)
        document = _ledger(workspace)
        assert len(document["runs"]) == 2 and document["unit_invocations"] == {"direct": 2}
        assert document["developer_attempts"] == len(first_calls) + len(resumed_calls)
        assert document["consumed_seconds"] >= after_first["consumed_seconds"]
        records = {record.run_id: record for record in list_run_records(workspace)}
        resumed_record = records[document["runs"][1]]
        assert resumed_record.work_ledger["resumed_from"] == document["runs"][0] and resumed_record.work_ledger["ledger_id"] == document["ledger_id"]
        assert records[document["runs"][0]].work_ledger["developer_attempts"] == len(first_calls)


def test_a_resumed_direct_run_continues_the_units_family_history(tmp_path):
    """The resumed run fails once more before it succeeds: what it records then holds the earlier run's families too (its budgets were continued, not reset)."""
    with kg.isolated_working_directory(tmp_path):
        workspace = _direct_workspace(tmp_path)
        _direct(tmp_path, workspace, lambda m, n, t: kg._oscillating(n), [], resume=False)
        earlier = _ledger(workspace)["retry"]["direct"]
        assert len(earlier["seen_failure_signatures"]) >= 2, "the oscillating run met two families"
        resumed_calls: List[str] = []
        _direct(tmp_path, workspace, lambda m, n, t: kg.BROKEN if n == 1 else kg.GOOD, resumed_calls, resume=True)
        assert len(resumed_calls) >= 2, "the resumed run failed once and repaired"
        later = _ledger(workspace)["retry"]["direct"]
        for signature in earlier["seen_failure_signatures"]:
            assert signature in later["seen_failure_signatures"], "pre-fix shape: a new invocation started from an empty family history"


@pytest.mark.parametrize("damage, reason", [
    ("missing", WORK_LEDGER_MISSING), ("corrupt", WORK_LEDGER_UNREADABLE), ("inconsistent", WORK_LEDGER_UNREADABLE), ("other_work", WORK_LEDGER_NOT_THIS_WORK),
])
def test_a_direct_resume_whose_ledger_cannot_be_continued_is_refused_before_any_work(tmp_path, damage, reason):
    """Owner ruling 2026-11-17 (negatives 1, 2 and 4). Pre-fix (MEASURED on the uncommitted S8 tree): a deleted ledger let the resume run under a NEW ledger - a
    fresh hour, fresh attempts, an empty family history - and a corrupt or foreign one was logged and the run generated everything again as a fresh run."""
    with kg.isolated_working_directory(tmp_path):
        workspace = _direct_workspace(tmp_path)
        first_calls: List[str] = []
        _direct(tmp_path, workspace, lambda m, n, t: kg._oscillating(n), first_calls, resume=False)
        path = Path(work_ledger_path(workspace))
        document = json.loads(path.read_text())
        if damage == "missing":
            path.unlink()
        elif damage == "corrupt":
            path.write_text("{")
        else:
            document.update({"inconsistent": {"developer_attempts": -1}, "other_work": {"runs": ["some-other-run"]}}[damage])
            path.write_text(json.dumps(document))
        found = path.read_bytes() if path.exists() else None
        runs_before = {record.run_id for record in list_run_records(workspace)}
        seen: Dict[str, Any] = {}
        resumed_calls: List[str] = []
        resumed = _direct(tmp_path, workspace, lambda m, n, t: kg.GOOD, resumed_calls, resume=True, seen=seen)
        assert resumed["status"] == "resume_refused" and resumed["reason_codes"] == [reason] and resumed["quality_gates_passed"] is False
        assert resumed["files"] == [] and resumed["error"].startswith(f"{reason}: resume refused")
        assert resumed_calls == [] and seen["model_calls"] == 0, "no Developer, Planner or Architect call"
        if damage == "other_work":
            # the run was counted when its state opened (before the checkpoint named its run); the ledger it found is the workspace's ledger again, never less
            after = _ledger(workspace)
            assert after["runs"] == ["some-other-run"] and after["developer_attempts"] == document["developer_attempts"]
            assert after["consumed_seconds"] >= document["consumed_seconds"] and after["retry"] == document["retry"]
        else:
            assert (path.read_bytes() if path.exists() else None) == found, "nothing was written: no new ledger, the damaged one untouched"
        refused_run = [record for record in list_run_records(workspace) if record.run_id not in runs_before]
        assert len(refused_run) == 1 and refused_run[0].resume_decision is None

        # negative 4: a NEW run on the same workspace state is legal and gets its own ledger and allowance
        fresh_calls: List[str] = []
        fresh = _direct(tmp_path, workspace, lambda m, n, t: kg.GOOD, fresh_calls, resume=False)
        # (the scripted reviewer closes no requirement, so the S6 result contract reports the applied change as ``applied_requirements_open``)
        assert fresh["status"] in ("success", "applied_requirements_open") and fresh_calls, {k: fresh.get(k) for k in ("status", "failure_category", "error")}
        assert "def mul" in (Path(workspace) / "calc.py").read_text(), "the new run did its work and applied it"
        new_ledger = _ledger(workspace)
        assert len(new_ledger["runs"]) == 1 and new_ledger["runs"][0] not in runs_before
        assert new_ledger["unit_invocations"] == {"direct": 1} and new_ledger["developer_attempts"] == len(fresh_calls)


def test_a_direct_resume_with_no_checkpoint_left_recomputes_under_the_same_ledger(tmp_path):
    """Negative 5 on the direct shape: the earlier run's checkpoint is gone but its ledger is intact - the work is generated again and nothing is refunded."""
    from kriya.control.checkpoint import checkpoint_path, list_checkpoints

    with kg.isolated_working_directory(tmp_path):
        workspace = _direct_workspace(tmp_path)
        first_calls: List[str] = []
        _direct(tmp_path, workspace, lambda m, n, t: kg._oscillating(n), first_calls, resume=False)
        before = _ledger(workspace)
        checkpoints = list_checkpoints(workspace)
        assert checkpoints, "the failed run left a checkpoint"
        for item in checkpoints:
            Path(checkpoint_path(workspace, item["run_id"])).unlink()
        assert not list_checkpoints(workspace)
        resumed_calls: List[str] = []
        _direct(tmp_path, workspace, lambda m, n, t: kg.GOOD, resumed_calls, resume=True)
        after = _ledger(workspace)
        assert resumed_calls, "the work was generated again"
        assert after["runs"][0] == before["runs"][0] and len(after["runs"]) == 2 and after["ledger_id"] == before["ledger_id"]
        assert after["unit_invocations"] == {"direct": 2} and after["developer_attempts"] == len(first_calls) + len(resumed_calls)
        assert after["consumed_seconds"] >= before["consumed_seconds"]
        for signature in before["retry"]["direct"]["seen_failure_signatures"]:
            assert signature in after["retry"]["direct"]["seen_failure_signatures"]


def test_an_invocation_the_ledger_refused_does_no_developer_work():
    """The invocation's own refusal (set when its unit had no invocation left) stops at the one place Developer work is admitted."""
    from kriya.config import AppConfig
    from kriya.core.kernel import Kernel
    from kriya.workflow.attempt import _ensure_generation_time_budget
    from kriya.workflow.failure import QualityGateFailure
    from kriya.workflow.state import GenerationState

    context = SimpleNamespace(kernel=Kernel(config=AppConfig()), developer=None, expected_files_upfront=["a.py"], worktree_path=None, workspace_path=None)
    state = GenerationState()
    _ensure_generation_time_budget(state, context, file_count=1)                       # granted (outside a run: nothing to refuse)
    state.work_refusal = f"{WORK_CEILING_EXHAUSTED}: unit 'direct' has used all 7 invocations its work ledger grants"
    with pytest.raises(QualityGateFailure) as refused:
        _ensure_generation_time_budget(state, context, file_count=1)
    assert refused.value.failure.type == "work_ceiling_exhausted" and refused.value.failure.message == state.work_refusal


def test_a_resume_after_the_budget_was_consumed_has_the_same_remaining_budget_none(tmp_path):
    def bounded(cfg):
        cfg.autonomy.generation_time_budget_seconds = 3600

    with kg.isolated_working_directory(tmp_path):
        workspace = _direct_workspace(tmp_path)
        first_calls: List[str] = []
        _direct(tmp_path, workspace, lambda m, n, t: kg._oscillating(n), first_calls, resume=False, configure=bounded)
        path = Path(work_ledger_path(workspace))
        document = json.loads(path.read_text())
        document["consumed_seconds"] = 3599.0                        # the earlier run had used all but a second of the hour
        path.write_text(json.dumps(document))
        resumed_calls: List[str] = []
        resumed = _direct(tmp_path, workspace, lambda m, n, t: kg.GOOD, resumed_calls, resume=True, configure=bounded)
        assert resumed_calls == [], "pre-fix: the resumed run had a fresh hour and called the Developer"
        assert resumed.get("quality_gates_passed") is not True
        assert resumed.get("failure_category") == "generation_budget_exhausted", {k: resumed.get(k) for k in ("status", "failure_category", "environment_failure")}


def test_the_direct_unit_ceiling_stops_typed_before_any_developer_work(tmp_path):
    with kg.isolated_working_directory(tmp_path):
        workspace = _direct_workspace(tmp_path)
        first_calls: List[str] = []
        _direct(tmp_path, workspace, lambda m, n, t: kg._oscillating(n), first_calls, resume=False)
        path = Path(work_ledger_path(workspace))
        document = json.loads(path.read_text())
        document["unit_invocations"] = {"direct": UNIT_INVOCATION_CEILING}
        path.write_text(json.dumps(document))
        resumed_calls: List[str] = []
        resumed = _direct(tmp_path, workspace, lambda m, n, t: kg.GOOD, resumed_calls, resume=True)
        assert resumed_calls == []
        assert resumed.get("failure_category") == "work_ceiling_exhausted", {k: resumed.get(k) for k in ("status", "failure_category", "environment_failure")}
        assert str(resumed.get("environment_failure") or "").startswith(f"{WORK_CEILING_EXHAUSTED}:")
        # the resume continues the ledger before the run's state opens, so the eighth invocation is refused where it is asked for and is never counted
        assert _ledger(workspace)["unit_invocations"] == {"direct": UNIT_INVOCATION_CEILING}
        assert _ledger(workspace)["developer_attempts"] == len(first_calls)


# =========================================================================== the enforce plan: the unit funnel grants before it invokes

def _enforce_plan() -> EngineeringPlan:
    return EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}], "acceptance_criteria": [],
        "subtasks": [{"id": "s1", "description": "write shop/a.py", "execution_method": "model", "planned_files": [{"path": "shop/a.py", "action": "create"}],
                      "relevant_global_invariant_ids": ["gi1"], "verification": [{"type": "tool", "tool_name": "test", "description": "test"}]}]})


def test_an_enforce_unit_is_granted_on_the_ledger_and_its_attempts_are_attributed_to_it(tmp_path):
    run = run_enforce(tmp_path, plan=_enforce_plan(), goal="Write a", mode="guardrails", units={"s1": ("shop/a.py", "def a():\n    return 1\n")},
                      base_files={"shop/__init__.py": ""})
    document = _ledger(str(run.workspace))
    assert document["unit_invocations"] == {"s1": 1}, "counted once: by the plan's unit funnel, not again by the nested generation"
    assert document["developer_attempts"] == run.developer_calls.count("s1") == 1 and document["open_grant"] is None


def test_an_enforce_unit_at_its_ceiling_is_not_invoked_again_after_a_resume(tmp_path):
    failing = {"success": False, "output": "shop/test_a.py F\nE   AssertionError: assert a() == 1\nshop/a.py:2: AssertionError\n1 failed"}
    first = run_enforce(tmp_path, plan=_enforce_plan(), goal="Write a", mode="guardrails", units={"s1": ("shop/a.py", "def a():\n    return 0\n")},
                        base_files={"shop/__init__.py": ""}, on_tests=lambda validator: failing)
    assert first.developer_calls
    path = Path(work_ledger_path(str(first.workspace)))
    document = json.loads(path.read_text())
    used = document["unit_invocations"]["s1"]
    assert 1 <= used <= UNIT_INVOCATION_CEILING, "the loop's own caps stay inside the ceiling"
    document["unit_invocations"]["s1"] = UNIT_INVOCATION_CEILING
    path.write_text(json.dumps(document))
    resumed = run_enforce(tmp_path, plan=_enforce_plan(), goal="Write a", mode="guardrails", units={"s1": ("shop/a.py", "def a():\n    return 1\n")},
                          on_tests=lambda validator: PASSING, existing_workspace=first.workspace, execute_kwargs={"resume": True})
    assert resumed.developer_calls == [], "the unit had no invocation left: nothing was generated"
    legacy = resumed.result.legacy_result
    assert legacy["status"] != "success" and not (resumed.workspace / "shop/a.py").exists()
    assert WORK_CEILING_EXHAUSTED in json.dumps(legacy, default=str)
    assert _ledger(str(resumed.workspace))["unit_invocations"]["s1"] == UNIT_INVOCATION_CEILING
