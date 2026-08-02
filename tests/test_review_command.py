import subprocess
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from kriya.cli import main


def _init_git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)


def test_review_command_uses_configured_reviewer_model(tmp_path):
    """Regression test for a real bug found via code review: the standalone
    `kriya review` command built its own ReviewerAgent with no role override at
    all, silently ignoring agent_llms.reviewer - unlike generate's internal
    reviewer stage (workflow.py), which correctly threads it through. A project
    that configures a specific reviewer model got inconsistent behavior between
    `generate`'s embedded review and standalone `review`."""
    (tmp_path / "kriya.yaml").write_text(
        "agent_llms:\n  reviewer:\n    llm:\n      model: devstral-small-2:24b\n"
    )
    (tmp_path / "app.py").write_text("def add(a, b):\n    return a + b\n")

    mock_complete = AsyncMock(return_value="Looks fine.")
    runner = CliRunner()

    with patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["--config", str(tmp_path / "kriya.yaml"), "review", str(tmp_path / "app.py")])

    assert res.exit_code == 0, res.output
    mock_complete.assert_called_once()
    assert mock_complete.call_args.kwargs.get("model_override") == "devstral-small-2:24b"


def test_review_directory_includes_ruby_files(tmp_path):
    """Regression test for a real bug found via code review: directory review's
    extension allowlist was {".py", ".java", ".xml"} - missing ".rb", even
    though PolymorphicValidator elsewhere explicitly supports Ruby. Ruby files
    in a directory review were silently skipped entirely."""
    (tmp_path / "app.py").write_text("print('hi')\n")
    (tmp_path / "app.rb").write_text("puts 'hi'\n")

    mock_complete = AsyncMock(return_value="Looks fine.")
    runner = CliRunner()

    with patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["review", str(tmp_path)])

    assert res.exit_code == 0, res.output
    prompt = mock_complete.call_args[0][1]
    assert "app.py" in prompt
    assert "app.rb" in prompt


def test_review_directory_git_status_handles_filename_with_space(tmp_path):
    """Regression test for a real bug found via code review: the old
    `line.strip().split()` + `parts[-1]` parsing of `git status --porcelain`
    output splits a space-containing filename into fragments, so `parts[-1]`
    resolves to a nonexistent file and the git-status path silently finds
    NOTHING - which then falls through to the (correct-by-accident) recursive
    fallback scan, masking the bug rather than exposing it. Distinguishes the
    two code paths directly: `unrelated.py` is committed with no git changes,
    so it must NEVER appear if the git-status path is genuinely the one that
    found the file - only the (unwanted, bug-masking) fallback scan would
    include it. Uses -z (NUL-separated, unquoted) instead of the ambiguous
    default porcelain format."""
    _init_git_repo(tmp_path)
    (tmp_path / "unrelated.py").write_text("# committed, never modified\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    spaced = tmp_path / "my task file.py"
    spaced.write_text("def run():\n    pass\n")

    mock_complete = AsyncMock(return_value="Looks fine.")
    runner = CliRunner()

    with patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["review", str(tmp_path)])

    assert res.exit_code == 0, res.output
    prompt = mock_complete.call_args[0][1]
    assert "my task file.py" in prompt
    assert "unrelated.py" not in prompt


def test_review_directory_git_status_handles_rename_with_space(tmp_path):
    """The -z porcelain format represents a rename as two separate
    NUL-terminated entries (status+old_path, then a bare new_path with no
    status prefix) - must correctly consume the destination path as the
    file to review, not the source, and not misparse a bare continuation
    entry as a fresh status-prefixed one."""
    _init_git_repo(tmp_path)
    original = tmp_path / "old_name.py"
    original.write_text("def run():\n    pass\n")
    (tmp_path / "unrelated.py").write_text("# committed, never modified\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    renamed = tmp_path / "new name with space.py"
    subprocess.run(["git", "mv", "old_name.py", "new name with space.py"], cwd=tmp_path, check=True)
    assert renamed.exists()

    mock_complete = AsyncMock(return_value="Looks fine.")
    runner = CliRunner()

    with patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["review", str(tmp_path)])

    assert res.exit_code == 0, res.output
    prompt = mock_complete.call_args[0][1]
    assert "new name with space.py" in prompt
    # Discriminates the correct git-status-driven path from the fallback
    # recursive scan (which would also happen to find the renamed file by
    # extension, masking a parsing bug the same way it did before this test
    # was strengthened) - unrelated.py has no git changes and must never
    # appear if the git-status path is the one actually doing the work.
    assert "unrelated.py" not in prompt


def test_review_directory_warns_when_truncated_at_ten_files(tmp_path):
    for i in range(12):
        (tmp_path / f"mod_{i}.py").write_text(f"x = {i}\n")

    mock_complete = AsyncMock(return_value="Looks fine.")
    runner = CliRunner()

    with patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["review", str(tmp_path)])

    assert res.exit_code == 0, res.output
    assert "only the first 10" in res.output


def test_review_directory_no_warning_under_ten_files(tmp_path):
    for i in range(3):
        (tmp_path / f"mod_{i}.py").write_text(f"x = {i}\n")

    mock_complete = AsyncMock(return_value="Looks fine.")
    runner = CliRunner()

    with patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["review", str(tmp_path)])

    assert res.exit_code == 0, res.output
    assert "only the first 10" not in res.output


def test_review_single_file_exceeding_budget_is_truncated_with_warning(tmp_path):
    """Regression test for a real, severe bug found live: with no size control at
    all, a file exceeding the model's context window got silently truncated from
    the FRONT by the backend, cutting off every "=== File: ... ===" framing
    marker along with it - the model received an unlabeled code fragment with no
    idea it was even being asked to review anything, produced a confused
    non-review response, and Kriya still reported success with no warning
    whatsoever. Must now: warn the user, and mark the truncation explicitly in
    what's sent to the model too, rather than truncate invisibly."""
    (tmp_path / "kriya.yaml").write_text("llm:\n  context_window: 500\n")
    # ~800 words - estimate_tokens (~1.3x word count) puts this well over the
    # budget (500 * 0.75 = 375 tokens).
    big_content = "\n".join(f"x_{i} = {i}  # padding line number {i}" for i in range(200))
    (tmp_path / "big.py").write_text(big_content)

    mock_complete = AsyncMock(return_value="Looks fine.")
    runner = CliRunner()

    with patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["--config", str(tmp_path / "kriya.yaml"), "review", str(tmp_path / "big.py")])

    assert res.exit_code == 0, res.output
    assert "too large to review in full" in res.output
    prompt_sent_to_model = mock_complete.call_args[0][1]
    assert "TRUNCATED" in prompt_sent_to_model
    assert "x_199" not in prompt_sent_to_model  # the tail never made it in


def test_review_multiple_files_over_budget_splits_into_batches(tmp_path):
    """When several files' combined content doesn't fit one call, must degrade
    to multiple separate review calls (each within budget) rather than either
    silently truncating the combined prompt or crashing - every file must
    actually reach the model in some call, clearly labeled which batch."""
    (tmp_path / "kriya.yaml").write_text("llm:\n  context_window: 500\n")
    # ~30 lines / ~150 words / ~195 estimated tokens each - comfortably under
    # the 375-token budget alone, but two of them combined (~390) exceed it.
    padding = "\n".join(f"x_{i} = {i}  # padding" for i in range(30))
    (tmp_path / "a.py").write_text(padding)
    (tmp_path / "b.py").write_text(padding.replace("x_", "y_"))

    mock_complete = AsyncMock(return_value="Looks fine.")
    runner = CliRunner()

    with patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["--config", str(tmp_path / "kriya.yaml"), "review", str(tmp_path)])

    assert res.exit_code == 0, res.output
    assert mock_complete.await_count == 2
    assert "batch 1/2" in res.output
    assert "batch 2/2" in res.output
    prompt_1 = mock_complete.await_args_list[0][0][1]
    prompt_2 = mock_complete.await_args_list[1][0][1]
    assert "a.py" in prompt_1 and "b.py" not in prompt_1
    assert "b.py" in prompt_2 and "a.py" not in prompt_2


def test_review_small_files_still_use_a_single_combined_call(tmp_path):
    # The common case (content comfortably fits) must be unchanged: one
    # combined call with full cross-file context, not needlessly split.
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")

    mock_complete = AsyncMock(return_value="Looks fine.")
    runner = CliRunner()

    with patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["review", str(tmp_path)])

    assert res.exit_code == 0, res.output
    assert mock_complete.await_count == 1
    prompt = mock_complete.await_args_list[0][0][1]
    assert "a.py" in prompt and "b.py" in prompt


def test_review_stdout_contains_only_the_review_text(tmp_path):
    """Regression test for a real bug found while auditing other commands for
    the same stdout-pollution shape as `prompt generate`: "Scanning
    directory: ...", "Reviewing N file(s)...", and the "=== Code Review
    Report ===" batch header were all plain click.secho() with no err=True,
    mixed into stdout alongside the reviewer's actual streamed output.
    Verified live before fixing. Chrome now goes to stderr; stdout must
    contain ONLY the streamed review text."""
    (tmp_path / "app.py").write_text("def add(a, b):\n    return a + b\n")

    async def fake_complete(*args, stream_callback=None, **kwargs):
        if stream_callback:
            stream_callback("This looks correct.")
        return "This looks correct."

    runner = CliRunner()
    with patch("kriya.core.llm.LLMClient.complete", new=AsyncMock(side_effect=fake_complete)):
        res = runner.invoke(main, ["review", str(tmp_path / "app.py")])

    assert res.exit_code == 0, res.output
    assert res.stdout.strip() == "This looks correct."
    assert "Scanning directory" not in res.stdout
    assert "Reviewing" not in res.stdout
    assert "Code Review Report" not in res.stdout
    assert "Reviewing 1 file(s)" in res.stderr
    assert "Code Review Report" in res.stderr
