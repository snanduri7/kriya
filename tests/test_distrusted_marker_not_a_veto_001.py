"""DISTRUSTED-MARKER-NOT-A-VETO-001 (Stage C run 2, 20261119T125126-20dc890c; owner P0 ruling 2026-11-19).

OLD ASSUMPTION (VER-006, 2026-09-10): when the program prints an ungrounded ``[VERIFICATION] PASS``, the captured output is the only
evidence channel of the verification path, so any grade over it is a grade over distrusted evidence and is forced to non-PASS.

NEW EVIDENCE (sealed run 2): the application exited 0 and printed the goal's own ``[RESULT] ... equals=true`` line; the grader, told of
the distrust, judged PASS on that line and not on the marker. The marker is Kriya's own soft convention, not a requirement of the goal.
The forced non-PASS stopped the verification unit, so the operator's bound verification authority - the independent judge of the
run's behaviour requirements - never ran.

REQUIRED CORRECTION: an ungrounded PASS marker is NON-EVIDENCE, not a veto. Where the operator's verification authority is bound to the
run and covers behaviour, the existing independent grading of the actual output and exit status decides the unit; the authority still
decides its requirements at the terminal gates. Without such an authority nothing changes: the grade is forced to non-PASS as before.

Every test below changes a decision. Sealed bytes: tests/fixtures/stage_c_run2 (digest-checked).
"""
import hashlib
import json
import os
from unittest.mock import MagicMock

import pytest
from _strict_doubles import strict_engine
from test_ver006_distrust_containment import _FakeRunVerifier

from kriya.workflow.attempt import AttemptContext, _resolve_runtime_verification_grade
from kriya.workflow.authority_bundle import AuthorityBundle, CoverageEntry
from kriya.workflow.requirements import API_PRESERVATION, BEHAVIOR, BEHAVIOR_GENERAL
from kriya.workflow.runtime_verification import DISTRUSTED_MARKER_FAILED_RULE, DISTRUSTED_MARKER_NOT_EVIDENCE_NOTE
from kriya.workflow.verification_contract import ContractVerdictState, classify_contract_verdict
from kriya.workflow.verifier_evidence import apply_runtime_disposition
from kriya.workflow.workflow import bound_behavior_authority

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "stage_c_run2")
SEALED = {
    "ProtocolApplication.call44.java": "ad70a1ae20cb1ef5db674b4122bcbdba97ed80cafb995e9e84088bbeebf1a877",
    "s8_marker_failure.raw_output.txt": "5d1c3ab56761e1f8c9ad58d89f1337f1e1f82ab9b28722d44753e6eb245df27a",
    "grader_verdict.call49.json": "d131eee4a447adc3b64f1829a24ba040ff84e573f0ae0ad252e319902a11cef9",
}


def _sealed(name):
    with open(os.path.join(FIXTURES, name), "rb") as handle:
        data = handle.read()
    assert hashlib.sha256(data).hexdigest() == SEALED[name], f"{name} is not the sealed run-2 content"
    return data.decode("utf-8")


class _Ctx:
    """The two things the grade resolution reads from the attempt context."""

    def __init__(self, verifier, *, bound):
        self.run_verifier = verifier
        if bound is not None:
            self.terminal_behavior_authority_bound = bound


def _sealed_grader():
    verdict = json.loads(_sealed("grader_verdict.call49.json"))
    return {"passed": verdict["passed"], "reasoning": verdict["reasoning"], "likely_files": []}


async def _grade(*, bound, grader=None, output=None, sources=None):
    output = _sealed("s8_marker_failure.raw_output.txt") if output is None else output
    sources = [_sealed("ProtocolApplication.call44.java")] if sources is None else sources
    state, verdict = classify_contract_verdict(output, sources)                      # the existing classifier decides the state
    verifier = _FakeRunVerifier(result=grader or _sealed_grader())
    grade, authority = await _resolve_runtime_verification_grade(
        _Ctx(verifier, bound=bound), state, verdict,
        {"goal": "g", "success_criteria": "s", "output": output, "returncode": 0, "files_written": ["ProtocolApplication.java"]})
    return state, grade, authority, verifier


def _bundle(*claims):
    covers = {f"REQ-{index}": (CoverageEntry(f"REQ-{index}", "0" * 64, claim, BEHAVIOR_GENERAL if claim == BEHAVIOR else None, "why"),)
              for index, claim in enumerate(claims, start=1)}
    return AuthorityBundle(
        digest="d" * 64, stored_dir="/nonexistent", source_path="/nonexistent/manifest.json", authority_id="stage-c-oracle",
        goal_sha256="0" * 64, requirement_set_sha256="0" * 64, base_revision="greenfield", assets={}, prepare=None, verify=("verify.sh",),
        timeout_seconds=60, language="java", build_tool="maven", visibility="hidden", covers=covers)


def _engine(bundle):
    engine = strict_engine()          # tests/_strict_doubles.py: a real, validated config behind engine.kernel (never a bare MagicMock)
    engine.verification_authority_bundle = bundle
    return engine


# ------------------------------------------------------------------------------- 11: the C2 execution is no longer vetoed by the marker

@pytest.mark.asyncio
async def test_c2_replay_with_a_bound_behaviour_authority_the_independent_grade_decides_the_unit():
    state, grade, authority, verifier = await _grade(bound=True)
    assert state is ContractVerdictState.INDETERMINATE_DISTRUSTED                    # still distrusted: the rule itself is kept
    assert grade["passed"] is True and authority == "llm"                            # the existing grader authority, no new outcome
    # the marker is disclosed to the grader as distrusted, exactly as before, and is recorded as not used
    assert len(verifier.calls) == 1 and "ungrounded" in verifier.calls[0]["distrust_notice"]
    assert grade["reasoning"] == DISTRUSTED_MARKER_NOT_EVIDENCE_NOTE + _sealed_grader()["reasoning"]
    assert "not used as evidence" in grade["reasoning"] and "still decides" in grade["reasoning"]
    assert "FAILED RULE" not in grade["reasoning"]
    # and the run's own exit status still outranks the grade: exit 0 leaves it a PASS
    disposition = apply_runtime_disposition(grade, {"success": True, "timed_out": False, "returncode": 0},
                                            goal_text="g", verification_authority=authority)
    assert grade["passed"] is True and disposition["final"] == "PASS"


# ------------------------------------------------------------------------------------------ 12: without the authority nothing changes

@pytest.mark.asyncio
@pytest.mark.parametrize("bound", [None, False, "yes", 1, MagicMock()])
async def test_without_a_bound_behaviour_authority_the_same_grade_is_still_forced_to_non_pass(bound):
    """None: the context has no such field (every caller before this change). Anything that is not exactly True - a truthy string, an
    integer, a bare double - is not a binding."""
    _state, grade, authority, _verifier = await _grade(bound=bound)
    assert grade["passed"] is False and authority == "llm_over_distrusted_evidence"
    assert DISTRUSTED_MARKER_FAILED_RULE in grade["reasoning"]                       # the actionable refusal text is kept


def test_the_attempt_context_is_unbound_unless_the_run_says_otherwise():
    assert AttemptContext.__dataclass_fields__["terminal_behavior_authority_bound"].default is False


def test_only_the_operators_real_bundle_covering_behaviour_is_a_binding():
    assert bound_behavior_authority(_engine(_bundle(BEHAVIOR))) is True
    assert bound_behavior_authority(_engine(_bundle(API_PRESERVATION, BEHAVIOR))) is True
    assert bound_behavior_authority(_engine(_bundle(API_PRESERVATION))) is False     # bound, but it does not judge behaviour
    assert bound_behavior_authority(_engine(None)) is False                          # no authority bound to the run
    assert bound_behavior_authority(MagicMock()) is False                            # a bare double's attribute is not a bundle
    assert bound_behavior_authority(_engine({"covers": {"REQ-1": [BEHAVIOR]}})) is False   # nor is anything shaped like one
    assert bound_behavior_authority(object()) is False


# ---------------------------------------------------------------------- 13: a failed run or a false result is rejected, marker or not

@pytest.mark.asyncio
async def test_a_grader_that_does_not_find_the_required_result_fails_the_unit_even_with_the_authority_bound():
    output = _sealed("s8_marker_failure.raw_output.txt").replace("equals=true", "equals=false")
    answer = {"passed": False, "reasoning": "The [RESULT] line reports equals=false.", "likely_files": []}
    _state, grade, authority, _verifier = await _grade(bound=True, grader=answer, output=output)
    assert grade["passed"] is False and authority == "llm_over_distrusted_evidence"
    assert DISTRUSTED_MARKER_FAILED_RULE in grade["reasoning"] and "equals=false" in grade["reasoning"]


@pytest.mark.asyncio
@pytest.mark.parametrize("run_result", [{"success": False, "timed_out": False, "returncode": 1},
                                        {"success": False, "timed_out": True, "returncode": None}])
async def test_a_failed_or_hung_process_is_rejected_whatever_the_grader_and_the_marker_say(run_result):
    _state, grade, authority, _verifier = await _grade(bound=True)
    assert grade["passed"] is True                                                   # the grader and the marker both say PASS ...
    disposition = apply_runtime_disposition(grade, run_result, goal_text="g", verification_authority=authority)
    assert grade["passed"] is False and disposition["final"] == "FAIL"               # ... and the process status still decides


@pytest.mark.asyncio
async def test_a_grader_that_cannot_be_asked_does_not_pass_the_unit():
    output, source = _sealed("s8_marker_failure.raw_output.txt"), _sealed("ProtocolApplication.call44.java")
    state, verdict = classify_contract_verdict(output, [source])
    verifier = _FakeRunVerifier(side_effect=RuntimeError("model unavailable"))
    grade, authority = await _resolve_runtime_verification_grade(
        _Ctx(verifier, bound=True), state, verdict, {"goal": "g", "success_criteria": "s", "output": output, "returncode": 0})
    assert grade["passed"] is False and authority == "llm_over_distrusted_evidence"


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", [None, True])
async def test_the_programs_own_fail_marker_and_a_grounded_pass_are_decided_as_before(bound):
    passing = {"passed": True, "reasoning": "looks fine", "likely_files": []}
    fail_output = "[RESULT] equals=false\n[VERIFICATION] FAIL: decoded protocol differs\n"
    state, grade, authority, verifier = await _grade(bound=bound, grader=passing, output=fail_output)
    assert state is ContractVerdictState.FAIL and grade["passed"] is False and authority == "contract" and verifier.calls == []
    grounded = [_sealed("ProtocolApplication.call44.java") + '\n// System.out.println("[VERIFICATION] FAIL: x");\n']
    state, grade, authority, verifier = await _grade(bound=bound, grader=passing, sources=grounded)
    assert state is ContractVerdictState.PASS and grade["passed"] is True and authority == "contract" and verifier.calls == []
