from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from kriya.cli import main


def test_prompt_render_supports_custom_template_dir(tmp_path):
    """Regression test for a real bug found via code review: PromptEngine has
    always supported a template_dir constructor arg for custom '<name>.jinja'
    files (checked before the 4 built-in defaults) - confirmed via its own
    unit tests in tests/test_prompt.py - but `kriya prompt render` was the
    ONLY call site for PromptEngine in the whole codebase (grepped) and never
    passed one, always constructing PromptEngine() with zero arguments. This
    made the class's own documented custom-template feature fully unreachable
    from the CLI - verified live before fixing that a real .jinja file on disk
    produced "Template not found" no matter what. Fixed with a --template-dir/
    -t option, the minimal wiring that actually exposes the existing
    capability."""
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    (template_dir / "my_custom.jinja").write_text("Custom prompt for {{ project_name }}: {{ goal }}")

    runner = CliRunner()
    res = runner.invoke(main, [
        "prompt", "render", "my_custom",
        "--template-dir", str(template_dir),
        "-v", "project_name=Kriya",
        "-v", "goal=build a widget",
    ])

    assert res.exit_code == 0
    assert "Custom prompt for Kriya: build a widget" in res.output


def test_prompt_render_default_templates_still_work_without_template_dir_option():
    runner = CliRunner()
    res = runner.invoke(main, ["prompt", "render", "system_instructions"])

    assert res.exit_code == 0
    assert "production-grade AI Engineering Platform" in res.output


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
