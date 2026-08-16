"""Untrusted, error-triggered live web lookup for skill-gap resolution (Stage 3 escalation) - a separate trust tier from the RAG index, see CLAUDE.md. Extracted from kriya/workflow/workflow.py (2026-08-11 modularization)."""

import asyncio
import difflib
import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


async def _resolve_via_web_lookup(terms: List[str], search_base_url: str, top_k: int) -> List[Dict[str, Any]]:
    """Auto-resolves a list of already-extracted, bare technology-name terms via a
    configured search backend, fetching up to `top_k` candidate pages per term
    (best-first, ranked by the search backend). `terms` MUST already be the product of
    a bounded, code-level extraction (e.g. extract_library_versions matched against
    goal/design text) - this function only ever issues the term string itself as the
    query, never any surrounding goal/design/code text, so a project's proprietary
    content can never end up in an outbound search request. Best-effort: a term that
    fails to search entirely is silently skipped, not an error.

    Returns one entry per term with a `candidates` list (each already-fetched page's
    url/snippet/text) so callers can try each in turn until one actually yields
    something extractable - a single unhelpful top result (a marketing/landing page,
    confirmed to happen in real testing) shouldn't sink the whole lookup. `url`/
    `snippet` at the top level mirror the best candidate, for a simple human-facing
    confirmation summary that doesn't need to enumerate every candidate.

    Query suffix is "example", not "documentation" - confirmed live against a real
    search backend (both a self-hosted SearXNG instance and, separately, Claude's
    own web search, using the exact Maven-coordinate term shape this function
    actually sends): "{term} documentation" consistently surfaces a project's
    landing/index page (qpid.apache.org/documentation.html, qpid.apache.org/) with
    nothing concrete to extract - "the landing-page problem" already documented
    below. "{term} example" instead surfaced genuinely extractable content for
    both Ignite and Qpid in the same live test - a GitHub example config file, an
    official quick-start code sample with the correct top-level IgniteCache import
    (the exact fact a real skill rule this session had to be hand-written for,
    after a JAR was manually unzipped to find it), and a wiki how-to page with a
    real Maven dependency block, confirmed independently to extract cleanly via
    this project's own fetch_url_text(). Deliberately kept as a single query, not
    a multi-variant fallback chain - a real self-hosted SearXNG instance got
    rate-limited/CAPTCHA'd by its own upstream engines during this same live
    testing after a modest handful of requests, so multiplying query volume
    per term is a real reliability risk, not just a latency cost."""
    from kriya.tools.search import search_web
    from kriya.tools.web import fetch_url_text

    resolved = []
    for term in terms:
        try:
            results = await search_web(f"{term} example", search_base_url, top_k=top_k)
        except Exception as ex:
            logger.debug(f"Live lookup search failed for '{term}': {ex}")
            continue
        if not results:
            continue

        candidates = []
        for r in results:
            try:
                text = await fetch_url_text(r["url"])
            except Exception as ex:
                logger.debug(f"Live lookup fetch failed for '{term}' ({r['url']}): {ex}")
                continue
            candidates.append({"url": r["url"], "snippet": r.get("snippet", ""), "text": text})

        if candidates:
            resolved.append({
                "term": term,
                "url": candidates[0]["url"],
                "snippet": candidates[0]["snippet"],
                "candidates": candidates,
            })
    return resolved


# Cheap proxy for "this candidate has real content, not just a thin
# landing/index page" - see _augment_error_with_live_lookup's own docstring
# for why this can't just call the LLM-based extraction judgment
# (_extract_first_usable) uses elsewhere.
_MIN_USABLE_LOOKUP_TEXT_CHARS = 200


def _first_usable_lookup_candidate(candidates: List[Dict[str, str]]) -> Dict[str, str]:
    """Picks the first candidate whose fetched text looks like real content
    rather than blindly using candidates[0] - the exact "landing-page
    problem" _resolve_via_web_lookup's own docstring documents finding live
    (a marketing/index page with nothing concrete to extract) is precisely
    why multiple candidates get fetched per term in the first place, but the
    caller here previously never looked past the first one regardless of
    whether it was actually useful. Falls back to candidates[0] if none
    clear the bar, so a term with only thin candidates still gets SOMETHING
    rather than nothing - this is a cheap length heuristic, not an LLM
    judgment call, matching this function's own deliberate avoidance of a
    slow extraction round-trip."""
    for candidate in candidates:
        if len(candidate.get("text", "").strip()) >= _MIN_USABLE_LOOKUP_TEXT_CHARS:
            return candidate
    return candidates[0]


async def _augment_error_with_live_lookup(
    error_text: str, terms: List[str], search_base_url: str, top_k: int
) -> str:
    """When the SAME Quality Gate failure repeats across consecutive Developer
    retry attempts - a sign the model isn't self-correcting on its own - tries
    live lookup for the extracted tool/library terms and appends anything found
    directly to the error text for the next retry's prompt. Deliberately skips
    the SkillGapAgent extraction call used elsewhere (Stage 1.2/2B) - that's
    another slow LLM round-trip, which would work against the whole point of
    this feature (fewer/faster retries) - and doesn't persist anything to a
    skill's rules.txt; this is ephemeral, scoped to the current retry only, not
    durable knowledge. Best-effort: returns error_text unchanged if lookup finds
    nothing usable."""
    found = await _resolve_via_web_lookup(terms, search_base_url, top_k)
    if not found:
        return error_text

    augmented = error_text
    for item in found:
        candidate = _first_usable_lookup_candidate(item["candidates"])
        snippet = candidate["text"][:2000]
        augmented += (
            f"\n\n=== Reference material found for '{item['term']}' (from {candidate['url']}) - "
            "this repeated failure may be resolved by it, but verify before relying on it ===\n"
            f"{snippet}"
        )
        logger.info(f"Live lookup found reference material for repeated failure term '{item['term']}' ({candidate['url']}).")
    return augmented


async def _extract_first_usable(
    skill_gap_agent: Any, target: Any, gap_description: str, candidates: List[Dict[str, str]]
) -> Dict[str, Any]:
    """Tries extraction against each candidate's fetched text in order (best search
    result first) and returns the first one that actually yields something (rules,
    examples, or even a flagged conflict - any of those is real signal). A URL being
    reachable is not the same as it containing anything usable - if none of the
    candidates for this term have anything extractable, returns the last (empty)
    result so downstream logging still fires, but nothing gets written to the skill."""
    result: Dict[str, Any] = {"rules": [], "examples": {}, "conflicts": []}
    for candidate in candidates:
        result = await skill_gap_agent.extract_skill_update(
            reference_text=candidate["text"],
            gap_description=gap_description,
            existing_rules=target.rules,
        )
        if result["rules"] or result["examples"] or result["conflicts"]:
            return result
    return result
