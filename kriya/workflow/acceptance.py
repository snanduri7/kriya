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


def goal_explicitly_requires_tests(goal: str) -> bool:
    return bool(_EXPLICIT_TEST_REQUEST_RE.search(goal or ""))


def output_confirms_nonzero_test_execution(output: str) -> bool:
    """Fail only on known zero-test evidence; unfamiliar runners remain allowed."""
    return not any(pattern.search(output or "") for pattern in _ZERO_TEST_PATTERNS)
