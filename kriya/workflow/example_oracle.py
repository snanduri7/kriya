"""VERIFICATION-CONTRACT-003: the goal's own example lines, compiled into a
sealed B2-a acceptance module (closer ``derived_examples``).

``USER TEXT`` is authority; ``MODEL INTERPRETATION`` is not. So the only
examples compiled here are the deliberately constrained, machine-readable
forms a user writes beside a value, found in the RAW goal text (never in a
model's paraphrase), and the transformation is a deterministic, version-
pinned function of those lines (``EXAMPLE_COMPILER_VERSION``):

- a doctest block: ``>>> expression`` lines (with ``...`` continuations) and
  their expected output, verified by Python's own ``doctest`` runner with no
  option flags (no ELLIPSIS, no whitespace normalization: the user's bytes);
- ``expression -> literal``: a Python call expression and a Python literal
  (``ast.literal_eval``), compared with ``==``;
- ``expression -> raises ExceptionType``: the call must raise that exception
  (a dotted path, a dotted path the goal states elsewhere for a bare name, or
  a builtin exception).

Anything else is rejected with a reason (a signature line, prose, a Java
snippet, an expression with no candidate module to import): nothing is
guessed. Each example binds to the one derived statement whose text contains
it, so the acceptance case names that requirement; B2-COV then applies
unchanged - finite examples close only an EXACT statement, a GENERAL one keeps
them as supporting evidence. The module is validated by the B2-a parser, its
imports must resolve to a flat-layout candidate package or module at the
project root (``candidate_modules``), it is stored content-addressed in the
acceptance store before any model call and runs under the B2-a boundary
(Kriya's runner, no conftest, no plugins). Its provenance (compiler version,
forms, source lines) is recorded on the artifact's authority entry; its text
is the goal's, so its visibility is ``goal_text``.
"""
from __future__ import annotations

import ast
import builtins
import doctest
import hashlib
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from kriya.workflow.acceptance_oracle import (
    ACCEPTANCE_MARKER,
    AcceptanceArtifact,
    AcceptanceError,
    acceptance_store,
    candidate_modules,
    parse_acceptance,
)
from kriya.workflow.contract_compilation import (
    AUTHORITY_GOAL_EXAMPLES,
    VISIBILITY_GOAL_TEXT,
    ExternalAuthority,
)
from kriya.workflow.requirements import BEHAVIOR, BEHAVIOR_EXACT, RequirementSet, _clean

EXAMPLE_COMPILER_VERSION = 1
EXAMPLE_SOURCE_NAME = "goal-examples.py"
FORM_DOCTEST = "doctest"
FORM_ARROW_LITERAL = "arrow_literal"
FORM_ARROW_RAISES = "arrow_raises"

_ARROW = re.compile(r"^\s*(?P<expr>\S.*?)\s*->\s*(?P<expected>\S.*?)\s*$")
_RAISES = re.compile(r"^raises\s+(?P<name>[A-Za-z_][\w.]*)\s*$", re.IGNORECASE)
_DOCTEST_LINE = re.compile(r"^\s*>>>\s")
_CONTINUATION = re.compile(r"^\s*\.\.\.(\s|$)")
_IMPORT_IN_DOCTEST = re.compile(r"^\s*>>>\s*(?:from\s+([A-Za-z_][\w.]*)\s+import\b|import\s+([A-Za-z_][\w.]*))")


@dataclass(frozen=True)
class GoalExample:
    form: str
    requirement_id: str
    lines: Tuple[int, ...]  # 1-based line numbers in the goal
    source: str  # the raw text the example was compiled from
    expression: Optional[str] = None
    expected_literal: Optional[str] = None  # repr of the literal
    exception: Optional[str] = None  # dotted path or builtin name
    modules: Tuple[str, ...] = ()  # candidate root modules the example imports
    import_paths: Tuple[str, ...] = ()  # dotted modules to import beside the roots (an exception's module)

    def to_dict(self) -> Dict[str, Any]:
        return {"form": self.form, "requirement_id": self.requirement_id, "lines": list(self.lines),
                "source": self.source, "expression": self.expression, "expected": self.expected_literal,
                "exception": self.exception, "modules": list(self.modules)}


@dataclass
class ExampleCompilation:
    examples: List[GoalExample] = field(default_factory=list)
    rejected: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def modules(self) -> List[str]:
        return sorted({module for example in self.examples for module in example.modules})

    @property
    def import_paths(self) -> List[str]:
        return sorted({path for example in self.examples for path in example.import_paths} - set(self.modules))

    def to_dict(self) -> Dict[str, Any]:
        return {"compiler_version": EXAMPLE_COMPILER_VERSION, "examples": [e.to_dict() for e in self.examples],
                "rejected": list(self.rejected), "modules": self.modules}


# ------------------------------------------------------------ recognition

def _binding_requirement(text: str, requirement_set: RequirementSet) -> Optional[str]:
    cleaned = _clean(text)
    if not cleaned:
        return None
    for requirement in requirement_set.requirements:
        if cleaned in requirement.text:
            return requirement.id
    return None


def _call_roots(tree: ast.AST) -> Tuple[List[str], bool]:
    """(root names of dotted calls, whether any call exists)."""
    roots: List[str] = []
    has_call = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            has_call = True
            target = node.func
            while isinstance(target, ast.Attribute):
                target = target.value
            if isinstance(target, ast.Name) and isinstance(node.func, ast.Attribute):
                roots.append(target.id)
    return sorted(set(roots)), has_call


def _resolve_exception(name: str, goal: str) -> Tuple[Optional[str], Optional[str]]:
    """(dotted exception path or builtin name, root module to import)."""
    if "." in name:
        return name, name.split(".")[0]
    stated = re.search(rf"\b((?:[A-Za-z_]\w*\.)+){re.escape(name)}\b", goal)
    if stated:
        dotted = stated.group(1) + name
        return dotted, dotted.split(".")[0]
    candidate = getattr(builtins, name, None)
    if isinstance(candidate, type) and issubclass(candidate, BaseException):
        return name, None
    return None, None


def _recognize_arrow(line_no: int, line: str, goal: str, requirement_set: RequirementSet,
                     compilation: ExampleCompilation) -> bool:
    match = _ARROW.match(line)
    if match is None:
        return False
    expression, expected = match.group("expr"), match.group("expected")
    rid = _binding_requirement(line, requirement_set)

    def reject(why: str) -> bool:
        compilation.rejected.append({"line": line_no, "text": line.strip(), "why": why})
        return True

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return reject("the left side is not a Python expression")
    roots, has_call = _call_roots(tree)
    if not has_call:
        return reject("the left side calls nothing")
    if rid is None:
        return reject("the line is not part of any derived statement")
    raises = _RAISES.match(expected)
    if raises:
        exception, module = _resolve_exception(raises.group("name"), goal)
        if exception is None:
            return reject(f"exception {raises.group('name')!r} is neither dotted, stated elsewhere in the goal "
                          "nor a builtin")
        modules = tuple(sorted(set(roots) | ({module} if module else set())))
        if not modules:
            return reject("no candidate module to import (bare function call)")
        exception_module = exception.rsplit(".", 1)[0] if "." in exception else None
        compilation.examples.append(GoalExample(FORM_ARROW_RAISES, rid, (line_no,), line.strip(), expression,
                                                exception=exception, modules=modules,
                                                import_paths=(exception_module,) if exception_module else ()))
        return True
    try:
        literal = ast.literal_eval(expected)
    except (ValueError, SyntaxError):
        return reject("the right side is not a Python literal")
    if not roots:
        return reject("no candidate module to import (bare function call)")
    compilation.examples.append(GoalExample(FORM_ARROW_LITERAL, rid, (line_no,), line.strip(), expression,
                                            expected_literal=repr(literal), modules=tuple(roots)))
    return True


def _recognize_doctest(start: int, lines: Sequence[str], requirement_set: RequirementSet,
                       compilation: ExampleCompilation) -> int:
    """Consume the doctest block starting at index ``start``; return the next index."""
    end = start
    while end < len(lines) and lines[end].strip():
        end += 1
    block = lines[start:end]
    # Normalise the common indentation so the doctest parser sees a plain session.
    indent = min((len(line) - len(line.lstrip()) for line in block if line.strip()), default=0)
    text = "\n".join(line[indent:] for line in block) + "\n"
    first_prompt = next(index for index, line in enumerate(block) if _DOCTEST_LINE.match(line))
    rid = _binding_requirement(block[first_prompt], requirement_set)
    try:
        examples = doctest.DocTestParser().get_examples(text)
    except ValueError as error:
        compilation.rejected.append({"line": start + 1, "text": block[0].strip(), "why": f"unreadable doctest: {error}"})
        return end
    modules = sorted({m.group(1) or m.group(2) for m in (_IMPORT_IN_DOCTEST.match(line) for line in block) if m})
    modules = sorted({module.split(".")[0] for module in modules})
    if not any(example.want for example in examples):
        compilation.rejected.append({"line": start + 1, "text": block[0].strip(),
                                     "why": "the doctest states no expected output"})
    elif rid is None:
        compilation.rejected.append({"line": start + 1, "text": block[0].strip(),
                                     "why": "the block is not part of any derived statement"})
    elif not modules:
        compilation.rejected.append({"line": start + 1, "text": block[0].strip(),
                                     "why": "the doctest imports no candidate module"})
    else:
        compilation.examples.append(GoalExample(FORM_DOCTEST, rid, tuple(range(start + 1, end + 1)), text,
                                                modules=tuple(modules)))
    return end


def recognize_goal_examples(goal: str, requirement_set: RequirementSet) -> ExampleCompilation:
    """Every machine-readable example of ``goal`` (pure, deterministic), each
    bound to the statement whose text contains it, and every rejected
    candidate line with why."""
    compilation = ExampleCompilation()
    lines = goal.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if _DOCTEST_LINE.match(line):
            index = _recognize_doctest(index, lines, requirement_set, compilation)
            continue
        if _CONTINUATION.match(line):
            index += 1
            continue
        _recognize_arrow(index + 1, line, goal, requirement_set, compilation)
        index += 1
    return compilation


# ------------------------------------------------------------ compilation

def compile_example_module(compilation: ExampleCompilation) -> Optional[str]:
    """The acceptance module's source for the recognized examples (None when
    there is none): one ``test_example_<n>`` per example, marked with its
    requirement; module-level imports of every candidate root module."""
    if not compilation.examples:
        return None
    out: List[str] = [
        '"""Kriya-derived acceptance module (VERIFICATION-CONTRACT-003, compiler version '
        f'{EXAMPLE_COMPILER_VERSION}): the goal\'s own example lines, compiled deterministically; never prose."""',
        "import doctest", "import pytest", "",
    ]
    out += [f"import {module}" for module in compilation.modules]
    out += [f"import {path}" for path in compilation.import_paths]
    out.append("")
    for number, example in enumerate(compilation.examples, start=1):
        out += ["", f'@pytest.mark.{ACCEPTANCE_MARKER}("{example.requirement_id}")',
                f"def test_example_{number}():"]
        if example.form == FORM_ARROW_LITERAL:
            out.append(f"    assert ({example.expression}) == {example.expected_literal}")
        elif example.form == FORM_ARROW_RAISES:
            out += [f"    with pytest.raises({example.exception}):", f"        {example.expression}"]
        else:
            out += [f"    text = {example.source!r}",
                    "    test = doctest.DocTestParser().get_doctest(text, {}, 'goal', None, 0)",
                    "    runner = doctest.DocTestRunner(optionflags=0, verbose=False)",
                    "    runner.run(test, out=lambda _line: None)",
                    "    assert runner.failures == 0, 'a goal example did not hold'"]
    out.append("")
    return "\n".join(out)


def derive_example_artifact(
    goal: str, requirement_set: RequirementSet, *, state_root: str, candidate_root: str,
) -> Tuple[Optional[AcceptanceArtifact], Dict[str, Any]]:
    """Recognize, compile, validate (B2-a parser and layout rules against the
    candidate root) and store the goal's example module. Returns (artifact or
    None, report). Never raises for an uncompilable goal: the report says why."""
    compilation = recognize_goal_examples(goal, requirement_set)
    report: Dict[str, Any] = {**compilation.to_dict(), "artifact": None, "refusal": None}
    source_text = compile_example_module(compilation)
    if source_text is None:
        return None, report
    source = source_text.encode("utf-8")
    try:
        cases, imports = parse_acceptance(source)
    except AcceptanceError as error:  # a compiler defect, never silent
        report["refusal"] = {"reason_code": error.reason_code, "message": str(error)}
        return None, report
    digest = hashlib.sha256(source).hexdigest()
    store = acceptance_store(state_root)
    os.makedirs(store, exist_ok=True)
    stored = os.path.join(store, f"{digest}.py")
    if not os.path.isfile(stored):
        temporary = f"{stored}.{uuid.uuid4().hex}.tmp"
        with open(temporary, "wb") as handle:
            handle.write(source)
        os.replace(temporary, stored)
    artifact = AcceptanceArtifact(digest=digest, stored_path=stored, source_name=EXAMPLE_SOURCE_NAME,
                                  requirement_set_digest=requirement_set.digest, cases=cases, imports=imports)
    try:
        modules = candidate_modules(candidate_root, artifact)
    except AcceptanceError as error:
        report["refusal"] = {"reason_code": error.reason_code, "message": str(error)}
        return None, report
    report["artifact"] = {"digest": digest, "stored_path": stored, "candidate_modules": modules,
                          "cases": {rid: artifact.identities_for(rid) for rid in artifact.requirement_ids}}
    return artifact, report


def example_authority(artifact: AcceptanceArtifact, compilation_report: Dict[str, Any]) -> ExternalAuthority:
    """The contract's view of the derived module: EXACT behaviour coverage of
    every requirement an example binds to (B2-COV: never GENERAL)."""
    coverage = {rid: {"claim": BEHAVIOR, "accepted_strength": BEHAVIOR_EXACT, "cases": artifact.identities_for(rid)}
                for rid in artifact.requirement_ids}
    return ExternalAuthority(AUTHORITY_GOAL_EXAMPLES, artifact.digest, coverage, visibility=VISIBILITY_GOAL_TEXT,
                             provenance={"compiler_version": EXAMPLE_COMPILER_VERSION,
                                         "forms": sorted({e["form"] for e in compilation_report.get("examples", [])}),
                                         "lines": sorted({line for e in compilation_report.get("examples", [])
                                                          for line in e["lines"]}),
                                         "stored_path": artifact.stored_path})


def bound_derived_examples(engine: Any) -> Optional[AcceptanceArtifact]:
    """The goal-example artifact bound to ``engine`` for this run (set once by
    the contract compilation, before any model call), or None."""
    artifact = getattr(engine, "derived_examples", None)
    return artifact if isinstance(artifact, AcceptanceArtifact) else None
