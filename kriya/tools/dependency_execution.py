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

import logging
import re
import shlex
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Sequence, Tuple

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
    r"|Cannot access central",
    re.IGNORECASE,
)
_PIP_OFFLINE_MISSING_RE = re.compile(
    r"Could not find a version that satisfies the requirement"
    r"|No matching distribution found"
    r"|WARNING: Retrying.*after connection broken",
    re.IGNORECASE,
)


def classify_maven_offline_failure_text(combined_output: str) -> OfflineFailureKind:
    """The text-only half of `_classify_maven_offline_failure`, factored
    out (SEC-001-P6 Stage 2, 2026-09-11) so a caller with its own
    dict-shaped result (kriya/tools/validate.py's `_run_cmd_with_timeout`,
    which predates - and is not itself migrated onto -
    `kriya.tools.process.ProcessResult`) can reuse the SAME real,
    empirically-verified Maven offline-error patterns instead of
    duplicating the regex. Callers decide success/failure themselves first
    (this function only classifies WHY a known failure happened)."""
    if _MAVEN_OFFLINE_MISSING_RE.search(combined_output):
        return OfflineFailureKind.MISSING_DEPENDENCY
    return OfflineFailureKind.ORDINARY_FAILURE


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
