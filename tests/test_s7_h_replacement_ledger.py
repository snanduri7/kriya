"""S8 (done): the H replacement-control ledger is enforced (tests/containment_h_ledger.py).

S8 deleted the exact-id H list of tests/containment_census.py (and the three ``legacy_host_mcp`` tests). The ledger is the RECORD of those deletions: every deleted id has a recorded
premise, at least one proof that project-controlled host execution is refused or unreachable for that surface, and either contained / typed replacement coverage or a reasoned
"nothing contained is left to cover". This file keeps that record honest after the deletion: a replacement control that no longer exists (a later rename or deletion) fails here,
so the evidence that justified a deletion can never silently vanish."""
import ast
from functools import lru_cache
from pathlib import Path

import containment_census as cc
import containment_h_ledger as ledger
import pytest

ROOT = Path(__file__).resolve().parent.parent

# MEASURED 2026-10-26 (every H skip lifted): these pass anyway. Pinned so a change in what passes is a reviewed decision, not a silent one.
INCIDENTAL_PASSES = {
    "tests/test_prd011_toolchain_evidence.py::test_host_mode_gate_outcomes_never_claim_a_toolchain_identity",
    "tests/test_prd011_toolchain_fingerprint.py::test_direct_resume_in_host_mode_stays_unverified",
    "tests/test_s4_1_managed_service_view.py::test_legacy_host_permitted_behavior_is_recorded_as_superseded_the_host_service_inherits_the_environment",
    "tests/test_sec005_shell_acquisition_network.py::test_maven_command_network_unaffected_when_containment_not_required",
    "tests/test_tool001_autonomous_tool_execution.py::test_scenario_g_shelltool_ordinary_command_unaffected_when_uncontained",
}


@lru_cache(maxsize=None)
def _functions(relpath: str) -> frozenset:
    path = ROOT / relpath
    assert path.is_file(), f"{relpath}: no such file"
    return frozenset(node.name for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))


def _resolves(node_id: str) -> bool:
    relpath, _, name = node_id.partition("::")
    return name.split("[", 1)[0] in _functions(relpath)


def test_the_ledger_records_exactly_the_54_plus_3_tests_s8_deleted_and_none_is_classified_legacy_host_any_more():
    assert len(ledger.LEDGER) == 54 and len(ledger.MARKED_LEDGER) == 3
    assert cc.legacy_host_ids() == ()


ALL = {**ledger.LEDGER, **ledger.MARKED_LEDGER}


@pytest.mark.parametrize("test_id", sorted(ALL))
def test_every_deletion_has_a_premise_an_unreachability_proof_and_a_replacement_or_a_reasoned_not_applicable(test_id):
    entry = ALL[test_id]
    assert len(entry.premise) >= 15
    assert entry.unreachable_by, "no proof that project-controlled host execution is refused or unreachable for this surface"
    if entry.semantic:
        return
    assert entry.why.startswith("NOT-APPLICABLE:") and len(entry.why) >= 80, "no contained replacement and no reasoned NOT-APPLICABLE"


@pytest.mark.parametrize("test_id", sorted(ALL))
def test_every_referenced_control_is_a_real_test_that_is_not_itself_legacy_host(test_id):
    entry = ALL[test_id]
    legacy = set(ledger.LEDGER) | set(ledger.MARKED_LEDGER)
    for control in (*entry.unreachable_by, *entry.semantic):
        assert _resolves(control), f"{control}: no such test function"
        assert control not in legacy and cc.classify(control) != ("H", "H"), f"{control} is itself a deleted legacy-host test"
        assert control != test_id


def test_the_incidentally_passing_set_is_exactly_the_measured_one():
    assert {test_id for test_id, entry in ledger.LEDGER.items() if entry.passes_unskipped} == INCIDENTAL_PASSES
    assert all(ledger.LEDGER[test_id].why for test_id in INCIDENTAL_PASSES)       # each says why it passes although its premise is gone


def test_no_legacy_host_test_is_a_whole_file_and_each_named_file_still_holds_other_tests():
    """S8's deletions must not delete a file that still carries live coverage by accident: every file with a recorded id still exists."""
    files = {test_id.partition("::")[0] for test_id in ledger.LEDGER}
    for relpath in files:
        assert (ROOT / relpath).is_file()
