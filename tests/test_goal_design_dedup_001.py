"""GOAL-DESIGN-DEDUP-001: GOAL != PREDETERMINED DESIGN.

Measured (Graphify preflight at c2b480b): the enforce bounded subtask passed
predetermined_design=target_goal, so the full original goal (3,350 chars)
reached the Developer three times as mandatory text - the overall request
constraints, the task (target_goal) and the "design". Under the qwen3.6
tokenizer the regression-retry fallback request needed 17,551 tokens against
a 16,096-token capacity; one copy fewer fits.

A validated Subtask has no design artifact of its own: the subtask supplies
an EMPTY design (still supplied, so the Architect is not called), and the
goal keeps its two authoritative representations. A genuine design passed to
run_generation_workflow still renders, on every Developer request."""
from unittest.mock import AsyncMock

import pytest
from test_workflow import _init_git_repo
from test_workflow_controller_enforce import _patched, _two_subtask_plan, _workflow_engine

from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow.workflow import WorkflowEngine
from kriya.workflow.workflow_controller import BOUNDED_SUBTASK_DESIGN, WorkflowController

GOAL = "GOALTOKEN-7Q: make generic calls resolve to their declared member exactly once"


async def _bounded_calls(tmp_path):
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        path = "a.py" if len(calls) == 1 else "b.py"
        (tmp_path / path).write_text(f"# {path}")
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(_two_subtask_plan())
    with p1, p2, p3:
        await WorkflowController(we).execute(GOAL, str(tmp_path), migration_mode="enforce")
    assert calls, "the bounded subtask call was not reached"
    return calls


@pytest.mark.asyncio
async def test_the_bounded_subtask_supplies_an_empty_design_never_the_goal(tmp_path):
    for call in await _bounded_calls(tmp_path):
        assert call["predetermined_design"] == BOUNDED_SUBTASK_DESIGN == ""
        assert call["predetermined_design"] is not None  # supplied: the Architect is not re-run
        assert GOAL not in call["predetermined_design"]


@pytest.mark.asyncio
async def test_the_goal_keeps_both_authoritative_representations(tmp_path):
    for call in await _bounded_calls(tmp_path):
        assert "Overall request constraints" in call["supplementary_context"]
        assert GOAL in call["supplementary_context"]
        assert GOAL in call["goal"]  # the Developer's task (target_goal embeds the authoritative goal)


@pytest.mark.asyncio
async def test_the_original_goal_reaches_a_subtask_twice_not_three_times(tmp_path):
    for call in await _bounded_calls(tmp_path):
        copies = sum(str(call[key]).count(GOAL) for key in ("supplementary_context", "goal", "predetermined_design"))
        assert copies == 2


@pytest.mark.asyncio
async def test_requirement_and_obligation_authority_inputs_are_unchanged(tmp_path):
    """Requirement lineage and verification authority come from the user's own
    goal (grounding_goal) and the structured work unit - never the design."""
    for call in await _bounded_calls(tmp_path):
        assert call["grounding_goal"] == GOAL
        assert call["work_unit"].work_unit_id in ("s1", "s2")
        assert call["predetermined_plan"] and call["predetermined_architect_files"]


def _engine(tmp_path, developer_answers):
    """The Reviewer approves; the Developer gives the scripted answers in
    order, then keeps giving the last one."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    llm = LLMClient(cfg)
    answers = list(developer_answers)

    async def complete(system_prompt, *args, **kwargs):
        if "Review" in str(system_prompt):
            return "Review: Approved"
        return answers.pop(0) if len(answers) > 1 else answers[0]

    llm.complete = AsyncMock(side_effect=complete)
    return WorkflowEngine(Kernel(config=cfg), llm), llm


_DEVELOPER_ROLES = ("You are the Kriya Developer Agent", "You are the Kriya File List Planner")


def _developer_requests(llm):
    """(system, user) of every Developer request: generation, repair, and the
    fallback escalation's file-list request - never review or lesson extraction."""
    requests = []
    for call in llm.complete.await_args_list:
        system = str(call.args[0] if call.args else call.kwargs.get("system_prompt", ""))
        if system.startswith(_DEVELOPER_ROLES):
            requests.append((system, call.args[1] if len(call.args) > 1 else call.kwargs.get("user_prompt", "")))
    return requests


@pytest.mark.asyncio
async def test_a_genuine_design_still_renders_on_the_first_request_and_on_the_retry(tmp_path):
    design = "GENUINE-DESIGN-9K: keep add() pure and module-level"
    broken = '[{"filepath": "math.py", "content": "def add(a, b)\\n    return a + b\\n"}]'
    fixed = '[{"filepath": "math.py", "content": "def add(a, b):\\n    return a + b\\n"}]'
    we, llm = _engine(tmp_path, [broken, fixed])
    res = await we.run_generation_workflow(
        goal="Create math library", workspace_path=str(tmp_path),
        predetermined_plan="Implement math.py", predetermined_design=design,
        predetermined_architect_files=["math.py"],
    )
    requests = _developer_requests(llm)
    repairs = [user for system, user in requests if "MODE: REPAIR" in system]
    file_lists = [user for system, user in requests if system.startswith("You are the Kriya File List Planner")]
    assert repairs, "no retry happened"
    assert file_lists, "no fallback escalation happened"
    assert all(design in user for _system, user in requests)
    assert res["design"] == design


@pytest.mark.asyncio
async def test_an_empty_design_skips_the_architect_and_restates_nothing(tmp_path):
    good = '[{"filepath": "math.py", "content": "def add(a, b):\\n    return a + b\\n"}]'
    we, llm = _engine(tmp_path, [good])
    res = await we.run_generation_workflow(
        goal="Create math library", workspace_path=str(tmp_path),
        predetermined_plan="Implement math.py", predetermined_design=BOUNDED_SUBTASK_DESIGN,
        predetermined_architect_files=["math.py"],
    )
    assert res["quality_gates_passed"] is True
    assert res["design"] == ""
    assert llm.complete.await_count == 2  # Developer + Reviewer: no Planner, no Architect


@pytest.mark.asyncio
async def test_a_graphify_sized_goal_is_not_duplicated_through_the_design_slot(tmp_path, monkeypatch):
    """The measured case: a 3,350-character goal (Graphify's size; synthetic
    text, never the benchmark's own) reaches each subtask twice, not three
    times, and never through the design slot."""
    import test_goal_design_dedup_001 as this

    long_goal = ("GOALTOKEN-7Q " + "the declared member must receive its calls edge; " * 70)[:3350]
    monkeypatch.setattr(this, "GOAL", long_goal)
    for call in await _bounded_calls(tmp_path):
        assert call["predetermined_design"] == ""
        assert sum(str(call[key]).count(long_goal)
                   for key in ("supplementary_context", "goal", "predetermined_design")) == 2
