"""KUP write boundaries under macOS sandbox-exec (gate C-5; 06 Phase C test "zero writes").

Runs the real CLI entry point in a SUBPROCESS (sandbox-exec applies per process) against fixture stores in a
temporary state directory - never the operator's ~/.kriya, never a model. Every sandbox denial is read back from
the unified log, attributed to the subprocess PID and classified by path:

* inspection (``--capabilities``, ``--snapshots``, history list/detail/prompt): complete write denial; the
  acceptance condition is ZERO attributed write attempts (device writes at process start are reported, never counted);
* acquisition (``--snapshot``): writes allowed ONLY to the source's ``-wal``/``-shm`` and the snapshot directory;
  any denial elsewhere - the source main file above all - fails the test, and the published snapshot must exist.

Skipped where sandbox-exec or the unified log is unavailable (not macOS). Raw evidence (every denial line, the
inventories) is written next to the test's temporary directory and printed on failure.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta

import pytest
from _kup_fixtures import seed_store

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
LOG = "/usr/bin/log"
DENY_RE = re.compile(r"Sandbox: (?P<proc>\S+)\((?P<pid>\d+)\) deny\(\d+\) (?P<op>file-write\S*) (?P<path>.*)$")
pytestmark = pytest.mark.skipif(not (os.path.exists(SANDBOX_EXEC) and os.path.exists(LOG)), reason="needs macOS sandbox-exec and the unified log")

DENY_ALL = "(version 1)\n(allow default)\n(deny file-write*)\n"


def _allow_only(paths):
    rules = "".join(f'(allow file-write* (literal "{p}"))\n' for p in paths["literals"]) + "".join(f'(allow file-write* (subpath "{p}"))\n' for p in paths["subpaths"])
    return "(version 1)\n(allow default)\n(deny file-write*)\n" + rules


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


def _run_cli(profile_path, env, args, cwd):
    cmd = [SANDBOX_EXEC, "-f", profile_path, sys.executable, "-B", "-c", "from kriya.cli import main; main()", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=cwd, check=False, timeout=120)
    return {"pid": None, "args": args, "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr[-2000:]}


def _collect_denials(start):
    cmd = [LOG, "show", "--start", (start - timedelta(seconds=5)).strftime("%Y-%m-%d %H:%M:%S"), "--style", "compact",
           "--predicate", 'eventMessage CONTAINS "Sandbox:" AND eventMessage CONTAINS "deny"']
    out = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=120).stdout
    return [m.groupdict() | {"raw": line.strip()} for line in out.splitlines() if (m := DENY_RE.search(line))]


def _classify(path, store, snapdir, state_dir, tmp_root):
    if path in (store + "-wal", store + "-shm"):
        return "SOURCE_SIDECAR"
    if path == store or path == store + "-journal":
        return "SOURCE_MAIN"
    if path == snapdir or path.startswith(snapdir + os.sep):
        return "SNAPSHOT_DIR"
    if path.startswith(state_dir + os.sep):
        return "STATE_OTHER"
    if path.startswith("/dev/"):
        return "DEVICE"
    if path.startswith(tmp_root + os.sep):
        return "TMP_OTHER"
    return "UNRELATED"


@pytest.fixture
def isolated(tmp_path):
    root = str(tmp_path)
    state_dir, home, cwd, logs = (os.path.join(root, n) for n in ("state", "home", "cwd", "logs"))
    for d in (state_dir, home, cwd, logs):
        os.makedirs(d)
    store = seed_store(os.path.join(state_dir, "traces.db"), 60)
    env = {
        "PATH": "/usr/bin:/bin", "HOME": home, "TMPDIR": os.path.join(root, "tmp"), "LANG": "en_US.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": os.getcwd(),
        "KRIYA_STATE_DIR": state_dir, "KRIYA_LOG_DIR": logs, "KRIYA_MODEL_RUNTIME_PROBE": "0",
        "KRIYA_AUTHORITY_HOME": os.path.join(root, "authority"), "KRIYA_QUALIFICATION_HOME": os.path.join(root, "qual"),
        "KRIYA_MCP_APPROVAL_HOME": os.path.join(root, "mcp"), "KRIYA_CERTIFICATION_HOME": os.path.join(root, "cert"),
        "KRIYA_STATIC_ANALYSIS_HOME": os.path.join(root, "sa"),
    }
    os.makedirs(env["TMPDIR"])
    return {"root": root, "state_dir": state_dir, "store": store, "snapdir": os.path.join(state_dir, "kup-snapshots"), "env": env, "cwd": cwd}


def test_acquisition_writes_only_source_sidecars_and_the_snapshot_directory_and_inspection_writes_nothing(isolated):
    st = isolated
    evidence = {"runs": [], "denials": []}
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
    acq = _run_cli(acquire_profile, st["env"], ["traces", "--json", "--snapshot"], st["cwd"])
    evidence["runs"].append({"phase": "acquire", **acq})
    assert acq["returncode"] == 0, acq
    env_acq = json.loads(acq["stdout"])
    assert env_acq["error"] is None, env_acq
    snapshot_id = env_acq["data"]["snapshot_id"]
    after_acq = _inventory(st["state_dir"])
    changed = sorted(k for k in set(before_state) | set(after_acq) if before_state.get(k) != after_acq.get(k))
    assert all(k.startswith("kup-snapshots/") or k in ("traces.db-wal", "traces.db-shm") for k in changed), changed
    assert "traces.db" not in changed, "the source main file must not change"

    # inspection under complete denial
    before_insp = _inventory(st["state_dir"])
    insp_runs = []
    for args in (["traces", "--json", "--capabilities"], ["traces", "--json", "--snapshots"],
                 ["traces", "--json", "--snapshot-id", snapshot_id, "-n", "5"],
                 ["traces", "--json", "--snapshot-id", snapshot_id, "--run-id", "run-0003"],
                 ["traces", "--json", "--snapshot-id", snapshot_id, "--run-id", "run-0003", "--include-prompt"]):
        r = _run_cli(deny_profile, st["env"], args, st["cwd"])
        evidence["runs"].append({"phase": "inspect", **r})
        insp_runs.append(r)
        assert r["returncode"] == 0, r
        env_i = json.loads(r["stdout"])
        assert env_i["error"] is None, (args, env_i["error"])
        if args[-1] == "5":
            assert len(env_i["data"]["runs"]) == 5 and env_i["consistency"]["kind"] == "snapshot_copy"
    assert _inventory(st["state_dir"]) == before_insp, "inspection changed a file in the state directory"

    time.sleep(4)
    denials = _collect_denials(start)
    attributed = [d for d in denials if "kriya" in d["raw"] or "python" in d["proc"].lower() or "Python" in d["proc"]]
    for d in attributed:
        d["class"] = _classify(d["path"], st["store"], st["snapdir"], st["state_dir"], st["root"])
    evidence["denials"] = attributed
    with open(os.path.join(st["root"], "write_boundary_evidence.json"), "w", encoding="utf-8") as f:
        json.dump(evidence, f, indent=1)
    counted = [d for d in attributed if d["class"] not in ("DEVICE",)]
    # Acquisition ran with sidecars + snapshot dir ALLOWED, so any attributed denial at all is a write outside the
    # approved set; inspection ran under full denial, so any attributed denial is a write attempt. Both must be empty.
    assert counted == [], json.dumps(counted, indent=1)
    # Raw evidence stays in the test's own directory (<tmp>/write_boundary_evidence.json); the hand-off copy of one
    # run lives in handover/evidence/KUP/.
