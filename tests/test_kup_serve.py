"""``kriya kup-stdio`` (GUI M5 persistent read transport): protocol, allowlist, fail-closed behaviour, per-request authority, and PARITY with the
one-shot commands (the same operation implementation must give the same typed result through either transport)."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import textwrap
from unittest.mock import patch

import pytest
from _kup_evidence_fixtures import (
    MESSAGES,
    acquire_snapshot_id,
    candidate_store,
    model_call_store,
    rich_store,
    seed_trace_run,
    state_dir,
)
from _strict_doubles import strict_config
from click.testing import CliRunner

from kriya.cli import main
from kriya.config import AppConfig
from kriya.core.attempt_evidence import model
from kriya.core.attempt_evidence.writer import AttemptEvidenceWriter
from kriya.kup import policy, serve

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _roundtrip(lines, config_factory=AppConfig):
    """Feed request lines to ``serve.serve`` in-process; returns the parsed response lines after the ready line."""
    stdin = io.BytesIO(b"".join(line if isinstance(line, bytes) else line.encode() + b"\n" for line in lines))
    stdout = io.BytesIO()
    with patch("kriya.config.load_config", side_effect=lambda *a, **k: config_factory()):
        assert serve.serve(stdin, stdout) == 0
    out = [json.loads(line) for line in stdout.getvalue().decode().splitlines()]
    assert out[0]["event"] == "ready" and out[0]["protocol"] == serve.PROTOCOL
    return out[1:]


def _req(operation, request_id="r1", **arguments):
    return json.dumps({"id": request_id, "schema_version": 1, "operation": operation, "arguments": arguments})


# ------------------------------------------------------------------ protocol

def test_one_response_per_request_in_order_with_the_matching_id():
    out = _roundtrip([_req("capabilities", "a"), _req("snapshot.list", "b"), _req("capabilities", "c")])
    assert [o["id"] for o in out] == ["a", "b", "c"] and all(o["ok"] for o in out)
    assert [o["envelope"]["operation"] for o in out] == ["capabilities", "snapshot.list", "capabilities"]
    assert all(o["envelope"]["request_id"] == o["id"] for o in out)


@pytest.mark.parametrize("line", [b"not json\n", b"[1,2]\n", b"\xff\xfe\n", b'{"id":"x"}\n', b'{"schema_version":1,"operation":"capabilities"}\n'])
def test_a_malformed_request_is_refused_and_the_process_keeps_serving(line):
    out = _roundtrip([line, _req("capabilities", "after")])
    assert out[0]["ok"] is False and out[0]["error"]["code"] in (policy.INVALID_REQUEST, policy.UNSUPPORTED_SCHEMA_VERSION)
    assert out[1]["ok"] is True and out[1]["id"] == "after"


def test_an_oversized_line_is_refused_whole_and_the_stream_stays_in_step():
    big = b'{"id":"big","schema_version":1,"operation":"capabilities","arguments":{"x":"' + b"a" * (serve.MAX_REQUEST_BYTES + 10) + b'"}}\n'
    out = _roundtrip([big, _req("capabilities", "next")])
    assert out[0]["ok"] is False and "exceeds" in out[0]["error"]["message"] and out[0]["id"] is None
    assert out[1] == {**out[1], "id": "next", "ok": True}
    assert len(out) == 2


@pytest.mark.parametrize("operation", ["os.system", "capabilities ", "", "history", "snapshot.acquire; rm -rf", None, 7, "evidence.blob"])
def test_only_allowlisted_operations_are_dispatchable(operation):
    out = _roundtrip([json.dumps({"id": "z", "schema_version": 1, "operation": operation, "arguments": {}})])
    assert out[0]["ok"] is False and out[0]["error"]["code"] == policy.INVALID_REQUEST and "allowlisted" in out[0]["error"]["message"]


def test_the_allowlist_is_exactly_the_kup_operation_set():
    assert set(serve.ARGUMENTS) == set(policy.OPERATIONS)
    for forbidden in ("recover", "generate", "fix", "shell", "exec", "authority.approve", "config.set"):
        assert forbidden not in serve.ARGUMENTS


def test_an_unknown_argument_or_request_field_or_value_type_is_refused():
    for line in (_req("capabilities", "a", extra=1), _req("history.list", "b", snapshot_id="s", workspace="/x", sneaky=True),
                 json.dumps({"id": "c", "schema_version": 1, "operation": "capabilities", "arguments": {}, "cwd": "/"}),
                 json.dumps({"id": "d", "schema_version": 1, "operation": "snapshot.list", "arguments": {"verify": {"nested": 1}}}),
                 json.dumps({"id": "e", "schema_version": 1, "operation": "capabilities", "arguments": []})):
        out = _roundtrip([line])
        assert out[0]["ok"] is False and out[0]["error"]["code"] == policy.INVALID_REQUEST, line


def test_the_schema_and_capability_versions_must_match_or_the_request_is_refused():
    for bad in (2, 0, "1", True, None):
        out = _roundtrip([json.dumps({"id": "v", "schema_version": bad, "operation": "capabilities", "arguments": {}})])
        assert out[0]["error"]["code"] == policy.UNSUPPORTED_SCHEMA_VERSION, bad
    out = _roundtrip([json.dumps({"id": "v", "schema_version": 1, "capability_set": "9.9", "operation": "capabilities", "arguments": {}})])
    assert out[0]["error"]["code"] == policy.UNSUPPORTED_SCHEMA_VERSION
    ok = _roundtrip([json.dumps({"id": "v", "schema_version": 1, "capability_set": "1.1", "operation": "capabilities", "arguments": {}})])
    assert ok[0]["ok"] is True


@pytest.mark.parametrize("bad_id", ["", " ", "-lead", "a b", "a" * 129, 5, None, "a/b", "x\ny"])
def test_a_bad_request_id_is_refused_with_a_null_id(bad_id):
    out = _roundtrip([json.dumps({"id": bad_id, "schema_version": 1, "operation": "capabilities", "arguments": {}})])
    assert out[0]["ok"] is False and out[0]["id"] is None and out[0]["error"]["code"] == policy.INVALID_REQUEST


def test_an_internal_fault_answers_that_request_with_host_error_and_the_process_keeps_serving(monkeypatch):
    from kriya.kup import cli_ops
    real = cli_ops.run_kup_operation
    calls = []

    def flaky(cfg, request, request_id="cli"):
        calls.append(request["operation"])
        if request["operation"] == "snapshot.list":
            raise RuntimeError("boom")
        return real(cfg, request, request_id)

    monkeypatch.setattr(cli_ops, "run_kup_operation", flaky)
    out = _roundtrip([_req("snapshot.list", "bad"), _req("capabilities", "good")])
    assert out[0]["ok"] is False and out[0]["error"]["code"] == serve.HOST_ERROR and "RuntimeError" in out[0]["error"]["message"] and out[0]["id"] == "bad"
    assert out[1]["ok"] is True and out[1]["envelope"]["error"] is None and calls == ["snapshot.list", "capabilities"]


def test_a_configuration_failure_is_the_typed_d7_envelope_per_request_and_the_next_request_recovers():
    from kriya.config.authority import ConfigAuthorityError, ConfigAuthorityViolation, ConfigSource, FieldClassification
    violation = ConfigAuthorityViolation(field_path="mcp.evil.command", source=ConfigSource.EXPLICIT_CONFIG_PATH_INSIDE_WORKSPACE,
                                         classification=FieldClassification.SECURITY_AUTHORITY, reason="repository-sourced security authority")
    outcomes = iter([ConfigAuthorityError([violation]), ValueError("paths.state must be absolute"), AppConfig()])

    def factory(*_a, **_k):
        item = next(outcomes)
        if isinstance(item, Exception):
            raise item
        return item

    stdin = io.BytesIO(b"".join(_req("capabilities", i).encode() + b"\n" for i in ("a", "b", "c")))
    stdout = io.BytesIO()
    with patch("kriya.config.load_config", side_effect=factory):
        serve.serve(stdin, stdout)
    a, b, c = (json.loads(line) for line in stdout.getvalue().decode().splitlines()[1:])
    assert a["ok"] and a["envelope"]["error"]["code"] == policy.CONFIG_AUTHORITY_REFUSED and "SEC-009" in a["envelope"]["error"]["message"] and a["envelope"]["data"] is None
    assert b["envelope"]["error"]["code"] == policy.CONFIG_LOAD_FAILED
    assert c["envelope"]["error"] is None


def test_workspace_status_loads_no_configuration_like_runs_status(tmp_path):
    with patch("kriya.config.load_config", side_effect=AssertionError("workspace.status must not load configuration")):
        out = serve.parse_request(_req("workspace.status", "w", workspace=str(tmp_path)).encode())
        line = json.loads(serve.answer(out, None, None))
    assert line["envelope"]["data"]["status"] == "CLEAN"


# ------------------------------------------------------------------ authority is evaluated per request, never cached

def test_content_visibility_is_read_again_for_every_request():
    _d, seqs = model_call_store("srv-auth")
    state = {"visibility": "off"}
    request = _req("evidence.call_content", "c", run_id="srv-auth", seq=seqs["request"], part="messages")

    def factory(*_a, **_k):
        return strict_config(evidence={"attempt_recorder": {"content_visibility": state["visibility"]}})

    seen = []
    for visibility in ("off", "on", "off", "on"):
        state["visibility"] = visibility
        out = _roundtrip([request], config_factory=factory)[0]["envelope"]
        seen.append(out["error"]["reason"] if out["error"] else json.loads(out["data"]["content"]) == MESSAGES)
    assert seen == ["content_visibility_off", True, "content_visibility_off", True]


def test_verification_is_recomputed_for_every_records_request_after_the_store_is_tampered():
    directory, _writer = rich_store("srv-tamper")
    request = _req("evidence.records", "r", run_id="srv-tamper", limit=5)
    before = _roundtrip([request])[0]["envelope"]
    assert before["error"] is None and before["consistency"]["kind"] == "sealed_store"
    path = os.path.join(directory, "records.jsonl")
    raw = open(path, "rb").read().split(b"\n")
    raw[1] = raw[1].replace(b"planning", b"PLANNING")
    open(path, "wb").write(b"\n".join(raw))
    after = _roundtrip([request, _req("evidence.verify", "v", run_id="srv-tamper")])
    assert after[0]["envelope"]["error"] is not None and after[0]["envelope"]["data"] is None, "a tampered store was served from a once-verified state"
    assert after[1]["envelope"]["data"]["status"] != "VERIFIED"


# ------------------------------------------------------------------ the real process: stdout is protocol only; death and EOF

def _spawn(extra_code=""):
    code = "import sys\n" + textwrap.dedent(extra_code) + "\nfrom kriya.cli import main\nmain(['kup-stdio'])\n"
    env = {**os.environ, "PYTHONPATH": ROOT}
    return subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=ROOT)


def test_library_output_cannot_contaminate_the_protocol_stream():
    noisy = """
import kriya.kup.cli_ops as c
_real = c.run_kup_operation
def noisy(cfg, request, request_id="cli"):
    print("NOISE ON STDOUT")
    import os; os.write(1, b"RAW FD1 NOISE\\n")
    return _real(cfg, request, request_id)
c.run_kup_operation = noisy
"""
    proc = _spawn(noisy)
    out, err = proc.communicate((_req("capabilities", "n1") + "\n" + _req("capabilities", "n2") + "\n").encode(), timeout=60)
    lines = out.decode().splitlines()
    assert [json.loads(line).get("id", "ready") for line in lines] == ["ready", "n1", "n2"], (lines, err)
    assert b"NOISE ON STDOUT" in err and b"RAW FD1 NOISE" in err and proc.returncode == 0


def test_eof_ends_the_process_cleanly_and_a_closed_stdin_before_any_request_is_fine():
    proc = _spawn()
    out, _err = proc.communicate(b"", timeout=60)
    assert proc.returncode == 0 and json.loads(out.decode().splitlines()[0])["event"] == "ready"


# ------------------------------------------------------------------ PARITY: the same typed result through either transport

VOLATILE = {"observed_at", "request_id", "duration_ms", "verified_at"}  # per-call clocks and the echoed id: transport metadata, not results


def _strip(value):
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def _one_shot(argv, cfg):
    with patch("kriya.cli.load_config", return_value=cfg):
        res = CliRunner().invoke(main, argv)
    assert res.exit_code == 0, res.output
    return json.loads(res.output)


def _persistent(operation, cfg, **arguments):
    out = _roundtrip([_req(operation, "p1", **arguments)], config_factory=lambda: cfg)[0]
    assert out["ok"] is True, out
    return out["envelope"]


def _same(operation, argv, cfg, **arguments):
    one, persistent = _one_shot(argv, cfg), _persistent(operation, cfg, **arguments)
    assert _strip(persistent) == _strip(one), operation
    assert one["operation"] == persistent["operation"] == operation
    return persistent


def test_representative_operations_give_equal_typed_results_through_both_transports(tmp_path):
    cfg = strict_config(evidence={"attempt_recorder": {"content_visibility": "on"}})
    _d, mseqs = model_call_store("par-model")
    _c, cseqs = candidate_store("par-cand")
    rich_store("par-rich")
    ws = tmp_path / "ws"
    ws.mkdir()
    writer = AttemptEvidenceWriter(state_dir(), "par-ev", capture=model.CAPTURE_FULL, manifest={"workspace_identity": __import__("kriya.control.workspace_identity", fromlist=["x"]).workspace_identity(str(ws)), "workspace_path": str(ws)})
    writer.append("run.opened", {})
    writer.seal("run_closed")
    seed_trace_run("par-trace", {"run_id": "par-ev", "status": "OPEN", "capture": "full"})
    snap = acquire_snapshot_id()
    caps = _same("capabilities", ["traces", "--json", "--capabilities"], cfg)
    assert caps["data"]["features"]["evidence_content"] is True
    _same("snapshot.list", ["traces", "--json", "--snapshots"], cfg)
    listing = _same("history.list", ["traces", "--json", "--snapshot-id", snap, "-n", "10"], cfg, snapshot_id=snap, limit=10)
    assert listing["data"]["runs"]
    scoped = _same("history.list", ["traces", "--json", "--snapshot-id", snap, "-n", "10", "--workspace", str(ws)], cfg, snapshot_id=snap, limit=10, workspace=str(ws))
    assert [r["run_id"] for r in scoped["data"]["runs"]] == ["par-trace"]
    _same("history.detail", ["traces", "--json", "--snapshot-id", snap, "--run-id", "par-trace"], cfg, snapshot_id=snap, run_id="par-trace")
    _same("history.prompt", ["traces", "--json", "--snapshot-id", snap, "--run-id", "par-trace", "--include-prompt"], cfg, snapshot_id=snap, run_id="par-trace")
    _same("workspace.describe", ["traces", "--json", "--describe-workspace", str(ws)], cfg, workspace=str(ws))
    _same("workspace.status", ["runs", "status", "--workspace", str(ws), "--json", "--kup-version", "1"], cfg, workspace=str(ws))
    _same("evidence.manifest", ["evidence", "manifest", "par-rich", "--json", "--kup-version", "1"], cfg, run_id="par-rich")
    _same("evidence.manifest", ["evidence", "manifest", "par-trace", "--json", "--kup-version", "1", f"--snapshot-id={snap}", "--from-trace"], cfg, run_id="par-trace", snapshot_id=snap, from_trace=True)
    _same("evidence.verify", ["evidence", "verify", "par-rich", "--json", "--kup-version", "1"], cfg, run_id="par-rich")
    records = _same("evidence.records", ["evidence", "records", "par-rich", "--json", "--kup-version", "1", "--limit=4", "--kind=model.request"], cfg, run_id="par-rich", limit=4, kind=["model.request"])
    assert records["data"]["records"]
    _same("evidence.change_content", ["evidence", "change-content", "par-cand", "--json", "--kup-version", "1", f"--seq={cseqs['staged']}", "--part=diff"], cfg, run_id="par-cand", seq=cseqs["staged"], part="diff")
    served = _same("evidence.call_content", ["evidence", "call-content", "par-model", "--json", "--kup-version", "1", f"--seq={mseqs['request']}", "--part=messages"], cfg, run_id="par-model", seq=mseqs["request"], part="messages")
    assert json.loads(served["data"]["content"]) == MESSAGES


def test_refusals_are_equal_through_both_transports_and_content_is_off_by_default():
    _d, seqs = model_call_store("par-off")
    off = AppConfig()
    refused = _same("evidence.call_content", ["evidence", "call-content", "par-off", "--json", "--kup-version", "1", f"--seq={seqs['request']}", "--part=messages"], off,
                    run_id="par-off", seq=seqs["request"], part="messages")
    assert refused["error"]["code"] == policy.EVIDENCE_CONTENT_POLICY and refused["error"]["reason"] == "content_visibility_off" and refused["data"] is None
    wrong = _same("evidence.change_content", ["evidence", "change-content", "par-off", "--json", "--kup-version", "1", f"--seq={seqs['request']}", "--part=messages"],
                  strict_config(evidence={"attempt_recorder": {"content_visibility": "on"}}), run_id="par-off", seq=seqs["request"], part="messages")
    assert wrong["error"]["reason"] == "class_not_served_here"
    _same("evidence.verify", ["evidence", "verify", "no-such-run", "--json", "--kup-version", "1"], off, run_id="no-such-run")
    _same("workspace.describe", ["traces", "--json", "--describe-workspace", "/definitely/not/here"], off, workspace="/definitely/not/here")
