"""Fixture helpers for the KUP (Kriya UI Protocol) tests: deterministic trace
stores and a generation-aware fixture writer with cross-table invariants
(GUI-D4-READ-STRATEGY/03_GATE.md C-5). Fixtures only; never a real store."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
from typing import Iterable, List, Optional

from kriya.core.trace import TraceLogger
from kriya.kup.store import RAW_CONNECT

RUN_COLUMNS = (
    "run_id, timestamp, goal, duration_sec, attempts, status, files_modified, retrieved_chunks, active_skills, "
    "prompt_rendered, gate_outcomes, model_hops, failure_category, milestone_group_id, milestone_index, "
    "milestone_total, run_events, evidence_records, generation_metrics, failure_report"
)


def deterministic_rows(count: int, equal_timestamps: bool = False) -> List[tuple]:
    rows = []
    for i in range(count):
        ts = "2026-09-20 12:00:00" if equal_timestamps else f"2026-09-{(i % 28) + 1:02d} 1{(i // 28) % 10}:00:00"
        rows.append((
            f"run-{i:04d}", ts, f"Goal number {i}", 1.23 + i, (i % 3) + 1,
            "success" if i % 2 == 0 else "failure", "a.py,b.py",
            json.dumps([{"file": "a.py", "score": 0.9}]), "skill-a",
            f"PLANNING PROMPT {i}",
            json.dumps([{"attempt": 1, "gate": "compile", "passed": i % 2 == 0}]),
            "[]", None if i % 2 == 0 else "quality_gate_failed",
            f"group-{i // 5}" if i % 2 else None, i % 5 if i % 2 else None, 5 if i % 2 else None,
            # Kriya's own event shape (kriya/workflow/run_events.py::RunEvent.to_dict): kind, attempt, source,
            # authority, message, failure_type, operation, details, created_at (epoch seconds), plus an unknown field.
            json.dumps([{"kind": "gate.compile", "attempt": 1, "source": "workflow", "authority": "authoritative",
                         "message": f"compile gate of run {i}", "failure_type": None, "operation": None,
                         "details": {"k": i}, "created_at": 1758369600.0 + i, "novel_field": "kept"}]),
            json.dumps([{"evidence_id": f"ev-{i}"}]), json.dumps({"prompt_tokens_estimated": 100 + i}),
            json.dumps([{"failure_type": "compile", "category": "quality_gate_failed", "attribution_tier": "locator"}]) if i % 2 else "[]",
        ))
    return rows


def seed_store(db_path: str, count: int = 7, equal_timestamps: bool = False) -> str:
    """A WAL-mode store exactly as Kriya creates it (TraceLogger -> get_connection -> wal_connect),
    with deterministic rows written through the same schema. Closed cleanly: the A1 C03 resting state."""
    TraceLogger(db_path)  # creates the schema with Kriya's own code
    conn = sqlite3.connect(db_path)
    with conn:
        conn.executemany(f"INSERT OR REPLACE INTO runs ({RUN_COLUMNS}) VALUES ({','.join('?' * 20)})", deterministic_rows(count, equal_timestamps))
    conn.close()
    return db_path


class GenerationWriter:
    """A fixture writer with KNOWN committed generations and a cross-table invariant (C-5).

    Generation N commits, in ONE transaction: a `runs` row `gen-N`, a `milestone_plans` row `gen-N`, and
    `kup_fixture_generation.gen = N` with `digest` = sha256 over the ordered gen run_ids. A consistent image
    therefore satisfies: count(runs gen-*) == count(milestone_plans gen-*) == gen and digest matches. A torn
    image cannot satisfy all three. Committed generations are recorded in `committed_log` AFTER each commit."""

    def __init__(self, db_path: str, committed_log: Optional[str] = None, timeout: float = 5.0) -> None:
        self.db_path = db_path
        self.committed_log = committed_log
        self.conn = sqlite3.connect(db_path, timeout=timeout, isolation_level=None)
        self.conn.execute("CREATE TABLE IF NOT EXISTS kup_fixture_generation (id INTEGER PRIMARY KEY CHECK (id = 1), gen INTEGER NOT NULL, digest TEXT NOT NULL)")
        self.conn.execute("INSERT OR IGNORE INTO kup_fixture_generation (id, gen, digest) VALUES (1, 0, '')")

    def current_generation(self) -> int:
        return int(self.conn.execute("SELECT gen FROM kup_fixture_generation WHERE id = 1").fetchone()[0])

    def begin_generation(self) -> int:
        """Open generation gen+1 and write its rows WITHOUT committing (for barrier-forced interleavings)."""
        gen = self.current_generation() + 1
        self.conn.execute("BEGIN IMMEDIATE")
        ids = [f"gen-{g:06d}" for g in range(1, gen + 1)]
        digest = hashlib.sha256("\n".join(ids).encode()).hexdigest()
        self.conn.execute(
            f"INSERT INTO runs ({RUN_COLUMNS}) VALUES ({','.join('?' * 20)})",
            (f"gen-{gen:06d}", f"2026-10-01 00:{gen // 60 % 60:02d}:{gen % 60:02d}", f"generation {gen}", float(gen), 1, "success",
             "x.py", "[]", "", "", "[]", "[]", None, None, None, None, "[]", "[]", "{}", "[]"))
        self.conn.execute("INSERT INTO milestone_plans (group_id, timestamp, status, schema_version, milestone_count) VALUES (?, ?, 'accepted', 1, 1)",
                          (f"gen-{gen:06d}", f"2026-10-01 00:{gen // 60 % 60:02d}:{gen % 60:02d}"))
        self.conn.execute("UPDATE kup_fixture_generation SET gen = ?, digest = ? WHERE id = 1", (gen, digest))
        return gen

    def commit_generation(self, gen: int) -> None:
        self.conn.execute("COMMIT")
        if self.committed_log:
            with open(self.committed_log, "a", encoding="utf-8") as f:
                f.write(f"{gen}\n")

    def write_generation(self) -> int:
        gen = self.begin_generation()
        self.commit_generation(gen)
        return gen

    def checkpoint(self, mode: str = "TRUNCATE") -> tuple:
        return tuple(self.conn.execute(f"PRAGMA wal_checkpoint({mode})").fetchone())

    def close(self) -> None:
        self.conn.close()


def committed_generations(committed_log: str) -> List[int]:
    if not os.path.exists(committed_log):
        return []
    with open(committed_log, encoding="utf-8") as f:
        return [int(line) for line in f if line.strip()]


def generation_invariant(db_path: str) -> dict:
    """Evaluate the cross-table invariant on an arbitrary database file (read-only URI, no pragma that writes)."""
    uri = "file:" + db_path + "?mode=ro"
    conn = RAW_CONNECT(uri, uri=True)  # never kriya.core.db's patched connect: it would attempt a WAL pragma
    try:
        gen, digest = conn.execute("SELECT gen, digest FROM kup_fixture_generation WHERE id = 1").fetchone()
        run_ids = [r[0] for r in conn.execute("SELECT run_id FROM runs WHERE run_id LIKE 'gen-%' ORDER BY run_id").fetchall()]
        plans = conn.execute("SELECT COUNT(*) FROM milestone_plans WHERE group_id LIKE 'gen-%'").fetchone()[0]
        expected = hashlib.sha256("\n".join(run_ids).encode()).hexdigest() if run_ids else ""
        consistent = (len(run_ids) == gen == plans) and (digest == expected)
        return {"generation": int(gen), "runs": len(run_ids), "plans": int(plans), "digest_ok": digest == expected, "consistent": consistent}
    finally:
        conn.close()


def wait_for(predicate, timeout: float = 10.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def file_names(directory: str) -> Iterable[str]:
    return sorted(os.listdir(directory)) if os.path.isdir(directory) else []


# ---------------------------------------------------------------------------------------------------------------
# The GUI host's production launch policy for the real `kriya` child (owner decision 2026-10-04; 08 review F-1/F-2).
# EXACT COPY of the rule in the GUI checkout's ui/standalone/src/main/child_env.ts - keep the two in step. The
# Kriya-side tests build the child environment from THIS rule so the sandbox evidence is produced under the same
# environment the real app provides, never a more permissive test-only one.
# ---------------------------------------------------------------------------------------------------------------
HOST_CHILD_PATH = "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"
HOST_CHILD_ENV_KEYS = ("PATH", "PYTHONDONTWRITEBYTECODE", "HOME", "KRIYA_STATE_DIR")


def host_child_env(home: str, state_dir: Optional[str] = None) -> dict:
    """Fixed approved PATH; fixed PYTHONDONTWRITEBYTECODE=1 (set before the interpreter imports anything, which
    `sys.dont_write_bytecode` inside the command cannot do); the operator's HOME; optionally the operator's
    KRIYA_STATE_DIR, absolute only. Nothing else: no PYTHONPATH/PYTHONHOME, no KRIYA_TRUST_FILE, no credentials, no
    config-path variable (configuration discovery stays Kriya's own: CWD, then the install directory)."""
    if not isinstance(home, str) or not os.path.isabs(home):
        raise ValueError("HOME must be an absolute path")
    env = {"PATH": HOST_CHILD_PATH, "PYTHONDONTWRITEBYTECODE": "1", "HOME": home}
    if state_dir is not None:
        if not isinstance(state_dir, str) or not os.path.isabs(state_dir):
            raise ValueError("KRIYA_STATE_DIR must be an absolute path")
        env["KRIYA_STATE_DIR"] = state_dir
    return env


def fresh_package_tree(dest: str) -> str:
    """A copy of the installed `kriya` package WITHOUT any __pycache__ / .pyc: what a fresh install looks like to the
    interpreter, so a bytecode write attempt on first import is observable (the checkout's own tree is already cached)."""
    import shutil

    import kriya

    shutil.copytree(os.path.dirname(kriya.__file__), os.path.join(dest, "kriya"), ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return dest


def cli_subprocess_argv(fresh_root: Optional[str] = None) -> List[str]:
    """How a test starts the real CLI entry point in a subprocess. Without ``fresh_root``: the installed console script
    (exactly what the GUI host executes), else `python -c` running the console script's own two lines. With
    ``fresh_root``: the package is imported from that tree, asserted inside the child so a wrong import location fails
    loudly instead of silently testing the cached checkout."""
    if fresh_root is None:
        script = os.path.join(os.path.dirname(sys.executable), "kriya")
        if os.path.exists(script):
            return [script]
        return [sys.executable, "-c", "import sys; from kriya.cli import main; sys.exit(main())"]
    program = ("import sys; sys.path.insert(0, %r); import kriya; assert kriya.__file__.startswith(%r), kriya.__file__; "
               "from kriya.cli import main; sys.exit(main())") % (fresh_root, fresh_root)
    return [sys.executable, "-c", program]
