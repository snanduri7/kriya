"""BACKEND-READINESS-V2 Stage C change 1: the goal-example classifier (COMPILED / AMBIGUOUS / UNSUPPORTED) and its renderer.

Grammar forms and their wording rules come from the frozen goals; every other line is classified, never guessed."""
import pytest

from kriya.workflow import goal_example_forms as gf
from kriya.workflow.goal_example_forms import Expect, Status, classify_goal_examples, render_probe_class

NBSP, EMSP = " ", " "


def _tree(tmp_path, units):
    root = tmp_path / "tree"
    for unit in units:
        (root / unit).parent.mkdir(parents=True, exist_ok=True)
        (root / unit).write_text("package x;\n")
    return str(root)


def _only(goal, root=None):
    found = classify_goal_examples(goal, root).examples
    assert len(found) == 1, found
    return found[0]


STATIC = ("src/main/java/demo/Calc.java",)


def test_expected_return_value_with_provenance(tmp_path):
    goal = "Intro text.\n\n  Calc.clamp(0, 1, 5) -> 1\n"
    example = _only(goal, _tree(tmp_path, STATIC))
    assert (example.status, example.form, example.expect, example.expected) == (Status.COMPILED, gf.FORM_STATIC_VALUE, Expect.VALUE, "1")
    assert example.qualified_type == "demo.Calc" and example.line == 3
    assert goal[example.start:example.end] == example.text == "Calc.clamp(0, 1, 5) -> 1"


def test_expected_exception_and_must_not_throw(tmp_path):
    root = _tree(tmp_path, STATIC)
    raises = _only("Calc.clamp(1, 5, 1) -> raises IllegalArgumentException", root)
    assert (raises.form, raises.expect, raises.exception) == (gf.FORM_STATIC_RAISES, Expect.RAISES, "java.lang.IllegalArgumentException")
    for phrase in ("compiles (no exception)", "does not throw", "no exception"):
        quiet = _only(f'Calc.parse("$.a[?(@.b < 10)]") -> {phrase}', root)
        assert (quiet.status, quiet.form, quiet.expect) == (Status.COMPILED, gf.FORM_STATIC_NO_THROW, Expect.NO_THROW)
    unknown = _only("Calc.clamp(1, 5, 1) -> raises Weird", root)
    assert unknown.status is Status.AMBIGUOUS and "neither qualified" in unknown.reason


def test_instance_form_takes_constructor_and_call_arguments(tmp_path):
    root = _tree(tmp_path, ("src/main/java/org/x/TextStringBuilder.java",))
    example = _only('new TextStringBuilder("abab").lastIndexOf("", 10) -> 4', root)
    assert (example.status, example.form, example.qualified_type) == (Status.COMPILED, gf.FORM_INSTANCE_VALUE, "org.x.TextStringBuilder")
    assert (example.constructor_arguments, example.arguments, example.expected) == ('"abab"', '"", 10', "4")
    assert _only('new TextStringBuilder().isEmpty() -> true', root).constructor_arguments == ""
    chained = _only('new TextStringBuilder("a").append("b").length() -> 2', root)
    assert chained.status is Status.UNSUPPORTED


def test_regression_wording_means_the_repaired_outcome_is_pass(tmp_path):
    goal = ('The matcher is Matchers.equalToCompressingWhiteSpace(String).\n'
            '  assertThat("my foo bar", equalToCompressingWhiteSpace("  my  foo bar "))   -> fails, used to pass\n')
    root = _tree(tmp_path, ("src/main/java/org/hamcrest/Matchers.java", "src/main/java/org/hamcrest/MatcherAssert.java"))
    example = classify_goal_examples(goal, root).examples[0]
    assert (example.status, example.form, example.expect, example.regression_wording) == (Status.COMPILED, gf.FORM_MATCHER_PASS, Expect.PASS, True)
    assert example.factory_type == "org.hamcrest.Matchers" and example.assertion_type == "org.hamcrest.MatcherAssert"
    source = render_probe_class([example], "kriya.examples", "Probe")
    assert "MatcherAssert.assertThat(\"my foo bar\", Matchers.equalToCompressingWhiteSpace(\"  my  foo bar \"));" in source
    assert "fails" not in source.split("class Probe")[1]  # the word 'fails' is never encoded as the desired behaviour


@pytest.mark.parametrize("wording,status", [
    ("passes", Status.COMPILED), ("no longer passes, used to pass", Status.COMPILED), ("fails (it used to pass)", Status.COMPILED),
    ("fails", Status.AMBIGUOUS), ("fails, used to fail", Status.AMBIGUOUS), ("passes, used to fail", Status.AMBIGUOUS),
    ("is great", Status.UNSUPPORTED)])
def test_matcher_outcome_wording(tmp_path, wording, status):
    goal = f'Matchers.same(String) is the factory.\nassertThat("a", same("a")) -> {wording}\n'
    root = _tree(tmp_path, ("src/main/java/org/hamcrest/Matchers.java", "src/main/java/org/hamcrest/MatcherAssert.java"))
    assert classify_goal_examples(goal, root).examples[0].status is status


def test_malformed_syntax_is_unsupported_or_ambiguous_never_compiled(tmp_path):
    root = _tree(tmp_path, STATIC)
    for line in ('Calc.clamp(0, 1 -> 1', 'Calc.clamp("open) -> 1', 'Calc.clamp(x, 1) -> 1', 'Calc.clamp(0)) -> 1', 'new Calc( -> 1',
                 'assertThat("a") -> passes', 'assertThat(x, y("a")) -> passes', 'Calc.clamp(0, 1, 5) -> [1, 2]', 'Calc.clamp(0) ->  '):
        examples = classify_goal_examples(line, root).examples
        assert all(e.status is not Status.COMPILED for e in examples), line


def test_ambiguous_target_is_never_guessed(tmp_path):
    two = _tree(tmp_path / "a", ("src/main/java/a/Calc.java", "src/main/java/b/Calc.java"))
    assert _only("Calc.clamp(0, 1, 5) -> 1", two).status is Status.AMBIGUOUS
    assert _only("Nope.go() -> 1", _tree(tmp_path / "b", STATIC)).status is Status.UNSUPPORTED
    matcher_tree = _tree(tmp_path / "c", ("src/main/java/org/hamcrest/Matchers.java", "src/main/java/org/hamcrest/MatcherAssert.java"))
    no_owner = _only('assertThat("a", same("a")) -> passes', matcher_tree)
    assert no_owner.status is Status.AMBIGUOUS and "states no owning type" in no_owner.reason
    two_owners = _only('A.same(1) and B.same(2) exist.\nassertThat("a", same("a")) -> passes'.split("\n", 1)[1],
                       matcher_tree)
    assert two_owners.status is Status.AMBIGUOUS


def test_ambiguous_expected_behaviour(tmp_path):
    root = _tree(tmp_path, STATIC)
    assert _only("Calc.clamp(0, 1, 5) -> fails", root).status is Status.AMBIGUOUS
    assert _only("Calc.clamp(0, 1, 5) -> throws", root).status is Status.AMBIGUOUS


def test_multiple_examples_keep_goal_order_and_spans(tmp_path):
    goal = "Calc.clamp(0, 1, 5) -> 1\r\n\r\nCalc.clamp(9, 1, 5) -> 5\n"
    found = classify_goal_examples(goal, _tree(tmp_path, STATIC)).examples
    assert [e.expected for e in found] == ["1", "5"] and [e.line for e in found] == [1, 3]
    assert [goal[e.start:e.end] for e in found] == ["Calc.clamp(0, 1, 5) -> 1", "Calc.clamp(9, 1, 5) -> 5"]


def test_contradictory_examples_become_ambiguous_and_repeats_do_not(tmp_path):
    root = _tree(tmp_path, STATIC)
    contradictory = classify_goal_examples("Calc.clamp(0, 1, 5) -> 1\nCalc.clamp(0, 1, 5) -> 2\n", root)
    assert [e.status for e in contradictory.examples] == [Status.AMBIGUOUS, Status.AMBIGUOUS]
    assert "contradicts" in contradictory.examples[0].reason
    repeated = classify_goal_examples('Calc.clamp(0, 1, 5) -> 1    (first call)\nCalc.clamp(0, 1, 5) -> 1    (second call, same JVM)\n', root)
    assert [e.status for e in repeated.examples] == [Status.COMPILED, Status.COMPILED]
    assert [e.expected for e in repeated.examples] == ["1", "1"]
    mixed = classify_goal_examples("Calc.clamp(0, 1, 5) -> 1\nCalc.clamp(0, 1, 5) -> raises IllegalStateException\n", root)
    assert all(e.status is Status.AMBIGUOUS for e in mixed.examples)


def test_unicode_and_escapes_are_rendered_as_ascii_java(tmp_path):
    goal = (f'Matchers.same(String) is the factory.\n'
            f'assertThat("price{NBSP}10 EUR", same("a{EMSP}b\\tc")) -> fails, used to pass\n')
    root = _tree(tmp_path, ("src/main/java/org/hamcrest/Matchers.java", "src/main/java/org/hamcrest/MatcherAssert.java"))
    source = render_probe_class(classify_goal_examples(goal, root).compiled(), "kriya.examples", "Probe")
    assert source.isascii()
    assert '"price\\u00a010 EUR"' in source and '"a\\u2003b\\tc"' in source
    assert gf.java_literal_source('"x\ny"') == '"x\\ny"'  # a raw line break can never end the literal early
    assert gf.java_literal_source('"\U0001F600"') == '"\\ud83d\\ude00"'
    assert gf.java_literal_source("'\\u0041'") == "'\\u0041'"


def test_t2_style_prose_and_non_literal_constants_are_unsupported(tmp_path):
    root = _tree(tmp_path, ("src/main/java/com/j/JsonPath.java",))
    nonliteral = _only('JsonPath.read(BaseTest.JSON_DOCUMENT, "$..book.length()") -> 4    (first call)', root)
    assert nonliteral.status is Status.UNSUPPORTED and "literal" in nonliteral.reason
    assert classify_goal_examples('returns "a" but should be "b"\n', root).examples == []


def test_graduation_gate_is_per_form():
    # only the individually replay-proven forms: instance_value (S3_A) and matcher_pass (T3, module-aware replay)
    assert gf.ENFORCED_FORMS == {gf.FORM_INSTANCE_VALUE, gf.FORM_MATCHER_PASS}
    for unproven in (gf.FORM_INSTANCE_RAISES, gf.FORM_INSTANCE_NO_THROW, gf.FORM_STATIC_NO_THROW):
        assert unproven not in gf.ENFORCED_FORMS


# --- integration with the enforced Java example compiler -----------------------------------------------------------------

def _compile(tmp_path, goal, units):
    from kriya.workflow import example_oracle_java as ej
    from kriya.workflow.requirements import derive_requirements

    root = _tree(tmp_path, units)
    compilation = ej.recognize_java_examples(goal, derive_requirements(goal), root)
    return compilation, ej.compile_java_example_class(compilation)


def test_a_graduated_form_is_enforced_in_the_sealed_class_and_a_shadow_form_is_not(tmp_path):
    goal = ('Fix the builder.\n\nnew Builder("abab").last("") -> 4\n\nnew Builder("a").broken() -> raises IllegalStateException\n\n'
            'Builder.parse("x") -> compiles (no exception)\n')
    compilation, source = _compile(tmp_path, goal, ("src/main/java/demo/Builder.java",))
    assert [e.method for e in compilation.graduated] == ["last"]
    assert 'assertEquals(4, Builder.last' not in source  # instance call, not a static one
    assert 'assertEquals(4, new Builder("abab").last(""));' in source
    # the instance_raises and static_no_throw forms have no replay evidence: shadow, not in the class, never a verdict
    shadow = {(e["form"], e["status"]) for e in compilation.shadow}
    assert ("java_instance_raises", "COMPILED") in shadow and ("java_static_no_throw", "COMPILED") in shadow
    assert "assertThrows(" not in source and "assertDoesNotThrow(" not in source
    assert compilation.to_dict()["graduated_examples"][0]["form"] == "java_instance_value"


def test_a_graduated_line_that_is_part_of_no_statement_is_not_enforced(tmp_path):
    from kriya.workflow import example_oracle_java as ej
    from kriya.workflow.requirements import derive_requirements

    root = _tree(tmp_path, ("src/main/java/demo/Builder.java",))
    elsewhere = 'new Builder("abab").last("") -> 4\n'
    compilation = ej.recognize_java_examples(elsewhere, derive_requirements("Unrelated text about nothing."), root)
    assert compilation.graduated == [] and ej.compile_java_example_class(compilation) is None


def test_the_sealed_class_with_graduated_examples_passes_the_b2c_parser_with_one_case_per_example(tmp_path):
    """Found by the end-to-end S3_A check: provenance text on the marker line broke the B2-c marker grammar."""
    from kriya.workflow.acceptance_jvm import parse_java_acceptance

    goal = 'Fix.\n\nnew Builder("abab").last("") -> 4\n\nnew Builder("").last("") -> 0\n\nBuilder.twice(2) -> 4\n'
    compilation, source = _compile(tmp_path, goal, ("src/main/java/demo/Builder.java",))
    cases, java = parse_java_acceptance(source.encode())
    assert len(cases) == 3 == len(compilation.graduated) + len(compilation.examples)
    assert java["injection_path"] == "src/test/java/kriya/examples/KriyaGoalExamplesTest.java"
    assert "// provenance: goal line 3" in source


@pytest.mark.parametrize("outcome,status,expected", [
    ("4    (first call)", Status.COMPILED, "4"), ("4 (second call, same JVM)", Status.COMPILED, "4"),
    ("4 (bug: should be 5)", Status.AMBIGUOUS, None), ("5 (currently 4)", Status.AMBIGUOUS, None),
    ("4 (before the fix)", Status.AMBIGUOUS, None), ("4", Status.COMPILED, "4")])
def test_only_repeat_notes_are_stripped_from_an_outcome_any_other_parenthetical_is_ambiguous(tmp_path, outcome, status, expected):
    example = _only(f"new Calc().twice(2) -> {outcome}", _tree(tmp_path, STATIC))
    assert example.status is status and example.expected == expected


@pytest.mark.parametrize("literal", ["4L", "0.1", "0.5f", "'a'", "1.0d"])
def test_overload_sensitive_literals_of_the_instance_form_are_not_enforced(tmp_path, literal):
    example = _only(f"new Calc().twice(2) -> {literal}", _tree(tmp_path, STATIC))
    assert example.status is Status.AMBIGUOUS and "return type" in example.reason
    assert _only(f"new Calc().twice(2) -> {literal.replace(chr(39), chr(34))}", _tree(tmp_path / 'x', STATIC)).status in (Status.COMPILED, Status.AMBIGUOUS)


# --- the repository license header (Apache RAT rejected the injected class on S3_A) ---------------------------------------

LICENSE = ("/*\n * Licensed to the Apache Software Foundation (ASF) under one or more\n * contributor license agreements.\n"
           " * the package com.example is mentioned in this header on purpose\n */\n")


def test_the_generated_class_carries_the_repository_license_header_verbatim(tmp_path):
    from kriya.workflow import example_oracle_java as ej
    from kriya.workflow.acceptance_jvm import parse_java_acceptance
    from kriya.workflow.requirements import derive_requirements

    root = _tree(tmp_path, ("src/main/java/demo/Builder.java",))
    (tmp_path / "tree" / "src/main/java/demo/Builder.java").write_text(LICENSE + "package demo;\npublic class Builder {}\n")
    goal = 'Fix.\n\nnew Builder("abab").last("") -> 4\n'
    compilation = ej.recognize_java_examples(goal, derive_requirements(goal), root)
    source = ej.compile_java_example_class(compilation)
    assert compilation.license_header == LICENSE and source.startswith(LICENSE.rstrip("\n") + "\n\npackage kriya.examples;")
    cases, java = parse_java_acceptance(source.encode())  # the header (even one that says "package") does not confuse the B2-c parser
    assert len(cases) == 1 and java["package"] == "kriya.examples"


def test_no_license_comment_means_no_header_and_a_non_license_comment_is_not_copied(tmp_path):
    from kriya.workflow import example_oracle_java as ej
    from kriya.workflow.requirements import derive_requirements

    root = _tree(tmp_path, ("src/main/java/demo/Builder.java",))
    goal = 'Fix.\n\nnew Builder("abab").last("") -> 4\n'
    assert ej.recognize_java_examples(goal, derive_requirements(goal), root).license_header == ""  # file is just "package x;"
    (tmp_path / "tree" / "src/main/java/demo/Builder.java").write_text("/* scratch notes about nothing */\npackage demo;\n")
    assert ej.recognize_java_examples(goal, derive_requirements(goal), root).license_header == ""
