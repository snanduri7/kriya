import os
import json
import sqlite3
import datetime

_orig_connect = sqlite3.connect
def wal_connect(*args, **kwargs):
    kwargs["timeout"] = 30.0
    conn = _orig_connect(*args, **kwargs)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
    except Exception:
        pass
    return conn
sqlite3.connect = wal_connect

class TraceLogger:
    def __init__(self, db_path: str) -> None:
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.init_db()

    def init_db(self) -> None:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DROP TABLE IF EXISTS runs")
        # Create table with all details
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                timestamp TEXT,
                goal TEXT,
                duration_sec REAL,
                attempts INTEGER,
                status TEXT,
                files_modified TEXT,
                retrieved_chunks TEXT,
                active_skills TEXT,
                prompt_rendered TEXT,
                gate_outcomes TEXT,
                model_hops TEXT
            )
        """)
        conn.commit()
        conn.close()

    def log_run(
        self, 
        run_id: str, 
        goal: str, 
        duration_sec: float, 
        attempts: int, 
        status: str, 
        files_modified: list,
        retrieved_chunks: list = None,
        active_skills: list = None,
        prompt_rendered: str = "",
        gate_outcomes: list = None,
        model_hops: list = None
    ) -> None:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        files_str = ",".join(files_modified)
        
        chunks_json = json.dumps(retrieved_chunks or [])
        skills_str = ",".join(active_skills or [])
        gates_json = json.dumps(gate_outcomes or [])
        hops_json = json.dumps(model_hops or [])
        
        cursor.execute("""
            INSERT OR REPLACE INTO runs (
                run_id, timestamp, goal, duration_sec, attempts, status, files_modified,
                retrieved_chunks, active_skills, prompt_rendered, gate_outcomes, model_hops
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            run_id, timestamp, goal, duration_sec, attempts, status, files_str,
            chunks_json, skills_str, prompt_rendered, gates_json, hops_json
        ))
        conn.commit()
        conn.close()
