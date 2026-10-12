"""PLAN-TARGET-GROUNDING-001 (b)/(c): deterministic facts about where the goal's own names live.

The enforce Planner chooses write targets from a file list, a ranked localization map and structural relationships.
Two measured mistakes were not failures of reasoning but of missing facts that Kriya could read exactly:

- a goal that says ``Element.absUrl`` where only Element's supertype declares ``absUrl`` (jsoup): the Planner planned
  the owner the goal named, a file that does not contain the method, in four of four runs;
- a goal about a missing dependency (python-slugify): the Planner planned ``setup.py``, a four-line shim whose own text
  says the metadata lives in ``pyproject.toml``, which holds the dependency declaration.

This module derives those facts - never from model output, only from the current repository files and the structural
index - and renders them as a small, bounded block labelled DERIVED_DETERMINISTIC. It states what was found; it never
says what to change, and a fact that was not found is simply absent. The one place it can refuse a plan is
``delegating_shim_targets``: a manifest edit aimed at a file that delegates to another file which the goal's own
package name occurs in, with that other file unplanned (``validate_plan``, PLAN_TARGETS_DELEGATING_SHIM).
"""
from __future__ import annotations

import ast
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Dependency / packaging manifests whose text can declare a name the goal mentions. Matched by base name.
_MANIFEST_NAMES = frozenset({
    "pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "pom.xml", "build.gradle", "build.gradle.kts",
    "settings.gradle", "settings.gradle.kts", "package.json", "Cargo.toml", "go.mod", "MANIFEST.in",
})
_REQUIREMENTS_RE = re.compile(r"^requirements[\w.-]*\.txt$")
_WALK_SKIP = frozenset({".git", ".kriya", ".hg", ".svn", "node_modules", "target", "build", "dist", ".venv", "venv",
                        "__pycache__", ".gradle", ".idea", ".tox"})
_MANIFEST_DEPTH = 2          # directories below the workspace root that are searched
_MAX_MANIFEST_BYTES = 256 * 1024
_MAX_LINE_CHARS = 160
_MAX_TOKENS = 24
_MAX_MANIFEST_HITS = 6
_MAX_MEMBER_FACTS = 6
_MAX_DELEGATE_FACTS = 4
MAX_BLOCK_CHARS = 1800

# A hyphen- or underscore-joined name (a package/module/dependency spelling): ``text-unidecode``, ``text_unidecode``.
_COMPOUND_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+\b")
_BACKTICK_RE = re.compile(r"`([A-Za-z][\w.-]{2,39})`")
# A line that only describes the project (its own name, summary, links) is not a declaration of anything the goal could be
# about: a goal that names the project's package must not match ``name = "<package>"``.
_METADATA_LINE_RE = re.compile(
    r"^\s*(?:name|description|summary|readme|homepage|home-page|repository|documentation|authors?|maintainers?|license|"
    r"keywords|version|url|urls|packages|artifactId|groupId)\b\s*[=:]", re.IGNORECASE)


def _canonical(token: str) -> str:
    return token.lower().replace("_", "-")


def goal_package_tokens(goal: str) -> List[str]:
    """Canonical spellings (lower case, ``_`` -> ``-``) of the compound names and code-formatted names a goal writes,
    in first-mention order, bounded. ``text_unidecode`` and ``text-unidecode`` are one token."""
    seen: Dict[str, None] = {}
    for match in list(_COMPOUND_RE.finditer(goal or "")) + list(_BACKTICK_RE.finditer(goal or "")):
        raw = match.group(1) if match.re is _BACKTICK_RE else match.group(0)
        token = _canonical(raw)
        if 3 <= len(token) <= 40 and not token.startswith("test-"):
            seen.setdefault(token, None)
    return list(seen)[:_MAX_TOKENS]


def _is_manifest(name: str) -> bool:
    return name in _MANIFEST_NAMES or bool(_REQUIREMENTS_RE.match(name))


def manifest_files(workspace_path: str) -> List[str]:
    """Workspace-relative paths of the manifests within ``_MANIFEST_DEPTH`` directories of the root, sorted."""
    found: List[str] = []
    root = os.path.abspath(workspace_path)
    for directory, subdirs, files in os.walk(root):
        depth = 0 if directory == root else os.path.relpath(directory, root).count(os.sep) + 1
        subdirs[:] = sorted(d for d in subdirs if d not in _WALK_SKIP and not d.startswith("."))
        if depth >= _MANIFEST_DEPTH:
            subdirs[:] = []
        for name in sorted(files):
            if _is_manifest(name):
                found.append(os.path.relpath(os.path.join(directory, name), root).replace(os.sep, "/"))
    return sorted(found)


def _read_text(workspace_path: str, rel: str) -> Optional[str]:
    path = os.path.join(workspace_path, rel)
    try:
        if os.path.getsize(path) > _MAX_MANIFEST_BYTES:
            return None
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


@dataclass(frozen=True)
class ManifestHit:
    token: str
    path: str
    line: int
    text: str


def manifest_hits(goal: str, workspace_path: str) -> List[ManifestHit]:
    """Where the goal's compound names occur in the workspace's manifests (whole-token, ``_`` ~ ``-``), first hit per
    (token, file), ordered by file then line, bounded."""
    tokens = goal_package_tokens(goal)
    if not tokens:
        return []
    patterns = {token: re.compile(r"(?<![A-Za-z0-9._-])" + re.escape(token) + r"(?![A-Za-z0-9_-])") for token in tokens}
    hits: List[ManifestHit] = []
    for rel in manifest_files(workspace_path):
        text = _read_text(workspace_path, rel)
        if text is None:
            continue
        found: set = set()
        for number, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("#", "//", "<!--")) or "://" in stripped or _METADATA_LINE_RE.match(line):
                continue  # a comment, a link or a project-description line declares nothing
            canonical = _canonical(line)
            for token, pattern in patterns.items():
                if token not in found and pattern.search(canonical):
                    found.add(token)
                    hits.append(ManifestHit(token, rel, number, line.strip()[:_MAX_LINE_CHARS]))
    return hits[:_MAX_MANIFEST_HITS]


def is_bare_setup_shim(source: str) -> bool:
    """A Python ``setup.py`` whose only statements are a docstring, imports and ONE ``setup()`` call without arguments:
    it declares nothing itself, so any metadata comes from elsewhere. Anything else (arguments, assignments, control
    flow other than an ``if __name__`` guard around that single call) is not a shim."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return False
    calls = 0
    body = list(tree.body)
    for node in body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            func = node.value.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if name == "setup" and not node.value.args and not node.value.keywords:
                calls += 1
                continue
        return False
    return calls == 1


def _static_project_table(pyproject_text: str) -> bool:
    return re.search(r"^\[project\]\s*$", pyproject_text, re.MULTILINE) is not None


def delegating_shims(workspace_path: str) -> Dict[str, str]:
    """{shim path: delegate path} for the delegating shims the workspace contains: a bare ``setup.py`` beside a
    ``pyproject.toml`` that carries a static ``[project]`` table (the metadata lives there)."""
    shims: Dict[str, str] = {}
    for rel in manifest_files(workspace_path):
        if os.path.basename(rel) != "setup.py":
            continue
        directory = os.path.dirname(rel)
        delegate = f"{directory}/pyproject.toml" if directory else "pyproject.toml"
        setup_text = _read_text(workspace_path, rel)
        project_text = _read_text(workspace_path, delegate)
        if setup_text is not None and project_text is not None and is_bare_setup_shim(setup_text) \
                and _static_project_table(project_text):
            shims[rel] = delegate
    return shims


def delegating_shim_targets(goal: str, workspace_path: str) -> Dict[str, str]:
    """The planning constraint: {shim: delegate} for the shims whose DELEGATE contains a name the goal writes. Without
    such a hit the goal gives no reason to suspect the shim, and an edit of it is left to the ordinary gates."""
    shims = delegating_shims(workspace_path)
    if not shims:
        return {}
    delegates_hit = {hit.path for hit in manifest_hits(goal, workspace_path)}
    return {shim: delegate for shim, delegate in shims.items() if delegate in delegates_hit}


@dataclass
class GoalFacts:
    members: List[str] = field(default_factory=list)
    manifests: List[ManifestHit] = field(default_factory=list)
    shims: Dict[str, str] = field(default_factory=dict)
    delegates: List[str] = field(default_factory=list)

    def empty(self) -> bool:
        return not (self.members or self.manifests or self.shims or self.delegates)


def member_facts(view: Any, goal: str) -> List[str]:
    """One line per goal-stated ``Owner.member`` that ``Owner`` does not declare and a supertype does."""
    from kriya.code_intel import locate as loc

    lines: List[str] = []
    for name in loc.extract_signals(goal).qualified:
        if "." not in name:
            continue
        owner, member = name.rsplit(".", 1)
        owner_types = [s for s in view.find_symbol(owner) if s.is_type]
        if not owner_types:
            continue
        inherited = view.inherited_members(owner, member)
        if not inherited:
            continue
        declared = ", ".join(f"{s.lookup_key} at {s.path}:{s.declaration.start_line}" for s in inherited[:3])
        more = f" (+{len(inherited) - 3} more)" if len(inherited) > 3 else ""
        lines.append(f"{owner}.{member}: {owner_types[0].lookup_key} ({owner_types[0].path}) does not declare "
                     f"{member}; a supertype declares it: {declared}{more}")
        if len(lines) >= _MAX_MEMBER_FACTS:
            break
    return lines


def delegate_facts(view: Any, goal: str) -> List[str]:
    """BACKEND-READINESS-V2 (T2): one line per goal-named ``Owner.member`` whose returned value is computed by another
    project method. Live: the goal named ``Element.absUrl``; its declaration (Node.absUrl) only ends in
    ``return StringUtil.resolve(...)``, so the behaviour the goal describes lives in the callee, yet two candidates were
    written into the delegate (one reimplemented the resolution there and broke eight existing tests, the other fixed
    only half of it). The Planner had even listed the callee as a preserved reference. Read from the member's own source and
    the structural index: the LAST ``return`` of a Java member whose expression calls ``Type.method(`` on a project type that
    declares ``method`` (another file), for a goal-named member with exactly ONE declaration. Names where the value comes
    from; never says what to change. Java only."""
    from kriya.code_intel import locate as loc
    from kriya.code_intel.delegation import delegate_targets

    lines: List[str] = []
    for name in loc.extract_signals(goal).qualified:
        if "." not in name:
            continue
        owner, member = name.rsplit(".", 1)
        declared = [s for s in view.find_symbol(f"{owner}.{member}") if s.is_callable and s.path.endswith(".java")]
        declared = declared or [s for s in view.inherited_members(owner, member) if s.is_callable and s.path.endswith(".java")]
        if len(declared) != 1:
            continue  # an overloaded name (a setup call in an example) does not say which declaration the goal means
        for symbol in declared:
            source = view.get_member(symbol.symbol_id)
            targets = delegate_targets(view, symbol, source.text) if source else []
            if not targets:
                continue
            callee_type, callee = targets[0].lookup_key.rsplit(".", 2)[-2:]
            where = ", ".join(f"{t.path}:{t.declaration.start_line}" for t in targets[:3])
            more = f" (+{len(targets) - 3} more)" if len(targets) > 3 else ""
            lines.append(f"{symbol.lookup_key} ({symbol.path}:{symbol.declaration.start_line}) returns the result of "
                         f"{callee_type}.{callee}(...); that method is declared at {where}{more}")
            if len(lines) >= _MAX_DELEGATE_FACTS:
                return lines
    return lines


def build_goal_facts(goal: str, workspace_path: str, view: Optional[Any] = None) -> GoalFacts:
    facts = GoalFacts(manifests=manifest_hits(goal, workspace_path))
    if view is not None:
        facts.members = member_facts(view, goal)
        facts.delegates = delegate_facts(view, goal)
    if facts.manifests:
        hit_files = {hit.path for hit in facts.manifests}
        facts.shims = {shim: delegate for shim, delegate in delegating_shims(workspace_path).items()
                       if delegate in hit_files}
    return facts


def render_goal_facts(facts: GoalFacts) -> str:
    """The Planner-request block, or "" when no fact was found. Bounded; states what was found, never what to change."""
    if facts.empty():
        return ""
    lines = ["", "=== GOAL-NAMED FACTS (DERIVED_DETERMINISTIC: read from the current repository files and the structural "
             "index, not model output; a name with no line here was not found) ==="]
    lines.extend(f"- {line}" for line in facts.members)
    lines.extend(f"- {line}" for line in facts.delegates)
    lines.extend(f"- \"{hit.token}\" occurs in {hit.path}:{hit.line}: {hit.text}" for hit in facts.manifests)
    lines.extend(f"- {shim} is a delegating shim (its only statement is a bare setup() call); the packaging metadata "
                 f"it delegates to is declared in {delegate}" for shim, delegate in sorted(facts.shims.items()))
    lines.append("These say where the goal's names live. They are not instructions about what to change.")
    return "\n".join(lines)[:MAX_BLOCK_CHARS] + "\n"


def goal_facts_block(dependency_graph_path: Optional[str], workspace_path: str, goal: str) -> str:
    """The rendered block for ``goal`` over the workspace and its structural index (``dependency_graph_path``; None or a
    missing index leaves the member facts out). Never raises into planning: an unreadable index is logged and the
    manifest facts still render."""
    from kriya.workflow.graph_retrieval import open_code_intelligence

    service = None
    try:
        service = open_code_intelligence(workspace_path, dependency_graph_path)
        return render_goal_facts(build_goal_facts(goal, workspace_path, service.current_view() if service else None))
    except Exception as error:  # evidence only: planning proceeds without the block
        logger.warning("Goal-named facts unavailable for planning: %s", error)
        return ""
    finally:
        if service is not None:
            service.close()


_DEVELOPER_FACTS_CACHE: Dict[Tuple[Any, ...], str] = {}


def developer_goal_facts(goal_text: str, workspace_path: str, dependency_graph_path: Optional[str]) -> str:
    """The same GOAL-NAMED FACTS block the Planner was given, for the Developer's request (BACKEND-READINESS-V2, T2: measured,
    the Planner had it in 7 of 15 runs and the Developer in 0 of 52 requests). ``goal_text`` is a subtask goal that may
    carry the Planner's own strategy: only the user's authoritative section is read, so the Planner's word choices never
    become facts. Same function, same inputs, same output as the Planner's block; "" when no fact was found."""
    from kriya.agents.contracts import AUTHORITATIVE_GOAL_SECTION_HEADER, PLANNED_IMPLEMENTATION_SECTION_HEADER

    text = goal_text
    if AUTHORITATIVE_GOAL_SECTION_HEADER in text:
        text = text.split(AUTHORITATIVE_GOAL_SECTION_HEADER, 1)[1].split(PLANNED_IMPLEMENTATION_SECTION_HEADER, 1)[0]
    # One block per (goal, workspace, index state): the Developer request is rebuilt for every attempt and file.
    try:
        stamp = os.stat(dependency_graph_path).st_mtime_ns if dependency_graph_path else None
    except OSError:
        stamp = None
    key = (text, workspace_path, dependency_graph_path, stamp)
    if key not in _DEVELOPER_FACTS_CACHE:
        if len(_DEVELOPER_FACTS_CACHE) >= 16:
            _DEVELOPER_FACTS_CACHE.clear()
        _DEVELOPER_FACTS_CACHE[key] = goal_facts_block(dependency_graph_path, workspace_path, text)
    return _DEVELOPER_FACTS_CACHE[key]


def shim_plan_errors(plan_files: List[Tuple[str, str, str]], shims: Dict[str, str]) -> List[str]:
    """Pure helper for ``validate_plan``: ``plan_files`` is [(subtask id, path, action)]. One error naming each subtask
    that MODIFIES a shim while the shim's delegate is planned by no subtask."""
    planned = {path for _sid, path, _action in plan_files}
    return [f"{sid}:{path}" for sid, path, action in plan_files
            if action == "modify" and path in shims and shims[path] not in planned]
