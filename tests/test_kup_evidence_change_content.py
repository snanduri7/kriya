"""KUP ``evidence.change_content`` (Run Cockpit M3): the semantic, authorized read of ONE part (diff/before/after/...) of ONE recorded
candidate change. Kriya's SECURITY_AUTHORITY ``evidence.attempt_recorder.content_visibility`` (default off) is consulted first and
fails closed; a blob key outside the class table is never served; the response is capped, digest-checked and carries its provenance."""
from __future__ import annotations

import ast
import json
import os
import pathlib

import pytest
from _kup_evidence_fixtures import DIFF_TEXT, candidate_store, rewrite_record_line
from _strict_doubles import strict_config

from kriya.config.config import AppConfig
from kriya.core.attempt_evidence import model
from kriya.kup import policy
from kriya.kup.cli_ops import run_kup_operation

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _cfg(visibility: str = "on"):
    return strict_config(evidence={"attempt_recorder": {"content_visibility": visibility}})


def _read(run_id, seq, part="diff", cfg=None):
    return run_kup_operation(cfg or _cfg(), {"operation": "evidence.change_content", "run_id": run_id, "seq": seq, "part": part})


def test_content_visibility_defaults_off_in_the_real_default_config():
    assert AppConfig().evidence.attempt_recorder.content_visibility == "off"
    assert strict_config().evidence.attempt_recorder.content_visibility == "off"


def test_authorized_diff_is_the_recorded_bytes_with_digest_provenance_and_the_verified_head():
    directory, seqs = candidate_store("cc-1")
    env = _read("cc-1", seqs["staged"])
    data = env["data"]
    assert env["error"] is None and env["operation"] == "evidence.change_content"
    assert data["content"] == DIFF_TEXT and data["encoding"] == "utf-8" and data["digest_ok"] is True and data["bytes_total"] == len(DIFF_TEXT.encode())
    assert data["content_class"] == "candidate_content" and data["part"] == "diff" and data["path"] == "src/A.java" and data["attempt"] == 1
    assert data["ref"] == model.digest(DIFF_TEXT.encode()) and data["run_id"] == "cc-1" and data["seq"] == seqs["staged"]
    assert env["consistency"]["kind"] == "sealed_store" and env["consistency"]["head_digest"] == data["head_digest"]


@pytest.mark.parametrize("part,expected", [("before", "class A {}\n"), ("after", "class A {}\n// added\n")])
def test_before_and_after_are_individual_lazy_reads(part, expected):
    _d, seqs = candidate_store(f"cc-{part}")
    assert _read(f"cc-{part}", seqs["staged"], part)["data"]["content"] == expected


def test_visibility_off_refuses_before_any_store_access_even_for_a_run_that_does_not_exist(monkeypatch):
    from kriya.core.attempt_evidence import reader
    monkeypatch.setattr(reader.EvidenceRun, "verify", lambda *a, **k: pytest.fail("touched the store before the policy check"))
    env = _read("no-such-run", 1, cfg=_cfg("off"))
    assert env["error"]["code"] == policy.EVIDENCE_CONTENT_POLICY and env["error"]["content_class"] == "candidate_content" and env["data"] is None
    assert env["error"]["reason"] == "content_visibility_off"


def test_default_config_is_off_and_refuses():
    _d, seqs = candidate_store("cc-def")
    assert _read("cc-def", seqs["staged"], cfg=strict_config())["error"]["code"] == policy.EVIDENCE_CONTENT_POLICY


@pytest.mark.parametrize("visibility", ["on", "off"])
def test_a_key_outside_the_class_table_is_never_served_even_when_visibility_is_on(visibility):
    _d, seqs = candidate_store(f"cc-unk-{visibility}")
    env = _read(f"cc-unk-{visibility}", seqs["parse"], "analysis_model_claimed", cfg=_cfg(visibility))
    assert env["error"]["code"] == policy.EVIDENCE_CONTENT_POLICY and env["error"]["content_class"] == "unclassified" and env["data"] is None


def test_refused_candidate_content_is_candidate_content_too():
    _d, seqs = candidate_store("cc-ref")
    assert _read("cc-ref", seqs["refused"], "proposed")["data"]["content"] == "class B {}\n"


def test_digest_only_capture_says_exactly_why_there_is_no_content():
    _d, seqs = candidate_store("cc-dig", capture=model.CAPTURE_DIGEST_ONLY)
    env = _read("cc-dig", seqs["staged"])
    assert env["error"]["code"] == policy.EVIDENCE_CONTENT_UNAVAILABLE and env["error"]["reason"] == "digest_only" and env["data"] is None


def test_a_part_the_record_never_had_is_not_recorded():
    _d, seqs = candidate_store("cc-nr")
    assert _read("cc-nr", seqs["refused"], "diff")["error"]["reason"] == "part_not_recorded"


def test_only_candidate_change_records_are_addressable():
    _d, seqs = candidate_store("cc-kind")
    env = _read("cc-kind", seqs["run"], "diff")
    assert env["error"]["code"] == policy.INVALID_REQUEST
    assert _read("cc-kind", 9999, "diff")["error"]["code"] == policy.INVALID_REQUEST


def test_the_cap_is_enforced_from_the_recorded_size_before_reading_the_blob(monkeypatch):
    _d, seqs = candidate_store("cc-big", big=64)
    monkeypatch.setattr(policy, "EVIDENCE_BLOB_MAX_BYTES", 40)
    from kriya.core.attempt_evidence import reader
    reads = []
    real_blob = reader.EvidenceRun.blob
    monkeypatch.setattr(reader.EvidenceRun, "blob", lambda self, ref: (reads.append(ref), real_blob(self, ref))[1])
    env = _read("cc-big", seqs["staged"], "after")
    assert env["error"]["code"] == policy.EVIDENCE_BLOB_TOO_LARGE and env["error"]["bytes_total"] > 40 and env["data"] is None
    # the store's own verification reads every blob exactly once; the content read must not have read the refused part a second time
    assert len(reads) == len(set(reads)), "the over-cap part was read again after the recorded size already refused it"
    assert _read("cc-big", seqs["staged"], "diff")["error"]["code"] == policy.EVIDENCE_BLOB_TOO_LARGE  # the 62-byte diff is over the 40-byte cap too


def test_non_utf8_content_is_base64_never_garbled():
    _d, seqs = candidate_store("cc-bin")
    data = _read("cc-bin", seqs["binary"], "after")["data"]
    assert data["encoding"] == "base64" and data["content"] == "//4AYmluYXJ5"


def test_a_corrupt_blob_is_refused_with_the_status():
    directory, seqs = candidate_store("cc-bad")
    blobs = sorted(os.path.join(r, n) for r, _d, f in os.walk(os.path.join(directory, "blobs")) for n in f)
    import gzip
    for path in blobs:
        with open(path, "wb") as handle:
            handle.write(gzip.compress(b"tampered"))
    env = _read("cc-bad", seqs["staged"])
    assert env["error"]["code"] == policy.EVIDENCE_VERIFY_FAILED and env["error"]["status"] == "BLOB_CORRUPT"


def test_a_broken_chain_serves_no_content():
    directory, seqs = candidate_store("cc-chain")
    rewrite_record_line(directory, 0, lambda r: model.canonical_bytes({**r, "payload": {"x": 1}}))
    env = _read("cc-chain", seqs["staged"])
    assert env["error"]["code"] == policy.EVIDENCE_VERIFY_FAILED and env["error"]["status"] == "CHAIN_BROKEN"


def test_unsealed_store_is_live_observation_and_still_authorized():
    _d, seqs = candidate_store("cc-open", seal=False)
    env = _read("cc-open", seqs["staged"])
    assert env["error"] is None and env["consistency"]["kind"] == "live_observation"


def test_missing_store_is_a_typed_not_found():
    assert _read("cc-none", 1)["error"]["code"] == policy.EVIDENCE_STORE_NOT_FOUND


@pytest.mark.parametrize("bad", [{"seq": 0}, {"seq": "1"}, {"seq": True}, {"seq": None}, {"part": ""}, {"part": "../x"}, {"part": "A B"}, {"part": 5}, {"run_id": ".hidden"}, {"run_id": None}])
def test_parameters_are_validated_before_the_policy_or_any_io(bad):
    request = {"operation": "evidence.change_content", "run_id": "r", "seq": 1, "part": "diff", **bad}
    env = run_kup_operation(_cfg("off"), request)
    assert env["error"]["code"] == policy.INVALID_REQUEST and env["data"] is None


def test_the_response_never_includes_content_of_another_part_or_record():
    _d, seqs = candidate_store("cc-iso")
    text = json.dumps(_read("cc-iso", seqs["staged"], "diff"))
    assert "class B" not in text and "reasoning" not in text


def test_capabilities_advertise_content_only_when_kriya_authorizes_it():
    off = run_kup_operation(strict_config(), {"operation": "capabilities"})["data"]
    on = run_kup_operation(_cfg("on"), {"operation": "capabilities"})["data"]
    assert off["features"]["evidence_content"] is False and on["features"]["evidence_content"] is True
    assert on["features"]["evidence_content_classes"] == ["candidate_content"] and off["features"]["evidence_content_classes"] == []
    assert on["limits"]["evidence_content_max_bytes"] == policy.EVIDENCE_BLOB_MAX_BYTES
    assert "evidence.change_content" in off["operations"]


# ------------------------------------------------------------------ the class table must cover every key the producers emit

def _candidate_content_keys():
    """Every blob key any production ``emit("candidate.change", ..., content={...})`` call can emit (AST of the producers)."""
    keys, calls = set(), 0
    for path in (ROOT / "kriya").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) == "emit" and node.args \
                    and isinstance(node.args[0], ast.Constant) and node.args[0].value == "candidate.change":
                calls += 1
                for kw in node.keywords:
                    if kw.arg == "content" and isinstance(kw.value, ast.Dict):
                        keys |= {k.value for k in kw.value.keys if isinstance(k, ast.Constant)}
    return keys, calls


def test_the_class_table_covers_every_candidate_blob_key_the_producers_emit():
    keys, calls = _candidate_content_keys()
    assert calls >= 3 and keys >= {"before", "after", "diff", "proposed", "attempted_edits"}, (calls, keys)
    assert keys <= set(policy.BLOB_CLASSES), keys - set(policy.BLOB_CLASSES)
    assert all(policy.BLOB_CLASSES[k] == policy.CONTENT_CLASS_CANDIDATE for k in keys)


def test_the_class_table_holds_only_candidate_classes_in_m3():
    assert set(policy.BLOB_CLASSES.values()) == {policy.CONTENT_CLASS_CANDIDATE}
    assert policy.CONTENT_CLASS_UNCLASSIFIED not in policy.BLOB_CLASSES.values()
