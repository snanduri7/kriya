"""W4: the ``structured_redirect`` qualification case (kriya-qualification/9).

kriya_sentinel_v2 (W3-4) added the explicit REDIRECT line, and no earlier case measured whether an exact runtime can emit it. The case is INFORMATIONAL:
a model that does not demonstrate it stays as qualified as before (the deterministic locator and INVALID_EDIT_PROTOCOL remain the safety behaviour), and the
record states honestly that the capability was not demonstrated. Live evidence is the Wave-4 qualification run; these tests are the deterministic equivalent."""
import json

import pytest
from test_prd014_model_qualification import MODEL, FakeLLM, _fp, _record, _result, _settings, run

from kriya.agents.response_protocol import LEGACY_STRICT, STRUCTURED
from kriya.config import AppConfig
from kriya.config.config import ModelQualificationConfig, QualificationCasesConfig
from kriya.core import model_qualification as mq
from kriya.core.completion import CompletionStatus

STRUCTURED_CTX = {"response_protocol": STRUCTURED}
TARGET, OTHER = "src/report.py", "src/pricing.py"

REDIRECTED = ("The traceback shows apply_discount is wrong, not render.\n"
              f'<<<KRIYA:REDIRECT to="{OTHER}">>>\n<<<KRIYA:NO_CHANGE path="{TARGET}">>>\n')
EDITED = ("render formats the total as an integer.\n"
          f'<<<KRIYA:EDIT path="{TARGET}">>>\n<<<KRIYA:SEARCH>>>\n    return "Total: %d" % total\n<<<KRIYA:REPLACE>>>\n'
          '    return "Total: %.2f" % total\n<<<KRIYA:END_EDIT>>>\n')


def _run(first, second, ctx=None):
    llm = FakeLLM(first if not isinstance(first, str) else _result(first), second if not isinstance(second, str) else _result(second))
    return run(mq.case_structured_redirect, llm, STRUCTURED_CTX if ctx is None else ctx), llm


def test_a_runtime_that_redirects_when_the_defect_is_elsewhere_and_stays_put_otherwise_passes():
    result, llm = _run(REDIRECTED, EDITED)
    assert result.status == mq.PASS, result.evidence
    probes = result.evidence["probes"]
    assert (probes["elsewhere"]["redirect_to"], probes["elsewhere"]["redirect_from"]) == (OTHER, TARGET)
    assert probes["here"]["redirect_to"] is None and probes["here"]["outcome_kind"] == "edits"
    assert len(llm.calls) == 2


@pytest.mark.parametrize(("name", "first", "second"), [
    # naming the file in the analysis is evidence, never a redirect (the parser reads the line only)
    ("prose_only", f"The defect is in {OTHER} apply_discount.\n<<<KRIYA:NO_CHANGE path=\"{TARGET}\">>>\n", EDITED),
    ("malformed_line", f"Wrong file.\n<<<KRIYA:REDIRECT to={OTHER}>>>\n<<<KRIYA:NO_CHANGE path=\"{TARGET}\">>>\n", EDITED),
    ("wrong_target", REDIRECTED.replace(OTHER, "src/other.py"), EDITED),
    ("after_the_outcome", f"Wrong file.\n<<<KRIYA:NO_CHANGE path=\"{TARGET}\">>>\n<<<KRIYA:REDIRECT to=\"{OTHER}\">>>\n", EDITED),
    ("two_lines", REDIRECTED.replace("<<<KRIYA:NO_CHANGE", f'<<<KRIYA:REDIRECT to="{OTHER}">>>\n<<<KRIYA:NO_CHANGE'), EDITED),
    # restraint: redirecting when the defect IS in the repaired file is a failure, not extra credit
    ("spurious_redirect", REDIRECTED, REDIRECTED.replace("apply_discount is wrong, not render", "render is fine")),
    ("second_probe_invalid", REDIRECTED, "def render(total):\n    return 'x'\n"),
    ("first_probe_invalid", "no protocol at all", EDITED),
])
def test_every_way_of_not_demonstrating_the_capability_fails(name, first, second):
    result, _ = _run(first, second)
    assert result.status == mq.FAIL, (name, result.evidence)


def test_a_truncated_answer_never_passes_even_when_its_text_conforms():
    truncated = _result(REDIRECTED, status=CompletionStatus.OUTPUT_TRUNCATED, finish_reason="length")
    result, _ = _run(truncated, EDITED)
    assert result.status == mq.FAIL and result.evidence["probes"]["elsewhere"]["status"] != "OK"


@pytest.mark.parametrize("ctx", [{"response_protocol": LEGACY_STRICT}, {}], ids=["legacy_protocol", "unset"])
def test_a_protocol_without_a_redirect_marker_is_unavailable_never_pass(ctx):
    llm = FakeLLM()
    result = run(mq.case_structured_redirect, llm, ctx)
    assert result.status == mq.UNAVAILABLE and llm.calls == []     # nothing was asked of the model


def test_the_two_probes_are_different_scenarios_and_the_second_does_not_mention_the_other_file():
    _, llm = _run(REDIRECTED, EDITED)
    elsewhere, here = (call["user"] for call in llm.calls)
    assert f"{OTHER}:3: in apply_discount" in elsewhere and "tests/test_pricing.py" in elsewhere
    assert "tests/test_report.py::test_render" in here and OTHER not in here     # the restraint probe offers nothing to redirect to


def test_the_case_sends_the_real_contract_with_the_redirect_instruction_and_the_default_budget():
    _, llm = _run(REDIRECTED, EDITED)
    for call in llm.calls:
        assert "<<<KRIYA:REDIRECT" in call["system"] and "ONLY IF the real defect is in a DIFFERENT file" in call["system"]
        assert call["max_tokens_override"] == 1024


# ------------------------------------------------------------------------------------------------- informational: no role requires it

def test_no_role_requires_the_capability_so_a_model_that_fails_it_stays_qualified():
    assert "structured_redirect" in mq.CAPABILITIES
    cfg = AppConfig()
    for role in mq.ROLES:
        assert "structured_redirect" not in mq.required_capabilities(cfg, role, MODEL), role
    required = mq.required_capabilities(cfg, "developer", MODEL)
    mq.save_record(_record({"structured_redirect": mq.FAIL}))
    assessment = mq.assess(_fp(), required, settings=_settings())
    assert assessment.status == mq.QUALIFIED, assessment.reasons


def test_a_record_states_whether_the_capability_was_demonstrated():
    record = _record({"structured_redirect": mq.FAIL})
    case = next(c for c in record["cases"] if c["capability"] == "structured_redirect")
    assert case["status"] == mq.FAIL


# ------------------------------------------------------------------------------------------------- policy: no new config field, the shared budget

def test_the_case_adds_no_config_field_so_no_configuration_fingerprint_moves():
    assert "structured_redirect" not in QualificationCasesConfig.model_fields
    assert "structured_redirect" not in json.dumps(ModelQualificationConfig().model_dump(mode="json"))


def test_the_case_shares_the_anchored_edit_budget_and_says_so_in_its_evidence():
    result, llm = _run(REDIRECTED, EDITED)
    assert [c["max_tokens_override"] for c in llm.calls] == [1024, 1024]
    assert result.evidence["policy"]["source"] == "model_qualification.cases.anchored_edit_protocol.max_tokens"
    policy = ModelQualificationConfig.model_validate({"cases": {"anchored_edit_protocol": {"max_tokens": 2048}}})
    _, llm = _run(REDIRECTED, EDITED, {**STRUCTURED_CTX, "policy": policy})
    assert [c["max_tokens_override"] for c in llm.calls] == [2048, 2048]
    reasoning = ModelQualificationConfig.model_validate({"cases": {"anchored_edit_protocol": {"reasoning_max_tokens": 4096}}})
    result, llm = _run(REDIRECTED, EDITED, {**STRUCTURED_CTX, "policy": reasoning, "reasoning": True})
    assert [c["max_tokens_override"] for c in llm.calls] == [4096, 4096]
    assert result.evidence["policy"]["source"].endswith("anchored_edit_protocol.reasoning_max_tokens")


def test_the_policy_version_was_bumped_for_the_new_case():
    assert mq.QUALIFICATION_POLICY_VERSION == "kriya-qualification/9"
    stale = _record()
    stale["policy_version"] = "kriya-qualification/8"
    assert mq.assess(_fp(), ("plain_completion",), settings=_settings(), record=stale).status == mq.STALE
