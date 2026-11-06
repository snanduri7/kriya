"""KERNEL-RUNTIME-DETERMINISTIC-RESULT-PASS-ON-FAIL-001 / OI-56: ``deterministic_result`` is a projection of the gate's AUTHORITATIVE verdict, so a
failed mandatory deterministic gate can never serialize (or display) PASS.

Measured defect (2026-11-06): ``_deterministic_result_provenance_field('process_exit')`` returned "PASS" whatever the verdict; the stored goldens
``attempt_d_deterministic_test_fail`` / ``attempt_ae_self_correction_rerun_still_failing`` carried ``"deterministic_result": "PASS"`` beside
``"success": false`` and the GUI Inspector shows the field. Contract (ledger): ONE authority, the derived field agrees with it; build/test success and
failure and not-applicable are never collapsed.
"""
import ast
import json
from pathlib import Path

import pytest

from kriya.workflow import attempt, runtime_verification

ROOT = Path(__file__).resolve().parent
GOLDENS = sorted((ROOT / "golden" / "runtime").glob("*.json"))
FIELD = runtime_verification._deterministic_result_provenance_field  # noqa: SLF001 - the projection under test


def _outcomes(node):
    """Every dict that is a gate outcome carrying a deterministic_result (recursive over a golden)."""
    if isinstance(node, dict):
        if "deterministic_result" in node and "success" in node:
            yield node
        for value in node.values():
            yield from _outcomes(value)
    elif isinstance(node, list):
        for value in node:
            yield from _outcomes(value)


@pytest.mark.parametrize("authority, passed, expected", [
    ("process_exit", True, "PASS"), ("process_exit", False, "FAIL"),                      # build or test, success or failure
    ("llm_over_distrusted_evidence", False, "DISTRUSTED"), ("llm_over_distrusted_evidence", True, "DISTRUSTED"),
    ("llm", True, None), ("llm", False, None), ("contract", True, None), ("contract", False, None),  # no deterministic verdict exists
])
def test_the_projection_table(authority, passed, expected):
    assert FIELD(authority, passed) == expected
    assert attempt._deterministic_result_provenance_field(authority, passed) == expected  # the facade keeps the same shape  # noqa: SLF001


@pytest.mark.parametrize("verdict", [None, 0, "", "yes", [], 1])
def test_only_an_explicit_true_ever_reads_pass(verdict):
    assert FIELD("process_exit", verdict) == ("PASS" if verdict is True else "FAIL")


def test_fail_distrusted_and_no_verdict_are_four_distinct_values_never_collapsed():
    assert len({FIELD("process_exit", True), FIELD("process_exit", False), FIELD("llm_over_distrusted_evidence", False), FIELD("llm", False)}) == 4


def test_the_stored_goldens_never_pair_a_failed_gate_with_pass_and_still_record_passes():
    seen = {"failed": 0, "passed_deterministic": 0}
    for golden in GOLDENS:
        for outcome in _outcomes(json.loads(golden.read_text(encoding="utf-8"))):
            result = outcome["deterministic_result"]
            if outcome["success"] is False:
                seen["failed"] += 1
                assert result != "PASS", f"{golden.name}: a FAILED gate recorded deterministic_result PASS"
            elif result == "PASS":
                seen["passed_deterministic"] += 1
    assert seen["failed"] >= 4 and seen["passed_deterministic"] >= 4, f"the invariant would be vacuous: {seen}"


def test_every_call_site_hands_the_authoritative_verdict_to_the_projection():
    tree = ast.parse((Path(runtime_verification.__file__)).read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "_deterministic_result_provenance_field"]
    assert len(calls) == 4 and all(len(c.args) == 2 and "grade" in ast.unparse(c.args[1]) for c in calls), [ast.unparse(c) for c in calls]


def test_mutation_the_old_one_argument_projection_violates_the_invariant():
    def old(authority):
        return "PASS" if authority == "process_exit" else None

    assert old("process_exit") == "PASS"                      # for a FAILED outcome the old code still said PASS
    assert FIELD("process_exit", False) != old("process_exit")
