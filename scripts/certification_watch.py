#!/usr/bin/env python3
"""PRD-034: watch a running certification (scripts/certify.sh) and print one line per progress step or health change.

usage: certification_watch.py OUT_DIR [--interval SECONDS] [--step-percent N] [--once]

Reads OUT_DIR/pytest/progress.json (tests/_certification_progress.py), samples the pytest process (CPU split, children) and Docker, and
- writes OUT_DIR/health.json (the latest snapshot + flags) and appends every printed line to OUT_DIR/health.log;
- prints a line each ``--step-percent`` of progress, whenever the set of health flags changes, and when the run finishes - a quiet run prints
  almost nothing, so it can be attached to the Monitor tool without producing a turn per test.

It only READS. It never stops the run: a flag is an instruction to investigate (the operator or the session decides), and a process being
alive is deliberately not treated as proof of health.
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))

from _certification_progress import assess, read_progress, render_line  # noqa: E402  (path set above)


def _cpu_seconds(pid: int):
    """Cumulative {"user", "system"} CPU seconds of ``pid`` (None when unreadable)."""
    try:
        if os.path.exists(f"/proc/{pid}/stat"):
            with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as handle:
                fields = handle.read().rsplit(")", 1)[1].split()
            ticks = os.sysconf("SC_CLK_TCK")
            return {"user": int(fields[11]) / ticks, "system": int(fields[12]) / ticks}
        out = subprocess.run(["ps", "-o", "utime=,stime=", "-p", str(pid)], capture_output=True, text=True, timeout=10, check=False).stdout.split()
        if len(out) != 2:
            return None

        def seconds(text: str) -> float:
            minutes, _, rest = text.partition(":")
            return float(minutes) * 60 + float(rest)

        return {"user": seconds(out[0]), "system": seconds(out[1])}
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _count(command) -> "int | None":
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return len([line for line in result.stdout.splitlines() if line.strip()]) if result.returncode == 0 else None


def main(argv) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    out = args[0]
    interval, step, once = 60.0, 5.0, "--once" in argv
    for index, arg in enumerate(argv):
        if arg == "--interval":
            interval = float(argv[index + 1])
        if arg == "--step-percent":
            step = float(argv[index + 1])
    pytest_dir = os.path.join(out, "pytest")
    previous_cpu, last_percent_step, last_flags = None, -1, ()
    while True:
        snapshot = read_progress(pytest_dir)
        now = time.time()
        if snapshot is None:
            line = None
            flags = []
        else:
            pid = int(snapshot["pid"])
            alive = _alive(pid)
            sample = _cpu_seconds(pid) if alive else None
            window = None
            if sample and previous_cpu:
                window = {k: max(0.0, sample[k] - previous_cpu[k]) for k in sample}
            previous_cpu = sample or previous_cpu
            containers = _count(["docker", "ps", "-q"])
            children = _count(["pgrep", "-P", str(pid)])
            flags = assess(snapshot, now=now, cpu=window, process_alive=alive, docker_containers=containers, child_processes=children)
            line = render_line(snapshot, now=now, flags=flags, cpu=window, docker_containers=containers)
            with open(os.path.join(out, "health.json"), "w", encoding="utf-8") as handle:
                json.dump({"at": now, "snapshot": snapshot, "flags": flags, "cpu_window": window, "docker_containers": containers,
                           "pytest_children": children}, handle, indent=1, sort_keys=True)
            percent_step = int(100 * snapshot["done"] / max(snapshot["total"], 1) // step)
            names = tuple(sorted(item["flag"] for item in flags))
            if percent_step != last_percent_step or names != last_flags or snapshot.get("finished") or once:
                print(line, flush=True)
                with open(os.path.join(out, "health.log"), "a", encoding="utf-8") as handle:
                    handle.write(f"{time.strftime('%FT%TZ', time.gmtime(now))} {line}\n")
                last_percent_step, last_flags = percent_step, names
            if snapshot.get("finished"):
                return 0
            if not alive:
                return 1
        if once:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
