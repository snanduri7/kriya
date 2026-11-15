"""A1 model-free probe (read-only): blocker identity facts on the pure owners. Run with PYTHONPATH=<closure worktree>."""
import os
import sys

import kriya
from kriya.workflow.failure import Failure, FileLocation
from kriya.workflow.failure_grounding import build_failure_signature
from kriya.workflow.open_findings import replaces_open_finding, update_open_findings
from kriya.workflow.retry_policy import (
    charge_failed_attempt,
    observe_failure_family,
    reset_scoped_budgets_for_new_family,
)
from kriya.workflow.state import RetryBudgets

EVIDENCE = sys.argv[1]
TEST_FILE = "src/test/java/com/example/ProtocolParserTest.java"


def gate_text(attempt):
    section = open(os.path.join(EVIDENCE, f"ws4_prev_error_section_attempt{attempt}.txt"), encoding="utf-8").read()
    start = section.index("Authoritative validator evidence:\n") + len("Authoritative validator evidence:\n")
    ends = [i for i in (section.find("\n=== ", start), section.find("\nThis is a TARGETED", start)) if i >= 0]
    return section[start:min(ends)]


def failure(raw, line, type_="targeted_test"):
    return Failure(type=type_, message=raw[-400:], raw_output=raw, likely_files=[TEST_FILE],
                   file_locations=[FileLocation(TEST_FILE, line)])


print("kriya imported from:", os.path.dirname(kriya.__file__))
COMPILE, ASSERT_75 = gate_text(2), gate_text(3)
ASSERT_76 = ASSERT_75.replace("ProtocolParserTest.java:75)", "ProtocolParserTest.java:76)").replace(
    "testEncodeDecodeWithLongBody:75 ", "testEncodeDecodeWithLongBody:76 ")
OTHER_TEST = ASSERT_75.replace("testEncodeDecodeWithLongBody", "testADifferentTestMethod")

sig = lambda raw: build_failure_signature("targeted_test", raw)  # noqa: E731
print("P1 testCompile vs assertion, same gate type and file -> same signature?", sig(COMPILE) == sig(ASSERT_75))
print("P2 assertion at line 75 vs line 76 -> same signature?", sig(ASSERT_75) == sig(ASSERT_76))
print("P3 same assertion text in a DIFFERENT test method of the same file -> same signature?", sig(ASSERT_75) == sig(OTHER_TEST))
print("   signature kind:", sig(ASSERT_75)[1][1], "| locations:", sig(ASSERT_75)[1][0])

f2, f5 = failure(ASSERT_75, 75), failure(ASSERT_75, 75)
open2 = update_open_findings([], f2, 2)
open5 = update_open_findings(open2, f5, 5)
print("P4 identical failure recorded at attempts 2 and 5 -> first_attempt:", open2[0].first_attempt, "->", open5[0].first_attempt,
      "| findings equal?", open2[0] == open5[0])
open8 = update_open_findings(open5, failure(ASSERT_76, 76), 8)
print("P5 line 75 -> 76 -> first_attempt:", open8[0].first_attempt, "| same locations?", open5[0].locations == open8[0].locations)
print("P6 OpenFinding fields:", sorted(open2[0].__dataclass_fields__))

refusal = Failure(type="context_budget_unsatisfiable", message="x", raw_output="x", source="orchestrator",
                  diagnostics={"reason_code": "CONTEXT_BUDGET_UNSATISFIABLE"})
print("P7 a context-budget refusal replaces the open finding?", replaces_open_finding(refusal),
      "| open set unchanged?", update_open_findings(open5, refusal, 6) == open5)

b = RetryBudgets(targeted_retry_count=3, fallback_targeted_attempted=True)
observe_failure_family(b, None, sig(COMPILE))   # attempt 1 records family A
new_family = observe_failure_family(b, sig(COMPILE), sig(ASSERT_75))
if new_family:
    reset_scoped_budgets_for_new_family(b)
charge_failed_attempt(b, attempt_mode="targeted", plan_scope_conflict=False, failure_family_changed=new_family)
print("P8 genuinely new family after a targeted attempt -> new_family:", new_family, "| targeted:", b.targeted_retry_count,
      "| fallback_targeted_attempted:", b.fallback_targeted_attempted, "| full-set:", b.retry_count)
again = observe_failure_family(b, sig(ASSERT_75), sig(COMPILE))
print("P9 return to a family seen earlier (A -> B -> A) -> new_family:", again)
