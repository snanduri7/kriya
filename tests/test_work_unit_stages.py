"""The design stages of a work unit (full modularization W4, E3) and, at the end, its review, approval/apply and lesson
stages (E4): stages 0-3 moved verbatim out of
``WorkflowEngine.run_generation_workflow`` into ``kriya/workflow/stage_{knowledge_guard,analysis,planning,architecture}.py``,
driven by ``kriya/workflow/work_unit_runner.py`` over one ``StageContext``, every collaborator in ``StageDeps``.

The end-to-end preservation proof is the kernel goldens (``tests/state_machine/test_sm_kernel_golden.py``: the eight
``legacy_*`` stage scenarios written before the move, and every E1 scenario). These tests drive each stage alone with
``StageContext`` fakes: the engine-built ``StageDeps`` (``WorkflowEngine._work_unit_deps``, a real engine on a real
``AppConfig``) with only the collaborator under test replaced, so every flag the stage reads is a real default.
"""
import asyncio
import dataclasses
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.tools.knowledge import GapReport
from kriya.workflow import work_unit_runner
from kriya.workflow.graph_retrieval import GraphRetrievalResult
from kriya.workflow.stage_analysis import analyze_work_unit, record_active_skills
from kriya.workflow.stage_architecture import design_work_unit
from kriya.workflow.stage_context import (
    StageCollaboratorMissing,
    StageContext,
    design_stage_fields,
    not_injected,
)
from kriya.workflow.stage_knowledge_guard import check_knowledge_guard
from kriya.workflow.stage_planning import plan_work_unit
from kriya.workflow.state import GenerationState
from kriya.workflow.triage import ChangeKind
from kriya.workflow.untrusted_context import fence_untrusted_reference
from kriya.workflow.workflow import WorkflowEngine

GAP = {"library": "mylib", "version": "1.2.3", "release_date": None, "risk_level": "MEDIUM",
       "reason": "No verified skill file found in the skills directory for 'mylib'."}


def _engine(tmp_path):
    cfg = AppConfig()
    cfg.paths.skills, cfg.paths.memory = str(tmp_path / "skills"), str(tmp_path / "memory")
    cfg.skills.load_global = cfg.skills.load_cwd = False
    cfg.knowledge.offline_mode = True
    return WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))


def _deps(engine, **overrides):
    return dataclasses.replace(engine._work_unit_deps(), trace_events=lambda state: [{"kind": "scripted"}], **overrides)


def _ctx(tmp_path, **fields):
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    (workspace / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    values = dict(goal="Add mul to calc.py.", workspace_path=str(workspace), state=GenerationState(),
                  trace_id="trace-1", start_time=0.0, run_id="run-1")
    values.update(fields)
    return StageContext(**values)


class _Guard:
    """KnowledgeGuard(...) -> itself; check_goal answers the scripted gaps (per text)."""

    def __init__(self, gaps_for):
        self.gaps_for, self.checked, self.kwargs = gaps_for, [], None

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        return self

    def check_goal(self, text, workspace_path=""):
        self.checked.append(text)
        report = GapReport()
        report.gaps = [dict(gap) for gap in self.gaps_for(text)]
        return report


def _log_run():
    return patch("kriya.control.trace.TraceLogger.log_run")


def _events(ctx, kind):
    return [event for event in ctx.state.run_events if event.kind == kind]


# ----------------------------------------------------------------------------- the dependencies


def test_the_engine_injects_every_design_stage_collaborator(tmp_path):
    engine = _engine(tmp_path)
    deps = engine._work_unit_deps()
    assert not_injected(deps) == []
    assert len(design_stage_fields()) == 61
    assert (deps.planner, deps.architect, deps.skill_gap_agent, deps.llm, deps.engineering_triage) == (
        engine.planner, engine.architect, engine.skill_gap_agent, engine.llm, engine.engineering_triage)
    assert deps.registry is engine.kernel.registry and deps.config is engine.kernel.config
    assert deps.approve_web_lookup == engine._approve_web_lookup and deps.authorize_action == engine._authorize_action


def test_a_stage_given_lifecycle_only_deps_fails_closed(tmp_path):
    """E2's ``_stage_deps()`` carries no design collaborators: their defaults raise on any use, never a silent None."""
    deps = _engine(tmp_path)._stage_deps()
    assert sorted(not_injected(deps)) == sorted(design_stage_fields())
    with pytest.raises(StageCollaboratorMissing, match=r"StageDeps\.KnowledgeGuard was not injected"):
        asyncio.run(check_knowledge_guard(_ctx(tmp_path), deps))
    with pytest.raises(StageCollaboratorMissing, match="CALLABLE_KINDS"):
        _ = "function" in deps.CALLABLE_KINDS
    assert dataclasses.replace(deps).registry is not None   # protocol probes still work on the dataclass


# ----------------------------------------------------------------------------- stage 0: KnowledgeGuard


@pytest.mark.parametrize("fields, stops", [
    ({}, True),
    ({"knowledge_risk_confirmed": True}, False),
    ({"resume_state": {"plan": "p"}}, False),                     # a resumed checkpoint already cleared the gate
    ({"resolved_knowledge_coordinates": ["mylib"]}, False),       # the run acknowledged exactly this library
    ({"resolved_knowledge_coordinates": ["otherlib"]}, True),     # a coordinate-scoped acknowledgement of another one
])
def test_the_knowledge_gap_stops_the_unit_unless_confirmed(tmp_path, fields, stops):
    guard = _Guard(lambda text: [GAP])
    steps = []
    ctx = _ctx(tmp_path, step_callback=lambda name, text: steps.append(name), milestone_group_id="g",
               milestone_index=1, milestone_total=2, enforce_run_id="e", **fields)
    with _log_run() as log_run:
        result = asyncio.run(check_knowledge_guard(ctx, _deps(_engine(tmp_path), KnowledgeGuard=guard)))
    assert guard.kwargs["offline"] is True and guard.checked == [ctx.goal]
    if not stops:
        assert result is None and steps == [] and not log_run.called
        assert ctx.guard is guard and ctx.all_gap_report.gaps == [GAP]   # for the analysis stage and Stage 2A
        return
    assert ctx.guard is None   # a stopped unit hands nothing on: its result is the run's result
    report = GapReport()
    report.gaps = [GAP]
    assert result == {"status": "knowledge_gap", "failure_category": "knowledge_gap", "gap_report": report.to_dict(), "goal": ctx.goal,     # W3-6: the category its trace row records
                      "workspace_path": ctx.workspace_path, "run_id": "trace-1"}
    assert steps == ["knowledge_gap"]
    row = log_run.call_args.kwargs
    assert (row["run_id"], row["status"], row["failure_category"], row["attempts"], row["files_modified"]) == (
        "trace-1", "knowledge_gap", "knowledge_gap", 0, [])
    assert (row["milestone_group_id"], row["milestone_index"], row["milestone_total"], row["enforce_run_id"]) == (
        "g", 1, 2, "e")
    assert row["run_events"] == [{"kind": "scripted"}]


def test_no_gap_lets_the_unit_go_on(tmp_path):
    ctx = _ctx(tmp_path)
    assert asyncio.run(check_knowledge_guard(ctx, _deps(_engine(tmp_path), KnowledgeGuard=_Guard(lambda t: [])))) is None
    assert ctx.all_gap_report.gaps == [] and ctx.knowledge_config is not None


# ----------------------------------------------------------------------------- stage 1: analysis


def _skill(tmp_path, name="calc-conventions", rule="Name every arithmetic helper with a verb."):
    skill = tmp_path / "skills" / name
    skill.mkdir(parents=True)
    (skill / "skill.yaml").write_text(f"name: {name}\ndescription: d\nverified: true\ntags:\n  - calc\n")
    (skill / "rules.txt").write_text(rule + "\n")
    return rule


class _VectorStore:
    def __init__(self, path):
        self.path, self.closed = path, False

    def close(self):
        self.closed = True


def test_the_convention_prompt_is_skills_then_graph_context_then_the_fenced_reference(tmp_path):
    engine = _engine(tmp_path)
    rule = _skill(tmp_path)
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "vector_index.db").write_bytes(b"")
    graph = "\n\n=== graph context ===\ncalc.py: add\n"
    retrieval = GraphRetrievalResult(matched=True, matched_files=["calc.py"], related_files=["util.py"],
                                     graph_rag_context=graph, expansion_seed_reason="seeded",
                                     expansion_seed_files=["calc.py"])
    calls = []

    async def retrieve(goal, workspace_path, **kwargs):
        calls.append((goal, kwargs["embedding_model"]))
        return retrieval
    stores = []
    deps = _deps(engine, retrieve_graph_context=retrieve, configured_client=lambda cfg: "client",
                 LocalVectorStore=lambda path: stores.append(_VectorStore(path)) or stores[-1])
    report = GapReport()
    report.gaps = [GAP]
    ctx = _ctx(tmp_path, all_gap_report=report, reference_context="learned fact", supplementary_context="SUPPLEMENT")
    asyncio.run(analyze_work_unit(ctx, deps))
    assert calls == [(ctx.goal, engine.kernel.config.embedding.model)] and stores[0].closed
    assert ctx.active_skills == ["calc-conventions"]
    assert ctx.skills_prompt.startswith("SUPPLEMENT")
    assert f"- {rule}\n" in ctx.skills_prompt and "KNOWLEDGE GUARD SAFETY CONSTRAINTS" in ctx.skills_prompt
    assert graph not in ctx.skills_prompt and "learned fact" not in ctx.skills_prompt
    assert ctx.learned_rag_context == fence_untrusted_reference("learned fact")
    assert ctx.convention_prompt == ctx.skills_prompt + graph + ctx.learned_rag_context
    assert (ctx.matched_files, ctx.related_files, ctx.graph_retrieval_result) == (["calc.py"], ["util.py"], retrieval)
    assert [e.details["reason_code"] for e in _events(ctx, "retrieval.expansion_seeds")] == ["seeded"]
    assert '"root_path"' not in ctx.repo_context and ctx.repo_model is not None and ctx.repo_slug == "ws"


def test_a_failed_graph_retrieval_degrades_to_the_skills_prompt(tmp_path):
    engine = _engine(tmp_path)
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "vector_index.db").write_bytes(b"")

    async def unavailable(*_args, **_kwargs):
        raise RuntimeError("index unreadable")
    ctx = _ctx(tmp_path, all_gap_report=GapReport())
    asyncio.run(analyze_work_unit(ctx, _deps(engine, retrieve_graph_context=unavailable,
                                                    configured_client=lambda cfg: None, LocalVectorStore=_VectorStore)))
    assert ctx.graph_rag_context == "" and ctx.graph_retrieval_result is None
    assert ctx.convention_prompt == ctx.skills_prompt + fence_untrusted_reference("")


def test_the_run_deadline_is_not_a_retrieval_failure(tmp_path):
    engine = _engine(tmp_path)
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "vector_index.db").write_bytes(b"")
    deadline = engine._work_unit_deps().InferenceDeadlineError

    async def expired(*_args, **_kwargs):
        raise deadline("DEADLINE_EXHAUSTED", {"deadline_source": "run"})
    with pytest.raises(deadline):
        asyncio.run(analyze_work_unit(_ctx(tmp_path, all_gap_report=GapReport()), _deps(
            engine, retrieve_graph_context=expired, configured_client=lambda cfg: None, LocalVectorStore=_VectorStore)))


def test_the_active_skill_snapshot_is_recorded_as_evidence(tmp_path):
    engine = _engine(tmp_path)
    rule = _skill(tmp_path)
    ctx = _ctx(tmp_path, all_gap_report=GapReport())
    asyncio.run(analyze_work_unit(ctx, _deps(engine)))
    record_active_skills(ctx, _deps(engine))
    assert ctx.active_skill_rules_snapshot == {"calc-conventions": [rule]}
    [record] = [r for r in ctx.state.evidence_records if r.kind == "active_skills"]
    assert record.payload == {"skills": ctx.active_skill_manifest} and ctx.active_skill_manifest


# ----------------------------------------------------------------------------- stage 2: planning


class _Planner:
    system_prompt = "You are the Kriya Planner Agent (scripted)"

    def __init__(self, answers):
        self.answers, self.prompts = list(answers), []

    async def run(self, prompt, stream_callback=None, candidate_prompt=None):
        self.prompts.append(prompt)
        return self.answers.pop(0)


class _Prompts:
    """CandidatePrompts(config, agent, role, prompt_for): the first candidate's prompt, without a model fit."""

    def __init__(self, _config, _agent, role, _prompt_for):
        self.role = role

    def first(self):
        return f"<{self.role} request>"


def _planning_deps(engine, planner, **overrides):
    return _deps(engine, planner=planner, CandidatePrompts=_Prompts, planner_response_model=lambda p: "scripted-model",
                 record_planner_outcome=lambda *a, **k: None, **overrides)


def _schema_invalid_plan():
    structured = {"global_invariants": [{"id": "gi1", "statement": "x"}], "acceptance_criteria": [
        {"id": "ac1", "description": "tests pass", "method": "judgment"}], "extension_points": [],
        "refactor_baseline": None, "subtasks": [{
            "id": "s1", "description": "verify", "execution_method": "tool", "execution_role": "verification",
            "depends_on": [], "planned_files": [], "provides": [], "requires": [], "relevant_global_invariant_ids": ["gi1"],
            "acceptance_criteria_ids": ["ac1"], "verification": [{"type": "tool", "description": "t", "tool_name": "test"}]}]}
    return "Plan\n```json\n" + json.dumps(structured) + "\n```"


def test_an_empty_plan_stops_the_unit_before_the_architect(tmp_path):
    engine, saved, steps = _engine(tmp_path), [], []
    ctx = _ctx(tmp_path, save_stage_checkpoint=lambda stage, **extra: saved.append((stage, extra)),
               step_callback=lambda name, text: steps.append(name), record_requirement_lineage=lambda *a: None)
    with _log_run() as log_run:
        result = asyncio.run(plan_work_unit(ctx, _planning_deps(engine, _Planner([""]))))
    assert result == {"status": "planner_output_incomplete", "failure_category": "planner_output_incomplete", "reason": result["reason"], "plan": "", "goal": ctx.goal,
                      "workspace_path": ctx.workspace_path, "run_id": "trace-1"}      # W3-6: + failure_category
    assert result["reason"].startswith("plan is empty")
    assert saved == [("plan", {"plan": ""})] and steps == ["planner_output_incomplete"]
    assert ctx.state.planner_calls == 1
    assert log_run.call_args.kwargs["failure_category"] == "planner_output_incomplete"


def test_the_bounded_plan_repair_uses_the_injected_bound(tmp_path):
    engine = _engine(tmp_path)
    planner = _Planner([_schema_invalid_plan()] * 3)
    ctx = _ctx(tmp_path, save_stage_checkpoint=lambda stage, **extra: None, record_requirement_lineage=lambda *a: None)
    with _log_run():
        result = asyncio.run(plan_work_unit(ctx, _planning_deps(engine, planner, STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS=1)))
    assert result["status"] == "planner_output_schema_invalid" and len(planner.prompts) == 2
    assert [len(_events(ctx, kind)) for kind in (
        "structured_plan_validation", "structured_plan_repair_requested", "structured_plan_repair_result")] == [1, 1, 1]


def test_a_predetermined_plan_skips_the_planner(tmp_path):
    engine, steps, lineage = _engine(tmp_path), [], []
    planner = _Planner([])
    # S18: the Planner is shown only plannable tools; a shipped READ_ONLY tool is the vehicle (an `object()` is not one)
    from _plugin_test_support import load_core_tools_module

    engine.kernel.registry.register("tool", "search", load_core_tools_module().SearchTool())
    ctx = _ctx(tmp_path, predetermined_plan="Step 1: add mul", step_callback=lambda name, text: steps.append(name),
               record_requirement_lineage=lambda stage, text: lineage.append((stage, text)),
               save_stage_checkpoint=lambda *a, **k: pytest.fail("a predetermined plan is never checkpointed"))
    assert asyncio.run(plan_work_unit(ctx, _planning_deps(engine, planner))) is None
    assert planner.prompts == [] and ctx.plan == "Step 1: add mul" and ctx.state.planner_calls == 0
    assert steps == ["Plan"] and lineage == [("plan", "Step 1: add mul")]
    assert 'Subtask.tool_name must be exactly one of these, never invented): search.' in ctx.plan_prompt
    assert ctx.owner_candidates == [] and ctx.ownership_findings == []


# ----------------------------------------------------------------------------- stage 3: architecture


class _Architect:
    system_prompt = "You are the Kriya Architect Agent (scripted)"

    def __init__(self, design, files):
        self.design, self.files, self.calls = design, files, 0

    async def run_with_file_list(self, prompt, stream_callback=None, candidate_prompt=None):
        self.calls += 1
        return self.design, self.files


def _design_ctx(tmp_path, **fields):
    report = GapReport()
    values = dict(plan="Step 1: add mul", all_gap_report=report, guard=_Guard(lambda text: []),
                  knowledge_config=_engine(tmp_path).kernel.config.knowledge,
                  save_stage_checkpoint=lambda stage, **extra: None, record_requirement_lineage=lambda *a: None)
    values.update(fields)
    return _ctx(tmp_path, **values)


def test_an_all_escaping_architect_file_list_stops_the_unit(tmp_path):
    engine, saved = _engine(tmp_path), []
    design = json.dumps({"files": ["../outside/evil.py"]})
    ctx = _design_ctx(tmp_path, save_stage_checkpoint=lambda stage, **extra: saved.append(stage))
    with _log_run() as log_run:
        result = asyncio.run(design_work_unit(ctx, _deps(engine, architect=_Architect(design, None),
                                                         CandidatePrompts=_Prompts)))
    assert result == {"status": "architect_file_list_rejected", "failure_category": "architect_file_list_rejected", "reason": result["reason"], "plan": ctx.plan,   # W3-6: + failure_category
                      "design": design, "goal": ctx.goal, "workspace_path": ctx.workspace_path, "run_id": "trace-1",
                      "rejected_file_list_entries": ["../outside/evil.py"]}
    assert saved == ["design"] and ctx.state.architect_calls == 1
    assert [e.details["rejected"] for e in _events(ctx, "plan.file_list_entries_rejected")] == [["../outside/evil.py"]]
    assert log_run.call_args.kwargs["status"] == "architect_file_list_rejected"


def test_a_brownfield_design_resolves_owners_through_the_patched_facade_name(tmp_path):
    """A predetermined design skips the Architect; on a TASK route the response-construction owners are resolved
    through ``kriya.workflow.workflow.include_response_construction_owners``, looked up when the engine builds the
    stage's deps (a patch of the facade name reaches the moved stage)."""
    from kriya.workflow import workflow as workflow_module

    route = type("Route", (), {"kind": ChangeKind.TASK})()
    architect = _Architect("unused", ["unused.py"])
    ctx = _design_ctx(tmp_path, predetermined_design="design", predetermined_architect_files=["calc.py"],
                      engineering_route=route)
    real = workflow_module.include_response_construction_owners
    with patch("kriya.workflow.workflow.include_response_construction_owners", wraps=real) as owners:
        deps = _deps(_engine(tmp_path), architect=architect)
        assert asyncio.run(design_work_unit(ctx, deps)) is None
    assert owners.call_args.args == (["calc.py"], ctx.goal, ctx.workspace_path)
    assert architect.calls == 0 and ctx.design == "design" and ctx.architect_files == ["calc.py"]


def _assert_a_stage_2a_stop(stop, status, reason):
    """1-E: Stage 2A ends the unit with the apply gate's two outcomes as a RESULT (no exception): a person's decline is `human_rejected`, nobody deciding is `approval_required`.
    Nothing is applied, no gate passed, and no Developer work started."""
    assert stop is not None, "the unit must stop"
    assert stop["status"] == status and reason in stop["reason"] and stop["review"] == stop["reason"], stop
    assert stop["quality_gates_passed"] is False and stop["files"] == [], stop
    assert {"plan", "design", "goal", "workspace_path", "run_id"} <= set(stop), stop


@pytest.mark.parametrize("answer,status", [(False, "human_rejected"), ("unavailable", "approval_required")], ids=["declined", "unavailable"])
def test_a_stage_2a_stop_is_recorded_as_the_short_trace_row_and_reported_to_the_step_callback(tmp_path, answer, status):
    """1-E: the same terminal bookkeeping as every other pre-Developer stop (architect_file_list_rejected): one short trace row whose status AND failure category are the
    stop's status, and the step callback told once, last."""
    from kriya.policy.approval import APPROVAL_LAUNCH_CANNOT_ASK, ApprovalOutcome
    steps = []
    given = ApprovalOutcome.unavailable_because(APPROVAL_LAUNCH_CANNOT_ASK) if answer == "unavailable" else answer
    guard = _Guard(lambda text: [GAP] if "mylib" in text else [])
    ctx = _design_ctx(tmp_path, predetermined_design="Use mylib==1.2.3.", predetermined_architect_files=["calc.py"], guard=guard, approval_callback=lambda diffs, reason: given,
                      step_callback=lambda name, detail: steps.append((name, detail)))
    with _log_run() as log_run:
        stop = asyncio.run(design_work_unit(ctx, _deps(_engine(tmp_path))))
    assert stop["status"] == status
    assert log_run.call_count == 1 and log_run.call_args.kwargs["status"] == status and log_run.call_args.kwargs["failure_category"] == status
    assert log_run.call_args.kwargs["attempts"] == 0 and log_run.call_args.kwargs["files_modified"] == []
    assert steps[-1] == (status, stop["reason"]) and [name for name, _detail in steps].count(status) == 1      # told once, and it is the last thing the stage reported


def test_a_rejected_stage_2a_dependency_aborts_the_run(tmp_path):
    guard = _Guard(lambda text: [GAP] if "mylib" in text else [])
    ctx = _design_ctx(tmp_path, predetermined_design="Use mylib==1.2.3.", predetermined_architect_files=["calc.py"],
                      guard=guard, approval_callback=lambda diffs, reason: False)
    stop = asyncio.run(design_work_unit(ctx, _deps(_engine(tmp_path))))
    _assert_a_stage_2a_stop(stop, "human_rejected", "User rejected the post-cutoff dependency risk in Stage 2A")
    assert guard.checked == ["Use mylib==1.2.3."]


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync-callback", "async-callback"])
@pytest.mark.parametrize("decision", [False, True], ids=["declined", "approved"])
def test_the_stage_2a_dependency_approval_awaits_an_async_callback_like_every_other_approval_site(tmp_path, asynchronous, decision):
    """An async approval callback is part of the contract (workflow.py, stage_apply.py, spec_compliance.py and policy/approval.ask all
    await a coroutine). Un-awaited, the coroutine object is truthy: a declined async answer would read as approval."""
    answered = []

    async def asking(diffs, reason):
        answered.append((diffs, reason))
        return decision

    def sync(diffs, reason):
        answered.append((diffs, reason))
        return decision

    guard = _Guard(lambda text: [GAP] if "mylib" in text else [])
    ctx = _design_ctx(tmp_path, predetermined_design="Use mylib==1.2.3.", predetermined_architect_files=["calc.py"],
                      guard=guard, approval_callback=asking if asynchronous else sync)
    if decision:
        assert asyncio.run(design_work_unit(ctx, _deps(_engine(tmp_path)))) is None          # approved: the stage continues
    else:
        _assert_a_stage_2a_stop(asyncio.run(design_work_unit(ctx, _deps(_engine(tmp_path)))), "human_rejected", "User rejected the post-cutoff dependency risk in Stage 2A")
    assert len(answered) == 1 and answered[0][0] == [] and "Knowledge Guard detected" in answered[0][1]     # asked once, with the prompt


def _shaped_callback(kind, value, answered):
    """An approval callback answering with ``value`` as a sync value, a coroutine, an asyncio Task or a Future (built inside the running loop)."""
    async def coroutine():
        return value

    def callback(diffs, reason):
        answered.append((diffs, reason))
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


@pytest.mark.parametrize("kind", ["sync", "coroutine", "task", "future"])
@pytest.mark.parametrize("value,outcome", [
    (True, None), (False, ("human_rejected", "User rejected the post-cutoff dependency risk in Stage 2A")),
    ("unavailable", ("approval_required", "Approval of the post-cutoff dependency risk could not be obtained in Stage 2A (APPROVAL_LAUNCH_CANNOT_ASK)")),
    (None, ("approval_required", "(APPROVAL_RESULT_UNRECOGNIZED)"))], ids=["approved", "declined", "unavailable", "unrecognized"])
def test_the_stage_2a_dependency_approval_acts_on_the_resolved_decision_only(tmp_path, kind, value, outcome):
    """F4 + N1 at Stage 2A. A Task/Future is not a coroutine and is truthy: it used to be read as approval. An UNAVAILABLE answer (a launch that cannot ask) is not a rejection:
    the run still stops, and says that no decision could be obtained, with the closed reason code."""
    from kriya.policy.approval import APPROVAL_LAUNCH_CANNOT_ASK, ApprovalOutcome
    answered = []
    answer = ApprovalOutcome.unavailable_because(APPROVAL_LAUNCH_CANNOT_ASK) if value == "unavailable" else value
    guard = _Guard(lambda text: [GAP] if "mylib" in text else [])
    ctx = _design_ctx(tmp_path, predetermined_design="Use mylib==1.2.3.", predetermined_architect_files=["calc.py"],
                      guard=guard, approval_callback=_shaped_callback(kind, answer, answered))
    stop = asyncio.run(design_work_unit(ctx, _deps(_engine(tmp_path))))
    if outcome is None:
        assert stop is None
    else:
        _assert_a_stage_2a_stop(stop, *outcome)
    assert len(answered) == 1


def test_the_post_architect_recomputation_updates_the_control_context_and_the_run_state(tmp_path):
    """A LOW/LIGHT request whose design plans pom.xml escalates to HIGH/HEAVY before the Developer runs: the stage replaces
    ``ctx.control`` and records the new route AND process profile on the run state (the run metrics read both). Found by an
    orchestrator mutant after E3: dropping the ``state.process_profile`` write failed no test."""
    from kriya.workflow.control_context import WorkflowControlContext
    from kriya.workflow.process_profile import HEAVY_PROFILE, LIGHT_PROFILE
    from kriya.workflow.triage import EngineeringRoute, ImpactVector, RiskClass, determine_execution_weight

    route = EngineeringRoute(kind=ChangeKind.TASK, impact=ImpactVector(), initial_risk_class=RiskClass.LOW,
                             current_risk_class=RiskClass.LOW, max_observed_risk_class=RiskClass.LOW,
                             execution_weight=determine_execution_weight(ChangeKind.TASK, RiskClass.LOW))
    control = WorkflowControlContext.for_route(route)
    assert control.process_profile == LIGHT_PROFILE
    ctx = _design_ctx(tmp_path, predetermined_design="design", predetermined_architect_files=["calc.py", "pom.xml"],
                      control=control, engineering_route=route)
    assert asyncio.run(design_work_unit(ctx, _deps(_engine(tmp_path)))) is None
    assert ctx.control.engineering_route.max_observed_risk_class == RiskClass.HIGH
    assert ctx.state.engineering_route.max_observed_risk_class == RiskClass.HIGH
    assert ctx.state.process_profile == HEAVY_PROFILE
    assert ctx.control.process_profile == HEAVY_PROFILE


# ----------------------------------------------------------------------------- the runner


def _recording_stages(monkeypatch, order, stop_at=None):
    def stage(name, result=None):
        async def run(ctx, deps):
            order.append(name)
            return result if name == stop_at else None
        return run
    monkeypatch.setattr(work_unit_runner, "check_knowledge_guard", stage("knowledge_guard", {"status": "stopped at knowledge_guard"}))
    monkeypatch.setattr(work_unit_runner, "analyze_work_unit", stage("analysis", {"status": "stopped at analysis"}))
    monkeypatch.setattr(work_unit_runner, "plan_work_unit", stage("planning", {"status": "stopped at planning"}))
    monkeypatch.setattr(work_unit_runner, "design_work_unit", stage("architecture", {"status": "stopped at architecture"}))
    monkeypatch.setattr(work_unit_runner, "record_active_skills", lambda ctx, deps: order.append("active_skills"))


def test_the_runner_drives_the_stages_in_order_and_opens_checkpoints_before_planning(tmp_path, monkeypatch):
    order = []
    _recording_stages(monkeypatch, order)

    def saver(*_args, **_kwargs):
        return None

    def open_checkpoints():
        order.append("open_checkpoints")
        return saver
    ctx = _ctx(tmp_path)
    assert asyncio.run(work_unit_runner.run_design_stages(ctx, None, open_checkpoints=open_checkpoints)) is None
    assert order == ["knowledge_guard", "analysis", "open_checkpoints", "planning", "architecture", "active_skills"]
    assert ctx.save_stage_checkpoint is saver


@pytest.mark.parametrize("stop_at, ran", [
    ("knowledge_guard", ["knowledge_guard"]),
    ("planning", ["knowledge_guard", "analysis", "open_checkpoints", "planning"]),
    ("architecture", ["knowledge_guard", "analysis", "open_checkpoints", "planning", "architecture"]),
])
def test_a_stage_result_stops_the_unit_and_is_returned_unchanged(tmp_path, monkeypatch, stop_at, ran):
    order = []
    _recording_stages(monkeypatch, order, stop_at=stop_at)
    result = asyncio.run(work_unit_runner.run_design_stages(
        _ctx(tmp_path), None, open_checkpoints=lambda: order.append("open_checkpoints")))
    assert result == {"status": f"stopped at {stop_at}"} and order == ran


# ----------------------------------------------------------------------------- E4: review, approval/apply, lessons
#
# Stages 5-7 moved verbatim out of run_generation_workflow into kriya/workflow/stage_review.py and stage_apply.py (the
# Developer loop that calls them stays in the engine until E5), the close_requirements_* family into
# requirement_closers.py behind same-signature forwarders. The end-to-end preservation proof is the fourteen E4 kernel
# goldens written from 3e5751c before the move (and every earlier golden); these tests drive the moved stages alone
# with a StageContext fake and the engine-built StageDeps (WorkflowEngine._late_stage_deps on a real AppConfig).

CALC_BASE = "def add(a, b):\n    return a + b\n"
CALC_NEW = CALC_BASE + "\n\ndef mul(a, b):\n    return a * b\n"


def _late_deps(engine, **overrides):
    return dataclasses.replace(engine._late_stage_deps(), trace_events=lambda state: [{"kind": "scripted"}], **overrides)


def _reviewing_engine(tmp_path, *, mode="human-in-the-loop", risk_threshold_lines=100, review="REVIEW: looks right"):
    engine = _engine(tmp_path)
    engine.kernel.config.autonomy.mode = mode
    engine.kernel.config.autonomy.risk_threshold_lines = risk_threshold_lines
    engine.reviewer.run = AsyncMock(return_value=review)
    return engine


def _apply_ctx(tmp_path, *, in_place=False, written=("calc.py",), **fields):
    from kriya.workflow.obligations import ObligationLedger
    from kriya.workflow.run_lifecycle import RunTrace

    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "calc.py").write_text(CALC_BASE)
    worktree = workspace if in_place else tmp_path / "wt"
    worktree.mkdir(exist_ok=True)
    state = GenerationState()
    for name in written:
        (worktree / name).parent.mkdir(parents=True, exist_ok=True)
        (worktree / name).write_text(CALC_NEW)
        state.all_files_written.add(name)
    state.all_original_contents["calc.py"] = CALC_BASE
    values = dict(goal="Add mul to calc.py.", workspace_path=str(workspace), worktree_path=str(worktree), state=state,
                  trace_id="trace-1", start_time=0.0, run_id="run-1", plan="the plan", design="the design",
                  resolved_obligation_ledger=ObligationLedger(), run_trace=RunTrace("trace-1", "Add mul to calc.py.", 0.0))
    values.update(fields)
    return StageContext(**values)


def _decisions(state):
    return [event.details for event in state.run_events if event.kind == "approval.decision"]


def test_the_engine_injects_every_late_stage_collaborator(tmp_path):
    from kriya.workflow.stage_context import late_stage_fields

    deps = _engine(tmp_path)._late_stage_deps()
    assert not_injected(deps, late_stage_fields()) == []
    assert len(late_stage_fields()) == 26
    # and the three E3 collaborators the late stages use
    assert not_injected(deps, ("llm", "_log_phase_banner", "InferenceDeadlineError")) == []


def test_the_late_stages_given_lifecycle_only_deps_fail_closed(tmp_path):
    """E2's ``_stage_deps()`` carries none of the E4 collaborators: their defaults raise on use, never a silent None."""
    from kriya.workflow.stage_apply import approval_gate
    from kriya.workflow.stage_context import late_stage_fields
    from kriya.workflow.stage_review import final_review

    engine = _engine(tmp_path)
    assert sorted(not_injected(engine._stage_deps(), late_stage_fields())) == sorted(late_stage_fields())
    with pytest.raises(StageCollaboratorMissing):
        asyncio.run(approval_gate(_apply_ctx(tmp_path), engine._stage_deps(), attempt_ctx=_NO_HELD_GATES))
    with pytest.raises(StageCollaboratorMissing):
        asyncio.run(final_review(_apply_ctx(tmp_path), engine._stage_deps()))


class _AttemptCtx:
    deferred_executing_gates = None


_NO_HELD_GATES = _AttemptCtx()


@pytest.mark.parametrize("mode, threshold, written, triggers, reason_start", [
    ("human-in-the-loop", 100, ("calc.py",), ["human_in_the_loop"], "Human-in-the-loop review policy"),
    ("guardrails", 1, ("calc.py",), ["diff_size"], "Risk threshold exceeded ("),
    ("guardrails", 100, ("config/secrets.py",), ["sensitive_path"], "Sensitive path matched: config/secrets.py"),
])
def test_an_approval_trigger_asks_the_person_with_the_review_attached(tmp_path, mode, threshold, written, triggers,
                                                                        reason_start):
    from kriya.control.launch_approval import REVIEW_SECTION_HEADER
    from kriya.workflow.stage_apply import approval_gate

    engine = _reviewing_engine(tmp_path, mode=mode, risk_threshold_lines=threshold)
    asked = []
    ctx = _apply_ctx(tmp_path, written=written, approval_callback=lambda diffs, reason: asked.append((diffs, reason)) or True)
    stop, required = asyncio.run(approval_gate(ctx, _late_deps(engine), attempt_ctx=_NO_HELD_GATES))
    assert (stop, required) == (None, True)
    [(diffs, reason)] = asked
    assert [d["filepath"] for d in diffs] == list(written) and "+def mul(a, b):" in diffs[0]["content"]
    assert reason.startswith(reason_start)
    assert f"{REVIEW_SECTION_HEADER}\nREVIEW: looks right" in reason and ctx.state.pre_approval_review == "REVIEW: looks right"
    assert _decisions(ctx.state) == [{"outcome": "approved", "triggers": triggers}]
    assert engine.reviewer.run.await_count == 1


@pytest.mark.parametrize("reason, stop_type", [
    ("CONTRACT_CONSUMER_VERIFICATION_MISSING", "contract_registry"),
    ("CONTRACT_REGISTRY_TRANSITION_INCOMPLETE", "contract_registry"),
    ("CONTRACT_REGISTRY_STAGING_FAILED", "contract_registry"),
    ("CONTRACT_REGISTRY_TRANSITION_INVALID", "contract_registry"),
    ("WORKSPACE_REVISION_CONFLICT", "workspace_commit"),
    (None, "workspace_commit"),
])
def test_a_commit_that_did_not_commit_is_a_typed_deterministic_stop_by_its_reason(tmp_path, reason, stop_type):
    """The terminal apply routes a refused commit by reason code: a contract-registry reason is a ``contract_registry`` stop
    (PRD-029: no terminal-commit payload, the reason is the message), every other reason a ``workspace_commit`` stop (PRD-032:
    the commit payload and a ``workspace_commit.failed`` event). Found by an orchestrator mutant after E4: skipping the
    contract-registry branch survived every existing test."""
    from kriya.workflow.failure import QualityGateFailure
    from kriya.workflow.stage_apply import apply_terminal_candidate
    from kriya.workflow.terminal_commit import TerminalCommitOutcome

    refused = TerminalCommitOutcome(committed=False, workspace_state="UNCHANGED", commit_result="REFUSED", transaction_id="tx-1",
                                    reason_code=reason, error=ValueError("the registry could not be updated"))
    ctx = _apply_ctx(tmp_path)
    deps = _late_deps(_engine(tmp_path), commit_terminal_candidate=lambda *args, **kwargs: refused)
    with pytest.raises(QualityGateFailure) as stop:
        apply_terminal_candidate(ctx, deps, attempt_ctx=_NO_HELD_GATES, final_candidate_contents={"calc.py": CALC_NEW})
    assert stop.value.failure.type == stop_type
    state = ctx.state
    if stop_type == "contract_registry":
        assert stop.value.failure.diagnostics == {"reason_code": reason}
        assert state.environment_failure == f"{reason}: the registry could not be updated"
        assert state.terminal_commit_failure is None
        assert not [e for e in state.run_events if e.kind == "workspace_commit.failed"]
    else:
        # the payload keeps the outcome's own reason (None stays None); the message and the event carry the fallback
        assert state.terminal_commit_failure["reason_code"] == reason
        assert state.terminal_commit_failure["workspace_state"] == "UNCHANGED"
        assert (reason or "WORKSPACE_COMMIT_FAILED") in state.environment_failure
        assert [e.details["reason_code"] for e in state.run_events if e.kind == "workspace_commit.failed"] == [
            reason or "WORKSPACE_COMMIT_FAILED"]


def test_no_trigger_and_no_source_mutation_ask_nobody(tmp_path):
    """Autonomous with a small diff, and human-in-the-loop with an empty change set (a verification-only unit): no
    approval, no pre-approval review, no decision record."""
    from kriya.workflow.stage_apply import approval_gate

    for mode, written in (("guardrails", ("calc.py",)), ("human-in-the-loop", ())):
        engine = _reviewing_engine(tmp_path / mode, mode=mode)
        callback = MagicMock(return_value=True)
        ctx = _apply_ctx(tmp_path / mode, written=written, approval_callback=callback)
        assert asyncio.run(approval_gate(ctx, _late_deps(engine), attempt_ctx=_NO_HELD_GATES)) == (None, False)
        callback.assert_not_called()
        assert engine.reviewer.run.await_count == 0 and _decisions(ctx.state) == []


def test_human_in_the_loop_without_a_callback_aborts_without_applying(tmp_path, monkeypatch):
    """Approval required and nobody can be asked: the typed abort (approval_required), the sandbox reset, the checkpoint
    deleted, the full trace row written; the real workspace keeps its bytes."""
    from kriya.workflow import stage_apply

    removed, deleted = [], []
    monkeypatch.setattr(stage_apply, "remove_git_worktree", lambda workspace, worktree: removed.append((workspace, worktree)))
    engine = _reviewing_engine(tmp_path)
    ctx = _apply_ctx(tmp_path)
    deps = _late_deps(engine, delete_checkpoint=lambda workspace, run_id: deleted.append((workspace, run_id)))
    with patch("kriya.control.trace.TraceLogger.log_run") as log_run:
        stop, required = asyncio.run(stage_apply.approval_gate(ctx, deps, attempt_ctx=_NO_HELD_GATES))
    assert required is True
    assert sorted(stop) == ["accepted_risk", "accepted_risks", "design", "failure_category", "files", "plan", "quality_gates_passed", "review",     # W3-6: + failure_category
                            "review_included_in_approval", "run_id", "static_analysis"]
    assert stop["failure_category"] == "approval_required"
    assert stop["review"].startswith("Not applied: human approval was required (Human-in-the-loop review policy")
    assert (stop["quality_gates_passed"], stop["files"], stop["run_id"], stop["review_included_in_approval"]) == (
        False, [], "run-1", False)
    assert removed == [(ctx.workspace_path, ctx.worktree_path)] and deleted == [(ctx.workspace_path, "run-1")]
    row = log_run.call_args.kwargs
    assert (row["status"], row["failure_category"], row["files_modified"], row["failure_report"]) == (
        "approval_required", "approval_required", ["calc.py"], [])
    assert list(row) == ["run_id", "goal", "duration_sec", "attempts", "status", "files_modified", "retrieved_chunks",
                         "active_skills", "prompt_rendered", "gate_outcomes", "model_hops", "failure_category",
                         "failure_report", "milestone_group_id", "milestone_index", "milestone_total", "enforce_run_id",
                         "run_events", "evidence_records", "generation_metrics"]
    assert _decisions(ctx.state) == [{"outcome": "unavailable", "triggers": ["human_in_the_loop"]}]
    assert engine.reviewer.run.await_count == 0      # nobody to show it to: no pre-approval review
    assert (tmp_path / "ws" / "calc.py").read_text() == CALC_BASE


def test_a_rejection_without_a_sandbox_restores_the_exact_original_bytes(tmp_path, monkeypatch):
    """The in-place branch of the abort (no worktree: unreachable from run_generation_workflow since D0 refuses an in-place
    sandbox, kept for the other callers): an existing file gets its exact original bytes back, a created one is removed."""
    from kriya.workflow import stage_apply

    monkeypatch.setattr(stage_apply, "remove_git_worktree", lambda *_a: pytest.fail("no sandbox to reset"))
    engine = _reviewing_engine(tmp_path)
    ctx = _apply_ctx(tmp_path, in_place=True, written=("calc.py", "made.py"),
                     approval_callback=lambda diffs, reason: False)
    # what the attempt captured before writing: calc.py's exact bytes, made.py did not exist
    ctx.state.all_original_contents["made.py"] = ""
    ctx.state.all_original_raw.update({"calc.py": CALC_BASE.encode(), "made.py": None})
    with patch("kriya.control.trace.TraceLogger.log_run") as log_run:
        stop, required = asyncio.run(stage_apply.approval_gate(ctx, _late_deps(engine), attempt_ctx=_NO_HELD_GATES))
    assert required is True and stop["review"] == "Rejected by user during approval gate review."
    assert stop["review_included_in_approval"] is True     # the person saw the review before saying no
    assert log_run.call_args.kwargs["status"] == "human_rejected"
    assert (tmp_path / "ws" / "calc.py").read_text() == CALC_BASE and not (tmp_path / "ws" / "made.py").exists()
    assert _decisions(ctx.state) == [{"outcome": "rejected", "triggers": ["human_in_the_loop"]}]


def test_the_final_review_reuses_the_pre_approval_report(tmp_path):
    from kriya.workflow.stage_review import final_review

    engine = _reviewing_engine(tmp_path)
    steps = []
    ctx = _apply_ctx(tmp_path, step_callback=lambda name, text: steps.append((name, text)))
    ctx.state.pre_approval_review = "the report the person saw"
    assert asyncio.run(final_review(ctx, _late_deps(engine))) == "the report the person saw"
    assert engine.reviewer.run.await_count == 0 and steps == [("Review", "the report the person saw")]


@pytest.mark.parametrize("error, reason_code", [
    ("deadline", "GENERATION_DEADLINE_EXHAUSTED"),
    (RuntimeError("scripted bug"), "FINAL_REVIEW_INTERNAL_ERROR"),
    (ConnectionError("backend down"), "FINAL_REVIEW_BACKEND_ERROR"),
])
def test_a_refused_or_failed_final_review_is_the_typed_refusal(tmp_path, error, reason_code):
    """Both refusal constructions (the typed deadline/budget refusal and any other review failure) set the refusal and
    record review.refused; the review text says it was not performed; nothing raises."""
    from kriya.core.llm import InferenceDeadlineError
    from kriya.workflow.stage_review import final_review

    if error == "deadline":
        error = InferenceDeadlineError("GENERATION_DEADLINE_EXHAUSTED",
                                       {"deadline_source": "run", "remaining_budget_at_dispatch_ms": 0})
    engine = _reviewing_engine(tmp_path)
    engine.reviewer.run = AsyncMock(side_effect=error)
    ctx = _apply_ctx(tmp_path)
    review = asyncio.run(final_review(ctx, _late_deps(engine)))
    assert review.startswith("Final review not performed: ")
    refusal = ctx.state.final_review_refusal
    assert refusal["reason_code"] == reason_code and (refusal["batch"], refusal["batches"]) == (1, 1)
    assert ("exception_type" in refusal) is (reason_code != "GENERATION_DEADLINE_EXHAUSTED")
    [event] = [e for e in ctx.state.run_events if e.kind == "review.refused"]
    assert event.details == refusal
    assert ctx.state.reviewer_calls == 0


def test_the_rejected_candidate_review_has_its_own_prompt_and_is_never_streamed(tmp_path):
    from kriya.workflow.stage_review import final_review

    engine = _reviewing_engine(tmp_path, review="Diagnosis: add() raises.\nHow to Run: python -m calc\n")
    streamed = []
    ctx = _apply_ctx(tmp_path, stream_callback=lambda name, token: streamed.append(name))
    ctx.state.final_attempt_contents["calc.py"] = CALC_NEW
    ctx.state.error_context = "test_add failed"
    review = asyncio.run(final_review(ctx, _late_deps(engine)))
    kwargs = engine.reviewer.run.await_args.kwargs
    assert kwargs["stream_callback"] is None
    assert kwargs["system_prompt_override"] == engine.reviewer.rejected_candidate_system_prompt("test_add failed")
    assert review == engine.reviewer.extract_rejected_candidate_diagnostic("Diagnosis: add() raises.\nHow to Run: python -m calc\n")
    assert ctx.state.reviewer_calls == 1 and streamed == []


@pytest.mark.parametrize("retries, candidates, override, self_corrected, extract", [
    (0, 0, None, False, False), (1, 1, None, False, False), (2, 0, None, False, True), (0, 2, None, False, True),
    (0, 0, "fallback-model", False, True), (0, 0, None, True, True),
])
def test_the_hard_won_lesson_trigger(tmp_path, retries, candidates, override, self_corrected, extract):
    from kriya.workflow.stage_apply import prepare_lessons

    ctx = _apply_ctx(tmp_path)
    ctx.state.budgets.retry_count, ctx.state.budgets.best_of_n_candidates_tried = retries, candidates
    ctx.state.last_model_override = override
    corrected = {"type": "compile", "self_corrected": True} if self_corrected else None
    if corrected:
        ctx.state.gate_outcomes.append(corrected)
    assert prepare_lessons(ctx, _late_deps(_engine(tmp_path))) == (corrected, extract)


def test_auto_accrual_scaffolds_only_a_suggested_dependency_the_final_content_uses(tmp_path):
    from kriya.workflow.stage_apply import prepare_lessons

    scaffolded = []

    class Guard:
        def generate_skill_template(self, coordinate, version):
            scaffolded.append((coordinate, version))
    ctx = _apply_ctx(tmp_path, guard=Guard())
    (tmp_path / "wt" / "calc.py").write_text(CALC_NEW + "# uses mylib-core\n")
    ctx.state.budgets.retry_count = 1
    ctx.state.gate_outcomes.append({"output": "=== KRIYA PLATFORM DEPENDENCY SUGGESTIONS ===\n"
                                              "<groupId>org.example</groupId><artifactId>mylib-core</artifactId><version>1.4.2</version>\n"
                                              "<groupId>org.x</groupId><artifactId>never-used</artifactId><version>0.1</version>"})
    prepare_lessons(ctx, _late_deps(_engine(tmp_path)))
    assert scaffolded == [("org.example:mylib-core", "1.4.2")]
    ctx.state.budgets.retry_count = 0     # a first-attempt success accrues nothing
    prepare_lessons(ctx, _late_deps(_engine(tmp_path)))
    assert scaffolded == [("org.example:mylib-core", "1.4.2")]


def test_the_lesson_extraction_stages_the_facts_and_never_fails_the_run(tmp_path):
    from kriya.workflow.stage_apply import extract_lessons

    staged = []

    class Staging:
        @staticmethod
        def stage_facts(folder, facts):
            staged.append((folder, facts))
            return list(facts)

    class Channel:
        def __init__(self, llm):
            self.llm = llm

        async def extract(self, context):
            return ["a fact about " + ",".join(sorted(context.file_contents))]

    from kriya.knowledge.channels.live_failure import LiveFailureContext

    engine = _engine(tmp_path)
    ctx = _apply_ctx(tmp_path, repo_slug="calc-repo")
    (tmp_path / "ws" / "calc.py").write_text(CALC_NEW)   # applied
    deps = _late_deps(engine, load_lesson_extraction=lambda: (Staging, Channel, LiveFailureContext))
    asyncio.run(extract_lessons(ctx, deps, should_extract_hard_won_knowledge=True, self_corrected_outcome=None))
    assert staged == [(os.path.join(engine.kernel.config.paths.skills, "auto-calc-repo"), ["a fact about calc.py"])]
    asyncio.run(extract_lessons(ctx, deps, should_extract_hard_won_knowledge=False, self_corrected_outcome=None))
    assert len(staged) == 1

    def broken():
        raise ImportError("knowledge staging unavailable")
    with patch("kriya.workflow.workflow.logger") as facade_logger:
        asyncio.run(extract_lessons(ctx, _late_deps(engine, load_lesson_extraction=broken),
                                    should_extract_hard_won_knowledge=True, self_corrected_outcome=None))
    facade_logger.warning.assert_called_once_with("Failed to extract lesson or update skills: knowledge staging unavailable")


def test_the_moved_review_and_closers_log_through_the_facades_patched_logger(tmp_path):
    """The ``kriya.workflow.workflow.logger`` guard for E4: the late stages get the facade's logger from
    ``_late_stage_deps`` and the closers from ``_closer_deps``, both at call time."""
    from kriya.workflow import workflow as workflow_module
    from kriya.workflow.stage_review import final_review

    engine = _reviewing_engine(tmp_path)
    engine.reviewer.run = AsyncMock(side_effect=RuntimeError("boom"))
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    with patch("kriya.workflow.workflow.logger") as facade_logger:
        asyncio.run(final_review(_apply_ctx(tmp_path), _late_deps(engine)))
        assert workflow_module._reference_test_files(str(plain), str(plain)) is None
    assert facade_logger.error.call_args_list[-1].args[0] == "Final review not performed: RuntimeError: boom"
    assert facade_logger.info.call_args_list[-1].args[0].startswith("Named-test oracle base revision unavailable: ")


FORWARDED_CLOSERS = ("immutable_test_files", "_reference_test_files", "close_requirements_with_acceptance_tests",
                     "close_requirements_with_derived_examples", "close_requirements_by_file_immutability",
                     "close_requirements_by_test_immutability", "close_requirements_with_authority_bundle_evidence",
                     "close_requirements_by_api_preservation", "close_requirements_by_test_addition",
                     "close_requirements_by_suite_preservation", "close_requirements_with_named_tests",
                     "_oracle_base_revision", "close_requirements_by_mutation_scope")


@pytest.mark.parametrize("name", FORWARDED_CLOSERS)
def test_each_closer_forwarder_keeps_the_moved_bodys_signature(name):
    """A forwarder that drifts from its body would turn a caller's TypeError into a different one, or drop a keyword."""
    import inspect

    from kriya.workflow import requirement_closers
    from kriya.workflow import workflow as workflow_module

    facade = list(inspect.signature(getattr(workflow_module, name)).parameters.values())
    body = list(inspect.signature(getattr(requirement_closers, name)).parameters.values())
    assert body[0].name == "deps"
    assert [(p.name, p.kind, p.default) for p in facade] == [(p.name, p.kind, p.default) for p in body[1:]]
    assert getattr(workflow_module, name).__module__ == "kriya.workflow.workflow"   # the facade keeps the definition
