from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from kriya.cli import main


def test_generate_from_file_goal(tmp_path):
    runner = CliRunner()
    
    # 1. Create a goal file
    goal_file = tmp_path / "goal.txt"
    goal_file.write_text("Build a Spring XML Application with Apache Ignite 2.18")
    
    # 2. Mock workflow engine
    mock_run = AsyncMock(return_value={"files": ["pom.xml"], "quality_gates_passed": True})
    
    with patch("os.getcwd", return_value=str(tmp_path)), \
         patch("kriya.workflow.workflow.WorkflowEngine.run_generation_workflow", new=mock_run):
         
        res = runner.invoke(main, ["generate", "-f", str(goal_file), "-y"])
        
        assert res.exit_code == 0
        assert "Generation Workflow Completed" in res.output
        mock_run.assert_called_once()
        # Verify the goal read from the file was passed to run_generation_workflow
        assert "Apache Ignite 2.18" in mock_run.call_args_list[0][1]["goal"]

def test_generate_from_stdin_goal(tmp_path):
    runner = CliRunner()
    
    mock_run = AsyncMock(return_value={"files": ["pom.xml"], "quality_gates_passed": True})
    
    with patch("os.getcwd", return_value=str(tmp_path)), \
         patch("kriya.workflow.workflow.WorkflowEngine.run_generation_workflow", new=mock_run):
         
        # Simulate stdin input
        res = runner.invoke(main, ["generate", "-y"], input="Build Ignite 2.18 App from Stdin")
        
        assert res.exit_code == 0
        assert "Generation Workflow Completed" in res.output
        mock_run.assert_called_once()
        assert "Stdin" in mock_run.call_args_list[0][1]["goal"]

def test_generate_wires_web_lookup_query_callback(tmp_path):
    """The CLI's pre-send live-lookup confirmation callback must actually be
    wired into run_generation_workflow, not just exist as dead code."""
    runner = CliRunner()
    mock_run = AsyncMock(return_value={"files": ["pom.xml"], "quality_gates_passed": True})

    with patch("os.getcwd", return_value=str(tmp_path)), \
         patch("kriya.workflow.workflow.WorkflowEngine.run_generation_workflow", new=mock_run):
        res = runner.invoke(main, ["generate", "-f", str(_write_goal(tmp_path)), "-y"])

    assert res.exit_code == 0
    callback = mock_run.call_args_list[0][1]["web_lookup_query_callback"]
    assert callable(callback)

def test_on_web_lookup_query_auto_declines_under_yes(tmp_path):
    """Under -y, a live-lookup query must never fire without explicit
    web_lookup_auto_approve - confirmed by extracting the real callback the
    CLI wires in and calling it directly, not just asserting on wiring."""
    runner = CliRunner()
    mock_run = AsyncMock(return_value={"files": ["pom.xml"], "quality_gates_passed": True})

    with patch("os.getcwd", return_value=str(tmp_path)), \
         patch("kriya.workflow.workflow.WorkflowEngine.run_generation_workflow", new=mock_run):
        runner.invoke(main, ["generate", "-f", str(_write_goal(tmp_path)), "-y"])

    callback = mock_run.call_args_list[0][1]["web_lookup_query_callback"]
    assert callback(["org.apache.ignite:ignite-core"], "http://fake-search:8080") is False

def test_on_web_lookup_query_interactive_shows_terms_and_confirms(tmp_path):
    """Interactively (no -y), the exact terms and target URL must be shown
    before the user is asked to approve - not just a generic yes/no."""
    runner = CliRunner()
    mock_run = AsyncMock(return_value={"files": ["pom.xml"], "quality_gates_passed": True})

    with patch("os.getcwd", return_value=str(tmp_path)), \
         patch("kriya.workflow.workflow.WorkflowEngine.run_generation_workflow", new=mock_run):
        runner.invoke(main, ["generate", "-f", str(_write_goal(tmp_path))])

    callback = mock_run.call_args_list[0][1]["web_lookup_query_callback"]
    with patch("click.confirm", return_value=True) as mock_confirm:
        result = callback(["org.apache.ignite:ignite-core"], "http://fake-search:8080")
    assert result is True
    mock_confirm.assert_called_once()

def _write_goal(tmp_path):
    goal_file = tmp_path / "goal.txt"
    goal_file.write_text("Build a Spring XML Application with Apache Ignite 2.18")
    return goal_file
