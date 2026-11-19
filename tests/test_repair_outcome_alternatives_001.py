"""REPAIR-OUTCOME-ALTERNATIVES-001 (Stage C run 2, 20261119T125126-20dc890c).

MEASURED in the sealed run: every repair request that offered the EDIT and the FILE template together (8 requests, two models) listed
them one after the other; 7 answers filled in more than one and were refused by the strict parser. Requests offering one content
template (13) were answered with one outcome every time.

The correction is prompt text only, in ``response_protocol.structured_contract``: that one combination is presented as numbered
alternatives. These tests prove, from the sealed request and answers (tests/fixtures/stage_c_run2, digest-checked):
- the reference below IS the contract the sealed request carried, so a comparison against it is a comparison against the real prompt;
- every combination that does not offer both templates is byte-identical to before (the qualification cases are among them);
- the changed combination keeps every earlier line in order (no outcome added or withdrawn) and only adds the alternative labels;
- the parser still refuses the sealed two-outcome answers and still accepts each of their halves alone.
"""
import hashlib
import itertools
import os

import pytest

from kriya.agents.response_protocol import CONFLICTING_DEVELOPER_RESPONSE, parse_structured, structured_contract

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "stage_c_run2")
SEALED = {
    "repair_system_prompt.call28.txt": "62b533694aff54bcfda76fdda7a9852ffc0678fac4671d14e5db183af4cf093f",
    "repair_answer.call28.txt": "f2b80313bfd038a25cf06459ecc15f4b3c7def3e3403d32b7182d38da565f54e",
    "repair_answer.call31.txt": "1dbee3513c561ded7b7ba11f05ea916e6bf9cfbd5dec03a41bebcce1f33fb4cd",
}
POM = "pom.xml"
# The inputs agent.py gave structured_contract for the sealed request 28 (a targeted repair_with_patch of an existing file).
SEALED_INPUTS = dict(analysis_required=True, allow_edit=True, allow_file=True, allow_no_change=True, allow_redirect=True)
SEALED_MODE = ("You are the Kriya Developer Agent. MODE: REPAIR.\nRepair exactly one existing file, 'pom.xml' - do not touch or return "
               "content for any other file, even one you're told is also part of this batch.\n")
EDIT_TEMPLATE = f'<<<KRIYA:EDIT path="{POM}">>>'
FILE_TEMPLATE = f'<<<KRIYA:FILE path="{POM}">>>'
NO_CHANGE_TEMPLATE = f'<<<KRIYA:NO_CHANGE path="{POM}">>>'


def _sealed(name):
    with open(os.path.join(FIXTURES, name), "rb") as handle:
        data = handle.read()
    assert hashlib.sha256(data).hexdigest() == SEALED[name], f"{name} is not the sealed run-2 content"
    return data.decode("utf-8")


def _contract_before(filepath, *, analysis_required, allow_edit, allow_file, allow_no_change, allow_redirect=False):
    """``structured_contract`` exactly as it was at ccefcf5 (the revision of the sealed run), kept here as the reference."""
    parts = ["RESPONSE PROTOCOL (exact lines, each on its own line, at column 0):"]
    if analysis_required:
        parts.append("First write 1-3 sentences of analysis as plain text. Then write exactly one outcome:")
    else:
        parts.append("Write exactly one outcome and nothing else:")
    if allow_edit:
        parts.append(
            f'<<<KRIYA:EDIT path="{filepath}">>>\n<<<KRIYA:SEARCH>>>\n<exact existing lines, copied verbatim>\n'
            "<<<KRIYA:REPLACE>>>\n<the replacement lines>\n<<<KRIYA:END_EDIT>>>\n"
            "(repeat SEARCH/REPLACE pairs inside one EDIT block for several changes; each SEARCH must be "
            "complete lines that occur exactly once in the file and must not overlap another SEARCH)"
        )
    if allow_file:
        parts.append(f'<<<KRIYA:FILE path="{filepath}">>>\n<the complete file content>\n<<<KRIYA:END_FILE>>>\n'
                     "(every line you write ends with a newline in the file)")
    if allow_no_change:
        parts.append(f'<<<KRIYA:NO_CHANGE path="{filepath}">>>   (only if this file needs no change)')
    if allow_redirect:
        parts.append(
            f'ONLY IF the real defect is in a DIFFERENT file than "{filepath}", add exactly one line '
            '<<<KRIYA:REDIRECT to="<that other file\'s path>">>> after your analysis and before the outcome. '
            "Naming a file in your analysis, or quoting an error message that names one, does NOT redirect the "
            "repair; only this line does. Omit it otherwise."
        )
    parts.append(
        "Everything between the protocol lines is copied into the file byte for byte - no markdown "
        "fences, no line numbers. Never combine outcomes. Nothing may follow the last protocol line."
    )
    return "\n".join(parts) + "\n"


FLAGS = ("analysis_required", "allow_edit", "allow_file", "allow_no_change", "allow_redirect")
COMBINATIONS = [dict(zip(FLAGS, values, strict=True)) for values in itertools.product((False, True), repeat=len(FLAGS))]


def test_the_reference_is_the_contract_the_sealed_request_carried():
    """Without this the other comparisons would be against my own reading of the old text, not against what the model was sent."""
    assert SEALED_MODE + _contract_before(POM, **SEALED_INPUTS) == _sealed("repair_system_prompt.call28.txt")


def test_a_request_that_does_not_offer_both_templates_is_byte_identical_to_before():
    unchanged = [flags for flags in COMBINATIONS if not (flags["allow_edit"] and flags["allow_file"])]
    assert len(unchanged) == 24
    for flags in unchanged:
        assert structured_contract(POM, **flags) == _contract_before(POM, **flags), flags
    # the three qualification prompts (kriya/core/model_qualification.py) are among them: their evidence still describes this text
    for flags in (dict(analysis_required=False, allow_edit=False, allow_file=True, allow_no_change=False),
                  dict(analysis_required=True, allow_edit=True, allow_file=False, allow_no_change=False),
                  dict(analysis_required=True, allow_edit=True, allow_file=False, allow_no_change=True, allow_redirect=True)):
        assert structured_contract("slug.py", **flags) == _contract_before("slug.py", **flags)


def test_the_sealed_request_shape_now_presents_numbered_alternatives_in_order():
    before, after = _contract_before(POM, **SEALED_INPUTS), structured_contract(POM, **SEALED_INPUTS)
    assert after != before
    marks = ["Then write exactly one outcome:", "your answer contains exactly ONE of them", "ALTERNATIVE 1 of 3 - ", EDIT_TEMPLATE,
             "OR ALTERNATIVE 2 of 3 - ", FILE_TEMPLATE, "OR ALTERNATIVE 3 of 3 - ", NO_CHANGE_TEMPLATE, "<<<KRIYA:REDIRECT to=",
             "Never combine outcomes."]
    positions = [after.index(mark) for mark in marks]
    assert positions == sorted(positions) and len(set(positions)) == len(marks)
    assert after.count("ALTERNATIVE ") == 3 and after.count("\nOR ALTERNATIVE ") == 2
    # nothing the model was told before is gone or reordered: the earlier lines are a subsequence of the new ones
    remaining = iter(after.splitlines())
    assert all(any(line == candidate for candidate in remaining) for line in before.splitlines())
    added = [line for line in after.splitlines() if line not in before.splitlines()]
    assert len(added) == 4 and all("ALTERNATIVE" in line for line in added)
    # tests/_protocol_responses.py reads the analysis line by index
    assert after.splitlines()[:2] == before.splitlines()[:2]


@pytest.mark.parametrize("analysis_required", [False, True])
def test_two_alternatives_when_no_change_is_not_offered_and_it_is_not_named(analysis_required):
    flags = dict(analysis_required=analysis_required, allow_edit=True, allow_file=True, allow_no_change=False)
    after = structured_contract(POM, **flags)
    assert "ALTERNATIVE 1 of 2 - " in after and "OR ALTERNATIVE 2 of 2 - " in after
    assert "ALTERNATIVE 3" not in after and "NO_CHANGE" not in after and "of 3" not in after
    assert after.index(EDIT_TEMPLATE) < after.index("OR ALTERNATIVE 2 of 2") < after.index(FILE_TEMPLATE)


def test_every_changed_combination_differs_only_by_the_alternative_lines():
    for flags in (flags for flags in COMBINATIONS if flags["allow_edit"] and flags["allow_file"]):
        before, after = _contract_before(POM, **flags), structured_contract(POM, **flags)
        kept = [line for line in after.splitlines() if "ALTERNATIVE" not in line]
        assert kept == before.splitlines(), flags


def test_the_parser_still_refuses_the_sealed_two_outcome_answers():
    """Strictness is unchanged: the correction is in what the model is told, never in what is accepted."""
    for name in ("repair_answer.call28.txt", "repair_answer.call31.txt"):
        parsed = parse_structured(_sealed(name), POM)
        assert parsed.reason_code == CONFLICTING_DEVELOPER_RESPONSE and parsed.detail == "more than one outcome block", name
        assert parsed.content is None and parsed.edits == ()


def test_each_half_of_the_sealed_answer_alone_is_one_valid_outcome():
    """The sealed answer 28 held the right fix twice. Either alternative, alone, is what the contract asks for."""
    answer = _sealed("repair_answer.call28.txt")
    cut = answer.index(FILE_TEMPLATE)
    edit_only = parse_structured(answer[:cut], POM)
    assert edit_only.reason_code is None and len(edit_only.edits) == 1
    assert "<exec.mainClass>com.example.ProtocolApplication</exec.mainClass>" in edit_only.edits[0][1]
    analysis = answer[:answer.index(EDIT_TEMPLATE)]
    file_only = parse_structured(analysis + answer[cut:], POM)
    assert file_only.reason_code is None and file_only.edits == ()
    assert "<exec.mainClass>com.example.ProtocolApplication</exec.mainClass>" in file_only.content
