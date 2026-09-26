"""PRD027-PRECISION-001: only corroborated search hits seed graph expansion.

The real-embedder certification missed its fixed 0.5 precision target
(0.4808) because weak single-leg hybrid hits each seeded a 2-hop graph walk.
Search hits stay visible; graph expansion authority follows the rule in
``graph_retrieval.select_expansion_seeds``. These tests use their own small
repository, not the certification fixture.
"""
import asyncio
import json
import os
import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

from kriya.analyzer.graph import DependencyGraph
from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.core.state_paths import trace_db_path
from kriya.memory.vector import LocalVectorStore
from kriya.workflow import graph_retrieval as gr
from kriya.workflow.context_budget import RetrievalLimits
from kriya.workflow.workflow import WorkflowEngine

# Two unrelated clusters. svc.py -> helper.py -> deep.py, with caller.py
# calling Service.charge; noise.py -> widget.py.
FILES = {
    "svc.py": "from helper import helper\n\nclass Service:\n    def charge(self, x):\n        return helper(x)\n",
    "caller.py": "def run(service):\n    return service.charge(1)\n",
    "helper.py": "from deep import deep\n\ndef helper(x):\n    return deep(x)\n",
    "deep.py": "def deep(x):\n    return x\n",
    "noise.py": "from widget import widget\n\ndef banner():\n    return widget()\n",
    "widget.py": "def widget():\n    return 0\n",
}
# The search index holds three files; the dependency graph holds all six, so
# helper.py, deep.py and widget.py can only ever arrive by graph expansion.
# svc.py and noise.py each own one embedding axis; caller.py is orthogonal to
# every query below (cosine 0 = not a valid vector hit).
EMBEDDINGS = {
    "svc.py": [1.0, 0.0, 0.0, 0.0],
    "noise.py": [0.0, 1.0, 0.0, 0.0],
    "caller.py": [0.0, 0.0, 1.0, 0.0],
}
SVC = [1.0, 0.0, 0.0, 0.0]
NOISE_OVER_SVC = [0.6, 0.8, 0.0, 0.0]  # noise.py ranks first, svc.py second
NOTHING = [0.0, 0.0, 0.0, 1.0]  # orthogonal to every document
LIMITS = RetrievalLimits(top_k=5, max_hops=2, max_neighborhood_results=30)


class _Embedder:
    def __init__(self, vector):
        self.vector = vector

    async def get_embedding(self, text, is_query=False):
        del text, is_query
        return list(self.vector)


def _retrieve(tmp_path, goal, query_vector):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    graph = DependencyGraph(str(tmp_path / "dependency_graph.db"))
    store = LocalVectorStore(str(tmp_path / "vector_index.db"))
    for path, content in FILES.items():
        (repo / path).write_text(content)
        graph.index_file(path, content, mtime=0.0)
        if path in EMBEDDINGS:
            store.add_document(path, content, EMBEDDINGS[path], chunk_index=0, model_name="m", dimensions=4)
    graph.close()
    try:
        return asyncio.run(gr.retrieve_graph_context(
            goal, str(repo), embed_client=_Embedder(query_vector), vector_store=store,
            dependency_graph_path=str(tmp_path / "dependency_graph.db"), limits=LIMITS,
            embedding_model="m", budget_limit=lambda: 8000,
        ))
    finally:
        store.close()


def test_a_hit_ranked_by_both_legs_seeds_its_callers_and_dependencies(tmp_path):
    result = _retrieve(tmp_path, "make the Service charge retry", SVC)
    assert result.expansion_seed_reason == gr.EXPANSION_CORROBORATED
    assert result.expansion_seed_files == ["svc.py"]
    assert {"helper.py", "deep.py"} <= set(result.related_files)  # one- and two-hop dependencies
    assert "caller.py" in set(result.matched_files) | set(result.related_files)
    assert "widget.py" not in result.related_files


def test_a_weak_embedding_only_hit_is_kept_but_does_not_seed_while_lexical_hits_exist(tmp_path):
    result = _retrieve(tmp_path, "make the Service charge retry", NOISE_OVER_SVC)
    assert "noise.py" in result.matched_files  # still a search result
    assert "noise.py" not in result.expansion_seed_files
    assert result.expansion_seed_files == ["svc.py"]
    assert "widget.py" not in result.related_files  # noise.py's neighbourhood is not pulled in
    assert {"helper.py", "deep.py"} <= set(result.related_files)


def test_a_weak_lexical_only_hit_is_kept_but_does_not_seed_while_embedding_hits_exist(tmp_path):
    result = _retrieve(tmp_path, "Service charge banner", SVC)
    assert "noise.py" in result.matched_files  # matched on "banner" only
    assert result.expansion_seed_files == ["svc.py"]
    assert result.expansion_seed_reason == gr.EXPANSION_CORROBORATED
    assert "widget.py" not in result.related_files


def test_a_genuine_embedding_only_query_still_expands(tmp_path):
    result = _retrieve(tmp_path, "zzzz qqqq", SVC)  # no lexical term matches anything
    assert result.expansion_seed_reason == gr.EXPANSION_EMBEDDING_ONLY
    assert result.expansion_seed_files == ["svc.py"]
    assert {"helper.py", "deep.py"} <= set(result.related_files)
    assert "widget.py" not in result.related_files


def test_a_genuine_lexical_only_query_still_expands(tmp_path):
    result = _retrieve(tmp_path, "make the Service charge retry", NOTHING)  # every cosine is 0
    assert result.expansion_seed_reason == gr.EXPANSION_LEXICAL_ONLY
    assert "svc.py" in result.expansion_seed_files
    assert {"helper.py", "deep.py"} <= set(result.related_files)
    assert "widget.py" not in result.related_files


def test_two_legs_that_disagree_keep_their_hits_and_seed_nothing(tmp_path):
    result = _retrieve(tmp_path, "banner", SVC)  # lexical finds noise.py, the vector leg svc.py
    assert {"svc.py", "noise.py"} <= set(result.matched_files)
    assert result.expansion_seed_reason == gr.EXPANSION_NO_CORROBORATED
    assert result.expansion_seed_files == []
    assert result.related_files == []
    assert result.context_package is not None
    assert {"svc.py", "noise.py"} <= {item.path for item in result.context_package.relevant_files}


def _hit(path, vector_rank, lexical_rank, vector_valid=5, lexical_valid=5):
    return {"filepath": path, "score": 0.03, "vector_rank": vector_rank, "lexical_rank": lexical_rank,
            "vector_valid_hits": vector_valid, "lexical_valid_hits": lexical_valid}


def test_seed_selection_rule():
    select = gr.select_expansion_seeds
    # Corroboration needs both ranks within top_k, not merely presence in both legs.
    assert select([_hit("a", 1, 1), _hit("b", 2, 6), _hit("c", 6, 2)], 5) == (["a"], gr.EXPANSION_CORROBORATED)
    assert select([_hit("b", 2, 6), _hit("c", 6, 2)], 5) == ([], gr.EXPANSION_NO_CORROBORATED)
    assert select([_hit("a", 1, None, lexical_valid=0), _hit("b", 6, None, lexical_valid=0)], 5) == (
        ["a"], gr.EXPANSION_EMBEDDING_ONLY)
    assert select([_hit("a", None, 2, vector_valid=0)], 5) == (["a"], gr.EXPANSION_LEXICAL_ONLY)
    assert select([_hit("a", None, None, 0, 0)], 5) == ([], gr.EXPANSION_NO_VALID_EVIDENCE)
    assert select([], 5) == ([], gr.EXPANSION_NO_VALID_EVIDENCE)
    # A hit without leg provenance cannot be classified, so it cannot seed.
    assert select([{"filepath": "a", "score": 0.03}], 5) == ([], gr.EXPANSION_PROVENANCE_UNAVAILABLE)
    # One seed entry per file, in hit order.
    assert select([_hit("a", 1, 1), _hit("a", 2, 2), _hit("b", 3, 3)], 5) == (
        ["a", "b"], gr.EXPANSION_CORROBORATED)
    assert set(gr.EXPANSION_SEED_REASON_CODES) == {
        gr.EXPANSION_CORROBORATED, gr.EXPANSION_EMBEDDING_ONLY, gr.EXPANSION_LEXICAL_ONLY,
        gr.EXPANSION_NO_CORROBORATED, gr.EXPANSION_NO_VALID_EVIDENCE, gr.EXPANSION_PROVENANCE_UNAVAILABLE}


def test_query_hybrid_reports_each_hit_s_leg_ranks(tmp_path):
    store = LocalVectorStore(str(tmp_path / "vector_index.db"))
    for path, vector in EMBEDDINGS.items():
        store.add_document(path, FILES[path], vector, chunk_index=0, model_name="m", dimensions=4)
    hits = store.query_hybrid("banner", SVC, top_k=5, model_name="m", dimensions=4)
    store.close()
    by_file = {hit["filepath"]: hit for hit in hits}
    assert by_file["svc.py"]["vector_rank"] == 1 and by_file["svc.py"]["lexical_rank"] is None
    assert by_file["noise.py"]["lexical_rank"] == 1 and by_file["noise.py"]["vector_rank"] is None
    # Only a positive cosine is a valid vector hit.
    assert all(hit["vector_valid_hits"] == 1 and hit["lexical_valid_hits"] == 1 for hit in hits)


async def _run_with_index(tmp_path, *, mode, responses, approval_callback=None):
    cfg = AppConfig()
    cfg.autonomy.mode = mode
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.paths.skills = str(tmp_path / "skills")
    os.makedirs(cfg.paths.memory, exist_ok=True)
    (tmp_path / "Existing.txt").write_text("chunk one\n")
    dim = 768
    doc_emb = [1.0] + [0.0] * (dim - 1)
    vs = LocalVectorStore(os.path.join(cfg.paths.memory, "vector_index.db"))
    vs.add_document("Existing.txt", "chunk one", doc_emb, chunk_index=0, model_name=cfg.embedding.model,
                    dimensions=dim)
    vs.close()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=responses)
    we = WorkflowEngine(Kernel(config=cfg), llm)
    with patch("kriya.memory.vector.OllamaEmbeddingClient.get_embedding", new=AsyncMock(return_value=doc_emb)):
        res = await we.run_generation_workflow(
            goal="Create math library", workspace_path=str(tmp_path), approval_callback=approval_callback,
        )
    connection = sqlite3.connect(trace_db_path(cfg))
    connection.row_factory = sqlite3.Row
    row = connection.execute("SELECT status, run_events FROM runs ORDER BY timestamp DESC LIMIT 1").fetchone()
    connection.close()
    events = [event for event in json.loads(row["run_events"] or "[]")
              if event.get("kind") == "retrieval.expansion_seeds"]
    return res, row["status"], events


def _assert_embedding_only_seed_event(events):
    assert events, "the expansion-seed decision was not recorded"
    details = events[-1]["details"]
    assert details["reason_code"] == gr.EXPANSION_EMBEDDING_ONLY
    assert details["seed_files"] == ["Existing.txt"]
    assert details["matched_files"] == ["Existing.txt"]


@pytest.mark.asyncio
async def test_the_run_records_which_hits_seeded_graph_expansion(tmp_path):
    res, _status, events = await _run_with_index(tmp_path, mode="guardrails", responses=[
        "Step 1: Write code", "Design: Write math.py", "def add(a,b):\n    return a+b", "Review: Approved",
    ])
    assert res["quality_gates_passed"] is True
    _assert_embedding_only_seed_event(events)


@pytest.mark.asyncio
async def test_the_seed_decision_is_recorded_on_a_human_rejected_run_too(tmp_path):
    res, status, events = await _run_with_index(
        tmp_path, mode="human-in-the-loop", approval_callback=lambda files, reason: False, responses=[
            "Step 1: Write code", "Design: Write app.py", '[{"filepath": "app.py", "content": "print(1)"}]',
            "Review: flagged for human judgment",
        ])
    assert res["quality_gates_passed"] is False
    assert status == "human_rejected"
    _assert_embedding_only_seed_event(events)
