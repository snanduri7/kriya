"""PRD-003: one JSON result on every callback terminal path."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

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

    def run(payload=None, args=(), input_text=None, setup_error=None, workflow_error=None, human=False):
        flags = () if human else ("--json",)
        with patch("kriya.cli.load_config", return_value=cfg), \
             patch("kriya.cli.Kernel", return_value=kernel), \
             patch("kriya.cli.LLMClient", autospec=True, side_effect=setup_error), \
             patch("kriya.cli.WorkflowEngine"), \
             patch("kriya.cli._dispatch_generation", new=AsyncMock(return_value=payload, side_effect=workflow_error)):
            return CliRunner().invoke(main, ["generate", "test goal", *flags, *args], input=input_text)
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


# ------------------------------------------------------------------------------------------ Q6: public failure truth
#
# After an unexpected exception the durable state is terminalised and the controller's .enforce row carries the exception class as
# ``failure_type``; the CLI used to drop it (human line and JSON) and to print an unconditional "--resume" suggestion. The class is now
# transported from the propagated exception into the result family's existing ``failure_type`` field, and the generic hint is gone:
# whether a resume is accepted is decided later by validate_resume_against_reality with state this handler does not have.

def test_a_workflow_exception_carries_its_class_as_failure_type_in_the_json_failure(invoke):
    result = invoke(workflow_error=RuntimeError("controlled failure"))
    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "status": "failed", "quality_gates_passed": False, "error": "controlled failure", "failure_type": "RuntimeError"}


@pytest.mark.parametrize("error", [AttributeError("'NoneType' object has no attribute 'x'"), KeyError("k"), OSError("disk vanished")],
                         ids=["AttributeError", "KeyError", "OSError"])
def test_the_failure_type_is_the_propagated_exceptions_own_class(invoke, error):
    result = invoke(workflow_error=error)
    assert json.loads(result.stdout)["failure_type"] == type(error).__name__


def test_a_setup_exception_and_an_interrupt_carry_their_class_too(invoke):
    assert json.loads(invoke(setup_error=RuntimeError("provider unavailable")).stdout)["failure_type"] == "RuntimeError"
    assert json.loads(invoke(setup_error=EOFError("input ended")).stdout)["failure_type"] == "EOFError"
    assert json.loads(invoke(setup_error=KeyboardInterrupt()).stdout)["failure_type"] == "KeyboardInterrupt"


def test_a_failure_that_is_not_an_exception_has_no_failure_type():
    from kriya.cli_output import GenerateOutput

    output = GenerateOutput(True)
    output.fail("--acceptance-approval without --acceptance")
    assert output.result == {"status": "failed", "quality_gates_passed": False,
                             "error": "--acceptance-approval without --acceptance"}
    output.fail_with(KeyError())                    # str(KeyError()) is empty: the class names the failure
    assert output.result["error"] == "KeyError" and output.result["failure_type"] == "KeyError"


def test_a_returned_workflow_result_is_untouched_by_the_failure_contract(invoke):
    payload = {"quality_gates_passed": False, "status": "failed", "files": [], "failure_type": "PLANNING_ERROR"}
    assert json.loads(invoke(payload).stdout) == payload             # success and returned results keep their own payload
    assert "failure_type" not in json.loads(invoke({"quality_gates_passed": True, "files": []}).stdout)


def test_the_human_failure_line_names_the_authoritative_class_and_suggests_no_resume(invoke):
    result = invoke(workflow_error=RuntimeError("controlled failure"), human=True)
    assert result.exit_code == 1
    assert "Workflow error: RuntimeError: controlled failure" in result.output
    assert "--resume" not in result.output and "pick up where it left off" not in result.output


def test_a_returned_run_id_still_prints_the_exact_resume_command(invoke):
    """Control for the resumable state the CLI can know: a returned result with a saved checkpoint's run id."""
    result = invoke({"quality_gates_passed": False, "files": ["calc.py"], "run_id": "run-42", "failure_category": "no_progress"},
                    human=True)
    assert result.exit_code == 1 and isinstance(result.exception, SystemExit)         # the result path completed normally
    assert "--resume-id run-42" in result.output and "Checkpoint saved" in result.output
    assert "Workflow error" not in result.output


def test_a_returned_result_without_a_run_id_prints_no_resume_command(invoke):
    result = invoke({"quality_gates_passed": False, "files": ["calc.py"], "failure_category": "no_progress"}, human=True)
    assert result.exit_code == 1 and isinstance(result.exception, SystemExit)         # the result path completed normally
    assert "Quality Gates: FAILED" in result.output and "Workflow error" not in result.output
    assert "--resume" not in result.output


# ------------------------------------------------------------------------------ the other presentations of the same failure

def _invoke_milestones(tmp_path, monkeypatch, error, *flags):
    """`generate --from-milestones` whose sequence raises `error`: the milestone path's own handler (not the single-run one)."""
    monkeypatch.chdir(tmp_path)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"group_id": "g", "original_goal": "goal", "milestones": [
        {"id": "M1", "goal": "build M1", "success_criterion": "done", "depends_on": []}]}))
    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.logging.file_enabled = False
    cfg.logging.run_file_enabled = False
    kernel = strict_kernel(cfg)
    kernel.start = AsyncMock()
    kernel.stop = AsyncMock()
    with patch("kriya.cli.load_config", return_value=cfg), \
         patch("kriya.cli.Kernel", return_value=kernel), \
         patch("kriya.cli.LLMClient", autospec=True), \
         patch("kriya.cli.WorkflowEngine"), \
         patch("kriya.cli._dispatch_milestones", new=AsyncMock(side_effect=error)):
        return CliRunner().invoke(main, ["generate", "--from-milestones", str(plan), "-y", *flags])


def test_the_milestone_failure_line_and_json_name_the_same_class(tmp_path, monkeypatch):
    """Presentation parity with the single-run path: the human line carries the class the structured result already carried."""
    error = AttributeError("'NoneType' object has no attribute 'x'")
    human = _invoke_milestones(tmp_path, monkeypatch, error)
    assert human.exit_code == 1
    assert "Milestone sequence error: AttributeError: 'NoneType' object has no attribute 'x'" in human.output
    structured = _invoke_milestones(tmp_path, monkeypatch, error, "--json")
    assert json.loads(structured.stdout)["failure_type"] == "AttributeError"


def test_the_fix_failure_line_names_the_class_and_suggests_no_resume(tmp_path, monkeypatch):
    """`kriya fix` has no structured result: its human line is the whole presentation, from the one place the class is read."""
    monkeypatch.chdir(tmp_path)
    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.logging.file_enabled = False
    cfg.logging.run_file_enabled = False
    kernel = strict_kernel(cfg)
    kernel.start = AsyncMock()
    kernel.stop = AsyncMock()
    engine = MagicMock()
    engine.run_generation_workflow = AsyncMock(side_effect=KeyError("missing"))
    with patch("kriya.cli.load_config", return_value=cfg), \
         patch("kriya.cli.Kernel", return_value=kernel), \
         patch("kriya.cli.LLMClient", autospec=True), \
         patch("kriya.cli.WorkflowEngine", return_value=engine):
        result = CliRunner().invoke(main, ["fix", "--error", "compile failed", "-y"])
    assert result.exit_code == 1
    assert "Error executing fix workflow: KeyError: 'missing'" in result.output
    assert "--resume" not in result.output and "pick up where it left off" not in result.output
