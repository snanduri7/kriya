"""MA6.1: EngineeringPlan/Subtask schema (kriya/workflow/plan_schema.py) -
first real pytest coverage for this module. No tests existed for any MA6
module before this; see project memory for why."""

import pytest
from pydantic import ValidationError

from kriya.workflow.plan_schema import (
    AcceptanceCriterion,
    EngineeringPlan,
    ExecutionMethod,
    ExecutionRole,
    FileAction,
    FileOwnershipRelation,
    GlobalInvariant,
    PlannedFile,
    PlannerStructuredOutput,
    RequirementOwnershipRelation,
    Subtask,
    VerificationMethod,
    VerificationMethodType,
    VerifierKind,
    build_engineering_plan_from_planner_output,
)
from kriya.workflow.triage import ChangeKind


def _model_subtask(**overrides):
    defaults = dict(id="s1", description="do a thing", execution_method=ExecutionMethod.MODEL)
    defaults.update(overrides)
    return Subtask(**defaults)


def _tool_subtask(**overrides):
    defaults = dict(id="s1", description="run a tool", execution_method=ExecutionMethod.TOOL, tool_name="lint")
    defaults.update(overrides)
    return Subtask(**defaults)


# --- PlannedFile ---

def test_planned_file_rejects_absolute_path():
    with pytest.raises(ValidationError):
        PlannedFile(path="/etc/passwd", action=FileAction.MODIFY)


def test_planned_file_rejects_path_traversal():
    with pytest.raises(ValidationError):
        PlannedFile(path="a/../../b.py", action=FileAction.CREATE)


def test_planned_file_rejects_blank_path():
    with pytest.raises(ValidationError):
        PlannedFile(path="   ", action=FileAction.CREATE)


def test_planned_file_accepts_a_real_relative_path():
    pf = PlannedFile(path="src/a.py", action=FileAction.MODIFY)
    assert pf.path == "src/a.py"


def test_planned_file_rejects_glob_wildcard_path():
    """Regression test for a real live bug, PRV-05 (2026-08-28): the Planner
    returned "src/main/java/**/*.java" as a planned_files[].path - a glob
    pattern, not a real file. Nothing rejected it, so every downstream
    consumer (Developer generation, the write loop, the compile gate)
    treated the literal string as an actual filename for the rest of the
    run - every retry failed identically against a file that could never
    satisfy javac's "class X must be declared in a file named X.java" rule,
    since the "filename" itself was never a real name at all."""
    with pytest.raises(ValidationError):
        PlannedFile(path="src/main/java/**/*.java", action=FileAction.MODIFY)


def test_planned_file_rejects_single_star_wildcard_path():
    with pytest.raises(ValidationError):
        PlannedFile(path="src/main/*.java", action=FileAction.MODIFY)


def test_planned_file_rejects_bracket_glob_path():
    with pytest.raises(ValidationError):
        PlannedFile(path="src/main/[A-Z]*.java", action=FileAction.MODIFY)


def test_planned_file_rejects_directory_path():
    """Regression test for a real live bug, PRV-17 (2026-09-03): the Planner
    returned "customers_project/" (a directory, trailing slash) as a
    planned_files[].path alongside real files nested under it. Nothing
    rejected it, so it flowed straight into allowed_write_relpaths as a
    literal generation target the Developer was asked to "write" - it never
    produced real content for a directory, and the later authorized-write
    comparison then rejected the Developer's own (correctly file-shaped)
    target as outside scope, since the plan's entry and the generated entry
    were never the same string. A PlannedFile names exactly ONE concrete
    file, the same reasoning as the glob-wildcard rejection above; parent
    directories are created deterministically as a side effect of writing
    the files under them."""
    with pytest.raises(ValidationError):
        PlannedFile(path="customers_project/", action=FileAction.CREATE)


def test_planned_file_rejects_directory_path_with_backslash_separator():
    with pytest.raises(ValidationError):
        PlannedFile(path="customers_project\\", action=FileAction.CREATE)


def test_planned_file_accepts_a_real_file_nested_under_a_would_be_directory_name():
    """The directory rejection above must not reject an ordinary file just
    because a sibling planned_files entry names its parent directory - only
    the bare, trailing-slash directory entry itself is a shape defect."""
    pf = PlannedFile(path="customers_project/manage.py", action=FileAction.CREATE)
    assert pf.path == "customers_project/manage.py"


# --- VerificationMethod ---

def test_verification_method_tool_requires_tool_name():
    with pytest.raises(ValidationError):
        VerificationMethod(type=VerificationMethodType.TOOL, description="compile check")


def test_verification_method_judgment_must_not_set_tool_name():
    with pytest.raises(ValidationError):
        VerificationMethod(type=VerificationMethodType.JUDGMENT, description="looks right", tool_name="pytest")


def test_verification_method_tool_with_tool_name_is_valid():
    vm = VerificationMethod(type=VerificationMethodType.TOOL, description="compile", tool_name="compile_check")
    assert vm.tool_name == "compile_check"


def test_verification_method_explicitly_declares_runtime_execution_requirement():
    vm = VerificationMethod(
        type=VerificationMethodType.JUDGMENT,
        description="observe the assembled behavior",
        requires_runtime_execution=True,
    )
    assert vm.requires_runtime_execution is True


def test_test_runtime_means_test_process_not_application_runtime():
    vm = VerificationMethod(
        type=VerificationMethodType.TOOL,
        description="execute unit tests",
        tool_name="test",
        requires_runtime_execution=True,
    )
    assert vm.verifier_kind is VerifierKind.TEST
    assert vm.requires_application_runtime is False


def test_application_runtime_verifier_explicitly_requires_application_execution():
    vm = VerificationMethod(
        type=VerificationMethodType.TOOL,
        description="execute the application",
        tool_name="run_app",
        verifier_kind=VerifierKind.APPLICATION_RUNTIME,
        requires_runtime_execution=True,
    )
    assert vm.requires_application_runtime is True


def test_application_runtime_verifier_self_heals_requires_runtime_execution():
    """Verification-routing fix (PRV-06, 2026-08-29) - live incident: the
    Planner's own initial system prompt (unlike its repair-prompt sibling)
    never paired verifier_kind=application_runtime with requires_runtime_
    execution=true, and plan_validation.py enforced no such pairing either.
    A verification-only subtask (files=[], write_scope_mode=DENY_ALL)
    whose sole verifier had verifier_kind=application_runtime but
    requires_runtime_execution=False silently failed attempt.py's own
    _directly_executable_runtime_verifiers() AND check, fell through to
    the ordinary Developer-generation retry loop, and burned 5 attempts
    inventing files it could never write before real runtime verification
    ever ran. There is no legitimate reason to classify a verifier as
    APPLICATION_RUNTIME while claiming it does not require runtime
    execution, so this is a deterministic self-heal, not a new inference
    heuristic - it may only ever flip False -> True."""
    vm = VerificationMethod(
        type=VerificationMethodType.JUDGMENT,
        description="run the application with sample input",
        verifier_kind=VerifierKind.APPLICATION_RUNTIME,
        requires_runtime_execution=False,
    )
    assert vm.requires_runtime_execution is True
    assert vm.requires_application_runtime is True


def test_non_runtime_verifier_is_never_touched_by_the_self_heal():
    vm = VerificationMethod(type=VerificationMethodType.JUDGMENT, description="check style")
    assert vm.verifier_kind is VerifierKind.JUDGMENT
    assert vm.requires_runtime_execution is False


def test_directly_executable_runtime_verifiers_matches_a_self_healed_entry():
    from kriya.workflow.attempt import _directly_executable_runtime_verifiers

    vm = VerificationMethod(
        type=VerificationMethodType.JUDGMENT,
        description="run the application with sample input",
        verifier_kind=VerifierKind.APPLICATION_RUNTIME,
        requires_runtime_execution=False,
    )
    matched = _directly_executable_runtime_verifiers([vm.model_dump(mode="json")])
    assert len(matched) == 1


def test_has_evidence_producer_true_for_application_runtime_with_runtime_true():
    vm = VerificationMethod(
        type=VerificationMethodType.JUDGMENT,
        description="run the application and observe uppercase output",
        verifier_kind=VerifierKind.APPLICATION_RUNTIME,
        requires_runtime_execution=True,
    )
    assert vm.has_evidence_producer is True


def test_has_evidence_producer_true_for_compile_tool():
    vm = VerificationMethod(type=VerificationMethodType.TOOL, description="compiles", tool_name="compile")
    assert vm.has_evidence_producer is True


def test_has_evidence_producer_false_for_judgment_with_no_runtime_and_no_tool():
    """PRV-11 (2026-08-30): the exact shape a live incident proved Kriya can
    plan but never execute - type=judgment, tool_name=None,
    requires_runtime_execution=false. Neither the compile/test executor nor
    the runtime/judge executor can ever produce evidence for it."""
    vm = VerificationMethod(
        type=VerificationMethodType.JUDGMENT,
        description="Verify that the application output shows transformed customer name in uppercase",
    )
    assert vm.tool_name is None
    assert vm.requires_runtime_execution is False
    assert vm.has_evidence_producer is False


def test_has_evidence_producer_true_after_application_runtime_self_heal():
    """The pairing self-heal (application_runtime -> requires_runtime_execution=True)
    must leave this entry with a real producer - the two mechanisms must agree."""
    vm = VerificationMethod(
        type=VerificationMethodType.JUDGMENT,
        description="run the application with sample input",
        verifier_kind=VerifierKind.APPLICATION_RUNTIME,
        requires_runtime_execution=False,
    )
    assert vm.requires_runtime_execution is True
    assert vm.has_evidence_producer is True


# --- AcceptanceCriterion ---

def test_acceptance_criterion_blank_id_rejected():
    with pytest.raises(ValidationError):
        AcceptanceCriterion(id="  ", description="works")


def test_acceptance_criterion_tool_method_requires_tool_name():
    with pytest.raises(ValidationError):
        AcceptanceCriterion(id="ac1", description="compiles", method=VerificationMethodType.TOOL)


def test_acceptance_criterion_judgment_method_must_not_set_tool_name():
    with pytest.raises(ValidationError):
        AcceptanceCriterion(
            id="ac1", description="looks right", method=VerificationMethodType.JUDGMENT, tool_name="pytest",
        )


def test_acceptance_criterion_defaults_to_judgment():
    ac = AcceptanceCriterion(id="ac1", description="works")
    assert ac.method == VerificationMethodType.JUDGMENT


# --- Subtask ---

def test_subtask_blank_id_rejected():
    with pytest.raises(ValidationError):
        _model_subtask(id="   ")


def test_subtask_tool_execution_requires_tool_name():
    with pytest.raises(ValidationError):
        Subtask(id="s1", description="run", execution_method=ExecutionMethod.TOOL)


def test_subtask_model_execution_must_not_set_tool_name():
    with pytest.raises(ValidationError):
        Subtask(id="s1", description="write code", execution_method=ExecutionMethod.MODEL, tool_name="lint")


def test_subtask_model_execution_must_not_set_tool_arguments():
    with pytest.raises(ValidationError):
        Subtask(
            id="s1", description="write code", execution_method=ExecutionMethod.MODEL,
            tool_arguments={"path": "a.py"},
        )


def test_subtask_cannot_depend_on_itself():
    with pytest.raises(ValidationError):
        _model_subtask(depends_on=["s1"])


def test_subtask_rejects_duplicate_dependency():
    with pytest.raises(ValidationError):
        _model_subtask(depends_on=["s2", "s2"])


def test_subtask_valid_tool_subtask():
    st = _tool_subtask(tool_arguments={"path": "a.py"})
    assert st.execution_method == ExecutionMethod.TOOL
    assert st.tool_arguments == {"path": "a.py"}


def test_subtask_valid_model_subtask_with_dependency():
    st = _model_subtask(depends_on=["s0"])
    assert st.depends_on == ["s0"]


def test_subtask_semantic_contract_metadata_round_trips():
    subtask = _model_subtask(
        provides=["build.ready"], requires=["config.ready"],
        relevant_global_invariant_ids=["gi1"],
    )
    restored = Subtask.model_validate(subtask.model_dump(mode="json"))
    assert restored.provides == ["build.ready"]
    assert restored.requires == ["config.ready"]
    assert restored.relevant_global_invariant_ids == ["gi1"]


# --- GlobalInvariant (PRV-06, 2026-08-28) ---

def test_global_invariant_rejects_blank_id():
    with pytest.raises(ValidationError):
        GlobalInvariant(id=" ", statement="must exit cleanly")


def test_global_invariant_rejects_blank_statement():
    with pytest.raises(ValidationError):
        GlobalInvariant(id="gi1", statement=" ")


def test_engineering_plan_rejects_duplicate_global_invariant_ids():
    with pytest.raises(ValidationError):
        EngineeringPlan(
            plan_id="p1", kind=ChangeKind.TASK, subtasks=[_model_subtask()],
            global_invariants=[
                GlobalInvariant(id="gi1", statement="a"),
                GlobalInvariant(id="gi1", statement="b"),
            ],
        )


# --- ExecutionRole (PRV-05, 2026-08-28) ---

def test_subtask_execution_role_defaults_to_implementation():
    """Backward compatibility: every plan/checkpoint predating this field
    deserializes unchanged."""
    st = _model_subtask()
    assert st.execution_role == ExecutionRole.IMPLEMENTATION


def test_subtask_verification_role_accepts_zero_planned_files():
    st = _model_subtask(
        execution_role=ExecutionRole.VERIFICATION,
        planned_files=[],
        verification=[VerificationMethod(
            type=VerificationMethodType.TOOL, description="run tests",
            tool_name="test", verifier_kind=VerifierKind.TEST,
        )],
    )
    assert st.execution_role == ExecutionRole.VERIFICATION
    assert st.planned_files == []


def test_subtask_verification_role_rejects_planned_files():
    """A verification-only subtask must never own writable files - the
    complementary invariant to PRV-04's UNREQUESTED_ARCHITECTURAL_SURFACE:
    verification observes and judges, it does not mutate architecture."""
    with pytest.raises(ValidationError):
        _model_subtask(
            execution_role=ExecutionRole.VERIFICATION,
            planned_files=[PlannedFile(path="a.py", action=FileAction.MODIFY)],
            verification=[VerificationMethod(type=VerificationMethodType.JUDGMENT, description="check")],
        )


def test_subtask_verification_role_requires_at_least_one_verifier():
    with pytest.raises(ValidationError):
        _model_subtask(execution_role=ExecutionRole.VERIFICATION, planned_files=[])


# --- EngineeringPlan ---

def test_engineering_plan_rejects_blank_plan_id():
    with pytest.raises(ValidationError):
        EngineeringPlan(plan_id=" ", kind=ChangeKind.TASK, subtasks=[_model_subtask()])


def test_engineering_plan_rejects_zero_subtasks():
    with pytest.raises(ValidationError):
        EngineeringPlan(plan_id="p1", kind=ChangeKind.TASK, subtasks=[])


def test_engineering_plan_subtask_by_id_found_and_missing():
    plan = EngineeringPlan(plan_id="p1", kind=ChangeKind.TASK, subtasks=[_model_subtask(id="s1")])
    assert plan.subtask_by_id("s1").id == "s1"
    assert plan.subtask_by_id("does-not-exist") is None


def test_engineering_plan_resolves_only_unique_file_owner():
    owner = _model_subtask(
        id="s1", planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)],
    )
    plan = EngineeringPlan(
        plan_id="p1", kind=ChangeKind.TASK, subtasks=[owner],
        global_invariants=[GlobalInvariant(id="gi1", statement="one application")],
    )
    assert plan.file_owner("pom.xml") == owner
    assert plan.file_owner("missing.xml") is None


def test_engineering_plan_content_hash_is_stable_and_deterministic():
    plan_a = EngineeringPlan(plan_id="p1", kind=ChangeKind.TASK, subtasks=[_model_subtask(id="s1")])
    plan_b = EngineeringPlan(plan_id="p1", kind=ChangeKind.TASK, subtasks=[_model_subtask(id="s1")])
    assert plan_a.content_hash() == plan_b.content_hash()
    assert plan_a.content_hash() == plan_a.content_hash()


def test_engineering_plan_content_hash_changes_with_content():
    plan_a = EngineeringPlan(plan_id="p1", kind=ChangeKind.TASK, subtasks=[_model_subtask(id="s1")])
    plan_b = EngineeringPlan(plan_id="p1", kind=ChangeKind.TASK, subtasks=[_model_subtask(id="s2")])
    assert plan_a.content_hash() != plan_b.content_hash()


# --- build_engineering_plan_from_planner_output ---

def test_build_engineering_plan_returns_none_for_zero_subtasks():
    output = PlannerStructuredOutput(subtasks=[])
    plan = build_engineering_plan_from_planner_output(output, plan_id="p1", kind=ChangeKind.TASK)
    assert plan is None


def test_build_engineering_plan_supplies_plan_id_and_kind_from_caller_not_output():
    output = PlannerStructuredOutput(
        subtasks=[_model_subtask(id="s1")],
        acceptance_criteria=[AcceptanceCriterion(id="ac1", description="works")],
        extension_points=["kriya.tools.registry"],
        refactor_baseline="abc123",
    )
    plan = build_engineering_plan_from_planner_output(output, plan_id="run-42", kind=ChangeKind.REFACTOR)
    assert plan is not None
    assert plan.plan_id == "run-42"
    assert plan.kind == ChangeKind.REFACTOR
    assert plan.extension_points == ["kriya.tools.registry"]
    assert plan.refactor_baseline == "abc123"
    assert len(plan.subtasks) == 1


# --- EngineeringPlan.classify_file_ownership / FileOwnershipRelation
# (PRV-05 run 7, 2026-08-28) ---

def _prv05_plan():
    """Reproduces the exact validated PRV-05 hardened run-7 plan: s1/s2
    sequentially co-own JsonService.java (a validated sequential ownership
    chain, plan_validation.py's _forms_sequential_ownership_chain), s3 owns
    the test file, s4 (later, dependency-ordered) owns pom.xml, s5 is
    terminal."""
    s1 = _model_subtask(
        id="s1", depends_on=[],
        planned_files=[PlannedFile(path="src/main/java/com/example/JsonService.java", action=FileAction.MODIFY)],
    )
    s2 = _model_subtask(
        id="s2", depends_on=["s1"],
        planned_files=[PlannedFile(path="src/main/java/com/example/JsonService.java", action=FileAction.MODIFY)],
    )
    s3 = _model_subtask(
        id="s3", depends_on=["s1"],
        planned_files=[PlannedFile(path="src/test/java/com/example/JsonServiceTest.java", action=FileAction.CREATE)],
    )
    s4 = _model_subtask(
        id="s4", depends_on=["s2", "s3"],
        planned_files=[PlannedFile(path="pom.xml", action=FileAction.MODIFY)],
    )
    s5 = _model_subtask(id="s5", depends_on=["s4"], planned_files=[])
    return EngineeringPlan(plan_id="prv05", kind=ChangeKind.REFACTOR, subtasks=[s1, s2, s3, s4, s5])


def test_classify_file_ownership_current_for_sequential_co_owner():
    """JsonService.java is legitimately co-owned by BOTH s1 and s2 in a
    validated sequential chain - CURRENT must fire for either, not just a
    sole owner. This is why the helper re-derives owners directly rather
    than reusing file_owner(), whose single-owner-only lookup would return
    None (treating this as "ambiguous") for a path exactly like this one."""
    plan = _prv05_plan()
    path = "src/main/java/com/example/JsonService.java"
    assert plan.classify_file_ownership("s1", path) == FileOwnershipRelation.CURRENT
    assert plan.classify_file_ownership("s2", path) == FileOwnershipRelation.CURRENT


def test_classify_file_ownership_future_ordered_for_not_yet_reached_owner():
    plan = _prv05_plan()
    assert plan.classify_file_ownership("s1", "pom.xml") == FileOwnershipRelation.FUTURE_ORDERED
    assert plan.classify_file_ownership("s2", "pom.xml") == FileOwnershipRelation.FUTURE_ORDERED
    assert plan.classify_file_ownership("s3", "pom.xml") == FileOwnershipRelation.FUTURE_ORDERED


def test_classify_file_ownership_current_at_the_owning_stage():
    plan = _prv05_plan()
    assert plan.classify_file_ownership("s4", "pom.xml") == FileOwnershipRelation.CURRENT


def test_classify_file_ownership_past_ordered_for_completed_predecessor():
    plan = _prv05_plan()
    path = "src/main/java/com/example/JsonService.java"
    assert plan.classify_file_ownership("s4", path) == FileOwnershipRelation.PAST_ORDERED
    assert plan.classify_file_ownership("s3", path) == FileOwnershipRelation.PAST_ORDERED


def test_classify_file_ownership_unowned_when_no_subtask_declares_the_path():
    plan = _prv05_plan()
    assert plan.classify_file_ownership("s1", "README.md") == FileOwnershipRelation.UNOWNED


def test_classify_file_ownership_unrelated_for_parallel_unordered_owners():
    """Two subtasks with no dependency relationship between them, each
    owning a different file - neither is FUTURE_ORDERED nor PAST_ORDERED
    relative to the other, so this is a genuine plan-scope conflict, not a
    timing question (see this module's own FileOwnershipRelation docstring)."""
    a = _model_subtask(id="a", depends_on=[], planned_files=[PlannedFile(path="a.py", action=FileAction.MODIFY)])
    b = _model_subtask(id="b", depends_on=[], planned_files=[PlannedFile(path="b.py", action=FileAction.MODIFY)])
    plan = EngineeringPlan(plan_id="p1", kind=ChangeKind.TASK, subtasks=[a, b])
    assert plan.classify_file_ownership("a", "b.py") == FileOwnershipRelation.UNRELATED


# --- EngineeringPlan.classify_requirement_ownership / RequirementOwnershipRelation
# (PRV-17, 2026-09-03) ---

def _stage_ownership_plan():
    """s1 -> s2 -> s3 -> s4 (linear chain), plus s5 with no dependency
    relationship to any of them - the same DAG shape _prv05_plan() above
    uses for file ownership, minus the co-owner branch (a requirement's
    OWN claimant list, not planned_files, drives ownership here)."""
    s1 = _model_subtask(id="s1", depends_on=[])
    s2 = _model_subtask(id="s2", depends_on=["s1"])
    s3 = _model_subtask(id="s3", depends_on=["s2"])
    s4 = _model_subtask(id="s4", depends_on=["s3"])
    s5 = _model_subtask(id="s5", depends_on=[])
    return EngineeringPlan(plan_id="stage-ownership", kind=ChangeKind.TASK, subtasks=[s1, s2, s3, s4, s5])


def test_classify_requirement_ownership_current_when_sole_claimant():
    plan = _stage_ownership_plan()
    assert plan.classify_requirement_ownership("s1", ["s1"]) == RequirementOwnershipRelation.CURRENT


def test_classify_requirement_ownership_future_ordered_for_a_real_downstream_claimant():
    """The real live shape this fix targets: a Planner marks the SAME
    requirement relevant to both an earlier subtask AND a genuinely later,
    dependency-ordered one - the later, not-yet-run claimant proves the
    plan still schedules it, so the earlier subtask's own claim must defer
    to it, not be treated as already due."""
    plan = _stage_ownership_plan()
    assert plan.classify_requirement_ownership("s1", ["s1", "s4"]) == RequirementOwnershipRelation.FUTURE_ORDERED
    # current need not claim it at all for this to hold - a downstream-only
    # claim is still proof enough that current isn't the (or a) due owner.
    assert plan.classify_requirement_ownership("s1", ["s4"]) == RequirementOwnershipRelation.FUTURE_ORDERED


def test_classify_requirement_ownership_unrelated_duplicate_does_not_defer():
    """The exact case a prior, now-replaced heuristic got wrong: bare
    duplicate occurrence (the requirement ALSO claimed by s5, which has no
    dependency-ordering relationship to s1 at all) is not proof of FUTURE
    ownership - an accidental/lazy Planner assignment onto an unrelated
    sibling must not silently defer a requirement that is, in fact, due
    for the subtask asking."""
    plan = _stage_ownership_plan()
    assert plan.classify_requirement_ownership("s1", ["s1", "s5"]) == RequirementOwnershipRelation.CURRENT


def test_classify_requirement_ownership_accidental_duplicate_on_past_owner_does_not_change_ownership():
    """A duplicate claim shared with an ALREADY-PAST subtask (s1, upstream
    of s4) is the legitimate "validated sequential ownership chain" shape
    classify_file_ownership() already treats as fine for files - must not
    disqualify s4 (the later, still-current claimant) from CURRENT."""
    plan = _stage_ownership_plan()
    assert plan.classify_requirement_ownership("s4", ["s1", "s4"]) == RequirementOwnershipRelation.CURRENT


def test_classify_requirement_ownership_past_ordered_when_only_an_earlier_subtask_claims_it():
    plan = _stage_ownership_plan()
    assert plan.classify_requirement_ownership("s4", ["s1"]) == RequirementOwnershipRelation.PAST_ORDERED


def test_classify_requirement_ownership_unowned_when_nothing_claims_it():
    plan = _stage_ownership_plan()
    assert plan.classify_requirement_ownership("s1", []) == RequirementOwnershipRelation.UNOWNED


def test_classify_requirement_ownership_unrelated_when_current_is_not_a_claimant_either():
    """Symmetric with classify_file_ownership's own UNRELATED case: current
    doesn't claim it at all, and the one subtask that does (s5) has no
    dependency-ordering relationship to current (s1) either way."""
    plan = _stage_ownership_plan()
    assert plan.classify_requirement_ownership("s1", ["s5"]) == RequirementOwnershipRelation.UNRELATED
