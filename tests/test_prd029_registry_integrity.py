"""PRD-029 P0: a corrupt ContractRegistry is never read as empty and never
overwritten.

Before the fix, ``load_contract_registry`` returned an empty registry for an
unreadable file. The milestone path then saved that empty registry over it,
and resume/commit proceeded as though no contracts existed.
"""
import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest
from _strict_doubles import strict_engine
from test_milestones import mkv2

from kriya.control.contracts import CONTRACT_REGISTRY_CORRUPT, ContractRegistry, ContractRegistryCorruptError
from kriya.control.persistence import contract_registry_path, load_contract_registry, save_contract_registry
from kriya.workflow.milestones import MilestoneRunState, run_milestones


def _write_raw(workspace, text):
    path = contract_registry_path(str(workspace))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def test_missing_registry_is_a_valid_empty_registry(tmp_path):
    assert load_contract_registry(str(tmp_path)).all_records() == ()


@pytest.mark.parametrize("content", [
    "{not json",
    "[]",
    json.dumps({"contracts": []}),
    json.dumps({"contracts": {"m:cap": []}}),
    json.dumps({"contracts": {"m:cap": [{"id": "m:cap"}]}}),
])
def test_corrupt_registry_raises_typed_error_and_is_left_untouched(tmp_path, content):
    path = _write_raw(tmp_path, content)
    with pytest.raises(ContractRegistryCorruptError) as exc_info:
        load_contract_registry(str(tmp_path))
    assert exc_info.value.reason_code == CONTRACT_REGISTRY_CORRUPT
    assert exc_info.value.path == path
    with open(path, encoding="utf-8") as handle:
        assert handle.read() == content


def test_a_valid_saved_registry_round_trips(tmp_path):
    registry = ContractRegistry()
    registry.register("M1:cap", "cap", "M1", "shape")
    save_contract_registry(str(tmp_path), registry)
    assert [record.id for record in load_contract_registry(str(tmp_path)).all_records()] == ["M1:cap"]


@pytest.mark.asyncio
async def test_milestone_run_fails_closed_on_a_corrupt_registry_without_overwriting_it(tmp_path):
    path = _write_raw(tmp_path, "{corrupt")
    state = MilestoneRunState(group_id="grp", original_goal="orig", milestones=[mkv2("M1", goal="g1")])
    we = strict_engine()
    we.run_generation_workflow = AsyncMock(side_effect=AssertionError("no milestone may run"))
    we.run_verifier = MagicMock()

    result = await run_milestones(we, state, str(tmp_path))

    assert result["status"] == "needs_review"
    assert result["reason_codes"] == [CONTRACT_REGISTRY_CORRUPT]
    assert result["persistence_store"] == "contract_registry"
    assert result["quality_gates_passed"] is False
    with open(path, encoding="utf-8") as handle:
        assert handle.read() == "{corrupt"


@pytest.mark.asyncio
async def test_controller_milestone_path_fails_closed_on_a_corrupt_registry(tmp_path):
    from unittest.mock import patch

    from test_workflow_controller import _milestone_run_state, _workflow_engine

    from kriya.workflow.workflow_controller import WorkflowController

    path = _write_raw(tmp_path, "{corrupt")
    fake_run_milestones = AsyncMock(side_effect=AssertionError("must not run"))
    with patch("kriya.workflow.milestones.run_milestones", fake_run_milestones):
        result = await WorkflowController(_workflow_engine()).execute_milestones(
            _milestone_run_state(), str(tmp_path),
        )
    assert result.legacy_result["status"] == "needs_review"
    assert result.legacy_result["reason_codes"] == [CONTRACT_REGISTRY_CORRUPT]
    fake_run_milestones.assert_not_awaited()
    with open(path, encoding="utf-8") as handle:
        assert handle.read() == "{corrupt"
