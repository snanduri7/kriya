"""S20 (OI-134 raw credential retention in stored evidence; OI-152 endpoint display): characterization FIRST.

OBS-003 / KRP-025 (REQUIRED): secrets are redacted in logs, traces and evidence. Preflight
(handover/MODULARIZATION/S20-PREFLIGHT.md) MEASURED which persisted surfaces keep a planted credential raw and which of
them no digest binds. The unbound ones are redacted AT THEIR ONE WRITER through the ONE redactor
(kriya/foundation/redaction.py):

* I-1  the traces.db ``runs`` row (``TraceLogger.log_run``);
* I-3  the attempt-evidence record envelope ``payload`` (``AttemptEvidenceWriter.append``; the manifest's
  ``redaction_policy_version`` becomes "2" - it now names a policy that is applied);
* I-9  the launch record's free text (``approval_required``, ``declined_decisions``, ``ended.error``, a pending change
  review's ``reason``);
* I-11 the decision ledger file (``DecisionLedger.append_to_file``);
* I-12 the enforce planning diagnostics (``persist_planning_attempt_diagnostic``).

Kept BYTE-IDENTICAL on purpose (owner question Q1/Q2 of the preflight; the pins below fail if a correction touches them):
the content-addressed attempt-evidence blobs and their ``content_digests`` (I-4), the review-diff sidecar (I-10, OI-146
approval surface), the review identity (``reason_sha256`` / ``review_id`` bind the RAW reason), and a positional secret
(OI-153, open owner question) everywhere. OI-152: the one endpoint display rule keeps an IPv6 host's brackets and a port
0, and shows an unset endpoint as "".

Pins marked FLIP are strict-xfail on the unfixed bytes (``--runxfail`` makes each fail there) and flip with the
correction commits.
"""
import gzip
import hashlib
import json
import os
import sqlite3

import pytest

from kriya.foundation.redaction import REDACTED

S = "Zq9S20SECv"
R = REDACTED
FLIP = pytest.mark.xfail(strict=True, raises=AssertionError, reason="S20 characterization: flips with the correction")


def _bytes_under(root) -> bytes:
    """Every file under ``root`` as raw bytes (sqlite main, -wal and -shm included; gzip blobs decompressed too)."""
    out = []
    for directory, _dirs, names in os.walk(str(root)):
        for name in names:
            data = open(os.path.join(directory, name), "rb").read()
            out.append(data)
            if data[:2] == b"\x1f\x8b":
                out.append(gzip.decompress(data))
    return b"\n".join(out)


# =========================================================================== I-1: the traces.db runs row

GATES = [{"attempt": 1, "type": "run_verification", "success": False, "output": f"connect failed: password={S}1",
          "steps": [{"command": ["run.sh", "--token", f"{S}2", f"--password={S}3"], "exit_code": 1,
                     "stdout": f"DB_PASSWORD={S}4\n", "stderr": "", "timed_out": False}]}]


def _log_secret_row(db):
    from kriya.control.trace import TraceLogger

    TraceLogger(db).log_run(
        run_id="r-s20", goal=f"deploy with api_key={S}5 please", duration_sec=1.0, attempts=1, status="failed",
        files_modified=["a.py"], retrieved_chunks=[{"text": f"token: {S}6"}], active_skills=["s"],
        prompt_rendered=f"=== Goal ===\nAuthorization: Bearer {S}7", gate_outcomes=GATES,
        model_hops=[{"error": f"http://u:{S}8@h/v1 refused"}], run_events=[{"kind": "k", "message": f"secret={S}9"}],
        evidence_records=[{"kind": "gate", "payload": {"raw_output": f"api_key={S}10"}}],
        generation_metrics={"note": f"token={S}11"}, failure_report=[{"failure_type": "x", "message": f"password={S}12"}])


def _row(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return dict(conn.execute("SELECT * FROM runs WHERE run_id = 'r-s20'").fetchone())
    finally:
        conn.close()


def test_the_trace_row_stores_no_secret(tmp_path):
    db = str(tmp_path / "traces.db")
    _log_secret_row(db)
    stored = _bytes_under(tmp_path)
    assert S.encode() not in stored                    # main db, -wal, -shm: no planted value in any column


def test_the_trace_row_keeps_its_non_secret_structure(tmp_path):
    """Before AND after: the row's structure, names and non-secret values are kept (the redaction hides values, never
    keys, list lengths or readable text)."""
    db = str(tmp_path / "traces.db")
    _log_secret_row(db)
    row = _row(db)
    gates = json.loads(row["gate_outcomes"])
    assert [g["type"] for g in gates] == ["run_verification"] and gates[0]["steps"][0]["command"][:2] == ["run.sh", "--token"]
    assert gates[0]["steps"][0]["exit_code"] == 1 and row["status"] == "failed" and row["files_modified"] == "a.py"
    assert row["goal"].startswith("deploy with api_key=") and row["goal"].endswith(" please")
    assert json.loads(row["failure_report"])[0]["failure_type"] == "x"


def test_the_trace_row_holds_the_redacted_values(tmp_path):
    db = str(tmp_path / "traces.db")
    _log_secret_row(db)
    row = _row(db)
    assert row["goal"] == f"deploy with api_key={R} please"
    assert json.loads(row["gate_outcomes"])[0]["steps"][0]["command"] == ["run.sh", "--token", R, f"--password={R}"]
    assert row["prompt_rendered"] == f"=== Goal ===\nAuthorization: {R} {R}"       # the header rule hides the scheme word too


def test_a_trace_row_without_secrets_is_byte_identical_to_the_unredacted_serialization(tmp_path):
    """The redaction is a no-op on text without a secret shape: every column equals ``json.dumps`` of what was passed."""
    from kriya.control.trace import TraceLogger

    db = str(tmp_path / "traces.db")
    gates = [{"type": "compile", "success": True, "output": "ok: max_tokens=4096, sha256:" + "a" * 64}]
    TraceLogger(db).log_run(run_id="r-s20", goal="add sub", duration_sec=1.0, attempts=1, status="ok", files_modified=[],
                            prompt_rendered="p", gate_outcomes=gates, run_events=[{"kind": "k", "details": {"x": [1, None]}}])
    row = _row(db)
    assert row["gate_outcomes"] == json.dumps(gates) and row["goal"] == "add sub" and row["prompt_rendered"] == "p"
    assert row["run_events"] == json.dumps([{"kind": "k", "details": {"x": [1, None]}}])


def test_the_trace_writer_never_mutates_what_the_caller_passed(tmp_path):
    from kriya.control.trace import TraceLogger

    gates = json.loads(json.dumps(GATES))
    events = [{"kind": "k", "message": f"secret={S}9"}]
    TraceLogger(str(tmp_path / "traces.db")).log_run(run_id="r", goal="g", duration_sec=1.0, attempts=1, status="s",
                                                     files_modified=[], gate_outcomes=gates, run_events=events)
    assert gates == GATES and events == [{"kind": "k", "message": f"secret={S}9"}]


# =========================================================================== I-3 / I-4: the attempt-evidence store

def _store(tmp_path):
    from kriya.core.attempt_evidence import model
    from kriya.core.attempt_evidence.writer import AttemptEvidenceWriter

    writer = AttemptEvidenceWriter(str(tmp_path / "state"), "s20-rec", capture=model.CAPTURE_FULL, manifest={"workspace_name": "f"})
    payload = {"tool_name": "shell", "status": "FAILED", "error": f"curl -H 'Authorization: Token {S}20' failed",
               "reason_codes": ["TOOL_FAILED"], "max_tokens": 4096, "path": "src/a.py"}
    writer.append("run.opened", {})
    writer.append("tool.execution", payload, content={"tool_output": f"DB_PASSWORD={S}21"})
    assert writer.seal("run_closed")
    return str(tmp_path / "state"), payload


@FLIP
def test_the_evidence_envelope_stores_no_secret(tmp_path):
    state, _ = _store(tmp_path)
    records = open(os.path.join(state, "attempt-evidence", "s20-rec", "records.jsonl"), "rb").read()
    assert f"{S}20".encode() not in records
    assert f"Authorization: {R} {R}".encode() in records


@FLIP
def test_the_evidence_manifest_names_the_applied_policy(tmp_path):
    from kriya.core.attempt_evidence import reader

    state, _ = _store(tmp_path)
    assert reader.open_run(state, "s20-rec").manifest()["redaction_policy_version"] == "2"


def test_the_evidence_content_blob_and_its_digest_stay_the_exact_recorded_bytes(tmp_path):
    """I-4 (owner question Q1): content-addressed bytes are NOT redacted by S20 - the blob is the exact content and
    ``content_digests`` is the digest of those raw bytes; the store still verifies."""
    from kriya.core.attempt_evidence import reader

    state, _ = _store(tmp_path)
    run = reader.open_run(state, "s20-rec")
    record = [r for r in run.records() if r["kind"] == "tool.execution"][0]
    raw = f"DB_PASSWORD={S}21".encode()
    assert run.blob(record["blobs"]["tool_output"]) == raw
    assert record["content_digests"]["tool_output"] == {"digest": "sha256:" + hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    assert run.verify().status == reader.VERIFIED


def test_the_evidence_envelope_keeps_its_readable_fields_and_the_caller_payload(tmp_path):
    from kriya.core.attempt_evidence import reader

    state, payload = _store(tmp_path)
    record = [r for r in reader.open_run(state, "s20-rec").records() if r["kind"] == "tool.execution"][0]
    assert {k: record["payload"][k] for k in ("tool_name", "status", "reason_codes", "max_tokens", "path")} == {
        "tool_name": "shell", "status": "FAILED", "reason_codes": ["TOOL_FAILED"], "max_tokens": 4096, "path": "src/a.py"}
    assert payload["error"] == f"curl -H 'Authorization: Token {S}20' failed"     # the caller's object is never mutated


# =========================================================================== I-9 / I-10: the launch record

LID = "20261020T101010-abcdef01"


def _launch(tmp_path):
    from kriya.cli import _runtime_command_decision
    from kriya.context.workspace_identity import canonical_workspace, workspace_identity
    from kriya.control import launch as launch_module
    from kriya.control.launch import LaunchSession
    from kriya.policy.approval import RuntimeCommandSubject

    ws = tmp_path / "proj"
    ws.mkdir()
    canonical = canonical_workspace(str(ws))
    session = LaunchSession(canonical, workspace_identity(canonical), LID, "goal", {"ready": True}, {})
    session.open()
    try:
        awaiting = launch_module.request_change_approval(
            [{"filepath": "app.env", "content": f"+API_KEY={S}30\n"}], f"size trigger; reviewer said password={S}31")
        assert awaiting is not None
        awaiting.close()
        decision = _runtime_command_decision(RuntimeCommandSubject("u2", "c", canonical, (("run.sh", "--token", f"{S}32"),), "none", None,
                                                                   f"run it; token: {S}33"), yes=False)
        assert decision.approved_fingerprint is None
        pending = launch_module.request_runtime_command_approval(
            RuntimeCommandSubject("u3", "c", canonical, (("sshpass", "-p", f"{S}34", "ssh", "h"),), "none", None, "r"))
        assert pending is not None                       # OI-153: a positional secret is offered for review (unchanged)
        pending.close()
        launch_module.decline_decision("web_lookup_query", f"lookup with secret={S}35", {"terms": [f"api_key={S}36"]})
    finally:
        session.finish(1, {"status": "failed", "error": f"boom Authorization: Bearer {S}37"}, None)
    return canonical


def _launch_text(ws):
    from kriya.control.persistence import launch_record_path

    return open(launch_record_path(ws, LID), encoding="utf-8").read()


@FLIP
def test_the_launch_record_stores_no_redactable_secret(tmp_path):
    ws = _launch(tmp_path)
    text = _launch_text(ws)
    for n in (31, 32, 33, 35, 36, 37):
        assert f"{S}{n}" not in text, n


@FLIP
def test_the_launch_record_holds_the_redacted_free_text(tmp_path):
    ws = _launch(tmp_path)
    record = json.loads(_launch_text(ws))
    assert record["approval_required"]["details"]["commands"] == [f"run.sh --token {R}"]
    assert record["approval_required"]["reason"] == f"run it; token: {R}"
    assert record["declined_decisions"][0]["reason"] == f"lookup with secret={R}"
    assert record["declined_decisions"][0]["details"] == {"terms": [f"api_key={R}"]}
    assert record["ended"]["error"] == f"boom Authorization: {R} {R}"
    change = [e for e in record["approval_history"] if e.get("kind") == "change_approval"][0]
    assert change["reason"] == f"size trigger; reviewer said password={R}"


def test_the_review_identity_and_the_sidecar_stay_bound_to_the_raw_bytes(tmp_path):
    """I-10 / OI-146: the review-diff sidecar holds the exact diff, and the review identity is computed from the RAW
    reason (``reason_sha256``, ``review_id``) - S20 never alters approval bytes or what an approval binds."""
    from kriya.control.launch_approval import review_identity
    from kriya.control.persistence import launch_review_diff_path

    ws = _launch(tmp_path)
    record = json.loads(_launch_text(ws))
    change = [e for e in record["approval_history"] if e.get("kind") == "change_approval"][0]
    reason = f"size trigger; reviewer said password={S}31"
    assert change["reason_sha256"] == "sha256:" + hashlib.sha256(reason.encode()).hexdigest()
    from kriya.context.workspace_identity import workspace_identity
    assert change["review_id"] == review_identity(LID, workspace_identity(ws), change["sequence"], change["candidate_digest"], reason)
    sidecar = json.loads(open(launch_review_diff_path(ws, LID, change["review_id"]), encoding="utf-8").read())
    assert sidecar["files"] == [{"filepath": "app.env", "diff": f"+API_KEY={S}30\n"}]


def test_a_positional_secret_stays_as_offered_for_review(tmp_path):
    """OI-153 (open owner question): the redactor does not recognise a positional secret, so the offered runtime review
    records it as the person will see it; S20 builds no command-grammar redaction."""
    ws = _launch(tmp_path)
    record = json.loads(_launch_text(ws))
    runtime = [e for e in [record["pending_approval"]] + record["approval_history"] if e and e.get("kind") == "runtime_command"]
    assert [["sshpass", "-p", f"{S}34", "ssh", "h"]] in [e["commands"] for e in runtime]


# =========================================================================== I-11 / I-12: decision ledger, planning diagnostics

@FLIP
def test_the_decision_ledger_file_stores_no_secret(tmp_path):
    from kriya.control.decisions import DecisionLedger
    from kriya.control.persistence import decision_ledger_path

    ws = tmp_path / "ws"
    ws.mkdir()
    ledger = DecisionLedger()
    decision = ledger.record_and_persist(str(ws), "triage", kind="x", note=f"token={S}40", count=3)
    line = json.loads(open(decision_ledger_path(str(ws)), encoding="utf-8").read().splitlines()[0])
    assert line["note"] == f"token={R}" and line["kind"] == "x" and line["count"] == 3 and line["type"] == "triage"
    assert decision.fields["note"] == f"token={S}40"          # the in-memory decision is the caller's, unchanged


@FLIP
def test_the_planning_diagnostic_stores_no_secret(tmp_path):
    from kriya.workflow.planning_diagnostics import persist_planning_attempt_diagnostic

    ws = tmp_path / "ws"
    ws.mkdir()
    path = persist_planning_attempt_diagnostic(
        str(ws), "r1", attempt=1, planner_request=f"goal: deploy with api_key={S}50", planner_system_prompt="sys",
        raw_plan_response=f'{{"note": "password={S}51"}}', plan=None, validation_errors=["E1"], reason_codes=["RC"],
        repository_evidence=[], repair_prompt=f"retry; token: {S}52")
    text = open(path, encoding="utf-8").read()
    assert S not in text
    line = json.loads(text.splitlines()[0])
    assert line["planner_request"] == f"goal: deploy with api_key={R}" and line["reason_codes"] == ["RC"]


# =========================================================================== the original symptom: a real scripted run

def _scripted_run(tmp_path, monkeypatch):
    """The preflight's part-A reproducer: the real direct pipeline (only the runtime port scripted), a goal carrying
    ``api_key=<m1>``, a first candidate whose failing test prints ``password=<m2>`` (gate output), a retry that
    succeeds, capture ``full``. Returns (traces.db main/-wal/-shm bytes, attempt-evidence bytes incl. gunzipped blobs)."""
    from _chaos_harness import (
        CALC,
        TEST_SUB,
        ChaosRuntime,
        RuntimeRegistration,
        benign_roles,
        chaos_config,
        chaos_engine,
        git_workspace,
        run_direct,
    )

    from kriya.core.state_paths import ENV_STATE_DIR

    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(ENV_STATE_DIR, str(state))
    cwd = tmp_path / "cwd"
    (cwd / "memory").mkdir(parents=True)
    monkeypatch.chdir(cwd)                                 # never the checkout's ./memory (OI-45 rule)
    wrong = CALC + f'\n\ndef sub(a, b):\n    print("password={RUN_M2}")\n    return a + b\n'
    right = CALC + "\n\ndef sub(a, b):\n    return a - b\n"
    answers = iter([wrong] + [right] * 20)
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB})
    cfg = chaos_config()
    cfg.evidence.attempt_recorder.capture = "full"
    runtime = ChaosRuntime(lambda role, request: next(answers) if role == "developer" else benign_roles(role, request))
    with RuntimeRegistration(runtime):
        result = run_direct(chaos_engine(cfg), f"add sub to calc.py (deploy note: api_key={RUN_M1})", workspace)
    traces = b"".join(p.read_bytes() for p in sorted(state.glob("traces.db*")))
    return result, traces, _bytes_under(state / "attempt-evidence")


RUN_M1, RUN_M2 = f"{S}g1", f"{S}g2"


def test_a_scripted_run_writes_its_trace_row_and_keeps_the_exact_content_blobs(tmp_path, monkeypatch):
    """Before AND after: the run is recorded (a trace row exists; the gate failed once, so its output was captured) and
    the default capture's content blobs hold both markers - content-addressed bytes are owner question Q1, unchanged."""
    result, traces, blobs = _scripted_run(tmp_path, monkeypatch)
    assert traces and b"add sub to calc.py" in traces
    assert RUN_M1.encode() in blobs and RUN_M2.encode() in blobs
    assert result.get("failure_report"), result.keys()       # the first attempt really failed (gate output recorded)


def test_a_scripted_run_keeps_no_planted_secret_in_its_trace_row(tmp_path, monkeypatch):
    """The original symptom (preflight part A): traces.db held the goal's and the gate output's credential raw."""
    _result, traces, _blobs = _scripted_run(tmp_path, monkeypatch)
    assert RUN_M1.encode() not in traces and RUN_M2.encode() not in traces


# =========================================================================== OI-152: the endpoint display rule

@pytest.mark.parametrize("url, shown", [
    ("http://[::1]:11434/v1", "http://[::1]:11434/v1"), ("http://u:p@[::1]/v1", "http://[::1]/v1"),
    ("http://[FE80::1]:8080/x?k=v", "http://[fe80::1]:8080/x"), ("http://h:0/v1", "http://h:0/v1"), (None, ""),
])
def test_the_endpoint_display_keeps_ipv6_brackets_and_port_zero(url, shown):
    from kriya.foundation.redaction import safe_endpoint

    assert safe_endpoint(url) == shown
