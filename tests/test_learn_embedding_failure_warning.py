from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from kriya.cli import main
from kriya.config import AppConfig


def test_learn_warns_when_embedding_generation_fails(tmp_path):
    """Regression (code review): `learn` used to WRITE placeholder zero vectors
    for failed embeddings and report success. EMBEDDING-CONTRACT-001: a failed
    embedding is a typed error - nothing is written for that source, the
    command says so and exits non-zero."""
    from kriya.memory.embedding import EmbeddingUnavailableError
    from kriya.memory.learned_knowledge import learned_knowledge_db_path
    from kriya.memory.vector import LocalVectorStore

    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
    failing = AsyncMock(side_effect=EmbeddingUnavailableError("embedding server unavailable"))
    runner = CliRunner()

    with patch("kriya.memory.vector.OllamaEmbeddingClient.get_embeddings", new=failing), \
         patch("kriya.cli.load_config", return_value=cfg):
        res = runner.invoke(main, ["learn", "-t", "The magic constant is 42."])

    assert res.exit_code == 1, res.output
    assert "EMBEDDING_UNAVAILABLE" in res.output and "nothing was indexed" in res.output
    store = LocalVectorStore(learned_knowledge_db_path(cfg))
    try:
        assert store.conn.execute("SELECT COUNT(*) FROM learned_knowledge").fetchone()[0] == 0
    finally:
        store.close()


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
