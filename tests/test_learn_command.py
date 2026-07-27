import os
import json
import pytest
from click.testing import CliRunner
from unittest.mock import AsyncMock, patch
from kriya.cli import main
from kriya.config import AppConfig

def test_learn_command_fetches_and_persists(tmp_path):
    # Setup paths configuration
    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
    
    # Mock web fetcher and embedding client
    mock_fetch = AsyncMock(return_value="Apache Ignite 2.18.0 is an in-memory computing platform.")
    
    async def mock_get_embeddings(texts):
        return [[0.1] * 384 for _ in texts]
        
    mock_embeddings = AsyncMock(side_effect=mock_get_embeddings)
    
    runner = CliRunner()
    
    with patch("kriya.tools.web.fetch_url_text", new=mock_fetch), \
         patch("kriya.memory.vector.OllamaEmbeddingClient.get_embeddings", new=mock_embeddings), \
         patch("kriya.cli.load_config", return_value=cfg):
         
        res = runner.invoke(main, ["learn", "-u", "http://example.com/ignite-docs"])
        
        assert res.exit_code == 0, f"Exception: {res.exception}, Output: {res.output}"
        assert "Successfully indexed" in res.output
        assert "Local knowledge base updated" in res.output
        
        # Verify file persistence
        index_path = os.path.join(cfg.paths.memory, "web_knowledge.json")
        assert os.path.exists(index_path)
        
        with open(index_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        assert "documents" in data
        docs = data["documents"]
        assert len(docs) > 0
        assert docs[0]["filepath"] == "http://example.com/ignite-docs"
        assert "Ignite 2.18.0" in docs[0]["text"]
        assert docs[0]["embedding"] == [0.1] * 384
