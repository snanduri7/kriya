"""RunService and ApprovalService (full modularization, slice B3): the service answers are the KUP/CLI answers, on success and refusal.

The envelopes themselves are pinned byte for byte by tests/test_application_write_services.py (goldens written before the extraction);
this file pins the service boundary: what each method returns or raises is exactly what the adapter serializes, ``start`` composes the
run from the names the CLI hands it (the documented ``kriya.cli`` patch points) in the CLI's construction order, ``mark_in_progress``
really writes its trace row (its broad catch must not hide a coding error), and ``ApprovalService.decide`` reaches the authority's own
stale-candidate refusal with the reviewer's identities, never a substituted one.
"""
from __future__ import annotations

import asyncio
import sqlite3
from typing import Any, Dict, List

import pytest
from _review_channel_host import attach_review_channel, channel_decide

from kriya.application import (
    ApplicationRefusal,
    ApprovalRefused,
    ApprovalService,
    ReviewDecisionRequest,
    RunArtifacts,
    RunService,
    RunServiceDeps,
)
from kriya.config import AppConfig
from kriya.context import RequestContext
from kriya.context.workspace_identity import canonical_workspace, workspace_identity
from kriya.control import launch as launch_module
from kriya.control.launch import LaunchSession
from kriya.control.persistence import load_launch_record
from kriya.control.run_coordinator import begin_mutating_run
from kriya.core.state_paths import trace_db_path
from kriya.kup import execution
from kriya.kup.cli_ops import run_kup_operation
from kriya.policy.approval import ApprovalOutcome

LID = "20261010T101010-abcdef01"
TOOLS = [{"tool": "git", "available": True, "path": "/usr/bin/git", "checked": "ran --version"}]
FILES = [{"filepath": "hello.py", "content": "--- a/hello.py\n+++ b/hello.py\n@@ -1 +1 @@\n-x\n+y\n"}]


@pytest.fixture(autouse=True)
def _stable(monkeypatch):
    monkeypatch.setattr("kriya.kup.execution.tool_visibility", lambda: [dict(t) for t in TOOLS])


@pytest.fixture
def ws(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    return canonical_workspace(str(folder))


def _ctx(ws: str) -> RequestContext:
    return RequestContext.local(ws, request_id="t")


# ------------------------------------------------------------------ preflight / status

def test_preflight_through_the_service_is_the_kup_answer(ws):
    data = RunService().preflight(_ctx(ws), AppConfig(), ws, tool_visibility=execution.tool_visibility)
    envelope = run_kup_operation(AppConfig(), {"operation": "execution.preflight", "workspace": ws})
    assert envelope["error"] is None and envelope["data"] == data
    assert data["ready"] is True and data["toolchains"]["tools"] == TOOLS, "the tool probe handed in is the one reported"
    assert envelope["source"]["state_directory"] == data["state"]["cockpit_directory"]


def test_the_launched_process_document_is_the_preflight_seen_from_nowhere(ws):
    """``kriya generate --launch-id`` re-resolves the document with no cockpit directory: the same blockers and facts, observability unknown."""
    seen = RunService().preflight(_ctx(ws), AppConfig(), ws, tool_visibility=execution.tool_visibility)
    own = RunService().build_preflight(_ctx(ws), ws, None, tool_visibility=execution.tool_visibility)
    assert own["state"]["cockpit_directory"] is None and own["state"]["observable_by_cockpit"] is None
    assert {k: v for k, v in own.items() if k != "state"} == {k: v for k, v in seen.items() if k != "state"}


def test_an_unusable_cockpit_state_directory_is_a_typed_refusal_on_the_wire(ws, monkeypatch):
    monkeypatch.setenv("KRIYA_STATE_DIR", "relative/state")
    with pytest.raises(ApplicationRefusal) as caught:
        RunService().preflight(_ctx(ws), AppConfig(), ws, tool_visibility=execution.tool_visibility)
    assert caught.value.reason_code == "CONFIG_LOAD_FAILED" and caught.value.extra == {}
    envelope = run_kup_operation(AppConfig(), {"operation": "execution.preflight", "workspace": ws})
    assert envelope["data"] is None and envelope["error"] == {"code": "CONFIG_LOAD_FAILED", "message": caught.value.message, "database_state": None}


def test_status_through_the_service_is_the_kup_answer_and_a_missing_launch_is_refused(ws):
    session = LaunchSession(ws, workspace_identity(ws), LID, "goal", {"ready": True}, {})
    attach_review_channel(session)
    session.open()
    session.finish(0, {"status": "success", "quality_gates_passed": True, "run_id": "ab12cd34"}, None)
    data = RunService().status(_ctx(ws), AppConfig(), ws, LID)
    envelope = run_kup_operation(AppConfig(), {"operation": "launch.status", "workspace": ws, "launch_id": LID})
    assert envelope["error"] is None and envelope["data"] == data and data["liveness"] == "ended"
    with pytest.raises(ApplicationRefusal) as caught:
        RunService().status(_ctx(ws), AppConfig(), ws, "20201010T101010-00000000")
    assert (caught.value.reason_code, caught.value.extra) == ("LAUNCH_NOT_FOUND", {"reason": "not_recorded"})


# ------------------------------------------------------------------ start / mark_in_progress

class _Recorder:
    def __init__(self) -> None:
        self.calls: List[str] = []

    def deps(self) -> RunServiceDeps:
        recorder = self

        class LLM:
            def __init__(self, cfg):
                recorder.calls.append("llm")
                self.cfg = cfg

        class Kern:
            def __init__(self, config):
                recorder.calls.append("kernel")
                self.config = config

        class Engine:
            def __init__(self, kernel, llm):
                recorder.calls.append("engine")
                self.kernel, self.llm = kernel, llm

        return RunServiceDeps(LLMClient=LLM, Kernel=Kern, WorkflowEngine=Engine)


class _Bundle:
    def authority(self) -> str:
        return "the-authority"


def test_start_composes_the_run_from_the_names_it_is_handed_in_the_cli_order_and_binds_every_artifact(ws):
    recorder, cfg = _Recorder(), AppConfig()
    artifacts = RunArtifacts(acceptance="acc", acceptance_approval="appr", requirement_contract="contract", requirement_dispositions="disp",
                             verification_authority_bundle=_Bundle())
    handle = RunService().start(_ctx(ws), cfg, artifacts, recorder.deps())
    assert recorder.calls == ["llm", "kernel", "engine"]
    assert handle.engine.kernel is handle.kernel and handle.engine.llm is handle.llm and handle.llm.cfg is cfg and handle.kernel.config is cfg
    bound: Dict[str, Any] = {name: getattr(handle.engine, name) for name in ("acceptance", "acceptance_approval", "requirement_contract",
                                                                            "requirement_dispositions", "verification_authorities")}
    assert bound == {"acceptance": "acc", "acceptance_approval": "appr", "requirement_contract": "contract", "requirement_dispositions": "disp",
                     "verification_authorities": ("the-authority",)}
    assert handle.engine.verification_authority_bundle is artifacts.verification_authority_bundle
    empty = RunService().start(_ctx(ws), cfg, RunArtifacts(), recorder.deps()).engine
    assert (empty.acceptance, empty.verification_authority_bundle, empty.verification_authorities) == (None, None, ())


def _trace_rows(cfg) -> List[tuple]:
    conn = sqlite3.connect(trace_db_path(cfg))
    try:
        return conn.execute("SELECT run_id, status, attempts FROM runs").fetchall()
    finally:
        conn.close()


def test_mark_in_progress_writes_the_in_progress_row_and_a_trace_failure_is_only_a_warning(ws, monkeypatch, caplog):
    cfg = AppConfig()
    RunService().mark_in_progress(_ctx(ws), cfg, None, "goal")  # no run id: nothing to mark
    RunService().mark_in_progress(_ctx(ws), cfg, "run-123", "goal")
    assert _trace_rows(cfg) == [("run-123", "in_progress", 0)], "the normal path writes exactly the marker (a coding error here must fail this test)"

    def broken(_cfg):
        raise OSError("disk gone")

    monkeypatch.setattr("kriya.application.run.trace_db_path", broken)
    RunService().mark_in_progress(_ctx(ws), cfg, "run-456", "goal")
    assert "Failed to mark run 'run-456' in_progress: disk gone" in caplog.text


# ------------------------------------------------------------------ ApprovalService.decide

def _held(ws: str, scenario):
    session = LaunchSession(ws, workspace_identity(ws), LID, "goal", {"ready": True}, {})
    attach_review_channel(session)
    session.open()
    out: Dict[str, Any] = {}

    async def main_():
        task = asyncio.ensure_future(launch_module.request_change_approval(FILES, "review"))
        for _ in range(500):
            record = load_launch_record(ws, LID)
            if record and record.get("pending_approval"):
                break
            await asyncio.sleep(0.01)
        out["scenario"] = await asyncio.to_thread(scenario, record["pending_approval"])
        out["approved"] = await asyncio.wait_for(task, 20)

    try:
        with begin_mutating_run(ws):
            asyncio.run(main_())
    finally:
        session.finish(0, None, None)
    return out


def test_the_service_decide_is_the_retired_file_action_and_the_review_is_decided_over_the_channel(ws):
    """D2 M2: ApprovalService.decide reaches the owner's retired action, which records nothing and answers the typed CHANNEL_REQUIRED - for a valid request as for a stale one.
    The review is decided over the launch's private channel (deleted with `kriya launch-decision` in slice 4)."""
    def scenario(pending):
        service, ctx = ApprovalService(), _ctx(ws)
        codes = []
        for digest, review in (("sha256:" + "0" * 64, pending["review_id"]), (pending["candidate_digest"], pending["review_id"])):
            with pytest.raises(ApprovalRefused) as caught:
                service.decide(ctx, ReviewDecisionRequest(LID, review, digest, "approve", "change_approval"))
            codes.append(caught.value.code)
        return codes, channel_decide(str(ws), LID, pending["review_id"], pending["candidate_digest"], "approve")

    out = _held(ws, scenario)
    codes, accepted = out["scenario"]
    assert codes == ["CHANNEL_REQUIRED", "CHANNEL_REQUIRED"], "the action no longer decides anything"
    assert accepted["accepted"] is True and accepted["decision"] == "approve" and out["approved"] == ApprovalOutcome.approve()


def test_a_decision_needs_a_workspace():
    with pytest.raises(ValueError):
        ApprovalService().decide(RequestContext.local(None), ReviewDecisionRequest(LID, "ra-" + "0" * 24, "sha256:" + "0" * 64, "approve",
                                                                                   "change_approval"))
