"""KUP write boundaries under macOS sandbox-exec (gate C-5; 06 Phase C test "zero writes"; 08 review F-1).

Runs the real CLI entry point in a SUBPROCESS (sandbox-exec applies per process) against a fixture store in a
temporary state directory - never the operator's ~/.kriya, never a model (owner permission for isolated fixture-only
CLI tests, 2026-10-04). The child's environment is EXACTLY the GUI host's production launch policy
(``_kup_fixtures.host_child_env``: fixed PATH, fixed PYTHONDONTWRITEBYTECODE=1, HOME, KRIYA_STATE_DIR), with HOME and
the state directory pointed at temporary roots; the package is imported from a FRESH tree (no __pycache__), so a
bytecode write on first import is observable. Every sandbox denial is read back from the unified log, attributed to
the child's PID and classified by path:

* inspection (``--capabilities``, ``--snapshots``, ``--snapshot-verify``, history list/detail/prompt): complete
  write denial and network denial; the acceptance condition is ZERO attributed attempts (device writes at process
  start are reported, never counted);
* acquisition (``--snapshot``): writes allowed ONLY to the source's ``-wal``/``-shm`` and the snapshot directory;
  any denial elsewhere - the source main file, a ``__pycache__`` in the install tree above all - fails the test, and the
  published snapshot must exist;
* negative control: the same fresh tree WITHOUT PYTHONDONTWRITEBYTECODE shows ``__pycache__`` write attempts, so the
  detector is proven able to see what the production policy suppresses.

Skipped where sandbox-exec or the unified log is unavailable (not macOS). Raw evidence (every denial line, the
inventories) is written next to the test's temporary directory and printed on failure.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime, timedelta

import pytest
from _kup_fixtures import HOST_CHILD_ENV_KEYS, cli_subprocess_argv, fresh_package_tree, host_child_env, seed_store

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
LOG = "/usr/bin/log"
DENY_RE = re.compile(r"Sandbox: (?P<proc>\S+)\((?P<pid>\d+)\) deny\(\d+\) (?P<op>(?:file-write|network)\S*) (?P<path>.*)$")
pytestmark = pytest.mark.skipif(not (os.path.exists(SANDBOX_EXEC) and os.path.exists(LOG)), reason="needs macOS sandbox-exec and the unified log")

# No write anywhere and no network: the KUP read path needs neither (a model probe or a store write would show here).
DENY_ALL = "(version 1)\n(allow default)\n(deny file-write*)\n(deny network*)\n"


def _allow_only(paths):
    rules = "".join(f'(allow file-write* (literal "{p}"))\n' for p in paths["literals"]) + "".join(f'(allow file-write* (subpath "{p}"))\n' for p in paths["subpaths"])
    return DENY_ALL + rules


def _inventory(directory):
    out = {}
    for root, _dirs, files in os.walk(directory):
        for name in files:
            p = os.path.join(root, name)
            try:
                st = os.stat(p)
                h = hashlib.sha256(open(p, "rb").read()).hexdigest()[:16]
            except OSError:
                continue
            out[os.path.relpath(p, directory)] = (st.st_size, st.st_mtime_ns, h)
    return out


def _run_cli(profile_path, env, args, cwd, fresh_root):
    """sandbox-exec applies the profile and execs the interpreter in the SAME pid, which the unified log reports."""
    cmd = [SANDBOX_EXEC, "-f", profile_path, *cli_subprocess_argv(fresh_root), *args]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=cwd)
    out, err = proc.communicate(timeout=120)
    return {"pid": proc.pid, "args": args, "returncode": proc.returncode, "stdout": out, "stderr": err[-2000:]}


def _collect_denials(start, pids):
    cmd = [LOG, "show", "--start", (start - timedelta(seconds=5)).strftime("%Y-%m-%d %H:%M:%S"), "--style", "compact",
           "--predicate", 'eventMessage CONTAINS "Sandbox:" AND eventMessage CONTAINS "deny"']
    out = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=120).stdout
    found = [m.groupdict() | {"raw": line.strip()} for line in out.splitlines() if (m := DENY_RE.search(line))]
    return [d for d in found if int(d["pid"]) in pids]  # attributed to THIS test's children, never by name or time alone


def _classify(d, st):
    path = d["path"]
    if d["op"].startswith("network"):
        return "NETWORK"
    if path in (st["store"] + "-wal", st["store"] + "-shm"):
        return "SOURCE_SIDECAR"
    if path == st["store"] or path == st["store"] + "-journal":
        return "SOURCE_MAIN"
    if path == st["snapdir"] or path.startswith(st["snapdir"] + os.sep):
        return "SNAPSHOT_DIR"
    if path.startswith(st["fresh"] + os.sep):
        return "BYTECODE" if "__pycache__" in path else "FRESH_TREE_OTHER"
    if path.startswith(st["state_dir"] + os.sep):
        return "STATE_OTHER"
    if path.startswith(st["home"] + os.sep):
        return "HOME"
    if path.startswith("/dev/"):
        return "DEVICE"
    if path.startswith(st["root"] + os.sep):
        return "TMP_OTHER"
    return "UNRELATED"


def _evidence(st, name, runs, denials):
    for d in denials:
        d["class"] = _classify(d, st)
    counted = [d for d in denials if d["class"] != "DEVICE"]
    with open(os.path.join(st["root"], f"{name}.json"), "w", encoding="utf-8") as f:
        json.dump({"environment_keys": sorted(st["env"]), "runs": runs, "denials": denials}, f, indent=1)
    return counted


@pytest.fixture
def isolated(tmp_path):
    root = str(tmp_path)
    state_dir, home, cwd = (os.path.join(root, n) for n in ("state", "home", "cwd"))
    for d in (state_dir, home, cwd):
        os.makedirs(d)
    store = seed_store(os.path.join(state_dir, "traces.db"), 60)
    fresh = fresh_package_tree(os.path.join(root, "fresh"))
    env = host_child_env(home, state_dir)  # the production launch policy, fixture roots only
    assert set(env) == set(HOST_CHILD_ENV_KEYS)
    return {"root": root, "state_dir": state_dir, "home": home, "store": store, "snapdir": os.path.join(state_dir, "kup-snapshots"),
            "fresh": fresh, "env": env, "cwd": cwd}


def test_acquisition_writes_only_source_sidecars_and_the_snapshot_directory_and_inspection_writes_nothing(isolated):
    st = isolated
    runs, pids = [], set()
    start = datetime.now()
    acquire_profile = os.path.join(st["root"], "acquire.sb")
    with open(acquire_profile, "w", encoding="utf-8") as f:
        f.write(_allow_only({"literals": [st["store"] + "-wal", st["store"] + "-shm"], "subpaths": [st["snapdir"]]}))
    deny_profile = os.path.join(st["root"], "deny.sb")
    with open(deny_profile, "w", encoding="utf-8") as f:
        f.write(DENY_ALL)
    # NOTE: the subpath rule needs the directory to exist for realpath matching; acquisition creates it, and the
    # first call therefore needs the parent permitted for exactly that mkdir. Pre-create it 0700 as Kriya would.
    os.makedirs(st["snapdir"], mode=0o700)

    before_state = _inventory(st["state_dir"])
    acq = _run_cli(acquire_profile, st["env"], ["traces", "--json", "--snapshot"], st["cwd"], st["fresh"])
    runs.append({"phase": "acquire", **acq})
    pids.add(acq["pid"])
    assert acq["returncode"] == 0, acq
    env_acq = json.loads(acq["stdout"])
    assert env_acq["error"] is None, env_acq
    snapshot_id = env_acq["data"]["snapshot_id"]
    after_acq = _inventory(st["state_dir"])
    changed = sorted(k for k in set(before_state) | set(after_acq) if before_state.get(k) != after_acq.get(k))
    assert all(k.startswith("kup-snapshots/") or k in ("traces.db-wal", "traces.db-shm") for k in changed), changed
    assert "traces.db" not in changed, "the source main file must not change"

    # inspection under complete write and network denial (digest verification at pin included)
    before_insp = _inventory(st["state_dir"])
    for args in (["traces", "--json", "--capabilities"], ["traces", "--json", "--snapshots"], ["traces", "--json", "--snapshot-verify", snapshot_id],
                 ["traces", "--json", "--snapshot-id", snapshot_id, "-n", "5"],
                 ["traces", "--json", "--snapshot-id", snapshot_id, "--run-id", "run-0003"],
                 ["traces", "--json", "--snapshot-id", snapshot_id, "--run-id", "run-0003", "--include-prompt"]):
        r = _run_cli(deny_profile, st["env"], args, st["cwd"], st["fresh"])
        runs.append({"phase": "inspect", **r})
        pids.add(r["pid"])
        assert r["returncode"] == 0, r
        env_i = json.loads(r["stdout"])
        assert env_i["error"] is None, (args, env_i["error"])
        if args[-1] == "5":
            assert len(env_i["data"]["runs"]) == 5 and env_i["consistency"]["kind"] == "snapshot_copy"
        if args[2] == "--snapshot-verify":
            assert env_i["data"]["digest_verified"] is True and env_i["data"]["snapshot_id"] == snapshot_id
    assert _inventory(st["state_dir"]) == before_insp, "inspection changed a file in the state directory"
    assert not any("__pycache__" in dirs for _r, dirs, _f in os.walk(st["fresh"])), "the fresh tree must stay free of bytecode"

    time.sleep(4)
    counted = _evidence(st, "write_boundary_evidence", runs, _collect_denials(start, pids))
    # Acquisition ran with sidecars + snapshot dir ALLOWED, so any attributed denial at all is a write outside the
    # approved set; inspection ran under full denial, so any attributed denial is a write or network attempt. Both empty.
    assert counted == [], json.dumps(counted, indent=1)
    # Raw evidence stays in the test's own directory (<tmp>/write_boundary_evidence.json); the hand-off copy of one
    # run lives in handover/evidence/KUP/.


def test_negative_control_without_the_fixed_bytecode_suppression_a_fresh_install_attempts_pyc_writes(isolated):
    """Proves the detector: the SAME fresh tree, the SAME sandbox, the production environment minus
    PYTHONDONTWRITEBYTECODE -> the interpreter tries to create __pycache__ inside the install tree (denied, so nothing is
    written, but the attempts are logged). This is what a GUI launch without the fixed variable would do on a fresh
    install (08 review F-1), and what the first test proves the production policy prevents."""
    st = isolated
    env = {k: v for k, v in st["env"].items() if k != "PYTHONDONTWRITEBYTECODE"}
    deny_profile = os.path.join(st["root"], "deny.sb")
    with open(deny_profile, "w", encoding="utf-8") as f:
        f.write(DENY_ALL)
    start = datetime.now()
    r = _run_cli(deny_profile, env, ["traces", "--json", "--capabilities"], st["cwd"], st["fresh"])
    assert r["returncode"] == 0 and json.loads(r["stdout"])["error"] is None, r  # a denied cache write is not fatal to Python
    time.sleep(4)
    counted = _evidence(st, "negative_control_evidence", [{"phase": "negative_control", **r}], _collect_denials(start, {r["pid"]}))
    bytecode = [d for d in counted if d["class"] == "BYTECODE"]
    assert bytecode, "without PYTHONDONTWRITEBYTECODE a fresh tree must show __pycache__ write attempts; the detector saw none:\n" + json.dumps(counted, indent=1)
    assert all(d["class"] in ("BYTECODE", "DEVICE") for d in counted), json.dumps([d for d in counted if d["class"] != "BYTECODE"], indent=1)
    assert not any("__pycache__" in dirs for _r, dirs, _f in os.walk(st["fresh"])), "the sandbox must have refused the writes"
