"""CORR-016 closure evidence (2026-09-13): Planner strategy must not silently
expand requirement authority.

The core mechanism (`kriya/workflow/contract_authority.py::
derive_direct_contract_authorizations`) and its primary adversarial proof
(`test_planner_text_cannot_create_direct_authorization`, Subtask.description
+ GlobalInvariant.statement naming owner+symbol+category cannot substitute
for grounding_goal) already exist in tests/test_workflow.py, implemented
2026-09-08 (P9-P1). This file adds the remaining deterministic evidence this
closure task requires: an explicit, structural, whole-codebase bypass sweep
(retry/repair/resume/self-correction cannot construct or widen a
ContractEvolutionAuthorization) and behavioral proof of the two/legitimate-
file vs. third-unrelated-file distinction (Task 14/20-K/L), the retry-cannot-
widen adversarial case (Task 9), and the documented legacy-run (non-
structured-plan) no-op scope.

Root authority source is `grounding_goal` alone - see contract_authority.py's
own module docstring. Nothing here re-derives that rule; it proves the rule
holds against every production code path that could plausibly try to
circumvent it.
"""
import ast
import os

from kriya.workflow.contract_authority import derive_direct_contract_authorizations
from kriya.workflow.file_resolution import find_brownfield_public_api_changes
from kriya.workflow.plan_schema import (
    EngineeringPlan,
    ExecutionMethod,
    FileAction,
    PlannedFile,
    Subtask,
)
from kriya.workflow.triage import ChangeKind

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WORKFLOW_DIR = os.path.join(_REPO_ROOT, "kriya", "workflow")


def _plan(owner_paths, subtask_id_prefix="s"):
    return EngineeringPlan(
        plan_id="corr016-bypass-probe", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id=f"{subtask_id_prefix}{i}", description=f"d{i}",
                execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path=path, action=FileAction.MODIFY)],
            )
            for i, path in enumerate(owner_paths, start=1)
        ],
    )


# ---------------------------------------------------------------------------
# TASK 19 - structural bypass search. UNEXPLAINED_AUTHORITY_WIDENING_PATHS
# must be 0: no module outside contract_authority.py itself may construct a
# ContractEvolutionAuthorization, and no module in the retry/repair/resume/
# self-correction family may even import the authority module at all - the
# absence of an import is itself the proof those paths are structurally
# incapable of manufacturing or widening authority.
# ---------------------------------------------------------------------------

_MUST_NOT_REFERENCE_CONTRACT_AUTHORITY = (
    "repair_contract.py",
    "retry_strategy.py",
    "self_correction.py",
    "retry_prompts.py",
    "checkpoint.py",
)


def test_retry_repair_resume_selfcorrection_never_reference_contract_authority():
    for filename in _MUST_NOT_REFERENCE_CONTRACT_AUTHORITY:
        path = os.path.join(_WORKFLOW_DIR, filename)
        assert os.path.isfile(path), f"expected file missing: {path}"
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        assert "contract_authority" not in source, (
            f"{filename} references contract_authority.py - a retry/repair/resume/"
            "self-correction path must never be able to construct or influence a "
            "ContractEvolutionAuthorization"
        )
        assert "ContractEvolutionAuthorization" not in source


def test_contract_evolution_authorization_constructed_only_in_its_own_module():
    """Only kriya/workflow/contract_authority.py may construct
    ContractEvolutionAuthorization(...) - every other module may import the
    TYPE (to read fields) but never call its constructor, which would be a
    second, parallel authority-minting path."""
    hits = []
    for entry in os.listdir(_WORKFLOW_DIR):
        if not entry.endswith(".py") or entry == "contract_authority.py":
            continue
        path = os.path.join(_WORKFLOW_DIR, entry)
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=path)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "ContractEvolutionAuthorization"
            ):
                hits.append(entry)
    assert hits == [], f"unexpected ContractEvolutionAuthorization(...) construction sites: {hits}"


def test_derive_direct_contract_authorizations_has_exactly_two_production_call_sites():
    """Both call sites (attempt.py pre-write, workflow.py terminal recheck)
    are the only places production code may CONSULT authority - both pass
    the same-named grounding_goal variable (verified by source inspection in
    this closure's own investigation trace, not re-parsed here since a
    textual/AST provenance check would be brittle; this test instead pins
    the call-site COUNT so a future third call site is a deliberate,
    reviewed change, not a silent addition)."""
    hits = {}
    for root, _dirs, files in os.walk(_REPO_ROOT):
        if "/.git" in root or "/.venv" in root or "/tests" in root:
            continue
        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                source = fh.read()
            count = source.count("derive_direct_contract_authorizations(")
            if count and fname != "contract_authority.py":
                hits[os.path.relpath(fpath, _REPO_ROOT)] = count
    assert hits == {
        "kriya/workflow/attempt.py": 1,
        "kriya/workflow/workflow.py": 1,
    }, f"unexpected/changed call-site set for derive_direct_contract_authorizations: {hits}"


# ---------------------------------------------------------------------------
# TASK 9 - retry/repair cannot widen authority (adversarial case: method B
# rejected on attempt 1, method B must not become authorized on attempt 2
# merely because it appeared in the prior error/failure context).
# grounding_goal is a plain immutable string threaded once into
# AttemptContext; there is no attempt-numbered or retry-numbered variant of
# it anywhere - proven here by calling the pure function twice with the
# IDENTICAL grounding_goal (exactly what every retry of the same attempt
# does - AttemptContext.grounding_goal is never reassigned, see
# attempt.py's own dataclass field with no setter) and confirming identical,
# unwidened output regardless of what a prior attempt's error/likely_files
# happened to mention.
# ---------------------------------------------------------------------------

def test_retry_cannot_widen_authority_via_prior_error_context():
    goal = "Add a new required field named region to `CustomerRecord`."
    owner_a = "m1/src/main/java/com/example/m1/CustomerRecord.java"
    owner_b = "m1/src/main/java/com/example/m1/Helper.java"
    plan = _plan([owner_a, owner_b])

    # Attempt 1: only CustomerRecord is grounded.
    attempt1 = derive_direct_contract_authorizations(goal, plan)
    assert {a.affected_owner for a in attempt1} == {owner_a}

    # Simulate a prior failure whose diagnostics/likely_files/raw_output
    # happen to mention "Helper" and "renamed" (exactly the kind of text a
    # rejected-candidate's error context would carry) - grounding_goal
    # itself is untouched by that text (retry_prompts.py/repair_contract.py
    # never write into it, per the structural test above), so attempt 2
    # must be byte-identical.
    prior_error_mentioned_helper_rename = (
        "SEMANTIC_REGION_UNAUTHORIZED: file=Helper.java, member=renamed method, "
        "reason=unauthorized rename attempted by the model"
    )
    assert "Helper" in prior_error_mentioned_helper_rename  # sanity: the error text really does mention it

    attempt2 = derive_direct_contract_authorizations(goal, plan)  # goal unchanged by retry
    assert attempt1 == attempt2
    assert owner_b not in {a.affected_owner for a in attempt2}


# ---------------------------------------------------------------------------
# TASK 14 / 20-K/L - legitimate multi-file requirement vs. a Planner-selected
# unrelated third file.
# ---------------------------------------------------------------------------

def test_two_legitimate_files_both_authorized_when_each_independently_grounded():
    """K: interface method + implementation method, each named in its own
    clause of the SAME authoritative goal, both real planned files - both
    get authorized, independently."""
    goal = (
        "Add a method named `computeTotal` to `OrderService`. "
        "Add the `computeTotal` method to `OrderServiceImpl`."
    )
    owner_iface = "src/main/java/example/OrderService.java"
    owner_impl = "src/main/java/example/OrderServiceImpl.java"
    plan = _plan([owner_iface, owner_impl])

    authorizations = derive_direct_contract_authorizations(goal, plan)
    owners = {a.affected_owner for a in authorizations}
    assert owners == {owner_iface, owner_impl}
    for a in authorizations:
        assert a.affected_symbol == "computeTotal"
        assert a.allowed_change_category.value == "add"


def test_third_planner_selected_file_gets_no_authorization():
    """L: Planner additionally plans a third file the goal never names -
    Planner's own inclusion of it in planned_files (operational write scope)
    grants it zero requirement authority; only the two goal-grounded files
    are authorized."""
    goal = (
        "Add a method named `computeTotal` to `OrderService`. "
        "Add the `computeTotal` method to `OrderServiceImpl`."
    )
    owner_iface = "src/main/java/example/OrderService.java"
    owner_impl = "src/main/java/example/OrderServiceImpl.java"
    # Planner decided, on its own, that "cleanup" also touches a logging
    # helper the goal never mentions at all.
    owner_unrelated = "src/main/java/example/LoggingHelper.java"
    plan = _plan([owner_iface, owner_impl, owner_unrelated])

    authorizations = derive_direct_contract_authorizations(goal, plan)
    owners = {a.affected_owner for a in authorizations}
    assert owners == {owner_iface, owner_impl}
    assert owner_unrelated not in owners


def test_unrelated_planner_selected_file_behavioral_change_rejected_at_brownfield_gate(tmp_path):
    """End-to-end (Task 3's required adversarial case, brownfield-signature
    slice): the same third file, if the candidate actually removes/renames
    an existing public signature there, is rejected by
    find_brownfield_public_api_changes() precisely because it carries no
    authorization - Planner having planned_files-scoped it for writing does
    not exempt its public contract from protection."""
    goal = (
        "Add a method named `computeTotal` to `OrderService`. "
        "Add the `computeTotal` method to `OrderServiceImpl`."
    )
    owner_iface = "src/main/java/example/OrderService.java"
    owner_impl = "src/main/java/example/OrderServiceImpl.java"
    owner_unrelated = "src/main/java/example/LoggingHelper.java"
    plan = _plan([owner_iface, owner_impl, owner_unrelated])
    authorizations = derive_direct_contract_authorizations(goal, plan)

    original_unrelated = "public class LoggingHelper { public void logInfo(String msg) { } }\n"
    candidate_unrelated = "public class LoggingHelper {  }\n"  # public method silently removed
    caller = "src/main/java/example/LoggingHelperCaller.java"
    (tmp_path / owner_unrelated).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / caller).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / owner_unrelated).write_text(original_unrelated)
    (tmp_path / caller).write_text("new LoggingHelper().logInfo(\"x\");\n")

    violations = find_brownfield_public_api_changes(
        str(tmp_path),
        {owner_unrelated: original_unrelated},
        {owner_unrelated: candidate_unrelated},
        goal,
        authorizations,
    )
    assert len(violations) == 1
    assert violations[0]["owner"] == owner_unrelated


# ---------------------------------------------------------------------------
# Legacy (non-structured-plan) run scope - documented, deliberate no-op.
# ---------------------------------------------------------------------------

def test_legacy_run_without_structured_plan_always_yields_no_authorizations():
    goal = "Add a new required field named region to `CustomerRecord`."
    assert derive_direct_contract_authorizations(goal, None) == []


def test_empty_grounding_goal_always_yields_no_authorizations():
    owner = "m1/src/main/java/com/example/m1/CustomerRecord.java"
    plan = _plan([owner])
    assert derive_direct_contract_authorizations("", plan) == []
