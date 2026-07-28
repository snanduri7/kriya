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
        db_path = os.path.join(cfg.paths.memory, "web_knowledge.db")
        assert os.path.exists(db_path)
        
        import sqlite3
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT text, provenance_url FROM learned_knowledge")
        rows = cursor.fetchall()
        conn.close()
        
        assert len(rows) > 0
        assert rows[0][1] == "http://example.com/ignite-docs"
        assert "Ignite 2.18.0" in rows[0][0]
        
        # Verify embedding list length
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT embedding FROM learned_knowledge")
        emb_blob = cursor.fetchone()[0]
        conn.close()
        from kriya.memory.vector import deserialize_embedding
        assert len(deserialize_embedding(emb_blob)) == 384
