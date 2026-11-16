"""S4 closure (C-04 item 3, INT-06, BYP-A4-02): three authorities that used to be read from the wrong place.

* MCP workspace authority is the configuration's BOUND PROJECT ROOT (S3 ``bind_project_identity``), never the process working directory:
  the capability scope, the containment mapping and the durable invocation approval all act for the bound project, a process standing in
  another project cannot redirect them, and a configuration (or a client) that carries no project is refused, typed, before anything starts.
* A resumed unit's knowledge gate is the CURRENT knowledge truth: a checkpoint's existence opens nothing.
* A unit selects its skills over the USER's goal, the text the Planner's own selection reads: Planner text neither adds nor removes a skill.
"""
import asyncio
import contextlib
import dataclasses
import os
import sys

import pytest
from _mcp_containment_standin import NoPrefixBackend, autonomy
from _strict_doubles import for_project
from test_s3_project_state_namespace import A_FILES, _config, _project, _real_engine
from test_s4_knowledge_truth import GOAL as WIDGET_GOAL
from test_s4_knowledge_truth import WIDGET
from test_s4_knowledge_truth import _skill as _widget_skill

from kriya.config.config import AppConfig
from kriya.config.resolution import PROJECT_IDENTITY_MISSING, ProjectStateError, bound_project_root
from kriya.context.workspace_identity import workspace_identity
from kriya.core.kernel import Kernel
from kriya.mcp import mcp as mcp_module
from kriya.mcp.capability import MCPPathScope, compute_mcp_capability_profile_digest, resolve_mcp_capability_profile
from kriya.mcp.invocation_approval import (
    add_approval,
    default_local_approval_path,
    empty_artifact,
    save_approval_artifact,
)
from kriya.mcp.lifecycle import MCPLifecycleError
from kriya.mcp.mcp import MCP_WORKSPACE_UNBOUND, MCPClient, MCPManager
from kriya.policy.model import MCPCapabilityProfileIdentity, MCPToolIdentity
from kriya.tools.containment import ContainmentSetupError

MOCK_SERVER = os.path.join(os.path.dirname(__file__), "mock_mcp_server.py")


@contextlib.contextmanager
def _standing_in(path):
    previous = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


@pytest.fixture
def places(tmp_path, monkeypatch):
    """The project the configuration is bound to, and a FOREIGN directory the process stands in."""
    monkeypatch.setenv("KRIYA_MCP_APPROVAL_HOME", str(tmp_path / "_mcp_approval_home"))
    project, foreign = tmp_path / "project", tmp_path / "foreign"
    (project / "notes").mkdir(parents=True)
    foreign.mkdir()
    return os.path.realpath(project), os.path.realpath(foreign)


def _manager(project=None) -> MCPManager:
    cfg = AppConfig(autonomy=autonomy())
    if project is not None:
        for_project(cfg, project)
    return MCPManager(Kernel(config=cfg))


def _spied(monkeypatch, name):
    seen, real = [], getattr(mcp_module, name)

    def spy(*args, **kwargs):
        seen.append(kwargs["workspace_root"])
        return real(*args, **kwargs)
    monkeypatch.setattr(mcp_module, name, spy)
    return seen


# ------------------------------------------------------------------ MCP: the bound project root, never the working directory

def test_a_started_server_is_scoped_and_contained_for_the_bound_project_wherever_the_process_stands(places, mcp_standin, monkeypatch):
    project, foreign = places
    resolved = _spied(monkeypatch, "resolve_mcp_capability_profile")
    mapped = _spied(monkeypatch, "map_capability_profile_to_containment")
    manager = _manager(project)
    server = {"command": sys.executable, "args": [MOCK_SERVER], "env": {},
              "capabilities": {"workspace_read": True, "additional_read_paths": [os.path.join(project, "notes")]}}

    async def run():
        await manager.start_all({"srv": server})
        try:
            return manager.clients["srv"].capability_profile, sorted(manager.registered_tools["srv"])
        finally:
            await manager.shutdown_all()

    with _standing_in(foreign):
        profile, tools = asyncio.run(run())
    assert resolved == [project] and mapped == [project], "the capability scope and the containment mapping were taken for the bound project"
    assert [path.scope for path in profile.additional_read_paths] == [MCPPathScope.WORKSPACE_RELATIVE], "a path of the project is the project's"
    assert tools == ["srv_echo_test"] and len(mcp_standin.prepared_profiles) == 1, "an ordinary read-only server still starts and registers"


def test_the_working_directory_decided_the_scope_before_the_fix_is_what_this_would_have_measured(places):
    """The discriminating fact: the SAME declaration resolves differently for the two roots, so which root is used is observable."""
    project, foreign = places
    declared = {"additional_read_paths": [os.path.join(project, "notes")]}
    assert [p.scope for p in resolve_mcp_capability_profile(declared, workspace_root=project).additional_read_paths] == [MCPPathScope.WORKSPACE_RELATIVE]
    assert [p.scope for p in resolve_mcp_capability_profile(declared, workspace_root=foreign).additional_read_paths] == [MCPPathScope.HOST_PATH]


def _approve(workspace_root, identity, digest):
    save_approval_artifact(default_local_approval_path(workspace_root),
                           add_approval(empty_artifact(workspace_identity(workspace_root)), identity, digest))


def test_the_invocation_approval_is_the_bound_projects_and_a_foreign_working_directory_cannot_supply_one(places):
    project, foreign = places
    identity = MCPToolIdentity(server_identity="srv", tool_name="echo_test", schema_digest="0" * 64)
    digest = compute_mcp_capability_profile_digest(resolve_mcp_capability_profile({}, workspace_root=project))
    capability = MCPCapabilityProfileIdentity(server_identity="srv", profile_digest=digest)
    manager = _manager(project)
    with _standing_in(foreign):
        assert manager._approval_for_bound_project(identity, capability) is False   # nothing approved anywhere yet
        _approve(foreign, identity, digest)                                          # an approval that exists for the directory the process stands in
        assert manager._approval_for_bound_project(identity, capability) is False, "the working directory's approval is not the project's"
        _approve(project, identity, digest)
        assert manager._approval_for_bound_project(identity, capability) is True, "the project's own approval is found from anywhere"
    assert manager._execution_policy._mcp_invocation_approval_resolver == manager._approval_for_bound_project   # the production default resolver


def test_a_configuration_bound_to_no_project_starts_no_server_and_approves_nothing(places, mcp_standin):
    project, _foreign = places
    for manager in (_manager(None), MCPManager(Kernel())):
        with _standing_in(project), pytest.raises(MCPLifecycleError) as refused:
            asyncio.run(manager.start_all({"srv": {"command": sys.executable, "args": [MOCK_SERVER], "env": {}}}))
        assert isinstance(refused.value.__cause__, ProjectStateError) and refused.value.__cause__.reason_code == PROJECT_IDENTITY_MISSING
        assert manager.clients == {} and mcp_standin.prepared_profiles == [], "refused before a client or a containment existed"
        with _standing_in(project), pytest.raises(ProjectStateError):
            manager._approval_for_bound_project(MCPToolIdentity(server_identity="srv", tool_name="t", schema_digest="0" * 64),
                                                MCPCapabilityProfileIdentity(server_identity="srv", profile_digest="0" * 64))
    with pytest.raises(ProjectStateError):
        bound_project_root(None)


def test_a_client_handed_no_workspace_root_is_refused_before_its_containment_is_prepared(places):
    project, _foreign = places
    backend = NoPrefixBackend()
    client = MCPClient("srv", sys.executable, [MOCK_SERVER], containment_backend=backend)
    with _standing_in(project), pytest.raises(ContainmentSetupError, match=MCP_WORKSPACE_UNBOUND):
        asyncio.run(client.start())
    assert backend.cleaned == 0 and client.containment_active is False, "nothing was prepared, so nothing had to be released"


# ------------------------------------------------------------------ INT-06: a checkpoint is not knowledge truth

def _guard_stage(engine, workspace, **fields):
    from kriya.workflow.stage_context import StageContext
    from kriya.workflow.stage_knowledge_guard import check_knowledge_guard
    from kriya.workflow.state import GenerationState

    deps = dataclasses.replace(engine._work_unit_deps(), trace_events=lambda state: [])
    ctx = StageContext(goal=WIDGET_GOAL, workspace_path=workspace, state=GenerationState(), trace_id="t-1", start_time=0.0, run_id="run-1", **fields)
    return asyncio.run(check_knowledge_guard(ctx, deps)), ctx


CHECKPOINT = {"plan": "Step 1: upgrade the cache", "design": "d", "architect_files": ["cache.py"]}


def test_a_resumed_unit_is_judged_by_the_current_knowledge_and_not_by_its_checkpoint(tmp_path, monkeypatch):
    a = _project(tmp_path, "A", A_FILES)
    cfg = _config(monkeypatch, a)
    cfg.knowledge.offline_mode = True
    engine, _llm = _real_engine(cfg)
    fresh, _ = _guard_stage(engine, a)
    resumed, ctx = _guard_stage(engine, a, resume_state=dict(CHECKPOINT))
    assert fresh["status"] == "knowledge_gap"
    assert resumed is not None and resumed["status"] == "knowledge_gap", "the checkpoint exists and the knowledge is still missing: the gate holds"
    assert [gap["library"] for gap in resumed["gap_report"]["gaps"]] == [WIDGET] and ctx.guard is None
    accepted, _ = _guard_stage(engine, a, resume_state=dict(CHECKPOINT), knowledge_risk_confirmed=True)
    assert accepted is None, "the acceptance THIS invocation carries opens it, as on a fresh run"
    _widget_skill(cfg.paths.skills, "widget-core")   # the knowledge now exists in the project's overlay
    resumed, ctx = _guard_stage(engine, a, resume_state=dict(CHECKPOINT))
    assert resumed is None and ctx.all_gap_report.gaps == [], "the checkpoint exists and the knowledge is valid: the resume proceeds"


# ------------------------------------------------------------------ BYP-A4-02: one skill selection text per run

USER_GOAL = "Add a warm-up command to the alphalib client."


def _selection_fixture(tmp_path):
    from test_work_unit_stages import _ctx, _deps, _engine

    from kriya.workflow.plan_schema import EngineeringPlan
    from kriya.workflow.planning_request import build_subtask_goal_text

    engine = _engine(tmp_path)
    for name, tag in (("alpha-skill", "alphalib"), ("beta-skill", "betalib")):
        folder = tmp_path / "skills" / name
        folder.mkdir(parents=True)
        (folder / "skill.yaml").write_text(f"name: {name}\ndescription: d\nverified: true\ntags:\n  - {tag}\n")
        (folder / "rules.txt").write_text(f"Follow the {tag} conventions.\n")
    plan = EngineeringPlan.model_validate({"plan_id": "p", "kind": "task", "subtasks": [
        {"id": "s1", "description": "Implement the warm-up command.", "execution_method": "model",
         "planned_files": [{"path": "app/warmup.py", "action": "create"}], "provides": ["warmup"], "requirement_ids": ["REQ-1"]},
        {"id": "s2", "description": "Write the unit test for the warm-up command with the betalib test harness.", "execution_method": "model",
         "depends_on": ["s1"], "planned_files": [{"path": "tests/test_warmup.py", "action": "create"}], "requires": ["warmup"],
         "requirement_ids": ["REQ-1"]}]})
    unit_goal = build_subtask_goal_text(plan.subtasks[1], 2, 2, plan=plan, grounding_goal=USER_GOAL)
    assert "betalib" in unit_goal and "betalib" not in USER_GOAL   # the Planner's text, not the user's, names the second library
    return engine, unit_goal, _ctx, _deps


def _unit_selection(tmp_path, engine, _ctx, _deps, **fields):
    from kriya.tools.knowledge import GapReport
    from kriya.workflow.stage_analysis import analyze_work_unit

    ctx = _ctx(tmp_path, all_gap_report=GapReport(), **fields)
    asyncio.run(analyze_work_unit(ctx, _deps(engine)))
    return ctx.active_skills


def test_a_unit_applies_the_skills_the_planner_was_given_and_planner_text_adds_none(tmp_path):
    from kriya.workflow.skill_extraction import planning_skill_constraints

    engine, unit_goal, _ctx, _deps = _selection_fixture(tmp_path)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _block, planner_names = planning_skill_constraints(engine.kernel.config, str(workspace), USER_GOAL)
    unit_names = _unit_selection(tmp_path, engine, _ctx, _deps, goal=unit_goal, grounding_goal=USER_GOAL)
    assert planner_names == ["alpha-skill"]
    assert unit_names == planner_names, "the unit's Developer is given exactly the skills the plan was made under"


def test_a_direct_run_still_selects_over_its_own_goal(tmp_path):
    """Negative control: with no separate user goal (the direct shape) the unit's goal IS the user's text and selects as before."""
    engine, _unit_goal, _ctx, _deps = _selection_fixture(tmp_path)
    assert _unit_selection(tmp_path, engine, _ctx, _deps, goal="Use the betalib harness in calc.py.") == ["beta-skill"]
