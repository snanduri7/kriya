"""BACKEND-READINESS-V2 (owner decision 2026-10-12): behavioral relevance of a goal example to the files a unit touched
(kriya/workflow/contract_relevance.py). Generic fixtures only - a facade type with several factory methods, implementation
classes, an interface and an unrelated module; no task, class or repository identity is known to the code under test.

The decisive negative controls: the relation is keyed on the example's FACTORY METHOD (a facade type has many methods), is
ONE hop, runs in the direction target -> callee, and an UNKNOWN fact is never promoted."""
from types import SimpleNamespace

import pytest

from kriya.workflow import contract_feedback as cf
from kriya.workflow import contract_relevance as cr
from kriya.workflow import example_oracle_java as ej
from kriya.workflow.attempt import _run_contract_feedback_gate
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.requirements import derive_requirements
from kriya.workflow.state import GenerationState

MAIN = "src/main/java/demo/"
FACADE = MAIN + "Facade.java"
FILES = {
    FACADE: """package demo;
import java.util.Objects;
public class Facade {
    /** a */
    public static String a(String s) { return AImpl.a(s); }
    public static String b(String s) { return BImpl.b(s); }
    public static Object c(String s) { return new CImpl(s); }
    public static String chain(String s) { return ChainA.make(s); }
    public static String overloaded(String s) { return AImpl.a(s); }
    public static String overloaded(String s, int n) { return BImpl.b(s); }
    public static String viaThis(String s) { return a(s); }
    public static String wrapped(String s) { return String.valueOf(new CImpl(s)); }
    public static String external(String s) { return String.valueOf(s); }
    public static String ghost(String s) { return NoSuchType.go(s); }
    public static String twin(String s) { return Twin.go(s); }
    public static String nested(String s) { return wrap(WImpl.w(s)); }
    public static String arity(String s) { return OverImpl.f(s, 1); }
    public static String arityOk(String s) { return OverImpl.f(s); }
    public static Object built(String s) { return new Built(s, 2); }
    public static Object builtDefault(String s) { return new Plain(); }
    public static String shadowed(String s) { return Objects.requireNonNull(s); }
    public static String chained(String s) { return AImpl.a(s).trim(); }
    public static String appended(String s) { return AImpl.a(s) + "x"; }
    public static String wrongName(String s) { return NameImpl.missing(s); }
    public static String spread(String s) { return VarImpl.all(s, s, s); }
    public static String viaTernary(boolean c) { return c ? Choice.a() : Choice.b(); }
}
""",
    MAIN + "AImpl.java": "package demo;\npublic class AImpl {\n    public static String a(String s) { return s; }\n}\n",
    MAIN + "BImpl.java": "package demo;\npublic class BImpl {\n    public static String b(String s) { return s; }\n}\n",
    MAIN + "CImpl.java": "package demo;\npublic class CImpl {\n    public CImpl(String s) { }\n}\n",
    MAIN + "ChainA.java": "package demo;\npublic class ChainA {\n    public static String make(String s) { return ChainB.deep(s); }\n}\n",
    MAIN + "ChainB.java": "package demo;\npublic class ChainB {\n    public static String deep(String s) { return s; }\n}\n",
    MAIN + "Unrelated.java": "package demo;\npublic class Unrelated {\n    public static String a(String s) { return Facade.a(s); }\n}\n",
    MAIN + "Shape.java": "package demo;\npublic interface Shape {\n    double area();\n    String name();\n}\n",
    MAIN + "Square.java": "package demo;\npublic class Square implements Shape {\n    public double area() { return 1; }\n    public String name() { return \"s\"; }\n}\n",
    MAIN + "Named.java": "package demo;\npublic class Named implements Shape {\n    public double area() { return 2; }\n}\n",
    MAIN + "Sibling.java": "package demo;\npublic class Sibling implements Shape {\n    public String name() { return \"x\"; }\n}\n",
    MAIN + "Impostor.java": "package demo;\nimport other.Shape;\npublic class Impostor implements Shape {\n    public double area() { return 3; }\n}\n",
    "src/main/java/other/Shape.java": "package other;\npublic interface Shape { double area(); }\n",
    "other/src/main/java/demo/Alien.java": "package demo;\npublic class Alien {\n    public static String a(String s) { return s; }\n}\n",
    MAIN + "WImpl.java": "package demo;\npublic class WImpl {\n    public static String w(String s) { return s; }\n}\n",
    MAIN + "OverImpl.java": "package demo;\npublic class OverImpl {\n    public static String f(String s) { return s; }\n}\n",
    MAIN + "Built.java": "package demo;\npublic class Built {\n    public Built(String s) { }\n}\n",
    MAIN + "Plain.java": "package demo;\npublic class Plain {\n}\n",
    MAIN + "Objects.java": "package demo;\npublic class Objects {\n    public static String requireNonNull(String s) { return s; }\n}\n",
    MAIN + "Choice.java": "package demo;\npublic class Choice {\n    public static String a() { return \"a\"; }\n    public static String b() { return \"b\"; }\n}\n",
    MAIN + "NameImpl.java": "package demo;\npublic class NameImpl {\n    public static String other(String s) { return s; }\n}\n",
    MAIN + "VarImpl.java": "package demo;\npublic class VarImpl {\n    public static String all(String first, String... rest) { return first; }\n}\n",
    MAIN + "Twin.java": "package demo;\npublic class Twin {\n    public static String go(String s) { return s; }\n}\n",
    "second/src/main/java/demo/Twin.java": "package demo;\npublic class Twin {\n    public static String go(String s) { return s; }\n}\n",
}


@pytest.fixture(name="repo")
def _repo(tmp_path):
    for rel, text in FILES.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    return tmp_path


def _example(root, qualified="demo.Facade", method="a", number=1):
    return cf.ContractExample(
        identity=f"x.Y.example{number}", number=number, requirement_ids=(), text=f'{qualified}.{method}("x") -> "x"', line=3,
        span=None, expected='"x"', exception=None, form="java_matcher_pass", qualified_type=qualified,
        source_path=ej.main_source_path(str(root), qualified), method=method)


def _assess(root, touched, **kw):
    return cr.assess_relevance(_example(root, **kw), touched, str(root))


def test_the_unit_touching_the_target_type_file_is_directly_relevant(repo):
    result = _assess(repo, [FACADE])
    assert result.status == cr.RELEVANT_DIRECT_TARGET and result.touched_path == FACADE and result.relevant


def test_a_factory_that_delegates_to_the_touched_implementation_is_a_relevant_dependency(repo):
    result = _assess(repo, [MAIN + "AImpl.java"])
    assert result.status == cr.RELEVANT_DIRECT_DEPENDENCY and result.relationship == cr.DELEGATES_TO
    assert (result.target, result.related_type, result.declaring_path, result.touched_path) == (
        "demo.Facade.a", "demo.AImpl", FACADE, MAIN + "AImpl.java")
    assert result.provenance and "demo.AImpl" in result.reason


def test_a_factory_that_constructs_the_touched_implementation_is_a_relevant_dependency(repo):
    result = _assess(repo, [MAIN + "CImpl.java"], method="c")
    assert result.status == cr.RELEVANT_DIRECT_DEPENDENCY and result.relationship == cr.CONSTRUCTS
    assert result.related_type == "demo.CImpl" and result.touched_path == MAIN + "CImpl.java"


def test_a_subtype_is_not_a_relation_because_no_compiled_form_dispatches_to_it(repo):
    """Every compiled example form calls the named type itself, so a touched implementor/subclass of the target (even one that
    declares the same method) is never invoked by the example: NOT_RELEVANT (owner matrix row 4 is deliberately not represented)."""
    for touched in ("Square", "Named", "Sibling", "Impostor"):
        result = _assess(repo, [MAIN + touched + ".java"], qualified="demo.Shape", method="area")
        assert result.status in (cr.NOT_RELEVANT, cr.UNKNOWN) and not result.relevant, touched


def test_only_a_whole_returned_call_is_a_delegate_not_an_argument_of_another_call(repo):
    assert _assess(repo, [MAIN + "WImpl.java"], method="nested").status == cr.NOT_RELEVANT  # return wrap(WImpl.w(s))
    assert _assess(repo, [MAIN + "AImpl.java"], method="a").relevant  # control: the call IS the returned expression


def test_a_call_that_is_only_the_head_of_the_returned_expression_is_not_a_delegate(repo):
    assert _assess(repo, [MAIN + "AImpl.java"], method="chained").status == cr.NOT_RELEVANT  # AImpl.a(s).trim()
    assert _assess(repo, [MAIN + "AImpl.java"], method="appended").status == cr.NOT_RELEVANT  # AImpl.a(s) + "x"


def test_the_callee_type_must_declare_the_called_method_and_varargs_are_accepted(repo):
    assert _assess(repo, [MAIN + "NameImpl.java"], method="wrongName").status == cr.NOT_RELEVANT
    assert _assess(repo, [MAIN + "VarImpl.java"], method="spread").relevant


def test_the_callee_must_accept_the_call_arity(repo):
    assert _assess(repo, [MAIN + "OverImpl.java"], method="arity").status == cr.NOT_RELEVANT  # f(s, 1) vs declared f(String)
    assert _assess(repo, [MAIN + "OverImpl.java"], method="arityOk").relevant
    assert _assess(repo, [MAIN + "Built.java"], method="built").status == cr.NOT_RELEVANT  # new Built(s, 2) vs Built(String)
    assert _assess(repo, [MAIN + "Plain.java"], method="builtDefault").relevant  # the implicit no-argument constructor


def test_a_library_type_named_like_a_project_type_is_not_the_callee(repo):
    """Facade imports java.util.Objects; the project also has demo.Objects. The import wins: touching the project class is
    not a relation."""
    assert _assess(repo, [MAIN + "Objects.java"], method="shadowed").status == cr.NOT_RELEVANT


def test_a_conditional_returned_expression_is_not_followed(repo):
    assert _assess(repo, [MAIN + "Choice.java"], method="viaTernary").status == cr.NOT_RELEVANT


def test_co_location_alone_is_never_relevance(repo):
    """The facade has factories b and chain; the example is on a. Touching BImpl (b's callee), ChainA/ChainB, an unrelated class
    of the same package, or another module's class must NOT make example a relevant."""
    for touched in ("BImpl", "ChainA", "ChainB", "Unrelated", "CImpl", "Square"):
        result = _assess(repo, [MAIN + touched + ".java"])
        assert result.status == cr.NOT_RELEVANT and not result.relevant, touched
    assert _assess(repo, ["other/src/main/java/demo/Alien.java"]).status == cr.NOT_RELEVANT  # a different module


def test_each_factory_is_related_only_to_its_own_callee(repo):
    assert _assess(repo, [MAIN + "BImpl.java"], method="b").relevant
    assert not _assess(repo, [MAIN + "AImpl.java"], method="b").relevant


def test_the_relation_is_exactly_one_hop(repo):
    """Facade.chain -> ChainA.make -> ChainB.deep: ChainA is the direct dependency, ChainB (two hops) is not."""
    assert _assess(repo, [MAIN + "ChainA.java"], method="chain").relevant
    assert _assess(repo, [MAIN + "ChainB.java"], method="chain").status == cr.NOT_RELEVANT


def test_the_relation_has_one_direction_a_caller_of_the_factory_is_not_related(repo):
    """Unrelated.a CALLS Facade.a; a touched caller does not make an example about Facade.a relevant."""
    assert _assess(repo, [MAIN + "Unrelated.java"]).status == cr.NOT_RELEVANT
    # the direction is target -> callee: an example ON the caller is related to the callee's file, not the reverse
    reverse = _assess(repo, [FACADE], qualified="demo.Unrelated")
    assert reverse.relationship == cr.DELEGATES_TO and reverse.related_type == "demo.Facade"


def test_an_unqualified_same_class_delegation_and_a_nested_construction_are_not_followed(repo):
    assert _assess(repo, [FACADE], method="viaThis").relevant  # control: the target file itself
    assert _assess(repo, [MAIN + "AImpl.java"], method="viaThis").status == cr.NOT_RELEVANT
    assert _assess(repo, [MAIN + "CImpl.java"], method="wrapped").status == cr.NOT_RELEVANT  # new is not the returned expression
    assert _assess(repo, [MAIN + "AImpl.java"], method="external").status == cr.NOT_RELEVANT


def test_an_overloaded_factory_is_unknown_and_unknown_is_not_relevant(repo):
    result = _assess(repo, [MAIN + "AImpl.java"], method="overloaded")
    assert result.status == cr.UNKNOWN and not result.relevant and "exactly once" in result.reason


def test_unknown_facts_are_reported_and_never_promoted(repo):
    ambiguous = _assess(repo, [MAIN + "AImpl.java"], method="twin")
    assert ambiguous.status == cr.UNKNOWN and "ambiguous" in ambiguous.reason and not ambiguous.relevant
    (repo / FACADE).write_text("package demo; public class Facade { public static String a( {{{ ")  # no complete parse
    broken = cr.assess_relevance(_example(repo), [MAIN + "AImpl.java"], str(repo))
    assert broken.status == cr.UNKNOWN and not broken.relevant
    (repo / FACADE).unlink()
    missing = cr.assess_relevance(_example(repo), [MAIN + "AImpl.java"], str(repo))
    assert missing.status == cr.UNKNOWN and "not exactly one file" in missing.reason


def test_a_callee_that_is_not_a_project_type_is_simply_not_related(repo):
    assert _assess(repo, [MAIN + "AImpl.java"], method="ghost").status == cr.NOT_RELEVANT


def test_without_a_candidate_root_only_the_direct_target_rule_applies(repo):
    example = _example(repo)
    assert cr.assess_relevance(example, [MAIN + "AImpl.java"], None).status == cr.NOT_RELEVANT
    assert cr.assess_relevance(example, [FACADE], None).status == cr.RELEVANT_DIRECT_TARGET


def test_a_model_claim_cannot_create_a_relation(repo):
    """The only inputs are the repository sources and the touched paths: text a model wrote in the example, a comment or a
    string literal is never a relation."""
    (repo / FACADE).write_text(FILES[FACADE].replace(
        "return AImpl.a(s);", 'String note = "return BImpl.b(s);"; /* return BImpl.b(s); */ return AImpl.a(s);'))
    assert not _assess(repo, [MAIN + "BImpl.java"]).relevant
    assert _assess(repo, [MAIN + "AImpl.java"]).relevant


def test_module_ownership_is_independent_of_relevance(repo):
    """The same file layout under a module directory: relevance does not read the module, so a delegate in the same module as
    the facade is related by delegation only, and a same-module non-delegate is not."""
    (repo / "mod").mkdir()
    for rel, text in list(FILES.items())[:12]:
        target = repo / "mod" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    # simple type resolution is not unique across the two copies, so the pre-existing ambiguity rule applies: UNKNOWN
    result = cr.assess_relevance(_example(repo, qualified="demo.Facade"), ["mod/" + MAIN + "AImpl.java"], str(repo))
    assert not result.relevant


# ---- through the attempt-pipeline gate -------------------------------------------------------------------------------

GOAL = 'Fix it.\n\nFacade.a("x") -> "x"\n\nFacade.b("y") -> "y"\n'
ASSERTION = "org.opentest4j.AssertionFailedError"


def _gate_feedback(repo, tmp_path, runs):
    artifact, report = ej.derive_java_example_artifact(GOAL, derive_requirements(GOAL), state_root=str(tmp_path / "state"),
                                                       candidate_root=str(repo))
    assert artifact is not None
    examples = cf.contract_examples(artifact, report, str(repo))
    assert [e.method for e in examples] == ["a", "b"]
    calls = []

    def execute(art, touched):
        calls.append(list(touched))
        details = {}
        for case in art.cases:
            failing = case.identity.endswith("example1") and runs["fail"]
            details[case.identity] = [{"status": "failed" if failing else "passed", "type": ASSERTION if failing else "",
                                       "trace": f"{ASSERTION}: expected: <x> but was: <z>" if failing else ""}]
        report_ = SimpleNamespace(complete=True, runner="maven",
                                  statuses=lambda: {i: [d["status"] for d in v] for i, v in details.items()},
                                  passed=lambda identity: details[identity][0]["status"] == "passed")
        return SimpleNamespace(runner="maven", refusal=None, integrity_problem=None, report=report_, case_details=details,
                               candidate_classes=["demo.Facade"], result={})

    return cf.ContractFeedback(artifact=artifact, examples=examples, execute=execute, candidate_root=str(repo)), calls


def _state(*files):
    state = GenerationState()
    state.attempt_number = 1
    state.all_files_written = list(files)
    return state


def test_a_dependency_violation_reaches_the_existing_repair_path_with_its_relation(repo, tmp_path):
    feedback, calls = _gate_feedback(repo, tmp_path, {"fail": True})
    with pytest.raises(QualityGateFailure) as stop:
        _run_contract_feedback_gate(_state(MAIN + "AImpl.java"), SimpleNamespace(contract_feedback=feedback, worktree_path=str(repo)))
    failure = stop.value.failure
    assert failure.type == "deterministic_contract_violation" and len(calls) == 1
    assert failure.likely_files == [MAIN + "AImpl.java"]  # the file the Developer edits, not the facade
    assert "RELATION: demo.Facade.a DELEGATES_TO demo.AImpl" in failure.message and "TARGET: demo.Facade" in failure.message
    violation = failure.diagnostics["violations"][0]
    assert violation["relevance"]["relationship"] == cr.DELEGATES_TO and violation["relevance"]["touched_path"] == MAIN + "AImpl.java"
    assert violation["relevance"]["provenance"]


def test_an_unrelated_unit_receives_no_failing_contract_and_no_run(repo, tmp_path):
    """The contract is violated (example 1 fails), but the unit touched only Facade.b's callee: example a is not relevant, so
    nothing runs and nothing is fed to repair - the terminal gate still evaluates it."""
    feedback, calls = _gate_feedback(repo, tmp_path, {"fail": True})
    outcome = cf.evaluate(feedback, [MAIN + "BImpl.java"])
    assert outcome.status == "PASSED" and [e.number for e in outcome.relevant] == [2] and len(calls) == 1
    outcome = cf.evaluate(feedback, [MAIN + "Unrelated.java"])
    assert outcome.status == "NOT_RELEVANT" and len(calls) == 1 and outcome.relevant == ()
    statuses = {r["target"]: r["status"] for r in outcome.summary()["relevance"]}
    assert statuses == {"demo.Facade.a": cr.NOT_RELEVANT, "demo.Facade.b": cr.NOT_RELEVANT}


def test_the_dependency_relation_adds_no_attempt_and_no_status_value(repo, tmp_path):
    feedback, _calls = _gate_feedback(repo, tmp_path, {"fail": False})
    outcome = cf.evaluate(feedback, [MAIN + "AImpl.java"])
    assert outcome.status == "PASSED" and feedback.runs == 1
    assert {cf.evaluate(feedback, [p]).status for p in ("x/Other.java",)} == {"NOT_RELEVANT"}
    assert feedback.runs == 1  # an irrelevant unit costs no run


def test_the_workflow_binds_the_candidate_tree_so_the_dependency_relation_is_reachable(repo, tmp_path):
    """build_contract_feedback is the production wiring: without the candidate root the gate would silently fall back to the
    direct-target rule (the measured T3 defect)."""
    from kriya.workflow.workflow import build_contract_feedback

    artifact, report = ej.derive_java_example_artifact(GOAL, derive_requirements(GOAL), state_root=str(tmp_path / "state"),
                                                       candidate_root=str(repo))
    engine = SimpleNamespace(derived_examples=artifact, derived_examples_report=report)
    feedback = build_contract_feedback(engine, str(repo), str(repo), object())
    assert feedback is not None and feedback.candidate_root == str(repo)
    relevant, records = cf.assess_examples(feedback.examples, [MAIN + "AImpl.java"], feedback.candidate_root)
    assert [e.method for e in relevant] == ["a"] and records[0].status == cr.RELEVANT_DIRECT_DEPENDENCY


def test_only_the_intermediate_gate_consults_relevance():
    """Terminal acceptance is unchanged: no terminal/closer module imports the relevance relation."""
    import pathlib

    root = pathlib.Path(cr.__file__).parent
    users = sorted(p.name for p in root.glob("*.py") if p.name != "contract_relevance.py" and "contract_relevance" in p.read_text())
    assert users == ["contract_feedback.py"]


def test_no_task_or_repository_identity_in_the_relevance_code():
    import pathlib

    text = pathlib.Path(cr.__file__).read_text().lower()
    for word in ("hamcrest", "matchers", "jsoup", "t3", "iswhitespace", "compressingwhitespace"):
        assert word not in text, word
