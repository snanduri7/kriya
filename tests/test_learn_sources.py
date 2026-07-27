import os
import json
import pytest
from click.testing import CliRunner
from unittest.mock import AsyncMock, patch
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
        index_path = os.path.join(cfg.paths.memory, "web_knowledge.json")
        assert os.path.exists(index_path)
        
        with open(index_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        assert "documents" in data
        docs = data["documents"]
        
        # We indexed 2 items (1 local file, 1 manual text entry)
        assert len(docs) >= 2
        
        # Verify file source doc
        file_doc = [d for d in docs if d["filepath"] == str(doc_file)]
        assert len(file_doc) == 1
        assert "CacheConfiguration" in file_doc[0]["text"]
        
        # Verify text source doc
        text_doc = [d for d in docs if d["filepath"] == "Manual Entry 1"]
        assert len(text_doc) == 1
        assert "Java 11" in text_doc[0]["text"]
