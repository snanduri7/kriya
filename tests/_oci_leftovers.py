"""The real-daemon leak check, against the daemon's own removal completion (OCI-LEAK-CHECK-READS-DAEMON-REMOVAL-IN-PROGRESS-001).

MEASURED 2026-11-15 (two parallel runs, `-n 6`, Docker Desktop): `docker ps -a` listed two Kriya containers right after a run returned
(tests/test_s4_1_managed_service_view.py, twice; tests/test_finite_launch_target_start.py, the registry proxies, once); seconds later none
existed, and each test passes alone. TRACED: the backend's teardown is a synchronous `docker rm -f` client call (15 s limit, a failure is
logged and not raised) and every container is started `--rm`; the daemon completes a removal after the client call returns. INFERRED, not
yet observed: the listed containers were already exited and being removed.

So the check distinguishes what an immediate listing cannot:
- a container that is still RUNNING outlived its run: reported at once, never waited for;
- any other listed container is waited for with the daemon's own completion signal (`docker wait --condition=removed`), bounded; one that
  is still there afterwards is reported.
Whenever it had to wait it says so (a warning carrying each container's state, creation time and command), so the log of a run records
which case occurred. No sleep, and no leftover is ever accepted.
"""
from __future__ import annotations

import subprocess
import warnings
from typing import List

_FORMAT = "{{.Names}}|{{.State}}|{{.Status}}|{{.CreatedAt}}|{{.Command}}"
REMOVAL_BOUND_SECONDS = 60.0


def _listed(name_filter: str) -> List[str]:
    return subprocess.run(["docker", "ps", "-a", "--no-trunc", "--filter", f"name={name_filter}", "--format", _FORMAT],
                          capture_output=True, text=True, check=True).stdout.splitlines()


def leftover_containers(name_filter: str, *, bound: float = REMOVAL_BOUND_SECONDS) -> List[str]:
    """The containers matching ``name_filter`` that outlive the run, one ``name|state|status|created|command`` row each ([] = none)."""
    first = _listed(name_filter)
    if not first:
        return []
    running = [row for row in first if row.split("|")[1] == "running"]
    if running:
        return running
    for row in first:
        try:
            subprocess.run(["docker", "wait", "--condition=removed", row.split("|")[0]], capture_output=True, timeout=bound, check=False)
        except subprocess.TimeoutExpired:
            pass   # still there: the listing below reports it
    remaining = _listed(name_filter)
    warnings.warn(f"OCI leak check waited for the daemon's removal of {len(first)} non-running container(s) "
                  f"({len(remaining)} left afterwards): {first}", stacklevel=2)
    return remaining
