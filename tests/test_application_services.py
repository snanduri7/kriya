"""APPLICATION read-side services (full modularization, slice B2): golden envelopes through the KUP adapter.

The goldens in ``tests/golden/kup/application/`` were WRITTEN FROM THE PRE-B2 CODE (the KUP operation bodies at e91f05d, before
any of them delegated to ``kriya.application``) and are compared byte for byte after normalization: every operation the
services now answer must produce exactly the envelope it produced before. Normalization replaces only values that differ
between two runs of the SAME code (temporary paths, the workspace identity derived from them, the snapshot id, wall-clock
times, durations, chain digests over timestamped records): a field that moves, disappears, changes type or changes value
fails. Regenerate (only from a commit whose envelopes are known good) with ``KRIYA_WRITE_KUP_GOLDEN=1``.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
from _kup_evidence_fixtures import candidate_store, model_call_store, rich_store, state_dir
from _strict_doubles import strict_config

from kriya.config import AppConfig
from kriya.context.workspace_identity import workspace_identity
from kriya.core.attempt_evidence import model
from kriya.core.attempt_evidence.writer import AttemptEvidenceWriter
from kriya.core.state_paths import trace_db_path
from kriya.core.trace import TraceLogger
from kriya.kup.cli_ops import run_kup_operation
from kriya.kup.inspect import encode_response

GOLDEN = Path(__file__).resolve().parent / "golden" / "kup" / "application"
WRITE = os.environ.get("KRIYA_WRITE_KUP_GOLDEN") == "1"
# Keys whose values differ between two runs of identical code (MEASURED by generating twice): wall clock, durations, and digests
# that cover timestamped records or a freshly written snapshot file.
VOLATILE_KEYS = frozenset({"observed_at", "verified_at", "duration_ms", "acquired_at", "created_at", "sealed_at", "opened_at", "ts",
                           "mtime_ns", "inode", "sha256", "head_digest", "digest", "prev_digest", "record_digest", "chain_digest",
                           "seal_digest", "size", "bytes_on_disk", "wal_size", "shm_size", "journal_size", "source_metadata",
                           "metadata_at_acquire", "sqlite_version", "commit", "kriya_version", "build_provenance", "age_seconds",
                           "next_cursor", "cursor", "acquisition_started_at", "acquisition_completed_at", "t_mono_ms", "t_wall", "prev"})
# execution.model_probe lists endpoints in probe COMPLETION order (threads; MEASURED to vary between runs): compared as a set.
UNORDERED_KEYS = frozenset({"endpoints"})
RUN_TIMESTAMPS = {"t-old": "2026-01-01 10:00:00", "t-linked": "2026-01-02 10:00:00", "t-gate": "2026-01-03 10:00:00",
                  "t-other": "2026-01-04 10:00:00"}
GATES = [{"attempt": 1, "type": "compile", "success": True},
         {"attempt": 1, "type": "run_verification", "success": True, "steps": [
             {"command": ["java", "-jar", "app.jar", "--token=hunter2supersecret"], "exit_code": 0, "stdout": "Hello\n", "stderr": "", "timed_out": False}]}]


def _events(evidence_run_id: str | None) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = [{"kind": "egress.authority", "attempt": 0, "source": "workflow", "authority": "auxiliary", "message": "x",
                                     "failure_type": None, "operation": None, "details": {}, "created_at": 1.0}]
    if evidence_run_id is not None:
        events.append({"kind": "evidence.attempt_store", "attempt": 0, "source": "workflow.run_generation_workflow", "authority": "auxiliary",
                       "message": "attempt evidence store", "failure_type": None, "operation": None,
                       "details": {"run_id": evidence_run_id, "status": "CLOSED", "path": "/recorded/path"}, "created_at": 2.0})
    return events


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    """One deterministic state: a workspace folder (not a Git checkout) with two skills and a kriya.yaml, four trace runs (one linked to an
    evidence store recording this workspace, one to a store recording another, one with a recorded runtime gate, one pre-recorder), three
    evidence stores, and an acquired snapshot. Catalog/context configuration is injected as RESOLVED (the operator-only sections)."""
    ws = tmp_path / "Work Space"
    skills = ws / "skills"
    (skills / "alpha").mkdir(parents=True)
    (skills / "alpha" / "skill.yaml").write_text("name: alpha\ndescription: Alpha rules\ncategory: Framework\ntags: [a, b]\nverified: true\n"
                                                  "verified_context: ran gate\nverified_at: '2026-10-01'\n")
    (skills / "alpha" / "rules.txt").write_text("RULE one\nRULE two\n")
    (skills / "beta").mkdir()
    (skills / "beta" / "skill.yaml").write_text("name: beta\ndescription: Beta\n")
    (ws / "kriya.yaml").write_text("paths:\n  skills: ./skills\nskills:\n  load_global: false\n")
    wsid = workspace_identity(str(ws))
    linked = AttemptEvidenceWriter(state_dir(), "ev-linked", capture=model.CAPTURE_FULL, manifest={"workspace_identity": wsid, "workspace_name": "fixture"})
    linked.append("run.opened", {})
    assert linked.seal("run_closed")
    other = AttemptEvidenceWriter(state_dir(), "ev-other", capture=model.CAPTURE_FULL, manifest={"workspace_identity": "f" * 64})
    assert other.seal("run_closed")
    rich_store("ev-rich")
    candidate_store("ev-cand")
    model_call_store("ev-call")
    tracer = TraceLogger(trace_db_path(AppConfig()))
    tracer.log_run(run_id="t-old", goal="old goal", duration_sec=1.0, attempts=1, status="SUCCESS", files_modified=[], run_events=_events(None))
    tracer.log_run(run_id="t-linked", goal="linked goal", duration_sec=2.0, attempts=1, status="SUCCESS", files_modified=["a.py"],
                   run_events=_events("ev-linked"), prompt_rendered="the prompt")
    tracer.log_run(run_id="t-gate", goal="gate goal", duration_sec=3.0, attempts=2, status="SUCCESS", files_modified=[], run_events=_events("ev-rich"),
                   gate_outcomes=GATES)
    tracer.log_run(run_id="t-other", goal="other goal", duration_sec=4.0, attempts=1, status="FAILURE", files_modified=[],
                   run_events=_events("ev-other"), failure_category="quality_gate_failed")
    conn = sqlite3.connect(trace_db_path(AppConfig()))
    try:
        for run_id, stamp in RUN_TIMESTAMPS.items():  # fixed times: the history order must not depend on the wall clock
            conn.execute("UPDATE runs SET timestamp = ? WHERE run_id = ?", (stamp, run_id))
        conn.commit()
    finally:
        conn.close()
    acquired = run_kup_operation(AppConfig(), {"operation": "snapshot.acquire"})
    assert acquired["error"] is None, acquired
    cfg = strict_config(paths={"skills": str(skills), "memory": str(tmp_path / "mem")}, skills={"load_global": False, "load_cwd": False},
                        mcp={"files": {"command": "/opt/server", "args": ["--root", "/secret"], "env": {"TOKEN": "x"},
                                       "capabilities": {"workspace_read": True}}},
                        agent_llms={"reviewer": {"llm": {"model": "review-model", "base_url": "http://127.0.0.1:9/v1"}}},
                        llm_chain=[{"model": "fallback-model", "base_url": "http://127.0.0.1:8/v1"}])
    authority = {"status": "no_findings", "fields": []}
    monkeypatch.setattr("kriya.kup.catalog_ops._load_in", lambda w: (None, cfg, authority, None))
    monkeypatch.setattr("kriya.kup.context_ops._load_in", lambda w: (None, cfg, authority, None))
    monkeypatch.setattr("kriya.kup.context_ops._probe",
                        lambda url, key, policy_: {"endpoint": url.split("//")[-1], "state": "reachable", "reason": None, "models": ["fallback-model"]})
    return {"ws": str(ws), "wsid": wsid, "tmp": str(tmp_path), "snapshot_id": acquired["data"]["snapshot_id"]}


def _visible() -> AppConfig:
    return strict_config(evidence={"attempt_recorder": {"content_visibility": "on"}})


def _requests(s: Dict[str, str]) -> List[Tuple[str, Dict[str, Any], str]]:
    """(golden name, request, configuration kind): every operation a B2 service answers, success and refusal paths."""
    snap, ws = s["snapshot_id"], s["ws"]
    return [
        ("capabilities", {"operation": "capabilities"}, "default"),
        ("capabilities_visible", {"operation": "capabilities"}, "visible"),
        ("history_list", {"operation": "history.list", "snapshot_id": snap, "limit": 10}, "default"),
        ("history_list_page", {"operation": "history.list", "snapshot_id": snap, "limit": 1}, "default"),
        ("history_list_scoped", {"operation": "history.list", "snapshot_id": snap, "workspace": ws, "limit": 5}, "default"),
        ("history_list_scoped_bad_limit", {"operation": "history.list", "snapshot_id": snap, "workspace": ws, "limit": 0}, "default"),
        ("history_list_no_snapshot", {"operation": "history.list"}, "default"),
        ("history_detail", {"operation": "history.detail", "snapshot_id": snap, "run_id": "t-gate"}, "default"),
        ("history_detail_plain", {"operation": "history.detail", "snapshot_id": snap, "run_id": "t-old"}, "default"),
        ("history_prompt", {"operation": "history.prompt", "snapshot_id": snap, "run_id": "t-linked"}, "default"),
        ("workspace_status", {"operation": "workspace.status", "workspace": ws}, "default"),
        ("workspace_status_relative", {"operation": "workspace.status", "workspace": "relative"}, "default"),
        ("workspace_describe", {"operation": "workspace.describe", "workspace": ws}, "default"),
        ("workspace_describe_missing", {"operation": "workspace.describe", "workspace": ws + "-missing"}, "default"),
        ("evidence_manifest", {"operation": "evidence.manifest", "run_id": "ev-rich"}, "default"),
        ("evidence_manifest_absent", {"operation": "evidence.manifest", "run_id": "ev-none", "snapshot_id": snap}, "default"),
        ("evidence_manifest_absent_no_snapshot", {"operation": "evidence.manifest", "run_id": "ev-none"}, "default"),
        ("evidence_manifest_from_trace", {"operation": "evidence.manifest", "run_id": "t-gate", "from_trace": True, "snapshot_id": snap}, "default"),
        ("evidence_manifest_from_trace_pre", {"operation": "evidence.manifest", "run_id": "t-old", "from_trace": True, "snapshot_id": snap}, "default"),
        ("evidence_manifest_bad_id", {"operation": "evidence.manifest", "run_id": "../x"}, "default"),
        ("evidence_verify", {"operation": "evidence.verify", "run_id": "ev-rich"}, "default"),
        ("evidence_verify_missing", {"operation": "evidence.verify", "run_id": "ev-none"}, "default"),
        ("evidence_records", {"operation": "evidence.records", "run_id": "ev-rich", "limit": 4}, "default"),
        ("evidence_records_filtered", {"operation": "evidence.records", "run_id": "ev-rich", "role": "developer", "kind": ["model.request", "model.response"]}, "default"),
        ("evidence_records_bad_kind", {"operation": "evidence.records", "run_id": "ev-rich", "kind": ["no.such"]}, "default"),
        ("evidence_records_missing", {"operation": "evidence.records", "run_id": "ev-none"}, "default"),
        ("evidence_change_content", {"operation": "evidence.change_content", "run_id": "ev-cand", "seq": 2, "part": "diff"}, "visible"),
        ("evidence_change_content_binary", {"operation": "evidence.change_content", "run_id": "ev-cand", "seq": 5, "part": "after"}, "visible"),
        ("evidence_change_content_off", {"operation": "evidence.change_content", "run_id": "ev-cand", "seq": 2, "part": "diff"}, "default"),
        ("evidence_change_content_wrong_class", {"operation": "evidence.change_content", "run_id": "ev-cand", "seq": 2, "part": "messages"}, "visible"),
        ("evidence_change_content_proposed", {"operation": "evidence.change_content", "run_id": "ev-cand", "seq": 3, "part": "proposed"}, "visible"),
        ("evidence_change_content_not_recorded", {"operation": "evidence.change_content", "run_id": "ev-cand", "seq": 2, "part": "proposed"}, "visible"),
        ("evidence_call_content", {"operation": "evidence.call_content", "run_id": "ev-call", "seq": 1, "part": "messages"}, "visible"),
        ("evidence_call_content_wrong_kind", {"operation": "evidence.call_content", "run_id": "ev-call", "seq": 4, "part": "messages"}, "visible"),
        ("skills_list", {"operation": "skills.list", "workspace": ws}, "default"),
        ("skills_describe", {"operation": "skills.describe", "workspace": ws, "skill_id": "alpha"}, "default"),
        ("skills_describe_missing", {"operation": "skills.describe", "workspace": ws, "skill_id": "nope"}, "default"),
        ("skills_describe_bad_id", {"operation": "skills.describe", "workspace": ws, "skill_id": "a\nb"}, "default"),
        ("tools_list", {"operation": "tools.list", "workspace": ws}, "default"),
        ("tools_describe", {"operation": "tools.describe", "workspace": ws, "tool_id": "mcp:files"}, "default"),
        ("tools_describe_missing", {"operation": "tools.describe", "workspace": ws, "tool_id": "mcp:nope"}, "default"),
        ("tools_approval_state", {"operation": "tools.approval_state", "workspace": ws, "tool_id": "mcp:files"}, "default"),
        ("tools_approval_state_missing", {"operation": "tools.approval_state", "workspace": ws, "tool_id": "mcp:nope"}, "default"),
        ("execution_context", {"operation": "execution.context", "workspace": ws}, "default"),
        ("execution_model_probe", {"operation": "execution.model_probe", "workspace": ws}, "default"),
    ]


def _normalize(value: Any, s: Dict[str, str]) -> Any:
    if isinstance(value, dict):
        out = {k: ("<volatile>" if k in VOLATILE_KEYS and v is not None else _normalize(v, s)) for k, v in value.items()}
        for key in UNORDERED_KEYS & set(out):
            if isinstance(out[key], list):
                out[key] = sorted(out[key], key=lambda item: json.dumps(item, sort_keys=True))
        return out
    if isinstance(value, list):
        return [_normalize(v, s) for v in value]
    if isinstance(value, str):
        for real, token in ((os.path.realpath(state_dir()), "<STATE>"), (state_dir(), "<STATE>"), (os.path.realpath(s["tmp"]), "<TMP>"),
                            (s["tmp"], "<TMP>"), (s["wsid"], "<WSID>"), (s["snapshot_id"], "<SNAPSHOT>")):
            value = value.replace(real, token)
    return value


def _answer(request: Dict[str, Any], kind: str, s: Dict[str, str]) -> str:
    cfg = _visible() if kind == "visible" else AppConfig()
    encoded = encode_response(run_kup_operation(cfg, request, request_id="golden"))
    return json.dumps(_normalize(json.loads(encoded), s), indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def test_every_read_side_operation_answers_the_pre_extraction_envelope(scenario):
    mismatched = []
    for name, request, kind in _requests(scenario):
        text = _answer(request, kind, scenario)
        path = GOLDEN / f"{name}.json"
        if WRITE:
            GOLDEN.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            continue
        if path.read_text(encoding="utf-8") != text:
            mismatched.append(name)
    assert not WRITE, "goldens written; rerun without KRIYA_WRITE_KUP_GOLDEN"
    assert mismatched == [], f"envelopes differ from the pre-B2 goldens: {mismatched}"
    assert sorted(p.stem for p in GOLDEN.glob("*.json")) == sorted(name for name, _r, _k in _requests(scenario)), "a golden without a case"


def test_the_normalization_keeps_every_identity_and_decision_field(scenario):
    """The golden comparison cannot pass vacuously: the volatile set never hides a decision or identity field, and a planted change in a
    stable field is detected."""
    assert not VOLATILE_KEYS & {"status", "code", "reason", "run_id", "state", "found", "available", "approval", "store", "items", "records",
                                "runs", "workspace_scope", "application_outcome", "content", "encoding", "consistency", "error"}
    text = _answer({"operation": "evidence.manifest", "run_id": "ev-rich"}, "default", scenario)
    golden = (GOLDEN / "evidence_manifest.json").read_text(encoding="utf-8")
    assert text == golden
    planted = json.loads(text)
    planted["data"]["line_count"] = planted["data"]["line_count"] + 1
    assert json.dumps(planted, indent=1, sort_keys=True, ensure_ascii=False) + "\n" != golden


# ------------------------------------------------------------------ the service boundary itself

def test_refusal_codes_are_the_kup_vocabulary_one_to_one():
    from kriya.application import services
    from kriya.kup import policy

    assert services.REFUSAL_CODES <= set(policy.ERROR_CODES)
    for code in services.REFUSAL_CODES:
        assert getattr(services, code) == getattr(policy, code) == code
    with pytest.raises(ValueError):
        services.ApplicationRefusal("NOT_A_CODE", "x")
    refusal = services.ApplicationRefusal(services.EVIDENCE_STORE_NOT_FOUND, "no store", path_kind="store")
    assert (refusal.reason_code, refusal.message, refusal.extra, str(refusal)) == ("EVIDENCE_STORE_NOT_FOUND", "no store", {"path_kind": "store"}, "no store")


def test_a_service_refusal_reaches_the_wire_with_its_code_message_and_extra_fields(scenario, monkeypatch):
    from kriya.application import ApplicationRefusal, EvidenceService

    def refuse(self, ctx, cfg, run_id):
        raise ApplicationRefusal("EVIDENCE_VERIFY_FAILED", "planted", status="CHAIN_BROKEN", broken_seq=3)

    monkeypatch.setattr(EvidenceService, "verify", refuse)
    env = run_kup_operation(AppConfig(), {"operation": "evidence.verify", "run_id": "ev-rich"}, request_id="r")
    assert env["data"] is None and env["error"] == {"code": "EVIDENCE_VERIFY_FAILED", "message": "planted", "database_state": None,
                                                    "status": "CHAIN_BROKEN", "broken_seq": 3}


def test_each_service_answers_exactly_the_data_its_envelope_carries(scenario):
    from kriya.application import EvidenceService, HistoryService, ProjectService
    from kriya.context import RequestContext
    from kriya.control.history import require_snapshot, snapshot_directory

    cfg, ws, ctx = AppConfig(), scenario["ws"], RequestContext.local(scenario["ws"], request_id="golden")
    snap = require_snapshot(snapshot_directory(cfg), scenario["snapshot_id"])
    pairs = [
        (ProjectService().describe(ctx, cfg, ws, ws), {"operation": "workspace.describe", "workspace": ws}),
        (ProjectService().status(ctx, ws), {"operation": "workspace.status", "workspace": ws}),
        (HistoryService().detail(ctx, cfg, snap, "t-gate"), {"operation": "history.detail", "snapshot_id": snap.snapshot_id, "run_id": "t-gate"}),
        (HistoryService().list_in_workspace(ctx, cfg, snap, limit=5, cursor=None, workspace=ws),
         {"operation": "history.list", "snapshot_id": snap.snapshot_id, "workspace": ws, "limit": 5}),
        (EvidenceService().manifest(ctx, cfg, "ev-rich", from_trace=False, snapshot=None), {"operation": "evidence.manifest", "run_id": "ev-rich"}),
    ]
    for data, request in pairs:
        envelope = run_kup_operation(cfg, request, request_id="golden")
        assert envelope["error"] is None and envelope["data"] == data, request["operation"]
    assert pairs[2][0]["application_outcome"]["data"]["status"] == "EXECUTED"
    assert pairs[3][0]["workspace_scope"]["matched"] == 1 and [r["run_id"] for r in pairs[3][0]["runs"]] == ["t-linked"]


def test_application_owns_no_sqlite_http_environment_home_cwd_or_process_access_and_never_imports_the_adapters():
    from tests.architecture import access_sites as sites
    from tests.architecture import module_map as mm

    trees = {path: tree for path, tree in mm.trees().items() if path.startswith("kriya/application/")}
    assert len(trees) >= 8
    for finder in (sites.sqlite_sites, sites.http_sites, sites.environ_sites, sites.home_sites, sites.cwd_sites, sites.subprocess_sites,
                   sites.git_argv_sites, sites.kriya_dir_sites):
        assert {path: lines for path, tree in trees.items() for lines in [finder(tree)] if lines} == {}, finder.__name__
    table = mm.owner_table()
    targets = {mm.logical(edge.target, table) for edge in mm.scan() if edge.source.startswith("kriya.application")}
    assert not targets & {"ADAPTER-KUP", "ADAPTER-CLI", "CLIENT", "METRICS"}, targets
