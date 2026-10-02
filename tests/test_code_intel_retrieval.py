"""Code Intelligence R1 slice 2, item 2: fused localization inside the
production retrieval stage (``retrieve_graph_context``).

Through the real ``index_repository`` (deterministic embedder) and the real
stage: with a structural index the candidates come from one fused Code
Intelligence query (deterministic channels + the vector channel), each with
its channels; the legacy hybrid leg answers whole only without a structural
index, and otherwise only for files the structural index does not cover,
ranked in the similarity tier; files changed since indexing are localized
from their current bytes, never from stale rows.
"""
import asyncio
import os
import subprocess
import textwrap

import pytest
from _fake_embedding import StaticEmbedder

from kriya.analyzer.analyzer import RepositoryAnalyzer
from kriya.code_intel import locate as loc
from kriya.config import AppConfig
from kriya.memory.vector import LocalVectorStore
from kriya.workflow import graph_retrieval as gr
from kriya.workflow.context_budget import RetrievalLimits

ORDERS = textwrap.dedent("""\
    package shop;

    public class OrderService {
        public int total(int subtotal) {
            return subtotal - discount(subtotal);
        }

        int discount(int subtotal) {
            return subtotal / 10;
        }
    }
    """)
INVENTORY = textwrap.dedent("""\
    package shop;

    public class Inventory {
        public int reserve(int quantity) {
            return quantity;
        }
    }
    """)


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo, check=True,
                   capture_output=True)


@pytest.fixture
def indexed(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src/main/java/shop").mkdir(parents=True)
    (repo / "src/main/java/shop/OrderService.java").write_text(ORDERS)
    (repo / "src/main/java/shop/Inventory.java").write_text(INVENTORY)
    (repo / "web").mkdir()
    (repo / "web/discountPolicy.js").write_text("// The discount policy is capped for every order.\n"
                                               "export function cappedDiscount(order) { return order.discountPolicy; }\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.paths.skills = str(tmp_path / "skills")
    embedder = StaticEmbedder([0.3, 0.4, 0.5])
    asyncio.run(RepositoryAnalyzer(str(repo)).index_repository(
        cfg, generate_conventions_skill=False, embedding_client=embedder))
    return repo, cfg, embedder


def _retrieve(repo, cfg, embedder, goal, graph_path="default"):
    store = LocalVectorStore(os.path.join(cfg.paths.memory, "vector_index.db"))
    try:
        return asyncio.run(gr.retrieve_graph_context(
            goal, str(repo), embed_client=embedder, vector_store=store,
            dependency_graph_path=(os.path.join(cfg.paths.memory, "dependency_graph.db")
                                   if graph_path == "default" else graph_path),
            limits=RetrievalLimits(top_k=5, max_hops=2, max_neighborhood_results=30),
            embedding_model="test-embed", budget_limit=lambda: 4000))
    finally:
        store.close()


def test_a_structural_index_answers_with_fused_ranked_candidates_and_their_channels(indexed):
    repo, cfg, embedder = indexed
    result = _retrieve(repo, cfg, embedder, "OrderService.discount must be capped at half the subtotal")
    assert result.localization_source == gr.LOCALIZATION_CODE_INTELLIGENCE
    top = result.localization[0]
    assert (top.lookup_key, top.path) == ("shop.OrderService.discount", "src/main/java/shop/OrderService.java")
    assert "qualified_symbol" in dict(top.channels) and "vector" in dict(top.channels)
    assert all(c.channels for c in result.localization)
    assert result.matched_files[0] == "src/main/java/shop/OrderService.java"
    assert result.retrieval_member_hints["src/main/java/shop/OrderService.java"][0] == "OrderService.discount"
    assert "OrderService.discount" in result.verified_grounding["src/main/java/shop/OrderService.java"]
    assert result.separation["exact"] is True and result.separation["top1_symbol_id"] == top.symbol_id
    # Planner grounding: the compact map leads the graph context; never a body.
    assert result.graph_rag_context.startswith("\n=== CODE INTELLIGENCE LOCALIZATION CANDIDATES")
    assert "1. [method] shop.OrderService.discount" in result.graph_rag_context


def test_without_a_structural_index_the_legacy_hybrid_leg_answers_unchanged(indexed, tmp_path):
    repo, cfg, embedder = indexed
    result = _retrieve(repo, cfg, embedder, "OrderService.discount must be capped", graph_path=None)
    assert result.localization_source == gr.LOCALIZATION_LEGACY and result.localization == []
    assert result.matched_files and result.retrieved_chunks


def test_legacy_only_files_are_bounded_to_uncovered_files_in_the_similarity_tier(indexed):
    repo, cfg, embedder = indexed
    result = _retrieve(repo, cfg, embedder, "discountPolicy cappedDiscount for every order")
    assert result.legacy_only_files == ["web/discountPolicy.js"]
    assert "web/discountPolicy.js" in result.matched_files
    assert not any(c.path.endswith(".js") for c in result.localization)
    # The similarity tier: scaled into (0, SIMILARITY_MAX], never above it.
    exact = _retrieve(repo, cfg, embedder, "OrderService.discount and cappedDiscount")
    assert exact.localization[0].lookup_key == "shop.OrderService.discount"
    assert exact.matched_files.index("src/main/java/shop/OrderService.java") < exact.matched_files.index(
        "web/discountPolicy.js")


def test_a_file_changed_since_indexing_is_localized_from_its_current_bytes(indexed):
    repo, cfg, embedder = indexed
    (repo / "src/main/java/shop/Inventory.java").write_text(INVENTORY.replace(
        "public int reserve(int quantity) {", "public int release(int quantity) {"))
    result = _retrieve(repo, cfg, embedder, "Inventory.release must not go negative")
    assert result.localization[0].lookup_key == "shop.Inventory.release"
    assert result.retrieval_member_hints["src/main/java/shop/Inventory.java"][0] == "Inventory.release"
    stale = _retrieve(repo, cfg, embedder, "Inventory.reserve must not go negative")
    assert all(c.lookup_key != "shop.Inventory.reserve" for c in stale.localization)


def test_fused_expansion_seeds_follow_the_corroboration_rule():
    def cand(path, score, *channels):
        return gr.LocalizationCandidate(f"id:{path}:{score}", path, "method", "k", "", score,
                                        tuple((c, 1.0) for c in channels))

    exact = cand("a", loc.EXACT_EVIDENCE, "qualified_symbol")
    agree = cand("b", 15.0, "fts", "vector")
    lexical_only = cand("c", 9.0, "fts")
    vector_only = cand("d", 8.0, "vector")
    assert gr.select_fused_expansion_seeds([exact, agree, lexical_only, vector_only], 5) == (
        ["a", "b"], gr.EXPANSION_CORROBORATED)
    assert gr.select_fused_expansion_seeds([lexical_only, vector_only], 5) == ([], gr.EXPANSION_NO_CORROBORATED)
    assert gr.select_fused_expansion_seeds([lexical_only], 5) == (["c"], gr.EXPANSION_LEXICAL_ONLY)
    assert gr.select_fused_expansion_seeds([vector_only], 5) == (["d"], gr.EXPANSION_EMBEDDING_ONLY)
    assert gr.select_fused_expansion_seeds([], 5) == ([], gr.EXPANSION_NO_VALID_EVIDENCE)


def test_the_vector_channel_credits_the_member_its_chunk_span_holds(indexed):
    repo, cfg, _ = indexed
    from kriya.code_intel.service import CodeIntelligenceService

    service = CodeIntelligenceService(str(repo), os.path.join(cfg.paths.memory, "dependency_graph.db"))
    chunk = {"filepath": "src/main/java/shop/OrderService.java", "span_start": 8, "span_end": 10}
    hits = service.locate("", semantic=[chunk])
    assert [(h.lookup_key, [c for c, _ in h.channels]) for h in hits] == [("shop.OrderService.discount", ["vector"])]
    assert hits[0].score == loc.semantic_weight(0)
    # A chunk spanning a whole type credits every member inside its span.
    whole = {"filepath": "src/main/java/shop/OrderService.java", "span_start": 3, "span_end": 11}
    assert {h.lookup_key for h in service.locate("", semantic=[whole])} == {
        "shop.OrderService.total", "shop.OrderService.discount"}
    service.close()
