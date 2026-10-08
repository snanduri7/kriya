"""JAVA-EXAMPLE-COMPILER-001 (BACKEND-READINESS-004): the goal's Java example lines compiled into a sealed B2-c class.

Forms: ``Type.method(literals) -> literal`` and ``-> raises Exception``; a simple type resolves to exactly one main
compilation unit; everything else is rejected with a reason. The class passes the B2-c parser, carries markers,
binds EXACT behaviour (B2-COV), and runs through the real Maven gate on the B2-c fixture project.
"""
import shutil

import pytest
from _b2c_fixtures import TARGET, base_revision, candidate, maven_workspace

from kriya.config.config import AutonomyConfig
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow import acceptance_jvm as jvm
from kriya.workflow import acceptance_oracle as ao
from kriya.workflow import example_oracle_java as ej
from kriya.workflow.contract_compilation import CLOSER_DERIVED_EXAMPLES, compile_verification_contract
from kriya.workflow.example_oracle import example_authority
from kriya.workflow.requirements import derive_requirements, statement_origins

GOAL = ("Add a static clamp(value, min, max) method to Calc in src/main/java/demo/Calc.java.\n\n"
        "Calc.clamp(0, 1, 5) -> 1\n\nCalc.clamp(9, 1, 5) -> 5\n\n"
        "Calc.clamp(1, 5, 1) -> raises IllegalArgumentException\n")


def _tree(tmp_path, units=("src/main/java/demo/Calc.java",)):
    root = tmp_path / "tree"
    for unit in units:
        (root / unit).parent.mkdir(parents=True, exist_ok=True)
        (root / unit).write_text("package x;\n")
    return root


def test_01_recognizes_the_constrained_forms_and_rejects_everything_else(tmp_path):
    root = _tree(tmp_path, ("src/main/java/demo/Calc.java", "src/main/java/demo/util/Text.java", "src/test/java/demo/CalcTest.java"))
    goal = GOAL + ("\nnew Calc().twice(2) -> 4\n\nCalc.twice(x) -> 4\n\nCalc.list() -> [1, 2]\n\n"
                   "demo.util.Text.trim(\" a \") -> \"a\"\n\nNope.go() -> 1\n\nCalc.fail() -> raises Weird\n\n"
                   "Text.trim(\"\") -> \"\"\n")
    reqs = derive_requirements(goal)
    compilation = ej.recognize_java_examples(goal, reqs, str(root))
    forms = [(e.form, e.qualified_type, e.method, e.arguments, e.expected, e.exception) for e in compilation.examples]
    assert forms == [
        (ej.FORM_JAVA_LITERAL, "demo.Calc", "clamp", "0, 1, 5", "1", None),
        (ej.FORM_JAVA_LITERAL, "demo.Calc", "clamp", "9, 1, 5", "5", None),
        (ej.FORM_JAVA_RAISES, "demo.Calc", "clamp", "1, 5, 1", None, "java.lang.IllegalArgumentException"),
        (ej.FORM_JAVA_LITERAL, "demo.util.Text", "trim", '" a "', '"a"', None),
        (ej.FORM_JAVA_LITERAL, "demo.util.Text", "trim", '""', '""', None),  # a simple name resolving to one unit
    ]
    why = {r["text"]: r["why"] for r in compilation.rejected}
    assert "not a static call" in why["new Calc().twice(2) -> 4"]
    assert "Java literal" in why["Calc.twice(x) -> 4"] and "not a Java literal" in why["Calc.list() -> [1, 2]"]
    assert "no main compilation unit" in why["Nope.go() -> 1"] and "neither qualified" in why["Calc.fail() -> raises Weird"]
    # an ambiguous simple name (two main units) is rejected, a qualified one is taken as written
    both = _tree(tmp_path / "two", ("src/main/java/a/Calc.java", "src/main/java/b/Calc.java"))
    assert ej.resolve_java_type(str(both), "Calc")[0] is None and "ambiguous" in ej.resolve_java_type(str(both), "Calc")[1]
    assert ej.resolve_java_type(str(both), "b.Calc") == ("b.Calc", "")
    assert ej.resolve_java_type(str(both), "c.Calc")[0] is None
    # the exception named qualified elsewhere in the goal
    stated = "Calc.clamp(1, 5, 1) -> raises BadRange\n\nIt throws demo.err.BadRange today.\n"
    assert ej.recognize_java_examples(stated, derive_requirements(stated), str(root)).examples[0].exception == "demo.err.BadRange"


def test_02_the_compiled_class_is_a_valid_b2c_artifact_bound_as_exact_behaviour_authority(tmp_path):
    root = _tree(tmp_path)
    reqs = derive_requirements(GOAL)
    artifact, report = ej.derive_java_example_artifact(GOAL, reqs, state_root=str(tmp_path / "state"), candidate_root=str(root))
    assert artifact is not None and artifact.language == "java" and report["refusal"] is None
    assert artifact.java["injection_path"] == "src/test/java/kriya/examples/KriyaGoalExamplesTest.java"
    source = open(artifact.stored_path, encoding="utf-8").read()
    assert source.startswith("package kriya.examples;") and "import demo.Calc;" in source
    assert "assertEquals(1, Calc.clamp(0, 1, 5));" in source and "assertThrows(java.lang.IllegalArgumentException.class" in source
    assert "// kriya_requirement: REQ-2" in source and "@Disabled" not in source
    cases, java = jvm.parse_java_acceptance(source.encode())  # the B2-c parser accepts it, markers and all
    assert java["class_name"] == "KriyaGoalExamplesTest" and {c.identity for c in cases} == {
        "kriya.examples.KriyaGoalExamplesTest.example1", "kriya.examples.KriyaGoalExamplesTest.example2",
        "kriya.examples.KriyaGoalExamplesTest.example3"}
    authority = example_authority(artifact, report)
    assert authority.kind == "goal_examples" and set(authority.coverage) == {"REQ-2", "REQ-3", "REQ-4"}
    contract = compile_verification_contract(reqs, origins=statement_origins(GOAL), test_files=[], external_authorities=[authority],
                                             project_language="java")
    assert contract.entry("REQ-2").closers == [CLOSER_DERIVED_EXAMPLES] and contract.entry("REQ-2").status == "CLOSABLE"
    assert contract.entry("REQ-1").status == "AUTHORITY_REQUIRED"  # the prose request itself still needs authority
    # no examples: nothing bound, said so
    plain = "Make Calc faster.\n"
    assert ej.derive_java_example_artifact(plain, derive_requirements(plain), state_root=str(tmp_path / "s2"), candidate_root=str(root))[0] is None


@pytest.mark.skipif(shutil.which("mvn") is None, reason="real Maven is not installed")
@pytest.mark.parametrize("variant, outcome", [("correct", ao.ACCEPTANCE_PASSED), ("wrong", ao.ACCEPTANCE_VIOLATED)])
def test_03_the_derived_class_compiles_and_judges_a_real_maven_candidate(tmp_path, monkeypatch, variant, outcome):
    monkeypatch.setenv("KRIYA_STATE_DIR", str(tmp_path / "state"))
    ws = maven_workspace(tmp_path / "ws")
    cand = candidate(ws, tmp_path / "c", variant)
    goal = "Add a static clamp(value, min, max) method to Calc in src/main/java/demo/Calc.java.\n\nCalc.clamp(0, 1, 5) -> 1\n"
    reqs = derive_requirements(goal)
    artifact, report = ej.derive_java_example_artifact(goal, reqs, state_root=str(tmp_path / "state"), candidate_root=str(cand))
    assert artifact is not None, report
    run = jvm.run_java_acceptance(artifact, str(cand), candidate_paths=[TARGET], base_revision=base_revision(ws),
                                  validator_factory=lambda root: PolymorphicValidator(root, original_workspace_path=str(ws), autonomy_cfg=AutonomyConfig()))
    assert run.refusal is None and run.runner == "maven", run.refusal
    judgment = ao.judge_acceptance(artifact, run)["REQ-2"]
    assert judgment.code == outcome, judgment.reason
    assert not (cand / artifact.java["injection_path"]).exists()
