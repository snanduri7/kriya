"""Unit tests for kriya/workflow/repair_contract.py's pure functions
(MA9, PRV-06 Bucket A, 2026-08-29) - deterministic evidence extraction and
the single LOCAL/COORDINATED classification boundary. Integration-level
coverage (the actual _record_process_boundary_obligation/run_attempt wiring)
lives in tests/test_workflow.py alongside the rest of the process-boundary
obligation tests."""
from kriya.workflow.obligations import (
    ObligationAuthority,
    ObligationKind,
    ObligationRecord,
    ObligationStatus,
)
from kriya.workflow.repair_contract import (
    RepairContractStatus,
    RepairKind,
    _build_contract_from_shape,
    _ContractShape,
    build_repair_contract,
    derive_process_boundary_participants,
)

_CRASHED_TESTS_OUTPUT = (
    "[ERROR] The forked VM terminated without properly saying goodbye. VM crash or "
    "System.exit called?\n"
    "[ERROR] Crashed tests:\n"
    "[ERROR] AppTest\n"
)


def _write(tmp_path, relpath, content):
    full = tmp_path / relpath
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    return relpath


def test_derive_process_boundary_participants_no_crashed_tests_line(tmp_path):
    assert derive_process_boundary_participants(
        "ORDINARY ASSERTION FAILURE, nothing about crashed tests", str(tmp_path), [],
    ) is None


def test_derive_process_boundary_participants_unresolvable_consumer(tmp_path):
    """The crashed class name doesn't map to exactly one known file (zero
    matches here) - must not guess."""
    app = _write(tmp_path, "src/main/java/App.java", "class App { void x() { System.exit(1); } }")
    assert derive_process_boundary_participants(
        _CRASHED_TESTS_OUTPUT, str(tmp_path), [app],
    ) is None


def test_derive_process_boundary_participants_ambiguous_consumer_match(tmp_path):
    """Two known files share the basename AppTest.java (e.g. two modules) -
    ambiguous, must not guess which one crashed."""
    app = _write(tmp_path, "src/main/java/App.java", "class App { void x() { System.exit(1); } }")
    test_a = _write(tmp_path, "moduleA/src/test/java/AppTest.java", "class AppTest {}")
    test_b = _write(tmp_path, "moduleB/src/test/java/AppTest.java", "class AppTest {}")
    assert derive_process_boundary_participants(
        _CRASHED_TESTS_OUTPUT, str(tmp_path), [app, test_a, test_b],
    ) is None


def test_derive_process_boundary_participants_unambiguous(tmp_path):
    app = _write(tmp_path, "src/main/java/App.java", "class App { void x() { System.exit(1); } }")
    test = _write(tmp_path, "src/test/java/AppTest.java", "class AppTest {}")
    evidence = derive_process_boundary_participants(_CRASHED_TESTS_OUTPUT, str(tmp_path), [app, test])
    assert evidence == {
        "consumer": test, "producer": app, "termination_surface_candidates": (app,),
    }


def test_derive_process_boundary_participants_zero_termination_candidates(tmp_path):
    """The crashed test resolves fine, but no known file's content actually
    contains a termination call signature - nothing to coordinate against."""
    app = _write(tmp_path, "src/main/java/App.java", "class App { void x() { /* no exit call */ } }")
    test = _write(tmp_path, "src/test/java/AppTest.java", "class AppTest {}")
    assert derive_process_boundary_participants(
        _CRASHED_TESTS_OUTPUT, str(tmp_path), [app, test],
    ) is None


def test_derive_process_boundary_participants_multiple_termination_candidates(tmp_path):
    """2026-08-29 design review's own example: more than one plausible
    termination surface is ambiguous evidence, never 'pick the first.'"""
    app = _write(tmp_path, "src/main/java/App.java", "class App { void x() { System.exit(1); } }")
    hook = _write(
        tmp_path, "src/main/java/ShutdownHook.java",
        "class ShutdownHook { void y() { Runtime.getRuntime().halt(1); } }",
    )
    test = _write(tmp_path, "src/test/java/AppTest.java", "class AppTest {}")
    assert derive_process_boundary_participants(
        _CRASHED_TESTS_OUTPUT, str(tmp_path), [app, hook, test],
    ) is None


def _obligation(kind=ObligationKind.PROCESS_BOUNDARY_COMPATIBILITY, owner_subtask_id="s3"):
    return ObligationRecord(
        id="attempt.subtask.s3.process_boundary_compatibility", kind=kind,
        status=ObligationStatus.VIOLATED, authority=ObligationAuthority.DETERMINISTIC,
        description="d", source="test", owner_subtask_id=owner_subtask_id,
    )


def test_build_repair_contract_wrong_obligation_kind_returns_none():
    obligation = _obligation(kind=ObligationKind.PLAN_STRUCTURAL_VALIDITY)
    evidence = {"producer": "App.java", "consumer": "AppTest.java"}
    assert build_repair_contract(obligation, evidence, created_attempt=1) is None


def test_build_repair_contract_no_evidence_returns_none():
    obligation = _obligation()
    assert build_repair_contract(obligation, None, created_attempt=1) is None
    assert build_repair_contract(obligation, {}, created_attempt=1) is None


def test_build_repair_contract_degenerate_same_file_returns_none():
    obligation = _obligation()
    evidence = {"producer": "App.java", "consumer": "App.java"}
    assert build_repair_contract(obligation, evidence, created_attempt=1) is None


def test_build_repair_contract_happy_path():
    obligation = _obligation()
    evidence = {"producer": "src/main/java/App.java", "consumer": "src/test/java/AppTest.java"}
    contract = build_repair_contract(obligation, evidence, created_attempt=3)

    assert contract is not None
    assert contract.status == RepairContractStatus.ACTIVE
    assert contract.kind == RepairKind.COORDINATED
    assert contract.source_obligation_ids == (obligation.id,)
    assert contract.created_attempt == 3
    assert contract.participating_artifacts == tuple(sorted(
        ["src/main/java/App.java", "src/test/java/AppTest.java"],
    ))
    assert contract.generation_order == ("src/main/java/App.java", "src/test/java/AppTest.java")
    assert contract.participant_roles == {
        "src/main/java/App.java": "termination_surface",
        "src/test/java/AppTest.java": "crashed_consumer",
    }
    assert contract.immediate_correction_targets == contract.participating_artifacts
    assert "src/main/java/App.java" in contract.repair_intent
    assert "src/test/java/AppTest.java" in contract.repair_intent
    # N-artifact grouping (2026-08-29 v2 review): even this 2-participant
    # case is now derived generically via stack-neutral FileRole
    # classification, not a hand-specified 2-slot generation_order.
    # "App.java" -> stem "app" matches generation_manifest.py's own
    # ENTRYPOINT stem set - genuinely correct here too (it's the process
    # entrypoint), not a misclassification this test needs to work around.
    assert [g.id for g in contract.repair_groups] == ["group.entrypoint", "group.test"]
    assert contract.active_group_id == "group.entrypoint"
    assert contract.expected_postconditions


# --- _build_contract_from_shape: the generic constructor is N-artifact, not
# two-artifact - the process-boundary detector above is the only thing
# currently limited to a producer/consumer pair; the constructor itself
# knows nothing about that framing (2026-08-29 design review: "implement an
# N-artifact RepairContract executor, while keeping the initial detector
# intentionally limited to the currently proven process-boundary case"). ---

def test_build_contract_from_shape_supports_more_than_two_participants():
    """A hypothetical future obligation kind's shape function could report
    5 participants with no producer/consumer framing at all - the generic
    constructor must build a correct contract from it with zero changes.
    All 5 share the plain SOURCE role (no test/model/config markers), so
    they land in ONE group, alphabetically ordered - the correct default
    per _derive_repair_groups' own docstring when nothing separates them."""
    obligation = _obligation()
    shape = _ContractShape(
        participating_artifacts=("E.java", "C.java", "A.java", "D.java", "B.java"),
        participant_roles={
            "A.java": "role_a", "B.java": "role_b", "C.java": "role_c",
            "D.java": "role_d", "E.java": "role_e",
        },
        repair_intent="Resolve a hypothetical 5-file coordinated defect.",
        must_fix=("all five files must agree on the shared contract",),
        must_preserve=("existing unrelated behavior",),
    )

    contract = _build_contract_from_shape(obligation, shape, created_attempt=1)

    assert contract is not None
    assert len(contract.participating_artifacts) == 5
    assert contract.generation_order == ("A.java", "B.java", "C.java", "D.java", "E.java")
    assert len(contract.repair_groups) == 1
    assert contract.repair_groups[0].artifacts == contract.generation_order
    assert contract.participant_roles == shape.participant_roles
    assert contract.immediate_correction_targets == contract.participating_artifacts


def test_build_contract_from_shape_rejects_single_participant():
    obligation = _obligation()
    shape = _ContractShape(
        participating_artifacts=("A.java",),
        participant_roles={"A.java": "role_a"}, repair_intent="x",
        must_fix=("x",), must_preserve=("x",),
    )
    assert _build_contract_from_shape(obligation, shape, created_attempt=1) is None


def test_build_contract_from_shape_groups_by_stack_neutral_file_role():
    """Real multi-role grouping, generically derived - a build manifest
    (matching this codebase's own _BUILD_FILENAMES list, not Java-specific)
    alongside model/source/test files groups and orders exactly like
    generation_manifest.py's own build_generation_manifest() would order
    them for INITIAL generation - the same machinery, reused, not a
    second, drifting convention."""
    obligation = _obligation()
    shape = _ContractShape(
        participating_artifacts=(
            "pom.xml", "OrderRequest.java", "OrderService.java", "OrderServiceTest.java",
        ),
        participant_roles={},
        repair_intent="x", must_fix=("x",), must_preserve=("x",),
    )

    contract = _build_contract_from_shape(obligation, shape, created_attempt=1)

    assert contract is not None
    assert [g.id for g in contract.repair_groups] == [
        "group.build", "group.model", "group.source", "group.test",
    ]
    assert contract.repair_groups[0].artifacts == ("pom.xml",)
    assert contract.repair_groups[0].depends_on_group_ids == ()
    assert contract.repair_groups[-1].artifacts == ("OrderServiceTest.java",)
    assert contract.repair_groups[-1].depends_on_group_ids == (
        "group.build", "group.model", "group.source",
    )
    assert contract.generation_order == (
        "pom.xml", "OrderRequest.java", "OrderService.java", "OrderServiceTest.java",
    )
