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
