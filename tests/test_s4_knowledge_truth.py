"""C-06 / R-06 and C-04 ruling 11 (slice S4): storage presence is not knowledge, and a write grant is not a mutation approval.

Knowledge truth. Whether a library is covered is the Skills owner's answer (``SkillEngine.covers_library``) about the skills LOADED for the
project: a skill whose name or tags name the library AND that holds at least one rule trusted for this project. The knowledge gate
(``KnowledgeGuard.check_goal``) asks; it lists no directory. A4 probes P5 (an empty folder) and P11 (Kriya's own scaffold) and A5 probe P8 with
an explicit skills directory two projects name are inverted here. MEASURED before the fix at ed85a88: each of the three left NO gap.

Rule trust is decided once, at load (``SkillEngine._trusted_rules``): a workspace skill's rules are project-supplied bytes and never trusted
(A4 P8 inverted, SKILL-RULE-PROVENANCE-PROJECT-SUPPLIED-001); a rule a run wrote is trusted only after a committed run verified it, and
only for the projects that did (SKILLS-EXPLICIT-ROOT-IDENTITY-BINDING-001); an unreadable provenance file trusts nothing (INT-07).

MCP. A planned (model-authored) call to an MCP tool whose BOUND capability profile holds a write grant is operator-only (owner ruling 11).
MEASURED before the fix: plannable.
"""
import asyncio
import dataclasses
import json
import os

import pytest
from test_s3_project_state_namespace import A_FILES, B_FILES, _config, _project, _real_engine

from kriya.context.workspace_identity import workspace_identity
from kriya.mcp.capability import resolve_mcp_capability_profile
from kriya.mcp.mcp import MCPClient, MCPTool
from kriya.skills.skill import HeldSkillVerification, SkillEngine, get_global_skills_dir, mark_rules_verified
from kriya.tools.knowledge import KnowledgeGuard
from kriya.tools.tool import (
    TOOL_GOVERNANCE_INVALID,
    TOOL_OPERATOR_ONLY,
    ModelInvocationRefused,
    ModelToolInvocation,
    ToolGovernance,
    authorize_model_authored,
    resolve_planned_tool_governance,
)
from kriya.workflow.skill_conventions import render_skill_conventions
from kriya.workflow.skill_extraction import _split_rules_by_verification

# A library the shipped skill library does not cover.
WIDGET = "com.kriyaprobe.widgets:widget-core"
GOAL = f"Upgrade the cache to use {WIDGET}:2.16.0 everywhere."
RULE = "Always close the WidgetPool before the process exits."
OTHER_RULE = "Never share a WidgetHandle between threads."


def _skill(root, folder, *, tags=("widget-core",), rules=(RULE,), provenance=None, name=None):
    """A skill folder under ``root``; ``provenance`` None writes no provenance file, a str writes those bytes, a list writes it as JSON."""
    path = os.path.join(str(root), folder)
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "skill.yaml"), "w", encoding="utf-8") as handle:
        handle.write(f"name: {name or folder}\ndescription: d\ntags: [{', '.join(tags)}]\n")
    with open(os.path.join(path, "rules.txt"), "w", encoding="utf-8") as handle:
        handle.write("# rules\n" + "".join(f"{rule}\n" for rule in rules))
    if provenance is not None:
        with open(os.path.join(path, "rule_provenance.json"), "w", encoding="utf-8") as handle:
            handle.write(provenance if isinstance(provenance, str) else json.dumps(provenance))
    return path


def _record(text=RULE, verified=False, **more):
    return {"text": text, "verified": verified, "source": "live_lookup:https://example.invalid/doc", "added_at": "2026-11-01", **more}


def _engine(root, workspace):
    engine = SkillEngine(str(root), load_global=False, load_cwd=False, workspace_path=str(workspace))
    engine.discover_and_load()
    return engine


def _gaps(root, workspace, *, engine=None, library_dirs=()):
    engine = engine or _engine(root, workspace)
    guard = KnowledgeGuard(skills_dir=str(root), cutoff_date_str="2023-12-01", offline=True, library_dirs=library_dirs,
                           coverage=engine.covers_library)
    return [gap["library"] for gap in guard.check_goal(GOAL, str(workspace)).gaps]


@pytest.fixture
def places(tmp_path):
    """An operator-owned skills root OUTSIDE the workspace, and the workspace."""
    root, workspace = tmp_path / "operator-skills", tmp_path / "project"
    root.mkdir()
    workspace.mkdir()
    return root, workspace


# ------------------------------------------------------------------ presence is not knowledge (A4 P5, A4 P11)

def test_an_empty_folder_named_after_the_library_leaves_the_gap(places):
    root, workspace = places
    os.makedirs(root / "my-notes-on-widget-core-outage")
    os.makedirs(root / "com.kriyaprobe.widgets-widget-core")
    assert _gaps(root, workspace) == [WIDGET]


def test_kriyas_own_scaffolds_leave_the_gap(places):
    root, workspace = places
    guard = KnowledgeGuard(skills_dir=str(root), cutoff_date_str="2023-12-01", offline=True)
    scaffold = guard.generate_skill_template(WIDGET, "2.16.0")
    assert sorted(os.listdir(scaffold)) == ["examples", "instructions.md", "rules.txt", "skill.yaml"]
    engine = _engine(root, workspace)
    engine.create_skill_skeleton("widget-core")
    engine.discover_and_load()
    loaded = {skill.name: skill for skill in engine.list_skills()}
    assert set(loaded) == {"com.kriyaprobe.widgets-widget-core", "widget-core"}  # both ARE loaded skills whose tags name the library
    assert all(skill.rules == [] and skill.trusted_rules == [] for skill in loaded.values())
    assert _gaps(root, workspace, engine=engine) == [WIDGET]


def test_a_loaded_skill_with_a_matching_tag_and_a_trusted_rule_clears_the_gap(places):
    root, workspace = places
    _skill(root, "widgets-house-rules")
    engine = _engine(root, workspace)
    assert engine.get_skill("widgets-house-rules").trusted_rules == [RULE]  # authored by the root's owner: no record, an operator's root
    assert _gaps(root, workspace, engine=engine) == []


def test_a_trusted_rule_under_tags_that_do_not_name_the_library_leaves_the_gap(places):
    root, workspace = places
    _skill(root, "widget-core-notes", tags=("gadgets", "core"), name="notes")  # the FOLDER names the library; the skill does not
    engine = _engine(root, workspace)
    assert engine.get_skill("notes").trusted_rules == [RULE]
    assert _gaps(root, workspace, engine=engine) == [WIDGET]


def test_a_skill_outside_the_roots_that_count_leaves_the_gap(places, tmp_path):
    root, workspace = places
    elsewhere = tmp_path / "another-root"
    _skill(elsewhere, "widget-core")
    engine = SkillEngine(str(root), load_global=False, load_cwd=False, workspace_path=str(workspace))
    engine.skills_dirs.insert(0, str(elsewhere))  # loaded by the engine, as the shipped library or ./skills of the working directory can be
    engine.discover_and_load()
    assert engine.get_skill("widget-core").trusted_rules == [RULE]
    assert _gaps(root, workspace, engine=engine) == [WIDGET]
    assert _gaps(root, workspace, engine=engine, library_dirs=[str(elsewhere)]) == []


def test_a_guard_with_no_owner_to_ask_or_a_non_boolean_answer_knows_of_no_coverage(places):
    root, workspace = places
    _skill(root, "widget-core")
    unasked = KnowledgeGuard(skills_dir=str(root), cutoff_date_str="2023-12-01", offline=True)
    assert [gap["library"] for gap in unasked.check_goal(GOAL, str(workspace)).gaps] == [WIDGET]
    truthy = KnowledgeGuard(skills_dir=str(root), cutoff_date_str="2023-12-01", offline=True, coverage=lambda aliases, roots: "yes")
    assert [gap["library"] for gap in truthy.check_goal(GOAL, str(workspace)).gaps] == [WIDGET]


# ------------------------------------------------------------------ rule trust: one decision, at load

def test_a_rule_a_run_wrote_is_trusted_only_once_a_run_verified_it(places):
    root, workspace = places
    folder = _skill(root, "widget-core", rules=(RULE, OTHER_RULE), provenance=[_record(RULE), _record(OTHER_RULE)])
    engine = _engine(root, workspace)
    skill = engine.get_skill("widget-core")
    assert skill.trusted_rules == [] and _split_rules_by_verification(skill) == ([], [RULE, OTHER_RULE])
    assert _gaps(root, workspace, engine=engine) == [WIDGET]
    assert mark_rules_verified(folder, [OTHER_RULE]) is True
    engine.discover_and_load()
    skill = engine.get_skill("widget-core")
    assert _split_rules_by_verification(skill) == ([OTHER_RULE], [RULE])
    assert _gaps(root, workspace, engine=engine) == []


@pytest.mark.parametrize("claimed", ["true", 1, "yes"])
def test_only_a_literal_true_is_a_verification(places, claimed):
    root, workspace = places
    _skill(root, "widget-core", provenance=[_record(RULE, verified=claimed)])
    assert _engine(root, workspace).get_skill("widget-core").trusted_rules == []


@pytest.mark.parametrize("damaged", ["{not json", json.dumps({"text": RULE, "verified": True}), json.dumps(["a string, not a record"])])
def test_an_unreadable_provenance_file_trusts_no_rule_of_that_skill(places, damaged):
    """INT-07. Before S4 an unreadable file read as "no records", which made every rule trusted."""
    root, workspace = places
    _skill(root, "widget-core", provenance=damaged)
    engine = _engine(root, workspace)
    skill = engine.get_skill("widget-core")
    assert skill.rules == [RULE] and skill.trusted_rules == []
    assert _gaps(root, workspace, engine=engine) == [WIDGET]


def test_a_workspace_skill_cannot_attest_its_own_rules(tmp_path):
    """A4 P8 inverted (SKILL-RULE-PROVENANCE-PROJECT-SUPPLIED-001): the repository supplies rules.txt and rule_provenance.json."""
    workspace = tmp_path / "project"
    root = workspace / "skills"
    _skill(root, "widget-core", rules=(RULE, OTHER_RULE), provenance=[_record(OTHER_RULE, verified=True)])
    engine = _engine(root, workspace)
    skill = engine.get_skill("widget-core")
    assert skill.trusted_rules == []
    rendered = render_skill_conventions(skill, engine, _split_rules_by_verification)
    assert "\nRules:\n" not in rendered and "Unverified Rules" in rendered and RULE in rendered and OTHER_RULE in rendered
    assert _gaps(root, workspace, engine=engine) == [WIDGET]
    # ... while a passing run of THIS run still promotes its rules for the rest of the run (unchanged), without closing the gate.
    engine.hold_verifications([HeldSkillVerification("widget-core", "widget-core 2.16.0", (RULE,))])
    rendered = render_skill_conventions(skill, engine, _split_rules_by_verification)
    assert f"Rules:\n- {RULE}\n" in rendered and "Unverified Rules" in rendered
    assert _gaps(root, workspace, engine=engine) == [WIDGET]


def test_rules_a_run_creates_with_a_skill_are_unverified_and_do_not_close_the_gate(places):
    """The analyzer's conventions bootstrap writes model-derived rules through ``create_skill``. Without a record they would read as authored
    by the owner of the skills root: trusted on the next load."""
    root, workspace = places
    engine = _engine(root, workspace)
    created = engine.create_skill("widget-core", {"name": "widget-core", "description": "d", "tags": ["widget-core"]},
                                  "# instructions\n", f"# derived\n{RULE}\n{OTHER_RULE}\n", commit_message=None)
    with open(os.path.join(created, "rule_provenance.json"), encoding="utf-8") as handle:
        records = json.load(handle)
    assert [(record["text"], record["verified"], record["source"]) for record in records] == [
        (RULE, False, "skill_creation"), (OTHER_RULE, False, "skill_creation")]
    engine.discover_and_load()
    assert engine.get_skill("widget-core").rules == [RULE, OTHER_RULE] and engine.get_skill("widget-core").trusted_rules == []
    assert _gaps(root, workspace, engine=engine) == [WIDGET]
    skeleton = engine.create_skill_skeleton("empty-one")
    assert not os.path.exists(os.path.join(skeleton, "rule_provenance.json"))  # no rule, no record


# ------------------------------------------------------------------ one explicit skills directory, two projects (A5 P8)

def test_what_one_projects_run_proved_in_a_shared_skills_directory_is_not_knowledge_for_another(tmp_path):
    shared = tmp_path / "shared-skills"
    a, b = _project(tmp_path, "A", A_FILES), _project(tmp_path, "B", B_FILES)
    KnowledgeGuard(skills_dir=str(shared), cutoff_date_str="2023-12-01", offline=True).generate_skill_template(WIDGET, "2.16.0")
    assert _gaps(shared, a) == [WIDGET] and _gaps(shared, b) == [WIDGET]  # A's scaffold closes neither gate (MEASURED before: it closed B's)
    folder = _skill(shared, "widget-core", provenance=[_record(RULE)])  # a rule A's run extracted: unverified
    engine_a = _engine(shared, a)
    engine_a.hold_verifications([HeldSkillVerification("widget-core", "widget-core 2.16.0", (RULE,))])
    assert engine_a.apply_held_verifications() == []  # A's run verified its candidate and committed: the rule's verification is persisted
    with open(os.path.join(folder, "rule_provenance.json"), encoding="utf-8") as handle:
        (record,) = json.load(handle)
    assert record["verified"] is True and record["verified_for"] == [workspace_identity(a)]
    assert _gaps(shared, a) == [] and _engine(shared, a).get_skill("widget-core").trusted_rules == [RULE]
    assert _gaps(shared, b) == [WIDGET] and _engine(shared, b).get_skill("widget-core").trusted_rules == []
    # B's own committed run earns it for B, and A keeps it.
    engine_b = _engine(shared, b)
    engine_b.hold_verifications([HeldSkillVerification("widget-core", "widget-core 2.16.0", (RULE,))])
    assert engine_b.apply_held_verifications() == []
    with open(os.path.join(folder, "rule_provenance.json"), encoding="utf-8") as handle:
        (record,) = json.load(handle)
    assert record["verified_for"] == [workspace_identity(a), workspace_identity(b)]
    assert _gaps(shared, a) == [] and _gaps(shared, b) == []


def test_a_malformed_project_binding_binds_no_project(places):
    root, workspace = places
    _skill(root, "widget-core", provenance=[_record(RULE, verified=True, verified_for="everyone")])
    assert _engine(root, workspace).get_skill("widget-core").trusted_rules == []


def test_an_operators_verification_carries_no_project_list_and_is_left_alone(places):
    root, workspace = places
    folder = _skill(root, "widget-core", provenance=[_record(RULE, verified=True)])
    assert mark_rules_verified(folder, [RULE], project="some-project") is None  # nothing to change: not narrowed to one project
    assert _engine(root, workspace).get_skill("widget-core").trusted_rules == [RULE]


# ------------------------------------------------------------------ the shipped library and the run entries

def test_the_shipped_library_still_covers_its_libraries_for_a_default_project(tmp_path, monkeypatch):
    a = _project(tmp_path, "A", A_FILES)
    cfg = _config(monkeypatch, a)
    engine = SkillEngine.from_config(cfg, workspace_path=a)
    engine.discover_and_load()
    guard = KnowledgeGuard(skills_dir=cfg.paths.skills, cutoff_date_str="2023-12-01", offline=True,
                           library_dirs=SkillEngine.gate_library_roots(cfg), coverage=engine.covers_library)
    assert SkillEngine.gate_library_roots(cfg) == [get_global_skills_dir()]
    report = guard.check_goal(f"Bridge qpid to artemis and cache in ignite, then use {WIDGET}:2.16.0.", a)
    assert [gap["library"] for gap in report.gaps] == [WIDGET]


def _stage(engine, workspace, **replacements):
    from kriya.workflow.stage_context import StageContext
    from kriya.workflow.stage_knowledge_guard import check_knowledge_guard
    from kriya.workflow.state import GenerationState

    deps = dataclasses.replace(engine._work_unit_deps(), trace_events=lambda state: [], **replacements)
    ctx = StageContext(goal=GOAL, workspace_path=workspace, state=GenerationState(), trace_id="t-1", start_time=0.0, run_id="run-1")
    return asyncio.run(check_knowledge_guard(ctx, deps)), ctx


def test_the_unit_stage_asks_the_skills_owner_and_a_scaffold_does_not_open_it(tmp_path, monkeypatch):
    a = _project(tmp_path, "A", A_FILES)
    cfg = _config(monkeypatch, a)
    cfg.knowledge.offline_mode = True
    engine, _llm = _real_engine(cfg)
    KnowledgeGuard(skills_dir=cfg.paths.skills, cutoff_date_str="2023-12-01", offline=True).generate_skill_template(WIDGET, "2.16.0")
    result, _ctx = _stage(engine, a)
    assert result["status"] == "knowledge_gap" and [gap["library"] for gap in result["gap_report"]["gaps"]] == [WIDGET]
    _skill(cfg.paths.skills, "widget-core")  # the operator supplies the knowledge in the project's overlay
    result, ctx = _stage(engine, a)
    assert result is None and ctx.all_gap_report.gaps == []


def test_the_enforce_entry_asks_the_skills_owner_and_a_scaffold_does_not_open_it(tmp_path, monkeypatch):
    from test_s3_project_state_namespace import _git_project

    from kriya.workflow.workflow_controller import WorkflowController

    a, cfg = _git_project(tmp_path, monkeypatch)
    cfg.knowledge.offline_mode = True
    KnowledgeGuard(skills_dir=cfg.paths.skills, cutoff_date_str="2023-12-01", offline=True).generate_skill_template(WIDGET, "2.16.0")
    engine, _llm = _real_engine(cfg)
    calls = []

    class _Spy:
        def __new__(cls, **kwargs):
            calls.append(kwargs)
            return KnowledgeGuard(**kwargs)

    monkeypatch.setattr("kriya.tools.knowledge.KnowledgeGuard", _Spy)
    result = asyncio.run(WorkflowController(engine).execute(GOAL, a, migration_mode="enforce"))
    assert result.legacy_result["status"] == "knowledge_gap"
    (call,) = calls
    assert call["coverage"].__func__ is SkillEngine.covers_library and call["coverage"].__self__.workspace_path == os.path.abspath(a)


# ------------------------------------------------------------------ C-04 ruling 11: a planned call never reaches a write-granted MCP tool

META = {"name": "save_note", "description": "Read-only: returns the note. Never writes.", "inputSchema": {"type": "object", "properties": {}}}


def _mcp_tool(workspace, capabilities):
    profile = resolve_mcp_capability_profile(capabilities, workspace_root=str(workspace))
    return MCPTool(MCPClient("notes", "true", [], capability_profile=profile, containment_backend=object()), META)


def _write_path_grant(tmp_path):
    target = tmp_path / "outside-notes"
    target.mkdir()
    return {"additional_write_paths": [str(target)]}


def test_a_planned_call_to_a_tool_of_a_write_granted_mcp_server_is_operator_only(tmp_path):
    read_only = _mcp_tool(tmp_path, {})
    decision = resolve_planned_tool_governance(read_only, autonomy_mode="guardrails")
    assert decision.plannable and decision.governance is ToolGovernance.GOVERNED_SIDE_EFFECT  # model-planned read-only MCP: unchanged
    for capabilities in ({"workspace_write": True}, {"workspace_read": True, "workspace_write": True}, _write_path_grant(tmp_path)):
        tool = _mcp_tool(tmp_path, capabilities)
        assert tool.description.startswith("Read-only")  # what the server says about the tool decides nothing
        for mode in ("guardrails", "human-in-the-loop", None):
            decision = resolve_planned_tool_governance(tool, autonomy_mode=mode)
            assert (decision.plannable, decision.governance, decision.reason_code) == (False, ToolGovernance.OPERATOR_ONLY, TOOL_OPERATOR_ONLY)


def test_the_executor_recheck_refuses_it_before_anything_is_authorized_or_run(tmp_path):
    tool = _mcp_tool(tmp_path, {"workspace_write": True})
    reached = []
    tool._decide_invocation_authority = lambda: reached.append("authority")
    tool.client.call_tool = lambda *args, **kwargs: reached.append("dispatch")
    invocation = ModelToolInvocation(roots=(str(tmp_path),), autonomy_mode="guardrails")
    with pytest.raises(ModelInvocationRefused) as refused:
        authorize_model_authored(tool, {}, invocation)
    assert refused.value.reason_code == TOOL_OPERATOR_ONLY and reached == []
    assert os.listdir(tmp_path) == []  # the workspace is untouched


def test_a_capability_profile_that_cannot_be_read_is_not_plannable(tmp_path):
    tool = _mcp_tool(tmp_path, {})
    tool.client.capability_profile = None
    decision = resolve_planned_tool_governance(tool, autonomy_mode="guardrails")
    assert (decision.plannable, decision.reason_code) == (False, TOOL_GOVERNANCE_INVALID)
