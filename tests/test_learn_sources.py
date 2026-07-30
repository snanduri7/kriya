import os
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from kriya.cli import main
from kriya.config import AppConfig


def test_learn_command_multiple_sources(tmp_path):
    # Setup paths configuration
    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
    
    # Create local temp documentation file to index
    doc_file = tmp_path / "ignite_docs.txt"
    doc_file.write_text("Ignite 2.18 cache configuration is defined using CacheConfiguration.")
    
    # Mock embedding queries
    async def mock_get_embeddings(texts):
        return [[0.2] * 384 for _ in texts]
        
    mock_embeddings = AsyncMock(side_effect=mock_get_embeddings)
    
    runner = CliRunner()
    
    with patch("kriya.memory.vector.OllamaEmbeddingClient.get_embeddings", new=mock_embeddings), \
         patch("kriya.cli.load_config", return_value=cfg):
         
        res = runner.invoke(main, [
            "learn", 
            "-f", str(doc_file),
            "-t", "Ignite 2.18 requires Java 11."
        ])
        
        assert res.exit_code == 0, f"Exception: {res.exception}, Output: {res.output}"
        assert "Successfully indexed" in res.output
        
        # Verify file persistence and loaded chunks
        db_path = os.path.join(cfg.paths.memory, "web_knowledge.db")
        assert os.path.exists(db_path)
        
        import sqlite3
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT text, provenance_url FROM learned_knowledge")
        rows = cursor.fetchall()
        conn.close()
        
        # We indexed 2 items (1 local file, 1 manual text entry)
        assert len(rows) >= 2
        
        # Verify file source doc
        file_doc = [r for r in rows if r[1] == str(doc_file)]
        assert len(file_doc) == 1
        assert "CacheConfiguration" in file_doc[0][0]
        
        # Verify text source doc
        text_doc = [r for r in rows if r[1] == "Manual Entry 1"]
        assert len(text_doc) == 1
        assert "Java 11" in text_doc[0][0]
