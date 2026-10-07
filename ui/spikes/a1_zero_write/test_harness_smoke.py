"""Fast, fixture-only checks of the A1 harness pieces (run explicitly:
``.newvenv/bin/pytest ui/spikes/a1_zero_write`` - not part of Kriya's suite).
No sandbox, no unified log, no kriya import."""
import os
import sqlite3
import subprocess
import sys

import harness

HERE = os.path.dirname(os.path.abspath(__file__))


def test_denial_line_parses_measured_log_format():
    line = ("2026-10-04 09:06:49.110 E  kernel[0:27fe3e7] (Sandbox) Sandbox: python3.14(67671) deny(1) "
            "file-write-create /private/tmp/x/C03_wal_clean/sandboxed/traces.db-wal")
    m = harness.DENY_RE.search(line)
    assert m and m.group("pid") == "67671" and m.group("op") == "file-write-create"
    assert m.group("path") == "/private/tmp/x/C03_wal_clean/sandboxed/traces.db-wal"


def test_classification_counts_only_store_paths():
    case_dir, store, tmp = "/w/C/sandboxed", "/w/C/sandboxed/traces.db", "/w/tmp"
    assert harness.classify(store + "-shm", case_dir, store, tmp) == "STORE"
    assert harness.classify(store + "-journal", case_dir, store, tmp) == "STORE"
    assert harness.classify(case_dir + "/other", case_dir, store, tmp) == "CASE_DIR"
    assert harness.classify(tmp + "/etilqs_1", case_dir, store, tmp) == "RUN_TMPDIR"
    assert harness.classify("/dev/dtracehelper", case_dir, store, tmp) == "DEVICE"
    assert harness.classify("/Users/someone/.kriya/x", case_dir, store, tmp) == "UNRELATED"


def test_hot_journal_fixture_is_really_hot(tmp_path):
    store = str(tmp_path / "traces.db")
    subprocess.run([sys.executable, "-B", os.path.join(HERE, "fixtures.py"), "create", store,
                    "--journal", "delete", "--hot-journal"], check=True)
    header = open(store + "-journal", "rb").read(8)
    assert header == bytes.fromhex("d9d505f920a163d7"), header.hex()
    conn = sqlite3.connect(harness_uri(store), uri=True)
    try:
        conn.execute("SELECT COUNT(*) FROM runs").fetchone()
        raise AssertionError("expected SQLITE_READONLY_ROLLBACK")
    except sqlite3.OperationalError as error:
        assert error.sqlite_errorname == "SQLITE_READONLY_ROLLBACK"
    finally:
        conn.close()


def harness_uri(path):
    from reader import read_only_uri
    return read_only_uri(path)
