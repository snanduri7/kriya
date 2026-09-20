"""VAL-001 G1-DEVINV2 (2026-09-20).

kriya/workflow/regression_attribution.py::confirm_ambiguous_regressions -
resolves an ambiguous (NOT_COMPARABLE) full-regression delta entry by
replaying it, in isolation, against BOTH a pristine and a candidate
workspace copy. Two layers, matching this codebase's own established
pattern for a replay-based mechanism (see tests/test_deterministic_
failure_diagnostic.py):

1. Pure orchestration (confirm_ambiguous_regressions), with the actual
   subprocess-running replay helper mocked - the classification logic in
   isolation.
2. A real, end-to-end replay against two tiny synthetic Python projects -
   proves the isolation-based confirmation genuinely distinguishes a real
   candidate regression from a test that merely LOOKED new during one
   particular full-suite run.

No live model/Ollama calls anywhere in this file.
"""
from unittest.mock import patch

from kriya.config import AppConfig
from kriya.workflow.validation_baseline import DeltaClassification
from kriya.workflow.regression_attribution import confirm_ambiguous_regressions


def _cfg():
    return AppConfig().autonomy


# ---------------------------------------------------------------------------
# Layer 1: pure orchestration, replay mocked
# ---------------------------------------------------------------------------

def test_no_ambiguous_entries_is_a_pure_noop():
    level2 = {
        "tests/x.py::test_pre_existing": DeltaClassification.PRE_EXISTING_FAILURE,
        "tests/y.py::test_new": DeltaClassification.NEW_FAILURE,
    }
    with patch(
        "kriya.workflow.regression_attribution._replay_test_ids_against_workspace",
    ) as mocked_replay:
        resolved, evidence = confirm_ambiguous_regressions(
            level2, pristine_workspace_path="/pristine", candidate_workspace_path="/candidate",
            autonomy_cfg=_cfg(),
        )
    mocked_replay.assert_not_called()
    assert resolved == level2
    assert resolved is not level2  # a fresh dict is always returned
    assert evidence == {}


def test_fails_only_against_candidate_is_confirmed_new_failure():
    level2 = {"tests/x.py::test_ambiguous": DeltaClassification.NOT_COMPARABLE}
    pristine_result = {"success": True, "output": "===== 1 passed in 0.01s ====="}
    candidate_result = {
        "success": False,
        "output": (
            "FAILED tests/x.py::test_ambiguous - AssertionError\n"
            "===== 1 failed in 0.01s ====="
        ),
    }
    with patch(
        "kriya.workflow.regression_attribution._replay_test_ids_against_workspace",
        side_effect=[pristine_result, candidate_result],
    ):
        resolved, evidence = confirm_ambiguous_regressions(
            level2, pristine_workspace_path="/pristine", candidate_workspace_path="/candidate",
            autonomy_cfg=_cfg(),
        )
    assert resolved["tests/x.py::test_ambiguous"] == DeltaClassification.NEW_FAILURE
    assert "confirmed candidate-caused regression" in evidence["tests/x.py::test_ambiguous"]


def test_fails_against_both_is_not_attributable():
    level2 = {"tests/x.py::test_ambiguous": DeltaClassification.NOT_COMPARABLE}
    both_failing = {
        "success": False,
        "output": "FAILED tests/x.py::test_ambiguous - AssertionError\n===== 1 failed in 0.01s =====",
    }
    with patch(
        "kriya.workflow.regression_attribution._replay_test_ids_against_workspace",
        side_effect=[both_failing, both_failing],
    ):
        resolved, evidence = confirm_ambiguous_regressions(
            level2, pristine_workspace_path="/pristine", candidate_workspace_path="/candidate",
            autonomy_cfg=_cfg(),
        )
    assert resolved["tests/x.py::test_ambiguous"] == DeltaClassification.PRE_EXISTING_FAILURE
    assert "not attributable to this candidate" in evidence["tests/x.py::test_ambiguous"]


def test_passes_against_both_stays_not_comparable_never_pre_existing():
    """Live-confirmed real case (2026-09-20 G1 qwen3.8:27b run): a test can
    show up newly-failing in one particular full-suite run for reasons
    unrelated to either the pristine or the candidate code. Checking
    pristine alone would misclassify this as a confirmed regression -
    checking both sides, both passing in isolation, correctly excludes it
    from Developer repair. Critically, it must NOT be relabeled
    PRE_EXISTING_FAILURE either: passing both sides in isolation proves
    only that this candidate can't currently be shown to cause it, never
    that it's a known, accepted, understood condition - a real semantic
    distinction the user caught in review (2026-09-20), not merely wording.
    It stays NOT_COMPARABLE (indeterminate/non-reproducible)."""
    level2 = {"tests/x.py::test_ambiguous": DeltaClassification.NOT_COMPARABLE}
    both_passing = {"success": True, "output": "===== 1 passed in 0.01s ====="}
    with patch(
        "kriya.workflow.regression_attribution._replay_test_ids_against_workspace",
        side_effect=[both_passing, both_passing],
    ):
        resolved, evidence = confirm_ambiguous_regressions(
            level2, pristine_workspace_path="/pristine", candidate_workspace_path="/candidate",
            autonomy_cfg=_cfg(),
        )
    assert resolved["tests/x.py::test_ambiguous"] == DeltaClassification.NOT_COMPARABLE
    assert resolved["tests/x.py::test_ambiguous"] != DeltaClassification.PRE_EXISTING_FAILURE
    assert "NOT_COMPARABLE" in evidence["tests/x.py::test_ambiguous"]
    assert "not reclassified as a known pre-existing failure" in evidence["tests/x.py::test_ambiguous"]


def test_fails_against_pristine_only_is_not_a_regression():
    level2 = {"tests/x.py::test_ambiguous": DeltaClassification.NOT_COMPARABLE}
    pristine_failing = {
        "success": False,
        "output": "FAILED tests/x.py::test_ambiguous - AssertionError\n===== 1 failed in 0.01s =====",
    }
    candidate_passing = {"success": True, "output": "===== 1 passed in 0.01s ====="}
    with patch(
        "kriya.workflow.regression_attribution._replay_test_ids_against_workspace",
        side_effect=[pristine_failing, candidate_passing],
    ):
        resolved, evidence = confirm_ambiguous_regressions(
            level2, pristine_workspace_path="/pristine", candidate_workspace_path="/candidate",
            autonomy_cfg=_cfg(),
        )
    assert resolved["tests/x.py::test_ambiguous"] == DeltaClassification.PRE_EXISTING_FAILURE
    assert "not a regression" in evidence["tests/x.py::test_ambiguous"]


def test_replay_exception_fails_closed_as_not_comparable():
    """A broken replay (subprocess/timeout/environment error) is evidence
    of nothing - it must never be read as exoneration (PRE_EXISTING_FAILURE)
    NOR as confirmation (NEW_FAILURE). Every ambiguous id stays
    NOT_COMPARABLE (indeterminate); the caller's own level1-driven
    REGRESSION_UNATTRIBUTED stop is what fails this closed when nothing
    else is confirmed attributable, not a mislabel here."""
    level2 = {
        "tests/x.py::test_a": DeltaClassification.NOT_COMPARABLE,
        "tests/y.py::test_b": DeltaClassification.NOT_COMPARABLE,
    }
    with patch(
        "kriya.workflow.regression_attribution._replay_test_ids_against_workspace",
        side_effect=RuntimeError("subprocess timed out"),
    ):
        resolved, evidence = confirm_ambiguous_regressions(
            level2, pristine_workspace_path="/pristine", candidate_workspace_path="/candidate",
            autonomy_cfg=_cfg(),
        )
    assert resolved["tests/x.py::test_a"] == DeltaClassification.NOT_COMPARABLE
    assert resolved["tests/y.py::test_b"] == DeltaClassification.NOT_COMPARABLE
    assert "left NOT_COMPARABLE" in evidence["tests/x.py::test_a"]


def test_non_ambiguous_entries_are_never_touched():
    level2 = {
        "tests/x.py::test_ambiguous": DeltaClassification.NOT_COMPARABLE,
        "tests/y.py::test_pre_existing": DeltaClassification.PRE_EXISTING_FAILURE,
    }
    both_passing = {"success": True, "output": "===== 1 passed in 0.01s ====="}
    with patch(
        "kriya.workflow.regression_attribution._replay_test_ids_against_workspace",
        side_effect=[both_passing, both_passing],
    ):
        resolved, _ = confirm_ambiguous_regressions(
            level2, pristine_workspace_path="/pristine", candidate_workspace_path="/candidate",
            autonomy_cfg=_cfg(),
        )
    assert resolved["tests/y.py::test_pre_existing"] == DeltaClassification.PRE_EXISTING_FAILURE


# ---------------------------------------------------------------------------
# Layer 2: real, end-to-end replay (no live model, real subprocess)
# ---------------------------------------------------------------------------

def test_real_isolated_replay_distinguishes_regression_from_full_suite_artifact(tmp_path):
    """Real, non-mocked proof of the decisive property confirm_ambiguous_
    regressions exists for: an isolated single-test replay can tell a
    genuine candidate regression (passes pristine, fails candidate) apart
    from a test that merely looked newly-failing in one particular
    full-suite run but is unaffected by the actual code in either state
    (passes both in isolation) - the exact live-confirmed shape from a
    real G1 run (qwen3.8:27b, 2026-09-20)."""
    pristine_dir = tmp_path / "pristine"
    candidate_dir = tmp_path / "candidate"
    (pristine_dir / "tests").mkdir(parents=True)
    (candidate_dir / "tests").mkdir(parents=True)

    (pristine_dir / "tests" / "test_x.py").write_text(
        "def test_real_regression():\n    assert 1 == 1\n\n"
        "def test_looks_new_but_isnt():\n    assert 1 == 1\n"
    )
    (candidate_dir / "tests" / "test_x.py").write_text(
        "def test_real_regression():\n    assert 1 == 2  # candidate broke this\n\n"
        "def test_looks_new_but_isnt():\n    assert 1 == 1\n"
    )

    level2 = {
        "tests/test_x.py::test_real_regression": DeltaClassification.NOT_COMPARABLE,
        "tests/test_x.py::test_looks_new_but_isnt": DeltaClassification.NOT_COMPARABLE,
    }
    resolved, evidence = confirm_ambiguous_regressions(
        level2,
        pristine_workspace_path=str(pristine_dir),
        candidate_workspace_path=str(candidate_dir),
        autonomy_cfg=_cfg(),
    )
    assert resolved["tests/test_x.py::test_real_regression"] == DeltaClassification.NEW_FAILURE
    # Passes both sides in isolation -> stays NOT_COMPARABLE (indeterminate),
    # never relabeled PRE_EXISTING_FAILURE - see confirm_ambiguous_
    # regressions's own docstring for why that distinction is real, not
    # cosmetic (2026-09-20 user review).
    assert resolved["tests/test_x.py::test_looks_new_but_isnt"] == DeltaClassification.NOT_COMPARABLE
    assert "confirmed candidate-caused regression" in evidence["tests/test_x.py::test_real_regression"]
    assert "NOT_COMPARABLE" in evidence["tests/test_x.py::test_looks_new_but_isnt"]

    # Isolation proof: neither temp copy leaks back into the real source
    # directories this test created.
    assert (pristine_dir / "tests" / "test_x.py").read_text().count("assert 1 == 1") == 2
    assert (candidate_dir / "tests" / "test_x.py").read_text().count("assert 1 == 2") == 1
