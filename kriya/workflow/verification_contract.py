"""Deterministic pre-check for Runtime Verification's grading step.

Scans captured output for the "[VERIFICATION] PASS"/"[VERIFICATION] FAIL:
<reason>" marker convention (see VERIFICATION_CONTRACT_HEADER in
kriya/workflow/retry_prompts.py) and, if present, returns a grade()-shaped
verdict directly - zero LLM involvement, therefore zero hallucination risk
for that outcome. Added 2026-08-11 after RunVerifierAgent.grade() twice
independently recomputed a wrong "expected" value (a UTF-8 byte length as 13
when it was actually 15) and rejected code that had been correct since the
first attempt, even though the program's own real comparison had already
passed and printed so. The program has the actual values in memory, computed
by real, exact, non-hallucinating arithmetic - this module lets Kriya trust
that computation directly instead of asking an LLM to reconstruct it worse
from flattened stdout.

Returns None when no marker is present, so the caller falls through to
today's LLM-based grade() completely unchanged - this is a soft convention
layered in front of an existing mechanism, not a replacement for it: a goal
whose generated entrypoint does not comply (or that genuinely cannot reduce
to a single verdict, e.g. free-form behavior with no fixed expected shape)
gets exactly today's behavior, never worse.
"""
import re
from enum import Enum
from typing import Any, Dict, Iterable, Optional, Tuple

_VERIFICATION_MARKER_RE = re.compile(
    r"^\[VERIFICATION\]\s+(PASS|FAIL)(?::\s*(.*))?\s*$", re.MULTILINE
)


def extract_contract_verdict(output: str) -> Optional[Dict[str, Any]]:
    """Scans ALL occurrences of the marker, not just the first - a goal whose
    Runtime Verification runs multiple sequential commands (e.g. "add a task,
    then list it") can legitimately produce one line per step. A single FAIL
    anywhere invalidates the whole sequence (AND semantics); only PASS on
    every occurrence counts as passed. Deliberately does not attempt to
    resolve which file is responsible on a FAIL - unlike a compile error or
    RunVerifierAgent.grade()'s own likely_files inference, a marker the
    generated code chose to print carries no locator information Kriya could
    trust without guessing; the retry loop's existing extract_implicated_files()
    fallback (bare basename-in-text matching) still gets a chance against the
    reason text and full captured output either way."""
    matches = list(_VERIFICATION_MARKER_RE.finditer(output))
    if not matches:
        return None

    fails = [m for m in matches if m.group(1) == "FAIL"]
    if fails:
        reasons = "; ".join((m.group(2) or "").strip() or "no reason given" for m in fails)
        return {
            "passed": False,
            "reasoning": (
                f"Deterministic verification contract: the generated program's own "
                f"entrypoint printed \"[VERIFICATION] FAIL\" - {reasons}."
            ),
            "likely_files": [],
        }

    return {
        "passed": True,
        "reasoning": (
            f"Deterministic verification contract: the generated program's own "
            f"entrypoint printed \"[VERIFICATION] PASS\" ({len(matches)} check(s), all passed) - "
            "a real comparison the program performed on real data, not an LLM judgment."
        ),
        "likely_files": [],
    }


def pass_verdict_is_grounded(files_content: Iterable[str]) -> bool:
    """Independent brutal review finding #4 (2026-08-15): a PASS verdict from
    extract_contract_verdict() above is trusted with zero independent check
    that the marker is actually gated behind a real comparison - it's printed
    by the SAME (possibly buggy) model that wrote the implementation, with no
    semantic verification at all. Deliberately narrow, considered fix: per
    VERIFICATION_CONTRACT_HEADER's own instruction (retry_prompts.py), a
    compliant implementation prints PASS "if... [it] confirms" or FAIL
    "otherwise" - so ANY genuine implementation, however it's structured
    (if/else, ternary, whatever), has to literally write the "[VERIFICATION]
    FAIL" string SOMEWHERE in its source for that branch to exist at all. If
    the written files contain a PASS but zero trace of the FAIL string
    anywhere, that's a strong, cheap, language-agnostic signal the "check"
    never actually branches on anything - not a full guarantee (a model could
    still write dead code like `if (true)`), the same "cheap, low-false-
    positive tripwire, not a semantic guarantee" scope every other structural
    check in this codebase already accepts (see find_structural_corruption()'s
    own docstring).

    Deliberately scoped to PASS only - a FAIL verdict has no equivalent risk
    (worst case is one unnecessary retry, not silently shipping broken code
    as "verified"). The caller's job when this returns False is to discard
    the deterministic verdict and fall through to grade() instead (see
    attempt.py's three call sites) - the exact same "never worse than
    today's behavior" fallback this module's own docstring already promises
    for a non-compliant program, just extended to cover an unconvincingly
    compliant one too. Honest tradeoff, not overclaimed: grade() itself has a
    documented history of its own hallucination failure mode (the reason this
    module exists at all) - a fallible second opinion for the suspicious
    minority of cases is still strictly safer than zero scrutiny, not a
    guarantee of correctness."""
    return any("[VERIFICATION] FAIL" in content for content in files_content)


class ContractVerdictState(str, Enum):
    """VER-006 (2026-09-10): the four states a caller of this module can
    actually be in, made explicit instead of collapsed. Before this, a
    caller only ever saw `_extract_grounded_contract_verdict() is None` for
    TWO structurally different situations - no marker existed at all
    (ABSENT), or a marker existed and was deterministically rejected as
    ungrounded (INDETERMINATE_DISTRUSTED) - and both fell through to
    identical, fully-trusted LLM grading. The live incident this closes
    (run `bpwsqscrg`): a bare `print("[VERIFICATION] PASS")` main.py hit
    exactly the DISTRUSTED case, was silently treated the same as ABSENT,
    and the grader (never told the marker was already distrusted) cited
    that same marker as "strong, primary evidence" and passed it."""

    PASS = "PASS"
    FAIL = "FAIL"
    INDETERMINATE_DISTRUSTED = "INDETERMINATE_DISTRUSTED"
    ABSENT = "ABSENT"


def classify_contract_verdict(
    output: str, files_content: Iterable[str],
) -> Tuple["ContractVerdictState", Optional[Dict[str, Any]]]:
    """The single source of truth for interpreting a runtime capture against
    the verification-marker contract - VER-006 containment. Returns
    (state, verdict): `verdict` is the grade()-shaped
    {"passed", "reasoning", "likely_files"} dict for PASS/FAIL/
    INDETERMINATE_DISTRUSTED (DISTRUSTED's own verdict still carries
    `passed: True` - the marker itself said PASS - callers must treat
    INDETERMINATE_DISTRUSTED's `passed` as informational only, never as an
    authoritative gate result); `verdict` is `None` only for ABSENT, where
    there is genuinely nothing deterministic to report.

    files_content must already be read (this module stays IO-free, same as
    pass_verdict_is_grounded's own convention) - callers own reading the
    written files from whatever path (worktree vs workspace) is correct for
    their context."""
    verdict = extract_contract_verdict(output)
    if verdict is None:
        return ContractVerdictState.ABSENT, None
    if not verdict["passed"]:
        return ContractVerdictState.FAIL, verdict
    if not pass_verdict_is_grounded(files_content):
        return ContractVerdictState.INDETERMINATE_DISTRUSTED, verdict
    return ContractVerdictState.PASS, verdict
