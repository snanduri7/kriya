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
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

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
    ) -> None:
        self.workspace_path = workspace_path
        self.worktree_path = worktree_path
        self.known_revisions = known_revisions or {}

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
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            return ResolvedSource(
                path=relpath, exists=False, content=None, revision="",
                root_used=root, status="unavailable",
            )
        real_revision = content_revision(content)
        hint = self.known_revisions.get(relpath)
        stale = hint is not None and hint != real_revision
        return ResolvedSource(
            path=relpath, exists=True, content=content, revision=real_revision,
            root_used=root, status="current", stale_hint=stale,
        )


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
