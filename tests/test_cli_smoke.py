import json
import os
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

# Importing `main` alone reproduces the historical bug: kriya/cli.py used
# List[...]/Dict[...] type annotations without importing them from `typing`,
# which raised NameError at module-import time on every Python version except
# 3.14 (where PEP 649 made annotation evaluation lazy by default).
from kriya.cli import _mark_run_in_progress, main

TOP_LEVEL_COMMANDS = [
    "version", "config", "doctor", "repl", "plugins", "analyze",
    "generate", "plan-milestones", "review", "ask", "learn", "fix", "traces", "completion",
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


def test_generate_without_json_returns_nonzero_when_quality_gates_fail(runner, tmp_path):
    failing_result = dict(_FAKE_GENERATE_RESULT, quality_gates_passed=False)
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with patch("kriya.cli.WorkflowEngine", return_value=_mock_workflow_engine(failing_result)), \
             patch("kriya.cli.Kernel", return_value=_mock_kernel()), \
             patch("kriya.cli.LLMClient"):
            result = runner.invoke(main, ["generate", "do a thing", "-y"])

    assert result.exit_code == 1
    assert "Generation Workflow Completed" in result.output


def test_generate_renders_streamed_reviewer_report_only_once(runner, tmp_path):
    """Reviewer tokens are progress, while the final report owns presentation."""
    mock_we = MagicMock()

    async def run_with_review_stream(**kwargs):
        kwargs["stream_callback"]("Review", "looks good")
        return dict(_FAKE_GENERATE_RESULT)

    mock_we.run_generation_workflow = AsyncMock(side_effect=run_with_review_stream)
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with patch("kriya.cli.WorkflowEngine", return_value=mock_we), \
             patch("kriya.cli.Kernel", return_value=_mock_kernel()), \
             patch("kriya.cli.LLMClient"):
            result = runner.invoke(main, ["generate", "do a thing", "-y"])

    assert result.exit_code == 0, result.output + result.stderr
    assert result.output.count("looks good") == 1
    assert result.output.count("=== Reviewer Report & Run Instructions ===") == 1


def test_generate_does_not_reprint_review_already_in_approval(runner, tmp_path):
    report = "UNIQUE PRE-APPROVAL REVIEW"
    mock_we = MagicMock()

    async def run_with_preapproval_review(**kwargs):
        reason = f"Human-in-the-loop review policy\n\n=== Automated Code Review ===\n{report}"
        assert kwargs["approval_callback"](
            [{"filepath": "a.py", "content": "+print('ok')"}], reason,
        )
        return dict(
            _FAKE_GENERATE_RESULT,
            review=report,
            review_included_in_approval=True,
        )

    mock_we.run_generation_workflow = AsyncMock(side_effect=run_with_preapproval_review)
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with patch("kriya.cli.WorkflowEngine", return_value=mock_we), \
             patch("kriya.cli.Kernel", return_value=_mock_kernel()), \
             patch("kriya.cli.LLMClient"):
            result = runner.invoke(main, ["generate", "do a thing", "-y"])

    assert result.exit_code == 0, result.output + result.stderr
    assert result.output.count(report) == 1
    assert "=== Reviewer Report & Run Instructions ===" not in result.output


def test_fix_reprints_full_reviewer_report(runner, tmp_path):
    """The dedicated final surface preserves a complete non-approval review."""
    long_review = "## How to Run the Application\n" + ("Detailed instructions. " * 30)
    assert len(long_review) > 300  # must actually exceed step_cb's truncation length
    fake_result = dict(_FAKE_GENERATE_RESULT, review=long_review)
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with patch("kriya.cli.WorkflowEngine", return_value=_mock_workflow_engine(fake_result)), \
             patch("kriya.cli.Kernel", return_value=_mock_kernel()), \
             patch("kriya.cli.LLMClient"):
            result = runner.invoke(main, ["fix", "--error", "some compile error", "-y"])

    assert result.exit_code == 0, result.output
    assert "=== Reviewer Report & Run Instructions ===" in result.output
    assert long_review in result.output


def test_fix_does_not_mislabel_a_human_rejection_as_a_reviewer_report(runner, tmp_path):
    """Independent review caught a real gap in the Finding 5 fix above: a
    human-rejected approval-gate run sets "review" to a one-line rejection notice
    ("Rejected by user during approval gate review.") with "files": [] - the same
    shape `generate`'s own reprint (gated on res.get("files"), not just
    res.get("review") alone) already knows to suppress. An unguarded reprint would
    misleadingly label that rejection notice as a "Reviewer Report"."""
    rejected_result = dict(_FAKE_GENERATE_RESULT, files=[], quality_gates_passed=False,
                            review="Rejected by user during approval gate review.")
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with patch("kriya.cli.WorkflowEngine", return_value=_mock_workflow_engine(rejected_result)), \
             patch("kriya.cli.Kernel", return_value=_mock_kernel()), \
             patch("kriya.cli.LLMClient"):
            result = runner.invoke(main, ["fix", "--error", "some compile error", "-y"])

    assert result.exit_code == 0, result.output
    assert "=== Reviewer Report & Run Instructions ===" not in result.output


def test_fix_does_not_preview_or_reprint_review_already_in_approval(runner, tmp_path):
    report = "UNIQUE FIX PRE-APPROVAL REVIEW"
    mock_we = MagicMock()

    async def run_with_preapproval_review(**kwargs):
        reason = f"Human-in-the-loop review policy\n\n=== Automated Code Review ===\n{report}"
        assert kwargs["approval_callback"](
            [{"filepath": "a.py", "content": "+print('fixed')"}], reason,
        )
        kwargs["step_callback"]("Review", report)
        return dict(
            _FAKE_GENERATE_RESULT,
            review=report,
            review_included_in_approval=True,
        )

    mock_we.run_generation_workflow = AsyncMock(side_effect=run_with_preapproval_review)
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with patch("kriya.cli.WorkflowEngine", return_value=mock_we), \
             patch("kriya.cli.Kernel", return_value=_mock_kernel()), \
             patch("kriya.cli.LLMClient"):
            result = runner.invoke(main, ["fix", "--error", "compile failed", "-y"])

    assert result.exit_code == 0, result.output
    assert result.output.count(report) == 1
    assert "=== Reviewer Report & Run Instructions ===" not in result.output


def test_mark_run_in_progress_writes_honest_status(tmp_path):
    """Regression test for a real live finding, 2026-08-12 (ignite_qpid_protocol via
    spikes/eval_harness, twice in the same session): a knowledge-gap retry that
    genuinely started running but got killed by an outer timeout before its own
    log_run() call left only the stale, terminal-sounding 'knowledge_gap' trace row
    behind - report.py showed '0 attempts, 8.5s' for a run that actually ran for the
    full 20-minute timeout. _mark_run_in_progress overwrites that row the instant the
    retry actually starts, so a killed retry is distinguishable from one that never
    proceeded past the gate at all."""
    from kriya.config import AppConfig

    cfg = AppConfig()
    cfg.paths.logs = str(tmp_path / "logs")

    _mark_run_in_progress(cfg, "run-123", "some goal")

    db_path = os.path.join(cfg.paths.logs, "traces.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM runs WHERE run_id = ?", ("run-123",)).fetchone()
    conn.close()

    assert row is not None
    assert row["status"] == "in_progress"
    assert row["goal"] == "some goal"


def test_mark_run_in_progress_noop_when_run_id_missing(tmp_path):
    from kriya.config import AppConfig

    cfg = AppConfig()
    cfg.paths.logs = str(tmp_path / "logs")

    _mark_run_in_progress(cfg, None, "some goal")

    # No traces.db should even be created - nothing to write without a run_id.
    assert not os.path.exists(os.path.join(cfg.paths.logs, "traces.db"))


def test_generate_marks_in_progress_before_knowledge_gap_retry_runs(runner, tmp_path):
    """CLI-level companion to the unit tests above: confirms _mark_run_in_progress is
    actually called, with the first call's own run_id, in between the two
    run_generation_workflow() calls a knowledge-gap retry makes (here via the
    no-unacked-gaps branch - the other branch, the 'warn' auto-confirm path, calls
    the exact same helper the exact same way, see kriya/cli.py)."""
    knowledge_gap_result = {"status": "knowledge_gap", "gap_report": {"gaps": []}, "run_id": "run-456"}
    real_result = dict(_FAKE_GENERATE_RESULT)

    mock_we = MagicMock()
    mock_we.run_generation_workflow = AsyncMock(side_effect=[knowledge_gap_result, real_result])

    calls = []

    def _record_mark(cfg, run_id, goal):
        calls.append(run_id)

    with runner.isolated_filesystem(temp_dir=tmp_path):
        with patch("kriya.cli.WorkflowEngine", return_value=mock_we), \
             patch("kriya.cli.Kernel", return_value=_mock_kernel()), \
             patch("kriya.cli.LLMClient"), \
             patch("kriya.cli._mark_run_in_progress", side_effect=_record_mark):
            result = runner.invoke(main, ["generate", "do a thing", "-y"])

    assert result.exit_code == 0, result.output + result.stderr
    assert calls == ["run-456"]
    assert mock_we.run_generation_workflow.await_count == 2


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


def _skills_config(tmp_path):
    """Writes a kriya.yaml pointing paths.skills at an isolated tmp dir, and
    returns (config_file_path, skills_dir)."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    config_file = tmp_path / "kriya.yaml"
    config_file.write_text(f"paths:\n  skills: {skills_dir}\n")
    return str(config_file), skills_dir


def test_skills_approve_does_not_promote_flagged_conflicts(runner, tmp_path):
    """Stage 5 SME review, Finding 1 (2026-08-15): _stage_skill_conflicts()
    (kriya/workflow/skill_extraction.py) is the only writer of staged_rules.txt
    in the current codebase, and it always writes a "[CONFLICT] ..." diagnostic
    line, never a plain rule. Before this fix, 'skills approve' promoted every
    staged line verbatim regardless of that prefix - writing the raw conflict
    diagnostic (marker, losing candidate, contradicted rule, and reasoning all
    together) into rules.txt as if it were a trusted engineering rule. A
    flagged conflict must stay staged for manual resolution instead."""
    config_file, skills_dir = _skills_config(tmp_path)
    skill_dir = skills_dir / "qpid"
    skill_dir.mkdir()
    (skill_dir / "rules.txt").write_text("Use qpid-broker-core 9.2.1.\n")
    (skill_dir / "staged_rules.txt").write_text(
        "Broker must bind AMQP to port 5673.\n"
        "[CONFLICT] Use qpid-broker-core 10.0.0. -- conflicts with existing rule: "
        "'Use qpid-broker-core 9.2.1.' (Different pinned version.)\n"
    )

    result = runner.invoke(main, ["--config", config_file, "skills", "approve", "qpid"])

    assert result.exit_code == 0, result.output
    assert "not auto-promoted" in result.output.lower()

    rules_text = (skill_dir / "rules.txt").read_text()
    assert "Broker must bind AMQP to port 5673." in rules_text
    assert "[CONFLICT]" not in rules_text
    assert "qpid-broker-core 10.0.0" not in rules_text

    # The conflict stays staged - the file must survive with only that line left.
    staged_path = skill_dir / "staged_rules.txt"
    assert staged_path.exists()
    staged_text = staged_path.read_text()
    assert "[CONFLICT]" in staged_text
    assert "Broker must bind AMQP to port 5673." not in staged_text


def test_skills_approve_removes_staged_file_once_fully_resolved(runner, tmp_path):
    """No flagged conflicts left after promotion - staged_rules.txt should be
    removed entirely, matching the pre-fix behavior for the ordinary case."""
    config_file, skills_dir = _skills_config(tmp_path)
    skill_dir = skills_dir / "qpid"
    skill_dir.mkdir()
    (skill_dir / "rules.txt").write_text("")
    (skill_dir / "staged_rules.txt").write_text("Broker must bind AMQP to port 5672.\n")

    result = runner.invoke(main, ["--config", config_file, "skills", "approve", "qpid"])

    assert result.exit_code == 0, result.output
    assert "Broker must bind AMQP to port 5672." in (skill_dir / "rules.txt").read_text()
    assert not (skill_dir / "staged_rules.txt").exists()


def test_skills_list_marks_conflict_lines_distinctly(runner, tmp_path):
    """CliRunner's captured output is never a real tty, so click strips ANSI color
    codes regardless of what `fg=` was passed - asserting on result.output alone
    can't actually tell a red-rendered line from a plain one (both come out as the
    same bytes). Patch click.secho (wraps=, so real rendering still happens) to
    inspect the fg= kwarg directly instead."""
    import click as click_module

    config_file, skills_dir = _skills_config(tmp_path)
    skill_dir = skills_dir / "qpid"
    skill_dir.mkdir()
    (skill_dir / "skill.yaml").write_text(
        "name: qpid\ndescription: Qpid broker skill\ncategory: messaging\ntags: []\n"
    )
    (skill_dir / "rules.txt").write_text("")
    (skill_dir / "staged_rules.txt").write_text(
        "[CONFLICT] Use qpid-broker-core 10.0.0. -- conflicts with existing rule: "
        "'Use qpid-broker-core 9.2.1.' (Different pinned version.)\n"
        "Broker must bind AMQP to port 5672.\n"
    )

    with patch("click.secho", wraps=click_module.secho) as mock_secho:
        result = runner.invoke(main, ["--config", config_file, "skills", "list"])

    assert result.exit_code == 0, result.output
    assert "[CONFLICT]" in result.output
    assert "Broker must bind AMQP to port 5672." in result.output

    conflict_calls = [c for c in mock_secho.call_args_list if "[CONFLICT]" in c.args[0]]
    assert len(conflict_calls) == 1
    assert conflict_calls[0].kwargs.get("fg") == "red"

    # The plain staged line must NOT go through secho(fg="red") - it's rendered via
    # plain click.echo instead, so no secho call should mention it at all.
    assert not any("port 5672" in c.args[0] for c in mock_secho.call_args_list)


def test_plan_milestones_bare_output_filename_does_not_crash(runner, tmp_path):
    """Regression guard for a real finding: `--output plan.json` (no directory
    component) made os.path.dirname(plan_path) return "", and
    os.makedirs("", exist_ok=True) raised an unhandled FileNotFoundError
    instead of the command's own clean error-handling pattern."""
    from kriya.agents.contracts import AcceptanceCriterion, MilestoneV2

    # MA3.7: MilestonePlannerAgent.run_with_milestone_list's real contract is
    # now List[MilestoneV2] (schema v2), not the old v1 Milestone shape - the
    # mock below must match what the REAL method actually returns.
    fake_milestones = [MilestoneV2(id="M1", goal="g1", acceptance=[AcceptanceCriterion(id="M1-A1", description="c1")])]
    with runner.isolated_filesystem(temp_dir=tmp_path) as cwd:
        mock_we = MagicMock()
        mock_we.milestone_planner = MagicMock()
        mock_we.milestone_planner.run_with_milestone_list = AsyncMock(return_value=("raw", fake_milestones))
        with patch("kriya.cli.WorkflowEngine", return_value=mock_we), \
             patch("kriya.cli.Kernel", return_value=_mock_kernel()), \
             patch("kriya.cli.LLMClient"):
            result = runner.invoke(main, ["plan-milestones", "a goal", "--output", "plan.json"])

        assert result.exit_code == 0, result.output
        assert os.path.exists(os.path.join(cwd, "plan.json"))
