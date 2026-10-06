"""FS-1C1 sentinel: the preserved post-P3 A1 specimen (freezegun, run
20261006T082942-595796b8; handover/LR_R1_POST_P3_LIVE_VALIDATION.md, evidence on
validation/lr-r1-post-p3).

Replays, through the production closure functions, exactly what the live run
measured at its terminal requirement gate: the live goal (tests/fixtures/fs1c1/
a1_goal.txt), the verifier's "satisfied" (a model claim), the live C0 judgment
(ORACLE_PASSED on tests/test_operations.py: unchanged trust surface, the 15
base-inventory cases all passed - tests/fixtures/fs1c1/a1_c0_inventory.json) and
the live mutation-scope evidence (actual = authorized = freezegun/api.py). The
applied candidate (a1_applied.diff.txt) changed only the helper, so the goal's
behaviour (`freeze_time(0)`, `freeze_time(86400.5)`) is not implemented
(independent check: both raise TypeError).
"""
import json
from pathlib import Path

from kriya.workflow.named_test_oracle import CLOSURE_METHOD, ORACLE_PASSED, OracleJudgment
from kriya.workflow.obligations import ObligationLedger
from kriya.workflow.requirements import (
    RequirementOutcome,
    blocking_requirements,
    close_mutation_scope_requirements,
    close_unverified_requirements_with_named_tests,
    derive_requirements,
    record_requirement_verdicts,
    requirement_outcomes,
    seed_requirement_obligations,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fs1c1"
GOAL = (FIXTURES / "a1_goal.txt").read_text()
INVENTORY = json.loads((FIXTURES / "a1_c0_inventory.json").read_text())
ORACLE = "tests/test_operations.py"
TARGET = "freezegun/api.py"
CANDIDATE = "a1-live-candidate"
TRACKED = [TARGET, ORACLE, "tests/__init__.py", "freezegun/__init__.py"]


def live_c0_judgment(named):
    """What the live C0 judge returned for the named oracle (ORACLE_PASSED)."""
    assert list(named) == [ORACLE]
    return OracleJudgment(ORACLE_PASSED, "", {
        "version": 1, "method": CLOSURE_METHOD, "base_revision": INVENTORY["base_revision"],
        "tests": [ORACLE], "runner": "pytest", "expected_cases": INVENTORY["expected_cases"],
        "provenance": "KRIYA_CONTROLLED"})


def terminal_requirements(judge=live_c0_judgment):
    """The live terminal requirement gate on the A1 candidate: mutation scope,
    then the named-test closure. Returns (requirements, ledger, closure attempts)."""
    reqs = derive_requirements(GOAL)
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    record_requirement_verdicts(ledger, reqs, {r.id: (RequirementOutcome.SATISFIED, "model: implemented")
                                               for r in reqs.requirements},
                                revision="terminal", evidence_fingerprint=CANDIDATE, source="spec_compliance")
    scope = close_mutation_scope_requirements(
        ledger, reqs, tracked_paths=TRACKED,
        scope_evidence={"run_id": "20261006T082942-595796b8", "base_revision": INVENTORY["base_revision"],
                        "candidate_revision": INVENTORY["base_revision"], "actual_paths": [TARGET],
                        "committed_history": [], "foreign_paths": []},
        source="requirement_closure.mutation_scope", revision="terminal")
    named = close_unverified_requirements_with_named_tests(
        ledger, reqs, test_files=[ORACLE], modified=[TARGET], judge=judge,
        source="requirement_closure.named_test_run", revision="terminal")
    return reqs, ledger, scope + named


def production_blocking(reqs, ledger):
    """runtime_profile: production seals both requirement policies to block."""
    return [r.id for r, _ in blocking_requirements(ledger, reqs, unknown_policy="block",
                                                   unverified_policy="block")]


def outcomes(reqs, ledger):
    return requirement_outcomes(ledger, reqs)
