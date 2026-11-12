"""TEST-DOCKER-WORKER-CONTRACT-001: the mechanical tripwire for "a test starts a real container outside the single docker worker".

THE CONTRACT (tests/conftest.py): under `-n N --dist loadgroup` every test that starts real containers runs on ONE worker (xdist group
"docker"). The group is derived from one fact only: a ``skip`` / ``skipif`` marker whose reason names docker (``_needs_real_docker``).

THE ESCAPE (MEASURED 2026-11-11, pre-live gate on b64bd4b): tests/test_prd032_chaos_commit.py's crash tests run under the in-process
containment stand-in, so they carried no docker gate - but each spawns a fresh Python process (the crash subprocess) that has no stand-in
and runs the real OCI backend. The containers were started by a GRANDCHILD of the pytest worker, on any worker, in parallel.

WHAT THIS RECORDS: with ``KRIYA_DOCKER_WORKER_CONTRACT`` set, the session puts a shim named ``docker`` first on PATH. Kriya resolves the
executable with ``shutil.which("docker")`` in whichever process builds the launch, and a child inherits PATH and ``PYTEST_CURRENT_TEST``,
so a start made by any descendant of the test is attributed to the test. The shim appends one line (test, verb) for ``run`` / ``create`` /
``start`` (with the argv, so a census can read the bind sources) and then ``exec``s the real docker with the unchanged argv: same process,
same environment, same streams, same exit status, same signals. It never retries, waits or alters a launch.

WHAT IT DECIDES (tests/conftest.py::_docker_starts_stay_on_the_docker_worker): ``enforce`` fails a test that started a container without
being in the docker group; ``report:FILE`` appends one JSON line per test that started containers (grouped or not) and fails nothing - the
census / launch-count mode. Unset (the default): nothing is installed, PATH is untouched.

Test harness only. No production module reads either variable (pinned by tests/test_docker_worker_contract_001.py).
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
from typing import List, Optional

ENV_MODE = "KRIYA_DOCKER_WORKER_CONTRACT"          # "enforce" | "report:<file>"
ENV_LOG = "KRIYA_TEST_DOCKER_STARTS_LOG"           # the shim's append-only log (set by install)
ENV_KEEP = "KRIYA_DOCKER_WORKER_CONTRACT_KEEP"     # optional directory: each worker's log is copied there when its session ends (a passing session removes its base temp)
START_VERBS = ("run", "create", "start")
_SHIM = """#!/bin/sh
# tests/_docker_worker_contract.py: record a container start, then become the real docker.
for word in "$@"; do
  case "$word" in
    -*) continue ;;
    {verbs}) printf '%s\\t%s\\t%s\\n' "${{PYTEST_CURRENT_TEST:-<no test>}}" "$word" "$*" >> "${{{log}:-/dev/null}}" ;;
  esac
  break
done
exec {real} "$@"
"""


def write_shim(directory: str, real_docker: str) -> str:
    """The shim executable ``<directory>/docker`` that execs ``real_docker``."""
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "docker")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(_SHIM.format(verbs="|".join(START_VERBS), log=ENV_LOG, real=shlex.quote(real_docker)))
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def install(directory: str, environ=os.environ) -> Optional[str]:
    """Put the shim first on PATH for this process and its descendants; the log path, or None when there is no docker to shim."""
    real = shutil.which("docker", path=environ.get("PATH"))
    if real is None:
        return None
    write_shim(directory, real)
    log = os.path.join(directory, "starts.log")
    environ["PATH"] = directory + os.pathsep + environ.get("PATH", "")
    environ[ENV_LOG] = log
    return log


def keep(log: Optional[str], environ=os.environ) -> Optional[str]:
    """Copy this worker's log to ``$KRIYA_DOCKER_WORKER_CONTRACT_KEEP`` (evidence for a census / qualification); the copy's path or None."""
    directory = environ.get(ENV_KEEP)
    if not directory or not log or not os.path.exists(log):
        return None
    os.makedirs(directory, exist_ok=True)
    target = os.path.join(directory, f"starts.{environ.get('PYTEST_XDIST_WORKER', 'main')}.{os.getpid()}.log")
    shutil.copyfile(log, target)
    return target


def starts_of(log: Optional[str], nodeid: str) -> List[str]:
    """The start verbs the shim recorded while ``nodeid`` was the current test (any phase), in order."""
    if not log or not os.path.exists(log):
        return []
    found = []
    with open(log, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            current, verb = (line.rstrip("\n").split("\t") + [""])[:2]
            if current.startswith(nodeid + " ("):
                found.append(verb)
    return found


def launches(log: Optional[str]) -> List[tuple]:
    """Every recorded start as (current test, verb, argv joined by spaces), in order - what a census reads the bind sources from."""
    if not log or not os.path.exists(log):
        return []
    with open(log, "r", encoding="utf-8", errors="replace") as handle:
        return [tuple((line.rstrip("\n").split("\t", 2) + ["", ""])[:3]) for line in handle if line.strip()]


def violation(nodeid: str, starts: List[str], docker_grouped: bool) -> Optional[str]:
    """The failure message for a test that started containers outside the docker worker; None when the contract holds."""
    if not starts or docker_grouped:
        return None
    return (f"{nodeid} started {len(starts)} real container(s) ({', '.join(sorted(set(starts)))}) but is not in the docker xdist group: "
            "give it the canonical gate (a skipif whose reason names docker, see tests/conftest.py::_needs_real_docker) so it runs on the single "
            "docker worker. A start made by a subprocess of the test counts.")


def report(mode: str, nodeid: str, starts: List[str], docker_grouped: bool) -> None:
    """``report:FILE``: one JSON line per test that started containers."""
    if not starts or not mode.startswith("report:"):
        return
    with open(mode[len("report:"):], "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"test": nodeid, "starts": len(starts), "docker_group": docker_grouped, "worker": os.environ.get("PYTEST_XDIST_WORKER", "main")}) + "\n")
