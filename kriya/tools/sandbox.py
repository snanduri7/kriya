import logging
import os
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from kriya.platform.services import platform_services

logger = logging.getLogger(__name__)


def build_restricted_env(allowlist: List[str]) -> Dict[str, str]:
    """Builds a subprocess environment containing only allowlisted variable names
    from the current process environment (plus PATH, always included).

    This blocks the common case of a subprocess inheriting secrets (API keys,
    cloud credentials, SSH agent sockets, etc.) that happen to be sitting in the
    parent shell environment. It does not stop a subprocess from reading
    credential files directly off disk, or from reaching the network - it only
    narrows what's handed to it via the environment.
    """
    restricted = {"PATH": os.environ.get("PATH", "")}
    for key in allowlist:
        if key in os.environ:
            restricted[key] = os.environ[key]
    return restricted


def posix_resource_limits_preexec_fn(
    cpu_seconds: Optional[int], memory_mb: Optional[int]
) -> Optional[Callable[[], None]]:
    """A preexec_fn that caps CPU time and/or address space for a
    subprocess, from the host's ResourceLimitPort (kriya/platform/; POSIX
    setrlimit on macOS and Linux, with RLIMIT_AS advisory on macOS - see
    kriya/platform/posix_resource_limits.py). None for one dimension leaves
    it untouched; pass None, never 0. A failure while applying a limit
    fails the spawn (SEC-001 fail-closed). A host that cannot apply a
    requested limit refuses with ResourceLimitSetupError, never an
    unbounded run (PLAT-005)."""
    from kriya.platform.capabilities import PlatformCapabilityUnavailable

    try:
        return platform_services().resource_limits.preexec_fn(cpu_seconds, memory_mb)
    except PlatformCapabilityUnavailable as error:
        from kriya.tools.containment import ResourceLimitSetupError

        raise ResourceLimitSetupError(str(error)) from error


# --- Resource strategy (LINUX-JVM-RLIMIT-AS-001) ---------------------------------
#
# RLIMIT_AS bounds VIRTUAL address space. A JVM reserves far more address
# space than it ever touches (the heap's maximum, the compressed-class space,
# the code cache, per-thread stacks), so on Linux, where RLIMIT_AS is
# enforced, a JVM under the sandbox's memory budget dies at startup ("There
# is insufficient memory for the Java Runtime Environment to continue") even
# though its real use would fit. macOS never showed it: there RLIMIT_AS is
# advisory-only (see posix_resource_limits_preexec_fn). A JVM is bounded the
# way the JVM itself is bounded instead: explicit heap, metaspace and direct-
# memory maxima derived from the same budget, carried in JAVA_TOOL_OPTIONS,
# which every JVM in the process tree reads (the build tool's own JVM and
# every JVM it forks: surefire/failsafe test forks, the Gradle daemon).
# CPU time, the process-tree kill on timeout, containment and every other
# limit are unchanged. Every other command keeps RLIMIT_AS.

ADDRESS_SPACE = "address_space"
JVM_HEAP = "jvm_heap"
UNBOUNDED = "unbounded"

# Executables that are JVMs or start one, matched on argv[0]'s basename only.
# Shell text is never parsed for them: a crafted `sh -c "java; ..."` must not
# be able to shed its RLIMIT_AS.
JVM_LAUNCHERS = frozenset({"java", "javac", "jar", "jshell", "mvn", "mvnw", "gradle", "gradlew", "sbt", "kotlinc"})

# The heap maximum of each JVM is half the budget: the build tool's JVM and
# one forked test JVM together stay within it. Metaspace and direct memory
# are capped from the same budget. Below the minimum no JVM can be bounded
# usefully, and the command is refused rather than run unbounded.
JVM_HEAP_SHARE = 2
JVM_MIN_BUDGET_MB = 256
JVM_METASPACE_MIN_MB = 128
JVM_METASPACE_MAX_MB = 512
JVM_DIRECT_MEMORY_SHARE = 8
JAVA_TOOL_OPTIONS = "JAVA_TOOL_OPTIONS"


def is_jvm_execution(command: Optional[Sequence[str]], language: Optional[str] = None) -> bool:
    """A JVM-backed execution: the toolchain the caller resolved is Java, or
    the executable is a known JVM launcher."""
    if language == "java":
        return True
    return bool(command) and os.path.basename(str(command[0])) in JVM_LAUNCHERS


@dataclass(frozen=True)
class ResourcePlan:
    """How one host process's CPU and memory budget is enforced."""

    strategy: str
    cpu_seconds: Optional[int]
    memory_budget_mb: Optional[int]
    jvm_options: Tuple[str, ...] = ()

    def preexec_fn(self) -> Optional[Callable[[], None]]:
        address_space_mb = self.memory_budget_mb if self.strategy == ADDRESS_SPACE else None
        if self.cpu_seconds is None and address_space_mb is None:
            return None
        return posix_resource_limits_preexec_fn(self.cpu_seconds, address_space_mb)

    def apply_env(self, env: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
        """``env`` with the JVM bounds added (None stays "inherit" when there
        are none). Kriya's options come last, so they win over any the
        environment already carries."""
        if not self.jvm_options:
            return env
        resolved = dict(env) if env is not None else dict(os.environ)
        existing = resolved.get(JAVA_TOOL_OPTIONS, "").strip()
        resolved[JAVA_TOOL_OPTIONS] = " ".join(filter(None, (existing, *self.jvm_options)))
        return resolved

    def evidence(self) -> Dict[str, object]:
        return {
            "strategy": self.strategy,
            "cpu_seconds": self.cpu_seconds,
            "memory_budget_mb": self.memory_budget_mb,
            "address_space_limit_mb": self.memory_budget_mb if self.strategy == ADDRESS_SPACE else None,
            # PLAT-006: how the host enforces that limit (macOS: advisory).
            "address_space_enforcement": (
                platform_services().resource_limits.address_space_enforcement().value
                if self.strategy == ADDRESS_SPACE else None),
            "jvm_options": list(self.jvm_options),
        }


def jvm_memory_options(memory_budget_mb: int) -> Tuple[str, ...]:
    metaspace = min(JVM_METASPACE_MAX_MB, max(JVM_METASPACE_MIN_MB, memory_budget_mb // 8))
    return (
        f"-Xmx{memory_budget_mb // JVM_HEAP_SHARE}m",
        f"-XX:MaxMetaspaceSize={metaspace}m",
        f"-XX:MaxDirectMemorySize={memory_budget_mb // JVM_DIRECT_MEMORY_SHARE}m",
    )


def resource_plan(
    command: Optional[Sequence[str]], cpu_seconds: Optional[int], memory_mb: Optional[int],
    *, language: Optional[str] = None,
) -> ResourcePlan:
    """The resource strategy for one host process. None for a limit keeps
    the existing contract (that dimension is not limited); a budget that
    cannot be enforced raises ResourceLimitSetupError, never runs unbounded."""
    from kriya.tools.containment import ResourceLimitSetupError

    if memory_mb is not None and memory_mb <= 0:
        raise ResourceLimitSetupError(f"invalid memory budget {memory_mb!r} MB: it must be positive")
    if memory_mb is None:
        return ResourcePlan(UNBOUNDED, cpu_seconds, None)
    if not is_jvm_execution(command, language):
        return ResourcePlan(ADDRESS_SPACE, cpu_seconds, memory_mb)
    if memory_mb < JVM_MIN_BUDGET_MB:
        raise ResourceLimitSetupError(
            f"memory budget {memory_mb} MB is below the {JVM_MIN_BUDGET_MB} MB a bounded JVM needs; "
            "refusing to run the JVM unbounded")
    return ResourcePlan(JVM_HEAP, cpu_seconds, memory_mb, jvm_memory_options(memory_mb))
