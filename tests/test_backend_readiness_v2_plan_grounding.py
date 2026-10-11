"""BACKEND-READINESS-V2: plan-time grounding reproducers (handover/BACKEND-READINESS-V2/01 and 02).

Every test here fails on the c98b18d baseline and passes only with the general fix it names. Fixtures are small
synthetic repositories that reproduce the *mechanism* measured in the frozen BACKEND-FINAL-CLOSURE-005 runs; no
production code is keyed to a task, repository or file name.
"""
import asyncio
import subprocess

from kriya.workflow.contract_compilation import compile_verification_contract
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, FileAction, PlannedFile, Subtask
from kriya.workflow.plan_validation import validate_plan
from kriya.workflow.requirements import derive_requirements, statement_origins
from kriya.workflow.triage import ChangeKind
from kriya.workflow.workflow import immutable_test_files


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _repo(root, files):
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@e")
    _git(root, "config", "user.name", "T")
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "seed")
    return root


def _plan(*units):
    """units: (subtask id, [(path, FileAction), ...])"""
    return EngineeringPlan(plan_id="p1", kind=ChangeKind.TASK, subtasks=[
        Subtask(id=sid, description=f"unit {sid}", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path=path, action=action) for path, action in files],
                depends_on=[units[i - 1][0]] if i else [])
        for i, (sid, files) in enumerate(units)])


def _contract(goal, test_files):
    return compile_verification_contract(derive_requirements(goal), origins=statement_origins(goal),
                                         test_files=test_files, project_language="python")


TESTS = ["tests/test_xmltodict.py", "tests/test_dicttoxml.py", "tests/test_other.py"]
REPO = {"xmltodict.py": "def parse():\n    return 1\n", **{path: "def test_a():\n    assert True\n" for path in TESTS}}
NAMED_GOAL = ("Streaming parse drops element text.\n\n"
              "Expected: a streamed item must be exactly what parse() would put at the same place.\n\n"
              "Every existing test in tests/test_xmltodict.py and tests/test_dicttoxml.py must keep passing unchanged.\n")


def _validate(plan, ws, immutable):
    return asyncio.run(validate_plan(plan, workspace_path=str(ws), immutable_test_files=immutable))


def test_s1a_a_named_pure_preservation_statement_freezes_exactly_the_named_test_files_for_planning(tmp_path):
    """BACKEND-FINAL-CLOSURE-005 S1_A (c3): the correct product fix was refused at the terminal gate because the plan's
    second unit edited tests/test_xmltodict.py, a file the goal names as "must keep passing unchanged". The plan-time
    rule fired for a goal saying "every existing test" (S4_A) but not for a goal naming the files, so the mistake
    surfaced only after both units ran. The planning constraint now mirrors the terminal named-test closure."""
    ws = _repo(tmp_path / "ws", REPO)
    contract = _contract(NAMED_GOAL, TESTS)
    immutable = immutable_test_files(contract, str(ws))
    assert immutable == ["tests/test_dicttoxml.py", "tests/test_xmltodict.py"]  # exactly the named files, not tests/test_other.py

    edited = _plan(("s1", [("xmltodict.py", FileAction.MODIFY)]), ("s2", [("tests/test_xmltodict.py", FileAction.MODIFY)]))
    result = _validate(edited, ws, immutable)
    assert result.valid is False and "PLAN_EDITS_IMMUTABLE_TEST" in result.reason_codes
    assert "s2:tests/test_xmltodict.py" in "".join(result.errors) and "new test file" in "".join(result.errors)

    deleted = _plan(("s1", [("tests/test_dicttoxml.py", FileAction.DELETE)]))
    assert _validate(deleted, ws, immutable).valid is False

    # the allowed shapes: a NEW test file; an unnamed existing test file; no named-test goal -> no constraint
    created = _plan(("s1", [("xmltodict.py", FileAction.MODIFY)]), ("s2", [("tests/test_streaming.py", FileAction.CREATE)]))
    assert _validate(created, ws, immutable).valid is True
    unnamed = _plan(("s1", [("tests/test_other.py", FileAction.MODIFY)]))
    assert _validate(unnamed, ws, immutable).valid is True
    assert _validate(edited, ws, None).valid is True  # the pre-existing contract with no constraint is unchanged


def test_s1a_statements_that_ask_to_change_the_named_test_are_never_frozen(tmp_path):
    """Negative controls (the rule must not over-refuse): a goal that asks for a test to be added to a named file carries a
    TEST_ADDITION claim, and a compound statement carries BEHAVIOR beside the preservation claim - neither is a pure
    preservation statement, so the terminal gate's behaviour for them is left to the terminal gate."""
    ws = _repo(tmp_path / "ws", REPO)
    add_goal = "Add a regression test to tests/test_xmltodict.py for the streaming bug.\n"
    compound_goal = "Fix the streaming parse so a streamed item equals parse() output, and tests/test_xmltodict.py must still pass.\n"
    for goal in (add_goal, compound_goal):
        assert immutable_test_files(_contract(goal, TESTS), str(ws)) is None, goal
    free = "Fix the streaming parse so a streamed item equals parse() output.\n"
    assert immutable_test_files(_contract(free, TESTS), str(ws)) is None
    edit_named = _plan(("s1", [("tests/test_xmltodict.py", FileAction.MODIFY)]))
    assert _validate(edit_named, ws, immutable_test_files(_contract(add_goal, TESTS), str(ws))).valid is True


def test_s1a_a_blanket_test_immutability_claim_and_a_named_statement_union(tmp_path):
    """Both claims in one goal: every tracked runnable test is frozen (the pre-existing behaviour), not only the named ones."""
    ws = _repo(tmp_path / "ws", REPO)
    goal = NAMED_GOAL + "Every existing test must keep passing unchanged.\n"
    immutable = immutable_test_files(_contract(goal, TESTS), str(ws))
    assert immutable == sorted(TESTS)
