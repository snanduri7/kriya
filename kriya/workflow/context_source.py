"""CTX-001 P1 WP4 + WP5: current-source resolution and member-level context
selection.

WP4 - CurrentSourceResolver: implements the "current-source invariant"
docs/assurance/CTX_001_P1_ARCHITECTURE.md section 7 requires: once a run's
git worktree exists, it is authoritative for source content for the rest of
that run, without exception - never a silent fallback to a possibly-stale
workspace copy. This directly fixes CTX-001-P0's C3/F9 finding (attempt.py's
retry-time build_code_context() call sites reading ctx.workspace_path
instead of ctx.worktree_path).

WP5 - member-level boundaries: Java reuses kriya/analyzer/java_members.py's
existing extract_java_members() as-is. Python gets a new, small ast-based
extractor (python_member_ranges) - context_budget.py's own
_python_declaration_ranges() returns only a declaration's HEADER span,
sufficient for the skeletonizer's own header-preservation need but not for
"retain this member's full body", so a distinct function is required (see
architecture doc section 6). Any other language has no member extractor -
member_boundaries_for() returns None, an explicit, honest "unsupported"
signal - member precision is never fabricated for a language with no real
structural extraction (WP5's own explicit requirement).
"""
import ast
import keyword
import os
import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from kriya.analyzer.java_members import extract_java_members
from kriya.workflow.edit_safety import content_revision

_SUPPORTED_MEMBER_EXTENSIONS = {".py", ".java"}


@dataclass(frozen=True)
class ResolvedSource:
    """One path's current content, resolved against the correct root for
    this point in the run - see CurrentSourceResolver's own docstring for
    the resolution rule. `status` is one of:
      - "current": content is real, current, and available at the
        resolved root.
      - "deleted": the path existed at workspace_path but no longer exists
        at the current (worktree) root - a real deletion within this run,
        never silently re-shown from the stale workspace copy.
      - "unavailable": the path does not exist at EITHER root (never
        existed, or is gone everywhere) - or exists but could not be read
        (a real OSError, e.g. a permissions problem).
    """

    path: str
    exists: bool
    content: Optional[str]
    revision: str
    root_used: str
    status: str
    # True only when a caller-supplied known_revisions hint for this path
    # did not match the actually-read content's real, freshly-computed
    # revision - the resolver never trusts a stale hint silently; it always
    # returns the REAL revision (see resolve()'s own docstring), and flags
    # this so a caller can record a "stale_revision_rejected" omission if it
    # cares to.
    stale_hint: bool = False


class CurrentSourceResolver:
    """WP4. `workspace_path` is the pre-mutation workspace; `worktree_path`
    is the run's own isolated worktree (kriya/workflow/worktree.py::
    create_git_worktree(), called once, before the retry loop, after
    attempt-1's own Graph RAG retrieval - see that function's own docstring
    and CTX_001_P1_ARCHITECTURE.md section 7). `worktree_path` is optional
    ONLY to let this resolver be constructed and tested independently of a
    real run_attempt() call; every real production caller in this codebase
    always has a real, already-created worktree_path by the time any
    context is resolved (attempt-1's own Graph RAG retrieval, the one
    caller that legitimately runs BEFORE worktree creation, does not use
    this resolver at all - see workflow.py's own retrieval stage, strictly
    before create_git_worktree()).

    known_revisions (path -> already-computed SHA-256) reuses, rather than
    duplicates, state.validated_file_revisions' existing role (see
    retry_package.py's own project_implementation_source() usage) - a hint
    only, never blindly trusted: resolve() always computes the REAL
    revision from the content it actually just read, and flags stale_hint
    when the hint doesn't match (see ResolvedSource's own docstring)."""

    def __init__(
        self, workspace_path: str, worktree_path: Optional[str] = None,
        known_revisions: Optional[Dict[str, str]] = None,
        content_cache: Optional[Dict[str, Tuple[float, str, str]]] = None,
    ) -> None:
        self.workspace_path = workspace_path
        self.worktree_path = worktree_path
        self.known_revisions = known_revisions or {}
        # CTX-001 P1 WP9: an OPTIONAL, caller-owned mtime-fast-pathed read
        # cache - "root\x00relpath" -> (mtime, revision, content). None (the
        # default) makes this resolver instance keep its own private,
        # empty dict, matching every existing caller's exact per-call-
        # fresh-read behavior unchanged (this resolver has never cached
        # anything across separate resolve() calls before this). A caller
        # that wants cross-call reuse within one attempt's own lifetime
        # (attempt.py) passes the SAME dict into every CurrentSourceResolver
        # it constructs during that attempt (AttemptContext.source_cache's
        # own content_cache) - this class never persists it anywhere else
        # or shares it across attempts/runs itself; that scoping is entirely
        # the caller's responsibility, matching this class's own established
        # "makes no root-selection decision of its own" discipline.
        self._content_cache: Dict[str, Tuple[float, str, str]] = (
            content_cache if content_cache is not None else {}
        )

    def _current_root(self) -> str:
        # Once a worktree exists, it is authoritative for the ENTIRE run -
        # this is the one, single decision point every context consumer in
        # this codebase should route through, rather than each call site
        # independently re-deriving "which root do I read from" (the exact
        # duplication that let two of attempt.py's own build_code_context()
        # call sites drift to the wrong answer - P0's C3/F9 finding).
        return self.worktree_path if self.worktree_path else self.workspace_path

    def resolve(self, relpath: str) -> ResolvedSource:
        root = self._current_root()
        full = os.path.join(root, relpath)
        if not os.path.isfile(full):
            # Distinguish "used to exist, now deleted" from "never existed
            # anywhere" - only meaningful when the current root differs
            # from workspace_path (otherwise there is no second root to
            # compare against).
            existed_in_workspace = (
                root != self.workspace_path
                and os.path.isfile(os.path.join(self.workspace_path, relpath))
            )
            status = "deleted" if existed_in_workspace else "unavailable"
            return ResolvedSource(
                path=relpath, exists=False, content=None, revision="",
                root_used=root, status=status,
            )
        read = _cached_read(self._content_cache, root, relpath)
        if read is None:
            return ResolvedSource(
                path=relpath, exists=False, content=None, revision="",
                root_used=root, status="unavailable",
            )
        content, real_revision = read
        hint = self.known_revisions.get(relpath)
        stale = hint is not None and hint != real_revision
        return ResolvedSource(
            path=relpath, exists=True, content=content, revision=real_revision,
            root_used=root, status="current", stale_hint=stale,
        )


def _cached_read(
    content_cache: Dict[str, Tuple[float, str, str]], root: str, relpath: str,
) -> Optional[Tuple[str, str]]:
    """WP9's one real mtime-fast-pathed read primitive, shared by
    CurrentSourceResolver.resolve() above and build_code_context_package()'s
    own matched/related file reads (context_budget.py) - a single
    implementation, not two independently-maintained copies of the same
    mtime-then-hash pattern. os.stat() (cheap, no file body read) lets an
    unchanged file skip its own open()+read() entirely when this exact
    root+relpath was already read earlier in the SAME cache's lifetime AND
    its mtime hasn't moved since - the same two-tier pattern
    DependencyGraph's own index_repository() incremental logic already
    established (graph.py get_cached_mtime/get_cached_hash), reused here
    rather than invented fresh. mtime is NEVER trusted as proof of
    identical content on its own - only as a reason to skip a read; any
    mtime change always falls through to a real read and a freshly-
    computed, real revision, which is the only value ever used as an
    actual cache/derivation key anywhere in this module. Returns None only
    when the file cannot be read at all (a real OSError) - the caller
    already established the path exists as a file before calling this."""
    full = os.path.join(root, relpath)
    try:
        stat_result = os.stat(full)
    except OSError:
        return None
    cache_key = f"{root}\x00{relpath}"
    cached = content_cache.get(cache_key)
    if cached is not None and cached[0] == stat_result.st_mtime:
        return cached[2], cached[1]
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError:
        return None
    revision = content_revision(content)
    content_cache[cache_key] = (stat_result.st_mtime, revision, content)
    return content, revision


# --- CTX-001 P1 WP9: AttemptContext-lifetime source-derivation reuse -------
# Scope, per the accepted architecture's own §12 B3/B4 design: cache the
# EXPENSIVE, DETERMINISTIC per-unit derivations (skeleton/signatures/member-
# exact rendering, and their own token estimate) a retry loop would
# otherwise recompute from scratch on every iteration for a file that
# hasn't changed at all - never authority, retry decisions, failure
# classification, terminal state, or a fully-rendered prompt (whose
# surrounding evidence legitimately differs attempt to attempt). Explicitly
# NOT a persistent cache - lives exactly as long as the AttemptContext it's
# attached to (one dict, discarded when that object is), never written to
# disk, never shared across separate `generate` invocations.

@dataclass(frozen=True)
class SourceDerivationKey:
    """(source identity, revision, tier) - the exact key the architecture
    doc's own B3 design names, made concrete: source identity is
    (path, member_id) - member_id is None for a whole-file-level
    derivation - NEVER pathname alone, since one file can have several
    independently-cacheable derivations at once (its own whole-file tiers,
    plus any member-exact/sibling-signature units). `revision` is always a
    real, freshly-verified content_revision() (via CurrentSourceResolver,
    §7's own current-source invariant) - a lookup miss on ANY part of this
    key, including a revision change, is a correctness-safe cache miss by
    construction, never a stale hit."""

    path: str
    member_id: Optional[str]
    tier: str
    revision: str


class SourceDerivationCache:
    """One small, source-derived cache object with two internal maps
    (content and derivations) - deliberately not two independent top-level
    caches (the task's own "prefer one small cache with optional derived
    fields" instruction): `content_cache` is handed straight through to
    every CurrentSourceResolver this attempt constructs (see that class's
    own content_cache constructor parameter); `derivations` holds this
    module's own SourceDerivationKey -> (rendered_content, token_count)
    entries.

    Concurrency: every value here is produced by a fully SYNCHRONOUS
    compute function (skeletonize_code/extract_member_body - no `await`
    anywhere inside them) called from a single asyncio task with no
    concurrent asyncio.gather()/create_task() over the same AttemptContext
    anywhere in this codebase (confirmed by inspection of kriya/workflow/
    attempt.py's own retry loop and coordinated-repair generation, both
    strictly sequential) - a plain dict needs no additional lock for this
    exact usage pattern. Every stored value is treated as immutable -
    nothing here ever mutates an already-returned tuple in place.

    hits/misses are exposed as plain counters (not a full telemetry
    system) for CTX-001 P1's own required deterministic performance
    evidence - never anything beyond that."""

    def __init__(self) -> None:
        self.content_cache: Dict[str, Tuple[float, str, str]] = {}
        self._derivations: Dict[SourceDerivationKey, Tuple[str, int]] = {}
        self.derivation_hits = 0
        self.derivation_misses = 0
        self.content_reads = 0
        self.content_read_hits = 0

    def read(self, root: str, relpath: str) -> Optional[Tuple[str, str]]:
        """Convenience wrapper around this module's own _cached_read() -
        lets a non-CurrentSourceResolver caller (build_code_context_package,
        context_budget.py) share the SAME mtime-fast-pathed read cache/
        implementation, rather than a second, independently-maintained
        read path. Returns (content, revision) or None if unreadable."""
        self.content_reads += 1
        try:
            was_cached = os.stat(os.path.join(root, relpath)).st_mtime == (
                self.content_cache.get(f"{root}\x00{relpath}", (None,))[0]
            )
        except OSError:
            was_cached = False
        result = _cached_read(self.content_cache, root, relpath)
        if result is not None and was_cached:
            self.content_read_hits += 1
        return result

    def get_or_compute_derivation(
        self, path: str, member_id: Optional[str], tier: str, revision: str,
        compute_fn: "Callable[[], str]",
    ) -> Tuple[str, int]:
        """compute_fn is called at most once per distinct
        (path, member_id, tier, revision) - its return value's own token
        count is memoized alongside it (folds context_budget.py's own
        estimate_tokens() work into the SAME cache entry, rather than a
        second independent token cache the task explicitly warns against)."""
        key = SourceDerivationKey(path=path, member_id=member_id, tier=tier, revision=revision)
        cached = self._derivations.get(key)
        if cached is not None:
            self.derivation_hits += 1
            return cached
        self.derivation_misses += 1
        content = compute_fn()
        from kriya.workflow.context_budget import estimate_tokens
        result = (content, estimate_tokens(content))
        self._derivations[key] = result
        return result


@dataclass(frozen=True)
class MemberBoundary:
    member_id: str
    start_line: int  # 1-indexed, inclusive
    end_line: int    # 1-indexed, inclusive


class _PythonMemberVisitor(ast.NodeVisitor):
    """Conservative, structural-only nested-member walk: only descends into
    a def/class's own DIRECT body statements (ast.iter_child_nodes), never
    into a def/class nested inside a conditional/loop/try body - "nested
    declarations conservatively" per WP5's own requirement. A member
    genuinely defined inside an `if`/`for`/`try` block is not addressed at
    the member level (it stays inside its enclosing member's own body
    range), never fabricated as its own separately-addressable unit."""

    def __init__(self) -> None:
        self.ranges: Dict[str, Tuple[int, int]] = {}

    def _visit_scope(self, node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start = child.lineno
                decorators = getattr(child, "decorator_list", None) or []
                if decorators:
                    # Decorators have their own, earlier line numbers - the
                    # member's real declaration START must include them
                    # ("decorators without losing declaration start", WP5's
                    # own explicit requirement), not just the def/class
                    # keyword's own line.
                    start = min(start, min(d.lineno for d in decorators))
                end = getattr(child, "end_lineno", child.lineno)
                name = f"{prefix}.{child.name}" if prefix else child.name
                self.ranges[name] = (start, end)
                self._visit_scope(child, name)


def python_member_ranges(content: str) -> Dict[str, Tuple[int, int]]:
    """member_id -> (start_line, end_line), 1-indexed inclusive, real body
    extents via `ast` (end_lineno, Python 3.8+) - NOT context_budget.py's
    own _python_declaration_ranges(), which returns only the declaration's
    HEADER span (sufficient for the skeletonizer's own header-preservation
    need, not for retaining a member's full body - see this module's own
    docstring). member_id is a dotted path relative to the file:
    "StandardInvoiceCalculator.calculate_total" for a method,
    "StandardInvoiceCalculator" for the class itself, "top_level_func" for
    a module-level function. Supports class/sync function/async function,
    multiple members in one file, and conservative nesting (see
    _PythonMemberVisitor's own docstring). Returns {} for unparseable
    content - never fabricates a range for invalid syntax."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return {}
    visitor = _PythonMemberVisitor()
    visitor._visit_scope(tree, "")
    return visitor.ranges


def java_member_boundaries(content: str) -> List[MemberBoundary]:
    """Reuses extract_java_members() as-is (already precise line spans per
    constructor/method) - member_id is "EnclosingType.methodName" to match
    Python's own dotted convention above; an overloaded method (same name,
    different parameters) gets ITS OWN entry keyed by the same member_id as
    its sibling overload, since JavaMember's own `signature` field (not
    exposed here) is what actually disambiguates overloads - out of scope
    for this narrow boundary lookup, which addresses "this named member",
    not "this exact overload"."""
    members = extract_java_members(content)
    return [
        MemberBoundary(
            member_id=f"{m.enclosing_type}.{m.name}" if m.enclosing_type else m.name,
            start_line=m.start_line, end_line=m.end_line,
        )
        for m in members
    ]


def python_member_boundaries(content: str) -> List[MemberBoundary]:
    return [
        MemberBoundary(member_id=member_id, start_line=start, end_line=end)
        for member_id, (start, end) in python_member_ranges(content).items()
    ]


def member_boundaries_for(path: str, content: str) -> Optional[List[MemberBoundary]]:
    """None means "this language has no member extractor" - an explicit,
    honest unsupported signal (WP5's own "never fabricate member precision"
    requirement), never an empty list standing in for "no members found"
    (which IS possible for a genuinely supported language, e.g. a file with
    no top-level def/class)."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".py":
        return python_member_boundaries(content)
    if ext == ".java":
        return java_member_boundaries(content)
    return None


def extract_member_body(content: str, start_line: int, end_line: int) -> str:
    """1-indexed, inclusive line range -> the exact source text for that
    range - the member-exact body extraction primitive both WP6/WP7 use.
    Never a heuristic/byte-offset cut - start_line/end_line always come
    from a real structural extractor (java_member_boundaries/
    python_member_boundaries) above."""
    lines = content.splitlines()
    start = max(1, start_line)
    end = min(len(lines), end_line)
    if start > end:
        return ""
    return "\n".join(lines[start - 1:end])


# --- CTX-001 P1 C2 production integration: deterministic member-hint
# resolution from two already-grounded evidence sources (docs/assurance/
# CTX_001_P1_ARCHITECTURE.md section 25) ------------------------------------
#
# Both sources below produce CANDIDATES only - never trusted on their own.
# The one real validation gate is the same for both: does this candidate
# resolve to a REAL member_boundaries_for() range in the file's CURRENT
# (CurrentSourceResolver-sourced) content? A candidate that fails this is
# simply not returned - "no hint" is always a silent, safe, structural
# no-op for the caller (Package 2's existing REASON_UNSUPPORTED_STRUCTURAL_
# EXTRACTION/file-level fallback), never a special error path.
#
# No scoring/confidence framework: validation against real, current
# structure is a binary gate, not a probabilistic score (see the
# architecture addendum's own HINT_CONFIDENCE discussion) - `provenance` is
# recorded on MemberHintCandidate purely for observability/trace, never for
# ranking or arbitration between sources.

@dataclass(frozen=True)
class MemberHintCandidate:
    member_id: str
    provenance: str  # "vector_chunk_header" | "failure_location"


# Matches ONLY the exact, controlled header line format
# kriya/analyzer/analyzer.py::chunk_file_with_metadata_headers() itself
# writes ("Method: {name}" / "Class: {name}", one per line, within the
# first few lines of a chunk's own text) - never a general-purpose scan of
# arbitrary source prose. A source file that happens to contain a literal
# "Method: Foo" string mid-body does not false-positive here because real
# indexed chunk headers are always among the FIRST lines of chunk text
# (see _HEADER_SCAN_LINES below) and because chunk text passed to this
# parser is the RETRIEVAL RESULT'S OWN text, never raw file content.
_CONTROLLED_HEADER_LINE_RE = re.compile(r"^(?:Method|Class): (.+)$")
_HEADER_SCAN_LINES = 8


def parse_controlled_chunk_header_name(chunk_text: str) -> Optional[str]:
    """Parses 'Method: X'/'Class: X' from a vector chunk's own text
    (kriya/analyzer/analyzer.py::chunk_file_with_metadata_headers'
    controlled format only). Returns the bare (unqualified) member/class
    name, or None if no such line appears within the header region - a
    malformed/absent header produces no candidate, never a guess.

    A METHOD chunk's own header ALSO carries a "Class: {parent}" line
    ahead of its "Method: {name}" line (parent-context, not the chunk's
    own identity - see chunk_file_with_metadata_headers' method-chunk
    header format, both the Python and Java branches). "Method:" is
    therefore preferred deterministically whenever both are present in the
    same header; "Class:" is only used when no "Method:" line exists at
    all (a real class-declaration chunk)."""
    if not chunk_text:
        return None
    class_name: Optional[str] = None
    for line in chunk_text.splitlines()[:_HEADER_SCAN_LINES]:
        match = _CONTROLLED_HEADER_LINE_RE.match(line)
        if not match:
            continue
        kind = line.split(":", 1)[0]
        name = match.group(1).strip()
        if not name:
            continue
        if kind == "Method":
            return name
        if kind == "Class" and class_name is None:
            class_name = name
    return class_name


def member_ids_matching_name(boundaries: List[MemberBoundary], name: str) -> List[str]:
    """Every DISTINCT real member_id (java_member_boundaries/
    python_member_boundaries output) whose own simple name equals `name` -
    a member_id is either bare ("TopLevelFunc"/"ClassName") or dotted
    ("Class.method"); matching on the LAST dotted segment (or the whole
    string when there is no dot) lets a bare "Method: calculate_total"
    header resolve against a real
    "StandardInvoiceCalculator.calculate_total" boundary without needing
    the enclosing type name at all.

    Returns each DISTINCT member_id at most once, even when multiple
    overloads share it (a Java class can have several overloads with the
    same name - extract_java_members() gives each its own JavaMember/
    MemberBoundary entry, but they all produce the identical dotted
    member_id string) - the member_hints dict this feeds is a flat name
    collection, not a boundary list. boundaries_matching_member_id() below
    is the one that expands an ambiguous member_id back out to every real,
    non-fabricated overload boundary sharing it, at consumption time - the
    conservative choice (retain all real candidates, never guess which
    overload was meant), not an error condition."""
    seen: List[str] = []
    for boundary in boundaries:
        simple = boundary.member_id.rsplit(".", 1)[-1]
        if (simple == name or boundary.member_id == name) and boundary.member_id not in seen:
            seen.append(boundary.member_id)
    return seen


def boundaries_matching_member_id(boundaries: List[MemberBoundary], member_id: str) -> List[MemberBoundary]:
    """Every boundary sharing this EXACT member_id - normally exactly one,
    but more than one for an ambiguous (overloaded) Java method name that
    member_ids_matching_name() above could not uniquely resolve. Consumers
    (build_known_target_context) use this instead of a single next()
    lookup specifically so an ambiguous member_id expands to ALL of its
    real overload bodies rather than arbitrarily picking the first."""
    return [b for b in boundaries if b.member_id == member_id]


def resolve_member_hints_from_chunk_header(
    path: str, current_content: str, chunk_text: str,
) -> List[MemberHintCandidate]:
    """SOURCE 1 (initial/attempt-1 evidence): a Graph-RAG vector hit's own
    chunk `text` (already retrieved by query_hybrid(), previously discarded
    at the retrieval call site) -> zero or more VALIDATED member hints for
    `path`, checked against `current_content` (caller-supplied - always the
    CurrentSourceResolver-resolved, worktree-authoritative content, never
    re-derived here; this function does no I/O and makes no root-selection
    decision of its own, per the "reuse CurrentSourceResolver" invariant).

    Unsupported language (member_boundaries_for returns None) or a name
    with zero structural matches both produce []; multiple structural
    matches (Java overloads) all come back, per member_ids_matching_name's
    own conservative-retention docstring."""
    name = parse_controlled_chunk_header_name(chunk_text)
    if not name:
        return []
    boundaries = member_boundaries_for(path, current_content)
    if boundaries is None:
        return []
    return [
        MemberHintCandidate(member_id=member_id, provenance="vector_chunk_header")
        for member_id in member_ids_matching_name(boundaries, name)
    ]


def resolve_verified_grounding_member_id(
    path: str, current_content: str, candidate_name: str,
) -> Optional[str]:
    """Pre-plan grounding (2026-09-19, VAL-001 G1 follow-up): the same
    real-vs-hypothesis validation resolve_member_hints_from_chunk_header()
    already performs for a full chunk header, exposed here for a caller
    (workflow.py's own pre-plan retrieval pass) that has ALREADY parsed the
    candidate name itself (parse_controlled_chunk_header_name) and needs to
    keep that raw name available for its own "unconfirmed candidate"
    bucket when validation does not resolve to exactly one real member -
    unlike resolve_member_hints_from_chunk_header, which simply discards
    the raw name on a validation miss (none of its own existing callers
    ever needed it back). Reuses the exact same two primitives
    (member_boundaries_for/member_ids_matching_name) - no new resolution
    logic, only a different return shape for a different caller's need.

    Returns the single real member_id when `candidate_name` resolves to
    EXACTLY one current structural member; None for zero matches (stale
    since indexing, or genuinely never existed), an unsupported language,
    or more than one match (a genuinely ambiguous name, e.g. an overloaded
    method sharing it) - ambiguity is never resolved by guessing, it stays
    unverified for the caller to treat as a hypothesis instead."""
    boundaries = member_boundaries_for(path, current_content)
    if boundaries is None:
        return None
    matches = member_ids_matching_name(boundaries, candidate_name)
    return matches[0] if len(matches) == 1 else None


def resolve_member_hints_from_failure_location(
    path: str, current_content: str, line: int,
) -> List[MemberHintCandidate]:
    """SOURCE 2 (retry evidence): one Failure.file_locations entry's
    (filepath, line) -> zero or one VALIDATED member hint for `path`, via
    plain range containment against member_boundaries_for(path,
    current_content) - `current_content` is always caller-supplied
    (CurrentSourceResolver-resolved), matching resolve_member_hints_from_
    chunk_header's own contract exactly.

    A line can legitimately fall inside more than one containing boundary
    at once (a method's own range is nested inside its enclosing class's
    own range) - the MOST SPECIFIC (smallest span) containing boundary
    wins deterministically, since a compiler/test failure at a specific
    line is a far more precise signal about the METHOD than the whole
    class. A line outside every known boundary, an unsupported language,
    or an unreadable/stale line number all produce []."""
    boundaries = member_boundaries_for(path, current_content)
    if boundaries is None:
        return []
    containing = [b for b in boundaries if b.start_line <= line <= b.end_line]
    if not containing:
        return []
    most_specific = min(containing, key=lambda b: b.end_line - b.start_line)
    return [MemberHintCandidate(member_id=most_specific.member_id, provenance="failure_location")]


# --- CTX-001-P1-C3: failure-grounded member escalation (2026-09-18) --------
#
# SOURCE 3: a rejected anchored-edit candidate's own SEARCH text. Added
# because VAL-001 G1's post-remediation rerun (run d756a833) proved SOURCE 1
# (vector_chunk_header) and SOURCE 2 (failure_location) can both be
# structurally silent for an entire run at once: no Graph-RAG retrieval ran
# at all (the Architect already named the target file directly), and neither
# `anchored_edit` nor `operation_contract` failures ever populate a
# FileLocation.line (they are response-SHAPE failures, not "found at
# file:line" failures) - so member_hint_paths stayed [] across all 8
# attempts despite the model's own rejected SEARCH blocks already containing
# real, current-file vocabulary (`fn_node`, `generic_name`,
# `member_access_expression`, ...), already captured in
# Failure.attempted_edits, and never consumed by anything.
#
# The SEARCH text is model-generated, UNTRUSTED localization evidence - it
# may correctly OR incorrectly describe real code (G1's own attempts 3/4/5/7
# each hallucinated a plausible-but-non-matching reconstruction of the real
# target; attempts 4/7 additionally invented local-variable names that don't
# exist anywhere in the real file at all). It is therefore used ONLY to
# generate a member_id CANDIDATE, never as source content itself - the
# member's real body is always read fresh via member_boundaries_for()/
# extract_member_body() against current_content, exactly like SOURCE 1/2;
# this function never returns text, only a member_id, and a caller that
# eventually builds a member_exact ContextItem does so from the CURRENT
# worktree's own real content, never from anything in this module's input.
#
# Deliberately NOT a scoring/ranking mechanism (see the two rules' own
# docstrings below) - a "highest similarity" pick would let a sufficiently
# plausible hallucination steer promotion toward the wrong real member,
# which would make member_exact's own "is_exact" meaning true of the SOURCE
# while the SELECTION of that source was probabilistic - two different
# guarantees that must not be conflated. Both rules here are pure binary
# membership checks against real, current structure; anything that cannot
# be uniquely grounded returns [] (no hint), the same safe no-op every other
# evidence source in this module already uses.

_MEMBER_HINT_STOPLIST = frozenset(keyword.kwlist) | frozenset(getattr(keyword, "softkwlist", ())) | {
    "self", "cls", "node", "nodes", "text", "name", "names", "value", "values",
    "content", "contents", "source", "sources", "type", "types", "result", "results",
    "data", "item", "items", "key", "keys", "path", "paths", "line", "lines",
    "col", "cols", "child", "children", "parent", "root", "true", "false", "none",
    "str", "int", "list", "dict", "set", "tuple", "len", "range", "print",
}
_MEMBER_HINT_MIN_TOKEN_LENGTH = 4
_MEMBER_HINT_MIN_DISTINCTIVE_TOKENS = 2

_IDENTIFIER_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _extract_identifier_tokens(text: str) -> List[str]:
    """Every distinct identifier-shaped token in `text`, first-seen order -
    a plain lexical scan, never a real parse (the SEARCH text is often not
    valid syntax on its own - a fragment, possibly with elided/placeholder
    content, per apply_anchored_edits' own exact-match requirement being
    what actually validates it, not this function)."""
    seen: List[str] = []
    seen_set = set()
    for match in _IDENTIFIER_TOKEN_RE.finditer(text or ""):
        token = match.group(0)
        if token not in seen_set:
            seen_set.add(token)
            seen.append(token)
    return seen


def _distinctive_search_tokens(text: str) -> List[str]:
    """Identifier tokens minus Python keywords and a small curated stoplist
    of pervasive, structurally-meaningless names (`self`, `node`, `text`, a
    handful of builtins, ...) and anything shorter than
    _MEMBER_HINT_MIN_TOKEN_LENGTH. Not a claim that a surviving token is
    globally rare in the file - only that it is not one of the small set of
    words common enough to appear in nearly every member regardless of
    subject matter. Joint containment of SEVERAL such tokens (see Rule B
    below) is what actually does the discriminating work, not any single
    token's own rarity."""
    return [
        token for token in _extract_identifier_tokens(text)
        if len(token) >= _MEMBER_HINT_MIN_TOKEN_LENGTH
        and token.lower() not in _MEMBER_HINT_STOPLIST
    ]


def _collapse_nested_containing(containing: List[MemberBoundary]) -> Optional[MemberBoundary]:
    """Python/Java member boundaries NEST - an outer function's own line
    range always includes every member declared inside it (see
    python_member_ranges' own dotted-member_id docstring) - so more than one
    boundary containing the exact same evidence is the ORDINARY, expected
    case for a match inside a nested function, never automatic ambiguity on
    its own. Returns the smallest (most specific) boundary only when every
    boundary in `containing` forms a single unbroken ancestor chain around
    it (each larger boundary's range fully encloses the smallest one's own
    range) - the deepest, narrowest real member that still legitimately
    contains all the evidence. Two boundaries that are NOT nested within
    each other (real siblings, e.g. two unrelated methods that each happen
    to reference the same handful of distinctive tokens) is genuine
    ambiguity - returns None, never an arbitrary pick between them."""
    if not containing:
        return None
    if len(containing) == 1:
        return containing[0]
    ordered = sorted(containing, key=lambda b: b.end_line - b.start_line)
    smallest = ordered[0]
    for other in ordered[1:]:
        if not (other.start_line <= smallest.start_line and smallest.end_line <= other.end_line):
            return None
    return smallest


@dataclass(frozen=True)
class SearchEvidenceGroundingResult:
    """CTX-001-P1-C3 observability (2026-09-18, VAL-001 G1-R2 post-mortem):
    the same candidates resolve_member_hints_from_search_evidence() already
    returns, PLUS a deterministic `outcome` code explaining why - never a
    new decision, purely a label on a decision this module already made.
    Callers that only need the candidates keep using
    resolve_member_hints_from_search_evidence() unchanged; a caller that
    needs to emit structured evidence (attempt.py's RunEvent recording)
    uses evaluate_member_hints_from_search_evidence() for this richer
    shape instead - one real implementation, two return shapes, never two
    independently-maintained copies of the grounding logic itself.

    `outcome` is one of: "empty_search_text", "unsupported_language",
    "no_distinctive_tokens", "grounded_by_name", "no_name_match",
    "ambiguous_name_conflict", "insufficient_distinctive_tokens",
    "no_containment_match", "ambiguous_containment",
    "grounded_by_containment"."""

    candidates: List[MemberHintCandidate]
    outcome: str
    distinctive_token_count: int


def evaluate_member_hints_from_search_evidence(
    path: str, current_content: str, search_text: str,
) -> SearchEvidenceGroundingResult:
    """The real implementation behind resolve_member_hints_from_search_
    evidence() (below, now a thin wrapper) - see that function's own
    docstring for the full Rule A/Rule B design rationale, unchanged here.
    This wrapper adds only a deterministic `outcome` label alongside the
    exact same candidates - selection semantics are byte-for-byte
    identical to before this function existed (confirmed by the existing
    resolve_member_hints_from_search_evidence() test suite, which now
    exercises this same code through the wrapper)."""
    if not search_text or not search_text.strip():
        return SearchEvidenceGroundingResult([], "empty_search_text", 0)
    boundaries = member_boundaries_for(path, current_content)
    if not boundaries:
        return SearchEvidenceGroundingResult([], "unsupported_language", 0)
    distinctive = _distinctive_search_tokens(search_text)
    if not distinctive:
        return SearchEvidenceGroundingResult([], "no_distinctive_tokens", 0)

    if len(distinctive) == 1:
        member_ids = member_ids_matching_name(boundaries, distinctive[0])
        if len(member_ids) == 1:
            return SearchEvidenceGroundingResult(
                [MemberHintCandidate(member_id=member_ids[0], provenance="search_symbol_reference")],
                "grounded_by_name", 1,
            )
        outcome = "ambiguous_name_conflict" if len(member_ids) > 1 else "no_name_match"
        return SearchEvidenceGroundingResult([], outcome, 1)

    if len(distinctive) < _MEMBER_HINT_MIN_DISTINCTIVE_TOKENS:
        return SearchEvidenceGroundingResult([], "insufficient_distinctive_tokens", len(distinctive))
    containing = [
        boundary for boundary in boundaries
        if all(
            token in extract_member_body(current_content, boundary.start_line, boundary.end_line)
            for token in distinctive
        )
    ]
    if not containing:
        return SearchEvidenceGroundingResult([], "no_containment_match", len(distinctive))
    grounded = _collapse_nested_containing(containing)
    if grounded is None:
        return SearchEvidenceGroundingResult([], "ambiguous_containment", len(distinctive))
    return SearchEvidenceGroundingResult(
        [MemberHintCandidate(member_id=grounded.member_id, provenance="search_token_containment")],
        "grounded_by_containment", len(distinctive),
    )


def resolve_member_hints_from_search_evidence(
    path: str, current_content: str, search_text: str,
) -> List[MemberHintCandidate]:
    """SOURCE 3 (retry evidence, CTX-001-P1-C3): a rejected anchored-edit
    candidate's own SEARCH text -> zero or one VALIDATED member hint for
    `path`, grounded deterministically against `current_content` (always
    caller-supplied, CurrentSourceResolver-resolved - same contract as
    SOURCE 1/2; this function does no I/O of its own).

    Two independently-deterministic grounding rules, tried in this exact
    order - see this module's own header comment for why neither is a
    score:

    RULE A (sole-evidence symbol self-reference): fires ONLY when exactly
    ONE distinctive token survives stoplist filtering AND it is the exact
    bare name of exactly one real current member (member_ids_matching_name)
    - the model's SEARCH text carried nothing else distinctive enough to
    corroborate or contradict that single name, so the name itself is the
    entire signal. Deliberately narrow: an earlier version of this rule
    fired on ANY distinctive token matching a real member's name, even
    among several OTHER distinctive tokens - found live, via a synthetic
    Java case in this same package's own self-test, that a SEARCH block
    editing calculateTotal()'s own body but CALLING a real, differently-
    named method applyDiscountSchedule() would wrongly ground to the
    CALLED method, not the one actually being edited (a reference to a
    real name is not evidence that name IS the edit target). Two or more
    distinctive tokens now always falls through to Rule B instead, which
    requires every token - including any that happen to name a real
    member - to jointly corroborate the same boundary.

    RULE B (structural containment, 2+ distinctive tokens): requires at
    least _MEMBER_HINT_MIN_DISTINCTIVE_TOKENS distinctive tokens (never a
    single word, however rare) ALL present, verbatim, in one real member's
    CURRENT body (extract_member_body against current_content, never the
    SEARCH text's own content) - this is what actually resolves the Java
    case above correctly: calculateTotal()'s body contains its own loop
    variables AND the applyDiscountSchedule() call it makes, so every
    distinctive token is jointly satisfied there, while
    applyDiscountSchedule()'s own body does not contain the loop
    variables. See _collapse_nested_containing for how nested-boundary
    containment is resolved without treating ordinary parent/child nesting
    as ambiguity.

    Returns [] (no hint, never a guess/highest-overlap pick) when:
    search_text is empty or whitespace-only, the language has no member
    extractor, no distinctive tokens survive stoplist filtering, or
    neither rule uniquely grounds a single member.

    Thin wrapper over evaluate_member_hints_from_search_evidence() (above) -
    identical selection behavior, just without that function's added
    `outcome` diagnostic. Existing callers that only need candidates keep
    using this exact signature unchanged."""
    return evaluate_member_hints_from_search_evidence(path, current_content, search_text).candidates
