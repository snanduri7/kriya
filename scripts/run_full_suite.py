#!/usr/bin/env python3
"""The canonical full deterministic test suite - one repository-supported
entry point instead of shell folklore (TEST-HARNESS-001, 2026-10-07).

    scripts/run_full_suite.py [--preflight-only] [--workers N] [PYTEST ARGS...]

What a certified full-suite run needs (every one measured to matter, see
handover/LR_R1_M1_FINAL_CERTIFICATION.md and the 2026-10-07 classification
in ~/kriya-m1-live/qas001-certification):
- the repository's own venv (.venv) with pytest and pytest-xdist;
- the repository root on the import path (tests import `tests._strict_doubles`;
  pyproject.toml's `pythonpath = ["."]` owns that for pytest itself, and the
  harness exports PYTHONPATH for any child process a test spawns);
- `ulimit -n 256` - the certified invocation's file-descriptor limit, applied
  to the pytest process here (RLIMIT_NOFILE, POSIX only);
- a reachable Docker daemon: the real-container tests skip without one, which
  is a silently incomplete run (certification mode even fails them);
- `jdtls`, `mvn` and `java` on PATH for the real-JDTLS and Maven tests;
- on macOS, no x86_64-only JVM under /Library/Java/JavaVirtualMachines when
  Rosetta is absent: JDTLS's Maven importer execs every installed JVM and
  gives up on the whole project when one cannot start
  (incident 2026-10-07, macOS 27 removed Rosetta 2);
- `-n 8 --dist loadgroup`: the repository's worker/distribution contract
  (tests/conftest.py pins real-Docker tests to one worker).
The default live-tier exclusions come from pyproject.toml's addopts and are
never restated here. A missing prerequisite is reported and the run refused
(exit 2); nothing is installed, skipped or silenced. After pytest the
repository root is checked for the TEST-WORKTREE-POLLUTION-001 artifacts; a
new one fails the run (exit 3) even when every test passed.
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from typing import Callable, Dict, Iterable, List, Optional, Sequence

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_WORKERS = 8
NOFILE_LIMIT = 256
REQUIRED_TOOLS = ("docker", "jdtls", "mvn", "java")
JVM_DIR = "/Library/Java/JavaVirtualMachines"
# Keep equal to tests/_worktree_pollution.py (a test asserts it).
PACKAGE_ARTIFACT_NAMES = ("package.json", "package-lock.json", "node_modules")

Runner = Callable[[Sequence[str]], int]


def venv_python(root: str) -> str:
    return os.path.join(root, ".venv", "Scripts" if os.name == "nt" else "bin",
                        "python.exe" if os.name == "nt" else "python")


def _run(command: Sequence[str]) -> int:
    try:
        return subprocess.run(list(command), capture_output=True, timeout=60).returncode
    except (OSError, subprocess.TimeoutExpired):
        return 1


def jvm_java_binaries(jvm_dir: str = JVM_DIR) -> List[str]:
    if not os.path.isdir(jvm_dir):
        return []
    return sorted(path for name in os.listdir(jvm_dir)
                  for path in [os.path.join(jvm_dir, name, "Contents", "Home", "bin", "java")]
                  if os.path.isfile(path))


def macos_foreign_jvms(java_binaries: Iterable[str], archs_of: Callable[[str], str],
                       rosetta_present: Callable[[], bool]) -> List[str]:
    """The installed JVMs this CPU cannot run: no arm64 slice and no Rosetta."""
    foreign = [java for java in java_binaries if "arm64" not in archs_of(java).split()]
    return foreign if foreign and not rosetta_present() else []


def _lipo_archs(java: str) -> str:
    result = subprocess.run(["lipo", "-archs", java], capture_output=True, text=True, timeout=30)
    return result.stdout if result.returncode == 0 else "arm64"  # unreadable: not this check's finding


def _rosetta_present() -> bool:
    return _run(["arch", "-x86_64", "/usr/bin/true"]) == 0


def preflight(root: str = ROOT, *, which: Callable[[str], Optional[str]] = shutil.which, run: Runner = _run,
              system: str = platform.system(), machine: str = platform.machine(),
              java_binaries: Optional[Iterable[str]] = None,
              archs_of: Callable[[str], str] = _lipo_archs,
              rosetta_present: Callable[[], bool] = _rosetta_present,
              nofile_hard_limit: Optional[int] = None) -> List[str]:
    """Every unmet prerequisite, as one sentence each; empty means run."""
    problems: List[str] = []
    python = venv_python(root)
    if not os.access(python, os.X_OK):
        problems.append(f"no repository venv at {python} (python3 -m venv .venv && .venv/bin/pip install -e '.[dev]')")
    elif run([python, "-c", "import pytest, xdist"]) != 0:
        problems.append("pytest or pytest-xdist is not importable from the repository venv")
    for tool in REQUIRED_TOOLS:
        if which(tool) is None:
            problems.append(f"`{tool}` is not on PATH (required by the real-Docker, JDTLS and Maven tests)")
    if which("docker") is not None and run(["docker", "info"]) != 0:
        problems.append("the Docker daemon is not reachable (`docker info` failed): start Docker Desktop")
    if system == "Darwin" and machine == "arm64":
        binaries = list(jvm_java_binaries() if java_binaries is None else java_binaries)
        for java in macos_foreign_jvms(binaries, archs_of, rosetta_present):
            problems.append(f"{java} has no arm64 slice and Rosetta is absent: JDTLS's Maven import fails on it "
                            "(move the JVM out of /Library/Java/JavaVirtualMachines; see "
                            "handover/claude-context and the 2026-10-07 incident)")
    if nofile_hard_limit is None and os.name == "posix":
        import resource

        nofile_hard_limit = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
    if nofile_hard_limit is not None and nofile_hard_limit != resource_unlimited(nofile_hard_limit) \
            and nofile_hard_limit < NOFILE_LIMIT:
        problems.append(f"the hard open-file limit {nofile_hard_limit} is below the required {NOFILE_LIMIT}")
    return problems


def resource_unlimited(value: int) -> int:
    return value if value < 0 else -1


def pytest_command(root: str, workers: int, extra: Sequence[str] = ()) -> List[str]:
    return [venv_python(root), "-m", "pytest", "-q", "-n", str(workers), "--dist", "loadgroup", *extra]


def child_environment(root: str, environ: Dict[str, str]) -> Dict[str, str]:
    env = dict(environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = root if not existing else os.pathsep.join([root, existing])
    return env


def present_package_artifacts(root: str) -> List[str]:
    return [name for name in PACKAGE_ARTIFACT_NAMES if os.path.lexists(os.path.join(root, name))]


def _apply_nofile_limit() -> None:
    import resource

    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (min(NOFILE_LIMIT, hard) if hard >= 0 else NOFILE_LIMIT, hard))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--preflight-only", action="store_true", help="check prerequisites and print the command")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help=f"xdist workers (default {DEFAULT_WORKERS})")
    args, extra = parser.parse_known_args(argv)
    if extra[:1] == ["--"]:
        extra = extra[1:]
    problems = preflight(ROOT)
    for problem in problems:
        print(f"[full-suite] PREREQUISITE: {problem}", file=sys.stderr)
    if problems:
        print(f"[full-suite] refused: {len(problems)} unmet prerequisite(s); nothing was run", file=sys.stderr)
        return 2
    command = pytest_command(ROOT, args.workers, extra)
    env = child_environment(ROOT, os.environ)
    print(f"[full-suite] cwd       {ROOT}")
    print(f"[full-suite] PYTHONPATH={env['PYTHONPATH']}")
    print(f"[full-suite] ulimit -n {NOFILE_LIMIT}")
    print(f"[full-suite] command   {' '.join(command)}")
    if args.preflight_only:
        return 0
    before = present_package_artifacts(ROOT)
    if before:
        print(f"[full-suite] note: {', '.join(before)} already present in the repository root before the run")
    if os.name == "posix":
        _apply_nofile_limit()
    sys.stdout.flush()
    returncode = subprocess.call(command, cwd=ROOT, env=env)
    created = [name for name in present_package_artifacts(ROOT) if name not in before]
    if created:
        print(f"[full-suite] TEST-WORKTREE-POLLUTION-001: the run created {', '.join(created)} in {ROOT}",
              file=sys.stderr)
        return 3
    print(f"[full-suite] pytest exit {returncode}; repository root gained no package artifacts")
    return returncode


if __name__ == "__main__":
    sys.exit(main())
