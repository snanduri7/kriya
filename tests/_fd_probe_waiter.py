"""Subprocess body of tests/test_d2_m2_fd_inheritance.py (not a test): plays the WAITER that the host started with the review socket inherited at fd 3.

It takes the descriptor over exactly as ``kriya generate --launch-id`` does (``open_review_channel_from_fd``), then launches real child commands through Kriya's own spawn paths;
every child enumerates and probes its own descriptor table and spawns a grandchild that does the same. Prints one ``REPORT {json}`` line.

modes
  paths         takeover, then the five production spawn paths (each child + grandchild)
  belt          takeover, then a (forbidden) ``close_fds=False`` spawn: only the non-inheritable flag stands between the descriptor and the child
  raw_belt      NO takeover (the descriptor stays inheritable), then ``close_fds=False``: shows why the takeover matters (the probe must see the leak)
  pass_fds      takeover, then an explicit ``pass_fds=(3,)``: shows the probe has teeth even after the takeover
"""
import asyncio
import json
import os
import stat
import subprocess
import sys
import time

CHILD = r'''
import json, os, socket, stat, subprocess, sys

def table():
    rows = []
    for name in sorted(os.listdir("/dev/fd"), key=int):
        fd = int(name)
        try:
            info = os.fstat(fd)
        except OSError:
            continue  # the descriptor listdir itself used
        kind = "socket" if stat.S_ISSOCK(info.st_mode) else "fifo" if stat.S_ISFIFO(info.st_mode) else "other"
        rows.append([fd, kind])
    return rows

def probe():
    seen = {"fds_above_2": [row for row in table() if row[0] >= 3]}
    try:
        os.write(3, b"X")
        seen["write_fd3"] = "WROTE"
    except OSError as error:
        seen["write_fd3"] = type(error).__name__
    try:
        socket.socket(fileno=os.dup(3))
        seen["dup_fd3"] = "DUPED"
    except OSError as error:
        seen["dup_fd3"] = type(error).__name__
    try:
        os.close(os.open("/dev/fd/3", os.O_RDWR))
        seen["reopen_dev_fd3"] = "OPENED"
    except OSError as error:
        seen["reopen_dev_fd3"] = type(error).__name__
    return seen

result = probe()
if len(sys.argv) > 1 and sys.argv[1] == "nest":
    grand = subprocess.run([sys.executable, "-c", sys.argv[2]], capture_output=True, text=True)
    result["grandchild"] = json.loads(grand.stdout)
print(json.dumps(result))
'''
GRAND = CHILD  # the grandchild runs the same probe, without nesting further


def command(nest):
    return [sys.executable, "-c", CHILD, "nest", GRAND] if nest else [sys.executable, "-c", CHILD]


def paths():
    from kriya.tools.process import ProcessController, spawn_subprocess_exec_fail_closed
    from kriya.tools.sandbox import posix_resource_limits_preexec_fn

    controller, cwd, found = ProcessController(), os.getcwd(), {}
    found["ProcessController.run"] = json.loads(controller.run(command(True), cwd=cwd, timeout=60).stdout)
    found["ProcessController.run + resource-limit preexec_fn"] = json.loads(
        controller.run(command(False), cwd=cwd, timeout=60, preexec_fn=posix_resource_limits_preexec_fn(30, None)).stdout)
    found["ProcessController.run_async"] = json.loads(asyncio.run(controller.run_async(command(True), cwd=cwd, timeout=60)).stdout)

    async def fail_closed():
        process = await spawn_subprocess_exec_fail_closed(*command(False), cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await process.communicate()
        return json.loads(out)

    found["spawn_subprocess_exec_fail_closed"] = asyncio.run(fail_closed())
    managed = controller.start_managed(command(False), cwd=cwd)
    for _ in range(200):
        if managed.poll() is not None:
            break
        time.sleep(0.05)
    found["ProcessController.start_managed"] = json.loads(managed.captured_output()[0])
    return found


def main():
    mode = sys.argv[1]
    report = {"mode": mode, "fd3_is_socket_at_start": stat.S_ISSOCK(os.fstat(3).st_mode), "fd3_inheritable_at_start": os.get_inheritable(3)}
    channel = None
    if mode != "raw_belt":
        from kriya.control.review_channel import open_review_channel_from_fd

        channel = open_review_channel_from_fd()  # the production takeover: a socket, made non-inheritable at once
    report["fd3_inheritable_after_takeover"] = os.get_inheritable(3)
    if mode == "paths":
        report["results"] = paths()
    elif mode in ("belt", "raw_belt"):
        report["results"] = {"close_fds=False": json.loads(subprocess.run(command(True), capture_output=True, text=True, close_fds=False).stdout)}
    elif mode == "pass_fds":
        report["results"] = {"pass_fds=(3,)": json.loads(subprocess.run(command(False), capture_output=True, text=True, pass_fds=(3,)).stdout)}
    sys.stdout.write("REPORT " + json.dumps(report) + "\n")
    if channel is not None:
        channel.close()


if __name__ == "__main__":
    main()
