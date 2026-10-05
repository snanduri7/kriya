"""FS-1C0: the named pre-existing test as an independent oracle.

A requirement whose own words name an existing test may be closed by running
that test on the candidate (PRD-020). The test file being unchanged is not
enough: what decides whether it passes also includes its support files, the
runner's configuration and plugins, and which of its cases run at all, and
the test process's exit code and JUnit report are produced by the same
process the candidate's code runs in (measured: a candidate ``conftest.py``
hook forges both). So a named test closes a requirement only when ALL of
these are established deterministically, else the requirement stays
UNVERIFIED (never a fallback to any model judgment):

1. Oracle author: the named test exists at the run's authorized base
   revision, byte-identical (never written by the candidate).
2. Trust surface unchanged: every file the supported runner reads to decide
   the outcome (``oracle_surface``) equals the base revision - before the run
   and again after it (a candidate rewriting one during the run is refused).
3. Expected identities: the exact test cases come from running the named
   tests on an export of the base revision (``baseline_inventory``), never
   from the candidate's run.
4. Same identities, all passed: the candidate's run is a COMPLETE, fresh
   TestExecutionReport (FS-1A) of the same runner that contains every
   expected case, each passed, and the runner itself reported success.
5. Binding: the closure records the base revision, runner, surface digests,
   expected cases and the report's gate id and file digests.

Trust surface (union over the supported runners; a path absent on both sides
is simply equal):

- pytest: the named files; ``conftest.py``, ``__init__.py``, ``pytest.ini``,
  ``.pytest.ini`` in every directory from the repository root to each named
  test; the pytest sections of ``pyproject.toml`` (``[tool.pytest*]``),
  ``setup.cfg`` (``[tool:pytest]``) and ``tox.ini`` (``[pytest]``) in those
  directories; the declared dependencies (installed plugins load from them):
  ``requirements.txt`` and the files it includes, the root ``pyproject.toml``
  ``[project]`` dependency tables, and the packaging files when the
  requirements install the project itself; the test-side import closure of the
  named tests and those conftests (imports and ``pytest_plugins``, resolved
  against every directory, followed only through test-side modules: a
  ``tests``/``test``/``testing`` directory, ``test_*.py``, ``*_test.py``,
  ``conftest.py``); and every non-Python file under each named test's
  top-level test directory (test resources).
- Maven/Gradle (JUnit): every build file (``pom.xml``, ``*.gradle[.kts]``,
  ``settings.gradle[.kts]``, ``gradle.properties``, ``mvnw``, ``gradlew``),
  ``.mvn/``, ``buildSrc/``, ``gradle/``, everything under any ``src/test/``,
  every ``junit-platform.properties`` and ``META-INF/services/`` file. The JVM
  test classpath is scanned (ServiceLoader, JUnit extension autodetection,
  Spring component scanning), so it is not narrowed by import analysis: any
  test-side change refuses the closure.
- Any runner: a candidate write under a build/tool output root
  (``__pycache__``, ``.pytest_cache``; ``target``/``build``/``.gradle`` beside
  a build file) is refused - those are loaded without being source.

Runners other than pytest/Maven/Gradle are outside the boundary (refused).

Not claimed: candidate PRODUCTION code the oracle legitimately executes runs in
the same process and could tamper with the runner in-process. That residual is
adversarial, disclosed, and bounded by containment; everything above is what a
candidate can change WITHOUT running code at import time.
"""
from __future__ import annotations

import ast
import configparser
import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from kriya.core.tomlcompat import tomllib
from kriya.tools import test_execution

NAMED_TEST_ORACLE_VERSION = 1
CLOSURE_METHOD = "named_test_oracle"
SUPPORTED_RUNNERS = ("pytest", "maven", "gradle")

ORACLE_PASSED = "ORACLE_PASSED"
ORACLE_BASE_UNAVAILABLE = "ORACLE_BASE_UNAVAILABLE"
ORACLE_NOT_AT_BASE = "ORACLE_NOT_AT_BASE"
ORACLE_DEPENDENCY_CHANGED = "ORACLE_DEPENDENCY_CHANGED"
ORACLE_RUNNER_UNSUPPORTED = "ORACLE_RUNNER_UNSUPPORTED"
ORACLE_BASELINE_INDETERMINATE = "ORACLE_BASELINE_INDETERMINATE"
ORACLE_EVIDENCE_INDETERMINATE = "ORACLE_EVIDENCE_INDETERMINATE"
ORACLE_IDENTITY_NOT_EXECUTED = "ORACLE_IDENTITY_NOT_EXECUTED"
ORACLE_IDENTITY_NOT_PASSED = "ORACLE_IDENTITY_NOT_PASSED"
ORACLE_RUN_FAILED = "ORACLE_RUN_FAILED"
ORACLE_CHANGED_DURING_RUN = "ORACLE_CHANGED_DURING_RUN"

ABSENT = "absent"
_TEST_DIR_NAMES = frozenset({"tests", "test", "testing"})
_PYTEST_DIR_FILES = ("conftest.py", "__init__.py", "pytest.ini", ".pytest.ini")
_JVM_BUILD_FILES = frozenset({"pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle",
                              "settings.gradle.kts", "gradle.properties", "mvnw", "gradlew",
                              "junit-platform.properties"})
_JVM_TOP_DIRS = frozenset({".mvn", "buildSrc", "gradle"})
_JVM_OUTPUT_DIRS = ("target", "build", ".gradle")
_JVM_MODULE_MARKERS = ("pom.xml", "build.gradle", "build.gradle.kts")
_PYTHON_OUTPUT_DIRS = frozenset({"__pycache__", ".pytest_cache"})
_SKIP_DIRS = frozenset({".git", ".kriya", "__pycache__", "node_modules"})


@dataclass(frozen=True)
class OracleJudgment:
    """One named-test oracle decision. ``closed`` only for ORACLE_PASSED."""

    reason_code: str
    reason: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def closed(self) -> bool:
        return self.reason_code == ORACLE_PASSED

    def as_entry(self) -> Dict[str, Any]:
        return {"closed": self.closed, "reason_code": self.reason_code, "reason": self.reason,
                "evidence": dict(self.evidence)}


# ---------------------------------------------------------------- the authorized base revision

class BaseTree:
    """The tracked content of one git revision, read without checking it out."""

    def __init__(self, repo: str, revision: str) -> None:
        self.repo = repo
        self.revision = revision
        listing = _git(repo, "ls-tree", "-r", "-z", "--full-tree", revision)
        self.modes: Dict[str, str] = {}
        for record in listing.split(b"\0"):
            if not record:
                continue
            meta, path = record.split(b"\t", 1)
            mode, kind, _oid = meta.split(b" ")
            if kind == b"blob":
                self.modes[os.fsdecode(path)] = mode.decode()

    @property
    def paths(self) -> Set[str]:
        return set(self.modes)

    def read_many(self, paths: Iterable[str]) -> Dict[str, Optional[bytes]]:
        """path -> bytes at the revision (None when absent there)."""
        wanted = sorted({path for path in paths if path in self.modes})
        found: Dict[str, Optional[bytes]] = {path: None for path in paths}
        if not wanted:
            return found
        request = b"".join(f"{self.revision}:{path}\n".encode() for path in wanted)
        out = subprocess.run(["git", "cat-file", "--batch"], cwd=self.repo, input=request, capture_output=True,
                             check=True).stdout
        offset = 0
        for path in wanted:
            header_end = out.index(b"\n", offset)
            header = out[offset:header_end].split(b" ")
            if header[-1] == b"missing":
                raise RuntimeError(f"base object missing: {path}")
            size = int(header[2])
            found[path] = out[header_end + 1:header_end + 1 + size]
            offset = header_end + 1 + size + 1
        return found


def _git(repo: str, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True).stdout


def export_base(base: BaseTree, destination: str) -> None:
    """The base revision's tracked tree, written by git/tar into a
    Kriya-owned directory outside the workspace."""
    archive = subprocess.Popen(["git", "archive", "--format=tar", base.revision], cwd=base.repo,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        extract = subprocess.run(["tar", "-x", "-f", "-", "-C", destination], stdin=archive.stdout,
                                 capture_output=True, check=False)
    finally:
        if archive.stdout is not None:
            archive.stdout.close()
        archive_code = archive.wait()
    if archive_code != 0 or extract.returncode != 0:
        raise RuntimeError(f"base export failed (git archive {archive_code}, tar {extract.returncode})")


# ---------------------------------------------------------------- the trust surface

Projection = Optional[Callable[[bytes], Any]]


def _candidate_files(root: str) -> Set[str]:
    """Every file in the candidate tree (no git ignore rules: a candidate can
    write .gitignore), minus git/Kriya control state, caches and virtualenvs."""
    files: Set[str] = set()
    for current, dirs, names in os.walk(root):
        module = any(os.path.exists(os.path.join(current, marker)) for marker in _JVM_MODULE_MARKERS)
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and (d == ".mvn" or not d.startswith("."))
                         and not (module and d in _JVM_OUTPUT_DIRS)
                         and not os.path.exists(os.path.join(current, d, "pyvenv.cfg")))
        rel_dir = os.path.relpath(current, root)
        for name in names:
            files.add(name if rel_dir == "." else f"{rel_dir}/{name}".replace(os.sep, "/"))
    return files


def _ancestor_dirs(path: str) -> List[str]:
    parts = path.split("/")[:-1]
    return [""] + ["/".join(parts[:i + 1]) for i in range(len(parts))]


def _join(directory: str, name: str) -> str:
    return f"{directory}/{name}" if directory else name


def _is_test_side(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    return (any(part in _TEST_DIR_NAMES for part in parts[:-1]) or name == "conftest.py"
            or name.startswith("test_") or name.endswith("_test.py"))


def _ini_section(section: str) -> Callable[[bytes], Any]:
    def project(data: bytes) -> Any:
        parser = configparser.RawConfigParser(strict=False, interpolation=None)
        parser.read_string(data.decode("utf-8"))
        return sorted(parser.items(section)) if parser.has_section(section) else None
    return project


def _pyproject(root: bool) -> Callable[[bytes], Any]:
    def project(data: bytes) -> Any:
        document = tomllib.loads(data.decode("utf-8"))
        tool = document.get("tool") or {}
        view: Dict[str, Any] = {"tool.pytest": tool.get("pytest") if isinstance(tool, dict) else tool}
        if root:
            project_table = document.get("project") or {}
            view["project.dependencies"] = project_table.get("dependencies")
            view["project.optional-dependencies"] = project_table.get("optional-dependencies")
        return view
    return project


def _requirement_includes(data: Optional[bytes]) -> Tuple[List[str], bool]:
    """(files a requirements file includes with -r/-c, whether it installs a
    local path such as the project itself)."""
    includes: List[str] = []
    local = False
    for raw in (data or b"").decode("utf-8", "replace").splitlines():
        line = raw.split("#", 1)[0].strip()
        for flag in ("-r", "--requirement", "-c", "--constraint"):
            if line.startswith(flag + " ") or line.startswith(flag + "="):
                includes.append(line[len(flag) + 1:].strip().lstrip("./"))
        if line.startswith(("-e", "--editable", ".", "/")) or line.startswith("file:"):
            local = True
    return includes, local


def _module_names(data: Optional[bytes], path: str) -> Set[str]:
    """Modules a Python file can import (every prefix of each dotted name,
    relative imports resolved against its package) plus ``pytest_plugins``
    names. Unparseable source imports nothing."""
    try:
        tree = ast.parse(data or b"")
    except (SyntaxError, ValueError):
        return set()
    package = path.split("/")[:-1]
    names: Set[str] = set()

    def add(parts: Sequence[str]) -> None:
        for i in range(1, len(parts) + 1):
            names.add(".".join(parts[:i]))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add(alias.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").split(".") if node.module else []
            if node.level:
                anchor = package[:len(package) - (node.level - 1)] if node.level > 1 else package
                module = list(anchor) + module
            add(module)
            for alias in node.names:
                add(list(module) + [alias.name])
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytest_plugins"
                                                   for t in node.targets):
            values = node.value.elts if isinstance(node.value, (ast.List, ast.Tuple)) else [node.value]
            for value in values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    add(value.value.split("."))
    return names


def _module_index(paths: Iterable[str]) -> Dict[str, List[str]]:
    """dotted-name suffix -> Python files it can denote, under any import root
    (rootdir insertion, ``pythonpath``, src layouts): a deliberate superset."""
    index: Dict[str, List[str]] = {}
    for path in paths:
        if not path.endswith(".py"):
            continue
        parts = path[:-3].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        for i in range(len(parts)):
            index.setdefault(".".join(parts[i:]), []).append(path)
    return index


def _read_candidate(root: str, path: str) -> Optional[bytes]:
    full = os.path.join(root, path)
    if os.path.islink(full):
        return b"link:" + os.fsencode(os.readlink(full))
    if not os.path.isfile(full):
        return None
    with open(full, "rb") as handle:
        return handle.read()


def _side_digest(data: Optional[bytes], projection: Projection, link: bool = False) -> str:
    if data is None:
        return ABSENT
    if link:
        return "link:" + hashlib.sha256(data).hexdigest()
    if projection is None:
        return "file:" + hashlib.sha256(data).hexdigest()
    try:
        view = projection(data)
    except Exception:  # unparseable configuration: compared by its bytes, never ignored
        return "unparseable:" + hashlib.sha256(data).hexdigest()
    return "view:" + hashlib.sha256(json.dumps(view, sort_keys=True, default=str).encode()).hexdigest()


class OracleSurface:
    """The trust surface of one set of named tests, discovered over the base
    revision AND the candidate (an addition that shadows an import or adds a
    conftest counts as much as an edit)."""

    def __init__(self, base: BaseTree, candidate_root: str, named: Sequence[str]) -> None:
        self.base = base
        self.root = candidate_root
        self.named = list(named)
        self.entries: Dict[str, Projection] = {}
        self._base_bytes: Dict[str, Optional[bytes]] = {}
        self.discover()

    def _both(self, paths: Iterable[str]) -> Dict[str, Tuple[Optional[bytes], Optional[bytes]]]:
        paths = list(paths)
        missing = [path for path in paths if path not in self._base_bytes]
        self._base_bytes.update(self.base.read_many(missing))
        return {path: (self._base_bytes[path], _read_candidate(self.root, path)) for path in paths}

    def discover(self) -> None:
        candidate_files = _candidate_files(self.root)
        universe = self.base.paths | candidate_files
        entries: Dict[str, Projection] = {path: None for path in self.named}
        dirs = sorted({d for path in self.named for d in _ancestor_dirs(path)})
        for directory in dirs:
            for name in _PYTEST_DIR_FILES:
                entries[_join(directory, name)] = None
            entries[_join(directory, "pyproject.toml")] = _pyproject(root=directory == "")
            entries[_join(directory, "setup.cfg")] = _ini_section("tool:pytest")
            entries[_join(directory, "tox.ini")] = _ini_section("pytest")
        # Declared dependencies: installed packages register pytest plugins.
        pending, visited = ["requirements.txt"], set()
        while pending:
            path = pending.pop()
            if path in visited:
                continue
            visited.add(path)
            entries[path] = None
            for data in self._both([path])[path]:
                includes, local = _requirement_includes(data)
                pending.extend(includes)
                if local:
                    for packaging in ("setup.py", "setup.cfg", "pyproject.toml"):
                        entries[packaging] = None
        # The test-side import closure of the named tests and their conftests.
        index = _module_index(universe)
        queue = [path for path in entries if path.endswith(".py")]
        seen = set(queue)
        while queue:
            path = queue.pop()
            for data in self._both([path])[path]:
                for module in _module_names(data, path):
                    for target in index.get(module, ()):
                        if _is_test_side(target) and target not in seen:
                            seen.add(target)
                            queue.append(target)
                            for directory in _ancestor_dirs(target):
                                init = _join(directory, "__init__.py")
                                if _is_test_side(init) and init not in seen and init in universe:
                                    seen.add(init)
                                    queue.append(init)
        for path in seen:
            entries.setdefault(path, None)
        # Test resources under each named test's top-level test directory.
        for named in self.named:
            parts = named.split("/")
            top = next((i for i, part in enumerate(parts[:-1]) if part in _TEST_DIR_NAMES), None)
            if top is None:
                continue
            prefix = "/".join(parts[:top + 1]) + "/"
            for path in universe:
                if path.startswith(prefix) and not path.endswith((".py", ".pyc")):
                    entries.setdefault(path, None)
        # JVM: build configuration and the whole scanned test classpath.
        for path in universe:
            parts = path.split("/")
            if (parts[-1] in _JVM_BUILD_FILES or parts[-1].endswith((".gradle", ".gradle.kts"))
                    or parts[0] in _JVM_TOP_DIRS or "/src/test/" in f"/{path}"
                    or "META-INF/services/" in path):
                entries.setdefault(path, None)
        self.entries = entries
        self.output_roots = sorted(
            _join(os.path.dirname(path).replace(os.sep, "/"), out) for path in universe
            if path.rsplit("/", 1)[-1] in _JVM_MODULE_MARKERS for out in _JVM_OUTPUT_DIRS)

    def digests(self) -> Tuple[Dict[str, str], Dict[str, str]]:
        """(base digests, candidate digests) per surface path."""
        base: Dict[str, str] = {}
        candidate: Dict[str, str] = {}
        for path, (base_data, candidate_data) in self._both(self.entries).items():
            projection = self.entries[path]
            base_link = self.base.modes.get(path) == "120000"
            base[path] = _side_digest(base_data, projection, link=base_link)
            cand_link = os.path.islink(os.path.join(self.root, path))
            candidate[path] = _side_digest(candidate_data[len(b"link:"):] if cand_link and candidate_data else
                                           candidate_data, projection, link=cand_link)
        return base, candidate

    def output_root_writes(self, modified: Iterable[str]) -> List[str]:
        """Candidate writes under a build/tool output root."""
        roots = tuple(root.rstrip("/") + "/" for root in self.output_roots)
        return sorted(path for path in modified
                      if path.startswith(roots) or any(part in _PYTHON_OUTPUT_DIRS for part in path.split("/"))
                      or path.endswith(".pyc"))


def changed_surface(base: Mapping[str, str], candidate: Mapping[str, str]) -> List[str]:
    """Surface paths whose candidate state differs from the base revision."""
    return sorted(path for path in set(base) | set(candidate) if base.get(path, ABSENT) != candidate.get(path, ABSENT))


def surface_digest(digests: Mapping[str, str]) -> str:
    return hashlib.sha256(json.dumps(sorted(digests.items())).encode()).hexdigest()


# ---------------------------------------------------------------- identities

def case_key(case: test_execution.TestCaseResult) -> str:
    """One exact executed case (parameterization kept: a candidate cannot
    shrink a parameter set unnoticed)."""
    return f"{case.classname}::{case.name}"


def baseline_inventory(result: Any, runner: str) -> Optional[List[str]]:
    """The exact cases the named tests execute at the base revision (any
    status), or None when that run is not COMPLETE structured evidence."""
    report = test_execution.report_from_result(result)
    if report is None or not report.complete or report.runner != runner or not report.cases:
        return None
    return sorted({case_key(case) for case in report.cases})


def judge_candidate_run(expected: Sequence[str], result: Any, runner: str) -> Tuple[str, str]:
    """The candidate run against the base inventory: a COMPLETE report of the
    same runner, every expected case present and passed, and the runner's own
    success. Neither the report nor the exit code alone is enough."""
    report = test_execution.report_from_result(result)
    if report is None or not report.complete or report.runner != runner:
        reason = "no structured report" if report is None else (report.reason or f"runner {report.runner}")
        return ORACLE_EVIDENCE_INDETERMINATE, f"candidate run evidence not complete: {reason}"
    statuses: Dict[str, List[str]] = {}
    for case in report.cases:
        statuses.setdefault(case_key(case), []).append(case.status)
    missing = [key for key in expected if key not in statuses]
    if missing:
        return ORACLE_IDENTITY_NOT_EXECUTED, "expected case(s) did not execute: " + ", ".join(missing)
    not_passed = [key for key in expected if any(status != test_execution.PASSED for status in statuses[key])]
    if not_passed:
        return ORACLE_IDENTITY_NOT_PASSED, "expected case(s) did not pass: " + ", ".join(not_passed)
    if not (isinstance(result, dict) and result.get("success")):
        return ORACLE_RUN_FAILED, "the runner reported failure"
    return ORACLE_PASSED, ""


# ---------------------------------------------------------------- the oracle

def judge_named_tests(
    named: Sequence[str], *, candidate_root: str, base_revision: Optional[str], modified: Iterable[str],
    candidate_validator: Callable[[], Any], base_validator: Callable[[str], Any],
) -> OracleJudgment:
    """Decide whether ``named`` (test files a requirement names) close it on
    the candidate at ``candidate_root``. ``candidate_validator()`` /
    ``base_validator(root)`` build the PolymorphicValidators for the
    candidate and for an export of the base revision; neither is built
    unless the surface check passes."""
    if not base_revision:
        return OracleJudgment(ORACLE_BASE_UNAVAILABLE, "no authorized base revision")
    try:
        base = BaseTree(candidate_root, base_revision)
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        return OracleJudgment(ORACLE_BASE_UNAVAILABLE, f"base revision unreadable: {type(error).__name__}")
    evidence: Dict[str, Any] = {"version": NAMED_TEST_ORACLE_VERSION, "method": CLOSURE_METHOD,
                                "base_revision": base_revision, "tests": list(named)}
    not_at_base = [path for path in named if path not in base.modes]
    if not_at_base:
        return OracleJudgment(ORACLE_NOT_AT_BASE, "not in the base revision (written by the candidate): "
                              + ", ".join(not_at_base), evidence)
    surface = OracleSurface(base, candidate_root, named)
    base_digests, candidate_digests = surface.digests()
    changed = changed_surface(base_digests, candidate_digests)
    changed_named = [path for path in changed if path in named]
    if changed_named:
        return OracleJudgment(ORACLE_NOT_AT_BASE, "changed by the candidate: " + ", ".join(changed_named), evidence)
    changed += surface.output_root_writes(modified)
    if changed:
        evidence["changed"] = changed
        return OracleJudgment(ORACLE_DEPENDENCY_CHANGED, "oracle dependency changed by the candidate: "
                              + ", ".join(changed), evidence)
    evidence["surface_digest"] = surface_digest(candidate_digests)
    evidence["surface_entries"] = len(candidate_digests)

    validator = candidate_validator()
    runner = validator._test_runner()
    evidence["runner"] = runner
    if runner not in SUPPORTED_RUNNERS:
        return OracleJudgment(ORACLE_RUNNER_UNSUPPORTED, f"runner {runner} is outside the oracle boundary", evidence)
    with tempfile.TemporaryDirectory(prefix="kriya-oracle-base-") as export:
        try:
            export_base(base, export)
            base_check = base_validator(export)
            base_runner = base_check._test_runner()
            base_result = base_check.run_tests(target_test=list(named)) if base_runner == runner else None
        except Exception as error:  # no base inventory is no evidence
            return OracleJudgment(ORACLE_BASELINE_INDETERMINATE, f"base run failed: {type(error).__name__}: {error}",
                                  evidence)
    expected = baseline_inventory(base_result, runner)
    if not expected:
        return OracleJudgment(ORACLE_BASELINE_INDETERMINATE,
                              "the named tests produced no complete structured inventory at the base revision", evidence)
    evidence["expected_cases"] = expected
    evidence["baseline_report"] = (base_result.get("test_execution") or {}).get("report_files")

    try:
        result = validator.run_tests(target_test=list(named)) or {}
    except Exception as error:  # a runner failure is no evidence either way
        return OracleJudgment(ORACLE_EVIDENCE_INDETERMINATE, f"runner raised: {type(error).__name__}: {error}",
                              evidence)
    report = result.get("test_execution") if isinstance(result, dict) else None
    if isinstance(report, dict):
        evidence["report"] = {"gate_id": report.get("gate_id"), "report_files": report.get("report_files")}
    after = OracleSurface(base, candidate_root, named).digests()[1]
    if after != candidate_digests:
        evidence["changed_during_run"] = changed_surface(candidate_digests, after)
        return OracleJudgment(ORACLE_CHANGED_DURING_RUN, "oracle dependency changed during the run: "
                              + ", ".join(evidence["changed_during_run"]), evidence)
    code, reason = judge_candidate_run(expected, result, runner)
    if code == ORACLE_PASSED:
        evidence["provenance"] = "KRIYA_CONTROLLED"
    return OracleJudgment(code, reason, evidence)
