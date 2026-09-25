"""Context budget allocation and skeletonization tiers for the Graph RAG code context assembled into each generation prompt. Extracted from kriya/workflow/workflow.py (2026-08-11 modularization)."""

import asyncio
import difflib
import hashlib
import io
import logging
import os
import re
import shutil
import subprocess
import sys
import tokenize
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from kriya.analyzer.analyzer import JAVA_METHOD_SIGNATURE_CORE
from kriya.workflow.edit_safety import _strip_java_comments_and_strings, content_revision
from kriya.workflow.process_profile import ContextDepth

# Annotation-only names, imported at runtime so typing.get_type_hints()
# resolves (PRD-001); no cycle.
from kriya.workflow.context_source import SourceDerivationCache

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievalLimits:
    """MA2.6 (control-plane implementation plan) - how far Graph RAG
    retrieval reaches (kriya/workflow/workflow.py's "1.5. Graph RAG Context
    Retrieval" stage: vector_store.query_hybrid's top_k, graph.get_
    neighborhood's max_hops/max_results), NOT how much of what it finds
    survives into the prompt - that stays entirely governed by
    _reserve_graph_context_budget/build_code_context's own token budget
    below, unchanged by this. Widening these limits only means MORE
    CANDIDATES get scored and considered; the token budget still trims to
    fit exactly as it does today."""

    top_k: int
    max_hops: int
    max_neighborhood_results: int


# NARROW matches today's hardcoded values EXACTLY (query_hybrid's top_k=5,
# get_neighborhood's default max_hops=2/max_results=30) - a LIGHT-profile
# request gets IDENTICAL retrieval behavior to what every request gets
# today, never less. DEPENDENCY_AWARE/IMPACT_WIDE only ever ADD reach on
# top of that baseline, matching the same purely-additive posture MA2.5
# already established for approval - nothing here can ever narrow
# retrieval below what Kriya already does unconditionally today.
_RETRIEVAL_LIMITS_BY_DEPTH: Dict[ContextDepth, RetrievalLimits] = {
    ContextDepth.NARROW: RetrievalLimits(top_k=5, max_hops=2, max_neighborhood_results=30),
    ContextDepth.DEPENDENCY_AWARE: RetrievalLimits(top_k=8, max_hops=2, max_neighborhood_results=40),
    ContextDepth.IMPACT_WIDE: RetrievalLimits(top_k=10, max_hops=3, max_neighborhood_results=50),
}


def retrieval_limits_for(depth: ContextDepth) -> RetrievalLimits:
    """Pure lookup, same contract as process_profile_for/determine_risk_class
    - no config, no LLM, no filesystem."""
    return _RETRIEVAL_LIMITS_BY_DEPTH[depth]


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


def _python_decorator_ranges(
    tokens: List[tokenize.TokenInfo],
    lines: List[str],
) -> List[Tuple[int, int, int]]:
    """Return ``(start_line, end_line, indent)`` for decorator statements."""
    ranges = []
    bracket_depth = 0
    for index, token in enumerate(tokens):
        if token.type == tokenize.OP and token.string in "([{":
            bracket_depth += 1
            continue
        if token.type == tokenize.OP and token.string in ")]}":
            bracket_depth = max(0, bracket_depth - 1)
            continue
        if token.type != tokenize.OP or token.string != "@":
            continue
        if bracket_depth != 0:
            # A bare '@' inside an open (), [] or {} is the matrix-
            # multiplication operator on a continuation line (common
            # Black/PEP8 style for numpy/torch, e.g. `x = (\n    a\n@ b\n)`),
            # never a decorator - a decorator statement can only start at
            # zero bracket depth (2026-08-18 review finding: this was
            # previously misattached to the next declaration).
            continue
        line_index = token.start[0] - 1
        if lines[line_index][:token.start[1]].strip():
            continue
        for following in tokens[index + 1:]:
            if following.type == tokenize.NEWLINE:
                ranges.append((line_index, following.end[0] - 1, token.start[1]))
                break
    return ranges


def _python_decorator_start(
    declaration_line: int,
    indent: int,
    decorator_ranges: List[Tuple[int, int, int]],
    lines: List[str],
) -> int:
    """Find the first decorator directly attached to a declaration."""
    output_start = declaration_line
    cursor = declaration_line
    for start_line, end_line, decorator_indent in reversed(decorator_ranges):
        if end_line >= cursor or decorator_indent != indent:
            continue
        intervening = lines[end_line + 1:cursor]
        if all(not line.strip() or line.lstrip().startswith("#") for line in intervening):
            output_start = start_line
            cursor = start_line
    return output_start


def _python_declaration_ranges(content: str) -> Dict[int, Tuple[int, int, str, int]]:
    """Return source ranges for every Python function and class declaration.

    Tokenizing rather than counting lines is important here: annotations and
    base lists can make a declaration span several lines and contain their own
    colons.  The first colon outside (), [] and {} after ``def``/``class`` is
    the suite delimiter.  The result is keyed by the first decorator line (or
    the declaration line) and stores ``end_line, colon_column, kind, indent``.
    Ranges discovered before a tokenization error remain usable for incomplete
    source files, including any decorators tokenized before that error.
    """
    tokens = []
    token_stream = tokenize.generate_tokens(io.StringIO(content).readline)
    try:
        while True:
            tokens.append(next(token_stream))
    except StopIteration:
        pass
    except (IndentationError, tokenize.TokenError):
        pass

    lines = content.splitlines()
    decorator_ranges = _python_decorator_ranges(tokens, lines)
    ranges: Dict[int, Tuple[int, int, str, int]] = {}
    for index, token in enumerate(tokens):
        if token.type != tokenize.NAME or token.string not in {"def", "class"}:
            continue

        bracket_depth = 0
        for following in tokens[index + 1:]:
            if following.type == tokenize.NEWLINE and bracket_depth == 0:
                break
            if following.type != tokenize.OP:
                continue
            if following.string in "([{":
                bracket_depth += 1
            elif following.string in ")]}" and bracket_depth:
                bracket_depth -= 1
            elif following.string == ":" and bracket_depth == 0:
                declaration_line = token.start[0] - 1
                indent = len(lines[declaration_line]) - len(lines[declaration_line].lstrip())
                output_start = _python_decorator_start(
                    declaration_line,
                    indent,
                    decorator_ranges,
                    lines,
                )
                ranges[output_start] = (
                    following.end[0] - 1,
                    following.end[1],
                    token.string,
                    indent,
                )
                break

    return ranges


def skeletonize_python(content: str, tier: str) -> str:
    lines = content.splitlines()
    output = []

    if tier == "signatures":
        declaration_ranges = _python_declaration_ranges(content)
        line_index = 0
        while line_index < len(lines):
            line = lines[line_index]
            line_strip = line.strip()

            declaration_range = declaration_ranges.get(line_index)
            if declaration_range is not None:
                end_line, colon_column, kind, indent = declaration_range
                declaration_lines = lines[line_index:end_line + 1]
                # A one-line declaration can have its suite after the colon.
                # Retain only the declaration, never that inline body.
                declaration_lines[-1] = declaration_lines[-1][:colon_column]
                output.extend(declaration_lines)
                if kind == "def":
                    output.append(" " * (indent + 4) + "...")
                line_index = end_line + 1
                continue

            if not line_strip:
                output.append(line)
            elif line_strip.startswith("import ") or line_strip.startswith("from "):
                output.append(line)

            line_index += 1

        while output and not output[0].strip():
            output.pop(0)
        while output and not output[-1].strip():
            output.pop()
        return "\n".join(output)
    
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


_BRACED_INLINE_ANNOTATIONS = (
    r"(?:@[A-Za-z_$][\w.$]*(?:[ \t]*\([^\r\n)]*\))?[ \t]+)*"
)
_BRACED_MEMBER_MODIFIERS = (
    r"(?:(?:public|protected|private|static|abstract|final|synchronized|native|strictfp|default)\s+)*"
)
_BRACED_EXTENDED_METHOD_SIGNATURE = (
    _BRACED_MEMBER_MODIFIERS
    + r"(?:<[^;{}()]+>\s+)?"
    + r"[A-Za-z_$][\w.$:]*"
    + r"(?:\s*<[^;{}()]+>)?"
    + r"(?:\s*\[\s*\])*"
    + r"(?:\s*[*&]+)?\s+"
    + r"[A-Za-z_$][\w$]*\s*\([^)]*\)"
)
_BRACED_THROWS_CLAUSE = r"(?:\s+throws\s+[\w.$<>, ?&\[\]\r\n\t]+)?"
_BRACED_DECLARATION_END = r"[ \t\r\n]*$"

_BRACED_TYPE_DECLARATION_PATTERN = re.compile(
    r"(?m)^(?P<declaration>[ \t]*"
    + _BRACED_INLINE_ANNOTATIONS
    + r"(?:(?:public|protected|private|abstract|static|final|strictfp|sealed|non-sealed)\s+)*"
    + r"(?:class|interface|enum|@interface)\s+(?P<name>[A-Za-z_$][\w$]*)\b[^;{}]*)"
    + _BRACED_DECLARATION_END
)

_BRACED_METHOD_DECLARATION_PATTERN = re.compile(
    r"(?m)^(?P<signature>[ \t]*"
    + _BRACED_INLINE_ANNOTATIONS
    + r"(?:"
    + JAVA_METHOD_SIGNATURE_CORE
    + r"|"
    + _BRACED_EXTENDED_METHOD_SIGNATURE
    + r")"
    + _BRACED_THROWS_CLAUSE
    + r")"
    + _BRACED_DECLARATION_END
)


@lru_cache(maxsize=256)
def _braced_constructor_declaration_pattern(type_name: str) -> re.Pattern:
    """Build an exact-name constructor matcher for the enclosing type.

    Requiring the tracked type name is what distinguishes constructors from
    one-token control-flow constructs such as ``if (...)`` and ``for (...)``.
    Bounded (not maxsize=None) so a long-running process (kriya repl, or many
    generate/fix/analyze runs across large/multiple repos) doesn't accumulate
    one permanently cached compiled regex per distinct type name for its
    entire lifetime (2026-08-18 review finding) - LRU eviction keeps the
    common case (a bounded working set of types actively being skeletonized)
    fully cached while letting old entries go.
    """
    return re.compile(
        r"(?m)^(?P<signature>[ \t]*"
        + _BRACED_INLINE_ANNOTATIONS
        + r"(?:(?:public|protected|private)\s+)?"
        + re.escape(type_name)
        + r"\s*\([^)]*\)"
        + _BRACED_THROWS_CLAUSE
        + r")"
        + _BRACED_DECLARATION_END
    )


def _braced_member_declaration_match(
    structural_buffer: str,
    enclosing_type: Optional[str],
    allow_regular_method: bool,
) -> Optional[re.Match]:
    """Match a constructor first, then a regular method when scope allows."""
    constructor_pattern = (
        _braced_constructor_declaration_pattern(enclosing_type)
        if enclosing_type is not None
        else None
    )
    candidates = (
        (enclosing_type is not None, constructor_pattern),
        (allow_regular_method, _BRACED_METHOD_DECLARATION_PATTERN),
    )
    for enabled, pattern in candidates:
        if enabled and pattern is not None:
            match = pattern.search(structural_buffer)
            if match is not None:
                return match
    return None


def _braced_declaration_source(
    buffer: str,
    match: re.Match,
    group_name: str,
) -> str:
    """Extract a declaration and any directly-attached annotation lines."""
    start, end = match.span(group_name)
    start = buffer.rfind("\n", 0, start) + 1

    # Signature-mode buffers are reset at the preceding member delimiter. If
    # their first nonblank line begins an annotation, retaining from there
    # also covers a multi-line annotation whose continuation lines do not
    # themselves begin with '@'.
    first_nonblank = 0
    while first_nonblank < start and buffer[first_nonblank].isspace():
        first_nonblank += 1
    if first_nonblank < start and buffer[first_nonblank] == "@":
        start = buffer.rfind("\n", 0, first_nonblank) + 1

    while start > 0:
        previous_end = start - 1
        previous_start = buffer.rfind("\n", 0, previous_end) + 1
        previous_line = buffer[previous_start:previous_end]
        if not previous_line.strip().startswith("@"):
            break
        start = previous_start

    declaration_lines = buffer[start:end].splitlines()
    while declaration_lines and not declaration_lines[0].strip():
        declaration_lines.pop(0)
    return "\n".join(declaration_lines).rstrip()


def skeletonize_braced_code(content: str, tier: str) -> str:
    result = []
    signatures_only = tier == "signatures"

    i = 0
    length = len(content)
    # Comment/string-stripped mirror (same length - comment/string spans
    # blanked to whitespace, everything else untouched) used only to detect
    # REAL structural braces; `content` itself (unchanged) is what actually
    # gets buffered/emitted, so a '{'/'}' inside a Java string literal or
    # comment no longer miscounts and truncates or merges skeleton
    # boundaries - the exact bug class edit_safety.py's own
    # _strip_java_comments_and_strings() was built to avoid, not previously
    # extended to this call site (2026-08-12 SME review).
    structural = _strip_java_comments_and_strings(content)

    if signatures_only:
        # Checked against the STRUCTURAL (comment/string-blanked) line, not
        # the raw content line: an example `import`/`package` statement
        # written inside a Javadoc/block comment would otherwise be emitted
        # as if it were real source (2026-08-18 review finding).
        for line, structural_line in zip(content.splitlines(), structural.splitlines()):
            structural_strip = structural_line.strip()
            if structural_strip.startswith("import ") or structural_strip.startswith("package "):
                result.append(line)

    buffer = ""
    structural_buffer = ""
    brace_depth = 0
    # (exact type name, depth inside its body, declaration indentation)
    type_stack: List[Tuple[str, int, str]] = []
    while i < length:
        char = content[i]
        if structural[i] == '{':
            type_match = _BRACED_TYPE_DECLARATION_PATTERN.search(structural_buffer)
            directly_inside_type = bool(type_stack and brace_depth == type_stack[-1][1])
            member_match = None
            if type_match is None:
                enclosing_type = type_stack[-1][0] if directly_inside_type else None
                # In signatures mode a member is emitted only when it belongs
                # directly to a tracked type (or there is no tracked type, as
                # for a C/C++ free function).  This prevents methods inside an
                # anonymous class/static initializer from being attributed to
                # their enclosing named class.  Skeleton mode keeps the old
                # all-scope method collapsing behavior.
                allow_regular_method = (
                    not signatures_only
                    or directly_inside_type
                    or (not type_stack and brace_depth == 0)
                )
                member_match = _braced_member_declaration_match(
                    structural_buffer,
                    enclosing_type,
                    allow_regular_method,
                )

            if member_match is not None:
                if signatures_only:
                    signature = _braced_declaration_source(
                        buffer,
                        member_match,
                        "signature",
                    )
                    result.append(signature + " { ... }")
                else:
                    result.append(buffer)
                    result.append(" { ... }")
                buffer = ""
                structural_buffer = ""
                brace_count = 1
                i += 1
                while i < length and brace_count > 0:
                    c = structural[i]
                    if c == '{':
                        brace_count += 1
                    elif c == '}':
                        brace_count -= 1
                    i += 1
                continue

            if type_match is not None:
                declaration = _braced_declaration_source(
                    buffer,
                    type_match,
                    "declaration",
                )
                type_name = type_match.group("name")
                declaration_indent = declaration[:len(declaration) - len(declaration.lstrip())]
                if signatures_only:
                    result.append(declaration + " {")
                else:
                    result.append(buffer)
                    result.append(char)
                brace_depth += 1
                type_stack.append((type_name, brace_depth, declaration_indent))
            else:
                if not signatures_only:
                    result.append(buffer)
                    result.append(char)
                brace_depth += 1

            buffer = ""
            structural_buffer = ""
            i += 1
        elif structural[i] == ';' and signatures_only:
            directly_inside_type = bool(type_stack and brace_depth == type_stack[-1][1])
            enclosing_type = type_stack[-1][0] if directly_inside_type else None
            member_match = _braced_member_declaration_match(
                structural_buffer,
                enclosing_type,
                allow_regular_method=(
                    directly_inside_type or (not type_stack and brace_depth == 0)
                ),
            )
            if member_match is not None:
                signature = _braced_declaration_source(
                    buffer,
                    member_match,
                    "signature",
                )
                result.append(signature + ";")
            # At signatures tier both a retained abstract method and a dropped
            # field/import end the current declaration candidate here.
            buffer = ""
            structural_buffer = ""
            i += 1
        elif structural[i] == '}':
            if type_stack and brace_depth == type_stack[-1][1]:
                _, _, declaration_indent = type_stack.pop()
                if signatures_only:
                    result.append(declaration_indent + "}")
            if not signatures_only:
                result.append(buffer)
                result.append(char)
            buffer = ""
            structural_buffer = ""
            brace_depth = max(0, brace_depth - 1)
            i += 1
        else:
            buffer += char
            structural_buffer += structural[i]
            i += 1

    if buffer and not signatures_only:
        result.append(buffer)

    return "\n".join(result) if signatures_only else "".join(result)


def estimate_tokens(text: str) -> int:
    """Estimates the number of tokens in a string.

    A whitespace-split word count (~1.3 tokens/word) systematically
    undercounts real BPE tokenization for dotted/punctuation-heavy
    identifiers common in code (2026-08-12 SME review) - a long Java import
    statement like `import com.example.very.long.package.ClassName;` splits
    into only 2 "words" by whitespace, but a real tokenizer splits on
    punctuation too (dots, semicolons, camelCase boundaries), producing far
    more actual tokens - undermining the context budget allocator this
    function directly feeds (see _reserve_graph_context_budget's own
    docstring for a real 2026-08-07 incident from exactly this class of
    under-reservation).

    A character-count heuristic (~4 chars/token, the standard rule-of-thumb
    approximation for English-like BPE tokenizers) is punctuation-agnostic -
    it doesn't rely on whitespace at all, so it degrades gracefully for
    code instead of specifically failing on it, while staying close to the
    old word-based estimate for ordinary prose (average English word ~4.7
    chars + a space ~= 5.7 chars * ~1.3 tokens/word ~= 1 token per ~4.4
    chars - almost the same ratio)."""
    return max(1, len(text) // 4) if text else 0


# --- PRD-016 / CTX-001: allocation against the dispatch budget ---------------
# The builders below size prompt sections in estimate_tokens() units (len//4).
# The dispatch check (kriya/core/token_budget.py) counts the same text with a
# conservative byte bound (2.5 bytes per token by default, the measured ratio
# once the runtime is qualified) against the served window, keeping the
# configured output budget free. Before this, every builder took a fraction
# of the raw context_window in len//4 units, so a fully allocated Developer
# prompt (graph 0.75 + siblings 0.15 + retry evidence 1.5 chars/token, before
# task/design/system text) counted to more than the whole window: the
# dispatch check refused it, and before that check existed the local server
# silently dropped part of it.
#
# prompt_allocation_window() converts the dispatch room of a call - window
# minus its output budget, framing and safety margin - into allocator units.
# The builders share it: the graph pool (skills, learned knowledge, graph
# context, known-target and member-hint source; each later section reserves
# what the earlier ones already used) 0.60, retry evidence 0.15, already-
# written siblings 0.15, leaving 0.10 for the system prompt, task and
# directives (design and plan are reserved from the graph pool). Opt-in
# investigation evidence (DEV-INV-001) is capped at 0.10 on top; the dispatch
# check then trims max_tokens rather than refusing. Optional context is sized to the PREFERRED window only, so it
# never causes the dispatch to select a larger context tier; the byte bound
# covers ASCII text exactly and the dispatch check stays the backstop for
# the rest.
_ALLOCATOR_CHARS_PER_TOKEN = 4
_GRAPH_CONTEXT_SHARE = 0.60
_RETRY_EVIDENCE_SHARE = 0.15
_INVESTIGATION_EVIDENCE_SHARE = 0.10


def prompt_allocation_window(context_window: int, output_budget: int, *,
                             bytes_per_token: Optional[float] = None) -> int:
    """Allocator-unit tokens a two-message prompt may occupy in
    ``context_window`` while ``output_budget`` stays free (at most half the
    window: a config whose output budget is larger has declared an
    output-dominated call, and the dispatch check then reduces max_tokens)."""
    from kriya.core.token_budget import (
        DEFAULT_BYTES_PER_TOKEN,
        DISPATCH_SAFETY_MARGIN_TOKENS,
        TWO_MESSAGE_FRAMING_TOKENS,
    )

    reserve = min(max(0, int(output_budget)), int(context_window) // 2)
    room = int(context_window) - reserve - TWO_MESSAGE_FRAMING_TOKENS - DISPATCH_SAFETY_MARGIN_TOKENS
    ratio = bytes_per_token if bytes_per_token and bytes_per_token > 0 else DEFAULT_BYTES_PER_TOKEN
    return max(0, int(room * ratio / _ALLOCATOR_CHARS_PER_TOKEN))


def allocation_window(config: Any, binding: Any = None) -> int:
    """The prompt allocation window of a Developer-shaped call to ``binding``
    (the primary ``config.llm`` by default, or an ``llm_chain`` entry): the
    same served window, output budget and counting ratio LLMClient's dispatch
    check uses for that call (kriya/core/llm.py::complete_result)."""
    from kriya.core.llm import REASONING_MIN_MAX_TOKENS

    binding = binding if binding is not None else config.llm
    window = binding.context_window
    ratio = None
    try:
        from kriya.core.model_qualification import measured_limits_for
        from kriya.core.model_runtime import resolve_configured_model_runtime

        fingerprint = resolve_configured_model_runtime(
            config, binding.model, base_url=binding.base_url, api_key=binding.api_key,
            extra_body=binding.extra_body or {},
        )
        window = fingerprint.effective_context_window or window
        ratio = measured_limits_for(fingerprint, config).get("bytes_per_token_floor")
    except Exception as error:  # never blocks context assembly
        logger.debug("Allocation window for %s uses the configured window: %s", binding.model, error)
    output = config.llm.max_tokens
    if binding.reasoning:
        output = max(output, REASONING_MIN_MAX_TOKENS)
    return prompt_allocation_window(window, output, bytes_per_token=ratio)


# A review prompt is the file batch plus a goal header and candidate diff.
_REVIEW_BATCH_SHARE = 0.75


def review_batch_budget(config: Any) -> int:
    """Token budget (allocator units) of one review batch
    (review_context.build_review_batches) for the primary model."""
    return int(allocation_window(config) * _REVIEW_BATCH_SHARE)


def investigation_evidence_char_budget(prompt_window: int) -> int:
    """Character budget of DEV-INV-001 investigation evidence appended to a
    Developer prompt (it uses the fixed-text headroom of the window)."""
    return int(prompt_window * _INVESTIGATION_EVIDENCE_SHARE * _ALLOCATOR_CHARS_PER_TOKEN)


def retry_evidence_char_budget(prompt_window: int) -> int:
    """Character budget of a retry package's source evidence."""
    share = int(prompt_window * _RETRY_EVIDENCE_SHARE * _ALLOCATOR_CHARS_PER_TOKEN)
    floor = _proportional_floor(1000, prompt_window) * _ALLOCATOR_CHARS_PER_TOKEN
    return max(floor, min(48000, share))


# A section floor keeps a small window's section useful, but never more than
# this share of the prompt window: absolute floors in a small window added
# up to more than the window itself, a prompt the dispatch check refuses.
_FLOOR_SHARE_CAP = 0.15


def _proportional_floor(floor: int, prompt_window: int) -> int:
    return min(floor, int(prompt_window * _FLOOR_SHARE_CAP))


# Floor for build_code_context()'s own budget after skills_prompt/learned_rag_context
# are subtracted below - keeps the allocator functional (still returns SOME matched-file
# context, just skeletonized more aggressively) rather than collapsing to 0 and silently
# dropping all graph-RAG context for a retry that's already fighting an undersized window.
_MIN_GRAPH_CONTEXT_BUDGET = 1000


def _reserve_graph_context_budget(prompt_window: int, *unbounded_texts: str) -> int:
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
    silently dropping all matched/related file context for the rest of this attempt.

    PRD-016: ``prompt_window`` is the call's prompt_allocation_window() (see
    above), not the raw context_window, and the pool is its graph share."""
    base_budget = int(prompt_window * _GRAPH_CONTEXT_SHARE)
    reserved = sum(estimate_tokens(t) for t in unbounded_texts if t and isinstance(t, str))
    return max(_proportional_floor(_MIN_GRAPH_CONTEXT_BUDGET, prompt_window), base_budget - reserved)


# Sibling content (kriya/agents/agent.py's DeveloperAgent._fill_missing_content(),
# the "Already-Written File This Batch" section) is reference-only material for
# cross-file consistency, not the primary content a per-file completion is
# generating - a smaller fraction than build_code_context()'s own 0.75 is
# appropriate, since the bulk of the window still needs to go to the file's
# own graph-RAG context, task description, design context, and the model's
# own output.
_SIBLING_CONTENT_BUDGET_FRACTION = 0.15


def _reserve_sibling_content_budget(prompt_window: int) -> int:
    """Token budget for the concatenated "already-written sibling" section of a
    per-file Developer completion prompt (2026-08-15 external review, Finding 8).

    Before this fix, _fill_missing_content() concatenated every already-written
    sibling's FULL content unconditionally, with zero token accounting - the
    same class of bug _reserve_graph_context_budget() above was built to fix for
    skills_prompt/learned_rag_context (see that function's own docstring for the
    2026-08-07 incident), just in a different part of the same prompt. A large
    multi-file batch (many files, each individually a reasonable size) could
    silently accumulate an unbounded sibling section - worst-case exactly on the
    LAST file generated in the batch, where the model has the least room left
    to also produce its own new content.

    Scales with the ACTIVE model's context window (same convention as
    _reserve_graph_context_budget - a primary-model attempt and a fallback-model
    attempt get proportionally different budgets, not one hardcoded number that's
    generous for one and starves the other). ``prompt_window`` is the call's
    prompt_allocation_window() (PRD-016); the former absolute 500-token floor
    was removed with it - in a small window the floors of every section added
    up to a prompt larger than the window."""
    return int(prompt_window * _SIBLING_CONTENT_BUDGET_FRACTION)


_TIER_STEPS = ("full", "skeleton", "signatures")


# --- CTX-001 P1 WP6: omission reason vocabulary -----------------------------
# docs/assurance/CTX_001_P1_ARCHITECTURE.md section 8 - extends, never
# replaces, today's implicit reasons. Every material degradation/omission
# this module's shared allocator produces uses one of these, never a bare
# unexplained drop.
REASON_BUDGET_EXHAUSTED = "budget_exhausted"
REASON_BODY_ELIDED = "body_elided"
REASON_UNSUPPORTED_STRUCTURAL_EXTRACTION = "unsupported_structural_extraction"
REASON_STALE_REVISION_REJECTED = "stale_revision_rejected"
REASON_LOWER_RELEVANCE = "lower_relevance"
REASON_SOURCE_UNAVAILABLE = "source_unavailable"

# CTX-001 P1 WP7/A5 (architecture doc section 10): a known-target file's
# EFFECTIVE score is max(retrieval_score, KNOWN_TARGET_FLOOR) - high enough
# that a known target degrades only after every non-target candidate has
# already degraded to its own floor, but not so high that many known
# targets can each claim the whole budget regardless of size. Matches the
# upper end of DependencyGraph._RELATION_WEIGHTS' own hop-1 scale (graph.py)
# - a real hop-1 "imports"/"inherits" hit scores exactly 1.0 there, so 0.75
# stays a real, principled ceiling below the strongest possible organic
# signal, not an arbitrary magic number.
KNOWN_TARGET_FLOOR = 0.75


def _build_file_tiers(
    matched_contents: Dict[str, str], related_contents: Dict[str, str],
    budget_limit: int, file_scores: Optional[Dict[str, float]],
    get_skeletonized: Callable[[str, str, str], str],
) -> Dict[str, str]:
    """The exact tier-assignment algorithm build_code_context() has always
    used (extracted verbatim, not rewritten) - the one place this decision
    is made, shared by both the legacy string renderer and the WP6
    ContextPackage-producing renderer below, so they can never drift apart."""
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

        file_tiers = {f: matched_tier for f in matched_contents}
        file_tiers.update({f: related_tier for f in related_contents})
        # Last related file first, then last matched file.
        drop_order = list(reversed(list(related_contents))) + list(reversed(list(matched_contents)))
        return _omit_over_budget(file_tiers, matched_contents, related_contents, budget_limit,
                                 get_skeletonized, drop_order)

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

    # Lowest score first (stable: earlier-listed files kept on ties).
    drop_order = sorted(reversed(list(file_tiers)), key=lambda f: file_scores.get(f, 0.0))
    return _omit_over_budget(file_tiers, matched_contents, related_contents, budget_limit,
                             get_skeletonized, drop_order)


# A file whose signatures still do not fit the budget is left out of the
# prompt entirely (recorded as a budget_exhausted omission and named in the
# rendered context). Before PRD-016 the tier search simply stopped at
# "signatures" and rendered everything, so the budget was not a bound: a
# dozen large classes rendered ~210K characters against a ~4.7K-token limit.
_OMITTED_TIER = "omitted"


def _omit_over_budget(
    file_tiers: Dict[str, str], matched_contents: Dict[str, str], related_contents: Dict[str, str],
    budget_limit: int, get_skeletonized: Callable[[str, str, str], str], drop_order: List[str],
) -> Dict[str, str]:
    contents = {**matched_contents, **related_contents}

    def total() -> int:
        return sum(
            estimate_tokens(get_skeletonized(contents[f], f, tier))
            for f, tier in file_tiers.items() if tier != _OMITTED_TIER
        )

    for filepath in drop_order:
        if total() <= budget_limit:
            break
        file_tiers[filepath] = _OMITTED_TIER
    return file_tiers


def build_code_context_package(
    matched_files: List[str], related_files: List[str], workspace_path: str, budget_limit: int,
    file_scores: Optional[Dict[str, float]] = None,
    cache: "Optional[SourceDerivationCache]" = None,
) -> Tuple[str, Any]:
    """CTX-001 P1 WP6: the real implementation build_code_context() (below)
    is now a thin wrapper around - becomes the one unit-producing AND
    rendering path for the default pipeline's Graph RAG matched/related
    context (architecture doc section 5a's own hard constraint). Internal
    difference only: alongside the identical rendered string, this also
    builds a real ContextPackage (relevant_files + an honest, reason-
    labeled omitted[] list) - closing C6 (the default pipeline previously
    had no structured provenance/omission tracking at all) without any
    caller-visible signature/output change for build_code_context()'s own
    existing callers.

    Returns (rendered_string, ContextPackage) - rendered_string is
    BYTE-IDENTICAL to what the pre-P1 build_code_context() produced for the
    same inputs (see _build_file_tiers()'s own docstring: the tier-
    assignment algorithm is reused verbatim, not reimplemented, and the
    render loop below reproduces the exact same block format/order)."""
    # Local imports: context_package.py has no dependency on this module
    # today, and this keeps that direction one-way (avoids a new
    # module-level import cycle risk for the common case where a caller
    # only wants the plain-string build_code_context() below).
    from kriya.policy.trust import TrustLevel
    from kriya.workflow.context_package import build_context_package, make_context_item, make_omitted_entry

    def _read(full_p: str, f: str) -> Optional[str]:
        # CTX-001 P1 WP9: cache is None for every existing caller (default) -
        # byte-identical fresh-read behavior, unchanged. A caller that wants
        # attempt-lifetime reuse passes its own SourceDerivationCache
        # (context_source.py) - shared with CurrentSourceResolver's own
        # content_cache, so a file already read via either path this
        # attempt is never re-read via the other.
        if cache is not None:
            read = cache.read(workspace_path, f)
            return read[0] if read is not None else None
        try:
            with open(full_p, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except Exception as e:
            logger.debug(f"Failed to read '{full_p}' for RAG context: {e}")
            return None

    matched_contents = {}
    for f in matched_files:
        full_p = os.path.join(workspace_path, f)
        if os.path.exists(full_p):
            content = _read(full_p, f)
            if content is not None:
                matched_contents[f] = content

    related_contents = {}
    for f in related_files:
        full_p = os.path.join(workspace_path, f)
        if os.path.exists(full_p):
            content = _read(full_p, f)
            if content is not None:
                related_contents[f] = content

    # Per-call memo (unchanged from pre-WP9 behavior) PLUS, when `cache` is
    # given, the attempt-lifetime SourceDerivationCache - the per-call dict
    # still avoids a second dict lookup for the ~3-5 repeat calls each file
    # gets within ONE _build_file_tiers() budget search; `cache` is what
    # actually survives across separate build_code_context_package() calls
    # (different retries in the same attempt).
    skel_cache = {}

    def get_skeletonized(content: str, filepath: str, tier: str) -> str:
        key = (filepath, tier)
        if key not in skel_cache:
            if cache is not None:
                revision = content_revision(content)
                rendered, _tokens = cache.get_or_compute_derivation(
                    filepath, None, tier, revision,
                    lambda: skeletonize_code(content, filepath, tier),
                )
                skel_cache[key] = rendered
            else:
                skel_cache[key] = skeletonize_code(content, filepath, tier)
        return skel_cache[key]

    omitted: List[Dict[str, Any]] = []
    # source_unavailable (P0's own finding: today this is a SILENT
    # `except Exception: logger.debug(...)` swallow, context_budget.py's
    # pre-P1 lines 642-643) - now also a real, recorded omission entry,
    # alongside the unchanged debug log.
    for f in matched_files:
        if f not in matched_contents:
            omitted.append(make_omitted_entry(path=f, rank=0, reason=REASON_SOURCE_UNAVAILABLE, estimated_tokens=0))
    for f in related_files:
        if f not in related_contents:
            omitted.append(make_omitted_entry(path=f, rank=0, reason=REASON_SOURCE_UNAVAILABLE, estimated_tokens=0))

    file_tiers = _build_file_tiers(matched_contents, related_contents, budget_limit, file_scores, get_skeletonized)

    graph_rag_context = "\n\n=== Codebase Semantic Reference Context ===\n"
    items = []
    budget_omitted: List[str] = []

    def _record_budget_omission(filepath: str, content: str) -> None:
        budget_omitted.append(filepath)
        omitted.append(make_omitted_entry(
            path=filepath, rank=0, reason=REASON_BUDGET_EXHAUSTED,
            estimated_tokens=estimate_tokens(get_skeletonized(content, filepath, "signatures")),
        ))

    for filepath, content in matched_contents.items():
        tier = file_tiers[filepath]
        if tier == _OMITTED_TIER:
            _record_budget_omission(filepath, content)
            continue
        skel = get_skeletonized(content, filepath, tier)
        graph_rag_context += f"\nFile: {filepath} (Tier: {tier})\n{skel}\n"
        items.append(make_context_item(
            path=filepath, content=skel, reason="graph_rag_matched_file", source_type="semantic_hit",
            trust_level=TrustLevel.REPOSITORY, score=(file_scores or {}).get(filepath),
            tier=tier, is_exact=(tier == "full"), revision=content_revision(content),
            omitted_regions=(tier != "full"),
        ))
        if tier != "full":
            omitted.append(make_omitted_entry(
                path=filepath, rank=0, reason=REASON_BODY_ELIDED, estimated_tokens=estimate_tokens(skel),
            ))

    if related_contents:
        graph_rag_context += "\n\n=== Bounded Neighborhood Dependency Context ===\n"
        for filepath, content in related_contents.items():
            tier = file_tiers[filepath]
            if tier == _OMITTED_TIER:
                _record_budget_omission(filepath, content)
                continue
            skel = get_skeletonized(content, filepath, tier)
            graph_rag_context += f"\nFile: {filepath} (Tier: {tier})\n{skel}\n"
            items.append(make_context_item(
                path=filepath, content=skel, reason="graph_rag_related_file", source_type="graph_dependency",
                trust_level=TrustLevel.REPOSITORY, score=(file_scores or {}).get(filepath),
                tier=tier, is_exact=(tier == "full"), revision=content_revision(content),
                omitted_regions=(tier != "full"),
            ))
            if tier != "full":
                omitted.append(make_omitted_entry(
                    path=filepath, rank=0, reason=REASON_BODY_ELIDED, estimated_tokens=estimate_tokens(skel),
                ))

    if budget_omitted:
        graph_rag_context += (
            f"\n(Left out for the context budget, not shown above: {', '.join(budget_omitted)})\n"
        )

    package = build_context_package(
        relevant_files=tuple(items),
        omitted=tuple(omitted),
        token_count=sum(estimate_tokens(item.content) for item in items),
    )
    return graph_rag_context, package


def build_code_context(
    matched_files: List[str], related_files: List[str], workspace_path: str, budget_limit: int,
    file_scores: Optional[Dict[str, float]] = None, cache: "Optional[SourceDerivationCache]" = None,
) -> str:
    return build_code_context_package(matched_files, related_files, workspace_path, budget_limit, file_scores, cache)[0]


def _fit_whole_file(content: str, remaining_tokens: int) -> Optional[Tuple[str, str, str, bool]]:
    """Whole-file (no member hint, or member extraction unavailable/
    over-budget) fallback for build_known_target_context() below. Unlike
    build_code_context()'s own skeleton/signatures structural tiers, a
    known-target file's worst case is a BOUNDED, revision-marked head+tail
    EXCERPT of its real body (project_implementation_source, already used
    by RetryPackage) - architecture doc section 9's own explicit "converges
    onto RetryPackage's pattern rather than reinventing" instruction: for a
    "repair this exact file" instruction, real (if truncated) implementation
    beats a structural skeleton with every body already replaced by "...".

    Returns (tier, rendered_content, reason, omitted_regions), or None only
    when remaining_tokens <= 0 (nothing at all can fit - the caller must
    record an explicit budget_exhausted omission instead; every other case
    always returns real content, since project_implementation_source always
    fits within the given character budget by construction).

    CTX-001 P1 WP9 (deliberately NOT cache-routed): unlike skeletonize_code()/
    extract_member_body() (pure functions of content+tier/line-range alone,
    safe to cache by (path, tier-or-member_id, revision)), this function's
    own bounded-excerpt output is a function of `remaining_tokens` too - the
    SAME file's cached excerpt from an earlier call (when a different
    amount of budget happened to be left) would be WRONG-SIZED for a later
    call with a different remaining_tokens, silently violating cached-vs-
    uncached semantic equivalence. Caching this would require folding the
    budget into the cache key, defeating cross-call reuse for exactly the
    case (budget genuinely differs run to run) real reuse would matter
    least. Left uncached; the FULL-content branch just above needs no
    caching benefit anyway (no real computation beyond estimate_tokens)."""
    if remaining_tokens <= 0:
        return None
    full_cost = estimate_tokens(content)
    if full_cost <= remaining_tokens:
        return ("full", content, "known_target_full_source", False)
    from kriya.workflow.context_projection import project_implementation_source

    max_chars = remaining_tokens * 4  # inverse of estimate_tokens' own len//4 heuristic
    projection = project_implementation_source(
        content, "known_target", max_chars, reason="known_target_bounded_excerpt",
    )
    # CTX_001_P1_ARCHITECTURE.md section 4's own compatibility mapping:
    # IMPLEMENTATION_EXCERPT -> tier "skeleton", omitted_regions=True -
    # distinguishable from a real STRUCTURAL skeleton only via `reason`
    # (free text), not a dedicated tier value.
    return ("skeleton", projection.content, "known_target_bounded_excerpt", True)


def _render_known_target_block(items: List[Any], omitted: List[Dict[str, Any]]) -> str:
    """Mirrors RetryPackage.render_context()'s own established pattern
    (retry_package.py's "=== Additional files omitted from retry evidence
    budget ===" block) - generalized here rather than reinvented. Renders a
    BOUNDED summary line for any omission, so "modification-critical exact
    target evidence could not be represented" is never silent from the
    model's own side (WP7's own critical invariant) - the full, reason-
    labeled detail lives on the returned ContextPackage/run trace, never
    dumped in full into the prompt itself (observability requirement: don't
    flood the prompt with internal metadata)."""
    if not items and not omitted:
        return ""
    blocks = []
    for item in items:
        label = f"member {item.member_id}" if item.member_id else "full source"
        blocks.append(f"=== EXISTING OWNER ({label}, tier={item.tier}): {item.path} ===\n{item.content}")
    if omitted:
        omitted_paths = sorted({str(o["path"]) for o in omitted})
        blocks.append(
            "=== Additional known-target evidence omitted from this context budget "
            "(see run trace for reason/path detail - do not assume it matches the code above) ===\n"
            + ", ".join(omitted_paths)
        )
    return (
        "\n\n=== AUTHORITATIVE BROWNFIELD OWNER CONTRACT: EXISTING SOURCE ===\n"
        + "\n\n".join(blocks)
    )


def build_known_target_context(
    known_target_files: List[str],
    workspace_path: str,
    worktree_path: Optional[str],
    budget_limit: int,
    *,
    file_scores: Optional[Dict[str, float]] = None,
    member_hints: Optional[Dict[str, Union[str, Sequence[str]]]] = None,
    known_revisions: Optional[Dict[str, str]] = None,
    exclude: Optional[Iterable[str]] = None,
    cache: "Optional[SourceDerivationCache]" = None,
) -> Tuple[str, Any]:
    """CTX-001 P1 WP7 (A3+A5): replaces attempt.py's own
    _brownfield_owner_contract_block()'s SOURCE-CONTENT responsibility (its
    instruction-text responsibility is unchanged, stays in task_desc - see
    that function's own updated docstring). Retires the naive 24,000-char
    combined prefix cap entirely (never raised - architecture doc section 9's
    explicit "do NOT solve by making the cap larger" instruction) in favor
    of: current-source resolution (WP4, worktree-authoritative), member-aware
    exact source where a caller supplies a member_hints entry and the
    language supports it (WP5), a priority floor rather than unconditional/
    unlimited inclusion (A5, KNOWN_TARGET_FLOOR above), and explicit,
    reason-labeled omission for anything that genuinely cannot fit - never a
    silent `break`.

    `exclude` lets a caller skip paths already represented through a
    DIFFERENT context producer (e.g. build_code_context_package()'s own
    Graph-RAG matched/related output) - the DUPLICATE_SOURCE_CONTEXT_PATHS=0
    requirement's own mechanism: known-target evidence always wins for
    EXACTNESS over a possibly-degraded Graph-RAG hit for the same path, so
    the correct precedence is "exclude a known-target path from the lower-
    fidelity producer", never the reverse - callers are expected to already
    apply that precedence before calling this function (see attempt.py's own
    integration).

    Returns (rendered_string, ContextPackage) - the rendered string is meant
    to be appended to active_code_context (source content), never task_desc
    (instructions) - see the module-level split this function implements."""
    from kriya.policy.trust import TrustLevel
    from kriya.workflow.context_package import build_context_package, make_context_item, make_omitted_entry
    from kriya.workflow.context_source import (
        CurrentSourceResolver, boundaries_matching_member_id, extract_member_body, member_boundaries_for,
    )

    exclude_set = set(exclude or ())
    # CTX-001 P1 WP9: cache is None for every existing caller (default) -
    # a fresh, private content_cache dict, byte-identical to pre-WP9
    # behavior. A caller wanting attempt-lifetime reuse passes its own
    # SourceDerivationCache - shared with build_code_context_package()'s
    # own reads, so no file is ever read twice via two different producers
    # in the same attempt.
    resolver = CurrentSourceResolver(
        workspace_path, worktree_path, known_revisions,
        content_cache=(cache.content_cache if cache is not None else None),
    )

    def effective_score(path: str) -> float:
        return max((file_scores or {}).get(path, 0.0), KNOWN_TARGET_FLOOR)

    # De-dup while preserving first-seen order (dict.fromkeys), then
    # priority-floor-then-score sort (stable - ties keep that first-seen/
    # Architect-list order, never an arbitrary one) - "allocation order
    # becomes a function of (floor, score), never list position" (A5).
    ordered_paths = [p for p in dict.fromkeys(known_target_files) if p not in exclude_set]
    ordered_paths.sort(key=effective_score, reverse=True)

    items: List[Any] = []
    omitted: List[Dict[str, Any]] = []
    consumed = 0

    for rank, path in enumerate(ordered_paths, start=1):
        resolved = resolver.resolve(path)
        if not resolved.exists:
            omitted.append(make_omitted_entry(path=path, rank=rank, reason=REASON_SOURCE_UNAVAILABLE, estimated_tokens=0))
            continue
        if resolved.stale_hint:
            # Not fatal - resolved.content/revision are already the REAL,
            # freshly-read values (the resolver never trusts a stale hint
            # silently); recorded so a caller can see a supplied
            # known_revisions hint didn't match reality.
            omitted.append(make_omitted_entry(path=path, rank=rank, reason=REASON_STALE_REVISION_REJECTED, estimated_tokens=0))

        remaining = budget_limit - consumed
        if remaining <= 0:
            omitted.append(make_omitted_entry(
                path=path, rank=rank, reason=REASON_BUDGET_EXHAUSTED,
                estimated_tokens=estimate_tokens(resolved.content),
            ))
            continue

        # CTX-001 P1 C2 production integration: member_hints[path] may be a
        # bare string (Package 2's original, single-member shape - still
        # fully supported) or a list/tuple of member_ids (multiple grounded
        # candidates for the same file - e.g. two Java overloads a bare
        # name alone could not uniquely distinguish; see
        # context_source.py::member_ids_matching_name's own docstring).
        # Never a new type, never a redesign of the call boundary - just an
        # additive Union on the existing dict's VALUE shape.
        raw_hint = (member_hints or {}).get(path)
        if isinstance(raw_hint, str):
            member_id_candidates = [raw_hint]
        elif raw_hint:
            member_id_candidates = list(raw_hint)
        else:
            member_id_candidates = []

        member_produced = False
        if member_id_candidates:
            boundaries = member_boundaries_for(path, resolved.content)
            if boundaries is None:
                for member_id in member_id_candidates:
                    omitted.append(make_omitted_entry(
                        path=path, rank=rank, reason=REASON_UNSUPPORTED_STRUCTURAL_EXTRACTION,
                        estimated_tokens=0, member_id=member_id,
                    ))
            else:
                for member_id in member_id_candidates:
                    # An ambiguous (overloaded) member_id expands to EVERY
                    # real boundary sharing it - never an arbitrary "first
                    # match" pick (context_source.py::
                    # boundaries_matching_member_id's own docstring).
                    matching_boundaries = boundaries_matching_member_id(boundaries, member_id)
                    if not matching_boundaries:
                        omitted.append(make_omitted_entry(
                            path=path, rank=rank, reason=REASON_UNSUPPORTED_STRUCTURAL_EXTRACTION,
                            estimated_tokens=0, member_id=member_id,
                        ))
                        continue
                    for boundary in matching_boundaries:
                        member_remaining = budget_limit - consumed
                        if member_remaining <= 0:
                            omitted.append(make_omitted_entry(
                                path=path, rank=rank, reason=REASON_BUDGET_EXHAUSTED,
                                estimated_tokens=0, member_id=member_id,
                            ))
                            continue
                        if cache is not None:
                            # Cache-key discriminator includes the boundary's
                            # own line range, not just member_id - an
                            # ambiguous (overloaded) name can resolve to
                            # SEVERAL distinct boundaries sharing one
                            # member_id (see boundaries_matching_member_id's
                            # own docstring); using member_id alone here
                            # would collide two real, DIFFERENT bodies into
                            # one cache entry. The ContextItem's own
                            # member_id (below) stays the clean, real value -
                            # this discriminator is a cache-key-only detail.
                            cache_member_id = f"{member_id}:{boundary.start_line}-{boundary.end_line}"
                            member_content, member_cost = cache.get_or_compute_derivation(
                                path, cache_member_id, "member_exact", resolved.revision,
                                lambda b=boundary: extract_member_body(resolved.content, b.start_line, b.end_line),
                            )
                        else:
                            member_content = extract_member_body(resolved.content, boundary.start_line, boundary.end_line)
                            member_cost = estimate_tokens(member_content)
                        if member_cost <= member_remaining:
                            items.append(make_context_item(
                                path=path, content=member_content, reason="known_target_member_exact",
                                source_type="named_in_request", trust_level=TrustLevel.REPOSITORY,
                                score=effective_score(path), member_id=member_id,
                                start_line=boundary.start_line, end_line=boundary.end_line,
                                tier="member_exact", is_exact=True, revision=resolved.revision,
                                omitted_regions=False,
                            ))
                            consumed += member_cost
                            member_produced = True
                        else:
                            omitted.append(make_omitted_entry(
                                path=path, rank=rank, reason=REASON_BODY_ELIDED, estimated_tokens=member_cost,
                                member_id=member_id,
                            ))

            if member_produced:
                sibling_remaining = budget_limit - consumed
                if sibling_remaining > 0:
                    if cache is not None:
                        sibling_text, sib_cost = cache.get_or_compute_derivation(
                            path, None, "signatures", resolved.revision,
                            lambda: skeletonize_code(resolved.content, path, "signatures"),
                        )
                    else:
                        sibling_text = skeletonize_code(resolved.content, path, "signatures")
                        sib_cost = estimate_tokens(sibling_text) if sibling_text else 0
                    if sibling_text:
                        if sib_cost <= sibling_remaining:
                            items.append(make_context_item(
                                path=path, content=sibling_text, reason="known_target_sibling_signatures",
                                source_type="named_in_request", trust_level=TrustLevel.REPOSITORY,
                                score=effective_score(path), tier="signatures", is_exact=False,
                                revision=resolved.revision, omitted_regions=True,
                            ))
                            consumed += sib_cost
                        else:
                            omitted.append(make_omitted_entry(
                                path=path, rank=rank, reason=REASON_BODY_ELIDED, estimated_tokens=sib_cost,
                            ))

        if member_produced:
            continue

        remaining = budget_limit - consumed
        fit = _fit_whole_file(resolved.content, remaining)
        if fit is None:
            omitted.append(make_omitted_entry(
                path=path, rank=rank, reason=REASON_BUDGET_EXHAUSTED,
                estimated_tokens=estimate_tokens(resolved.content),
            ))
            continue
        tier, rendered_content, reason, omitted_regions = fit
        items.append(make_context_item(
            path=path, content=rendered_content, reason=reason, source_type="named_in_request",
            trust_level=TrustLevel.REPOSITORY, score=effective_score(path), tier=tier,
            is_exact=(tier == "full"), revision=resolved.revision, omitted_regions=omitted_regions,
        ))
        consumed += estimate_tokens(rendered_content)
        if omitted_regions:
            omitted.append(make_omitted_entry(
                path=path, rank=rank, reason=REASON_BODY_ELIDED, estimated_tokens=estimate_tokens(rendered_content),
            ))

    package = build_context_package(
        relevant_files=tuple(items),
        omitted=tuple(omitted),
        token_count=sum(estimate_tokens(item.content) for item in items),
    )
    rendered = _render_known_target_block(items, omitted)
    return rendered, package
