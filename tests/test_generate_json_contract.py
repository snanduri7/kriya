"""PRD-003: one JSON result on every callback terminal path."""
import json
from unittest.mock import AsyncMock, patch

import pytest
from _strict_doubles import strict_kernel
from click.testing import CliRunner

from kriya.cli import main
from kriya.config import AppConfig

GAP = {"status": "knowledge_gap", "run_id": "gap-run", "gap_report": {"gaps": [
    {"library": "example", "version": "unspecified", "reason": "missing evidence", "risk_level": "high"}
]}}


@pytest.fixture
def invoke(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.logging.file_enabled = False
    cfg.logging.run_file_enabled = False
    kernel = strict_kernel(cfg)

    def run(payload=None, args=(), input_text=None, setup_error=None, workflow_error=None):
        with patch("kriya.cli.load_config", return_value=cfg), \
             patch("kriya.cli.Kernel", return_value=kernel), \
             patch("kriya.cli.LLMClient", side_effect=setup_error), \
             patch("kriya.cli.WorkflowEngine"), \
             patch("kriya.cli._dispatch_generation", new=AsyncMock(return_value=payload, side_effect=workflow_error)):
            return CliRunner().invoke(main, ["generate", "test goal", "--json", *args], input=input_text)
    return run


def test_strict_knowledge_gap_is_json(invoke):
    result = invoke(GAP, args=("--knowledge-policy", "strict"))
    assert result.exit_code == 1
    assert json.loads(result.stdout)["status"] == "knowledge_gap"
    assert "KRIYA BLOCKED" in result.stderr


def test_declined_knowledge_gap_is_json(invoke):
    result = invoke(GAP, input_text="n\nn\n")
    assert result.exit_code == 3
    assert json.loads(result.stdout)["run_id"] == "gap-run"


def test_constructor_failure_is_json(invoke):
    result = invoke(setup_error=RuntimeError("provider unavailable"))
    assert result.exit_code == 1
    assert json.loads(result.stdout)["quality_gates_passed"] is False


def test_unreadable_goal_file_is_json(invoke):
    result = invoke(args=("--file", "."))
    assert result.exit_code != 0
    assert json.loads(result.stdout)["quality_gates_passed"] is False


@pytest.mark.parametrize("payload,code", [
    ({"quality_gates_passed": True, "files": []}, 0),
    ({"quality_gates_passed": False, "files": []}, 1),
    ({"quality_gates_passed": False, "status": "human_rejected", "files": []}, 1),
    ({"quality_gates_passed": False, "environment_failure": "missing JDK", "files": []}, 1),
])
def test_workflow_result_preserved(invoke, payload, code):
    result = invoke(payload)
    assert result.exit_code == code, result.output
    assert json.loads(result.stdout) == payload


def test_workflow_exception_is_json(invoke):
    result = invoke(workflow_error=RuntimeError("controlled failure"))
    assert result.exit_code == 1
    assert json.loads(result.stdout)["quality_gates_passed"] is False


@pytest.mark.parametrize('error,code', [(KeyboardInterrupt(), 130), (EOFError('input ended'), 1)])
def test_interrupted_setup_restores_json(invoke, error, code):
    result = invoke(setup_error=error)
    assert result.exit_code == code
    assert json.loads(result.stdout)['quality_gates_passed'] is False


def test_aborted_knowledge_prompt_is_json(invoke):
    result = invoke(GAP, input_text='')
    assert result.exit_code == 1
    assert json.loads(result.stdout)['quality_gates_passed'] is False


@pytest.mark.parametrize('status,code', [('success', 0), ('failed', 1)])
def test_milestone_result_is_one_json_object(invoke, tmp_path, status, code):
    plan = tmp_path / 'plan.json'
    plan.write_text('{}')
    payload = {'status': status, 'milestones': []}
    with patch('kriya.workflow.milestones.load_or_resume_milestone_run_state'), \
         patch('kriya.cli._dispatch_milestones', new=AsyncMock(return_value=payload)):
        result = invoke(args=('--from-milestones', str(plan)))
    assert result.exit_code == code, result.output
    assert json.loads(result.stdout) == payload


def test_malformed_milestone_file_is_json(invoke, tmp_path):
    plan = tmp_path / 'plan.json'
    plan.write_text('not json')
    result = invoke(args=('--from-milestones', str(plan)))
    assert result.exit_code == 1
    assert json.loads(result.stdout)['quality_gates_passed'] is False


def test_stdout_restored_after_system_exit():
    import io
    import sys

    from kriya.cli_output import GenerateOutput
    original = io.StringIO()
    with patch.object(sys, 'stdout', original):
        with pytest.raises(SystemExit):
            with GenerateOutput(True) as output:
                output.result = {'status': 'knowledge_gap', 'quality_gates_passed': False}
                raise SystemExit(3)
        assert sys.stdout is original
    assert json.loads(original.getvalue())['status'] == 'knowledge_gap'


def test_workspace_lock_refusal_is_json(invoke):
    from kriya.control.run_ownership import WorkspaceLockHeldError
    with patch('kriya.cli.begin_mutating_run', side_effect=WorkspaceLockHeldError('busy')):
        result = invoke()
    assert result.exit_code == 1
    assert json.loads(result.stdout)['quality_gates_passed'] is False
    assert 'Workspace Locked' in result.stderr
