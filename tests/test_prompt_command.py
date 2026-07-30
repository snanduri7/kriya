from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from kriya.cli import main


def test_prompt_generate_command(tmp_path):
    runner = CliRunner()
    
    mock_complete = AsyncMock(return_value="### Optimized Prompt\nCreate an Ignite server node...")
    
    with patch("os.getcwd", return_value=str(tmp_path)), \
         patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
         
        res = runner.invoke(main, ["prompt", "generate", "Build a spring boot app with apache ignite"])
        
        assert res.exit_code == 0
        assert "GENERATED DEVELOPER PROMPT" in res.output
        assert "Optimized Prompt" in res.output
        mock_complete.assert_called_once()
