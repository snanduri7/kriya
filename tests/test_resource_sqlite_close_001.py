"""RESOURCE-SQLITE-CLOSE-001: production SQLite connections are closed
explicitly, never left for the garbage collector.

Measured (tracemalloc on tests/test_developer_prompt_fit_001.py, 2026-09-29):
every `ResourceWarning: unclosed database` was allocated by production code
on the run path - KnowledgeCache (``with sqlite3.connect(...)`` commits but
never closes), TraceLogger (a connection held for the object's life, and the
object usually a temporary), the Graph RAG walk's DependencyGraph and the
Graph RAG vector store. The reported warning locations (for example
kriya/analyzer/java_members.py) were only where the finalizer happened to run.

Every connection Kriya opens goes through kriya.core.db._orig_connect (both
sqlite3.connect, globally patched to wal_connect, and get_connection); the
spy below records them and each test asserts all are closed afterwards."""
import sqlite3
from datetime import datetime, timezone

import pytest

from kriya.core import db
from kriya.core.trace import TraceLogger
from kriya.tools.knowledge import KnowledgeCache


@pytest.fixture
def opened(monkeypatch):
    connections = []
    original = db._orig_connect

    def spy(*args, **kwargs):
        conn = original(*args, **kwargs)
        connections.append(conn)
        return conn

    monkeypatch.setattr(db, "_orig_connect", spy)
    return connections


def _is_closed(conn):
    try:
        conn.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return True
    return False


def _assert_all_closed(connections):
    assert connections, "the spy saw no connection: the test is not exercising the producer"
    assert [c for c in connections if not _is_closed(c)] == []


def test_trace_logger_closes_every_connection_it_opens(tmp_path, opened):
    path = str(tmp_path / "traces.db")
    logger = TraceLogger(path)
    logger.log_run(run_id="r1", goal="g", duration_sec=1, attempts=1, status="success", files_modified=["a.py"])
    logger.log_milestone_plan("m1", "valid", 1, 2, 1, 0, 0, 1, [], {"build_system": "pip"})
    TraceLogger(path).log_run(run_id="r2", goal="g", duration_sec=1, attempts=0, status="failure", files_modified=[])
    _assert_all_closed(opened)
    with sqlite3.connect(path) as check:
        assert [r[0] for r in check.execute("SELECT run_id FROM runs ORDER BY run_id")] == ["r1", "r2"]
        assert check.execute("SELECT group_id FROM milestone_plans").fetchall() == [("m1",)]
    check.close()


def test_the_knowledge_cache_closes_every_connection_it_opens(tmp_path, opened):
    cache = KnowledgeCache(str(tmp_path / "memory"))
    released = datetime(2026, 1, 2, tzinfo=timezone.utc)
    cache.set_release_date("pypi", "requests", "2.99", released)
    assert cache.get_release_date("pypi", "requests", "2.99") == released
    cache.invalidate("pypi", "requests", "2.99")
    assert cache.get_release_date("pypi", "requests", "2.99") is None
    _assert_all_closed(opened)


def test_the_graph_rag_walk_closes_its_dependency_graph(tmp_path, opened):
    from test_prd027_precision_expansion_seeds import SVC, _retrieve

    result = _retrieve(tmp_path, "charge", SVC)
    assert result.expansion_seed_files, "the walk must run for this test to mean anything"
    _assert_all_closed(opened)


def test_indexed_embedding_dimensions_closes_its_read_only_connection(tmp_path, opened):
    from kriya.config import AppConfig
    from kriya.memory.vector import LocalVectorStore
    from kriya.workflow.context_certification import indexed_embedding_dimensions

    config = AppConfig()
    config.paths.memory = str(tmp_path)
    store = LocalVectorStore(str(tmp_path / "vector_index.db"))
    store.add_document("a.py", "x = 1", [0.1, 0.2, 0.3], chunk_index=0, model_name=config.embedding.model,
                       dimensions=3)
    store.close()
    assert indexed_embedding_dimensions(config) == 3
    _assert_all_closed(opened)


def test_the_workflow_closes_its_graph_rag_vector_store():
    """The vector store run_generation_workflow opens for Graph RAG is used
    for one retrieve_graph_context call and closed in a finally."""
    import ast
    import pathlib

    source = (pathlib.Path(__file__).resolve().parents[1] / "kriya" / "workflow" / "workflow.py").read_text()
    tree = ast.parse(source)
    guarded = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Try) and any(
            isinstance(inner, ast.Call) and getattr(inner.func, "id", None) == "retrieve_graph_context"
            for stmt in node.body for inner in ast.walk(stmt)
        ):
            guarded = any(
                isinstance(inner, ast.Call) and getattr(inner.func, "attr", None) == "close"
                and getattr(inner.func.value, "id", None) == "vector_store"
                for stmt in node.finalbody for inner in ast.walk(stmt)
            )
    assert guarded
