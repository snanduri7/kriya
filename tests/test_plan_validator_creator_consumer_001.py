"""PLAN-VALIDATOR-CREATOR-INFERRED-AS-CONSUMER-001 (ACTIVE P1, owner ruling 2026-11-10).

MEASURED (replacement Scenario-A run 20261110T060714-704ab94f, FAIL - PLANNING_ERROR / KRIYA PLAN-VALIDATION DEFECT): `plan_validation._planned_artifact_prerequisite_evidence` inferred the subtask that ORIGINATES
an artifact as a CONSUMER of a later subtask that merely evolves the same artifact (s1 plans pom.xml first, s7 plans pom.xml later and depends on s1), emitted a "missing requires + depends_on edge" for s1 <- s7, and
the repair text then told the planner `add s7 to s1.depends_on` - an edge that cannot exist without breaking the order the plan already declares.

Invariant: artifact availability flows forward. A later owner of the SAME artifact path (downstream through depends_on, the existing sequential-ownership authority) is never a prerequisite provider for the
earlier owner. The controller canonicalizes every planned action against the repository BEFORE validation (a greenfield turns every `modify` into `create`), so the live shape is create/create ordered by depends_on:
the primary test goes through that exact preparation, not through hand-written actions. What must NOT change: a genuine consumer-before-provider, a missing/ambiguous provider and a real cycle stay failures, and a
reverse edge is never a reason to hide a finding (a prerequisite whose edge would close a cycle becomes a typed conflict finding, never an add-edge instruction).

The three sealed planner responses of that run are in tests/fixtures/plan_replay_scenario_a_20261110/ (sha256 of each in README.md); the replay is model-free.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from kriya.agents.contracts import parse_planner_structured_output
from kriya.workflow.plan_normalization import coalesce_same_file_owners
from kriya.workflow.plan_schema import (
    EngineeringPlan,
    ExecutionMethod,
    FileAction,
    PlannedFile,
    Subtask,
    build_engineering_plan_from_planner_output,
)
from kriya.workflow.plan_validation import (
    _planned_artifact_prerequisite_evidence,
    canonicalize_planned_file_actions,
    validate_plan,
)
from kriya.workflow.planner_repair import PLANNER_VALIDATION_FAILURE_CODES, build_structured_plan_repair_prompt
from kriya.workflow.triage import ChangeKind

FIXTURES = Path(__file__).parent / "fixtures" / "plan_replay_scenario_a_20261110"
CONFLICT = "SEMANTIC_DEPENDENCY_EDGE_CONFLICT"
SHA256_PREFIXES = {0: "6b83560b9e2531d2", 1: "1652ec2739423341", 2: "fd12cac1a634f84d"}


def _live_prepared_plan(attempt: int, workspace: str):
    """The planner's recorded response, prepared exactly as workflow/enforce_loop.py prepares a candidate before validate_plan (parse, build, canonicalize against the workspace, coalesce)."""
    text = (FIXTURES / f"planner_attempt{attempt}.json").read_text(encoding="utf-8")
    assert hashlib.sha256(text.encode()).hexdigest().startswith(SHA256_PREFIXES[attempt]), "the sealed fixture was altered"
    output, issue = parse_planner_structured_output(text)
    assert output is not None, issue
    raw = build_engineering_plan_from_planner_output(output, plan_id="replay", kind=ChangeKind.TASK)
    plan, _ = canonicalize_planned_file_actions(raw, workspace)
    plan, _ = coalesce_same_file_owners(plan)
    return text, plan


def _records(plan):
    return _planned_artifact_prerequisite_evidence(plan.subtasks)


def _pair(records):
    return {(r["consumer_subtask"], r["provider_subtask"]) for r in records}


# ------------------------------------------------------------------------------------------------ the exact regression (sealed replacement-run plans)

@pytest.mark.asyncio
async def test_the_sealed_attempt1_plan_no_longer_infers_the_creator_as_a_consumer_of_its_later_owner(tmp_path):
    _, plan = _live_prepared_plan(1, str(tmp_path))
    pom = {st.id: [(f.path, f.action.value) for f in st.planned_files if f.path == "pom.xml"] for st in plan.subtasks}
    assert pom["s1"] == [("pom.xml", "create")] and pom["s7"] == [("pom.xml", "create")], "the live shape: canonicalization makes BOTH create; only depends_on orders them"
    assert "s1" in {d for st in plan.subtasks if st.id == "s7" for d in st.depends_on}
    records = _records(plan)
    assert _pair(records) == {("s7", "s1")}, "only the valid creator->later-owner direction remains; s1 is never the consumer of s7"
    (record,) = records
    assert record["missing_requires_edge"] is False and record["missing_depends_on_edge"] is False and "depends_on_edge_creates_cycle" not in record
    result = await validate_plan(plan, workspace_path=str(tmp_path))
    assert result.valid is False, "the real planner faults remain failures"
    assert set(result.reason_codes) == {"AMBIGUOUS_SUBTASK_CAPABILITY_PROVIDER"}, result.reason_codes
    assert not any("must depend_on provider 's7'" in error or "'s1' file 'pom.xml' must require" in error for error in result.errors)


@pytest.mark.asyncio
async def test_the_sealed_attempt1_repair_prompt_no_longer_tells_the_planner_to_add_the_cycle_edge(tmp_path):
    text, plan = _live_prepared_plan(1, str(tmp_path))
    result = await validate_plan(plan, workspace_path=str(tmp_path))
    prompt = build_structured_plan_repair_prompt("goal", text, result.errors, result.reason_codes, 2, validation_evidence=result.evidence)
    assert "add s7 to s1.depends_on" not in prompt and "add runnable-maven-config to s1.requires" not in prompt
    assert "Apply these exact planned-prerequisite corrections" not in prompt, "nothing left to correct in the prerequisite family"
    assert "provided by more than one subtask" in prompt, "the ambiguous-provider guidance the plan actually needs is still given"


@pytest.mark.asyncio
async def test_the_sealed_attempt2_plan_replay_shows_exactly_what_changed(tmp_path):
    """Attempt 2 was the planner FOLLOWING the bad instruction (it reversed the order and made s7 the creator of pom.xml, s1 depend on s7). Replayed through the corrected validator it is judged on its own facts."""
    _, plan = _live_prepared_plan(2, str(tmp_path))
    assert _pair(_records(plan)) == {("s1", "s7")}, "the earlier owner (s7) is the origin; the later owner (s1) is the consumer, satisfied by its explicit edge"
    assert all(not r["missing_requires_edge"] and not r["missing_depends_on_edge"] for r in _records(plan))
    result = await validate_plan(plan, workspace_path=str(tmp_path))
    assert result.valid is False and "AMBIGUOUS_SUBTASK_CAPABILITY_PROVIDER" in result.reason_codes
    assert "PLANNED_ARTIFACT_PREREQUISITE_UNDECLARED" not in result.reason_codes and "SEMANTIC_DEPENDENCY_EDGE_MISSING" not in result.reason_codes


def test_the_sealed_attempt0_is_still_a_schema_failure(tmp_path):
    output, issue = parse_planner_structured_output((FIXTURES / "planner_attempt0.json").read_text(encoding="utf-8"))
    assert output is None and "both provides and requires" in issue


# ------------------------------------------------------------------------------------------------------------------------ negative controls (synthetic)

def _subtask(sid, *, files=(), depends_on=(), provides=(), requires=()):
    planned = [PlannedFile(path=path, action=action, environment_requirements=list(env)) for path, action, env in files]
    return Subtask(id=sid, description=f"{sid} work", execution_method=ExecutionMethod.MODEL, planned_files=planned,
                   depends_on=list(depends_on), provides=list(provides), requires=list(requires))


def _plan(*subtasks):
    return EngineeringPlan(plan_id="p", kind=ChangeKind.TASK, subtasks=list(subtasks))


def _manifest(path, action=FileAction.CREATE, tool="maven"):
    return (path, action, (tool,))


def test_creator_then_later_modifier_valid_in_the_declared_create_modify_form_too():
    creator = _subtask("c", files=[_manifest("pom.xml")], provides=["project-structure"])
    modifier = _subtask("m", files=[_manifest("pom.xml", FileAction.MODIFY)], depends_on=["c"], requires=["project-structure"], provides=["run-config"])
    records = _records(_plan(creator, modifier))
    assert _pair(records) == {("m", "c")} and not records[0]["missing_requires_edge"] and not records[0]["missing_depends_on_edge"]


@pytest.mark.parametrize("path,tool", [("package.json", "node"), ("pyproject.toml", "python"), ("go.mod", "go"), ("sub/module/pom.xml", "maven")])
def test_the_rule_is_about_the_artifact_not_about_one_ecosystem_or_name(path, tool):
    creator = _subtask("c", files=[_manifest(path, tool=tool)], provides=["project-structure"])
    later = _subtask("m", files=[_manifest(path, tool=tool)], depends_on=["c"], requires=["project-structure"], provides=["run-config"])
    assert _pair(_records(_plan(creator, later))) == {("m", "c")}


def test_a_later_owner_that_omits_its_requires_edge_still_fails():
    creator = _subtask("c", files=[_manifest("pom.xml")], provides=["project-structure"])
    modifier = _subtask("m", files=[_manifest("pom.xml", FileAction.MODIFY)], depends_on=["c"], provides=["run-config"])   # ordered after c, but never declares the capability it consumes
    (record,) = _records(_plan(creator, modifier))
    assert (record["consumer_subtask"], record["provider_subtask"]) == ("m", "c")
    assert record["missing_requires_edge"] is True and record["missing_depends_on_edge"] is False


def test_with_no_dependency_order_there_is_no_earlier_owner_so_the_creator_exemption_does_not_apply():
    """Unordered co-owners of one artifact: nothing declares who is the origin, so both directions are reported exactly as before (that ambiguity is the plan's own fault)."""
    creator = _subtask("c", files=[_manifest("pom.xml")], provides=["project-structure"])
    modifier = _subtask("m", files=[_manifest("pom.xml", FileAction.MODIFY)], provides=["run-config"])
    assert _pair(_records(_plan(creator, modifier))) == {("c", "m"), ("m", "c")}


@pytest.mark.asyncio
async def test_a_true_consumer_before_provider_still_fails_with_the_actionable_repair(tmp_path):
    provider = _subtask("p", files=[_manifest("pom.xml")], provides=["project-structure"])
    consumer = _subtask("t", files=[("src/test/AppTest.java", FileAction.CREATE, ("java", "maven"))])
    result = await validate_plan(_plan(provider, consumer), workspace_path=str(tmp_path))
    assert result.valid is False
    assert {"PLANNED_ARTIFACT_PREREQUISITE_UNDECLARED", "SEMANTIC_DEPENDENCY_EDGE_MISSING"} <= set(result.reason_codes) and CONFLICT not in result.reason_codes
    prompt = build_structured_plan_repair_prompt("g", "{}", result.errors, result.reason_codes, 1, validation_evidence=result.evidence)
    assert "add p to t.depends_on" in prompt and "add project-structure to t.requires" in prompt     # no cycle: the instruction is kept


@pytest.mark.asyncio
async def test_a_reverse_edge_between_different_artifacts_is_a_typed_conflict_never_hidden_never_an_add_edge(tmp_path):
    """The forbidden shortcut would suppress this. The consumer needs the provider's artifact, but the provider already depends on the consumer: a genuinely bad graph."""
    consumer = _subtask("t", files=[("src/test/AppTest.java", FileAction.CREATE, ("java", "maven"))], provides=["tests"])
    provider = _subtask("p", files=[_manifest("pom.xml")], depends_on=["t"], requires=["tests"], provides=["project-structure"])
    result = await validate_plan(_plan(consumer, provider), workspace_path=str(tmp_path))
    assert result.valid is False and CONFLICT in result.reason_codes, "the finding is neither suppressed nor downgraded"
    assert "PLANNED_ARTIFACT_PREREQUISITE_UNDECLARED" not in result.reason_codes and "SEMANTIC_DEPENDENCY_EDGE_MISSING" not in result.reason_codes
    (record,) = [r for r in result.evidence if r.get("consumer_subtask") == "t"]
    assert record["depends_on_edge_creates_cycle"] is True
    prompt = build_structured_plan_repair_prompt("g", "{}", result.errors, result.reason_codes, 1, validation_evidence=result.evidence)
    assert "add p to t.depends_on" not in prompt and "to t.requires" not in prompt, "an edge that closes a cycle is never recommended"
    assert "do NOT add that dependency" in prompt and "restructure" in prompt.lower()


@pytest.mark.asyncio
async def test_the_requires_provides_edge_check_does_not_recommend_a_cycle_either(tmp_path):
    first = _subtask("a", files=[("a.txt", FileAction.CREATE, ())], requires=["from-b"])
    second = _subtask("b", files=[("b.txt", FileAction.CREATE, ())], depends_on=["a"], provides=["from-b"])
    result = await validate_plan(_plan(first, second), workspace_path=str(tmp_path))
    assert result.valid is False and CONFLICT in result.reason_codes and "SEMANTIC_DEPENDENCY_EDGE_MISSING" not in result.reason_codes
    prompt = build_structured_plan_repair_prompt("g", "{}", result.errors, result.reason_codes, 1, validation_evidence=result.evidence)
    assert "do NOT add that dependency" in prompt


@pytest.mark.asyncio
async def test_a_requires_provides_edge_that_closes_no_cycle_keeps_its_ordinary_finding(tmp_path):
    first = _subtask("a", files=[("a.txt", FileAction.CREATE, ())], requires=["from-b"])
    second = _subtask("b", files=[("b.txt", FileAction.CREATE, ())], provides=["from-b"])
    result = await validate_plan(_plan(first, second), workspace_path=str(tmp_path))
    assert "SEMANTIC_DEPENDENCY_EDGE_MISSING" in result.reason_codes and CONFLICT not in result.reason_codes


@pytest.mark.asyncio
async def test_ambiguous_providers_still_fail(tmp_path):
    one = _subtask("a", files=[("a.txt", FileAction.CREATE, ())], provides=["cap"])
    two = _subtask("b", files=[("b.txt", FileAction.CREATE, ())], provides=["cap"])
    assert "AMBIGUOUS_SUBTASK_CAPABILITY_PROVIDER" in (await validate_plan(_plan(one, two), workspace_path=str(tmp_path))).reason_codes


@pytest.mark.asyncio
async def test_an_explicit_dependency_cycle_still_fails(tmp_path):
    one = _subtask("a", files=[("a.txt", FileAction.CREATE, ())], depends_on=["b"])
    two = _subtask("b", files=[("b.txt", FileAction.CREATE, ())], depends_on=["a"])
    assert "SUBTASK_DEPENDENCY_CYCLE" in (await validate_plan(_plan(one, two), workspace_path=str(tmp_path))).reason_codes


# ------------------------------------------------------------------------------------------------------------------- the reason code is registered and guided

def test_the_conflict_code_is_a_classified_plan_validation_failure():
    assert CONFLICT in PLANNER_VALIDATION_FAILURE_CODES


def test_the_fixture_readme_records_the_digest_of_every_sealed_response():
    readme = (FIXTURES / "README.md").read_text(encoding="utf-8")
    for prefix in SHA256_PREFIXES.values():
        assert prefix in readme
