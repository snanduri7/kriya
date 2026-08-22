"""Tests for kriya/knowledge/ - the structured knowledge-gap extraction backbone
(schema, rubric, repo_manifest/live_failure channels, staging, scaffold). Distinct
from tests/test_knowledge.py, which covers kriya/tools/knowledge.py (KnowledgeGuard/
registry adapters) - an unrelated, pre-existing module with a similar name.
"""
import json
import os
from unittest.mock import AsyncMock

import pytest

from kriya.analyzer.analyzer import RepositoryModel
from kriya.knowledge import scaffold, staging
from kriya.knowledge.channels.live_failure import (
    LiveFailureChannel,
    LiveFailureContext,
    _parse_fact_list,
)
from kriya.knowledge.channels.repo_manifest import RepoManifestChannel, RepoManifestContext
from kriya.knowledge.rubric import score_skill
from kriya.knowledge.schema import KNOWLEDGE_CATEGORIES, KnowledgeFact
from kriya.skills.skill import Skill


def _fact(category="Rules", value="A fact.", confidence="mechanical", verified=False, **kw):
    return KnowledgeFact(
        category=category,
        key=value[:20],
        value=value,
        source_channel=kw.pop("source_channel", "test"),
        extraction_confidence=confidence,
        provenance=kw.pop("provenance", "test"),
        verified=verified,
    )


# --- schema.py -----------------------------------------------------------------

def test_knowledge_fact_rejects_unknown_category():
    with pytest.raises(Exception):
        _fact(category="NotACategory")


def test_knowledge_fact_rejects_unknown_confidence_tier():
    with pytest.raises(Exception):
        _fact(confidence="not_a_tier")


# --- rubric.py -------------------------------------------------------------------

def test_score_skill_empty_facts_is_all_zero():
    readiness = score_skill("s", [])
    assert readiness.overall_level == 0
    assert set(readiness.missing_categories) == set(KNOWLEDGE_CATEGORIES)


def test_score_skill_level_progression_by_confidence():
    assert score_skill("s", [_fact(confidence="llm_inferred_no_quote")]).category_levels["Rules"] == 1
    assert score_skill("s", [_fact(confidence="llm_from_quote")]).category_levels["Rules"] == 2
    assert score_skill("s", [_fact(confidence="human_supplied")]).category_levels["Rules"] == 2
    assert score_skill("s", [_fact(confidence="mechanical")]).category_levels["Rules"] == 3
    assert score_skill("s", [_fact(confidence="llm_inferred_no_quote", verified=True)]).category_levels["Rules"] == 4


def test_score_skill_overall_is_the_weakest_category():
    readiness = score_skill("s", [
        _fact(category="Dependencies", confidence="mechanical"),
        _fact(category="APIs", confidence="llm_inferred_no_quote"),
    ])
    assert readiness.category_levels["Dependencies"] == 3
    assert readiness.category_levels["APIs"] == 1
    assert readiness.overall_level == 0  # every untouched category is still 0


# --- channels/repo_manifest.py ----------------------------------------------------

@pytest.mark.asyncio
async def test_repo_manifest_channel_scopes_to_relevant_dependencies_only():
    skill = Skill(name="qpid", description="x", tags=["ignite", "qpid"])
    repo_model = RepositoryModel(
        root_path="/tmp/proj",
        dependencies=["ignite-core", "some-unrelated-lib"],
        dependency_versions={"ignite-core": "2.18.0", "some-unrelated-lib": "9.9.9"},
        frameworks=[],
    )
    channel = RepoManifestChannel()
    facts = await channel.extract(RepoManifestContext(skill=skill, repo_model=repo_model))

    keys = {f.key for f in facts}
    assert "ignite-core" in keys
    assert "some-unrelated-lib" not in keys
    assert all(f.extraction_confidence == "mechanical" for f in facts)


@pytest.mark.asyncio
async def test_repo_manifest_channel_empty_when_skill_not_relevant():
    skill = Skill(name="django-app", description="x", tags=["django"])
    repo_model = RepositoryModel(
        root_path="/tmp/proj", dependencies=["ignite-core"],
        dependency_versions={"ignite-core": "2.18.0"}, frameworks=[],
    )
    channel = RepoManifestChannel()
    facts = await channel.extract(RepoManifestContext(skill=skill, repo_model=repo_model))
    assert facts == []


@pytest.mark.asyncio
async def test_repo_manifest_channel_handles_missing_version():
    skill = Skill(name="qpid", description="x", tags=["ignite"])
    repo_model = RepositoryModel(
        root_path="/tmp/proj", dependencies=["ignite-core"],
        dependency_versions={}, frameworks=[],
    )
    channel = RepoManifestChannel()
    facts = await channel.extract(RepoManifestContext(skill=skill, repo_model=repo_model))
    dep_facts = [f for f in facts if f.category == "Dependencies"]
    assert len(dep_facts) == 1
    assert "no version captured" in dep_facts[0].provenance


# --- channels/live_failure.py ------------------------------------------------------

def test_parse_fact_list_handles_markdown_fenced_json():
    text = '```json\n[{"category": "Rules", "value": "x", "quote": null}]\n```'
    parsed = _parse_fact_list(text)
    assert parsed == [{"category": "Rules", "value": "x", "quote": None}]


def test_parse_fact_list_recovers_array_with_prose_around_it():
    text = 'Sure, here is the array:\n[{"category": "Rules", "value": "x", "quote": null}]\nHope that helps.'
    parsed = _parse_fact_list(text)
    assert parsed == [{"category": "Rules", "value": "x", "quote": None}]


def test_parse_fact_list_returns_empty_on_garbage():
    assert _parse_fact_list("not json at all") == []


@pytest.mark.asyncio
async def test_live_failure_channel_tags_confidence_by_quote_presence():
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value=json.dumps([
        {"category": "Rules", "value": "Always close the client.", "quote": "client leak detected"},
        {"category": "APIs", "value": "Some vague description with no grounding.", "quote": None},
    ]))
    channel = LiveFailureChannel(llm)
    facts = await channel.extract(LiveFailureContext(
        error_context="RUNTIME VERIFICATION: client leak detected", file_contents={"Foo.java": "class Foo {}"},
    ))

    by_value = {f.value: f for f in facts}
    assert by_value["Always close the client."].extraction_confidence == "llm_from_quote"
    assert by_value["Some vague description with no grounding."].extraction_confidence == "llm_inferred_no_quote"


@pytest.mark.asyncio
async def test_live_failure_channel_drops_items_with_unknown_category():
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value=json.dumps([
        {"category": "NotACategory", "value": "x", "quote": None},
        {"category": "Rules", "value": "A real fact.", "quote": None},
    ]))
    channel = LiveFailureChannel(llm)
    facts = await channel.extract(LiveFailureContext(error_context="err", file_contents={}))
    assert len(facts) == 1
    assert facts[0].value == "A real fact."


@pytest.mark.asyncio
async def test_live_failure_channel_folds_self_correction_transcript_into_the_prompt():
    """Added 2026-08-22: a resolved self-correction loop is the richest
    evidence this pipeline produces all day (explicit tool-grounded
    diagnosis, a real before/after edit, a real verification call) - when
    present, it must reach the extraction prompt, not just error_context/
    file_contents."""
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value="[]")
    channel = LiveFailureChannel(llm)
    await channel.extract(LiveFailureContext(
        error_context="RUNTIME VERIFICATION FAILURE: could not find or load main class",
        file_contents={"pom.xml": "<project></project>"},
        transcript=[
            {"turn": 0, "tool": "list_compiled_output", "arguments": {}, "result": "(target/classes is empty)"},
            {"turn": 0, "tool": "apply_patch", "arguments": {"filepath": "pom.xml"}, "result": "Patch applied to 'pom.xml'."},
        ],
    ))

    prompt_sent = llm.complete.call_args.kwargs["user_prompt"]
    assert "list_compiled_output" in prompt_sent
    assert "target/classes is empty" in prompt_sent


@pytest.mark.asyncio
async def test_live_failure_channel_omits_transcript_section_when_none_given():
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value="[]")
    channel = LiveFailureChannel(llm)
    await channel.extract(LiveFailureContext(error_context="err", file_contents={}))

    prompt_sent = llm.complete.call_args.kwargs["user_prompt"]
    assert "tool-assisted diagnosis loop" not in prompt_sent


@pytest.mark.asyncio
async def test_live_failure_channel_returns_empty_list_on_llm_failure():
    llm = AsyncMock()
    llm.complete = AsyncMock(side_effect=RuntimeError("backend unreachable"))
    channel = LiveFailureChannel(llm)
    facts = await channel.extract(LiveFailureContext(error_context="err", file_contents={}))
    assert facts == []


# --- staging.py --------------------------------------------------------------------

def test_stage_facts_dedups_against_existing_staged_and_approved(tmp_path):
    skill_folder = str(tmp_path / "auto-test")
    written = staging.stage_facts(skill_folder, [_fact(value="Always close the client.")])
    assert len(written) == 1

    # exact duplicate re-stage attempt is filtered
    written_again = staging.stage_facts(skill_folder, [_fact(value="Always close the client.")])
    assert written_again == []


def test_promote_staged_writes_rules_txt_and_knowledge_json_and_clears_staged(tmp_path):
    skill_folder = str(tmp_path / "auto-test")
    staging.stage_facts(skill_folder, [_fact(category="Dependencies", value="Uses foo 1.0.")])

    promoted = staging.promote_staged(skill_folder)
    assert len(promoted) == 1
    assert not os.path.exists(staging._staged_path(skill_folder))

    with open(os.path.join(skill_folder, "rules.txt")) as f:
        assert "Uses foo 1.0." in f.read()

    knowledge = staging.load_knowledge(skill_folder)
    assert len(knowledge) == 1
    assert knowledge[0].value == "Uses foo 1.0."

    with open(os.path.join(skill_folder, "rule_provenance.json")) as f:
        provenance = json.load(f)
    assert provenance[0]["source"].startswith("knowledge_channel:")


def test_promote_staged_noop_when_nothing_staged(tmp_path):
    skill_folder = str(tmp_path / "auto-test")
    assert staging.promote_staged(skill_folder) == []


def test_record_direct_fact_skips_near_duplicate_of_existing_rule(tmp_path):
    skill_folder = str(tmp_path / "auto-test")
    os.makedirs(skill_folder)
    with open(os.path.join(skill_folder, "rules.txt"), "w") as f:
        f.write("Always close the client connection after use in every code path.")

    written = staging.record_direct_fact(skill_folder, _fact(
        value="Always close the client connection after use in every code path.",
        confidence="human_supplied",
    ))
    assert written is False


def test_record_direct_fact_writes_new_fact(tmp_path):
    skill_folder = str(tmp_path / "auto-test")
    written = staging.record_direct_fact(skill_folder, _fact(
        category="APIs", value="Use IgniteCache.put(K, V) for writes.", confidence="human_supplied",
    ))
    assert written is True
    knowledge = staging.load_knowledge(skill_folder)
    assert knowledge[0].value == "Use IgniteCache.put(K, V) for writes."
    assert knowledge[0].category == "APIs"


# --- scaffold.py -------------------------------------------------------------------

def test_generate_gap_questions_skips_categories_at_or_above_threshold():
    readiness = score_skill("s", [_fact(category="Dependencies", confidence="mechanical")])
    questions = scaffold.generate_gap_questions(readiness)
    assert not any(q.startswith("[Dependencies") for q in questions)
    assert any(q.startswith("[APIs") for q in questions)
    assert len(questions) == len(KNOWLEDGE_CATEGORIES) - 1


def test_generate_gap_questions_empty_when_everything_ready():
    facts = [_fact(category=cat, confidence="mechanical") for cat in KNOWLEDGE_CATEGORIES]
    readiness = score_skill("s", facts)
    assert scaffold.generate_gap_questions(readiness) == []


def test_record_scaffold_answer_round_trips_through_staging(tmp_path):
    skill_folder = str(tmp_path / "auto-test")
    written = scaffold.record_scaffold_answer(skill_folder, "APIs", "IgniteCache.put(K, V) is the write method.")
    assert written is True
    knowledge = staging.load_knowledge(skill_folder)
    assert knowledge[0].source_channel == "human_scaffold"
    assert knowledge[0].extraction_confidence == "human_supplied"
