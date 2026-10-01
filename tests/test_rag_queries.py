import os
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from kriya.cli import main
from kriya.config import AppConfig
from kriya.memory.learned_knowledge import learned_knowledge_db_path
from kriya.memory.vector import LocalVectorStore
from kriya.workflow.untrusted_context import UNTRUSTED_REFERENCE_BEGIN, UNTRUSTED_REFERENCE_END


def test_ask_command_performs_rag_search(tmp_path):
    """`ask` shows learned knowledge from the store `kriya learn` writes.

    The memory directory sits outside the repository `ask` scans, so the
    text can only reach the prompt through the learned-knowledge read (an
    earlier version of this test seeded a JSON file in the scanned directory
    and passed through the key-files context alone, while the read itself
    returned nothing - KNOWLEDGE-READPATH-001)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
    os.makedirs(cfg.paths.memory, exist_ok=True)
    store = LocalVectorStore(learned_knowledge_db_path(cfg))
    store.add_learned_knowledge(
        "Apache Ignite 2.18.0 features high-performance clustering.", [0.5] * 384,
        model_name=cfg.embedding.model, dimensions=384,
        provenance_url="http://example.com/ignite-docs", fetch_date="2026-09-27 10:00:00",
    )
    store.close()

    mock_emb = AsyncMock(return_value=[0.5] * 384)

    async def mock_impl(*args, **kwargs):
        cb = kwargs.get("stream_callback")
        if cb:
            cb("Yes, I have local knowledge about Ignite 2.18.0.")
        return "Yes, I have local knowledge about Ignite 2.18.0."

    mock_llm_complete = AsyncMock(side_effect=mock_impl)

    with patch("os.getcwd", return_value=str(repo)), \
         patch("kriya.memory.vector.OllamaEmbeddingClient.get_embedding", new=mock_emb), \
         patch("kriya.core.llm.LLMClient.complete", new=mock_llm_complete), \
         patch("kriya.cli.load_config", return_value=cfg):
        res = CliRunner().invoke(main, ["ask", "Tell me about Ignite 2.18.0"])

    assert res.exit_code == 0, f"Exception: {res.exception}, Output: {res.output}"
    assert "Yes, I have local knowledge" in res.output
    mock_llm_complete.assert_called_once()
    user_prompt = mock_llm_complete.call_args[0][1]
    fenced = user_prompt[user_prompt.index(UNTRUSTED_REFERENCE_BEGIN):user_prompt.index(UNTRUSTED_REFERENCE_END)]
    assert "Apache Ignite 2.18.0 features high-performance clustering" in fenced
    assert "[Source: http://example.com/ignite-docs (Fetched: 2026-09-27 10:00:00)]" in fenced
