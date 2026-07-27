import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from kriya.config import AppConfig
from kriya.analyzer.analyzer import RepositoryAnalyzer

@pytest.mark.asyncio
async def test_indexing_repository_files(tmp_path):
    # Setup mock files
    py_file = tmp_path / "main.py"
    py_file.write_text("x = 1\n")
    java_file = tmp_path / "Service.java"
    java_file.write_text("package service;\n")
    
    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
    
    analyzer = RepositoryAnalyzer(str(tmp_path))
    
    # Mock embedding response
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {"embedding": [0.1] * 384}
            ]
        }
        mock_post.return_value = mock_response
        
        callback_files = []
        def progress_cb(filepath, idx, total):
            callback_files.append(filepath)
            
        await analyzer.index_repository(cfg, progress_callback=progress_cb)
        
        # Verify both files were indexed
        assert "main.py" in callback_files
        assert "Service.java" in callback_files
        
        # Verify vector store index file was written to disk
        vector_index_file = os.path.join(cfg.paths.memory, "vector_index.json")
        assert os.path.exists(vector_index_file)
