import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

# Importing `main` alone reproduces the historical bug: kriya/cli.py used
# List[...]/Dict[...] type annotations without importing them from `typing`,
# which raised NameError at module-import time on every Python version except
# 3.14 (where PEP 649 made annotation evaluation lazy by default).
from kriya.cli import main

TOP_LEVEL_COMMANDS = [
    "version", "config", "doctor", "repl", "plugins", "analyze",
    "generate", "review", "ask", "learn", "fix", "traces", "completion",
]
SUBCOMMAND_GROUPS = {
    "prompt": ["render", "generate"],
    "tools": ["list", "execute"],
    "skills": ["list", "show", "create", "approve"],
}


@pytest.fixture
def runner():
    return CliRunner()


def test_top_level_help(runner):
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0, result.output
    assert "Kriya" in result.output


def test_bare_invocation_on_non_tty_prints_help_and_exits_cleanly(runner):
    # CliRunner's stdin is never a real TTY, so bare invocation must fall back
    # to today's exact behavior (help text, clean exit) rather than trying to
    # start the interactive session - a script/CI job hitting bare `kriya` by
    # accident must fail fast, not hang waiting on stdin.
    result = runner.invoke(main, [])
    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output and "Kriya" in result.output


def _invoke_bare_in_process(args):
    """CliRunner.invoke() swaps out sys.stdin internally to simulate piped
    input, which would make a `patch("sys.stdin.isatty", ...)` applied before
    the swap invisible to the code under test - so the two tests below invoke
    Click's own programmatic-invocation entry point directly instead, the
    same mechanism kriya/repl.py itself uses to dispatch commands, with
    sys.stdin.isatty patched on the real (unswapped) sys.stdin."""
    import click
    try:
        main.main(args=args, prog_name="kriya", standalone_mode=False)
    except click.exceptions.Exit:
        pass


def test_bare_invocation_on_a_real_tty_starts_the_repl():
    with patch("sys.stdin.isatty", return_value=True), \
         patch("kriya.repl.run_repl") as mock_run_repl:
        _invoke_bare_in_process([])
    # invoked without --config: config_path should be None
    mock_run_repl.assert_called_once_with(None)


def test_bare_invocation_on_a_real_tty_passes_through_the_config_path(tmp_path):
    config_file = tmp_path / "kriya.yaml"
    config_file.write_text("paths:\n  skills: ./skills\n")
    with patch("sys.stdin.isatty", return_value=True), \
         patch("kriya.repl.run_repl") as mock_run_repl:
        _invoke_bare_in_process(["--config", str(config_file)])
    mock_run_repl.assert_called_once_with(str(config_file))


@pytest.mark.parametrize("command", TOP_LEVEL_COMMANDS)
def test_command_help(runner, command):
    result = runner.invoke(main, [command, "--help"])
    assert result.exit_code == 0, f"{command} --help failed: {result.output}"


@pytest.mark.parametrize("group,subcommands", SUBCOMMAND_GROUPS.items())
def test_subcommand_group_help(runner, group, subcommands):
    result = runner.invoke(main, [group, "--help"])
    assert result.exit_code == 0, f"{group} --help failed: {result.output}"
    for sub in subcommands:
        sub_result = runner.invoke(main, [group, sub, "--help"])
        assert sub_result.exit_code == 0, f"{group} {sub} --help failed: {sub_result.output}"


def test_version_command_runs_end_to_end(runner):
    result = runner.invoke(main, ["version"])
    assert result.exit_code == 0, result.output
    assert "Kriya version" in result.output


@pytest.mark.parametrize("shell,expected_snippet", [
    ("bash", 'eval "$(_KRIYA_COMPLETE=bash_source kriya)"'),
    ("zsh", 'eval "$(_KRIYA_COMPLETE=zsh_source kriya)"'),
    ("fish", "_KRIYA_COMPLETE=fish_source kriya | source"),
])
def test_completion_command_prints_setup_instructions(runner, shell, expected_snippet):
    """Architectural add-on from a 2026-08-12 SME review: Click already
    generates a working completion script for free via a magic env var
    (confirmed live: `_KRIYA_COMPLETE=bash_source kriya` works with zero
    kriya-side code) - this command exists purely for discoverability."""
    result = runner.invoke(main, ["completion", shell])
    assert result.exit_code == 0, result.output
    assert expected_snippet in result.output


def test_completion_command_rejects_unknown_shell(runner):
    result = runner.invoke(main, ["completion", "powershell"])
    assert result.exit_code != 0


def _mock_workflow_engine(fake_result):
    mock_we = MagicMock()
    mock_we.run_generation_workflow = AsyncMock(return_value=fake_result)
    return mock_we


def _mock_kernel():
    mock_kernel = MagicMock()
    mock_kernel.start = AsyncMock()
    mock_kernel.stop = AsyncMock()
    return mock_kernel


_FAKE_GENERATE_RESULT = {
    "plan": "do the thing",
    "design": "design text",
    "files": ["a.py"],
    "quality_gates_passed": True,
    "environment_failure": None,
    "failure_category": None,
    "toolchain_warning": None,
    "lsp_warning": None,
    "unresolved_skill_gaps": None,
    "skill_staleness_warnings": None,
    "review": "looks good",
    "run_id": "abc123",
}


def test_generate_json_flag_prints_only_json_on_stdout(runner, tmp_path):
    """Architectural add-on from a 2026-08-12 SME review: --json swaps
    sys.stdout to stderr for the whole run so every existing narrative
    click.echo/secho call is redirected for free, then restores stdout to
    print only the final structured result - for CI/scripting use."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with patch("kriya.cli.WorkflowEngine", return_value=_mock_workflow_engine(_FAKE_GENERATE_RESULT)), \
             patch("kriya.cli.Kernel", return_value=_mock_kernel()), \
             patch("kriya.cli.LLMClient"):
            result = runner.invoke(main, ["generate", "do a thing", "-y", "--json"])

    assert result.exit_code == 0, result.output + result.stderr
    parsed = json.loads(result.stdout)
    assert parsed == _FAKE_GENERATE_RESULT
    # Narrative output (the streamed step banner, the approval/summary text)
    # must land on stderr instead, never polluting the JSON stdout stream.
    assert result.stdout.strip().startswith("{")
    # Narrative summary text (normally on stdout) must land on stderr instead.
    assert "Generation Workflow Completed" in result.stderr
    assert "Generation Workflow Completed" not in result.stdout


def test_generate_json_flag_exit_code_reflects_quality_gates_failure(runner, tmp_path):
    failing_result = dict(_FAKE_GENERATE_RESULT, quality_gates_passed=False)
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with patch("kriya.cli.WorkflowEngine", return_value=_mock_workflow_engine(failing_result)), \
             patch("kriya.cli.Kernel", return_value=_mock_kernel()), \
             patch("kriya.cli.LLMClient"):
            result = runner.invoke(main, ["generate", "do a thing", "-y", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.stdout) == failing_result


def test_generate_without_json_flag_is_unchanged(runner, tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with patch("kriya.cli.WorkflowEngine", return_value=_mock_workflow_engine(_FAKE_GENERATE_RESULT)), \
             patch("kriya.cli.Kernel", return_value=_mock_kernel()), \
             patch("kriya.cli.LLMClient"):
            result = runner.invoke(main, ["generate", "do a thing", "-y"])

    assert result.exit_code == 0, result.output + result.stderr
    # Default behavior stays human-readable narrative text on stdout, not JSON.
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.stdout)
    assert "looks good" in result.stdout


def test_tools_execute_shell_requires_confirmation_without_yes(runner):
    # CliRunner's stdin is non-interactive, so this should hit the non-TTY safety
    # exit rather than hang waiting for a confirmation prompt.
    result = runner.invoke(main, ["tools", "execute", "shell", '{"command": "echo hi"}'])
    assert result.exit_code != 0
    assert "CONFIRMATION REQUIRED" in result.output
    assert "Non-TTY" in result.output


def test_tools_execute_shell_runs_with_yes_flag(runner):
    result = runner.invoke(main, ["tools", "execute", "-y", "shell", '{"command": "echo hi"}'])
    assert result.exit_code == 0, result.output
    assert "hi" in result.output


def test_tools_execute_non_confirmation_tool_runs_without_yes(runner, tmp_path):
    result = runner.invoke(main, ["tools", "execute", "filesystem", f'{{"operation": "list", "path": "{tmp_path}"}}'])
    assert result.exit_code == 0, result.output
