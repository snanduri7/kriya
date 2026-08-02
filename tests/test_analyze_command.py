import logging
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from kriya.analyzer.analyzer import RepositoryAnalyzer
from kriya.cli import main
from kriya.config import AppConfig


def test_analyze_rejects_single_file_with_clear_error(tmp_path):
    """Regression test for a real bug found via code review: click.Path(exists=True)
    accepts a file as well as a directory, but RepositoryAnalyzer.analyze() walks
    the path as a directory (os.walk on a file path silently yields nothing) -
    pointing analyze at a single file produced an empty-looking but "successful"
    analysis (languages: {}, total_files_indexed: 0), with no error or indication
    that a directory was actually required, despite the command's own docstring
    saying exactly that."""
    single_file = tmp_path / "single_file.py"
    single_file.write_text("print('hello')\n")

    runner = CliRunner()
    res = runner.invoke(main, ["analyze", str(single_file)])

    assert res.exit_code != 0
    assert "directory" in res.output.lower()


@pytest.mark.asyncio
async def test_analyze_changed_on_non_git_dir_warns_and_indexes_everything(tmp_path, caplog):
    """Regression test for a real bug found via code review: --changed's git
    commands both fail (returncode != 0, not an exception) on a non-git
    directory, and the old code silently left the changed-files set empty
    either way - filtering files_to_index down to NOTHING with no indication
    this happened because --changed genuinely couldn't be honored, rather than
    because there really were zero changes. Must warn clearly and fall back to
    indexing everything instead of silently indexing nothing."""
    (tmp_path / "main.py").write_text("x = 1\n")

    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.paths.skills = str(tmp_path / "skills")  # never the real repo's own skills/

    analyzer = RepositoryAnalyzer(str(tmp_path))

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": [{"embedding": [0.1] * 384}]}
        mock_post.return_value = mock_response

        indexed_files = []

        def progress_cb(filepath, idx, total):
            indexed_files.append(filepath)

        with caplog.at_level(logging.WARNING):
            await analyzer.index_repository(cfg, changed=True, progress_callback=progress_cb)

    assert any("requires a git repository" in r.message for r in caplog.records)
    assert any("main.py" in f for f in indexed_files)
