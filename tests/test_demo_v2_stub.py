"""Demo V2 scenario 03: the scripted model stub, its script builder and the committed script. CONTROLLED / SCRIPTED: none of this is a model.

The stub runs in-process on an ephemeral localhost port; no model server, no network beyond loopback.
"""
from __future__ import annotations

import http.client
import importlib.util
import json
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SC = ROOT / "demo-v2" / "scenarios" / "03-controlled-repair"
FIXTURE = ROOT / "demo-v2" / "fixtures" / "controlled-repair" / "repo"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


stub = _load("demo_v2_stub", SC / "stub" / "stub_model_server.py")
SCRIPT = json.loads((SC / "script" / "controlled_script.json").read_text(encoding="utf-8"))


def _chat(port, role_system, stream=False, user="do it"):
    body = json.dumps({"model": "x", "stream": stream, "messages": [{"role": "system", "content": role_system}, {"role": "user", "content": user}]})
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("POST", "/v1/chat/completions", body, {"Content-Type": "application/json"})
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, data


@pytest.fixture
def replay(tmp_path):
    script = {"steps": [{"role": "developer", "label": "first", "content": "FIRST"}, {"role": "developer", "label": "second", "content": "SECOND"},
                        {"role": "reviewer", "label": "r", "content": "REVIEW"}]}
    server = stub.Server(("127.0.0.1", 0), "replay", None, script, str(tmp_path / "log.jsonl"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, tmp_path / "log.jsonl"
    server.shutdown()
    server.server_close()


def test_role_keys():
    assert stub.role_of([{"role": "system", "content": "You are the Kriya Developer Agent. MODE: X"}]) == "developer"
    assert stub.role_of([{"role": "system", "content": "You are the Kriya Reviewer Agent."}]) == "reviewer"
    key = stub.role_of([{"role": "system", "content": "You are the Kriya Goal Spec Compliance Checker. Classify"}])
    assert key.startswith("system:You are the Kriya Goal Spec Compliance")
    assert stub.role_of([{"role": "user", "content": "hi"}]) == "no_system"


def test_sse_is_a_well_formed_stream_with_the_content_and_a_usage_chunk():
    payload = stub.sse("A" * 1000, prompt_tokens=321).decode()
    events = [line[6:] for line in payload.split("\n\n") if line.startswith("data: ")]
    assert events[-1] == "[DONE]"
    chunks = [json.loads(e) for e in events[:-1]]
    assert "".join((c["choices"][0]["delta"].get("content") or "") for c in chunks if c["choices"]) == "A" * 1000
    assert chunks[-2]["choices"][0]["finish_reason"] == "stop"
    assert chunks[-1]["usage"]["prompt_tokens"] == 321 and chunks[-1]["choices"] == []
    assert stub.accumulate(payload.encode())["content"] == "A" * 1000


def test_reported_prompt_tokens_are_honest_for_a_large_prompt():
    # Kriya refuses a provider that reports far fewer prompt tokens than the prompt can hold (PROVIDER_PROMPT_TRUNCATED): 1 token for 8 KB was refused in the measured run
    request = {"messages": [{"role": "user", "content": "x" * 8000}]}
    assert stub.prompt_tokens_of(request) >= 1024
    assert stub.prompt_tokens_of({"messages": []}) >= 1


def test_replay_serves_each_roles_steps_in_order_streamed_or_not(replay):
    server, log = replay
    system = "You are the Kriya Developer Agent."
    status, data = _chat(server.server_address[1], system, stream=True)
    assert status == 200 and stub.accumulate(data)["content"] == "FIRST"
    status, data = _chat(server.server_address[1], system, stream=False)
    assert status == 200 and json.loads(data)["choices"][0]["message"]["content"] == "SECOND"
    assert json.loads(data)["usage"]["prompt_tokens"] >= 1
    status, data = _chat(server.server_address[1], "You are the Kriya Reviewer Agent.")
    assert json.loads(data)["choices"][0]["message"]["content"] == "REVIEW"
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert [(r["role"], r["index"], r["step"]) for r in rows] == [("developer", 0, "first"), ("developer", 1, "second"), ("reviewer", 0, "r")]


def test_an_exhausted_or_unknown_role_is_a_logged_500_never_an_invented_answer(replay):
    server, log = replay
    status, data = _chat(server.server_address[1], "You are the Kriya Reviewer Agent.")
    assert status == 200
    status, data = _chat(server.server_address[1], "You are the Kriya Reviewer Agent.")
    assert status == 500 and "stub_script_exhausted" in data.decode()
    status, _ = _chat(server.server_address[1], "You are the Kriya Mystery Agent.")
    assert status == 500
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert [bool(r.get("unscripted")) for r in rows] == [False, True, True]


def test_models_embeddings_and_unknown_paths(replay):
    server, _ = replay
    port = server.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", "/v1/models")
    assert json.loads(conn.getresponse().read())["data"][0]["id"] == "scripted-stub"
    conn.request("POST", "/v1/embeddings", json.dumps({"input": ["a", "b"]}), {"Content-Type": "application/json"})
    assert len(json.loads(conn.getresponse().read())["data"]) == 2
    conn.request("GET", "/nope")
    assert conn.getresponse().status == 404
    conn.close()


def test_replay_mode_never_opens_an_outbound_connection(replay, monkeypatch):
    server, _ = replay

    def refuse(*_args, **_kwargs):
        raise AssertionError("replay mode must not contact any upstream")

    monkeypatch.setattr(stub.urllib.request, "urlopen", refuse)
    port = server.server_address[1]
    assert _chat(port, "You are the Kriya Developer Agent.")[0] == 200
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    for method, path, body in (("GET", "/v1/models", None), ("GET", "/api/ps", None), ("GET", "/api/version", None), ("POST", "/v1/embeddings", '{"input": "x"}'), ("POST", "/v1/other", "{}")):
        conn.request(method, path, body, {"Content-Type": "application/json"})
        conn.getresponse().read()
    conn.close()
    # the only outbound path in the module is the record-mode forwarder
    assert (SC / "stub" / "stub_model_server.py").read_text(encoding="utf-8").count("urllib.request.urlopen(") == 1


def test_accumulate_reads_json_sse_and_tool_calls():
    assert stub.accumulate(json.dumps(stub.completion("hi")).encode())["content"] == "hi"
    chunk1 = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "edit", "arguments": '{"a"'}}]}}]}
    chunk2 = {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ": 1}"}}]}, "finish_reason": "tool_calls"}]}
    raw = (f"data: {json.dumps(chunk1)}\n\ndata: {json.dumps(chunk2)}\n\ndata: [DONE]\n\n").encode()
    result = stub.accumulate(raw)
    assert result["tool_calls"][0]["function"] == {"name": "edit", "arguments": '{"a": 1}'} and result["finish_reason"] == "tool_calls"


def test_the_builder_is_deterministic_and_labels_overrides(tmp_path):
    log = tmp_path / "record.jsonl"
    rows = [
        {"kind": "chat", "role": "developer", "overridden": True, "override_label": "SCRIPTED: wrong", "response": {"content": "W"}},
        {"kind": "chat", "role": "reviewer", "response": {"content": "R"}},
        {"kind": "other"},
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    outs = []
    for n in (1, 2):
        out = tmp_path / f"out{n}.json"
        subprocess.run([sys.executable, str(SC / "stub" / "build_script.py"), str(log), str(out), "--recorded-model", "m", "--recorded-on", "d"], check=True, capture_output=True)
        outs.append(out.read_bytes())
    assert outs[0] == outs[1]
    built = json.loads(outs[0])
    assert built["controlled"] is True and "CONTROLLED" in built["notice"]
    assert [(s["role"], s["label"], s["content"]) for s in built["steps"]] == [("developer", "SCRIPTED: wrong", "W"), ("reviewer", "recorded verbatim", "R")]


# ---- the committed script -------------------------------------------------------------------------------------------------------

def _file_block(content: str) -> str:
    match = re.search(r'<<<KRIYA:FILE path="pricing.py">>>\n(.*)<<<KRIYA:END_FILE>>>', content, flags=re.S)
    assert match, content
    return match.group(1)


def _run_fixture_tests(source: str, tmp_path: Path):
    """Run the fixture's own tests against `source` as pricing.py (real pytest, isolated directory). Returns the set of failing test names."""
    work = tmp_path / "w"
    work.mkdir(parents=True)
    (work / "pricing.py").write_text(source)
    (work / "test_pricing.py").write_text((FIXTURE / "test_pricing.py").read_text())
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=no", "-rf"], cwd=work, capture_output=True, text=True, check=False,
                            env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"})
    return set(re.findall(r"FAILED test_pricing.py::(\w+)", result.stdout)), result.stdout


def test_the_committed_script_is_controlled_and_well_formed():
    assert SCRIPT["controlled"] is True and "CONTROLLED DETERMINISTIC MODEL-RESPONSE SCENARIO" in SCRIPT["notice"]
    steps = SCRIPT["steps"]
    assert [s["role"] for s in steps].count("developer") == 2
    assert steps[0]["label"].startswith("SCRIPTED") and steps[1]["label"].startswith("SCRIPTED")  # planner and the wrong candidate
    assert all(s["label"] == "recorded verbatim" for s in steps[2:])
    plan = json.loads(steps[0]["content"])
    assert len(plan["subtasks"]) == 1 and plan["subtasks"][0]["planned_files"][0]["path"] == "pricing.py"


def test_the_scripted_wrong_candidate_really_fails_and_the_repair_really_passes(tmp_path):
    developer = [s for s in SCRIPT["steps"] if s["role"] == "developer"]
    wrong, right = _file_block(developer[0]["content"]), _file_block(developer[1]["content"])
    baseline_failures, _ = _run_fixture_tests((FIXTURE / "pricing.py").read_text(), tmp_path / "base")
    wrong_failures, _ = _run_fixture_tests(wrong, tmp_path / "wrong")
    right_failures, out = _run_fixture_tests(right, tmp_path / "right")
    assert baseline_failures == {"test_quarter_off", "test_everything_off"}
    # the wrong candidate must NEWLY break a test that passed at baseline: that is what Kriya's regression-versus-baseline gate rejects it for
    assert "test_no_discount" in wrong_failures and "test_no_discount" not in baseline_failures
    assert right_failures == set(), out
    assert wrong != right


def test_every_scripted_role_is_a_role_the_stub_can_key_on():
    for step in SCRIPT["steps"]:
        assert step["role"] in ("developer", "reviewer") or step["role"].startswith("system:You are")
