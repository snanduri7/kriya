"""SKILL-INTEGRATION-DECISION-AUTHORITY-001 (owner rulings 2026-11-18 after Stage B runs 8 and 9).

MEASURED in both runs (Planner request sha256 a17894de..., identical): an applicable skill offered two mutually exclusive start-up
mechanisms, the Planner was told three times to choose once, its answer carried no integration relationship, plan validation accepted the
plan, and the two subtasks each chose alone - incompatibly. SHARED-DECISION-AUTHORITY-001 could represent, validate and transport a decision
the Planner DECLARED; nothing machine-readable said a decision was REQUIRED.

The contract: a skill's ``integration_decisions`` metadata declares that a decision exists, its alternative ids (each with the skill's own
summary of what it means) and the planned artifacts that take part. The Planner only selects. Plan validation derives the participating
subtasks itself (declaration x ``planned_files``) and, when there are two or more, requires exactly one relationship with the canonical
decision id, exactly the declared alternative ids and exactly those subtasks - before any Developer runs. Unusable metadata stops the run
before the Planner is asked. A skill that declares nothing behaves as it always did.
"""
import inspect
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import test_shared_decision_authority_001 as sda
import yaml
from _strict_doubles import strict_engine

from kriya.skills import skill as skill_module
from kriya.skills.skill import SkillEngine, parse_integration_decisions
from kriya.workflow import enforce_loop, plan_validation, planner_repair, skill_conventions, skill_extraction
from kriya.workflow.plan_schema import EngineeringPlan
from kriya.workflow.plan_validation import (
    PlanValidationResult,
    decision_participant_subtasks,
    integration_decision_findings,
    required_integration_decision_findings,
    validate_plan,
)
from kriya.workflow.planning_request import integration_contracts_for
from kriya.workflow.skill_extraction import planning_skill_constraints
from kriya.workflow.triage import ChangeKind
from kriya.workflow.workflow_controller import WorkflowController

C = sda.C
GOAL = sda.GOAL                                   # "Build a codecwire exporter and a codecwire importer"
DECISION = "codecwire:wire-mode"                  # <skill name>:<local id>
RED, BLUE = "mode-red", "mode-blue"
RED_SUMMARY = "the exporter writes frames and the importer reads frames with the framed reader"
BLUE_SUMMARY = "the exporter writes lines and the importer reads lines with the line reader"
WIRE_MODE = {"id": "wire-mode",
             "alternatives": [{"id": RED, "summary": RED_SUMMARY}, {"id": BLUE, "summary": BLUE_SUMMARY}],
             "participants": ["exporter.py", "importer.py"]}
REQUIRED = [{"id": DECISION, "alternatives": [RED, BLUE], "participants": ["exporter.py", "importer.py"], "skill": "codecwire"}]


def _skill(root, name, tags, *, decisions=None, rules=(sda.MENU_RULE,), folder=None):
    path = os.path.join(root, folder or name)
    os.makedirs(path, exist_ok=True)
    meta = {"name": name, "description": "test skill", "category": "General", "verified": True, "tags": list(tags)}
    if decisions is not None:
        meta["integration_decisions"] = decisions
    with open(os.path.join(path, "skill.yaml"), "w", encoding="utf-8") as handle:
        yaml.safe_dump(meta, handle)
    with open(os.path.join(path, "rules.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(rules) + "\n")


@pytest.fixture
def skills_root(tmp_path):
    root = str(tmp_path.parent / f"{tmp_path.name}-skills")      # outside the workspace, as the operator's skills directory is
    _skill(root, "codecwire", ["codecwire"], decisions=[WIRE_MODE])
    return root


def _rel(rel_id=DECISION, *, alternatives=(RED, BLUE), selected=RED, producers=("s1",), consumers=("s2",)):
    return sda._rel(rel_id, alternatives=alternatives, selected=selected, producers=producers, consumers=consumers)


def _codes(plan, required=REQUIRED):
    return [code for code, _ in required_integration_decision_findings(plan, required)]


# ------------------------------------------------------------------------------------------------- the skill owns the declaration

def test_a_wellformed_declaration_parses_and_every_malformed_one_is_refused_whole():
    decisions, error = parse_integration_decisions([WIRE_MODE])
    assert error is None and [d.id for d in decisions] == ["wire-mode"]
    assert [(a.id, a.summary) for a in decisions[0].alternatives] == [(RED, RED_SUMMARY), (BLUE, BLUE_SUMMARY)]
    assert parse_integration_decisions(None) == ([], None)

    def broken(**changes):
        return [WIRE_MODE, {**WIRE_MODE, "id": "second", **changes}]       # a good entry first: nothing of it survives either

    for case in (
        "not a list",
        [WIRE_MODE, WIRE_MODE],                                             # one id declared twice
        broken(id=""), broken(id="a:b"), broken(id=" padded "),
        broken(alternatives=[{"id": RED, "summary": RED_SUMMARY}]),         # fewer than two
        broken(alternatives=[{"id": RED, "summary": RED_SUMMARY}, {"id": RED, "summary": BLUE_SUMMARY}]),
        broken(alternatives=[{"id": RED, "summary": ""}, {"id": BLUE, "summary": BLUE_SUMMARY}]),
        broken(alternatives=[{"id": "", "summary": RED_SUMMARY}, {"id": BLUE, "summary": BLUE_SUMMARY}]),
        broken(alternatives=[RED, BLUE]),                                   # ids without summaries
        broken(participants=[]), broken(participants=[""]), broken(participants=["/abs/x.py"]),
        broken(participants=["a/../b.py"]), broken(participants=["a\\b.py"]), broken(participants=[7]),
        [{**WIRE_MODE, "extra": 1}],
    ):
        decisions, error = parse_integration_decisions(case)
        assert decisions == [] and error, case


def test_the_loader_reads_the_declaration_only_where_skill_metadata_is_believed(tmp_path, skills_root):
    cfg = sda._config(tmp_path, skills_root)
    engine = SkillEngine.from_config(cfg, workspace_path=sda._workspace(tmp_path))
    engine.discover_and_load()
    loaded = engine.get_skill("codecwire")
    assert [d.id for d in loaded.integration_decisions] == ["wire-mode"] and loaded.integration_decisions_error is None

    inside = tmp_path / "ws" / "skills"                                      # a skill INSIDE the workspace: project-supplied bytes
    _skill(str(inside), "codecwire", ["codecwire"], decisions=[WIRE_MODE])
    project = SkillEngine.from_config(sda._config(tmp_path, str(inside)), workspace_path=sda._workspace(tmp_path))
    project.discover_and_load()
    assert project.get_skill("codecwire").integration_decisions == []       # no new trust level
    assert project.get_skill("codecwire").integration_decisions_error is None


def test_a_malformed_declaration_keeps_the_skill_and_marks_it_never_drops_it(tmp_path):
    root = str(tmp_path.parent / f"{tmp_path.name}-bad")
    _skill(root, "codecwire", ["codecwire"], decisions=[{**WIRE_MODE, "participants": []}])
    engine = SkillEngine.from_config(sda._config(tmp_path, root), workspace_path=sda._workspace(tmp_path))
    engine.discover_and_load()
    loaded = engine.get_skill("codecwire")
    assert loaded.rules == [sda.MENU_RULE]                                   # the skill is still there
    assert loaded.integration_decisions == [] and "participants" in loaded.integration_decisions_error

    found = planning_skill_constraints(sda._config(tmp_path, root), sda._workspace(tmp_path), GOAL)
    assert found.integration_decisions == () and len(found.authority_errors) == 1 and "codecwire" in found.authority_errors[0]


# ------------------------------------------------------------------------------------------------- one rendering, one selection

def test_the_rendered_skill_gives_every_reader_the_ids_and_what_they_mean(tmp_path, skills_root):
    cfg = sda._config(tmp_path, skills_root)
    engine = SkillEngine.from_config(cfg, workspace_path=sda._workspace(tmp_path))
    engine.discover_and_load()
    developer_side = skill_conventions.render_skill_conventions(engine.get_skill("codecwire"), engine, skill_extraction._split_rules_by_verification)
    found = planning_skill_constraints(cfg, sda._workspace(tmp_path), GOAL, skill_engine=engine)
    block, names = found

    assert block == skill_conventions.PLANNING_SKILL_CONSTRAINTS_HEADER + developer_side      # the Planner reads the Developer's rendering
    for text in (f"decision id: {DECISION}", f"alternative id: {RED} = {RED_SUMMARY}", f"alternative id: {BLUE} = {BLUE_SUMMARY}",
                 "participants: exporter.py, importer.py", sda.MENU_RULE):
        assert text in developer_side
    assert names == ["codecwire"] and found == (block, names)                                  # still the pair it always was
    assert list(found.integration_decisions) == REQUIRED and found.authority_errors == ()      # from the same selection


def test_a_skill_that_declares_nothing_is_rendered_and_selected_exactly_as_before(tmp_path):
    root = str(tmp_path.parent / f"{tmp_path.name}-plain")
    _skill(root, "codecwire", ["codecwire"])
    cfg = sda._config(tmp_path, root)
    engine = SkillEngine.from_config(cfg, workspace_path=sda._workspace(tmp_path))
    engine.discover_and_load()
    plain = engine.get_skill("codecwire")

    assert skill_conventions.render_integration_decisions(plain) == ""
    assert "Integration decisions" not in skill_conventions.render_skill_conventions(plain, engine, skill_extraction._split_rules_by_verification)
    found = planning_skill_constraints(cfg, sda._workspace(tmp_path), GOAL, skill_engine=engine)
    assert found.integration_decisions == () and found.authority_errors == ()
    assert "integration_decisions" not in json.dumps(engine.manifest_for(["codecwire"]))       # (the manifest holds a hash only)
    nothing = planning_skill_constraints(cfg, sda._workspace(tmp_path), "Write a haiku")
    assert nothing == ("", []) and nothing.integration_decisions == ()


def test_declaring_a_decision_changes_the_skills_manifest_identity(tmp_path, skills_root):
    plain_root = str(tmp_path.parent / f"{tmp_path.name}-plain")
    _skill(plain_root, "codecwire", ["codecwire"])

    def content_hash(root):
        engine = SkillEngine.from_config(sda._config(tmp_path, root), workspace_path=sda._workspace(tmp_path))
        engine.discover_and_load()
        return engine.manifest_for(["codecwire"])[0]["content_hash"]

    assert content_hash(skills_root) != content_hash(plain_root)


def test_two_skills_keep_their_decisions_apart_and_one_id_declared_twice_is_an_authority_error(tmp_path, skills_root):
    _skill(skills_root, "importkit", ["importer"], decisions=[{**WIRE_MODE, "participants": ["importer.py", "unrelated.py"]}],
           rules=("Importers validate their input.",))
    found = planning_skill_constraints(sda._config(tmp_path, skills_root), sda._workspace(tmp_path), GOAL)
    assert sorted(d["id"] for d in found.integration_decisions) == ["codecwire:wire-mode", "importkit:wire-mode"]
    assert found.authority_errors == ()

    _skill(skills_root, "codecwire", ["codecwire"], decisions=[WIRE_MODE], folder="codecwire-copy")   # a second folder, the same name
    again = planning_skill_constraints(sda._config(tmp_path, skills_root), sda._workspace(tmp_path), GOAL)
    assert any(DECISION in error and "more than one" in error for error in again.authority_errors)


# ------------------------------------------------------------------------------------------------- validation owns "is it recorded"

def test_a_required_decision_recorded_once_over_exactly_its_participants_is_accepted():
    assert _codes(sda._plan(_rel())) == []
    assert _codes(sda._plan(_rel(selected=BLUE, producers=("s2",), consumers=("s1",)))) == []


def test_an_omitted_required_decision_is_refused():
    assert _codes(sda._plan()) == ["INTEGRATION_DECISION_REQUIRED_MISSING"]
    assert _codes(sda._plan(_rel("d1"))) == ["INTEGRATION_DECISION_REQUIRED_MISSING"]          # a decision under another id is not it
    message = required_integration_decision_findings(sda._plan(), REQUIRED)[0][1]
    assert DECISION in message and RED in message and BLUE in message and "'s1', 's2'" in message


def test_alternatives_that_are_not_exactly_the_declared_ids_are_refused():
    for alternatives in ((RED, BLUE, "mode-green"), (RED, "mode-green"), (sda.RED, sda.BLUE), ()):
        assert _codes(sda._plan(_rel(alternatives=alternatives, selected=RED))) == ["INTEGRATION_DECISION_SELECTION_INVALID"], alternatives
    assert _codes(sda._plan(_rel(), _rel())) == ["INTEGRATION_DECISION_SELECTION_INVALID"]     # recorded twice


def test_a_relationship_over_a_subset_or_a_superset_of_the_derived_participants_is_refused():
    with_third = sda._plan(_rel(), extra_units=[sda._unit("s3", "pkg/importer.py")])
    assert decision_participant_subtasks(with_third, ["exporter.py", "*importer.py"]) == ["s1", "s2", "s3"]
    wide = [{**REQUIRED[0], "participants": ["exporter.py", "*importer.py"]}]
    assert _codes(with_third, wide) == ["INTEGRATION_DECISION_REQUIRED_MISSING"]               # the Planner named s1 and s2 only
    named_all = sda._plan(_rel(consumers=("s2", "s3")), extra_units=[sda._unit("s3", "pkg/importer.py")])
    assert _codes(named_all, wide) == []
    extra = sda._plan(_rel(consumers=("s2", "s9")), extra_units=[sda._unit("s9", "unrelated.py")])
    assert _codes(extra) == ["INTEGRATION_DECISION_REQUIRED_MISSING"]                          # s9 plans no participating file


def test_a_decision_whose_participants_reach_one_subtask_requires_nothing():
    one = [{**REQUIRED[0], "participants": ["exporter.py", "missing/*.py"]}]
    assert decision_participant_subtasks(sda._plan(), one[0]["participants"]) == ["s1"]
    assert _codes(sda._plan(), one) == []
    assert _codes(sda._plan(), None) == [] and _codes(sda._plan(), ()) == []


def test_participants_are_matched_on_planned_paths_only_case_sensitively_and_star_crosses_directories():
    plan = sda._plan(extra_units=[sda._unit("s3", "src/main/java/com/example/App.java"), sda._unit("s4", "src/main/resources/app.xml")])
    assert decision_participant_subtasks(plan, ["*App.java"]) == ["s3"]
    assert decision_participant_subtasks(plan, ["*app.java"]) == []                           # case-sensitive
    assert decision_participant_subtasks(plan, ["src/main/resources/*.xml"]) == ["s4"]
    assert decision_participant_subtasks(plan, ["src/*.java", "exporter.py"]) == ["s1", "s3"]  # '*' also matches '/'
    assert decision_participant_subtasks(plan, ["App.java"]) == []                            # the whole path, not a suffix


def test_two_decisions_of_two_skills_are_validated_independently():
    both = [REQUIRED[0], {**REQUIRED[0], "id": "importkit:wire-mode", "skill": "importkit"}]
    assert _codes(sda._plan(_rel()), both) == ["INTEGRATION_DECISION_REQUIRED_MISSING"]        # the second one is still owed
    assert _codes(sda._plan(_rel(), _rel("importkit:wire-mode", selected=BLUE)), both) == []


@pytest.mark.asyncio
async def test_validate_plan_refuses_the_plan_and_accepts_it_once_the_decision_is_recorded(tmp_path):
    ws = sda._workspace(tmp_path)
    missing = await validate_plan(sda._plan(), workspace_path=ws, required_integration_decisions=REQUIRED)
    assert not missing.valid and "INTEGRATION_DECISION_REQUIRED_MISSING" in missing.reason_codes
    recorded = await validate_plan(sda._plan(_rel()), workspace_path=ws, required_integration_decisions=REQUIRED)
    assert recorded.valid, recorded.errors
    outside = await validate_plan(sda._plan(_rel(selected="mode-green")), workspace_path=ws, required_integration_decisions=REQUIRED)
    assert not outside.valid and "INTEGRATION_DECISION_SELECTION_INVALID" in outside.reason_codes       # the existing selection check
    unaffected = await validate_plan(sda._plan(), workspace_path=ws)                                  # no declaration: as before
    assert unaffected.valid, unaffected.errors


def test_the_selected_id_reaches_every_participant_unchanged():
    plan = sda._plan(_rel(selected=BLUE), extra_units=[sda._unit("s9", "unrelated.py")])
    rebuilt = EngineeringPlan.model_validate(json.loads(plan.model_dump_json()))                   # the persisted plan
    for subtask_id in ("s1", "s2"):
        (contract,) = integration_contracts_for(rebuilt, subtask_id)
        assert contract["id"] == DECISION and contract["decision"] == {"alternatives": [RED, BLUE], "selected": BLUE}
    assert integration_contracts_for(rebuilt, "s9") == []


# ------------------------------------------------------------------------------------------------- the enforce run

async def _enforce(tmp_path, skills_root, plans):
    """The real controller and enforce loop. The Planner's parsed answers are ``plans`` in order; validation is the two real decision
    checks only (the rest of validate_plan is not this contract); the Developer is scripted as in the shared-decision file."""
    engine = strict_engine(sda._config(tmp_path, skills_root))
    engine.engineering_triage.classify = AsyncMock(return_value=sda._route())
    engine.planner.run = AsyncMock(return_value="plan")
    engine.planner.max_output_tokens = None
    developer_calls, validations = [], []

    async def developer(**kwargs):
        developer_calls.append(kwargs)
        files = list(kwargs["allowed_write_relpaths"] or [])
        for rel in files:
            path = os.path.join(kwargs["workspace_path"], rel)
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(f"# {rel}\n")
        evidence = [{"type": vm["type"], "tool_name": vm.get("tool_name"), "description": vm["description"], "passed": True}
                    for vm in kwargs.get("required_verification") or []]
        return {"status": "success", "quality_gates_passed": True, "files": files, "verification_results": evidence}

    async def decision_validation(plan, **kwargs):
        found = (integration_decision_findings(plan.integration_relationships)
                 + required_integration_decision_findings(plan, kwargs.get("required_integration_decisions")))
        validations.append({"required": kwargs.get("required_integration_decisions"), "developer_calls_so_far": len(developer_calls),
                            "codes": [code for code, _ in found]})
        return PlanValidationResult(valid=not found, errors=[message for _, message in found], reason_codes=[code for code, _ in found])

    engine.run_generation_workflow = developer
    with patch(f"{C}.parse_planner_structured_output", return_value=(MagicMock(), None)), \
            patch(f"{C}.build_engineering_plan_from_planner_output", side_effect=list(plans)), \
            patch(f"{C}.validate_plan", new=decision_validation), \
            patch(f"{C}.create_git_worktree", side_effect=lambda workspace: workspace):
        result = await WorkflowController(engine).execute(GOAL, str(tmp_path), migration_mode="enforce")
    requests = [call.args[0] for call in engine.planner.run.await_args_list]
    return result, requests, developer_calls, validations


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_enforce_accepts_a_recorded_decision_and_both_developers_carry_the_same_selected_id(tmp_path, skills_root):
    _result, requests, developer_calls, validations = await _enforce(
        tmp_path, skills_root, [sda._plan(_rel(), extra_units=[sda._unit("s9", "unrelated.py")])])

    assert len(requests) == 1 and f"decision id: {DECISION}" in requests[0] and f"alternative id: {RED} = {RED_SUMMARY}" in requests[0]
    assert validations[0]["required"] == tuple(REQUIRED) and validations[0]["codes"] == []
    context = {call["current_subtask_id"]: call["supplementary_context"] for call in developer_calls}
    assert set(context) == {"s1", "s2", "s9"}
    for subtask_id in ("s1", "s2"):
        assert f'"id": "{DECISION}"' in context[subtask_id] and f'"selected": "{RED}"' in context[subtask_id]
    assert "integration_contracts" not in context["s9"]


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_enforce_refuses_an_omitted_decision_before_any_developer_and_the_planner_repairs_it(tmp_path, skills_root):
    _result, requests, developer_calls, validations = await _enforce(tmp_path, skills_root, [sda._plan(), sda._plan(_rel(selected=BLUE))])

    assert validations[0]["codes"] == ["INTEGRATION_DECISION_REQUIRED_MISSING"] and validations[0]["developer_calls_so_far"] == 0
    assert len(requests) == 2                                                    # the existing repair round, nothing new
    assert DECISION in requests[1] and "is exactly the decision id given in the error" in requests[1]
    assert f"decision id: {DECISION}" in requests[1]                             # the repair round is given the same skill text
    assert validations[1]["codes"] == [] and validations[1]["developer_calls_so_far"] == 0
    context = {call["current_subtask_id"]: call["supplementary_context"] for call in developer_calls}
    assert set(context) == {"s1", "s2"} and all(f'"selected": "{BLUE}"' in text for text in context.values())


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_enforce_never_executes_a_plan_whose_decision_stays_omitted(tmp_path, skills_root):
    result, requests, developer_calls, validations = await _enforce(tmp_path, skills_root, [sda._plan()] * 6)

    assert developer_calls == [] and len(requests) >= 2
    assert all(entry["codes"] == ["INTEGRATION_DECISION_REQUIRED_MISSING"] for entry in validations)
    assert "INTEGRATION_DECISION_REQUIRED_MISSING" in json.dumps(getattr(result, "legacy_result", result), default=str)


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_enforce_stops_before_the_planner_when_an_applicable_skills_decision_metadata_is_unusable(tmp_path):
    root = str(tmp_path.parent / f"{tmp_path.name}-bad")
    _skill(root, "codecwire", ["codecwire"], decisions=[{**WIRE_MODE, "alternatives": [{"id": RED, "summary": RED_SUMMARY}]}])
    result, requests, developer_calls, validations = await _enforce(tmp_path, root, [sda._plan(_rel())])

    assert requests == [] and validations == [] and developer_calls == []        # no Planner answer could repair this
    assert enforce_loop.SKILL_INTEGRATION_DECISIONS_INVALID in json.dumps(getattr(result, "legacy_result", result), default=str)


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_enforce_passes_validation_what_it_always_passed_when_no_skill_declares_a_decision(tmp_path):
    root = str(tmp_path.parent / f"{tmp_path.name}-plain")
    _skill(root, "codecwire", ["codecwire"])
    _result, requests, developer_calls, validations = await _enforce(tmp_path, root, [sda._plan()])

    assert len(requests) == 1 and "Integration decisions" not in requests[0]
    assert validations[0]["required"] is None                                    # the keyword is not even passed
    assert {call["current_subtask_id"] for call in developer_calls} == {"s1", "s2"}


def test_every_plan_validation_of_the_enforce_loop_receives_the_declared_decisions():
    """A recovery revision is validated by the same call shape: none of the loop's validate_plan calls may omit the requirements, so a
    revised plan cannot drop a required decision. (The revision paths themselves are not driven here.)"""
    source = inspect.getsource(enforce_loop)
    calls = source.split("await deps.validate_plan(")[1:]
    assert len(calls) == 4
    for call in calls:
        assert "**decision_requirements," in call[:400]


def test_nothing_in_the_owning_code_names_a_product_or_a_bean():
    for owner in (parse_integration_decisions, skill_module.SkillIntegrationDecision, skill_conventions.render_integration_decisions,
                  skill_conventions.canonical_decision_id, skill_extraction.planning_skill_constraints,
                  skill_extraction.PlanningSkillConstraints, plan_validation.decision_participant_subtasks,
                  plan_validation.required_integration_decision_findings):
        text = inspect.getsource(owner).lower()
        assert "ignite" not in text and "spring" not in text and "bean" not in text, owner
    repair = inspect.getsource(planner_repair)
    assert "ignite" not in repair[repair.index("INTEGRATION_DECISION_REQUIRED_MISSING"):][:900].lower()


# ------------------------------------------------------------------------------------------------- the shipped skill that needed it

def test_the_shipped_skill_that_runs_8_and_9_used_declares_its_start_up_decision_over_the_two_units_that_disagreed():
    folder = Path(__file__).resolve().parents[1] / "skills" / "ignite-java17"
    decisions, error = parse_integration_decisions(yaml.safe_load((folder / "skill.yaml").read_text(encoding="utf-8")).get("integration_decisions"))
    assert error is None and [d.id for d in decisions] == ["spring-xml-startup-mechanism"]
    assert [a.id for a in decisions[0].alternatives] == ["direct-ignition-start", "spring-managed-ignite-bean"]

    def unit(sid, path, depends_on=()):
        return sda._unit(sid, path, depends_on)

    run9 = EngineeringPlan(plan_id="run9", kind=ChangeKind.TASK, subtasks=[
        unit("s1", "pom.xml"), unit("s2", "src/main/java/com/example/Protocol.java", ["s1"]),
        unit("s3", "src/main/java/com/example/ProtocolParser.java", ["s2"]), unit("s4", "src/main/resources/ignite-config.xml", ["s1"]),
        unit("s5", "src/main/java/com/example/App.java", ["s3", "s4"]),
        unit("s6", "src/test/java/com/example/ProtocolParserTest.java", ["s3"])])
    assert decision_participant_subtasks(run9, decisions[0].participants) == ["s4", "s5"]
    required = [{"id": "ignite-java17:spring-xml-startup-mechanism", "alternatives": [a.id for a in decisions[0].alternatives],
                 "participants": decisions[0].participants, "skill": "ignite-java17"}]
    assert _codes(run9, required) == ["INTEGRATION_DECISION_REQUIRED_MISSING"]                 # the plan both runs were given
