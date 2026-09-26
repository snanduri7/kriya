"""A Developer answer of `[]` (a file-list protocol answer) is never file content.

handover/DEFECT_DEVELOPER_EMPTY_ARRAY_WRITTEN_AS_FILE.md: in MODE:
REPAIR_WITH_FULL_FILE the Developer answered `[]` and the two characters
replaced calc.py. `[]` is valid Python and the repository had no tests, so
every gate passed and the destroyed file was committed as SUCCESS.

Proved at the producer (DeveloperAgent), at the operation contract, and end
to end through both execution paths - a direct `generate` and a milestone
sequence - with a real workflow, real gates and the real commit seam.
"""
import asyncio
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from _milestone_proof_harness import _milestone, _run, git_workspace  # noqa: F401 - pytest fixture

from kriya.agents.agent import FILE_LIST_PROTOCOL_ANSWER_AS_CONTENT, DeveloperAgent
from kriya.config import AppConfig
from kriya.control.persistence import scan_run_records
from kriya.control.run_record import RunLifecycle
from kriya.core import LLMClient
from kriya.core.kernel import Kernel
from kriya.workflow.failure import Failure
from kriya.workflow.operations import CodeOperation, all_results_are_no_change, validate_operation_result
from kriya.workflow.workflow import WorkflowEngine

CALC = "def add(a, b):\n    return a + b\n"


# --- the detector -----------------------------------------------------------

@pytest.mark.parametrize("content", [
    "[]", "[ ]\n", '["calc.py"]', '[{"filepath": "calc.py"}]',
    "{}", '{"files": []}', '{"files": [{"path": "calc.py"}]}', '{"path": "calc.py"}',
])
def test_file_list_protocol_answers_are_detected_for_source_files(content):
    error = DeveloperAgent._file_list_protocol_answer_error(content, "src/calc.py")
    assert error and error.startswith(FILE_LIST_PROTOCOL_ANSWER_AS_CONTENT)


@pytest.mark.parametrize("content", [
    CALC, "x = []\n", "[1, 2]  # not JSON\n", '"a string"', "42", "", "null",
    '{"name": "calc"}',  # a JSON object without envelope keys is not a file-list answer
])
def test_real_or_non_envelope_content_is_not_a_protocol_answer(content):
    assert DeveloperAgent._file_list_protocol_answer_error(content, "calc.py") is None


@pytest.mark.parametrize("filepath,content", [
    ("data.json", "[]"),
    ("config.yaml", "[]"),
    ("conf/settings.yml", "{}"),
    ("package.json", '{"name": "pkg", "files": ["dist"]}'),
    (".watchmanconfig", "{}"),
    ("web/.babelrc", "{}"),
])
def test_data_format_targets_keep_json_shaped_content(filepath, content):
    assert DeveloperAgent._file_list_protocol_answer_error(content, filepath) is None


# --- the producer -------------------------------------------------------------

async def _generate(response, filepath="calc.py", operation=CodeOperation.REPAIR_WITH_FULL_FILE, **kwargs):
    llm = LLMClient(AppConfig())
    llm.complete = AsyncMock(return_value=response)
    return await DeveloperAgent("developer", llm).run_generation(
        "Task", "Design", "Existing code",
        known_target_files=[filepath],
        operation_by_file={filepath: operation},
        **kwargs,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("response", ["[]", "```json\n[]\n```", '{"files": []}'])
async def test_full_file_repair_answering_a_file_list_carries_no_content(response):
    [entry] = await _generate(response)
    assert entry["content"] is None
    assert entry["protocol_reason_code"] == FILE_LIST_PROTOCOL_ANSWER_AS_CONTENT
    assert entry["protocol_error"].startswith(FILE_LIST_PROTOCOL_ANSWER_AS_CONTENT)


@pytest.mark.asyncio
async def test_create_full_file_answering_an_empty_array_carries_no_content():
    [entry] = await _generate("[]", filepath="new_module.py", operation=CodeOperation.CREATE_FULL_FILE)
    assert entry["content"] is None
    assert entry["protocol_reason_code"] == FILE_LIST_PROTOCOL_ANSWER_AS_CONTENT


@pytest.mark.asyncio
async def test_retry_file_content_block_holding_an_empty_array_carries_no_content():
    [entry] = await _generate(
        "FIX ANALYSIS: nothing to change.\nFILE CONTENT:\n[]",
        prior_error_context="compile failed", operation=CodeOperation.REPAIR_WITH_FULL_FILE,
    )
    assert entry["content"] is None
    assert entry["protocol_reason_code"] == FILE_LIST_PROTOCOL_ANSWER_AS_CONTENT


@pytest.mark.asyncio
async def test_an_envelope_with_real_content_is_still_unwrapped():
    [entry] = await _generate('[{"filepath": "calc.py", "content": "x = 1\\n"}]')
    assert entry["content"] == "x = 1\n"
    assert "protocol_error" not in entry


@pytest.mark.asyncio
async def test_a_data_file_may_legitimately_become_an_empty_array():
    [entry] = await _generate("[]", filepath="data.json")
    assert entry["content"] == "[]"
    assert "protocol_error" not in entry


# --- the operation contract ---------------------------------------------------

def test_the_rejected_entry_is_a_contract_failure_never_a_no_change_assessment():
    entry = {
        "filepath": "calc.py", "content": None,
        "protocol_error": f"{FILE_LIST_PROTOCOL_ANSWER_AS_CONTENT}: ...",
        "protocol_reason_code": FILE_LIST_PROTOCOL_ANSWER_AS_CONTENT,
    }
    _, error = validate_operation_result(
        entry, expected=CodeOperation.REPAIR_WITH_FULL_FILE, file_exists=True,
    )
    assert error and FILE_LIST_PROTOCOL_ANSWER_AS_CONTENT in error
    # Independent of call order: a rejected response never counts as "no change".
    assert not all_results_are_no_change([entry])
    assert all_results_are_no_change([{"filepath": "calc.py", "content": None}])


# --- end to end: both execution paths ------------------------------------------

def _config():
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    return cfg


def _role_llm(cfg, developer_calls):
    """Every Developer completion answers `[]`; other roles answer plausibly."""
    async def complete(system_prompt, *args, **kwargs):
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        if "Developer Agent" in first:
            developer_calls.append(system_prompt)
            return "[]"
        if "File List Planner" in first:
            return '["calc.py"]'
        if "Planner Agent" in first:
            return "Step 1: verify calc.py add"
        if "Architect Agent" in first:
            return "Design: calc.py already has add"
        return "Review: Approved"

    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=complete)
    return llm


@pytest.fixture
def calc_workspace(git_workspace):  # noqa: F811
    (git_workspace / "calc.py").write_text(CALC)
    subprocess.run(["git", "add", "calc.py"], cwd=git_workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "calc"], cwd=git_workspace, check=True)
    return git_workspace


@pytest.fixture
def contract_failures(monkeypatch):
    """Every Failure turned into a gate outcome, recorded without replacing
    any class the workflow raises or catches."""
    recorded = []
    original = Failure.to_gate_outcome

    def recording(self):
        recorded.append(self)
        return original(self)

    monkeypatch.setattr(Failure, "to_gate_outcome", recording)
    return recorded


def _assert_untouched_and_never_committed(workspace, failures, developer_calls):
    assert Path(workspace, "calc.py").read_text() == CALC
    assert developer_calls, "the Developer was never asked - the scenario did not run"
    typed = [
        f for f in failures
        if f.type == "operation_contract"
        and (f.diagnostics or {}).get("reason_code") == FILE_LIST_PROTOCOL_ANSWER_AS_CONTENT
    ]
    assert typed, [(f.type, f.diagnostics) for f in failures]
    records = scan_run_records(str(workspace)).records
    assert records
    for record in records:
        assert record.lifecycle_state is not RunLifecycle.SUCCESS
        assert all(cycle.get("result") != "COMMITTED" for cycle in record.commits), record.commits


def test_direct_generate_never_writes_an_empty_array_over_a_file(calc_workspace, contract_failures):
    cfg = _config()
    developer_calls = []
    engine = WorkflowEngine(Kernel(config=cfg), _role_llm(cfg, developer_calls))
    engine.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})

    result = asyncio.run(engine.run_generation_workflow(
        goal="ensure calc.py has add", workspace_path=str(calc_workspace),
    ))

    assert not result["quality_gates_passed"]
    assert "MODE: REPAIR_WITH_FULL_FILE" in developer_calls[0]
    _assert_untouched_and_never_committed(calc_workspace, contract_failures, developer_calls)


def test_milestone_sequence_never_writes_an_empty_array_over_a_file(calc_workspace, contract_failures):
    cfg = _config()
    developer_calls = []
    engine = WorkflowEngine(Kernel(config=cfg), _role_llm(cfg, developer_calls))
    engine.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})

    result, state = _run(calc_workspace, [_milestone("M1")], engine)

    assert result["status"] != "success", result
    assert "M1" not in state.completed_milestone_ids
    _assert_untouched_and_never_committed(calc_workspace, contract_failures, developer_calls)
