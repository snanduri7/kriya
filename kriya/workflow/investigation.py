"""DEV-INV-001: bounded, governed, model-directed repository investigation
inside the Developer attempt lifecycle.

Moves the Developer from `precomputed context -> generate -> validate ->
retry` toward `initial evidence -> investigate as needed -> propose ->
existing mutation authority -> verify -> repair`. Only the INVESTIGATE step
is new - PROPOSE/MUTATE/VERIFY/REPAIR are entirely unchanged (this module
never calls DeveloperAgent.run_generation, never writes a file, never
touches D1's own `_completeness_gated_operation`/AuthorizedFileWriter).

CORE INVARIANT: the LLM proposes, Kriya governs. INVESTIGATE is strictly
read-only - every verb here wraps an EXISTING, already-authoritative
resolver (CurrentSourceResolver/member_boundaries_for, DependencyGraph,
LocalVectorStore.query_hybrid) and every path-backed read passes through
kriya/policy/filesystem.py::AuthorizedFileReader (workspace containment +
sensitive-path denial) before its content or location is ever returned.
Model-directed request text is normalized into a small, closed vocabulary
(InvestigationRequest) and is NEVER interpreted as an instruction beyond
"which of these four governed lookups do you want" - repository content
returned as evidence remains untrusted data, never authority.

Reuses ContextItem.source_type's EXISTING closed vocabulary rather than
adding a "developer_investigation" value: `inspect_member` (the model
explicitly named this exact path/member) maps to "named_in_request",
already reused this same way by kriya/workflow/attempt.py's own retry-
projection context items for "explicitly targeted, not inferred" content;
`find_symbol`/`find_callers` (DependencyGraph-backed) map to
"graph_dependency"; `search_code` (vector-store-backed) maps to
"semantic_hit". `reason` (free text, e.g. "developer_investigation:
inspect_member:Foo.bar") is what actually distinguishes DEV-INV-001
provenance for observability - tests/test_context_package.py's own
`test_context_source_types_cover_the_design_docs_own_vocabulary` pins this
vocabulary as closed on purpose (an intentional tripwire, not incidental);
extending it needs proof the EXISTING values cannot represent the evidence,
which is not the case here.

No new EvidenceItem class (ContextItem already covers path/revision/
member_id/tier/is_exact/provenance), no MCP, no Planner/Architect
investigation, no new retrieval architecture, no unrestricted shell,
no model-directed writes. See docs/assurance/DEV_INV_001_ARCHITECTURE.md
for the full design rationale.

Feature-flagged: kriya/workflow/attempt.py's `_maybe_run_developer_
investigation` is the only real caller, gated on
`autonomy.developer_investigation_enabled` (default False). This module is
otherwise a pure, `state`/`ctx`-free library - every function here is
testable with a stub LLM/search_code callable and zero live model/embedding
calls, by construction (see InvestigationDependencies.search_code)."""

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union

from kriya.config.config import ModelCapabilities
from kriya.core.llm import LLMClient
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.filesystem import AuthorizedFileReader
from kriya.workflow.context_package import ContextItem, make_context_item
from kriya.workflow.context_source import (
    CurrentSourceResolver,
    SourceDerivationCache,
    boundaries_matching_member_id,
    extract_member_body,
    member_boundaries_for,
    resolve_member_hints_from_chunk_header,
)
from kriya.workflow.run_events import EventAuthority, RunEvent

logger = logging.getLogger(__name__)

# --- 1. MVP verb set (DEV-INV-001 section 2) --------------------------------
# Deliberately small and closed - do not add inspect_dependency/
# find_implementations/inspect_related_tests/etc. here. A verb not in this
# tuple fails closed at both protocol-normalization sites below AND at
# dispatch_investigation_request()'s own defensive fallback.
INVESTIGATION_VERBS: Tuple[str, ...] = (
    "inspect_member", "find_symbol", "find_callers", "search_code",
)

# Bounds enforced regardless of what the model asks for (section 8 - context
# budget must refine, not accumulate). Small, fixed module constants rather
# than new config surface - these are safety ceilings, not tuning knobs a
# repository should ever need to change.
_MAX_SYMBOL_RESULTS = 8
_MAX_SEARCH_RESULTS = 5
_MAX_SEARCH_RESULT_CHARS = 1500
_MAX_MEMBER_LISTING_ENTRIES = 60


# --- 2. One internal request representation (section 3) ---------------------

@dataclass(frozen=True)
class InvestigationRequest:
    """The ONE internal representation both the native tool-call protocol
    and the marker/text fallback protocol normalize into. Everything
    downstream (authorization, resolution, no-progress fingerprinting)
    operates on this alone and never on which protocol produced it."""

    verb: str
    arguments: Dict[str, Any]


@dataclass(frozen=True)
class ProposeSignal:
    """The model is ready to move on to PROPOSE - either it said so
    explicitly (marker protocol: no INVESTIGATE: line: native protocol: no
    tool call), or it never engaged with INVESTIGATE at all (a weak/non-
    participating Developer degrades to this, never to MalformedInvestigation
    Request - see parse_marker_response's own docstring)."""


@dataclass(frozen=True)
class MalformedInvestigationRequest:
    """A genuine, detected attempt to use INVESTIGATE that failed to parse
    (unknown verb, missing/invalid arguments) - section 4's own "do NOT
    interpret a malformed request as sufficient evidence" case. Carries
    enough for one bounded protocol-correction message, never raw model
    output beyond what's needed for that message."""

    raw_text: str
    detail: str


InvestigationTurnInput = Union[InvestigationRequest, ProposeSignal, MalformedInvestigationRequest]


class InvestigationVerbArgumentError(ValueError):
    """A well-formed InvestigationRequest (known verb, dict arguments) whose
    argument VALUES are still unusable (missing/wrong-typed required key).
    Caught by dispatch_investigation_request() and turned into an ordinary
    fed-back ERROR string - never a crash of the loop."""


# --- 3. Protocol normalization (section 3) -----------------------------------
# Native tool-call schemas - small-argument-only, mirroring kriya/workflow/
# self_correction.py's own established READ_FILE_TOOL/LIST_FILES_TOOL shape
# (the one other bounded native-tool-calling loop in this codebase). No tool
# here ever accepts or returns a whole file's content as a single argument.

INSPECT_MEMBER_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "inspect_member",
        "description": (
            "Look up the real, current structure of one file already in this repository. "
            "With 'member_id', returns that member's exact current body (class/method/function). "
            "Without it, returns a listing of the file's top-level members and their line ranges, "
            "so you can pick the right member_id on a follow-up call."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the project root."},
                "member_id": {
                    "type": "string",
                    "description": "Optional dotted member id, e.g. 'ClassName.methodName' or 'top_level_func'.",
                },
            },
            "required": ["path"],
        },
    },
}

FIND_SYMBOL_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "find_symbol",
        "description": (
            "Find which file(s) declare a class/method/function by name, using the repository's "
            "real indexed symbol table - not a guess. Returns file + line range per match."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "The exact symbol name to look up."},
            },
            "required": ["symbol"],
        },
    },
}

FIND_CALLERS_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "find_callers",
        "description": (
            "Find which files contain a call to a given function/method name, using the "
            "repository's real indexed dependency graph - useful before changing a signature or "
            "behavior that other code already depends on. File-level precision, not per-call-site."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "The exact called symbol name to look up."},
            },
            "required": ["symbol"],
        },
    },
}

SEARCH_CODE_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "search_code",
        "description": (
            "Semantic search over the repository's indexed source for a natural-language "
            "description of the code you're looking for - use this when you don't know an exact "
            "symbol or file name."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "A short natural-language description."},
            },
            "required": ["query"],
        },
    },
}

INVESTIGATION_TOOLS: Tuple[Dict[str, Any], ...] = (
    INSPECT_MEMBER_TOOL, FIND_SYMBOL_TOOL, FIND_CALLERS_TOOL, SEARCH_CODE_TOOL,
)


def normalize_native_tool_call(call: Dict[str, Any]) -> InvestigationTurnInput:
    """LLMClient.complete_with_tools() already decodes wire-format tool
    calls into {id, name, arguments} (or {..., argument_error} for
    malformed JSON arguments - see that method's own docstring). This is
    the ONE place native-protocol shape becomes an InvestigationTurnInput -
    an unknown tool name fails closed exactly like an unknown marker verb
    (section 4/5's "unknown verbs fail closed", no fuzzy match)."""
    name = call.get("name")
    if call.get("argument_error"):
        return MalformedInvestigationRequest(
            raw_text=f"{name}({call.get('arguments')!r})",
            detail=str(call["argument_error"]),
        )
    if name not in INVESTIGATION_VERBS:
        return MalformedInvestigationRequest(
            raw_text=str(name), detail=f"unknown investigation tool '{name}'",
        )
    arguments = call.get("arguments")
    if not isinstance(arguments, dict):
        return MalformedInvestigationRequest(
            raw_text=str(name), detail="tool arguments did not decode to an object",
        )
    return InvestigationRequest(verb=name, arguments=arguments)


# Marker/text fallback protocol, for a model without reliable native tool
# calling (the majority of local models - see kriya/core/model_capabilities.
# py's own conservative default). One line, verb + a single JSON object,
# anchored at line start so a narrative sentence can never accidentally
# match: "INVESTIGATE: <verb> <json-object>". \w+ (not \S+) for the verb
# token so a JSON object immediately following with no space (no separator
# at all) still parses correctly - JSON's own leading '{' is not a word
# character, so \w+ stops there on its own.
_INVESTIGATE_MARKER_RE = re.compile(r"^\s*INVESTIGATE:\s*(\w+)\s*(.*)$", re.IGNORECASE)


def parse_marker_response(text: str) -> InvestigationTurnInput:
    """Scans every line (not just the first) for the first one matching the
    INVESTIGATE: marker at line start. No matching line at all -> an
    IMPLICIT ProposeSignal - this is what makes a weak/non-participating
    Developer (one that never learned or used the marker protocol) degrade
    safely to today's behavior (section 14's own required proof), rather
    than being treated as a malformed request. A line that DOES start with
    INVESTIGATE: but fails to parse (unknown verb, missing/invalid JSON) is
    a genuine, detected malformed attempt - never silently reinterpreted as
    PROPOSE (section 4's own explicit rule)."""
    for line in (text or "").splitlines():
        match = _INVESTIGATE_MARKER_RE.match(line)
        if not match:
            continue
        verb = match.group(1)
        rest = match.group(2).strip()
        if verb not in INVESTIGATION_VERBS:
            return MalformedInvestigationRequest(raw_text=line, detail=f"unknown verb '{verb}'")
        if not rest:
            return MalformedInvestigationRequest(raw_text=line, detail="missing arguments object")
        try:
            arguments = json.loads(rest)
        except json.JSONDecodeError as exc:
            return MalformedInvestigationRequest(raw_text=line, detail=f"arguments are not valid JSON: {exc}")
        if not isinstance(arguments, dict):
            return MalformedInvestigationRequest(raw_text=line, detail="arguments must decode to a JSON object")
        return InvestigationRequest(verb=verb, arguments=arguments)
    return ProposeSignal()


def marker_protocol_instructions() -> str:
    """The exact instructions embedded in the fallback system prompt -
    exposed as its own function so a test can assert the documented syntax
    and parse_marker_response() can never silently drift apart."""
    verb_lines = "\n".join(
        f"  INVESTIGATE: {verb} {{...}}" for verb in INVESTIGATION_VERBS
    )
    return (
        "To investigate before proposing, reply with EXACTLY ONE line of this form "
        "(nothing else in the reply):\n"
        f"{verb_lines}\n"
        "  inspect_member {\"path\": \"<repo-relative path>\", \"member_id\": \"<optional dotted id>\"}\n"
        "  find_symbol {\"symbol\": \"<exact name>\"}\n"
        "  find_callers {\"symbol\": \"<exact name>\"}\n"
        "  search_code {\"query\": \"<short natural-language description>\"}\n"
        "When you have enough evidence, reply with a short plain-text confirmation that you are "
        "ready to propose the implementation - do not include an INVESTIGATE: line in that reply."
    )


# --- 4. Evidence resolvers (section 6/2) -------------------------------------

@dataclass(frozen=True)
class InvestigationDependencies:
    """Everything a resolver needs, injected - no resolver ever imports
    kriya.analyzer.graph/kriya.memory.vector at call time for anything other
    than the short-lived DependencyGraph open/query/close idiom kriya/
    workflow/attempt.py's own _extract_class_names_best_effort already
    established (dependency_graph_db_path). `search_code` is injected as a
    plain async callable (query -> list of {filepath, text, score} dicts,
    the exact shape LocalVectorStore.query_hybrid already returns) so this
    module never constructs an embedding client or knows an embedding
    endpoint exists - the real implementation (kriya/workflow/attempt.py)
    wires OllamaEmbeddingClient + LocalVectorStore exactly the way
    workflow.py's own Graph RAG retrieval stage already does; a test wires a
    stub returning canned dicts instead. This is what makes search_code
    testable with NO live model/embedding call, from the start."""

    workspace_path: str
    worktree_path: Optional[str]
    dependency_graph_db_path: str
    search_code: Callable[[str], Awaitable[List[Dict[str, Any]]]]
    source_cache: SourceDerivationCache


def _current_root(deps: InvestigationDependencies) -> str:
    return deps.worktree_path if deps.worktree_path else deps.workspace_path


def _reader(deps: InvestigationDependencies) -> AuthorizedFileReader:
    extra = (deps.worktree_path,) if deps.worktree_path else ()
    return AuthorizedFileReader(deps.workspace_path, extra_readable_roots=extra)


def _resolve_inspect_member(
    deps: InvestigationDependencies, arguments: Dict[str, Any],
) -> Tuple[str, List[ContextItem]]:
    path = arguments.get("path")
    if not isinstance(path, str) or not path:
        raise InvestigationVerbArgumentError("inspect_member requires a non-empty 'path' string.")
    member_id = arguments.get("member_id")
    if member_id is not None and (not isinstance(member_id, str) or not member_id):
        raise InvestigationVerbArgumentError("inspect_member's 'member_id', if given, must be a non-empty string.")

    root = _current_root(deps)
    try:
        _reader(deps).raise_if_denied(os.path.join(root, path))
    except PolicyDeniedError as denial:
        return f"ERROR: {denial}", []

    resolver = CurrentSourceResolver(
        deps.workspace_path, deps.worktree_path, content_cache=deps.source_cache.content_cache,
    )
    resolved = resolver.resolve(path)
    if not resolved.exists:
        return f"ERROR: '{path}' does not exist in the current worktree ({resolved.status}).", []

    boundaries = member_boundaries_for(path, resolved.content)
    if boundaries is None:
        return (
            f"ERROR: no structural member extraction is available for '{path}' "
            "(unsupported language for member-level inspection)."
        ), []

    if not member_id:
        if not boundaries:
            return f"'{path}' has no top-level members detected.", []
        shown = boundaries[:_MAX_MEMBER_LISTING_ENTRIES]
        listing = "\n".join(f"{b.member_id}: lines {b.start_line}-{b.end_line}" for b in shown)
        item = make_context_item(
            path=path, content=listing,
            reason="developer_investigation:inspect_member:listing",
            source_type="named_in_request", trust_level="repository",
            tier="signatures", is_exact=True, revision=resolved.revision,
        )
        return f"Members of '{path}':\n{listing}", [item]

    matches = boundaries_matching_member_id(boundaries, member_id)
    if not matches:
        known = ", ".join(b.member_id for b in boundaries[:_MAX_MEMBER_LISTING_ENTRIES])
        return f"ERROR: no member '{member_id}' found in '{path}'. Known members: {known}", []
    # More than one boundary for the same member_id is a real, non-
    # fabricated ambiguity (an overloaded Java method) - conservatively
    # return every real overload body, mirroring boundaries_matching_
    # member_id's own documented consumption pattern, never guessing which
    # overload was meant.
    items: List[ContextItem] = []
    rendered_blocks: List[str] = []
    for boundary in matches:
        body = extract_member_body(resolved.content, boundary.start_line, boundary.end_line)
        items.append(make_context_item(
            path=path, content=body,
            reason=f"developer_investigation:inspect_member:{member_id}",
            source_type="named_in_request", trust_level="repository",
            member_id=member_id, start_line=boundary.start_line, end_line=boundary.end_line,
            tier="member_exact", is_exact=True, revision=resolved.revision,
        ))
        rendered_blocks.append(
            f"=== {path}::{member_id} (lines {boundary.start_line}-{boundary.end_line}) ===\n{body}"
        )
    return "\n\n".join(rendered_blocks), items


def _render_location_matches(
    deps: InvestigationDependencies, symbol: str, locations: List[Dict[str, Any]], *, verb: str,
) -> Tuple[str, List[ContextItem]]:
    """Shared by find_symbol/find_callers - both produce a bounded list of
    {filepath, name, type, start_line, end_line} matches from an already-
    indexed, already-trusted source (DependencyGraph); the only thing this
    does beyond formatting is apply the SAME read-authority gate every
    path-backed investigation result passes through, per-match (a denied
    match is silently omitted, not a whole-call failure - never a fuzzy
    partial escape, since a genuinely denied path never contributes any
    content, only a count)."""
    if not locations:
        kind = "callers" if verb == "find_callers" else "symbol locations"
        return f"No {kind} found for '{symbol}'.", []
    reader = _reader(deps)
    root = _current_root(deps)
    allowed: List[Dict[str, Any]] = []
    omitted = 0
    for loc in locations:
        filepath = loc.get("filepath")
        if not filepath:
            continue
        try:
            reader.raise_if_denied(os.path.join(root, filepath))
        except PolicyDeniedError:
            omitted += 1
            continue
        allowed.append(loc)
    if not allowed:
        return f"ERROR: every match for '{symbol}' is outside investigation read authority.", []

    items: List[ContextItem] = []
    lines: List[str] = []
    for loc in allowed:
        start, end = loc.get("start_line"), loc.get("end_line")
        span = f" (lines {start}-{end})" if start and end else ""
        label = loc.get("name") or symbol
        kind = loc.get("type") or "symbol"
        lines.append(f"{loc['filepath']}: {label} [{kind}]{span}")
        items.append(make_context_item(
            path=loc["filepath"], content=lines[-1],
            reason=f"developer_investigation:{verb}:{symbol}",
            source_type="graph_dependency", trust_level="repository",
            tier="signatures", is_exact=True, revision="",
        ))
    if omitted:
        lines.append(f"({omitted} additional match(es) omitted by read policy)")
    return "\n".join(lines), items


def _resolve_find_symbol(
    deps: InvestigationDependencies, arguments: Dict[str, Any],
) -> Tuple[str, List[ContextItem]]:
    symbol = arguments.get("symbol")
    if not isinstance(symbol, str) or not symbol:
        raise InvestigationVerbArgumentError("find_symbol requires a non-empty 'symbol' string.")
    if not os.path.exists(deps.dependency_graph_db_path):
        return "No dependency graph index is available for this workspace.", []
    from kriya.analyzer.graph import DependencyGraph
    graph = DependencyGraph(deps.dependency_graph_db_path)
    try:
        locations = graph.find_symbol_locations(symbol, limit=_MAX_SYMBOL_RESULTS)
    finally:
        graph.close()
    return _render_location_matches(deps, symbol, locations, verb="find_symbol")


def _resolve_find_callers(
    deps: InvestigationDependencies, arguments: Dict[str, Any],
) -> Tuple[str, List[ContextItem]]:
    symbol = arguments.get("symbol")
    if not isinstance(symbol, str) or not symbol:
        raise InvestigationVerbArgumentError("find_callers requires a non-empty 'symbol' string.")
    if not os.path.exists(deps.dependency_graph_db_path):
        return "No dependency graph index is available for this workspace.", []
    from kriya.analyzer.graph import DependencyGraph
    graph = DependencyGraph(deps.dependency_graph_db_path)
    try:
        callers = graph.get_callers(symbol)
    finally:
        graph.close()
    # get_callers()'s own "calls" relations are recorded source=<calling
    # FILE>, target=<called symbol name> (both _parse_python and _parse_java
    # record the call SITE'S FILE as source, never a specific enclosing
    # function/method identity - see graph.py's own _parse_python docstring
    # comments at its ast.Call handling). Its LEFT JOIN onto symbols (s.name
    # == r.source) is therefore always a miss for a real call relation
    # (r.source is a filepath, never a symbol name) - c["filepath"] is
    # structurally always None; c["source"] is the one real, populated
    # field, and it already IS the calling file. This is an honest,
    # file-level ("this file calls X somewhere"), not function-level,
    # precision - the real precision the existing schema can offer.
    locations = [
        {"filepath": c.get("source"), "name": symbol, "type": "caller"}
        for c in callers if c.get("source")
    ][:_MAX_SYMBOL_RESULTS]
    return _render_location_matches(deps, symbol, locations, verb="find_callers")


def _promote_search_hit_to_members(
    deps: InvestigationDependencies, filepath: str, hit_text: str, query: str,
) -> List[ContextItem]:
    """SEARCH_TO_MEMBER_PROMOTION (2026-09-19, VAL-001 G1 DEV-INV rerun):
    reuses context_source.py's own SOURCE 1 resolver
    (resolve_member_hints_from_chunk_header) - already relied on by
    workflow.py's initial Graph-RAG retrieval for exactly this purpose - so
    a search_code hit whose own `text` carries the analyzer's real,
    controlled "Method: X"/"Class: X" chunk header (chunk_file_with_
    metadata_headers - never model-generated) can be promoted from an
    unquotable truncated skeleton fragment into real, exact, fully-quotable
    current source. `hit_text` is the vector store's own indexed chunk text
    (used ONLY to read its controlled header line - the real body always
    comes from CurrentSourceResolver below, never from the store's own,
    possibly-stale/truncated snippet - "CurrentSourceResolver remains
    source authority").

    Deliberately conservative: promotes only when resolve_member_hints_
    from_chunk_header grounds to exactly ONE distinct member_id against the
    file's CURRENT real structure - zero (no header, unsupported language)
    or ambiguous grounding both return [] here, falling through to the
    caller's existing skeleton rendering unchanged ("ambiguous hit -> no
    authority increase", never a guess). A grounded member_id sharing more
    than one real boundary (an overloaded Java method) returns every real
    overload body, mirroring _resolve_inspect_member's own established,
    non-fabricated-ambiguity handling - never an arbitrary pick."""
    resolver = CurrentSourceResolver(
        deps.workspace_path, deps.worktree_path, content_cache=deps.source_cache.content_cache,
    )
    resolved = resolver.resolve(filepath)
    if not resolved.exists:
        return []
    candidates = resolve_member_hints_from_chunk_header(filepath, resolved.content, hit_text)
    if len(candidates) != 1:
        return []
    boundaries = member_boundaries_for(filepath, resolved.content)
    if not boundaries:
        return []
    matches = boundaries_matching_member_id(boundaries, candidates[0].member_id)
    if not matches:
        return []
    return [
        make_context_item(
            path=filepath, content=extract_member_body(resolved.content, boundary.start_line, boundary.end_line),
            reason=f"developer_investigation:search_code:{query}",
            source_type="semantic_hit", trust_level="repository",
            member_id=candidates[0].member_id, start_line=boundary.start_line, end_line=boundary.end_line,
            tier="member_exact", is_exact=True, revision=resolved.revision,
        )
        for boundary in matches
    ]


async def _resolve_search_code(
    deps: InvestigationDependencies, arguments: Dict[str, Any],
) -> Tuple[str, List[ContextItem]]:
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise InvestigationVerbArgumentError("search_code requires a non-empty 'query' string.")
    hits = await deps.search_code(query)
    if not hits:
        return f"No indexed matches found for '{query}'.", []

    reader = _reader(deps)
    root = _current_root(deps)
    items: List[ContextItem] = []
    lines: List[str] = []
    omitted = 0
    for hit in hits[:_MAX_SEARCH_RESULTS]:
        filepath = hit.get("filepath")
        text = hit.get("text") or ""
        if not filepath or not text:
            continue
        try:
            reader.raise_if_denied(os.path.join(root, filepath))
        except PolicyDeniedError:
            omitted += 1
            continue

        promoted = _promote_search_hit_to_members(deps, filepath, text, query)
        if promoted:
            for item in promoted:
                items.append(item)
                lines.append(
                    f"=== {filepath}::{item.member_id} (lines {item.start_line}-{item.end_line}, "
                    f"score={hit.get('score', 0.0):.3f}) ===\n{item.content}"
                )
            continue

        bounded_text = text[:_MAX_SEARCH_RESULT_CHARS]
        # Explicit fragment marker (2026-09-19): a truncated skeleton hit
        # with no marker looks, to the model, indistinguishable from a
        # short but COMPLETE snippet - directly upstream of a SEARCH block
        # quoting text past what was actually shown ("search text is
        # localization evidence, never source authority").
        truncated_notice = (
            " [TRUNCATED - not the complete member; call inspect_member on this path for the full body]"
            if len(text) > _MAX_SEARCH_RESULT_CHARS else ""
        )
        items.append(make_context_item(
            path=filepath, content=bounded_text,
            reason=f"developer_investigation:search_code:{query}",
            source_type="semantic_hit", trust_level="repository",
            tier="skeleton", is_exact=False, omitted_regions=True, revision="",
        ))
        lines.append(f"=== {filepath} (score={hit.get('score', 0.0):.3f}){truncated_notice} ===\n{bounded_text}")
    if not items:
        return f"ERROR: every match for '{query}' is outside investigation read authority.", []
    if omitted:
        lines.append(f"({omitted} additional match(es) omitted by read policy)")
    return "\n\n".join(lines), items


async def dispatch_investigation_request(
    deps: InvestigationDependencies, request: InvestigationRequest,
) -> Tuple[str, List[ContextItem]]:
    """The one closed dispatch point - an unrecognized verb fails closed
    here too (defense in depth beyond protocol normalization already
    rejecting it), and a well-formed-but-unusable argument set (Investigation
    VerbArgumentError) becomes an ordinary fed-back ERROR string, never a
    crash of the loop."""
    try:
        if request.verb == "inspect_member":
            return _resolve_inspect_member(deps, request.arguments)
        if request.verb == "find_symbol":
            return _resolve_find_symbol(deps, request.arguments)
        if request.verb == "find_callers":
            return _resolve_find_callers(deps, request.arguments)
        if request.verb == "search_code":
            return await _resolve_search_code(deps, request.arguments)
        return f"ERROR: unknown investigation verb '{request.verb}'.", []
    except InvestigationVerbArgumentError as exc:
        return f"ERROR: {exc}", []


# --- 5. No-progress / loop control (section 9) -------------------------------

def request_fingerprint(request: InvestigationRequest) -> str:
    """Deterministic identity for (verb, arguments) - a different verb or a
    different argument value is always a different fingerprint (progress by
    construction); this never inspects revision/content itself, only the
    REQUEST shape, so a repeated request after a real revision change still
    fingerprints identically and relies on _NoProgressTracker's own
    content-hash comparison (not this function) to tell the two apart."""
    return f"{request.verb}:{json.dumps(request.arguments, sort_keys=True, default=str)}"


_MISS = "__NO_EVIDENCE__"


class _NoProgressTracker:
    """One dict, fingerprint -> the content_hash the LAST resolution for
    that exact fingerprint produced (or the _MISS sentinel for "resolved to
    no evidence/an error"). Generalizes distance-independent oscillation
    (A->B->A->...->A) automatically: however far apart, a fingerprint
    repeat with an unchanged outcome is always caught; a repeat whose
    outcome differs (revision changed, stronger evidence resolved this
    time) is never mistaken for a repeat, because the two are compared on
    the RESULT (content_hash), not merely on request identity."""

    def __init__(self) -> None:
        self._last_outcome: Dict[str, str] = {}

    def record_and_check(self, fingerprint: str, items: List[ContextItem]) -> bool:
        # Combined over EVERY returned item, not just the first - a repeat
        # request whose result SET differs (e.g. an extra caller now found)
        # must never be misread as "unchanged" just because the first
        # item's hash happens to match.
        marker = "|".join(sorted(item.content_hash for item in items)) if items else _MISS
        prior = self._last_outcome.get(fingerprint)
        self._last_outcome[fingerprint] = marker
        return prior is not None and prior == marker


# --- 6. Main loop (sections 1, 11, 12) ---------------------------------------

@dataclass
class InvestigationLoopResult:
    evidence: List[ContextItem] = field(default_factory=list)
    turns_used: int = 0
    terminal_reason: str = "PROPOSE"
    events: List[RunEvent] = field(default_factory=list)


def _native_system_prompt() -> str:
    return (
        "You are the Developer agent, about to implement a coding task in an existing "
        "repository. Before proposing any code, you may call ONE of the available read-only "
        "investigation tools per turn if you need to see the repository's real, current "
        "structure - never guess a method's real body, a caller, or another class's real shape "
        "from memory when a tool can show you the real thing.\n"
        "Rules:\n"
        "- These tools are READ-ONLY. They can never write or change anything.\n"
        "- Call at most one tool per turn.\n"
        "- When you have enough evidence, stop calling tools and reply with a short plain-text "
        "confirmation that you are ready to propose the implementation."
    )


def _marker_system_prompt() -> str:
    return _native_system_prompt() + "\n\n" + marker_protocol_instructions()


def _to_tool_call_wire_dicts(tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Reconstructs the OpenAI wire-format tool_calls list for the assistant
    message appended to the running conversation - complete_with_tools()
    already decoded arguments into a dict for our convenience; the next
    turn's message history needs the original wire shape. Mirrors kriya/
    workflow/self_correction.py's own _to_openai_tool_call_dicts exactly (a
    small, stable shape not worth importing a private cross-module
    helper for)."""
    return [
        {
            "id": tc["id"], "type": "function",
            "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])},
        }
        for tc in tool_calls
    ]


def _initial_user_message(
    task_description: str, design_context: str, existing_code_context: str,
    known_target_files: Optional[List[str]],
) -> str:
    targets_line = (
        f"Files this attempt is expected to write: {', '.join(known_target_files)}\n\n"
        if known_target_files else ""
    )
    return (
        f"=== Task ===\n{task_description}\n\n"
        f"=== Design guidance ===\n{design_context}\n\n"
        f"{targets_line}"
        f"=== Evidence already gathered ===\n{existing_code_context or '(none yet)'}\n\n"
        "Investigate if you need more evidence, or confirm you are ready to propose."
    )


async def run_investigation_loop(
    *,
    llm: LLMClient,
    capabilities: ModelCapabilities,
    deps: InvestigationDependencies,
    task_description: str,
    design_context: str,
    existing_code_context: str,
    max_turns: int,
    known_target_files: Optional[List[str]] = None,
    model_override: Optional[str] = None,
    base_url_override: Optional[str] = None,
    api_key_override: Optional[str] = None,
    extra_body_override: Optional[Dict[str, Any]] = None,
    attempt_number: int = 0,
) -> InvestigationLoopResult:
    """The bounded turn-loop (section 1). Protocol selection reads
    capabilities.native_tool_calls directly (resolved by the caller via
    kriya/core/model_capabilities.py::resolve_model_capability_profile,
    itself already model-agnostic and fail-closed to the marker fallback for
    any unverified model - section 3/12's "no model-specific enablement" is
    satisfied by construction, never a model-name branch here).

    Never raises on a model/tool-call error it can degrade from - any
    unexpected exception from the LLM call itself is treated as an early,
    safe PROPOSE (mirroring self_correction.py's own "optional micro-loop,
    never worse than not having run" posture), never propagated up to fail
    the whole Developer attempt over an optional evidence-gathering step."""
    if max_turns <= 0:
        return InvestigationLoopResult(turns_used=0, terminal_reason="BUDGET_EXHAUSTED")

    tracker = _NoProgressTracker()
    evidence: List[ContextItem] = []
    events: List[RunEvent] = []
    turns_used = 0
    terminal_reason = "PROPOSE"

    use_native = bool(capabilities.native_tool_calls)
    messages: List[Dict[str, Any]] = []
    transcript = ""
    if use_native:
        messages = [
            {"role": "system", "content": _native_system_prompt()},
            {"role": "user", "content": _initial_user_message(
                task_description, design_context, existing_code_context, known_target_files,
            )},
        ]
    else:
        transcript = _initial_user_message(
            task_description, design_context, existing_code_context, known_target_files,
        )

    for turn in range(max_turns):
        turns_used = turn + 1
        try:
            if use_native:
                result = await llm.complete_with_tools(
                    messages, list(INVESTIGATION_TOOLS),
                    model_override=model_override, base_url_override=base_url_override,
                    api_key_override=api_key_override, extra_body_override=extra_body_override,
                )
                tool_calls = result.get("tool_calls") or []
                if not tool_calls:
                    terminal_reason = "PROPOSE"
                    break
                messages.append({
                    "role": "assistant", "content": result.get("content"),
                    "tool_calls": _to_tool_call_wire_dicts(tool_calls),
                })
                call = tool_calls[0]
                turn_input = normalize_native_tool_call(call)
            else:
                raw = await llm.complete(
                    _marker_system_prompt(), transcript,
                    model_override=model_override, base_url_override=base_url_override,
                    api_key_override=api_key_override, extra_body_override=extra_body_override,
                )
                turn_input = parse_marker_response(raw)
        except Exception as exc:
            logger.warning(
                "DEV-INV-001 investigation loop stopped early on an LLM-call error "
                "(optional pre-pass, degrading to PROPOSE): %s", exc,
            )
            terminal_reason = "PROPOSE"
            break

        if isinstance(turn_input, ProposeSignal):
            terminal_reason = "PROPOSE"
            break

        if isinstance(turn_input, MalformedInvestigationRequest):
            events.append(RunEvent(
                kind="investigation.malformed_request", attempt=attempt_number,
                source="investigation.run_investigation_loop", authority=EventAuthority.ADVISORY,
                message=f"Malformed investigation request on turn {turn + 1}: {turn_input.detail}",
                details={"turn": turn + 1, "detail": turn_input.detail},
            ))
            feedback = (
                f"ERROR: could not parse your investigation request ({turn_input.detail}). "
                f"Valid verbs: {', '.join(INVESTIGATION_VERBS)}. "
                + (marker_protocol_instructions() if not use_native else "")
            )
            if use_native:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_calls[0]["id"],
                    "content": feedback,
                })
                for extra_call in tool_calls[1:]:
                    messages.append({
                        "role": "tool", "tool_call_id": extra_call["id"],
                        "content": "IGNORED: only one investigation call is processed per turn.",
                    })
            else:
                transcript += f"\n\n=== Your reply ===\n{raw}\n\n=== Result ===\n{feedback}"
            continue

        # Well-formed InvestigationRequest.
        fingerprint = request_fingerprint(turn_input)
        feedback_text, items = await dispatch_investigation_request(deps, turn_input)
        no_progress = tracker.record_and_check(fingerprint, items)
        events.append(RunEvent(
            kind="investigation.turn", attempt=attempt_number,
            source="investigation.run_investigation_loop", authority=EventAuthority.ADVISORY,
            message=f"Investigation turn {turn + 1}: {turn_input.verb}.",
            details={
                "turn": turn + 1, "verb": turn_input.verb,
                "evidence_count": len(items),
                "evidence_changed": not no_progress,
                "paths": sorted({item.path for item in items}),
            },
        ))
        if use_native:
            messages.append({
                "role": "tool", "tool_call_id": tool_calls[0]["id"], "content": feedback_text,
            })
            for extra_call in tool_calls[1:]:
                messages.append({
                    "role": "tool", "tool_call_id": extra_call["id"],
                    "content": "IGNORED: only one investigation call is processed per turn.",
                })
        else:
            transcript += f"\n\n=== Your reply ===\n{raw}\n\n=== Result ===\n{feedback_text}"

        if no_progress:
            events.append(RunEvent(
                kind="investigation.no_progress", attempt=attempt_number,
                source="investigation.run_investigation_loop", authority=EventAuthority.ADVISORY,
                message=f"No-progress detected on turn {turn + 1} - repeated request produced unchanged evidence.",
                details={"turn": turn + 1, "verb": turn_input.verb, "fingerprint": fingerprint},
            ))
            terminal_reason = "NO_PROGRESS"
            evidence.extend(items)
            break

        evidence.extend(items)
    else:
        terminal_reason = "BUDGET_EXHAUSTED"

    events.append(RunEvent(
        kind="investigation.completed", attempt=attempt_number,
        source="investigation.run_investigation_loop", authority=EventAuthority.ADVISORY,
        message=f"Investigation loop finished: {terminal_reason} after {turns_used} turn(s).",
        details={
            "terminal_reason": terminal_reason, "turns_used": turns_used,
            "evidence_count": len(evidence), "protocol": "native" if use_native else "marker",
        },
    ))
    return InvestigationLoopResult(
        evidence=evidence, turns_used=turns_used, terminal_reason=terminal_reason, events=events,
    )


def render_investigation_evidence(evidence: List[ContextItem]) -> str:
    """Renders gathered evidence into the same kind of plain text block
    build_code_context() already produces for retrieval-sourced context -
    appended to existing_code_context, never a new prompt-assembly
    mechanism. Empty input renders to "" (a no-op append)."""
    if not evidence:
        return ""
    blocks = [
        f"=== Investigation evidence: {item.path}"
        f"{'::' + item.member_id if item.member_id else ''} ===\n{item.content}"
        for item in evidence
    ]
    return "\n\n=== Begin Developer-Requested Investigation Evidence ===\n" + "\n\n".join(blocks) + \
        "\n=== End Developer-Requested Investigation Evidence ===\n"
