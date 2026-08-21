"""Deterministic acceptance assertions above compiler/test exit codes."""

import re


_EXPLICIT_TEST_REQUEST_RE = re.compile(
    r"\b(?:include|add|write|create|provide|with|run)\s+(?:appropriate\s+)?"
    r"(?:unit\s+|integration\s+|automated\s+)?tests?\b(?!\s+where\s+appropriate)"
    # "test" used as an attributive adjective before a data noun ("test
    # values", "test data") describes sample/demo input, not a request for a
    # test suite - found live, 2026-08-21 (protocol_encoder_java milestone
    # 5): the Planner's own milestone goal read "initializes it with test
    # values, encodes it..." (ordinary English for "some sample values"),
    # which satisfied "with (appropriate)? (unit|integration|automated)?
    # tests?" purely because "test" is a whole-word match for "tests?" (the
    # trailing 's' is optional) - the milestone was never about writing
    # tests at all, but Quality Gates demanded a runnable test module for it
    # anyway and burned its entire retry budget on an unwinnable gate before
    # the milestone failed outright.
    r"(?!\s+(?:values?|data)\b)",
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


def goal_explicitly_requires_tests(goal: str) -> bool:
    return bool(_EXPLICIT_TEST_REQUEST_RE.search(goal or ""))


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
