"""Single decision point for "which file(s) is this failure about" - the
Developer + Quality Gates retry loop's file-attribution logic, previously
re-derived independently at four separate call sites (verification_contract.py's
deliberate no-guess on a deterministic-contract FAIL, failure_grounding.py's
extract_implicated_files(), retry_strategy.py's `failure.likely_files or
extract_implicated_files(...)` fallback, and attempt.py/agent.py's implicit
"implicated_files=None means every file is implicated" full-set default).
Four independently-correct pieces of logic with no shared contract is exactly
the shape that lets a new failure type silently bypass whatever fallback
strategy exists - this module exists to close that structurally, not just fix
the one instance that surfaced it.

Built 2026-08-13 after a live, reproduced failure
(spikes/eval_harness/runs/20260812-222120/logs/ignite_qpid_protocol.stdout.log):
a deterministic verification-contract FAIL correctly declined to guess a file
(by design - extract_contract_verdict() always returns likely_files=[] on
FAIL, see its own docstring), which fell through to a blind, unordered
full-set walk. The model's own fix-analysis text was correct every single
time it ran ("the ProtocolParser decode method incorrectly handles the
3-byte dataLength field..."), but got attached to pom.xml, then
BrokerServer.java - not because either was chosen as a target, but because
full-set mode processes files in whatever order the file-list call returns,
and the run was killed by the external 1200s timeout before ever reaching
ProtocolParser.java (9 files, against the slow fallback model
devstral-small-2:24b, some single completions took 90s-6min).

Four tiers, most-trusted first, deliberately reusing rather than
reimplementing the middle two (both already evidenced-reliable):

  0. "self_diagnosis" - NEW (2026-08-13). The model's own FIX ANALYSIS text
     from the immediately-preceding attempt, when it names a DIFFERENT known
     file than the one it was attached to (extract_self_diagnosed_files(),
     below), on a CONFIRMED repeat of the same failure. Ranked above
     locator/judge: found live (spikes/eval_harness/runs/
     attribution-fix-validation-2) that a precise stack-trace locator kept
     routing every retry to the file where an exception was THROWN
     (ProtocolApp.java:51, a JMS connection-open call), while the model's
     own analysis correctly named the file where the actual misconfiguration
     LIVED (qpid-initial-config.json, a missing defaultAlias entry - already
     documented in the active qpid skill). A locator identifies where a
     failure surfaced, not necessarily where the fix belongs - a real,
     generalizable gap for any bug where root cause and surfacing site
     differ, not just config-vs-code. See kriya/workflow/retry_strategy.py
     for the signature-matching that gates this - a stale diagnosis from an
     unrelated failure must never override a fresh locator.
  1. "locator"/"judge" - failure.likely_files is already populated at the
     raise site for every QualityGateFailure that has real evidence
     (_build_quality_gate_failure() in failure_grounding.py combines a
     precise file:line locator with RunVerifierAgent.grade()'s own
     already-validated likely_files; an anchored-edit failure sets
     likely_files=[filepath] directly). When empty (general_error, or any
     source that never ran through that construction), fall back to a
     fresh extract_implicated_files() scan - the exact fallback
     retry_strategy.py used to do inline, moved here so it's one tested
     path instead of two. Tier label/confidence for observability is
     "locator"/high when a genuine file:line match backs the result,
     "judge"/medium otherwise (already-validated inference or a plain
     substring match) - the upstream data doesn't structurally preserve
     which one contributed, so this is a best-effort label, not a claim
     of perfect provenance.
  2. "triage" - NEW. Fires only when the above finds nothing at all (the
     verification_contract.py no-guess case, or any other silent
     failure). One short classification call - NOT a fix-analysis+regen
     call - asking which known file is most likely responsible, given a
     short skeleton of each candidate file (skeletonize_code(...,
     tier="signatures"), reused from context_budget.py - per-file
     free-text descriptions from the Architect's design were considered
     but confirmed NOT to survive anywhere structured, only as opaque
     checkpoint-blob prose, so skeletons are the honest cheap option).
     Rides the SAME model-escalation ladder the current retry attempt is
     already on (resolve_fallback_model(), below) rather than inventing a
     separate "always use the fast model" policy - deliberate: model
     escalation exists specifically because the primary model can get
     stuck reasoning the same wrong path on a given failure, and a fast
     classification call answered by that same stuck model risks
     inheriting the same bias. The speed win instead comes from the call
     itself being small (one short question, not a full-file
     regeneration), which stays cheap even on a slow fallback model.
  3. "full_set" - honest "found nothing" fallback, unchanged behavior:
     empty files list, low confidence. Matches
     verification_contract.py's own deliberate philosophy for the case
     that motivated this module - an honest "I don't know" beats a wrong
     guess, but a full-set walk that happens anyway should at least know
     it's flying blind.

The result's `files` field is exactly what retry_strategy.py already assigns
to state.last_implicated_files - unchanged downstream contract, so the
existing targeted-retry path (apply_anchored_edits(), attempt.py:93's
use_targeted check) picks up a triage-confirmed file automatically. No new
plumbing needed there.
"""
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, List, Literal, Optional

from kriya.workflow.failure import Failure
from kriya.workflow.failure_grounding import extract_error_source_locations, extract_implicated_files

logger = logging.getLogger(__name__)

AttributionTier = Literal["self_diagnosis", "locator", "judge", "triage", "full_set"]
Confidence = Literal["high", "medium", "low"]


@dataclass
class AttributionResult:
    tier: AttributionTier
    files: List[str]
    confidence: Confidence
    reasoning: str


def resolve_fallback_model(retry_count: int, chain: list) -> Optional[Any]:
    """Same escalation-ladder formula as attempt.py's full-set branch
    (`chain[min(retry_count - 1, len(chain) - 1)]` once retry_count > 0),
    extracted here so both call sites share one implementation instead of
    two copies that can silently drift apart. Returns None (primary model)
    when retry_count is 0 or there's no chain configured.

    This is deliberately the ONLY place this formula should exist - a new
    caller needing "what model is THIS retry attempt on" (e.g. this
    module's own triage tier) must call this, not invent its own model-
    selection policy. That constraint isn't incidental: model escalation
    exists specifically because the primary model can get stuck reasoning
    the same wrong path on a given failure, and handing a fast
    classification question to that same stuck model risks inheriting the
    same bias - so triage rides whatever model generation is already on,
    never a separately-chosen "fast" model."""
    if retry_count <= 0 or not chain:
        return None
    idx = min(retry_count - 1, len(chain) - 1)
    return chain[idx]


def extract_self_diagnosed_files(files: List[dict], known_files: List[str]) -> List[str]:
    """Reads the model's own FIX ANALYSIS text (threaded out of
    DeveloperAgent.run_generation() as an optional "analysis" key on each
    file dict, kriya/agents/agent.py's `_fill_missing_content()`) and checks
    whether it names a DIFFERENT known file than the one it's attached to -
    reusing extract_implicated_files(), the exact same basename-matching
    primitive the locator/judge tier already uses, just pointed at a new
    input. A self-mention (the analysis for App.java happening to say
    "App.java") isn't signal - only union results EXCLUDING the file the
    analysis is attached to.

    Found live, 2026-08-13 (ignite_qpid_protocol validation-2): a stack-trace
    locator confidently routed every retry to ProtocolApp.java (the file
    where a JMS connection exception was thrown), but the model's own fix-
    analysis on that same file correctly named qpid-initial-config.json's
    missing defaultAlias entry as the real cause - already documented
    verbatim in the active qpid skill. Nothing read that text again after
    logging it, so the next retry re-derived the same wrong locator forever.
    Generalizes beyond this one case: in a full-set attempt with no
    narrowing at all, every implicated file gets its own analysis - if any
    one of them names a different real cause, this catches it too."""
    implicated: List[str] = []
    for file_entry in files:
        analysis = file_entry.get("analysis")
        if not analysis:
            continue
        own_path = file_entry.get("filepath")
        candidates = [f for f in known_files if f != own_path]
        for f in extract_implicated_files(analysis, candidates):
            if f not in implicated:
                implicated.append(f)
    return implicated


def _attribute_from_existing_evidence(failure: Failure, known_files: List[str]) -> Optional[AttributionResult]:
    raw_text = failure.raw_output or failure.message

    # A real file:line locator always wins - both the tier label AND which
    # files get used - over failure.likely_files/a substring scan, even if
    # likely_files is already non-empty (e.g. stale/judge-provided from a
    # DIFFERENT signal than the precise locator this failure's own text
    # actually carries). Mirrors extract_implicated_files()'s own internal
    # "prefer a locator" precedence, just surfaced here as an explicit tier.
    located_basenames = {b for b, _ in extract_error_source_locations(raw_text)}
    if located_basenames:
        locator_files = [f for f in known_files if os.path.basename(f) in located_basenames]
        if locator_files:
            return AttributionResult(
                tier="locator", files=locator_files, confidence="high",
                reasoning="Precise file:line locator found in the failure output.",
            )

    files = list(failure.likely_files) if failure.likely_files else []
    if not files:
        files = extract_implicated_files(raw_text, known_files)
    if not files:
        return None
    return AttributionResult(
        tier="judge", files=files, confidence="medium",
        reasoning="Already-validated likely_files (e.g. RunVerifierAgent.grade()'s own inference, "
        "or an anchored-edit's known filepath) or a filename substring match, with no precise line locator.",
    )


_TRIAGE_SYSTEM_PROMPT = (
    "You are triaging a build/test/runtime failure for a code-generation system. "
    "You will be shown the failure text and a short skeleton of each candidate "
    "file. Identify which file(s) are MOST LIKELY responsible for the failure. "
    "Respond with ONLY a JSON object of this exact shape: "
    '{"files": ["path/one.ext"], "confidence": "high|medium|low", "reasoning": '
    '"one sentence"}. If you genuinely cannot tell from the given information, '
    'return an empty "files" list and "confidence": "low" rather than guessing.'
)


async def _tier_triage(
    failure: Failure,
    known_files: List[str],
    retry_count: int,
    chain: list,
    llm,
    file_content_provider: Callable[[str], Optional[str]],
) -> Optional[AttributionResult]:
    from kriya.workflow.context_budget import skeletonize_code

    fallback = resolve_fallback_model(retry_count, chain)
    model_override = fallback.model if fallback else None
    base_url_override = fallback.base_url if fallback else None
    api_key_override = fallback.api_key if fallback else None

    skeleton_sections = []
    for filepath in known_files:
        content = file_content_provider(filepath)
        if not content:
            skeleton_sections.append(f"--- {filepath} ---\n(no content available)")
            continue
        try:
            skeleton = skeletonize_code(content, filepath, "signatures")
        except Exception:
            skeleton = content[:400]
        skeleton_sections.append(f"--- {filepath} ---\n{skeleton}")

    raw_text = failure.raw_output or failure.message
    user_prompt = (
        f"=== Failure ===\n{raw_text[:4000]}\n\n"
        f"=== Candidate files ===\n" + "\n\n".join(skeleton_sections)
    )

    try:
        response = await llm.complete(
            _TRIAGE_SYSTEM_PROMPT, user_prompt,
            json_mode=True,
            model_override=model_override,
            base_url_override=base_url_override,
            api_key_override=api_key_override,
            max_tokens_override=300,
        )
        parsed = json.loads(response)
        files = [f for f in parsed.get("files", []) if f in known_files]
        confidence = parsed.get("confidence") if parsed.get("confidence") in ("high", "medium", "low") else "low"
        reasoning = str(parsed.get("reasoning", ""))[:500]
    except Exception as ex:
        logger.warning(f"Attribution triage call failed or returned unparseable output: {ex}")
        return None

    if not files:
        return None
    return AttributionResult(tier="triage", files=files, confidence=confidence, reasoning=reasoning)


async def attribute_failure(
    failure: Failure,
    known_files: List[str],
    retry_count: int,
    chain: list,
    llm,
    file_content_provider: Callable[[str], Optional[str]],
    self_diagnosed_files: Optional[List[str]] = None,
) -> AttributionResult:
    """The one entry point every retry site should call instead of
    independently re-deriving "which file". Always returns a result - the
    honest, low-confidence full_set case (files=[]) when nothing narrows the
    failure, never a silent None that a caller might forget to handle.

    self_diagnosed_files, when passed, MUST already be gated by the caller
    to only the case where the CURRENT failure is a confirmed repeat of the
    exact failure the self-diagnosis was generated in response to (see
    kriya/workflow/retry_strategy.py's signature-matching against
    state.last_self_diagnosis) - this function trusts it unconditionally
    once passed, since a stale diagnosis from an unrelated failure would be
    actively wrong to prefer over a fresh locator. Ranked ABOVE locator/judge
    deliberately: a locator that already led to one failed fix attempt on
    this exact repeat is weaker evidence than the model's own stated
    disagreement with that target."""
    if self_diagnosed_files:
        return AttributionResult(
            tier="self_diagnosis", files=self_diagnosed_files, confidence="high",
            reasoning="The model's own FIX ANALYSIS from the immediately-preceding attempt "
            "(responding to this exact same failure recurring) named a different file as the "
            "real cause.",
        )

    result = _attribute_from_existing_evidence(failure, known_files)
    if result:
        return result

    # No point spending a real completion call asking "which file" when
    # there's at most one candidate to begin with - the answer, if any,
    # is already unambiguous by elimination, no LLM needed. Also skips
    # triage for a genuinely unresolvable failure (an environment/toolchain
    # crash with zero file evidence, classified separately by
    # classify_environment_failure() upstream in retry_strategy.py) - a
    # failure with no locator, no likely_files, AND no environment
    # classification usually means there's nothing file-specific to reason
    # about at all, not that triage would find something a cheaper check
    # missed.
    if len(known_files) > 1:
        result = await _tier_triage(failure, known_files, retry_count, chain, llm, file_content_provider)
        if result:
            return result

    return AttributionResult(
        tier="full_set", files=[], confidence="low",
        reasoning="No locator, no likely_files, and triage found nothing actionable.",
    )


def read_worktree_file(worktree_path: str, filepath: str) -> Optional[str]:
    """Default file_content_provider for attribute_failure()'s triage tier -
    reads a file's current content from the worktree, same tolerance as
    failure_grounding.py's _capture_failed_content()/_build_error_source_context()
    (best-effort, a file that can't be read is silently skipped rather than
    raising, since a missing/deleted file just means one less candidate
    skeleton for triage to look at)."""
    try:
        with open(os.path.join(worktree_path, filepath), "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except Exception as ex:
        logger.debug(f"Failed to read '{filepath}' from worktree for attribution triage: {ex}")
        return None
