"""GUI M6: ``execution.preflight``, ``launch.status`` and the recorded, non-interactive ``kriya generate --launch-id``.

These pin the authority split of a desktop launch: Kriya resolves configuration/state/models/authority exactly as a terminal run in that folder
would (the process working directory decides), the launch id IS the run id and is claimed once by the first top-level run in the launch's own
workspace, the launch record links the run to its workspace before anything can fail, nothing is auto-approved, and no outcome is ever inferred
from a process exit."""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys

import pytest
from click.testing import CliRunner

from kriya.cli import main
from kriya.config import AppConfig
from kriya.control import launch as launch_module
from kriya.control.launch import GOAL_MAX_CHARS, LaunchRefused, LaunchSession, goal_digest, valid_launch_id
from kriya.control.persistence import launch_record_path, load_launch_record, load_run_record
from kriya.control.run_coordinator import begin_mutating_run, coordinated_mutation
from kriya.control.run_ownership import acquire_run_lock
from kriya.control.workspace_identity import canonical_workspace, workspace_identity
from kriya.kup import policy, serve
from kriya.kup.cli_ops import run_kup_operation

LID = "20261010T101010-abcdef01"
GOAL = "Create a utility library with a slugify function and tests.\nSecond line; $(touch /tmp/pwned) `x`"


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def _preflight(path, cfg=None):
    return run_kup_operation(cfg or AppConfig(), {"operation": "execution.preflight", "workspace": str(path)})


def _status(path, launch_id=LID, cfg=None):
    return run_kup_operation(cfg or AppConfig(), {"operation": "launch.status", "workspace": str(path), "launch_id": launch_id})


@pytest.fixture(autouse=True)
def _stable_tools(request, monkeypatch):
    """Tests that are not about tool visibility must not depend on THIS machine's git/python (the Xcode shims differ per machine): a healthy git is stubbed."""
    if "tool" in request.node.name or "preflight_reports_tool" in request.node.name:
        return
    monkeypatch.setattr("kriya.kup.execution.tool_visibility", lambda: [{"tool": "git", "available": True, "path": "/usr/bin/git", "checked": "ran --version"}])


@pytest.fixture
def ws(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    return folder


def _tree(path):
    return sorted((os.path.relpath(os.path.join(d, n), path), os.stat(os.path.join(d, n)).st_mtime_ns) for d, _dirs, names in os.walk(path) for n in names)


# ------------------------------------------------------------------ preflight

def test_preflight_describes_the_run_exactly_and_is_read_only(ws):
    (ws / "a.txt").write_text("a")
    before, cwd = _tree(ws), os.getcwd()
    env = _preflight(ws)
    assert env["error"] is None and env["operation"] == "execution.preflight"
    data = env["data"]
    assert data["ready"] is True and data["blockers"] == []
    assert data["workspace"] == {"path": canonical_workspace(str(ws)), "identity": workspace_identity(canonical_workspace(str(ws))), "name": "proj"}
    cfg = data["configuration"]
    assert cfg["status"] == "loaded" and cfg["source"] == "packaged_default" and cfg["file"] is None and cfg["authority"]["status"] == "no_findings"
    assert cfg["directory"] == canonical_workspace(str(ws)) and len(cfg["fingerprint"]) == 64
    assert data["state"]["observable_by_cockpit"] is True and data["state"]["directory"] == data["state"]["cockpit_directory"]
    assert data["model"]["model"] and data["model"]["provider"] and "api_key" not in json.dumps(data)
    assert data["interaction"]["approvals"] == "change_review" and data["interaction"]["autonomy_mode"] == "human-in-the-loop" and data["interaction"]["knowledge_policy"] == "strict"
    assert os.getcwd() == cwd, "the preflight must restore the process working directory"
    assert _tree(ws) == before and not (ws / ".kriya").exists(), "a preflight writes nothing into the folder"


def test_configuration_is_resolved_from_the_folder_like_a_terminal_run_there(ws):
    (ws / "kriya.yaml").write_text("llm:\n  context_window: 12345\n")
    data = _preflight(ws)["data"]
    assert data["configuration"]["source"] == "auto_discovered_cwd"
    assert os.path.samefile(data["configuration"]["file"], ws / "kriya.yaml")
    assert data["model"]["context_window"] == 12345, "the folder's own repository-safe setting is the one a run would use"


def test_a_field_only_the_operator_may_set_blocks_the_start_with_the_field_named(ws):
    (ws / "kriya.yaml").write_text("execution_policy:\n  enabled: true\n")
    data = _preflight(ws)["data"]
    assert data["ready"] is False
    assert [b["code"] for b in data["blockers"]] == ["CONFIG_AUTHORITY_REFUSED"]
    assert data["configuration"]["status"] == "refused" and data["configuration"]["authority"]["status"] == "refused"
    assert any(f["field"].startswith("execution_policy") for f in data["configuration"]["authority"]["fields"])
    assert data["model"] is None and data["state"]["directory"] is None


def test_a_state_directory_the_cockpit_does_not_read_blocks_the_start(ws, tmp_path, monkeypatch):
    monkeypatch.delenv("KRIYA_STATE_DIR")  # the environment override would (correctly) win on both sides; this is the configured-path case
    (ws / "kriya.yaml").write_text("paths:\n  state: .kriya/state\n")
    data = _preflight(ws, AppConfig(paths={"state": str(tmp_path / "cockpit-state")}))["data"]
    assert data["ready"] is False and data["state"]["observable_by_cockpit"] is False
    assert [b["code"] for b in data["blockers"]] == ["STATE_DIRECTORY_NOT_OBSERVABLE"]
    assert data["state"]["directory"].endswith(os.path.join(".kriya", "state")) and data["state"]["cockpit_directory"] != data["state"]["directory"]


def test_an_active_run_in_the_folder_blocks_the_start(ws):
    with acquire_run_lock(canonical_workspace(str(ws)), run_id=LID):
        data = _preflight(ws)["data"]
    assert data["ready"] is False and data["blockers"][0]["code"] == "RUN_ACTIVE" and LID in data["blockers"][0]["message"]
    assert _preflight(ws)["data"]["ready"] is True, "the lock is released: nothing is remembered"


def test_preflight_is_not_cached_between_calls(ws):
    assert _preflight(ws)["data"]["configuration"]["source"] == "packaged_default"
    (ws / "kriya.yaml").write_text("llm:\n  context_window: 4096\n")
    assert _preflight(ws)["data"]["configuration"]["source"] == "auto_discovered_cwd"
    (ws / "kriya.yaml").unlink()
    assert _preflight(ws)["data"]["configuration"]["source"] == "packaged_default"


@pytest.mark.parametrize("workspace", ["relative/path", "", "/tmp/with\nnewline"])
def test_preflight_refuses_a_workspace_that_is_not_a_clean_absolute_path(workspace):
    env = run_kup_operation(AppConfig(), {"operation": "execution.preflight", "workspace": workspace})
    assert env["error"]["code"] == policy.INVALID_REQUEST


def test_preflight_of_a_missing_folder_is_workspace_unavailable(tmp_path):
    env = _preflight(tmp_path / "nope")
    assert env["error"]["code"] == policy.WORKSPACE_UNAVAILABLE and env["error"]["reason"] == "not_found"


# ------------------------------------------------------------------ the launch id is the run id

def test_the_launch_id_is_claimed_once_by_the_first_top_level_run_in_its_workspace(ws):
    canonical = canonical_workspace(str(ws))
    session = LaunchSession(canonical, workspace_identity(canonical), LID, GOAL, {"ready": True}, {})
    session.open()
    try:
        with begin_mutating_run(str(ws)) as first:
            assert first.run_id == LID
            assert load_run_record(canonical, LID) is not None
        with begin_mutating_run(str(ws)) as second:
            assert second.run_id != LID, "a second run in the same process gets its own id"
    finally:
        launch_module.deactivate(session)


def test_a_launch_is_not_claimed_by_a_run_in_another_workspace(ws, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    canonical = canonical_workspace(str(ws))
    session = LaunchSession(canonical, workspace_identity(canonical), LID, GOAL, {"ready": True}, {})
    session.open()
    try:
        with begin_mutating_run(str(other)) as run:
            assert run.run_id != LID
    finally:
        launch_module.deactivate(session)


def test_the_real_coordinated_mutation_decorator_adopts_the_launch_id(ws):
    class Engine:
        seen = None

        @coordinated_mutation
        async def run_generation_workflow(self, goal, workspace_path):
            from kriya.control.run_coordinator import current_run_context
            Engine.seen = current_run_context().run_id
            return {"status": "success"}

    canonical = canonical_workspace(str(ws))
    session = LaunchSession(canonical, workspace_identity(canonical), LID, GOAL, {"ready": True}, {})
    session.open()
    try:
        asyncio.run(Engine().run_generation_workflow(GOAL, str(ws)))
    finally:
        launch_module.deactivate(session)
    assert Engine.seen == LID
    record = load_launch_record(canonical, LID)
    assert record["phase"] == "RUNNING" and record["run"]["run_id"] == LID


@pytest.mark.parametrize("bad", ["", "x", "../escape", "20261010T101010-ABCDEF01", "20261010T101010-abcdef0", "20261010T101010-abcdef01/../x", None, 5])
def test_only_the_run_id_shape_is_a_launch_id(bad):
    assert not valid_launch_id(bad)
    with pytest.raises(LaunchRefused) as refused:
        LaunchSession("/w", "i", bad, GOAL, {}, {})
    assert refused.value.code == "LAUNCH_ID_INVALID"


def test_a_launch_id_already_recorded_is_refused_not_overwritten(ws):
    canonical = canonical_workspace(str(ws))
    first = LaunchSession(canonical, workspace_identity(canonical), LID, GOAL, {"ready": True}, {})
    first.open()
    launch_module.deactivate(first)
    with pytest.raises(LaunchRefused) as refused:
        LaunchSession(canonical, workspace_identity(canonical), LID, "another goal", {"ready": True}, {}).open()
    assert refused.value.code == "LAUNCH_ID_IN_USE"
    assert load_launch_record(canonical, LID)["goal"] == goal_digest(GOAL)


def test_the_record_holds_a_digest_of_the_goal_never_the_text(ws):
    canonical = canonical_workspace(str(ws))
    session = LaunchSession(canonical, workspace_identity(canonical), LID, GOAL, {"ready": True}, {})
    session.open()
    launch_module.deactivate(session)
    raw = open(launch_record_path(canonical, LID), encoding="utf-8").read()
    assert "slugify" not in raw and "pwned" not in raw
    assert json.loads(raw)["goal"] == goal_digest(GOAL) and json.loads(raw)["workspace"]["identity"] == workspace_identity(canonical)


# ------------------------------------------------------------------ the recorded launch through the real CLI

def _install_fake_workflow(monkeypatch, outcome, seen=None):
    """Replaces ONLY the workflow body: the click command, the preflight, the launch record, the run coordinator and GenerateOutput are real."""
    import kriya.cli as cli

    def fake(ctx, goal, file, yes, knowledge_policy, ack, resume, resume_id, json_output, milestones, output, **kwargs):
        if seen is not None:
            seen.update(goal=goal, yes=yes, knowledge_policy=knowledge_policy, file=file, resume=resume, cwd=os.getcwd())
        with begin_mutating_run(os.getcwd()) as context:
            if seen is not None:
                seen["run_id"] = context.run_id
            if isinstance(outcome, BaseException):
                raise outcome
            output.result = outcome
        sys.exit(0 if outcome.get("status") == "success" else 1)

    monkeypatch.setattr(cli, "_generate_impl", fake)


def _launch(ws, monkeypatch, goal=GOAL, launch_id=LID, extra=()):
    monkeypatch.chdir(ws)
    result = CliRunner().invoke(main, ["generate", "--json", "--launch-id", launch_id, *extra], input=goal)
    return result


def _last_json(result):
    return json.loads(result.stdout[result.stdout.index("{"):])


def test_a_launch_runs_the_real_path_noninteractively_and_seals_its_record(ws, monkeypatch):
    seen = {}
    _install_fake_workflow(monkeypatch, {"status": "success", "quality_gates_passed": True, "run_id": "ab12cd34", "files": ["a.py"]}, seen)
    result = _launch(ws, monkeypatch)
    assert result.exit_code == 0, result.output
    assert seen["goal"] == GOAL and seen["yes"] is False and seen["knowledge_policy"] == "strict" and seen["file"] is None and seen["resume"] is False
    assert os.path.samefile(seen["cwd"], ws), "the workspace is the process working directory, as for a terminal run"
    assert seen["run_id"] == LID
    canonical = canonical_workspace(str(ws))
    record = load_launch_record(canonical, LID)
    assert record["phase"] == "ENDED" and [h["phase"] for h in record["history"]] == ["STARTING", "RUNNING", "ENDED"]
    assert record["workspace"] == {"path": canonical, "identity": workspace_identity(canonical)}
    assert record["run"]["run_id"] == LID and record["preflight"]["ready"] is True and record["options"] == {"knowledge_policy": "strict", "interactive": False}
    assert record["ended"]["exit_code"] == 0 and record["ended"]["workflow_status"] == "success"
    assert record["ended"]["trace_run_id"] is None, "no traces.db row linked to this launch was written, and the result's own run_id is never trusted as one"
    assert _last_json(result)["status"] == "success"


def test_a_workflow_failure_is_sealed_as_the_workflows_own_words_not_as_success(ws, monkeypatch):
    _install_fake_workflow(monkeypatch, {"status": "failure", "quality_gates_passed": False, "failure_category": "knowledge_gap", "error": "gap", "run_id": "ee00ee00"})
    result = _launch(ws, monkeypatch)
    assert result.exit_code == 1
    ended = load_launch_record(canonical_workspace(str(ws)), LID)["ended"]
    assert ended["exit_code"] == 1 and ended["workflow_status"] == "failure" and ended["quality_gates_passed"] is False and ended["failure_category"] == "knowledge_gap"


def test_a_crash_inside_the_run_is_sealed_with_the_error_and_the_run_record_fails_too(ws, monkeypatch):
    _install_fake_workflow(monkeypatch, RuntimeError("boom"))
    result = _launch(ws, monkeypatch)
    assert result.exit_code == 1
    canonical = canonical_workspace(str(ws))
    ended = load_launch_record(canonical, LID)["ended"]
    assert ended["exit_code"] == 1 and "RuntimeError: boom" in ended["error"] and ended["workflow_status"] is None  # no workflow result existed: none is invented
    assert load_run_record(canonical, LID).terminal_status == "FAILURE"


def test_an_interrupt_is_sealed_as_130(ws, monkeypatch):
    _install_fake_workflow(monkeypatch, KeyboardInterrupt())
    result = _launch(ws, monkeypatch)
    assert result.exit_code == 130
    assert load_launch_record(canonical_workspace(str(ws)), LID)["ended"]["exit_code"] == 130


def test_a_refused_preflight_is_a_recorded_launch_that_never_starts_the_workflow(ws, monkeypatch):
    (ws / "kriya.yaml").write_text("execution_policy:\n  enabled: true\n")
    seen = {}
    _install_fake_workflow(monkeypatch, {"status": "success"}, seen)
    # main() itself refuses a configuration with unapproved authority fields before the command runs: no launch, no workflow, a typed refusal
    result = _launch(ws, monkeypatch)
    assert result.exit_code != 0 and seen == {}


def test_a_blocked_launch_is_recorded_and_sealed_as_refused(ws, monkeypatch):
    seen = {}
    _install_fake_workflow(monkeypatch, {"status": "success"}, seen)
    canonical = canonical_workspace(str(ws))
    monkeypatch.chdir(ws)
    with acquire_run_lock(canonical, run_id="20261010T000000-00000000"):
        result = CliRunner().invoke(main, ["generate", "--json", "--launch-id", LID], input=GOAL)
    assert result.exit_code == 2 and seen == {}
    body = _last_json(result)
    assert body["status"] == "refused" and body["code"] == "RUN_ACTIVE" and body["launch_id"] == LID
    record = load_launch_record(canonical, LID)
    assert record["phase"] == "ENDED" and record["ended"]["exit_code"] == 2 and record["run"] is None, "linked to the workspace although no run started"


@pytest.mark.parametrize("goal", ["", "   \n", "x" * (GOAL_MAX_CHARS + 1), "a\x00b"])
def test_an_invalid_goal_is_refused_before_anything_is_recorded(ws, monkeypatch, goal):
    _install_fake_workflow(monkeypatch, {"status": "success"})
    result = _launch(ws, monkeypatch, goal=goal)
    assert result.exit_code == 2 and _last_json(result)["status"] == "refused"
    assert not os.path.exists(launch_record_path(canonical_workspace(str(ws)), LID))


def test_a_launch_id_in_use_is_refused_and_the_first_record_is_untouched(ws, monkeypatch):
    _install_fake_workflow(monkeypatch, {"status": "success", "quality_gates_passed": True})
    assert _launch(ws, monkeypatch).exit_code == 0
    first = open(launch_record_path(canonical_workspace(str(ws)), LID), "rb").read()
    result = _launch(ws, monkeypatch, goal="different")
    assert result.exit_code == 2 and _last_json(result)["code"] == "LAUNCH_ID_IN_USE"
    assert open(launch_record_path(canonical_workspace(str(ws)), LID), "rb").read() == first


@pytest.mark.parametrize("extra", [["--yes"], ["--resume"], ["--file", __file__], ["--from-milestones", __file__], ["--knowledge-policy", "permissive"], ["GOAL"]])
def test_a_launch_cannot_carry_options_that_would_broaden_it(ws, monkeypatch, extra):
    seen = {}
    _install_fake_workflow(monkeypatch, {"status": "success"}, seen)
    result = _launch(ws, monkeypatch, extra=extra)
    assert result.exit_code == 2 and seen == {}
    assert not os.path.exists(launch_record_path(canonical_workspace(str(ws)), LID))


def test_a_launch_requires_json_output(ws, monkeypatch):
    _install_fake_workflow(monkeypatch, {"status": "success"})
    monkeypatch.chdir(ws)
    result = CliRunner().invoke(main, ["generate", "--launch-id", LID], input=GOAL)
    assert result.exit_code == 2


def test_the_goal_is_never_placed_in_a_shell_or_a_file(ws, monkeypatch, tmp_path):
    marker = tmp_path / "pwned"
    goal = f"x; touch {marker}; $(touch {marker}) `touch {marker}`"
    _install_fake_workflow(monkeypatch, {"status": "success", "quality_gates_passed": True})
    assert _launch(ws, monkeypatch, goal=goal).exit_code == 0
    assert not marker.exists()
    assert goal not in open(launch_record_path(canonical_workspace(str(ws)), LID), encoding="utf-8").read()


# ------------------------------------------------------------------ launch.status

def test_launch_status_reads_durable_records_only(ws, monkeypatch):
    _install_fake_workflow(monkeypatch, {"status": "success", "quality_gates_passed": True, "run_id": "ab12cd34"})
    assert _launch(ws, monkeypatch).exit_code == 0
    data = _status(ws)["data"]
    assert data["liveness"] == "ended" and data["launch"]["phase"] == "ENDED"
    assert data["run_record"]["found"] is True and data["run_record"]["lifecycle_state"] in ("SUCCESS", "FAILURE", "RUNNING", "NEW", "PLANNING")
    assert data["workspace_lock"] == {"held": False, "held_by_this_launch": False}
    assert data["evidence"]["state"] == "absent", "no evidence configuration in this test: absent is reported, not invented"
    assert "pid" in data["launch"]["process"] and "DERIVED" in data["liveness_note"]
    assert "goal" in data["launch"] and "slugify" not in json.dumps(data)


def test_a_running_launch_is_live_while_the_lock_is_held_and_interrupted_when_it_is_not(ws):
    canonical = canonical_workspace(str(ws))
    session = LaunchSession(canonical, workspace_identity(canonical), LID, GOAL, {"ready": True}, {})
    session.open()
    try:
        assert _status(ws)["data"]["liveness"] == "starting"
        with acquire_run_lock(canonical, run_id=LID):
            session.run_started(LID, None)
            live = _status(ws)["data"]
            assert live["liveness"] == "live" and live["workspace_lock"]["held_by_this_launch"] is True
        assert _status(ws)["data"]["liveness"] == "interrupted", "RUNNING but the lock is free: the process died without sealing - not success"
    finally:
        launch_module.deactivate(session)


def test_another_runs_lock_does_not_make_this_launch_live(ws):
    canonical = canonical_workspace(str(ws))
    session = LaunchSession(canonical, workspace_identity(canonical), LID, GOAL, {"ready": True}, {})
    session.open()
    launch_module.deactivate(session)
    session.run_started(LID, None)
    with acquire_run_lock(canonical, run_id="20261010T000000-00000000"):
        data = _status(ws)["data"]
    assert data["workspace_lock"]["held"] is True and data["workspace_lock"]["held_by_this_launch"] is False and data["liveness"] == "interrupted"


def test_an_unknown_launch_is_launch_not_found_and_a_bad_id_is_invalid(ws):
    assert _status(ws, "20261010T101010-00000000")["error"]["code"] == policy.LAUNCH_NOT_FOUND
    assert _status(ws, "../../etc/passwd")["error"]["code"] == policy.INVALID_REQUEST


def test_a_launch_record_owned_by_another_workspace_is_not_trusted(ws, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    canonical = canonical_workspace(str(ws))
    session = LaunchSession(canonical, workspace_identity(canonical), LID, GOAL, {"ready": True}, {})
    session.open()
    launch_module.deactivate(session)
    target = launch_record_path(canonical_workspace(str(other)), LID)
    os.makedirs(os.path.dirname(target))
    os.replace(launch_record_path(canonical, LID), target)
    assert _status(other)["error"]["code"] == policy.EVIDENCE_STORE_UNREADABLE


# ------------------------------------------------------------------ transports

def test_the_new_operations_are_allowlisted_with_typed_arguments_and_answer_the_same_through_both_transports(ws):
    assert {"execution.preflight", "launch.status"} <= set(policy.OPERATIONS)
    assert serve.ARGUMENTS["execution.preflight"] == frozenset({"workspace"})
    assert serve.ARGUMENTS["launch.status"] == frozenset({"workspace", "launch_id"})
    assert policy.LAUNCH_NOT_FOUND in policy.ERROR_CODES
    one_shot = CliRunner().invoke(main, ["traces", "--json", "--execution-preflight", str(ws)])
    assert one_shot.exit_code == 0
    served = json.loads(serve.answer({"id": "r1", "operation": "execution.preflight", "arguments": {"workspace": str(ws)}}, None, None))
    assert served["ok"] is True and served["id"] == "r1"
    assert json.loads(one_shot.output)["data"] == served["envelope"]["data"], "the same operation implementation answers both transports"
    one_status = CliRunner().invoke(main, ["traces", "--json", "--launch-status", LID, "--workspace", str(ws)])
    served_status = json.loads(serve.answer({"id": "r2", "operation": "launch.status", "arguments": {"workspace": str(ws), "launch_id": LID}}, None, None))
    assert json.loads(one_status.output)["error"] == served_status["envelope"]["error"] and served_status["envelope"]["error"]["code"] == policy.LAUNCH_NOT_FOUND


# ------------------------------------------------------------------ approval required (GUI M7: never auto-approved, recorded honestly)

def _open_session(ws):
    canonical = canonical_workspace(str(ws))
    session = LaunchSession(canonical, workspace_identity(canonical), LID, GOAL, {"ready": True}, {})
    session.open()
    return canonical, session


def test_a_decision_the_desktop_cannot_make_is_recorded_with_kriyas_reason_and_the_first_one_is_kept(ws):
    canonical, session = _open_session(ws)
    try:
        launch_module.note_approval_required("change_approval", "touches a sensitive path", {"files": ["a", "b"]})
        launch_module.note_approval_required("skill_gap_decision", "later one", {"skills": ["x"]})
    finally:
        session.finish(1, None, "Aborted!")
    record = load_launch_record(canonical, LID)
    assert record["approval_required"]["kind"] == "change_approval" and record["approval_required"]["details"] == {"files": ["a", "b"]}
    assert record["approval_required"]["reason"] == "touches a sensitive path"
    assert _status(ws)["data"]["launch"]["approval_required"]["kind"] == "change_approval"


def test_the_note_is_a_noop_outside_a_desktop_launch(ws):
    assert launch_module.note_approval_required("change_approval", "x", {}) is False  # no active session: nothing raised, nothing written, the caller may prompt
    assert not os.path.exists(os.path.join(canonical_workspace(str(ws)), ".kriya"))


def test_inside_a_launch_the_approval_note_tells_the_caller_to_decline_instead_of_prompting(ws):
    """MEASURED (second live smoke): the callback prompted, click.confirm found no terminal and raised Abort, the workflow counted it as a failed attempt and
    retried three more times (quality_gates_exhausted). The note must report 'inside a launch' so the callback returns False (-> human_rejected)."""
    _canonical, session = _open_session(ws)
    try:
        assert launch_module.note_approval_required("change_approval", "review policy", {"files": ["a"]}) is True
    finally:
        session.finish(1, None, None)


def test_the_launch_links_the_trace_row_whose_recorded_store_names_its_run_not_the_results_run_id(ws, monkeypatch):
    """MEASURED: the workflow result's run_id was a checkpoint id (20261011T082819-2cada8bc) while the traces.db row was 34ad53c0, so the cockpit selected a run
    that does not exist. The link is the row's own evidence.attempt_store event, the same one the read side resolves."""
    import kriya.cli as cli
    from kriya.core.trace import TraceLogger

    def fake(ctx, goal, file, yes, knowledge_policy, ack, resume, resume_id, json_output, milestones, output, **kwargs):
        with begin_mutating_run(os.getcwd()) as context:
            store = {"kind": "evidence.attempt_store", "details": {"run_id": context.run_id, "status": "OPEN"}}
            other = {"kind": "evidence.attempt_store", "details": {"run_id": "20200101T000000-00000000", "status": "OPEN"}}
            db = os.path.join(os.environ["KRIYA_STATE_DIR"], "traces.db")
            TraceLogger(db).log_run(run_id="34ad53c0", goal="x", duration_sec=1.0, attempts=1, status="failure", files_modified=[], run_events=[store])
            TraceLogger(db).log_run(run_id="aaaa1111", goal="x", duration_sec=1.0, attempts=1, status="failure", files_modified=[], run_events=[other])
            output.result = {"status": "failure", "quality_gates_passed": False, "run_id": "20261011T082819-2cada8bc", "failure_category": "quality_gates_exhausted"}
        sys.exit(1)

    monkeypatch.setattr(cli, "_generate_impl", fake)
    result = _launch(ws, monkeypatch)
    assert result.exit_code == 1, result.output
    assert load_launch_record(canonical_workspace(str(ws)), LID)["ended"]["trace_run_id"] == "34ad53c0"


def test_a_strict_knowledge_gap_stop_is_recorded_as_an_acknowledgement_the_desktop_cannot_give(ws):
    canonical, session = _open_session(ws)
    session.finish(1, {"status": "knowledge_gap", "gap_report": {"gaps": [{"library": "org.apache.ignite:ignite-core"}]}}, None)
    needed = load_launch_record(canonical, LID)["approval_required"]
    assert needed["kind"] == "knowledge_gap_acknowledgement" and needed["details"] == {"libraries": ["org.apache.ignite:ignite-core"]}


def test_a_launch_that_needs_no_decision_records_none(ws):
    canonical, session = _open_session(ws)
    session.finish(0, {"status": "success", "quality_gates_passed": True, "run_id": "ab12cd34"}, None)
    assert load_launch_record(canonical, LID)["approval_required"] is None


def test_every_interactive_callback_of_generate_records_the_decision_before_it_would_prompt():
    import inspect

    import kriya.cli as cli
    source = inspect.getsource(cli._generate_impl)
    # a decision that STOPS the run is recorded as an approval requirement; the four the run can proceed without are declined and recorded, never prompted
    expectations = {"on_approval": "note_approval_required(", "on_skill_gap": "decline_decision(", "on_skill_conflict": "decline_decision(",
                    "on_web_lookup": "decline_decision(", "on_web_lookup_query": "decline_decision("}
    for callback, call in expectations.items():
        body = source[source.index(f"    def {callback}("):]
        body = body[: body.index("\n    def ", 10)] if "\n    def " in body[10:] else body
        prompts = [body.index(marker) for marker in ("click.confirm(", "click.prompt(") if marker in body]
        assert call in body, f"{callback} must CALL {call}"
        assert body.index(call) < min(prompts), f"{callback} must record the decision BEFORE it can prompt"
    approval = source[source.index("    def on_approval("):]
    approval = approval[: approval.index("\n    def ", 10)]
    assert re.search(r"if note_approval_required\(.*\):\s*\n\s*return False", approval), "on_approval must DECLINE inside a launch (a prompt with no terminal is a retried failure)"
    assert "True, KNOWLEDGE_POLICY" not in inspect.getsource(cli._launched_generate)


def test_a_declined_decision_is_recorded_informationally_and_never_becomes_an_approval_requirement(ws):
    canonical, session = _open_session(ws)
    try:
        assert launch_module.decline_decision("skill_gap_decision", "no verified skill for lib X", {"skills": ["lib-x"]}) is True
    finally:
        session.finish(0, {"status": "success", "quality_gates_passed": True, "run_id": "ab12cd34"}, None)
    record = load_launch_record(canonical, LID)
    assert record["approval_required"] is None
    assert [d["kind"] for d in record["declined_decisions"]] == ["skill_gap_decision"] and record["declined_decisions"][0]["details"] == {"skills": ["lib-x"]}
    assert _status(ws)["data"]["launch"]["declined_decisions"][0]["reason"] == "no verified skill for lib X"


def test_declining_outside_a_desktop_launch_changes_nothing_and_tells_the_caller_to_prompt_as_before(ws):
    assert launch_module.decline_decision("web_lookup_review", "x", {}) is False
    assert not os.path.exists(os.path.join(canonical_workspace(str(ws)), ".kriya"))


def test_declined_decisions_are_bounded(ws):
    canonical, session = _open_session(ws)
    try:
        for i in range(40):
            launch_module.decline_decision("skill_gap_decision", f"n{i}", {})
    finally:
        session.finish(0, {"status": "success"}, None)
    assert len(load_launch_record(canonical, LID)["declined_decisions"]) == 20


# ------------------------------------------------------------------ toolchain visibility (reported, never widened)

def test_the_preflight_reports_tool_visibility_under_the_processs_own_environment(ws, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    tools = {t["tool"]: t for t in _preflight(ws)["data"]["toolchains"]["tools"]}
    assert set(tools) == {"python3", "git", "java", "mvn", "gradle", "node"}
    for entry in tools.values():
        assert entry["available"] == (entry["path"] is not None)
    assert "no login shell" in _preflight(ws)["data"]["toolchains"]["note"]
    monkeypatch.setenv("PATH", "/nonexistent")
    nothing = {t["tool"]: t["available"] for t in _preflight(ws)["data"]["toolchains"]["tools"] if t["tool"] != "java"}
    assert not any(nothing.values())


def test_tool_visibility_never_changes_the_environment_or_blocks_the_start(ws, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    before = dict(os.environ)
    data = _preflight(ws)["data"]
    assert [b["code"] for b in data["blockers"]] == ["TOOL_UNAVAILABLE"], "only git (needed by every run's sandbox) blocks; a missing python3/java/mvn is information"
    assert "git" in data["blockers"][0]["message"]
    assert dict(os.environ) == before


def test_a_tool_that_exists_on_path_but_does_not_run_is_reported_unavailable_with_the_reason(ws, monkeypatch, tmp_path):
    """The macOS Xcode shims (/usr/bin/git, /usr/bin/python3 without developer tools) exist and fail with 69: 'found on PATH' must not read as 'usable'."""
    broken = tmp_path / "bin"
    broken.mkdir()
    for name in ("python3", "git"):
        (broken / name).write_text("#!/bin/sh\nexit 69\n")
        (broken / name).chmod(0o755)
    (broken / "node").write_text("#!/bin/sh\necho v1.0.0\n")
    (broken / "node").chmod(0o755)
    monkeypatch.setenv("PATH", str(broken))
    tools = {t["tool"]: t for t in _preflight(ws)["data"]["toolchains"]["tools"]}
    assert tools["python3"]["available"] is False and "69" in tools["python3"]["reason"]
    assert tools["git"]["available"] is False
    assert tools["node"]["available"] is True and tools["node"]["checked"] == "ran --version"
    assert tools["mvn"]["available"] is False and tools["mvn"]["path"] is None


def test_the_FIRST_tool_on_path_decides_a_working_copy_later_is_only_mentioned(ws, monkeypatch, tmp_path):
    """A run executes the first `git` on PATH: a working one later on the same PATH must not make a broken first one look usable."""
    shim, good = tmp_path / "shim", tmp_path / "good"
    for directory, body in ((shim, "echo 'You have not agreed to the Xcode license agreements.' >&2; exit 69"), (good, "echo git version 2.0")):
        directory.mkdir()
        (directory / "git").write_text(f"#!/bin/sh\n{body}\n")
        (directory / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim}:{good}")
    data = _preflight(ws)["data"]
    entry = {t["tool"]: t for t in data["toolchains"]["tools"]}["git"]
    assert entry["available"] is False and entry["alternatives"] == [str(good / "git")]
    assert "Xcode license" in entry["reason"] and "69" in entry["reason"]


def test_a_tool_git_that_does_not_run_blocks_the_start_with_the_reason_and_the_alternative(ws, monkeypatch, tmp_path):
    """Traced from the live smoke: every run needs git for its isolated sandbox, so the start is refused up front instead of failing at the first step."""
    shim, good = tmp_path / "shim", tmp_path / "good"
    for directory, body in ((shim, "echo 'Xcode license not accepted' >&2; exit 69"), (good, "echo git version 2.0")):
        directory.mkdir()
        (directory / "git").write_text(f"#!/bin/sh\n{body}\n")
        (directory / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim}:{good}")
    data = _preflight(ws)["data"]
    assert data["ready"] is False
    blocker = next(b for b in data["blockers"] if b["code"] == "TOOL_UNAVAILABLE")
    assert "git is required for every run" in blocker["message"] and "Xcode license not accepted" in blocker["message"] and str(good / "git") in blocker["message"]


def test_a_tool_git_working_first_on_path_does_not_block(ws, monkeypatch, tmp_path):
    good = tmp_path / "good"
    good.mkdir()
    (good / "git").write_text("#!/bin/sh\necho git version 2.0\n")
    (good / "git").chmod(0o755)
    monkeypatch.setenv("PATH", str(good))
    data = _preflight(ws)["data"]
    assert not [b for b in data["blockers"] if b["code"] == "TOOL_UNAVAILABLE"]


def test_preflight_reports_the_configured_autonomy_mode_and_never_changes_it():
    from kriya.kup.execution import _interaction

    assert _interaction(AppConfig())["approvals"] == "change_review"
    autonomous = AppConfig()
    autonomous.autonomy.mode = "autonomous"
    shown = _interaction(autonomous)
    assert shown["approvals"] == "configured_mode" and shown["autonomy_mode"] == "autonomous" and "autonomous" in shown["note"]
    assert autonomous.autonomy.mode == "autonomous", "the report must not alter the configuration"
    assert _interaction(None)["approvals"] == "unknown"
