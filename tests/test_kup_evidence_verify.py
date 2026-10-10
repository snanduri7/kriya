"""KUP ``evidence.verify`` (Run Cockpit v1.1, 04 section 4.2 and 5): every reader status is reported as data with its
head digest, ``sealed_store`` consistency only when the chain, blobs and seal were verified in this invocation, typed errors for
unreadable stores, and no record or blob content in the response."""
from __future__ import annotations

import gzip
import json
import os

import pytest
from _kup_evidence_fixtures import rewrite_record_line, write_store
from _strict_doubles import strict_config

from kriya.core.attempt_evidence import model
from kriya.kup import policy
from kriya.kup.cli_ops import run_kup_operation

KEYS = {"run_id", "status", "record_count", "sealed", "broken_seq", "detail", "seal", "blob_problems", "head_digest", "verified_at"}


def _verify(run_id):
    return run_kup_operation(strict_config(), {"operation": "evidence.verify", "run_id": run_id})


def _chain_head(directory):
    with open(os.path.join(directory, "records.jsonl"), "rb") as handle:
        last = [line for line in handle.read().split(b"\n") if line][-1]
    return model.digest(last)


def test_verified_store_reports_the_head_and_is_a_sealed_store_observation():
    directory = write_store("v-ok")
    env = _verify("v-ok")
    data = env["data"]
    assert env["error"] is None and set(data) == KEYS
    assert data["status"] == "VERIFIED" and data["record_count"] == 3 and data["sealed"] is True and data["broken_seq"] is None
    assert data["blob_problems"] == [] and data["head_digest"] == _chain_head(directory) == data["seal"]["head_digest"]
    assert data["verified_at"].endswith("Z")
    assert env["consistency"] == {"kind": "sealed_store", "live_stream": False, "head_digest": data["head_digest"]}


def test_unsealed_store_is_unsealed_live_observation_with_the_current_head():
    directory = write_store("v-un", seal=False)
    env = _verify("v-un")
    assert env["data"]["status"] == "UNSEALED" and env["data"]["sealed"] is False and env["data"]["seal"] is None
    assert env["data"]["head_digest"] == _chain_head(directory)
    assert env["consistency"] == {"kind": "live_observation", "live_stream": False}


def test_missing_store_is_not_found_data_not_an_error():
    env = _verify("v-none")
    assert env["error"] is None and env["data"]["status"] == "NOT_FOUND" and env["data"]["record_count"] == 0
    assert env["data"]["head_digest"] is None and env["consistency"]["kind"] == "live_observation"


def test_chain_broken_by_editing_one_line_names_the_first_bad_seq_and_is_never_sealed_store():
    directory = write_store("v-chain")
    rewrite_record_line(directory, 1, lambda record: model.canonical_bytes({**record, "payload": {"n": 999}}))
    env = _verify("v-chain")
    assert env["data"]["status"] == "CHAIN_BROKEN" and env["data"]["broken_seq"] == 3 and env["data"]["head_digest"] is None
    assert env["consistency"]["kind"] == "live_observation"


def test_truncated_tail_is_reported_with_the_head_of_the_complete_records():
    directory = write_store("v-tail", seal=False)
    with open(os.path.join(directory, "records.jsonl"), "ab") as handle:
        handle.write(b'{"schema": "kriya.attempt_evid')
    env = _verify("v-tail")
    assert env["data"]["status"] == "TRUNCATED_TAIL" and env["data"]["record_count"] == 3 and env["consistency"]["kind"] == "live_observation"
    assert env["data"]["head_digest"] is not None


def test_a_sealed_store_with_a_partial_tail_is_not_a_sealed_store_observation():
    directory = write_store("v-tail2")
    with open(os.path.join(directory, "records.jsonl"), "ab") as handle:
        handle.write(b'{"partial')
    env = _verify("v-tail2")
    assert env["data"]["status"] == "TRUNCATED_TAIL" and env["consistency"]["kind"] == "live_observation"


def test_blob_corruption_is_reported_with_the_problem_and_not_trusted():
    directory = write_store("v-blob")
    blobs = [os.path.join(root, name) for root, _d, files in os.walk(os.path.join(directory, "blobs")) for name in files]
    with open(sorted(blobs)[0], "wb") as handle:
        handle.write(gzip.compress(b"tampered", mtime=0))
    env = _verify("v-blob")
    assert env["data"]["status"] == "BLOB_CORRUPT" and len(env["data"]["blob_problems"]) == 1 and env["consistency"]["kind"] == "live_observation"
    assert env["data"]["head_digest"] is not None


def test_seal_naming_another_head_is_a_mismatch_not_a_sealed_store():
    directory = write_store("v-seal")
    path = os.path.join(directory, "seal.json")
    with open(path, encoding="utf-8") as handle:
        seal = json.load(handle)
    seal["head_digest"] = "sha256:" + "f" * 64
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(seal, handle)
    env = _verify("v-seal")
    assert env["data"]["status"] == "SEAL_MISMATCH" and env["consistency"]["kind"] == "live_observation"
    assert env["data"]["seal"]["head_digest"] == "sha256:" + "f" * 64  # verbatim, not corrected


def test_a_non_object_record_line_is_chain_broken_not_a_crash():
    directory = write_store("v-list")
    rewrite_record_line(directory, 1, lambda _record: b"[1, 2]")
    env = _verify("v-list")
    assert env["error"] is None and env["data"]["status"] == "CHAIN_BROKEN" and env["data"]["broken_seq"] == 2


def test_an_unsupported_record_schema_is_refused_with_the_schema_seen():
    directory = write_store("v-schema")
    rewrite_record_line(directory, 0, lambda record: model.canonical_bytes({**record, "schema": "kriya.attempt_evidence/99"}))
    env = _verify("v-schema")
    assert env["error"]["code"] == policy.EVIDENCE_SCHEMA_UNSUPPORTED and env["error"]["schema_seen"] == "kriya.attempt_evidence/99"


def test_an_unreadable_records_file_is_a_typed_error():
    directory = write_store("v-io")
    os.remove(os.path.join(directory, "records.jsonl"))
    env = _verify("v-io")
    assert env["error"]["code"] == policy.EVIDENCE_STORE_UNREADABLE and env["data"] is None


@pytest.mark.parametrize("bad", ["", ".hidden", "../x", "a/b", "x" * 300, None])
def test_run_id_is_validated_before_any_lookup(bad):
    assert _verify(bad)["error"]["code"] == policy.INVALID_REQUEST


def test_the_response_carries_no_record_payload_or_blob_content():
    write_store("v-leak")
    text = json.dumps(_verify("v-leak"))
    assert "prompt number" not in text and "model.request" not in text
