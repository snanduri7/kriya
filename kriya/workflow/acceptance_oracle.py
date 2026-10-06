"""FS-1C2 B2-a: operator executable acceptance evidence for BEHAVIOR claims (Python).

A requirement statement that asks for new behaviour ("``freeze_time(0)`` freezes
time at the epoch") makes a BEHAVIOR claim (requirements.requirement_claims).
Nothing the run produces may close it: not the model's verdict (FS-1B), not a
test the candidate wrote, not a pre-existing regression test (FS-1C1). This
module is its producer. Authority invariant: the oracle

1. exists before candidate generation - an operator file passed with the goal
   (``kriya generate --acceptance <file>``), read, digested and copied into
   Kriya's state directory (``<state>/acceptance/<sha256>.py``) before any
   model call, never generated from prose;
2. is bound to the run: every case names the requirement whose BEHAVIOR claim
   it proves (``@pytest.mark.kriya_requirement("REQ-1")``, metadata only); an id
   outside the derived requirement set, a mutation-scope requirement, or a case
   without a requirement refuses the file before generation; the artifact
   records the requirement-set digest it was bound to;
3. is immutable to the candidate: the stored copy lives outside the workspace,
   and each run stages it into the candidate's ``.kriya/`` control path (no
   candidate write reaches it), re-checking the digest before and after;
4. runs under a Kriya-controlled boundary, never the repository's pytest
   machinery: Kriya's own runner script and ini file (``-c``), ``--rootdir``
   at the staging directory, ``--noconftest`` (no candidate ``conftest.py``),
   ``PYTEST_DISABLE_PLUGIN_AUTOLOAD`` (no installed plugin), ``PYTEST_ADDOPTS``
   / ``PYTEST_PLUGINS`` cleared, ``-p no:cacheprovider``, ``python -I -B``
   (no environment paths, no script directory, no bytecode written into the
   candidate); the runner reports every registered plugin, and any plugin
   outside pytest itself makes the run INDETERMINATE (measured: with
   autoload on, anyio/pytest_asyncio/testmon/xdist load from the environment).
   The candidate project root is appended to ``sys.path`` only so
   its package imports; the acceptance module itself is imported by path
   (``--import-mode=importlib``). Measured on freezegun: a candidate
   ``conftest.py`` forcing every report to pass and a ``pytest.ini`` disabling
   the JUnit plugin are both ignored;
5. is judged on evidence only: a COMPLETE fresh FS-1A TestExecutionReport of
   this invocation plus Kriya's own in-process observations (each case's phase,
   outcome, exception type and traceback files; where each candidate module
   was loaded from), cross-checked case by case.

Judgment per requirement (``judge_acceptance``):

- PASSED: every expected case executed and passed in a complete report. The
  BEHAVIOR claim is recorded SATISFIED (method ``acceptance_oracle``).
- VIOLATED (owner decision, deterministic counter-evidence): the evidence is
  complete and consistent, the artifact and candidate were unchanged during the
  run, every candidate module the file imports was loaded from the candidate
  root, and an expected case's call phase failed with a contradiction - an
  assertion (``AssertionError`` / ``pytest.fail``) after candidate code ran, or
  an exception whose traceback passes through a candidate source file (the
  candidate raised it). The requirement becomes VIOLATED whatever the verifier
  said.
- Anything else is INDETERMINATE, never VIOLATED: no report, malformed or
  incomplete report, timeout, a case that did not execute, collection or setup
  errors, an import failure, an exception only in the acceptance file's own
  code, a candidate module loaded from outside the candidate root. The claim
  stays open (UNVERIFIED).

Supported layouts (B2-a): the acceptance file imports candidate code that is a
regular package (``pkg/__init__.py``) or module (``pkg.py``) at the project
root - a flat layout, with or without a ``tests/`` package. Refused with
``ACCEPTANCE_LAYOUT_UNSUPPORTED`` before anything runs: a ``src/`` layout, a
namespace package (directory without ``__init__.py``), an import of test-side
code (``tests``/``conftest``/``test_*`` - candidate-writable test code is never
part of the oracle), a relative import, or no candidate import at all. There is
no fallback to the repository's own test configuration. Project plugins and
conftest fixtures are not loaded: a case that needs them errors and is
INDETERMINATE.

Residual (disclosed, as FS-1C0): candidate production code the oracle imports
runs in the same process and could tamper with the runner in-process; the
project's dependency environment (``.kriya/venv``, built from the candidate's
declared dependencies) is used as-is (installed plugins never autoload, but a
dependency's ``.pth`` file runs at interpreter start).
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from kriya.tools import test_execution

ACCEPTANCE_CONTRACT_VERSION = 1
ACCEPTANCE_METHOD = "acceptance_oracle"
ACCEPTANCE_MARKER = "kriya_requirement"
ACCEPTANCE_RUNNER = "pytest"

ACCEPTANCE_PASSED = "ACCEPTANCE_PASSED"
ACCEPTANCE_VIOLATED = "ACCEPTANCE_VIOLATED"
ACCEPTANCE_ARTIFACT_INVALID = "ACCEPTANCE_ARTIFACT_INVALID"
ACCEPTANCE_UNKNOWN_REQUIREMENT = "ACCEPTANCE_UNKNOWN_REQUIREMENT"
ACCEPTANCE_REQUIREMENT_NOT_BEHAVIORAL = "ACCEPTANCE_REQUIREMENT_NOT_BEHAVIORAL"
ACCEPTANCE_REQUIREMENT_SET_MISMATCH = "ACCEPTANCE_REQUIREMENT_SET_MISMATCH"
ACCEPTANCE_ARTIFACT_CHANGED = "ACCEPTANCE_ARTIFACT_CHANGED"
ACCEPTANCE_LAYOUT_UNSUPPORTED = "ACCEPTANCE_LAYOUT_UNSUPPORTED"
ACCEPTANCE_RUNNER_UNSUPPORTED = "ACCEPTANCE_RUNNER_UNSUPPORTED"
ACCEPTANCE_ENVIRONMENT_UNAVAILABLE = "ACCEPTANCE_ENVIRONMENT_UNAVAILABLE"
ACCEPTANCE_EVIDENCE_INDETERMINATE = "ACCEPTANCE_EVIDENCE_INDETERMINATE"
ACCEPTANCE_CANDIDATE_CHANGED_DURING_RUN = "ACCEPTANCE_CANDIDATE_CHANGED_DURING_RUN"
ACCEPTANCE_CANDIDATE_NOT_EXERCISED = "ACCEPTANCE_CANDIDATE_NOT_EXERCISED"
ACCEPTANCE_FOREIGN_PLUGIN = "ACCEPTANCE_FOREIGN_PLUGIN"
ACCEPTANCE_IDENTITY_NOT_EXECUTED = "ACCEPTANCE_IDENTITY_NOT_EXECUTED"
ACCEPTANCE_FAILED_WITHOUT_CONTRADICTION = "ACCEPTANCE_FAILED_WITHOUT_CONTRADICTION"
ACCEPTANCE_SUPERSEDED = "ACCEPTANCE_SUPERSEDED"
# B2-COV: every case passed, but the statement is a general rule finite cases
# cannot prove; the cases are recorded as supporting evidence only.
ACCEPTANCE_GENERAL_RULE_UNPROVEN = "ACCEPTANCE_GENERAL_RULE_UNPROVEN"

# The staged module's name is fixed, so every case identity is known from the
# file alone: ``kriya_acceptance.<test>`` / ``kriya_acceptance.<Class>.<test>``.
STAGED_MODULE = "kriya_acceptance"
STAGING_DIR = os.path.join(".kriya", "acceptance-runs")
_RUNNER_FILE = "kriya_acceptance_runner.py"
_INI_FILE = "kriya-acceptance.ini"
_REQUEST_FILE = "request.json"
_OBSERVATIONS_FILE = "observations.json"

_TEST_SIDE_NAMES = frozenset({"tests", "test", "testing", "conftest"})
_ALLOWED_MARKS = frozenset({ACCEPTANCE_MARKER, "parametrize"})
_ASSERTION_TYPES = frozenset({"AssertionError", "Failed"})
_ENVIRONMENT_TYPES = frozenset({"ImportError", "ModuleNotFoundError"})

_INI_SOURCE = (
    "[pytest]\n"
    "python_files = kriya_acceptance.py\n"
    "python_classes = Test\n"
    "python_functions = test\n"
    "markers =\n"
    f"    {ACCEPTANCE_MARKER}(requirement_id): the original requirement whose BEHAVIOR claim this case proves\n"
)

# Kriya's runner: written into the staging directory by Kriya, never candidate
# content. It runs in the project's interpreter (host or container), so every
# path it reports is relative to its own working directory (the candidate
# root), which it reports too.
_RUNNER_SOURCE = '''"""Kriya acceptance runner (FS-1C2 B2-a, contract %(version)d). Written by Kriya."""
import json
import os
import sys

STAGE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.getcwd()
for _name in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
    os.environ.pop(_name, None)
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
_excluded = {os.path.realpath(ROOT), os.path.realpath(STAGE)}
sys.path = [p for p in sys.path if p and os.path.realpath(p) not in _excluded]
sys.path.append(ROOT)
with open(os.path.join(STAGE, "%(request)s"), "r", encoding="utf-8") as _handle:
    REQUEST = json.load(_handle)

import pytest  # noqa: E402 - only after the environment is fixed


def _plugin_module(plugin):
    if isinstance(plugin, type(sys)):
        return plugin.__name__
    return plugin.__module__ if isinstance(plugin, type) else type(plugin).__module__


class _Observer:
    def __init__(self):
        self.reports = []
        self.plugins = []

    def pytest_sessionfinish(self, session):
        self.plugins = sorted({_plugin_module(p) for p in session.config.pluginmanager.get_plugins()})

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        outcome = yield
        report = outcome.get_result()
        excinfo = call.excinfo
        frames = [str(entry.path) for entry in excinfo.traceback] if excinfo is not None else []
        self.reports.append({"nodeid": item.nodeid, "when": call.when, "outcome": report.outcome,
                             "exception": excinfo.typename if excinfo is not None else None,
                             "frames": frames})


_observer = _Observer()
_code = int(pytest.main(list(sys.argv[1:]), plugins=[_observer]))
_modules = {}
for _module_name in REQUEST["candidate_modules"]:
    _module = sys.modules.get(_module_name)
    _path = getattr(_module, "__file__", None) if _module is not None else None
    _modules[_module_name] = os.path.realpath(_path) if _path else None
with open(os.path.join(STAGE, "%(observations)s"), "w", encoding="utf-8") as _handle:
    json.dump({"contract": %(version)d, "exit_code": _code, "root": os.path.realpath(ROOT),
               "reports": _observer.reports, "modules": _modules, "plugins": _observer.plugins}, _handle)
sys.exit(_code)
''' % {"version": ACCEPTANCE_CONTRACT_VERSION, "request": _REQUEST_FILE, "observations": _OBSERVATIONS_FILE}

RUNNER_CONTRACT_DIGEST = hashlib.sha256((_RUNNER_SOURCE + _INI_SOURCE).encode("utf-8")).hexdigest()


class AcceptanceError(Exception):
    """A typed refusal of an acceptance artifact (before generation) or of
    its execution (a layout or runner this stage does not support)."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(f"{reason_code}: {message}")
        self.reason_code = reason_code
        self.message = message


@dataclass(frozen=True)
class AcceptanceCase:
    requirement_ids: Tuple[str, ...]
    identity: str  # kriya_acceptance.<test> / kriya_acceptance.<Class>.<test>
    line: int


@dataclass(frozen=True)
class AcceptanceArtifact:
    """The operator's acceptance file, bound before generation."""

    digest: str
    stored_path: str
    source_name: str
    requirement_set_digest: str
    cases: Tuple[AcceptanceCase, ...]
    imports: Tuple[str, ...]
    # B2-c: "java" for a JUnit 5 class (kriya/workflow/acceptance_jvm.py), with
    # its package, class name and injection path.
    language: str = "python"
    java: Optional[Dict[str, str]] = None

    @property
    def requirement_ids(self) -> List[str]:
        return sorted({rid for case in self.cases for rid in case.requirement_ids})

    def identities_for(self, requirement_id: str) -> List[str]:
        return sorted(case.identity for case in self.cases if requirement_id in case.requirement_ids)

    def to_dict(self) -> Dict[str, Any]:
        return {"digest": self.digest, "source_name": self.source_name, "language": self.language,
                "contract": ACCEPTANCE_CONTRACT_VERSION,
                "runner_contract_digest": RUNNER_CONTRACT_DIGEST,
                "requirement_set_digest": self.requirement_set_digest,
                "cases": {rid: self.identities_for(rid) for rid in self.requirement_ids}}


def bound_acceptance(engine: Any) -> Optional["AcceptanceArtifact"]:
    """The acceptance artifact bound to ``engine`` for this run (the CLI sets
    ``WorkflowEngine.acceptance``), or None - never anything that merely
    looks like one."""
    artifact = getattr(engine, "acceptance", None)
    return artifact if isinstance(artifact, AcceptanceArtifact) else None


# ------------------------------------------------------------ the artifact

def _marker(decorator: ast.expr) -> Optional[Tuple[str, Optional[ast.Call]]]:
    """``pytest.mark.<name>`` / ``pytest.mark.<name>(...)`` -> (name, call)."""
    call = decorator if isinstance(decorator, ast.Call) else None
    target = call.func if call is not None else decorator
    if (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Attribute)
            and target.value.attr == "mark" and isinstance(target.value.value, ast.Name)
            and target.value.value.id == "pytest"):
        return target.attr, call
    return None


def _case(function: ast.AST, identity: str) -> AcceptanceCase:
    if isinstance(function, ast.AsyncFunctionDef):
        raise AcceptanceError(ACCEPTANCE_ARTIFACT_INVALID, f"{identity}: async test functions are not supported")
    requirement_ids: List[str] = []
    for decorator in function.decorator_list:
        found = _marker(decorator)
        if found is None:
            continue
        name, call = found
        if name not in _ALLOWED_MARKS:
            raise AcceptanceError(ACCEPTANCE_ARTIFACT_INVALID,
                                  f"{identity}: pytest.mark.{name} is not allowed on an acceptance case "
                                  "(it could skip or invert the case)")
        if name != ACCEPTANCE_MARKER:
            continue
        if call is None or call.keywords or not call.args or not all(
                isinstance(arg, ast.Constant) and isinstance(arg.value, str) for arg in call.args):
            raise AcceptanceError(ACCEPTANCE_ARTIFACT_INVALID,
                                  f"{identity}: {ACCEPTANCE_MARKER} takes requirement ids as string literals")
        requirement_ids.extend(arg.value for arg in call.args)
    if not requirement_ids:
        raise AcceptanceError(ACCEPTANCE_ARTIFACT_INVALID,
                              f"{identity}: every acceptance case must name the requirement it proves "
                              f"(@pytest.mark.{ACCEPTANCE_MARKER}(\"REQ-n\"))")
    return AcceptanceCase(tuple(sorted(set(requirement_ids))), identity, function.lineno)


def parse_acceptance(source: bytes) -> Tuple[Tuple[AcceptanceCase, ...], Tuple[str, ...]]:
    """The cases (each with the requirements it proves) and the top-level
    modules an acceptance file imports. Only what the file literally states
    counts: a case is a module-level ``test*`` function or a ``test*`` method
    of a module-level ``Test*`` class."""
    try:
        tree = ast.parse(source.decode("utf-8"), filename=f"{STAGED_MODULE}.py")
    except (UnicodeDecodeError, SyntaxError, ValueError) as error:
        raise AcceptanceError(ACCEPTANCE_ARTIFACT_INVALID, f"not a readable Python file: {error}") from error
    cases: List[AcceptanceCase] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark"
                                                for t in node.targets):
            raise AcceptanceError(ACCEPTANCE_ARTIFACT_INVALID, "module-level pytestmark is not allowed")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            cases.append(_case(node, f"{STAGED_MODULE}.{node.name}"))
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            if any(_marker(decorator) for decorator in node.decorator_list):
                raise AcceptanceError(ACCEPTANCE_ARTIFACT_INVALID,
                                      f"{node.name}: put markers on each test method, not on the class")
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if member.name == "__init__":
                        raise AcceptanceError(ACCEPTANCE_ARTIFACT_INVALID,
                                              f"{node.name}: a test class with __init__ is never collected")
                    if member.name.startswith("test"):
                        cases.append(_case(member, f"{STAGED_MODULE}.{node.name}.{member.name}"))
    identities = [case.identity for case in cases]
    duplicates = sorted({identity for identity in identities if identities.count(identity) > 1})
    if duplicates:
        raise AcceptanceError(ACCEPTANCE_ARTIFACT_INVALID, "duplicate case names: " + ", ".join(duplicates))
    if not cases:
        raise AcceptanceError(ACCEPTANCE_ARTIFACT_INVALID, "no acceptance case (test function) found")
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                raise AcceptanceError(ACCEPTANCE_ARTIFACT_INVALID, "relative imports are not supported")
            imports.add((node.module or "").split(".")[0])
        elif isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
    imports.discard("pytest")
    imports.discard("")
    return tuple(cases), tuple(sorted(imports))


def acceptance_store(state_root: str) -> str:
    return os.path.join(state_root, "acceptance")


def load_acceptance(path: str, requirements: Any, state_root: str) -> AcceptanceArtifact:
    """Read, validate and bind the operator's acceptance file before
    generation: every case names requirements of ``requirements`` (the run's
    derived set) that make a behaviour statement; the exact bytes are copied,
    content-addressed, into Kriya's state directory."""
    from kriya.workflow.requirements import is_mutation_scope_requirement

    try:
        with open(path, "rb") as handle:
            source = handle.read()
    except OSError as error:
        raise AcceptanceError(ACCEPTANCE_ARTIFACT_INVALID, f"cannot read {path!r}: {error}") from error
    java: Optional[Dict[str, str]] = None
    if path.endswith(".java"):
        from kriya.workflow.acceptance_jvm import parse_java_acceptance

        cases, java = parse_java_acceptance(source)
        imports: Tuple[str, ...] = ()
    elif path.endswith(".py"):
        cases, imports = parse_acceptance(source)
    else:
        raise AcceptanceError(ACCEPTANCE_ARTIFACT_INVALID, "an acceptance file is a .py (pytest) or .java (JUnit 5) file")
    known = set(requirements.ids)
    unknown = sorted({rid for case in cases for rid in case.requirement_ids if rid not in known})
    if unknown:
        raise AcceptanceError(ACCEPTANCE_UNKNOWN_REQUIREMENT,
                              f"requirement id(s) not in the goal's derived requirements "
                              f"({', '.join(requirements.ids) or 'none'}): {', '.join(unknown)}")
    scope_only = sorted({rid for case in cases for rid in case.requirement_ids
                         if is_mutation_scope_requirement(requirements.get(rid).text)})
    if scope_only:
        raise AcceptanceError(ACCEPTANCE_REQUIREMENT_NOT_BEHAVIORAL,
                              "mutation-scope requirement(s) are decided from the run's own changes, never by "
                              "an acceptance case: " + ", ".join(scope_only))
    digest = hashlib.sha256(source).hexdigest()
    store = acceptance_store(state_root)
    os.makedirs(store, exist_ok=True)
    stored = os.path.join(store, f"{digest}{'.java' if java is not None else '.py'}")
    if not os.path.isfile(stored) or _file_digest(stored) != digest:
        temporary = f"{stored}.{uuid.uuid4().hex}.tmp"
        with open(temporary, "wb") as handle:
            handle.write(source)
        os.replace(temporary, stored)
    return AcceptanceArtifact(digest=digest, stored_path=stored, source_name=os.path.basename(path),
                              requirement_set_digest=requirements.digest, cases=cases, imports=imports,
                              language="java" if java is not None else "python", java=java)


def _file_digest(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return None


def read_stored(artifact: AcceptanceArtifact) -> bytes:
    """The stored acceptance bytes, only if they are still the bound digest."""
    try:
        with open(artifact.stored_path, "rb") as handle:
            data = handle.read()
    except OSError as error:
        raise AcceptanceError(ACCEPTANCE_ARTIFACT_CHANGED, f"stored acceptance file unreadable: {error}") from error
    if hashlib.sha256(data).hexdigest() != artifact.digest:
        raise AcceptanceError(ACCEPTANCE_ARTIFACT_CHANGED, "stored acceptance file differs from its bound digest")
    return data


# ------------------------------------------------------------ layout

def candidate_modules(candidate_root: str, artifact: AcceptanceArtifact) -> List[str]:
    """The modules the acceptance file imports from the candidate (B2-a
    layouts only), or a typed refusal."""
    found: List[str] = []
    for name in artifact.imports:
        if name in _TEST_SIDE_NAMES or name.startswith("test_") or name.endswith("_test"):
            raise AcceptanceError(ACCEPTANCE_LAYOUT_UNSUPPORTED,
                                  f"the acceptance file imports test-side code ({name}); candidate test code is "
                                  "never part of the oracle")
        package = os.path.join(candidate_root, name)
        if os.path.isfile(os.path.join(package, "__init__.py")) or os.path.isfile(package + ".py"):
            found.append(name)
        elif os.path.isdir(package):
            raise AcceptanceError(ACCEPTANCE_LAYOUT_UNSUPPORTED,
                                  f"{name}/ is a namespace package (no __init__.py): not supported yet")
        elif os.path.exists(os.path.join(candidate_root, "src", name)) or os.path.isfile(
                os.path.join(candidate_root, "src", name + ".py")):
            raise AcceptanceError(ACCEPTANCE_LAYOUT_UNSUPPORTED, f"src/ layout ({name}): not supported yet")
    if not found:
        raise AcceptanceError(ACCEPTANCE_LAYOUT_UNSUPPORTED,
                              "the acceptance file imports no package or module at the project root, so it "
                              "cannot exercise the candidate (supported: flat package/module layouts)")
    return found


# ------------------------------------------------------------ execution

@dataclass
class AcceptanceRun:
    """One Kriya-controlled execution of the acceptance file on a candidate."""

    refusal: Optional[AcceptanceError] = None
    result: Dict[str, Any] = field(default_factory=dict)
    report: Optional[test_execution.TestExecutionReport] = None
    observations: Optional[Dict[str, Any]] = None
    candidate_modules: List[str] = field(default_factory=list)
    integrity_problem: Optional[Tuple[str, str]] = None  # (reason code, detail)
    candidate_digest: Optional[str] = None
    # B2-c (JVM) only: the trust-surface digest checked before the run, the
    # candidate's main classes and per-case report detail.
    language: str = "python"
    trust_surface_digest: Optional[str] = None
    candidate_classes: Optional[List[str]] = None
    case_details: Optional[Dict[str, List[Dict[str, str]]]] = None

    def evidence(self) -> Dict[str, Any]:
        report = self.report
        return {"gate_id": report.gate_id if report else None,
                "report_files": list(report.report_files) if report else [],
                "completeness": report.completeness if report else None,
                "exit_code": (self.observations or {}).get("exit_code"),
                "candidate_modules": self.candidate_modules, "candidate_digest": self.candidate_digest}


def _tree_digest(root: str, paths: Iterable[str]) -> str:
    payload = {path: _file_digest(os.path.join(root, path)) for path in sorted(set(paths))}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def run_acceptance(
    artifact: AcceptanceArtifact, candidate_root: str, *, candidate_paths: Iterable[str],
    validator_factory: Callable[[], Any],
) -> AcceptanceRun:
    """Stage the bound artifact into the candidate's control path and run it
    once through the validator's acceptance gate."""
    run = AcceptanceRun()
    try:
        source = read_stored(artifact)
        run.candidate_modules = candidate_modules(candidate_root, artifact)
    except AcceptanceError as refusal:
        run.refusal = refusal
        return run
    tracked = sorted(set(candidate_paths) | {os.path.relpath(p, candidate_root) for p in _module_files(
        candidate_root, run.candidate_modules)})
    run.candidate_digest = _tree_digest(candidate_root, tracked)
    stage_rel = os.path.join(STAGING_DIR, uuid.uuid4().hex)
    stage = os.path.join(candidate_root, stage_rel)
    try:
        os.makedirs(stage)
        staged = {f"{STAGED_MODULE}.py": source, _RUNNER_FILE: _RUNNER_SOURCE.encode("utf-8"),
                  _INI_FILE: _INI_SOURCE.encode("utf-8"),
                  _REQUEST_FILE: json.dumps({"candidate_modules": run.candidate_modules}).encode("utf-8")}
        for name, data in staged.items():
            with open(os.path.join(stage, name), "xb") as handle:
                handle.write(data)
        arguments = ["-c", os.path.join(stage_rel, _INI_FILE), "--rootdir", stage_rel, "--noconftest",
                     "-p", "no:cacheprovider", "--import-mode=importlib", "-q",
                     os.path.join(stage_rel, f"{STAGED_MODULE}.py")]
        validator = validator_factory()
        if getattr(validator, "stack", None) != "python":
            run.refusal = AcceptanceError(ACCEPTANCE_RUNNER_UNSUPPORTED,
                                          f"executable acceptance supports Python projects only (stack: "
                                          f"{getattr(validator, 'stack', None)})")
            return run
        run.result = validator.run_acceptance(os.path.join(stage_rel, _RUNNER_FILE), arguments) or {}
        run.report = test_execution.report_from_result(run.result)
        run.observations = _read_observations(os.path.join(stage, _OBSERVATIONS_FILE))
        for name, data in staged.items():
            if _file_digest(os.path.join(stage, name)) != hashlib.sha256(data).hexdigest():
                run.integrity_problem = (ACCEPTANCE_ARTIFACT_CHANGED, f"{name} changed during the run")
                break
        if run.integrity_problem is None and _tree_digest(candidate_root, tracked) != run.candidate_digest:
            run.integrity_problem = (ACCEPTANCE_CANDIDATE_CHANGED_DURING_RUN,
                                     "candidate files changed while the acceptance file ran")
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return run


def _module_files(root: str, modules: Sequence[str]) -> List[str]:
    files: List[str] = []
    for name in modules:
        single = os.path.join(root, name + ".py")
        if os.path.isfile(single):
            files.append(single)
            continue
        for directory, dirs, names in os.walk(os.path.join(root, name)):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            files.extend(os.path.join(directory, n) for n in names if n.endswith(".py"))
    return files


def _read_observations(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if (not isinstance(data, dict) or data.get("contract") != ACCEPTANCE_CONTRACT_VERSION
            or not isinstance(data.get("reports"), list) or not isinstance(data.get("modules"), dict)
            or not isinstance(data.get("root"), str) or not isinstance(data.get("plugins"), list)):
        return None
    return data


# ------------------------------------------------------------ judgment

@dataclass(frozen=True)
class AcceptanceJudgment:
    code: str
    reason: str
    evidence: Dict[str, Any]

    @property
    def passed(self) -> bool:
        return self.code == ACCEPTANCE_PASSED

    @property
    def violated(self) -> bool:
        return self.code == ACCEPTANCE_VIOLATED


def _within(path: str, root: str) -> bool:
    """Inside the candidate root but outside Kriya's own ``.kriya/`` (the
    staged acceptance file, the project venv)."""
    relative = os.path.relpath(os.path.realpath(path), root)
    return not relative.startswith("..") and relative.split(os.sep)[0] != ".kriya"


def _nodeid(case: test_execution.TestCaseResult) -> Optional[str]:
    if case.classname != STAGED_MODULE and not case.classname.startswith(STAGED_MODULE + "."):
        return None
    classes = case.classname[len(STAGED_MODULE) + 1:].split(".") if case.classname != STAGED_MODULE else []
    return "::".join([f"{STAGED_MODULE}.py", *[c for c in classes if c], case.name])


def _observed_status(phases: List[Dict[str, Any]]) -> str:
    """The JUnit status the observed phases correspond to."""
    by_when = {phase.get("when"): phase.get("outcome") for phase in phases}
    if any(outcome == "skipped" for outcome in by_when.values()):
        return test_execution.SKIPPED
    if by_when.get("setup") == "failed" or by_when.get("teardown") == "failed":
        return test_execution.ERROR
    if by_when.get("call") == "failed":
        return test_execution.FAILED
    return test_execution.PASSED if by_when.get("call") == "passed" else "unknown"


def _contradiction(phases: List[Dict[str, Any]], root: str, exercised: bool) -> bool:
    call = next((phase for phase in phases if phase.get("when") == "call"), None)
    if call is None or call.get("outcome") != "failed":
        return False
    exception = call.get("exception")
    if not exception or exception in _ENVIRONMENT_TYPES:
        return False
    if any(_within(frame, root) for frame in call.get("frames") or () if isinstance(frame, str)):
        return True  # the candidate's own code raised it
    return exercised and exception in _ASSERTION_TYPES


def judge_acceptance(artifact: AcceptanceArtifact, run: AcceptanceRun) -> Dict[str, AcceptanceJudgment]:
    """One judgment per requirement the artifact covers (see the module
    docstring): PASSED, VIOLATED, or an INDETERMINATE reason code."""
    base = {"acceptance_digest": artifact.digest, "contract": ACCEPTANCE_CONTRACT_VERSION,
            "runner_contract_digest": RUNNER_CONTRACT_DIGEST, "runner": ACCEPTANCE_RUNNER,
            "acceptance_requirement_set_digest": artifact.requirement_set_digest, **run.evidence()}
    if artifact.language == "java":
        from kriya.workflow.acceptance_jvm import judge_java_acceptance

        return judge_java_acceptance(artifact, run, base)

    def every(code: str, reason: str) -> Dict[str, AcceptanceJudgment]:
        return {rid: AcceptanceJudgment(code, reason, {**base, "cases": artifact.identities_for(rid)})
                for rid in artifact.requirement_ids}

    if run.refusal is not None:
        return every(run.refusal.reason_code, run.refusal.message)
    if run.integrity_problem is not None:
        return every(*run.integrity_problem)
    report, observed = run.report, run.observations
    if report is None or not report.complete or report.runner != ACCEPTANCE_RUNNER:
        reason = "no structured report" if report is None else (report.reason or f"runner {report.runner}")
        return every(ACCEPTANCE_EVIDENCE_INDETERMINATE, f"acceptance run evidence not complete: {reason}")
    if observed is None:
        return every(ACCEPTANCE_EVIDENCE_INDETERMINATE, "Kriya's run observations are missing or malformed")
    # pytest's own completed-session exit codes (FS-1A): passed, some failed,
    # nothing collected. Interrupted / internal / usage errors are not.
    if observed.get("exit_code") not in (0, 1, 5):
        return every(ACCEPTANCE_EVIDENCE_INDETERMINATE, f"pytest session incomplete (exit {observed.get('exit_code')})")
    # Only pytest's own plugins and Kriya's observer (the runner script,
    # ``__main__``) may take part: a conftest or an installed plugin that got
    # in anyway (autoload, -p) could decide outcomes.
    foreign = sorted(name for name in observed["plugins"] if not (
        isinstance(name, str) and (name in ("__main__", "pytest") or name.startswith("_pytest."))))
    if foreign:
        return every(ACCEPTANCE_FOREIGN_PLUGIN, "plugin(s) outside pytest itself were loaded: " + ", ".join(foreign))
    root = observed["root"]
    modules = observed["modules"]
    elsewhere = sorted(name for name, path in modules.items() if path and not _within(path, root))
    if elsewhere:
        return every(ACCEPTANCE_CANDIDATE_NOT_EXERCISED,
                     "candidate module(s) loaded from outside the candidate root: " + ", ".join(elsewhere))
    exercised = any(path for path in modules.values())
    phases: Dict[str, List[Dict[str, Any]]] = {}
    for entry in observed["reports"]:
        if isinstance(entry, dict) and isinstance(entry.get("nodeid"), str):
            phases.setdefault(entry["nodeid"], []).append(entry)
    cases: Dict[str, List[Tuple[test_execution.TestCaseResult, List[Dict[str, Any]]]]] = {}
    for case in report.cases:
        nodeid = _nodeid(case)
        observed_phases = phases.get(nodeid or "", [])
        if nodeid is None or _observed_status(observed_phases) != case.status:
            return every(ACCEPTANCE_EVIDENCE_INDETERMINATE,
                         f"report and observations disagree on {case.classname}::{case.name}")
        cases.setdefault(case.identity, []).append((case, observed_phases))

    judgments: Dict[str, AcceptanceJudgment] = {}
    for rid in artifact.requirement_ids:
        identities = artifact.identities_for(rid)
        evidence = {**base, "cases": identities,
                    "case_results": {identity: sorted(case.status for case, _ in cases.get(identity, ()))
                                     for identity in identities}}
        contradicted = sorted(identity for identity in identities for case, ph in cases.get(identity, ())
                              if case.status == test_execution.FAILED and _contradiction(ph, root, exercised))
        missing = [identity for identity in identities if identity not in cases]
        not_passed = sorted({identity for identity in identities for case, _ in cases.get(identity, ())
                             if case.status != test_execution.PASSED})
        if contradicted:
            judgments[rid] = AcceptanceJudgment(ACCEPTANCE_VIOLATED, "acceptance case(s) observed the behaviour "
                                                "contradicted: " + ", ".join(sorted(set(contradicted))), evidence)
        elif missing:
            judgments[rid] = AcceptanceJudgment(ACCEPTANCE_IDENTITY_NOT_EXECUTED,
                                                "expected case(s) did not execute: " + ", ".join(missing), evidence)
        elif not_passed:
            judgments[rid] = AcceptanceJudgment(ACCEPTANCE_FAILED_WITHOUT_CONTRADICTION,
                                                "case(s) did not pass, but not as an observed contradiction of "
                                                "the behaviour (setup/import/acceptance-code error or skip): "
                                                + ", ".join(not_passed), evidence)
        else:
            judgments[rid] = AcceptanceJudgment(ACCEPTANCE_PASSED, "", evidence)
    return judgments


# ------------------------------------------------------------ closure

def close_requirements_with_acceptance(
    ledger: Any, requirements: Any, acceptance: Optional[AcceptanceArtifact], *,
    test_files: Optional[Iterable[str]], execute: Callable[[AcceptanceArtifact], AcceptanceRun], source: str,
    revision: Any,
) -> List[Dict[str, Any]]:
    """Judge every requirement the acceptance artifact covers on the
    candidate its verdict judged (``evidence_id``), recording the BEHAVIOR
    claim SATISFIED / VIOLATED / INDETERMINATE (method ``acceptance_oracle``).
    An earlier acceptance judgment of the same candidate under another (or
    no) artifact is superseded. ``execute(artifact)`` runs the file once,
    only when some requirement needs it; if it raises, every pending claim is
    recorded INDETERMINATE before the error propagates.

    ``test_files``: every test file that existed before the run or exists in
    the candidate - which claims a statement makes (and so whether behaviour
    alone may close it) is decided against them, so a candidate deleting a
    named regression test never drops that claim. None (unknown): every
    statement is held to both claims."""
    from kriya.workflow.obligations import ObligationStatus
    from kriya.workflow.requirements import (
        BEHAVIOR,
        BEHAVIOR_EXACT,
        BEHAVIOR_EXAMPLES,
        REGRESSION_PRESERVATION,
        RequirementOutcome,
        behavior_strength,
        named_existing_tests,
        record_requirement_claim,
        requirement_claim_record,
        requirement_claims,
        requirement_obligation_id,
        requirement_outcomes,
    )

    files = list(test_files) if test_files is not None else None
    current_digest = acceptance.digest if acceptance is not None else None
    attempts: List[Dict[str, Any]] = []
    pending: List[Tuple[Any, str, Tuple[str, ...], Dict[str, Any]]] = []

    def record(requirement: Any, evidence_id: str, status: Any, detail: Dict[str, Any], claim: str = BEHAVIOR) -> None:
        record_requirement_claim(ledger, requirements, requirement.id, claim, evidence_id=evidence_id,
                                 method=ACCEPTANCE_METHOD, detail=detail, source=source, revision=revision,
                                 status=status)

    for requirement in requirements.requirements:
        verdict = ledger.current(requirement_obligation_id(requirement.id))
        evidence_id = (verdict.evidence or {}).get("evidence_id") if verdict is not None else None
        prior = requirement_claim_record(ledger, requirement.id, BEHAVIOR, evidence_id)
        prior_digest = (prior.evidence or {}).get("acceptance_digest") if prior is not None else None
        covered = acceptance is not None and requirement.id in acceptance.requirement_ids
        if not covered:
            if (prior is not None and prior.status is not ObligationStatus.INDETERMINATE
                    and (prior.evidence or {}).get("method") == ACCEPTANCE_METHOD and prior_digest != current_digest):
                record(requirement, evidence_id, ObligationStatus.INDETERMINATE,
                       {"reason_code": ACCEPTANCE_SUPERSEDED, "acceptance_digest": current_digest,
                        "superseded_digest": prior_digest})
                attempts.append({"requirement": requirement.id, "kind": ACCEPTANCE_METHOD, "closed": False,
                                 "reason_code": ACCEPTANCE_SUPERSEDED,
                                 "reason": "an earlier acceptance judgment of this candidate used another artifact"})
            continue
        entry: Dict[str, Any] = {"requirement": requirement.id, "kind": ACCEPTANCE_METHOD, "closed": False,
                                 "acceptance_digest": acceptance.digest,
                                 "cases": acceptance.identities_for(requirement.id)}
        attempts.append(entry)
        if not evidence_id:
            entry["reason"] = "the verdict has no evidence id to bind to"
            continue
        claims = (requirement_claims(requirement.text, named_existing_tests(requirement.text, files))
                  if files is not None else (BEHAVIOR, REGRESSION_PRESERVATION))
        entry["claims"] = list(claims)
        if BEHAVIOR not in claims:
            entry["reason"] = "the statement makes no behaviour claim (it only names tests that keep passing)"
            continue
        if acceptance.requirement_set_digest != requirements.digest:
            entry["reason_code"] = ACCEPTANCE_REQUIREMENT_SET_MISMATCH
            entry["reason"] = "the acceptance file was bound to another requirement set"
            record(requirement, evidence_id, ObligationStatus.INDETERMINATE,
                   {"reason_code": ACCEPTANCE_REQUIREMENT_SET_MISMATCH, "acceptance_digest": acceptance.digest})
            continue
        pending.append((requirement, evidence_id, claims, entry))

    if pending:
        try:
            run = execute(acceptance)
        except Exception as error:
            for requirement, evidence_id, _, entry in pending:
                record(requirement, evidence_id, ObligationStatus.INDETERMINATE,
                       {"reason_code": ACCEPTANCE_EVIDENCE_INDETERMINATE, "acceptance_digest": acceptance.digest,
                        "error": type(error).__name__})
                entry.update({"reason_code": ACCEPTANCE_EVIDENCE_INDETERMINATE,
                              "reason": f"the acceptance run raised {type(error).__name__}"})
            raise
        judgments = judge_acceptance(acceptance, run)
        for requirement, evidence_id, claims, entry in pending:
            judgment = judgments[requirement.id]
            # B2-COV: a counterexample disproves any statement; passing cases
            # close only an EXACT (enumerated) one.
            strength, why = behavior_strength(requirement.text,
                                              regression_covered=REGRESSION_PRESERVATION in claims)
            code, reason = judgment.code, judgment.reason
            detail = {**judgment.evidence, "required_claims": list(claims), "strength": strength,
                      "strength_reasons": why["reasons"]}
            if judgment.passed and strength != BEHAVIOR_EXACT:
                code = ACCEPTANCE_GENERAL_RULE_UNPROVEN
                reason = ("every acceptance case passed, but the statement is a general rule that finite cases "
                          "cannot prove (" + "; ".join(why["reasons"]) + ") - recorded as supporting evidence")
                record(requirement, evidence_id, ObligationStatus.SATISFIED,
                       {**detail, "reason_code": judgment.code}, claim=BEHAVIOR_EXAMPLES)
            status = (ObligationStatus.SATISFIED if code == ACCEPTANCE_PASSED
                      else ObligationStatus.VIOLATED if judgment.violated else ObligationStatus.INDETERMINATE)
            record(requirement, evidence_id, status, {**detail, "reason_code": code})
            entry.update({"reason_code": code, "behavior": status.value, "strength": strength,
                          "case_results": judgment.evidence.get("case_results")})
            if reason:
                entry["reason"] = reason
        outcomes = requirement_outcomes(ledger, requirements)
        for requirement, _, _, entry in pending:
            entry["closed"] = outcomes[requirement.id] is RequirementOutcome.CLOSED_BY_EVIDENCE
            entry["violated"] = outcomes[requirement.id] is RequirementOutcome.VIOLATED
    return attempts
