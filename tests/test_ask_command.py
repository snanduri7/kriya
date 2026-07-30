from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from kriya.cli import main


def test_ask_command_execution(tmp_path):
    # Create mock files to pass RepositoryAnalyzer checks
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "index.py").write_text("class MyService: pass")

    async def mock_impl(*args, **kwargs):
        cb = kwargs.get("stream_callback")
        if cb:
            cb("To run the Java program, execute 'mvn spring-boot:run'.")
        return "To run the Java program, execute 'mvn spring-boot:run'."
        
    mock_complete = AsyncMock(side_effect=mock_impl)
    
    runner = CliRunner()
    
    # Use context manager patches to intercept os.getcwd and LLMClient.complete
    with patch("os.getcwd", return_value=str(tmp_path)), \
         patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
         
        # Execute "kriya ask" command
        res = runner.invoke(main, ["ask", "How to run the java program?"])
        
        assert res.exit_code == 0
        assert "To run the Java program" in res.output
        
        # Verify that mock_complete was called with the context
        mock_complete.assert_called_once()
        args = mock_complete.call_args[0]
        assert "expert codebase Q&A assistant" in args[0]
        assert "How to run the java program?" in args[1]
        assert "Repository Context" in args[1]
