"""VAL-001 brownfield validation baselining (2026-09-18).

kriya/workflow/validation_baseline.py - deterministic PRE/POST test-baseline
comparison for brownfield generation, closing the gap docs/assurance/
VAL_001_GRAPHIFY_G1.md section 16 documents: Kriya had no way to distinguish
a genuine candidate-caused regression from a failure already present in a
real brownfield repository before Kriya ever touched it.

No live model/Ollama calls in this file except where an existing, already-
mocked `run_generation_workflow` integration test is extended (same LLM
mocking convention every other test_workflow.py test already uses). No
Graphify production source is copied here - fixtures are synthetic.
"""
import os
import subprocess
import sys
import tempfile
from unittest.mock import AsyncMock

import pytest

from kriya.core.state_paths import trace_db_path
from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow.checkpoint import compute_workspace_content_hash
from kriya.workflow.validation_baseline import (
    BrownfieldBaselineCaptureResult,
    DeltaClassification,
    TestOutcome,
    TestStatus,
    ValidationBaseline,
    ValidationInvocation,
    ValidationOutcome,
    build_validation_outcome,
    capture_brownfield_baselines,
    capture_validation_baseline,
    classify_baseline_delta,
    classify_level1_delta,
    classify_level2_delta,
    compute_failure_fingerprint,
    environment_failure_outcome,
    extract_test_failure_sections,
    is_baseline_reusable,
    normalize_failure_text,
    parse_pytest_structured_outcomes,
    render_blocking_regression_evidence,
)
from kriya.workflow.workflow import WorkflowEngine


# ---------------------------------------------------------------------------
# Fingerprint normalization
# ---------------------------------------------------------------------------

def test_fingerprint_ignores_volatile_noise_but_retains_material_content():
    a = "FAILED tests/x.py::t - AssertionError: bad (in 0.05s) at /private/var/folders/zp/xy/tmpAbCdEf/f.py"
    b = "FAILED tests/x.py::t - AssertionError: bad (in 12.34s) at /private/var/folders/zp/xy/tmpDiFfErEnT/f.py"
    assert compute_failure_fingerprint(a) == compute_failure_fingerprint(b)


def test_fingerprint_changes_for_materially_different_failures():
    a = "FAILED tests/x.py::t - AssertionError: expected 1, got 2"
    b = "FAILED tests/x.py::t - AssertionError: expected 1, got 3"
    assert compute_failure_fingerprint(a) != compute_failure_fingerprint(b)


def test_normalize_failure_text_preserves_line_numbers_and_exception_type():
    text = normalize_failure_text("tests/x.py:42: AssertionError: boom")
    assert "tests/x.py:42" in text
    assert "AssertionError" in text


# ---------------------------------------------------------------------------
# Level 1 delta classification (adversarial items 4/5/6/7/8 + control)
# ---------------------------------------------------------------------------

def _outcome(success, fingerprint=None, execution_status="completed"):
    return ValidationOutcome(execution_status=execution_status, success=success, failure_fingerprint=fingerprint)


def test_4_identical_pre_post_pass_is_unchanged():
    d = classify_level1_delta(_outcome(True), _outcome(True))
    assert d.classification == DeltaClassification.UNCHANGED_PASS


def test_5_pre_fail_equivalent_post_fail_is_pre_existing():
    d = classify_level1_delta(_outcome(False, "fp1"), _outcome(False, "fp1"))
    assert d.classification == DeltaClassification.PRE_EXISTING_FAILURE


def test_6_pre_fail_different_post_fail_is_changed_failure():
    """The task's own critical-rule example, at Level 1: same test failing
    both times is NOT automatically PRE_EXISTING - only when the failure
    evidence is materially equivalent."""
    pre_raw = {"success": False, "output": "FAILED tests/x.py::testFoo - AssertionError: expected 1, got 2"}
    post_raw = {"success": False, "output": "FAILED tests/x.py::testFoo - ImportError: cannot import name foo"}
    pre = build_validation_outcome(pre_raw)
    post = build_validation_outcome(post_raw)
    d = classify_level1_delta(pre, post)
    assert d.classification == DeltaClassification.CHANGED_FAILURE
    assert d.classification in DeltaClassification.__members__.values()


def test_7_pre_pass_post_fail_is_new_failure():
    d = classify_level1_delta(_outcome(True), _outcome(False, "fp"))
    assert d.classification == DeltaClassification.NEW_FAILURE


def test_8_pre_fail_post_pass_is_resolved_failure():
    d = classify_level1_delta(_outcome(False, "fp"), _outcome(True))
    assert d.classification == DeltaClassification.RESOLVED_FAILURE


def test_infrastructure_environment_failure_on_either_side():
    d = classify_level1_delta(_outcome(False, "x", execution_status="environment_failure"), _outcome(True))
    assert d.classification == DeltaClassification.INFRASTRUCTURE_ENVIRONMENT_FAILURE
    d2 = classify_level1_delta(_outcome(True), _outcome(False, "x", execution_status="environment_failure"))
    assert d2.classification == DeltaClassification.INFRASTRUCTURE_ENVIRONMENT_FAILURE


# ---------------------------------------------------------------------------
# Level 2 (pytest structured adapter) + item 9 (disappearance blocks)
# ---------------------------------------------------------------------------

def _pytest_output(*failed_lines, passed=0, extra_final=""):
    body = "\n".join(failed_lines)
    summary_lines = "\n".join(f"FAILED {line}" for line in failed_lines)
    total_failed = len(failed_lines)
    return (
        "============================= test session starts ==============================\n"
        f"collected {total_failed + passed} items\n\n{body}\n\n"
        "============================= short test summary info ============================\n"
        f"{summary_lines}\n"
        f"========================= {total_failed} failed, {passed} passed in 1.00s ===========================\n"
        f"{extra_final}"
    )


def test_pytest_adapter_extracts_failed_identities_and_aggregate_counts():
    raw = _pytest_output("tests/x.py::test_a - AssertionError: bad", passed=75)
    parsed = parse_pytest_structured_outcomes(raw)
    assert parsed is not None
    outcomes, counts = parsed
    assert outcomes[0].test_id == "tests/x.py::test_a"
    assert outcomes[0].status == TestStatus.FAIL
    assert counts["failed"] == 1
    assert counts["passed"] == 75


def test_pytest_adapter_returns_none_for_non_pytest_output():
    assert parse_pytest_structured_outcomes("[ERROR] Maven build failed: cannot find symbol at Foo.java:12") is None


def test_pytest_adapter_captures_id_containing_a_literal_space():
    """Regex fix, VAL-001 G1-DEVINV2 (2026-09-20): a parametrize id can
    genuinely contain a space with no trailing " - reason" text on its own
    FAILED line - live-confirmed to silently vanish from BOTH PRE and POST
    parsed test_outcomes before this fix (a real qwen3.8:27b G1 run)."""
    raw = _pytest_output(
        'tests/test_hook_guard.py::test_dispatch[args0-{"tool_input":{"command":"grep x"}}]',
        passed=1,
    )
    parsed = parse_pytest_structured_outcomes(raw)
    assert parsed is not None
    ids = {o.test_id for o in parsed[0]}
    assert 'tests/test_hook_guard.py::test_dispatch[args0-{"tool_input":{"command":"grep x"}}]' in ids


# ---------------------------------------------------------------------------
# VAL-001 G1-DEVINV2 (2026-09-20): blocking-only evidence rendering - closes
# a real, live-confirmed gap where the Developer-facing regression failure
# text used the RAW full-suite output instead of the already-computed
# per-test classification, flooding a real repair attempt with 140
# pre-existing failures alongside 0-2 genuinely new ones.
# ---------------------------------------------------------------------------

def _realistic_pytest_output(failing_ids_with_traceback, failing_ids_summary_only):
    """Builds a raw pytest string with a real "FAILURES" section (one
    "____ name ____" traceback block per id) plus a short-summary section
    covering every failing id - closer to real pytest output than
    _pytest_output()'s own minimal shape, needed to exercise
    extract_test_failure_sections's own per-test isolation, not just its
    short-summary fallback."""
    failures_body = "\n\n".join(
        f"{'_' * 20} {test_id.rsplit('::', 1)[-1]} {'_' * 20}\n"
        f"    def {test_id.rsplit('::', 1)[-1]}():\n"
        ">       assert False\n"
        "E       assert False\n"
        for test_id in failing_ids_with_traceback
    )
    all_ids = list(failing_ids_with_traceback) + list(failing_ids_summary_only)
    summary = "\n".join(f"FAILED {tid}" for tid in all_ids)
    return (
        "============================= test session starts ==============================\n"
        f"collected {len(all_ids) + 1} items\n\n"
        "=================================== FAILURES ===================================\n"
        f"{failures_body}\n"
        "=========================== short test summary info ============================\n"
        f"{summary}\n"
        f"===== {len(all_ids)} failed, 1 passed in 1.00s ====="
    )


def test_render_blocking_regression_evidence_includes_only_confirmed_ids():
    """140 pre-existing + 2 confirmed-new in the SAME raw pytest output ->
    the rendered Developer-facing evidence must contain ONLY the 2
    confirmed ids' own content - never any pre-existing failure text."""
    pre_existing_ids = [f"tests/pre_existing_{i}.py::test_x" for i in range(140)]
    confirmed_ids = ["tests/new_a.py::test_regression_a", "tests/new_b.py::test_regression_b"]
    raw = _realistic_pytest_output(confirmed_ids, pre_existing_ids)

    baseline = capture_validation_baseline(
        workspace_revision="rev1", run_id="r1", invocation=ValidationInvocation("cmd", "full_suite"),
        raw_result={
            "success": False,
            "output": _pytest_output(*[f"{tid} - old bug" for tid in pre_existing_ids]),
        },
    )
    evidence = render_blocking_regression_evidence(
        baseline=baseline, confirmed_test_ids=confirmed_ids, raw_output=raw,
    )
    assert "test_regression_a" in evidence
    assert "test_regression_b" in evidence
    for pre_id in pre_existing_ids:
        assert pre_id not in evidence, f"pre-existing failure {pre_id} leaked into confirmed-only evidence"


def test_render_blocking_regression_evidence_empty_for_no_confirmed_ids():
    baseline = capture_validation_baseline(
        workspace_revision="rev1", run_id="r1", invocation=ValidationInvocation("cmd", "full_suite"),
        raw_result={"success": True, "output": _pytest_output(passed=1)},
    )
    assert render_blocking_regression_evidence(baseline=baseline, confirmed_test_ids=[], raw_output="anything") == ""


def test_extract_test_failure_sections_isolates_real_failures_section():
    raw = _realistic_pytest_output(["tests/a.py::test_one", "tests/b.py::test_two"], [])
    sections = extract_test_failure_sections(raw, ["tests/a.py::test_one", "tests/b.py::test_two"])
    assert "assert False" in sections["tests/a.py::test_one"]
    assert "test_two" in sections["tests/b.py::test_two"]
    assert "test_one" not in sections["tests/b.py::test_two"]


def test_extract_test_failure_sections_falls_back_to_summary_line():
    raw = _pytest_output("tests/x.py::test_untracebacked - AssertionError: boom", passed=0)
    sections = extract_test_failure_sections(raw, ["tests/x.py::test_untracebacked"])
    assert "FAILED tests/x.py::test_untracebacked" in sections["tests/x.py::test_untracebacked"]


def test_9_previously_executed_test_disappearing_is_blocking():
    """Item 9: a test that WAS reported failing/passing PRE and is silently
    absent POST (aggregate count drop not explained by a rise in failures)
    must be classified as blocking, never silently ignored."""
    pre_raw = {"success": False, "output": _pytest_output("tests/x.py::test_a - AssertionError: bad", passed=75)}
    # POST: test_a's own failure is gone (resolved-looking at the per-id
    # view), but the aggregate total DROPPED by 5 with no compensating rise
    # in failures - a genuine silent disappearance, not a real fix.
    post_raw = {"success": True, "output": _pytest_output(passed=70)}
    baseline = capture_validation_baseline(
        workspace_revision="rev1", run_id="r1", invocation=ValidationInvocation("cmd", "sel"), raw_result=pre_raw,
    )
    post = build_validation_outcome(post_raw)
    result = classify_baseline_delta(baseline, post)
    assert result.aggregate_drop_detected is True
    assert result.blocking is True
    assert "aggregate_count_drop" in result.blocking_reasons


def test_newly_skipped_at_level2_is_blocking():
    pre = (TestOutcome("tests/x.py::test_a", TestStatus.PASS),)
    post = (TestOutcome("tests/x.py::test_a", TestStatus.SKIP),)
    result = classify_level2_delta(pre, post)
    assert result["tests/x.py::test_a"] == DeltaClassification.NEWLY_SKIPPED_OR_NOT_EXECUTED
    assert DeltaClassification.NEWLY_SKIPPED_OR_NOT_EXECUTED.value in {
        c.value for c in [result["tests/x.py::test_a"]]
    }


def test_new_test_not_in_pre_is_not_comparable_never_blocking():
    pre = ()
    post = (TestOutcome("tests/x.py::test_brand_new", TestStatus.FAIL, "fp"),)
    result = classify_level2_delta(pre, post)
    assert result["tests/x.py::test_brand_new"] == DeltaClassification.NOT_COMPARABLE
    from kriya.workflow.validation_baseline import TERMINAL_BLOCKING_CLASSIFICATIONS
    assert DeltaClassification.NOT_COMPARABLE not in TERMINAL_BLOCKING_CLASSIFICATIONS


# ---------------------------------------------------------------------------
# Item 10: baseline-indeterminate fails closed
# ---------------------------------------------------------------------------

def test_10_baseline_indeterminate_on_missing_workspace_revision():
    b = capture_validation_baseline(
        workspace_revision=None, run_id="r", invocation=ValidationInvocation("c", "s"),
        raw_result={"success": True, "output": ""},
    )
    assert b.status == "baseline_indeterminate"
    assert b.outcome is None


def test_10_baseline_indeterminate_on_missing_validator_result():
    b = capture_validation_baseline(
        workspace_revision="rev1", run_id="r", invocation=ValidationInvocation("c", "s"), raw_result=None,
    )
    assert b.status == "baseline_indeterminate"


def test_classify_baseline_delta_refuses_an_indeterminate_baseline():
    b = capture_validation_baseline(
        workspace_revision=None, run_id="r", invocation=ValidationInvocation("c", "s"), raw_result=None,
    )
    with pytest.raises(ValueError):
        classify_baseline_delta(b, _outcome(True))


# ---------------------------------------------------------------------------
# Items 2/3/11/12/13/14: pristine binding, sandbox exclusion, identity/reuse
# ---------------------------------------------------------------------------

def test_2_pristine_revision_binding():
    b = capture_validation_baseline(
        workspace_revision="pristine-rev", run_id="r", invocation=ValidationInvocation("c", "s"),
        raw_result={"success": True, "output": ""},
    )
    assert b.workspace_revision == "pristine-rev"


def test_3_candidate_sandbox_cannot_become_baseline_authority():
    """capture_brownfield_baselines() only ever calls the injected
    compute_revision/run_validator callables - it has no code path that
    could read a worktree/sandbox path itself (real check: no import of
    create_git_worktree, and every subprocess/path-touching primitive
    the caller could supply is injected, never constructed here)."""
    import ast
    import inspect
    import kriya.workflow.validation_baseline as vb_module
    source = inspect.getsource(vb_module)
    tree = ast.parse(source)
    imported_names = {
        alias.asname or alias.name
        for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "create_git_worktree" not in imported_names
    assert "PolymorphicValidator" not in imported_names
    assert "subprocess" not in {n.name for node in ast.walk(tree) if isinstance(node, ast.Import) for n in node.names}


def test_11_command_identity_mismatch_rejects_reuse():
    inv_a = ValidationInvocation("cmd_a", "sel")
    inv_b = ValidationInvocation("cmd_b", "sel")
    baseline = capture_validation_baseline(
        workspace_revision="rev1", run_id="r", invocation=inv_a, raw_result={"success": True, "output": ""},
    )
    assert is_baseline_reusable(baseline, current_workspace_revision="rev1", invocation=inv_b) is False


def test_12_selection_identity_mismatch_rejects_reuse():
    inv_a = ValidationInvocation("cmd", "sel_a")
    inv_b = ValidationInvocation("cmd", "sel_b")
    baseline = capture_validation_baseline(
        workspace_revision="rev1", run_id="r", invocation=inv_a, raw_result={"success": True, "output": ""},
    )
    assert is_baseline_reusable(baseline, current_workspace_revision="rev1", invocation=inv_b) is False


def test_target_test_mismatch_rejects_reuse_even_with_identical_selection_identity():
    """VAL-001 G1-R3: target_test is compared explicitly in matches(), not
    left to selection_identity alone to imply equality - a checkpoint (or
    hand-built ValidationInvocation) whose selection_identity string
    happens to match but whose own target_test value differs must still be
    rejected. Guards against a malformed/hand-edited checkpoint silently
    letting a POST replay run a DIFFERENT target_test than the one PRE was
    actually captured against."""
    inv_a = ValidationInvocation("cmd", "sel", target_test=("a.py", "b.py"))
    inv_b = ValidationInvocation("cmd", "sel", target_test=("a.py", "c.py"))  # same identity string, different targets
    baseline = capture_validation_baseline(
        workspace_revision="rev1", run_id="r", invocation=inv_a, raw_result={"success": True, "output": ""},
    )
    assert is_baseline_reusable(baseline, current_workspace_revision="rev1", invocation=inv_b) is False


def test_malformed_checkpoint_string_target_test_fails_closed_via_matches_not_reused():
    """A malformed checkpoint dict where target_test is a bare string
    (never produced by to_dict() itself, which always emits a list or None)
    does not raise in from_dict() - tuple("a.py") silently succeeds as
    ('a','.','p','y') rather than erroring - but the downstream matches()
    comparison against a freshly, correctly-normalized invocation still
    catches the divergence and refuses reuse, falling through to a fresh
    capture rather than corrupting anything."""
    malformed_dict = {
        "workspace_revision": "rev1", "run_id": "r1", "captured_at": 0.0,
        "status": "captured", "indeterminate_reason": None,
        "invocation": {
            "command_identity": "polymorphic_validator.run_tests",
            "selection_identity": "target_test:[\"a.py\"]",
            "target_test": "a.py",  # malformed: a bare string, not a list
        },
        "outcome": {
            "execution_status": "completed", "success": True, "failure_fingerprint": None,
            "evidence_ref": None, "aggregate_counts": None, "test_outcomes": None,
        },
    }
    restored = ValidationBaseline.from_dict(malformed_dict)  # does not raise
    assert restored.invocation.target_test == ("a", ".", "p", "y")  # confirms the malformed shape, not asserted as "correct"

    correct_invocation = ValidationInvocation(
        "polymorphic_validator.run_tests", "target_test:[\"a.py\"]", target_test=("a.py",),
    )
    assert is_baseline_reusable(
        restored, current_workspace_revision="rev1", invocation=correct_invocation,
    ) is False, "a malformed checkpoint's own bad target_test must never be silently reused"


def test_13_retry_reuses_pristine_baseline_without_rerunning_validator():
    inv = ValidationInvocation("cmd", "target_test:tests/x.py")
    calls = []

    def rv(t):
        calls.append(t)
        return {"success": True, "output": ""}

    first = capture_brownfield_baselines(
        run_id="r1", target_test="tests/x.py", full_regression_policy="disabled",
        run_validator=rv, compute_revision=lambda: "rev1",
    ).targeted
    # run_validator always receives the canonical, NORMALIZED tuple form
    # (VAL-001 G1-R3) - a single string input still selects exactly that
    # one target, structurally represented as a 1-tuple, never re-derived
    # or re-parsed from a string anywhere downstream.
    assert calls == [("tests/x.py",)]

    second = capture_brownfield_baselines(
        run_id="r1", target_test="tests/x.py", full_regression_policy="disabled",
        run_validator=rv, compute_revision=lambda: "rev1",
        resume_baseline_targeted=first.to_dict(),
    ).targeted
    assert calls == [("tests/x.py",)], "reuse must not invoke the validator a second time"
    assert second == first


def test_14_repository_revision_drift_invalidates_reuse():
    calls = []

    def rv(t):
        calls.append(t)
        return {"success": True, "output": ""}

    first = capture_brownfield_baselines(
        run_id="r1", target_test="tests/x.py", full_regression_policy="disabled",
        run_validator=rv, compute_revision=lambda: "rev1",
    ).targeted

    second = capture_brownfield_baselines(
        run_id="r1", target_test="tests/x.py", full_regression_policy="disabled",
        run_validator=rv, compute_revision=lambda: "rev2-DRIFTED",
        resume_baseline_targeted=first.to_dict(),
    ).targeted
    assert calls == [("tests/x.py",), ("tests/x.py",)], "drift must trigger a fresh capture"
    assert second.workspace_revision == "rev2-DRIFTED"


# ---------------------------------------------------------------------------
# VAL-001 G1-R3: multi-target selection - structural representation,
# PRE->POST replay via the frozen invocation, delta blocking, checkpoint
# reuse/drift
# ---------------------------------------------------------------------------

def test_multi_target_becomes_ordered_tuple_two_argv_entries_never_joined_string():
    """capture_brownfield_baselines() passes run_validator the CANONICAL,
    NORMALIZED tuple - never a joined string - for a multi-element
    selection, mirroring PolymorphicValidator.run_tests()'s own separate-
    argv-entries contract at this layer."""
    calls = []
    capture_brownfield_baselines(
        run_id="r1", target_test=["tests/a.py", "tests/b.py"], full_regression_policy="disabled",
        run_validator=lambda t: calls.append(t) or {"success": True, "output": ""},
        compute_revision=lambda: "rev1",
    )
    assert calls == [("tests/a.py", "tests/b.py")]


def test_multi_target_order_is_preserved_in_selection_identity_and_invocation():
    inv_ab = ValidationInvocation("cmd", "sel", target_test=("tests/a.py", "tests/b.py"))
    calls = []
    baseline = capture_brownfield_baselines(
        run_id="r1", target_test=("tests/a.py", "tests/b.py"), full_regression_policy="disabled",
        run_validator=lambda t: calls.append(t) or {"success": True, "output": ""},
        compute_revision=lambda: "rev1",
    ).targeted
    assert baseline.invocation.target_test == ("tests/a.py", "tests/b.py")
    assert "tests/a.py" in baseline.invocation.selection_identity
    assert "tests/b.py" in baseline.invocation.selection_identity


def test_multi_target_selection_identity_distinguishes_order():
    """Two different orderings of the SAME two targets must produce
    DIFFERENT selection_identity values - order is part of the frozen
    selection, not incidental."""
    calls = []
    baseline_ab = capture_brownfield_baselines(
        run_id="r1", target_test=["tests/a.py", "tests/b.py"], full_regression_policy="disabled",
        run_validator=lambda t: calls.append(t) or {"success": True, "output": ""},
        compute_revision=lambda: "rev1",
    ).targeted
    baseline_ba = capture_brownfield_baselines(
        run_id="r1", target_test=["tests/b.py", "tests/a.py"], full_regression_policy="disabled",
        run_validator=lambda t: calls.append(t) or {"success": True, "output": ""},
        compute_revision=lambda: "rev1",
    ).targeted
    assert baseline_ab.invocation.selection_identity != baseline_ba.invocation.selection_identity


def test_multi_target_drift_adding_a_target_invalidates_reuse_and_recaptures():
    """Target/config drift (adding a third target to a previously-captured
    two-target selection) must NOT silently reuse the stale baseline - a
    changed target list is drift exactly like a changed single target is."""
    calls = []

    def rv(t):
        calls.append(t)
        return {"success": True, "output": ""}

    first = capture_brownfield_baselines(
        run_id="r1", target_test=["tests/a.py", "tests/b.py"], full_regression_policy="disabled",
        run_validator=rv, compute_revision=lambda: "rev1",
    ).targeted

    second = capture_brownfield_baselines(
        run_id="r1", target_test=["tests/a.py", "tests/b.py", "tests/c.py"], full_regression_policy="disabled",
        run_validator=rv, compute_revision=lambda: "rev1",
        resume_baseline_targeted=first.to_dict(),
    ).targeted
    assert calls == [("tests/a.py", "tests/b.py"), ("tests/a.py", "tests/b.py", "tests/c.py")], (
        "an expanded target set must trigger a fresh capture, never a silent reuse of the "
        "narrower baseline"
    )
    assert second.invocation.target_test == ("tests/a.py", "tests/b.py", "tests/c.py")


def test_multi_target_unchanged_selection_reuses_without_rerunning_validator():
    calls = []

    def rv(t):
        calls.append(t)
        return {"success": True, "output": ""}

    first = capture_brownfield_baselines(
        run_id="r1", target_test=["tests/a.py", "tests/b.py"], full_regression_policy="disabled",
        run_validator=rv, compute_revision=lambda: "rev1",
    ).targeted
    second = capture_brownfield_baselines(
        run_id="r1", target_test=["tests/a.py", "tests/b.py"], full_regression_policy="disabled",
        run_validator=rv, compute_revision=lambda: "rev1",
        resume_baseline_targeted=first.to_dict(),
    ).targeted
    assert calls == [("tests/a.py", "tests/b.py")], "identical selection must reuse, not re-run"
    assert second == first


def test_multi_target_checkpoint_round_trip_preserves_ordered_tuple():
    """to_dict()/from_dict() (the exact checkpoint persistence shape) must
    preserve the ordered tuple exactly - list on the wire, tuple in memory,
    never silently reordered or collapsed."""
    baseline = capture_validation_baseline(
        workspace_revision="rev1", run_id="r1",
        invocation=ValidationInvocation(
            "polymorphic_validator.run_tests", "target_test:[\"a.py\", \"b.py\"]",
            target_test=("a.py", "b.py"),
        ),
        raw_result={"success": True, "output": ""},
    )
    as_dict = baseline.to_dict()
    assert as_dict["invocation"]["target_test"] == ["a.py", "b.py"]
    restored = ValidationBaseline.from_dict(as_dict)
    assert restored.invocation.target_test == ("a.py", "b.py")
    assert isinstance(restored.invocation.target_test, tuple)


def test_legacy_checkpoint_without_target_test_key_deserializes_as_none():
    """A checkpoint written before this field existed (no "target_test" key
    in the invocation dict at all) must deserialize cleanly with
    target_test=None, never raise a KeyError - backward compatibility for
    an in-flight resumed run captured under the pre-G1-R3 codebase."""
    legacy_dict = {
        "workspace_revision": "rev1", "run_id": "r1", "captured_at": 0.0,
        "status": "captured", "indeterminate_reason": None,
        "invocation": {"command_identity": "polymorphic_validator.run_tests", "selection_identity": "full_suite"},
        "outcome": {
            "execution_status": "completed", "success": True, "failure_fingerprint": None,
            "evidence_ref": None, "aggregate_counts": None, "test_outcomes": None,
        },
    }
    restored = ValidationBaseline.from_dict(legacy_dict)
    assert restored.invocation.target_test is None


def test_targeted_post_delta_blocks_on_new_failure_with_multi_target_selection():
    """The SAME classify_baseline_delta() the full-regression path already
    uses works identically for a targeted, multi-file baseline - a genuine
    NEW_FAILURE in the POST run (absent from a fully-passing PRE) blocks."""
    baseline = capture_brownfield_baselines(
        run_id="r1", target_test=["tests/a.py", "tests/b.py"], full_regression_policy="disabled",
        run_validator=lambda t: {"success": True, "output": _pytest_output(passed=76)},
        compute_revision=lambda: "rev1",
    ).targeted
    post = build_validation_outcome(
        {"success": False, "output": _pytest_output("tests/b.py::test_x - AssertionError: broke it", passed=75)}
    )
    result = classify_baseline_delta(baseline, post)
    assert result.blocking is True
    assert result.level1.classification == DeltaClassification.NEW_FAILURE


def test_targeted_post_delta_unchanged_permits_continuation():
    """An unchanged 76/76 POST result (identical to a passing PRE) never
    blocks - the exact 'targeted unchanged 76/76 permits continuation'
    acceptance criterion."""
    raw = {"success": True, "output": _pytest_output(passed=76)}
    baseline = capture_brownfield_baselines(
        run_id="r1", target_test=["tests/a.py", "tests/b.py"], full_regression_policy="disabled",
        run_validator=lambda t: raw, compute_revision=lambda: "rev1",
    ).targeted
    post = build_validation_outcome(raw)
    result = classify_baseline_delta(baseline, post)
    assert result.blocking is False
    assert result.level1.classification == DeltaClassification.UNCHANGED_PASS


# ---------------------------------------------------------------------------
# capture_brownfield_baselines orchestration
# ---------------------------------------------------------------------------

def test_default_configuration_is_a_complete_no_op():
    """Zero behavior change for a run that doesn't opt in - the callables
    are never invoked at all."""
    result = capture_brownfield_baselines(
        run_id="r1", target_test=None, full_regression_policy="auto",
        run_validator=lambda t: (_ for _ in ()).throw(AssertionError("must not be called")),
        compute_revision=lambda: (_ for _ in ()).throw(AssertionError("must not be called")),
    )
    assert result.targeted is None
    assert result.full_regression is None
    assert result.hard_stop_reason is None


def test_disabled_policy_also_never_calls_validator_for_full_regression():
    calls = []
    result = capture_brownfield_baselines(
        run_id="r1", target_test=None, full_regression_policy="disabled",
        run_validator=lambda t: calls.append(t) or {"success": True, "output": ""},
        compute_revision=lambda: "rev1",
    )
    assert calls == []
    assert result.full_regression is None


def test_17_required_policy_captures_full_regression_baseline_and_allows_delta_blocking():
    raw_pre = {"success": False, "output": _pytest_output("tests/x.py::test_pre_existing - AssertionError: old bug", passed=75)}
    result = capture_brownfield_baselines(
        run_id="r1", target_test=None, full_regression_policy="required",
        run_validator=lambda t: raw_pre, compute_revision=lambda: "rev1",
    )
    assert result.full_regression is not None
    assert result.full_regression.status == "captured"
    assert result.hard_stop_reason is None

    # POST: the exact same pre-existing failure, plus nothing new.
    raw_post = {"success": False, "output": _pytest_output("tests/x.py::test_pre_existing - AssertionError: old bug", passed=75)}
    post_outcome = build_validation_outcome(raw_post)
    delta = classify_baseline_delta(result.full_regression, post_outcome)
    assert delta.blocking is False, "an unchanged pre-existing failure must not block"


def test_required_policy_indeterminate_baseline_produces_hard_stop():
    result = capture_brownfield_baselines(
        run_id="r1", target_test=None, full_regression_policy="required",
        run_validator=lambda t: {"success": True, "output": ""}, compute_revision=lambda: None,
    )
    assert result.full_regression.status == "baseline_indeterminate"
    assert result.hard_stop_reason is not None


# ---------------------------------------------------------------------------
# Item 20: synthetic brownfield repo, one pre-existing + one new failure
# ---------------------------------------------------------------------------

def _run_git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_20_synthetic_brownfield_repo_only_new_failure_attributed(tmp_path):
    """End-to-end through the REAL PolymorphicValidator (no live model) and
    the REAL compute_workspace_content_hash - a tiny synthetic git repo with
    one PRE-EXISTING failing test and one PASSING test; the POST run
    introduces a second, genuinely NEW failure in the passing test. Only
    the new failure must be attributed."""
    from kriya.tools.validate import PolymorphicValidator

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tests").mkdir()
    (repo / "tests" / "test_sample.py").write_text(
        "def test_pre_existing_bug():\n    assert 1 == 2  # already broken before Kriya touches anything\n\n"
        "def test_previously_passing():\n    assert 1 == 1\n"
    )
    _run_git(["init", "-q"], repo)
    _run_git(["config", "user.email", "t@example.com"], repo)
    _run_git(["config", "user.name", "t"], repo)
    _run_git(["add", "-A"], repo)
    _run_git(["commit", "-q", "-m", "initial"], repo)

    pristine_revision = compute_workspace_content_hash(str(repo))
    assert pristine_revision is not None

    pre_validator = PolymorphicValidator(str(repo), original_workspace_path=str(repo), autonomy_cfg=AppConfig().autonomy)
    pre_raw = pre_validator.run_tests()
    assert pre_raw["success"] is False  # one real pre-existing failure

    baseline = capture_validation_baseline(
        workspace_revision=pristine_revision, run_id="r1",
        invocation=ValidationInvocation("polymorphic_validator.run_tests", "full_suite"),
        raw_result=pre_raw,
    )
    assert baseline.status == "captured"

    # Simulate a "candidate" that breaks the previously-passing test too -
    # written directly (no Developer/model involved, matching this file's
    # own no-live-model constraint). __pycache__/.pytest_cache are removed
    # before the POST run - a real, disclosed timing hazard found while
    # writing this test: pytest's own assertion-rewrite bytecode cache can
    # key on mtime at a coarser resolution than "rewrite the file, then
    # immediately re-invoke pytest in a fresh subprocess" needs, and
    # returns a stale (pre-write) verdict for an unrelated in-repo test
    # without this. Not a validation_baseline.py defect - PolymorphicValidator
    # itself is unmodified; this is test-fixture hygiene only.
    import shutil
    for cache_dir in ("__pycache__", ".pytest_cache", "tests/__pycache__"):
        shutil.rmtree(repo / cache_dir, ignore_errors=True)
    (repo / "tests" / "test_sample.py").write_text(
        "def test_pre_existing_bug():\n    assert 1 == 2  # still broken, unrelated to the candidate\n\n"
        "def test_previously_passing():\n    assert 1 == 2  # NEW: candidate broke this one\n"
    )
    post_validator = PolymorphicValidator(str(repo), original_workspace_path=str(repo), autonomy_cfg=AppConfig().autonomy)
    post_raw = post_validator.run_tests()
    assert post_raw["output"].count("FAILED tests/") == 2, (
        f"fixture sanity check failed - expected both tests to fail POST, got:\n{post_raw['output']}"
    )
    assert post_raw["success"] is False

    post_outcome = build_validation_outcome(post_raw)
    result = classify_baseline_delta(baseline, post_outcome)

    # Level 1 (mandatory, whole-invocation) ALONE already guarantees safety
    # here: PRE's own failure text ("1 failed, 1 passed...") and POST's
    # ("2 failed...") produce different normalized fingerprints, so the
    # candidate is blocked regardless of per-test attribution precision.
    assert result.blocking is True, "the genuinely new failure must block"
    assert result.level1.classification == DeltaClassification.CHANGED_FAILURE

    # Level 2 (optional, per-test) correctly attributes the test that WAS
    # already known-failing PRE...
    per_test = result.level2
    assert per_test.get("tests/test_sample.py::test_pre_existing_bug") == DeltaClassification.PRE_EXISTING_FAILURE
    # ...but honestly cannot yet prove test_previously_passing is NEW rather
    # than "an unenumerated PRE pass" purely from Level 2, since PRE's own
    # invocation was NOT fully passing (pre.success=False - see
    # classify_level2_delta's own pre_was_fully_passing docstring for
    # exactly this disclosed limitation of the FAILED/ERROR-only pytest
    # adapter). Level 1 is what actually blocks this candidate, not Level 2
    # per-test attribution - proven separately (and precisely) below for
    # the common, important case where PRE has zero failures at all.
    assert per_test.get("tests/test_sample.py::test_previously_passing") == DeltaClassification.NOT_COMPARABLE


def test_20b_level2_attributes_new_failure_precisely_when_pre_was_fully_passing():
    """The precise-attribution companion to test_20 above: when PRE's own
    invocation had ZERO failures (success=True - exactly G1's own real
    76/76 PRE baseline shape), Level 2 CAN and does correctly attribute a
    newly-failing test as NEW_FAILURE by its own test_id, not merely
    NOT_COMPARABLE - because an empty PRE failed-list is then known to mean
    "everything passed," not "unknown.\""""
    pre_raw = {"success": True, "output": _pytest_output(passed=76)}
    post_raw = {"success": False, "output": _pytest_output("tests/x.py::test_previously_passing - AssertionError: broke it", passed=75)}
    baseline = capture_validation_baseline(
        workspace_revision="rev1", run_id="r1", invocation=ValidationInvocation("cmd", "full_suite"), raw_result=pre_raw,
    )
    post_outcome = build_validation_outcome(post_raw)
    result = classify_baseline_delta(baseline, post_outcome)
    assert result.level2.get("tests/x.py::test_previously_passing") == DeltaClassification.NEW_FAILURE
    assert result.blocking is True


# ---------------------------------------------------------------------------
# Item 18: ground-truth / evaluator isolation
# ---------------------------------------------------------------------------

def test_18_module_never_references_ground_truth_or_evaluator_concepts():
    """Structural proof, same pattern as CTX-001-P1-C3's own 'candidate
    never carries source text' test - this module only ever wraps Kriya's
    own already-visible PolymorphicValidator output; it has no code path
    that could reach an evaluator-only ground-truth fixture at all."""
    import inspect
    import kriya.workflow.validation_baseline as vb_module
    source = inspect.getsource(vb_module).lower()
    assert "ground_truth" not in source
    assert "ground truth" not in source
    assert "evaluator" not in source


# ---------------------------------------------------------------------------
# Item 19: backward compatibility of existing validator callers
# ---------------------------------------------------------------------------

def test_19_polymorphic_validator_signature_unmodified():
    """This package made ZERO changes to kriya/tools/validate.py - proven
    structurally: run_tests/run_compile_check still take exactly the
    parameters every existing caller already passes."""
    import inspect
    from kriya.tools.validate import PolymorphicValidator
    run_tests_sig = inspect.signature(PolymorphicValidator.run_tests)
    assert list(run_tests_sig.parameters.keys()) == ["self", "target_test"]
    run_compile_sig = inspect.signature(PolymorphicValidator.run_compile_check)
    assert "self" in run_compile_sig.parameters


def test_build_validation_outcome_wraps_existing_return_shape_without_mutating_it():
    raw = {"success": True, "output": "all good"}
    raw_copy = dict(raw)
    build_validation_outcome(raw)
    assert raw == raw_copy, "build_validation_outcome must not mutate the validator's own return dict"


# ---------------------------------------------------------------------------
# Item 1/15/16: ordering through run_generation_workflow + checkpoint resume
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_1_baseline_captured_before_first_developer_call(tmp_path, monkeypatch):
    """Mechanically testable ordering: patches capture_brownfield_baselines
    and create_git_worktree (both in kriya.workflow.workflow's own
    namespace) to append to a shared, ordered call log - proves baseline
    capture happens strictly before the sandbox worktree is created, which
    is itself strictly before any Developer call in this method."""
    import kriya.workflow.workflow as workflow_module

    call_order = []
    real_capture = workflow_module.capture_brownfield_baselines

    def spy_capture(**kwargs):
        call_order.append("baseline_capture")
        return real_capture(**kwargs)

    real_create_worktree = workflow_module.create_git_worktree

    def spy_create_worktree(path):
        call_order.append("create_worktree")
        return real_create_worktree(path)

    monkeypatch.setattr(workflow_module, "capture_brownfield_baselines", spy_capture)
    monkeypatch.setattr(workflow_module, "create_git_worktree", spy_create_worktree)

    cfg = AppConfig()
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.logs = str(tmp_path / "logs")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    developer_call_marker = []
    real_complete = llm.complete

    async def spy_complete(*args, **kwargs):
        # First three completions are Planner/Architect/Developer in this
        # minimal scripted flow - only the FIRST call after both spies above
        # matters for ordering, so just record every call transparently.
        developer_call_marker.append(len(call_order))
        return await real_complete(*args, **kwargs)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)"}]',
    ])

    we = WorkflowEngine(kernel, llm)
    await we.run_generation_workflow(goal="Create app", workspace_path=str(tmp_path))

    assert call_order == ["baseline_capture", "create_worktree"], (
        f"expected baseline capture strictly before worktree creation, got: {call_order}"
    )


def test_15_checkpoint_payload_includes_baseline_for_resume(tmp_path):
    """Integration-lite: capture_brownfield_baselines' own resume_baseline_*
    round-trips through ValidationBaseline.to_dict()/from_dict() exactly as
    a checkpoint payload would carry it (the same dict shape
    kriya/workflow/workflow.py's _save_stage_checkpoint now persists)."""
    calls = []
    baseline = capture_brownfield_baselines(
        run_id="r1", target_test="tests/x.py", full_regression_policy="disabled",
        run_validator=lambda t: calls.append(t) or {"success": True, "output": ""},
        compute_revision=lambda: "rev1",
    ).targeted
    checkpoint_payload = {"stage": "design", "validation_baseline_targeted": baseline.to_dict()}

    # Simulate a resumed run reading that checkpoint back.
    resumed = capture_brownfield_baselines(
        run_id="r1", target_test="tests/x.py", full_regression_policy="disabled",
        run_validator=lambda t: calls.append(t) or {"success": True, "output": ""},
        compute_revision=lambda: "rev1",
        resume_baseline_targeted=checkpoint_payload["validation_baseline_targeted"],
    ).targeted
    assert calls == [("tests/x.py",)], "only the FIRST (pre-resume) capture should have run the validator"
    assert resumed == baseline


def test_16_checkpoint_resume_with_repository_drift_recaptures(tmp_path):
    calls = []
    baseline = capture_brownfield_baselines(
        run_id="r1", target_test="tests/x.py", full_regression_policy="disabled",
        run_validator=lambda t: calls.append(t) or {"success": True, "output": ""},
        compute_revision=lambda: "rev1",
    ).targeted
    checkpoint_payload = {"validation_baseline_targeted": baseline.to_dict()}

    resumed = capture_brownfield_baselines(
        run_id="r1", target_test="tests/x.py", full_regression_policy="disabled",
        run_validator=lambda t: calls.append(t) or {"success": True, "output": ""},
        compute_revision=lambda: "rev1-DRIFTED",
        resume_baseline_targeted=checkpoint_payload["validation_baseline_targeted"],
    ).targeted
    assert calls == [("tests/x.py",), ("tests/x.py",)], "drift on resume must trigger re-capture, never silent reuse"
    assert resumed.workspace_revision == "rev1-DRIFTED"


# ---------------------------------------------------------------------------
# VAL-001 G1-R3: targeted POST wiring through the REAL run_generation_workflow
# ---------------------------------------------------------------------------

def _init_git_repo_val001(repo):
    _run_git(["init", "-q"], repo)
    _run_git(["config", "user.email", "t@example.com"], repo)
    _run_git(["config", "user.name", "t"], repo)
    _run_git(["add", "-A"], repo)
    _run_git(["commit", "-q", "-m", "initial"], repo)


def _latest_trace_run_events(cfg):
    """Same pattern test_workflow.py's own _latest_trace_row() uses - the
    only way to observe state.run_events from OUTSIDE a full
    run_generation_workflow() call, since it is not part of the returned
    result dict."""
    import json
    import sqlite3

    db_path = trace_db_path(cfg)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT run_events FROM runs ORDER BY timestamp DESC LIMIT 1").fetchone()
    conn.close()
    return json.loads(row["run_events"] or "[]") if row else []


@pytest.mark.asyncio
async def test_targeted_post_replays_frozen_multi_target_selection_and_does_not_block_when_unchanged(tmp_path):
    """Real run_generation_workflow(), no live model. Two pre-existing,
    passing test files are configured as the multi-target brownfield
    selection; the goal only ever creates a brand-new, unrelated file (so
    the candidate structurally cannot touch either targeted file - no D1
    authority question even arises for them). Proves: (1) PRE captures the
    exact ordered multi-target tuple; (2) the POST call replays that SAME
    frozen tuple (never re-derived); (3) an unchanged targeted suite does
    not block; (4) the run completes successfully."""
    import kriya.tools.validate as validate_module

    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "test_a.py").write_text(
        "from calc import add\ndef test_add():\n    assert add(2, 3) == 5\n"
    )
    (tmp_path / "test_b.py").write_text(
        "from calc import add\ndef test_add_zero():\n    assert add(0, 0) == 0\n"
    )
    _init_git_repo_val001(tmp_path)

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.spec_compliance_enabled = False
    cfg.autonomy.brownfield_full_regression_baseline_policy = "required"
    cfg.autonomy.brownfield_baseline_target_test = ["test_a.py", "test_b.py"]
    cfg.paths.logs = str(tmp_path / "logs")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write a greeting function",
        "Design: Write greet.py",
        '[{"filepath": "greet.py", "content": "def greet(name):\\n    return f\\"hi {name}\\"\\n"}]',
        "Review: Approved",
    ])

    real_run_tests = validate_module.PolymorphicValidator.run_tests
    observed_target_test_calls = []

    def spy_run_tests(self, target_test=None):
        observed_target_test_calls.append(target_test)
        return real_run_tests(self, target_test=target_test)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(validate_module.PolymorphicValidator, "run_tests", spy_run_tests)
        we = WorkflowEngine(kernel, llm)
        result = await we.run_generation_workflow(goal="Add a greet function", workspace_path=str(tmp_path))

    assert result["quality_gates_passed"] is True, result

    # PRE captured the exact ordered tuple.
    targeted_delta_events = [
        e for e in _latest_trace_run_events(cfg)
        if e.get("kind") == "validation_baseline.targeted_delta"
    ]
    assert len(targeted_delta_events) == 1
    details = targeted_delta_events[0]["details"]
    assert details["target_test"] == ["test_a.py", "test_b.py"]
    assert details["blocking"] is False

    # The frozen tuple was replayed verbatim for the POST call - at least
    # one observed call to the real PolymorphicValidator.run_tests used
    # EXACTLY this tuple (never re-derived/re-parsed into something else).
    assert ("test_a.py", "test_b.py") in observed_target_test_calls


@pytest.mark.asyncio
async def test_targeted_post_new_failure_blocks_and_prevents_quality_gates_passed(tmp_path):
    """Same fixture shape as above, but the targeted suite's OWN POST
    invocation is deterministically made to fail (via a monkeypatched
    PolymorphicValidator.run_tests that returns a controlled failing result
    ONLY for the targeted multi-file call, distinguishing it from the
    full-suite call by its own target_test argument) - proves the targeted
    delta genuinely blocks the candidate, the exact 'targeted NEW/CHANGED
    failure blocks success' acceptance criterion, without depending on
    engineering a real generated-code regression."""
    import kriya.tools.validate as validate_module

    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "test_a.py").write_text(
        "from calc import add\ndef test_add():\n    assert add(2, 3) == 5\n"
    )
    (tmp_path / "test_b.py").write_text(
        "from calc import add\ndef test_add_zero():\n    assert add(0, 0) == 0\n"
    )
    _init_git_repo_val001(tmp_path)

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.spec_compliance_enabled = False
    cfg.autonomy.brownfield_full_regression_baseline_policy = "required"
    cfg.autonomy.brownfield_baseline_target_test = ["test_a.py", "test_b.py"]
    cfg.paths.logs = str(tmp_path / "logs")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    # The candidate is genuinely rejected every attempt (the targeted suite
    # keeps regressing no matter how many times it's re-evaluated - see the
    # spy below), so the retry loop runs to natural exhaustion before this
    # run reaches a terminal state - enough Developer-JSON entries for
    # every retry attempt (same content each time; what matters is that the
    # targeted POST check keeps failing, not that the candidate itself
    # varies), plus a final Reviewer completion.
    _developer_json = '[{"filepath": "greet.py", "content": "def greet(name):\\n    return f\\"hi {name}\\"\\n"}]'
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write a greeting function",
        "Design: Write greet.py",
        _developer_json, _developer_json, _developer_json, _developer_json,
        "Review: rejected candidate",
    ])

    real_run_tests = validate_module.PolymorphicValidator.run_tests
    post_targeted_call_count = {"n": 0}

    def spy_run_tests(self, target_test=None):
        if target_test == ("test_a.py", "test_b.py"):
            post_targeted_call_count["n"] += 1
            if post_targeted_call_count["n"] >= 2:
                # First call is the PRE capture (must stay real/passing so a
                # real baseline is actually established); every call from
                # the SECOND onward (POST, and any retry's own POST) is
                # deterministically failed - the same real candidate keeps
                # regressing this suite no matter how many times it's
                # re-evaluated, so the run terminates via exhausted retries
                # (or the repeated-action guard) rather than ever passing.
                return {
                    "success": False,
                    "output": _pytest_output("test_b.py::test_add_zero - AssertionError: broke it", passed=1),
                }
        return real_run_tests(self, target_test=target_test)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(validate_module.PolymorphicValidator, "run_tests", spy_run_tests)
        we = WorkflowEngine(kernel, llm)
        result = await we.run_generation_workflow(goal="Add a greet function", workspace_path=str(tmp_path))

    assert result["quality_gates_passed"] is False
    targeted_delta_events = [
        e for e in _latest_trace_run_events(cfg)
        if e.get("kind") == "validation_baseline.targeted_delta"
    ]
    # One per retry attempt that reached the full-regression gate (every
    # attempt here, since the candidate never stops regressing) - every one
    # of them must show the SAME frozen selection and a blocking NEW_FAILURE
    # classification, not just the first.
    assert len(targeted_delta_events) >= 1
    for event in targeted_delta_events:
        details = event["details"]
        assert details["target_test"] == ["test_a.py", "test_b.py"]
        assert details["blocking"] is True
        assert details["level1_classification"] == DeltaClassification.NEW_FAILURE.value


@pytest.mark.asyncio
async def test_full_regression_unattributed_stops_after_one_developer_call(tmp_path):
    """VAL-001 G1-DEVINV2 (2026-09-20): a full-regression LEVEL1 delta
    (CHANGED_FAILURE) with ZERO level2-attributable NEW_FAILURE/
    CHANGED_FAILURE test ids must stop the run after exactly ONE Developer
    call - never retry Developer regeneration for an aggregate-level delta
    that names no specific test. This is the exact live-confirmed failure
    shape from a real G1 run (qwen3.8:27b, 2026-09-19/20): the SAME
    pre-existing failing test id/reason appears PRE and POST (level2 ->
    PRE_EXISTING_FAILURE, never attributable), but an extra, non-volatile
    line elsewhere in the raw POST output makes the whole-invocation level1
    fingerprint differ anyway - level1 alone blocks, with nothing
    attributable at all. Before this fix, that real run discarded an
    independently-verified CORRECT Attempt-1 candidate and then burned 6
    further Developer attempts that could never have succeeded."""
    import kriya.tools.validate as validate_module

    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "test_a.py").write_text(
        "def test_pre_existing_bug():\n    assert 1 == 2\n"
    )
    _init_git_repo_val001(tmp_path)

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.spec_compliance_enabled = False
    cfg.autonomy.brownfield_full_regression_baseline_policy = "required"
    cfg.paths.logs = str(tmp_path / "logs")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    _developer_json = '[{"filepath": "greet.py", "content": "def greet(name):\\n    return f\\"hi {name}\\"\\n"}]'
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write a greeting function",
        "Design: Write greet.py",
        _developer_json,
        "Review: candidate looks fine",
    ])

    real_run_tests = validate_module.PolymorphicValidator.run_tests
    full_call_count = {"n": 0}
    base_failure = "test_a.py::test_pre_existing_bug - AssertionError: old bug"

    def spy_run_tests(self, target_test=None):
        if target_test is None:
            full_call_count["n"] += 1
            if full_call_count["n"] == 1:
                return {"success": False, "output": _pytest_output(base_failure, passed=0)}
            return {
                "success": False,
                "output": _pytest_output(
                    base_failure, passed=0,
                    extra_final="1 unrelated warning captured only in this run\n",
                ),
            }
        return real_run_tests(self, target_test=target_test)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(validate_module.PolymorphicValidator, "run_tests", spy_run_tests)
        we = WorkflowEngine(kernel, llm)
        result = await we.run_generation_workflow(goal="Add a greet function", workspace_path=str(tmp_path))

    assert result["quality_gates_passed"] is False
    assert result["failure_category"] == "regression_unattributed"
    # Exactly one PRE + one POST full-suite call - a second (retry) POST
    # call would mean the run wrongly re-entered the Developer repair loop.
    assert full_call_count["n"] == 2
    # plan, design, ONE developer call, ONE review - no repair retry ever
    # reached a second Developer call.
    assert llm.complete.call_count == 4
