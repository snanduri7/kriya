"""KUP snapshot acquisition (GUI M1 Phase C; handover/GUI-D4-READ-STRATEGY/03_GATE.md C-2, C-4, C-5).

Fixtures only. Every published snapshot must be ONE valid committed generation of the fixture writer (cross-table
invariant, known committed log) or acquisition must refuse typed. Interleavings are forced with the acquisition's
step hook (deterministic barriers), never with sleeps alone."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading

import pytest
from _kup_fixtures import GenerationWriter, committed_generations, generation_invariant, seed_store

from kriya.kup import policy
from kriya.kup.acquire import acquire_snapshot, prune_snapshots
from kriya.kup.store import RAW_CONNECT, SnapshotError, list_snapshots, require_snapshot, stat_metadata, verify_digest

BIG_ROWS = 400  # enough pages for several backup steps at step_pages=8


@pytest.fixture
def store(tmp_path):
    src = str(tmp_path / "state" / "traces.db")
    os.makedirs(os.path.dirname(src))
    seed_store(src, BIG_ROWS)
    return {"src": src, "snapdir": str(tmp_path / "state" / "kup-snapshots"), "log": str(tmp_path / "committed.log"), "tmp": tmp_path}


def _assert_one_committed_generation(db_path: str, log: str) -> int:
    inv = generation_invariant(db_path)
    assert inv["consistent"], inv
    assert inv["generation"] in committed_generations(log) or inv["generation"] == 0, (inv, committed_generations(log))
    return inv["generation"]


def _side_files(src: str) -> dict:
    return {s: os.path.exists(src + s) for s in ("-wal", "-shm", "-journal")}


# ------------------------------------------------------------------ consistency with a live writer

def test_quiet_store_snapshot_is_the_committed_generation(store):
    w = GenerationWriter(store["src"], store["log"])
    for _ in range(3):
        w.write_generation()
    w.close()
    result = acquire_snapshot(store["src"], store["snapdir"], step_pages=8)
    snap = result["snapshot"]
    assert _assert_one_committed_generation(snap.db_path, store["log"]) == 3
    assert result["manifest"]["backup_steps"] > 1
    assert verify_digest(snap) is None
    assert oct(os.stat(snap.db_path).st_mode & 0o777) == "0o400"
    assert oct(os.stat(snap.directory).st_mode & 0o777) == "0o700"
    assert RAW_CONNECT("file:" + snap.db_path + "?mode=ro", uri=True).execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_writer_commits_between_backup_steps_snapshot_stays_one_generation(store):
    """Barrier: after the first backup step the writer commits generation 4 and checkpoints; the image must still be
    exactly generation 3 (the read transaction's snapshot) - never a mix, never torn."""
    w = GenerationWriter(store["src"], store["log"])
    for _ in range(3):
        w.write_generation()
    seen = []

    def hook(progress):
        seen.append(progress)
        if progress["step"] == 1:
            w.write_generation()
            w.checkpoint("PASSIVE")
    result = acquire_snapshot(store["src"], store["snapdir"], step_pages=8, step_hook=hook)
    w.close()
    assert len(seen) > 1, "the barrier must fire mid-copy, before the backup finished"
    gen = _assert_one_committed_generation(result["snapshot"].db_path, store["log"])
    assert gen == 3, f"snapshot must be the generation committed before the read transaction began, got {gen}"
    assert committed_generations(store["log"]) == [1, 2, 3, 4]


def test_uncommitted_writer_transaction_is_never_in_the_snapshot(store):
    w = GenerationWriter(store["src"], store["log"])
    w.write_generation()
    gen = w.begin_generation()  # open, not committed, written rows pending in the writer's WAL frames

    def hook(progress):
        if progress["step"] == 1:
            w.commit_generation(gen)
    result = acquire_snapshot(store["src"], store["snapdir"], step_pages=8, step_hook=hook)
    w.close()
    assert _assert_one_committed_generation(result["snapshot"].db_path, store["log"]) == 1


def test_truncate_checkpoint_during_backup_does_not_tear_the_image(store):
    w = GenerationWriter(store["src"], store["log"])
    for _ in range(2):
        w.write_generation()

    def hook(progress):
        if progress["step"] == 1:
            w.write_generation()
            w.checkpoint("TRUNCATE")  # a reader holds its snapshot, so TRUNCATE cannot complete past it
    result = acquire_snapshot(store["src"], store["snapdir"], step_pages=8, step_hook=hook)
    w.close()
    assert _assert_one_committed_generation(result["snapshot"].db_path, store["log"]) == 2


def test_source_file_replaced_during_backup_snapshot_is_the_opened_image(store):
    w = GenerationWriter(store["src"], store["log"])
    w.write_generation()
    w.close()
    replacement = store["src"] + ".new"
    seed_store(replacement, 5)
    before = stat_metadata(store["src"])

    def hook(progress):
        if progress["step"] == 1:
            os.replace(replacement, store["src"])  # another process swaps the file under the path
    result = acquire_snapshot(store["src"], store["snapdir"], step_pages=8, step_hook=hook)
    assert _assert_one_committed_generation(result["snapshot"].db_path, store["log"]) == 1
    m = result["manifest"]
    assert m["source_metadata_at_acquisition"]["inode"] == before["inode"]
    assert m["source_metadata_after_backup"]["inode"] != before["inode"], "metadata records the replacement"


def test_side_files_appear_and_disappear_around_acquisition(store):
    # clean WAL store (C03): acquisition creates -wal/-shm and leaves them; a later writer's close removes them.
    assert _side_files(store["src"]) == {"-wal": False, "-shm": False, "-journal": False}
    acquire_snapshot(store["src"], store["snapdir"])
    assert _side_files(store["src"]) == {"-wal": True, "-shm": True, "-journal": False}
    w = GenerationWriter(store["src"], store["log"])
    w.write_generation()
    w.close()  # the last connection checkpoints and removes the side files
    assert _side_files(store["src"]) == {"-wal": False, "-shm": False, "-journal": False}
    result = acquire_snapshot(store["src"], store["snapdir"])
    assert _assert_one_committed_generation(result["snapshot"].db_path, store["log"]) == 1


def test_subsequent_writer_operates_normally_after_acquisition(store):
    acquire_snapshot(store["src"], store["snapdir"])
    w = GenerationWriter(store["src"], store["log"])
    for _ in range(5):
        w.write_generation()
    assert w.checkpoint("TRUNCATE")[0] == 0, "a Kriya-style writer can still checkpoint after acquisition"
    w.close()
    assert generation_invariant(store["src"])["generation"] == 5
    main_size_before = os.path.getsize(store["src"])
    acquire_snapshot(store["src"], store["snapdir"])
    assert os.path.getsize(store["src"]) == main_size_before


# ------------------------------------------------------------------ refusals

def test_hot_journal_and_missing_store_are_refused_typed(tmp_path):
    src = str(tmp_path / "s" / "traces.db")
    with pytest.raises(SnapshotError) as excinfo:
        acquire_snapshot(src, str(tmp_path / "s" / "kup-snapshots"))
    assert excinfo.value.code == policy.READ_ONLY_UNAVAILABLE and excinfo.value.database_state == policy.DB_STATE_MISSING
    assert not os.path.exists(src)
    assert not os.path.exists(str(tmp_path / "s" / "kup-snapshots")), "nothing is created for a store that cannot be acquired"
    # hot journal: a rollback-journal store whose writer crashed mid-transaction after a cache spill
    os.makedirs(os.path.dirname(src), exist_ok=True)
    conn = RAW_CONNECT(src)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, goal TEXT)")
    conn.commit()
    conn.execute("PRAGMA cache_size=10")
    conn.executemany("INSERT INTO runs VALUES (?, ?)", ((f"r{i}", "x" * 2000) for i in range(300)))
    # simulate the crash: leave the journal hot by killing the connection without rollback
    script = (f"import sqlite3; c = sqlite3.connect({src!r}); c.execute('PRAGMA journal_mode=DELETE'); c.execute('PRAGMA cache_size=10');"
              " c.executemany('INSERT INTO runs VALUES (?, ?)', ((f'h{i}', 'y' * 2000) for i in range(300))); import os; os._exit(0)")
    conn.rollback()
    conn.close()
    subprocess.run([sys.executable, "-c", script], check=True)
    assert os.path.exists(src + "-journal")
    with pytest.raises(SnapshotError) as excinfo:
        acquire_snapshot(src, str(tmp_path / "s" / "kup-snapshots"))
    assert excinfo.value.code == policy.STORE_BUSY and excinfo.value.database_state == policy.DB_STATE_HOT_JOURNAL
    assert os.path.exists(src + "-journal"), "acquisition never recovers another writer's journal"
    assert list_snapshots(str(tmp_path / "s" / "kup-snapshots")) == []


def test_oversized_store_is_refused_and_nothing_is_published(store):
    with pytest.raises(SnapshotError) as excinfo:
        acquire_snapshot(store["src"], store["snapdir"], max_bytes=64 * 1024)
    assert excinfo.value.code == policy.SNAPSHOT_TOO_LARGE
    assert [n for n in os.listdir(store["snapdir"]) if not n.startswith(".")] == []


def test_deadline_exceeded_mid_copy_leaves_nothing_published(store):
    def slow(progress):
        if progress["step"] == 1:
            import time
            time.sleep(0.3)
    with pytest.raises(SnapshotError) as excinfo:
        acquire_snapshot(store["src"], store["snapdir"], step_pages=8, step_hook=slow, deadline_seconds=0.1)
    assert excinfo.value.code == policy.SNAPSHOT_FAILED and excinfo.value.extra["reason"] == "deadline_exceeded"
    assert [n for n in os.listdir(store["snapdir"]) if not n.startswith(".")] == []


def test_run_active_policy_refuses_before_touching_anything(store):
    with pytest.raises(SnapshotError) as excinfo:
        acquire_snapshot(store["src"], store["snapdir"], run_active_check=lambda: "pid 4242 holds the run lock")
    assert excinfo.value.code == policy.ACQUISITION_REFUSED_RUN_ACTIVE
    assert not os.path.exists(store["snapdir"])
    assert _side_files(store["src"])["-shm"] is False


# ------------------------------------------------------------------ concurrency, interruption, cleanup, retention

def test_concurrent_acquisition_is_refused_typed_and_the_first_completes(store):
    entered = threading.Event()
    release = threading.Event()
    outcome = {}

    def hook(progress):
        if progress["step"] == 1:
            entered.set()
            release.wait(5)

    def first():
        outcome["first"] = acquire_snapshot(store["src"], store["snapdir"], step_pages=8, step_hook=hook)
    t = threading.Thread(target=first)
    t.start()
    assert entered.wait(5)
    with pytest.raises(SnapshotError) as excinfo:
        acquire_snapshot(store["src"], store["snapdir"])
    assert excinfo.value.code == policy.ACQUISITION_IN_PROGRESS
    with pytest.raises(SnapshotError) as excinfo2:
        prune_snapshots(store["snapdir"])
    assert excinfo2.value.code == policy.ACQUISITION_IN_PROGRESS
    staging = [n for n in os.listdir(store["snapdir"]) if n.startswith(policy.STAGING_PREFIX)]
    assert len(staging) == 1, "the active acquisition's staging directory is present and untouched"
    release.set()
    t.join(10)
    assert [s.snapshot_id for s in list_snapshots(store["snapdir"])] == [outcome["first"]["snapshot"].snapshot_id]
    assert not [n for n in os.listdir(store["snapdir"]) if n.startswith(policy.STAGING_PREFIX)]


def test_interrupted_acquisition_leaves_nothing_published_and_is_cleaned_up_next_time(store):
    """A SIGKILL mid-copy (a real subprocess): staging stays behind, nothing is published; the next acquisition
    (which holds the lock, so the orphan is proven abandoned) removes it."""
    script = f"""
import os, sys
sys.path.insert(0, {os.getcwd()!r})
from kriya.kup.acquire import acquire_snapshot
def hook(p):
    if p['step'] == 1:
        print('MID', flush=True)
        os.kill(os.getpid(), 9)
acquire_snapshot({store['src']!r}, {store['snapdir']!r}, step_pages=8, step_hook=hook)
"""
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)
    assert "MID" in proc.stdout and proc.returncode == -9
    names = os.listdir(store["snapdir"])
    staging = [n for n in names if n.startswith(policy.STAGING_PREFIX)]
    assert len(staging) == 1 and list_snapshots(store["snapdir"]) == []
    result = acquire_snapshot(store["src"], store["snapdir"])
    assert result["orphans_removed"] == staging
    assert [s.snapshot_id for s in list_snapshots(store["snapdir"])] == [result["snapshot"].snapshot_id]
    assert not [n for n in os.listdir(store["snapdir"]) if n.startswith(policy.STAGING_PREFIX)]


def test_retention_keeps_newest_three_and_prune_is_explicit(store):
    ids = [acquire_snapshot(store["src"], store["snapdir"])["snapshot"].snapshot_id for _ in range(5)]
    kept = [s.snapshot_id for s in list_snapshots(store["snapdir"])]
    assert kept == sorted(ids[-3:], reverse=True)
    with pytest.raises(SnapshotError) as excinfo:
        require_snapshot(store["snapdir"], ids[0])
    assert excinfo.value.code == policy.SNAPSHOT_UNAVAILABLE
    result = prune_snapshots(store["snapdir"], keep=1)
    assert result["kept"] == [ids[-1]] and sorted(result["removed"]) == sorted(ids[-3:-1])
    assert prune_snapshots(store["snapdir"], keep=0)["kept"] == []
    assert os.path.exists(store["src"]), "prune never touches the live store"


def test_require_snapshot_detects_a_tampered_or_missing_file(store):
    snap = acquire_snapshot(store["src"], store["snapdir"])["snapshot"]
    os.chmod(snap.db_path, 0o600)
    with open(snap.db_path, "ab") as f:
        f.write(b"x")
    with pytest.raises(SnapshotError) as excinfo:
        require_snapshot(store["snapdir"], snap.snapshot_id)
    assert excinfo.value.code == policy.SNAPSHOT_CORRUPT
    with pytest.raises(SnapshotError) as missing:
        require_snapshot(str(store["tmp"] / "nowhere"), snap.snapshot_id)
    assert missing.value.code == policy.SNAPSHOT_MISSING


def test_manifest_records_both_acquisition_times_and_source_metadata(store):
    m = acquire_snapshot(store["src"], store["snapdir"])["manifest"]
    assert m["acquisition_started_at"] <= m["acquisition_completed_at"]
    assert set(m["source_metadata_at_acquisition"]) >= {"size", "mtime_ns", "inode", "wal_size", "shm_size"}
    assert m["source_journal_mode"] == "wal" and m["quick_check"] == "ok" and m["rows"] == BIG_ROWS
    with open(os.path.join(os.path.dirname(m["source"]), "kup-snapshots", m["snapshot_id"], "manifest.json"), encoding="utf-8") as f:
        assert json.load(f)["snapshot_file"]["sha256"] == m["snapshot_file"]["sha256"]
    # the source connection must have left the journal mode alone
    conn = RAW_CONNECT("file:" + store["src"] + "?mode=ro", uri=True)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.close()
    assert sqlite3.sqlite_version == m["sqlite_version"]
