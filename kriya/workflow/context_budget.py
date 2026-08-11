"""Context budget allocation and skeletonization tiers for the Graph RAG code context assembled into each generation prompt. Extracted from kriya/workflow/workflow.py (2026-08-11 modularization)."""

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


def skeletonize_code(content: str, filepath: str, tier: str) -> str:
    if tier == "full" or not tier:
        return content
        
    _, ext = os.path.splitext(filepath)
    ext = ext.lower()
    
    if ext == ".py":
        return skeletonize_python(content, tier)
    elif ext in {".java", ".cpp", ".c", ".h", ".cs"}:
        return skeletonize_braced_code(content, tier)
    else:
        if tier == "signatures":
            return "\n".join(content.splitlines()[:15]) + "\n... [Remaining content elided]"
        return content


def skeletonize_python(content: str, tier: str) -> str:
    lines = content.splitlines()
    output = []
    
    in_class = False
    class_indent = 0
    
    for line in lines:
        line_strip = line.strip()
        if not line_strip:
            output.append(line)
            continue
            
        if line_strip.startswith("import ") or line_strip.startswith("from "):
            output.append(line)
            continue
            
        if line_strip.startswith("class "):
            output.append(line)
            in_class = True
            class_indent = len(line) - len(line.lstrip())
            continue
            
        if tier == "signatures":
            if line_strip.startswith("def "):
                continue
            indent = len(line) - len(line.lstrip())
            if not line_strip.startswith("def ") and (not in_class or indent <= class_indent + 4):
                output.append(line)
            continue
            
        if line_strip.startswith("def "):
            output.append(line)
            indent = len(line) - len(line.lstrip())
            output.append(" " * (indent + 4) + "...")
            continue
            
        indent = len(line) - len(line.lstrip())
        if not in_class and indent == 0:
            output.append(line)
        elif in_class and indent <= class_indent + 4:
            output.append(line)
            
    return "\n".join(output)


def skeletonize_braced_code(content: str, tier: str) -> str:
    if tier == "signatures":
        lines = content.splitlines()
        output = []
        for line in lines:
            line_strip = line.strip()
            if line_strip.startswith("import ") or line_strip.startswith("package "):
                output.append(line)
            elif "class " in line or "interface " in line or "enum " in line:
                output.append(line)
        return "\n".join(output)
        
    result = []
    i = 0
    length = len(content)
    method_sig_pattern = re.compile(r'(?:public|protected|private|static|\s)+[\w<>]+\s+\w+\s*\([^\)]*\)\s*$')
    
    buffer = ""
    while i < length:
        char = content[i]
        if char == '{':
            if method_sig_pattern.search(buffer.strip()):
                result.append(buffer)
                result.append(" { ... }")
                buffer = ""
                brace_count = 1
                i += 1
                while i < length and brace_count > 0:
                    c = content[i]
                    if c == '{':
                        brace_count += 1
                    elif c == '}':
                        brace_count -= 1
                    i += 1
                continue
            else:
                result.append(buffer)
                result.append("{")
                buffer = ""
                i += 1
        elif char == '}':
            result.append(buffer)
            result.append("}")
            buffer = ""
            i += 1
        else:
            buffer += char
            i += 1
            
    if buffer:
        result.append(buffer)
        
    return "".join(result)


def estimate_tokens(text: str) -> int:
    """Estimates the number of tokens in a string using word heuristics (~1.3 tokens per word)."""
    return int(len(text.split()) * 1.3)


# Floor for build_code_context()'s own budget after skills_prompt/learned_rag_context
# are subtracted below - keeps the allocator functional (still returns SOME matched-file
# context, just skeletonized more aggressively) rather than collapsing to 0 and silently
# dropping all graph-RAG context for a retry that's already fighting an undersized window.
_MIN_GRAPH_CONTEXT_BUDGET = 1000


def _reserve_graph_context_budget(model_context_window: int, *unbounded_texts: str) -> int:
    """Every retry path computes build_code_context()'s token budget as a flat fraction of
    the ACTIVE model's context_window (0.75), then separately prepends skills_prompt and
    learned_rag_context to the result - both unbounded, un-budgeted strings that are
    IDENTICAL in size regardless of which model is currently active. Confirmed live,
    2026-08-07 (ignite_qpid_person): 5 active skills' rules+instructions alone measured
    ~6800 tokens - comfortably absorbed by a large primary model's window, but over half
    of a 16K-context fallback model's ENTIRE 0.75 budget (12288 tokens) before a single
    byte of graph-RAG code context was even added. The fallback's own subsequent completion
    calls then hit real 400 'prompt is longer than context length' errors, and a targeted
    retry immediately after (same fallback, same unaccounted overhead) produced a truncated,
    malformed edit - both consistent with the SAME root cause: the model was operating with
    far less actual headroom than the allocator believed it had.

    Subtracts the estimated size of every unbounded text about to be concatenated onto the
    SAME prompt (skills_prompt, learned_rag_context) from the flat 0.75 budget BEFORE it's
    handed to build_code_context() as the graph-RAG budget - so the total prompt this
    attempt actually sends stays proportioned to what the ACTIVE model can really hold,
    instead of assuming graph-RAG context is the only occupant. Floored at
    _MIN_GRAPH_CONTEXT_BUDGET so a very large skills_prompt still leaves build_code_context()
    something to work with (already-aggressive skeletonization, not a hard zero) rather than
    silently dropping all matched/related file context for the rest of this attempt."""
    base_budget = int(model_context_window * 0.75)
    reserved = sum(estimate_tokens(t) for t in unbounded_texts if t)
    return max(_MIN_GRAPH_CONTEXT_BUDGET, base_budget - reserved)


_TIER_STEPS = ("full", "skeleton", "signatures")


def build_code_context(matched_files: List[str], related_files: List[str], workspace_path: str, budget_limit: int, file_scores: Optional[Dict[str, float]] = None) -> str:
    matched_contents = {}
    for f in matched_files:
        full_p = os.path.join(workspace_path, f)
        if os.path.exists(full_p):
            try:
                with open(full_p, "r", encoding="utf-8", errors="replace") as fh:
                    matched_contents[f] = fh.read()
            except Exception as e:
                logger.debug(f"Failed to read matched file '{full_p}' for RAG context: {e}")

    related_contents = {}
    for f in related_files:
        full_p = os.path.join(workspace_path, f)
        if os.path.exists(full_p):
            try:
                with open(full_p, "r", encoding="utf-8", errors="replace") as fh:
                    related_contents[f] = fh.read()
            except Exception as e:
                logger.debug(f"Failed to read related file '{full_p}' for RAG context: {e}")

    # Introduce cache for skeletonized content to optimize performance
    skel_cache = {}

    def get_skeletonized(content: str, filepath: str, tier: str) -> str:
        key = (filepath, tier)
        if key not in skel_cache:
            skel_cache[key] = skeletonize_code(content, filepath, tier)
        return skel_cache[key]

    if file_scores is None:
        # Original categorical degradation: every related file degrades one
        # tier before any matched file loses its own next tier - no signal
        # to prefer one specific file over another within a category.
        matched_tier = "full"
        related_tier = "full"

        def total_len():
            total = 0
            for filepath, content in matched_contents.items():
                total += estimate_tokens(get_skeletonized(content, filepath, matched_tier))
            for filepath, content in related_contents.items():
                total += estimate_tokens(get_skeletonized(content, filepath, related_tier))
            return total

        while total_len() > budget_limit:
            if related_tier == "full":
                related_tier = "skeleton"
            elif related_tier == "skeleton":
                related_tier = "signatures"
            elif matched_tier == "full":
                matched_tier = "skeleton"
            elif matched_tier == "skeleton":
                matched_tier = "signatures"
            else:
                break

        graph_rag_context = "\n\n=== Codebase Semantic Reference Context ===\n"
        for filepath, content in matched_contents.items():
            skel = get_skeletonized(content, filepath, matched_tier)
            graph_rag_context += f"\nFile: {filepath} (Tier: {matched_tier})\n{skel}\n"

        if related_contents:
            graph_rag_context += "\n\n=== Bounded Neighborhood Dependency Context ===\n"
            for filepath, content in related_contents.items():
                skel = get_skeletonized(content, filepath, related_tier)
                graph_rag_context += f"\nFile: {filepath} (Tier: {related_tier})\n{skel}\n"

        return graph_rag_context

    # Score-aware degradation (2026-08-12 SME review, re-ranking retrieval):
    # each file (matched or related alike) has its own tier, degraded one
    # step at a time starting from whichever remaining file scores lowest -
    # a low-relevance matched file can lose detail before a high-relevance
    # related file does, which the purely categorical path above can never
    # express. A file missing from file_scores is treated as the lowest
    # possible priority (degrades first) rather than assumed relevant.
    file_tiers = {f: "full" for f in list(matched_contents) + list(related_contents)}

    def total_len():
        total = 0
        for filepath, content in matched_contents.items():
            total += estimate_tokens(get_skeletonized(content, filepath, file_tiers[filepath]))
        for filepath, content in related_contents.items():
            total += estimate_tokens(get_skeletonized(content, filepath, file_tiers[filepath]))
        return total

    while total_len() > budget_limit:
        degradable = [f for f in file_tiers if file_tiers[f] != "signatures"]
        if not degradable:
            break
        degradable.sort(key=lambda f: file_scores.get(f, 0.0))
        lowest = degradable[0]
        next_tier = _TIER_STEPS[_TIER_STEPS.index(file_tiers[lowest]) + 1]
        file_tiers[lowest] = next_tier

    graph_rag_context = "\n\n=== Codebase Semantic Reference Context ===\n"
    for filepath, content in matched_contents.items():
        tier = file_tiers[filepath]
        skel = get_skeletonized(content, filepath, tier)
        graph_rag_context += f"\nFile: {filepath} (Tier: {tier})\n{skel}\n"

    if related_contents:
        graph_rag_context += "\n\n=== Bounded Neighborhood Dependency Context ===\n"
        for filepath, content in related_contents.items():
            tier = file_tiers[filepath]
            skel = get_skeletonized(content, filepath, tier)
            graph_rag_context += f"\nFile: {filepath} (Tier: {tier})\n{skel}\n"

    return graph_rag_context
