"""D-F1 of the consolidated addendum: once an enforce run ends on an unexpected error, its durable state agrees with that
terminal result.

MEASURED by the independent reviewer on 72d9182: after the terminal gate raised (an ordinary closer error, correctly
``status=error`` / ``failure_category=<Type>``), the approved plan stayed ``lifecycle_state: in_progress`` with ``s1: completed``
and ``control/state.json`` kept ``s1: completed`` plus its written files - the enforce loop's sandbox-state reset and its final
plan save sit AFTER the failing call, so an exception skipped them. The resume guard neutralised the stale state, but the
persisted state still said an active run had completed work. The ordinary failed path (a requirements gap) persists
``lifecycle_state: failed`` and ``s1: pending`` with no written files, because a sandbox-only completion cannot be resumed once the
candidate is discarded.

Invariant: the exception path persists exactly what the ordinary failed path persists - no new status - while the exception, its
class, the ``.enforce`` row (status=error), the fail-closed outcome (nothing committed) and the candidate cleanup are unchanged.

D-F2 (decided with the code contract, not assumed): ``gate.result`` records are written by
``terminal_gate_service._record_terminal_report`` "from the report the commit decision itself reads" (each payload carries
``commit_eligible``). When the gate raises there is no report and no commit decision, so there is nothing to record; the
controller's ``run.exception`` is the one authoritative terminal record and the explainer ranks it first. A synthetic
``gate.result`` would invent a ``commit_eligible`` verdict, so none is written - pinned at the bottom with a control.

Real enforce route (the harness of test_taf3_enforce_terminal_refusal); no new patch sites.
"""
import asyncio
import json
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from _strict_doubles import strict_config
from test_prd030_terminal_services import _request, _run, _validators
from test_taf3_enforce_terminal_refusal import _enforce, _engine, _repo

from kriya.config.config import AppConfig
from kriya.core.state_paths import trace_db_path
from kriya.workflow import requirement_closers, terminal_gate_service

PLAN_ID = "taf3-e2e"          # the plan id the shared harness builds


def _repo_in(root: Path) -> Path:
    root.mkdir(parents=True)
    return _repo(root, root / "FILTER_RAN")


def _enforce_rows(config) -> list:
    """Every .enforce trace row of this test's state directory (two runs share it)."""
    conn = sqlite3.connect(trace_db_path(config))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM runs WHERE run_id LIKE '%.enforce'")]
    finally:
        conn.close()


def _persisted(repo: Path) -> dict:
    """What the run left durable: the approved plan document's lifecycle and stage states, the control state's subtask states
    and written files, and how many git worktrees the repository still lists (the candidate's cleanup)."""
    control = repo / ".kriya" / "control"
    plan = json.loads((control / "plans" / f"{PLAN_ID}.json").read_text())
    state = json.loads((control / "state.json").read_text())
    listed = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True).stdout
    return {
        "plan_lifecycle": plan.get("lifecycle_state"),
        "plan_stages": plan.get("stage_states"),
        "subtask_states": state.get("subtask_states"),
        "written_files": state.get("subtask_written_files"),
        "git_worktrees": listed.count("worktree "),
    }


async def _ordinary_failed_run(tmp_path) -> dict:
    """An ordinary (non-exception) terminal failure: the verifier judges 'Update app.py.' missing -> a requirements gap."""
    repo = _repo_in(tmp_path / "gap")
    config = strict_config(autonomy={"spec_compliance_enabled": True})
    engine = _engine(config, bind_filter=False)
    engine.spec_compliance.check = AsyncMock(return_value={
        "requirement_verdicts": [{"id": "REQ-1", "verdict": "missing", "evidence": "app.py is not updated"}]})
    result = await _enforce(repo, engine)
    assert result.legacy_result["status"] == "failed", result.legacy_result
    return _persisted(repo)


@pytest.mark.parametrize("error", [RuntimeError("closer broke"), OSError("disk vanished")], ids=["RuntimeError", "OSError"])
@pytest.mark.asyncio
async def test_an_unexpected_terminal_error_persists_exactly_what_the_ordinary_failed_path_persists(tmp_path, monkeypatch, error):
    ordinary = await _ordinary_failed_run(tmp_path)
    # the reference is itself meaningful (not an empty comparison): failed, nothing completed, nothing written
    assert ordinary["plan_lifecycle"] == "failed" and set(ordinary["plan_stages"].values()) == {"pending"}, ordinary
    assert set(ordinary["subtask_states"].values()) == {"pending"} and ordinary["written_files"] == {}, ordinary

    repo = _repo_in(tmp_path / "boom")
    config = strict_config(autonomy={"spec_compliance_enabled": True})

    def broken(*_args, **_kwargs):
        raise error
    monkeypatch.setattr(requirement_closers, "close_requirements_by_mutation_scope", broken)
    with pytest.raises(type(error)) as raised:
        await _enforce(repo, _engine(config, bind_filter=False))
    assert raised.value is error                                                 # the class is retained
    errored = [row for row in _enforce_rows(config) if row["status"] == "error"]
    assert [row["failure_category"] for row in errored] == [type(error).__name__], errored
    assert (repo / "app.py").read_text() == "original\n"                         # fail closed: nothing committed
    assert _persisted(repo) == ordinary                                          # D-F1: the durable state is the failed path's


@pytest.mark.asyncio
async def test_the_run_after_an_unexpected_terminal_error_is_not_blocked_by_its_state(tmp_path, monkeypatch):
    """Resume safety: the repository is usable straight away (the lease is released and nothing stale is reused)."""
    repo = _repo_in(tmp_path / "boom")
    config = strict_config(autonomy={"spec_compliance_enabled": True})

    def broken(*_args, **_kwargs):
        raise RuntimeError("closer broke")
    with monkeypatch.context() as patched:
        patched.setattr(requirement_closers, "close_requirements_by_mutation_scope", broken)
        with pytest.raises(RuntimeError):
            await _enforce(repo, _engine(config, bind_filter=False))
    result = await _enforce(repo, _engine(config, bind_filter=False))
    assert result.legacy_result["status"] == "success", result.legacy_result
    assert (repo / "app.py").read_text() == "verified candidate\n"


@pytest.mark.asyncio
async def test_an_interrupt_after_the_durable_commit_never_marks_the_plan_failed(tmp_path):
    """The guard of the new handler: once the workspace commit is durable the work is in the workspace, so an interrupt that
    arrives afterwards (a BaseException - the terminal event emitter swallows only Exception) must not record `failed`."""
    repo = _repo_in(tmp_path / "committed")
    config = strict_config(autonomy={"spec_compliance_enabled": True})
    engine = _engine(config, bind_filter=False)

    async def emit(event_name, _data):
        if event_name == "workspace_commit_completed":      # emitted only after commit.completed: raising proves the commit
            raise asyncio.CancelledError()                   # is durable, and BaseException escapes the emitter's `except Exception`
    engine.kernel.events.emit = emit
    with pytest.raises(asyncio.CancelledError):
        await _enforce(repo, engine)
    assert (repo / "app.py").read_text() == "verified candidate\n"                # the commit is durable
    assert _persisted(repo)["plan_lifecycle"] != "failed"


# ------------------------------------------------------------------------------------------------ D-F2 (intentional contract)

def _record_gate_results(monkeypatch) -> list:
    emitted: list = []
    scope = terminal_gate_service.attempt_evidence_scope
    monkeypatch.setattr(scope, "capture_mode", lambda: "on")
    monkeypatch.setattr(scope, "emit", lambda kind, payload, content=None: emitted.append((kind, payload)))
    return emitted


def test_d_f2_control_a_completed_gate_run_records_one_gate_result_per_gate_from_its_report(tmp_path, monkeypatch):
    emitted = _record_gate_results(monkeypatch)
    report, _events = _run(_request(tmp_path), _validators())
    gate_results = [payload for kind, payload in emitted if kind == "gate.result"]
    assert len(gate_results) == 8 and report.commit_eligible
    assert all("commit_eligible" in payload and payload["stage"] == "terminal" for payload in gate_results)


def test_d_f2_a_terminal_gate_that_raises_records_no_gate_result_because_no_commit_decision_exists(tmp_path, monkeypatch):
    """The exception leaves no report for the commit decision to read, so no gate.result is written - not even for the gates
    that had passed (a record would have to invent commit_eligible). The controller's run.exception is the terminal record."""
    emitted = _record_gate_results(monkeypatch)
    autonomy = AppConfig().autonomy
    autonomy.spec_compliance_enabled = True

    def broken(*_args, **_kwargs):
        raise RuntimeError("closer broke")
    monkeypatch.setattr(requirement_closers, "close_requirements_by_mutation_scope", broken)
    with pytest.raises(RuntimeError, match="closer broke"):
        _run(_request(tmp_path, autonomy=autonomy), _validators())
    assert [kind for kind, _payload in emitted if kind == "gate.result"] == []
