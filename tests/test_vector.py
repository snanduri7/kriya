from unittest.mock import MagicMock, patch

import pytest

from kriya.memory.vector import LocalVectorStore, OllamaEmbeddingClient


@pytest.mark.asyncio
async def test_ollama_embedding_client():
    client = OllamaEmbeddingClient(base_url="http://localhost:11434/v1", model="nomic-embed-text:latest")
    
    # Mock httpx AsyncClient post
    with patch("httpx.AsyncClient.post") as mock_post:
        # Standard OpenAI layout response mock
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value={
            "data": [
                {
                    "embedding": [0.1, 0.2, 0.3]
                }
            ]
        })
        mock_post.return_value = mock_response
        
        vector = await client.get_embedding("hello vector")
        assert vector == [0.1, 0.2, 0.3]

def test_local_vector_store(tmp_path):
    index_file = tmp_path / "vector_index.json"
    store = LocalVectorStore(str(index_file))
    
    # Add documents
    store.add_document("file1.py", "def add(a, b): return a + b", [1.0, 0.0, 0.0], chunk_index=0)
    store.add_document("file2.py", "def sub(a, b): return a - b", [0.0, 1.0, 0.0], chunk_index=0)
    store.save()
    
    # Reload and test query
    store2 = LocalVectorStore(str(index_file))
    assert len(store2.documents) == 2
    
    # Query matching file1 (vector closest to [1.0, 0.0, 0.0])
    results = store2.query([0.9, 0.1, 0.0], top_k=1)
    assert len(results) == 1
    assert results[0]["filepath"] == "file1.py"
    assert results[0]["score"] > 0.8
