"""QUAL-CONFIG-001: model qualification policy is configuration.

Mechanics stay in kriya/core/model_qualification.py; the per-case budgets,
the capacity probe's bounds, the cancellation timing bounds and the
measurement margins come from ``model_qualification`` (kriya/config/config.py).
Defaults reproduce the kriya-qualification/3 literals exactly, and a record
is bound to the effective policy: a record made under another policy is
STALE."""
import ast
import asyncio
import os
import pathlib

import pytest
from click.testing import CliRunner
from pydantic import ValidationError
from test_prd014_model_qualification import MODEL, FakeLLM, _call, _fp, _result

from kriya.config import AppConfig
from kriya.config.authority import FieldClassification, classify_field, is_known_field
from kriya.config.config import ModelQualificationConfig
from kriya.core import model_qualification as mq
from kriya.core.completion import CompletionStatus
from kriya.core.inference_runtime import ChatResponse
from kriya.core.inference_settings import InferenceSettings

# The kriya-qualification/3 budgets as they were hard-coded (git: unchanged
# from the /3 bump at 6c163f2 until this change). Written out, never derived
# from the defaults, so a later default change cannot move the legacy
# digest with it.
V3_LITERAL_BUDGETS = {
    "plain_completion": 64, "finish_reason_stop": 256, "structured_json": 256, "multiline_json": 512,
    "native_tool_calls": 256, "multiple_tool_calls": 512, "tool_argument_integrity": 512,
    "streaming_assembly": 256, "reasoning_behavior": 2048, "full_file_raw_content": 1024,
    "anchored_edit_protocol": 1024, "malformed_output_recovery": 512, "cancellation_semantics": 1024,
    "context_capacity": 64,
}
V3_LITERAL_POLICY = {
    "cases": {
        **{case: {"max_tokens": tokens} for case, tokens in V3_LITERAL_BUDGETS.items()},
        "cancellation_semantics": {"max_tokens": 1024, "health_check_max_tokens": 64,
                                   "first_delta_timeout_seconds": 120.0, "max_settle_seconds": 10.0},
        "context_capacity": {"max_tokens": 64, "headroom_tokens": 384, "min_fill_ratio": 0.9,
                             "request_timeout_seconds": 600.0},
    },
    "measurement": {"bytes_per_token_margin": 0.9, "reasoning_tokens_headroom": 1.5},
}

# One passing scripted answer per budgeted case (tests/test_prd014_model_qualification.py's shapes).
_ANSWERS = {
    "plain_completion": (mq.case_plain_completion, [_result("READY")]),
    "finish_reason_stop": (mq.case_finish_reason_stop, [_result("Hello there.")]),
    "structured_json": (mq.case_structured_json, [_result('{"status": "ok", "count": 3}')]),
    "multiline_json": (mq.case_multiline_json, [_result(
        '{"filepath": "greet.py", "content": ' + __import__("json").dumps(mq._MULTILINE_EXPECTED) + "}")]),
    "native_tool_calls": (mq.case_native_tool_calls, [_result(tool_calls=[_call("get_weather", city="Paris")])]),
    "multiple_tool_calls": (mq.case_multiple_tool_calls, [_result(tool_calls=[
        _call("get_weather", city="Paris"), _call("get_weather", city="Tokyo")])]),
    "tool_argument_integrity": (mq.case_tool_argument_integrity,
                                [_result(tool_calls=[_call("save_note", text=mq.NOTE_TEXT)])]),
    "reasoning_behavior": (mq.case_reasoning_behavior, [_result("85")]),
    "full_file_raw_content": (mq.case_full_file_raw_content, [_result(
        "import re\n\ndef slugify(text: str) -> str:\n    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')\n")]),
    "anchored_edit_protocol": (mq.case_anchored_edit_protocol, [_result(
        "FIX ANALYSIS: skip negatives.\nSEARCH:\n        result += p\nREPLACE:\n        if p >= 0:\n"
        "            result += p\n")]),
    "malformed_output_recovery": (mq.case_malformed_output_recovery,
                                  [_result('Here.\n```json\n["a.py", "b.py", "c.py"]\n```')]),
}


def _sent_budget(capability, ctx=None):
    case, answers = _ANSWERS[capability]
    llm = FakeLLM(*answers)
    result = asyncio.run(case(llm, MODEL, ctx if ctx is not None else {}))
    assert result.status == mq.PASS, result.evidence
    return llm.calls[0]["max_tokens_override"], result


def _policy(**cases):
    return ModelQualificationConfig.model_validate({"cases": cases})


def _settings(reasoning=False):
    return InferenceSettings(temperature=0.7, reasoning=reasoning)


# --- 1. defaults reproduce the existing budgets -------------------------------------------

@pytest.mark.parametrize("capability", sorted(_ANSWERS))
def test_the_default_policy_sends_every_case_its_v3_budget(capability):
    sent, _ = _sent_budget(capability)
    assert sent == V3_LITERAL_BUDGETS[capability]


def test_the_default_policy_is_exactly_the_v3_literals():
    assert ModelQualificationConfig().model_dump(mode="json") == ModelQualificationConfig.model_validate(
        V3_LITERAL_POLICY).model_dump(mode="json")


def test_the_legacy_digest_is_the_digest_of_the_v3_literals_not_of_the_current_defaults():
    literal = ModelQualificationConfig.model_validate(V3_LITERAL_POLICY)
    assert mq.LEGACY_V3_POLICY_DIGEST == mq.qualification_policy_digest(literal)


# --- 2./3./9. configured and capability-aware budgets are what is sent -----------------------

def test_a_configured_case_budget_is_sent_and_reported_with_its_source():
    policy = _policy(tool_argument_integrity={"max_tokens": 1234})
    sent, result = _sent_budget("tool_argument_integrity", {"policy": policy})
    assert sent == 1234
    assert result.evidence["policy"] == {
        "max_tokens": 1234, "source": "model_qualification.cases.tool_argument_integrity.max_tokens"}


def test_the_reasoning_budget_applies_only_to_a_reasoning_identity():
    policy = _policy(tool_argument_integrity={"max_tokens": 512, "reasoning_max_tokens": 4096})
    reasoning, evidence = _sent_budget("tool_argument_integrity", {"policy": policy, "reasoning": True})
    plain, _ = _sent_budget("tool_argument_integrity", {"policy": policy, "reasoning": False})
    assert (reasoning, plain) == (4096, 512)
    assert evidence.evidence["policy"]["source"].endswith("tool_argument_integrity.reasoning_max_tokens")


def test_a_reasoning_identity_without_a_reasoning_budget_keeps_the_case_budget():
    sent, _ = _sent_budget("tool_argument_integrity", {"policy": ModelQualificationConfig(), "reasoning": True})
    assert sent == 512


def test_run_qualification_takes_the_reasoning_flag_from_the_identity_qualified():
    cfg = AppConfig(model_qualification={"cases": {"tool_argument_integrity": {"max_tokens": 512,
                                                                               "reasoning_max_tokens": 3000}}})

    def budget(settings):
        llm = FakeLLM(_result(tool_calls=[_call("save_note", text=mq.NOTE_TEXT)]))
        record = asyncio.run(mq.run_qualification(cfg, MODEL, llm=llm, fingerprint=_fp(),
                                                  only=["tool_argument_integrity"], settings=settings))
        return llm.calls[0]["max_tokens_override"], record

    assert budget(_settings(reasoning=True))[0] == 3000
    sent, record = budget(_settings(reasoning=False))
    assert sent == 512
    assert record["cases"][0]["evidence"]["policy"]["max_tokens"] == 512
    assert record["qualification_policy"] == cfg.model_qualification.model_dump(mode="json")
    assert record[mq.POLICY_DIGEST_FIELD] == mq.policy_digest_for(cfg)


def test_more_budget_never_passes_a_malformed_tool_argument():
    """Budget is not the assertion: a wrong argument fails at any budget,
    and truncation is its own status, never reported as a wrong argument."""
    policy = _policy(tool_argument_integrity={"max_tokens": 512, "reasoning_max_tokens": 8192})
    wrong = FakeLLM(_result(tool_calls=[_call("save_note", text=mq.NOTE_TEXT.replace("Line one ", ""))]))
    result = asyncio.run(mq.case_tool_argument_integrity(wrong, MODEL, {"policy": policy, "reasoning": True}))
    assert result.status == mq.FAIL and wrong.calls[0]["max_tokens_override"] == 8192
    truncated = FakeLLM(_result(status=CompletionStatus.OUTPUT_TRUNCATED, finish_reason="length"))
    cut = asyncio.run(mq.case_tool_argument_integrity(truncated, MODEL, {"policy": policy}))
    assert cut.status == mq.FAIL
    assert cut.evidence["status"] == "OUTPUT_TRUNCATED" and cut.evidence["received"] == []


# --- 4./5. validation and defaults ------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    {"cases": {"tool_argument_integrity": {"max_tokens": 0}}},
    {"cases": {"tool_argument_integrity": {"max_tokens": -1}}},
    {"cases": {"tool_argument_integrity": {"max_tokens": 512, "reasoning_max_tokens": 0}}},
    {"cases": {"context_capacity": {"min_fill_ratio": 0}}},
    {"cases": {"context_capacity": {"min_fill_ratio": 1.5}}},
    {"cases": {"context_capacity": {"headroom_tokens": 0}}},
    {"cases": {"cancellation_semantics": {"max_settle_seconds": 0}}},
    {"measurement": {"bytes_per_token_margin": 0}},
    {"measurement": {"bytes_per_token_margin": 1.2}},
    {"measurement": {"reasoning_tokens_headroom": 0.5}},
    {"cases": {"output_truncation": {"max_tokens": 16}}},
    {"cases": {"tool_argument_integrity": {"max_tokens": 512, "maxtokens": 1}}},
    {"sample_count": 3},
])
def test_invalid_qualification_policy_is_rejected(bad):
    with pytest.raises(ValidationError):
        AppConfig(model_qualification=bad)


def test_a_partial_policy_keeps_every_other_default():
    cfg = AppConfig(model_qualification={"cases": {"tool_argument_integrity": {"reasoning_max_tokens": 4096}}})
    cases = cfg.model_qualification.cases
    assert cases.tool_argument_integrity.max_tokens == 512
    assert cases.reasoning_behavior.max_tokens == 2048 and cases.context_capacity.headroom_tokens == 384
    assert AppConfig().model_qualification == ModelQualificationConfig()


def test_an_unapproved_config_cannot_set_the_qualification_policy(tmp_path):
    """load_config denies model_qualification from any non-trusted source
    until it is explicitly approved (SEC-009)."""
    from kriya.config.authority import ConfigAuthorityError
    from kriya.config.config import load_config

    path = tmp_path / "kriya.yaml"
    path.write_text("model_qualification:\n  cases:\n    tool_argument_integrity:\n      max_tokens: 900\n")
    with pytest.raises(ConfigAuthorityError, match="model_qualification"):
        load_config(config_path=str(path))


def test_a_partial_case_entry_keeps_that_cases_other_defaults():
    cases = AppConfig(model_qualification={"cases": {"context_capacity": {"max_tokens": 900}}}).model_qualification.cases
    assert cases.context_capacity.max_tokens == 900
    assert (cases.context_capacity.headroom_tokens, cases.context_capacity.min_fill_ratio) == (384, 0.9)
    assert cases.cancellation_semantics.max_tokens == 1024


# --- 6./7./8. identity binding -----------------------------------------------------------------

def test_the_policy_digest_follows_the_policy_and_nothing_else():
    base = AppConfig()
    changed = AppConfig(model_qualification={"cases": {"tool_argument_integrity": {"max_tokens": 4096}}})
    assert mq.policy_digest_for(base) != mq.policy_digest_for(changed)
    unrelated = AppConfig()
    unrelated.llm.temperature = 0.1
    unrelated.autonomy.mode = "human-in-the-loop"
    unrelated.static_analysis.enabled = True
    assert mq.policy_digest_for(unrelated) == mq.policy_digest_for(base)


def _record_under(policy):
    return mq.build_record(_fp(), [mq.CaseResult("plain_completion", mq.PASS)], settings=_settings(),
                           policy=policy)


def test_a_record_made_under_another_policy_is_stale():
    record = _record_under(ModelQualificationConfig())
    changed = ModelQualificationConfig.model_validate({"cases": {"tool_argument_integrity": {"max_tokens": 4096}}})
    current = mq.assess(_fp(), ("plain_completion",), settings=_settings(), record=record,
                        policy_digest=mq.qualification_policy_digest(ModelQualificationConfig()))
    stale = mq.assess(_fp(), ("plain_completion",), settings=_settings(), record=record,
                      policy_digest=mq.qualification_policy_digest(changed))
    assert current.status == mq.QUALIFIED
    assert stale.status == mq.STALE and any("qualification policy" in r for r in stale.reasons)


def test_a_record_made_under_a_non_default_policy_is_stale_under_the_defaults():
    changed = ModelQualificationConfig.model_validate({"cases": {"tool_argument_integrity": {"max_tokens": 4096}}})
    record = _record_under(changed)
    assert mq.assess(_fp(), ("plain_completion",), settings=_settings(), record=record).status == mq.STALE
    assert mq.assess(_fp(), ("plain_completion",), settings=_settings(), record=record,
                     policy_digest=mq.qualification_policy_digest(changed)).status == mq.QUALIFIED


def test_a_pre_qual_config_record_counts_only_under_the_v3_policy():
    record = _record_under(ModelQualificationConfig())
    record.pop(mq.POLICY_DIGEST_FIELD)
    record.pop("qualification_policy")
    changed = ModelQualificationConfig.model_validate({"cases": {"reasoning_behavior": {"max_tokens": 4096}}})
    assert mq.assess(_fp(), ("plain_completion",), settings=_settings(), record=record,
                     policy_digest=mq.LEGACY_V3_POLICY_DIGEST).status == mq.QUALIFIED
    assert mq.assess(_fp(), ("plain_completion",), settings=_settings(), record=record,
                     policy_digest=mq.qualification_policy_digest(changed)).status == mq.STALE


def test_measured_limits_come_only_from_a_record_under_the_configured_policy(monkeypatch):
    record = mq.build_record(_fp(), [mq.CaseResult("tokenizer_measurement", mq.PASS,
                                                   measured={"bytes_per_token_floor": 3.0})],
                             settings=_settings(), policy=ModelQualificationConfig())
    mq.save_record(record)
    changed = AppConfig(model_qualification={"measurement": {"bytes_per_token_margin": 0.5}})
    assert mq.measured_limits_for(_fp(), AppConfig(), settings=_settings())["bytes_per_token_floor"] == 3.0
    assert "bytes_per_token_floor" not in mq.measured_limits_for(_fp(), changed, settings=_settings())


def test_environment_evidence_is_not_merged_across_policies():
    first = _record_under(ModelQualificationConfig())
    first["environment_evidence"] = {"sha256:env-a": {"cases": []}}
    mq.save_record(first)
    other = _record_under(ModelQualificationConfig.model_validate(
        {"cases": {"plain_completion": {"max_tokens": 128}}}))
    other["environment_evidence"] = {"sha256:env-b": {"cases": []}}
    path = mq.save_record(other)
    import json

    with open(path, encoding="utf-8") as stream:
        saved = json.load(stream)
    assert set(saved["environment_evidence"]) == {"sha256:env-b"}


# --- policy-driven bounds beyond max_tokens ----------------------------------------------------

class _CapacityRuntime:
    def __init__(self, answer):
        self.requests, self.answer = [], answer

    async def complete(self, client, request):
        self.requests.append(request)
        text = "".join(m["content"] for m in request.messages)
        tokens = len(text) // 4
        if request.max_tokens == 1:
            return ChatResponse(content="", prompt_tokens=tokens, finish_reason="length")
        return ChatResponse(content=self.answer(request), prompt_tokens=tokens, finish_reason="stop")


def _codes(request):
    first = request.messages[0]["content"].split("first code is ")[1].split(".")[0]
    second = request.messages[1]["content"].split("second code is ")[1].split(".")[0]
    return f"{first} {second}"


def test_the_capacity_probe_uses_the_configured_answer_budget_headroom_and_fill_ratio():
    runtime = _CapacityRuntime(_codes)
    made = []

    class _Client:
        client = object()

    policy = _policy(context_capacity={"max_tokens": 700, "headroom_tokens": 2000, "min_fill_ratio": 0.5,
                                       "request_timeout_seconds": 42})
    ctx = {"policy": policy, "context_window": 8192, "runtime": runtime,
           "client_factory": lambda timeout: made.append(timeout) or _Client()}
    result = asyncio.run(mq.case_context_capacity(FakeLLM(), MODEL, ctx))
    assert result.status == mq.PASS, result.evidence
    assert runtime.requests[-1].max_tokens == 700 and made == [42]
    assert result.evidence["target_prompt_tokens"] == 8192 - 2000
    assert result.evidence["policy"] == {"max_tokens": 700, "source": "model_qualification.cases.context_capacity.max_tokens",
                                         "headroom_tokens": 2000, "min_fill_ratio": 0.5,
                                         "request_timeout_seconds": 42}


def test_the_capacity_fill_ratio_decides_the_verdict():
    """The same short prompt passes a lenient ratio and fails the default."""
    class _Short(_CapacityRuntime):
        async def complete(self, client, request):
            response = await super().complete(client, request)
            if request.max_tokens != 1:
                response.prompt_tokens = int(response.prompt_tokens * 0.6)
            return response

    def run(policy):
        ctx = {"policy": policy, "context_window": 8192, "runtime": _Short(_codes),
               "client_factory": lambda timeout: type("C", (), {"client": object()})()}
        return asyncio.run(mq.case_context_capacity(FakeLLM(), MODEL, ctx)).status

    assert run(_policy(context_capacity={"min_fill_ratio": 0.5})) == mq.PASS
    assert run(ModelQualificationConfig()) == mq.FAIL


def test_cancellation_waits_only_the_configured_first_delta_timeout():
    class _Silent(FakeLLM):
        async def complete_result(self, system, user, stream_callback=None, **kw):
            self.calls.append(kw)
            await asyncio.sleep(30)

    policy = _policy(cancellation_semantics={"first_delta_timeout_seconds": 0.05})
    llm = _Silent()
    result = asyncio.run(asyncio.wait_for(
        mq.case_cancellation_semantics(llm, MODEL, {"policy": policy}), timeout=5))
    assert result.status == mq.FAIL and result.evidence["reason"] == "no streamed output before cancelling"
    assert result.evidence["policy"]["first_delta_timeout_seconds"] == 0.05


def test_measurement_margins_come_from_policy():
    cases = [mq.CaseResult("reasoning_behavior", mq.PASS, measured={"reasoning_tokens_observed": 100})]
    policy = ModelQualificationConfig.model_validate({"measurement": {"reasoning_tokens_headroom": 3}})
    assert mq.measured_limits(cases)["reasoning_tokens_max"] == 150
    assert mq.measured_limits(cases, policy)["reasoning_tokens_max"] == 300

    def usage():
        return FakeLLM(*[_result("x", status=CompletionStatus.OUTPUT_TRUNCATED, finish_reason="length",
                                 prompt_tokens=100, tokens_estimated=False) for _ in mq.TOKENIZER_CORPORA])

    margin = _policy()
    margin.measurement.bytes_per_token_margin = 0.5
    measured = asyncio.run(mq.case_tokenizer_measurement(usage(), MODEL, {"policy": margin}))
    default = asyncio.run(mq.case_tokenizer_measurement(usage(), MODEL, {})).measured
    assert measured.status == mq.PASS
    assert measured.measured["bytes_per_token_floor"] == pytest.approx(
        default["bytes_per_token_floor"] / 0.9 * 0.5, rel=1e-3)
    assert measured.evidence["policy"]["bytes_per_token_margin"] == 0.5


def test_budgets_that_are_the_test_itself_are_not_policy():
    """output_truncation's tiny budget is what it tests; it is not configurable."""
    llm = FakeLLM(_result("1", status=CompletionStatus.OUTPUT_TRUNCATED, finish_reason="length"))
    asyncio.run(mq.case_output_truncation(llm, MODEL, {"policy": ModelQualificationConfig()}))
    assert llm.calls[0]["max_tokens_override"] == 16
    assert "output_truncation" not in type(ModelQualificationConfig().cases).model_fields


# --- 10. production evidence --------------------------------------------------------------------

def test_the_doctor_reports_the_policy_every_record_was_checked_against(tmp_path):
    from test_production_doctor import _checks, _production_cfg, _qualify_all_roles, _run

    cfg = _production_cfg(tmp_path)
    _qualify_all_roles(cfg)
    check = _checks(_run(tmp_path, cfg=cfg))["model.qualification"]
    assert check.evidence["qualification_policy_digest"] == mq.policy_digest_for(cfg)
    assert check.evidence["qualification_policy"]["cases"]["tool_argument_integrity"]["max_tokens"] == 512
    cfg.model_qualification.cases.tool_argument_integrity.max_tokens = 4096
    changed = _checks(_run(tmp_path, cfg=cfg))["model.qualification"]
    assert changed.evidence["roles"]["developer"][0]["status"] == mq.STALE
    assert changed.evidence["qualification_policy"]["cases"]["tool_argument_integrity"]["max_tokens"] == 4096


def test_cli_qualify_names_the_controlling_policy_of_a_failed_case(monkeypatch):
    from kriya.cli import main

    async def fake_run(cfg, model, progress=None, **kwargs):
        failed = mq.CaseResult("tool_argument_integrity", mq.FAIL, {
            "policy": {"max_tokens": 512, "source": "model_qualification.cases.tool_argument_integrity.max_tokens"},
            "max_tokens": 512, "finish_reason": "length"})
        progress(failed)
        return mq.build_record(_fp(), [failed], settings=_settings())

    monkeypatch.setattr(mq, "run_qualification", fake_run)
    result = CliRunner().invoke(main, ["model", "qualify"])
    assert "max_tokens=512 (model_qualification.cases.tool_argument_integrity.max_tokens)" in result.output
    assert "finish_reason=length" in result.output
    assert f"Qualification policy: {mq.qualification_policy_digest(ModelQualificationConfig())}" in result.output


# --- authority and the production call sites ---------------------------------------------------

def test_a_repository_can_never_change_the_qualification_policy():
    for leaf in ("max_tokens", "reasoning_max_tokens", "min_fill_ratio", "bytes_per_token_margin"):
        assert classify_field("model_qualification", leaf) is FieldClassification.SECURITY_AUTHORITY
        assert is_known_field("model_qualification", leaf)


_KRIYA = pathlib.Path(__file__).resolve().parents[1] / "kriya"


def _production_calls(name):
    for path in sorted(_KRIYA.rglob("*.py")):
        if path.name == "model_qualification.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == name:
                yield f"{path.relative_to(_KRIYA.parent)}:{node.lineno}", node


def test_every_production_assessment_passes_the_configured_policy():
    """assess() defaults to the default policy; a production caller that
    omitted policy_digest would accept records made under another one."""
    calls = list(_production_calls("assess"))
    assert len(calls) >= 8
    missing = [where for where, node in calls if "policy_digest" not in {kw.arg for kw in node.keywords}]
    assert missing == []


def test_every_production_measured_limits_lookup_passes_its_config():
    calls = list(_production_calls("measured_limits_for"))
    assert calls
    for where, node in calls:
        config = node.args[1] if len(node.args) > 1 else next(
            (kw.value for kw in node.keywords if kw.arg == "config"), None)
        assert config is not None and not (isinstance(config, ast.Constant) and config.value is None), where


def test_the_qualification_home_used_by_these_tests_is_isolated():
    assert os.environ.get(mq.QUALIFICATION_HOME_ENV)


def test_cli_qualify_reports_the_legacy_policy_of_a_record_without_a_digest(monkeypatch, tmp_path):
    """A record without a policy digest is a legacy /3 record; the CLI reports
    that policy instead of failing on the missing field."""
    from kriya.cli import main

    async def legacy_run(cfg, model, **kwargs):
        record = mq.build_record(_fp(), [mq.CaseResult("plain_completion", mq.PASS)], settings=_settings())
        record.pop(mq.POLICY_DIGEST_FIELD)
        return record

    monkeypatch.setattr(mq, "run_qualification", legacy_run)
    monkeypatch.setattr(mq, "save_record", lambda record, workspace_root=None: str(tmp_path / "r.json"))
    result = CliRunner().invoke(main, ["model", "qualify"])
    assert result.exit_code == 0, result.output
    assert f"Qualification policy: {mq.LEGACY_V3_POLICY_DIGEST}" in result.output


def _literal_leaves(tree, prefix=()):
    for key, value in tree.items():
        if isinstance(value, dict):
            yield from _literal_leaves(value, prefix + (key,))
        else:
            yield prefix + (key,), value


@pytest.mark.parametrize("path", [path for path, _ in _literal_leaves(V3_LITERAL_POLICY)],
                         ids=lambda path: ".".join(path))
def test_changing_any_historical_value_makes_a_legacy_record_stale(path):
    """A record without a policy digest counts only under exactly the /3
    values: every single one of them, changed alone, makes it STALE."""
    import copy

    record = _record_under(ModelQualificationConfig())
    record.pop(mq.POLICY_DIGEST_FIELD)
    changed = copy.deepcopy(V3_LITERAL_POLICY)
    node = changed
    for key in path[:-1]:
        node = node[key]
    value = node[path[-1]]
    node[path[-1]] = (value * 0.5 if isinstance(value, float) and value <= 1 else value + 1)
    policy = ModelQualificationConfig.model_validate(changed)
    assessment = mq.assess(_fp(), ("plain_completion",), settings=_settings(), record=record,
                           policy_digest=mq.qualification_policy_digest(policy))
    assert assessment.status == mq.STALE, path
    assert mq.assess(_fp(), ("plain_completion",), settings=_settings(), record=record,
                     policy_digest=mq.LEGACY_V3_POLICY_DIGEST).status == mq.QUALIFIED


def test_a_reasoning_budget_added_to_the_historical_values_makes_a_legacy_record_stale():
    record = _record_under(ModelQualificationConfig())
    record.pop(mq.POLICY_DIGEST_FIELD)
    policy = ModelQualificationConfig.model_validate(
        {"cases": {"tool_argument_integrity": {"reasoning_max_tokens": 4096}}})
    assert mq.assess(_fp(), ("plain_completion",), settings=_settings(), record=record,
                     policy_digest=mq.qualification_policy_digest(policy)).status == mq.STALE
