"""PRD-014: exact-runtime qualification. Deterministic fixture tests of every
case evaluator's result states, the record store, staleness and role
requirements. Live qualification is tests/test_live_prd014_qualification.py."""
import asyncio
import json
import os
from dataclasses import replace

import pytest
from click.testing import CliRunner

from kriya.config import AppConfig
from kriya.core import model_qualification as mq
from kriya.core.completion import CompletionResult, CompletionStatus
from kriya.core.model_runtime import ModelRuntimeFingerprint

MODEL = "qwen3-coder:30b"


def _fp(**changes):
    fp = ModelRuntimeFingerprint(
        alias=MODEL, endpoint="http://localhost:11434/v1", provider="ollama", provider_version="0.34.2",
        artifact_digest="sha256:abc", kriya_protocol="capabilities-sha256:x",
    )
    return replace(fp, **changes)


def _result(content="", status=CompletionStatus.OK, **kw):
    kw.setdefault("finish_reason", "stop")
    return CompletionResult(status=status, content=content, model=MODEL, **kw)


class FakeLLM:
    """Scripted normalized results, in call order."""

    def __init__(self, *results, stream_chunks=None):
        self.results = list(results)
        self.calls = []
        self.stream_chunks = stream_chunks
        self.last_completion = None

    async def complete_result(self, system, user, stream_callback=None, **kw):
        self.calls.append({"system": system, "user": user, **kw})
        result = self.results.pop(0)
        if stream_callback is not None:
            for chunk in (self.stream_chunks or [result.content]):
                stream_callback(chunk)
        self.last_completion = result
        return result

    async def complete_with_tools_result(self, messages, tools, **kw):
        self.calls.append({"messages": messages, "tools": tools, **kw})
        result = self.results.pop(0)
        self.last_completion = result
        return result


def run(case, llm, ctx=None):
    return asyncio.run(case(llm, MODEL, ctx if ctx is not None else {}))


def _call(name, **arguments):
    return {"id": "1", "name": name, "arguments": arguments, "source": "native"}


# --- every case: PASS and FAIL states ---------------------------------------------------

CASES = [
    (mq.case_plain_completion, [_result("READY")], [_result("I am ready to help")]),
    (mq.case_finish_reason_stop, [_result("Hello there.")], [_result("Hello", finish_reason=None)]),
    (mq.case_structured_json, [_result('{"status": "ok", "count": 3}')],
     [_result('{"status": "ok", "count": "3"}')]),
    (mq.case_multiline_json, [_result(json.dumps({"filepath": "greet.py", "content": mq._MULTILINE_EXPECTED}))],
     [_result(json.dumps({"filepath": "greet.py", "content": mq._MULTILINE_EXPECTED.replace("\n", " ")}))]),
    (mq.case_native_tool_calls, [_result(tool_calls=[_call("get_weather", city="Paris")])],
     [_result("I cannot call tools. <tool_call>", tool_calls=[])]),
    (mq.case_multiple_tool_calls,
     [_result(tool_calls=[_call("get_weather", city="Paris"), _call("get_weather", city="Tokyo")])],
     [_result(tool_calls=[_call("get_weather", city="Paris")])]),
    (mq.case_tool_argument_integrity, [_result(tool_calls=[_call("save_note", text=mq.NOTE_TEXT)])],
     [_result(tool_calls=[_call("save_note", text=mq.NOTE_TEXT.replace("\\", ""))])]),
    (mq.case_output_truncation,
     [_result("1\n2\n3", status=CompletionStatus.OUTPUT_TRUNCATED, finish_reason="length")],
     [_result("1\n2\n3", finish_reason="stop")]),
    (mq.case_reasoning_behavior, [_result("85", reasoning_present=True, reasoning_chars=900, completion_tokens=300)],
     [_result("<think>let me see</think>85")]),
    (mq.case_full_file_raw_content,
     [_result("```python\nimport re\n\ndef slugify(text: str) -> str:\n    return re.sub(r'[^a-z0-9]+', '-', "
              "text.lower()).strip('-')\n```")],
     [_result("Here is the file: def slugify(text) return text")]),
    (mq.case_anchored_edit_protocol,
     [_result("FIX ANALYSIS: skip negatives.\nSEARCH:\n        result += p\nREPLACE:\n        if p >= 0:\n"
              "            result += p\n")],
     [_result("def total(prices):\n    return sum(p for p in prices if p >= 0)\n")]),
    (mq.case_malformed_output_recovery,
     [_result('Here you go.\n```json\n["a.py", "b.py", "c.py"]\n```')],
     [_result("Here you go: a.py, b.py and c.py")]),
    (mq.case_endpoint_error_semantics,
     [_result(status=CompletionStatus.BACKEND_ERROR, error=RuntimeError("404 model not found"))],
     [_result("hello")]),
]


@pytest.mark.parametrize(("case", "passing", "failing"), CASES, ids=[c[0].capability for c in CASES])
def test_each_case_passes_on_conforming_output_and_fails_otherwise(case, passing, failing):
    passed = run(case, FakeLLM(*passing))
    failed = run(case, FakeLLM(*failing))
    assert passed.status == mq.PASS, passed.evidence
    assert failed.status == mq.FAIL, failed.evidence
    assert passed.capability == case.capability


def test_a_truncated_answer_never_passes_a_content_case():
    truncated = _result("READY", status=CompletionStatus.OUTPUT_TRUNCATED, finish_reason="length")
    assert run(mq.case_plain_completion, FakeLLM(truncated)).status == mq.FAIL


def test_the_truncation_case_sends_a_tiny_output_budget_without_the_reasoning_floor():
    llm = FakeLLM(_result("1", status=CompletionStatus.OUTPUT_TRUNCATED, finish_reason="length"))
    run(mq.case_output_truncation, llm)
    assert llm.calls[0]["max_tokens_override"] == 16 and llm.calls[0]["reasoning_override"] is False


def test_tool_cases_are_unavailable_not_pass_when_the_profile_disables_tools():
    for case in (mq.case_native_tool_calls, mq.case_multiple_tool_calls, mq.case_tool_argument_integrity):
        result = run(case, FakeLLM(), {"native_tool_calls_enabled": False})
        assert result.status == mq.UNAVAILABLE


def test_textual_tool_calls_do_not_pass_the_native_tool_case():
    textual = _result(tool_calls=[{**_call("get_weather", city="Paris"), "source": "hermes"}])
    assert run(mq.case_native_tool_calls, FakeLLM(textual)).status == mq.FAIL


def test_streaming_assembly_compares_deltas_with_the_normalized_content():
    expected = " ".join(str(n) for n in range(1, 21))
    ok = FakeLLM(_result(expected), stream_chunks=[expected[:10], expected[10:]])
    assert run(mq.case_streaming_assembly, ok).status == mq.PASS
    lost = FakeLLM(_result(expected), stream_chunks=[expected[:10], "x"])
    assert run(mq.case_streaming_assembly, lost).status == mq.FAIL


def test_model_output_is_never_executed_by_qualification(tmp_path):
    marker = tmp_path / "pwned"
    hostile = _result(f"import os\nos.system('touch {marker}')\n\ndef slugify(text: str) -> str:\n    return text\n")
    run(mq.case_full_file_raw_content, FakeLLM(hostile))
    assert not marker.exists()


def test_timeout_case_uses_a_short_timeout_client():
    probe = FakeLLM(_result(status=CompletionStatus.TIMEOUT, error=TimeoutError("timed out")))
    made = []
    result = run(mq.case_timeout_semantics, FakeLLM(),
                 {"client_factory": lambda timeout: made.append(timeout) or probe})
    assert result.status == mq.PASS and made == [0.001]
    ok_probe = FakeLLM(_result("story"))
    assert run(mq.case_timeout_semantics, FakeLLM(), {"client_factory": lambda timeout: ok_probe}).status == mq.FAIL


def test_cancellation_case_requires_cancel_recorded_and_a_healthy_endpoint_afterwards():
    class SlowLLM(FakeLLM):
        async def complete_result(self, system, user, stream_callback=None, **kw):
            if stream_callback is None:
                return _result("READY")
            stream_callback("Once")
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                self.last_completion = _result(status=CompletionStatus.CANCELLED)
                raise
            return _result("never")

    assert run(mq.case_cancellation_semantics, SlowLLM()).status == mq.PASS


def test_endpoint_restart_is_unavailable_never_pass():
    assert run(mq.case_endpoint_restart_semantics, FakeLLM()).status == mq.UNAVAILABLE


def test_tokenizer_measurement_derives_margined_ascii_and_non_ascii_floors_from_real_usage():
    def tokens(name, text):
        ascii_part = sum(1 for ch in text if ord(ch) < 128)
        if name == "unicode":  # ASCII at 4 bytes/token, non-ASCII at 2 bytes/token
            return round(ascii_part / 4 + (len(text.encode()) - ascii_part) / 2)
        return len(text.encode()) // 4

    results = [
        _result("x", status=CompletionStatus.OUTPUT_TRUNCATED, finish_reason="length",
                prompt_tokens=tokens(name, text), tokens_estimated=False)
        for name, text in mq.TOKENIZER_CORPORA.items()
    ]
    measured = run(mq.case_tokenizer_measurement, FakeLLM(*results))
    assert measured.status == mq.PASS
    ascii_ratios = measured.evidence["ascii_bytes_per_token"]
    assert set(ascii_ratios) == set(mq.TOKENIZER_CORPORA) - {"unicode"}
    assert measured.measured["bytes_per_token_floor"] == pytest.approx(min(ascii_ratios.values()) * 0.9, rel=1e-3)
    assert measured.evidence["non_ascii_bytes_per_token"] == pytest.approx(2.0, rel=0.05)
    assert measured.measured["non_ascii_bytes_per_token_floor"] == pytest.approx(1.8, rel=0.05)


def test_tokenizer_measurement_without_reported_usage_is_unavailable():
    results = [_result("x", prompt_tokens=10, tokens_estimated=True) for _ in mq.TOKENIZER_CORPORA]
    assert run(mq.case_tokenizer_measurement, FakeLLM(*results)).status == mq.UNAVAILABLE


def test_a_crashing_case_is_a_fail_with_evidence():
    class Broken(FakeLLM):
        async def complete_result(self, *a, **k):
            raise RuntimeError("boom")

    result = run(mq.case_plain_completion, Broken())
    assert result.status == mq.FAIL and "boom" in result.evidence["error"]


# --- record, store and assessment -----------------------------------------------------------

def _record(statuses=None, fp=None):
    results = [mq.CaseResult(cap, (statuses or {}).get(cap, mq.PASS)) for cap in mq.CAPABILITIES]
    return mq.build_record(fp or _fp(), results)


def test_the_record_carries_every_binding_input():
    record = _record()
    assert record["fingerprint_digest"] == _fp().digest
    assert record["adapter_version"] and record["policy_version"] == mq.QUALIFICATION_POLICY_VERSION
    assert record["summary"] == {"PASS": len(mq.CAPABILITIES), "FAIL": 0, "UNAVAILABLE": 0}


def test_store_round_trip_and_default_location_is_outside_the_workspace(tmp_path, monkeypatch):
    path = mq.save_record(_record(), workspace_root=str(tmp_path / "ws"))
    assert os.path.realpath(path).startswith(os.path.realpath(os.environ[mq.QUALIFICATION_HOME_ENV]))
    assert mq.load_record(_fp().digest)["fingerprint_digest"] == _fp().digest
    monkeypatch.delenv(mq.QUALIFICATION_HOME_ENV)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert mq.qualification_home() == os.path.realpath(str(tmp_path / "home" / ".kriya" / "qualifications"))


def test_a_store_inside_the_workspace_is_refused(tmp_path, monkeypatch):
    workspace = tmp_path / "repo"
    monkeypatch.setenv(mq.QUALIFICATION_HOME_ENV, str(workspace / ".kriya" / "qualifications"))
    with pytest.raises(mq.QualificationPathInsideWorkspaceError):
        mq.save_record(_record(), workspace_root=str(workspace))
    assert mq.load_record(_fp().digest, workspace_root=str(workspace)) is None


def test_assessment_states():
    required = ("plain_completion", "structured_json")
    assert mq.assess(_fp(artifact_digest="unavailable"), required).status == mq.NOT_EXACT
    assert mq.assess(_fp(), required).status == mq.MISSING
    mq.save_record(_record())
    assert mq.assess(_fp(), required).status == mq.QUALIFIED
    mq.save_record(_record({"structured_json": mq.FAIL}))
    failed = mq.assess(_fp(), required)
    assert failed.status == mq.NOT_QUALIFIED and failed.failed == ("structured_json",)
    mq.save_record(_record({"structured_json": mq.UNAVAILABLE}))
    unavailable = mq.assess(_fp(), required)
    assert unavailable.status == mq.NOT_QUALIFIED and unavailable.missing == ("structured_json",)


@pytest.mark.parametrize(("field", "value"), [
    ("adapter_version", "kriya-openai-compat/0"),
    ("policy_version", "kriya-qualification/0"),
    ("fingerprint_digest", "0" * 64),
])
def test_protocol_or_policy_or_runtime_drift_makes_a_record_stale(field, value):
    record = _record()
    record[field] = value
    assessment = mq.assess(_fp(), ("plain_completion",), record=record)
    assert assessment.status == mq.STALE and assessment.reasons


def test_runtime_drift_finds_no_record_for_the_new_identity():
    mq.save_record(_record())
    assert mq.assess(_fp(artifact_digest="sha256:re-pulled"), ("plain_completion",)).status == mq.MISSING


def test_measured_limits_are_used_only_from_a_current_record():
    record = _record()
    record["measured_limits"] = {"bytes_per_token_floor": 3.1}
    mq.save_record(record)
    assert mq.measured_limits_for(_fp()) == {"bytes_per_token_floor": 3.1}
    record["adapter_version"] = "old"
    mq.save_record(record)
    assert mq.measured_limits_for(_fp()) == {}
    assert mq.measured_limits_for(_fp(provider_version="unavailable")) == {}


def test_measured_limits_come_only_from_passing_cases():
    cases = [
        mq.CaseResult("tokenizer_measurement", mq.PASS, measured={"bytes_per_token_floor": 3.0}),
        mq.CaseResult("reasoning_behavior", mq.PASS, measured={"reasoning_tokens_observed": 1000}),
        mq.CaseResult("tool_argument_integrity", mq.FAIL, measured={"verified_tool_argument_chars": 99}),
    ]
    assert mq.measured_limits(cases) == {"bytes_per_token_floor": 3.0, "reasoning_tokens_max": 1500}


def test_required_capabilities_follow_the_protocols_kriya_uses():
    cfg = AppConfig()
    cfg.llm.model = MODEL
    cfg.llm.capabilities.native_tool_calls = True
    cfg.llm.capabilities.json_mode = True
    cfg.llm.capabilities.reliable_multiline_json = False
    cfg.llm.capabilities.streaming = True
    developer = mq.required_capabilities(cfg, "developer", MODEL)
    assert {"full_file_raw_content", "anchored_edit_protocol", "native_tool_calls", "structured_json",
            "streaming_assembly", "output_truncation"} <= set(developer)
    assert "multiline_json" not in developer
    reviewer = mq.required_capabilities(cfg, "reviewer", MODEL)
    assert "full_file_raw_content" not in reviewer and "plain_completion" in reviewer
    cfg.llm.capabilities.native_tool_calls = False
    assert "native_tool_calls" not in mq.required_capabilities(cfg, "developer", MODEL)
    assert not set(mq.required_capabilities(cfg, "developer", MODEL)) - set(mq.CAPABILITIES)


def test_role_models_include_every_escalation_model():
    cfg = AppConfig()
    roles = mq.role_models(cfg)
    assert roles["developer"][0] == cfg.llm.model
    assert [c.model for c in cfg.llm_chain] == roles["developer"][1:]
    assert set(roles) == set(mq.ROLES)


def test_run_qualification_refuses_a_runtime_that_is_not_exact():
    with pytest.raises(mq.QualificationError, match=mq.NOT_EXACT):
        asyncio.run(mq.run_qualification(AppConfig(), MODEL, llm=FakeLLM(),
                                         fingerprint=_fp(artifact_digest="unavailable")))


def test_run_qualification_runs_selected_cases_and_binds_the_fingerprint():
    record = asyncio.run(mq.run_qualification(
        AppConfig(), MODEL, llm=FakeLLM(_result("READY")), fingerprint=_fp(), only=["plain_completion"],
    ))
    assert [c["capability"] for c in record["cases"]] == ["plain_completion"]
    assert record["cases"][0]["status"] == mq.PASS
    assert record["fingerprint_digest"] == _fp().digest


def test_cli_qualify_saves_a_full_record_and_writes_the_handover_report(tmp_path, monkeypatch):
    from kriya.cli import main

    async def fake_run(cfg, model, **kwargs):
        return _record()

    monkeypatch.setattr(mq, "run_qualification", fake_run)
    out = tmp_path / "report.json"
    result = CliRunner().invoke(main, ["model", "qualify", "--json", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text())["fingerprint_digest"] == _fp().digest
    assert mq.load_record(_fp().digest) is not None


def test_cli_qualify_reports_a_non_exact_runtime_and_exits_nonzero(monkeypatch):
    from kriya.cli import main

    async def refuse(cfg, model, **kwargs):
        raise mq.QualificationError(f"{mq.NOT_EXACT}: no metadata")

    monkeypatch.setattr(mq, "run_qualification", refuse)
    result = CliRunner().invoke(main, ["model", "qualify"])
    assert result.exit_code == 1 and mq.NOT_EXACT in result.output


def test_cli_status_is_nonzero_until_every_role_is_qualified(monkeypatch):
    from kriya.cli import main
    from kriya.core import model_runtime

    monkeypatch.setattr(model_runtime, "resolve_configured_model_runtime",
                        lambda cfg, model=None, **kw: _fp(alias=model or cfg.llm.model,
                                                          artifact_digest="sha256:never-qualified"))
    result = CliRunner().invoke(main, ["model", "status", "--json"])
    assert result.exit_code == 1
    report = json.loads(result.output[result.output.index("{"):])
    assert report["developer"][0]["status"] == mq.MISSING


def test_the_doctor_and_qualify_fingerprint_the_same_runtime_identically(monkeypatch):
    """One shared fake endpoint: the digest `kriya model qualify` records is the
    digest `doctor --production` computes, so a qualified deployment can pass."""
    from kriya.core import model_runtime
    from kriya.production_doctor import probe_llm_runtime

    cfg = AppConfig()
    cfg.llm.model = MODEL
    cfg.llm.extra_body = {"options": {"num_ctx": 16384}}
    responses = {
        "/v1/models": {"data": [{"id": MODEL, "created": 1}]},
        "/api/version": {"version": "0.34.2"},
        "/api/tags": {"models": [{"name": MODEL, "digest": "abc"}]},
        "/api/show": {"details": {"format": "gguf", "quantization_level": "Q4_K_M"},
                      "modelfile": "FROM /b/sha256-99\nRENDERER r\nPARSER p\n",
                      "model_info": {"general.architecture": "a", "a.context_length": 32768}},
    }

    def serve(url):
        return responses["/" + url.split("/", 3)[3]]

    monkeypatch.setenv(model_runtime.PROBE_ENV_VAR, "1")
    monkeypatch.setattr(model_runtime, "_http_json", lambda url, payload, api_key: serve(url))
    monkeypatch.setattr("kriya.production_doctor._json_request", lambda url, **kwargs: serve(url))

    doctor = probe_llm_runtime(cfg)["runtime"]
    record = asyncio.run(mq.run_qualification(cfg, llm=FakeLLM(_result("READY")), only=["plain_completion"]))
    assert doctor.exact and record["fingerprint_digest"] == doctor.digest
    assert doctor.effective_context_window == 16384
