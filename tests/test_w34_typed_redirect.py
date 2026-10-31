"""W3-4 (ATTRIBUTION-SELF-DIAGNOSIS-MENTION-001): only an explicit typed Developer redirect can acquire
``self_diagnosis`` authority; FIX ANALYSIS prose that merely mentions a file is advisory.

Chain under test: Developer response protocol (REDIRECT line) -> DeveloperAgent file entry (``redirect_to``)
-> ``extract_self_diagnosed_files`` (validated attribution input) -> ``attribute_failure``.

The retry-time gates (signature/attempt freshness, stage allowlist, repository re-grounding) live in
``retry_strategy.handle_attempt_failure`` and are pinned in tests/test_workflow.py
(``test_self_diagnosis_still_wins_for_the_attempt_that_produced_it``, the stale-diagnosis test beside it,
``test_self_diagnosis_attribution_no_longer_triggers_plan_scope_conflict``,
``test_an_out_of_scope_self_diagnosis_never_strands_an_in_scope_edit_failure``) and are not duplicated here.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from kriya.agents.agent import DeveloperAgent
from kriya.agents.response_protocol import (
    EDITS,
    FILE,
    INVALID,
    INVALID_EDIT_PROTOCOL,
    NO_CHANGE,
    PROTOCOL_IDENTITIES,
    STRUCTURED,
    RedirectSignal,
    parse_legacy_repair,
    parse_structured,
    structured_contract,
)
from kriya.config import AppConfig
from kriya.core.llm import LLMClient
from kriya.workflow.attribution import (
    DETERMINISTIC_ATTRIBUTION_TIERS,
    attribute_failure,
    extract_self_diagnosed_files,
)
from kriya.workflow.failure import Failure

PARSER = "src/main/java/p/ProtocolParser.java"
PROTOCOL = "src/main/java/p/Protocol.java"
KNOWN = [PARSER, PROTOCOL]
RAW = "[ERROR] /w/src/main/java/p/ProtocolParser.java:[12,20] error: body has private access in Protocol"
NO_CHANGE_BLOCK = f'<<<KRIYA:NO_CHANGE path="{PARSER}">>>\n'
EDIT_BLOCK = (f'<<<KRIYA:EDIT path="{PARSER}">>>\n<<<KRIYA:SEARCH>>>\nold();\n<<<KRIYA:REPLACE>>>\nnew();\n'
              "<<<KRIYA:END_EDIT>>>\n")
FILE_BLOCK = f'<<<KRIYA:FILE path="{PARSER}">>>\nclass P {{}}\n<<<KRIYA:END_FILE>>>\n'
REDIRECT = f'<<<KRIYA:REDIRECT to="{PROTOCOL}">>>'


# ------------------------------------------------------------------ the parser (typed signal)

@pytest.mark.parametrize("block, kind", [(NO_CHANGE_BLOCK, NO_CHANGE), (EDIT_BLOCK, EDITS), (FILE_BLOCK, FILE)])
def test_a_redirect_line_before_any_outcome_is_parsed_as_a_typed_signal(block, kind):
    parsed = parse_structured(f"Protocol lacks accessors.\n{REDIRECT}\n{block}", PARSER)
    assert parsed.kind == kind
    assert parsed.redirect == RedirectSignal(from_file=PARSER, to_file=PROTOCOL, statement="Protocol lacks accessors.")
    assert "KRIYA" not in (parsed.analysis or "")  # the signal line is protocol, not analysis text


@pytest.mark.parametrize("prose", [
    "The compiler says: body has private access in Protocol.",                 # a quotation
    "Protocol.java is fine and needs no change; the bug is in this parser.",   # a negation
    "The real defect is in src/main/java/p/Protocol.java: its fields need accessors.",   # a claim, untyped
])
def test_prose_alone_never_produces_a_redirect(prose):
    parsed = parse_structured(f"{prose}\n{EDIT_BLOCK}", PARSER)
    assert parsed.kind == EDITS and parsed.redirect is None
    assert parsed.analysis == prose  # kept verbatim as evidence


def test_the_legacy_protocol_has_no_redirect_marker_so_it_never_redirects():
    legacy = parse_legacy_repair(f"FIX ANALYSIS: {REDIRECT}\nThe defect is in Protocol.java.\nNO CHANGE NEEDED", PARSER, patch_allowed=True)
    assert legacy.redirect is None


@pytest.mark.parametrize("response", [
    f'<<<KRIYA:REDIRECT to="{PROTOCOL}"\n{NO_CHANGE_BLOCK}',                       # unterminated sentinel
    f'<<<KRIYA:REDIRECT>>>\n{NO_CHANGE_BLOCK}',                                     # no target
    f'<<<KRIYA:REDIRECT to="">>>\n{NO_CHANGE_BLOCK}',                               # empty target
    f'<<<KRIYA:REDIRECT to="../outside.java">>>\n{NO_CHANGE_BLOCK}',                # escapes the repository
    f'<<<KRIYA:REDIRECT to="/etc/passwd">>>\n{NO_CHANGE_BLOCK}',                    # absolute
    f'<<<KRIYA:REDIRECT to="a\\b.java">>>\n{NO_CHANGE_BLOCK}',                      # backslash
    f'<<<KRIYA:REDIRECT path="{PROTOCOL}">>>\n{NO_CHANGE_BLOCK}',                   # wrong attribute
    f"{REDIRECT}\n{REDIRECT}\n{NO_CHANGE_BLOCK}",                                    # more than one
    f"{NO_CHANGE_BLOCK}{REDIRECT}\n",                                                # after the outcome
])
def test_a_malformed_misplaced_or_repeated_redirect_line_is_refused_typed_and_carries_no_signal(response):
    parsed = parse_structured(response, PARSER)
    assert parsed.kind == INVALID and parsed.reason_code == INVALID_EDIT_PROTOCOL
    assert parsed.redirect is None


def test_a_redirect_to_the_requested_file_itself_is_no_signal_not_an_error():
    parsed = parse_structured(f'x\n<<<KRIYA:REDIRECT to="{PARSER}">>>\n{NO_CHANGE_BLOCK}', PARSER)
    assert parsed.kind == NO_CHANGE and parsed.redirect is None


def test_the_redirect_target_is_normalized_like_every_protocol_path():
    parsed = parse_structured(f'<<<KRIYA:REDIRECT to="./src//main/java/p/Protocol.java">>>\n{NO_CHANGE_BLOCK}', PARSER)
    assert parsed.redirect.to_file == PROTOCOL


def test_the_contract_offers_the_redirect_only_to_repair_requests_and_keeps_the_analysis_line_stable():
    base = dict(analysis_required=True, allow_edit=True, allow_file=False, allow_no_change=True)
    offered = structured_contract(PARSER, allow_redirect=True, **base)
    plain = structured_contract(PARSER, **base)
    assert "KRIYA:REDIRECT" in offered and "KRIYA:REDIRECT" not in plain
    assert "does NOT redirect" in offered  # tells the model a mention is not a redirect
    # tests/_protocol_responses.py reads the analysis-required line by index: it must not move.
    assert offered.splitlines()[1] == plain.splitlines()[1]


def test_the_structured_protocol_identity_was_bumped_for_the_changed_grammar():
    assert PROTOCOL_IDENTITIES[STRUCTURED] == "kriya_sentinel_v2"


# ------------------------------------------- Developer response -> file entry -> attribution (the chain)

def _repair_entries(response: str):
    llm = LLMClient(AppConfig())
    llm.complete = AsyncMock(return_value=response)
    developer = DeveloperAgent("developer", llm)
    entries = [{"filepath": PARSER, "content": None, "edits": None}]
    files = asyncio.run(developer._fill_missing_content(
        entries, "Task", "Design", "Existing code", None, None, None, None,
        prior_error_context=RAW, error_source_context={PARSER: "\n>> 12: int b = p.body;\n"},
    ))
    return files, llm


def _attribute(entries):
    failure = Failure(type="compile", message="COMPILATION FAILURE", raw_output=RAW, likely_files=[PARSER])
    diagnosed = extract_self_diagnosed_files(entries, KNOWN)
    return asyncio.run(attribute_failure(failure, KNOWN, 2, [], MagicMock(), lambda fp: None,
                                         self_diagnosed_files=diagnosed or None))


def test_chain_a_typed_redirect_from_a_real_repair_response_reaches_self_diagnosis():
    entries, llm = _repair_entries(f"The accessor is missing in Protocol.\n{REDIRECT}\n{NO_CHANGE_BLOCK}")
    assert "KRIYA:REDIRECT" in llm.complete.call_args_list[0][0][0]  # the repair prompt offers the line
    assert entries[0]["redirect_to"] == [PROTOCOL]
    result = _attribute(entries)
    assert (result.tier, result.files, result.confidence) == ("self_diagnosis", [PROTOCOL], "high")


@pytest.mark.parametrize("analysis", [
    "The compiler says: body has private access in Protocol. Use getBody() instead of the field.",
    "Protocol.java is fine and needs no change; the bug is in this parser.",
    "The real defect is in Protocol.java: its fields need accessors.",
])
def test_chain_prose_only_repair_responses_keep_the_compiler_locator(analysis):
    entries, _ = _repair_entries(f"{analysis}\n{EDIT_BLOCK}")
    assert entries[0]["analysis"] == analysis and "redirect_to" not in entries[0]
    result = _attribute(entries)
    assert (result.tier, result.files) == ("locator", [PARSER])


def test_chain_an_invalid_response_carries_no_redirect():
    entries, _ = _repair_entries(f"{REDIRECT}\n{REDIRECT}\n{NO_CHANGE_BLOCK}")
    assert "redirect_to" not in entries[0] and entries[0]["protocol_reason_code"] == INVALID_EDIT_PROTOCOL
    assert _attribute(entries).tier == "locator"


def test_chain_a_truncated_completion_carries_no_redirect(monkeypatch):
    # A valid REDIRECT line in a completion the provider cut off is never trusted (PRD-015: truncated output is not a response).
    monkeypatch.setattr("kriya.agents.agent._truncated_completion_error", lambda llm, filepath: "output truncated")
    entries, _ = _repair_entries(f"x\n{REDIRECT}\n{NO_CHANGE_BLOCK}")
    assert "redirect_to" not in entries[0] and "protocol_error" in entries[0]
    assert extract_self_diagnosed_files(entries, KNOWN) == []


def test_chain_a_redirect_to_an_unknown_file_falls_back_to_the_locator():
    entries, _ = _repair_entries(f'x\n<<<KRIYA:REDIRECT to="src/main/java/p/Nope.java">>>\n{NO_CHANGE_BLOCK}')
    assert entries[0]["redirect_to"] == ["src/main/java/p/Nope.java"]  # carried as a CLAIM ...
    assert extract_self_diagnosed_files(entries, KNOWN) == []  # ... and refused at the validated input
    assert _attribute(entries).tier == "locator"


def test_the_first_pass_generation_prompt_does_not_offer_a_redirect():
    llm = LLMClient(AppConfig())
    llm.complete = AsyncMock(return_value=f'<<<KRIYA:FILE path="{PARSER}">>>\nclass P {{}}\n<<<KRIYA:END_FILE>>>\n')
    developer = DeveloperAgent("developer", llm)
    entries = [{"filepath": PARSER, "content": None, "edits": None}]
    asyncio.run(developer._fill_missing_content(entries, "Task", "Design", "Existing code", None, None, None, None))
    assert "KRIYA:REDIRECT" not in llm.complete.call_args_list[0][0][0]


# -------------------------------------------------------------- authority classification unchanged (10)

def test_the_self_diagnosis_tier_remains_judgment_not_deterministic():
    assert "self_diagnosis" not in DETERMINISTIC_ATTRIBUTION_TIERS
