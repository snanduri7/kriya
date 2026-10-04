#!/usr/bin/env python
"""A1 zero-write SQLite measurement harness (06 §A1, D-4, P-29).

For every fixture case it builds a fresh synthetic store (fixtures.py, outside
the sandbox), snapshots the case directory, runs reader.py twice - once under
``sandbox-exec`` with ``(deny file-write*)`` and once unsandboxed (so an
inventory diff shows what SQLite WOULD write when allowed) - snapshots again,
and finally reads the unified log (``/usr/bin/log show``, no sudo) for every
``Sandbox: ... deny(1) file-write-*`` line of the sandboxed reader PIDs.

Every denial is classified by path:
  STORE          the store, or its -wal/-shm/-journal sidecar  -> counts (D-4)
  CASE_DIR       another path inside the case directory         -> counts
  RUN_TMPDIR     the harness-owned TMPDIR (SQLite scratch)      -> harness/adapter finding
  DEVICE         /dev/*                                          -> process noise, reported
  UNRELATED      anything else                                   -> HARNESS BUG: exit 2
No denial is ever silently ignored.
"""
import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
READER = os.path.join(HERE, "reader.py")
FIXTURES = os.path.join(HERE, "fixtures.py")
PROFILE = "(version 1)\n(allow default)\n(deny file-write*)\n"
DENY_RE = re.compile(r"Sandbox: (?P<proc>\S+)\((?P<pid>\d+)\) deny\(\d+\) (?P<op>file-write\S*) (?P<path>.*)$")

CASES = [
    # id, description, fixture kwargs, extra flags
    ("C01_rollback_clean", "rollback journal (journal_mode=DELETE), closed cleanly", dict(journal="delete"), {}),
    ("C02_rollback_hot_journal", "rollback journal with a HOT -journal left by a crashed writer", dict(journal="delete", hot_journal=True), {}),
    ("C03_wal_clean", "WAL mode, closed cleanly, no -wal/-shm", dict(journal="wal"), {}),
    ("C04_wal_sidecars_orphaned", "WAL mode with -wal and -shm left by a writer that exited without checkpoint", dict(journal="wal", leave_sidecars=True), {}),
    ("C05_wal_active_writer", "WAL mode with an active fixture writer process (outside the sandbox) committing every 20 ms", dict(journal="wal"), {"active_writer": True}),
    ("C06_readonly_dir_rollback", "rollback-journal store in a read-only (0555) directory", dict(journal="delete"), {"readonly_dir": True}),
    ("C07_readonly_dir_wal_clean", "clean WAL store (no sidecars) in a read-only (0555) directory", dict(journal="wal"), {"readonly_dir": True}),
    ("C08_vanish_during_read", "rollback store deleted (with sidecars) after the reader fetched its first list row", dict(journal="delete"), {"vanish": True}),
    ("C09_missing_store", "store path does not exist (deleted before open / never created)", dict(journal="delete"), {"delete_before_open": True}),
]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def inventory(case_dir):
    out = {}
    if not os.path.isdir(case_dir):
        return out
    for name in sorted(os.listdir(case_dir)):
        p = os.path.join(case_dir, name)
        st = os.lstat(p)
        entry = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "mode": oct(stat.S_IMODE(st.st_mode))}
        if stat.S_ISREG(st.st_mode):
            try:
                entry["sha256"] = sha256(p)
            except OSError as e:
                entry["sha256"] = f"unreadable: {e}"
        out[name] = entry
    return out


def inventory_diff(before, after):
    changes = []
    for name in sorted(set(before) | set(after)):
        if name not in before:
            changes.append({"file": name, "change": "created"})
        elif name not in after:
            changes.append({"file": name, "change": "deleted"})
        elif before[name] != after[name]:
            what = [k for k in before[name] if before[name].get(k) != after[name].get(k)]
            changes.append({"file": name, "change": "modified", "fields": what})
    return changes


def classify(path, case_dir, store, run_tmpdir):
    sidecars = {store, store + "-wal", store + "-shm", store + "-journal"}
    if path in sidecars:
        return "STORE"
    if path.startswith(case_dir + os.sep):
        return "CASE_DIR"
    if path.startswith(run_tmpdir + os.sep) or path == run_tmpdir:
        return "RUN_TMPDIR"
    if path.startswith("/dev/"):
        return "DEVICE"
    return "UNRELATED"


def run_reader(python, store, sandboxed, profile_path, env, extra_args, on_marker=None):
    cmd = []
    if sandboxed:
        cmd += ["/usr/bin/sandbox-exec", "-f", profile_path]
    cmd += [python, "-B", READER, store, "--timeout", "2.0"] + extra_args
    started = time.monotonic()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                            env=env, text=True)
    lines = []
    report = None
    for line in proc.stdout:
        line = line.rstrip("\n")
        lines.append(line)
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if "marker" in obj and on_marker:
            on_marker(obj)
        elif "steps" in obj:
            report = obj
    proc.wait(timeout=60)
    stderr = proc.stderr.read()
    return {
        "command": cmd,
        "pid": proc.pid,
        "returncode": proc.returncode,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
        "report": report,
        "stderr": stderr[-2000:],
        "raw_stdout_tail": lines[-3:] if report is None else [],
    }


def build_fixture(python, store, fixture_kwargs):
    cmd = [python, "-B", FIXTURES, "create", store, "--journal", fixture_kwargs.get("journal", "delete"),
           "--rows", "120"]
    if fixture_kwargs.get("leave_sidecars"):
        cmd.append("--leave-sidecars")
    if fixture_kwargs.get("hot_journal"):
        cmd.append("--hot-journal")
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)


def run_case(python, case, variant, work_root, run_tmpdir, profile_path):
    case_id, description, fixture_kwargs, flags = case
    case_dir = os.path.join(work_root, case_id, variant)
    os.makedirs(case_dir)
    store = os.path.join(case_dir, "traces.db")
    build_fixture(python, store, fixture_kwargs)
    writer = None
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": os.path.join(work_root, "fake-home"),  # never the operator's home
        "TMPDIR": run_tmpdir,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    extra = []
    if flags.get("active_writer"):
        writer = subprocess.Popen([python, "-B", FIXTURES, "active-writer", store, "--interval", "0.02"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ready = writer.stdout.readline()
        assert "writer_ready" in ready, ready
    if flags.get("readonly_dir"):
        os.chmod(case_dir, 0o555)
    if flags.get("delete_before_open"):
        os.remove(store)
    if flags.get("vanish"):
        extra = ["--pause-after-first-row", "1.5"]
    before = inventory(case_dir)

    def on_marker(obj):
        if obj.get("marker") == "first_row_fetched" and flags.get("vanish"):
            for suffix in ("", "-journal", "-wal", "-shm"):
                try:
                    os.remove(store + suffix)
                except FileNotFoundError:
                    pass

    result = run_reader(python, store, variant == "sandboxed", profile_path, env, extra, on_marker)
    if writer is not None:
        writer.send_signal(signal.SIGTERM)
        try:
            writer.wait(timeout=10)
        except subprocess.TimeoutExpired:
            writer.kill()
        result["writer"] = {"returncode": writer.returncode, "stdout_tail": writer.stdout.read()[-300:],
                            "stderr_tail": writer.stderr.read()[-300:]}
    if flags.get("readonly_dir"):
        os.chmod(case_dir, 0o755)
    after = inventory(case_dir)
    result.update({
        "case": case_id, "description": description, "variant": variant, "case_dir": case_dir, "store": store,
        "inventory_before": before, "inventory_after": after, "inventory_changes": inventory_diff(before, after),
    })
    return result


def collect_denials(start_time):
    start = (start_time - timedelta(seconds=5)).strftime("%Y-%m-%d %H:%M:%S")
    cmd = ["/usr/bin/log", "show", "--start", start, "--style", "compact",
           "--predicate", 'eventMessage CONTAINS "Sandbox:" AND eventMessage CONTAINS "deny"']
    out = subprocess.run(cmd, capture_output=True, text=True, check=False)
    denials = []
    for line in out.stdout.splitlines():
        m = DENY_RE.search(line)
        if m:
            denials.append({"process": m.group("proc"), "pid": int(m.group("pid")), "op": m.group("op"),
                            "path": m.group("path").strip(), "raw": line.strip()})
    return denials, cmd, out.returncode, out.stderr[-500:]


def summarize_outcome(result):
    rep = result.get("report")
    if rep is None:
        return "READER_CRASHED", "no JSON report"
    failed = [s for s in rep["steps"] if not s.get("ok")]
    if not failed:
        return "READ_OK", "all steps ok"
    names = ", ".join(f"{s['step']}: {s.get('sqlite_errorname') or s.get('error')}" for s in failed)
    return "READ_FAILED", names


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--work-dir", default=None)
    parser.add_argument("--out-dir", default=os.path.join(HERE, "results"))
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    work_root = args.work_dir or tempfile.mkdtemp(prefix="kriya-a1-")
    os.makedirs(work_root, exist_ok=True)
    run_tmpdir = os.path.join(work_root, "tmp")
    os.makedirs(run_tmpdir, exist_ok=True)
    os.makedirs(os.path.join(work_root, "fake-home"), exist_ok=True)
    profile_path = os.path.join(work_root, "deny-file-write.sb")
    with open(profile_path, "w") as f:
        f.write(PROFILE)

    start_time = datetime.now()
    sw_vers = subprocess.run(["sw_vers"], capture_output=True, text=True).stdout
    interp = subprocess.run([args.python, "-B", "-c",
                             "import sqlite3,sys,json;print(json.dumps({'python':sys.version.split()[0],"
                             "'executable':sys.executable,'sqlite_version':sqlite3.sqlite_version}))"],
                            capture_output=True, text=True, check=True).stdout
    results = []
    for case in CASES:
        for variant in ("sandboxed", "unsandboxed"):
            print(f"[{case[0]}] {variant} ...", flush=True)
            results.append(run_case(args.python, case, variant, work_root, run_tmpdir, profile_path))

    time.sleep(4)  # the unified log lags a little
    denials, log_cmd, log_rc, log_err = collect_denials(start_time)
    pid_to_result = {r["pid"]: r for r in results if r["variant"] == "sandboxed"}
    harness_bug = False
    for r in results:
        r["denials"] = []
    unattributed = []
    for d in denials:
        r = pid_to_result.get(d["pid"])
        if r is None:
            unattributed.append(d)
            continue
        d["class"] = classify(d["path"], r["case_dir"], r["store"], run_tmpdir)
        if d["class"] == "UNRELATED":
            harness_bug = True
        r["denials"].append(d)
    for r in results:
        r["outcome"], r["outcome_detail"] = summarize_outcome(r)
        store_denials = [d for d in r.get("denials", []) if d["class"] in ("STORE", "CASE_DIR")]
        r["store_write_attempted"] = bool(store_denials)
        if r["variant"] == "sandboxed":
            r["d4_verdict"] = ("READ_OK_ZERO_WRITE" if r["outcome"] == "READ_OK" and not store_denials
                               else "READ_ONLY_UNAVAILABLE")

    out = {
        "measured_at": start_time.isoformat(timespec="seconds"),
        "host": {"sw_vers": sw_vers.strip().splitlines(), "kernel": platform.release(), "machine": platform.machine(),
                 "sandbox_exec_note": "sandbox-exec is marked DEPRECATED in its man page on this macOS but still enforces the profile (MEASURED: EPERM + unified-log denial lines)."},
        "interpreter": json.loads(interp),
        "harness_interpreter": {"python": sys.version.split()[0], "sqlite_version": sqlite3.sqlite_version},
        "sandbox_profile": PROFILE,
        "log_show_command": log_cmd, "log_show_returncode": log_rc, "log_show_stderr": log_err,
        "work_root": work_root,
        "results": results,
        "unattributed_denials": unattributed,
        "harness_bug_unrelated_denial": harness_bug,
    }
    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, "results.json"), "w") as f:
        json.dump(out, f, indent=1, sort_keys=False)
    write_markdown(out, os.path.join(args.out_dir, "results.md"))
    if not args.keep:
        shutil.rmtree(work_root, ignore_errors=True)
    print(f"wrote {args.out_dir}/results.json and results.md; harness_bug={harness_bug}")
    return 2 if harness_bug else 0


def write_markdown(out, path):
    lines = [
        "# A1 zero-write measurement - generated by harness.py (MEASURED)", "",
        f"- measured_at: {out['measured_at']}",
        f"- host: {' / '.join(out['host']['sw_vers'])}; kernel {out['host']['kernel']}; {out['host']['machine']}",
        f"- reader interpreter: {out['interpreter']['executable']} (Python {out['interpreter']['python']}, SQLite {out['interpreter']['sqlite_version']})",
        f"- sandbox profile: `{out['sandbox_profile'].strip().replace(chr(10), ' ')}`",
        f"- unified-log query: `{' '.join(out['log_show_command'])}` (rc {out['log_show_returncode']})",
        f"- unrelated-path denials (harness bug): {out['harness_bug_unrelated_denial']}; unattributed denial lines: {len(out['unattributed_denials'])}",
        "",
        "| case | variant | outcome | failing steps / sqlite error | denials (class: op path) | inventory changes | D-4 verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in out["results"]:
        den = "; ".join(f"{d['class']}: {d['op']} {os.path.basename(d['path']) if d['class'] in ('STORE','CASE_DIR') else d['path']}"
                        for d in r.get("denials", [])) or "none"
        inv = "; ".join(f"{c['file']} {c['change']}" + (f" ({','.join(c['fields'])})" if c.get('fields') else "")
                        for c in r["inventory_changes"]) or "none"
        lines.append(f"| {r['case']} | {r['variant']} | {r['outcome']} | {r['outcome_detail']} | {den} | {inv} | {r.get('d4_verdict', '-')} |")
    lines += ["", "## Case descriptions", ""]
    for c in CASES:
        lines.append(f"- **{c[0]}**: {c[1]}")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())
