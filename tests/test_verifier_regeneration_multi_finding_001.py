"""VERIFIER-REGENERATION-MULTI-FINDING-001 (Stage B run 12, 20261119T061917-bcee8862; owner ruling 2026-11-19; Type C, an intentional
prompt-behaviour correction).

MEASURED: the person's rationale named three invalid expectations (wrong time bytes; a whole-object equality that requires Protocol.equals();
a null-body round trip). The regeneration request closed with the singular "remove or correct the expectation the reason identifies". The
model corrected the time bytes and left the other two, in the live run and again when the sealed request was replayed. With ONLY that closing
directive replaced by the text below, two replays of the same request removed the whole-object equality as well (one sample each; the
null-body expectation stayed in both, a characterized model-output limitation this change does not fix).

The directive is generic: every distinct finding of the rationale is an action, over the whole verifier, including assertions in tests that
pass. Nothing else of the request changes: the reviewed source, the rationale and its scope, the complete-file and unchanged-answer
instructions, the operation's mode, its delivery retries and its response rules.
"""
from __future__ import annotations

import hashlib

import pytest

from kriya.workflow.verifier_semantic import VerifierRegenerationRequest

PATH = "src/test/java/com/example/ProtocolParserTest.java"
SOURCE = "class ProtocolParserTest {\n    // the reviewed verifier\n}\n"
SHA = hashlib.sha256(SOURCE.encode("utf-8")).hexdigest()
# run 12's rationale, verbatim: three findings in one sentence
RATIONALE = ("The verifier is invalid because it hardcodes the wrong 4-byte big-endian time: 1700000000 is 0x6553F100 (65 53 F1 00), not 65 88 "
             "D9 00; it also unnecessarily requires Protocol.equals() value semantics and null-body behavior, neither of which the canonical "
             "contract requires.")
DIRECTIVE = (
    "The operator's semantic-review rationale may identify multiple independent defects in this verifier. Treat every distinct finding as "
    "requiring action for this file. Review the entire verifier, including assertions in tests that currently pass. Correct invalid expected "
    "values and remove or replace expectations the rationale identifies as unsupported by the Authoritative Goal; preserve valid "
    "contract-backed expectations. Do not preserve an unsupported expectation merely because the current candidate implementation satisfies "
    "it or the test passes. Do not introduce new expectations for unspecified behavior. Return a verifier implementing all findings, not "
    "merely fixes for the reported failing tests."
)
SINGULAR = ("remove or correct the expectation the reason identifies, and keep every expectation the Authoritative Goal supports. Add no "
            "expectation the Authoritative Goal does not state.")
BEFORE = f"Rewrite exactly '{PATH}': "
AFTER = (" Return the COMPLETE replacement file. This is the verifier's one regeneration: returning it unchanged is not an accepted outcome of "
         "this request.\n")
SCOPE = (f"A person reviewed '{PATH}' (sha256 {SHA[:16]}, shown above) against the Authoritative Goal and declared what it expects INVALID. "
         "Their stated reason, quoted between the markers, is evidence about this one file only: it is not part of the goal, not a new "
         "requirement and not an instruction about any other file.\n")
# The exact text sent in the measured replay (704 characters) and the complete block for the inputs above.
DIRECTIVE_SHA256 = "2f673b9f0e2a7ddff1ddf5d3b4783084d879d6871e9ed9e6055586ea4c964723"
BLOCK_SHA256 = "18f2006094f9d499b98b91a023e4cfa7e7216329a8aee9166e2fa2af49ce031b"
BLOCK_WITH_THE_SINGULAR_DIRECTIVE_SHA256 = "8e9570c1d564dce410713e1e60d7091ccee9e263f4024a7aa84ca5277a12b32a"


def _block(rationale: str = RATIONALE) -> str:
    return VerifierRegenerationRequest(PATH, SHA, rationale, SOURCE).prompt_block


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_the_approved_closing_instruction(block: str) -> None:
    """The one check of the closing instruction: the multi-finding directive, between the two instructions that stay."""
    assert block.endswith(BEFORE + DIRECTIVE + AFTER), block[-900:]
    assert block.count(DIRECTIVE) == 1 and SINGULAR not in block and "the expectation the reason identifies" not in block


def test_the_regeneration_request_tells_the_developer_to_act_on_every_finding_over_the_whole_verifier():
    block = _block()
    _is_the_approved_closing_instruction(block)
    assert (len(DIRECTIVE), _sha(DIRECTIVE)) == (704, DIRECTIVE_SHA256), "the measured wording, not a paraphrase"
    for required in ("multiple independent defects", "every distinct finding", "including assertions in tests that currently pass",
                     "merely because the current candidate implementation satisfies it or the test passes",
                     "Do not introduce new expectations for unspecified behavior", "not merely fixes for the reported failing tests"):
        assert required in block


def test_the_adjacent_instructions_are_preserved_and_end_the_block():
    block = _block()
    assert block.count(BEFORE) == 1 and block.count(AFTER) == 1 and block.index(BEFORE) < block.index(DIRECTIVE) < block.index(AFTER)
    assert block.endswith(AFTER), "the complete-file and unchanged-answer instructions are still the last words of the request"


def test_restoring_the_singular_directive_is_refused_by_the_same_check():
    """Negative control: the block as it was before this change (only the directive differs) fails the focused check."""
    restored = _block().replace(DIRECTIVE, SINGULAR)
    assert _sha(restored) == BLOCK_WITH_THE_SINGULAR_DIRECTIVE_SHA256, "exactly the earlier emitted block for these inputs"
    with pytest.raises(AssertionError):
        _is_the_approved_closing_instruction(restored)
    for partial in (_block().replace("every distinct finding", "the finding"), _block().replace(DIRECTIVE, ""), _block().replace(AFTER, "\n")):
        with pytest.raises(AssertionError):
            _is_the_approved_closing_instruction(partial)


def test_the_rationale_its_scope_and_the_reviewed_source_are_unmodified():
    block = _block()
    assert block.count(f"<<<OPERATOR_RATIONALE\n{RATIONALE}\nOPERATOR_RATIONALE>>>\n") == 1, "the person's words, verbatim, once"
    assert block.count(SCOPE) == 1, "still evidence about this one file: not goal text, not a requirement, not about another file"
    assert block.count(f"<<<VERIFIER_SOURCE\n{SOURCE}VERIFIER_SOURCE>>>\n") == 1 and f"(sha256 {SHA[:16]})" in block
    assert block.index("<<<VERIFIER_SOURCE") < block.index(SCOPE) < block.index("<<<OPERATOR_RATIONALE") < block.index(BEFORE)
    assert RATIONALE not in block.replace(f"<<<OPERATOR_RATIONALE\n{RATIONALE}\nOPERATOR_RATIONALE>>>\n", ""), "quoted nowhere else"


def test_nothing_but_the_directive_changed_in_the_emitted_block():
    block = _block()
    assert _sha(block) == BLOCK_SHA256
    assert len(block) - len(block.replace(DIRECTIVE, SINGULAR)) == len(DIRECTIVE) - len(SINGULAR) == 530


@pytest.mark.parametrize("rationale", ["one finding only", RATIONALE, "first; second; third; fourth"])
def test_the_directive_is_generic_it_does_not_depend_on_the_rationale_or_the_scenario(rationale):
    block = _block(rationale)
    _is_the_approved_closing_instruction(block)
    assert f"<<<OPERATOR_RATIONALE\n{rationale}\nOPERATOR_RATIONALE>>>\n" in block
    for scenario_word in ("Protocol", "equals", "null", "time", "byte", "Ignite"):
        assert scenario_word not in DIRECTIVE, "no scenario-specific rule"


def test_the_operation_its_delivery_and_its_response_rules_are_unchanged():
    request = VerifierRegenerationRequest(PATH, SHA, RATIONALE, SOURCE)
    assert request.mode_text == (
        "MODE: VERIFIER_REGENERATION.\nReturn the complete replacement content of exactly one existing file - the requested path "
        f"'{PATH}'. The whole file is replaced by what you return: include every part of it that stays. Never return content for "
        "another file.\n")
    assert request.delivery_retries == 1
    assert request.response_violation({"filepath": PATH, "content": "class X {}\n"}) is None
    assert "no file content" in request.response_violation({"filepath": PATH, "content": None})
    assert "anchored EDIT" in request.response_violation({"filepath": PATH, "content": None, "edits": [{"search": "a", "replace": "b"}]})
    correction = request.correction_block("the machine error")
    assert "the machine error" in correction and DIRECTIVE not in correction and RATIONALE not in correction
