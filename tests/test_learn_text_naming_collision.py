import sqlite3
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from kriya.cli import main
from kriya.config import AppConfig


def _run_learn(runner, cfg, args):
    async def mock_get_embeddings(texts):
        return [[0.1] * 384 for _ in texts]

    with patch("kriya.memory.vector.OllamaEmbeddingClient.get_embeddings", new=AsyncMock(side_effect=mock_get_embeddings)), \
         patch("kriya.cli.load_config", return_value=cfg):
        return runner.invoke(main, args)


def test_learn_text_entries_do_not_collide_across_invocations(tmp_path):
    """Regression test for a real bug found via code review: inline --text entries
    are named "Manual Entry {N}" purely by their POSITIONAL INDEX within a single
    invocation, not any stable/unique identifier. Since index_text_content() always
    calls remove_learned_knowledge(source_name) before writing (an intentional
    dedup-on-re-learn mechanism for URLs/files, where the same source_name
    genuinely means "the same source"), a LATER, entirely unrelated `learn -t`
    invocation whose Nth --text flag happens to land on the same index silently
    deletes an EARLIER, unrelated manually-taught fact - purely because they
    shared a position, not because they're the same source being re-taught."""
    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
    runner = CliRunner()

    # First invocation: teach one fact, becomes "Manual Entry 1".
    res1 = _run_learn(runner, cfg, ["learn", "-t", "The magic constant is 42."])
    assert res1.exit_code == 0, res1.output

    # Second, separate invocation: teach two DIFFERENT, unrelated facts. The first
    # of these also becomes "Manual Entry 1" (same positional index, unrelated
    # content) - must NOT delete the first invocation's fact.
    res2 = _run_learn(runner, cfg, ["learn", "-t", "The sky is blue.", "-t", "Water boils at 100C."])
    assert res2.exit_code == 0, res2.output

    db_path = str(tmp_path / "memory" / "web_knowledge.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT text, provenance_url FROM learned_knowledge")
    rows = cursor.fetchall()
    conn.close()

    all_text = " ".join(r[0] for r in rows)
    assert "42" in all_text, "the first invocation's fact was silently deleted by an unrelated later learn -t call"
    assert "sky is blue" in all_text
    assert "boils at 100" in all_text
