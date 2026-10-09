"""SUITE-PRESERVATION-CLOSURE-BASELINE-ATTRIBUTION-001 (P1, BACKEND-FINAL-CLOSURE-005 third repair cycle): the
deterministic reproducer written BEFORE the production change (ENGINEERING_RULES rule 4).

Measured (C2-S2_A-final on 557035d, generate.log 13:54:25): the terminal suite-preservation closer judged REQ-9 from
the raw suite result - 193 cases, 191 passed / 1 skipped / 1 failed, COMPLETE report, closed False, "the full suite did
not pass on this candidate" - while the same run had attributed that one failure PRE_EXISTING_FAILURE at twelve gate
sites through the D1 owner (kriya/workflow/suite_attribution.py). Producer: kriya/workflow/requirements.py
close_suite_preservation_requirements reads ``result["success"]`` and the failing-case list with no PRE/POST baseline
attribution; its wrapper kriya/workflow/workflow.py close_requirements_by_suite_preservation (the enforce terminal
gate's and the direct boundary's one entry point) hands it only the candidate's own suite. A brownfield repository with
one stable pre-existing failure can therefore never close a REGRESSION_PRESERVATION requirement, whatever the candidate.

Owner decision (2026-10-09): the closure compares the candidate's suite with the frozen baseline through the one
attribution owner (attribute_suite_result: PRE_EXISTING / CHANGED / NEW / REGRESSION_UNATTRIBUTED semantics unchanged);
a stable pre-existing failure never violates preservation, a candidate-introduced or worsened failure does, and an
unavailable baseline / replay / attribution fails closed as REGRESSION_UNATTRIBUTED. The baseline is captured lazily on
the untouched workspace, once per closure attempt, never when the candidate suite is green; the raw suite result and
the attribution decision are both preserved in the closure evidence.

Every case runs the real wrapper on a real git workspace with the production pytest gate (host mode, Kriya's own
interpreter), a candidate copy at the same HEAD, and real PRE/POST attribution - nothing is stubbed.
"""
import os
import shutil
import subprocess
import sys
from unittest.mock import patch

from kriya.config.config import AutonomyConfig
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow import workflow as wf
from kriya.workflow.obligations import ObligationLedger
from kriya.workflow.requirements import (
    RequirementOutcome,
    derive_requirements,
    record_requirement_verdicts,
    requirement_evidence,
    requirement_outcomes,
    seed_requirement_obligations,
)

CALC = "def add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n    return a * b\n\n\ndef note():\n    return 'base'\n"
CALC_TIDIED = CALC.replace("'base'", "'tidied'")  # the correct candidate: an unrelated, harmless change
CALC_MUL_BROKEN = CALC.replace("a * b", "a + b")  # a NEW failure: test_mul breaks
CALC_ADD_WORSE = CALC.replace("a + b", "a + b + 3", 1)  # the pre-existing failure's message changes: "got 2" -> "got 5"
SUITE = (
    "import calc\n\n\n"
    "def test_add_legacy():\n    assert calc.add(1, 1) == 3, f'got {calc.add(1, 1)}'\n\n\n"  # pre-existing, stable text
    "def test_mul():\n    assert calc.mul(2, 3) == 6\n\n\n"
    "def test_note():\n    assert calc.note()\n"
)
GREEN_SUITE = SUITE.replace("== 3, f'got {calc.add(1, 1)}'", "== 2")
LEGACY = "tests/test_suite.py::test_add_legacy"
MUL = "tests/test_suite.py::test_mul"
GOAL = "Tidy the note.\nAll existing tests must keep passing."  # no "unchanged": the suite alone closes it
TWO_STATEMENTS = GOAL + "\nThe existing test suite stays green."


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True, capture_output=True)


def _workspace(tmp_path, suite=SUITE):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "requirements.txt").write_text("")
    (root / ".gitignore").write_text(".kriya/\n__pycache__/\n.pytest_cache/\n*.pyc\n")
    (root / "calc.py").write_text(CALC)
    (root / "tests").mkdir()
    (root / "tests" / "__init__.py").write_text("")
    (root / "tests" / "test_suite.py").write_text(suite)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    return root


def _candidate(tmp_path, workspace, calc):
    """The candidate tree the terminal gate judges: a copy of the workspace at the same HEAD with the change applied.
    Like a git worktree or the contract-baseline export, it carries no other tree's bytecode, pytest cache or Kriya
    runtime state (a copied tests/__pycache__ embeds the workspace path in its code objects and is reused when the
    mtimes match, so the candidate's tracebacks would name ../ws/... - measured 2026-10-09)."""
    candidate = tmp_path / "cand"
    shutil.copytree(workspace, candidate, symlinks=True,
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".kriya"))
    (candidate / "calc.py").write_text(calc)
    return candidate


def _unverified_ledger(reqs, evidence_id="candidate-1"):
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    record_requirement_verdicts(ledger, reqs, {r.id: (RequirementOutcome.UNVERIFIED, "cannot confirm from code")
                                               for r in reqs.requirements},
                                revision="terminal", evidence_fingerprint=evidence_id, source="test")
    return ledger


def _close(workspace, candidate, goal=GOAL, **direct_path_inputs):
    """The real wrapper, the production pytest gate on both trees; every suite run is accounted by the tree it ran on.
    ``direct_path_inputs``: the run's own ``suite_baseline`` / ``stability_cache`` / ``baseline_suite_run`` (the direct
    boundary's call site); none = the enforce terminal gate's call (lazy capture)."""
    reqs = derive_requirements(goal)
    ledger = _unverified_ledger(reqs)
    runs = []
    real_run_tests = PolymorphicValidator.run_tests

    def run_tests(self, target_test=None):
        runs.append((os.path.realpath(self.workspace_path), target_test))
        return real_run_tests(self, target_test=target_test)

    with patch.object(PolymorphicValidator, "_resolve_python_interpreter", new=lambda self: (sys.executable, None)), \
         patch.object(PolymorphicValidator, "run_tests", new=run_tests):
        entries = wf.close_requirements_by_suite_preservation(
            AutonomyConfig(), ledger, reqs, str(candidate), str(workspace), revision="terminal",
            toolchain_declaration_mutable=False, candidate_paths=["calc.py"], **direct_path_inputs)
    base = [t for root, t in runs if root == os.path.realpath(str(workspace))]
    cand = [t for root, t in runs if root == os.path.realpath(str(candidate))]
    assert len(base) + len(cand) == len(runs), runs  # every run was on one of the two trees
    return entries, reqs, ledger, base, cand


def _suite_entries(entries):
    return [e for e in entries if e["kind"] == "suite_preservation"]


# ---------------------------------------------------------------- 1. the measured shape
def test_p1_2_a_stable_pre_existing_failure_and_a_correct_candidate_close_the_suite_statement(tmp_path):
    """C2-S2_A-final: one pre-existing failing test whose text is stable, a candidate that introduces nothing. The
    suite statement closes by evidence; the raw suite result (failed 1) and the attribution decision are both kept."""
    workspace = _workspace(tmp_path)
    candidate = _candidate(tmp_path, workspace, CALC_TIDIED)
    entries, reqs, ledger, base, cand = _close(workspace, candidate)
    [entry] = _suite_entries(entries)
    assert entry["requirement"] == "REQ-2"
    assert entry["closed"] is True, entry
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.CLOSED_BY_EVIDENCE
    assert entry["success"] is False and entry["test_execution"]["status_counts"] == {"passed": 2, "failed": 1}
    attribution = entry["regression_attribution"]
    assert attribution["blocking"] is False and attribution["pre_existing_only"] is True
    assert attribution["confirmed_regressions"] == []
    assert cand == [None]  # the candidate suite ran once, whole
    assert base and all(t is None for t in base)  # the untouched workspace was captured (and only replayed whole)
    evidence = str(requirement_evidence(ledger, reqs)["REQ-2"])
    assert "regression_attribution" in evidence and "full_regression_oracle" in evidence


# ---------------------------------------------------------------- 2. a new failure (negative control)
def test_p1_2_a_candidate_introduced_failure_does_not_close_and_names_the_regression(tmp_path):
    workspace = _workspace(tmp_path)
    candidate = _candidate(tmp_path, workspace, CALC_MUL_BROKEN)
    entries, reqs, ledger, _base, cand = _close(workspace, candidate)
    [entry] = _suite_entries(entries)
    assert entry["closed"] is False
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.UNVERIFIED
    assert entry["success"] is False and entry["test_execution"]["status_counts"] == {"passed": 1, "failed": 2}
    assert MUL in entry["reason"], entry["reason"]
    assert entry["regression_attribution"]["blocking"] is True
    assert entry["regression_attribution"]["confirmed_regressions"] == [MUL]
    assert cand == [None]


# ---------------------------------------------------------------- 3. a worsened pre-existing failure (negative control)
def test_p1_2_a_pre_existing_failure_whose_text_the_candidate_changed_does_not_close(tmp_path):
    """The failing test still fails, but differently ("got 2" -> "got 5"): a CHANGED failure is the candidate's."""
    workspace = _workspace(tmp_path)
    candidate = _candidate(tmp_path, workspace, CALC_ADD_WORSE)
    entries, reqs, ledger, base, _cand = _close(workspace, candidate)
    [entry] = _suite_entries(entries)
    assert entry["closed"] is False
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.UNVERIFIED
    assert entry["test_execution"]["status_counts"] == {"passed": 2, "failed": 1}  # the same count: only attribution tells
    assert entry["regression_attribution"]["blocking"] is True
    assert entry["regression_attribution"]["confirmed_regressions"] == [LEGACY], (entry["regression_attribution"], entry["reason"], base)
    assert LEGACY in entry["reason"], entry["reason"]
    assert base  # the baseline was captured (the owner may replay it, bounded, to measure the disputed text)


# ---------------------------------------------------------------- 4. the baseline cannot be established (fail closed)
def test_p1_2_an_unavailable_baseline_is_a_typed_unattributed_refusal_never_a_closure(tmp_path, monkeypatch):
    """The frozen baseline's identity cannot be computed: nothing is excused, the entry says REGRESSION_UNATTRIBUTED."""
    monkeypatch.setattr(wf, "compute_workspace_content_hash", lambda _path: None)
    workspace = _workspace(tmp_path)
    candidate = _candidate(tmp_path, workspace, CALC_TIDIED)
    entries, reqs, ledger, _base, cand = _close(workspace, candidate)
    [entry] = _suite_entries(entries)
    assert entry["closed"] is False
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.UNVERIFIED
    assert entry["reason"].startswith("REGRESSION_UNATTRIBUTED"), entry["reason"]
    assert entry["success"] is False and entry["test_execution"]["status_counts"] == {"passed": 2, "failed": 1}
    assert cand == [None]


# ---------------------------------------------------------------- 5. a green suite (unchanged behaviour)
def test_p1_2_a_green_suite_closes_as_before_and_never_runs_the_baseline(tmp_path):
    workspace = _workspace(tmp_path, suite=GREEN_SUITE)
    candidate = _candidate(tmp_path, workspace, CALC_TIDIED)
    entries, reqs, ledger, base, cand = _close(workspace, candidate)
    [entry] = _suite_entries(entries)
    assert entry["closed"] is True and entry["success"] is True
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.CLOSED_BY_EVIDENCE
    assert entry["test_execution"]["status_counts"] == {"passed": 3}
    assert cand == [None] and base == []  # no baseline suite for a green candidate
    assert not entry.get("regression_attribution")


# ---------------------------------------------------------------- 6. one capture per closure attempt
def test_p1_2_two_suite_statements_share_one_baseline_capture(tmp_path):
    """Two whole-suite statements in one goal: one candidate run, one baseline capture, both close."""
    workspace = _workspace(tmp_path)
    candidate = _candidate(tmp_path, workspace, CALC_TIDIED)
    entries, reqs, ledger, base, cand = _close(workspace, candidate, goal=TWO_STATEMENTS)
    suite = _suite_entries(entries)
    assert [e["requirement"] for e in suite] == ["REQ-2", "REQ-3"] and all(e["closed"] for e in suite), suite
    assert {requirement_outcomes(ledger, reqs)[r] for r in ("REQ-2", "REQ-3")} == {RequirementOutcome.CLOSED_BY_EVIDENCE}
    assert cand == [None] and base == [None]  # exactly one run on each tree


# ---------------------------------------------------------------- 7/8. the direct boundary's own inputs
def _run_baseline(workspace):
    """The run's own baseline call (run_generation_workflow's baseline_suite_run shape), with its calls counted."""
    calls = []

    def replay(target_test):
        calls.append(target_test)
        with patch.object(PolymorphicValidator, "_resolve_python_interpreter", new=lambda self: (sys.executable, None)):
            return PolymorphicValidator(str(workspace), original_workspace_path=str(workspace),
                                        autonomy_cfg=AutonomyConfig()).run_tests(target_test=target_test)
    replay.calls = calls
    return replay


def test_p1_2_the_direct_boundary_reuses_the_runs_captured_baseline_and_replay_without_a_second_capture(tmp_path):
    """The run captured its full-regression baseline at the start; the closure judges through it and the run's own
    replay - the wrapper never runs its own baseline suite."""
    from kriya.workflow.checkpoint import compute_workspace_content_hash
    from kriya.workflow.validation_baseline import _capture_single_baseline

    workspace = _workspace(tmp_path)
    replay = _run_baseline(workspace)
    captured = _capture_single_baseline(run_id="run-1", target_test=None, run_validator=replay,
                                        compute_revision=lambda: compute_workspace_content_hash(str(workspace)))
    assert captured.status == "captured" and replay.calls == [None]
    candidate = _candidate(tmp_path, workspace, CALC_TIDIED)
    cache = {}
    entries, reqs, ledger, base, cand = _close(workspace, candidate, suite_baseline=captured, stability_cache=cache,
                                               baseline_suite_run=replay)
    [entry] = _suite_entries(entries)
    assert entry["closed"] is True and entry["regression_attribution"]["pre_existing_only"] is True
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.CLOSED_BY_EVIDENCE
    assert cand == [None] and replay.calls == [None]  # the candidate once; the run's baseline call not repeated
    # ``base`` counts the wrapper's own validator runs on the workspace: the replay above is the run's, counted by itself
    assert base == []


def test_p1_2_an_indeterminate_run_baseline_is_reported_unavailable_never_recaptured(tmp_path):
    """The run's baseline exists but carries no trustworthy PRE evidence: the closure says so and fails closed - it does
    not quietly capture a new one behind the run's back."""
    from kriya.workflow.validation_baseline import _capture_single_baseline

    workspace = _workspace(tmp_path)
    indeterminate = _capture_single_baseline(run_id="run-1", target_test=None, run_validator=lambda _t: {"success": True},
                                             compute_revision=lambda: None)
    assert indeterminate.status == "baseline_indeterminate"
    candidate = _candidate(tmp_path, workspace, CALC_TIDIED)
    entries, reqs, ledger, base, cand = _close(workspace, candidate, suite_baseline=indeterminate)
    [entry] = _suite_entries(entries)
    assert entry["closed"] is False
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.UNVERIFIED
    assert entry["reason"].startswith("REGRESSION_UNATTRIBUTED") and "indeterminate" in entry["reason"], entry["reason"]
    assert entry["regression_attribution"] == {"available": False, "reason": entry["regression_attribution"]["reason"]}
    assert cand == [None] and base == []  # no lazy capture for a run that owns a (bad) baseline
