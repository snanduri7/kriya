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
from typing import Any, Dict, Optional

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
