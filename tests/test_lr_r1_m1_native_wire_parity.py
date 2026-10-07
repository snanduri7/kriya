"""LR-R1-M1 OBS-1: the native request body M1 records is the body the native
runtime actually received.

Real LLMClient, real OllamaNativeRuntimeAdapter, real HTTP transport, a
loopback server standing in for Ollama's /api/chat. For every native request
shape M1 records - plain, streamed, JSON mode, schema-constrained, tool
calls, and the two internal resends that make one logical call two wire
requests (``empty_content_floor``, ``response_format_dropped``) - each
recorded ``model.request`` ``wire_body`` must equal, one for one and in
order, the JSON body the server received: equal as parsed structures and
equal in canonical bytes. Test-only assurance: no provider semantics change.
"""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from kriya.config import AppConfig
from kriya.core.attempt_evidence import model, reader, scope
from kriya.core.llm import LLMClient
from kriya.core.role_metrics import model_role
from kriya.core.state_paths import ENV_STATE_DIR


class _Loopback(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False


def _reply(content="ok", **extra):
    return {"message": {"role": "assistant", "content": content, "thinking": "", **extra}, "done": True,
            "done_reason": "stop", "prompt_eval_count": 0, "eval_count": 1, "model": "m:1"}


class _ScriptedNative:
    """Answers each POST with the next scripted (status, body); records the
    raw bytes and the parsed body of every request it received."""

    def __init__(self, script):
        self.script = list(script)
        self.received = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            timeout = 5

            def log_message(self, *args):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                body = json.loads(raw)
                server.received.append((self.path, raw, body))
                status, answer = server.script.pop(0) if server.script else (200, _reply())
                if status == 200 and body.get("stream"):
                    lines = [{"message": {"content": "o"}, "done": False, "model": "m:1"},
                             {"message": {"content": "k"}, "done": True, "done_reason": "stop",
                              "prompt_eval_count": 0, "eval_count": 2}]
                    self._send(200, "".join(json.dumps(line) + "\n" for line in lines).encode())
                else:
                    self._send(status, json.dumps(answer).encode())

            def _send(self, status, payload):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.httpd = _Loopback(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _config(url, *, reasoning=False, tools=False):
    from kriya.config.config import ModelCapabilities

    cfg = AppConfig()
    if tools:
        cfg.llm.capabilities = ModelCapabilities(native_tool_calls=True)
    cfg.llm.base_url = url
    cfg.llm.model = "m:1"
    cfg.llm.inference_runtime = "ollama_native"
    cfg.llm.context_window = 8192
    cfg.llm.reasoning = reasoning
    cfg.llm.extra_body = {"options": {"top_p": 0.8, "top_k": 20}}
    cfg.llm_chain = []
    return cfg


class _Context:
    def __init__(self, run_id):
        self.run_id = run_id


def _run(tmp_path, monkeypatch, script, call, *, reasoning=False, tools=False):
    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path / "state"))
    server = _ScriptedNative(script)
    try:
        cfg = _config(server.url, reasoning=reasoning, tools=tools)

        async def body():
            llm = LLMClient(cfg)
            try:
                with model_role("developer"):
                    return await call(llm)
            finally:
                await llm.aclose()
        with scope.run_scope(_Context("run-parity")):
            scope.ensure_store(cfg)
            result = asyncio.run(body())
            scope._RUN.get().writer.seal("test")
    finally:
        server.close()
    run = reader.open_run(str(tmp_path / "state"), "run-parity")
    requests = [r for r in run.records() if r["kind"] == "model.request"]
    return result, run, requests, server.received


def _assert_parity(run, requests, received, *, wires):
    assert len(requests) == len(received) == wires, (len(requests), len(received))
    for record, (path, raw, body) in zip(requests, received, strict=True):
        assert path == "/api/chat"
        recorded = json.loads(run.blob(record["blobs"]["wire_body"]))
        assert recorded == body                                            # the same structure
        assert model.canonical_bytes(recorded) == model.canonical_bytes(json.loads(raw))  # the same canonical bytes
        assert record["payload"]["wire_body"] == "RECORDED" and record["payload"]["dispatched"] is True


def test_a_plain_request(tmp_path, monkeypatch):
    _result, run, requests, received = _run(tmp_path, monkeypatch, [(200, _reply())],
                                            lambda llm: llm.complete_result("SYS", "USER"))
    _assert_parity(run, requests, received, wires=1)
    assert received[0][2]["stream"] is False and received[0][2]["truncate"] is False


def test_a_streamed_request(tmp_path, monkeypatch):
    tokens = []
    _result, run, requests, received = _run(tmp_path, monkeypatch, [],
                                            lambda llm: llm.complete_result("S", "U", stream_callback=tokens.append))
    _assert_parity(run, requests, received, wires=1)
    assert received[0][2]["stream"] is True and tokens == ["o", "k"]


def test_a_json_mode_request(tmp_path, monkeypatch):
    _result, run, requests, received = _run(tmp_path, monkeypatch, [(200, _reply('{"a": 1}'))],
                                            lambda llm: llm.complete_result("S", "U", json_mode=True))
    _assert_parity(run, requests, received, wires=1)
    assert received[0][2]["format"] == "json"


def test_a_schema_constrained_request(tmp_path, monkeypatch):
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}
    _result, run, requests, received = _run(tmp_path, monkeypatch, [(200, _reply('{"a": 1}'))],
                                            lambda llm: llm.complete_result("S", "U", response_schema=schema))
    _assert_parity(run, requests, received, wires=1)
    assert received[0][2]["format"] == schema


def test_a_tool_call_request(tmp_path, monkeypatch):
    tools = [{"type": "function", "function": {"name": "find_symbol", "parameters": {"type": "object"}}}]
    answer = _reply("", tool_calls=[{"function": {"name": "find_symbol", "arguments": {"symbol": "x"}}}])
    _result, run, requests, received = _run(
        tmp_path, monkeypatch, [(200, answer)],
        lambda llm: llm.complete_with_tools_result([{"role": "user", "content": "find x"}], tools), tools=True)
    _assert_parity(run, requests, received, wires=1)
    assert received[0][2]["tools"] == tools


def test_the_empty_content_floor_resend_is_two_recorded_wires(tmp_path, monkeypatch):
    _result, run, requests, received = _run(tmp_path, monkeypatch, [(200, _reply("")), (200, _reply('{"a": 1}'))],
                                            lambda llm: llm.complete_result("S", "U", json_mode=True))
    _assert_parity(run, requests, received, wires=2)
    assert [r["payload"]["wire_reason"] for r in requests] == ["initial", "empty_content_floor"]
    assert received[0][2]["options"]["num_predict"] < received[1][2]["options"]["num_predict"]


def test_the_response_format_dropped_resend_is_two_recorded_wires(tmp_path, monkeypatch):
    _result, run, requests, received = _run(
        tmp_path, monkeypatch, [(400, {"error": "format not supported with thinking"}), (200, _reply("ok"))],
        lambda llm: llm.complete_result("S", "U", json_mode=True), reasoning=True)
    _assert_parity(run, requests, received, wires=2)
    assert [r["payload"]["wire_reason"] for r in requests] == ["initial", "response_format_dropped"]
    assert "format" in received[0][2] and "format" not in received[1][2]


@pytest.mark.parametrize("capture", ["digest_only"])
def test_digest_only_records_the_exact_bodys_digest(tmp_path, monkeypatch, capture):
    """Without content, the recorded digest is still of the exact body sent."""
    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path / "state"))
    server = _ScriptedNative([(200, _reply())])
    try:
        cfg = _config(server.url)
        cfg.evidence.attempt_recorder.capture = capture

        async def body():
            llm = LLMClient(cfg)
            try:
                return await llm.complete_result("S", "U")
            finally:
                await llm.aclose()
        with scope.run_scope(_Context("run-digest")):
            scope.ensure_store(cfg)
            asyncio.run(body())
            scope._RUN.get().writer.seal("test")
    finally:
        server.close()
    [request] = [r for r in reader.open_run(str(tmp_path / "state"), "run-digest").records()
                 if r["kind"] == "model.request"]
    [(_path, _raw, sent)] = server.received
    assert request["blobs"] == {}
    assert request["content_digests"]["wire_body"]["digest"] == model.digest(model.as_bytes(sent))


def test_the_plain_native_body_is_pinned_exactly(tmp_path, monkeypatch):
    """OBS-1's original gap: the native /api/chat body was not pinned
    exactly, so an extra or missing key passed every suite. This pins every
    key and value of the plain request (parity above then extends the pin
    to the recorded body)."""
    _result, _run_store, _requests, received = _run(tmp_path, monkeypatch, [(200, _reply())],
                                                    lambda llm: llm.complete_result("SYS", "USER"))
    [(_path, _raw, body)] = received
    assert body == {
        "model": "m:1",
        "messages": [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}],
        "stream": False,
        "options": {"num_ctx": 8192, "top_p": 0.8, "top_k": 20, "temperature": 0.2,
                    "num_predict": body["options"]["num_predict"]},
        "think": False,
        "truncate": False,
    }
    assert isinstance(body["options"]["num_predict"], int) and body["options"]["num_predict"] > 0
