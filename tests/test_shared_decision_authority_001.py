"""SHARED-DECISION-AUTHORITY-001: one upstream authority selects a cross-subtask alternative; downstream transports it.

MEASURED (run 20261110T064040-fcf2b210, sealed): an applicable skill offered two mutually exclusive mechanisms (A or B);
the Planner never saw the skill, recorded no choice, and two sibling Developers each picked a mechanism legal under the
skill but incompatible with the other - a runtime failure no per-unit gate could attribute.

These controls are model-free and fixture-neutral (an A-or-B rule that names nothing of the sealed scenario):
  Planner sees the applicable skill (only the applicable ones, losslessly, through the Developer-side functions);
  the plan records the choice once in the existing IntegrationRelationship seam; validation requires a declared choice to
  have exactly one selection from its menu and refuses two selections under one id; every participating Developer prompt
  carries the same authoritative contract and a non-participant's does not.
"""
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _strict_doubles import strict_config, strict_engine

from kriya.skills.skill import SkillEngine
from kriya.workflow import skill_conventions
from kriya.workflow.plan_schema import (
    EngineeringPlan,
    ExecutionMethod,
    FileAction,
    IntegrationRelationship,
    IntegrationRelationshipKind,
    PlannedFile,
    Subtask,
    VerificationMethod,
    VerificationMethodType,
)
from kriya.workflow.plan_validation import PlanValidationResult, integration_decision_findings, validate_plan
from kriya.workflow.planning_request import (
    AUTHORITATIVE_PLANNER_SYSTEM_PROMPT,
    INTEGRATION_CONTRACT_AUTHORITY,
    build_subtask_semantic_context,
    integration_contracts_for,
)
from kriya.workflow.skill_extraction import planning_skill_constraints
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass
from kriya.workflow.workflow_controller import WorkflowController

C = "kriya.workflow.workflow_controller"
GOAL = "Build a codecwire exporter and a codecwire importer"
RED = "mode-red: the exporter writes frames and the importer reads frames with the framed reader"
BLUE = "mode-blue: the exporter writes lines and the importer reads lines with the line reader"
MENU_RULE = f"Use EXACTLY ONE of these two wire modes for a codecwire pair, never both: 1) {RED}; 2) {BLUE}"


def _skill(root, name, tags, rules, instructions=""):
    folder = os.path.join(root, name)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "skill.yaml"), "w", encoding="utf-8") as handle:
        handle.write(f"name: {name}\ndescription: test skill\ncategory: General\nverified: true\ntags:\n"
                     + "".join(f"- {tag}\n" for tag in tags))
    with open(os.path.join(folder, "rules.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(rules) + "\n")
    if instructions:
        with open(os.path.join(folder, "instructions.md"), "w", encoding="utf-8") as handle:
            handle.write(instructions)


@pytest.fixture
def skills_root(tmp_path):
    # Outside the workspace (a sibling of tmp_path): inside it the skill files would be repository evidence for the Planner.
    root = str(tmp_path.parent / f"{tmp_path.name}-skills")
    _skill(root, "codecwire", ["codecwire"], [MENU_RULE, "Always close the stream after the last frame."])
    _skill(root, "gizmo-store", ["gizmo"], ["Never reuse a gizmo handle across threads."])   # not applicable to GOAL
    return root


def _workspace(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    return str(workspace)


def _config(tmp_path, skills_root):
    return strict_config(paths={"skills": skills_root, "memory": str(tmp_path / "memory")},
                         autonomy={"spec_compliance_enabled": False})


# -- Planner sees the applicable skill ------------------------------------------------------------------------------------

def test_the_planner_is_given_the_applicable_skill_losslessly_and_only_that_one(tmp_path, skills_root):
    block, names = planning_skill_constraints(_config(tmp_path, skills_root), _workspace(tmp_path), GOAL)
    assert names == ["codecwire"]
    assert MENU_RULE in block and "Always close the stream after the last frame." in block   # every rule, as written
    assert "gizmo" not in block                                                                # an unrelated skill is never dumped
    assert "choice affects more than one subtask" in block                                    # and it says what to do with a menu


def test_no_applicable_skill_means_no_planner_block(tmp_path, skills_root):
    assert planning_skill_constraints(_config(tmp_path, skills_root), _workspace(tmp_path), "Write a haiku") == ("", [])


def test_the_planner_block_is_the_developer_side_rendering_not_a_second_one(tmp_path, skills_root):
    """One selection and one renderer: the block is the header plus exactly what render_skill_conventions gives the Developer."""
    cfg = _config(tmp_path, skills_root)
    engine = SkillEngine.from_config(cfg, workspace_path=_workspace(tmp_path))
    engine.discover_and_load()
    from kriya.workflow.skill_extraction import _split_rules_by_verification

    developer_side = skill_conventions.render_skill_conventions(engine.get_skill("codecwire"), engine, _split_rules_by_verification)
    block, _ = planning_skill_constraints(cfg, _workspace(tmp_path), GOAL, skill_engine=engine)
    assert block == skill_conventions.PLANNING_SKILL_CONSTRAINTS_HEADER + developer_side


def test_the_planner_contract_states_the_choice_rule_without_naming_any_scenario():
    text = AUTHORITATIVE_PLANNER_SYSTEM_PROMPT
    assert "mutually exclusive" in text and "selected_alternative" in text and "choose exactly ONE alternative" in text
    for word in ("ignite", "spring", "ignitenode", "ignite.cfg", "protocol", "scenario"):
        assert word not in (text + INTEGRATION_CONTRACT_AUTHORITY + skill_conventions.PLANNING_SKILL_CONSTRAINTS_HEADER).lower()


# -- the plan records the choice once, in the existing seam ---------------------------------------------------------------

def _rel(rel_id="d1", *, alternatives=(RED, BLUE), selected=RED, producers=("s1",), consumers=("s2",), **extra):
    return IntegrationRelationship(
        id=rel_id, kind=IntegrationRelationshipKind.USES, producer_subtask_ids=list(producers),
        consumer_subtask_ids=list(consumers), relationship_statement="the importer reads what the exporter writes",
        alternatives=list(alternatives), selected_alternative=selected, **extra)


def _codes(*relationships):
    return [code for code, _ in integration_decision_findings(list(relationships))]


def test_a_declared_choice_with_exactly_one_selection_from_its_menu_is_well_formed():
    assert _codes(_rel()) == []


def test_a_declared_choice_with_no_selection_is_refused_for_repair():
    assert _codes(_rel(selected="")) == ["INTEGRATION_DECISION_SELECTION_MISSING"]


def test_a_selection_outside_the_menu_or_a_malformed_menu_is_refused():
    assert _codes(_rel(selected="mode-green: something else")) == ["INTEGRATION_DECISION_SELECTION_INVALID"]
    assert _codes(_rel(alternatives=(RED,), selected=RED)) == ["INTEGRATION_DECISION_SELECTION_INVALID"]       # one option is no choice
    assert _codes(_rel(alternatives=(RED, RED), selected=RED)) == ["INTEGRATION_DECISION_SELECTION_INVALID"]  # not distinct
    assert _codes(_rel(alternatives=(RED, "  "), selected=RED)) == ["INTEGRATION_DECISION_SELECTION_INVALID"]  # a blank option


def test_two_selections_under_one_decision_id_are_a_conflict_never_resolved_by_order():
    assert _codes(_rel(selected=RED), _rel(selected=BLUE)) == ["INTEGRATION_DECISION_CONFLICT"]
    assert _codes(_rel(selected=BLUE), _rel(selected=RED)) == ["INTEGRATION_DECISION_CONFLICT"]   # not "last wins" nor "first wins"
    assert _codes(_rel(selected=RED), _rel(selected=RED)) == []                                     # a repeat of the same choice is not one


def test_independent_choices_are_never_forced_into_one_decision():
    """Negative control: two unrelated choices (different ids), even from the same menu, may select differently."""
    assert _codes(_rel("d1", selected=RED), _rel("d2", selected=BLUE, producers=("s3",), consumers=("s4",))) == []


def test_a_relationship_that_records_no_choice_is_unchanged():
    plain = IntegrationRelationship(id="p1", kind=IntegrationRelationshipKind.USES, producer_subtask_ids=["s1"],
                                    consumer_subtask_ids=["s2"], relationship_statement="reads it")
    assert plain.alternatives == [] and plain.selected_alternative == "" and _codes(plain) == []


def _unit(sid, path, depends_on=()):
    return Subtask(id=sid, description=f"write {path}", execution_method=ExecutionMethod.MODEL,
                   planned_files=[PlannedFile(path=path, action=FileAction.CREATE)], depends_on=list(depends_on),
                   verification=[VerificationMethod(type=VerificationMethodType.TOOL, description="compiles", tool_name="compile")])


def _plan(*relationships, extra_units=()):
    return EngineeringPlan(plan_id="run1", kind=ChangeKind.TASK,
                           subtasks=[_unit("s1", "exporter.py"), _unit("s2", "importer.py", ["s1"]), *extra_units],
                           integration_relationships=list(relationships))


@pytest.mark.asyncio
async def test_validate_plan_reports_the_decision_codes_and_accepts_a_selected_choice(tmp_path):
    ws = _workspace(tmp_path)
    good = await validate_plan(_plan(_rel()), workspace_path=ws)
    assert good.valid, good.errors
    for relationship, code in ((_rel(selected=""), "INTEGRATION_DECISION_SELECTION_MISSING"),
                               (_rel(selected="nope"), "INTEGRATION_DECISION_SELECTION_INVALID")):
        bad = await validate_plan(_plan(relationship), workspace_path=ws)
        assert not bad.valid and code in bad.reason_codes
    both = await validate_plan(_plan(_rel(selected=RED), _rel(selected=BLUE)), workspace_path=ws)
    assert not both.valid and "INTEGRATION_DECISION_CONFLICT" in both.reason_codes


def test_the_repair_prompt_asks_for_a_selection_not_for_a_developer_choice():
    from kriya.workflow.planner_repair import build_structured_plan_repair_prompt

    prompt = build_structured_plan_repair_prompt(
        GOAL, "{}", ["integration relationship 'd1' declares alternatives but selects none"],
        ["INTEGRATION_DECISION_SELECTION_MISSING"], 1)
    assert "selected_alternative must be exactly ONE" in prompt and "no subtask has to choose again" in prompt


def test_the_choice_survives_the_planner_json_and_the_same_file_owner_merge():
    from kriya.workflow.plan_normalization import coalesce_same_file_owners

    parsed = IntegrationRelationship.model_validate_json(json.dumps({
        "id": "d1", "kind": "uses", "producer_subtask_ids": ["s1"], "consumer_subtask_ids": ["s2"],
        "relationship_statement": "x", "alternatives": [f" {RED} ", BLUE], "selected_alternative": f" {RED} "}))
    assert parsed.alternatives == [RED, BLUE] and parsed.selected_alternative == RED         # trimmed, otherwise as written
    two_owners = EngineeringPlan(plan_id="p", kind=ChangeKind.TASK,
                                 subtasks=[_unit("s1", "shared.py"), _unit("s2", "shared.py"), _unit("s3", "later.py", ["s2"])],
                                 integration_relationships=[_rel(producers=("s2",), consumers=("s3",))])
    merged, records = coalesce_same_file_owners(two_owners)
    assert records and merged.integration_relationships[0].producer_subtask_ids == ["s1"]   # s2 was absorbed into s1
    assert merged.integration_relationships[0].selected_alternative == RED
    assert merged.integration_relationships[0].alternatives == [RED, BLUE]


# -- every participant is handed the same authoritative contract ----------------------------------------------------------

@pytest.mark.parametrize("choice", [RED, BLUE])   # either alternative: a renderer that always shows the first one fails
def test_every_participating_developer_context_carries_the_same_selection_and_a_non_participant_none(choice):
    plan = _plan(_rel(selected=choice), extra_units=[_unit("s9", "unrelated.py")])
    contexts = {sid: build_subtask_semantic_context(plan, plan.subtask_by_id(sid)) for sid in ("s1", "s2", "s9")}
    for sid in ("s1", "s2"):
        assert integration_contracts_for(plan, sid)[0]["decision"] == {"alternatives": [RED, BLUE], "selected": choice}
        assert '"selected": "' + choice in contexts[sid] and INTEGRATION_CONTRACT_AUTHORITY in contexts[sid]
    assert contexts["s1"].count(choice) == contexts["s2"].count(choice) and "integration_contracts" not in contexts["s9"]
    assert integration_contracts_for(plan, "s1")[0]["this_subtask_is"] == "producer"
    assert integration_contracts_for(plan, "s2")[0]["this_subtask_is"] == "consumer"


def test_a_plan_with_no_relationship_renders_exactly_the_context_it_always_did():
    context = build_subtask_semantic_context(_plan(), _plan().subtask_by_id("s2"))
    assert "integration_contract" not in context


def test_the_context_distinguishes_the_authoritative_plan_contract_from_advisory_skill_text():
    text = INTEGRATION_CONTRACT_AUTHORITY
    assert "AUTHORITATIVE" in text and "advisory" in text and "exactly the selected alternative and no other" in text
    assert "does not choose between them" in text and "Do not infer the choice from another file's content" in text


# -- end to end through the real enforce controller: planner request and Developer prompts --------------------------------

def _route():
    return EngineeringRoute(kind=ChangeKind.TASK, impact=ImpactVector(), initial_risk_class=RiskClass.LOW,
                            current_risk_class=RiskClass.LOW, max_observed_risk_class=RiskClass.LOW,
                            execution_weight=ExecutionWeight.LIGHT)


async def _run_enforce(tmp_path, skills_root, plan, goal=GOAL, *, validations=None):
    cfg = _config(tmp_path, skills_root)
    engine = strict_engine(cfg)
    engine.engineering_triage.classify = AsyncMock(return_value=_route())
    engine.planner.run = AsyncMock(return_value="plan")
    engine.planner.max_output_tokens = None
    calls = []

    async def developer(**kwargs):
        calls.append(kwargs)
        files = list(kwargs["allowed_write_relpaths"] or [])
        for rel in files:
            path = os.path.join(kwargs["workspace_path"], rel)
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(f"# {rel}\n")
        evidence = [{"type": vm["type"], "tool_name": vm.get("tool_name"), "description": vm["description"], "passed": True}
                    for vm in kwargs.get("required_verification") or []]
        return {"status": "success", "quality_gates_passed": True, "files": files, "verification_results": evidence}

    engine.run_generation_workflow = developer
    with patch(f"{C}.parse_planner_structured_output", return_value=(MagicMock(), None)), \
            patch(f"{C}.build_engineering_plan_from_planner_output", return_value=plan), \
            patch(f"{C}.validate_plan", new=AsyncMock(side_effect=validations) if validations
                  else AsyncMock(return_value=PlanValidationResult(valid=True))), \
            patch(f"{C}.create_git_worktree", side_effect=lambda workspace: workspace):
        await WorkflowController(engine).execute(goal, str(tmp_path), migration_mode="enforce")
    requests = [call.args[0] for call in engine.planner.run.await_args_list]
    return (requests if validations else requests[0]), calls


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_the_enforce_planner_request_carries_the_skill_and_both_developers_carry_one_selection(tmp_path, skills_root):
    request, calls = await _run_enforce(tmp_path, skills_root, _plan(_rel(), extra_units=[_unit("s9", "unrelated.py")]))
    assert MENU_RULE in request and "gizmo" not in request
    by_subtask = {call["current_subtask_id"]: call["supplementary_context"] for call in calls}
    assert set(by_subtask) == {"s1", "s2", "s9"}
    for sid in ("s1", "s2"):
        assert '"selected": "' + RED in by_subtask[sid] and INTEGRATION_CONTRACT_AUTHORITY in by_subtask[sid]
    assert "integration_contracts" not in by_subtask["s9"]


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_the_enforce_planner_request_is_built_from_the_runs_one_skill_engine_not_a_second_one(tmp_path, skills_root):
    """ARCH-TESTS-FOUR-FAIL-AT-77603DB-SKILL-CONVENTIONS-CYCLE-001 (argument preservation of the EnforceDeps transport): the
    enforce loop hands the run's shared engine (``skill_engine_override``, loaded once by the controller) to
    ``planning_skill_constraints``. Called without it, the function builds a SECOND engine from the configuration - a second
    registry load that would not see what the run holds."""
    real = SkillEngine.from_config
    built = []

    def counting(*args, **kwargs):
        built.append(real(*args, **kwargs))
        return built[-1]

    with patch.object(SkillEngine, "from_config", side_effect=counting):
        request, _ = await _run_enforce(tmp_path, skills_root, _plan())
    assert MENU_RULE in request   # the Planner block was built (so the callable ran)
    assert len(built) == 1        # ... from the controller's one engine


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_goal_with_no_applicable_skill_sends_the_planner_request_it_always_sent(tmp_path, skills_root):
    request, _ = await _run_enforce(tmp_path, skills_root, _plan(), goal="Write a haiku generator")
    assert "Applicable skill constraints" not in request and MENU_RULE not in request


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_single_subtask_choice_needs_no_relationship_and_the_run_is_unaffected(tmp_path, skills_root):
    """A choice used inside one subtask is not forced into the integration seam: the plan has no relationship at all."""
    _, calls = await _run_enforce(tmp_path, skills_root, _plan())
    assert {call["current_subtask_id"] for call in calls} == {"s1", "s2"}
    assert all("integration_contracts" not in call["supplementary_context"] for call in calls)


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_the_repair_round_is_given_the_same_skills_so_a_selection_is_corrected_against_the_menu(tmp_path, skills_root):
    invalid = PlanValidationResult(
        valid=False, errors=["integration relationship 'd1' declares alternatives but selects none"],
        reason_codes=["INTEGRATION_DECISION_SELECTION_MISSING"])
    requests, _ = await _run_enforce(tmp_path, skills_root, _plan(_rel()), validations=[invalid, PlanValidationResult(valid=True)])
    assert len(requests) == 2
    assert all(MENU_RULE in request and "gizmo" not in request for request in requests)           # first request AND the repair
    assert "selected_alternative must be exactly ONE" in requests[1]                              # and it asks for the selection
