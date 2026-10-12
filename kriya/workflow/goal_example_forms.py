"""BACKEND-READINESS-V2 (Stage C, change 1): a deterministic classifier for the Java example lines of a goal.

USER TEXT is authority, MODEL INTERPRETATION is not. Every arrow line (``expression -> outcome``) of the RAW goal is read
by a pure function of the goal text (and, for type binding only, the candidate's main source tree) into exactly one of

- ``COMPILED``    the line is one of a few constrained forms, every argument is a Java literal, the outcome is
                  unambiguous and the types it names resolve: it can be rendered into a JUnit 5 assertion;
- ``AMBIGUOUS``   the line could mean more than one thing (an unresolvable or non-unique target, a bare ``fails``, a
                  contradiction with another example): never turned into an assertion;
- ``UNSUPPORTED`` the line is outside the grammar (non-literal argument, chained expression, prose).

UNSUPPORTED and AMBIGUOUS never reject a candidate. Each result keeps provenance: the exact line, the character span in the
goal, and the reason. No model output is read and no task identifier is consulted.

Forms (``form``):

- ``java_static_value`` / ``java_static_raises`` ``Type.method(lits) -> literal | raises E`` (enforced elsewhere, by
  ``example_oracle_java``; classified here for completeness);
- ``java_static_no_throw``   ``Type.method(lits) -> compiles (no exception)``;
- ``java_instance_value`` / ``_raises`` / ``_no_throw``   ``new Type(lits).method(lits) -> ...``;
- ``java_matcher_pass``      ``assertThat(literal, factory(lits)) -> passes`` and the regression wording
  ``-> fails, used to pass``.

REGRESSION WORDING: ``fails, used to pass`` describes the broken baseline. The repaired behaviour is that the assertion
PASSES. The word ``fails`` is never the expected outcome unless the goal says so unambiguously (it never does here); a bare
``fails`` is AMBIGUOUS because it may describe the bug or the target.

Graduation: a form is enforced only when it is in ``ENFORCED_FORMS``; every other COMPILED form is SHADOW (classified and
replayable, mismatches are observational evidence only).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from kriya.workflow.example_oracle_java import (
    _ARROW,
    _CALL,
    _RAISES,
    _resolve_exception,
    _split_arguments,
    resolve_java_type,
)

GOAL_EXAMPLE_FORMS_VERSION = 1

# A Java literal. NOTE: example_oracle_java._LITERAL is written inside a raw string with doubled backslashes, so it only
# accepts a string escape spelled with TWO backslashes; a real goal line such as "my\tfoo" is rejected there (safe
# direction, but it hides T3's own example). This pattern accepts the escapes Java accepts.
_LITERAL = re.compile(
    r"^(?:-?\d+[lL]|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?[fFdD]?|-?\d+|\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\u[0-9a-fA-F]{4}|\\.)'|true|false|null)$")


class Status(str, Enum):
    COMPILED = "COMPILED"
    AMBIGUOUS = "AMBIGUOUS"
    UNSUPPORTED = "UNSUPPORTED"


class Expect(str, Enum):
    VALUE = "VALUE"
    RAISES = "RAISES"
    NO_THROW = "NO_THROW"
    PASS = "PASS"


FORM_STATIC_VALUE = "java_static_value"
FORM_STATIC_RAISES = "java_static_raises"
FORM_STATIC_NO_THROW = "java_static_no_throw"
FORM_INSTANCE_VALUE = "java_instance_value"
FORM_INSTANCE_RAISES = "java_instance_raises"
FORM_INSTANCE_NO_THROW = "java_instance_no_throw"
FORM_MATCHER_PASS = "java_matcher_pass"
ALL_FORMS = (FORM_STATIC_VALUE, FORM_STATIC_RAISES, FORM_STATIC_NO_THROW, FORM_INSTANCE_VALUE, FORM_INSTANCE_RAISES,
             FORM_INSTANCE_NO_THROW, FORM_MATCHER_PASS)
# Forms graduated to enforcement THROUGH THIS MODULE by shadow replay (doc 15), one by one. java_instance_value: S3_A replay,
# 9/9 MATCH on the independent known-good and on the unrelated change over two runs, 5 wrong subjects rejected (including the
# recorded Kriya candidate). java_matcher_pass: replay-clean as a probe (T3: 4/4 on known-good, unrelated and the recorded GENUINE
# candidate; wrong subjects incl. the recorded failing candidate rejected), and graduated (BACKEND-READINESS-V2 final cycle,
# JVM-MODULE-OWNERSHIP-001) after the replay the module-aware runner required: the PRODUCTION derive -> run_java_acceptance ->
# judge path, real Gradle under the production OCI containment with the sanitized git metadata, in the declaring build module
# (module-replay/module_replay.json: 24 runs over 8 subjects - correct
# subjects PASS 9/9 incl. the recorded GENUINE candidate, wrong subjects VIOLATED 15/15 incl. both recorded failing candidates,
# identical verdict on every repeat). The replay evidence is ONE project (the sealed class is JUnit 5; a project whose test source
# set lacks Jupiter or the matcher library yields HARNESS_COMPILE_FAILED / INDETERMINATE evidence - it blocks closure, it is
# never a rejection). A matcher example whose declaring module cannot be resolved stays shadow
# (MODULE_NOT_ENFORCEABLE, example_oracle_java._scope_to_owning_module). java_static_no_throw has no valid replay (the only
# project with the form uses JUnit 4). The two static value/raises forms are enforced by example_oracle_java on its own
# (narrower) recognizer; they are classified here too.
ENFORCED_FORMS: frozenset = frozenset({FORM_INSTANCE_VALUE, FORM_MATCHER_PASS})

_NO_THROW_PHRASES = frozenset({
    "compiles (no exception)", "no exception", "no exception thrown", "does not throw", "doesn't throw", "does not raise",
    "compiles without exception", "compiles without exceptions", "runs without exception"})
_PASS_PHRASES = frozenset({"passes", "succeeds", "holds", "matches"})
_REGRESSION_PASS = re.compile(
    r"^(?:fails|no longer (?:passes|holds|matches))[,;]?\s*\(?(?:but )?(?:it )?(?:used to|previously|formerly|once|did) (?:pass|work|match|hold)(?:ed|es)?\)?$")
_BARE_FAILURE = re.compile(r"^(?:fails|throws|errors?|breaks|is rejected|raises)$")
# Only these parentheticals are repeat/sequence notes (S4_B: "(first call)", "(second call, same JVM)"); any other trailing
# parenthetical may reverse the meaning ("-> 4 (bug: should be 5)") and makes the line AMBIGUOUS.
_REPEAT_NOTE = re.compile(r"\s*\((?:(?:first|second|third|fourth|fifth|\d+(?:st|nd|rd|th)|next|again|same|another|repeat\w*|later|last)\b[^()]*)\)\s*$", re.I)
_ANY_PAREN_NOTE = re.compile(r"\s*\([^()]*\)\s*$")
# Literal kinds whose assertEquals overload depends on the (unknown) declared return type: not enforced.
_TYPE_SENSITIVE_LITERAL = re.compile(r"^(?:-?\d+[lL]|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?[fFdD]|-?\d+\.\d+(?:[eE][+-]?\d+)?|'.*')$")
_FACTORY = re.compile(r"^(?P<type>(?:[a-z]\w*\.)*[A-Z]\w*\.)?(?P<name>[a-z]\w*)$")
_TYPE_NAME = re.compile(r"^(?:[a-z]\w*\.)*[A-Z]\w*$")
_METHOD_NAME = re.compile(r"^[a-z]\w*$")


@dataclass(frozen=True)
class GoalExample:
    """One arrow line of the goal and what the classifier decided about it."""

    status: Status
    form: Optional[str]
    reason: str
    line: int
    start: int  # character offsets of the arrow line (stripped) in the goal text
    end: int
    text: str
    requirement_id: Optional[str] = None
    expect: Optional[Expect] = None
    expected: Optional[str] = None  # Java literal as written (VALUE)
    exception: Optional[str] = None  # qualified name (RAISES)
    regression_wording: bool = False
    target_type: Optional[str] = None  # as written
    qualified_type: Optional[str] = None  # resolved package.Type
    constructor_arguments: Optional[str] = None  # instance forms
    method: Optional[str] = None
    arguments: Optional[str] = None
    actual_literal: Optional[str] = None  # matcher form: the examined value
    factory_type: Optional[str] = None  # matcher form: resolved factory type
    assertion_type: Optional[str] = None  # matcher form: resolved assertThat holder (org.hamcrest.MatcherAssert)

    @property
    def enforced(self) -> bool:
        return self.status is Status.COMPILED and self.form in ENFORCED_FORMS

    def to_dict(self) -> Dict[str, Any]:
        return {"status": self.status.value, "form": self.form, "reason": self.reason, "line": self.line,
                "span": [self.start, self.end], "text": self.text, "requirement_id": self.requirement_id,
                "expect": self.expect.value if self.expect else None, "expected": self.expected,
                "exception": self.exception, "regression_wording": self.regression_wording,
                "enforced": self.enforced, "qualified_type": self.qualified_type, "method": self.method}


@dataclass
class GoalExampleClassification:
    examples: List[GoalExample] = field(default_factory=list)

    def compiled(self, *, enforced: Optional[bool] = None) -> List[GoalExample]:
        return [e for e in self.examples if e.status is Status.COMPILED and (enforced is None or e.enforced == enforced)]

    def counts(self) -> Dict[str, int]:
        out = {status.value: 0 for status in Status}
        for example in self.examples:
            out[example.status.value] += 1
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {"version": GOAL_EXAMPLE_FORMS_VERSION, "counts": self.counts(),
                "examples": [e.to_dict() for e in self.examples]}


def _matching_paren(text: str, open_index: int) -> Optional[int]:
    """Index of the ')' closing the '(' at ``open_index``, honouring string and char literals; None when unbalanced."""
    depth, quote, escaped = 0, None, False
    for index in range(open_index, len(text)):
        ch = text[index]
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _literal_arguments(text: str) -> Optional[List[str]]:
    """The arguments when every one is a Java literal; None otherwise."""
    parts = _split_arguments(text)
    if parts is None or any(not _LITERAL.match(part) for part in parts):
        return None
    return parts


def _outcome(raw: str, goal: str, matcher: bool) -> Tuple[Optional[Expect], Optional[str], Optional[str], bool, str, Status]:
    """(expect, literal, exception, regression, reason, status) for the text right of the arrow."""
    text = " ".join(raw.split())
    lowered = text.lower()
    if lowered in _NO_THROW_PHRASES:
        return Expect.NO_THROW, None, None, False, "", Status.COMPILED
    raises = _RAISES.match(text)
    if raises:
        exception = _resolve_exception(raises.group("name"), goal)
        if exception is None:
            return None, None, None, False, f"exception {raises.group('name')!r} is neither qualified, stated qualified elsewhere in the goal nor a java.lang exception", Status.AMBIGUOUS
        return Expect.RAISES, None, exception, False, "", Status.COMPILED
    if matcher:
        if lowered in _PASS_PHRASES:
            return Expect.PASS, None, None, False, "", Status.COMPILED
        if _REGRESSION_PASS.match(lowered):
            return Expect.PASS, None, None, True, "", Status.COMPILED
        if re.search(r"used to fail|did not pass|didn't pass", lowered):
            return None, None, None, False, "the wording does not say which of baseline and target passes", Status.AMBIGUOUS
        if _BARE_FAILURE.match(lowered):
            return None, None, None, False, "a bare 'fails' may describe the broken baseline or the target", Status.AMBIGUOUS
        return None, None, None, False, "the outcome of an assertThat example must be 'passes' or the regression wording 'fails, used to pass'", Status.UNSUPPORTED
    if _BARE_FAILURE.match(lowered):
        return None, None, None, False, "a bare failure word without an exception type is not an expected behaviour", Status.AMBIGUOUS
    stripped = _REPEAT_NOTE.sub("", text) if _REPEAT_NOTE.search(text) and _REPEAT_NOTE.sub("", text) else text
    if _ANY_PAREN_NOTE.search(stripped) and not _LITERAL.match(stripped):
        return None, None, None, False, "the outcome carries a parenthetical that is not a repeat note and may change its meaning", Status.AMBIGUOUS
    if _LITERAL.match(stripped):
        return Expect.VALUE, stripped, None, False, "", Status.COMPILED
    return None, None, None, False, "the right side is not a Java literal, 'raises E' or a no-exception phrase", Status.UNSUPPORTED


def _call_parts(expression: str) -> Optional[Tuple[str, Optional[str], Optional[str], str]]:
    """(kind, type, constructor_args, method) + args for ``Type.m(a)`` / ``new Type(c).m(a)``; None when it is neither.
    Returned as (kind, type, ctor_args_or_None, "method\\0args")."""
    new = re.match(r"^new\s+((?:[a-z]\w*\.)*[A-Z]\w*)\(", expression)
    if new:
        open_index = new.end() - 1
        close = _matching_paren(expression, open_index)
        if close is None:
            return None
        rest = expression[close + 1:]
        call = re.match(r"^\.([a-z]\w*)\(", rest)
        if not call:
            return None
        inner_open = close + 1 + call.end() - 1
        inner_close = _matching_paren(expression, inner_open)
        if inner_close is None or inner_close != len(expression) - 1:
            return None
        return ("instance", new.group(1), expression[open_index + 1:close],
                call.group(1) + "\0" + expression[inner_open + 1:inner_close])
    static = _CALL.match(expression)
    if static and _matching_paren(expression, expression.index("(")) == len(expression) - 1:
        return "static", static.group("type"), None, static.group("method") + "\0" + static.group("args")
    return None


def _classify_line(goal: str, number: int, start: int, line: str, root: Optional[str], requirement_set: Any) -> GoalExample:
    stripped = line.strip()
    begin = start + (len(line) - len(line.lstrip()))
    end = begin + len(stripped)
    match = _ARROW.match(line)
    expression, outcome_text = match.group("expr"), match.group("expected")

    def result(status: Status, reason: str, **fields: Any) -> GoalExample:
        requirement_id = None
        if requirement_set is not None:
            from kriya.workflow.example_oracle import _binding_requirement

            requirement_id = _binding_requirement(line, requirement_set)
        return GoalExample(status=status, form=fields.pop("form", None), reason=reason, line=number, start=begin, end=end,
                           text=stripped, requirement_id=requirement_id, **fields)

    if expression.startswith("assertThat("):
        return _classify_matcher(goal, expression, outcome_text, root, result)
    parts = _call_parts(expression)
    if parts is None:
        return result(Status.UNSUPPORTED, "the left side is not Type.method(literals) or new Type(literals).method(literals)")
    kind, type_name, constructor_arguments, packed = parts
    method, arguments_text = packed.split("\0", 1)
    arguments = _literal_arguments(arguments_text) if arguments_text.strip() else []
    if arguments is None:
        return result(Status.UNSUPPORTED, "every argument must be a Java literal (int, long, double, String, char, boolean, null)")
    constructor = None
    if constructor_arguments is not None:
        constructor = _literal_arguments(constructor_arguments) if constructor_arguments.strip() else []
        if constructor is None:
            return result(Status.UNSUPPORTED, "every constructor argument must be a Java literal")
    expect, literal, exception, regression, reason, status = _outcome(outcome_text, goal, matcher=False)
    if status is not Status.COMPILED:
        return result(status, reason)
    qualified = None
    if root is not None:
        qualified, why = resolve_java_type(root, type_name)
        if qualified is None:
            return result(Status.AMBIGUOUS if "ambiguous" in why else Status.UNSUPPORTED, why)
    if kind == "instance" and expect is Expect.VALUE and literal is not None and _TYPE_SENSITIVE_LITERAL.match(literal):
        return result(Status.AMBIGUOUS, "the expected literal's overload depends on the declared return type (long/float/double/char)")
    suffix = {Expect.VALUE: "value", Expect.RAISES: "raises", Expect.NO_THROW: "no_throw"}[expect]
    return result(Status.COMPILED, "", form=f"java_{kind}_{suffix}", expect=expect, expected=literal, exception=exception,
                  target_type=type_name, qualified_type=qualified, method=method, arguments=", ".join(arguments),
                  constructor_arguments=None if constructor is None else ", ".join(constructor))


def _classify_matcher(goal: str, expression: str, outcome_text: str, root: Optional[str], result: Any) -> GoalExample:
    close = _matching_paren(expression, len("assertThat") )
    if close is None or close != len(expression) - 1:
        return result(Status.UNSUPPORTED, "assertThat(...) must be the whole left side")
    pieces = _split_arguments(expression[len("assertThat") + 1:close])
    if pieces is None or len(pieces) != 2:
        return result(Status.UNSUPPORTED, "assertThat must have exactly two arguments: a literal and a matcher factory call")
    actual, factory_call = pieces
    if not _LITERAL.match(actual):
        return result(Status.UNSUPPORTED, "the examined value must be a Java literal")
    paren = factory_call.find("(")
    if paren < 0 or _matching_paren(factory_call, paren) != len(factory_call) - 1:
        return result(Status.UNSUPPORTED, "the matcher must be one factory call")
    factory = _FACTORY.match(factory_call[:paren].strip())
    arguments_text = factory_call[paren + 1:-1]
    arguments = _literal_arguments(arguments_text) if arguments_text.strip() else []
    if factory is None or arguments is None:
        return result(Status.UNSUPPORTED, "the matcher must be factory(literals) or Type.factory(literals)")
    expect, _literal, _exception, regression, reason, status = _outcome(outcome_text, goal, matcher=True)
    if status is not Status.COMPILED:
        return result(status, reason)
    name = factory.group("name")
    factory_type = factory.group("type")[:-1] if factory.group("type") else None
    qualified = assertion = None
    if root is not None:
        if factory_type is None:
            stated = sorted({m.group(1) for m in re.finditer(rf"\b((?:[a-z]\w*\.)*[A-Z]\w*)\.{re.escape(name)}\(", goal)})
            if len(stated) != 1:
                return result(Status.AMBIGUOUS, f"factory {name!r} is not qualified and the goal "
                              + ("states no owning type" if not stated else "states several owning types: " + ", ".join(stated)))
            factory_type = stated[0]
        qualified, why = resolve_java_type(root, factory_type)
        if qualified is None:
            return result(Status.AMBIGUOUS if "ambiguous" in why else Status.UNSUPPORTED, why)
        assertion, why = resolve_java_type(root, "MatcherAssert")
        if assertion is None:
            return result(Status.UNSUPPORTED, "no MatcherAssert main compilation unit: " + why)
    return result(Status.COMPILED, "", form=FORM_MATCHER_PASS, expect=Expect.PASS, regression_wording=regression,
                  target_type=factory_type, qualified_type=qualified, method=name, arguments=", ".join(arguments),
                  actual_literal=actual, factory_type=qualified, assertion_type=assertion)


def _signature(example: GoalExample) -> Tuple[Any, ...]:
    return (example.form and example.form.rsplit("_", 1)[0], example.qualified_type or example.target_type,
            example.constructor_arguments, example.method, example.arguments, example.actual_literal)


def _outcome_key(example: GoalExample) -> Tuple[Any, ...]:
    return (example.expect, example.expected, example.exception)


def classify_goal_examples(goal: str, candidate_root: Optional[str] = None, requirement_set: Any = None) -> GoalExampleClassification:
    """Every arrow line of ``goal`` classified (pure given the goal and, for type binding, the main source tree).

    Two COMPILED examples of the same call with different expected outcomes contradict each other: both become AMBIGUOUS
    (a contradiction is evidence the goal is unclear, never a basis to reject)."""
    classification = GoalExampleClassification()
    offset = 0
    for number, line in enumerate(goal.splitlines(keepends=True), start=1):
        body = line.rstrip("\r\n")
        if _ARROW.match(body):
            classification.examples.append(_classify_line(goal, number, offset, body, candidate_root, requirement_set))
        offset += len(line)
    groups: Dict[Tuple[Any, ...], set] = {}
    for example in classification.compiled():
        groups.setdefault(_signature(example), set()).add(_outcome_key(example))
    contradictory = {signature for signature, outcomes in groups.items() if len(outcomes) > 1}
    if contradictory:
        classification.examples = [
            replace(example, status=Status.AMBIGUOUS, reason="contradicts another example of the same call")
            if example.status is Status.COMPILED and _signature(example) in contradictory else example
            for example in classification.examples]
    return classification


# ---- rendering (JUnit 5) ---------------------------------------------------------------------------------------------

_SIMPLE_ESCAPES = {"\n": "\\n", "\r": "\\r", "\t": "\\t", "\b": "\\b", "\f": "\\f"}


def java_literal_source(literal: str) -> str:
    """The literal with every raw control or non-ASCII character as a Java escape, so the generated source is ASCII and a
    raw U+000A/U+000D can never end a literal early. Existing escape sequences (``\\t``, ``\\uXXXX``) are kept as written."""
    out: List[str] = []
    index = 0
    while index < len(literal):
        ch = literal[index]
        if ch == "\\" and index + 1 < len(literal):
            out.append(literal[index:index + 2])
            index += 2
            continue
        if ch in _SIMPLE_ESCAPES:
            out.append(_SIMPLE_ESCAPES[ch])
        elif ord(ch) < 0x20 or ord(ch) > 0x7E:
            units = ch.encode("utf-16-be")
            out.extend("\\u%04x" % int.from_bytes(units[i:i + 2], "big") for i in range(0, len(units), 2))
        else:
            out.append(ch)
        index += 1
    return "".join(out)


def _args_source(text: Optional[str]) -> str:
    return ", ".join(java_literal_source(part) for part in (_split_arguments(text) or [])) if text else ""


def render_test_method(example: GoalExample, number: int) -> List[str]:
    """The JUnit 5 test method (as lines) for one COMPILED example; shadow and enforced forms render identically."""
    simple = (example.qualified_type or example.target_type or "").rsplit(".", 1)[-1]
    call = f"{simple}.{example.method}({_args_source(example.arguments)})"
    if example.constructor_arguments is not None:
        call = f"new {simple}({_args_source(example.constructor_arguments)}).{example.method}({_args_source(example.arguments)})"
    lines = [f"    // kriya_requirement: {example.requirement_id or 'REQ-0'}",  # the B2-c marker grammar takes ids only
             f"    // provenance: goal line {example.line}, chars {example.start}-{example.end}",
             "    @Test", f"    void example{number}() {{"]
    if example.expect is Expect.RAISES:
        lines.append(f"        assertThrows({example.exception}.class, () -> {call});")
    elif example.expect is Expect.NO_THROW:
        lines.append(f"        assertDoesNotThrow(() -> {call});")
    elif example.expect is Expect.PASS:
        holder = (example.assertion_type or "MatcherAssert").rsplit(".", 1)[-1]
        factory = f"{simple}.{example.method}({_args_source(example.arguments)})"
        lines.append(f"        {holder}.assertThat({java_literal_source(example.actual_literal or '')}, {factory});")
    elif example.expected == "null":
        lines.append(f"        assertNull({call});")
    else:
        lines.append(f"        assertEquals({java_literal_source(example.expected or '')}, {call});")
    lines.append("    }")
    return lines


def render_probe_class(examples: Sequence[GoalExample], package: str, class_name: str) -> Optional[str]:
    """A self-contained JUnit 5 class for ``examples`` (None when there is none)."""
    if not examples:
        return None
    imports = sorted({e.qualified_type for e in examples if e.qualified_type and "." in e.qualified_type}
                     | {e.assertion_type for e in examples if e.assertion_type and "." in e.assertion_type}
                     | {e.exception for e in examples if e.exception and not e.exception.startswith("java.lang.")})
    out = [f"package {package};", "",
           f"// Kriya goal-example probe (goal_example_forms v{GOAL_EXAMPLE_FORMS_VERSION}): the goal's own example lines, compiled deterministically.",
           "import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;",
           "import static org.junit.jupiter.api.Assertions.assertEquals;",
           "import static org.junit.jupiter.api.Assertions.assertNull;",
           "import static org.junit.jupiter.api.Assertions.assertThrows;", "", "import org.junit.jupiter.api.Test;"]
    out += [f"import {name};" for name in imports]
    out += ["", f"class {class_name} {{"]
    for number, example in enumerate(examples, start=1):
        out += render_test_method(example, number)
    out += ["}", ""]
    return "\n".join(out)
