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
import re
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
# Frozen text of GATES: a writer that rewrote the caller's list in place would also rewrite the module constant (every
# test passes copies; the non-mutation test compares against this text, never against the shared object).
GATES_TEXT = json.dumps(GATES, sort_keys=True)


# One plant per stored column (each redaction site is killed on its own by a missing column marker).
COLUMN_MARKERS = {"gate_outcomes": ("1", "2", "3", "4"), "goal": ("5",), "retrieved_chunks": ("6",), "prompt_rendered": ("7",),
                  "model_hops": ("8",), "run_events": ("9",), "evidence_records": ("10",), "generation_metrics": ("11",),
                  "failure_report": ("12",), "files_modified": ("13",), "active_skills": ("14",),
                  "milestone_plans.validation_failures": ("15",), "milestone_plans.repository_topology": ("16",)}


def _log_secret_row(db):
    from kriya.control.trace import TraceLogger

    logger = TraceLogger(db)
    logger.log_run(
        run_id="r-s20", goal=f"deploy with api_key={S}5 please", duration_sec=1.0, attempts=1, status="failed",
        files_modified=["a.py", f"token={S}13"], retrieved_chunks=[{"text": f"token: {S}6"}], active_skills=["s", f"api_key={S}14"],
        prompt_rendered=f"=== Goal ===\nAuthorization: Bearer {S}7", gate_outcomes=json.loads(GATES_TEXT),
        model_hops=[{"error": f"http://u:{S}8@h/v1 refused"}], run_events=[{"kind": "k", "message": f"secret={S}9"}],
        evidence_records=[{"kind": "gate", "payload": {"raw_output": f"api_key={S}10"}}],
        generation_metrics={"note": f"token={S}11"}, failure_report=[{"failure_type": "x", "message": f"password={S}12"}])
    logger.log_milestone_plan("g-s20", "rejected", 3, 1, 0, 0, 0, 1, [f"password={S}15"], {"note": f"secret={S}16"})


def _row(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return dict(conn.execute("SELECT * FROM runs WHERE run_id = 'r-s20'").fetchone())
    finally:
        conn.close()


@pytest.mark.parametrize("column", sorted(COLUMN_MARKERS))
def test_the_trace_row_stores_no_secret(tmp_path, column):
    db = str(tmp_path / "traces.db")
    _log_secret_row(db)
    stored = _bytes_under(tmp_path)                    # main db, -wal, -shm: no planted value in any column
    leaked = [n for n in COLUMN_MARKERS[column] if re.search(re.escape(f"{S}{n}").encode() + rb"(?![0-9])", stored)]
    assert leaked == [], column                        # (?![0-9]): marker 1 is not a prefix hit of marker 13


def test_the_trace_row_keeps_its_non_secret_structure(tmp_path):
    """Before AND after: the row's structure, names and non-secret values are kept (the redaction hides values, never
    keys, list lengths or readable text)."""
    db = str(tmp_path / "traces.db")
    _log_secret_row(db)
    row = _row(db)
    gates = json.loads(row["gate_outcomes"])
    assert [g["type"] for g in gates] == ["run_verification"] and gates[0]["steps"][0]["command"][:2] == ["run.sh", "--token"]
    assert gates[0]["steps"][0]["exit_code"] == 1 and row["status"] == "failed" and row["files_modified"].startswith("a.py,token=")
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

    gates = json.loads(GATES_TEXT)
    events = [{"kind": "k", "message": f"secret={S}9"}]
    TraceLogger(str(tmp_path / "traces.db")).log_run(run_id="r", goal="g", duration_sec=1.0, attempts=1, status="s",
                                                     files_modified=[], gate_outcomes=gates, run_events=events)
    assert json.dumps(gates, sort_keys=True) == GATES_TEXT and f"{S}2" in GATES_TEXT
    assert events == [{"kind": "k", "message": f"secret={S}9"}]


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


def test_the_evidence_envelope_stores_no_secret(tmp_path):
    state, _ = _store(tmp_path)
    records = open(os.path.join(state, "attempt-evidence", "s20-rec", "records.jsonl"), "rb").read()
    assert f"{S}20".encode() not in records
    assert f"Authorization: {R} {R}".encode() in records


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


# S20r: the store's other free text (the gap record's error and the seal's degraded reasons) and the order redact -> cut.
STAMP1 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "s20r_stamp1_evidence")


def _records_bytes(state, run_id="s20-rec"):
    return open(os.path.join(state, "attempt-evidence", run_id, "records.jsonl"), "rb").read()


def _gap_store(tmp_path):
    """A record the store refuses (it names another run) becomes a ``recorder.gap`` whose error quotes the refused value,
    and the seal lists the degradation. The quoted value carries a URL credential whose password straddles the 500-char
    cut of the gap's error: hidden whole only when the text is redacted BEFORE it is cut."""
    from kriya.core.attempt_evidence import model
    from kriya.core.attempt_evidence.writer import AttemptEvidenceWriter

    writer = AttemptEvidenceWriter(str(tmp_path / "state"), "s20-rec", capture=model.CAPTURE_FULL, manifest={})
    writer.append("run.opened", {})
    # ValueError("record for run '<offered>' offered to the store of 's20-rec'"): the offered id starts at character 16
    offered = "x" * 450 + f" https://u:{S}60zzzzzzzzzzzz@h/x password={S}61"    # raw: the cut at 500 falls before the "@"
    assert writer.record("diagnosis", {"x": 1}, identity={"run_id": offered}) is None
    assert writer.seal("run_closed") is True
    return str(tmp_path / "state")


def test_the_gap_record_and_the_seal_store_no_secret(tmp_path):
    state = _gap_store(tmp_path)
    stored = _records_bytes(state) + open(os.path.join(state, "attempt-evidence", "s20-rec", "seal.json"), "rb").read()
    assert f"{S}60".encode() not in stored and f"{S}61".encode() not in stored
    gap = [json.loads(line) for line in _records_bytes(state).splitlines()][-1]
    assert gap["kind"] == "recorder.gap" and gap["payload"]["lost_kind"] == "diagnosis" and gap["payload"]["error_type"] == "ValueError"
    assert len(gap["payload"]["error"]) == 500 and f"https://{R}@" in gap["payload"]["error"]


def test_a_freshly_written_chain_verifies_and_hashes_each_line_as_stored(tmp_path):
    """Before AND after: the hash chain binds the STORED line - every ``prev`` is the digest of the previous line as it is
    on disk, the seal's head is the last stored line's digest, and the store VERIFIES (after S20r the stored line is the
    redacted one, so the chain is computed after redaction)."""
    from kriya.core.attempt_evidence import model, reader

    state, _ = _store(tmp_path)
    lines = _records_bytes(state).splitlines()
    prev = model.GENESIS_PREV
    for line in lines:
        assert json.loads(line)["prev"] == prev
        prev = model.digest(line)
    run = reader.open_run(state, "s20-rec")
    assert len(lines) == 2 and run.seal()["head_digest"] == prev and run.verify().status == reader.VERIFIED


def test_an_envelope_without_a_secret_is_stored_byte_identical(tmp_path):
    """Before AND after: a payload with no secret shape (``max_tokens``, ``token_budget``, a digest, a commit SHA, an
    e-mail address, an argv with ``--token-file``) is stored exactly as its unredacted canonical serialization."""
    from kriya.core.attempt_evidence import model
    from kriya.core.attempt_evidence.writer import AttemptEvidenceWriter

    payload = {"tool_name": "shell", "status": "OK", "max_tokens": 4096, "token_budget": 10, "path": "src/a.py",
               "after_digest": "sha256:" + "a" * 64, "command": ["pytest", "-q", "--token-file", "t.txt"], "nested": {"x": [1, None, True]},
               "reason_codes": ["R"], "message": "commit 0123456789abcdef0123456789abcdef01234567 by dev@example.com"}
    writer = AttemptEvidenceWriter(str(tmp_path / "state"), "s20-rec", capture=model.CAPTURE_FULL, manifest={})
    writer.append("tool.execution", payload)
    assert writer.seal("run_closed")
    stored = json.loads(_records_bytes(str(tmp_path / "state")).splitlines()[0])
    assert model.canonical_bytes(stored["payload"]) == model.canonical_bytes(payload)


def test_a_stamp_1_store_written_before_s20r_still_verifies_reads_and_is_served_redacted():
    """Before AND after: old evidence is never rewritten. A store written by the pre-S20r writer (the fixture bytes were
    produced by AttemptEvidenceWriter at 6fd5933: stamp "1", its envelope RAW) still VERIFIES and reads as recorded, its
    manifest is served with the stamp it was written with, and the KUP projection still hides its secret (the read-side
    redaction stays for exactly these stores)."""
    import shutil

    from kriya.config import AppConfig
    from kriya.core.attempt_evidence import reader
    from kriya.core.state_paths import resolve_state_directory
    from kriya.kup.cli_ops import run_kup_operation
    from kriya.kup.inspect import encode_response

    state = resolve_state_directory(AppConfig())[0]
    shutil.copytree(os.path.join(STAMP1, "attempt-evidence", "s20r-stamp1"), os.path.join(state, "attempt-evidence", "s20r-stamp1"))
    run = reader.open_run(state, "s20r-stamp1")
    assert run.manifest()["redaction_policy_version"] == "1"
    assert run.verify().status == reader.VERIFIED
    record = [r for r in run.records() if r["kind"] == "tool.execution"][0]
    assert record["payload"]["error"] == "curl -H 'Authorization: Token Zq9S20RSTAMP1' failed"      # read as recorded
    assert run.blob(record["blobs"]["tool_output"]) == b"DB_PASSWORD=Zq9S20RSTAMP1blob"
    records = run_kup_operation(AppConfig(), {"operation": "evidence.records", "run_id": "s20r-stamp1"})
    assert records["error"] is None, records["error"]
    assert [r["kind"] for r in records["data"]["records"]] == ["run.opened", "tool.execution"]
    assert "Zq9S20RSTAMP1" not in encode_response(records)
    manifest = run_kup_operation(AppConfig(), {"operation": "evidence.manifest", "run_id": "s20r-stamp1"})
    assert manifest["error"] is None and manifest["data"]["manifest"]["redaction_policy_version"] == "1"


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
        assert pending is None                           # S2d (OI-153 (c)): a positional secret is not offered for review
        launch_module.decline_decision("web_lookup_query", f"lookup with secret={S}35", {"terms": [f"api_key={S}36"]})
    finally:
        session.finish(1, {"status": "failed", "error": f"boom Authorization: Bearer {S}37"}, None)
    return canonical


def _launch_text(ws):
    from kriya.control.persistence import launch_record_path

    return open(launch_record_path(ws, LID), encoding="utf-8").read()


def test_the_launch_record_stores_no_redactable_secret(tmp_path):
    ws = _launch(tmp_path)
    text = _launch_text(ws)
    for n in (31, 32, 33, 35, 36, 37):
        assert f"{S}{n}" not in text, n


def test_the_launch_record_holds_the_redacted_free_text(tmp_path):
    """S20r changed this pin's ``approval_required`` lines (they encoded the shape before S2d): the `--token` refusal is
    S2d's (OI-153 (c)) - the fixed refusal text, the REDACTED command and the fingerprint of the exact bytes. The other
    lines are I-9's."""
    from kriya.policy.approval import RUNTIME_COMMAND_SECRET_REFUSAL

    ws = _launch(tmp_path)
    record = json.loads(_launch_text(ws))
    assert record["approval_required"]["details"]["commands"] == [f"run.sh --token {R}"]
    assert record["approval_required"]["reason"] == RUNTIME_COMMAND_SECRET_REFUSAL
    assert record["approval_required"]["details"]["command_fingerprint"].startswith("sha256:")
    assert record["declined_decisions"][0]["reason"] == f"lookup with secret={R}"
    assert record["declined_decisions"][0]["details"] == {"terms": [f"api_key={R}"]}
    assert record["ended"]["error"] == f"boom Authorization: {R} {R}"
    change = [e for e in [record["pending_approval"]] + record["approval_history"] if e and e.get("kind") == "change_approval"][0]
    assert change["reason"] == f"size trigger; reviewer said password={R}"


def test_the_review_identity_and_the_sidecar_stay_bound_to_the_raw_bytes(tmp_path):
    """I-10 / OI-146: the review-diff sidecar holds the exact diff, and the review identity is computed from the RAW
    reason (``reason_sha256``, ``review_id``) - S20 never alters approval bytes or what an approval binds."""
    from kriya.control.launch_approval import review_identity
    from kriya.control.persistence import launch_review_diff_path

    ws = _launch(tmp_path)
    record = json.loads(_launch_text(ws))
    # S2d: the change review is no longer displaced into the history by an offered positional-secret runtime review
    change = [e for e in [record["pending_approval"]] + record["approval_history"] if e and e.get("kind") == "change_approval"][0]
    reason = f"size trigger; reviewer said password={S}31"
    assert change["reason_sha256"] == "sha256:" + hashlib.sha256(reason.encode()).hexdigest()
    from kriya.context.workspace_identity import workspace_identity
    assert change["review_id"] == review_identity(LID, workspace_identity(ws), change["sequence"], change["candidate_digest"], reason)
    sidecar = json.loads(open(launch_review_diff_path(ws, LID, change["review_id"]), encoding="utf-8").read())
    assert sidecar["files"] == [{"filepath": "app.env", "diff": f"+API_KEY={S}30\n"}]


def test_a_positional_secret_is_not_offered_for_review_nor_recorded(tmp_path):
    """S2d (OI-153, owner decisions (b)+(c)) changed this pin, which encoded the defect (it asserted the positional secret
    OFFERED for review and recorded raw): the owner now reads `sshpass -p <pw>` through the command's grammar, so the
    review is not offered and no runtime review in the record carries the command."""
    ws = _launch(tmp_path)
    record = json.loads(_launch_text(ws))
    runtime = [e for e in [record["pending_approval"]] + record["approval_history"] if e and e.get("kind") == "runtime_command"]
    assert [e["commands"] for e in runtime] == [] and f"{S}34" not in _launch_text(ws)


# S20r (I-9): the notes S2d does not write, the order redact -> cut, OI-153 part B, and the non-secret control.
LID2 = "20261020T101011-abcdef02"


def _session_at(ws_path, launch_id):
    from kriya.context.workspace_identity import workspace_identity
    from kriya.control.launch import LaunchSession

    session = LaunchSession(ws_path, workspace_identity(ws_path), launch_id, "goal", {"ready": True}, {})
    session.open()
    return session


def _workspace(tmp_path):
    from kriya.context.workspace_identity import canonical_workspace

    ws = tmp_path / "proj"
    ws.mkdir(exist_ok=True)
    return canonical_workspace(str(ws))


def test_a_note_s2d_does_not_write_is_stored_redacted(tmp_path):
    """The NON-secret refusal path: an unreviewable (empty) command set whose reason quotes a positional secret, and an
    ``other_approval`` note whose details carry a flag secret - S2d records neither; both are stored redacted."""
    from kriya.cli import _runtime_command_decision
    from kriya.control import launch as launch_module
    from kriya.policy.approval import RuntimeCommandSubject

    ws = _workspace(tmp_path)
    session = _session_at(ws, LID)
    try:
        _runtime_command_decision(RuntimeCommandSubject("u9", "c", ws, (), "none", None, f"run mysql -u root -p{S}70 db"), False)
    finally:
        session.finish(0, None, None)
    session = _session_at(ws, LID2)
    try:
        assert launch_module.note_approval_required("other_approval", f"needs token: {S}71", {"files": [f"--password {S}72"]})
    finally:
        session.finish(0, None, None)
    first, second = json.loads(_launch_text(ws)), json.loads(_launch_text_of(ws, LID2))
    assert first["approval_required"]["reason"] == f"run mysql -u root -p{R} db"
    assert first["approval_required"]["details"] == {"commands": []}
    assert second["approval_required"]["reason"] == f"needs token: {R}"
    assert second["approval_required"]["details"] == {"files": [f"--password {R}"]}


def _launch_text_of(ws, launch_id):
    from kriya.control.persistence import launch_record_path

    return open(launch_record_path(ws, launch_id), encoding="utf-8").read()


def _straddling(limit, marker):
    """A text whose URL password straddles the ``limit``-char cut: cut first, the ``@`` is gone and the userinfo rule no
    longer matches, so the stored prefix of the password would leak; redacted first, it is hidden whole."""
    head = "x" * (limit - 30) + " https://u:"
    return head + marker + "z" * 40 + "@h/x"


def test_each_launch_note_is_redacted_before_it_is_cut(tmp_path, monkeypatch):
    from kriya.control import launch as launch_module
    from kriya.control import launch_approval

    monkeypatch.setattr(launch_approval, "REASON_MAX_CHARS", 500)
    ws = _workspace(tmp_path)
    session = _session_at(ws, LID)
    try:
        assert launch_module.note_approval_required("other_approval", _straddling(400, f"{S}73"), {})
        assert launch_module.decline_decision("skill_gap_decision", _straddling(300, f"{S}74"), {"skills": ["a"]})
        awaiting = launch_module.request_change_approval([{"filepath": "a.py", "content": "+x = 1\n"}], _straddling(500, f"{S}75"))
        assert awaiting is not None
        awaiting.close()
    finally:
        session.finish(1, {"status": "failed", "error": _straddling(400, f"{S}76")}, None)
    text = _launch_text(ws)
    for n in (73, 74, 75, 76):
        assert f"{S}{n}" not in text, n
    record = json.loads(text)
    change = record["pending_approval"]
    stored = [record["approval_required"]["reason"], record["declined_decisions"][0]["reason"], change["reason"], record["ended"]["error"]]
    assert all(text.endswith(f" https://{R}@h/x") for text in stored), stored        # hidden whole, the cut fell after it
    assert change["reason_truncated"] is True                                          # it describes the EXACT reason


def test_a_positional_secret_launch_leaves_no_raw_secret_under_the_launch_dir_or_the_state_dir(tmp_path):
    """OI-153 part B (S2d display/review + S20r I-9): a desktop launch reaches a runtime command carrying a RECOGNIZED
    positional secret (S2d grammar: ``sshpass -p <pw>``); its reason quotes the command, and the run ends with an error that
    quotes it too. Every file under the launch record's directory and the state directory is free of the secret; the
    record holds the redacted command and the digest of the exact bytes (the subject's fingerprint), never the raw bytes."""
    from kriya.cli import _runtime_command_decision
    from kriya.config import AppConfig
    from kriya.control.persistence import launch_record_path
    from kriya.core.state_paths import resolve_state_directory
    from kriya.policy.approval import RuntimeCommandSubject

    secret = f"{S}77"
    ws = _workspace(tmp_path)
    subject = RuntimeCommandSubject("u1", "c", ws, (("sshpass", "-p", secret, "ssh", "h"),), "none", None, f"run sshpass -p {secret} ssh h")
    session = _session_at(ws, LID)
    try:
        decision = _runtime_command_decision(subject, yes=False)
        assert decision.approved_fingerprint is None
    finally:
        session.finish(1, {"status": "failed", "error": f"verification declined: sshpass -p {secret} ssh h was not run"}, None)
    launch_dir = os.path.dirname(launch_record_path(ws, LID))
    state = resolve_state_directory(AppConfig())[0]
    assert os.listdir(launch_dir) and secret.encode() not in _bytes_under(launch_dir) + _bytes_under(state)
    assert secret.encode() not in _bytes_under(os.path.join(ws, ".kriya"))
    record = json.loads(_launch_text(ws))
    assert record["approval_required"]["details"] == {"commands": [f"sshpass -p {R} ssh h"], "command_fingerprint": subject.fingerprint}
    assert record["ended"]["error"] == f"verification declined: sshpass -p {R} ssh h was not run"


def test_launch_notes_without_a_secret_are_stored_as_given(tmp_path):
    """Before AND after: notes with no secret shape are stored byte-identical (``max_tokens``, a digest, a path, a review
    reason with the section header)."""
    from kriya.control import launch as launch_module

    reason = "Human-in-the-loop review policy\n\n=== Automated Code Review ===\nLooks right; max_tokens=4096 kept."
    ws = _workspace(tmp_path)
    session = _session_at(ws, LID)
    try:
        assert launch_module.note_approval_required("other_approval", "a policy action needs approval", {"files": ["src/a.py"], "n": 2})
        assert launch_module.decline_decision("web_lookup_query", "Kriya wants to send a live-lookup search", {"terms": ["pydantic v2 model_dump"]})
        awaiting = launch_module.request_change_approval([{"filepath": "a.py", "content": "+x = 1\n"}], reason)
        awaiting.close()
    finally:
        session.finish(1, {"status": "failed", "error": "gate failed: sha256:" + "a" * 64}, None)
    record = json.loads(_launch_text(ws))
    assert record["approval_required"]["reason"] == "a policy action needs approval"
    assert record["approval_required"]["details"] == {"files": ["src/a.py"], "n": 2}
    assert record["declined_decisions"][0]["reason"] == "Kriya wants to send a live-lookup search"
    assert record["declined_decisions"][0]["details"] == {"terms": ["pydantic v2 model_dump"]}
    assert record["pending_approval"]["reason"] == reason
    assert record["ended"]["error"] == "gate failed: sha256:" + "a" * 64


# =========================================================================== I-11 / I-12: decision ledger, planning diagnostics

def test_the_decision_ledger_file_stores_no_secret(tmp_path):
    from kriya.control.decisions import DecisionLedger
    from kriya.control.persistence import decision_ledger_path

    ws = tmp_path / "ws"
    ws.mkdir()
    ledger = DecisionLedger()
    decision = ledger.record_and_persist(str(ws), "triage", kind="x", note=f"token={S}40", count=3)
    line = json.loads(open(decision_ledger_path(str(ws)), encoding="utf-8").read().splitlines()[0])
    assert line["note"] == f"token={R}"
    assert (line["kind"], line["count"], line["type"]) == ("x", 3, "triage")
    assert decision.fields["note"] == f"token={S}40"          # the in-memory decision is the caller's, unchanged


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
    assert line["planner_request"] == f"goal: deploy with api_key={R}"
    assert line["reason_codes"] == ["RC"] and line["validation_errors"] == ["E1"]


def test_the_planning_diagnostic_is_redacted_before_it_is_bounded(tmp_path):
    from kriya.workflow.planning_diagnostics import _MAX_TEXT_CHARS, persist_planning_attempt_diagnostic

    ws = tmp_path / "ws"
    ws.mkdir()
    path = persist_planning_attempt_diagnostic(
        str(ws), "r1", attempt=1, planner_request=_straddling(_MAX_TEXT_CHARS, f"{S}53"), planner_system_prompt="sys",
        raw_plan_response="{}", plan=None, validation_errors=[], reason_codes=[], repository_evidence=[], repair_prompt=None)
    line = json.loads(open(path, encoding="utf-8").read().splitlines()[0])
    assert f"{S}53" not in line["planner_request"]
    assert line["planner_request"].endswith(f" https://{R}@h/x")         # hidden whole; the redacted text fits the bound


def test_every_structured_field_of_the_planning_diagnostic_is_stored_redacted(tmp_path):
    """Model and goal text reach the parsed plan (descriptions, a planned file's reason and path), the validation errors,
    the repository evidence and - for a valid plan - the approved plan and the ownership records: each is stored redacted
    (one marker per field, so each redaction site is checked on its own)."""
    from kriya.workflow.plan_schema import (
        ChangeKind,
        EngineeringPlan,
        ExecutionMethod,
        FileAction,
        PlannedFile,
        Subtask,
    )
    from kriya.workflow.planning_diagnostics import persist_planning_attempt_diagnostic

    def plan(path):
        return EngineeringPlan(plan_id="p1", kind=ChangeKind.TASK, subtasks=[Subtask(
            id="s1", description=f"deploy with password={S}54", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path=path, action=FileAction.MODIFY, reason=f"token: {S}55")])])

    ws = tmp_path / "ws"
    ws.mkdir()
    common = dict(planner_request="r", planner_system_prompt="s", raw_plan_response="{}", reason_codes=["RC"], repair_prompt=None)
    path = persist_planning_attempt_diagnostic(str(ws), "r1", attempt=1, plan=plan("src/a.py"), validation_errors=[f"E1 secret={S}56"],
                                               repository_evidence=[{"path": f"cfg/api_key={S}57", "exists": False}], **common)
    persist_planning_attempt_diagnostic(str(ws), "r1", attempt=2, plan=plan(f"cfg/api_key={S}58"), validation_errors=[],
                                        repository_evidence=[], **common)
    first, second = [json.loads(line) for line in open(path, encoding="utf-8").read().splitlines()]
    for field, marker in (("parsed_plan", 54), ("parsed_plan", 55), ("validation_errors", 56), ("repository_evidence", 57)):
        assert f"{S}{marker}" not in json.dumps(first[field]), (field, marker)
    for field in ("parsed_plan", "approved_plan", "ownership_validation"):
        assert second[field] and f"{S}58" not in json.dumps(second[field]), field
    assert first["parsed_plan"]["subtasks"][0]["description"] == f"deploy with password={R}"
    assert first["validation_errors"] == [f"E1 secret={R}"] and first["reason_codes"] == ["RC"]


def test_a_planning_diagnostic_without_a_secret_is_stored_as_given(tmp_path):
    """Before AND after: text without a secret shape is stored byte-identical (``max_tokens``, digests, paths)."""
    from kriya.workflow.planning_diagnostics import persist_planning_attempt_diagnostic

    ws = tmp_path / "ws"
    ws.mkdir()
    request = "goal: add sub to calc.py; keep max_tokens=4096; base sha256:" + "a" * 64
    path = persist_planning_attempt_diagnostic(
        str(ws), "r1", attempt=2, planner_request=request, planner_system_prompt="sys", raw_plan_response='{"subtasks": []}',
        plan=None, validation_errors=["E1 src/a.py"], reason_codes=["RC"], repository_evidence=[{"path": "src/a.py", "exists": True}],
        repair_prompt="retry with the token_budget kept")
    line = json.loads(open(path, encoding="utf-8").read().splitlines()[0])
    assert (line["planner_request"], line["raw_plan_response"], line["repair_prompt"]) == (request, '{"subtasks": []}', "retry with the token_budget kept")
    assert line["validation_errors"] == ["E1 src/a.py"] and line["repository_evidence"] == [{"path": "src/a.py", "exists": True}]


def test_a_decision_line_without_a_secret_is_stored_as_given(tmp_path):
    """Before AND after: a ledger line without a secret shape is the decision's dict plus the ownership stamp, unchanged."""
    from kriya.context.workspace_identity import workspace_identity
    from kriya.control.decisions import DecisionLedger
    from kriya.control.persistence import decision_ledger_path

    ws = tmp_path / "ws"
    ws.mkdir()
    decision = DecisionLedger().record_and_persist(str(ws), "triage", kind="x", note="max_tokens=4096 kept", count=3, files=["a.py"])
    line = json.loads(open(decision_ledger_path(str(ws)), encoding="utf-8").read().splitlines()[0])
    assert line == {**decision.to_dict(), "_workspace_id": workspace_identity(str(ws))}


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
    ("http://[FE80::1]:8080/x?k=v", "http://[fe80::1]:8080/x"), ("http://[2001:db8:1:2:3:4:5:6]:1/", "http://[2001:db8:1:2:3:4:5:6]:1/"),
    ("http://h:0/v1", "http://h:0/v1"), (None, ""), ("http://h/v1", "http://h/v1"), ("http://127.0.0.1:9/v1", "http://127.0.0.1:9/v1"),
])
def test_the_endpoint_display_keeps_ipv6_brackets_and_port_zero(url, shown):
    from kriya.foundation.redaction import safe_endpoint

    assert safe_endpoint(url) == shown
