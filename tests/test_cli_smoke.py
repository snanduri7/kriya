from unittest.mock import patch

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
