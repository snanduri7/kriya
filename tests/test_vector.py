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


def test_verify_model_raises_when_no_row_matches_the_configured_model(tmp_path):
    store = LocalVectorStore(str(tmp_path / "vector_index.db"))
    store.add_document("file1.py", "text", [1.0, 0.0], chunk_index=0, model_name="old-model", dimensions=2)

    with pytest.raises(ValueError, match="Index model mismatch"):
        store.verify_model("new-model", 2)


def test_verify_model_silent_when_every_row_matches(tmp_path):
    store = LocalVectorStore(str(tmp_path / "vector_index.db"))
    store.add_document("file1.py", "text", [1.0, 0.0], chunk_index=0, model_name="my-model", dimensions=2)
    store.add_document("file2.py", "text", [0.0, 1.0], chunk_index=0, model_name="my-model", dimensions=2)

    store.verify_model("my-model", 2)  # must not raise


def test_verify_model_warns_instead_of_silently_passing_on_a_partial_mismatch(tmp_path, caplog):
    """Regression test for a finding from the 2026-08-12 SME review:
    verify_model() only checked ONE arbitrary row (LIMIT 1) - a partially-
    mismatched index could pass verification while still containing rows
    from a different model/dimensions, which query() then silently drops
    with no warning at all. add_document() already guards against writing
    a second model/dimensions through the normal API (it calls
    verify_model() itself before every insert) - a mixed table only arises
    from something outside that guard (a crashed mid-migration re-index, a
    manually edited/merged db file), so this constructs it directly at the
    SQL layer rather than through add_document(), same technique already
    used elsewhere in this suite for a graph.py cross-file-collision test."""
    store = LocalVectorStore(str(tmp_path / "vector_index.db"))
    store.add_document("file1.py", "text", [1.0, 0.0], chunk_index=0, model_name="my-model", dimensions=2)
    cursor = store.conn.cursor()
    cursor.execute(
        "INSERT INTO vector_chunks (filepath, chunk_index, text, embedding, model_name, dimensions) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("file2.py", 0, "text", b"\x00", "old-model", 3),
    )
    store.conn.commit()

    with caplog.at_level("WARNING"):
        store.verify_model("my-model", 2)  # must not raise - file1's row is still usable

    assert "other model/dimension combination" in caplog.text
