import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from kriya.analyzer.analyzer import RepositoryAnalyzer
from kriya.config import AppConfig


def _init_git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)


def _mock_embedding_post():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"data": [{"embedding": [0.1] * 384}]}
    return mock_response


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
        vector_index_file = os.path.join(cfg.paths.memory, "vector_index.db")
        assert os.path.exists(vector_index_file)


@pytest.mark.asyncio
async def test_indexing_does_not_cache_a_file_whose_embedding_failed(tmp_path):
    """Regression test for a real bug found live, 2026-08-12 (SME architecture
    review): OllamaEmbeddingClient silently substitutes an all-zero "dummy"
    vector on any embedding-API failure to degrade gracefully - index_repository()
    previously treated that as a normal successful chunk and unconditionally
    updated the file's cache metadata (mtime/hash), so a transient embedding
    failure permanently corrupted that file's RAG entries: it became silently
    unsearchable forever, and a later non---force `kriya analyze` would see the
    mtime/hash as already up-to-date and never retry it. Fixed: a file with any
    zero-vector chunk no longer gets its cache metadata updated, so the NEXT
    (non-force) index_repository() call retries it automatically."""
    py_file = tmp_path / "broken_embedding.py"
    py_file.write_text("x = 1\n")

    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")

    analyzer = RepositoryAnalyzer(str(tmp_path))

    from kriya.memory.vector import LocalVectorStore

    # Run 1: the embedding server is unreachable for every request (both the
    # primary OpenAI-shaped endpoint and the Ollama-native fallback
    # get_embedding()/get_embeddings() try internally) - degrades to a
    # zero-vector per OllamaEmbeddingClient's own graceful-degradation
    # contract.
    with patch("httpx.AsyncClient.post", side_effect=Exception("embedding server unavailable")):
        await analyzer.index_repository(cfg)

    # The file's cache metadata must NOT have been updated - proves the next
    # run will retry it instead of treating it as already up-to-date.
    store = LocalVectorStore(os.path.join(cfg.paths.memory, "vector_index.db"))
    assert "broken_embedding.py" not in store.file_metadata

    # Run 2 (still non---force): the embedding server is healthy now - this
    # must actually retry the file (not skip it as "up-to-date"), confirming
    # the fix's whole point, not just that metadata was left unset.
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        # Same dimension (768) as OllamaEmbeddingClient's zero-vector default
        # from run 1's failure above - a mismatched dimension here would trip
        # LocalVectorStore.verify_model()'s own (unrelated, pre-existing)
        # index-consistency check, not the behavior this test is about.
        mock_response.json.return_value = {"data": [{"embedding": [0.1] * 768}]}
        mock_post.return_value = mock_response
        await analyzer.index_repository(cfg)

    store2 = LocalVectorStore(os.path.join(cfg.paths.memory, "vector_index.db"))
    assert "broken_embedding.py" in store2.file_metadata


@pytest.mark.asyncio
async def test_indexing_covers_languages_beyond_the_original_hardcoded_four(tmp_path):
    """Regression test for a finding from the 2026-08-12 SME review:
    index_repository()'s target_extensions was hardcoded to {.py, .java,
    .xml, .rb} even though RepositoryAnalyzer.analyze() already detects far
    more languages via EXTENSION_MAP (JS/TS/Go/Rust/C#/etc.) - every other
    detected language was structurally a no-op for Graph RAG retrieval,
    embedding zero chunks regardless of how much of the repo used it."""
    (tmp_path / "app.js").write_text("function add(a, b) { return a + b; }\n")
    (tmp_path / "main.go").write_text("package main\nfunc main() {}\n")

    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
    analyzer = RepositoryAnalyzer(str(tmp_path))

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_post.return_value = _mock_embedding_post()
        callback_files = []
        await analyzer.index_repository(cfg, progress_callback=lambda f, i, t: callback_files.append(f))

    assert "app.js" in callback_files
    assert "main.go" in callback_files


@pytest.mark.asyncio
async def test_indexing_changed_flag_includes_a_staged_but_unmodified_file(tmp_path):
    """Regression test for a finding from the 2026-08-12 SME review:
    --changed only ran `git diff --name-only` (unstaged changes) plus `git
    ls-files --others` (untracked files) - a file that's been `git add`ed
    but not modified again since staging appeared in neither, so it was
    silently excluded from incremental indexing."""
    _init_git_repo(tmp_path)
    (tmp_path / "committed.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "committed.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    (tmp_path / "staged_only.py").write_text("y = 2\n")
    subprocess.run(["git", "add", "staged_only.py"], cwd=tmp_path, check=True)
    # Deliberately no further edit after `git add` - staged_only.py is fully
    # staged and otherwise "clean", the exact case `git diff --name-only`
    # (unstaged) and `git ls-files --others` (untracked) both miss.

    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
    analyzer = RepositoryAnalyzer(str(tmp_path))

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_post.return_value = _mock_embedding_post()
        callback_files = []
        await analyzer.index_repository(cfg, changed=True, progress_callback=lambda f, i, t: callback_files.append(f))

    assert "staged_only.py" in callback_files
    assert "committed.py" not in callback_files  # unchanged since the initial commit
