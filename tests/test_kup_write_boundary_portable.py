"""KUP write boundaries, platform-neutral proof (HOSTED-KUP-WRITE-BOUNDARY-PORTABLE-001, gate C-5 / 06 Phase C "zero writes").

tests/test_kup_write_boundary.py proves the KUP read path writes nothing outside its allowed set with macOS ``sandbox-exec`` and the unified log: a kernel-level
mechanism that exists only on macOS, so on a hosted Linux runner it is (correctly) unavailable. The INVARIANT is not macOS-specific - inspection writes nothing,
acquisition writes only the source's ``-wal``/``-shm`` sidecars and the snapshot directory, and a fresh install gains no bytecode - so it is proved here on every
platform, and the sandbox-exec test stays the stronger macOS-only proof (it also sees C-level and child-process writes).

The real CLI entry point runs in a SUBPROCESS with the GUI host's production environment (``_kup_fixtures.host_child_env``) against a fixture store in a temporary state
directory, imported from a FRESH package tree. Two independent witnesses:

* a Python audit hook installed before ``kriya`` is imported records EVERY write-class operation of the process (open for write/append/create, mkdir, rename, remove,
  chmod, ...). Inspection must record none; acquisition only paths under the snapshot directory. (Limit, stated: writes made by C libraries - SQLite's own file
  writes - and by child processes are invisible to an audit hook; the second witness and the macOS test cover them.)
* the state tree is the effect witness: its inventory is unchanged by inspection, and the source main file is unchanged by acquisition. Where the OS enforces it
  (not root), the whole tree is also made read-only on disk for inspection, so a write attempt would fail the command instead of being recorded.

The negative control proves the recorder can see what production policy suppresses: the same fresh tree WITHOUT PYTHONDONTWRITEBYTECODE records ``__pycache__`` writes.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys

import pytest
from _kup_fixtures import HOST_CHILD_ENV_KEYS, fresh_package_tree, host_child_env, seed_store

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND

# Installed before ``kriya`` is imported. The log is a file descriptor opened BEFORE the hook exists and written with os.write (which raises no audit event), so the
# recorder never records itself.
_RECORDER = r'''
import json, os, sys
_FD = os.open(%(log)r, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
_FLAGS = %(flags)d
def _hook(event, args):
    record = None
    try:
        if event == "open":
            path, mode, flags = args
            if isinstance(path, (str, bytes, os.PathLike)) and ((isinstance(flags, int) and flags & _FLAGS) or (isinstance(mode, str) and any(c in mode for c in "wax+"))):
                record = [event, os.fsdecode(path)]
        elif event in ("os.mkdir", "os.rmdir", "os.remove", "os.truncate", "os.chmod", "os.chown", "os.utime", "os.mkfifo", "os.mknod"):
            record = [event, os.fsdecode(args[0])]
        elif event in ("os.rename", "os.link", "os.symlink", "shutil.move", "shutil.copyfile", "shutil.copytree"):
            record = [event, os.fsdecode(args[0]), os.fsdecode(args[1])]
    except Exception:
        record = [event, "<unreadable>"]
    if record is not None:
        os.write(_FD, (json.dumps(record) + "\n").encode())
sys.addaudithook(_hook)
'''


def _program(fresh_root: str, log: str) -> str:
    return (_RECORDER % {"log": log, "flags": _WRITE_FLAGS}
            + "sys.path.insert(0, %r)\nimport kriya\nassert kriya.__file__.startswith(%r), kriya.__file__\nfrom kriya.cli import main\nsys.exit(main())\n" % (fresh_root, fresh_root))


def _inventory(directory):
    out = {}
    for root, _dirs, files in os.walk(directory):
        for name in files:
            path = os.path.join(root, name)
            try:
                info = os.stat(path)
                digest = hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]
            except OSError:
                continue
            out[os.path.relpath(path, directory)] = (info.st_size, info.st_mtime_ns, digest)
    return out


def _writes(log: str):
    if not os.path.exists(log):
        return []
    return [json.loads(line) for line in open(log, encoding="utf-8").read().splitlines() if line]


def _run(st, name, args, *, env=None):
    log = os.path.join(st["root"], f"writes-{name}.jsonl")
    done = subprocess.run([sys.executable, "-c", _program(st["fresh"], log), *args], env=env or st["env"], cwd=st["cwd"], capture_output=True, text=True, check=False, timeout=120)
    return done, _writes(log)


def _read_only(*trees: str):
    """Make each tree (files and directories) read-only on disk; returns a restore function. A no-op for root, where permissions do not bind (the audit hook still does).
    The recorder's own log directory is deliberately not among the trees."""
    if os.geteuid() == 0:
        return lambda: None
    saved = []
    for tree in trees:
        for root, dirs, files in os.walk(tree):
            for name in dirs + files:
                path = os.path.join(root, name)
                saved.append((path, stat.S_IMODE(os.lstat(path).st_mode)))
        saved.append((tree, stat.S_IMODE(os.lstat(tree).st_mode)))
    for path, mode in saved:
        if not os.path.islink(path):
            os.chmod(path, mode & ~0o222)

    def restore():
        for path, mode in reversed(saved):
            if not os.path.islink(path):
                os.chmod(path, mode)
    return restore


@pytest.fixture
def isolated(tmp_path):
    root = str(tmp_path)
    state_dir, home, cwd = (os.path.join(root, n) for n in ("state", "home", "cwd"))
    for directory in (state_dir, home, cwd):
        os.makedirs(directory)
    store = seed_store(os.path.join(state_dir, "traces.db"), 60)
    fresh = fresh_package_tree(os.path.join(root, "fresh"))
    env = host_child_env(home, state_dir)
    assert set(env) == set(HOST_CHILD_ENV_KEYS)
    return {"root": root, "state_dir": state_dir, "home": home, "store": store, "snapdir": os.path.join(state_dir, "kup-snapshots"), "fresh": fresh, "env": env, "cwd": cwd}


def test_the_recorder_sees_a_write_and_a_read_only_open_is_not_a_write(tmp_path):
    """Control for the witness itself: it must record a real write and must not record a read (otherwise 'no writes recorded' proves nothing)."""
    log = str(tmp_path / "w.jsonl")
    target, source = tmp_path / "out.txt", tmp_path / "in.txt"
    source.write_text("x")
    body = (_RECORDER % {"log": log, "flags": _WRITE_FLAGS}
            + f"open({str(source)!r}).read()\nopen({str(target)!r}, 'w').write('y')\nimport os\nos.mkdir({str(tmp_path / 'd')!r})\nos.rename({str(target)!r}, {str(tmp_path / 'moved.txt')!r})\n")
    assert subprocess.run([sys.executable, "-c", body], capture_output=True, check=False, timeout=60).returncode == 0
    recorded = _writes(log)
    assert ["open", str(target)] in recorded and ["os.mkdir", str(tmp_path / "d")] in recorded and ["os.rename", str(target), str(tmp_path / "moved.txt")] in recorded
    assert all(record[1] != str(source) for record in recorded), "a read-only open was recorded as a write"


def test_acquisition_writes_only_under_the_snapshot_directory_and_inspection_writes_nothing(isolated):
    st = isolated
    os.makedirs(st["snapdir"], mode=0o700)  # as Kriya would; the acquisition then writes inside it
    before_state = _inventory(st["state_dir"])
    acquired, acquisition_writes = _run(st, "acquire", ["traces", "--json", "--snapshot"])
    assert acquired.returncode == 0, acquired.stderr[-2000:]
    envelope = json.loads(acquired.stdout)
    assert envelope["error"] is None, envelope
    snapshot_id = envelope["data"]["snapshot_id"]
    outside = [record for record in acquisition_writes if not any(os.path.realpath(p).startswith(os.path.realpath(st["snapdir"]) + os.sep) or os.path.realpath(p) == os.path.realpath(st["snapdir"])
                                                                for p in record[1:])]
    assert outside == [], f"acquisition wrote outside the snapshot directory: {outside}"
    changed = sorted(k for k in set(before_state) | set(_inventory(st["state_dir"])) if before_state.get(k) != _inventory(st["state_dir"]).get(k))
    assert all(k.startswith("kup-snapshots/") or k in ("traces.db-wal", "traces.db-shm") for k in changed), changed
    assert "traces.db" not in changed, "the source main file must not change"

    before_inspection = _inventory(st["state_dir"])
    restore = _read_only(st["state_dir"], st["home"], st["cwd"], st["fresh"])  # where the OS enforces it, a write would now FAIL the command, not just be recorded
    try:
        for index, args in enumerate((["traces", "--json", "--capabilities"], ["traces", "--json", "--snapshots"], ["traces", "--json", "--snapshot-verify", snapshot_id],
                                      ["traces", "--json", "--snapshot-id", snapshot_id, "-n", "5"],
                                      ["traces", "--json", "--snapshot-id", snapshot_id, "--run-id", "run-0003"],
                                      ["traces", "--json", "--snapshot-id", snapshot_id, "--run-id", "run-0003", "--include-prompt"])):
            done, writes = _run(st, f"inspect-{index}", args)
            assert done.returncode == 0, (args, done.stderr[-2000:])
            result = json.loads(done.stdout)
            assert result["error"] is None, (args, result["error"])
            assert writes == [], (args, writes)
            if args[-1] == "5":
                assert len(result["data"]["runs"]) == 5 and result["consistency"]["kind"] == "snapshot_copy"
            if args[2] == "--snapshot-verify":
                assert result["data"]["digest_verified"] is True and result["data"]["snapshot_id"] == snapshot_id
    finally:
        restore()
    assert _inventory(st["state_dir"]) == before_inspection, "inspection changed a file in the state directory"
    assert not any("__pycache__" in dirs for _r, dirs, _f in os.walk(st["fresh"])), "the fresh tree must stay free of bytecode"


def test_negative_control_without_the_fixed_bytecode_suppression_the_recorder_sees_pyc_writes(isolated):
    """The production policy (PYTHONDONTWRITEBYTECODE=1) suppresses bytecode writes into a fresh install tree; without it the same fresh tree gains ``__pycache__`` and the
    recorder names the writes - so the 'writes nothing' results above are not a blind spot."""
    st = isolated
    env = {k: v for k, v in st["env"].items() if k != "PYTHONDONTWRITEBYTECODE"}
    scratch = fresh_package_tree(os.path.join(st["root"], "scratch"))
    log = os.path.join(st["root"], "writes-negative-control.jsonl")
    done = subprocess.run([sys.executable, "-c", _program(scratch, log), "traces", "--json", "--capabilities"], env=env, cwd=st["cwd"], capture_output=True, text=True,
                          check=False, timeout=120)
    assert done.returncode == 0 and json.loads(done.stdout)["error"] is None, done.stderr[-2000:]
    assert [p for r_, dirs, _f in os.walk(scratch) for p in dirs if p == "__pycache__"], "a fresh tree must gain __pycache__ on first import without the suppression"
    pyc = [record for record in _writes(log) if "__pycache__" in record[1]]  # temp name + os.replace: the final .pyc name is not an open
    assert pyc, "the recorder did not see the bytecode writes it exists to detect"
