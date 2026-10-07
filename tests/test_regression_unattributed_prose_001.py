"""REGRESSION-UNATTRIBUTED-PROSE-001: the unattributed-regression stop states what the delta established, and
regression PASS/FAIL is decided from structured, attributable evidence only - prose never decides.

Measured (blind cohort T1/T5, 2026-10-07): PRE and POST pytest evidence both incomplete, whole-output fingerprints
equal (level1 PRE_EXISTING_FAILURE), POST suite failing; the stop reason still said "the full-regression suite's
aggregate outcome changed relative to the captured PRE-mutation baseline". The decision (fail closed) was right; the
sentence was not. The message is now derived from ``BaselineDeltaResult.blocking_reasons`` and the fingerprints.
"""
import ast
import pathlib

import pytest
from _reg_r1_fixture import validator_for, write_project
from test_reg_r1_pytest_regression_authority import KEY, PASSED, F, decide, make_baseline, raw_result

from kriya.workflow import validation_baseline as vb
from kriya.workflow.validation_baseline import (
    ATTRIBUTION_AGGREGATE_DELTA,
    ATTRIBUTION_EVIDENCE_INCOMPLETE,
    ATTRIBUTION_PER_TEST_UNATTRIBUTABLE,
    BaselineDeltaResult,
    DeltaClassification,
    Level1Delta,
    build_validation_outcome,
    classify_baseline_delta,
    regression_unattributed_diagnosis,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
CHANGED = "the full-regression suite's aggregate outcome changed"


def delta(reasons, *, level1=DeltaClassification.PRE_EXISTING_FAILURE, pre="fp", post="fp", status=None):
    return BaselineDeltaResult(level1=Level1Delta(level1, pre, post), level2={}, aggregate_drop_detected=False,
                               blocking=True, blocking_reasons=tuple(reasons), pytest_evidence_status=status)


# ---------------------------------------------------------------- the message states what the delta established
def test_01_incomplete_evidence_on_both_sides_with_equal_fingerprints_never_claims_an_aggregate_change():
    attribution, message = regression_unattributed_diagnosis(delta(
        ["pytest_evidence_incomplete:PRE:PYTEST_SESSION_INCOMPLETE:exit_2; POST:PYTEST_SESSION_INCOMPLETE:exit_2"],
        status="PRE:PYTEST_SESSION_INCOMPLETE:exit_2; POST:PYTEST_SESSION_INCOMPLETE:exit_2"))
    assert attribution == ATTRIBUTION_EVIDENCE_INCOMPLETE
    assert message.startswith("REGRESSION_UNATTRIBUTED: ") and CHANGED not in message
    assert "per-test evidence is incomplete (PRE:PYTEST_SESSION_INCOMPLETE:exit_2; POST:PYTEST_SESSION_INCOMPLETE:exit_2)" in message
    assert "fingerprints of the PRE-mutation baseline and this candidate are equal (level1=PRE_EXISTING_FAILURE)" in message
    assert "failed closed" in message


def test_02_incomplete_evidence_with_different_fingerprints_says_so_without_claiming_attribution():
    attribution, message = regression_unattributed_diagnosis(delta(
        ["pytest_evidence_incomplete:POST:JUNIT_COUNTS_INCONSISTENT"], level1=DeltaClassification.CHANGED_FAILURE,
        pre="a", post="b"))
    assert attribution == ATTRIBUTION_EVIDENCE_INCOMPLETE
    assert "are different (level1=CHANGED_FAILURE)" in message and CHANGED not in message


def test_03_an_aggregate_level1_block_keeps_the_aggregate_wording():
    attribution, message = regression_unattributed_diagnosis(delta(
        ["level1:CHANGED_FAILURE"], level1=DeltaClassification.CHANGED_FAILURE, pre="a", post="b"))
    assert attribution == ATTRIBUTION_AGGREGATE_DELTA
    assert message.startswith(f"REGRESSION_UNATTRIBUTED: {CHANGED}") and "(level1=CHANGED_FAILURE)" in message


def test_04_per_test_or_count_drop_blocks_name_their_reasons():
    attribution, message = regression_unattributed_diagnosis(delta(
        ["level2:tests.t::test_x:NOT_COMPARABLE", "aggregate_count_drop"]))
    assert attribution == ATTRIBUTION_PER_TEST_UNATTRIBUTABLE
    assert "level2:tests.t::test_x:NOT_COMPARABLE; aggregate_count_drop" in message and CHANGED not in message
    # evidence-incomplete alongside a structural reason: the structural reason is what blocks
    attribution, _ = regression_unattributed_diagnosis(delta(
        ["pytest_evidence_incomplete:POST:X", "level1:NEW_FAILURE"], level1=DeltaClassification.NEW_FAILURE))
    assert attribution == ATTRIBUTION_AGGREGATE_DELTA


def test_05_the_cohort_shape_through_the_real_comparator():
    """T5's mechanism: both sides incomplete (session incomplete), identical output text, POST not passing."""
    base = make_baseline({KEY: F()})
    base = vb.ValidationBaseline(workspace_revision=base.workspace_revision, run_id=base.run_id, captured_at=0.0,
                                 outcome=build_validation_outcome(raw_result({KEY: F()}, complete=False)),
                                 invocation=base.invocation)
    post = build_validation_outcome(raw_result({KEY: F()}, complete=False))
    result = classify_baseline_delta(base, post)
    assert result.blocking and result.level1.classification is DeltaClassification.PRE_EXISTING_FAILURE
    assert result.level1.pre_fingerprint == result.level1.post_fingerprint
    assert all(r.startswith("pytest_evidence_incomplete:") for r in result.blocking_reasons)
    attribution, message = regression_unattributed_diagnosis(result)
    assert attribution == ATTRIBUTION_EVIDENCE_INCOMPLETE and CHANGED not in message and "are equal" in message


# ---------------------------------------------------------------- prose never decides; structure always does
def test_06_structured_failure_beats_console_text_claiming_success():
    # POST evidence: a failing case; the console text says the suite passed
    passed_text = raw_result({KEY: F()}, success=False)
    passed_text["output"] = "=== test session starts ===\n2 passed in 0.1s\n"
    result = classify_baseline_delta(make_baseline({KEY: PASSED}), build_validation_outcome(passed_text))
    assert result.blocking and result.authority == vb.PYTEST_PER_TEST_AUTHORITY
    assert result.level2[KEY] is DeltaClassification.NEW_FAILURE


def test_07_console_text_claiming_success_without_authoritative_evidence_is_not_verified():
    post = raw_result({}, complete=False, success=False)
    post["output"] = "=== 5 passed in 0.2s ==="
    result = classify_baseline_delta(make_baseline({KEY: F()}), build_validation_outcome(post))
    assert result.blocking and result.blocking_reasons[-1].startswith("pytest_evidence_incomplete:POST:")
    # the whole-output fingerprints differ here, so the aggregate change is a fact the message may state;
    # the prose "5 passed" changed nothing
    attribution, message = regression_unattributed_diagnosis(result)
    assert attribution == ATTRIBUTION_AGGREGATE_DELTA and "no specific test could be confirmed" in message


def test_08_mixed_partial_evidence_reaches_a_bounded_conclusion():
    # PRE complete, POST incomplete but passing: the whole-output authority applies, nothing is attributed
    result = decide({KEY: F()}, {}, post_success=False)
    assert result[0].blocking  # a failing POST without complete evidence blocks
    passing = classify_baseline_delta(make_baseline({KEY: F()}),
                                      build_validation_outcome(raw_result({}, complete=False, success=True)))
    assert not passing.blocking and passing.authority == vb.WHOLE_OUTPUT_AUTHORITY
    assert passing.pytest_evidence_status.startswith("POST:")


def test_09_a_real_pytest_run_decides_from_its_own_report_not_its_text(tmp_path):
    root = write_project(tmp_path / "ws", "def test_a():\n    assert True\n")
    pre = validator_for(root).run_tests()
    (root / "tests" / "test_suite.py").write_text("def test_a():\n    assert False, 'all tests passed'\n")
    post = validator_for(root).run_tests()
    post["output"] += "\n=== 1 passed in 0.0s ===\n"  # appended prose cannot turn a structured failure into a pass
    result = classify_baseline_delta(
        vb.ValidationBaseline(workspace_revision="r", run_id="run", captured_at=0.0,
                              outcome=build_validation_outcome(pre),
                              invocation=vb.ValidationInvocation("c", "full_suite")),
        build_validation_outcome(post))
    assert result.blocking and list(result.level2.values()) == [DeltaClassification.NEW_FAILURE]


# ---------------------------------------------------------------- structural tripwire: the decision reads structure only
_DECISION_NAMES = {"_regression_should_block", "_targeted_regression_should_block", "_full_regression_unattributed"}
# The only inputs a regression decision may be assigned from: the gate verdicts and the structured delta results.
_ALLOWED_SOURCES = {"full_test_res", "_baseline_delta_result", "_targeted_test_res", "_targeted_baseline_delta_result",
                    "_regression_should_block", "_targeted_regression_should_block", "_compile_regression",
                    "_full_regression_unattributed"}


_GATE_RESULTS = {"full_test_res", "_targeted_test_res"}
_DELTAS = {"_baseline_delta_result", "_targeted_baseline_delta_result"}


def _decision_violations(node):
    """Everything in a regression-decision expression that is not the gate's own verdict (``<gate>["success"]``),
    the structured delta's own decision (``<delta>.blocking``), an identity test on them, another decision
    variable, or the compile-attribution result. Text membership and string constants are never a source."""
    if isinstance(node, ast.Subscript):
        if (isinstance(node.value, ast.Name) and node.value.id in _GATE_RESULTS
                and isinstance(node.slice, ast.Constant) and node.slice.value == "success"):
            return
        yield f"subscript at line {node.lineno}"
        return
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id in _DELTAS and node.attr == "blocking":
            return
        yield f"attribute .{node.attr} at line {node.lineno}"
        return
    if isinstance(node, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
        yield f"membership test at line {node.lineno}"
    elif isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield f"string constant {node.value!r} at line {node.lineno}"
    elif isinstance(node, ast.Call):
        yield f"call at line {node.lineno}"
    elif isinstance(node, ast.Name) and node.id not in _ALLOWED_SOURCES:
        yield f"name {node.id} at line {node.lineno}"
    for child in ast.iter_child_nodes(node):
        yield from _decision_violations(child)


def test_10_regression_decisions_are_assigned_only_from_gate_verdicts_and_structured_deltas():
    """Tripwire over plain assignments to the three decision names; an augmented assignment, a new decision
    name or a decision made under another name is outside it (the anchor count below guards the names)."""
    tree = ast.parse((ROOT / "kriya" / "workflow" / "workflow.py").read_text())
    assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id in _DECISION_NAMES for t in node.targets)]
    assert len(assignments) >= 4, "the regression decision variables disappeared - re-anchor this tripwire"
    violations = [v for node in assignments if not isinstance(node.value, ast.Constant)
                  for v in _decision_violations(node.value)]
    assert violations == []


def test_10b_the_tripwire_catches_a_planted_prose_decision():
    planted = ast.parse('_regression_should_block = "passed" not in full_test_res["output"]\n').body[0].value
    assert list(_decision_violations(planted))
    legitimate = ast.parse("_regression_should_block = not full_test_res[\"success\"] or _baseline_delta_result.blocking\n")
    assert list(_decision_violations(legitimate.body[0].value)) == []


def test_11_regression_authority_modules_never_import_a_model_or_agent():
    for module in ("validation_baseline", "regression_attribution", "pytest_stability", "compile_regression"):
        tree = ast.parse((ROOT / "kriya" / "workflow" / f"{module}.py").read_text())
        imported = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not [m for m in imported if m.startswith(("kriya.agents", "kriya.core.llm", "kriya.core.inference"))], module


@pytest.mark.parametrize("field", ["blocking_reasons", "pytest_evidence_status", "attribution"])
def test_12_the_stop_carries_its_structured_diagnostics(field):
    source = (ROOT / "kriya" / "workflow" / "workflow.py").read_text()
    stop = source[source.index('type="regression_unattributed"'):]
    assert f'"{field}"' in stop[:1500]
