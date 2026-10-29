"""SEC-001-P6: the approved two-phase dependency-execution concept -
`controlled acquisition -> cache -> network-denied execution` - implemented
for Maven and Python, sufficient to support contained real builds under
`OCIContainmentBackend` without ever needing network access at the point
untrusted repo content (a pom.xml's plugins, a package's build backend) is
actually built/run.

Deliberately modular and thin: this module ONLY constructs the right
commands and `ContainmentProfile`s and runs them through the existing
`ProcessController`/`ContainmentBackend` machinery (kriya/tools/process.py,
kriya/tools/containment.py) - it is not a package manager, and it does not
duplicate any containment/execution logic already owned by those modules
(Invariant 14).

Two phases, always:
1. ACQUISITION - network=UNRESTRICTED (the one place this module allows
   network access at all), still UNTRUSTED_EXECUTION and still routed
   through the SAME containment backend as everything else (filesystem/
   process controls stay in force even while fetching) - because parsing
   a hostile repo's own pom.xml/build backend during dependency resolution
   already runs SOME of that repo's own code (Maven plugins declared in
   the pom, a Python sdist's build_backend hook), this phase is never
   "trusted", only "network-permitted".
2. EXECUTION - network=DENIED, using ONLY what acquisition already cached.
   A missing dependency/plugin here produces deterministic, typed evidence
   (`DependencyExecutionOutcome.offline_failure_kind`) rather than an
   unrestricted network fallback - the caller decides whether to re-run
   acquisition, this module never does so silently.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shlex
import shutil
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence

from kriya.config.environment import toolchain, user_home
from kriya.tools.containment import ContainmentBackend, ContainmentProfile, NetworkAuthority, TrustClass
from kriya.tools.containment_oci import (
    MAVEN_CACHE_MOUNT,
    PIP_CACHE_MOUNT,
    finalize_registry_acquisition_result,
)
from kriya.tools.process import ProcessController, ProcessResult


class OfflineFailureKind(str, Enum):
    """Distinguishes WHY an offline execution failed - deterministic
    evidence a caller (validate.py's retry loop, a future workflow-layer
    consumer) can act on, never silently swallowed into a generic
    command-failed result."""

    # Maven/pip's own offline-mode error text indicates a dependency or
    # plugin that acquisition did not fetch - re-running acquisition (with
    # network) is the controlled, evidence-driven next step.
    MISSING_DEPENDENCY = "missing_dependency"
    # The command failed for a reason that has nothing to do with offline
    # mode/missing dependencies (a real compile error, a failing test,
    # etc.) - reacquisition would not help.
    ORDINARY_FAILURE = "ordinary_failure"


@dataclass(frozen=True)
class DependencyExecutionOutcome:
    result: ProcessResult
    offline_failure_kind: Optional[OfflineFailureKind]

    @property
    def succeeded(self) -> bool:
        return self.result.returncode == 0 and not self.result.timeout


_MAVEN_OFFLINE_MISSING_RE = re.compile(
    r"in offline mode|was cached in the local repository, resolution will not be reattempted"
    r"|Cannot access central"
    # D2: a plugin prefix (e.g. `exec`) resolves only from plugin metadata,
    # which an offline run cannot fetch (measured: `mvn ... exec:exec`).
    r"|No plugin found for prefix",
    re.IGNORECASE,
)
_PIP_OFFLINE_MISSING_RE = re.compile(
    r"Could not find a version that satisfies the requirement"
    r"|No matching distribution found"
    r"|WARNING: Retrying.*after connection broken",
    re.IGNORECASE,
)


# W2-16a (Step 3): Maven reports a located POM-model problem in a line that QUOTES the candidate's own value (a packaging, a module name, an id, a coordinate, the text around a
# parser fault). Resolution-looking words inside such a line are the candidate's text, not Maven's report that a dependency could not be obtained, so they are no evidence for
# the resolution / acquisition decisions (MEASURED: a packaging of "... in offline mode" made `_run_maven_cmd` run a registry acquisition and write the
# `MAVEN_ACQUISITION_INCOMPLETE:` marker, which the POM-validate classifier then trusted). CLOSED table of the measured Maven openings, anchored at the start of a Maven
# `[ERROR]`/`[FATAL]`/`[WARNING]` line (the level marker repeats and is indented; Maven reports a validation WARNING for e.g. an invalid `<scope>` in the same quoted-field shape); an opening not listed here keeps the line in the decision view (fail closed). Maven's own resolution
# openings (`Non-resolvable ... POM`, `Unresolveable build extension`, `Plugin ... could not be resolved`, `Failed to execute goal`, ...) are deliberately NOT in it, and
# neither is `Internal error:`.
_MAVEN_MODEL_PROBLEM_WRAPPER_RE = re.compile(
    r"^(?:\[(?:ERROR|FATAL|WARNING)\][ \t]*)+(?:"
    r"Unknown packaging: |Child module |"
    r"'[A-Za-z][\w.\[\]:\-]*' (?:with value|for) |"
    r"The project .* \(.+\) has \d+ errors?$|"
    r"(?:Malformed|Non-parseable) POM )"
)


def maven_resolution_decision_view(output: str) -> str:
    """The text VIEW the Maven resolution / acquisition decisions read: ``output`` without the lines of ``_MAVEN_MODEL_PROBLEM_WRAPPER_RE``. It only drops lines for those
    decisions; the captured output is the caller's to preserve and is never replaced by this view. Known residual (fail closed): a candidate value that itself contains a newline
    can place a forged ``[ERROR] Non-resolvable ...`` line in the view, which is a stop, never a pass or a conviction."""
    return "\n".join(line for line in output.splitlines() if not _MAVEN_MODEL_PROBLEM_WRAPPER_RE.match(line))


def classify_maven_offline_failure_text(combined_output: str) -> OfflineFailureKind:
    """The text-only half of `_classify_maven_offline_failure`, factored
    out (SEC-001-P6 Stage 2, 2026-09-11) so a caller with its own
    dict-shaped result (kriya/tools/validate.py's `_run_cmd_with_timeout`,
    which predates - and is not itself migrated onto -
    `kriya.tools.process.ProcessResult`) can reuse the SAME real,
    empirically-verified Maven offline-error patterns instead of
    duplicating the regex. Callers decide success/failure themselves first
    (this function only classifies WHY a known failure happened)."""
    if _MAVEN_OFFLINE_MISSING_RE.search(maven_resolution_decision_view(combined_output)):
        return OfflineFailureKind.MISSING_DEPENDENCY
    return OfflineFailureKind.ORDINARY_FAILURE


# --- Gradle (GRADLE-WRAPPER-CONTAINMENT-001) ---------------------------------------
#
# Typed environment reason codes a Gradle gate result may carry
# (``environment_reason_code``): the verification tool could not start, so
# nothing about the candidate was observed - never a code failure.
GRADLE_DISTRIBUTION_UNAVAILABLE = "GRADLE_DISTRIBUTION_UNAVAILABLE"
# FINITE-START-FACT-UNCONSUMED-001: the contained Gradle launcher itself (the project's `./gradlew`, or `gradle`) never started - the backend's typed never-started fact, never an exit status or text.
GRADLE_INVOCATION_FAILED = "GRADLE_INVOCATION_FAILED"
# VERIFICATION-UNIT-ENV-FALLBACK-001 (BACKEND-FINAL-CLOSURE-005): the Python
# gate's twin - the project's required verification environment (its
# .kriya/venv) could not be created, so no interpreter may produce an
# authoritative verdict for the workspace; never a substitute interpreter.
PYTHON_ENVIRONMENT_UNAVAILABLE = "PYTHON_ENVIRONMENT_UNAVAILABLE"
# F7 (Wave-1 closure delta; owner decision): the Maven POM-validation gate's twins. The invariant is `no trustworthy validator verdict -> no candidate conviction`:
# `pom_semantic_validation` is reserved for a Maven that RAN and rejected the POM with trustworthy model diagnostics; every other failure to obtain a verdict is an
# environment outcome (`environment_reason_code`), raised as the existing `verification_infrastructure_failure`.
MAVEN_INVOCATION_FAILED = "MAVEN_INVOCATION_FAILED"                        # `mvn` could not be started (missing / not executable / spawn OSError)
MAVEN_VALIDATE_TIMED_OUT = "MAVEN_VALIDATE_TIMED_OUT"                      # started, did not finish within its bound
MAVEN_VALIDATE_TERMINATED = "MAVEN_VALIDATE_TERMINATED"                    # killed by a signal without a verdict (not our timeout)
MAVEN_VALIDATE_RESOLUTION_UNVERIFIED = "MAVEN_VALIDATE_RESOLUTION_UNVERIFIED"  # parent / plugin / dependency resolution or transfer failed: no trustworthy POM verdict
MAVEN_VALIDATE_NO_DIAGNOSTICS = "MAVEN_VALIDATE_NO_DIAGNOSTICS"            # non-zero exit with no interpretable POM-model diagnostics
MAVEN_VALIDATE_OPERATOR_CONFIG_INVALID = "MAVEN_VALIDATE_OPERATOR_CONFIG_INVALID"  # Maven refused its OWN configuration (settings / toolchains) before it read the POM
# F9 (Wave-2; owner decision D6): the compile gate's twins of the same invariant - no compile path returns PASS unless a trustworthy compiler established it. A compile
# adapter that met an exception or an uninterpretable result observed nothing about the candidate: an environment outcome, never a PASS and never a candidate conviction.
MAVEN_COMPILE_INDETERMINATE = "MAVEN_COMPILE_INDETERMINATE"                # the Maven compile step raised, or its result could not be interpreted
GRADLE_COMPILE_INDETERMINATE = "GRADLE_COMPILE_INDETERMINATE"              # the Gradle compile step raised, or its result could not be interpreted
# Slice F (COMPILE-GATE-CONVICTION-WITHOUT-VERDICT-001; owner decision 2026-10-28): source compilation is convicted ONLY from a positively located compiler diagnostic
# (see classify_compile_failure). A build tool that ran, failed and left no such diagnostic observed nothing about the candidate source. Compile-specific names: the
# MAVEN_VALIDATE_* codes describe the POM-validation gate and keep their own semantics (W2-16 is separate).
MAVEN_COMPILE_TIMED_OUT = "MAVEN_COMPILE_TIMED_OUT"                        # started, did not finish within its bound
GRADLE_COMPILE_TIMED_OUT = "GRADLE_COMPILE_TIMED_OUT"
MAVEN_COMPILE_TERMINATED = "MAVEN_COMPILE_TERMINATED"                      # killed by a signal (host: no exit status; contained: a 128+signal exit such as 137) with no located diagnostic
GRADLE_COMPILE_TERMINATED = "GRADLE_COMPILE_TERMINATED"
MAVEN_COMPILE_OPERATOR_CONFIG_INVALID = "MAVEN_COMPILE_OPERATOR_CONFIG_INVALID"  # Maven refused its OWN configuration (settings / toolchains), a document the candidate did not write
MAVEN_COMPILE_RESOLUTION_UNVERIFIED = "MAVEN_COMPILE_RESOLUTION_UNVERIFIED"      # dependency / plugin resolution, acquisition or transfer failed: ownership of the failure is NOT inferred from text
GRADLE_COMPILE_RESOLUTION_UNVERIFIED = "GRADLE_COMPILE_RESOLUTION_UNVERIFIED"
MAVEN_COMPILE_NO_DIAGNOSTICS = "MAVEN_COMPILE_NO_DIAGNOSTICS"              # non-zero exit with no located source diagnostic (empty output, a bare summary, unrelated text)
GRADLE_COMPILE_NO_DIAGNOSTICS = "GRADLE_COMPILE_NO_DIAGNOSTICS"
# COMPILE-GATE-RAW-JAVAC-CONVICTION-WITHOUT-VERDICT-001 (owner decision 2026-10-28): the raw-javac fallback's twins of the same invariant. javac has no resolution phase; its only
# source evidence is a diagnostic located at one of the exact source paths it was given (see classify_javac_failure). Compile-specific names: no Maven / Gradle code is reused.
JAVAC_INVOCATION_FAILED = "JAVAC_INVOCATION_FAILED"                        # `javac` could not be started (the backend's never-started fact, or a spawn OSError)
JAVAC_COMPILE_INDETERMINATE = "JAVAC_COMPILE_INDETERMINATE"                # the process result could not be interpreted (no integer return code, no stderr text)
JAVAC_COMPILE_TIMED_OUT = "JAVAC_COMPILE_TIMED_OUT"                        # started, did not finish within its bound, and printed no located diagnostic
JAVAC_COMPILE_TERMINATED = "JAVAC_COMPILE_TERMINATED"                      # killed by a signal (host: no exit status; contained: a 128+signal exit such as 137) with no located diagnostic
JAVAC_COMPILE_NO_DIAGNOSTICS = "JAVAC_COMPILE_NO_DIAGNOSTICS"              # non-zero exit with no located diagnostic (empty output, a usage error, a JVM that did not start)
COMPILE_NO_VERDICT_CODES = frozenset({
    MAVEN_COMPILE_TIMED_OUT, GRADLE_COMPILE_TIMED_OUT, MAVEN_COMPILE_TERMINATED, GRADLE_COMPILE_TERMINATED, MAVEN_COMPILE_OPERATOR_CONFIG_INVALID,
    MAVEN_COMPILE_RESOLUTION_UNVERIFIED, GRADLE_COMPILE_RESOLUTION_UNVERIFIED, MAVEN_COMPILE_NO_DIAGNOSTICS, GRADLE_COMPILE_NO_DIAGNOSTICS,
    JAVAC_COMPILE_TIMED_OUT, JAVAC_COMPILE_TERMINATED, JAVAC_COMPILE_NO_DIAGNOSTICS})
# The codes of a build tool that DID start or produced no interpretable verdict (the gate words them "returned no trustworthy verdict" instead of "could not start").
MAVEN_VALIDATE_NO_VERDICT_CODES = frozenset({MAVEN_VALIDATE_TIMED_OUT, MAVEN_VALIDATE_TERMINATED, MAVEN_VALIDATE_RESOLUTION_UNVERIFIED, MAVEN_VALIDATE_NO_DIAGNOSTICS,
                                             MAVEN_VALIDATE_OPERATOR_CONFIG_INVALID, MAVEN_COMPILE_INDETERMINATE, GRADLE_COMPILE_INDETERMINATE,
                                             JAVAC_COMPILE_INDETERMINATE}) | COMPILE_NO_VERDICT_CODES


def compile_environment_result(code: str, output: str) -> Dict[str, Any]:
    """The compile gate's typed "no trustworthy verdict" result (F9): ``success`` False plus the ``environment_reason_code`` the gates already consume
    (``_stop_on_environment_gate_result`` raises it as ``verification_infrastructure_failure``). Never a PASS, never a candidate conviction."""
    return {"success": False, "environment_reason_code": code, "output": output}


def compile_no_verdict_result(validation_result: Dict[str, Any], command_result: Mapping[str, Any]) -> Dict[str, Any]:
    """Slice F: a compile gate result (built by ``_validation_result`` with the typed code) plus the diagnostics of the run that gave no verdict - its exit status, whether it
    timed out and the acquisition phase's bounded record (``acquisition_evidence``: useful evidence, truncated, NOT authority for ownership). Never a PASS, never a conviction."""
    result = dict(validation_result)
    result["returncode"] = command_result.get("returncode")
    if command_result.get("timeout"):
        result["timed_out"] = True
    if command_result.get("acquisition_evidence") is not None:
        result["acquisition_evidence"] = command_result["acquisition_evidence"]
    return result

# Resolution / transport diagnostics, as real Maven 3.9 prints them (recorded: tests/fixtures/maven_validate and the live logs they cite). Any of them makes the POM verdict untrustworthy,
# because a model problem reported next to an unresolved parent / BOM / plugin is usually a cascade of the failed resolution (the recorded env-T2 output shows exactly that).
_MAVEN_RESOLUTION_OR_TRANSPORT_RE = re.compile(
    r"Could not transfer|Could not find artifact|in offline mode|Non-resolvable (?:parent|import) POM|Unresolveable build extension|could not be resolved|"
    r"Could not resolve (?:dependencies|plugin|artifact)|Failed to read artifact descriptor|Failed to process POM for|Unable to get dependency information|"
    r"MAVEN_ACQUISITION_INCOMPLETE|(?:Connect|Read) timed out|Connection (?:refused|reset)|Unknown ?[Hh]ost|Temporary failure in name resolution|"
    r"PKIX path|SSLHandshake|Network is unreachable|No route to host|status code: \d{3}|Transfer failed|Failure to transfer|Checksum validation failed|nodename nor servname"
)
# Maven's diagnostics about its OWN operator-level documents (the user / global settings and the toolchains file), recorded in tests/fixtures/maven_validate/recorded_operator_config.json:
# `[ERROR] 1 problem was encountered while building the effective settings` / `[FATAL] Non-parseable settings <file>: Duplicated tag ... @ <file>, line 2, column 54`. They share the
# located-problem shape (`Duplicated tag`, `@ ..., line N, column M`) of a POM-model problem, but name a document the candidate did not write and the Developer cannot repair.
# Anchored to the start of a Maven log line: Maven also echoes candidate bytes (`position: ... seen ...<!-- ... -->`) and candidate paths into the same stream, so the same words inside a
# POM's own comment, URL or module directory name (all measured, recorded as pom_defect_*) must not reclassify a genuine POM defect. Maven reads `.mvn/maven.config`, so a candidate that
# points `-s` at a settings file it wrote is also seen here as an operator document: no repair is offered, and the run stops (fail closed) rather than convicting a document the POM check
# never judged.
_MAVEN_OPERATOR_CONFIG_RE = re.compile(
    r"^\[(?:ERROR|FATAL)\] (?:Non-(?:parseable|readable) (?:settings|toolchains)\b|\d+ problems? (?:was|were) encountered while building the effective (?:settings|toolchains))", re.MULTILINE)
# Maven's own POM-model diagnostics (a located model problem, a malformed / non-parseable document, an unknown packaging, ...).
_MAVEN_POM_MODEL_PROBLEM_RE = re.compile(
    r"Non-parseable POM|Malformed POM|Expected root element|Unrecognised tag|Duplicated tag|Unknown packaging|Invalid packaging|Invalid POM|"
    r"@ line \d+, column \d+|Child module .{1,300} does not exist"
)


def classify_maven_validate_failure(returncode: int, timed_out: bool, combined_output: str) -> Optional[str]:
    """Why a failed (non-zero) ``mvn validate`` may NOT convict the POM: the environment reason code, or None when Maven ran and rejected the POM with trustworthy model diagnostics.

    Order: a timeout and a signal kill carry no verdict; Maven's own refusal of an operator document (settings / toolchains) is not a POM verdict; resolution / transport evidence (checked BEFORE model problems - a cascade is not a verdict) makes any model message
    untrustworthy; only then do located POM-model diagnostics convict; a non-zero exit with neither is uninterpretable. Captured output is the caller's to preserve."""
    if timed_out:
        return MAVEN_VALIDATE_TIMED_OUT
    if returncode < 0:
        return MAVEN_VALIDATE_TERMINATED
    if _MAVEN_OPERATOR_CONFIG_RE.search(combined_output):
        return MAVEN_VALIDATE_OPERATOR_CONFIG_INVALID
    if (classify_maven_offline_failure_text(combined_output) is OfflineFailureKind.MISSING_DEPENDENCY
            or _MAVEN_RESOLUTION_OR_TRANSPORT_RE.search(maven_resolution_decision_view(combined_output))):
        return MAVEN_VALIDATE_RESOLUTION_UNVERIFIED
    if _MAVEN_POM_MODEL_PROBLEM_RE.search(combined_output):
        return None
    return MAVEN_VALIDATE_NO_DIAGNOSTICS
# --- Slice F: the compile-gate classifier ---------------------------------------------------------------------------------------------------------------------------------
# The compiler's verdict on the SOURCE is the only thing that may convict it. Maven prints one `[ERROR] <abs path>.java:[line,col] <message>` line per javac error; Gradle's javac
# output is `<abs path>.java:<line>: error: <message>`. Both are anchored to the start of a build-output line: Maven's own `[ERROR] ` prefix, and for Gradle (whose javac output
# carries no prefix and echoes the offending source line raw) an absolute path that is never `//`, so a source line javac echoes cannot pose as one. A `[WARNING] ...java:[l,c]`
# line is not a conviction and is not suppression either. A summary line (`COMPILATION ERROR`,
# `Compilation failure`) is printed by the build tool whether or not a located diagnostic exists, so it supports located evidence but never substitutes for it.
_MAVEN_LOCATED_SOURCE_ERROR_RE = re.compile(r"^\[ERROR\] (?!\s)[^\n]*?\.java:\[\d+,\d+\] \S", re.MULTILINE)
_GRADLE_LOCATED_SOURCE_ERROR_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|/(?!/))[^\n]*?\.java:\d+: error: \S", re.MULTILINE)
# Error-level dependency / plugin resolution, acquisition and transfer failures (Maven resolves them before it compiles, so they never coexist with a located source error).
# The existing resolution/transport pattern plus the wordings the measured Maven 3.9 adds: a cached failure ("was not found in <repo> during a previous attempt"), the
# dependency-collection variant and an unavailable ("present, but unavailable") artifact. Applied to `[ERROR]` lines only: a `[WARNING] Could not transfer metadata` is noise.
_MAVEN_COMPILE_RESOLUTION_RE = re.compile(
    _MAVEN_RESOLUTION_OR_TRANSPORT_RE.pattern
    + r"|Could not resolve|Could not collect dependencies|was not found in .{1,400} during a previous attempt|\(present, but unavailable\)|Cannot access .{1,400} in offline mode"
)
_MAVEN_ACQUISITION_MARKER_RE = re.compile(r"^MAVEN_ACQUISITION_INCOMPLETE:", re.MULTILINE)
# Gradle has no log levels: anchored to the start of its failure-report lines (`> Could not resolve ...`, `Could not GET ...`, ...).
_GRADLE_COMPILE_RESOLUTION_RE = re.compile(
    r"^[ >*]*(?:Could not (?:resolve|GET|HEAD|POST|PUT|download|determine the dependencies)|No cached version of|Cannot resolve external dependency|"
    r"Plugin \[id: [^\n]*was not found|Failed to (?:resolve|transfer)|(?:java\.net\.)?(?:UnknownHostException|ConnectException|SocketTimeoutException)|"
    r"(?:javax\.net\.ssl\.)?SSLHandshakeException|PKIX path|(?:Connect|Read) timed out|Connection (?:refused|reset)|Network is unreachable|No route to host)"
    r"|^GRADLE_ACQUISITION_INCOMPLETE:|^GRADLE_DISTRIBUTION_UNAVAILABLE", re.MULTILINE)


def _has_resolution_failure(build_system: str, output: str) -> bool:
    if build_system == "gradle":
        return bool(_GRADLE_COMPILE_RESOLUTION_RE.search(output))
    view = maven_resolution_decision_view(output)
    if _MAVEN_ACQUISITION_MARKER_RE.search(view):
        return True
    return any(_MAVEN_COMPILE_RESOLUTION_RE.search(line) for line in view.splitlines()
               if line.startswith("[ERROR]") and not _MAVEN_LOCATED_SOURCE_ERROR_RE.match(line))


def classify_compile_failure(build_system: str, returncode: int, timed_out: bool, combined_output: str) -> Optional[str]:
    """Why a failed (non-zero) ``mvn``/``gradle`` compile may NOT convict the candidate's source: the environment reason code, or None when positively located compiler
    evidence shows the source was judged (a source failure).

    Order: a timeout and a host signal kill (no exit status) carry no verdict; Maven's refusal of its own operator documents is not a source verdict; error-level
    resolution / acquisition / transfer failures are NO verdict (whether a bad coordinate or an absent cache entry caused them is not inferred from text); only then does a
    located source diagnostic convict - whatever the exit status, so a build tool killed after javac reported (a contained 128+signal exit) still convicts what javac judged
    - and a non-zero exit with no located diagnostic is NO verdict (a contained signal-style exit is named as terminated). Captured output is the caller's to preserve."""
    gradle = build_system == "gradle"
    if timed_out:
        return GRADLE_COMPILE_TIMED_OUT if gradle else MAVEN_COMPILE_TIMED_OUT
    if returncode < 0:
        return GRADLE_COMPILE_TERMINATED if gradle else MAVEN_COMPILE_TERMINATED
    if not gradle and _MAVEN_OPERATOR_CONFIG_RE.search(combined_output):
        return MAVEN_COMPILE_OPERATOR_CONFIG_INVALID
    if _has_resolution_failure(build_system, combined_output):
        return GRADLE_COMPILE_RESOLUTION_UNVERIFIED if gradle else MAVEN_COMPILE_RESOLUTION_UNVERIFIED
    located = _GRADLE_LOCATED_SOURCE_ERROR_RE if gradle else _MAVEN_LOCATED_SOURCE_ERROR_RE
    if located.search(combined_output):
        return None
    if returncode in _POSIX_SIGNAL_EXIT_CODE_RANGE:
        return GRADLE_COMPILE_TERMINATED if gradle else MAVEN_COMPILE_TERMINATED
    return GRADLE_COMPILE_NO_DIAGNOSTICS if gradle else MAVEN_COMPILE_NO_DIAGNOSTICS


def classify_javac_failure(returncode: int, timed_out: bool, stderr: str, source_paths: Sequence[str]) -> Optional[str]:
    """Why a failed (non-zero) raw ``javac`` compile may NOT convict the candidate's source: the environment reason code, or None when javac positively judged the source.

    Only a diagnostic LOCATED AT ONE OF THE EXACT SOURCE PATHS this invocation was given convicts: a ``stderr`` line that begins with ``<path>:<line>: error:``. javac prints each
    path as it was passed, and echoes an offending source line verbatim, so the anchor is the line start plus exact membership in ``source_paths`` (measured: a forged
    ``Other.java:99: error:`` echoed from a text block, a forged ``N error`` trailer and a file named like a diagnostic never pose as a judgement of a passed path; the
    ``error: file not found: <name>`` form starts with ``error:``). Neither the exit status (a JVM that did not start also exits 1) nor the ``N error(s)`` trailer (forgeable, and absent
    from a killed run) is evidence.

    Order: a located diagnostic convicts first - what javac judged is not erased by a later timeout or signal; then a timeout; then a signal kill (host: no exit status; contained: 128+signal);
    then any other non-zero exit is NO verdict. The caller has already ruled out a never-started javac and an uninterpretable result."""
    if source_paths and re.search(r"^(?:" + "|".join(re.escape(path) for path in source_paths) + r"):\d+: error:", stderr, re.MULTILINE):
        return None
    if timed_out:
        return JAVAC_COMPILE_TIMED_OUT
    if returncode < 0 or returncode in _POSIX_SIGNAL_EXIT_CODE_RANGE:
        return JAVAC_COMPILE_TERMINATED
    return JAVAC_COMPILE_NO_DIAGNOSTICS


# The offline run still misses a dependency/plugin after one bounded,
# registry-scoped acquisition (the Maven marker's Gradle twin; a marker in
# the output, repair-eligible like Maven's: a candidate may have declared
# a coordinate that does not exist).
GRADLE_ACQUISITION_INCOMPLETE_MARKER = "GRADLE_ACQUISITION_INCOMPLETE:"

# The wrapper's own stage, before any Gradle code runs: it tries to obtain
# its distribution (MEASURED, cohort T3: "Downloading https://services.
# gradle.org/distributions/gradle-8.10.1-bin.zip" then
# "java.net.UnknownHostException: services.gradle.org" at
# org.gradle.wrapper.Install.forceFetch).
# Anchored on the wrapper's install/download stage only: the launcher frames
# (GradleWrapperMain, WrapperExecutor, BootstrapMainStarter) appear in any
# build error trace printed with --stacktrace and prove nothing (review).
_GRADLE_WRAPPER_START_RE = re.compile(
    r"org\.gradle\.wrapper\.(?:Install|Download)\b|Downloading https?://\S+/distributions/"
    r"|Could not (?:install|download) Gradle distribution|Distribution .* could not be (?:downloaded|installed)",
)
_GRADLE_OFFLINE_MISSING_RE = re.compile(
    r"Could not resolve all (?:files|dependencies|artifacts) for|No cached version of .* available for offline mode"
    r"|Plugin \[id: .*\] was not found|Could not download .* (?:from|in) offline mode|available for offline mode",
    re.IGNORECASE,
)


def gradle_wrapper_start_failed(combined_output: str) -> bool:
    """The wrapper never started Gradle: its distribution could not be
    obtained (the text is the wrapper's own, before any build script runs)."""
    return bool(_GRADLE_WRAPPER_START_RE.search(combined_output or ""))


def classify_gradle_offline_failure_text(combined_output: str) -> OfflineFailureKind:
    """MISSING_DEPENDENCY when Gradle's offline run named something it has
    not cached (a dependency, a plugin, or the wrapper's distribution);
    ORDINARY_FAILURE for anything else (a compile error, a failing test)."""
    text = combined_output or ""
    if gradle_wrapper_start_failed(text) or _GRADLE_OFFLINE_MISSING_RE.search(text):
        return OfflineFailureKind.MISSING_DEPENDENCY
    return OfflineFailureKind.ORDINARY_FAILURE


@dataclass(frozen=True)
class GradleWrapperDistribution:
    """What ``gradle/wrapper/gradle-wrapper.properties`` declares."""

    url: str
    name: str            # e.g. gradle-8.10.1-bin
    version: str         # e.g. 8.10.1
    url_hash: str        # Gradle's own distribution directory name for ``url``
    sha256: Optional[str]  # distributionSha256Sum, when declared


_WRAPPER_PROPERTIES = os.path.join("gradle", "wrapper", "gradle-wrapper.properties")
_DIST_NAME_RE = re.compile(r"^gradle-(\d+(?:\.\d+)*(?:-[a-z0-9]+)?)-(?:bin|all)$")


def gradle_distribution_hash(url: str) -> str:
    """Gradle's own name for a distribution's directory under
    ``wrapper/dists/<name>/``: the MD5 of the distribution URL as an unsigned
    base-36 integer (org.gradle.wrapper.PathAssembler.getHash; MEASURED
    against the host cache: gradle-8.10.1-bin.zip -> e90i968nv55tch01zkse4avv3)."""
    value = int.from_bytes(hashlib.md5(url.encode("utf-8")).digest(), "big")  # noqa: S324 - Gradle's own scheme, not security
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while value:
        value, rem = divmod(value, 36)
        out = digits[rem] + out
    return out or "0"


def parse_gradle_wrapper_properties(workspace_path: str) -> Optional[GradleWrapperDistribution]:
    """The wrapper distribution the project itself declares, or None when
    the project ships no wrapper properties, declares no distribution URL,
    or stores it anywhere but the default ``GRADLE_USER_HOME/wrapper/dists``
    (then Kriya does not know where the wrapper will look)."""
    path = os.path.join(workspace_path, _WRAPPER_PROPERTIES)
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return None
    values: Dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith(("#", "!")) or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().replace("\\:", ":").replace("\\=", "=")
    url = values.get("distributionUrl", "")
    if not url:
        return None
    for key, default in (("distributionBase", "GRADLE_USER_HOME"), ("distributionPath", "wrapper/dists"),
                         ("zipStoreBase", "GRADLE_USER_HOME"), ("zipStorePath", "wrapper/dists")):
        if values.get(key, default) != default:
            return None
    name = url.rsplit("/", 1)[-1]
    if not name.endswith(".zip"):
        return None
    name = name[:-4]
    match = _DIST_NAME_RE.match(name)
    if match is None:
        return None
    sha256 = values.get("distributionSha256Sum") or None
    return GradleWrapperDistribution(url=url, name=name, version=match.group(1), url_hash=gradle_distribution_hash(url),
                                     sha256=sha256.lower() if sha256 else None)


def _sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def seed_gradle_distribution_from_host(
    workspace_path: str, cache_dir: str, *, host_gradle_home: Optional[str] = None,
) -> Dict[str, Any]:
    """GRADLE-WRAPPER-CONTAINMENT-001, owner-decided order step 2: before any
    network, copy the project's declared wrapper distribution from the host
    user's own Gradle cache into the Kriya-managed Gradle home when - and
    only when - its identity is verified: the host directory is Gradle's
    own ``wrapper/dists/<name>/<md5-base36(url)>/`` for the exact declared
    URL, carries the wrapper's ``<name>.zip.ok`` completion marker and the
    extracted ``gradle-<version>/lib`` matching the URL's version; when the
    project declares ``distributionSha256Sum`` the zip must be present and
    match it, else the seed is refused (nothing is ever accepted on a name
    alone, no checksum is invented). Returns a provenance record (recorded
    in gate evidence): status seeded / present / no_wrapper /
    not_in_host_cache / unverifiable / refused, with the URL, hash, source
    and what was verified. Copies, never links; the host cache is read only."""
    distribution = parse_gradle_wrapper_properties(workspace_path)
    if distribution is None:
        return {"status": "no_wrapper"}
    record: Dict[str, Any] = {"url": distribution.url, "name": distribution.name, "url_hash": distribution.url_hash,
                              "declared_sha256": distribution.sha256}
    target = os.path.join(cache_dir, "wrapper", "dists", distribution.name, distribution.url_hash)
    marker = f"{distribution.name}.zip.ok"
    extracted = f"gradle-{distribution.version}"
    if os.path.isfile(os.path.join(target, marker)) and os.path.isdir(os.path.join(target, extracted, "lib")):
        return {**record, "status": "present", "source": target}
    home = host_gradle_home or toolchain("GRADLE_USER_HOME") or os.path.join(user_home(), ".gradle")
    source = os.path.join(home, "wrapper", "dists", distribution.name, distribution.url_hash)
    if not os.path.isdir(source):
        return {**record, "status": "not_in_host_cache", "source": source}
    verified = ["url_hash_directory"]
    if not os.path.isfile(os.path.join(source, marker)):
        return {**record, "status": "unverifiable", "source": source, "reason": f"no {marker} completion marker"}
    verified.append("completion_marker")
    if not os.path.isdir(os.path.join(source, extracted, "lib")):
        return {**record, "status": "unverifiable", "source": source,
                "reason": f"no extracted {extracted}/lib for the declared version"}
    verified.append("version_directory")
    zip_path = os.path.join(source, f"{distribution.name}.zip")
    strength = "shape"  # Gradle's own layout: url-hash directory, completion marker, extracted version (no digest)
    if distribution.sha256:
        if not os.path.isfile(zip_path):
            return {**record, "status": "unverifiable", "source": source,
                    "reason": "distributionSha256Sum is declared but the host cache keeps no zip to verify"}
        actual = _sha256_of(zip_path)
        if actual != distribution.sha256:
            return {**record, "status": "refused", "source": source,
                    "reason": f"host zip sha256 {actual} != declared {distribution.sha256}"}
        verified.append("sha256")
        strength = "declared_sha256"
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.isdir(target):
            shutil.rmtree(target)
        shutil.copytree(os.path.join(source, extracted), os.path.join(target, extracted), symlinks=False)
        with open(os.path.join(target, marker), "wb") as handle:
            handle.write(b"")
    except OSError as error:
        shutil.rmtree(target, ignore_errors=True)
        return {**record, "status": "unverifiable", "source": source, "reason": f"copy failed: {error}"}
    return {**record, "status": "seeded", "source": source, "target": target, "verified": verified,
            "verification_strength": strength}


def classify_pip_offline_failure_text(combined_output: str) -> OfflineFailureKind:
    """Text-only half of `_classify_pip_offline_failure` - see
    `classify_maven_offline_failure_text`'s own docstring for why."""
    if _PIP_OFFLINE_MISSING_RE.search(combined_output):
        return OfflineFailureKind.MISSING_DEPENDENCY
    return OfflineFailureKind.ORDINARY_FAILURE


_MAVEN_MISSING_ARTIFACT_RE = re.compile(
    r"the artifact ([^\s]+) has not been downloaded"
    r"|Plugin ([^\s]+) or one of its dependencies could not be resolved"
    r"|dependency:\s*\n?\s*([^\s(]+)",
)


def maven_missing_artifact_signature(combined_output: str) -> Optional[str]:
    """Extracts the specific artifact/plugin coordinate Maven's own
    offline-mode error text names as unresolvable (e.g.
    'org.junit.jupiter:junit-jupiter:jar:5.10.2') - used ONLY to compare
    two offline failures for "materially identical missing-artifact
    evidence" (kriya/tools/validate.py's bounded-reacquisition deterministic-
    termination check), never to build a static plugin/artifact allowlist.
    Returns None if the text doesn't match any known Maven error shape -
    callers fall back to comparing `OfflineFailureKind` alone in that
    case."""
    m = _MAVEN_MISSING_ARTIFACT_RE.search(combined_output)
    if not m:
        return None
    return next((g for g in m.groups() if g), None)


def _classify_maven_offline_failure(result: ProcessResult) -> Optional[OfflineFailureKind]:
    if result.returncode == 0 and not result.timeout:
        return None
    return classify_maven_offline_failure_text(result.stdout + result.stderr)


def _classify_pip_offline_failure(result: ProcessResult) -> Optional[OfflineFailureKind]:
    if result.returncode == 0 and not result.timeout:
        return None
    return classify_pip_offline_failure_text(result.stdout + result.stderr)


# SEC-007 (2026-09-12): acquisition's own resource authority, separate
# from whatever cpu_seconds/memory_mb a caller applies to TARGET/execution
# profiles (kriya.config.config.AutonomyConfig's own
# acquisition_cpu_seconds/acquisition_memory_mb carry the same defaults
# for validate.py's own, separately-built acquisition profiles - kept as
# plain module constants here since this module has no AutonomyConfig
# reference of its own). Confirmed live, 2026-09-11/12: a target-code
# memory cap tightened to bound hostile application execution (128MB)
# OOM-killed Maven's acquisition-phase JVM resolving a legitimately large
# transitive plugin tree when the SAME cap was reused for acquisition -
# these constants exist so that never happens for THIS module's own
# acquisition functions either.
_ACQUISITION_CPU_SECONDS = 300
_ACQUISITION_MEMORY_MB = 2048

# POSIX convention: a process killed by signal N reports exit status 128+N
# to its parent (128=no signal info available, 129..159 covers every real
# signal number) - the ONLY platform evidence available from a plain
# returncode alone, without inspecting /proc or a platform-specific wait()
# status decomposition this module doesn't otherwise need. Never asserted
# as certain (a genuine `exit(137)` call is indistinguishable from
# SIGKILL(9) this way) - reported as "likely", per OBS-005's own scope
# ("distinguishable where platform evidence permits").
_POSIX_SIGNAL_EXIT_CODE_RANGE = range(129, 160)

_logger = logging.getLogger(__name__)


def log_acquisition_outcome(
    purpose: str, goal_desc: str, *, returncode: Optional[int], timed_out: bool,
) -> None:
    """OBS-005 (2026-09-12): always records a deterministic, diagnosable
    acquisition outcome - exit code, timeout, a best-effort resource-
    termination signal, and the acquisition's own purpose/goal - so a real
    infra-level acquisition failure (an OOM-killed build-tool JVM, the
    SEC-007 incident this exists because of) leaves evidence of its own,
    not just whatever a SEPARATE authoritative offline attempt later
    reports. Takes plain `returncode`/`timed_out` values rather than a
    whole result object on purpose - shared by this module's own
    acquisition functions (which have a real `ProcessResult`) AND
    kriya/tools/validate.py's `_run_maven_cmd`/`_ensure_project_venv`
    (which build their own dict-shaped results) without either side
    needing to match the other's result TYPE, just its two relevant
    field values. Deliberately metadata-only: never logs stdout/stderr
    (verbose, not secret-shaped, but this function's job is a diagnosable
    OUTCOME SUMMARY, not a second copy of the full transcript) and never
    touches environment variables at all - there is nothing to redact
    because nothing here ever reads the environment."""
    timed_out = bool(timed_out)
    if returncode == 0 and not timed_out:
        _logger.info(f"Acquisition ({purpose}, goal={goal_desc!r}): succeeded (exit 0).")
        return
    if timed_out:
        _logger.warning(f"Acquisition ({purpose}, goal={goal_desc!r}): timed out.")
        return
    likely_signal = isinstance(returncode, int) and returncode in _POSIX_SIGNAL_EXIT_CODE_RANGE
    if likely_signal:
        _logger.warning(
            f"Acquisition ({purpose}, goal={goal_desc!r}): exited {returncode} - in the POSIX "
            f"128+signal range, likely terminated by a signal (e.g. OOM/SIGKILL), not an "
            f"ordinary tool-reported failure."
        )
        return
    _logger.warning(f"Acquisition ({purpose}, goal={goal_desc!r}): exited {returncode}.")


def _acquisition_profile(
    workspace_path: str, cache_path: str, env_allowlist: List[str],
    cpu_seconds: Optional[int] = _ACQUISITION_CPU_SECONDS, memory_mb: Optional[int] = _ACQUISITION_MEMORY_MB,
    *, registry_hosts: Sequence[str],
) -> ContainmentProfile:
    """SEC-006: `registry_hosts` is a REQUIRED keyword-only parameter, not
    a default - this module has no `AutonomyConfig` reference of its own
    (by design, see the module docstring), so it can never silently invent
    or widen registry authority on its own; every caller must pass
    whatever `AutonomyConfig.acquisition_registry_hosts` already
    authorizes. An empty sequence is accepted here (the backend itself
    fails closed on an empty `network_destinations` set) rather than
    validated twice."""
    return ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION,
        workspace_path=workspace_path,
        dependency_cache_paths=[cache_path],
        dependency_cache_writable=True,
        network=NetworkAuthority.DEPENDENCY_REGISTRY_ONLY,
        network_destinations=tuple(sorted(set(registry_hosts))),
        env_allowlist=env_allowlist,
        cpu_seconds=cpu_seconds,
        memory_mb=memory_mb,
    )


def _execution_profile(
    workspace_path: str, cache_path: str, env_allowlist: List[str],
    cpu_seconds: Optional[int], memory_mb: Optional[int],
) -> ContainmentProfile:
    # dependency_cache_writable=True even during EXECUTION (empirically
    # required, 2026-09-11): Maven writes internal lock/bookkeeping files
    # into its local repo directory even for a read-mostly offline
    # `validate`/`-o` run - a read-only mount fails with "Read-only file
    # system" on that lock file, not a security-meaningful write. This does
    # NOT reopen "smuggle in new dependencies" - network=DENIED already
    # makes that impossible regardless of mount mode; a writable mount here
    # can only rewrite bookkeeping around content acquisition already
    # legitimately cached.
    return ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION,
        workspace_path=workspace_path,
        dependency_cache_paths=[cache_path],
        dependency_cache_writable=True,
        network=NetworkAuthority.DENIED,
        env_allowlist=env_allowlist,
        cpu_seconds=cpu_seconds,
        memory_mb=memory_mb,
    )


# --- Maven ---

def maven_acquire_dependencies(
    workspace_path: str, cache_path: str, *, controller: ProcessController,
    containment_backend: ContainmentBackend, registry_hosts: Sequence[str],
    timeout: int = 600, env_allowlist: Optional[List[str]] = None,
) -> ProcessResult:
    """Phase 1: `mvn dependency:go-offline` populates `cache_path` (mounted
    as Maven's own local repository at MAVEN_CACHE_MOUNT by
    OCIContainmentBackend's own image/cache-path detection) - real,
    registry-scoped network access (SEC-006: `registry_hosts` is the ONLY
    source of destination authority - the caller's own
    `AutonomyConfig.acquisition_registry_hosts`, never inferred here),
    still fully filesystem/process-contained. Does NOT guarantee every
    subsequent offline build will succeed (a plugin invoked only during a
    later lifecycle phase, e.g. `package`, may not be resolved by
    `dependency:go-offline` alone) - that is `maven_execute_offline`'s own
    `OfflineFailureKind.MISSING_DEPENDENCY` evidence to surface, not
    something this phase can guarantee away."""
    profile = _acquisition_profile(workspace_path, cache_path, env_allowlist or [], registry_hosts=registry_hosts)
    result = controller.run(
        ["mvn", "-B", f"-Dmaven.repo.local={MAVEN_CACHE_MOUNT}", "dependency:go-offline"],
        cwd=workspace_path, timeout=timeout,
        containment_profile=profile, containment_backend=containment_backend,
    )
    # SEC-006: raises RegistryAcquisitionSetupError before this function
    # ever returns a value if the acquisition container's own trusted
    # setup (firewall/proxy/IPv6/privilege-drop) failed - never let that
    # be mistaken for an ordinary `mvn` failure.
    finalize_registry_acquisition_result(result)
    log_acquisition_outcome("maven", "mvn dependency:go-offline", returncode=result.returncode, timed_out=result.timeout)
    return result


def maven_execute_offline(
    workspace_path: str, cache_path: str, goals: List[str], *, controller: ProcessController,
    containment_backend: ContainmentBackend, timeout: int = 300,
    env_allowlist: Optional[List[str]] = None, cpu_seconds: Optional[int] = None, memory_mb: Optional[int] = None,
) -> DependencyExecutionOutcome:
    """Phase 2: `mvn -o <goals>` (offline flag) - network=DENIED, using
    only what acquisition already cached. A missing dependency/plugin
    produces `OfflineFailureKind.MISSING_DEPENDENCY` (Maven's own offline-
    mode error text), never a silent fallback to network access."""
    profile = _execution_profile(workspace_path, cache_path, env_allowlist or [], cpu_seconds, memory_mb)
    result = controller.run(
        ["mvn", "-B", "-o", f"-Dmaven.repo.local={MAVEN_CACHE_MOUNT}", *goals],
        cwd=workspace_path, timeout=timeout,
        containment_profile=profile, containment_backend=containment_backend,
    )
    return DependencyExecutionOutcome(result=result, offline_failure_kind=_classify_maven_offline_failure(result))


# --- Python ---

def python_acquire_dependencies(
    workspace_path: str, requirements_path: str, cache_path: str, *, controller: ProcessController,
    containment_backend: ContainmentBackend, registry_hosts: Sequence[str],
    timeout: int = 600, env_allowlist: Optional[List[str]] = None,
) -> ProcessResult:
    """Phase 1: `pip download` into `cache_path` (mounted at pip's own
    cache location by OCIContainmentBackend). Prefers wheels only
    (`--only-binary=:all:`) so arbitrary sdist build_backend hooks are
    never executed during acquisition; a package with no wheel available
    falls back to a plain download (which MAY execute its own build
    backend as part of producing a downloadable artifact) - that fallback
    attempt still runs through the exact same containment as every other
    untrusted command here (filesystem/process-contained throughout,
    registry-scoped network access only because this IS the acquisition
    phase - SEC-006: `registry_hosts` is the ONLY source of destination
    authority, never inferred here), never "trusted" and never
    uncontained, per this module's own docstring."""
    profile = _acquisition_profile(workspace_path, cache_path, env_allowlist or [], registry_hosts=registry_hosts)
    wheels_only = controller.run(
        ["python3", "-m", "pip", "download", "--dest", PIP_CACHE_MOUNT, "--only-binary=:all:", "-r", requirements_path],
        cwd=workspace_path, timeout=timeout,
        containment_profile=profile, containment_backend=containment_backend,
    )
    finalize_registry_acquisition_result(wheels_only)
    log_acquisition_outcome("python", "pip download --only-binary=:all:", returncode=wheels_only.returncode, timed_out=wheels_only.timeout)
    if wheels_only.returncode == 0:
        return wheels_only
    fallback = controller.run(
        ["python3", "-m", "pip", "download", "--dest", PIP_CACHE_MOUNT, "-r", requirements_path],
        cwd=workspace_path, timeout=timeout,
        containment_profile=profile, containment_backend=containment_backend,
    )
    finalize_registry_acquisition_result(fallback)
    log_acquisition_outcome("python", "pip download (fallback, sdist allowed)", returncode=fallback.returncode, timed_out=fallback.timeout)
    return fallback


_PYDEPS_TARGET_DIR = ".kriya_pydeps"


def python_execute_offline(
    workspace_path: str, cache_path: str, requirements_path: str, command: List[str], *,
    controller: ProcessController, containment_backend: ContainmentBackend, timeout: int = 300,
    env_allowlist: Optional[List[str]] = None, cpu_seconds: Optional[int] = None, memory_mb: Optional[int] = None,
) -> DependencyExecutionOutcome:
    """Phase 2: installs from the local cache only (`--no-index
    --find-links`) then runs `command` - both steps network=DENIED. A
    missing wheel surfaces as `OfflineFailureKind.MISSING_DEPENDENCY`
    (pip's own offline-mode error text) from the install step; `command`
    itself only runs if the offline install succeeded.

    Installs with `--target <workspace>/.kriya_pydeps`, NOT a plain `pip
    install` into the container's own site-packages (empirically required,
    2026-09-11): `OCIContainmentBackend.prepare()` runs EACH
    `controller.run()` call in its OWN fresh, `--rm`-torn-down container -
    a package installed into that ephemeral container's filesystem is gone
    before the next `controller.run()` call (running `command`) ever
    starts. `--target` writes into the workspace bind mount instead, which
    (unlike the container's own rootfs) is the one thing that actually
    persists across the two separate container invocations - `command`
    itself is wrapped in a `/bin/sh -c` invocation that prepends
    `PYTHONPATH` so it can find what was installed there."""
    profile = _execution_profile(workspace_path, cache_path, env_allowlist or [], cpu_seconds, memory_mb)
    install_result = controller.run(
        [
            "python3", "-m", "pip", "install", "--no-index", "--find-links", PIP_CACHE_MOUNT,
            "--target", _PYDEPS_TARGET_DIR, "-r", requirements_path,
        ],
        cwd=workspace_path, timeout=timeout,
        containment_profile=profile, containment_backend=containment_backend,
    )
    install_failure = _classify_pip_offline_failure(install_result)
    if install_failure is not None:
        return DependencyExecutionOutcome(result=install_result, offline_failure_kind=install_failure)

    wrapped_command = [
        "/bin/sh", "-c",
        f"PYTHONPATH={_PYDEPS_TARGET_DIR}:$PYTHONPATH {shlex.join(command)}",
    ]
    run_result = controller.run(
        wrapped_command, cwd=workspace_path, timeout=timeout,
        containment_profile=profile, containment_backend=containment_backend,
    )
    return DependencyExecutionOutcome(result=run_result, offline_failure_kind=None)
