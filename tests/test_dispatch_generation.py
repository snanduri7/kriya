"""MA7.1: _dispatch_generation (kriya/cli.py) - routes `kriya generate`
through WorkflowController when workflow_controller.enabled, otherwise a
pure passthrough to WorkflowEngine.run_generation_workflow. Module-level
(not a nested closure) specifically so it's independently testable here.

Also covers _dispatch_milestones (MA7-C4, 2026-08-25 external review) -
the identical gate/shape for `generate --from-milestones`, closing the
architectural split where that CLI path called run_milestones() directly,
bypassing WorkflowController even when workflow_controller.enabled was
true for every other generate call."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from kriya.cli import _dispatch_generation, _dispatch_milestones
from kriya.workflow.workflow_types import WorkflowResult


@pytest.mark.asyncio
async def test_disabled_is_a_pure_passthrough_to_run_generation_workflow():
    we = MagicMock()
    we.run_generation_workflow = AsyncMock(return_value={"status": "success", "run_id": "abc"})
    cfg = MagicMock()
    cfg.workflow_controller.enabled = False

    res = await _dispatch_generation(we, cfg, goal="do x", workspace_path="/tmp/proj")

    assert res == {"status": "success", "run_id": "abc"}
    we.run_generation_workflow.assert_awaited_once_with(goal="do x", workspace_path="/tmp/proj")


@pytest.mark.asyncio
async def test_enabled_routes_through_workflow_controller_and_returns_legacy_result(monkeypatch):
    we = MagicMock()
    cfg = MagicMock()
    cfg.workflow_controller.enabled = True
    cfg.workflow_controller.mode = "shadow"

    fake_result = WorkflowResult(
        run_id="r1", control_state=None, route=None,
        legacy_result={"status": "success", "run_id": "r1"},
    )
    captured = {}

    async def fake_execute(self, **kwargs):
        captured.update(kwargs)
        captured["workflow_engine"] = self.workflow_engine
        return fake_result

    monkeypatch.setattr("kriya.cli.WorkflowController.execute", fake_execute)

    res = await _dispatch_generation(we, cfg, goal="do y", workspace_path="/tmp/proj2")

    assert res == {"status": "success", "run_id": "r1"}
    assert captured["migration_mode"] == "shadow"
    assert captured["goal"] == "do y"
    assert captured["workflow_engine"] is we


@pytest.mark.asyncio
async def test_enabled_passes_the_configured_mode_through_unchanged(monkeypatch):
    we = MagicMock()
    cfg = MagicMock()
    cfg.workflow_controller.enabled = True
    cfg.workflow_controller.mode = "legacy"

    fake_result = WorkflowResult(run_id="r1", control_state=None, route=None, legacy_result={"status": "success"})
    captured = {}

    async def fake_execute(self, **kwargs):
        captured.update(kwargs)
        return fake_result

    monkeypatch.setattr("kriya.cli.WorkflowController.execute", fake_execute)

    await _dispatch_generation(we, cfg, goal="do z", workspace_path="/tmp/proj3")

    assert captured["migration_mode"] == "legacy"


@pytest.mark.asyncio
async def test_dispatch_milestones_disabled_is_a_pure_passthrough_to_run_milestones(monkeypatch):
    we = MagicMock()
    cfg = MagicMock()
    cfg.workflow_controller.enabled = False
    run_state = MagicMock()

    fake_run_milestones = AsyncMock(return_value={"status": "success"})
    monkeypatch.setattr("kriya.workflow.milestones.run_milestones", fake_run_milestones)

    res = await _dispatch_milestones(we, cfg, run_state, "/tmp/proj", knowledge_risk_confirmed=True)

    assert res == {"status": "success"}
    fake_run_milestones.assert_awaited_once_with(we, run_state, "/tmp/proj", knowledge_risk_confirmed=True)


@pytest.mark.asyncio
async def test_dispatch_milestones_enabled_routes_through_workflow_controller(monkeypatch):
    we = MagicMock()
    cfg = MagicMock()
    cfg.workflow_controller.enabled = True
    run_state = MagicMock()

    fake_result = WorkflowResult(
        run_id="grp-1", control_state=None, route=None,
        legacy_result={"status": "success", "group_id": "grp-1"},
    )
    captured = {}

    async def fake_execute_milestones(self, rs, workspace_path, **kwargs):
        captured["run_state"] = rs
        captured["workspace_path"] = workspace_path
        captured["kwargs"] = kwargs
        captured["workflow_engine"] = self.workflow_engine
        return fake_result

    monkeypatch.setattr("kriya.cli.WorkflowController.execute_milestones", fake_execute_milestones)

    res = await _dispatch_milestones(we, cfg, run_state, "/tmp/proj", knowledge_risk_confirmed=True)

    assert res == {"status": "success", "group_id": "grp-1"}
    assert captured["run_state"] is run_state
    assert captured["workspace_path"] == "/tmp/proj"
    assert captured["kwargs"] == {"knowledge_risk_confirmed": True}
    assert captured["workflow_engine"] is we
