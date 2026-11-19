"""DISTRUSTED-MARKER-ACTIONABLE-FAILURE-001 (Stage C run 2, 20261119T125126-20dc890c; owner ruling item 1A).

MEASURED in the sealed run: the gate distrusted an unconditional ``[VERIFICATION] PASS`` and forced a non-PASS (correct). The text it
gave as the reason - the only thing the reopened owner is shown - said "ungrounded" without the rule, next to the grader's "independent
evidence is present". The owner changed one comment.

The correction is that message only (``runtime_verification._resolve_runtime_verification_grade``, the distrusted branch): it now names
the failed rule and the required correction. These tests prove, on the sealed bytes (tests/fixtures/stage_c_run2, digest-checked):
- the sealed failure, classified by the existing classifier, yields the actionable text, and that text reaches the reopened owner
  through the existing evidence and recovery-context builders;
- the verdict, the forced non-PASS and the authority label are what they were; a grounded PASS, a FAIL marker and a failure with no
  marker keep their classification, authority and message;
- the longer message neither creates nor hides a marker line for the recovery decision that re-reads the gate output;
- a unit whose declared verification is failed or unresolved still cannot complete the plan (the terminal gates' precondition).
"""
import hashlib
import json
import os

import pytest
from test_e12_enforce_characterization import _COMPILED
from test_ver006_distrust_containment import _FakeCtx, _FakeRunVerifier
from test_verification_owner_reopen_review_and_owner_search_001 import _verification_unit

from kriya.workflow import owner_recovery
from kriya.workflow.attempt import _resolve_runtime_verification_grade
from kriya.workflow.enforce_loop import _evaluate_subtask_verification, distrusted_marker_cause_stands
from kriya.workflow.resume_support import all_subtasks_completed
from kriya.workflow.runtime_verification import DISTRUSTED_MARKER_FAILED_RULE
from kriya.workflow.verification_contract import ContractVerdictState, classify_contract_verdict, verification_markers
from kriya.workflow.workflow_types import SubtaskStatus

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "stage_c_run2")
SEALED = {
    "ProtocolApplication.call44.java": "ad70a1ae20cb1ef5db674b4122bcbdba97ed80cafb995e9e84088bbeebf1a877",
    "ProtocolApplication.call51.java": "9d0bcabf52ed61b3f742b42df3da019b41c79984748419f3affa3045b1d0ce98",
    "s8_marker_failure.raw_output.txt": "5d1c3ab56761e1f8c9ad58d89f1337f1e1f82ab9b28722d44753e6eb245df27a",
    "s8_npe_failure.raw_output.txt": "f2827610a0a11402bd188dba0d0facd7e4cdb7d8562cf4a5648204bd5e41f7ee",
    "grader_verdict.call49.json": "d131eee4a447adc3b64f1829a24ba040ff84e573f0ae0ad252e319902a11cef9",
}
UNCONDITIONAL = '        System.out.println("[VERIFICATION] PASS");\n'
GROUNDED = ('        if (!equals) {\n            System.out.println("[VERIFICATION] FAIL: decoded protocol differs from the original");\n'
            '            return;\n        }\n        System.out.println("[VERIFICATION] PASS");\n')
OLD_MESSAGE_HEAD = ("A deterministic check found a self-reported verification marker but rejected it as ungrounded (present but never "
                    "demonstrated to gate on anything). ")


def _sealed(name):
    with open(os.path.join(FIXTURES, name), "rb") as handle:
        data = handle.read()
    assert hashlib.sha256(data).hexdigest() == SEALED[name], f"{name} is not the sealed run-2 content"
    return data.decode("utf-8")


def _grader():
    """The sealed grader answer to the second s8 run: PASS, citing the [RESULT] line."""
    verdict = json.loads(_sealed("grader_verdict.call49.json"))
    assert verdict["passed"] is True
    return {"passed": True, "reasoning": verdict["reasoning"], "likely_files": []}


async def _grade(output, sources, grader):
    """The existing classifier on the given bytes, then the existing grade resolution: nothing is hand-fed a state."""
    state, verdict = classify_contract_verdict(output, sources)
    verifier = _FakeRunVerifier(result=grader)
    grade, authority = await _resolve_runtime_verification_grade(
        _FakeCtx(verifier), state, verdict,
        {"goal": "g", "success_criteria": "s", "output": output, "returncode": 0, "files_written": ["ProtocolApplication.java"]})
    return state, grade, authority, verifier


def _as_last_failure(output, reasoning):
    """What the verification-only path stores (runtime_verification: output + grader reasoning) and outcome.last_failure_export bounds."""
    return {"last_failure": {"type": "run_verification", "message": "runtime verification failed",
                             "raw_output": (output + f"\n\n[Grader reasoning]: {reasoning}")[-4000:]}}


# ------------------------------------------------------------------------------------------------- 1: the C2 failure is actionable

@pytest.mark.asyncio
async def test_c2_marker_failure_states_the_failed_rule_and_the_required_correction():
    output, source = _sealed("s8_marker_failure.raw_output.txt"), _sealed("ProtocolApplication.call44.java")
    state, grade, authority, verifier = await _grade(output, [source], _grader())
    assert state is ContractVerdictState.INDETERMINATE_DISTRUSTED
    # unchanged: the verdict is forced whatever the grader said, under the same authority label, and the grader was told of the distrust
    assert grade["passed"] is False and authority == "llm_over_distrusted_evidence"
    assert len(verifier.calls) == 1 and "distrust_notice" in verifier.calls[0]
    reasoning = grade["reasoning"]
    # the earlier opening is kept; the rule and the correction follow it
    assert reasoning.startswith(OLD_MESSAGE_HEAD + DISTRUSTED_MARKER_FAILED_RULE)
    for required in ("FAILED RULE:", 'no written file contains "[VERIFICATION] FAIL" anywhere', "REQUIRED CORRECTION:",
                     'print "[VERIFICATION] PASS" only on the branch where the program\'s own comparison of the real values succeeded',
                     'print "[VERIFICATION] FAIL: <one-sentence reason>" on the branch where it did not',
                     "a change to comments or unrelated code, does not correct this"):
        assert required in reasoning, required
    # the grader's opinion is still shown, and no longer reads as a result
    assert "Grader's own verdict (overridden, not a result): " + _grader()["reasoning"] in reasoning
    assert "cannot pass on LLM judgment over that same distrusted evidence alone" in reasoning


@pytest.mark.asyncio
async def test_the_actionable_text_reaches_the_reopened_owner_whole_through_the_existing_builders():
    output = _sealed("s8_marker_failure.raw_output.txt")
    _state, grade, _authority, _verifier = await _grade(output, [_sealed("ProtocolApplication.call44.java")], _grader())
    evidence = owner_recovery.verification_failure_evidence(_as_last_failure(output, grade["reasoning"]))
    assert DISTRUSTED_MARKER_FAILED_RULE in evidence                                  # inside the 2,000 characters the owner is shown
    context = owner_recovery.verification_owner_recovery_context(
        verification_subtask_id="s8", owner_id="s5", owner_files=["src/main/java/com/example/ProtocolApplication.java"], evidence=evidence)
    assert DISTRUSTED_MARKER_FAILED_RULE in context and context.startswith("--- VERIFICATION FAILURE RECOVERY ---\n")


@pytest.mark.asyncio
async def test_the_message_creates_and_hides_no_marker_line_for_the_recovery_decision(tmp_path):
    """The recovery acceptance re-reads the stored gate output with the same classifier. The message names both marker strings, so it
    must stay inside one line: the only marker line is still the program's own."""
    output = _sealed("s8_marker_failure.raw_output.txt")
    _state, grade, _authority, _verifier = await _grade(output, [_sealed("ProtocolApplication.call44.java")], _grader())
    assert "\n" not in grade["reasoning"]
    stored = _as_last_failure(output, grade["reasoning"])
    assert verification_markers(stored["last_failure"]["raw_output"]) == (("PASS", ""),)
    (tmp_path / "App.java").write_text(_sealed("ProtocolApplication.call51.java"), encoding="utf-8")       # the comment-only answer
    assert distrusted_marker_cause_stands(stored, str(tmp_path), ["App.java"]) is True                    # 3: the cause still stands
    grounded = _sealed("ProtocolApplication.call44.java").replace(UNCONDITIONAL, GROUNDED)
    (tmp_path / "App.java").write_text(grounded, encoding="utf-8")
    assert distrusted_marker_cause_stands(stored, str(tmp_path), ["App.java"]) is False                   # 4: a real branch clears it


# ------------------------------------------------------------------------------- 2: every other classification and message is as it was

@pytest.mark.asyncio
async def test_a_grounded_pass_keeps_its_contract_verdict_and_message():
    grounded = _sealed("ProtocolApplication.call44.java").replace(UNCONDITIONAL, GROUNDED)
    state, grade, authority, verifier = await _grade(_sealed("s8_marker_failure.raw_output.txt"), [grounded], _grader())
    assert state is ContractVerdictState.PASS and grade["passed"] is True and authority == "contract" and verifier.calls == []
    assert grade["reasoning"].startswith("Deterministic verification contract:") and "FAILED RULE" not in grade["reasoning"]


@pytest.mark.asyncio
async def test_a_fail_marker_keeps_its_contract_verdict_and_message():
    output = "[RESULT] equals=false\n[VERIFICATION] FAIL: decoded protocol differs from the original\n"
    state, grade, authority, verifier = await _grade(output, [_sealed("ProtocolApplication.call44.java")], _grader())
    assert state is ContractVerdictState.FAIL and grade["passed"] is False and authority == "contract" and verifier.calls == []
    assert "decoded protocol differs from the original" in grade["reasoning"] and "FAILED RULE" not in grade["reasoning"]


@pytest.mark.asyncio
async def test_a_runtime_failure_without_a_marker_keeps_the_graders_own_answer_untouched():
    """The sealed first s8 failure (NullPointerException): no marker, so the grader is the authority and its text is not rewritten."""
    answer = {"passed": False, "reasoning": "The application failed with a NullPointerException.", "likely_files": ["A.java"]}
    state, grade, authority, verifier = await _grade(_sealed("s8_npe_failure.raw_output.txt"),
                                                     [_sealed("ProtocolApplication.call44.java")], answer)
    assert state is ContractVerdictState.ABSENT and authority == "llm" and grade == answer
    assert "distrust_notice" not in verifier.calls[0]


# ------------------------------------------------------------------- 6: the obligations in front of the terminal gates are as they were

def test_a_failed_or_unresolved_declared_verification_still_cannot_complete_the_plan():
    """Nothing here was changed; this pins what the ruling requires to stay: a verification unit that did not pass is FAILED or
    NEEDS_REVIEW, never COMPLETED, and the plan is complete (the one precondition of the terminal gates, where the bound authority
    runs) only when every unit is COMPLETED."""
    unit = _verification_unit(["s5"], sid="s8")
    for passed, expected in ((True, SubtaskStatus.COMPLETED), (False, SubtaskStatus.FAILED), (None, SubtaskStatus.NEEDS_REVIEW)):
        status, _error, _codes = _evaluate_subtask_verification(unit, {"verification_results": [{**_COMPILED, "passed": passed}]})
        assert status == expected
        done = {"s5": SubtaskStatus.COMPLETED, "s8": status}
        assert all_subtasks_completed({"s5", "s8"}, done) is (expected == SubtaskStatus.COMPLETED)
    assert _evaluate_subtask_verification(unit, {})[0] == SubtaskStatus.NEEDS_REVIEW                       # no evidence at all
