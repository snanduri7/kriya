from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from kriya.cli import main
from kriya.config import AppConfig


def test_learn_warns_when_embedding_generation_fails(tmp_path):
    """Regression test for a real bug found via code review: OllamaEmbeddingClient.
    get_embeddings() silently substitutes an all-zero "dummy" vector on any failure
    (embedding server unreachable, malformed response, etc.) to "degrade gracefully"
    - by design, for other callers where a dummy query vector is harmless (ask's RAG
    lookup naturally filters near-zero similarity scores). But `learn` WRITES these
    dummy vectors permanently into the index and reports "Successfully indexed"
    regardless - the content silently becomes unsearchable forever (a zero vector
    never ranks meaningfully against a real query) while the user is told it worked.
    Must warn clearly instead of silently claiming success."""
    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")

    # Simulates get_embeddings()'s own real degrade-to-dummy-vector behavior on
    # failure (kriya/memory/vector.py), not a different/synthetic failure shape.
    async def mock_get_embeddings(texts):
        return [[0.0] * 384 for _ in texts]

    mock_embeddings = AsyncMock(side_effect=mock_get_embeddings)
    runner = CliRunner()

    with patch("kriya.memory.vector.OllamaEmbeddingClient.get_embeddings", new=mock_embeddings), \
         patch("kriya.cli.load_config", return_value=cfg):
        res = runner.invoke(main, ["learn", "-t", "The magic constant is 42."])

    assert res.exit_code == 0, res.output
    assert "Warning" in res.output
    assert "embedding" in res.output.lower()


def test_learn_no_warning_on_successful_embeddings(tmp_path):
    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")

    async def mock_get_embeddings(texts):
        return [[0.1, 0.2, 0.3] for _ in texts]

    mock_embeddings = AsyncMock(side_effect=mock_get_embeddings)
    runner = CliRunner()

    with patch("kriya.memory.vector.OllamaEmbeddingClient.get_embeddings", new=mock_embeddings), \
         patch("kriya.cli.load_config", return_value=cfg):
        res = runner.invoke(main, ["learn", "-t", "The magic constant is 42."])

    assert res.exit_code == 0, res.output
    assert "Warning" not in res.output
