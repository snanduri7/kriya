"""PRD-025: bounded runtime-verifier evidence package.

Covers the retained-evidence scan, the budgeted package (markers at start,
middle and end of huge output), both kinds of truncation, the tri-state
grader verdict, per-model package rebuild on fallback, and the
deterministic runtime disposition (nonzero exit / timeout authority).
"""
import json
from unittest.mock import AsyncMock

import pytest

from kriya.agents.agent import RunVerifierAgent
from kriya.config import AppConfig, FallbackModelConfig, LLMConfig
from kriya.core.llm import LLMClient
from kriya.tools.process import ProcessController, ProcessResult
from kriya.workflow.context_budget import allocation_window
from kriya.workflow.verifier_evidence import (
    ANY_NONZERO_EXIT,
    CAPTURE_LOSS_UNRESOLVED,
    DECISIVE_EVIDENCE_OMITTED,
    EXPECTED_NONZERO_EXIT_GROUNDED,
    NONZERO_EXIT_AUTHORITATIVE,
    TIMEOUT_AUTHORITATIVE,
    VERIFIER_CALL_FAILED,
    VERIFIER_CONFIRMED,
    VERIFIER_RESULT_MALFORMED,
    RetainedRuntimeEvidence,
    RuntimeVerdict,
    apply_runtime_disposition,
    build_package_for_budget,
    build_verifier_evidence_package,
    declared_nonzero_exit_codes,
    finalize_semantic_verdict,
    goal_declares_expected_nonzero_exit,
    parse_reported_verdict,
    runtime_evidence_outcome_fields,
)

_FILLER_LINE = "INFO worker heartbeat ok tick\n"


def _huge_with_markers(total_lines: int = 40_000) -> str:
    """A verbose log with a decisive marker at the start, the exact middle
    and the end - the shape a head/tail-only truncation would lose."""
    lines = [_FILLER_LINE] * total_lines
    lines[5] = "java.lang.IllegalStateException: START_MARKER broken\n"
    lines[total_lines // 2] = "AssertionError: MIDDLE_MARKER expected 3 but got 4\n"
    lines[-3] = "[VERIFICATION] FAIL: END_MARKER mismatch\n"
    return "".join(lines)


def _run_result(stdout: str, *, exit_code=0, lost=0, success=None, timed_out=False, stderr=""):
    step = {
        "command": ["python3", "app.py"], "exit_code": exit_code, "stdout": stdout,
        "stderr": stderr, "timed_out": timed_out,
    }
    if lost:
        step["stdout_lost_chars"] = lost
    return {
        "success": (exit_code == 0 and not timed_out) if success is None else success,
        "timed_out": timed_out, "returncode": exit_code, "output": stdout, "steps": [step],
    }


# --- retained evidence and package -----------------------------------------


def test_small_output_is_sent_verbatim_and_not_truncated():
    evidence = RetainedRuntimeEvidence.from_run_result(_run_result("hello\n"))
    package = build_verifier_evidence_package(evidence, 10_000, model="m")
    assert "hello" in package.rendered
    assert "exit_code=0" in package.rendered
    assert package.package_truncated is False
    assert package.capture_truncated is False
    assert package.to_dict()["truncation"] == []


def test_huge_output_keeps_start_middle_and_end_markers_within_budget():
    text = _huge_with_markers()
    evidence = RetainedRuntimeEvidence.from_run_result(_run_result(text))
    budget = 20_000
    package = build_verifier_evidence_package(evidence, budget, model="m")

    assert len(package.rendered.encode("utf-8")) <= budget
    for marker in ("START_MARKER", "MIDDLE_MARKER", "END_MARKER"):
        assert marker in package.rendered, marker
    assert package.package_truncated is True
    assert package.omitted_ranges, "omitted ranges must be explicit"
    assert "PACKAGE_TRUNCATION" in package.rendered
    assert package.decisive_windows_omitted is False
    kinds = {window.kind for window in package.included_windows}
    assert {"exception", "assertion", "kriya_marker"} <= kinds
    data = package.to_dict()
    assert data["truncation"] == ["PACKAGE_TRUNCATION"]
    assert data["head_tail"] and data["budget_bytes"] == budget


def test_repeated_signature_lines_collapse_with_occurrence_count():
    text = "".join(f"ERROR: retry {i} failed\n" for i in range(5_000)) + "done\n"
    evidence = RetainedRuntimeEvidence.from_run_result(_run_result(text))
    stream = evidence.steps[0].streams[0]
    assert dict(stream.signature_counts)["error"] == 5_000
    # digits normalize away, so every line is one signature: first + last window
    assert len(stream.windows) == 2
    assert stream.windows[0].occurrences == 5_000


def test_capture_truncation_is_reported_and_distinct_from_package_truncation():
    evidence = RetainedRuntimeEvidence.from_run_result(_run_result("tail only\n", lost=1_234))
    assert evidence.capture_lost_chars == 1_234
    package = build_verifier_evidence_package(evidence, 50_000, model="m")
    assert package.capture_truncated is True
    assert package.package_truncated is False
    assert "CAPTURE_TRUNCATION: 1234 earlier stdout characters were lost" in package.rendered
    assert "never scanned" in package.rendered
    assert package.to_dict()["truncation"] == ["CAPTURE_TRUNCATION"]
    assert package.to_dict()["capture_truncation"] == [{"step": 1, "stream": "stdout", "lost_chars": 1_234}]


def test_decisive_window_that_cannot_fit_is_recorded_as_omitted():
    text = _huge_with_markers(2_000)
    evidence = RetainedRuntimeEvidence.from_run_result(_run_result(text))
    package = build_verifier_evidence_package(evidence, 300, model="m")
    assert package.decisive_windows_omitted is True
    assert package.to_dict()["omitted_windows"]


def test_byte_budget_holds_for_non_ascii_output():
    text = "ü" * 50_000 + "\nTraceback (most recent call last):\n  x\nValueError: boom\n"
    evidence = RetainedRuntimeEvidence.from_run_result(_run_result(text))
    package = build_package_for_budget(evidence, 8_000, model="m")
    assert len(package.rendered.encode("utf-8")) <= 8_000
    assert "ValueError: boom" in package.rendered


def test_combined_output_fallback_when_steps_carry_no_stream_text():
    evidence = RetainedRuntimeEvidence.from_run_result({
        "success": True, "returncode": 0, "output": "all good", "steps": [{"command": ["x"], "exit_code": 0}],
    })
    assert evidence.steps[0].streams[0].name == "output"
    assert evidence.steps[0].streams[0].text == "all good"


def test_decisive_signature_overflow_counts_but_routine_error_overflow_does_not():
    decisive = "".join(f"Caused by: Distinct{chr(65 + i % 26)}{chr(65 + i // 26)}Failure\n" for i in range(80))
    evidence = RetainedRuntimeEvidence.from_run_result(_run_result(decisive))
    assert evidence.steps[0].streams[0].distinct_overflow > 0
    routine = "".join(f"ERROR: {chr(65 + i % 26)}{chr(65 + i // 26)} slow\n" for i in range(80))
    evidence = RetainedRuntimeEvidence.from_run_result(_run_result(routine))
    assert evidence.steps[0].streams[0].distinct_overflow == 0


# --- semantic verdict ceiling --------------------------------------------


def _package(text="ok\n", lost=0, budget=50_000):
    return build_verifier_evidence_package(
        RetainedRuntimeEvidence.from_run_result(_run_result(text, lost=lost)), budget, model="m",
    )


def test_verbose_package_truncation_alone_does_not_demote_pass():
    verbose = _FILLER_LINE * 20_000
    package = _package(verbose, budget=5_000)
    assert package.package_truncated is True
    assert finalize_semantic_verdict(RuntimeVerdict.PASS, package) == (RuntimeVerdict.PASS, VERIFIER_CONFIRMED)


def test_pass_with_capture_loss_becomes_unknown():
    assert finalize_semantic_verdict(RuntimeVerdict.PASS, _package(lost=10)) == (
        RuntimeVerdict.UNKNOWN, CAPTURE_LOSS_UNRESOLVED,
    )


def test_pass_with_omitted_decisive_window_becomes_unknown():
    package = _package(_huge_with_markers(2_000), budget=300)
    assert finalize_semantic_verdict(RuntimeVerdict.PASS, package) == (
        RuntimeVerdict.UNKNOWN, DECISIVE_EVIDENCE_OMITTED,
    )


def test_fail_and_unknown_are_never_upgraded():
    package = _package()
    assert finalize_semantic_verdict(RuntimeVerdict.FAIL, package)[0] is RuntimeVerdict.FAIL
    assert finalize_semantic_verdict(RuntimeVerdict.UNKNOWN, package)[0] is RuntimeVerdict.UNKNOWN


@pytest.mark.parametrize(("parsed", "passed", "expected"), [
    ({"verdict": "PASS"}, True, RuntimeVerdict.PASS),
    ({"verdict": "fail"}, False, RuntimeVerdict.FAIL),
    ({"verdict": "Unknown"}, False, RuntimeVerdict.UNKNOWN),
    ({"verdict": "MAYBE"}, True, RuntimeVerdict.UNKNOWN),
    ({}, True, RuntimeVerdict.PASS),
    ({}, False, RuntimeVerdict.FAIL),
])
def test_parse_reported_verdict(parsed, passed, expected):
    assert parse_reported_verdict(parsed, passed) is expected


# --- deterministic runtime disposition -------------------------------------


@pytest.mark.parametrize(("goal", "declared"), [
    ("Invalid input prints INVALID_INPUT and exits non-zero.", True),
    ("On bad input exit with a nonzero status", True),
    ("the CLI should exit with code 2 when the file is missing", True),
    ("return a non-zero exit code on failure", True),
    ("exits with an error for unknown flags", True),
    ("Print hello and exit.", False),
    ("The program must not exit non-zero.", False),
    ("never exit with a nonzero status", False),
    ("", False),
])
def test_goal_declares_expected_nonzero_exit(goal, declared):
    assert goal_declares_expected_nonzero_exit(goal) is declared


def _pass_grade():
    return {"passed": True, "verdict": "PASS", "reason_code": VERIFIER_CONFIRMED, "reasoning": "looks right"}


def test_nonzero_exit_overrides_llm_pass_when_goal_does_not_declare_it():
    grade = _pass_grade()
    disposition = apply_runtime_disposition(
        grade, _run_result("done", exit_code=1), goal_text="Print hello", verification_authority="llm",
    )
    assert grade["passed"] is False
    assert disposition["final"] == "FAIL"
    assert disposition["deterministic_reason"] == NONZERO_EXIT_AUTHORITATIVE
    assert disposition["semantic"] == "PASS"
    assert "Deterministic runtime evidence overrides" in grade["reasoning"]


def test_nonzero_exit_admitted_only_when_goal_declares_it_and_app_launched():
    grade = _pass_grade()
    disposition = apply_runtime_disposition(
        grade, _run_result("INVALID_INPUT", exit_code=2),
        goal_text="Reject invalid input and exit with a nonzero status.", verification_authority="llm",
    )
    assert grade["passed"] is True
    assert disposition["final"] == "PASS"
    assert disposition["deterministic_reason"] == EXPECTED_NONZERO_EXIT_GROUNDED


def test_declared_nonzero_exit_still_fails_when_a_setup_step_failed():
    run = {
        "success": False, "timed_out": False, "returncode": 2, "output": "",
        "steps": [
            {"command": ["javac", "A.java"], "exit_code": 1, "stdout": "", "stderr": "err", "timed_out": False},
            {"command": ["java", "A"], "exit_code": 2, "stdout": "", "stderr": "", "timed_out": False},
        ],
    }
    grade = _pass_grade()
    disposition = apply_runtime_disposition(
        grade, run, goal_text="exit with a nonzero status on bad input", verification_authority="llm",
    )
    assert grade["passed"] is False
    assert disposition["deterministic_reason"] == NONZERO_EXIT_AUTHORITATIVE


@pytest.mark.parametrize(("goal", "codes"), [
    ("Invalid input exits non-zero.", ANY_NONZERO_EXIT),
    ("the CLI should exit with code 2 when the file is missing", frozenset({"2"})),
    ("return code of 3 for bad flags; exit with code 4 for a missing file", frozenset({"3", "4"})),
    ("exit with code 2, or exits non-zero on any other error", ANY_NONZERO_EXIT),
    ("never exit with code 2", frozenset()),
    ("Print hello.", frozenset()),
])
def test_declared_nonzero_exit_codes(goal, codes):
    assert declared_nonzero_exit_codes(goal) == codes


@pytest.mark.parametrize(("exit_code", "final", "reason"), [
    (2, "PASS", EXPECTED_NONZERO_EXIT_GROUNDED),
    (1, "FAIL", NONZERO_EXIT_AUTHORITATIVE),
    (139, "FAIL", NONZERO_EXIT_AUTHORITATIVE),
])
def test_an_exit_code_other_than_the_declared_one_always_fails(exit_code, final, reason):
    """A goal naming the expected code admits only that code: a crash (1) or
    a segfault (139) is an unrelated failure, whatever the grader says."""
    grade = _pass_grade()
    disposition = apply_runtime_disposition(
        grade, _run_result("", exit_code=exit_code),
        goal_text="Exit with code 2 when the input file is missing.", verification_authority="llm",
    )
    assert disposition["final"] == final
    assert disposition["deterministic_reason"] == reason
    assert grade["passed"] is (final == "PASS")


def test_a_declared_exit_from_an_application_that_never_launched_fails():
    run = _run_result("Error: Could not find or load main class App", exit_code=1)
    run["output"] = "Error: Could not find or load main class App"
    grade = _pass_grade()
    disposition = apply_runtime_disposition(
        grade, run, goal_text="Invalid input exits non-zero.", verification_authority="llm",
    )
    assert disposition["final"] == "FAIL"
    assert disposition["deterministic_reason"] == NONZERO_EXIT_AUTHORITATIVE


def test_a_declared_exit_still_needs_the_semantic_verdict():
    """The goal only makes the exit admissible evidence; the verifier must
    still confirm the behaviour (an unrelated crash with the declared code is
    graded FAIL and stays FAIL)."""
    grade = {"passed": False, "verdict": "FAIL", "reasoning": "traceback, not a rejection"}
    disposition = apply_runtime_disposition(
        grade, _run_result("Traceback ...", exit_code=2),
        goal_text="Invalid input exits non-zero.", verification_authority="llm",
    )
    assert disposition["final"] == "FAIL" and grade["passed"] is False


def test_verifier_text_declaring_the_exit_grants_nothing():
    """The grader/judge saying the exit is expected is not the user's goal."""
    grade = {**_pass_grade(), "reasoning": "The app correctly exits non-zero as the success criteria require."}
    disposition = apply_runtime_disposition(
        grade, _run_result("INVALID_INPUT", exit_code=2), goal_text="Build a greeting CLI.",
        verification_authority="llm",
    )
    assert disposition["final"] == "FAIL"
    assert disposition["deterministic_reason"] == NONZERO_EXIT_AUTHORITATIVE


def test_timeout_is_authoritative_over_a_pass_grade():
    grade = _pass_grade()
    disposition = apply_runtime_disposition(
        grade, _run_result("done", exit_code=-1, timed_out=True), goal_text="exits non-zero",
        verification_authority="llm",
    )
    assert grade["passed"] is False
    assert disposition["deterministic_reason"] == TIMEOUT_AUTHORITATIVE


def test_unknown_semantic_verdict_is_never_pass_on_a_clean_exit():
    grade = {"passed": False, "verdict": "UNKNOWN", "reason_code": CAPTURE_LOSS_UNRESOLVED, "reasoning": "r"}
    disposition = apply_runtime_disposition(
        grade, _run_result("ok"), goal_text="print ok", verification_authority="llm",
    )
    assert disposition["final"] == "UNKNOWN"
    assert disposition["semantic_reason"] == CAPTURE_LOSS_UNRESOLVED
    assert grade["passed"] is False


def test_clean_exit_pass_stays_pass_and_is_idempotent():
    grade = _pass_grade()
    run = _run_result("ok")
    first = apply_runtime_disposition(grade, run, goal_text="print ok", verification_authority="llm")
    second = apply_runtime_disposition(grade, run, goal_text="print ok", verification_authority="llm")
    assert first == second
    assert first["final"] == "PASS" and first["deterministic"] == "NONE"
    assert grade["passed"] is True


@pytest.mark.parametrize(("success", "final"), [(True, "PASS"), (False, "FAIL")])
def test_process_exit_authority_disposition(success, final):
    grade = {"passed": success, "reasoning": "exit status"}
    disposition = apply_runtime_disposition(
        grade, _run_result("", exit_code=0 if success else 1), goal_text="g",
        verification_authority="process_exit",
    )
    assert disposition["final"] == final
    assert disposition["deterministic"] == final


def test_outcome_fields_only_when_present():
    assert runtime_evidence_outcome_fields({"passed": True}) == {}
    grade = {"disposition": {"final": "PASS"}, "evidence_packages": [{"model": "m"}]}
    assert runtime_evidence_outcome_fields(grade) == {
        "runtime_disposition": {"final": "PASS"}, "verifier_evidence": [{"model": "m"}],
    }


# --- capture loss accounting at the process boundary ------------------------


def test_process_controller_counts_lost_capture_characters():
    result = ProcessController(max_output_chars=100).run(
        ["python3", "-c", "print('x' * 1000)"], cwd=".", timeout=30,
    )
    assert result.stdout_truncated is True
    assert result.stdout_lost_chars == 1001 - 100
    assert result.to_dict()["stdout_lost_chars"] == 901


def test_process_result_dict_omits_lost_counts_when_nothing_lost():
    data = ProcessResult(returncode=0, stdout="a", stderr="", timeout=False).to_dict()
    assert "stdout_lost_chars" not in data and "stderr_lost_chars" not in data


# --- RunVerifierAgent.grade ---------------------------------------------------


def _verifier(*, primary_window=32768, fallback_window=None):
    cfg = AppConfig()
    llm = LLMClient(cfg)
    role_llm = LLMConfig(model="grader-primary", context_window=primary_window)
    chain = (
        [FallbackModelConfig(model="grader-small", context_window=fallback_window)]
        if fallback_window else []
    )
    return cfg, llm, RunVerifierAgent("run_verifier", llm, role_llm, chain)


@pytest.mark.asyncio
async def test_grade_prompt_is_bounded_by_the_selected_model_budget():
    cfg, llm, verifier = _verifier()
    llm.complete = AsyncMock(return_value=json.dumps({"verdict": "FAIL", "passed": False, "reasoning": "r"}))
    text = _huge_with_markers()
    grade = await verifier.grade(
        goal="g", success_criteria="c", output=text, returncode=1,
        evidence=RetainedRuntimeEvidence.from_run_result(_run_result(text, exit_code=1)),
    )
    system_prompt, user_prompt = llm.complete.call_args_list[0][0][:2]
    window_bytes = allocation_window(cfg, verifier.role_llm) * 4
    assert len((system_prompt + user_prompt).encode("utf-8")) <= window_bytes
    for marker in ("START_MARKER", "MIDDLE_MARKER", "END_MARKER"):
        assert marker in user_prompt
    assert grade["verdict"] == "FAIL"
    [package] = grade["evidence_packages"]
    assert package["model"] == "grader-primary" and package["answered"] is True
    assert package["package_truncated"] is True


@pytest.mark.asyncio
async def test_grade_rebuilds_the_package_for_a_smaller_fallback():
    _, llm, verifier = _verifier(primary_window=32768, fallback_window=8192)
    llm.complete = AsyncMock(side_effect=[
        RuntimeError("primary down"),
        json.dumps({"verdict": "PASS", "passed": True, "reasoning": "saw it"}),
    ])
    text = _FILLER_LINE * 20_000
    grade = await verifier.grade(
        goal="g", success_criteria="c", output=text, returncode=0,
        evidence=RetainedRuntimeEvidence.from_run_result(_run_result(text)),
    )
    primary, fallback = grade["evidence_packages"]
    assert primary["model"] == "grader-primary" and primary["answered"] is False
    assert fallback["model"] == "grader-small" and fallback["answered"] is True
    assert fallback["budget_bytes"] < primary["budget_bytes"]
    first_prompt = llm.complete.call_args_list[0][0][1]
    second_prompt = llm.complete.call_args_list[1][0][1]
    assert len(second_prompt) < len(first_prompt)
    assert grade["verdict"] == "PASS" and grade["passed"] is True


@pytest.mark.asyncio
async def test_grade_pass_over_capture_loss_is_unknown():
    _, llm, verifier = _verifier()
    llm.complete = AsyncMock(return_value=json.dumps({"verdict": "PASS", "passed": True, "reasoning": "ok"}))
    grade = await verifier.grade(
        goal="g", success_criteria="c", output="tail", returncode=0,
        evidence=RetainedRuntimeEvidence.from_run_result(_run_result("tail", lost=5_000)),
    )
    assert grade["passed"] is False
    assert grade["verdict"] == "UNKNOWN"
    assert grade["reason_code"] == CAPTURE_LOSS_UNRESOLVED
    assert grade["reasoning"].startswith(CAPTURE_LOSS_UNRESOLVED)


@pytest.mark.asyncio
async def test_grade_call_failure_is_unknown_not_pass():
    _, llm, verifier = _verifier()
    llm.complete = AsyncMock(side_effect=RuntimeError("down"))
    grade = await verifier.grade(goal="g", success_criteria="c", output="o", returncode=0)
    assert grade["passed"] is False
    assert grade["verdict"] == "UNKNOWN"
    assert grade["reason_code"] == VERIFIER_CALL_FAILED
    assert grade["evidence_packages"][0]["answered"] is False


@pytest.mark.asyncio
async def test_grade_malformed_response_is_unknown():
    _, llm, verifier = _verifier()
    llm.complete = AsyncMock(return_value="not json")
    grade = await verifier.grade(goal="g", success_criteria="c", output="o", returncode=0)
    assert grade["passed"] is False
    assert grade["verdict"] == "UNKNOWN"
    assert grade["reason_code"] == VERIFIER_RESULT_MALFORMED


@pytest.mark.asyncio
async def test_grade_legacy_passed_only_response_still_supported():
    _, llm, verifier = _verifier()
    llm.complete = AsyncMock(return_value=json.dumps({"passed": True, "reasoning": "fine"}))
    grade = await verifier.grade(goal="g", success_criteria="c", output="hello", returncode=0)
    assert grade["passed"] is True
    assert grade["verdict"] == "PASS"
    assert grade["reason_code"] == VERIFIER_CONFIRMED
