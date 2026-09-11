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

import os
import re
import shlex
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from kriya.tools.containment import ContainmentBackend, ContainmentProfile, NetworkAuthority, TrustClass
from kriya.tools.containment_oci import MAVEN_CACHE_MOUNT, PIP_CACHE_MOUNT
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


def _classify_maven_offline_failure(result: ProcessResult) -> Optional[OfflineFailureKind]:
    if result.returncode == 0 and not result.timeout:
        return None
    if _MAVEN_OFFLINE_MISSING_RE.search(result.stdout + result.stderr):
        return OfflineFailureKind.MISSING_DEPENDENCY
    return OfflineFailureKind.ORDINARY_FAILURE


def _classify_pip_offline_failure(result: ProcessResult) -> Optional[OfflineFailureKind]:
    if result.returncode == 0 and not result.timeout:
        return None
    if _PIP_OFFLINE_MISSING_RE.search(result.stdout + result.stderr):
        return OfflineFailureKind.MISSING_DEPENDENCY
    return OfflineFailureKind.ORDINARY_FAILURE


def _acquisition_profile(workspace_path: str, cache_path: str, env_allowlist: List[str]) -> ContainmentProfile:
    return ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION,
        workspace_path=workspace_path,
        dependency_cache_paths=[cache_path],
        dependency_cache_writable=True,
        network=NetworkAuthority.UNRESTRICTED,
        env_allowlist=env_allowlist,
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
    containment_backend: ContainmentBackend, timeout: int = 600, env_allowlist: Optional[List[str]] = None,
) -> ProcessResult:
    """Phase 1: `mvn dependency:go-offline` populates `cache_path` (mounted
    as Maven's own local repository, /root/.m2, by OCIContainmentBackend's
    own image/cache-path detection) - real network access, still fully
    filesystem/process-contained. Does NOT guarantee every subsequent
    offline build will succeed (a plugin invoked only during a later
    lifecycle phase, e.g. `package`, may not be resolved by
    `dependency:go-offline` alone) - that is `maven_execute_offline`'s own
    `OfflineFailureKind.MISSING_DEPENDENCY` evidence to surface, not
    something this phase can guarantee away."""
    profile = _acquisition_profile(workspace_path, cache_path, env_allowlist or [])
    return controller.run(
        ["mvn", "-B", f"-Dmaven.repo.local={MAVEN_CACHE_MOUNT}", "dependency:go-offline"],
        cwd=workspace_path, timeout=timeout,
        containment_profile=profile, containment_backend=containment_backend,
    )


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
    containment_backend: ContainmentBackend, timeout: int = 600, env_allowlist: Optional[List[str]] = None,
) -> ProcessResult:
    """Phase 1: `pip download` into `cache_path` (mounted at pip's own
    cache location by OCIContainmentBackend). Prefers wheels only
    (`--only-binary=:all:`) so arbitrary sdist build_backend hooks are
    never executed during acquisition; a package with no wheel available
    falls back to a plain download (which MAY execute its own build
    backend as part of producing a downloadable artifact) - that fallback
    attempt still runs through the exact same containment as every other
    untrusted command here (filesystem/process-contained throughout,
    network open only because this IS the acquisition phase), never
    "trusted" and never uncontained, per this module's own docstring."""
    profile = _acquisition_profile(workspace_path, cache_path, env_allowlist or [])
    wheels_only = controller.run(
        ["python3", "-m", "pip", "download", "--dest", PIP_CACHE_MOUNT, "--only-binary=:all:", "-r", requirements_path],
        cwd=workspace_path, timeout=timeout,
        containment_profile=profile, containment_backend=containment_backend,
    )
    if wheels_only.returncode == 0:
        return wheels_only
    return controller.run(
        ["python3", "-m", "pip", "download", "--dest", PIP_CACHE_MOUNT, "-r", requirements_path],
        cwd=workspace_path, timeout=timeout,
        containment_profile=profile, containment_backend=containment_backend,
    )


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
