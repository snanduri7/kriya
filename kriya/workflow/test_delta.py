"""FS-1A test-execution integrity: did the intended test delta execute?

A subtask explicitly responsible for adding tests (``subtask_owns_test_obligation``)
that changes test sources must show, from deterministic evidence, that its
test delta produced executable test identities that executed and passed. Four
facts are kept separate - written, discovered, executed, passed:

- written: the structural delta of each changed test file against its
  baseline (``code_intel`` parse of both versions): callables added, and
  callables whose declaration text changed;
- discovered/executed/passed: the runner-native inventory of the exact gate
  invocation that accepted the candidate (kriya/tools/test_execution.py).

Decision (never from method naming, never from an aggregate count change):

- a delta callable that declares itself a test structurally (a JUnit test
  annotation) must have executed and passed;
- a delta callable the runner reported must have passed (skipped = not
  executed; failed/error = not passed);
- at least one delta callable must have executed and passed - so a helper
  added beside a real test is fine (it need not run on its own), but a delta
  of which nothing ran (a missing @Test; a pytest function the runner never
  collects) is TEST_NOT_EXECUTED, naming the identities;
- evidence that is missing, incomplete or for a file the structural parser
  cannot read is INDETERMINATE - never positive.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence

from kriya.code_intel.model import ParseState
from kriya.code_intel.parsing import is_structural_path, parse_text
from kriya.tools.test_execution import SKIPPED, TestExecutionReport

TEST_DELTA_EXECUTED = "TEST_DELTA_EXECUTED"
TEST_NOT_EXECUTED = "TEST_NOT_EXECUTED"
TEST_NOT_PASSED = "TEST_NOT_PASSED"
TEST_EXECUTION_EVIDENCE_INDETERMINATE = "TEST_EXECUTION_EVIDENCE_INDETERMINATE"

# JUnit 4/5 annotations that make a method a test the platform executes.
JUNIT_TEST_ANNOTATIONS = frozenset({"Test", "ParameterizedTest", "RepeatedTest", "TestFactory", "TestTemplate"})

_DELTA_KINDS = frozenset({"method", "function"})
_PARSED = (ParseState.PARSED, ParseState.PARTIALLY_PARSED)


@dataclass(frozen=True)
class DeltaCallable:
    path: str
    identity: str           # the qualified name the runner reports (lookup_key)
    change: str             # "added" | "modified"
    declared_test: bool     # structurally declared a test (JUnit annotation)


@dataclass
class TestDeltaVerdict:
    __test__ = False  # not a pytest test class

    reason_code: str
    delta: List[DeltaCallable] = field(default_factory=list)
    executed: List[str] = field(default_factory=list)
    not_executed: List[str] = field(default_factory=list)
    not_passed: List[str] = field(default_factory=list)
    detail: str = ""

    @property
    def satisfied(self) -> bool:
        return self.reason_code == TEST_DELTA_EXECUTED

    def to_dict(self) -> Dict[str, object]:
        return {"reason_code": self.reason_code, "detail": self.detail, "executed": list(self.executed),
                "not_executed": list(self.not_executed), "not_passed": list(self.not_passed),
                "delta": [{"path": c.path, "identity": c.identity, "change": c.change,
                           "declared_test": c.declared_test} for c in self.delta]}


def _declares_test(annotations: Sequence[str]) -> bool:
    return any(annotation.rsplit(".", 1)[-1].split("(", 1)[0] in JUNIT_TEST_ANNOTATIONS
               for annotation in annotations)


def _callables(path: str, text: str) -> Optional[Dict[str, tuple]]:
    """symbol_id -> (identity, declaration text, declared_test) for every
    method/function directly in a module or a type; None when unparseable."""
    structure = parse_text(path, text)
    if structure.state not in _PARSED:
        return None
    data = text.encode("utf-8")
    kinds = {symbol.symbol_id: symbol.kind for symbol in structure.symbols}
    found = {}
    for symbol in structure.symbols:
        if symbol.kind not in _DELTA_KINDS:
            continue
        if symbol.parent_id is not None and kinds.get(symbol.parent_id) in _DELTA_KINDS:
            continue  # a function nested in a function is no runner identity
        span = symbol.declaration
        found[symbol.symbol_id] = (symbol.lookup_key, data[span.start_byte:span.end_byte],
                                   _declares_test(symbol.annotations))
    return found


def test_source_delta(path: str, baseline: Optional[str], candidate: str) -> Optional[List[DeltaCallable]]:
    """The callables ``candidate`` adds or changes relative to ``baseline``
    (None: a new file). None when either version cannot be parsed
    structurally (an unsupported language included)."""
    if not is_structural_path(path):
        return None
    after = _callables(path, candidate)
    before = _callables(path, baseline) if baseline is not None else {}
    if after is None or before is None:
        return None
    delta = []
    for symbol_id, (identity, text, declared) in sorted(after.items(), key=lambda item: item[1][0]):
        prior = before.get(symbol_id)
        if prior is None:
            delta.append(DeltaCallable(path, identity, "added", declared))
        elif prior[1] != text:
            delta.append(DeltaCallable(path, identity, "modified", declared))
    return delta


test_source_delta.__test__ = False  # not a pytest test function


def judge_test_delta(
    changed_tests: Mapping[str, tuple], report: Optional[TestExecutionReport],
) -> TestDeltaVerdict:
    """``changed_tests``: path -> (baseline text or None, candidate text) of
    every test source the candidate changed; ``report``: the accepted gate
    invocation's TestExecutionReport."""
    if report is None or not report.complete:
        reason = "no structured report" if report is None else f"report {report.reason}"
        return TestDeltaVerdict(TEST_EXECUTION_EVIDENCE_INDETERMINATE,
                                detail=f"test execution evidence is indeterminate: {reason}")
    delta: List[DeltaCallable] = []
    for path in sorted(changed_tests):
        baseline, candidate = changed_tests[path]
        file_delta = None if candidate is None else test_source_delta(path, baseline, candidate)
        if file_delta is None:
            return TestDeltaVerdict(TEST_EXECUTION_EVIDENCE_INDETERMINATE,
                                    detail=f"the test delta of {path} cannot be read structurally")
        delta.extend(file_delta)
    statuses = report.statuses()
    verdict = TestDeltaVerdict(TEST_NOT_EXECUTED, delta=delta)
    for item in delta:
        reported = statuses.get(item.identity)
        if reported and report.passed(item.identity):
            verdict.executed.append(item.identity)
        elif reported and not all(status == SKIPPED for status in reported):
            verdict.not_passed.append(item.identity)
        elif reported or item.declared_test:
            verdict.not_executed.append(item.identity)  # skipped, or a declared test the runner never ran
    if verdict.not_passed:
        verdict.reason_code = TEST_NOT_PASSED
        verdict.detail = "changed tests that did not pass: " + ", ".join(verdict.not_passed)
    elif verdict.not_executed:
        verdict.detail = "changed tests that were not executed: " + ", ".join(verdict.not_executed)
    elif not verdict.executed:
        names = ", ".join(item.identity for item in delta) or "none (no test method or function changed)"
        verdict.detail = ("none of the changed test code executed as a test; the runner reported none of: "
                          + names)
    else:
        verdict.reason_code = TEST_DELTA_EXECUTED
        verdict.detail = "executed and passed: " + ", ".join(verdict.executed)
    return verdict
