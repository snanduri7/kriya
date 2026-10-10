"""KUP ``evidence.call_content`` (Run Cockpit M4, Context): the semantic, authorized read of what ONE recorded model request carried (messages,
tools, wire body). Same fail-closed order as ``evidence.change_content``; a different content class (``model_input``): a key of the other
class is refused here and this class's keys are refused there. Kriya's own visibility setting (default off) owns it."""
from __future__ import annotations

import ast
import json
import pathlib

import pytest
from _kup_evidence_fixtures import MESSAGES, candidate_store, model_call_store
from _strict_doubles import strict_config

from kriya.core.attempt_evidence import model
from kriya.kup import policy
from kriya.kup.cli_ops import run_kup_operation

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _cfg(visibility="on"):
    return strict_config(evidence={"attempt_recorder": {"content_visibility": visibility}})


def _call(run_id, seq, part="messages", cfg=None):
    return run_kup_operation(cfg or _cfg(), {"operation": "evidence.call_content", "run_id": run_id, "seq": seq, "part": part})


def test_authorized_messages_are_the_recorded_bytes_with_call_identity_and_provenance():
    _d, seqs = model_call_store("cl-1")
    env = _call("cl-1", seqs["request"])
    data = env["data"]
    assert env["error"] is None and env["operation"] == "evidence.call_content"
    assert json.loads(data["content"]) == MESSAGES and data["encoding"] == "utf-8" and data["digest_ok"] is True
    assert data["content_class"] == "model_input" and data["part"] == "messages" and data["role"] == "developer" and data["call_seq"] == 1 and data["attempt"] == 1
    assert data["ref"] == model.digest(json.dumps(MESSAGES, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()) and data["bytes_total"] == len(data["content"].encode())
    assert env["consistency"]["kind"] == "sealed_store" and env["consistency"]["head_digest"] == data["head_digest"]
    assert _call("cl-1", seqs["request"], "wire_body")["data"]["content"] == '{"model":"dev-model"}'


def test_visibility_off_refuses_before_any_store_access(monkeypatch):
    from kriya.core.attempt_evidence import reader
    monkeypatch.setattr(reader.EvidenceRun, "verify", lambda *a, **k: pytest.fail("touched the store before the policy check"))
    env = _call("nothing", 1, cfg=_cfg("off"))
    assert env["error"]["code"] == policy.EVIDENCE_CONTENT_POLICY and env["error"]["content_class"] == "model_input" and env["error"]["reason"] == "content_visibility_off"
    assert _call("nothing", 1, cfg=strict_config())["error"]["code"] == policy.EVIDENCE_CONTENT_POLICY  # the default config is off


def test_each_operation_serves_only_its_own_class():
    _d, seqs = model_call_store("cl-cls")
    _c, cseqs = candidate_store("cl-cand")
    env = _call("cl-cls", seqs["candidate"], "diff")
    assert env["error"]["code"] == policy.EVIDENCE_CONTENT_POLICY and env["error"]["reason"] == "class_not_served_here" and env["error"]["content_class"] == "candidate_content"
    wrong = run_kup_operation(_cfg(), {"operation": "evidence.change_content", "run_id": "cl-cls", "seq": seqs["request"], "part": "messages"})
    assert wrong["error"]["code"] == policy.EVIDENCE_CONTENT_POLICY and wrong["error"]["reason"] == "class_not_served_here" and wrong["error"]["content_class"] == "model_input"
    assert run_kup_operation(_cfg(), {"operation": "evidence.change_content", "run_id": "cl-cand", "seq": cseqs["staged"], "part": "diff"})["error"] is None


@pytest.mark.parametrize("visibility", ["on", "off"])
def test_a_key_outside_the_class_table_is_never_served(visibility):
    _d, seqs = model_call_store(f"cl-unk-{visibility}")
    for part in ("content", "reasoning", "tool_calls", "analysis_model_claimed", "event"):
        env = _call(f"cl-unk-{visibility}", seqs["response"], part, cfg=_cfg(visibility))
        assert env["error"]["code"] == policy.EVIDENCE_CONTENT_POLICY and env["error"]["content_class"] == "unclassified" and env["error"]["reason"] == "unclassified_key" and env["data"] is None, part


def test_digest_only_capture_names_the_reason_and_a_bare_request_has_nothing_recorded():
    _d, seqs = model_call_store("cl-dig", capture=model.CAPTURE_DIGEST_ONLY)
    assert _call("cl-dig", seqs["request"])["error"]["reason"] == "digest_only"
    _e, seqs2 = model_call_store("cl-bare")
    assert _call("cl-bare", seqs2["bare"])["error"]["reason"] == "part_not_recorded"


def test_only_model_request_records_are_addressable():
    _d, seqs = model_call_store("cl-kind")
    for seq in (seqs["response"], seqs["candidate"], 999):
        assert _call("cl-kind", seq)["error"]["code"] == policy.INVALID_REQUEST


def test_the_cap_comes_from_the_recorded_size_before_the_blob_is_read(monkeypatch):
    from kriya.core.attempt_evidence import reader
    _d, seqs = model_call_store("cl-big")
    monkeypatch.setattr(policy, "EVIDENCE_BLOB_MAX_BYTES", 20)
    reads = []
    real = reader.EvidenceRun.blob
    monkeypatch.setattr(reader.EvidenceRun, "blob", lambda self, ref: (reads.append(ref), real(self, ref))[1])
    env = _call("cl-big", seqs["request"])
    assert env["error"]["code"] == policy.EVIDENCE_BLOB_TOO_LARGE and env["error"]["bytes_total"] > 20
    assert len(reads) == len(set(reads))


def test_a_missing_store_and_bad_parameters_are_typed():
    assert _call("cl-none", 1)["error"]["code"] == policy.EVIDENCE_STORE_NOT_FOUND
    for bad in ({"seq": 0}, {"seq": "1"}, {"part": "../x"}, {"part": ""}, {"run_id": ".hidden"}):
        request = {"operation": "evidence.call_content", "run_id": "r", "seq": 1, "part": "messages", **bad}
        assert run_kup_operation(_cfg("off"), request)["error"]["code"] == policy.INVALID_REQUEST


def test_capabilities_list_both_content_classes_only_when_kriya_authorizes_content():
    off = run_kup_operation(strict_config(), {"operation": "capabilities"})["data"]
    on = run_kup_operation(_cfg(), {"operation": "capabilities"})["data"]
    assert off["features"]["evidence_content_classes"] == [] and on["features"]["evidence_content_classes"] == ["candidate_content", "model_input"]
    assert "evidence.call_content" in off["operations"]


def _emitted_keys(kind):
    keys, calls = set(), 0
    for path in (ROOT / "kriya").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) == "emit" and node.args \
                    and isinstance(node.args[0], ast.Constant) and node.args[0].value == kind:
                calls += 1
                for kw in node.keywords:
                    if kw.arg == "content" and isinstance(kw.value, ast.Dict):
                        keys |= {k.value for k in kw.value.keys if isinstance(k, ast.Constant)}
    return keys, calls


def test_the_class_table_covers_every_blob_key_the_model_request_producers_emit():
    keys, calls = _emitted_keys("model.request")
    assert calls >= 2 and keys >= {"messages", "tools", "wire_body"}, (calls, keys)
    assert keys <= set(policy.BLOB_CLASSES) and all(policy.BLOB_CLASSES[k] == policy.CONTENT_CLASS_MODEL_INPUT for k in keys)


def test_model_output_keys_are_deliberately_not_classified_in_m4():
    # scope.py builds the model.response content dict in a variable (content / tool_calls / reasoning); Context shows what the model SAW, so none is served
    assert not ({"content", "tool_calls", "reasoning"} & set(policy.BLOB_CLASSES))
