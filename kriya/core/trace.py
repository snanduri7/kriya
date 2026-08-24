import datetime
import json
import os
from typing import Optional

from kriya.core.db import get_connection


class TraceLogger:
    def __init__(self, db_path: str) -> None:
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn = get_connection(self.db_path)
        self.init_db()

    def init_db(self) -> None:
        cursor = self.conn.cursor()
        # Create table with all details without dropping it first (preserves history!)
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
        try:
            cursor.execute("ALTER TABLE runs ADD COLUMN failure_category TEXT")
        except Exception:
            pass
        # Additive, nullable columns for kriya/workflow/milestones.py's
        # orchestrator - existing rows get NULL, nothing existing breaks.
        # milestone_group_id links every milestone (plus the final
        # integration call) belonging to one decomposed goal under one
        # shared, orchestrator-minted UUID (NOT any individual call's own
        # run_id) so a `kriya milestones` view can group/order them; without
        # this, N milestone calls are indistinguishable from N unrelated runs
        # since run_id is this table's PRIMARY KEY and INSERT OR REPLACE has
        # no merge semantics across separate calls.
        for col, coltype in (
            ("milestone_group_id", "TEXT"),
            ("milestone_index", "INTEGER"),
            ("milestone_total", "INTEGER"),
            ("run_events", "TEXT"),
            ("evidence_records", "TEXT"),
            ("generation_metrics", "TEXT"),
            # MA7.6 (kriya/workflow/failure_reporting.py) - additive to,
            # never a replacement for, failure_category above: this is a
            # JSON list of {failure_type, category, attribution_tier} - one
            # entry per real failed attempt (gate_outcomes), letting a
            # human/dashboard ask "what KIND of thing kept failing across
            # this run's attempts", not just "why did the loop stop".
            ("failure_report", "TEXT"),
        ):
            try:
                cursor.execute(f"ALTER TABLE runs ADD COLUMN {col} {coltype}")
            except Exception:
                pass

        # MA3.9 - one row per kriya/workflow/milestones.py::plan_milestones()
        # call, keyed by that call's own group_id (shared with the `runs`
        # rows for the milestones actually executed under it, when the plan
        # is accepted). A SEPARATE table from `runs`, not more nullable
        # columns there - a plan is logged even when it's REJECTED (no
        # MilestoneRunState, no group_id-linked runs rows ever created), and
        # a plan can be re-logged (INSERT OR REPLACE) if the SAME group_id
        # is somehow re-planned, which never applies to an individual run.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS milestone_plans (
                group_id TEXT PRIMARY KEY,
                timestamp TEXT,
                status TEXT,
                schema_version INTEGER,
                milestone_count INTEGER,
                dependency_edges INTEGER,
                extension_count INTEGER,
                composition_count INTEGER,
                validation_attempts INTEGER,
                validation_failures TEXT,
                repository_topology TEXT
            )
        """)
        self.conn.commit()

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
        model_hops: list = None,
        failure_category: Optional[str] = None,
        milestone_group_id: Optional[str] = None,
        milestone_index: Optional[int] = None,
        milestone_total: Optional[int] = None,
        run_events: list = None,
        evidence_records: list = None,
        generation_metrics: dict = None,
        failure_report: list = None,
    ) -> None:
        cursor = self.conn.cursor()
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        files_str = ",".join(files_modified)

        chunks_json = json.dumps(retrieved_chunks or [])
        skills_str = ",".join(active_skills or [])
        gates_json = json.dumps(gate_outcomes or [])
        hops_json = json.dumps(model_hops or [])
        events_json = json.dumps(run_events or [])
        evidence_json = json.dumps(evidence_records or [])
        generation_metrics_json = json.dumps(generation_metrics or {})
        failure_report_json = json.dumps(failure_report or [])

        cursor.execute("""
            INSERT OR REPLACE INTO runs (
                run_id, timestamp, goal, duration_sec, attempts, status, files_modified,
                retrieved_chunks, active_skills, prompt_rendered, gate_outcomes, model_hops,
                failure_category, milestone_group_id, milestone_index, milestone_total,
                run_events, evidence_records, generation_metrics, failure_report
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            run_id, timestamp, goal, duration_sec, attempts, status, files_str,
            chunks_json, skills_str, prompt_rendered, gates_json, hops_json,
            failure_category, milestone_group_id, milestone_index, milestone_total,
            events_json, evidence_json, generation_metrics_json, failure_report_json
        ))
        self.conn.commit()

    def log_milestone_plan(
        self,
        group_id: str,
        status: str,
        schema_version: int,
        milestone_count: int,
        dependency_edges: int,
        extension_count: int,
        composition_count: int,
        validation_attempts: int,
        validation_failures: list,
        repository_topology: dict,
    ) -> None:
        """MA3.9 - see init_db()'s own docstring for why this is a separate
        table from log_run(). `validation_failures` is the flat list of
        reason codes (kriya/workflow/milestone_validation.py's
        DUPLICATE_MILESTONE_ID/UNJUSTIFIED_ENTRYPOINT/etc.) accumulated
        across every REJECTED attempt before this outcome - empty when the
        plan was valid on its first try. `repository_topology` is the
        compact summary the design doc's own telemetry spec calls for
        (build_system/module_count/entrypoint_count), not the full
        RepositoryTopology dataclass."""
        cursor = self.conn.cursor()
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            INSERT OR REPLACE INTO milestone_plans (
                group_id, timestamp, status, schema_version, milestone_count,
                dependency_edges, extension_count, composition_count,
                validation_attempts, validation_failures, repository_topology
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            group_id, timestamp, status, schema_version, milestone_count,
            dependency_edges, extension_count, composition_count,
            validation_attempts, json.dumps(validation_failures), json.dumps(repository_topology),
        ))
        self.conn.commit()

    def close(self) -> None:
        if hasattr(self, "conn") and self.conn:
            self.conn.close()
