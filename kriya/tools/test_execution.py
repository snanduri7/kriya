"""FS-1A: structured test-execution evidence (TestExecutionReport).

A test gate's console output says how many tests ran; it does not say WHICH.
Which test identities a gate invocation executed, and with what status, comes
from the runner's own structured JUnit-XML report produced by that exact
invocation - never inferred from console text:

- pytest: ``--junitxml=<path>`` at a Kriya-owned, per-invocation path
  (``.kriya/test-reports/<gate_id>.xml``, workspace-relative so the same
  argument resolves inside a container whose workdir is the mounted workspace;
  ``.kriya/**`` is a trusted control path no candidate write can reach);
- Maven Surefire / Gradle: the ``TEST-*.xml`` reports the build already
  writes under each module's ``target/surefire-reports`` /
  ``build/test-results``. Kriya deletes every such report before the run, so
  whatever is there afterwards was produced by this invocation.

Freshness binding: a unique destination (pytest) or a cleared destination
(JVM) per invocation; only reports this invocation produced are read; the
report files are digested into the evidence, and Kriya removes its own
pytest report after reading it. Anything missing, malformed, timed out or
from an unsupported runner is INDETERMINATE - never positive evidence.

Residual (disclosed): the candidate's own test code runs inside the gate and
could in principle write a report file itself; pytest writes its report at
session end (after every test), and Surefire writes per class after the
class completes, so a forgery needs hook-level cooperation. The exit code and
the console summary stay authoritative for pass/fail exactly as before; this
report only adds which identities ran.
"""
from __future__ import annotations

import glob
import hashlib
import logging
import os
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

TEST_EXECUTION_REPORT_VERSION = 1

COMPLETE = "COMPLETE"
INDETERMINATE = "INDETERMINATE"

PASSED = "passed"
FAILED = "failed"
ERROR = "error"
SKIPPED = "skipped"

# Kriya-owned, under the trusted control path (candidate writes are denied there).
PYTEST_REPORT_DIR = os.path.join(".kriya", "test-reports")

# pytest exit codes that mean the session ran to completion: tests passed (0),
# some failed (1), none collected (5). Interrupted (2), internal error (3) and
# usage error (4) still write a report, but not one of a completed run.
_PYTEST_COMPLETE_EXIT_CODES = frozenset({0, 1, 5})


@dataclass(frozen=True)
class TestCaseResult:
    """One executed (or reported) test identity. ``identity`` is the runner's
    ``classname.name`` with a parameterization suffix (``[...]``/``(...)``)
    removed - the same qualified name the structural parser gives the
    method/function (``code_intel`` ``lookup_key``); ``name`` keeps the
    runner's exact case name."""

    __test__ = False  # not a pytest test class

    identity: str
    classname: str
    name: str
    status: str


@dataclass
class TestExecutionReport:
    """What one test-gate invocation executed, per identity."""

    __test__ = False  # not a pytest test class

    gate_id: str
    runner: str
    workspace: str
    completeness: str = INDETERMINATE
    reason: Optional[str] = None
    cases: List[TestCaseResult] = field(default_factory=list)
    report_files: List[Dict[str, str]] = field(default_factory=list)
    cleared_reports: int = 0
    version: int = TEST_EXECUTION_REPORT_VERSION
    # REG-R1 (pytest only, never part of summary()/to_dict()): each case's
    # failure evidence and the report's own integrity counts
    # (parse_pytest_case_evidence), plus the raw bytes this invocation
    # produced - the runner's stdout/stderr and the JUnit report - for the
    # per-test regression authority and its retained evidence.
    case_evidence: Optional[Dict[str, Any]] = None
    raw_stdout: Optional[str] = None
    raw_stderr: Optional[str] = None
    raw_report: Optional[str] = None

    @property
    def complete(self) -> bool:
        return self.completeness == COMPLETE

    def statuses(self) -> Dict[str, List[str]]:
        """identity -> every status it was reported with (a parameterized
        test reports one case per parameter set)."""
        found: Dict[str, List[str]] = {}
        for case in self.cases:
            found.setdefault(case.identity, []).append(case.status)
        return found

    def passed(self, identity: str) -> bool:
        """Executed, and every reported case of it passed."""
        statuses = self.statuses().get(identity)
        return bool(statuses) and all(status == PASSED for status in statuses)

    def summary(self) -> Dict[str, Any]:
        counts: Dict[str, int] = {}
        for case in self.cases:
            counts[case.status] = counts.get(case.status, 0) + 1
        return {"version": self.version, "gate_id": self.gate_id, "runner": self.runner,
                "completeness": self.completeness, "reason": self.reason, "cases": len(self.cases),
                "status_counts": counts, "report_files": list(self.report_files),
                "cleared_reports": self.cleared_reports}

    def to_dict(self) -> Dict[str, Any]:
        data = self.summary()
        data["workspace"] = self.workspace
        data["test_cases"] = [{"identity": c.identity, "classname": c.classname, "name": c.name,
                               "status": c.status} for c in self.cases]
        return data

    def pytest_evidence(self) -> Optional[Dict[str, Any]]:
        """REG-R1: what a pytest invocation reports per test, for the
        per-test regression authority (kriya/workflow/validation_baseline.py
        decides from it; nothing here judges). None for any other runner.
        ``complete`` is this report's own completeness; the per-case
        evidence and its integrity counts are None when the report could
        not be read."""
        if self.runner != "pytest":
            return None
        return {"version": PYTEST_CASE_EVIDENCE_VERSION, "complete": self.complete, "reason": self.reason,
                "report_files": list(self.report_files), "evidence": self.case_evidence,
                "raw": {"stdout": self.raw_stdout, "stderr": self.raw_stderr, "junit": self.raw_report}}


@dataclass
class ReportBinding:
    """The destination one invocation is bound to, decided before it runs."""

    gate_id: str
    runner: str
    workspace: str
    pytest_report: Optional[str] = None      # workspace-relative path
    jvm_report_dirs: Tuple[str, ...] = ()    # absolute directories
    cleared_reports: int = 0
    unsupported: Optional[str] = None
    # The test process this invocation ran (observe()): its exit code and
    # whether it timed out. Not observed = no test process ran.
    observed: bool = False
    exit_code: Optional[int] = None
    timed_out: bool = False
    stdout: Optional[str] = None
    stderr: Optional[str] = None

    def observe(self, process_result: Optional[Dict[str, Any]]) -> None:
        """Record the runner process's own result (the adapter's command
        result, before it is reduced to a gate verdict)."""
        if not isinstance(process_result, dict):
            return
        self.observed = True
        self.exit_code = process_result.get("returncode", process_result.get("exit_code"))
        self.timed_out = bool(process_result.get("timed_out"))
        # REG-R1: the raw streams, kept apart (the gate output joins them).
        stdout, stderr = process_result.get("stdout"), process_result.get("stderr")
        self.stdout = stdout if isinstance(stdout, str) else None
        self.stderr = stderr if isinstance(stderr, str) else None

    @property
    def pytest_argument(self) -> Optional[str]:
        return f"--junitxml={self.pytest_report}" if self.pytest_report else None


def _identity(classname: str, name: str) -> str:
    """``classname.name`` without a parameterization suffix; a JVM nested
    class's ``$`` becomes ``.`` (the parser's qualified name)."""
    base = name
    for marker in ("[", "("):
        index = base.find(marker)
        if index > 0:
            base = base[:index]
    owner = classname.replace("$", ".")
    return f"{owner}.{base}" if owner else base


def _status(testcase: ET.Element) -> str:
    for child in testcase:
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "failure":
            return FAILED
        if tag == "error":
            return ERROR
        if tag == "skipped":
            return SKIPPED
    return PASSED


def parse_junit_xml(data: bytes) -> List[TestCaseResult]:
    """Every ``<testcase>`` of one JUnit XML document (``<testsuite>`` or
    ``<testsuites>`` root). Raises ``ET.ParseError`` on malformed XML."""
    root = ET.fromstring(data)
    cases = []
    for testcase in root.iter():
        if testcase.tag.rsplit("}", 1)[-1] != "testcase":
            continue
        classname = testcase.get("classname") or ""
        name = testcase.get("name") or ""
        cases.append(TestCaseResult(identity=_identity(classname, name), classname=classname, name=name,
                                    status=_status(testcase)))
    return cases


PYTEST_CASE_EVIDENCE_VERSION = 1
_FAILURE_TAGS = ("failure", "error")
# pytest ends every failure body (str(report.longrepr)) with the crash
# location line "<path>:<line>: <ExceptionType>" (ReprFileLocation).
_CRASH_LOCATION_RE = re.compile(r"^.+:\d+: ([A-Za-z_][\w.]*)$")
_MESSAGE_TYPE_RE = re.compile(r"^([A-Za-z_][\w.]*)(?::|$)")


def _failure_type(message: str, body: str) -> Optional[str]:
    """The exception type pytest reports for a failure: its crash location
    line (the last line of the body), else the ``Type:`` prefix of the
    message, else None (never guessed)."""
    lines = [line for line in body.splitlines() if line.strip()]
    if lines:
        match = _CRASH_LOCATION_RE.match(lines[-1].strip())
        if match:
            return match.group(1)
    first = message.splitlines()[0] if message else ""
    match = _MESSAGE_TYPE_RE.match(first)
    return match.group(1) if match else None


def _declared_count(suite: ET.Element, name: str) -> Optional[int]:
    try:
        return int(suite.get(name))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def parse_pytest_case_evidence(data: bytes) -> Dict[str, Any]:
    """REG-R1: every pytest ``<testcase>`` of one JUnit document, keyed
    ``classname::name`` (pytest's own address, parameters included), with
    its outcome and - for a failure or error - the exception type, the
    reported message and the failure body (``longrepr``), verbatim. Several
    failure/error children of one case (a failing call and a failing
    teardown) are kept in document order.

    ``integrity`` says whether the document can be trusted as the complete
    per-test record: every ``<testsuite>`` declares its tests/failures/
    errors/skipped counts, the parsed elements match them exactly and no
    case key repeats. Raises ``ET.ParseError`` on malformed XML."""
    root = ET.fromstring(data)
    cases: Dict[str, Dict[str, Any]] = {}
    duplicates: List[str] = []
    parsed = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    declared = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    undeclared = False
    suites = 0
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "testsuite":
            suites += 1
            for name in declared:
                count = _declared_count(element, name)
                if count is None:
                    undeclared = True
                else:
                    declared[name] += count
            continue
        if tag != "testcase":
            continue
        parsed["tests"] += 1
        key = f"{element.get('classname') or ''}::{element.get('name') or ''}"
        children = [(child.tag.rsplit("}", 1)[-1], child) for child in element]
        failures = [(child_tag, child) for child_tag, child in children if child_tag in _FAILURE_TAGS]
        parsed["failures"] += sum(1 for child_tag, _ in failures if child_tag == "failure")
        parsed["errors"] += sum(1 for child_tag, _ in failures if child_tag == "error")
        parsed["skipped"] += sum(1 for child_tag, _ in children if child_tag == "skipped")
        message = "\n".join(child.get("message") or "" for _, child in failures)
        body = "\n".join(child.text or "" for _, child in failures)
        if key in cases:
            duplicates.append(key)
        cases[key] = {"outcome": _status(element),
                      "failure_type": _failure_type(failures[0][1].get("message") or "",
                                                    failures[0][1].text or "") if failures else None,
                      "message": message if failures else None, "body": body if failures else None}
    reason = None
    if suites == 0 or undeclared:
        reason = "JUNIT_COUNTS_UNDECLARED"
    elif duplicates:
        reason = "JUNIT_DUPLICATE_CASE"
    elif parsed != declared:
        reason = "JUNIT_COUNTS_INCONSISTENT"
    return {"cases": cases,
            "integrity": {"ok": reason is None, "reason": reason, "declared": declared, "parsed": parsed,
                          "duplicates": sorted(set(duplicates))}}


def _jvm_report_dirs(workspace: str, build_system: str) -> List[str]:
    from kriya.tools.validate import _project_dirs

    if build_system == "maven":
        return [os.path.join(d, "target", "surefire-reports")
                for d in _project_dirs(workspace, lambda files: "pom.xml" in files)]
    return [os.path.join(d, "build", "test-results")
            for d in _project_dirs(workspace, lambda files: "build.gradle" in files or "build.gradle.kts" in files)]


def _jvm_reports(directory: str) -> List[str]:
    return sorted(glob.glob(os.path.join(directory, "**", "TEST-*.xml"), recursive=True))


def prepare(workspace: str, runner: str) -> ReportBinding:
    """Bind a fresh report destination to the invocation about to run.
    ``runner``: ``pytest``, ``maven``, ``gradle``; anything else has no
    structured report (INDETERMINATE)."""
    binding = ReportBinding(gate_id=uuid.uuid4().hex, runner=runner, workspace=workspace)
    if runner == "pytest":
        binding.pytest_report = os.path.join(PYTEST_REPORT_DIR, f"{binding.gate_id}.xml")
        os.makedirs(os.path.join(workspace, PYTEST_REPORT_DIR), exist_ok=True)
    elif runner in ("maven", "gradle"):
        binding.jvm_report_dirs = tuple(_jvm_report_dirs(workspace, runner))
        try:
            for directory in binding.jvm_report_dirs:
                for stale in _jvm_reports(directory):
                    os.remove(stale)
                    binding.cleared_reports += 1
        except OSError as error:  # a stale report that cannot be cleared is never read as fresh
            binding.unsupported = f"STALE_REPORT_NOT_CLEARED:{type(error).__name__}"
    else:
        binding.unsupported = f"NO_STRUCTURED_REPORT_FOR_RUNNER:{runner}"
    return binding


def _digest(path: str, data: bytes, workspace: str) -> Dict[str, str]:
    return {"path": os.path.relpath(path, workspace), "sha256": hashlib.sha256(data).hexdigest()}


def collect(binding: ReportBinding) -> TestExecutionReport:
    """Read only the reports this invocation produced; decide completeness."""
    report = TestExecutionReport(gate_id=binding.gate_id, runner=binding.runner, workspace=binding.workspace,
                                 cleared_reports=binding.cleared_reports)
    if binding.runner == "pytest":
        report.raw_stdout, report.raw_stderr = binding.stdout, binding.stderr
    if binding.unsupported:
        report.reason = binding.unsupported
        return report
    if not binding.observed:
        report.reason = "TEST_PROCESS_NOT_RUN"
        _discard_pytest_report(binding)
        return report
    if binding.timed_out:
        report.reason = "GATE_TIMED_OUT"
    if binding.runner == "pytest":
        paths = [os.path.join(binding.workspace, binding.pytest_report)]
        if binding.exit_code not in _PYTEST_COMPLETE_EXIT_CODES and report.reason is None:
            report.reason = f"PYTEST_SESSION_INCOMPLETE:exit_{binding.exit_code}"
    else:
        paths = [path for directory in binding.jvm_report_dirs for path in _jvm_reports(directory)]
    present = [path for path in paths if os.path.isfile(path)]
    if not present:
        report.reason = report.reason or "STRUCTURED_REPORT_MISSING"
        return report
    try:
        for path in present:
            with open(path, "rb") as handle:
                data = handle.read()
            report.report_files.append(_digest(path, data, binding.workspace))
            report.cases.extend(parse_junit_xml(data))
            if binding.runner == "pytest":  # one report per pytest invocation
                report.raw_report = data.decode("utf-8", "replace")
                report.case_evidence = parse_pytest_case_evidence(data)
    except (OSError, ET.ParseError) as error:
        report.reason = report.reason or f"STRUCTURED_REPORT_UNREADABLE:{type(error).__name__}"
        report.cases = []
        report.case_evidence = None
        return report
    finally:
        _discard_pytest_report(binding)
    if report.reason is None:
        report.completeness = COMPLETE
    return report


def _discard_pytest_report(binding: ReportBinding) -> None:
    """Kriya's own pytest report is removed once read (the evidence keeps its
    digest and cases); a leftover could only ever be another gate's stale file."""
    if not binding.pytest_report:
        return
    try:
        os.remove(os.path.join(binding.workspace, binding.pytest_report))
    except FileNotFoundError:
        pass


def report_from_result(result: Any) -> Optional[TestExecutionReport]:
    """The TestExecutionReport a test-gate result carries (``run_tests``
    attaches it as ``test_execution``), or None when it carries none."""
    data = result.get("test_execution") if isinstance(result, dict) else None
    if not isinstance(data, dict):
        return None
    return TestExecutionReport(
        gate_id=str(data.get("gate_id") or ""), runner=str(data.get("runner") or ""),
        workspace=str(data.get("workspace") or ""), completeness=str(data.get("completeness") or INDETERMINATE),
        reason=data.get("reason"),
        cases=[TestCaseResult(identity=c["identity"], classname=c["classname"], name=c["name"], status=c["status"])
               for c in data.get("test_cases") or ()],
        report_files=list(data.get("report_files") or ()), cleared_reports=int(data.get("cleared_reports") or 0),
        version=int(data.get("version") or 0))
