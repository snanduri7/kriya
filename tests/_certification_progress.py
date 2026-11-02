"""PRD-034: inspectable progress of a long certification run, and the health verdict derived from it.

The canonical pytest run is ~19.7k tests. Its first execution (c3-run1, 2026-11-02) crawled for hours behind a silent command: the process was alive,
the output was a progress bar of dots, and nothing said that ~83 % of its CPU was kernel time. A live process is not a healthy one, so:

* ``ProgressWriter`` (hooked in tests/conftest.py when ``--certification-report DIR`` is given - the canonical command is unchanged) keeps
  ``DIR/progress.json`` current: pid, start, totals, the passed/failed/error/skipped counts so far, the node now executing with its tiers, and
  a sampled ``(time, done)`` history for throughput. The file is replaced atomically, so a reader never sees half a document.
* ``assess`` is the pure health judgement over one snapshot plus a process sample. Every trigger is RELATIVE to the run itself (its own recent
  throughput against its own earlier throughput, a node's age against the slowest node completed so far): there is no wall-clock budget, because
  the legitimate nodes of this suite range from milliseconds to ~9 minutes.
* tests/../scripts/certification_watch.py reads both and emits one line per progress step or health change.

A trigger is an instruction to INVESTIGATE, not an automatic kill: a failure that already makes certification impossible is reported at once.
"""
from __future__ import annotations

import json
import os
import statistics
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence

PROGRESS_FILE = "progress.json"
HISTORY_INTERVAL_S = 30.0
HISTORY_LIMIT = 1440  # 12 h at one sample / 30 s
WINDOW_S = 900.0  # the "recent" throughput window (15 min)

# Health flags (closed set; the watchdog prints them verbatim).
FAILURE_SEEN = "FAILURE_SEEN"
NO_PROGRESS = "NO_PROGRESS"
SLOW_NODE = "SLOW_NODE"
THROUGHPUT_COLLAPSE = "THROUGHPUT_COLLAPSE"
KERNEL_BOUND = "KERNEL_BOUND"
PROJECTION_DIVERGES = "PROJECTION_DIVERGES"
DOCKER_INCONSISTENT = "DOCKER_INCONSISTENT"
PROCESS_GONE = "PROCESS_GONE"

COLLAPSE_RATIO = 0.25  # recent rate below a quarter of the run's own median window rate
KERNEL_FRACTION = 0.6  # system CPU above 60 % of (user + system) over the sample window
SLOW_NODE_FACTOR = 3.0  # a node older than 3x the slowest completed node (and at least SLOW_NODE_FLOOR_S)
SLOW_NODE_FLOOR_S = 900.0
NO_PROGRESS_S = 1200.0
PROJECTION_FACTOR = 2.0


# The writer runs INSIDE the pytest process, in hooks that fire while a test's own monkeypatches are still active. It therefore binds every OS
# primitive it needs at import, before any test can retarget it (c3-run2, 2026-11-02: tests/test_d1_durable_write.py patches ``os.replace`` to raise,
# the flush ran in pytest_runtest_logreport during that test, and the run aborted at 15 % with zero test failures).
_OPEN = open
_REPLACE = os.replace
_GETPID = os.getpid
_DUMP = json.dump


def _atomic_write(path: str, payload: Mapping[str, Any]) -> None:
    temporary = f"{path}.{_GETPID()}.tmp"
    with _OPEN(temporary, "w", encoding="utf-8") as handle:
        _DUMP(payload, handle, sort_keys=True)
    _REPLACE(temporary, path)


class ProgressWriter:
    """Keeps ``DIR/progress.json`` current while pytest runs."""

    def __init__(self, directory: str, tiers: Mapping[str, Sequence[str]], *, clock=time.time) -> None:
        self._path = os.path.join(directory, PROGRESS_FILE)
        self._tiers = tiers
        self._clock = clock
        self._state: Dict[str, Any] = {
            "pid": os.getpid(), "started": clock(), "total": len(tiers), "done": 0,
            "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "failed_nodes": [],
            "current": None, "current_tiers": [], "current_started": None, "longest_node_s": 0.0,
            "history": [[clock(), 0]], "finished": False, "exit_status": None, "write_errors": 0,
        }
        self._current_began: Optional[float] = None
        self.flush()

    def flush(self) -> None:
        """Observability must never change the outcome of the run it observes: a failed write (full disk, vanished report directory) is COUNTED in the
        file's ``write_errors`` and the run continues; a stale file is then visible to the watchdog as NO_PROGRESS. Only OSError is absorbed - a coding
        error in this module still fails loudly."""
        self._state["updated"] = self._clock()
        try:
            _atomic_write(self._path, self._state)
        except OSError:
            self._state["write_errors"] = self._state.get("write_errors", 0) + 1

    def start(self, nodeid: str) -> None:
        now = self._clock()
        self._current_began = now
        self._state.update(current=nodeid, current_tiers=list(self._tiers.get(nodeid, ())), current_started=now)
        self.flush()

    def report(self, report) -> None:
        """One pytest phase report. ``done`` counts a node once, at its teardown."""
        state = self._state
        if report.failed:
            kind = "failed" if report.when == "call" else "errors"
            state[kind] += 1
            if len(state["failed_nodes"]) < 50:
                state["failed_nodes"].append(f"{report.when}:{report.nodeid}")
        elif report.skipped and report.when != "teardown":
            state["skipped"] += 1
        elif report.passed and report.when == "call":
            state["passed"] += 1
        if report.when == "teardown":
            now = self._clock()
            state["done"] += 1
            if self._current_began is not None:
                state["longest_node_s"] = max(state["longest_node_s"], now - self._current_began)
            history = state["history"]
            if now - history[-1][0] >= HISTORY_INTERVAL_S:
                history.append([now, state["done"]])
                del history[:-HISTORY_LIMIT]
        self.flush()

    def finish(self, exit_status: int) -> None:
        self._state.update(finished=True, exit_status=int(exit_status), current=None)
        self._state["history"].append([self._clock(), self._state["done"]])
        self.flush()


def read_progress(directory: str) -> Optional[Dict[str, Any]]:
    try:
        with open(os.path.join(directory, PROGRESS_FILE), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def window_rates(history: Sequence[Sequence[float]], width: float = WINDOW_S) -> List[float]:
    """Tests per second over consecutive windows of ``width`` seconds (the last, partial window is excluded)."""
    rates: List[float] = []
    if len(history) < 2:
        return rates
    start_time, start_done = history[0]
    for time_point, done in history[1:]:
        if time_point - start_time >= width:
            rates.append((done - start_done) / (time_point - start_time))
            start_time, start_done = time_point, done
    return rates


def recent_rate(history: Sequence[Sequence[float]], now: float, width: float = WINDOW_S) -> Optional[float]:
    """Tests per second over the last ``width`` seconds of history, or None when the history does not cover it."""
    if len(history) < 2:
        return None
    last_time, last_done = history[-1]
    for time_point, done in reversed(history[:-1]):
        if last_time - time_point >= width:
            return (last_done - done) / (last_time - time_point)
    return None


def assess(snapshot: Mapping[str, Any], *, now: float, cpu: Optional[Mapping[str, float]] = None,
           process_alive: bool = True, docker_containers: Optional[int] = None, child_processes: Optional[int] = None) -> List[Dict[str, str]]:
    """The health flags of a snapshot (empty = healthy). ``cpu`` is {"user": s, "system": s} consumed over a recent window."""
    flags: List[Dict[str, str]] = []

    def flag(name: str, detail: str) -> None:
        flags.append({"flag": name, "detail": detail})

    if snapshot.get("finished"):
        return flags
    if not process_alive:
        flag(PROCESS_GONE, "pytest exited without finishing its progress file")
        return flags
    if snapshot["failed"] or snapshot["errors"]:
        flag(FAILURE_SEEN, f"{snapshot['failed']} failed / {snapshot['errors']} errors so far: " + ", ".join(snapshot["failed_nodes"][:3]))
    history = snapshot["history"]
    node_age = now - snapshot["current_started"] if snapshot.get("current_started") else 0.0
    stale_for = now - snapshot["updated"]
    if max(stale_for, node_age) > NO_PROGRESS_S:
        flag(NO_PROGRESS, f"no node has completed for {max(stale_for, node_age) / 60:.0f} min (executing: {snapshot.get('current')})")
    elif node_age > max(SLOW_NODE_FLOOR_S, SLOW_NODE_FACTOR * snapshot["longest_node_s"]):
        flag(SLOW_NODE, f"{snapshot.get('current')} has run {node_age / 60:.0f} min; the slowest completed node took {snapshot['longest_node_s']:.0f}s")
    windows = window_rates(history)
    recent = recent_rate(history, now)
    if recent is not None and len(windows) >= 4:
        baseline = statistics.median(windows[:-1])
        if baseline > 0 and recent < COLLAPSE_RATIO * baseline:
            flag(THROUGHPUT_COLLAPSE, f"recent {recent * 60:.1f} tests/min vs the run's median window {baseline * 60:.1f} tests/min")
    if cpu:
        total = cpu.get("user", 0.0) + cpu.get("system", 0.0)
        if total >= 30.0 and cpu.get("system", 0.0) / total > KERNEL_FRACTION:
            flag(KERNEL_BOUND, f"{100 * cpu['system'] / total:.0f}% of the recent CPU is kernel time")
    elapsed = now - snapshot["started"]
    if recent is not None and snapshot["done"] > 0 and recent > 0 and elapsed > 2 * WINDOW_S:
        average = snapshot["done"] / elapsed
        remaining = snapshot["total"] - snapshot["done"]
        if remaining / recent > PROJECTION_FACTOR * (remaining / average):
            flag(PROJECTION_DIVERGES, f"{remaining} tests left: {remaining / recent / 3600:.1f} h at the recent rate vs {remaining / average / 3600:.1f} h at the run average")
    if "docker" in snapshot.get("current_tiers", ()) and node_age > 300 and docker_containers == 0 and child_processes == 0:
        flag(DOCKER_INCONSISTENT, f"{snapshot.get('current')} is a Docker-tier node running {node_age / 60:.0f} min but there is no container and no child process")
    return flags


def render_line(snapshot: Mapping[str, Any], *, now: float, flags: Sequence[Mapping[str, str]], cpu: Optional[Mapping[str, float]] = None,
                docker_containers: Optional[int] = None) -> str:
    """The one-line progress snapshot: ``C3: 4,820 / 19,672 (24.5%), elapsed 58m, ..., 0F/0E, ~82 tests/min, pytest alive, Docker tier ...``."""
    total, done = snapshot["total"], snapshot["done"]
    elapsed = now - snapshot["started"]
    rate = recent_rate(snapshot["history"], now)
    tiers = snapshot.get("current_tiers") or []
    live = {"live_model", "live_static_analysis", "real_toolchain", "docker"}
    tier_text = "/".join(t for t in tiers if t in live) or "ordinary pytest"
    docker_text = ("Docker tier inactive" if "docker" not in tiers else
                   f"Docker tier ACTIVE ({docker_containers if docker_containers is not None else '?'} containers)")
    kernel = ""
    if cpu and cpu.get("user", 0.0) + cpu.get("system", 0.0) > 0:
        kernel = f", sys {100 * cpu['system'] / (cpu['user'] + cpu['system']):.0f}% of CPU"
    line = (f"C3: {done:,} / {total:,} ({100 * done / max(total, 1):.1f}%), elapsed {int(elapsed // 3600)}h{int(elapsed % 3600 // 60):02d}m, "
            f"now {snapshot.get('current') or 'finished'} [{tier_text}], {snapshot['failed']}F/{snapshot['errors']}E/{snapshot['skipped']}S, "
            f"{'~%.0f tests/min' % (rate * 60) if rate is not None else 'rate pending'}{kernel}, "
            f"{'pytest alive' if not snapshot.get('finished') else 'pytest finished'}, {docker_text}, no live-model inference in this run")
    if flags:
        line += " | HEALTH: " + "; ".join(f"{item['flag']} - {item['detail']}" for item in flags)
    return line
