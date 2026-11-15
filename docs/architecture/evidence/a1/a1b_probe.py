"""A1b model-free probe (read-only). Run with PYTHONPATH=<closure worktree>. No model, network, container or repository write.

P10 staging rejection type vs the open-finding predicate (SEMANTIC-SAFETY-REPAIR-CONTINUITY-001)
P11 the same blocker with a changed embedded value (the INFERRED row of SM-A1-02)
P12 the unit attempt ceiling and the enforce re-invocation arithmetic (GAP-A1-04)
P13 the per-requirement recovery bound: obligation id per generation, ledger history of an unrecorded id
P14 what a new unit invocation starts with, and what the enforce control state can hold (GAP-A1-02)
P15 the no-progress limit: configured value vs effective value (GAP-A1-05)
"""
import dataclasses
import os
import sys
from types import SimpleNamespace

import kriya
from kriya.config.config import AutonomyConfig
from kriya.control.state import ControlState
from kriya.workflow import retry_policy
from kriya.workflow.failure import Failure, FileLocation
from kriya.workflow.failure_grounding import build_failure_signature
from kriya.workflow.failure_reporting import categorize_failure
from kriya.workflow.obligations import ObligationLedger
from kriya.workflow.open_findings import is_candidate_finding, replaces_open_finding, update_open_findings
from kriya.workflow.owner_recovery import _cross_owner_obligation_id
from kriya.workflow.retry_policy import observe_failure_family
from kriya.workflow.state import RetryBudgets

EVIDENCE = sys.argv[1]
print("kriya imported from:", os.path.dirname(kriya.__file__))

# --- P10 -------------------------------------------------------------------------------------------------------------
masking = Failure(
    type="unsafe_exception_masking", message="UNSAFE EXCEPTION MASKING in A.java: catch (Throwable t)",
    raw_output="catch (Throwable t). do not widen a catch to Error/Throwable.",
    file_locations=[FileLocation(filepath="A.java")], likely_files=["A.java"], attempt=3,
)
kept = update_open_findings([], masking, 3)
budget_refusal = Failure(type="context_budget_unsatisfiable", message="x", raw_output="x", source="orchestrator",
                         diagnostics={"reason_code": "CONTEXT_BUDGET_UNSATISFIABLE"})
print("P10 unsafe_exception_masking: category =", categorize_failure("unsafe_exception_masking"),
      "| is_candidate_finding =", is_candidate_finding("unsafe_exception_masking"),
      "| authority =", masking.authority, "| replaces_open_finding =", replaces_open_finding(masking))
print("    open set after the rejection:", [(f.failure_type, f.first_attempt) for f in kept],
      "| still open after an intervening budget refusal:", update_open_findings(kept, budget_refusal, 4) == kept)
print("    evidence text carries the prohibition:", "do not widen a catch" in (kept[0].evidence if kept else ""))
# OPEN-ITEMS OI-78 (2026-10-19) recorded "reproduced: no_progress_retry replaces unsafe_exception_masking".
# Built as its two producers build it (candidate_transaction.py, repair_orchestrator.py): an orchestrator refusal raised BEFORE inference.
no_progress = Failure(type="no_progress_retry", message="NO_PROGRESS_RETRY_EXHAUSTED: retry evidence materially identical",
                      raw_output="edit_capability=abc123", source="orchestrator", likely_files=["A.java"],
                      diagnostics={"reason_code": "NO_PROGRESS_RETRY_EXHAUSTED"})
compile_next = Failure(type="compile", message="A.java:[3,1] error: ';' expected", raw_output="A.java:[3,1] error: ';' expected",
                       likely_files=["A.java"], file_locations=[FileLocation("A.java", 3)])
print("    a following no_progress_retry failure replaces it?", update_open_findings(kept, no_progress, 4) != kept,
      "(is_candidate_finding('no_progress_retry') =", is_candidate_finding("no_progress_retry"), ")")
from kriya.workflow.retry_policy import reset_scoped_budgets_for_new_family  # noqa: E402

nb = RetryBudgets(targeted_retry_count=3, fallback_targeted_attempted=True)
masking_sig = build_failure_signature("unsafe_exception_masking", masking.raw_output)
no_progress_sig = build_failure_signature("no_progress_retry", "edit_capability=abc123")
observe_failure_family(nb, None, masking_sig)
# retry_strategy._record_attempt_failure inherits the previous signature only when replaces_open_finding is False
effective_sig = no_progress_sig if replaces_open_finding(no_progress) else masking_sig
if observe_failure_family(nb, masking_sig, effective_sig):
    reset_scoped_budgets_for_new_family(nb)
print("    scoped budgets after that no_progress_retry (were targeted=3, fallback one-shot used): targeted =",
      nb.targeted_retry_count, "| fallback_targeted_attempted =", nb.fallback_targeted_attempted)
after_compile = update_open_findings(kept, compile_next, 4)
print("    a following compile failure replaces it?", [f.failure_type for f in after_compile],
      "| prohibition still in the open set:", any("do not widen a catch" in f.evidence for f in after_compile))


# --- P11 -------------------------------------------------------------------------------------------------------------
def gate_text(attempt):
    section = open(os.path.join(EVIDENCE, f"ws4_prev_error_section_attempt{attempt}.txt"), encoding="utf-8").read()
    start = section.index("Authoritative validator evidence:\n") + len("Authoritative validator evidence:\n")
    ends = [i for i in (section.find("\n=== ", start), section.find("\nThis is a TARGETED", start)) if i >= 0]
    return section[start:min(ends)]


ASSERT = gate_text(3)
sig = lambda raw: build_failure_signature("targeted_test", raw)  # noqa: E731
message = sig(ASSERT)[1][-1] if isinstance(sig(ASSERT)[1], tuple) else None
print("P11 last element of the preserved assertion's signature detail:", repr(message)[:200])
import re  # noqa: E402

digits = re.findall(r"<(\d+)>", ASSERT)
if digits:
    changed = ASSERT.replace(f"<{digits[-1]}>", f"<{int(digits[-1]) + 1}>")
    b = RetryBudgets()
    observe_failure_family(b, None, sig(ASSERT))
    print("    same test, same line, the reported actual value changed by one -> same signature?", sig(ASSERT) == sig(changed),
          "| read as a new family?", observe_failure_family(b, sig(ASSERT), sig(changed)))
else:
    print("    the preserved assertion text embeds no <number>; P11 not applicable to this input")

# --- P12 -------------------------------------------------------------------------------------------------------------
state = SimpleNamespace(budgets=RetryBudgets())
for chain_len in (0, 1, 3):
    max_retries = max(4, 1 + chain_len) if chain_len else 4        # development_loop.py
    ceiling = retry_policy.max_total_attempts_for(state, max_retries=max_retries, targeted_max_retries=3,
                                                  has_fallback_model=bool(chain_len))
    print(f"P12 unit ceiling with a fallback chain of {chain_len}: max_retries={max_retries} ceiling={ceiling}")
print("    constants: API_CONTRACT_RECOVERY_MAX_ATTEMPTS =", retry_policy.API_CONTRACT_RECOVERY_MAX_ATTEMPTS,
      "| FALLBACK_ALLOWANCE =", retry_policy.FALLBACK_ALLOWANCE,
      "| STRATEGY_TRANSITION_AFTER_NO_PROGRESS =", retry_policy.STRATEGY_TRANSITION_AFTER_NO_PROGRESS)
print("    one stage with a persistent upstream requirement (pinned by tests/test_workflow_controller_enforce.py::"
      "test_enforce_bounds_recovery_attempts_per_downstream_owner_pair): 1 planned + 3 x (owner_recovery + consumer_retry) = 7 "
      "unit invocations; x ceiling 11 = 77 attempts possible for one blocker before the stage stops")

# --- P13 -------------------------------------------------------------------------------------------------------------
ids = [_cross_owner_obligation_id("s3", "s1", ["pom.xml"], "compile", g) for g in (0, 1, 2)]
ledger = ObligationLedger()
print("P13 requirement ids for generations 0..2 distinct:", len(set(ids)) == 3,
      "| history of a not-yet-recorded id:", len(ledger.history(ids[1])))

# --- P14 -------------------------------------------------------------------------------------------------------------
fresh = RetryBudgets()
print("P14 a new RetryBudgets:", {f.name: getattr(fresh, f.name) for f in dataclasses.fields(fresh)})
fields = sorted(f.name for f in dataclasses.fields(ControlState)) if dataclasses.is_dataclass(ControlState) else sorted(
    getattr(ControlState, "model_fields", {}))
print("    ControlState fields:", fields)
wanted = ("retry", "budget", "recovery", "finding", "no_progress", "generation", "verifier", "replay", "convergence")
print("    ControlState fields naming retry/recovery state:", [f for f in fields if any(w in f for w in wanted)])

# --- P15 -------------------------------------------------------------------------------------------------------------
print("P15 autonomy.max_consecutive_no_progress_attempts default:", AutonomyConfig().max_consecutive_no_progress_attempts)
for configured in (1, 2, 3, 5):
    accepted = AutonomyConfig(max_consecutive_no_progress_attempts=configured).max_consecutive_no_progress_attempts
    print(f"    configured {configured} -> accepted {accepted} -> effective limit (retry_strategy: max(3, value)) {max(3, accepted)}")
