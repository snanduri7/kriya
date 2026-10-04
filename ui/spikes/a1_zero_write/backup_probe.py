#!/usr/bin/env python
"""Measurement for the D-4 gate (NOT an implementation): what does a stable-snapshot ACQUISITION through SQLite's
online backup API write on the source side, per store state, and is the resulting snapshot then readable with ZERO
writes under the A1 read candidate? Fixtures only; runs the acquisition OUTSIDE the sandbox (it is allowed to touch the
source's -wal/-shm by design) and the inspection INSIDE sandbox-exec (deny file-write*), exactly like A1.

Acquisition candidate: open the source `file:...?mode=ro` (uri) with a bounded busy timeout, SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE
set, PRAGMA wal_autocheckpoint=0, then Connection.backup(dest, pages=-1) in ONE step (a single read transaction),
then on the DESTINATION only: PRAGMA journal_mode=DELETE, PRAGMA quick_check, close. The destination is a rollback-
journal database = A1 case C01, the one state measured to read with zero writes.
"""
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import harness  # noqa: E402  (reuses inventory, classify, DENY_RE, PROFILE, run_reader)

CASES = [c for c in harness.CASES if c[0] in ("C02_rollback_hot_journal", "C03_wal_clean", "C04_wal_sidecars_orphaned", "C05_wal_active_writer", "C09_missing_store")]


def acquire(source: str, dest: str, timeout: float = 2.0) -> dict:
    out = {"steps": []}
    t0 = time.monotonic()
    try:
        src = sqlite3.connect(harness_uri(source), uri=True, timeout=timeout)
        out["steps"].append("connect_ro")
        src.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
        out["steps"].append("no_ckpt_on_close")
        src.execute("PRAGMA wal_autocheckpoint=0")
        out["journal_mode_source"] = src.execute("PRAGMA journal_mode").fetchone()[0]
        out["steps"].append("source_journal_mode_read")
        dst = sqlite3.connect(dest)
        src.backup(dst, pages=-1)  # one step = one read transaction = a consistent image
        out["steps"].append("backup_one_step")
        out["journal_mode_dest"] = dst.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
        out["quick_check"] = dst.execute("PRAGMA quick_check").fetchone()[0]
        out["rows"] = dst.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        dst.close(); src.close()
        out["steps"].append("closed")
        out["ok"] = True
    except sqlite3.Error as e:
        out["ok"] = False
        out["error"] = str(e)
        out["sqlite_errorname"] = getattr(e, "sqlite_errorname", None)
    out["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
    return out


def harness_uri(path):
    from reader import read_only_uri
    return read_only_uri(path)


def main():
    python = sys.executable
    work = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ.get("TMPDIR", "/tmp"), "kriya-backup-probe")
    os.makedirs(work, exist_ok=True)
    run_tmp = os.path.join(work, "tmp"); os.makedirs(run_tmp, exist_ok=True)
    os.makedirs(os.path.join(work, "fake-home"), exist_ok=True)
    profile = os.path.join(work, "deny.sb"); open(profile, "w").write(harness.PROFILE)
    start = datetime.now()
    results = []
    for case_id, desc, fx, flags in CASES:
        case_dir = os.path.join(work, case_id); os.makedirs(case_dir)
        source = os.path.join(case_dir, "traces.db")
        harness.build_fixture(python, source, fx)
        writer = None
        if flags.get("active_writer"):
            writer = subprocess.Popen([python, "-B", harness.FIXTURES, "active-writer", source, "--interval", "0.02"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            assert "writer_ready" in writer.stdout.readline()
        if flags.get("delete_before_open"):
            os.remove(source)
        snap_dir = os.path.join(case_dir, "snapshot"); os.makedirs(snap_dir)
        dest = os.path.join(snap_dir, "traces.snapshot.db")
        before = harness.inventory(case_dir)
        acq = acquire(source, dest)
        after = harness.inventory(case_dir)
        if writer is not None:
            writer.terminate(); writer.wait(timeout=10)
        source_changes = [c for c in harness.inventory_diff(before, after) if not c["file"].startswith("snapshot")]
        inspect = None
        if acq.get("ok"):
            env = {"PATH": "/usr/bin:/bin", "HOME": os.path.join(work, "fake-home"), "TMPDIR": run_tmp, "PYTHONDONTWRITEBYTECODE": "1"}
            inspect = harness.run_reader(python, dest, True, profile, env, [])
            snap_before = harness.inventory(snap_dir)
            inspect["snapshot_inventory_changes"] = harness.inventory_diff(snap_before, harness.inventory(snap_dir))
            inspect["sha256"] = hashlib.sha256(open(dest, "rb").read()).hexdigest()[:16]
        results.append({"case": case_id, "description": desc, "acquisition": acq, "source_side_changes": source_changes, "inspection": inspect})
        print(f"[{case_id}] acquisition ok={acq.get('ok')} {acq.get('sqlite_errorname', '')} source changes={[c['file'] + ':' + c['change'] for c in source_changes]}", flush=True)
    time.sleep(4)
    denials, cmd, rc, err = harness.collect_denials(start)
    for r in results:
        if r["inspection"]:
            pid = r["inspection"]["pid"]
            r["inspection"]["denials"] = [dict(d, **{"class": harness.classify(d["path"], os.path.dirname(r["inspection"]["command"][-3]) if False else os.path.join(work, r["case"]), os.path.join(work, r["case"], "snapshot", "traces.snapshot.db"), run_tmp)}) for d in denials if d["pid"] == pid]
            rep = r["inspection"]["report"]
            r["inspection"]["outcome"] = "READ_OK" if rep and all(s.get("ok") for s in rep["steps"]) else "READ_FAILED"
            r["inspection"]["store_write_attempted"] = any(d["class"] in ("STORE", "CASE_DIR") for d in r["inspection"]["denials"])
    out = {"measured_at": start.isoformat(timespec="seconds"), "python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version, "work": work, "results": results}
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    json.dump(out, open(os.path.join(HERE, "results", "backup_probe.json"), "w"), indent=1)
    lines = ["# Backup-API acquisition probe (MEASURED) - for the D-4 gate decision, not an implementation", "",
             f"- measured_at {out['measured_at']}, Python {out['python']}, SQLite {out['sqlite']}", "",
             "| case | acquisition | source-side changes (inventory) | snapshot inspection under deny-write | inspection denials | snapshot files changed |", "|---|---|---|---|---|---|"]
    for r in results:
        a = r["acquisition"]; i = r["inspection"]
        lines.append(f"| {r['case']} | {'OK' if a.get('ok') else 'REFUSED ' + str(a.get('sqlite_errorname'))} ({a.get('elapsed_ms')} ms; src journal {a.get('journal_mode_source', '-')}; dest journal {a.get('journal_mode_dest', '-')}; quick_check {a.get('quick_check', '-')}; rows {a.get('rows', '-')}) | {'; '.join(c['file'] + ' ' + c['change'] for c in r['source_side_changes']) or 'none'} | {i['outcome'] if i else '-'} | {'; '.join(d['class'] + ': ' + d['op'] + ' ' + os.path.basename(d['path']) for d in i['denials']) if i else '-'} | {'; '.join(c['file'] + ' ' + c['change'] for c in i['snapshot_inventory_changes']) if i and i['snapshot_inventory_changes'] else ('none' if i else '-')} |")
    open(os.path.join(HERE, "results", "backup_probe.md"), "w").write("\n".join(lines) + "\n")
    print("wrote results/backup_probe.{json,md}")


if __name__ == "__main__":
    main()
