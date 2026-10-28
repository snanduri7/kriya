"""Wave 2 W2-02: the lifecycle, recovery truth, operator disposition and result truth of a DIRECT `kriya tools execute` run.

RunRecord is the single authority for a direct run. A run durably STARTED and never resolved blocks every mutating run and is an OPERATOR matter: `kriya runs status` says
MANUAL_ACTION_REQUIRED (never CLEAN), `kriya runs recover` leaves it untouched, and `kriya runs tool-inspect|tool-abandon --run RUN_ID` is the only way past it
(audit decision first, then the RunRecord `abandoned` transition; the ledger is audit evidence only). There is no direct-run retry: a new `kriya tools execute` is a new run
id with fresh confirmation, policy and authority. The crash scenarios use the real child process that dies with os._exit(137) (tests/_d1_direct_tool_child.py)."""
import json
import os
import subprocess
import sys
from unittest.mock import patch

import pytest
from _d1_direct_tool_support import TOOL, lines, make_workspace
from click.testing import CliRunner
from test_d1_tool_lifecycle import Faults
from test_slice_a_tool_disposition import _seed
from test_tools_command import _cfg, _write_plugin

from kriya.cli import main
from kriya.context.workspace_identity import workspace_identity
from kriya.control import commit_state, recovery
from kriya.control import tool_disposition as td
from kriya.control.decisions import load_decision_ledger
from kriya.control.persistence import (
    list_run_records,
    load_control_state,
    load_run_record,
    run_record_path,
    save_run_record,
)
from kriya.control.run_record import IllegalRunTransitionError, RunLifecycle, RunRecord
from kriya.control.state import SUBTASK_STATE_TOOL_FAILED
from kriya.repository import atomic_write

CHILD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_d1_direct_tool_child.py")

PLUGIN = """
import os
from kriya.plugins.plugin import BasePlugin
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.model import ActionRequest, ActionType, PolicyDecision, PolicyResult
from kriya.tools.tool import BaseTool
from pydantic import BaseModel

class _NoArgs(BaseModel):
    pass

class _NeedsN(BaseModel):
    n: int

def _log(line):
    with open(os.environ["W202_LOG"], "a", encoding="utf-8") as handle:
        handle.write(line + "\\n")

class _Ok(BaseTool):
    name = "w202_ok"
    description = "succeeds"
    arguments_schema = _NoArgs
    async def _run(self, args):
        _log("EFFECT_BEGIN")
        return "OK-RESULT"

class _Fails(BaseTool):
    name = "w202_fail"
    description = "fails after its effect began"
    arguments_schema = _NoArgs
    async def _run(self, args):
        _log("EFFECT_BEGIN")
        raise RuntimeError("tool-reported failure")

class _Refuses(BaseTool):
    name = "w202_refuse"
    description = "its own policy refuses"
    arguments_schema = _NoArgs
    async def _run(self, args):
        raise PolicyDeniedError(request=ActionRequest(action_type=ActionType.WRITE_FILE, target="x"),
                                result=PolicyResult(decision=PolicyDecision.DENY, reason_code="W202_DENY", explanation="denied by test"))

class _NeedsArgs(BaseTool):
    name = "w202_args"
    description = "requires an integer"
    arguments_schema = _NeedsN
    async def _run(self, args):
        return "unreachable"

class _ReadOnlyFails(BaseTool):
    name = "w202_readonly_fail"
    description = "non-mutating tool that fails"
    arguments_schema = _NoArgs
    def mutates_workspace(self, arguments):
        return False
    async def _run(self, args):
        raise RuntimeError("read-only failure")

class _Confirm(BaseTool):
    name = "w202_confirm"
    description = "needs approval every time"
    arguments_schema = _NoArgs
    @property
    def requires_confirmation(self):
        return True
    async def _run(self, args):
        _log("EFFECT_BEGIN")
        return "CONFIRMED-RESULT"

class W202Plugin(BasePlugin):
    @property
    def name(self) -> str:
        return "w202_plugin"
    @property
    def version(self) -> str:
        return "1.0"
    async def initialize(self) -> None:
        for tool in (_Ok(), _Fails(), _Refuses(), _NeedsArgs(), _ReadOnlyFails(), _Confirm()):
            self.kernel.registry.register("tool", tool.name, tool)
"""


def _plugin_workspace(base):
    workspace, plugins = base / "ws", base / "plugins"
    workspace.mkdir()
    plugins.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    _write_plugin(plugins, "w202plugin", PLUGIN)
    sys.modules.pop("w202plugin", None)
    return workspace, plugins, base / "w202.log"


def _exec(workspace, plugins, log, tool, arguments="{}", *, yes=False):
    os.environ["W202_LOG"] = str(log)
    cwd = os.getcwd()
    os.chdir(workspace)
    try:
        with patch("kriya.cli.load_config", return_value=_cfg(plugins)):
            sys.modules.pop("w202plugin", None)
            return CliRunner().invoke(main, ["tools", "execute", tool, arguments, *(["-y"] if yes else [])])
    finally:
        os.chdir(cwd)


def _crashed_direct_run(tmp_path):
    """A REAL `kriya tools execute` killed (os._exit(137)) during the effect: what survives is the durable STARTED RunRecord."""
    workspace, plugins, effects = make_workspace(tmp_path)
    done = subprocess.run([sys.executable, CHILD, str(tmp_path), "during_effect"], capture_output=True, text=True, timeout=240, check=False,
                          env={**os.environ, "D1_EFFECT_LOG": str(effects)})
    assert done.returncode == 137, done.stderr[-1500:]
    (record,) = list_run_records(str(workspace))
    assert record.tool_execution == {"tool": TOOL, "state": "started"} and record.lifecycle_state == RunLifecycle.RUNNING
    return workspace, plugins, effects, record.run_id


def _synthetic_record(workspace, run_id, *, tool_execution, lifecycle=RunLifecycle.RUNNING):
    record = RunRecord.new(run_id, workspace_identity(str(workspace)), None, None)
    save_run_record(str(workspace), record, expected_revision=None)
    record = record.transition(lifecycle, **({"tool_execution": tool_execution} if tool_execution is not None else {}))
    save_run_record(str(workspace), record, expected_revision=1)
    return record


def _runs(workspace, *args):
    result = CliRunner().invoke(main, ["runs", *args, "--workspace", str(workspace)])
    # a typed CLI result only: an uncaught exception is a crash that happens to exit nonzero, never an acceptable outcome (CliRunner would otherwise hide it)
    assert result.exception is None or isinstance(result.exception, SystemExit), repr(result.exception)
    return result


def _bytes(workspace, run_id):
    return open(run_record_path(str(workspace), run_id), "rb").read()


def _gate_blocks(workspace):
    return not commit_state.assess_workspace_commit_state(str(workspace)).safe


def _direct_audit(workspace):
    return [d for d in load_decision_ledger(str(workspace)).filter_by_type(td.DECISION_TYPE) if d.fields.get("kind") == td.DIRECT_KIND]


def _abandon(workspace, run_id, *, confirmed=True):
    return td.apply_direct_abandon(str(workspace), run_id, operator="op", confirmed=confirmed)


# ------------------------------------------------------------------ RECOVERY TRUTH (P-2)
def test_a_crashed_started_direct_run_is_operator_action_required_in_status_never_clean_or_recoverable(tmp_path):
    workspace, _, _, run_id = _crashed_direct_run(tmp_path)
    assessment = recovery.assess_recovery(str(workspace))
    assert assessment.status == recovery.STATUS_MANUAL_ACTION_REQUIRED and assessment.unresolved_tool_run_ids == (run_id,)
    (finding,) = assessment.records
    assert commit_state.REASON_UNRESOLVED_TOOL_EXECUTION in finding.reason_codes and not finding.recoverable
    assert finding.to_dict()["proposed_lifecycle_state"] is None and finding.to_dict()["reason_codes"] == [commit_state.REASON_UNRESOLVED_TOOL_EXECUTION]
    result = _runs(workspace, "status")
    assert result.exit_code == 1 and "MANUAL_ACTION_REQUIRED" in result.output and "UNRESOLVED_TOOL_EXECUTION" in result.output
    assert "RECOVERY_AVAILABLE" not in result.output and "Status: CLEAN" not in result.output


def test_recover_leaves_the_unresolved_direct_record_untouched_and_never_claims_clean_while_the_gate_blocks(tmp_path):
    """NEGATIVE CONTROL for the measured contradiction: `runs recover` used to mark it RECOVERED/FAILURE and print 'Status: CLEAN / Nothing needs recovery' while every mutating run
    was still refused."""
    workspace, _, _, run_id = _crashed_direct_run(tmp_path)
    before = _bytes(workspace, run_id)
    result = _runs(workspace, "recover")
    assert _bytes(workspace, run_id) == before, "recover rewrote the unresolved direct record"
    assert result.exit_code == 1 and "Status: MANUAL_ACTION_REQUIRED" in result.output and "Recovered run" not in result.output
    assert "Nothing needs recovery" not in result.output and "Status: CLEAN" not in result.output
    assert load_run_record(str(workspace), run_id).lifecycle_state == RunLifecycle.RUNNING
    assert _gate_blocks(workspace) and recovery.assess_recovery(str(workspace)).status != recovery.STATUS_CLEAN


def test_a_terminal_record_that_still_says_started_is_also_operator_action_required(tmp_path):
    """A Ctrl-C ends the run FAILURE but the effect-in-progress evidence stays 'started': the gate blocks it, so status must too (it is not a needs_recovery record)."""
    workspace, _, _, run_id = _crashed_direct_run(tmp_path)
    record = load_run_record(str(workspace), run_id)
    save_run_record(str(workspace), record.transition(RunLifecycle.FAILURE), expected_revision=record.revision)
    assert load_run_record(str(workspace), run_id).terminal and _gate_blocks(workspace)
    assert recovery.assess_recovery(str(workspace)).status == recovery.STATUS_MANUAL_ACTION_REQUIRED
    assert _runs(workspace, "recover").exit_code == 1


def test_the_gate_names_the_command_that_can_see_a_direct_run_not_the_plan_commands(tmp_path):
    workspace, _, _, run_id = _crashed_direct_run(tmp_path)
    message = commit_state.assess_workspace_commit_state(str(workspace)).describe()
    assert f"run {run_id}" in message and "tool-inspect --run RUN_ID" in message and "tool-abandon --run RUN_ID --confirm" in message
    assert "the control state is the only evidence" not in message and "tool-retry" not in message


# ------------------------------------------------------------------ BLOCKING + INSPECT
def test_the_gate_blocks_a_new_mutating_run_before_disposition_and_nothing_runs(tmp_path):
    workspace, plugins, effects, _ = _crashed_direct_run(tmp_path)
    from _d1_direct_tool_support import execute_tool
    again = execute_tool(workspace, plugins, effects)
    assert again.exit_code == 1 and "unresolved TOOL execution" in again.output
    assert lines(effects).count("EFFECT_BEGIN") == 1, "an unresolved STARTED run allowed a new mutating run"


def test_inspect_run_shows_the_authoritative_record_is_read_only_and_does_not_guess_the_effect(tmp_path):
    workspace, _, effects, run_id = _crashed_direct_run(tmp_path)
    before, effects_before = _bytes(workspace, run_id), lines(effects)
    result = _runs(workspace, "tool-inspect", "--run", run_id, "--json")
    assert result.exit_code == 1
    entry = json.loads(result.output)["entry"]
    assert (entry["run_id"], entry["tool"], entry["lifecycle_state"], entry["execution_state"], entry["unresolved"]) == (run_id, TOOL, "RUNNING", "started", True)
    assert entry["allowed_actions"] == ["abandon"] and entry["revision"] == 2 and entry["created_at"] and entry["updated_at"]
    assert "may or may not have happened" in entry["classification"] and "never started" not in entry["classification"] and "failed" not in entry["classification"]
    assert _bytes(workspace, run_id) == before and lines(effects) == effects_before and _direct_audit(workspace) == []


def test_plan_tool_inspect_does_not_claim_a_direct_run_but_points_to_it(tmp_path):
    workspace, _, _, run_id = _crashed_direct_run(tmp_path)
    assert td.inspect_tool_dispositions(str(workspace)).entries == (), "the plan-TOOL owner claimed a direct run"
    result = _runs(workspace, "tool-inspect")
    assert result.exit_code == 1 and f"tool-inspect --run {run_id}" in result.output and "Subtask " not in result.output
    assert "No TOOL subtask is awaiting an operator." not in result.output
    data = json.loads(_runs(workspace, "tool-inspect", "--json").output)
    assert data["entries"] == [] and [e["run_id"] for e in data["direct_runs_awaiting_operator"]] == [run_id]


# ------------------------------------------------------------------ ABANDON
def test_abandon_requires_explicit_confirmation_and_changes_nothing_without_it(tmp_path):
    workspace, _, _, run_id = _crashed_direct_run(tmp_path)
    before = _bytes(workspace, run_id)
    refused = _abandon(workspace, run_id, confirmed=False)
    assert refused.status == td.STATUS_REFUSED and refused.reason_code == td.CONFIRMATION_REQUIRED
    assert _bytes(workspace, run_id) == before and _direct_audit(workspace) == [] and _gate_blocks(workspace)


def test_abandon_clears_the_block_preserves_the_started_evidence_and_is_never_completed(tmp_path):
    workspace, _, effects, run_id = _crashed_direct_run(tmp_path)
    effects_before = lines(effects)
    result = _abandon(workspace, run_id)
    assert result.status == td.STATUS_APPLIED and (result.prior_state, result.new_state) == ("started", "abandoned") and result.run_id == run_id
    record = load_run_record(str(workspace), run_id)
    execution = record.tool_execution
    assert execution["state"] == "abandoned" != "completed" and execution["tool"] == TOOL
    assert execution["started_evidence"] == {"tool": TOOL, "state": "started"}                       # the original STARTED evidence is kept
    assert (execution["prior_lifecycle_state"], execution["prior_revision"], execution["run_created_at"]) == ("RUNNING", 2, record.created_at)
    assert execution["disposition"]["operator"] == "op" and execution["disposition"]["disposition_id"] == result.disposition_id
    assert record.lifecycle_state == RunLifecycle.RUNNING and record.revision == 3                     # no edge to a success state, nothing settled
    assert record.unresolved_tool_execution is None and not _gate_blocks(workspace)
    (audit,) = _direct_audit(workspace)
    assert audit.fields["run_id"] == run_id and audit.fields["prior_tool_execution"] == {"tool": TOOL, "state": "started"} and audit.fields["prior_revision"] == 2
    assert lines(effects) == effects_before, "abandon executed the tool"


def test_abandon_is_idempotent_and_writes_nothing_the_second_time(tmp_path):
    workspace, _, _, run_id = _crashed_direct_run(tmp_path)
    first = _abandon(workspace, run_id)
    after_first = _bytes(workspace, run_id)
    again = _abandon(workspace, run_id)
    assert again.status == td.STATUS_ALREADY_APPLIED and again.disposition_id == first.disposition_id
    assert _bytes(workspace, run_id) == after_first and len(_direct_audit(workspace)) == 1


def test_a_later_recover_labels_an_abandoned_record_needs_review_never_failure(tmp_path):
    workspace, _, _, run_id = _crashed_direct_run(tmp_path)
    _abandon(workspace, run_id)
    assert _runs(workspace, "recover").exit_code == 0
    record = load_run_record(str(workspace), run_id)
    assert record.lifecycle_state == RunLifecycle.RECOVERED and record.terminal_status == "NEEDS_REVIEW", "an abandoned effect was relabelled FAILURE"
    assert record.tool_execution["state"] == "abandoned"


def test_abandon_clears_only_the_intended_direct_run_block(tmp_path):
    workspace, _, _, run_id = _crashed_direct_run(tmp_path)
    other = _synthetic_record(workspace, "20260101T000000-deadbeef", tool_execution={"tool": "other_tool", "state": "started"})
    assert _abandon(workspace, run_id).status == td.STATUS_APPLIED
    assert _gate_blocks(workspace), "abandoning one run cleared the other run's block"
    assert load_run_record(str(workspace), other.run_id).tool_execution == {"tool": "other_tool", "state": "started"}
    assert _abandon(workspace, other.run_id).status == td.STATUS_APPLIED and not _gate_blocks(workspace)


def test_the_ledger_is_audit_only_an_audit_record_alone_never_clears_the_gate(tmp_path, monkeypatch):
    """audit succeeds, the RunRecord write fails: the audit entry exists, the gate still blocks (abandon authority is the RunRecord, never a ledger overlay)."""
    workspace, _, _, run_id = _crashed_direct_run(tmp_path)
    Faults(monkeypatch, write=2, step="file_fsync")
    with pytest.raises(atomic_write.DurableWriteError):
        _abandon(workspace, run_id)
    monkeypatch.undo()
    assert len(_direct_audit(workspace)) == 1 and _gate_blocks(workspace)
    assert load_run_record(str(workspace), run_id).tool_execution == {"tool": TOOL, "state": "started"}


@pytest.mark.parametrize("step", atomic_write.DURABLE_WRITE_STEPS)
def test_an_audit_write_failure_leaves_the_run_record_unresolved_and_nothing_advances(tmp_path, monkeypatch, step):
    workspace, _, _, run_id = _crashed_direct_run(tmp_path)
    before = _bytes(workspace, run_id)
    Faults(monkeypatch, write=1, step=step)                       # durable write 1 = the audit decision
    with pytest.raises(atomic_write.DurableWriteError) as raised:
        _abandon(workspace, run_id)
    monkeypatch.undo()
    assert raised.value.step == step
    assert _bytes(workspace, run_id) == before, "the RunRecord advanced although its audit evidence was not durable"
    assert _gate_blocks(workspace)
    assert _abandon(workspace, run_id).status == td.STATUS_APPLIED and len(_direct_audit(workspace)) == 1 and not _gate_blocks(workspace)


@pytest.mark.parametrize("step", atomic_write.DURABLE_WRITE_STEPS)
def test_a_run_record_write_failure_after_the_audit_is_surfaced_and_the_same_abandon_converges(tmp_path, monkeypatch, step):
    workspace, _, _, run_id = _crashed_direct_run(tmp_path)
    Faults(monkeypatch, write=2, step=step)                       # durable write 2 = the RunRecord disposition
    with pytest.raises(atomic_write.DurableWriteError) as raised:
        _abandon(workspace, run_id)
    monkeypatch.undo()
    assert raised.value.step == step and len(_direct_audit(workspace)) == 1
    state = load_run_record(str(workspace), run_id).tool_execution["state"]
    if step == "directory_fsync":
        # MEASURED, not hidden: DurableWriteError documents that after the atomic replace the new bytes are already visible and only their durability is unconfirmed. The command
        # was told it FAILED, yet the file shows `abandoned`, so the gate reads it as resolved until a crash could revert it.
        assert state == "abandoned" and not _gate_blocks(workspace)
    else:
        assert state == "started" and _gate_blocks(workspace), "a failed RunRecord write cleared the gate"
    result = _abandon(workspace, run_id)                          # the same command converges
    assert result.status == (td.STATUS_ALREADY_APPLIED if step == "directory_fsync" else td.STATUS_APPLIED)
    assert len(_direct_audit(workspace)) == 1 and load_run_record(str(workspace), run_id).tool_execution["state"] == "abandoned" and not _gate_blocks(workspace)


def test_a_stale_audit_record_made_against_another_revision_authorizes_nothing(tmp_path, monkeypatch):
    workspace, _, _, run_id = _crashed_direct_run(tmp_path)
    Faults(monkeypatch, write=2, step="file_fsync")
    with pytest.raises(atomic_write.DurableWriteError):
        _abandon(workspace, run_id)
    monkeypatch.undo()
    record = load_run_record(str(workspace), run_id)              # the run record moves on (revision 3) after the audit was made against revision 2
    save_run_record(str(workspace), record.transition(RunLifecycle.PLANNING), expected_revision=record.revision)
    result = _abandon(workspace, run_id)
    assert result.status == td.STATUS_REFUSED and result.reason_code == td.STATE_CHANGED_SINCE_RECORDED and _gate_blocks(workspace)


# ------------------------------------------------------------------ WHAT IS NOT A DIRECT TOOL EXECUTION
def test_a_pre_d1_ambiguous_running_record_is_not_reinterpreted_as_a_tool_execution(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    ambiguous = _synthetic_record(workspace, "20250101T000000-aaaaaaaa", tool_execution=None)
    assert ambiguous.unresolved_tool_execution is None and not _gate_blocks(workspace)
    assert recovery.assess_recovery(str(workspace)).unresolved_tool_run_ids == ()
    refused = _abandon(workspace, ambiguous.run_id)
    assert refused.status == td.STATUS_REFUSED and refused.reason_code == td.NOT_AN_UNRESOLVED_DIRECT_EXECUTION
    assert _bytes(workspace, ambiguous.run_id) == open(run_record_path(str(workspace), ambiguous.run_id), "rb").read() and _direct_audit(workspace) == []
    assert td.unresolved_direct_runs(str(workspace)) == ()


@pytest.mark.parametrize("state", ["completed", "failed", "abandoned"])
def test_a_resolved_direct_run_is_not_blocking_and_not_abandonable(tmp_path, state):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    record = _synthetic_record(workspace, "20250101T000000-bbbbbbbb", tool_execution={"tool": "t", "state": state})
    assert record.unresolved_tool_execution is None and not _gate_blocks(workspace)
    assert _abandon(workspace, record.run_id).reason_code == td.NOT_AN_UNRESOLVED_DIRECT_EXECUTION


@pytest.mark.parametrize("bad", [{"tool": "t", "state": "weird"}, {"tool": "t"}, {}, "started", {"tool": "", "state": "started"}])
def test_an_unknown_or_malformed_tool_execution_state_fails_closed_and_is_not_abandonable_here(tmp_path, bad):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    record = _synthetic_record(workspace, "20250101T000000-cccccccc", tool_execution=bad)
    assert record.unresolved_tool_execution, "an unknown/malformed state must block, never pass"
    assert _gate_blocks(workspace) and recovery.assess_recovery(str(workspace)).status == recovery.STATUS_MANUAL_ACTION_REQUIRED
    if bad != {"tool": "", "state": "started"}:
        assert _abandon(workspace, record.run_id).status == td.STATUS_REFUSED


def test_abandon_refuses_a_missing_run_an_invalid_id_and_a_success_record_with_started_evidence(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    assert _abandon(workspace, "20250101T000000-missing0").reason_code == td.RUN_NOT_FOUND
    assert _abandon(workspace, "../escape").reason_code == td.RUN_NOT_FOUND
    record = _synthetic_record(workspace, "20250101T000000-dddddddd", tool_execution={"tool": "t", "state": "started"})
    with pytest.raises(IllegalRunTransitionError):
        record.transition(RunLifecycle.SUCCESS).abandon_tool_execution({})   # contradictory evidence is never disposed
    with pytest.raises(IllegalRunTransitionError):
        record.transition(RunLifecycle.FAILURE, tool_execution={"tool": "t", "state": "failed"}).abandon_tool_execution({})


# ------------------------------------------------------------------ CLI SURFACE
def test_run_and_plan_selectors_are_mutually_exclusive_and_retry_has_no_direct_form(tmp_path):
    workspace, _, _, run_id = _crashed_direct_run(tmp_path)
    both = _runs(workspace, "tool-abandon", "t1", "--run", run_id, "--confirm")
    neither = _runs(workspace, "tool-abandon", "--confirm")
    attempt = _runs(workspace, "tool-abandon", "--run", run_id, "--attempt", "1", "--confirm")
    retry = _runs(workspace, "tool-retry", "--run", run_id, "--confirm")
    assert (both.exit_code, neither.exit_code, attempt.exit_code, retry.exit_code) == (2, 2, 2, 2)
    assert "cannot be combined" in both.output and "SUBTASK_ID or --run" in neither.output and "No such option" in retry.output
    assert _gate_blocks(workspace) and _direct_audit(workspace) == []


def test_the_cli_abandon_run_path_confirms_applies_and_is_idempotent(tmp_path):
    workspace, _, _, run_id = _crashed_direct_run(tmp_path)
    unconfirmed = _runs(workspace, "tool-abandon", "--run", run_id)
    assert unconfirmed.exit_code == 1 and "DISPOSITION_CONFIRMATION_REQUIRED" in unconfirmed.output and _gate_blocks(workspace)
    done = _runs(workspace, "tool-abandon", "--run", run_id, "--confirm", "--operator", "alice", "--json")
    payload = json.loads(done.output)
    assert done.exit_code == 0 and payload["status"] == "applied" and payload["run_id"] == run_id and not _gate_blocks(workspace)
    again = _runs(workspace, "tool-abandon", "--run", run_id, "--confirm", "--json")
    assert again.exit_code == 0 and json.loads(again.output)["status"] == "already_applied"
    assert load_run_record(str(workspace), run_id).tool_execution["disposition"]["operator"] == "alice"


@pytest.mark.parametrize("as_json", [False, True])
def test_a_durable_write_failure_in_a_direct_abandon_is_a_typed_nonzero_cli_result_not_a_traceback(tmp_path, monkeypatch, as_json):
    workspace, _, _, run_id = _crashed_direct_run(tmp_path)
    Faults(monkeypatch, write=1, step="replace")
    result = _runs(workspace, "tool-abandon", "--run", run_id, "--confirm", *(["--json"] if as_json else []))
    monkeypatch.undo()
    assert result.exit_code == 4 and isinstance(result.exception, SystemExit), repr(result.exception)
    assert "DISPOSITION_DURABLE_WRITE_FAILED" in result.output and "replace" in result.output and "Traceback" not in result.output
    assert _gate_blocks(workspace) and load_run_record(str(workspace), run_id).tool_execution["state"] == "started"


def test_a_durable_write_failure_in_a_plan_tool_disposition_is_also_typed_and_leaves_the_state(tmp_path, monkeypatch):
    _seed(tmp_path, {"t1": SUBTASK_STATE_TOOL_FAILED}, {"t1": 1})
    Faults(monkeypatch, write=1, step="file_fsync")
    result = _runs(tmp_path, "tool-retry", "t1", "--attempt", "1", "--confirm")
    monkeypatch.undo()
    assert result.exit_code == 4 and isinstance(result.exception, SystemExit) and "DISPOSITION_DURABLE_WRITE_FAILED" in result.output
    assert load_control_state(str(tmp_path)).tool_attempts == {"t1": 1} and load_control_state(str(tmp_path)).subtask_states == {"t1": SUBTASK_STATE_TOOL_FAILED}


# ------------------------------------------------------------------ DIRECT LIFECYCLE + RESULT TRUTH (P-4, P-5)
def test_success_exits_zero_and_records_completed(tmp_path):
    workspace, plugins, log = _plugin_workspace(tmp_path)
    result = _exec(workspace, plugins, log, "w202_ok")
    assert result.exit_code == 0 and "OK-RESULT" in result.output
    (record,) = list_run_records(str(workspace))
    assert record.tool_execution == {"tool": "w202_ok", "state": "completed"} and record.lifecycle_state == RunLifecycle.SUCCESS


def test_a_tool_failure_exits_nonzero_after_the_terminal_record_is_persisted_and_is_not_blocking(tmp_path):
    workspace, plugins, log = _plugin_workspace(tmp_path)
    result = _exec(workspace, plugins, log, "w202_fail")
    assert result.exit_code == 1 and "Execution failed" in result.output, "a failed tool must not exit 0"
    (record,) = list_run_records(str(workspace))
    assert record.tool_execution == {"tool": "w202_fail", "state": "failed"} and record.lifecycle_state == RunLifecycle.FAILURE   # the exit came AFTER the terminal transition
    assert record.unresolved_tool_execution is None and not _gate_blocks(workspace), "a terminal FAILED direct run must not block"
    assert recovery.assess_recovery(str(workspace)).unresolved_tool_run_ids == ()


def test_a_policy_refusal_exits_with_the_typed_refusal_code_and_invalid_arguments_with_the_usage_code(tmp_path):
    workspace, plugins, log = _plugin_workspace(tmp_path)
    refused = _exec(workspace, plugins, log, "w202_refuse")
    invalid = _exec(workspace, plugins, log, "w202_args", "{}")
    assert refused.exit_code == 4 and "W202_DENY" in refused.output
    assert invalid.exit_code == 2 and "Argument validation failed" in invalid.output
    assert lines(log) == [], "a refused or invalid call ran an effect"


def test_a_non_mutating_tool_failure_also_exits_nonzero_and_leaves_no_run_record(tmp_path):
    workspace, plugins, log = _plugin_workspace(tmp_path)
    result = _exec(workspace, plugins, log, "w202_readonly_fail")
    assert result.exit_code == 1 and "Execution failed" in result.output and list_run_records(str(workspace)) == []


def test_a_new_invocation_after_a_failure_gets_a_new_run_id_and_fresh_authority(tmp_path):
    workspace, plugins, log = _plugin_workspace(tmp_path)
    assert _exec(workspace, plugins, log, "w202_fail").exit_code == 1
    approved = _exec(workspace, plugins, log, "w202_confirm", yes=True)
    unapproved = _exec(workspace, plugins, log, "w202_confirm")                  # the earlier approval is not remembered: confirmation is asked again
    assert approved.exit_code == 0 and unapproved.exit_code == 1 and "--yes" in unapproved.output
    assert lines(log).count("EFFECT_BEGIN") == 2                                 # w202_fail's effect + one approved w202_confirm; the unapproved one never ran
    records = list_run_records(str(workspace))
    assert len({r.run_id for r in records}) == len(records) == 2


def test_a_new_direct_execution_never_reuses_the_failed_run_identity(tmp_path):
    workspace, plugins, log = _plugin_workspace(tmp_path)
    _exec(workspace, plugins, log, "w202_fail")
    (first,) = list_run_records(str(workspace))
    _exec(workspace, plugins, log, "w202_ok")
    ids = {r.run_id for r in list_run_records(str(workspace))}
    assert first.run_id in ids and len(ids) == 2
    assert load_run_record(str(workspace), first.run_id).tool_execution == {"tool": "w202_fail", "state": "failed"}, "the failed run's record was reused or rewritten"
