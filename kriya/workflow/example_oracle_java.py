"""JAVA-EXAMPLE-COMPILER-001 (BACKEND-READINESS-004): the goal's own Java example lines, compiled into a sealed
B2-c acceptance class (closer ``derived_examples``, the same closer the Python compiler feeds).

USER TEXT is authority, MODEL INTERPRETATION is not: only deliberately constrained, machine-readable forms found in
the RAW goal are compiled, by a deterministic, version-pinned function of those lines:

- ``Type.method(args) -> literal``: a static call on a simple or qualified type with Java literal arguments
  (int, long, double/float, String, char, boolean, null) and a Java literal result, asserted with JUnit 5
  ``assertEquals`` (``assertNull`` for ``null``);
- ``Type.method(args) -> raises ExceptionType``: ``assertThrows``; the exception is a qualified name, a name the goal
  states qualified elsewhere, or one of ``java.lang``'s standard exceptions.

A simple type name must resolve to exactly ONE compilation unit under a ``src/main/java`` root of the candidate (the
package is its path); zero or several is a rejection with a reason, never a guess. Anything else (an instance
expression, a non-literal argument, a collection result, prose) is rejected with a reason. Each example binds to the
one derived statement whose text contains it (B2-COV: finite examples close only an EXACT statement). The class lives
in package ``kriya.examples`` (``src/test/java/kriya/examples/KriyaGoalExamplesTest.java``, a path the collision rule
protects), carries ``// kriya_requirement`` markers, is validated by the B2-c parser, stored content-addressed in the
acceptance store before any model call and runs under the B2-c boundary (Maven or Gradle gate, trust surface,
integrity). A generated assertion the candidate's types make ambiguous (``assertEquals(1, Integer)``) fails to
compile and is reported ACCEPTANCE_HARNESS_COMPILE_FAILED - never a verdict, never a guess at a cast.
"""
from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from kriya.workflow.acceptance_oracle import AcceptanceArtifact, AcceptanceError, acceptance_store
from kriya.workflow.example_oracle import EXAMPLE_COMPILER_VERSION, _binding_requirement
from kriya.workflow.requirements import RequirementSet

JAVA_EXAMPLE_COMPILER_VERSION = 1
JAVA_EXAMPLE_PACKAGE = "kriya.examples"
JAVA_EXAMPLE_CLASS = "KriyaGoalExamplesTest"
JAVA_EXAMPLE_SOURCE_NAME = JAVA_EXAMPLE_CLASS + ".java"
FORM_JAVA_LITERAL = "java_arrow_literal"
FORM_JAVA_RAISES = "java_arrow_raises"

_ARROW = re.compile(r"^\s*(?P<expr>\S.*?)\s*->\s*(?P<expected>\S.*?)\s*$")
_RAISES = re.compile(r"^raises\s+(?P<name>[A-Za-z_][\w.]*)\s*$", re.IGNORECASE)
_CALL = re.compile(r"^(?P<type>(?:[a-z]\w*\.)*[A-Z]\w*)\.(?P<method>[a-z]\w*)\((?P<args>.*)\)$")
_LITERAL = re.compile(
    r"^(?:-?\d+[lL]|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?[fFdD]?|-?\d+|\"(?:[^\"\\\\]|\\\\.)*\"|'(?:[^'\\\\]|\\\\.)'|true|false|null)$")
_JAVA_LANG_EXCEPTIONS = frozenset({
    "ArithmeticException", "ArrayIndexOutOfBoundsException", "ArrayStoreException", "ClassCastException", "Exception",
    "IllegalArgumentException", "IllegalStateException", "IndexOutOfBoundsException", "NegativeArraySizeException",
    "NullPointerException", "NumberFormatException", "RuntimeException", "SecurityException",
    "StringIndexOutOfBoundsException", "UnsupportedOperationException"})
_MAIN_ROOT = ("src", "main", "java")


@dataclass(frozen=True)
class JavaGoalExample:
    form: str
    requirement_id: str
    line: int
    source: str
    type_name: str  # as written
    qualified_type: str  # resolved package.Type
    method: str
    arguments: str
    expected: Optional[str] = None  # the Java literal as written
    exception: Optional[str] = None  # qualified exception name

    def to_dict(self) -> Dict[str, Any]:
        return {"form": self.form, "requirement_id": self.requirement_id, "lines": [self.line], "source": self.source,
                "expression": f"{self.type_name}.{self.method}({self.arguments})", "expected": self.expected,
                "exception": self.exception, "qualified_type": self.qualified_type}


@dataclass
class JavaExampleCompilation:
    examples: List[JavaGoalExample] = field(default_factory=list)
    rejected: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"compiler_version": EXAMPLE_COMPILER_VERSION, "java_compiler_version": JAVA_EXAMPLE_COMPILER_VERSION,
                "examples": [e.to_dict() for e in self.examples], "rejected": list(self.rejected),
                "modules": sorted({e.qualified_type for e in self.examples})}


def _split_arguments(text: str) -> Optional[List[str]]:
    """Top-level comma split honouring string/char literals; None when unbalanced."""
    parts: List[str] = []
    current: List[str] = []
    quote: Optional[str] = None
    escaped = False
    for ch in text:
        if quote:
            current.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in ("\"", "'"):
            quote = ch
            current.append(ch)
        elif ch == ",":
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if quote:
        return None
    tail = "".join(current).strip()
    if tail or parts:
        parts.append(tail)
    return parts


def resolve_java_type(candidate_root: str, name: str) -> Tuple[Optional[str], str]:
    """(qualified name, why) for a type the goal names: a qualified name must
    exist as ``src/main/java/<pkg>/<Type>.java``; a simple name must match
    exactly one such unit under any ``src/main/java`` root."""
    simple = name.rsplit(".", 1)[-1]
    matches: List[str] = []
    for current, dirs, files in os.walk(candidate_root):
        dirs[:] = sorted(d for d in dirs if d not in (".git", ".kriya", "target", "build", "node_modules"))
        if f"{simple}.java" not in files:
            continue
        rel = os.path.relpath(current, candidate_root).split(os.sep)
        try:
            index = next(i for i in range(len(rel) - 2) if tuple(rel[i:i + 3]) == _MAIN_ROOT)
        except StopIteration:
            continue
        package = ".".join(rel[index + 3:])
        matches.append(f"{package}.{simple}" if package else simple)
    if "." in name:
        return (name, "") if name in matches else (None, f"no main compilation unit for {name}")
    if len(matches) == 1:
        return matches[0], ""
    if not matches:
        return None, f"no main compilation unit named {simple}.java under a src/main/java root"
    return None, f"type {simple} is ambiguous: " + ", ".join(sorted(matches))


def _resolve_exception(name: str, goal: str) -> Optional[str]:
    if "." in name:
        return name
    stated = re.search(rf"\b((?:[a-z]\w*\.)+){re.escape(name)}\b", goal)
    if stated:
        return stated.group(1) + name
    if name in _JAVA_LANG_EXCEPTIONS:
        return "java.lang." + name
    return None


def recognize_java_examples(goal: str, requirement_set: RequirementSet, candidate_root: str) -> JavaExampleCompilation:
    """Every machine-readable Java example of ``goal`` (pure given the goal and
    the candidate's main source tree), each bound to its statement; every
    rejected arrow line with why."""
    compilation = JavaExampleCompilation()
    for number, line in enumerate(goal.splitlines(), start=1):
        match = _ARROW.match(line)
        if match is None:
            continue
        expression, expected = match.group("expr"), match.group("expected")

        def reject(why: str, line_no: int = number, text: str = line) -> None:
            compilation.rejected.append({"line": line_no, "text": text.strip(), "why": why})

        call = _CALL.match(expression)
        if call is None:
            reject("the left side is not a static call Type.method(literal arguments)")
            continue
        arguments = _split_arguments(call.group("args"))
        if arguments is None or any(not _LITERAL.match(a) for a in arguments):
            reject("every argument must be a Java literal (int, long, double, String, char, boolean, null)")
            continue
        rid = _binding_requirement(line, requirement_set)
        if rid is None:
            reject("the line is not part of any derived statement")
            continue
        qualified, why = resolve_java_type(candidate_root, call.group("type"))
        if qualified is None:
            reject(why)
            continue
        raises = _RAISES.match(expected)
        if raises:
            exception = _resolve_exception(raises.group("name"), goal)
            if exception is None:
                reject(f"exception {raises.group('name')!r} is neither qualified, stated qualified elsewhere in the goal "
                       "nor a java.lang exception")
                continue
            compilation.examples.append(JavaGoalExample(FORM_JAVA_RAISES, rid, number, line.strip(), call.group("type"),
                                                        qualified, call.group("method"), ", ".join(arguments),
                                                        exception=exception))
            continue
        if not _LITERAL.match(expected):
            reject("the right side is not a Java literal")
            continue
        compilation.examples.append(JavaGoalExample(FORM_JAVA_LITERAL, rid, number, line.strip(), call.group("type"),
                                                    qualified, call.group("method"), ", ".join(arguments), expected=expected))
    return compilation


def compile_java_example_class(compilation: JavaExampleCompilation) -> Optional[str]:
    """The JUnit 5 class for the recognized examples (None when there is none)."""
    if not compilation.examples:
        return None
    imports = sorted({e.qualified_type for e in compilation.examples if "." in e.qualified_type}
                     | {e.exception for e in compilation.examples if e.exception and not e.exception.startswith("java.lang.")})
    out = [f"package {JAVA_EXAMPLE_PACKAGE};", "",
           "// Kriya-derived acceptance class (VERIFICATION-CONTRACT-003 / JAVA-EXAMPLE-COMPILER-001, compiler version",
           f"// {EXAMPLE_COMPILER_VERSION}.{JAVA_EXAMPLE_COMPILER_VERSION}): the goal's own example lines, compiled deterministically; never prose.",
           "import static org.junit.jupiter.api.Assertions.assertEquals;",
           "import static org.junit.jupiter.api.Assertions.assertNull;",
           "import static org.junit.jupiter.api.Assertions.assertThrows;", "",
           "import org.junit.jupiter.api.Test;"]
    out += [f"import {name};" for name in imports]
    out += ["", f"class {JAVA_EXAMPLE_CLASS} {{"]
    for number, example in enumerate(compilation.examples, start=1):
        call = f"{example.qualified_type.rsplit('.', 1)[-1]}.{example.method}({example.arguments})"
        out += [f"    // kriya_requirement: {example.requirement_id}", "    @Test", f"    void example{number}() {{"]
        if example.form == FORM_JAVA_RAISES:
            out.append(f"        assertThrows({example.exception}.class, () -> {call});")
        elif example.expected == "null":
            out.append(f"        assertNull({call});")
        else:
            out.append(f"        assertEquals({example.expected}, {call});")
        out.append("    }")
    out += ["}", ""]
    return "\n".join(out)


def derive_java_example_artifact(
    goal: str, requirement_set: RequirementSet, *, state_root: str, candidate_root: str,
) -> Tuple[Optional[AcceptanceArtifact], Dict[str, Any]]:
    """Recognize, compile, validate (the B2-c parser) and store the goal's Java
    example class. (artifact or None, report); never raises for an
    uncompilable goal - the report says why."""
    from kriya.workflow.acceptance_jvm import parse_java_acceptance

    compilation = recognize_java_examples(goal, requirement_set, candidate_root)
    report: Dict[str, Any] = {**compilation.to_dict(), "artifact": None, "refusal": None}
    source_text = compile_java_example_class(compilation)
    if source_text is None:
        return None, report
    source = source_text.encode("utf-8")
    try:
        cases, java = parse_java_acceptance(source)
    except AcceptanceError as error:  # a compiler defect, never silent
        report["refusal"] = {"reason_code": error.reason_code, "message": str(error)}
        return None, report
    digest = hashlib.sha256(source).hexdigest()
    store = acceptance_store(state_root)
    os.makedirs(store, exist_ok=True)
    stored = os.path.join(store, f"{digest}.java")
    if not os.path.isfile(stored):
        temporary = f"{stored}.{uuid.uuid4().hex}.tmp"
        with open(temporary, "wb") as handle:
            handle.write(source)
        os.replace(temporary, stored)
    artifact = AcceptanceArtifact(digest=digest, stored_path=stored, source_name=JAVA_EXAMPLE_SOURCE_NAME,
                                  requirement_set_digest=requirement_set.digest, cases=cases, imports=(),
                                  language="java", java=java)
    report["artifact"] = {"digest": digest, "stored_path": stored, "injection_path": java["injection_path"],
                          "cases": {rid: artifact.identities_for(rid) for rid in artifact.requirement_ids}}
    return artifact, report


def java_examples_of(goal: str) -> Sequence[str]:
    """The arrow lines of ``goal`` that look like Java static calls (diagnostic only)."""
    return [line.strip() for line in goal.splitlines() if _ARROW.match(line) and _CALL.match(_ARROW.match(line).group("expr"))]
