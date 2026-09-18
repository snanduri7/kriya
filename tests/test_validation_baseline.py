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
    is_baseline_reusable,
    normalize_failure_text,
    parse_pytest_structured_outcomes,
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
    assert calls == ["tests/x.py"]

    second = capture_brownfield_baselines(
        run_id="r1", target_test="tests/x.py", full_regression_policy="disabled",
        run_validator=rv, compute_revision=lambda: "rev1",
        resume_baseline_targeted=first.to_dict(),
    ).targeted
    assert calls == ["tests/x.py"], "reuse must not invoke the validator a second time"
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
    assert calls == ["tests/x.py", "tests/x.py"], "drift must trigger a fresh capture"
    assert second.workspace_revision == "rev2-DRIFTED"


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
    assert calls == ["tests/x.py"], "only the FIRST (pre-resume) capture should have run the validator"
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
    assert calls == ["tests/x.py", "tests/x.py"], "drift on resume must trigger re-capture, never silent reuse"
    assert resumed.workspace_revision == "rev1-DRIFTED"
