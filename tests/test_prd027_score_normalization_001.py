"""PRD027-SCORE-NORMALIZATION-001: direct query evidence never loses
priority to graph-expanded evidence merely because the graph walk scores on
a numerically larger scale.

A direct hit carries its hybrid RRF score (at most ~0.033); a graph-expanded
file carries relation weight over hops (0.5-1.0). The budget builders
degrade and omit the lowest score first, so comparing the raw scales shrank
or dropped the files the query itself matched before secondary evidence.
graph_retrieval.evidence_scores ranks the two classes in explicit tiers.
"""
import asyncio
import os

import pytest
from test_prompt_budget_fit_001ab import EMBEDDER, _config, _workspace_with_index

from kriya.memory.vector import LocalVectorStore
from kriya.workflow import graph_retrieval
from kriya.workflow.context_budget import RetrievalLimits, build_code_context_package, estimate_tokens, skeletonize_code
from kriya.workflow.context_recall_fixtures import JAVA_SHOP
from kriya.workflow.graph_retrieval import DIRECT_EVIDENCE_TIER, evidence_scores, retrieve_graph_context

# --- the ranking --------------------------------------------------------------

def test_every_direct_hit_outranks_every_expanded_file_whatever_the_raw_scales():
    ranked = evidence_scores({"pom.xml": 0.0328, "Test.java": 0.0161}, {"PriceCalculator.java": 1.0, "Util.java": 0.5})
    assert min(ranked["pom.xml"], ranked["Test.java"]) > max(ranked["PriceCalculator.java"], ranked["Util.java"])


@pytest.mark.parametrize("graph_scale", [1.0, 10.0, 1000.0])
def test_the_invariant_holds_for_any_graph_scale(graph_scale):
    """Normalization, not the current 0.5-1.0 range of relation weights,
    keeps direct evidence first: a graph walk scoring on any larger scale
    still ranks below every direct hit."""
    ranked = evidence_scores({"pom.xml": 0.0328}, {"A.java": 1.0 * graph_scale, "B.java": 0.5 * graph_scale})
    assert ranked["pom.xml"] > max(ranked["A.java"], ranked["B.java"])
    assert ranked["A.java"] == 1.0 and ranked["B.java"] == 0.5


def test_order_within_each_class_is_its_raw_order():
    ranked = evidence_scores({"a": 0.03, "b": 0.01, "c": 0.02}, {"x": 0.5, "y": 1.0, "z": 0.75})
    assert sorted("abc", key=ranked.get, reverse=True) == ["a", "c", "b"]
    assert sorted("xyz", key=ranked.get, reverse=True) == ["y", "z", "x"]


def test_the_tiers_are_explicit_and_bounded():
    ranked = evidence_scores({"a": 0.03, "b": 0.01}, {"x": 1.0, "y": 0.25})
    assert all(DIRECT_EVIDENCE_TIER < ranked[f] <= DIRECT_EVIDENCE_TIER + 1 for f in "ab")
    assert all(0 < ranked[f] <= DIRECT_EVIDENCE_TIER for f in "xy")


def test_a_file_in_both_classes_is_direct_evidence():
    ranked = evidence_scores({"a": 0.01}, {"a": 1.0, "x": 1.0})
    assert ranked["a"] > ranked["x"]


@pytest.mark.parametrize("direct, expanded", [({}, {"x": 1.0}), ({"a": 0.03}, {}), ({}, {})])
def test_a_missing_class_is_simply_absent(direct, expanded):
    """Embedding-only or keyword-only retrieval, or no graph walk at all."""
    ranked = evidence_scores(direct, expanded)
    assert set(ranked) == set(direct) | set(expanded)
    assert all(score > 0 for score in ranked.values())


# --- the invariant under a binding budget ---------------------------------------

def _files(tmp_path):
    direct = "pom.xml"
    expanded = "src/main/java/com/shop/PriceCalculator.java"
    (tmp_path / "src/main/java/com/shop").mkdir(parents=True)
    (tmp_path / direct).write_text("<project>\n" + "  <dependency>junit</dependency>\n" * 60 + "</project>\n")
    body = "".join(f"    public int step{i}(int v) {{\n        return v + {i};\n    }}\n\n" for i in range(60))
    (tmp_path / expanded).write_text(f"package com.shop;\n\npublic class PriceCalculator {{\n{body}}}\n")
    return direct, expanded


def _tiers(tmp_path, scores, budget=None):
    """(direct tier, expanded tier) of one package build. The default budget
    holds the direct file whole beside the expanded file's signatures - one
    of the two has to give way."""
    direct, expanded = _files(tmp_path)
    if budget is None:
        budget = (estimate_tokens((tmp_path / direct).read_text())
                  + estimate_tokens(skeletonize_code((tmp_path / expanded).read_text(), expanded, "signatures")) + 5)
    _, package = build_code_context_package([direct], [expanded], str(tmp_path), budget, file_scores=scores)
    tiers = {item.path: item.tier for item in package.relevant_files}
    tiers.update({entry["path"]: "omitted" for entry in package.omitted})
    return tiers[direct], tiers[expanded]


DIRECT, EXPANDED = "pom.xml", "src/main/java/com/shop/PriceCalculator.java"


def test_under_a_binding_budget_the_graph_file_gives_way_first(tmp_path):
    direct_tier, expanded_tier = _tiers(tmp_path, evidence_scores({DIRECT: 0.0328}, {EXPANDED: 1.0}))
    assert direct_tier == "full" and expanded_tier != "full"


def test_the_raw_scales_degraded_the_direct_hit_first(tmp_path):
    """Control: the pre-fix scores (raw RRF beside raw graph weight) shrink
    the file the query itself matched first."""
    direct_tier, _ = _tiers(tmp_path, {DIRECT: 0.0328, EXPANDED: 1.0})
    assert direct_tier != "full"


def test_useful_graph_evidence_is_kept_whenever_it_fits(tmp_path):
    assert _tiers(tmp_path, evidence_scores({DIRECT: 0.0328}, {EXPANDED: 1.0}), 10**6) == ("full", "full")


# --- the real retrieval path --------------------------------------------------------

def _retrieve(tmp_path, goal, budget):
    tmp_path.mkdir(exist_ok=True)
    cfg = _config(tmp_path, 32768)
    workspace = _workspace_with_index(tmp_path, cfg, 40)

    async def run():
        store = LocalVectorStore(os.path.join(cfg.paths.memory, "vector_index.db"))
        try:
            return await retrieve_graph_context(
                goal, str(workspace), embed_client=EMBEDDER, vector_store=store,
                dependency_graph_path=os.path.join(cfg.paths.memory, "dependency_graph.db"),
                limits=RetrievalLimits(top_k=5, max_hops=2, max_neighborhood_results=30),
                embedding_model=cfg.embedding.model, budget_limit=lambda: budget,
            )
        finally:
            store.close()

    return asyncio.run(run())


JUNIT_GOAL = next(case.goal for case in JAVA_SHOP.cases if case.name == "junit-upgrade")


def test_retrieval_ranks_the_querys_own_hits_above_the_graph_walk(tmp_path):
    result = _retrieve(tmp_path, JUNIT_GOAL, 10**6)
    assert result.matched_files and result.related_files
    assert min(result.file_scores[f] for f in result.matched_files) > \
        max(result.file_scores[f] for f in result.related_files)


_TIER_RANK = {"full": 3, "skeleton": 2, "signatures": 1, "omitted": 0}


def test_retrieval_under_a_binding_budget_keeps_direct_hits_ahead_of_the_graph_walk(tmp_path):
    """junit-upgrade: the golden pom.xml is a direct hit; before the fix it
    scored ~0.03 against the graph walk's 0.5-1.0, so a binding budget shrank
    the query's own hits before the files the walk added."""
    whole = _retrieve(tmp_path / "whole", JUNIT_GOAL, 10**6)
    assert "pom.xml" in whole.matched_files and whole.related_files
    matched_cost = sum(estimate_tokens(item.content) for item in whole.context_package.relevant_files
                       if item.path in whole.matched_files)
    constrained = _retrieve(tmp_path / "tight", JUNIT_GOAL, matched_cost + 5)
    tiers = {item.path: item.tier for item in constrained.context_package.relevant_files}
    tiers.update({entry["path"]: "omitted" for entry in constrained.context_package.omitted})
    assert tiers["pom.xml"] == "full"
    assert min(_TIER_RANK[tiers[f]] for f in constrained.matched_files) >= \
        max(_TIER_RANK[tiers[f]] for f in constrained.related_files)
    assert any(tiers[f] != "full" for f in constrained.related_files)


def test_the_ranking_is_part_of_the_certification_identity(monkeypatch):
    from kriya.workflow import context_certification as cc

    before = cc.index_implementation_digest()
    real = cc.inspect.getsource
    monkeypatch.setattr(cc.inspect, "getsource", lambda o: "changed" if o is graph_retrieval else real(o))
    assert cc.index_implementation_digest() != before
    assert "def evidence_scores" in real(graph_retrieval)
