"""Deterministic acceptance assertions above compiler/test exit codes."""

import re
from typing import Any, Dict, TYPE_CHECKING, Optional

from kriya.workflow.generation_manifest import FileRole, classify_file_role

if TYPE_CHECKING:
    from kriya.workflow.plan_schema import EngineeringPlan, Subtask


_EXPLICIT_TEST_REQUEST_RE = re.compile(
    r"\b(?:include|add|write|create|provide|with|run)\s+(?:appropriate\s+)?"
    r"(?:"
    # A qualified test - "unit test(s)", "integration tests",
    # "automated test" - singular or plural, always a genuine request.
    r"(?:unit\s+|integration\s+|automated\s+)tests?\b"
    # Bare PLURAL "tests" always counts - "write tests", "include tests" -
    # nothing after it needs checking, plural is unambiguous.
    r"|tests\b"
    # Bare SINGULAR "test" only counts when followed by a noun that
    # itself names a real test-suite artifact (suite/case/class/module/
    # file) - "test suite", "test case".
    r"|test\s+(?:suite|case|cases|class|classes|module|modules|file|files)\b"
    r")(?!\s+where\s+appropriate)",
    # Structural fix, 2026-08-24, replacing a growing per-phrase exclusion
    # list: bare singular "test" used as an attributive adjective before an
    # arbitrary noun ("test values", "test data", "test logic", "test
    # functionality", "test code", ...) describes sample/demo input or ad
    # hoc verification code, not a request for a real test suite - found
    # live 3 times on the same project (2026-08-21 "test values", 2026-08-24
    # "test logic", 2026-08-24 again "test functionality" minutes after the
    # "test logic" fix shipped), always in PLANNER-GENERATED subtask/goal
    # text, never anything the human actually asked for. Each prior fix
    # added one more excluded noun to a blocklist and predicted, correctly,
    # that another paraphrase would defeat it - "functionality" wasn't on
    # the list. Rather than adding a fourth word, this closes the whole
    # class structurally: singular "test" only ever counts when qualified
    # (unit/integration/automated before it, or suite/case/class/module/
    # file after it) or when plural - "test <anything else>" can no longer
    # match regardless of which noun follows, so no future paraphrase of
    # this exact shape can reopen the same bug.
    re.IGNORECASE,
)
_ZERO_TEST_PATTERNS = (
    re.compile(r"collected\s+0\s+items?", re.IGNORECASE),
    re.compile(r"tests?\s+run:\s*0\b", re.IGNORECASE),
    re.compile(r"\bno\s+tests?\s+to\s+run\b", re.IGNORECASE),
    re.compile(r"\bno\s+tests?\s+(?:were\s+)?(?:collected|executed|found|ran)\b", re.IGNORECASE),
    re.compile(r"\bno\s+tests?\s+matching\s+pattern\b", re.IGNORECASE),
    re.compile(r"\b0\s+tests?\s+(?:passed|executed|run)\b", re.IGNORECASE),
    re.compile(r"\btest\b.*\bno[- ]source\b", re.IGNORECASE),
)
# A runtime-verification command whose target simply doesn't exist - found
# live, 2026-08-21 (ignite_qpid_protocol milestone 2/4): RunVerifierAgent.
# judge() inferred `java -cp . ProtocolParserTest` (a test class that was
# never generated), and that judgment stays cached for the rest of the run
# (kriya/workflow/attempt.py's state.cached_run_verification_judgment) -
# already a documented, not-yet-fixed gap (SME review finding #8: "a stale
# inferred entrypoint path from attempt 1 can persist even after the file
# layout changes"). Confirmed live: attempt 5 added a main() method directly
# to ProtocolParser.java specifically to satisfy verification, but the
# cached command still tried to run the never-existent ProtocolParserTest -
# identical failure, 6 attempts straight, budget exhausted. Deliberately
# broad, not Java-only (Python/Ruby covered too) - the cost of a false
# positive here is one extra judge() call, not a wrongly-rejected fix, so
# erring toward re-checking more often than strictly necessary is the right
# tradeoff (contrast _EXPLICIT_TEST_REQUEST_RE above, where a false positive
# actually burns a retry budget on an unwinnable gate).
_MISSING_ENTRYPOINT_PATTERNS = (
    re.compile(r"could not find or load main class", re.IGNORECASE),
    re.compile(r"\bClassNotFoundException\b"),
    re.compile(r"\bNoClassDefFoundError\b"),
    re.compile(r"\bModuleNotFoundError\b"),
    re.compile(r"\bNo module named\b", re.IGNORECASE),
    re.compile(r"\bcannot load such file\b", re.IGNORECASE),
    re.compile(r"\bLoadError\b"),
)
_RUNTIME_BEHAVIOR_RE = re.compile(
    r"\b(?:run|start|launch|execute|print|connect|send|receive|publish|consume|"
    r"put|get|read\s+back|round[- ]trip|exit\s+(?:normally|cleanly)|shut\s*down)\b",
    re.IGNORECASE,
)


def goal_explicitly_requires_tests(goal: str) -> bool:
    return bool(_EXPLICIT_TEST_REQUEST_RE.search(goal or ""))


def _subtask_owns_tests(subtask: "Subtask") -> bool:
    return (
        any(classify_file_role(pf.path) is FileRole.TEST for pf in subtask.planned_files)
        or goal_explicitly_requires_tests(subtask.description)
    )


def subtask_owns_test_obligation(
    overall_goal: str,
    plan: Optional["EngineeringPlan"],
    current_subtask_id: Optional[str],
) -> bool:
    """Whether the CURRENTLY EXECUTING bounded subtask must itself produce a
    runnable test module to satisfy the goal's own test requirement -
    obligation/ownership-aware for a structured plan, never a blind scan over
    the FULL goal text (which, since the authority-isolation fix (§11.13),
    legitimately contains the ENTIRE raw top-level goal on EVERY subtask's
    own ctx.goal, not just the current one's own scope).

    PRV-11 (2026-08-30, live incident): `goal_explicitly_requires_tests(ctx.goal)`
    used to be called directly and unconditionally on every subtask attempt.
    Before the authority-isolation fix, this happened to be harmless for a
    subtask whose own Planner-authored description never mentioned tests
    (ctx.goal was scoped to just that text). Once ctx.goal started ALSO
    carrying the real top-level goal (correct and necessary for
    SpecCompliance/the Developer - see contracts.py's own
    AUTHORITATIVE_GOAL_SECTION_HEADER docstring), this ownership-blind check
    started firing for ANY subtask whenever the goal mentioned tests
    ANYWHERE in the plan - live case: s1 (Customer.java, zero test
    ownership) kept failing TEST ACCEPTANCE FAILURE because s3 (which
    genuinely owns "add unit tests...", with its own planned test file) was
    a LATER, not-yet-reached subtask. In MA8 terms: a FUTURE_ORDERED
    obligation was being treated as CURRENT merely because ITS OWN TEXT
    happened to be visible in ctx.goal - not because plan-scope recovery (or
    anything else) actually reassigned ownership.

    ctx.goal itself is deliberately NOT narrowed back to fix this - the
    authority-isolation fix stays exactly as it is. This function fixes the
    ACTUAL bug: a consumer that was using raw goal text as a proxy for
    subtask ownership, when real plan/ownership state was available and
    should have been consulted instead.

    Rule: the overall goal must require tests at all (unchanged legacy
    signal, still text-based - there is no narrower authoritative source for
    "does this GOAL want tests" than the goal's own words). If a structured
    plan and a current subtask id are both available, defer to whichever
    OTHER subtask already owns test-writing (a planned file that classifies
    as FileRole.TEST, or its own description explicitly requesting tests) -
    when one exists, this subtask's own obligation is FUTURE_ORDERED/
    PAST_ORDERED relative to it, not CURRENT, regardless of whether this
    subtask was just reopened by plan-scope recovery (recovery never
    reassigns verification ownership by itself - see
    revise_plan_for_grounded_scope_owner's own docstring). When NO other
    subtask owns it (including the single-subtask-plan case, and the
    legacy/unpartitioned pipeline where no plan exists at all), the current
    subtask remains responsible - unchanged, backward-compatible legacy
    behavior."""
    if not goal_explicitly_requires_tests(overall_goal):
        return False
    if plan is None or not current_subtask_id:
        return True
    other_subtask_owns_it = any(
        st.id != current_subtask_id and _subtask_owns_tests(st)
        for st in plan.subtasks
    )
    return not other_subtask_owns_it


def goal_requires_runtime_behavior(goal: str) -> bool:
    """Conservative deterministic signal that observable execution is required."""
    return bool(_RUNTIME_BEHAVIOR_RE.search(goal or ""))


def output_confirms_nonzero_test_execution(output: str) -> bool:
    """Fail only on known zero-test evidence; unfamiliar runners remain allowed."""
    return not any(pattern.search(output or "") for pattern in _ZERO_TEST_PATTERNS)


def run_command_targets_missing_entrypoint(output: str) -> bool:
    """True when captured runtime-verification output shows the run command
    itself targeted a class/module/file that doesn't exist - as opposed to a
    genuine application-logic failure. The caller's job (kriya/workflow/
    attempt.py) is to invalidate a cached RunVerifierAgent.judge() judgment
    when this fires, so the NEXT attempt re-infers a fresh command against
    the CURRENT file layout instead of repeating the same broken one."""
    return any(pattern.search(output or "") for pattern in _MISSING_ENTRYPOINT_PATTERNS)


def runtime_verification_infrastructure_reason(result: Dict[str, Any]) -> Optional[str]:
    """Identify a verifier launch failure before behavioral grading.

    These signals describe Kriya's command/execution environment, not the
    candidate's behavior. They must never be localized to an application
    source file or sent to the Developer repair loop.
    """
    output = result.get("output") or ""
    if run_command_targets_missing_entrypoint(output):
        return "runtime command could not load its configured application entrypoint"
    for step in result.get("steps") or []:
        if step.get("exit_code") is None and not step.get("timed_out"):
            return "runtime verification command could not be executed"
    return None


def runtime_application_step_started(result: Dict[str, Any]) -> bool:
    """Whether a nonzero final application result is behavioral evidence.

    Every setup command must have succeeded, the final process must have
    launched and exited normally (rather than timing out), and its output
    must not carry a known entrypoint/classpath launch failure. Only then may
    semantic grading recognize an expected invalid-input rejection.
    """
    if runtime_verification_infrastructure_reason(result) is not None:
        return False
    steps = result.get("steps") or []
    if not steps:
        return False
    if any(step.get("exit_code") != 0 or step.get("timed_out") for step in steps[:-1]):
        return False
    final = steps[-1]
    return final.get("exit_code") is not None and not final.get("timed_out")
