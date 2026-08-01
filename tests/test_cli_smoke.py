import pytest
from click.testing import CliRunner

# Importing `main` alone reproduces the historical bug: kriya/cli.py used
# List[...]/Dict[...] type annotations without importing them from `typing`,
# which raised NameError at module-import time on every Python version except
# 3.14 (where PEP 649 made annotation evaluation lazy by default).
from kriya.cli import main

TOP_LEVEL_COMMANDS = [
    "version", "config", "doctor", "repl", "plugins", "analyze",
    "generate", "review", "ask", "learn", "fix", "traces",
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
