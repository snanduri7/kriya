"""Wave 2 Slice A, stage 4: the operator's disposition of a TOOL subtask (kriya/control/tool_disposition.py).

inspect / abandon / retry-as-new-attempt change CONTROL STATE ONLY. None executes an effect and none reaches ``completed``; a retry re-arms and the effect runs later through the
ordinary resume path (and its own fresh authority). The sequence is lock -> reload -> validate -> durable ledger record -> durable state -> unlock, restart-safe and idempotent."""
import json
import os
import subprocess
import sys
from unittest.mock import AsyncMock, patch

import pytest
from test_d1_tool_lifecycle import DONE, Faults, _plan, _state
from test_e12_enforce_characterization import C, _enforce, _enforce_engine

from kriya.control import tool_disposition as td
from kriya.control.decisions import load_decision_ledger
from kriya.control.persistence import control_state_path, decision_ledger_path, load_control_state, save_control_state
from kriya.control.run_ownership import WorkspaceLockHeldError, acquire_run_lock
from kriya.control.state import (
    SUBTASK_STATE_TOOL_ABANDONED,
    SUBTASK_STATE_TOOL_FAILED,
    SUBTASK_STATE_TOOL_STARTED,
    ControlState,
)
from kriya.repository import atomic_write
from kriya.tools.tool import ToolEffectBoundary
from kriya.workflow.workflow_types import SubtaskResult, SubtaskStatus

MAY_FAILED = SubtaskResult(subtask_id="t1", status=SubtaskStatus.FAILED, execution_method="tool", error="died after the effect began",
                           effect_boundary=ToolEffectBoundary.MAY_HAVE_EFFECTED)
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _seed(tmp_path, states, attempts):
    save_control_state(str(tmp_path), ControlState(schema_version=1, run_id="r1", subtask_states=dict(states), tool_attempts=dict(attempts), current_plan_hash="ph"))


def _apply(tmp_path, action, subtask="t1", attempt=1, confirmed=True):
    return td.apply_disposition(str(tmp_path), action, subtask, expected_attempt=attempt, operator="op", confirmed=confirmed)


def _dispositions(tmp_path):
    return load_decision_ledger(str(tmp_path)).filter_by_type(td.DECISION_TYPE)


def _bytes(tmp_path):
    ledger = decision_ledger_path(str(tmp_path))
    return open(control_state_path(str(tmp_path)), "rb").read(), (open(ledger, "rb").read() if os.path.isfile(ledger) else b"")


def test_inspect_lists_only_positively_identified_tool_records_and_changes_nothing(tmp_path):
    _seed(tmp_path, {"a": SUBTASK_STATE_TOOL_STARTED, "b": SUBTASK_STATE_TOOL_FAILED, "c": SUBTASK_STATE_TOOL_ABANDONED, "d": "failed", "e": "needs_review",
                     "f": "completed", "g": "in_progress", "h": "pending"}, {"b": 2, "c": 1})
    before = _bytes(tmp_path)
    report = td.inspect_tool_dispositions(str(tmp_path))
    assert [(e.subtask_id, e.state, e.attempt, e.legacy_unversioned, e.allowed_actions) for e in report.entries] == [
        ("a", SUBTASK_STATE_TOOL_STARTED, None, True, ("abandon",)),                  # legacy/unversioned: never silently attempt 1, abandon only
        ("b", SUBTASK_STATE_TOOL_FAILED, 2, False, ("abandon", "retry")),
        ("c", SUBTASK_STATE_TOOL_ABANDONED, 1, False, ("retry",))]
    assert _bytes(tmp_path) == before                                                   # read-only


def test_inspect_reports_an_unreadable_state_and_an_absent_one_without_inventing_anything(tmp_path):
    assert td.inspect_tool_dispositions(str(tmp_path)).entries == ()
    _seed(tmp_path, {"t1": SUBTASK_STATE_TOOL_FAILED}, {"t1": 1})
    open(control_state_path(str(tmp_path)), "w", encoding="utf-8").write("{not json")
    report = td.inspect_tool_dispositions(str(tmp_path))
    assert report.entries == () and report.unreadable_control_state is not None


def test_abandon_is_a_terminal_disposition_distinct_from_completed_and_needs_confirmation(tmp_path):
    _seed(tmp_path, {"t1": SUBTASK_STATE_TOOL_FAILED}, {"t1": 1})
    before = _bytes(tmp_path)
    refused = _apply(tmp_path, "abandon", confirmed=False)
    assert refused.status == td.STATUS_REFUSED and refused.reason_code == td.CONFIRMATION_REQUIRED and _bytes(tmp_path) == before
    done = _apply(tmp_path, "abandon")
    state = load_control_state(str(tmp_path))
    assert done.status == td.STATUS_APPLIED and state.subtask_states["t1"] == SUBTASK_STATE_TOOL_ABANDONED != "completed" and state.tool_attempts == {"t1": 1}
    (record,) = _dispositions(tmp_path)
    assert record.fields["action"] == "abandon" and record.fields["prior_state"] == SUBTASK_STATE_TOOL_FAILED and record.fields["from_attempt"] == 1
    assert record.fields["operator"] == "op" and record.fields["prior_state_digest"]


def test_retry_rearms_as_a_new_attempt_n_plus_1_and_preserves_attempt_n(tmp_path):
    _seed(tmp_path, {"t1": SUBTASK_STATE_TOOL_FAILED}, {"t1": 1})
    done = _apply(tmp_path, "retry")
    state = load_control_state(str(tmp_path))
    assert done.status == td.STATUS_APPLIED and (done.from_attempt, done.to_attempt) == (1, 2)
    assert state.subtask_states["t1"] == "pending" and state.tool_attempts == {"t1": 2}          # re-armed, NOT completed, NOT executed
    (record,) = _dispositions(tmp_path)
    assert (record.fields["from_attempt"], record.fields["to_attempt"], record.fields["prior_state"]) == (1, 2, SUBTASK_STATE_TOOL_FAILED)   # attempt 1 stays on record


def test_a_second_identical_retry_is_idempotent_and_never_creates_a_second_new_attempt(tmp_path):
    _seed(tmp_path, {"t1": SUBTASK_STATE_TOOL_FAILED}, {"t1": 1})
    _apply(tmp_path, "retry")
    again = _apply(tmp_path, "retry")                                                             # same command, same inspected attempt
    assert again.status == td.STATUS_ALREADY_APPLIED
    assert load_control_state(str(tmp_path)).tool_attempts == {"t1": 2} and len(_dispositions(tmp_path)) == 1


@pytest.mark.parametrize("state_value", [SUBTASK_STATE_TOOL_STARTED, SUBTASK_STATE_TOOL_FAILED])
def test_neither_operator_edge_ever_reaches_completed(tmp_path, state_value):
    for action in td.ACTIONS:
        _seed(tmp_path, {"t1": state_value}, {"t1": 1})
        _apply(tmp_path, action)
        assert load_control_state(str(tmp_path)).subtask_states["t1"] in (SUBTASK_STATE_TOOL_ABANDONED, "pending")


@pytest.mark.parametrize("state_value", ["completed", "failed", "needs_review", "pending", "in_progress"])
def test_a_subtask_that_is_not_awaiting_an_operator_is_refused_and_untouched(tmp_path, state_value):
    _seed(tmp_path, {"t1": state_value}, {"t1": 1})
    before = _bytes(tmp_path)
    for action in td.ACTIONS:
        result = _apply(tmp_path, action)
        assert result.status == td.STATUS_REFUSED and result.reason_code == td.SUBTASK_NOT_DISPOSABLE
    assert _bytes(tmp_path) == before


def test_a_stale_attempt_a_legacy_retry_and_an_unknown_subtask_are_refused(tmp_path):
    _seed(tmp_path, {"t1": SUBTASK_STATE_TOOL_FAILED, "old": SUBTASK_STATE_TOOL_STARTED}, {"t1": 3})
    before = _bytes(tmp_path)
    assert _apply(tmp_path, "retry", attempt=1).reason_code == td.STALE_ATTEMPT                    # inspected attempt 1, state is at 3
    assert _apply(tmp_path, "retry", subtask="old", attempt=None).reason_code == td.LEGACY_ATTEMPT_UNVERSIONED
    assert _apply(tmp_path, "retry", subtask="nope").reason_code == td.SUBTASK_NOT_DISPOSABLE
    assert _bytes(tmp_path) == before
    legacy = _apply(tmp_path, "abandon", subtask="old", attempt=None)                              # legacy resolves only by abandon-with-evidence
    assert legacy.status == td.STATUS_APPLIED and load_control_state(str(tmp_path)).subtask_states["old"] == SUBTASK_STATE_TOOL_ABANDONED
    assert "old" not in load_control_state(str(tmp_path)).tool_attempts                            # no attempt fabricated for it


def test_an_unreadable_control_state_and_a_held_workspace_lock_are_refused_not_worked_around(tmp_path):
    _seed(tmp_path, {"t1": SUBTASK_STATE_TOOL_FAILED}, {"t1": 1})
    with acquire_run_lock(td._canonical(str(tmp_path)), run_id="a-run"), pytest.raises(WorkspaceLockHeldError):
        _apply(tmp_path, "abandon")
    assert load_control_state(str(tmp_path)).subtask_states["t1"] == SUBTASK_STATE_TOOL_FAILED
    open(control_state_path(str(tmp_path)), "w", encoding="utf-8").write("{not json")
    assert _apply(tmp_path, "abandon").reason_code == td.CONTROL_STATE_UNREADABLE
    assert open(control_state_path(str(tmp_path)), encoding="utf-8").read() == "{not json"


@pytest.mark.parametrize("window", ["before_record", "between_record_and_state"])
def test_a_crash_at_either_step_leaves_exactly_one_coherent_disposition_after_a_rerun(tmp_path, window):
    _seed(tmp_path, {"t1": SUBTASK_STATE_TOOL_FAILED}, {"t1": 1})
    target = "kriya.control.decisions.DecisionLedger.append_to_file" if window == "before_record" else "kriya.control.tool_disposition.save_control_state"
    with patch(target, side_effect=OSError("crash")), pytest.raises(OSError):
        _apply(tmp_path, "retry")
    state = load_control_state(str(tmp_path))
    assert state.tool_attempts == {"t1": 1} and state.subtask_states["t1"] == SUBTASK_STATE_TOOL_FAILED, "the prior attempt must be intact after the crash"
    assert len(_dispositions(tmp_path)) == (0 if window == "before_record" else 1)
    assert _apply(tmp_path, "retry").status == td.STATUS_APPLIED                                   # rerun completes it
    state = load_control_state(str(tmp_path))
    assert state.tool_attempts == {"t1": 2} and state.subtask_states["t1"] == "pending"           # one retry, not two
    assert len(_dispositions(tmp_path)) == 1                                                        # one record, not two
    assert _apply(tmp_path, "retry").status == td.STATUS_ALREADY_APPLIED and load_control_state(str(tmp_path)).tool_attempts == {"t1": 2}


def test_a_genuine_process_kill_between_the_ledger_record_and_the_state_write_is_recoverable(tmp_path):
    """The child dies with os._exit (no finalizer) exactly after the durable record and before the state write; a fresh parent reloads from disk only."""
    _seed(tmp_path, {"t1": SUBTASK_STATE_TOOL_FAILED}, {"t1": 1})
    child = (
        "import os,sys\n"
        "from unittest.mock import patch\n"
        "from kriya.control import tool_disposition as td\n"
        "def die(*a, **k):\n    os._exit(137)\n"
        "with patch('kriya.control.tool_disposition.save_control_state', die):\n"
        f"    td.apply_disposition({str(tmp_path)!r}, 'retry', 't1', expected_attempt=1, operator='op', confirmed=True)\n"
    )
    done = subprocess.run([sys.executable, "-c", child], cwd=REPO_ROOT, capture_output=True, text=True, timeout=240, check=False,
                          env={**os.environ, "PYTHONPATH": REPO_ROOT})
    assert done.returncode == 137, done.stderr[-1500:]
    assert load_control_state(str(tmp_path)).tool_attempts == {"t1": 1} and len(_dispositions(tmp_path)) == 1   # the record is on disk, the state is not changed
    assert _apply(tmp_path, "retry").status == td.STATUS_APPLIED
    assert load_control_state(str(tmp_path)).tool_attempts == {"t1": 2} and len(_dispositions(tmp_path)) == 1


@pytest.mark.asyncio
async def test_end_to_end_retry_runs_nothing_itself_then_the_next_resume_executes_attempt_2_and_attempt_1_stays_on_record(tmp_path):
    seen = {}

    async def failing_effect(**kwargs):
        return MAY_FAILED

    with patch(f"{C}.subtask_executor.execute", new=AsyncMock(side_effect=failing_effect)):
        await _enforce(_enforce_engine(tmp_path), tmp_path, _plan())
    assert _state(tmp_path).subtask_states["t1"] == SUBTASK_STATE_TOOL_FAILED and _state(tmp_path).tool_attempts == {"t1": 1}

    never = AsyncMock(return_value=DONE)
    with patch(f"{C}.subtask_executor.execute", new=never):                                         # the retry COMMAND is run with the executor patched to fail the test
        assert _apply(tmp_path, "retry").status == td.STATUS_APPLIED
    never.assert_not_awaited()                                                                      # the retry command itself executed nothing

    async def second_attempt(**kwargs):
        seen["state"] = (_state(tmp_path).subtask_states.get("t1"), dict(_state(tmp_path).tool_attempts))
        return DONE

    effect = AsyncMock(side_effect=second_attempt)
    with patch(f"{C}.subtask_executor.execute", new=effect):
        result, calls = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan(), resume=True)
    assert effect.await_count == 1 and seen["state"] == (SUBTASK_STATE_TOOL_STARTED, {"t1": 2})       # attempt 2, on disk, before its effect
    assert result.legacy_result["status"] == "success" and [c["current_subtask_id"] for c in calls] == ["s2"]
    assert _state(tmp_path).subtask_states["t1"] == "completed" and _state(tmp_path).tool_attempts == {"t1": 2}
    history = [(d.type, d.fields.get("attempt"), d.fields.get("status")) for d in load_decision_ledger(str(tmp_path)).all() if d.fields.get("subtask_id") == "t1"]
    assert ("subtask_attempt", 1, "failed") in history and ("subtask_attempt", 2, "completed") in history and ("tool_disposition", None, None) in history
    assert [h[0] for h in history].index("tool_disposition") > history.index(("subtask_attempt", 1, "failed"))   # attempt 1 was recorded BEFORE the operator's decision


@pytest.mark.asyncio
async def test_a_retried_attempt_gets_fresh_authority_a_refusal_at_that_moment_re_arms_nothing_and_runs_no_effect(tmp_path):
    with patch(f"{C}.subtask_executor.execute", new=AsyncMock(return_value=MAY_FAILED)):
        await _enforce(_enforce_engine(tmp_path), tmp_path, _plan())
    _apply(tmp_path, "retry")
    denied = SubtaskResult(subtask_id="t1", status=SubtaskStatus.FAILED, execution_method="tool", reason_codes=("MCP_TOOL_REQUIRES_APPROVAL",),
                           error="approval revoked since", effect_boundary=ToolEffectBoundary.PROVEN_PRE_EFFECT)
    with patch(f"{C}.subtask_executor.execute", new=AsyncMock(return_value=denied)) as execute:
        result, calls = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan(), resume=True)
    assert execute.await_count == 1 and result.legacy_result["status"] != "success" and calls == []   # authority is decided at execution time, by the executor path
    assert _state(tmp_path).subtask_states["t1"] == "failed" and _state(tmp_path).tool_attempts == {"t1": 2}   # still attempt 2, not completed, no hidden grant


@pytest.mark.asyncio
async def test_an_abandoned_subtask_stops_the_run_and_the_plan_never_completes(tmp_path):
    with patch(f"{C}.subtask_executor.execute", new=AsyncMock(return_value=MAY_FAILED)):
        await _enforce(_enforce_engine(tmp_path), tmp_path, _plan())
    assert _apply(tmp_path, "abandon").status == td.STATUS_APPLIED
    effect = AsyncMock(return_value=DONE)
    with patch(f"{C}.subtask_executor.execute", new=effect):
        result, calls = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan(), resume=True)
    effect.assert_not_awaited()
    assert result.legacy_result["status"] != "success" and result.legacy_result["quality_gates_passed"] is False and calls == []
    assert json.loads(open(control_state_path(str(tmp_path)), encoding="utf-8").read())["subtask_states"]["t1"] == SUBTASK_STATE_TOOL_ABANDONED


def _spy_on_control_writes(monkeypatch):
    """Records (file name, durable) of every control-store write the two stores make."""
    seen = []
    from kriya.control import decisions, persistence
    for module in (decisions, persistence):
        real = module.write_control_file

        def spy(workspace_path, path, content, *, expected_revision, durable=False, _real=real, **rest):   # **rest: the writer's optional content_bytes (B-004: exact bytes)
            seen.append((os.path.basename(path), durable))
            return _real(workspace_path, path, content, expected_revision=expected_revision, durable=durable, **rest)

        monkeypatch.setattr(module, "write_control_file", spy)
    return seen


def test_the_operator_decision_and_the_new_state_are_written_fsync_grade(tmp_path, monkeypatch):
    _seed(tmp_path, {"t1": SUBTASK_STATE_TOOL_FAILED}, {"t1": 1})
    seen = _spy_on_control_writes(monkeypatch)
    _apply(tmp_path, "retry")
    assert ("decisions.jsonl", True) in seen and ("state.json", True) in seen
    assert [name for name, durable in seen if not durable] == [], "an authoritative disposition write was not fsync-grade"


@pytest.mark.asyncio
async def test_a_tool_attempts_outcome_reaches_the_ledger_fsync_grade_and_survives_a_reload(tmp_path, monkeypatch):
    seen = _spy_on_control_writes(monkeypatch)
    with patch(f"{C}.subtask_executor.execute", new=AsyncMock(return_value=MAY_FAILED)):
        await _enforce(_enforce_engine(tmp_path), tmp_path, _plan())
    assert ("decisions.jsonl", True) in seen
    attempts = [d.fields for d in load_decision_ledger(str(tmp_path)).filter_by_type("subtask_attempt") if d.fields.get("execution_method") == "tool"]
    assert attempts == [{**attempts[0], "subtask_id": "t1", "attempt": 1, "status": "failed", "effect_boundary": "may_have_effected", "error": "died after the effect began"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("step", atomic_write.DURABLE_WRITE_STEPS)
async def test_a_retry_whose_durable_evidence_write_fails_is_surfaced_re_arms_nothing_and_the_next_resume_executes_nothing(tmp_path, monkeypatch, step):
    """The REAL durable writer fails at each of its steps while the operator's retry record is written (not a patched method): attempt N stays authoritative on disk, nothing
    is re-armed as N+1, the next resume still executes nothing, and the very same retry later converges to exactly one attempt N+1."""
    with patch(f"{C}.subtask_executor.execute", new=AsyncMock(return_value=MAY_FAILED)):
        await _enforce(_enforce_engine(tmp_path), tmp_path, _plan())
    assert _state(tmp_path).subtask_states["t1"] == SUBTASK_STATE_TOOL_FAILED and _state(tmp_path).tool_attempts == {"t1": 1}
    Faults(monkeypatch, write=1, step=step)                       # durable write 1 of the retry = the decision-ledger record; write 2 would be the new state
    with pytest.raises(atomic_write.DurableWriteError) as raised:
        _apply(tmp_path, "retry")
    assert raised.value.step == step                              # surfaced, naming the step that failed
    monkeypatch.undo()
    reloaded = _state(tmp_path)                                   # a fresh read from disk: the pre-retry state
    assert reloaded.subtask_states["t1"] == SUBTASK_STATE_TOOL_FAILED and reloaded.tool_attempts == {"t1": 1}
    effect = AsyncMock(return_value=DONE)
    with patch(f"{C}.subtask_executor.execute", new=effect):
        result, calls = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan(), resume=True)
    effect.assert_not_awaited()                                   # nothing was re-armed, so nothing executes
    assert result.legacy_result["status"] != "success" and calls == []
    assert _state(tmp_path).subtask_states["t1"] == SUBTASK_STATE_TOOL_FAILED and _state(tmp_path).tool_attempts == {"t1": 1}
    assert _apply(tmp_path, "retry").status == td.STATUS_APPLIED  # the same command, once the store works, completes the one disposition
    assert _state(tmp_path).tool_attempts == {"t1": 2} and _state(tmp_path).subtask_states["t1"] == "pending" and len(_dispositions(tmp_path)) == 1
