"""The design stages of a work unit (full modularization W4, E3): stages 0-3 moved verbatim out of
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
from unittest.mock import patch

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
    assert result == {"status": "knowledge_gap", "gap_report": report.to_dict(), "goal": ctx.goal,
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
    assert result == {"status": "planner_output_incomplete", "reason": result["reason"], "plan": "", "goal": ctx.goal,
                      "workspace_path": ctx.workspace_path, "run_id": "trace-1"}
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
    engine.kernel.registry.register("tool", "compile", object())
    ctx = _ctx(tmp_path, predetermined_plan="Step 1: add mul", step_callback=lambda name, text: steps.append(name),
               record_requirement_lineage=lambda stage, text: lineage.append((stage, text)),
               save_stage_checkpoint=lambda *a, **k: pytest.fail("a predetermined plan is never checkpointed"))
    assert asyncio.run(plan_work_unit(ctx, _planning_deps(engine, planner))) is None
    assert planner.prompts == [] and ctx.plan == "Step 1: add mul" and ctx.state.planner_calls == 0
    assert steps == ["Plan"] and lineage == [("plan", "Step 1: add mul")]
    assert 'Subtask.tool_name must be exactly one of these, never invented): compile.' in ctx.plan_prompt
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
    assert result == {"status": "architect_file_list_rejected", "reason": result["reason"], "plan": ctx.plan,
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


def test_a_rejected_stage_2a_dependency_aborts_the_run(tmp_path):
    guard = _Guard(lambda text: [GAP] if "mylib" in text else [])
    ctx = _design_ctx(tmp_path, predetermined_design="Use mylib==1.2.3.", predetermined_architect_files=["calc.py"],
                      guard=guard, approval_callback=lambda diffs, reason: False)
    with pytest.raises(ValueError, match="User rejected post-cutoff dependency risk in Stage 2A"):
        asyncio.run(design_work_unit(ctx, _deps(_engine(tmp_path))))
    assert guard.checked == ["Use mylib==1.2.3."]


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
