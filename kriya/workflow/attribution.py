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

--------------------------------------------------------------------------
MODULE SCOPE, 2026-08-14 consolidation - this docstring IS the taxonomy.
--------------------------------------------------------------------------
Every "which file does this concern" question the Developer + Quality Gates
retry loop asks now lives in this one module, colocated specifically so a
future gap is visible by inspection instead of by the next live failure.
Researched against SWE-bench-style fault-localization failure studies and
real Aider/Cline/Copilot GitHub issues, cross-referenced against Kriya's
actual code (full citations in the session that did this research).

  Question                          | Mechanism                              | Tested?
  -----------------------------------|-----------------------------------------|--------
  A. Which known file does a         | attribute_failure() - self_diagnosis >  | tests/test_attribution.py
     FAILURE implicate?              | locator/judge > triage > full_set       |
  B1. Did a failed edit aim at the   | find_misdirected_edit_target()          | tests/test_workflow.py
      WRONG file?                    |                                          |
  B2. Did an edit actually implement | find_edits_ignoring_own_diagnosis()     | tests/test_workflow.py
      its own stated diagnosis?      | (addition signal + removal signal)      |
  B3. Did an edit leave the          | find_edits_ignoring_reported_line()     | tests/test_workflow.py
      COMPILER-reported line         |                                          |
      unchanged?                     |                                          |
  E. Is a required build manifest    | _detect_missing_build_manifest()        | tests/test_workflow.py
     MISSING entirely (never         |                                          |
     written, not lost)?             |                                          |

B1/B2/B3 moved here from kriya/workflow/edit_safety.py on 2026-08-14 (that
module now holds only mechanical edit-safety - does an edit apply cleanly,
does the result look structurally sound - never "which file"; see its own
docstring). _detect_missing_build_manifest() moved here from
kriya/workflow/toolchain.py the same day. Deliberately NOT folded in:
apply_anchored_edits()/find_structural_corruption() (edit_safety.py stays
the mechanical-edit-safety home, a different question from "which file");
file_resolution.py's find_missing_expected_files()/extract_target_test()
(a different question - "does an EXPECTED file exist", not "which file does
a failure/edit concern"); _build_error_source_context() (failure_grounding.py
- reuses tier A's own locators for prompt display, not an independent
attribution mechanism).

Fixed as part of this consolidation, found by code inspection (no live
incident): failure_grounding.py's _build_error_source_context() and
_resolve_file_locations() each independently built a basename->file DICT
(`{os.path.basename(f): f for f in known_files}`), silently keeping only the
LAST file when two known files share a basename (e.g. a same-named class in
two packages) - a FileLocation or source-context snippet could attach to the
wrong file's path. extract_implicated_files() was never affected (it already
scans every known file via a list comprehension, not a basename dict), which
is exactly why this went unnoticed. Both call sites now share one helper,
_files_by_basename() (failure_grounding.py), that keeps every matching file.

Known, explicitly untested gaps (surfaced by this same taxonomy review, not
acted on in this increment - flagged here rather than silently dropped):
  - Multi-file coordination: targeted retries are already soft-scoped (the
    prompt permits touching files beyond likely_files), but nothing asserts
    a genuinely-coordinated 2-file fix actually gets produced/accepted in
    one attempt (SWE-bench research: ~52% of correct patches need multi-file
    coordination).
  - Chained-exception depth: extract_error_source_locations() extracts every
    file:line match with no concept of "Caused by:" chain depth (outer
    exception frame vs. root-cause frame) - unclear whether a multi-file
    chained-exception case is handled correctly, never tested either way.
"""
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Literal, Optional

from kriya.workflow.edit_safety import normalize_whitespace
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

# A tight budget here isn't just "keep this cheap" - it can silently starve the
# call outright. Root-caused live (2026-08-13, spikes/tool_call_developer's
# run_spike_real_triage_shape.py, 3/3 exact reproduction): some models reason
# internally before ever committing to the requested JSON regardless of
# whether Kriya's own llm_chain config classifies them as "reasoning" (that
# flag only gates complete()'s own <think>-stripping and its 12288-token
# floor for models IT thinks reason - a model can reason silently without
# being classified that way, and then the completion hits max_tokens with
# nothing ever written to content). 300 reproduced an empty response 3/3
# times against gpt-oss:20b classified reasoning=False; 2000 was clean 3/3
# (no empty response) in the same reproduction - the actual JSON answer here
# is only a few dozen tokens, this budget exists to give room for whatever
# reasoning happens first, not for the answer itself.
_TRIAGE_MAX_TOKENS = 2000


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
            max_tokens_override=_TRIAGE_MAX_TOKENS,
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


# --------------------------------------------------------------------------
# B. Edit -> file: did a failed/applied edit actually concern the right
# file, and did it actually implement what it claimed? Moved here from
# kriya/workflow/edit_safety.py on 2026-08-14 - see this module's own
# docstring taxonomy for the full B1/B2/B3 breakdown and why they live here
# now instead of alongside apply_anchored_edits()/find_structural_corruption().
# --------------------------------------------------------------------------

def _search_block_matches(content: str, search_block: str) -> bool:
    """Whitespace-tolerant "does this search block exist anywhere in this
    content at all" check - shares apply_anchored_edits()'s own two-tier
    matching philosophy (an exact substring match first, then a blank-line-
    tolerant, stripped-line subsequence match) but answers a simpler
    question: containment, not uniqueness or splicing (no position is ever
    needed by this function's only caller, find_misdirected_edit_target()
    below - it only needs a yes/no)."""
    if not search_block:
        return False
    if search_block in content:
        return True
    search_norm_lines = [ln.strip() for ln in search_block.splitlines() if ln.strip()]
    if not search_norm_lines:
        return False
    content_norm_lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    n = len(search_norm_lines)
    return any(
        content_norm_lines[i:i + n] == search_norm_lines
        for i in range(len(content_norm_lines) - n + 1)
    )


def find_misdirected_edit_target(
    edits: List[Dict[str, str]], orig_text: str, other_files: Dict[str, str]
) -> Optional[str]:
    """Called from attempt.py's `except ValueError as anchor_ex:` handler, right
    after apply_anchored_edits() has ALREADY raised a "matched 0 times" failure -
    this function asks one more question before accepting that failure at face
    value: does the search block that failed to match its intended file actually
    exist, verbatim or near-verbatim, inside a DIFFERENT known file instead? If
    so, that's direct, ground-truth evidence the model's edit was aimed at the
    wrong file, not that its content was simply wrong.

    Found live, 2026-08-14 (spikes/eval_harness/runs/a-3, ignite_qpid_protocol):
    a runtime verification failure's stack trace -
    `at com.example.ProtocolApp.testProtocolLayer(ProtocolApp.java:73)` - has
    exactly one locatable frame, because the failure is an EXPLICIT
    `if (!protocol.equals(decoded)) throw new RuntimeException(...)` check
    written directly in ProtocolApp.java (the exact pattern Kriya's own
    VERIFICATION_CONTRACT_HEADER prompts every goal to write). extract_implicated_
    files() correctly scoped the resulting targeted retry to ProtocolApp.java -
    the only file with a locator - but the REAL bug was silently-wrong data
    returned from ProtocolParser.encode() (a local `dataLength` variable
    computed from body.length that was never written back to the encoded
    header the same way on every call path), a method that doesn't itself
    throw and so never appears in the stack trace at all. The model's own FIX
    ANALYSIS text was textbook-correct ("...in the encode method, we're not
    using the protocol.dataLength field - instead we're using body.length
    directly...") but, constrained to editing only ProtocolApp.java, it wrote a
    SEARCH block that was actually ProtocolParser.encode()'s own body - which
    can never match ProtocolApp.java's real content, guaranteeing "matched 0
    times" and burning the whole retry attempt (confirmed directly from the
    real captured kriya.log and worktree file contents, not inferred).

    This is a different trigger of the same general shape the 2026-08-10
    _NO_CHANGE_NEEDED_RE incident (kriya/agents/agent.py) closed for a stack
    trace that names TWO files (one needing no change) - here the stack trace
    only ever names ONE file, because an explicit-throw runtime check
    structurally can't produce a locator for whatever file's logic actually
    computed the wrong value. No amount of improving extract_implicated_files()
    itself can fix this: the file that needs the fix is never named anywhere in
    the error text, precisely because it fails silently rather than throwing.
    The one piece of ground truth Kriya already has at this exact moment - the
    edit's own search-block bytes, and the real on-disk content of every OTHER
    file already written this run - is what this function checks instead,
    sidestepping the need to name the right file from error text at all.

    Deliberately generic: no Java/Ignite/protocol-specific logic anywhere here,
    just whitespace-tolerant text containment across two known texts, reusing
    apply_anchored_edits()'s own tolerant-match philosophy via
    _search_block_matches() above. Only considers an edit whose search block did
    NOT already match its own intended file (orig_text) - an edit that matched
    fine is never the culprit, regardless of what else it might coincidentally
    also match elsewhere. Returns the first other file whose content contains a
    failing edit's search block, or None if no failing edit's search block is
    found anywhere else either (the caller falls through to the existing,
    unchanged generic "matched 0 times" failure in that case - this is purely
    additive, never a regression on the prior behavior)."""
    for edit in edits:
        search_block = edit.get("search") or ""
        if not search_block or _search_block_matches(orig_text, search_block):
            continue
        for other_path, other_content in other_files.items():
            if _search_block_matches(other_content, search_block):
                return other_path
    return None


def find_edits_ignoring_reported_line(
    original_content: str, edits: List[Dict[str, str]], filepath: str, error_context: str
) -> List[int]:
    """Layer 1 pre-flight check, added 2026-08-07 in direct response to a real
    live failure (ignite_qpid_person): a targeted retry's own SEARCH block
    spanned the exact line the compiler reported (javac's universal
    file:[line,col] locator - the same source extract_error_source_locations()
    already uses to build _build_error_source_context()'s prompt), yet the
    REPLACE text at that same relative position was byte-identical to SEARCH.
    The edit applied cleanly - no anchor-match failure, Kriya's own plumbing
    worked - but it never actually changed the line the compiler pointed at,
    so the IDENTICAL compile error recurred verbatim on the very next attempt,
    burning a full, expensive compile-and-fail cycle to discover something
    checkable for free beforehand.

    Deliberately narrower than "did the file change anywhere": only flags a
    reported line when SOME edit's own search block already claimed
    responsibility for it (by including it in what it chose to match
    against). An edit whose search block doesn't span the reported line at
    all is a legitimate alternative fix (e.g. correcting a variable's
    generic-typed declaration several lines above instead of its usage line,
    which needs no change at the usage line itself) and is never flagged -
    this catches the model contradicting its OWN stated scope, not every
    possible way of fixing the underlying bug.

    Locates each edit's absolute line offset against the ORIGINAL, pre-edit
    content (not the progressively-edited content apply_anchored_edits()
    itself walks through as it applies edits in sequence), so line numbers
    always line up with what the compiler actually reported regardless of how
    many edits precede this one or whether an earlier edit shifted the line
    count. Returns the reported line number(s) found unaddressed, empty if
    none (including the common case where the error has no locatable line at
    all, or this file isn't the one it names)."""
    locations = extract_error_source_locations(error_context)
    reported_lines = {line for fname, line in locations if fname == os.path.basename(filepath)}
    if not reported_lines:
        return []

    orig_lines = original_content.splitlines()
    ignored: List[int] = []
    for edit in edits:
        search_block = edit.get("search") or ""
        search_lines = search_block.splitlines()
        if not search_lines:
            continue

        norm_search = normalize_whitespace(search_block)
        matched_start = -1
        for i in range(len(orig_lines) - len(search_lines) + 1):
            window = orig_lines[i:i + len(search_lines)]
            if normalize_whitespace("\n".join(window)) == norm_search:
                matched_start = i
                break
        if matched_start == -1:
            # Not this check's job to explain - apply_anchored_edits() itself
            # already raises a precise "matched 0 times" failure for an edit
            # whose search block can't be located at all.
            continue

        replace_lines = (edit.get("replace") or "").splitlines()
        for line_no in reported_lines:
            offset = line_no - 1 - matched_start
            if not (0 <= offset < len(search_lines)):
                continue
            old_line = search_lines[offset].strip()
            new_line = replace_lines[offset].strip() if offset < len(replace_lines) else None
            # Skip trivially short lines (a lone brace, a blank line) - too
            # easy to coincidentally "reappear" unchanged to be a meaningful
            # signal on their own.
            if len(old_line) >= 8 and old_line == new_line and line_no not in ignored:
                ignored.append(line_no)
    return ignored


_ANALYSIS_QUOTED_SPAN_RE = re.compile(r"`([^`]+)`")

# A bare relative file path (multiple `/`-separated segments, ending in a file
# extension) - used to exclude a self-referential filepath quote from
# find_edits_ignoring_own_diagnosis()'s "must be new" evidence requirement. See
# that function's own inline comment for the full incident. Deliberately
# structural, not a lookup against the real expected-files list - keeps this
# check self-contained without threading a filepath through the function
# signature, at the cost of also excluding a genuine code quote that happens to
# look like a bare path (accepted tradeoff, see the same comment).
_BARE_FILE_PATH_RE = re.compile(r"^[\w.\-]+(?:/[\w.\-]+)+\.[a-zA-Z0-9]+$")

# Phrases that explicitly mark the quoted span right after them as the
# PROBLEM being moved away from, not the fix - "instead of `var`", "never a
# raw or var-inferred cache handle" style language already seen verbatim in
# real captured fix-analysis text this session, and in skills/binary-wire-
# protocol/rules.txt's own wording ("do NOT use ByteBuffer.putInt()... for
# that field"). Deliberately a small, closed set of unambiguous removal-
# signaling phrases - not a general negation/NLP detector - see
# find_edits_ignoring_own_diagnosis's own docstring for why this exists as a
# second, independent signal from the addition-check above it.
_REMOVAL_PHRASE_RE = re.compile(
    r"(?:instead of|rather than|not|never|remove|removing|stop using|no longer)\s+(?:using\s+)?`([^`]+)`",
    re.IGNORECASE,
)

# Found live, 2026-08-16/17 (ignite_qpid_person, run b-10i): "instead of" is
# ambiguous between an INSTRUCTION ("do Y instead of `X`" - X is what to
# discard, the shape _REMOVAL_PHRASE_RE was built for) and a RESULT
# DESCRIPTION ("returns `Object` instead of `Person`" - Person is the
# correct/desired value, not something to remove; it's expected to still
# appear, unchanged, in a correct fix). A missing-cast diagnosis phrased as
# "`cache.get(1)` returns an `Object` type instead of `Person`" got its
# correct, quoted "Person" wrongly captured as a removal target - `Person`
# legitimately still appears in a correct cast-insertion fix
# (`(Person) cache.get(1)`), so the stale-removal check rejected an
# already-correct edit. Narrowly scoped to the confirmed live shape: a
# result-describing verb (returns/produces/gives/yields/is/was) appearing
# before "instead of" in the SAME clause (no sentence boundary crossed)
# means the term after it is the DESIRED value, not a removal target -
# exclude it from removal_quoted rather than trying to make
# _REMOVAL_PHRASE_RE itself direction-aware (that would risk the same
# fragility the addition signals already learned to avoid - see the
# per-pair scoping comment above).
_RESULT_DESCRIBING_INSTEAD_OF_RE = re.compile(
    r"(?:returns?|returned|produces?|produced|gives?|gave|yields?|yielded|\bis\b|\bwas\b)"
    r"[^.\n]{0,60}instead of\s+(?:using\s+)?`([^`]+)`",
    re.IGNORECASE,
)


def _still_contains(needle: str, haystack: str) -> bool:
    """Word-boundary-aware containment check, only anchoring `\\b` at
    whichever edge of `needle` is itself a word character (alnum/underscore).
    A plain `\\b...\\b` wrapper was tried first and found to fail in both
    directions: a bare substring check lets a short flagged token like `var`
    false-positive inside an unrelated identifier like `variable` (why a
    boundary check is needed at all), but a short token that legitimately
    ends in punctuation - `buffer.putInt(dataLength)`, immediately followed
    by `;` in real code - can never satisfy a trailing `\\b` either: `\\b`
    only matches at a transition between a word and a non-word character,
    and both `)` and `;` are non-word, so no such transition exists there no
    matter how exactly the real match lines up. Anchoring `\\b` only where
    `needle`'s own edge is actually a word character avoids both failure
    modes."""
    pattern = re.escape(needle)
    if needle[:1].isalnum() or needle[:1] == "_":
        pattern = r"\b" + pattern
    if needle[-1:].isalnum() or needle[-1:] == "_":
        pattern = pattern + r"\b"
    return re.search(pattern, haystack) is not None


def find_edits_ignoring_own_diagnosis(
    analysis: Optional[str],
    edits: Optional[List[Dict[str, str]]],
    content: Optional[str],
    orig_text: str,
) -> Optional[str]:
    """Layer 2 pre-flight check, sibling to find_edits_ignoring_reported_line() above -
    that check catches an edit that left the COMPILER's own reported line unchanged,
    but deliberately does NOT flag an edit at a different line, since its own docstring
    correctly treats that as a legitimate alternative fix. This one closes the gap that
    leaves open: found live, 2026-08-13 (spikes/eval_harness/runs/
    attribution-fix-validation-3) - a real compile error (`incompatible types:
    java.lang.Object cannot be converted to com.example.Protocol`, from an untyped
    `var cache = ignite.getOrCreateCache(...)` declaration) recurred VERBATIM across 3
    consecutive targeted retries. Each retry's own FIX ANALYSIS text was textbook-correct
    every time ("...requires explicitly declaring the cache with proper generic type
    parameters `IgniteCache<Integer, Protocol>` instead of using `var`"), confirmed via
    kriya.log - yet the worktree file still showed the identical untyped declaration
    unchanged after all 3 rounds, confirmed directly against the file. No anchor-match
    failure occurred (edits applied cleanly) and no worktree reset happened between
    retries - the edit itself simply never implemented what its own analysis said, at
    ANY line, not just the compiler-reported ones.

    Detection, deliberately avoiding an old-vs-new quote ambiguity: a diagnosis often
    quotes BOTH the broken pattern and the prescribed fix in the same sentence (as
    above - `IgniteCache<Integer, Protocol>` the fix, `var` the problem, both
    backtick-quoted). Naively checking "does any quoted span appear anywhere in the new
    code" is ambiguous - the OLD quoted pattern trivially still appears in old code too.
    Resolved precisely: a quoted span counts as evidence the fix was made if EITHER (a)
    it appears in the NEW content but did NOT already appear in the OLD content (the
    original signal - a quote describing the problem, already present in the old code,
    is naturally excluded without needing to guess which quoted span is "the fix" from
    sentence structure), OR (b) it appears in the new content AND the quote IS the
    entire old (search) text, not merely present somewhere within a larger old text (a
    WRAP/EXTEND edit, e.g. `X` -> `print(X)` - the quoted span legitimately stays
    byte-identical because the fix adds context AROUND the whole thing being replaced,
    so signal (a) alone always misses this shape). Found live, 2026-08-13
    (python_greeter, reproduced across two separate eval-harness runs): this check
    itself was flagging a genuinely correct fix ("wrap the bare `[VERIFICATION] PASS`
    marker in a print() call") as a mismatch on every single retry, for both
    qwen3-coder:30b and glm-4.7-flash, because the quoted marker text is unavoidably
    identical before and after a correct wrap - not a model execution failure at all,
    this check's own false positive was blocking a fix that was likely already correct.

    Signal (b) is deliberately scoped to `q == old_text.strip()`, not the broader "the
    whole old_text survives as a substring of new_text" - that broader version was
    tried first and found to reintroduce a real regression: an edit that appends an
    unrelated trailing comment to an unfixed line (`X;` -> `X; // unchanged`) trivially
    makes the ENTIRE old line a substring of the new one, which would incorrectly clear
    every quote in the analysis, not just one actually being wrapped - confirmed via
    test_find_edits_ignoring_own_diagnosis_regression_validation_3, which still expects
    that shape to be flagged. Requiring the quote to equal the whole search block ties
    the signal specifically to "this edit's target WAS just this quoted text," which
    the append-a-comment case doesn't satisfy (its search block is a full statement, not
    the bare quoted fragment) but the print-wrap case does exactly.

    Returns a descriptive mismatch reason (naming the specific quoted content that never
    appeared) when analysis has at least one backtick-quoted span and NONE of them are
    new (or a wrapped whole-search-block) in the resulting content; None when analysis
    has no quoted spans at all (nothing specific enough to check against - never flag a
    prose-only diagnosis), or when at least one quoted span satisfies either signal.

    SECOND, INDEPENDENT SIGNAL (2026-08-14) - a sibling gap the design above never
    covered: found via spikes/protocol_bug_pocs/05_incremental_composition, reproducing
    a bug skills/binary-wire-protocol/rules.txt already documents from live incidents -
    a generated ProtocolParser.java contained BOTH the wrong `buffer.putInt(dataLength)`
    call (writes 4 bytes for a 3-byte wire field) AND the correct manual byte-shift
    replacement, in the SAME method - a half-finished migration that throws
    BufferOverflowException. If a live retry produced this exact shape - a diagnosis
    correctly saying "instead of `buffer.putInt(dataLength)`, manually byte-shift" and
    an edit that ADDS the byte-shift lines without ever DELETING the old putInt line -
    the check above would pass it: the quoted fix genuinely is new content, satisfying
    signal (a). The bug survives because that check only ever validates the diagnosis's
    POSITIVE claim (what should be added); "instead of X" also makes a NEGATIVE claim
    (X should be gone) that nothing checked.

    Extracts a second, narrower category of quoted span - one immediately preceded by an
    explicit removal-signaling phrase ("instead of `X`", "never `X`", "remove `X`", etc.
    - see _REMOVAL_PHRASE_RE, a small closed set of phrasings already confirmed verbatim
    in real captured fix-analysis text and in skills/binary-wire-protocol's own rules.txt,
    not a general negation detector). If any such span is STILL present in the new
    content - checked via _still_contains() above, not a bare substring - that's direct
    evidence the diagnosis's own removal instruction wasn't followed. Checked independently
    of, and takes priority over, the
    addition signal above: the two are separate claims, and either one failing means the
    edit doesn't actually implement what its own analysis said. Same dual edit-shape
    handling (edits list vs. full content) as the rest of this function.

    FOURTH signal (2026-08-16) - see its own inline comment below (the "cast insertion"
    check) for the full incident: a missing-cast diagnosis quotes the operand and type
    name as separate spans, never the fused `(Type) operand` cast expression, so a
    genuinely correct cast-insertion edit satisfies none of signals (a)/(b)/(c) and was
    confirmed live to be wrongly rejected 3 consecutive retries in a row."""
    if not analysis:
        return None
    quoted = [q for q in _ANALYSIS_QUOTED_SPAN_RE.findall(analysis) if len(q.strip()) >= 2]
    # Found live, 2026-08-17 (ignite_qpid_person, run b-10k): a diagnosis for a
    # malformed/missing file routinely backtick-quotes the FILE'S OWN PATH purely
    # for self-identification ("malformed XML in `src/main/resources/ignite-
    # config.xml`"), not as a claimed code fragment - but this check has no way to
    # tell that apart from a real quoted code snippet, and a self-referential path
    # obviously never appears literally inside that file's own content. Confirmed
    # live via raw-completion DEBUG capture: the model proposed genuinely valid,
    # complete XML content identically on 3 consecutive retries, and every one was
    # rejected, because the ONLY backtick-quoted span in each vague analysis was
    # the file's own path. Excluded via a narrow structural pattern (multiple
    # `/`-separated path segments ending in a file extension) rather than
    # threading the real filepath through this function's signature -
    # deliberately narrow: a genuine code fragment that happens to look like a
    # bare path (e.g. a resource-path string literal being added as new code)
    # could be excluded too, but that's a safe degrade (one fewer piece of
    # evidence, not a wrong rejection), matching every other false-positive/
    # negative tradeoff already accepted in this function.
    quoted = [q for q in quoted if not _BARE_FILE_PATH_RE.match(q.strip())]
    if not quoted:
        return None

    # Signal (a)/(b) are evaluated per-edit-PAIR (each edit's own search vs. its own
    # replace), not against every edit's search/replace flat-joined into one shared
    # old_text/new_text pool. Found live, 2026-08-16 (ignite_qpid_person, runs b-8/b-9):
    # a genuinely correct, single-line cast fix (search "return cache.get(1);", replace
    # "return (Person) cache.get(1);" - textbook signal (a), "Person" is real new
    # content) was flagged as a mismatch anyway, because the SAME response's response
    # also included a second, unrelated edit elsewhere in the file whose OWN search
    # text happened to already mention "Person" (a common identifier in a
    # Person-caching app) - flat-joining pollutes the "was this quote already present"
    # check with content from a completely different edit that has nothing to do with
    # the one actually implementing the diagnosed fix. A multi-edit response touching
    # more than one spot in the same retry is normal, not rare - reproduced directly
    # via find_edits_ignoring_own_diagnosis() itself, no live model call needed.
    if edits:
        pairs = [(e.get("search") or "", e.get("replace") or "") for e in edits]
    else:
        pairs = [(orig_text, content or "")]
    new_text = "\n".join(replace for _, replace in pairs)

    removal_quoted = [q for q in _REMOVAL_PHRASE_RE.findall(analysis) if len(q.strip()) >= 2]
    result_describing = set(_RESULT_DESCRIBING_INSTEAD_OF_RE.findall(analysis))
    removal_quoted = [q for q in removal_quoted if q not in result_describing]
    stale_removals = [q for q in removal_quoted if _still_contains(q, new_text)]
    if stale_removals:
        stale_desc = ", ".join(f"`{q}`" for q in stale_removals)
        return (
            f"your analysis said \"{analysis.strip()}\" - explicitly marking {stale_desc} as "
            f"what to stop using - but {stale_desc} still appears, unchanged, in your proposed "
            f"change. You added the fix without removing what it was supposed to replace."
        )

    for q in quoted:
        # Signal (b) is deliberately scoped to "the quote IS the entire old
        # text OF THIS SAME PAIR" (not just present somewhere within a larger
        # old_text, and not borrowed from a DIFFERENT pair) - a broader
        # "old_text survives anywhere inside new_text" version was tried and
        # found to also let a genuine mismatch through: appending an
        # unrelated trailing comment to an unfixed line trivially makes the
        # whole old line a substring of the new one too, for every quote in
        # the analysis, not just the one actually being wrapped. Requiring
        # q == search.strip() ties the signal to the specific quote whose
        # surrounding statement was JUST that quote, which is exactly the
        # real wrap shape (search: "X", replace: "print(X)") and excludes a
        # multi-token old_text merely containing q somewhere.
        #
        # THIRD signal (2026-08-16) - a "disappearance" check, sibling to the
        # ADDITIVE signal above but for the opposite shape: a diagnosis that
        # names the WRONG value being corrected to a right one (a deletion/
        # substitution, not an addition) never satisfies signal (a)/(b) at
        # all, because every quoted fragment of a dotted/qualified identifier
        # being shortened is trivially a substring of BOTH the old and new
        # text - there's no genuinely "new" token to find. Found live,
        # 2026-08-16 (ignite_qpid_person, run b-10a): "the code imports
        # `org.apache.ignite.cache.IgniteCache`... the correct import
        # location... is the top-level `org.apache.ignite` package" -
        # correcting `import org.apache.ignite.cache.IgniteCache;` to
        # `import org.apache.ignite.IgniteCache;` is a completely correct,
        # real fix, but flagged as a mismatch because "IgniteCache",
        # "org.apache.ignite.cache", and "org.apache.ignite" are all
        # substrings of the ORIGINAL (wrong) import too. The one quote that
        # DOES prove something happened is the full wrong path itself,
        # `org.apache.ignite.cache.IgniteCache` - present in this pair's
        # search, gone from this pair's replace. Scoped to the SAME pair
        # (not the flat-joined pools, same reasoning as the two signals
        # above) so an unrelated edit elsewhere can't manufacture a false
        # "disappearance" either.
        # FOURTH signal (2026-08-16, ignite_qpid_person run b-10h) - a "cast
        # insertion" check, sibling to the other three but for a shape none of
        # them cover: a diagnosis correcting a missing-cast compile error
        # ("Object cannot be converted to Person") naturally quotes the
        # OPERAND (`cache.get(1)`) and the TYPE (`Person`) as separate spans,
        # not the fused `(Person) cache.get(1)` expression as one span - a
        # model doesn't write prose that way. Both separate quotes are
        # trivially present in BOTH old and new text (the operand and the
        # type name are unchanged; only a parenthesized cast prefix was
        # inserted immediately before the operand), so signals (a)/(b)/(c)
        # all miss it - none of them see anything "new". Confirmed live via
        # the new raw-completion DEBUG logging (kriya/agents/agent.py): a
        # textbook-correct `Person cachedPerson = cache.get(1);` ->
        # `Person cachedPerson = (Person) cache.get(1);` edit was rejected as
        # a mismatch 3 consecutive retries in a row, real compile fix,
        # wrongly blocked every time. Likely the same root cause behind the
        # still-unresolved historical incident in the backlog (`Protocol
        # cachedProtocol = cache.get(1)` -> `(Protocol) cache.get(1)`,
        # "root cause NOT confirmed" - this predates DEBUG-level raw-
        # completion capture, so it was never traced to this function
        # before). Scoped narrowly to a literal `(q)` parenthesization of the
        # quoted span appearing new in this pair's replace - deliberately
        # not a general "any change counts" relaxation, which would reopen
        # the no-op-edit gap this whole check exists to close.
        if any(
            (q in replace and (q not in search or q == search.strip()))
            or (q in search and q not in replace)
            or (f"({q})" in replace and f"({q})" not in search)
            for search, replace in pairs
        ):
            return None

    quoted_desc = ", ".join(f"`{q}`" for q in quoted)
    return (
        f"your analysis said \"{analysis.strip()}\" - specifically naming {quoted_desc} - "
        f"but none of that appears anywhere new in your proposed change"
    )


# --------------------------------------------------------------------------
# E. Missing-file detection: is a required build manifest missing entirely
# (never requested by the Architect, not lost after being requested)? Moved
# here from kriya/workflow/toolchain.py on 2026-08-14.
# --------------------------------------------------------------------------

_UNRESOLVED_PACKAGE_PATTERN = re.compile(r"package [\w.]+ does not exist")


def _detect_missing_build_manifest(worktree_path: str, raw_error_text: str) -> Optional[str]:
    """Deterministically detects a Java compile failure caused by a build
    manifest the Architect never explicitly asked the Developer to create -
    not one the Developer merely dropped after being asked (that's
    IncompleteGenerationError's job, and it already works).

    Confirmed live, 2026-08-07 (kriya-protocol-parser-app): pom.xml was
    never written across two separate full runs, three days apart. Every
    retry's own fix-analysis correctly diagnosed "the dependencies aren't
    declared in pom.xml" and explicitly declined to fix it, since a
    per-file targeted retry is told (correctly, in every other case) to
    stay in scope. extract_implicated_files()'s basename-in-text matching
    can never implicate pom.xml either, since a "package X does not exist"
    error never names the missing manifest file that's the real cause -
    so nothing in the retry loop could ever recover it, no matter how many
    attempts ran, because it was never requested in the first place, not
    because it was requested and lost.

    Fires purely from the error shape and the worktree's own current state,
    independent of whether the Architect's design ever listed the file at
    all - closes that structural blind spot as its own detection path,
    parallel to (not replacing) IncompleteGenerationError. A "package X does
    not exist" error can only happen for a genuinely external dependency (a
    JDK-standard java.*/javax.* package always resolves regardless of any
    build manifest), so requiring BOTH "no pom.xml/build.gradle exists" AND
    this specific error shape is a low-false-positive combination - a
    stdlib-only Java goal that never needed a manifest at all will simply
    never produce this error shape to begin with.

    Deliberately Maven-specific (returns "pom.xml", never "build.gradle") -
    only one real instance of this problem class has been found, and it was
    a Maven goal; a Gradle instance would need its own detection (different
    error shape) rather than being guessed at here. Mirrors
    _JDK_INCOMPATIBLE_JVM_FLAGS' (kriya/workflow/toolchain.py) philosophy:
    fix the confirmed instance precisely, don't build generality for one
    that hasn't happened yet."""
    if os.path.exists(os.path.join(worktree_path, "pom.xml")):
        return None
    if os.path.exists(os.path.join(worktree_path, "build.gradle")):
        return None
    if _UNRESOLVED_PACKAGE_PATTERN.search(raw_error_text):
        return "pom.xml"
    return None
