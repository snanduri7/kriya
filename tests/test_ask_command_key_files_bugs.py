from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from kriya.cli import main


def _mock_complete_capturing():
    captured = {}

    async def mock_impl(*args, **kwargs):
        captured["prompt"] = args[1]
        cb = kwargs.get("stream_callback")
        if cb:
            cb("Answer.")
        return "Answer."

    return AsyncMock(side_effect=mock_impl), captured


def test_ask_includes_large_python_entrypoint_file(tmp_path):
    """Regression test for a real bug found via code review: the "skip large
    non-entry-point files" heuristic only ever checks for the JAVA-specific
    marker "public static void main", but is applied uniformly to .java/.py/.rb
    files alike. That marker can never appear in a Python or Ruby file, so
    EVERY large (>3000 char) Python/Ruby file was silently excluded from
    context regardless of whether it's the actual entry point - including
    Kriya's own cli.py if asked about its own repo."""
    (tmp_path / "main.py").write_text(
        "#!/usr/bin/env python3\n" + ("# padding line\n" * 800) +
        "\nif __name__ == '__main__':\n    print('entrypoint')\n"
    )
    mock_complete, captured = _mock_complete_capturing()
    runner = CliRunner()

    with patch("os.getcwd", return_value=str(tmp_path)), \
         patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["ask", "How do I run this?"])

    assert res.exit_code == 0, res.output
    assert "main.py" in captured["prompt"]
    assert "entrypoint" in captured["prompt"]


def test_ask_includes_large_ruby_entrypoint_file(tmp_path):
    (tmp_path / "main.rb").write_text(
        ("# padding line\n" * 800) + "\nputs 'entrypoint' if __FILE__ == $0\n"
    )
    mock_complete, captured = _mock_complete_capturing()
    runner = CliRunner()

    with patch("os.getcwd", return_value=str(tmp_path)), \
         patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["ask", "How do I run this?"])

    assert res.exit_code == 0, res.output
    assert "main.rb" in captured["prompt"]


def test_ask_includes_python_manifest_files(tmp_path):
    """Regression test for a real bug found via code review: requirements.txt
    and pyproject.toml - the actual dependency/run manifests for a Python
    project - matched neither the special-filename set (which only covered
    pom.xml/beans.xml/build.gradle/README.md/package.json, all Java/Node-
    centric) nor the extension set (.txt/.toml aren't source extensions), so
    they were never included even though they're exactly what a user asking
    "what does this project depend on" or "how do I run it" would need."""
    (tmp_path / "requirements.txt").write_text("flask==3.0.0\nrequests==2.31.0\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = \"myapp\"\nversion = \"1.0.0\"\n")

    mock_complete, captured = _mock_complete_capturing()
    runner = CliRunner()

    with patch("os.getcwd", return_value=str(tmp_path)), \
         patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["ask", "What does this depend on?"])

    assert res.exit_code == 0, res.output
    assert "flask==3.0.0" in captured["prompt"]
    assert "pyproject.toml" in captured["prompt"]


def test_ask_does_not_walk_into_kriya_worktree(tmp_path):
    """Regression test for a real bug found via code review: the directory
    walk's ignore set didn't include .kriya, so a leftover/active generate
    worktree (.kriya/worktree - a full nested git working copy of the source
    tree) could get walked into, duplicating or polluting context with a
    stale copy of the same files."""
    (tmp_path / "real.py").write_text("x = 1\n")
    worktree_dir = tmp_path / ".kriya" / "worktree"
    worktree_dir.mkdir(parents=True)
    (worktree_dir / "stale_copy.py").write_text("STALE_MARKER_CONTENT = True\n")

    mock_complete, captured = _mock_complete_capturing()
    runner = CliRunner()

    with patch("os.getcwd", return_value=str(tmp_path)), \
         patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
        res = runner.invoke(main, ["ask", "What files exist?"])

    assert res.exit_code == 0, res.output
    assert "real.py" in captured["prompt"]
    assert "STALE_MARKER_CONTENT" not in captured["prompt"]
