"""GUI v1 hardening (Phase D): every evidence operation against isolated, deliberately damaged copies of recorder-written stores.

Expected behaviour, for EVERY damage: a typed answer (data with a non-VERIFIED status, or a typed error), never an exception, and nothing served from a store
that does not verify - no records, no content, no invented evidence. Only genuinely unsealed (live) stores serve records, as live observations. Owner evidence is
never touched: every store here is written by the real recorder into the test's own isolated state directory."""
from __future__ import annotations

import json
import os
import shutil

import pytest
from _kup_evidence_fixtures import (
    acquire_snapshot_id,
    candidate_store,
    model_call_store,
    rewrite_record_line,
    rich_store,
    seed_trace_run,
)
from _strict_doubles import strict_config

from kriya.core.attempt_evidence import model as evidence_model
from kriya.kup import policy
from kriya.kup.cli_ops import run_kup_operation


def _cfg():
    return strict_config(evidence={"attempt_recorder": {"content_visibility": "on"}})


def _op(**request):
    return run_kup_operation(_cfg(), request)


def _write(path, data: bytes):
    with open(path, "wb") as handle:
        handle.write(data)


def _blobs(directory):
    return sorted(os.path.join(root, name) for root, _dirs, names in os.walk(os.path.join(directory, "blobs")) for name in names)


def _truncate_tail(d):
    with open(os.path.join(d, "records.jsonl"), "rb") as handle:
        data = handle.read()
    _write(os.path.join(d, "records.jsonl"), data[:-40])


UNREADABLE, NOT_FOUND, VERIFY_FAILED = policy.EVIDENCE_STORE_UNREADABLE, policy.EVIDENCE_STORE_NOT_FOUND, policy.EVIDENCE_VERIFY_FAILED

# case -> (damage, expected code of records/change_content, expected evidence.verify outcome: a status string or an error code)
DAMAGES = {
    "manifest_garbage": (lambda d: _write(d + "/manifest.json", b"{not json"), UNREADABLE, UNREADABLE),
    "manifest_json_list": (lambda d: _write(d + "/manifest.json", b"[]"), UNREADABLE, UNREADABLE),
    "manifest_json_string": (lambda d: _write(d + "/manifest.json", b'"x"'), UNREADABLE, UNREADABLE),
    "manifest_json_null": (lambda d: _write(d + "/manifest.json", b"null"), UNREADABLE, UNREADABLE),
    "manifest_json_number": (lambda d: _write(d + "/manifest.json", b"5"), UNREADABLE, UNREADABLE),
    "manifest_empty": (lambda d: _write(d + "/manifest.json", b""), UNREADABLE, UNREADABLE),
    "manifest_missing": (lambda d: os.remove(d + "/manifest.json"), NOT_FOUND, "NOT_FOUND"),
    "seal_garbage": (lambda d: _write(d + "/seal.json", b"{not json"), UNREADABLE, UNREADABLE),
    "seal_json_list": (lambda d: _write(d + "/seal.json", b"[]"), UNREADABLE, UNREADABLE),
    "seal_json_null": (lambda d: _write(d + "/seal.json", b"null"), UNREADABLE, UNREADABLE),
    "seal_empty": (lambda d: _write(d + "/seal.json", b""), UNREADABLE, UNREADABLE),
    "records_empty": (lambda d: _write(d + "/records.jsonl", b""), VERIFY_FAILED, "SEAL_MISMATCH"),
    "records_missing": (lambda d: os.remove(d + "/records.jsonl"), UNREADABLE, UNREADABLE),
    "record_line_garbage": (lambda d: rewrite_record_line(d, 1, lambda r: b"{garbage"), VERIFY_FAILED, "CHAIN_BROKEN"),
    "record_line_json_list": (lambda d: rewrite_record_line(d, 1, lambda r: b"[1,2]"), VERIFY_FAILED, "CHAIN_BROKEN"),
    "record_line_json_string": (lambda d: rewrite_record_line(d, 1, lambda r: b'"s"'), VERIFY_FAILED, "CHAIN_BROKEN"),
    "record_wrong_chain_hash": (lambda d: rewrite_record_line(d, 1, lambda r: json.dumps({**r, "prev": "sha256:" + "0" * 64}).encode()), VERIFY_FAILED, "CHAIN_BROKEN"),
    "record_edited_payload": (lambda d: rewrite_record_line(d, 1, lambda r: json.dumps({**r, "payload": {"tampered": True}}).encode()), VERIFY_FAILED, "CHAIN_BROKEN"),
    "tail_truncated_physically": (_truncate_tail, VERIFY_FAILED, "TRUNCATED_TAIL"),
    "blob_missing": (lambda d: os.remove(_blobs(d)[0]), VERIFY_FAILED, "BLOB_CORRUPT"),
    "blob_digest_mismatch": (lambda d: _write(_blobs(d)[0], b"tampered bytes"), VERIFY_FAILED, "BLOB_CORRUPT"),
    "blob_emptied": (lambda d: _write(_blobs(d)[0], b""), VERIFY_FAILED, "BLOB_CORRUPT"),
    "blobs_directory_missing": (lambda d: shutil.rmtree(d + "/blobs"), VERIFY_FAILED, "BLOB_CORRUPT"),
}


@pytest.mark.parametrize("case", list(DAMAGES))
def test_a_damaged_store_gives_typed_answers_and_serves_nothing(case):
    damage, served_code, verify_outcome = DAMAGES[case]
    run_id = "dm-" + case.replace("_", "-")
    directory, seqs = candidate_store(run_id)
    damage(directory)
    manifest = _op(operation="evidence.manifest", run_id=run_id)
    verify = _op(operation="evidence.verify", run_id=run_id)
    records = _op(operation="evidence.records", run_id=run_id, limit=3)
    content = _op(operation="evidence.change_content", run_id=run_id, seq=seqs["staged"], part="diff")
    calls = _op(operation="evidence.call_content", run_id=run_id, seq=seqs["staged"], part="messages")
    for name, env in (("manifest", manifest), ("verify", verify), ("records", records), ("change_content", content), ("call_content", calls)):
        assert set(env) >= {"data", "error", "operation"} and (env["data"] is None) != (env["error"] is None), (name, env)
    assert records["data"] is None and records["error"]["code"] == served_code, records
    assert content["data"] is None and content["error"]["code"] == served_code, content
    assert calls["data"] is None, "no model-call content is served from a store that does not verify"
    assert calls["error"]["code"] in (policy.INVALID_REQUEST, served_code), calls  # (this store has no model.request at that seq: see the healthy-control test below for the real call_content path)
    if verify_outcome in (UNREADABLE, NOT_FOUND, VERIFY_FAILED):
        assert verify["error"]["code"] == verify_outcome
    else:
        assert verify["error"] is None and verify["data"]["status"] == verify_outcome and verify["data"]["sealed"] in (True, False)
        assert verify["consistency"]["kind"] != policy.CONSISTENCY_SEALED_STORE, "a damaged store is never reported as a sealed-store observation"


def test_a_missing_seal_is_a_live_unsealed_observation_not_a_damaged_store():
    directory, seqs = candidate_store("dm-live")
    os.remove(directory + "/seal.json")
    verify = _op(operation="evidence.verify", run_id="dm-live")
    records = _op(operation="evidence.records", run_id="dm-live", limit=2)
    assert verify["data"]["status"] == "UNSEALED" and verify["consistency"]["kind"] == policy.CONSISTENCY_LIVE_OBSERVATION
    assert records["error"] is None and records["consistency"]["kind"] == policy.CONSISTENCY_LIVE_OBSERVATION and len(records["data"]["records"]) == 2


def test_a_store_copied_under_another_runs_name_is_never_served_as_that_run():
    """Evidence binds to its exact run identity (found by the hardening sweep: such a store used to be served as VERIFIED under the wrong run id)."""
    directory, seqs = candidate_store("orig-run")
    shutil.copytree(directory, os.path.join(os.path.dirname(directory), "other-run"))
    for request in ({"operation": "evidence.manifest", "run_id": "other-run"}, {"operation": "evidence.verify", "run_id": "other-run"},
                    {"operation": "evidence.records", "run_id": "other-run", "limit": 2}, {"operation": "evidence.change_content", "run_id": "other-run", "seq": seqs["staged"], "part": "diff"}):
        env = _op(**request)
        assert env["data"] is None and env["error"]["code"] == UNREADABLE and env["error"]["reason"] == "run_identity_mismatch", (request["operation"], env)
    assert _op(operation="evidence.verify", run_id="orig-run")["data"]["status"] == "VERIFIED", "the original is unaffected"


def test_a_store_whose_manifest_was_rewritten_to_match_but_whose_records_name_another_run_is_refused():
    """The records are hash-chained, so they cannot be edited to match; a store copied under another name whose MANIFEST was rewritten still carries the original run id in
    every (valid) record, and the records' own identity is what refuses it."""
    directory, seqs = candidate_store("rec-orig")
    target = os.path.join(os.path.dirname(directory), "rec-ident")
    shutil.copytree(directory, target)
    with open(target + "/manifest.json", encoding="utf-8") as handle:
        manifest = json.load(handle)
    manifest["run_id"] = "rec-ident"
    with open(target + "/manifest.json", "w", encoding="utf-8") as handle:
        json.dump(manifest, handle)
    assert _op(operation="evidence.verify", run_id="rec-orig")["data"]["status"] == "VERIFIED"
    for request in ({"operation": "evidence.verify", "run_id": "rec-ident"}, {"operation": "evidence.records", "run_id": "rec-ident", "limit": 3},
                    {"operation": "evidence.change_content", "run_id": "rec-ident", "seq": seqs["staged"], "part": "diff"}):
        env = _op(**request)
        assert env["data"] is None and env["error"]["code"] == UNREADABLE and env["error"]["reason"] == "run_identity_mismatch", (request["operation"], env)


@pytest.mark.parametrize("cursor", ["", "abc", "A" * 20000, "‮\U0001F600", "../../x", "a\x00b", "WyJ4Il0", "eyJhIjoxfQ"])
def test_a_malformed_cursor_is_a_typed_invalid_request(cursor):
    rich_store("cur-bad")
    env = _op(operation="evidence.records", run_id="cur-bad", limit=3, cursor=cursor)
    assert env["data"] is None and env["error"]["code"] == policy.INVALID_REQUEST, env


def test_a_cursor_cannot_cross_runs_filters_or_a_grown_store():
    rich_store("cur-a")
    rich_store("cur-b")
    first = _op(operation="evidence.records", run_id="cur-a", limit=3)
    cursor = first["data"]["next_cursor"]
    assert _op(operation="evidence.records", run_id="cur-b", limit=3, cursor=cursor)["error"]["code"] == policy.INVALID_REQUEST  # another run
    assert _op(operation="evidence.records", run_id="cur-a", limit=3, cursor=cursor, role="developer")["error"]["code"] == policy.INVALID_REQUEST  # other filters
    assert _op(operation="evidence.records", run_id="cur-a", limit=3, cursor=cursor)["error"] is None  # the right one still works


def test_an_oversized_blob_is_refused_by_its_recorded_size_and_an_unclassified_or_hostile_part_name_is_never_served():
    directory, seqs = candidate_store("big", big=2 * 1024 * 1024)
    assert _op(operation="evidence.change_content", run_id="big", seq=seqs["staged"], part="after")["error"]["code"] == policy.EVIDENCE_BLOB_TOO_LARGE
    assert _op(operation="evidence.change_content", run_id="big", seq=seqs["staged"], part="secret_blob")["error"]["code"] == policy.EVIDENCE_CONTENT_POLICY
    for part in ("../after", "..", "a/b", "AFTER", "", "a" * 64, "af ter"):
        assert _op(operation="evidence.change_content", run_id="big", seq=seqs["staged"], part=part)["error"]["code"] == policy.INVALID_REQUEST, part


def test_trace_rows_with_no_link_a_dangling_link_or_another_stores_link_never_invent_evidence():
    candidate_store("linked-store")
    seed_trace_run("t-nolink", None, with_event=False)
    seed_trace_run("t-dangling", {"run_id": "no-such-evidence", "status": "CLOSED"})
    snapshot = acquire_snapshot_id()
    nolink = _op(operation="evidence.manifest", run_id="t-nolink", snapshot_id=snapshot, from_trace=True)["data"]
    dangling = _op(operation="evidence.manifest", run_id="t-dangling", snapshot_id=snapshot, from_trace=True)["data"]
    assert nolink["store"] == "absent" and nolink["run_id"] is None and nolink["absence"]["reason"] in ("pre_recorder", "unknown")
    assert dangling["store"] == "absent" and dangling["run_id"] == "no-such-evidence"
    assert _op(operation="evidence.manifest", run_id="t-nolink", snapshot_id=snapshot)["data"]["store"] == "absent"  # a trace id is not an evidence id
    for env in (nolink, dangling):
        assert env["manifest"] is None and env["seal"] is None


def _forge(directory, index, mutate):
    """Replace record ``index`` with ``mutate(record)`` and RE-CHAIN every later record, so the chain stays valid: only the content is wrong, which is the case the
    reader's own structural checks must catch (an attacker or a bug can write valid hashes over a malformed record)."""
    path = os.path.join(directory, "records.jsonl")
    with open(path, "rb") as handle:
        lines = [line for line in handle.read().split(b"\n") if line]
    out, prev = [], evidence_model.GENESIS_PREV
    for i, raw in enumerate(lines):
        record = json.loads(raw)
        if i == index:
            record = mutate(record)
        record["prev"] = prev
        raw = evidence_model.canonical_bytes(record)
        out.append(raw)
        prev = evidence_model.digest(raw)
    _write(path, b"\n".join(out) + b"\n")


@pytest.mark.parametrize("blobs", [[], None, "x", 7, ["sha256:" + "0" * 64]])
def test_a_hash_chained_record_whose_blobs_is_not_an_object_is_damage_never_an_exception(blobs):
    """Independent review P2-5(b): a valid-hash record with `blobs` as a list/null/str raised AttributeError out of the reader."""
    run_id = "forge-blobs"
    directory, seqs = model_call_store(run_id)
    _forge(directory, 1, lambda r: {**r, "blobs": blobs})
    for request in ({"operation": "evidence.verify", "run_id": run_id}, {"operation": "evidence.records", "run_id": run_id, "limit": 3},
                    {"operation": "evidence.call_content", "run_id": run_id, "seq": seqs["request"], "part": "messages"}):
        env = _op(**request)  # must not raise
        assert (env["data"] is None) != (env["error"] is None), env
        if env["error"] is None:
            assert env["data"]["status"] in ("CHAIN_BROKEN", "BLOB_CORRUPT", "SEAL_MISMATCH"), env
        else:
            assert env["error"]["code"] in (VERIFY_FAILED, UNREADABLE), env


def test_an_absurdly_nested_record_line_is_damage_never_a_recursion_crash():
    """Independent review P2-5(a): a 200000-deep line made evidence.verify and evidence.records raise RecursionError."""
    directory, seqs = candidate_store("deep-nest")
    with open(directory + "/records.jsonl", "rb") as handle:
        lines = handle.read().split(b"\n")
    lines[1] = b"[" * 200000
    _write(directory + "/records.jsonl", b"\n".join(lines))
    verify = _op(operation="evidence.verify", run_id="deep-nest")
    records = _op(operation="evidence.records", run_id="deep-nest", limit=3)
    assert verify["error"] is None and verify["data"]["status"] == "CHAIN_BROKEN", verify
    assert records["data"] is None and records["error"]["code"] == VERIFY_FAILED, records


def test_call_content_has_a_healthy_control_and_is_refused_from_every_damaged_model_call_store():
    """The matrix above asks a candidate.change seq for call_content (always a refusal); this is the real path with a control."""
    healthy, seqs = model_call_store("cc-healthy")
    ok = _op(operation="evidence.call_content", run_id="cc-healthy", seq=seqs["request"], part="messages")
    assert ok["error"] is None and ok["data"]["content"], ok  # the control: a healthy store DOES serve it
    for case in ("record_wrong_chain_hash", "record_edited_payload", "blob_digest_mismatch", "blob_missing", "tail_truncated_physically", "manifest_garbage"):
        run_id = "cc-" + case.replace("_", "-")
        directory, seqs = model_call_store(run_id)
        DAMAGES[case][0](directory)
        env = _op(operation="evidence.call_content", run_id=run_id, seq=seqs["request"], part="messages")
        assert env["data"] is None and env["error"]["code"] in (VERIFY_FAILED, UNREADABLE), (case, env)


def test_a_store_copied_under_another_name_attributes_nothing_in_the_workspace_scope_or_the_launch_summary():
    from kriya.kup.evidence_ops import recorded_workspace_identity, store_summary
    directory, _seqs = candidate_store("scope-orig")
    shutil.copytree(directory, os.path.join(os.path.dirname(directory), "scope-copy"))
    cfg = _cfg()
    assert recorded_workspace_identity(cfg, "scope-orig")[0] == "recorded"
    assert recorded_workspace_identity(cfg, "scope-copy") == ("unattributed", None)
    assert store_summary(cfg, "scope-orig")["state"] == "sealed"
    assert store_summary(cfg, "scope-copy")["state"] == "unreadable"


def test_an_absurdly_nested_manifest_is_unreadable_never_a_recursion_crash():
    """The manifest is parsed by a different code path than the record lines: its RecursionError is caught at the operation boundary (`_guard`)."""
    directory, _seqs = candidate_store("deep-manifest")
    _write(directory + "/manifest.json", b"[" * 200000)
    for request in ({"operation": "evidence.manifest", "run_id": "deep-manifest"}, {"operation": "evidence.verify", "run_id": "deep-manifest"},
                    {"operation": "evidence.records", "run_id": "deep-manifest", "limit": 2}):
        env = _op(**request)
        assert env["data"] is None and env["error"]["code"] in (UNREADABLE, VERIFY_FAILED), (request, env)
