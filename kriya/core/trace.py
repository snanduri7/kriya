import os
import sqlite3
import datetime

class TraceLogger:
    def __init__(self, db_path: str) -> None:
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.init_db()

    def init_db(self) -> None:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                timestamp TEXT,
                goal TEXT,
                duration_sec REAL,
                attempts INTEGER,
                status TEXT,
                files_modified TEXT
            )
        """)
        conn.commit()
        conn.close()

    def log_run(self, run_id: str, goal: str, duration_sec: float, attempts: int, status: str, files_modified: list) -> None:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        files_str = ",".join(files_modified)
        cursor.execute("""
            INSERT OR REPLACE INTO runs (run_id, timestamp, goal, duration_sec, attempts, status, files_modified)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (run_id, timestamp, goal, duration_sec, attempts, status, files_str))
        conn.commit()
        conn.close()
