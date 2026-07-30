import json
import os
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from kriya.cli import main
from kriya.config import AppConfig


def test_ask_command_performs_rag_search(tmp_path):
    # Setup paths configuration
    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
    os.makedirs(cfg.paths.memory, exist_ok=True)
    
    # Write pre-indexed document chunks to the database
    index_path = os.path.join(cfg.paths.memory, "web_knowledge.json")
    mock_data = {
        "documents": [
            {
                "filepath": "http://example.com/ignite-docs",
                "chunk_index": 0,
                "text": "Apache Ignite 2.18.0 features high-performance clustering.",
                "embedding": [0.5] * 384
            }
        ],
        "file_metadata": {}
    }
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(mock_data, f)
        
    # Mock LLM Client complete calls and embedding lookups
    mock_emb = AsyncMock(return_value=[0.5] * 384)
    
    async def mock_impl(*args, **kwargs):
        cb = kwargs.get("stream_callback")
        if cb:
            cb("Yes, I have local knowledge about Ignite 2.18.0.")
        return "Yes, I have local knowledge about Ignite 2.18.0."
        
    mock_llm_complete = AsyncMock(side_effect=mock_impl)
    
    runner = CliRunner()
    
    with patch("os.getcwd", return_value=str(tmp_path)), \
         patch("kriya.memory.vector.OllamaEmbeddingClient.get_embedding", new=mock_emb), \
         patch("kriya.core.llm.LLMClient.complete", new=mock_llm_complete), \
         patch("kriya.cli.load_config", return_value=cfg):
         
        res = runner.invoke(main, ["ask", "Tell me about Ignite 2.18.0"])
        
        assert res.exit_code == 0, f"Exception: {res.exception}, Output: {res.output}"
        assert "Yes, I have local knowledge" in res.output
        
        # Verify that mock_llm_complete was called and args[1] (user prompt)
        # contained the retrieved RAG context from the vector database
        mock_llm_complete.assert_called_once()
        args = mock_llm_complete.call_args[0]
        user_prompt = args[1]
        
        assert "=== Web Resources Context ===" in user_prompt
        assert "Apache Ignite 2.18.0 features high-performance clustering" in user_prompt
        assert "http://example.com/ignite-docs" in user_prompt
