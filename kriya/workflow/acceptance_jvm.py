"""FS-1C2 B2-c: operator executable acceptance evidence for JVM projects (Maven + JUnit 5/Surefire only).

The authority model is B2-a's (kriya/workflow/acceptance_oracle.py): an operator-authored JUnit test class, bound
before generation (`kriya generate --acceptance <File>.java`), stored content-addressed outside the workspace, digest-
bound to the run and its requirements, executed against the exact candidate, and judged only on a complete, fresh
FS-1A report; B2-COV (claim strength) applies unchanged because both languages close through
``close_requirements_with_acceptance``.

Artifact format (deterministic, no inference): one ``.java`` file with exactly one ``package`` declaration and exactly
one type declaration, a class. Every JUnit 5 ``@Test`` method has a ``// kriya_requirement: REQ-n[, REQ-m]`` line
comment above it; a marker on anything else, a ``@Test`` without one, and annotations that could skip, repeat or
generate cases (``@Disabled``, ``@ParameterizedTest``, ``@RepeatedTest``, ``@TestFactory``, ``@TestTemplate``,
``@Nested``, ``@EnabledIf*``/``@DisabledIf*``...) are refused. Case identity: ``<package>.<Class>.<method>`` - the
Surefire report's ``classname`` + ``name`` (measured: JUnit 5 / Surefire 3 report the bare method name).

Execution (never in the candidate's real workspace):

1. Trust surface: every JVM execution-affecting path C0 already defines (named_test_oracle.OracleSurface: every build
   file and parent/module pom, ``.mvn/``, ``mvnw``, every non-main source set ``src/<set>/``, ``junit-platform.
   properties``, ``META-INF/services/``) must equal the run's authorized base revision, and the candidate must not
   have written under a build output root - else ``ACCEPTANCE_TRUST_SURFACE_CHANGED`` (no evidence).
2. The injection path ``src/test/java/<package>/<Class>.java`` must exist in neither the base nor the candidate -
   else ``ACCEPTANCE_PATH_COLLISION`` (never overwritten).
3. The candidate tree is copied into a fresh Kriya-owned directory under the state root; Maven-only projects
   (``pom.xml`` at the root) are supported, anything else is ``ACCEPTANCE_RUNNER_UNSUPPORTED``.
4. The candidate's own main code is compiled there first (the ordinary Maven compile gate): a failure is
   ``ACCEPTANCE_CANDIDATE_COMPILE_FAILED`` - an ordinary candidate failure, never acceptance counter-evidence.
5. The operator class is injected (create-exclusive) and run alone through the ordinary Maven test gate
   (``-Dtest=<Class>``, containment/offline policy and the FS-1A fresh report binding unchanged). A missing report
   with the injected class in a compilation error is ``ACCEPTANCE_HARNESS_COMPILE_FAILED``; any other missing or
   incomplete report is ``ACCEPTANCE_EVIDENCE_INDETERMINATE`` (naming the plugin goal when the build was rejected
   before the tests ran - measured: Apache RAT rejects an operator class without the repository's license header,
   so on such repositories the operator file carries that header). The injected file is re-hashed after the run.
6. Per-case detail is read from the report files FS-1A digested (each re-verified against that digest).

Judgment (per requirement, ``judge_java_acceptance``): PASSED when every expected identity executed and passed;
VIOLATED when an expected case observed a contradiction - a ``<failure>`` of an assertion type
(``org.opentest4j.AssertionFailedError``, ``java.lang.AssertionError``, ...) in a class that references candidate
code, or an ``<error>`` whose stack trace passes through a candidate main class (the candidate threw) and whose type is
not a linkage/class-loading error; everything else (identity not executed, skipped, harness error without a candidate
frame, linkage errors) is INDETERMINATE, never VIOLATED.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import uuid
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from kriya.tools import test_execution

JVM_ACCEPTANCE_CONTRACT_VERSION = 1
JVM_RUNNER = "maven"

ACCEPTANCE_TRUST_SURFACE_CHANGED = "ACCEPTANCE_TRUST_SURFACE_CHANGED"
ACCEPTANCE_TRUST_SURFACE_UNAVAILABLE = "ACCEPTANCE_TRUST_SURFACE_UNAVAILABLE"
ACCEPTANCE_PATH_COLLISION = "ACCEPTANCE_PATH_COLLISION"
ACCEPTANCE_CANDIDATE_COMPILE_FAILED = "ACCEPTANCE_CANDIDATE_COMPILE_FAILED"
ACCEPTANCE_HARNESS_COMPILE_FAILED = "ACCEPTANCE_HARNESS_COMPILE_FAILED"

_PACKAGE = re.compile(r"^\s*package\s+([A-Za-z_][\w.]*)\s*;", re.M)
_TYPE = re.compile(r"^\s*(?:(?:public|protected|private|final|abstract|static|sealed|non-sealed|strictfp)\s+)*"
                   r"(class|interface|enum|record|@interface)\s+([A-Za-z_]\w*)", re.M)
_MARKER = re.compile(r"^\s*//\s*kriya_requirement\s*:\s*(.+?)\s*$")
_TEST = re.compile(r"^\s*@(?:org\.junit\.jupiter\.api\.)?Test\s*$")
_FORBIDDEN = re.compile(r"@(?:org\.junit\.(?:jupiter\.api\.|jupiter\.params\.)?)?(?:Disabled\w*|Enabled\w*|Ignore|"
                        r"ParameterizedTest|RepeatedTest|TestFactory|TestTemplate|Nested|Tag|Timeout|"
                        r"ExtendWith|RegisterExtension)\b")
_METHOD = re.compile(r"^\s*(?:(?:public|protected|private|static|final)\s+)*void\s+([A-Za-z_]\w*)\s*\(\s*\)")
_REQ_ID = re.compile(r"^REQ-C?\d+$")
_ASSERTION_TYPES = ("org.opentest4j.AssertionFailedError", "org.opentest4j.MultipleFailuresError",
                    "java.lang.AssertionError", "junit.framework.AssertionFailedError",
                    "junit.framework.ComparisonFailure", "org.junit.ComparisonFailure")
_LINKAGE_TYPES = ("java.lang.NoClassDefFoundError", "java.lang.ClassNotFoundException", "java.lang.NoSuchMethodError",
                  "java.lang.NoSuchFieldError", "java.lang.LinkageError", "java.lang.ExceptionInInitializerError",
                  "java.lang.UnsatisfiedLinkError", "java.lang.IncompatibleClassChangeError",
                  "java.lang.AbstractMethodError", "java.lang.VerifyError", "java.lang.ClassFormatError")
_FRAME = re.compile(r"^\s*at\s+(?:[\w.]+/)?([\w.$]+)\.[\w$<>]+\(", re.M)
_COPY_IGNORE = (".git", ".kriya", "target", "build", ".gradle", "node_modules")
_FAILED_GOAL = re.compile(r"Failed to execute goal (\S+)")
_STAGING_DIR = "acceptance-runs"

RUNNER_SOURCE_DIGEST = hashlib.sha256(
    f"jvm/{JVM_ACCEPTANCE_CONTRACT_VERSION}/{JVM_RUNNER}/ordinary-maven-test-gate/-Dtest=<Class>".encode()
).hexdigest()


def parse_java_acceptance(source: bytes) -> Tuple[Tuple[Any, ...], Dict[str, str]]:
    """The cases of a Java acceptance class (each with the requirements it
    proves) and its ``{"package", "class_name", "injection_path"}``."""
    from kriya.workflow.acceptance_oracle import (
        ACCEPTANCE_ARTIFACT_INVALID,
        AcceptanceCase,
        AcceptanceError,
    )

    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as error:
        raise AcceptanceError(ACCEPTANCE_ARTIFACT_INVALID, f"not UTF-8: {error}") from error
    packages = _PACKAGE.findall(text)
    if len(packages) != 1:
        raise AcceptanceError(ACCEPTANCE_ARTIFACT_INVALID, "exactly one package declaration is required")
    types = _TYPE.findall(text)
    if len(types) != 1 or types[0][0] != "class":
        raise AcceptanceError(ACCEPTANCE_ARTIFACT_INVALID,
                              "exactly one type declaration, a class, is required (nested types are not supported)")
    forbidden = sorted(set(_FORBIDDEN.findall(text)))
    if forbidden:
        raise AcceptanceError(ACCEPTANCE_ARTIFACT_INVALID,
                              "annotations that could skip, repeat, generate or extend cases are not allowed: "
                              + ", ".join(forbidden))
    package, class_name = packages[0], types[0][1]
    cases: List[Any] = []
    pending: Optional[List[str]] = None
    is_test = False
    for number, line in enumerate(text.splitlines(), start=1):
        marker = _MARKER.match(line)
        if marker:
            ids = [part.strip() for part in marker.group(1).split(",") if part.strip()]
            if not ids or not all(_REQ_ID.match(rid) for rid in ids):
                raise AcceptanceError(ACCEPTANCE_ARTIFACT_INVALID,
                                      f"line {number}: kriya_requirement takes REQ ids, e.g. 'REQ-1, REQ-2'")
            pending = ids
            continue
        if _TEST.match(line):
            is_test = True
            continue
        method = _METHOD.match(line)
        if method is None:
            continue
        name = method.group(1)
        if is_test and not pending:
            raise AcceptanceError(ACCEPTANCE_ARTIFACT_INVALID,
                                  f"{class_name}.{name}: every @Test case must name the requirement it proves "
                                  "('// kriya_requirement: REQ-n' above it)")
        if pending and not is_test:
            raise AcceptanceError(ACCEPTANCE_ARTIFACT_INVALID,
                                  f"{class_name}.{name}: kriya_requirement marks only @Test methods")
        if is_test:
            cases.append(AcceptanceCase(tuple(sorted(set(pending))), f"{package}.{class_name}.{name}", number))
        pending, is_test = None, False
    if pending or is_test:
        raise AcceptanceError(ACCEPTANCE_ARTIFACT_INVALID, "a marker or @Test is not followed by a void test method")
    identities = [case.identity for case in cases]
    duplicates = sorted({identity for identity in identities if identities.count(identity) > 1})
    if duplicates:
        raise AcceptanceError(ACCEPTANCE_ARTIFACT_INVALID, "duplicate case names: " + ", ".join(duplicates))
    if not cases:
        raise AcceptanceError(ACCEPTANCE_ARTIFACT_INVALID, "no @Test case found")
    injection = "/".join(["src", "test", "java", *package.split("."), f"{class_name}.java"])
    return tuple(cases), {"package": package, "class_name": class_name, "injection_path": injection}


# ------------------------------------------------------------ execution

def _state_root() -> str:
    from kriya.core.state_paths import ENV_STATE_DIR, default_state_directory

    return os.path.realpath(os.path.expanduser(os.environ.get(ENV_STATE_DIR) or default_state_directory()))


def _file_digest(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return None


def _main_classes(root: str) -> Set[str]:
    """Fully-qualified names of the candidate's main classes (every module's
    ``src/main/java``)."""
    found: Set[str] = set()
    for directory, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _COPY_IGNORE]
        rel = os.path.relpath(directory, root).replace(os.sep, "/")
        marker = "src/main/java"
        index = rel.find(marker)
        if index < 0 or (index > 0 and rel[index - 1] != "/"):
            continue
        package = rel[index + len(marker):].strip("/").replace("/", ".")
        for name in files:
            if name.endswith(".java"):
                found.add(f"{package}.{name[:-5]}" if package else name[:-5])
    return found


def _main_digest(root: str) -> str:
    entries = []
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _COPY_IGNORE)
        for name in sorted(files):
            path = os.path.join(directory, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if "src/main/" in f"/{rel}" or rel.startswith("src/main/"):
                entries.append(f"{rel}\0{_file_digest(path)}")
    return hashlib.sha256("\n".join(entries).encode()).hexdigest()


def _case_details(workspace: str, report: test_execution.TestExecutionReport) -> Optional[Dict[str, List[Dict[str, str]]]]:
    """identity -> [{"status", "type", "trace"}] from the report files FS-1A
    digested for this invocation (each re-verified), or None."""
    details: Dict[str, List[Dict[str, str]]] = {}
    for entry in report.report_files:
        path = os.path.join(workspace, entry["path"])
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError:
            return None
        if hashlib.sha256(data).hexdigest() != entry.get("sha256"):
            return None
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            return None
        for testcase in root.iter():
            if testcase.tag.rsplit("}", 1)[-1] != "testcase":
                continue
            identity = test_execution._identity(testcase.get("classname") or "", testcase.get("name") or "")  # pylint: disable=protected-access
            status, kind, trace = test_execution.PASSED, "", ""
            for child in testcase:
                tag = child.tag.rsplit("}", 1)[-1]
                if tag in ("failure", "error", "skipped"):
                    status = {"failure": test_execution.FAILED, "error": test_execution.ERROR,
                              "skipped": test_execution.SKIPPED}[tag]
                    kind, trace = child.get("type") or "", child.text or ""
                    break
            details.setdefault(identity, []).append({"status": status, "type": kind, "trace": trace})
    return details


def run_java_acceptance(
    artifact: Any, candidate_root: str, *, candidate_paths: Iterable[str], base_revision: Optional[str],
    validator_factory: Callable[[str], Any],
) -> Any:
    """Check the trust surface on the candidate, then run the bound operator
    class once in a fresh Kriya-owned copy of the candidate."""
    from kriya.workflow.acceptance_oracle import AcceptanceError, AcceptanceRun, read_stored
    from kriya.workflow.named_test_oracle import BaseTree, OracleSurface, changed_surface, surface_digest

    run = AcceptanceRun()
    run.language = "java"
    injection = artifact.java["injection_path"]
    candidate_paths = list(candidate_paths)
    try:
        source = read_stored(artifact)
        if not os.path.isfile(os.path.join(candidate_root, "pom.xml")):
            raise AcceptanceError("ACCEPTANCE_RUNNER_UNSUPPORTED",
                                  "JVM acceptance supports Maven projects (pom.xml at the root) only")
        if not base_revision:
            raise AcceptanceError(ACCEPTANCE_TRUST_SURFACE_UNAVAILABLE, "no authorized base revision")
        try:
            base = BaseTree(candidate_root, base_revision)
        except Exception as error:  # an unreadable base is no trust anchor
            raise AcceptanceError(ACCEPTANCE_TRUST_SURFACE_UNAVAILABLE,
                                  f"base revision unreadable: {type(error).__name__}") from error
        surface = OracleSurface(base, candidate_root, ())
        base_digests, candidate_digests = surface.digests()
        changed = changed_surface(base_digests, candidate_digests) + surface.output_root_writes(candidate_paths)
        if changed:
            raise AcceptanceError(ACCEPTANCE_TRUST_SURFACE_CHANGED,
                                  "JVM build/test configuration differs from the authorized base: "
                                  + ", ".join(sorted(set(changed))))
        run.trust_surface_digest = surface_digest(candidate_digests)
        if injection in base.paths or os.path.lexists(os.path.join(candidate_root, injection)):
            raise AcceptanceError(ACCEPTANCE_PATH_COLLISION,
                                  f"{injection} already exists in the repository or the candidate; never overwritten")
    except AcceptanceError as refusal:
        run.refusal = refusal
        return run
    stage = os.path.join(_state_root(), _STAGING_DIR, uuid.uuid4().hex)
    export = os.path.join(stage, "workspace")
    try:
        os.makedirs(stage)
        shutil.copytree(candidate_root, export, symlinks=True, ignore=shutil.ignore_patterns(*_COPY_IGNORE))
        run.candidate_digest = _main_digest(export)
        run.candidate_classes = sorted(_main_classes(export))
        validator = validator_factory(export)
        if getattr(validator, "stack", None) != "java":
            run.refusal = AcceptanceError("ACCEPTANCE_RUNNER_UNSUPPORTED",
                                          f"not a JVM project (stack: {getattr(validator, 'stack', None)})")
            return run
        compiled = validator.run_compile_check([p for p in candidate_paths if p.endswith(".java")] or ["pom.xml"])
        if not (isinstance(compiled, dict) and compiled.get("success")):
            run.refusal = AcceptanceError(ACCEPTANCE_CANDIDATE_COMPILE_FAILED,
                                          "the candidate's own main code does not compile (an ordinary candidate "
                                          "failure, not acceptance evidence)")
            return run
        target = os.path.join(export, injection)
        if os.path.lexists(target):
            run.refusal = AcceptanceError(ACCEPTANCE_PATH_COLLISION, f"{injection} appeared during the compile")
            return run
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "xb") as handle:
            handle.write(source)
        run.result = validator.run_tests(target_test=injection) or {}
        run.report = test_execution.report_from_result(run.result)
        if run.report is not None and run.report.complete:
            run.case_details = _case_details(export, run.report)
        if _file_digest(target) != artifact.digest:
            run.integrity_problem = ("ACCEPTANCE_ARTIFACT_CHANGED", "the injected acceptance class changed during the run")
        elif _main_digest(export) != run.candidate_digest:
            run.integrity_problem = ("ACCEPTANCE_CANDIDATE_CHANGED_DURING_RUN",
                                     "candidate main sources changed while the acceptance class ran")
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return run


# ------------------------------------------------------------ judgment

def _contradiction(entries: Sequence[Dict[str, str]], candidate_classes: Set[str], references_candidate: bool) -> bool:
    for entry in entries:
        kind = entry.get("type") or ""
        if entry.get("status") == test_execution.FAILED and kind in _ASSERTION_TYPES and references_candidate:
            return True
        if entry.get("status") == test_execution.ERROR and kind not in _LINKAGE_TYPES:
            frames = {frame.split("$", 1)[0] for frame in _FRAME.findall(entry.get("trace") or "")}
            if frames & candidate_classes:
                return True  # the candidate's own code threw
    return False


def judge_java_acceptance(artifact: Any, run: Any, base: Dict[str, Any]) -> Dict[str, Any]:
    from kriya.workflow.acceptance_oracle import (
        ACCEPTANCE_EVIDENCE_INDETERMINATE,
        ACCEPTANCE_FAILED_WITHOUT_CONTRADICTION,
        ACCEPTANCE_IDENTITY_NOT_EXECUTED,
        ACCEPTANCE_PASSED,
        ACCEPTANCE_VIOLATED,
        AcceptanceError,
        AcceptanceJudgment,
    )

    base = {**base, "runner": JVM_RUNNER, "contract": JVM_ACCEPTANCE_CONTRACT_VERSION,
            "runner_contract_digest": RUNNER_SOURCE_DIGEST, "trust_surface_digest": run.trust_surface_digest,
            "injection_path": artifact.java["injection_path"]}

    def every(code: str, reason: str) -> Dict[str, Any]:
        return {rid: AcceptanceJudgment(code, reason, {**base, "cases": artifact.identities_for(rid)})
                for rid in artifact.requirement_ids}

    if run.refusal is not None:
        return every(run.refusal.reason_code, run.refusal.message)
    if run.integrity_problem is not None:
        return every(*run.integrity_problem)
    report = run.report
    if report is None or not report.complete or report.runner != JVM_RUNNER:
        output = str((run.result or {}).get("output") or "")
        class_file = os.path.basename(artifact.java["injection_path"])
        if "COMPILATION ERROR" in output and class_file in output:
            return every(ACCEPTANCE_HARNESS_COMPILE_FAILED,
                         "the operator acceptance class did not compile against the candidate (harness integration, "
                         "not a requirement violation)")
        reason = "no structured report" if report is None else (report.reason or f"runner {report.runner}")
        rejected = _FAILED_GOAL.search(output)
        if rejected:
            # e.g. a validate-phase check (Apache RAT license headers) rejected the
            # build before any test ran: the operator class must satisfy the
            # repository's own source rules. Diagnostic only - still no evidence.
            reason += f"; the build failed before the tests ran: {rejected.group(1)}"
        return every(ACCEPTANCE_EVIDENCE_INDETERMINATE, f"acceptance run evidence not complete: {reason}")
    details = run.case_details
    if details is None:
        return every(ACCEPTANCE_EVIDENCE_INDETERMINATE, "the report files could not be re-read against their digests")
    statuses = report.statuses()
    if any(sorted(e["status"] for e in details.get(identity, ())) != sorted(found)
           for identity, found in statuses.items()):
        return every(ACCEPTANCE_EVIDENCE_INDETERMINATE, "report and per-case detail disagree")
    candidate_classes = set(run.candidate_classes or ())
    simple = {name.rsplit(".", 1)[-1] for name in candidate_classes}
    try:
        source = artifact_source_text(artifact)
    except AcceptanceError:
        source = ""
    references = bool(simple & set(re.findall(r"\b[A-Z]\w*\b", source)))
    judgments: Dict[str, Any] = {}
    for rid in artifact.requirement_ids:
        identities = artifact.identities_for(rid)
        evidence = {**base, "cases": identities,
                    "case_results": {identity: sorted(statuses.get(identity, ())) for identity in identities}}
        contradicted = [identity for identity in identities
                        if _contradiction(details.get(identity, ()), candidate_classes, references)]
        missing = [identity for identity in identities if identity not in statuses]
        not_passed = [identity for identity in identities
                      if identity in statuses and not report.passed(identity)]
        if contradicted:
            judgments[rid] = AcceptanceJudgment(ACCEPTANCE_VIOLATED, "acceptance case(s) observed the behaviour "
                                                "contradicted: " + ", ".join(contradicted), evidence)
        elif missing:
            judgments[rid] = AcceptanceJudgment(ACCEPTANCE_IDENTITY_NOT_EXECUTED,
                                                "expected case(s) did not execute: " + ", ".join(missing), evidence)
        elif not_passed:
            judgments[rid] = AcceptanceJudgment(ACCEPTANCE_FAILED_WITHOUT_CONTRADICTION,
                                                "case(s) did not pass, but not as an observed contradiction of the "
                                                "behaviour: " + ", ".join(not_passed), evidence)
        else:
            judgments[rid] = AcceptanceJudgment(ACCEPTANCE_PASSED, "", evidence)
    return judgments


def artifact_source_text(artifact: Any) -> str:
    from kriya.workflow.acceptance_oracle import read_stored

    return read_stored(artifact).decode("utf-8", errors="strict")
