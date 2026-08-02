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


def test_prompt_generate_stdout_contains_only_the_generated_prompt(tmp_path):
    """Regression test for a real bug found while answering a user question
    about chaining `prompt generate` into `generate`: the "Generating
    optimized prompt..." and "=== GENERATED DEVELOPER PROMPT ===" lines were
    plain click.secho() with no err=True, landing on stdout mixed in with the
    actual generated prompt - so `kriya prompt generate "x" | kriya generate
    -y` would feed that chrome to `generate` as if it were part of the goal.
    Verified live before fixing that both lines appeared in res.stdout.
    Status/header chrome now goes to stderr; stdout must contain ONLY the
    model's actual output."""
    runner = CliRunner()
    mock_complete = AsyncMock(return_value="### Optimized Prompt\nBuild a todo REST API.")

    with patch("os.getcwd", return_value=str(tmp_path)), \
         patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["prompt", "generate", "a todo REST API"])

    assert res.exit_code == 0
    assert res.stdout.strip() == "### Optimized Prompt\nBuild a todo REST API."
    assert "Generating optimized prompt" not in res.stdout
    assert "GENERATED DEVELOPER PROMPT" not in res.stdout
    assert "Generating optimized prompt" in res.stderr
    assert "GENERATED DEVELOPER PROMPT" in res.stderr


def test_prompt_generate_auto_saves_for_repl_chaining(tmp_path):
    """A REPL session has no shell pipe between two typed lines, so the
    stdout/stderr split above doesn't help a REPL user chain this into a
    follow-up `generate` call. prompt generate must auto-save its output to a
    predictable path and print a copy-pasteable next step reusing generate's
    EXISTING --file flag."""
    runner = CliRunner()
    mock_complete = AsyncMock(return_value="### Optimized Prompt\nBuild a todo REST API.")

    with patch("os.getcwd", return_value=str(tmp_path)), \
         patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["prompt", "generate", "a todo REST API"])

    assert res.exit_code == 0
    saved = tmp_path / ".kriya" / "last_prompt.md"
    assert saved.exists()
    assert saved.read_text() == "### Optimized Prompt\nBuild a todo REST API."
    assert "generate --file" in res.stderr
    assert "last_prompt.md" in res.stderr


def test_prompt_generate_warns_instead_of_silently_failing_when_autosave_fails(tmp_path):
    """A failed save must not silently claim success - warn instead of
    printing nothing, and don't fail the whole command just because the
    convenience save didn't work (the prompt was still generated correctly).
    Forces a real failure (not a mock) by pre-occupying the .kriya path with
    a plain file, so os.makedirs(".kriya") genuinely can't create the
    directory Kriya needs - avoids mocking a builtin like open()/os.makedirs
    globally, which would also break unrelated file I/O earlier in the same
    invocation (config loading, logging setup)."""
    runner = CliRunner()
    (tmp_path / ".kriya").write_text("not a directory")
    mock_complete = AsyncMock(return_value="### Optimized Prompt\nBuild a todo REST API.")

    with patch("os.getcwd", return_value=str(tmp_path)), \
         patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["prompt", "generate", "a todo REST API"])

    assert res.exit_code == 0
    assert "### Optimized Prompt" in res.stdout
    assert "[WARNING]" in res.stderr
    assert "last_prompt.md" in res.stderr
