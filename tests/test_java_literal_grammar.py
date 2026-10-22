"""One Java-literal grammar for the goal-example compilers (GOAL-EXAMPLE-LITERAL-BACKSLASH-001).

MEASURED at f6194c4: ``example_oracle_java._LITERAL`` was written inside a raw string with doubled backslashes, so it accepted a
string escape spelled with TWO backslashes and rejected the real goal line ``Text.echo("my\\tfoo") -> "a\\nb"`` (a single backslash,
Java's own spelling), while ``goal_example_forms._LITERAL`` accepted it: the two compilers disagreed about the same goal text.
There is now ONE grammar (the base module's), it accepts exactly Java's escapes, and ``goal_example_forms`` imports it.

Goal lines below are raw strings: the backslash characters are the ones a user types."""
import pytest

from kriya.workflow import example_oracle_java as ej
from kriya.workflow import goal_example_forms as gf
from kriya.workflow.goal_example_forms import Status, classify_goal_examples
from kriya.workflow.requirements import derive_requirements

ACCEPTED = [
    r'"ordinary"', r'""', r'"my\tfoo"', r'"a\nb"', r'"say \"hi\""', r'"back\\slash"', r"'x'", r"'\n'", r"'\''", r"'\u0041'",
    r'"\u00e9"', r'"\0"', r'"\12"', r'"\377"', r'"\s"', r'"\b\f\r"', r'"\400"', "1", "-2", "3L", "1.5", "2e3", "1.0f", "true", "null",
]
MALFORMED = [
    r'"bad\q"',            # not a Java escape
    r'"dangling\"',        # the last quote is escaped: the literal never ends
    r'"dangling\\\"',      # backslash, backslash, escaped quote: still unterminated
    '"trailing\\',         # a lone trailing backslash
    r'"\u12"',             # truncated unicode escape
    r"'ab'", r"''", r"'\q'",
    r'"unterminated', r'unquoted', r'[1, 2]',
]


@pytest.mark.parametrize("literal", ACCEPTED)
def test_a_java_literal_is_accepted(literal):
    assert ej._LITERAL.match(literal), literal             # pylint: disable=protected-access


@pytest.mark.parametrize("literal", MALFORMED)
def test_a_malformed_literal_is_refused(literal):
    assert not ej._LITERAL.match(literal), literal         # pylint: disable=protected-access


def test_there_is_one_grammar_not_two():
    assert gf._LITERAL is ej._LITERAL                       # pylint: disable=protected-access


def _tree(tmp_path):
    root = tmp_path / "tree"
    unit = root / "src/main/java/demo/Text.java"
    unit.parent.mkdir(parents=True)
    unit.write_text("package demo;\n")
    return str(root)


def _recognized(tmp_path, goal, root=None):
    return ej.recognize_java_examples(goal, derive_requirements(goal), root or _tree(tmp_path))


@pytest.mark.parametrize("line, arguments, expected", [
    (r'Text.echo("my\tfoo") -> "a\nb"', r'"my\tfoo"', r'"a\nb"'),
    (r'Text.echo("say \"hi\"") -> "ok"', r'"say \"hi\""', r'"ok"'),
    (r'Text.echo("plain") -> "plain"', r'"plain"', r'"plain"'),
    (r'Text.pair("a\\b", 7) -> "x"', r'"a\\b", 7', r'"x"'),
], ids=["tab-and-newline", "escaped-quote", "ordinary", "backslash-and-number"])
def test_the_enforced_static_form_recognizes_what_the_goal_says(tmp_path, line, arguments, expected):
    compilation = _recognized(tmp_path, "Add Text.echo.\n\n" + line + "\n")
    assert [(e.form, e.arguments, e.expected) for e in compilation.examples] == [(ej.FORM_JAVA_LITERAL, arguments, expected)], \
        compilation.rejected


@pytest.mark.parametrize("line", [r'Text.echo("bad\q") -> "x"', r'Text.echo("x") -> "bad\q"', r'Text.echo("open\") -> "x"'],
                         ids=["bad-argument", "bad-expected", "unterminated"])
def test_a_malformed_escape_is_rejected_with_a_reason_never_guessed(tmp_path, line):
    compilation = _recognized(tmp_path, "Add Text.echo.\n\n" + line + "\n")
    assert compilation.examples == [] and len(compilation.rejected) == 1, compilation
    assert "Java literal" in compilation.rejected[0]["why"]


def test_the_classifier_and_the_enforced_compiler_agree_on_the_same_line(tmp_path):
    """The two compilers read the same goal text: an escaped literal is COMPILED by the classifier exactly when the enforced
    compiler recognizes it, and a malformed escape is neither."""
    root = _tree(tmp_path)
    for line, ok in ((r'Text.echo("my\tfoo") -> "a\nb"', True), (r'Text.echo("bad\q") -> "x"', False)):
        goal = "Add Text.echo.\n\n" + line + "\n"
        compiled = [e for e in classify_goal_examples(goal, root).examples if e.status is Status.COMPILED]
        recognized = _recognized(tmp_path, goal, root).examples
        assert bool(compiled) is ok and bool(recognized) is ok, (line, compiled, recognized)
