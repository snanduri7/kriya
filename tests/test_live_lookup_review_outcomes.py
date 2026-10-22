"""1-A (Wave-1 final fix-forward): what each live-lookup REFERENCE review does with the resolved approval outcome.

The review of externally fetched references (``web_lookup_callback(found)``) runs at two sites, ``stage_analysis`` (Stage 1.2, a goal-named skill gap) and ``stage_architecture``
(Stage 2B, a design-derived technology). Both adopt the fetched references ONLY on an APPROVED outcome. The blind reviewer's mutants A17/A18 (``.approved`` -> ``not .declined``,
i.e. an unrecognised or unavailable answer adopts the references) survived because no test acted on the resolved outcome; ``test_no_approval_callback_answer_is_read_directly`` only
proves the call is wrapped by a resolver. These tests drive the real workflow and observe the EFFECT: the extracted rule reaches the skill, or it does not.
"""
import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from kriya.config.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.policy.approval import APPROVAL_LAUNCH_CANNOT_ASK, ApprovalOutcome
from kriya.skills.skill import SkillEngine
from kriya.workflow.workflow import WorkflowEngine

RULE = "The magic widget constant is 42."
EXTRACTION = json.dumps({"rules": [RULE], "examples": {}, "conflicts": []})

# (answer given by the review callback, are the fetched references adopted?)
ANSWERS = [
    pytest.param(True, True, id="True"),
    pytest.param(ApprovalOutcome.approve(), True, id="APPROVED"),
    pytest.param(False, False, id="False"),
    pytest.param(ApprovalOutcome.decline(), False, id="DECLINED"),
    pytest.param(ApprovalOutcome.unavailable_because(APPROVAL_LAUNCH_CANNOT_ASK), False, id="UNAVAILABLE"),
    pytest.param(None, False, id="None"),
    pytest.param("yes", False, id="truthy-string"),
    pytest.param(1, False, id="truthy-int"),
]
KINDS = ["sync", "task"]     # the other awaitable shapes (coroutine, Future) are pinned per site by tests/test_n1_f4_approval_outcome.py; a Task is the shape that used to read as approval


def _review_callback(kind, value):
    """The review callback answering ``value`` as a sync value, a coroutine, an asyncio Task or a Future (the Task/Future are built inside the running loop)."""
    async def coroutine():
        return value

    def callback(found):
        assert found, "the review is only asked when something was found"
        if kind == "sync":
            return value
        if kind == "coroutine":
            return coroutine()
        if kind == "task":
            return asyncio.ensure_future(coroutine())
        future = asyncio.get_running_loop().create_future()
        future.set_result(value)
        return future
    return callback


def _config(tmp_path, skills_dir, term):
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.web_lookup_enabled = True
    cfg.autonomy.web_lookup_auto_approve = True      # the pre-send query gate is not under test
    cfg.search.public_terms = [term]
    cfg.search.base_url = "http://fake-search:8080"
    return cfg


def _run(tmp_path, cfg, responses, found, **kwargs):
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=responses)
    engine = WorkflowEngine(Kernel(config=cfg), llm)
    with patch("kriya.tools.search.search_web", new=AsyncMock(return_value=found)), patch("kriya.tools.web.fetch_url_text", new=AsyncMock(return_value=RULE)):
        result = asyncio.run(engine.run_generation_workflow(goal=kwargs.pop("goal"), workspace_path=str(tmp_path), **kwargs))
    return result, llm


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("answer,adopted", ANSWERS)
def test_stage_analysis_adopts_the_fetched_references_only_on_an_approved_review(tmp_path, kind, answer, adopted):
    """Stage 1.2: an adopted reference resolves the gap (the human-ask fallback never fires); anything else falls back to asking, exactly as if lookup had found nothing."""
    skills_dir = tmp_path / "skills"
    folder = skills_dir / "widgetlib"
    folder.mkdir(parents=True)
    (folder / "skill.yaml").write_text("name: widgetlib\ndescription: Test\ntags: [widgetlib]\n")
    (folder / "rules.txt").write_text("Existing rule.\n")
    cfg = _config(tmp_path, skills_dir, "widgetlib")
    developer = [("Step 1: Write code"), "Design: Write app.py", '[{"filepath": "app.py", "content": "print(1)\\n"}]', "Review: Approved"]
    responses = ([EXTRACTION] if adopted else []) + developer
    asked = []

    def skill_gap_callback(reason, names):
        asked.append(names)
        return None
    found = [{"term": "widgetlib", "title": "Widgetlib Docs", "url": "https://example.com/widgetlib", "snippet": "..."}]
    result, _llm = _run(tmp_path, cfg, responses, found, goal="Build something with the widgetlib skill", skill_gap_callback=skill_gap_callback,
                  web_lookup_callback=_review_callback(kind, answer))
    assert result["quality_gates_passed"] is True
    engine = SkillEngine(str(skills_dir), load_global=False)
    engine.discover_and_load()
    rules = engine.get_skill("widgetlib").rules
    assert (RULE in rules) is adopted, rules
    assert asked == ([] if adopted else [["widgetlib"]])


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("answer,adopted", ANSWERS)
def test_stage_architecture_adopts_the_design_derived_references_only_on_an_approved_review(tmp_path, kind, answer, adopted):
    """Stage 2B: the design names a technology no skill covers; an unapproved review creates no skill and adopts nothing."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    cfg = _config(tmp_path, skills_dir, "gizmolib")
    responses = (["Step 1: Write code", "Design: use gizmolib==3.1.0 to connect to the service"] + ([json.dumps({"rules": ["Use gizmolib.connect() to open a connection."],
                                                                                                                   "examples": {}, "conflicts": []})] if adopted else [])
                 + ['[{"filepath": "app.py", "content": "print(1)\\n"}]', "Review: Approved"])
    found = [{"term": "gizmolib", "title": "Gizmolib Docs", "url": "https://example.com/gizmolib", "snippet": "..."}]
    result, _llm = _run(tmp_path, cfg, responses, found, goal="Build an app that talks to an external service", web_lookup_callback=_review_callback(kind, answer))
    assert result["quality_gates_passed"] is True
    engine = SkillEngine(str(skills_dir), load_global=False)
    engine.discover_and_load()
    if adopted:
        assert "Use gizmolib.connect() to open a connection." in engine.get_skill("gizmolib").rules
    else:
        assert not (skills_dir / "gizmolib").exists(), "an unapproved review must not even bootstrap the skill"
        with pytest.raises(KeyError):
            engine.get_skill("gizmolib")


@pytest.mark.parametrize("path", ["analysis", "architecture"])
def test_a_failing_review_callback_adopts_nothing(tmp_path, path):
    """A review callback that raises is the existing fail-closed branch: the references are discarded (and the error is not the run's)."""
    def boom(found):
        raise RuntimeError("the review channel broke")
    if path == "analysis":
        skills_dir = tmp_path / "skills"
        folder = skills_dir / "widgetlib"
        folder.mkdir(parents=True)
        (folder / "skill.yaml").write_text("name: widgetlib\ndescription: Test\ntags: [widgetlib]\n")
        (folder / "rules.txt").write_text("Existing rule.\n")
        cfg = _config(tmp_path, skills_dir, "widgetlib")
        responses = ["Step 1: Write code", "Design: Write app.py", '[{"filepath": "app.py", "content": "print(1)\\n"}]', "Review: Approved"]
        found = [{"term": "widgetlib", "title": "Widgetlib Docs", "url": "https://example.com/widgetlib", "snippet": "..."}]
        _result, llm = _run(tmp_path, cfg, responses, found, goal="Build something with the widgetlib skill", skill_gap_callback=lambda reason, names: None, web_lookup_callback=boom)
        engine = SkillEngine(str(skills_dir), load_global=False)
        engine.discover_and_load()
        assert RULE not in engine.get_skill("widgetlib").rules
        assert llm.complete.await_count == 4, "no extraction call: the discarded references were never read"
    else:
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        cfg = _config(tmp_path, skills_dir, "gizmolib")
        responses = ["Step 1: Write code", "Design: use gizmolib==3.1.0 to connect to the service", '[{"filepath": "app.py", "content": "print(1)\\n"}]', "Review: Approved"]
        found = [{"term": "gizmolib", "title": "Gizmolib Docs", "url": "https://example.com/gizmolib", "snippet": "..."}]
        _result, llm = _run(tmp_path, cfg, responses, found, goal="Build an app that talks to an external service", web_lookup_callback=boom)
        assert not (skills_dir / "gizmolib").exists()
        assert llm.complete.await_count == 4, "no extraction call: the discarded references were never read"
