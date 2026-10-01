import os
import subprocess
from unittest.mock import MagicMock, patch

import httpx
import pytest

from kriya.analyzer.analyzer import RepositoryAnalyzer
from kriya.config import AppConfig
from kriya.memory.vector import deserialize_embedding


def _init_git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)


def _embed_post(dimension=384, fail_for=()):
    """A native /api/embed answer: one vector per input. An input containing
    any of ``fail_for`` fails the request with a connection error (retried
    once by the client, then EMBEDDING_UNAVAILABLE)."""
    async def post(_client, url, json=None, **_kwargs):
        if any(marker in text for text in json["input"] for marker in fail_for):
            raise httpx.ConnectError("embedding server unavailable")
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"embeddings": [[0.1] * dimension for _ in json["input"]]}
        return response
    return post


def _mock_embedding_post():
    """Compatibility name for the shared native mock."""
    return _embed_post()


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
    with patch("httpx.AsyncClient.post", new=_embed_post()):
        callback_files = []
        def progress_cb(filepath, idx, total):
            callback_files.append(filepath)
            
        await analyzer.index_repository(cfg, generate_conventions_skill=False, progress_callback=progress_cb)
        
        # Verify both files were indexed
        assert "main.py" in callback_files
        assert "Service.java" in callback_files
        
        # Verify vector store index file was written to disk
        vector_index_file = os.path.join(cfg.paths.memory, "vector_index.db")
        assert os.path.exists(vector_index_file)


@pytest.mark.asyncio
async def test_indexing_does_not_cache_a_file_whose_embedding_failed(tmp_path):
    """Regression (2026-08-12 SME review), restated by EMBEDDING-CONTRACT-001:
    a file whose embedding fails gets no current vectors (never a placeholder
    vector), is not cached as up to date, is reported failed, and the next
    non-force run retries it."""
    (tmp_path / "broken_embedding.py").write_text("x = 1\n")
    (tmp_path / "healthy.py").write_text("y = 2\n")

    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
    analyzer = RepositoryAnalyzer(str(tmp_path))

    from kriya.memory.vector import LocalVectorStore

    # Run 1: the server answers the identity probe and healthy.py, but every
    # request for broken_embedding.py fails.
    with patch("httpx.AsyncClient.post", new=_embed_post(fail_for=("broken_embedding.py",))):
        report = await analyzer.index_repository(cfg, generate_conventions_skill=False)
    assert report.failed == {"broken_embedding.py": "EMBEDDING_UNAVAILABLE"} and report.indexed == 1

    store = LocalVectorStore(os.path.join(cfg.paths.memory, "vector_index.db"))
    assert "broken_embedding.py" not in store.file_metadata and "healthy.py" in store.file_metadata
    current = {row[0] for row in store.conn.execute("SELECT filepath FROM vector_chunks WHERE is_current = 1")}
    assert current == {"healthy.py"}  # no vector of the failed file is current
    zero_vectors = [blob for (blob,) in store.conn.execute("SELECT embedding FROM vector_chunks")
                    if not any(deserialize_embedding(blob))]
    assert zero_vectors == []
    store.close()

    # Run 2 (still non-force), healthy server: the file is retried, not skipped.
    with patch("httpx.AsyncClient.post", new=_embed_post()):
        report = await analyzer.index_repository(cfg, generate_conventions_skill=False)
    assert report.failed == {} and report.indexed == 1
    store2 = LocalVectorStore(os.path.join(cfg.paths.memory, "vector_index.db"))
    assert "broken_embedding.py" in store2.file_metadata
    store2.close()


@pytest.mark.asyncio
async def test_a_failed_identity_probe_stops_indexing(tmp_path):
    """EMBEDDING-CONTRACT-001: the dimension is never assumed - with the
    embedding server down, nothing is indexed and the error is typed."""
    from kriya.memory.embedding import EmbeddingUnavailableError

    (tmp_path / "a.py").write_text("x = 1\n")
    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
    with patch("httpx.AsyncClient.post", new=_embed_post(fail_for=("",))):
        with pytest.raises(EmbeddingUnavailableError):
            await RepositoryAnalyzer(str(tmp_path)).index_repository(cfg, generate_conventions_skill=False)


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

    with patch("httpx.AsyncClient.post", new=_embed_post()):
        callback_files = []
        await analyzer.index_repository(cfg, generate_conventions_skill=False, progress_callback=lambda f, i, t: callback_files.append(f))

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

    with patch("httpx.AsyncClient.post", new=_embed_post()):
        callback_files = []
        await analyzer.index_repository(cfg, generate_conventions_skill=False, changed=True, progress_callback=lambda f, i, t: callback_files.append(f))

    assert "staged_only.py" in callback_files
    assert "committed.py" not in callback_files  # unchanged since the initial commit
