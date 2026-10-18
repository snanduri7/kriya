"""Characterization of the controller's module-level planning-request, plan-revision, owner-recovery and
resume-support decisions (full modularization E11), written BEFORE E11 moved them out of
``kriya/workflow/workflow_controller.py`` and green at the pre-move commit.

Each pin is a decision a pre-move mutant changed without any existing test failing (the narrow E11 tier: the files
that import these names); every pin calls the name ON ``kriya.workflow.workflow_controller`` (after the move it is a
forwarder there, so the pins exercise the moved body through the facade the enforce loop calls) and asserts the exact
value, so the mutant that removed the decision fails here. Patch-through pins (``save_artifact_registry`` /
``save_control_state`` patched on the controller reach the legacy-ownership migrations) prove the names the moved
migrations take from the controller are resolved there at call time.
"""
import json
import logging
from unittest.mock import patch

import pytest

from kriya.context.workspace_identity import workspace_identity
from kriya.control.decisions import load_decision_ledger
from kriya.control.state import ControlState
from kriya.workflow import workflow_controller as wc
from kriya.workflow.obligations import (
    ObligationAuthority,
    ObligationKind,
    ObligationLedger,
    ObligationRecord,
    ObligationStatus,
)
from kriya.workflow.plan_schema import (
    AcceptanceCriterion,
    EngineeringPlan,
    ExecutionMethod,
    ExecutionRole,
    FileAction,
    FileOwnershipRelation,
    GlobalInvariant,
    IntegrationRelationship,
    IntegrationRelationshipKind,
    PlannedFile,
    Subtask,
    VerificationMethod,
    VerificationMethodType,
    VerifierKind,
)
from kriya.workflow.recovery_plan import RecoveryAction, RecoveryParticipantRole
from kriya.workflow.triage import ChangeKind
from kriya.workflow.workflow_types import SubtaskStatus

COMPILE = VerificationMethod(type=VerificationMethodType.TOOL, description="compile", tool_name="compile")
RUNTIME = VerificationMethod(type=VerificationMethodType.JUDGMENT, description="run the app",
                             verifier_kind=VerifierKind.APPLICATION_RUNTIME, requires_runtime_execution=True)


def st(sid, files=(), deps=(), provides=(), requires=(), *, action=FileAction.MODIFY, verification=(COMPILE,),
       acceptance=(), invariants=(), role=ExecutionRole.IMPLEMENTATION, method=ExecutionMethod.MODEL, preserved=None,
       reasons=None, description=None, actions=None):
    planned = [PlannedFile(path=p, action=(actions or {}).get(p, action), reason=(reasons or {}).get(p, ""),
                           preserved_references=list((preserved or {}).get(p, ()))) for p in files]
    extra = {"tool_name": "compile"} if method == ExecutionMethod.TOOL else {}
    return Subtask(id=sid, description=description or f"do {sid}", execution_method=method, execution_role=role,
                   depends_on=list(deps), planned_files=planned, provides=list(provides), requires=list(requires),
                   verification=list(verification), acceptance_criteria_ids=list(acceptance),
                   relevant_global_invariant_ids=list(invariants), **extra)


def plan(*subtasks, **kwargs):
    return EngineeringPlan(plan_id="plan-e11", kind=ChangeKind.TASK, subtasks=list(subtasks), **kwargs)


# ============================================================================== planning request: subtask texts

def test_the_subtask_goal_text_sections_and_their_order():
    p = plan(st("s1", ["a.py"], provides=["A"]),
             st("s2", ["b.py", "c.py"], deps=["s1"], requires=["A"], acceptance=["ac1", "ac9"],
                verification=(COMPILE, RUNTIME), reasons={"b.py": "the owner"}),
             acceptance_criteria=[AcceptanceCriterion(id="ac1", description="b works")])
    text = wc.build_subtask_goal_text(p.subtask_by_id("s2"), 2, 3, plan=p)
    assert text == (
        "This is subtask 's2' (2 of 3) of a larger structured plan, depending on: s1. Earlier subtasks' output has "
        "already been applied to this project on disk - inspect the existing code/build files in the Workspace Context "
        "below rather than assuming a blank project, and do NOT recreate, restructure, or rename anything that already "
        "exists and already works unless this subtask's own goal below explicitly requires changing it.\n\n"
        "do s2"
        "\n\nAcceptance criteria assigned to this subtask:\n- b works"
        "\n\nFiles this subtask should touch:\n- b.py (modify) - the owner\n- c.py (modify)"
        "\n\nVerification: compile; run the app")
    assert wc.build_subtask_goal_text(p.subtask_by_id("s1"), 1, 3) == (
        "do s1\n\nFiles this subtask should touch:\n- a.py (modify)\n\nVerification: compile")
    grounded = wc.build_subtask_goal_text(p.subtask_by_id("s1"), 1, 3, grounding_goal="THE GOAL")
    assert grounded.endswith("for THIS subtask - follow it, but it may be adapted if it conflicts with the "
                             "authoritative goal or with real constraints discovered while implementing it.\n"
                             "do s1\n\nFiles this subtask should touch:\n- a.py (modify)\n\nVerification: compile")
    assert "\nTHE GOAL\n\n" in grounded


def test_the_semantic_context_payload():
    p = plan(st("s1", ["svc.py"], provides=["SVC"], verification=(COMPILE,)),
             st("s2", ["test_svc.py"], deps=["s1"], requires=["SVC"], invariants=["gi1"]),
             st("s3", ["main.py"], deps=["s2"], provides=["APP"], verification=(COMPILE, RUNTIME)),
             st("s4", ["other.py"], deps=["s1"], requires=["SVC"]),
             global_invariants=[GlobalInvariant(id="gi1", statement="never print")])
    payload = json.loads(wc.build_subtask_semantic_context(p, p.subtask_by_id("s1")).split("\n", 1)[1])
    assert payload["downstream_requirements"] == [{"consumer": "s2", "capability": "SVC"},
                                                  {"consumer": "s4", "capability": "SVC"}]
    assert payload["application_runtime_owners"] == ["s3"]
    assert payload["verification_ownership"] is None
    test_stage = json.loads(wc.build_subtask_semantic_context(p, p.subtask_by_id("s2")).split("\n", 1)[1])
    assert test_stage["upstream_contracts"] == [{"capability": "SVC", "provider": "s1"}]
    assert test_stage["relevant_global_invariants"] == ["never print"]
    assert test_stage["verification_ownership"].startswith("This subtask's unit-test artifacts do not own")
    assert test_stage["runtime_execution_required"] is False
    text = wc.build_subtask_semantic_context(p, p.subtask_by_id("s3"))
    assert text.index('"application_runtime_owners"') < text.index('"downstream_requirements"') < text.index('"local_description"')
    assert json.loads(text.split("\n", 1)[1])["runtime_execution_required"] is True


def test_the_subtask_plan_and_constraint_texts():
    assert wc.build_subtask_plan_text(st("s1", ["a.py"], description="add mul")) == "Implement: add mul"
    assert wc.build_subtask_constraint_context("") == ""
    assert wc.build_subtask_constraint_context("G").startswith("Overall request constraints (context only; not this "
                                                               "stage's completion scope):\nG\n\n")


def test_split_planned_source_separates_the_planned_file_items():
    from kriya.workflow.context_package import ContextPackage, make_context_item

    items = (make_context_item("a.py", "A", wc.PLANNED_FILE_SOURCE_REASON, "workspace", "trusted"),
             make_context_item("b.py", "B", "retrieved", "workspace", "trusted"))
    package = ContextPackage(relevant_files=items)
    paths, rest = wc.split_planned_source(package)
    assert paths == ["a.py"] and [item.path for item in rest.relevant_files] == ["b.py"]


# ============================================================================== planning request: feasibility

class _Assessment:
    def __init__(self, subtask_id, path, needs_grounding=False):
        self.subtask_id, self.path = subtask_id, path
        self.evidence = {"needs_grounding": needs_grounding}


@pytest.mark.asyncio
async def test_feasibility_considers_only_existing_modified_files_of_model_implementation_units(tmp_path):
    (tmp_path / "a.py").write_text("A")
    (tmp_path / "n.py").write_text("N")
    p = plan(st("s1", ["a.py"]), st("s2", ["n.py"], action=FileAction.DELETE, deps=["s1"]),
             st("s3", ["missing.py"], deps=["s1"]))
    seen = []

    def assess(**kwargs):
        seen.append((kwargs["subtask_id"], kwargs["path"], kwargs["content"], kwargs["unit_goal"]))
        return _Assessment(kwargs["subtask_id"], kwargs["path"])

    with patch("kriya.workflow.workflow_controller.model_capacities", return_value=[]), \
            patch("kriya.workflow.workflow_controller.assess_planned_edit", side_effect=assess):
        results = await wc.evaluate_plan_edit_feasibility(object(), p, "THE GOAL", str(tmp_path))
    assert [(r.subtask_id, r.path) for r in results] == [("s1", "a.py")]
    assert [s[:3] for s in seen] == [("s1", "a.py", "A")]
    assert seen[0][3] == wc.build_subtask_goal_text(p.subtask_by_id("s1"), 1, 3, plan=p, grounding_goal="THE GOAL")


@pytest.mark.asyncio
async def test_feasibility_collects_unit_grounding_once_per_unit(tmp_path):
    for name in ("a.py", "b.py"):
        (tmp_path / name).write_text(name)
    p = plan(st("s1", ["a.py", "b.py"]))
    calls = []

    def assess(**kwargs):
        calls.append((kwargs["path"], kwargs["grounding"]))
        return _Assessment(kwargs["subtask_id"], kwargs["path"], needs_grounding=kwargs["grounding"] is None)

    async def grounding(config, workspace_path, unit_goal):
        return "G"

    with patch("kriya.workflow.workflow_controller.model_capacities", return_value=[]), \
            patch("kriya.workflow.workflow_controller.assess_planned_edit", side_effect=assess), \
            patch("kriya.workflow.workflow_controller.collect_unit_grounding", side_effect=grounding) as collect:
        await wc.evaluate_plan_edit_feasibility(object(), p, "goal", str(tmp_path))
    assert collect.await_count == 1
    assert calls == [("a.py", None), ("a.py", "G"), ("b.py", "G")]


# ============================================================================== planning request: candidates

def test_the_planner_candidates_skip_ignored_paths_rank_by_the_goal_and_are_bounded(tmp_path):
    for rel in ("README.md", "kriya.yaml", "zeta.py", "Alpha.py", "memory/x.py", "logs/y.py", "docs/notes.md",
                "src/CustomerService.java", "src/b.py", "x/a.py"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    assert wc._authoritative_planner_extension_candidates(str(tmp_path)) == [
        "Alpha.py", "zeta.py", "docs/notes.md", "src/CustomerService.java", "src/b.py", "x/a.py"]
    assert wc._authoritative_planner_extension_candidates(str(tmp_path), goal="fix the customer service a") == [
        "src/CustomerService.java", "Alpha.py", "docs/notes.md", "src/b.py", "x/a.py", "zeta.py"]
    assert wc._authoritative_planner_extension_candidates(str(tmp_path), 2) == ["Alpha.py", "zeta.py"]


# ============================================================================== planning request: regression guards

def _ledger_with(*records):
    ledger = ObligationLedger()
    for record in records:
        ledger.record(record)
    return ledger


def _rec(oid, kind, status, evidence, owner=None):
    return ObligationRecord(id=oid, kind=kind, status=status, authority=ObligationAuthority.DETERMINISTIC,
                            description="d", source="test", evidence=evidence, owner_subtask_id=owner)


def test_the_strict_regression_is_a_proper_superset_of_a_retained_baseline():
    assert wc._is_strict_regression(None, frozenset({"A"})) is False
    assert wc._is_strict_regression(frozenset({"D"}), frozenset({"D"})) is False
    assert wc._is_strict_regression(frozenset({"D"}), frozenset({"A", "D"})) is True
    assert wc._is_strict_regression(frozenset({"D"}), frozenset({"X"})) is False


def test_the_must_preserve_lines_of_satisfied_obligations():
    ledger = _ledger_with(
        _rec("pr1", ObligationKind.PRESERVED_REFERENCE, ObligationStatus.SATISFIED, {"source": "T.java", "target": "B.java"}),
        _rec("pr2", ObligationKind.PRESERVED_REFERENCE, ObligationStatus.SATISFIED, {"source": "T.java"}),
        _rec("sc1", ObligationKind.SUBTASK_SEMANTIC_CONTRACT, ObligationStatus.SATISFIED,
             {"relation": "requires", "subtask_id": "s2", "requirement": "SVC"}),
        _rec("sc2", ObligationKind.SUBTASK_SEMANTIC_CONTRACT, ObligationStatus.SATISFIED,
             {"relation": "requires", "subtask_id": "s3"}),
        _rec("sc3", ObligationKind.SUBTASK_SEMANTIC_CONTRACT, ObligationStatus.SATISFIED,
             {"relation": "provides", "subtask_id": "s1", "capability": "SVC"}))
    assert wc._preserved_reference_must_preserve_lines(ledger) == [
        "T.java must keep declaring B.java in its own planned_files[].preserved_references (already validated - a real "
        "grounded edge with no legitimate owner; do not remove this entry while fixing other reported issues, and do "
        "not replace it with a different target)"]
    assert wc._semantic_contract_must_preserve_lines(ledger) == [
        "subtask 's2' must keep declaring 'SVC' in its own requires (already validated - a real provider exists with a "
        "correct depends_on edge; do not drop this entry while fixing other reported issues)",
        "subtask 's1' must keep declaring 'SVC' in its own provides (already validated as its sole provider; do not "
        "drop this entry while fixing other reported issues)"]


def test_the_subtask_ids_a_round_was_asked_to_fix():
    assert wc._subtask_ids_mentioned([
        "subtask 's1' requires 'X' which no subtask provides",
        "subtask(s) ['s2', 's3'] declare an ambiguous capability",
        "subtask 's4' has preserved_references conflicting with ownership",
    ]) == {"s1", "s2", "s3"}


def _regressed(ledger, oid, kind, evidence, owner=None):
    ledger.record(_rec(oid, kind, ObligationStatus.SATISFIED, evidence, owner))
    before = len(ledger.regressions)
    ledger.record(_rec(oid, kind, ObligationStatus.VIOLATED, evidence, owner))
    return before


def test_the_semantic_contract_regressions_of_this_round():
    ledger = ObligationLedger()
    _regressed(ledger, "old", ObligationKind.SUBTASK_SEMANTIC_CONTRACT, {"subtask_id": "s9"})
    before = len(ledger.regressions)
    _regressed(ledger, "a", ObligationKind.SUBTASK_SEMANTIC_CONTRACT, {"subtask_id": "s1"})
    _regressed(ledger, "b", ObligationKind.SUBTASK_SEMANTIC_CONTRACT, {}, owner="s2")
    _regressed(ledger, "c", ObligationKind.SUBTASK_SEMANTIC_CONTRACT, {"subtask_id": "s3"})
    _regressed(ledger, "d", ObligationKind.SUBTASK_SEMANTIC_CONTRACT, {"subtask_id": "s1"})
    _regressed(ledger, "e", ObligationKind.PRESERVED_REFERENCE, {"subtask_id": "s5", "source": "T", "target": "B"})
    assert wc._semantic_contract_regression_subtasks(ledger, before, {"s3"}) == ["s1", "s2"]


def test_the_preserved_reference_regressions_of_this_round():
    texts = ["artifact 'T.java' declares 'B.java' as a preserved reference, but 'B.java' is itself planned"]
    assert wc._preserved_reference_pairs_mentioned(texts) == {("T.java", "B.java")}
    ledger = ObligationLedger()
    before = len(ledger.regressions)
    _regressed(ledger, "p1", ObligationKind.PRESERVED_REFERENCE, {"source": "T.java", "target": "B.java"})
    _regressed(ledger, "p2", ObligationKind.PRESERVED_REFERENCE, {"source": "T.java", "target": "V.java"})
    _regressed(ledger, "p3", ObligationKind.SUBTASK_SEMANTIC_CONTRACT, {"source": "S", "target": "Q"})
    assert wc._preserved_reference_regressions(ledger, before, {("T.java", "B.java")}) == [("T.java", "V.java")]


# ============================================================================== planning request: the request

def test_the_authoritative_request_route_guidance_candidates_and_evidence():
    milestone = wc.build_authoritative_planner_request(
        "G", route_kind=ChangeKind.MILESTONE, extension_candidates=["a.py"], repository_candidates=["r.py"],
        structural_evidence="a.py references -> b.py")
    assert ("- This request is routed as milestone. extension_points must name at least one real existing path where "
            'the capability attaches. Select only from these local workspace candidates: ["a.py"].\n') in milestone
    assert 'Existing local workspace paths available as repository evidence are: ["r.py"]. For modify' in milestone
    assert milestone.endswith("where it names a relationship relevant to this change):\na.py references -> b.py\n")
    empty = wc.build_authoritative_planner_request("G", route_kind=ChangeKind.ENHANCEMENT)
    assert ("- This request is routed as enhancement, but the workspace has no existing project files that can be an "
            "insertion point; use extension_points=[].\n") in empty
    assert "Grounded structural evidence" not in empty
    assert "routed as" not in wc.build_authoritative_planner_request("G", route_kind=ChangeKind.TASK)


# ============================================================================== planning request: structural evidence

def _java(tmp_path, files):
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return sorted(files)


def test_the_structural_evidence_edges(tmp_path):
    paths = _java(tmp_path, {
        "src/Customer.java": "package p;\npublic class Customer { public String name() { return \"n\"; } }\n",
        "src/CustomerService.java": ("package p;\nimport p.*;\nimport q.Other;\npublic class CustomerService {\n"
                                     "  public Customer find() { Customer c = new Customer(); return c; }\n"
                                     "  public void wrap() { }\n  void self() { wrap(); }\n}\n"),
        "src/CustomerController.java": ("package p;\npublic class CustomerController {\n"
                                        "  /** CustomerService s = new CustomerService(); */\n"
                                        "  String s = \"new Customer()\";\n"
                                        "  void go(CustomerService svc) { svc.find(); }\n}\n"),
        "src/Twin.java": "package p;\npublic class Twin { public void wrap() { } void self() { wrap(); } }\n",
        "src/Caller.java": "package p;\npublic class Caller { void go() { x.wrap(); } }\n",
        "src/Holder.java": "package p;\nimport p.Twin;\npublic class Holder { Twin t; Customer c = new Customer(); }\n",
        "src/Self.java": "package p;\nimport p.Self;\npublic class Self { }\n",
        "src/Ambiguous.java": "package p;\npublic class Ambiguous { void go(CustomerService s) { s.wrap(); } }\n",
    })
    text, edges = wc.build_planning_structural_evidence(str(tmp_path), paths + ["src/Missing.java"])
    # imports resolve to the declaring candidate (never the importing file itself, never a wildcard); a callee declared
    # by several other candidates resolves to nothing; targets are sorted (Holder: the import of Twin is found first)
    assert edges == {"src/CustomerController.java": ["src/CustomerService.java"],
                     "src/CustomerService.java": ["src/Customer.java"],
                     "src/Holder.java": ["src/Customer.java", "src/Twin.java"]}
    assert text == ("src/CustomerController.java references -> src/CustomerService.java\n"
                    "src/CustomerService.java references -> src/Customer.java\n"
                    "src/Holder.java references -> src/Customer.java\n"
                    "src/Holder.java references -> src/Twin.java")


def test_the_structural_evidence_of_python_imports(tmp_path):
    paths = _java(tmp_path, {"app/models.py": "class Model:\n    pass\n",
                             "app/views.py": "from app.models import Model\n\ndef view():\n    return Model()\n",
                             "app/text.py": 'HELP = "use new Model() here"\n'})
    _text, edges = wc.build_planning_structural_evidence(str(tmp_path), paths)
    # a non-Java file's text is scanned raw: the constructor pattern inside a Python string is an edge today
    assert edges == {"app/text.py": ["app/models.py"], "app/views.py": ["app/models.py"]}


# ============================================================================== planning request: grounded artifacts

def test_the_missing_grounded_production_artifact_shapes(tmp_path):
    for rel in ("src/A.java", "src/B.java", "src/C.java", "src/test/ATest.java", "src/test/BTest.java", "README.md",
                "src/test/CTest.java"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x")
    p = plan(st("s1", ["src/A.java"], provides=["A"]),
             st("s2", ["src/B.java"], provides=["B"]),
             st("s3", ["src/test/ATest.java"], deps=["s1"], requires=["A"]),
             st("s4", ["src/test/BTest.java"], deps=["s1"], requires=["A"]),
             st("s5", ["src/test/CTest.java"], deps=["s2"], requires=["B"]),
             st("s6", ["src/C.java"], deps=["s2"]),
             st("s7", ["src/test/DTest.java", "src/D.java"]))
    edges = {"src/test/ATest.java": ["src/A.java", "src/test/BTest.java", "README.md", "src/Unowned.java",
                                     "src/TestHelper.java"],   # a runnable-test NAME outside a test directory
             "src/test/DTest.java": ["src/D.java"],            # the same subtask owns both ends
             "src/test/NotPlannedTest.java": ["src/Unowned.java"],
             "src/test/BTest.java": ["src/B.java"],
             "src/test/CTest.java": ["src/B.java", "src/Unowned.java", "src/Unowned.java"],
             "src/C.java": ["src/Gone.java", "src/A.java"],
             "src/NotPlanned.java": ["src/Unowned.java"]}
    assert wc.find_missing_grounded_production_artifacts(p, edges) == [
        {"test_file": "src/test/ATest.java", "missing_production_artifact": "src/Unowned.java", "consumer_subtask": "s3",
         "reason": "unowned"},
        {"test_file": "src/test/BTest.java", "missing_production_artifact": "src/B.java", "owning_subtask": "s2",
         "consumer_subtask": "s4", "reason": "not_in_dependency_chain"},
        {"test_file": "src/test/CTest.java", "missing_production_artifact": "src/Unowned.java", "consumer_subtask": "s5",
         "reason": "unowned"},
        {"test_file": "src/C.java", "missing_production_artifact": "src/A.java", "owning_subtask": "s1",
         "consumer_subtask": "s6", "reason": "not_in_dependency_chain"},
    ]


def test_the_transitive_dependency_chain_and_the_semantic_mismatch():
    p = plan(st("s1", ["src/A.java"], provides=["A"]), st("s2", ["src/M.java"], deps=["s1"], provides=["M"]),
             st("s3", ["src/test/ATest.java"], deps=["s2"], requires=["M"]),
             st("s4", ["src/U.java"], deps=["s2"]))
    edges = {"src/test/ATest.java": ["src/A.java"], "src/U.java": ["src/A.java"]}
    assert wc.find_missing_grounded_production_artifacts(p, edges) == [
        {"test_file": "src/test/ATest.java", "missing_production_artifact": "src/A.java", "owning_subtask": "s1",
         "consumer_subtask": "s3", "owner_provides": ["A"], "test_requires": ["M"],
         "reason": "semantic_provider_mismatch"}]


def test_a_preserved_reference_is_recorded_and_a_dropped_one_violated(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "Base.java").write_text("class Base {}")
    (tmp_path / "src" / "test").mkdir()
    (tmp_path / "src" / "test" / "PetTest.java").write_text("x")
    p = plan(st("s1", ["src/test/PetTest.java"], preserved={"src/test/PetTest.java": ["src/Base.java"]}))
    edges = {"src/test/PetTest.java": ["src/Base.java"]}
    ledger = ObligationLedger()
    assert wc.find_missing_grounded_production_artifacts(
        p, edges, workspace_path=str(tmp_path), obligation_ledger=ledger, revision="r1") == []
    record = ledger.current("plan.preserved_reference.src/test/PetTest.java->src/Base.java")
    assert (record.status, record.terminal_required, record.revision) == (ObligationStatus.SATISFIED, True, "r1")
    assert record.evidence["source"] == "src/test/PetTest.java" and record.evidence["target"] == "src/Base.java"
    from kriya.workflow.edit_safety import read_file_revision
    assert record.evidence["baseline_hash"] == read_file_revision(str(tmp_path / "src" / "Base.java"))
    assert wc.find_missing_grounded_production_artifacts(p, edges, obligation_ledger=ObligationLedger()) == []
    dropped = plan(st("s1", ["src/test/PetTest.java"]))
    assert wc.find_missing_grounded_production_artifacts(
        dropped, edges, workspace_path=str(tmp_path), obligation_ledger=ledger, revision="r2") == [
        {"test_file": "src/test/PetTest.java", "missing_production_artifact": "src/Base.java", "consumer_subtask": "s1",
         "reason": "unowned"}]
    record = ledger.current("plan.preserved_reference.src/test/PetTest.java->src/Base.java")
    assert (record.status, record.terminal_required, record.evidence["dropped_between_revisions"]) == (
        ObligationStatus.VIOLATED, False, True)
    regressed_before = len(ledger.regressions)
    history_before = len(ledger.history("plan.preserved_reference.src/test/PetTest.java->src/Base.java"))
    wc.find_missing_grounded_production_artifacts(dropped, edges, workspace_path=str(tmp_path), obligation_ledger=ledger)
    assert len(ledger.regressions) == regressed_before   # a VIOLATED record is never re-violated
    assert len(ledger.history("plan.preserved_reference.src/test/PetTest.java->src/Base.java")) == history_before


def test_the_approved_plan_document():
    p = plan(st("s2", ["b.py"], deps=["s1"]), st("s1", ["a.py"]))
    document = wc.build_approved_plan_document(p, plan_hash="h", repair_attempts=2, stage_states={"s1": "pending"},
                                               lifecycle_state="approved")
    assert {k: v for k, v in document.items() if k != "plan"} == {
        "schema_version": 1, "plan_id": "plan-e11", "plan_hash": "h", "approval_status": "approved",
        "approval_basis": "authoritative_validation", "repair_attempts": 2, "lifecycle_state": "approved",
        "stage_order": ["s1", "s2"], "stage_states": {"s1": "pending"}}


# ============================================================================== plan revision

def test_the_transitive_dependents_in_plan_order():
    p = plan(st("s1", ["a.py"]), st("s9", ["b.py"], deps=["s1"]), st("s2", ["c.py"], deps=["s9"]),
             st("s5", ["d.py"]))
    assert wc.transitive_subtask_dependents(p, "s1") == ["s9", "s2"]   # execution order, not id order


def test_the_nearest_mutating_upstream_owner():
    p = plan(st("s1", ["a.py"]), st("s2", ["b.py"], deps=["s1"]),
             st("v1", [], deps=["s2"], role=ExecutionRole.VERIFICATION),
             st("v2", [], deps=["v1"], role=ExecutionRole.VERIFICATION))
    v2 = p.subtask_by_id("v2")
    assert wc.nearest_mutating_upstream_owner(p, v2, ["s1", "s2", "v1"]).id == "s2"
    assert wc.nearest_mutating_upstream_owner(p, v2, ["s1", "v1"]).id == "s1"
    assert wc.nearest_mutating_upstream_owner(p, v2, ["v1"]) is None


def test_the_scope_conflict_report_and_files():
    assert wc.subtask_scope_conflict_report({"reason_code": "PLAN_SCOPE_REVISION_REQUIRED", "required_files": ["a"],
                                             "allowed_files": ["b"]}) == (
        "subtask repair requires approved-plan scope revision; grounded required files ['a'] are outside this "
        "stage's allowed files ['b']", ("PLAN_SCOPE_REVISION_REQUIRED",))
    assert wc.subtask_scope_conflict_report({"reason_code": ["X"], "required_files": []})[1] == (
        "PLAN_SCOPE_REVISION_REQUIRED",)
    assert wc.subtask_scope_conflict_report({"reason_code": "RUNTIME_PLAN_GAP", "reason": " missing x "}) == (
        "subtask repair requires approved-plan revision (RUNTIME_PLAN_GAP): missing x",
        ("PLAN_SCOPE_REVISION_REQUIRED", "RUNTIME_PLAN_GAP"))
    assert wc._plan_scope_conflict_files({"grounded_owner_files": ["g"], "required_files": ["r"]}) == ["g"]
    assert wc._plan_scope_conflict_files({"grounded_owner_files": [], "required_files": ["r"]}) == ["r"]
    assert wc._plan_scope_conflict_files(None) == []


def _files(tmp_path, *names):
    for name in names:
        (tmp_path / name).write_text("x")


def test_the_grounded_scope_owner_revision_moves_and_merges(tmp_path):
    _files(tmp_path, "a.py", "b.py", "own.py")
    vm = VerificationMethod(type=VerificationMethodType.TOOL, description="test", tool_name="test")
    p = plan(st("s1", ["a.py"], provides=["A"], reasons={"a.py": "first"}, actions={"a.py": FileAction.CREATE}),
             st("s2", ["a.py", "own.py"], deps=["s1"], provides=["A2"], reasons={"a.py": "second"}),
             st("s3", ["b.py"], deps=["s1"], provides=["B"], requires=["A"], acceptance=["ac1"], invariants=["gi1"],
                verification=(COMPILE, vm)),
             st("s4", ["c.py"], deps=["s3"], requires=["B"]),
             st("s5", ["d.py"], deps=["s2"], requires=["A2"]),
             acceptance_criteria=[AcceptanceCriterion(id="ac1", description="b")],
             global_invariants=[GlobalInvariant(id="gi1", statement="x")])
    revised = wc.revise_plan_for_grounded_scope_owner(p, "s5", ["b.py", "a.py", "b.py", "new.py"] if False else
                                                      ["b.py", "a.py", "b.py"], str(tmp_path))
    s5 = revised.subtask_by_id("s5")
    assert [(pf.path, pf.action, pf.reason) for pf in s5.planned_files] == [
        ("d.py", FileAction.MODIFY, ""), ("b.py", FileAction.MODIFY, "deterministically grounded architectural owner"),
        ("a.py", FileAction.MODIFY, "deterministically grounded architectural owner")]
    assert revised.subtask_by_id("s3") is None and revised.subtask_by_id("s1") is None
    assert [pf.path for pf in revised.subtask_by_id("s2").planned_files] == ["own.py"]
    assert s5.depends_on == ["s2"]
    assert s5.acceptance_criteria_ids == ["ac1"] and s5.relevant_global_invariant_ids == ["gi1"]
    assert [v.description for v in s5.verification] == ["compile", "test"]
    assert s5.provides == ["B", "A"] and s5.requires == ["A2"]
    assert revised.subtask_by_id("s4").depends_on == ["s5"]
    assert s5.description == "do s5 Authoritative scope revision: modify grounded owner(s) b.py, a.py."
    with pytest.raises(ValueError, match="grounded scope owner does not exist: gone.py"):
        wc.revise_plan_for_grounded_scope_owner(p, "s5", ["gone.py"], str(tmp_path))
    with pytest.raises(ValueError, match="unknown failed subtask"):
        wc.revise_plan_for_grounded_scope_owner(p, "s9", ["a.py"], str(tmp_path))


def test_the_grounded_revision_of_an_unowned_file_and_of_a_failed_stage_that_already_declares_it(tmp_path):
    _files(tmp_path, "a.py", "u.py")
    p = plan(st("s1", ["a.py"]), st("s2", ["b.py"], deps=["s1"]))
    revised = wc.revise_plan_for_grounded_scope_owner(p, "s2", ["u.py"], str(tmp_path))
    assert [(pf.path, pf.action) for pf in revised.subtask_by_id("s2").planned_files] == [
        ("b.py", FileAction.MODIFY), ("u.py", FileAction.MODIFY)]
    both = plan(st("s1", ["a.py", "k.py"]), st("s2", ["a.py", "z.py"], deps=["s1"]))
    revised = wc.revise_plan_for_grounded_scope_owner(both, "s2", ["a.py"], str(tmp_path))
    # the failed stage's own declaration is kept as it was (never re-derived as a moved one)
    assert [(pf.path, pf.reason) for pf in revised.subtask_by_id("s2").planned_files] == [("a.py", ""), ("z.py", "")]
    assert [pf.path for pf in revised.subtask_by_id("s1").planned_files] == ["k.py"]


def test_a_downstream_owner_merge_never_inherits_an_edge_to_a_stage_downstream_of_the_failed_one(tmp_path):
    _files(tmp_path, "c.py")
    p = plan(st("s1", ["a.py"], provides=["A"]), st("s2", ["b.py"], deps=["s1"], requires=["A"], provides=["B"]),
             st("s5", ["e.py"], deps=["s2"], requires=["B"], provides=["E"]),
             st("s3", ["c.py"], deps=["s2", "s5"], requires=["B", "E"], provides=["C"]),
             st("s4", ["d.py"], deps=["s3"], requires=["C"]))
    s2 = wc.revise_plan_for_grounded_scope_owner(p, "s2", ["c.py"], str(tmp_path)).subtask_by_id("s2")
    assert s2.depends_on == ["s1"] and s2.requires == ["A"] and s2.provides == ["B", "C"]


def test_a_downstream_owner_merge_drops_the_cyclic_edges_and_their_requires(tmp_path):
    _files(tmp_path, "c.py")
    p = plan(st("s1", ["a.py"], provides=["A"]), st("s2", ["b.py"], deps=["s1"], requires=["A"], provides=["B"]),
             st("s3", ["c.py"], deps=["s2"], requires=["B"], provides=["C"]),
             st("s4", ["d.py"], deps=["s3"], requires=["C"]))
    revised = wc.revise_plan_for_grounded_scope_owner(p, "s2", ["c.py"], str(tmp_path))
    s2 = revised.subtask_by_id("s2")
    assert s2.depends_on == ["s1"] and s2.requires == ["A"] and s2.provides == ["B", "C"]
    assert revised.subtask_by_id("s4").depends_on == ["s2"]


def test_a_capability_with_several_providers_is_not_dropped_with_one_dropped_provider(tmp_path):
    # only a capability whose SOLE provider is the dropped downstream stage leaves the merged owner's requires
    _files(tmp_path, "c.py")
    p = plan(st("s1", ["a.py"], provides=["A"]), st("s2", ["b.py"], deps=["s1"], requires=["A"], provides=["B"]),
             st("s5", ["e.py"], deps=["s2"], requires=["B"], provides=["E"]),
             st("s6", ["g.py"], provides=["E"]),
             st("s3", ["c.py"], deps=["s2", "s5"], requires=["B", "E"], provides=["C"]),
             st("s4", ["d.py"], deps=["s3"], requires=["C"]))
    s2 = wc.revise_plan_for_grounded_scope_owner(p, "s2", ["c.py"], str(tmp_path)).subtask_by_id("s2")
    assert s2.depends_on == ["s1"] and s2.requires == ["A", "E"] and s2.provides == ["B", "C"]


def test_an_upstream_consumer_of_the_merged_owner_drops_the_edge(tmp_path):
    _files(tmp_path, "m.xml")
    p = plan(st("s1", ["m.xml"], provides=["M"]), st("s2", ["a.py"], deps=["s1"], requires=["M"], provides=["A"]),
             st("s9", ["b.py"], deps=["s2"], requires=["A"], provides=["B"]),
             st("s3", ["t.py"], deps=["s9"], requires=["B"]))
    revised = wc.revise_plan_for_grounded_scope_owner(p, "s3", ["m.xml"], str(tmp_path))
    assert revised.subtask_by_id("s2").depends_on == []   # s2 is (transitively) upstream of s3: dropped, not redirected
    assert revised.subtask_by_id("s3").depends_on == ["s9"]


def test_an_owner_transitively_downstream_of_the_failed_stage_never_hands_it_that_edge(tmp_path):
    _files(tmp_path, "c.py")
    p = plan(st("s1", ["a.py"]), st("s2", ["b.py"], deps=["s1"]), st("s5", ["e.py"], deps=["s2"]),
             st("s6", ["f.py"], deps=["s5"]), st("s3", ["c.py"], deps=["s6"]))
    assert wc.revise_plan_for_grounded_scope_owner(p, "s2", ["c.py"], str(tmp_path)).subtask_by_id("s2").depends_on == [
        "s1"]


def test_the_planned_prerequisite_revision():
    p = plan(st("s1", ["m.xml"], provides=["BUILD"]), st("s2", ["t.py", "u.py"]))
    revised = wc.revise_plan_for_planned_prerequisite(p, consumer_subtask_id="s2", owner_subtask_id="s1",
                                                      capability="BUILD", consumer_artifacts=["t.py"])
    s2 = revised.subtask_by_id("s2")
    assert s2.depends_on == ["s1"] and s2.requires == ["BUILD"]
    assert [pf.requires_capabilities for pf in s2.planned_files] == [["BUILD"], []]
    with pytest.raises(ValueError, match="is not provided by"):
        wc.revise_plan_for_planned_prerequisite(p, consumer_subtask_id="s2", owner_subtask_id="s1", capability="X")
    with pytest.raises(ValueError, match="must be different subtasks"):
        wc.revise_plan_for_planned_prerequisite(p, consumer_subtask_id="s1", owner_subtask_id="s1", capability="BUILD")
    with pytest.raises(ValueError, match="unknown subtask"):
        wc.revise_plan_for_planned_prerequisite(p, consumer_subtask_id="s9", owner_subtask_id="s1", capability="BUILD")


def test_the_python_module_resolution(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "exists.py").write_text("x")
    (tmp_path / "pkg" / "subpkg").mkdir()
    assert wc.resolve_python_module_to_candidate_artifact_path("pkg.views", str(tmp_path)) == "pkg/views.py"
    assert wc.resolve_python_module_to_candidate_artifact_path("views", str(tmp_path)) is None
    assert wc.resolve_python_module_to_candidate_artifact_path("nopkg.views", str(tmp_path)) is None
    assert wc.resolve_python_module_to_candidate_artifact_path("pkg.exists", str(tmp_path)) is None
    assert wc.resolve_python_module_to_candidate_artifact_path("pkg.subpkg", str(tmp_path)) is None


def test_the_runtime_plan_gap_owner_and_revision():
    p = plan(st("s1", ["pkg/__init__.py"]), st("s2", ["pkg/urls.py"], deps=["s1"]), st("s3", ["x.py"]))
    assert wc.resolve_runtime_plan_gap_owner(p, "pkg/views.py", p.subtask_by_id("s2")) == "s1"
    assert wc.resolve_runtime_plan_gap_owner(p, "views.py", p.subtask_by_id("s2")) is None
    assert wc.resolve_runtime_plan_gap_owner(p, "pkg/views.py", p.subtask_by_id("s1")) is None
    assert wc.resolve_runtime_plan_gap_owner(p, "pkg/views.py", p.subtask_by_id("s3")) is None
    assert p.classify_file_ownership("s2", "pkg/__init__.py") is FileOwnershipRelation.PAST_ORDERED
    revised = wc.revise_plan_for_runtime_plan_gap(p, owner_subtask_id="s1", artifact_path="pkg/views.py", reason="gap")
    assert [(pf.path, pf.action, pf.reason) for pf in revised.subtask_by_id("s1").planned_files][-1] == (
        "pkg/views.py", FileAction.CREATE, "gap")
    with pytest.raises(ValueError, match="is already planned by"):
        wc.revise_plan_for_runtime_plan_gap(p, owner_subtask_id="s1", artifact_path="pkg/__init__.py", reason="x")


# ============================================================================== owner recovery: ownership

def _state(states, written):
    control = ControlState.new(run_id="r")
    return control.with_updates(subtask_states=states, subtask_written_files=written)


def test_the_effective_owner_is_the_latest_completed_modifier():
    p = plan(st("s1", ["App.java"]), st("s2", ["App.java"], deps=["s1"]), st("s3", ["T.java"], deps=["s1"]),
             st("s4", ["X.java"], deps=["s3"]))
    order = ["s1", "s2", "s3", "s4"]
    control = _state({"s1": "completed", "s2": "completed", "s3": "failed", "s4": "failed"},
                     {"s1": ["App.java"], "s2": ["App.java"], "s3": ["App.java"], "s4": ["App.java"]})
    resolution = wc.resolve_effective_artifact_owner(p, "App.java", p.subtask_by_id("s3"), order, control)
    assert (resolution.owner_subtask_id, resolution.resolution_basis, resolution.evidence) == (
        "s2", wc.ArtifactOwnerResolutionBasis.LATEST_SUCCESSFUL_MODIFIER, {"prior_modifiers": ["s1", "s2"]})
    only_failed = _state({"s3": "failed"}, {"s3": ["App.java"]})
    assert wc.resolve_effective_artifact_owner(p, "App.java", p.subtask_by_id("s4"), order, only_failed) == \
        wc.OwnerResolution(artifact="App.java", owner_subtask_id=None,
                           resolution_basis=wc.ArtifactOwnerResolutionBasis.UNRESOLVED, evidence={})
    self_only = _state({"s2": "completed"}, {"s2": ["App.java"]})
    assert wc.resolve_effective_artifact_owner(p, "App.java", p.subtask_by_id("s2"), order, self_only).owner_subtask_id is None
    unwritten = _state({"s1": "completed"}, {"s1": ["Other.java"]})
    assert wc.resolve_effective_artifact_owner(p, "App.java", p.subtask_by_id("s3"), order, unwritten).owner_subtask_id is None


def test_the_dependency_ancestry_fallback():
    p = plan(st("s1", ["A.java"]), st("s2", ["B.java"], deps=["s1"]), st("s3", ["C.java"], deps=["s2"]),
             st("s4", ["D.java"]))
    resolution = wc.resolve_effective_artifact_owner(p, "A.java", p.subtask_by_id("s3"))
    assert (resolution.owner_subtask_id, resolution.resolution_basis, resolution.evidence) == (
        "s1", wc.ArtifactOwnerResolutionBasis.DEPENDENCY_ANCESTRY, {})
    assert wc.resolve_effective_artifact_owner(p, "D.java", p.subtask_by_id("s3")).owner_subtask_id is None
    assert wc.resolve_effective_artifact_owner(p, "C.java", p.subtask_by_id("s3")).owner_subtask_id is None
    assert wc.resolve_scope_conflict_owners(p, ["A.java", "B.java", "D.java", "C.java"], p.subtask_by_id("s3")) == {
        "s1": ["A.java"], "s2": ["B.java"]}


def test_execution_provenance_needs_both_the_order_and_the_control_state_else_the_ancestry_fallback_decides():
    # the docstring contract: "skipped entirely (falls through to step 2) when either is omitted"
    p = plan(st("s1", ["A.java"]), st("s2", ["B.java"], deps=["s1"]), st("s3", ["C.java"], deps=["s2"]))
    control = _state({"s1": "completed"}, {"s1": ["A.java"]})
    for order, state in ((["s1", "s2", "s3"], None), (None, control)):
        resolution = wc.resolve_effective_artifact_owner(p, "A.java", p.subtask_by_id("s3"), order, state)
        assert (resolution.owner_subtask_id, resolution.resolution_basis) == (
            "s1", wc.ArtifactOwnerResolutionBasis.DEPENDENCY_ANCESTRY)


def test_the_scope_conflict_owner_map_logs_every_resolution(caplog):
    p = plan(st("s1", ["A.java"]), st("s2", ["B.java"], deps=["s1"]), st("s4", ["D.java"]))
    with caplog.at_level(logging.INFO, logger="kriya.workflow.workflow_controller"):
        owners = wc.resolve_effective_scope_conflict_owners(p, ["A.java", "D.java"], p.subtask_by_id("s2"))
    assert owners == {"s1": ["A.java"]}
    messages = [r.getMessage() for r in caplog.records if r.name == "kriya.workflow.workflow_controller"]
    assert messages == ["ARTIFACT_OWNER_RESOLVED artifact=A.java owner_subtask_id=s1 resolution_basis=dependency_ancestry "
                        "prior_modifiers=[]",
                        "ARTIFACT_OWNER_UNRESOLVED artifact=D.java candidate_owners=[] reason=unresolved"]


# ============================================================================== owner recovery: verification texts

def test_the_verification_failure_evidence():
    assert wc.verification_failure_evidence({"last_failure": {"type": "static_analysis", "raw_output": "R",
                                                              "message": "M"}}) == "R"
    assert wc.verification_failure_evidence({"last_failure": {"type": "test", "message": "M"}}) == "M"
    assert wc.verification_failure_evidence({"last_failure": {"type": "requirements_unresolved", "raw_output": "R"}}) == (
        "the verification unit stopped with failure type 'requirements_unresolved'; its detail is not a gate output "
        "and is not shared")
    assert wc.verification_failure_evidence({"last_failure": {"type": "test", "raw_output": "x" * 2500}}) == "x" * 2000
    assert wc.verification_failure_evidence({"environment_failure": "E"}) == "E"
    text = wc.verification_owner_recovery_context(verification_subtask_id="v1", owner_id="s1", owner_files=["a"],
                                                  evidence="")
    assert "Deterministic verification evidence (the unit's own gate output):\nunavailable\n" in text


# ============================================================================== owner recovery: cross-owner obligation

def test_the_cross_owner_obligation_identity_authority_and_stickiness():
    ledger = ObligationLedger()
    conflict = {"failure_type": "diagnosis_mismatch", "reason": "needs X", "raw_evidence": "E",
                "attribution_tier": "compiler_locator"}
    assert wc._cross_owner_obligation_id("s3", "s1", ["b", "a"], "compile", 2) == "recovery.s3.s1.a.b.compile.2"
    first = wc._get_or_create_cross_owner_obligation(ledger, originating_subtask_id="s3", owner_subtask_id="s1",
                                                     required_files=["b", "a"], scope_conflict=conflict, generation=0,
                                                     revision="r")
    assert first.id == "recovery.s3.s1.a.b.diagnosis_mismatch.0"
    assert (first.authority, first.repair_scope, first.source, first.status) == (
        ObligationAuthority.JUDGMENT, ("a", "b"), "workflow_controller.owner_recovery", ObligationStatus.VIOLATED)
    assert first.evidence["generation"] == 0 and first.evidence["failure_family"] == "diagnosis_mismatch"
    assert first.evidence["must_preserve"] == ["existing project/module identity",
                                              "existing configuration and declarations unrelated to this requirement"]
    assert ledger.current(first.id) == first
    again = wc._get_or_create_cross_owner_obligation(ledger, originating_subtask_id="s3", owner_subtask_id="s1",
                                                     required_files=["a", "b"], scope_conflict={"failure_type": "diagnosis_mismatch",
                                                                                                "reason": "weaker"},
                                                     generation=0, revision="r2")
    assert again.evidence == first.evidence and again.authority == first.authority
    assert len(ledger.history(first.id)) == 2
    later = wc._get_or_create_cross_owner_obligation(ledger, originating_subtask_id="s4", owner_subtask_id="s1",
                                                     required_files=["a", "b"], scope_conflict={"reason": "needs Y"},
                                                     generation=1, revision="r3")
    assert later.id == "recovery.s4.s1.a.b.unknown.1"
    assert later.evidence["must_preserve"][2:] == ["needs X"]
    other_scope = wc._get_or_create_cross_owner_obligation(
        ledger, originating_subtask_id="s4", owner_subtask_id="s1", required_files=["a"], scope_conflict={"reason": "Z"},
        generation=1, revision="r4")
    assert other_scope.evidence["must_preserve"][2:] == []   # another artifact scope's corrections are not folded in


def test_the_scope_conflict_evidence_authority():
    from kriya.workflow.attribution import DETERMINISTIC_ATTRIBUTION_TIERS

    tier = sorted(DETERMINISTIC_ATTRIBUTION_TIERS)[0]
    assert wc._scope_conflict_evidence_authority({"attribution_tier": tier}) is ObligationAuthority.DETERMINISTIC
    assert wc._scope_conflict_evidence_authority({"attribution_tier": "self_diagnosis"}) is ObligationAuthority.JUDGMENT
    assert wc._scope_conflict_evidence_authority({"failure_type": "attribution_rejected",
                                                  "attribution_tier": tier}) is ObligationAuthority.JUDGMENT
    assert wc._participant_evidence_is_grounded({"failure_type": "compile"}) is True
    assert wc._participant_evidence_is_grounded({"failure_type": "attribution_rejected"}) is False


def test_the_owner_recovery_context_texts():
    record = ObligationRecord(id="o", kind=ObligationKind.CROSS_OWNER_ARTIFACT_REQUIREMENT,
                              status=ObligationStatus.VIOLATED, authority=ObligationAuthority.JUDGMENT, description="d",
                              source="s", evidence={"must_fix": ["f1", "f2"], "must_preserve": ["p1"],
                                                    "raw_evidence": "E", "acceptance_conditions": ["ok"]})
    assert wc._build_owner_recovery_context(owner_id="s1", failed_subtask_id="s3", required_owner_files=["a"],
                                            cross_owner_obligation=record, scope_conflict={}) == (
        "--- ACTIVE CROSS-OWNER RECOVERY REQUIREMENT ---\nReopened owner: s1\nOriginating failure: subtask s3\n"
        "Required repair files: [\"a\"]\n\nMUST FIX: f1; f2\nMUST PRESERVE: p1\nEVIDENCE: E\nACCEPTANCE: ok\n\n"
        "This is the exact grounded reason this owner was reopened - address it specifically, not a generic "
        "regeneration. Preserve every other existing declaration this file already has.")
    assert wc._build_owner_recovery_context(owner_id="s1", failed_subtask_id="s3", required_owner_files=["a"],
                                            cross_owner_obligation=None,
                                            scope_conflict={"failure_type": "compile"}) == (
        "--- authoritative plan recovery ---\nFailed consumer: s3\nReopened owner: s1\nRequired repair files: [\"a\"]\n"
        "Failure type: compile\nGrounded failure diagnosis: unavailable\nRepair only the reopened owner's approved "
        "files. Preserve its provided contracts and all relevant global invariants.")


def test_the_recovery_acceptance_precheck(tmp_path):
    (tmp_path / "a.txt").write_text("main=com.example.AppMain\n")
    check = wc._evaluate_recovery_acceptance_precheck
    assert check(scope_conflict={}, plan_workspace_path=str(tmp_path), required_owner_files=["a.txt"]) is None
    assert check(scope_conflict={"required_reference_token": "AppMain"}, plan_workspace_path=str(tmp_path),
                 required_owner_files=["gone.txt", "a.txt"]) is True
    assert check(scope_conflict={"required_reference_token": "App"}, plan_workspace_path=str(tmp_path),
                 required_owner_files=["a.txt"]) is False


# ============================================================================== owner recovery: the recovery plan

def test_the_recovery_participants():
    p = plan(st("s1", ["pom.xml"]), st("s2", ["App.java"], deps=["s1"]), st("s3", ["T.java"], deps=["s2"]))
    s3 = p.subtask_by_id("s3")
    participants = wc.derive_recovery_participants(
        p, {"grounded_owner_files": ["pom.xml", "Gone.java"], "required_files": ["App.java"], "reason": "R",
            "failure_type": "attribution_rejected"}, s3, recovery_generation_by_key={("s3", "s1", ("pom.xml",)): 1})
    assert [(x.artifact, x.role, x.effective_owner_subtask_id, x.owner_resolution_basis, x.mutation_reason,
             x.recovery_action) for x in participants] == [
        ("pom.xml", RecoveryParticipantRole.REQUIRED_MUTATION, "s1", "dependency_ancestry", "R", RecoveryAction.MUST_CHANGE),
        ("Gone.java", RecoveryParticipantRole.READ_ONLY_CONTEXT, None, "unresolved", "", RecoveryAction.MUST_CHANGE)]
    verify = wc.derive_recovery_participants(p, {"required_files": ["App.java"], "failure_type": "attribution_rejected"}, s3)
    assert [(x.artifact, x.recovery_action) for x in verify] == [("App.java", RecoveryAction.VERIFY)]
    control = _state({"s2": "completed"}, {"s2": ["pom.xml"]})
    [modified] = wc.derive_recovery_participants(p, {"required_files": ["pom.xml"]}, s3, order=["s1", "s2", "s3"],
                                                 control_state=control)
    assert (modified.effective_owner_subtask_id, modified.owner_resolution_basis) == ("s2", "latest_successful_modifier")


def test_the_recovery_groups_order_and_the_execution_plan():
    p = plan(st("s1", ["pom.xml"]), st("s2", ["src/App.java", "src/B.java"], deps=["s1"]),
             st("s4", ["src/Z.java"]), st("s3", ["src/test/T.java"], deps=["s2", "s4"]))
    s3 = p.subtask_by_id("s3")
    recovery = wc.build_recovery_execution_plan(
        p, {"required_files": ["src/B.java", "src/Z.java", "src/App.java", "pom.xml"], "reason": "R"}, s3,
        plan_generation=4)
    assert recovery.id == "recovery.s3.4" and recovery.originating_subtask_id == "s3"
    assert [(g.group_id, g.owner_subtask_id, [m.artifact for m in g.participants], g.depends_on_group_ids,
             g.relationship_basis) for g in recovery.groups] == [
        ("group.s1", "s1", ["pom.xml"], (), "file_role_priority"),
        ("group.s2", "s2", ["src/App.java", "src/B.java"], ("group.s1",), "plan_dependency"),
        ("group.s4", "s4", ["src/Z.java"], ("group.s1", "group.s2"), "file_role_priority")]
    assert recovery.group_order == ("group.s1", "group.s2", "group.s4") and recovery.active_group_id == "group.s1"
    assert wc.build_recovery_execution_plan(p, {}, s3) is None
    assert wc.build_recovery_execution_plan(p, {"required_files": ["src/App.java", "nowhere.py"]}, s3) is None
    assert wc._order_recovery_groups(p, ()) == ()


def test_an_ownerless_and_an_ambiguous_recovery_fail_closed(caplog):
    p = plan(st("s1", ["a.py"]), st("s2", ["b.py"], deps=["s1"]))
    with caplog.at_level(logging.WARNING, logger="kriya.workflow.workflow_controller"):
        assert wc.build_recovery_execution_plan(p, {}, p.subtask_by_id("s2")) is None   # nothing to recover: silent
        assert wc.build_recovery_execution_plan(p, {"required_files": ["zzz.py"]}, p.subtask_by_id("s2")) is None
    assert [r.getMessage().split(" ")[0] for r in caplog.records] == ["SCOPE_RECOVERY_OWNER_UNRESOLVED"]


def test_a_cyclic_owner_order_fails_closed(caplog):
    # an EngineeringPlan accepts a dependency cycle (validate_plan rejects it); the group order refuses to guess one
    p = plan(st("s1", ["a.py"], deps=["s2"]), st("s2", ["b.py"], deps=["s1"]), st("s3", ["c.py"], deps=["s1", "s2"]))
    s3 = p.subtask_by_id("s3")
    participants = wc.derive_recovery_participants(p, {"required_files": ["a.py", "b.py"]}, s3)
    assert wc._order_recovery_groups(p, participants) is None
    with caplog.at_level(logging.WARNING, logger="kriya.workflow.workflow_controller"):
        assert wc.build_recovery_execution_plan(p, {"required_files": ["a.py", "b.py"]}, s3) is None
    assert [r.getMessage().split(" ")[0] for r in caplog.records] == ["RECOVERY_GROUP_ORDER_AMBIGUOUS"]


def test_unrelated_recovery_owners_are_ordered_by_file_role_before_id():
    p = plan(st("s1", ["src/Z.java"]), st("s9", ["pom.xml"]), st("s3", ["src/test/T.java"], deps=["s1", "s9"]))
    recovery = wc.build_recovery_execution_plan(p, {"required_files": ["src/Z.java", "pom.xml"]}, p.subtask_by_id("s3"))
    assert recovery.group_order == ("group.s9", "group.s1")   # the build file's owner first, though s1 < s9


# ============================================================================== owner recovery: self-correction

def _Kernel(enabled):
    from _strict_doubles import strict_config, strict_kernel

    return strict_kernel(strict_config(autonomy={"self_correction_loop_enabled": enabled,
                                                 "self_correction_loop_max_turns": 3}))


@pytest.mark.asyncio
async def test_the_owner_recovery_self_correction_gate(tmp_path):
    (tmp_path / "pom.xml").write_text("<project/>")
    owner = st("s1", ["pom.xml", "other.xml"])
    seen = {}

    class Result:
        def __init__(self, resolved, modified):
            self.resolved, self.modified_files, self.turns_used = resolved, modified, 1

    outcome = {"value": Result(True, {"pom.xml": "x"})}

    async def loop(**kwargs):
        seen.update(kwargs)
        return outcome["value"]

    kwargs = dict(developer_llm=None, plan_workspace_path=str(tmp_path), workspace_path=str(tmp_path), owner=owner,
                  required_owner_files=["pom.xml", "gone.xml"], scope_conflict={"raw_evidence": "ERR"})
    with patch("kriya.workflow.self_correction.run_self_correction_loop", side_effect=loop):
        assert await wc._attempt_owner_recovery_self_correction(kernel=_Kernel(False), **kwargs) is None
        assert seen == {}
        assert await wc._attempt_owner_recovery_self_correction(
            kernel=_Kernel(True), **{**kwargs, "scope_conflict": {"raw_evidence": "  "}}) is None
        assert await wc._attempt_owner_recovery_self_correction(
            kernel=_Kernel(True), **{**kwargs, "required_owner_files": ["gone.xml"]}) is None
        assert seen == {}
        assert await wc._attempt_owner_recovery_self_correction(kernel=_Kernel(True), **kwargs) == {
            "status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        assert (seen["files_in_scope"], seen["writable_files"], seen["compile_error_output"], seen["max_turns"]) == (
            ["gone.xml", "other.xml", "pom.xml"], ["pom.xml", "gone.xml"], "ERR", 3)
        assert seen["active_code_context"] == "--- pom.xml ---\n<project/>"
        outcome["value"] = Result(True, {})
        assert await wc._attempt_owner_recovery_self_correction(kernel=_Kernel(True), **kwargs) is None


# ============================================================================== owner recovery: integration

def _integration_plan(**rel):
    return plan(st("s1", ["src/Service.java", "src/Extra.java"]), st("s2", ["src/App.java"], deps=["s1"]),
                integration_relationships=[IntegrationRelationship(
                    id="ir1", kind=IntegrationRelationshipKind.USES, producer_subtask_ids=["s1"],
                    consumer_subtask_ids=["s2"], relationship_statement="App uses Service", **rel)])


def _pending(ledger, rel_id="ir1"):
    ledger.record(ObligationRecord(id=f"plan.integration.{rel_id}", kind=ObligationKind.CROSS_SUBTASK_INTEGRATION,
                                   status=ObligationStatus.PENDING, authority=ObligationAuthority.DETERMINISTIC,
                                   description="App uses Service", source="seed", evidence={"seed": 1},
                                   owner_subtask_id="s2", terminal_required=True, repair_scope=("src/App.java",)))


def test_the_integration_obligation_without_provenance():
    p = _integration_plan(participating_artifacts=["src/Service.java", "src/App.java"])
    ledger = ObligationLedger()
    _pending(ledger)
    wc._evaluate_integration_obligations(p, ledger, "s1", {"src/App.java": "Service s;"}, "r")
    assert ledger.current("plan.integration.ir1").status is ObligationStatus.PENDING   # s1 is not the consumer
    wc._evaluate_integration_obligations(p, ledger, "s2", {"src/App.java": "MyServiceImpl s;"}, "r")
    record = ledger.current("plan.integration.ir1")
    assert (record.status, record.evidence, record.source, record.terminal_required) == (
        ObligationStatus.VIOLATED, {"seed": 1, "missing_producer_references": ["src/Service.java"]},
        "workflow_controller.integration_check", True)
    wc._evaluate_integration_obligations(p, ledger, "s2", {"src/App.java": "Service s;",
                                                           "src/Service.java": "class Service {}"}, "r")
    assert ledger.current("plan.integration.ir1").status is ObligationStatus.VIOLATED   # settled: never re-evaluated
    fresh = ObligationLedger()
    _pending(fresh)
    wc._evaluate_integration_obligations(p, fresh, "s2", {"src/App.java": "Service s;",
                                                          "src/Service.java": "class Service {}"}, "r")
    assert fresh.current("plan.integration.ir1").status is ObligationStatus.SATISFIED
    unestablished = ObligationLedger()
    _pending(unestablished)
    wc._evaluate_integration_obligations(p, unestablished, "s2", {"src/App.java": "Service s;"}, "r")
    assert unestablished.current("plan.integration.ir1").evidence["missing_producer_references"] == ["src/Service.java"]


def test_the_integration_evidence_with_provenance(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "Service.java").write_text("class Service {}")
    (src / "App.java").write_text("class App { Service s; }")
    provenance = wc.EstablishedProvenance(str(tmp_path))
    provenance.record("src/Service.java", "s1")
    provenance.record("src/App.java", "s2")
    p = _integration_plan(participating_artifacts=["src/Service.java", "src/App.java"])
    rel = p.integration_relationships[0]
    missing, evidence = wc._integration_evidence(p, rel, "s2", ["src/Service.java"], ["src/App.java"],
                                                 {"src/App.java": "class App { Service s; }"}, provenance)
    assert missing == [] and evidence == {
        "provider_evidence": {"src/Service.java": {"state": "valid", "established_by": "s1"}},
        "consumer_evidence": {"paths": ["src/App.java"], "sources": {"src/App.java": "written_this_run"},
                              "verification": ["tool:compile"]},
        "evaluation": "reference"}
    missing, _evidence = wc._integration_evidence(p, rel, "s2", ["src/Service.java"], ["src/App.java"],
                                                  {"src/App.java": "class App { ServiceImpl s; }"}, provenance)
    assert missing == ["src/Service.java"]   # a whole-word reference, never a substring
    other = wc.EstablishedProvenance(str(tmp_path))
    other.record("src/Service.java", "s1")
    other.record("src/App.java", "s9")
    missing, evidence = wc._integration_evidence(p, rel, "s2", ["src/Service.java"], ["src/App.java"],
                                                 {"src/App.java": "class App { }"}, other)
    assert missing == [] and evidence["consumer_evidence"]["sources"] == {"src/App.java": "current_workspace"}
    (src / "Service.java").write_text("class Service { int changed; }")
    missing, evidence = wc._integration_evidence(p, rel, "s2", ["src/Service.java"], ["src/App.java"],
                                                 {"src/App.java": "class App { Service s; }"}, provenance)
    assert missing == ["src/Service.java"]
    assert evidence["failure_reason"] == "src/Service.java: invalidated"
    verification_only = plan(st("s1", ["src/Service.java"]), st("s2", [], deps=["s1"], verification=(),
                                                                 role=ExecutionRole.IMPLEMENTATION))
    missing, evidence = wc._integration_evidence(verification_only, rel, "s2", ["src/Service.java"], [], {}, provenance)
    assert (missing, evidence["evaluation"], evidence["failure_reason"]) == (
        ["src/Service.java"], "verification_only_consumer", "consumer has no planned artifact and no declared verification")
    missing, _evidence = wc._integration_evidence(verification_only, rel, "s2", [], [], {}, provenance)
    assert missing == ["<no consumer evidence>"]


def test_the_integration_obligation_with_provenance_mirrors_its_decision(tmp_path, monkeypatch):
    from kriya.core.attempt_evidence import scope as attempt_evidence_scope

    src = tmp_path / "src"
    src.mkdir()
    (src / "Service.java").write_text("class Service {}")
    provenance = wc.EstablishedProvenance(str(tmp_path))
    provenance.record("src/Service.java", "s1")
    provenance.record("src/App.java", "s2")
    events = []
    monkeypatch.setattr(attempt_evidence_scope, "mirror_event", events.append)
    p = _integration_plan()
    ledger = ObligationLedger()
    _pending(ledger)
    wc._evaluate_integration_obligations(p, ledger, "s2", {"src/App.java": "Service s;"}, "r", provenance=provenance)
    record = ledger.current("plan.integration.ir1")
    assert record.status is ObligationStatus.VIOLATED   # src/Extra.java (all s1 files: no participating list) missing
    assert record.evidence["missing_producer_references"] == ["src/Extra.java"]
    assert record.evidence["evaluation"] == "reference"
    [event] = events
    assert (event.kind, event.message, event.details["status"], event.details["missing_producer_references"]) == (
        "integration.obligation", "plan.integration.ir1 VIOLATED", "violated", ["src/Extra.java"])
    events.clear()
    other = ObligationLedger()
    _pending(other)
    wc._evaluate_integration_obligations(p, other, "s2", {"src/App.java": "Service s;"}, "r")
    assert events == []


# ============================================================================== resume support

def test_tool_subtasks_are_never_resume_completed():
    p = plan(st("s1", ["a.py"]), st("t2", [], method=ExecutionMethod.TOOL, verification=(COMPILE,)),
             st("t1", [], method=ExecutionMethod.TOOL, verification=(COMPILE,)))
    states = {"s1": "completed", "t2": "completed", "t1": "completed", "gone": "completed", "t3": "failed"}
    filtered, excluded = wc.exclude_tool_subtasks_from_resume(p, states)
    assert excluded == ["t1", "t2"] and filtered == {"s1": "completed", "gone": "completed", "t3": "failed"}
    assert wc.exclude_tool_subtasks_from_resume(p, {"s1": "completed"}) == ({"s1": "completed"}, [])


def test_all_subtasks_completed_reads_the_current_plan_only():
    done = SubtaskStatus.COMPLETED
    assert wc.all_subtasks_completed({"a", "b"}, {"a": done, "b": done, "gone": SubtaskStatus.FAILED}) is True
    assert wc.all_subtasks_completed({"a", "b"}, {"a": done, "b": SubtaskStatus.FAILED}) is False
    assert wc.all_subtasks_completed({"a", "b"}, {"a": done}) is False


def test_the_abandoned_plan_files_and_their_quarantine(tmp_path):
    assert wc.compute_abandoned_plan_files(
        {"s1": "completed", "s2": "failed", "s3": "completed"},
        {"s1": ["z.py", "keep.py"], "s2": ["fail.py"], "s3": ["a.py"]}, ["keep.py"]) == ["a.py", "z.py"]
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("A")
    moved = wc.quarantine_abandoned_plan_files(str(tmp_path), ["pkg/a.py", "gone.py"], "run-1")
    assert moved == ["pkg/a.py"]
    assert not (tmp_path / "pkg" / "a.py").exists()
    assert (tmp_path / ".kriya" / "abandoned_plan_files" / "run-1" / "pkg" / "a.py").read_text() == "A"


def _ownerless(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _owned(path, workspace):
    return (json.loads(path.read_text()).get("_workspace") or {}).get("workspace_id") == workspace_identity(str(workspace))


def _legacy_workspace(tmp_path, *, owned_checkpoint=True):
    from kriya.control.artifacts import ArtifactRegistry
    from kriya.control.contracts import ContractRegistry
    from kriya.workflow.checkpoint import save_checkpoint

    control = tmp_path / ".kriya" / "control"
    _ownerless(control / "state.json", ControlState.new(run_id="old").to_dict())
    _ownerless(control / "contracts.json", ContractRegistry().to_dict())
    _ownerless(control / "artifacts.json", ArtifactRegistry().to_dict())
    _ownerless(tmp_path / ".kriya" / "checkpoints" / "legacy-run.json", {"run_id": "legacy-run", "stage": "plan"})
    _ownerless(tmp_path / ".kriya" / "checkpoints" / "no-id.json", {"stage": "plan"})
    if owned_checkpoint:
        save_checkpoint(str(tmp_path), "owned-run", {"run_id": "owned-run", "stage": "plan"})
    ledger = control / "decisions.jsonl"
    ledger.write_text(json.dumps({"type": "x", "fields": {}, "timestamp": "t"}) + "\n")


def test_the_controller_migration_stamps_every_legacy_store_and_records_one_decision(tmp_path):
    _legacy_workspace(tmp_path)
    saved = {"control": 0, "artifacts": 0}
    from kriya.control.persistence import save_artifact_registry, save_control_state

    def control_spy(*args, **kwargs):
        saved["control"] += 1
        return save_control_state(*args, **kwargs)

    def artifacts_spy(*args, **kwargs):
        saved["artifacts"] += 1
        return save_artifact_registry(*args, **kwargs)

    with patch("kriya.workflow.workflow_controller.save_control_state", side_effect=control_spy), \
            patch("kriya.workflow.workflow_controller.save_artifact_registry", side_effect=artifacts_spy):
        migrated = wc._migrate_legacy_controller_ownership(str(tmp_path), "new-run")
    assert saved == {"control": 1, "artifacts": 1}   # the controller's names, resolved at call time
    assert migrated == ("artifact_registry", "checkpoints:1", "contract_registry", "control_state",
                        "decision_ledger_entries:1")
    control = tmp_path / ".kriya" / "control"
    for path in (control / "state.json", control / "contracts.json", control / "artifacts.json",
                 tmp_path / ".kriya" / "checkpoints" / "legacy-run.json"):
        assert _owned(path, tmp_path), path.name
    decisions = [d for d in load_decision_ledger(str(tmp_path)).all() if d.type == "legacy_workspace_ownership_migrated"]
    assert [d.fields for d in decisions] == [{"stores": list(migrated), "run_id": "new-run"}]
    assert wc._migrate_legacy_controller_ownership(str(tmp_path), "again") == ()
    assert len([d for d in load_decision_ledger(str(tmp_path)).all()
                if d.type == "legacy_workspace_ownership_migrated"]) == 1


def test_the_milestone_migration_stamps_the_legacy_milestone_stores(tmp_path):
    from kriya.control.artifacts import ArtifactRegistry
    from kriya.control.contracts import ContractRegistry
    from kriya.workflow.milestones import MilestoneRunState

    _legacy_workspace(tmp_path)
    run_state = MilestoneRunState(group_id="grp", original_goal="g", milestones=[])
    _ownerless(tmp_path / ".kriya" / "milestones" / "grp.json", run_state.to_dict())
    with patch("kriya.workflow.workflow_controller.save_artifact_registry",
               side_effect=__import__("kriya.control.persistence", fromlist=["x"]).save_artifact_registry) as artifacts:
        migrated = wc._migrate_legacy_milestone_ownership(
            str(tmp_path), run_state, ContractRegistry(), ArtifactRegistry(), prior_control_state_ownerless=True)
    assert artifacts.call_count == 1
    assert migrated == ("artifact_registry", "checkpoints:1", "contract_registry", "control_state",
                        "decision_ledger_entries:1", "milestone_run_state")
    for rel in ("control/contracts.json", "control/artifacts.json", "milestones/grp.json", "checkpoints/legacy-run.json"):
        assert _owned(tmp_path / ".kriya" / rel, tmp_path), rel
    assert not _owned(tmp_path / ".kriya" / "control" / "state.json", tmp_path)   # reported, never re-saved here
    decisions = [d for d in load_decision_ledger(str(tmp_path)).all() if d.type == "legacy_workspace_ownership_migrated"]
    assert [d.fields for d in decisions] == [{"stores": list(migrated), "milestone_group_id": "grp"}]
    assert wc._migrate_legacy_milestone_ownership(
        str(tmp_path), run_state, ContractRegistry(), ArtifactRegistry(), prior_control_state_ownerless=False) == ()
