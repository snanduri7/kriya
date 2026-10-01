"""CodeIntelligenceService: the one seam workflow code uses for structure.

- ``BaselineIndex``: the persisted ``StructuralStore`` for the repository
  revision last indexed (each file bound to its raw digest + parser identity).
- ``CandidateOverlay``: a candidate's added/changed files and deletions,
  parsed in memory. Overlay entries shadow the baseline file-for-file; a
  tombstone hides a baseline file; unchanged files answer from the baseline.
  Discarding the overlay discards its state - nothing is ever written.

Localization answers from the index. Mutation input never does: ``get_member``
and ``member_at`` read the CURRENT bytes (overlay, else the workspace file),
bind them by sha256, and re-parse when the index holds another revision, so
an index that no longer matches the repository can never become mutation
authority.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional

from kriya.code_intel import locate as loc
from kriya.code_intel.model import FileStructure, ParseState, Span, Symbol, source_digest
from kriya.code_intel.parsing import language_for_path, parse_file
from kriya.code_intel.store import StructuralStore, identifier_terms

# Kinds a localization result may name (types are kept: a goal can target one).
_LOCATABLE = frozenset({"method", "constructor", "function", "field", "attribute", "variable", "enum_constant",
                        "annotation_element", "class", "interface", "enum", "record", "annotation_type"})


@dataclass(frozen=True)
class MemberSource:
    """A member's exact current source. ``text`` is the decoded byte range
    ``span`` of the file whose raw sha256 is ``source_digest``."""

    symbol: Symbol
    path: str
    source_digest: str
    span: Span
    text: str
    # True when the baseline index held exactly these bytes.
    index_current: bool


@dataclass
class RefreshReport:
    parsed: List[str] = field(default_factory=list)
    unchanged: int = 0
    removed: List[str] = field(default_factory=list)
    states: Dict[str, str] = field(default_factory=dict)  # non-PARSED states


class CandidateOverlay:
    """path -> raw bytes (added/changed) or None (deleted)."""

    def __init__(self, files: Mapping[str, Optional[bytes]]) -> None:
        self.data: Dict[str, bytes] = {p: b for p, b in files.items() if b is not None}
        self.tombstones = frozenset(p for p, b in files.items() if b is None)
        self.structures: Dict[str, FileStructure] = {
            p: parse_file(p, b) for p, b in self.data.items() if language_for_path(p)}

    @property
    def shadowed(self) -> frozenset:
        return frozenset(self.data) | self.tombstones

    def symbols(self) -> Iterable[Symbol]:
        for structure in self.structures.values():
            yield from structure.symbols


def discover_source_files(root: str) -> List[str]:
    """Tracked and untracked-not-ignored files with a structural parser."""
    result = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=root,
                            capture_output=True, text=True)
    if result.returncode != 0:
        found = []
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            found.extend(os.path.relpath(os.path.join(dirpath, f), root) for f in files)
        candidates = found
    else:
        candidates = result.stdout.splitlines()
    return sorted(p for p in candidates if language_for_path(p) and os.path.isfile(os.path.join(root, p)))


class CodeIntelligenceService:
    def __init__(self, workspace_root: str, db_path: str, overlay: Optional[CandidateOverlay] = None,
                 _store: Optional[StructuralStore] = None) -> None:
        self.workspace_root = workspace_root
        self.db_path = db_path
        self.store = _store or StructuralStore(db_path)
        self.overlay = overlay

    @classmethod
    def for_config(cls, cfg, workspace_root: str) -> "CodeIntelligenceService":
        return cls(workspace_root, os.path.join(cfg.paths.memory, "dependency_graph.db"))

    def close(self) -> None:
        self.store.close()

    def with_overlay(self, files: Mapping[str, Optional[bytes]]) -> "CodeIntelligenceService":
        """A view of the baseline shadowed by a candidate (shares the store)."""
        return CodeIntelligenceService(self.workspace_root, self.db_path, CandidateOverlay(files), _store=self.store)

    def without_overlay(self) -> "CodeIntelligenceService":
        return CodeIntelligenceService(self.workspace_root, self.db_path, None, _store=self.store)

    # -- baseline refresh -------------------------------------------------

    def refresh(self, paths: Optional[Iterable[str]] = None) -> RefreshReport:
        """Bring the baseline up to the files' current bytes. ``paths=None``
        refreshes every source file and removes vanished ones."""
        report = RefreshReport()
        full = paths is None
        wanted = discover_source_files(self.workspace_root) if full else list(paths)
        known = self.store.file_states()
        for path in wanted:
            absolute = os.path.join(self.workspace_root, path)
            if not os.path.isfile(absolute):
                if path in known:
                    self.store.remove(path)
                    report.removed.append(path)
                continue
            with open(absolute, "rb") as handle:
                data = handle.read()
            if known.get(path, ("",))[0] == source_digest(data):
                report.unchanged += 1
                continue
            structure = parse_file(path, data)
            self.store.publish(structure, data)
            report.parsed.append(path)
            if structure.state is not ParseState.PARSED:
                report.states[path] = structure.state.value
        if full:
            for path in sorted(set(known) - set(wanted)):
                self.store.remove(path)
                report.removed.append(path)
        return report

    # -- symbol queries (overlay aware) ----------------------------------

    def _shadowed(self) -> frozenset:
        return self.overlay.shadowed if self.overlay else frozenset()

    def _overlay_symbols(self) -> Iterable[Symbol]:
        return self.overlay.symbols() if self.overlay else ()

    def find_symbol(self, name: str) -> List[Symbol]:
        """Exact qualified key, else qualified suffix (``Class.member``), else
        simple name."""
        shadowed = self._shadowed()
        found = self.store.by_lookup_key(name, shadowed) + [s for s in self._overlay_symbols() if s.lookup_key == name]
        if not found and "." in name:
            found = self.store.by_lookup_suffix(name, shadowed) + [
                s for s in self._overlay_symbols() if s.lookup_key.endswith("." + name)]
        if not found:
            found = self.store.by_name(name, shadowed) + [s for s in self._overlay_symbols() if s.name == name]
        return sorted(found, key=lambda s: s.symbol_id)

    def _by_name(self, name: str) -> List[Symbol]:
        return self.store.by_name(name, self._shadowed()) + [s for s in self._overlay_symbols() if s.name == name]

    def _resolve_path(self, mentioned: str) -> List[str]:
        mentioned = mentioned.replace("\\", "/")
        if os.path.isabs(mentioned):
            relative = os.path.relpath(mentioned, self.workspace_root)
            if not relative.startswith(".."):
                mentioned = relative
        tombstones = self.overlay.tombstones if self.overlay else frozenset()
        # The longest path suffix that names indexed files: tool output
        # carries absolute paths of another root (a worktree, a container).
        parts = mentioned.strip("/").split("/")
        for start in range(len(parts)):
            suffix = "/".join(parts[start:])
            found = [p for p in self.overlay.data if p == suffix or p.endswith("/" + suffix)] if self.overlay else []
            found += [p for p in self.store.paths_with_suffix(suffix) if p not in tombstones and p not in found]
            if found:
                return found
        return []

    # -- current bytes ----------------------------------------------------

    def _current(self, path: str) -> Optional[bytes]:
        if self.overlay:
            if path in self.overlay.tombstones:
                return None
            if path in self.overlay.data:
                return self.overlay.data[path]
        absolute = os.path.join(self.workspace_root, path)
        if not os.path.isfile(absolute):
            return None
        with open(absolute, "rb") as handle:
            return handle.read()

    def current_structure(self, path: str) -> Optional[FileStructure]:
        if self.overlay and path in self.overlay.structures:
            return self.overlay.structures[path]
        data = self._current(path)
        return parse_file(path, data) if data is not None and language_for_path(path) else None

    def member_at(self, path: str, line: int) -> Optional[Symbol]:
        """The most specific declaration containing ``line`` of the CURRENT
        file (the index answers only when it holds exactly those bytes)."""
        data = self._current(path)
        if data is None:
            return None
        if (not self.overlay or path not in self.overlay.data) and self.store.file_digest(path) == source_digest(data):
            containing = self.store.containing(path, line)
            return min(containing, key=lambda s: (s.declaration.line_count, s.symbol_id)) if containing else None
        structure = self.current_structure(path)
        return structure.symbol_at_line(line) if structure else None

    def get_member(self, symbol_id: str) -> Optional[MemberSource]:
        """The member's exact current source, bound to the current digest.
        A member whose file changed since indexing is re-found by id in the
        current bytes (by qualified key + parameter types when ids moved)."""
        indexed = self._lookup_id(symbol_id)
        if indexed is None:
            return None
        data = self._current(indexed.path)
        if data is None:
            return None
        digest = source_digest(data)
        symbol = indexed
        if digest != indexed.source_digest:
            structure = parse_file(indexed.path, data)
            symbol = next((s for s in structure.symbols if s.symbol_id == symbol_id), None) or next(
                (s for s in structure.symbols if s.lookup_key == indexed.lookup_key
                 and s.parameter_types == indexed.parameter_types), None)
            if symbol is None:
                return None
        span = symbol.declaration
        return MemberSource(symbol, symbol.path, digest, span,
                            data[span.start_byte:span.end_byte].decode("utf-8", "replace"),
                            index_current=digest == indexed.source_digest and not (
                                self.overlay and indexed.path in self.overlay.data))

    def _lookup_id(self, symbol_id: str) -> Optional[Symbol]:
        if self.overlay:
            hit = next((s for s in self.overlay.symbols() if s.symbol_id == symbol_id), None)
            if hit is not None:
                return hit
            path = symbol_id.split(":", 1)[-1].split("#", 1)[0]
            if path in self.overlay.shadowed:
                return None
        return self.store.by_id(symbol_id)

    # -- localization -----------------------------------------------------

    def search(self, text: str, limit: int = 10) -> List[loc.LocateHit]:
        evidence: Dict[str, loc.Evidence] = {}
        meta: Dict[str, tuple] = {}
        self._credit_fts(text, evidence, meta, limit)
        return loc.rank(evidence, meta, limit)

    def _credit(self, evidence, meta, symbol: Symbol, channel: str, weight: float) -> None:
        if symbol.kind not in _LOCATABLE:
            return
        evidence.setdefault(symbol.symbol_id, loc.Evidence()).add(channel, weight)
        meta[symbol.symbol_id] = (symbol.path, symbol.lookup_key, symbol.kind)

    def _credit_fts(self, text: str, evidence, meta, limit: int) -> None:
        terms = [t for t in identifier_terms(text) if t not in loc._STOP and len(t) > 2]
        shadowed = self._shadowed()
        hits = self.store.search(terms, limit, shadowed)
        overlay_hits = self._overlay_term_hits(terms)
        merged = sorted(hits + overlay_hits, key=lambda h: -h[1])[:limit]
        if not merged:
            return
        top = merged[0][1]
        symbols = {s.symbol_id: s for s in self._symbols_by_id([h[0] for h in merged])}
        about_tests = loc.mentions_tests(text)
        for symbol_id, score in merged:
            symbol = symbols.get(symbol_id)
            if symbol is None or self._names_scope(symbol):
                continue  # lexical similarity never promotes a scope (type/constructor)
            weight = loc.FTS_MAX * score / top
            if not about_tests and loc.is_test_path(symbol.path):
                weight *= loc.TEST_CODE_FACTOR
            self._credit(evidence, meta, symbol, "fts", weight)

    def _overlay_term_hits(self, terms) -> List[tuple]:
        if not self.overlay or not terms:
            return []
        wanted = set(terms)
        hits = []
        for structure in self.overlay.structures.values():
            data = self.overlay.data[structure.path]
            for symbol in structure.symbols:
                body = data[symbol.declaration.start_byte:symbol.declaration.end_byte].decode("utf-8", "replace")
                names = set(identifier_terms(symbol.lookup_key))
                overlap = 4 * len(wanted & names) + len(wanted & set(identifier_terms(body)))
                if overlap:
                    hits.append((symbol.symbol_id, float(overlap)))
        return hits

    def _symbols_by_id(self, ids: List[str]) -> List[Symbol]:
        overlay = {s.symbol_id: s for s in self._overlay_symbols()}
        stored = self.store.by_ids([i for i in ids if i not in overlay])
        return [overlay.get(i) or stored[i] for i in ids if i in overlay or i in stored]

    def locate(self, text: str, limit: int = 10) -> List[loc.LocateHit]:
        """Deterministic localization of ``text`` (goal, compiler output,
        stack trace) to members, best first, each with its channels."""
        signals = loc.extract_signals(text)
        evidence: Dict[str, loc.Evidence] = {}
        meta: Dict[str, tuple] = {}
        owners: set = set()

        for mentioned, line in signals.file_lines:
            for path in self._resolve_path(mentioned):
                member = self.member_at(path, line)
                if member is not None:
                    self._credit(evidence, meta, member, "file_line", loc.FILE_LINE)
        for owner, method, line in signals.frames:
            types = [s for s in self.find_symbol(owner.split("$", 1)[0]) if s.is_type]
            for owner_type in types:
                member = self.member_at(owner_type.path, line)
                if member is not None:
                    self._credit(evidence, meta, member, "stack_frame", loc.FILE_LINE)
                for candidate in self.find_symbol(f"{owner_type.lookup_key}.{method}"):
                    self._credit(evidence, meta, candidate, "stack_frame_method", loc.QUALIFIED_SUFFIX)
        for name in signals.qualified:
            exact = self.store.by_lookup_key(name, self._shadowed()) + [
                s for s in self._overlay_symbols() if s.lookup_key == name]
            matches = exact or self.store.by_lookup_suffix(name, self._shadowed()) + [
                s for s in self._overlay_symbols() if s.lookup_key.endswith("." + name)]
            weight = loc.QUALIFIED_EXACT if exact else loc.QUALIFIED_SUFFIX
            if matches and len(matches) <= loc.MAX_SIMPLE_MATCHES:
                for symbol in matches:
                    if self._names_scope(symbol):
                        self._credit(evidence, meta, symbol, "qualified_symbol", loc.OWNER_NAMED)
                        owners.add(symbol.parent_id if symbol.kind == "constructor" else symbol.symbol_id)
                        continue
                    self._credit(evidence, meta, symbol, "qualified_symbol", weight)
                    if symbol.parent_id:
                        owners.add(symbol.parent_id)
        for mentioned in signals.paths:
            for path in self._resolve_path(mentioned):
                for symbol in self._file_types(path):
                    self._credit(evidence, meta, symbol, "path", loc.PATH_MENTIONED)
                    owners.add(symbol.symbol_id)
        for word in signals.words:
            matches = self._by_name(word)
            prose = loc.is_prose_word(word)
            if prose:
                # A plain lower-case word ("abbreviate", "daylight") names a
                # callable at most; it is weaker evidence than a code-shaped
                # identifier and never names a field or variable.
                matches = [s for s in matches if s.is_callable or self._names_scope(s)]
            weight = loc.specificity(len(matches))
            if weight is not None and prose:
                weight *= loc.PROSE_FACTOR
            if weight is None:
                continue
            for symbol in matches:
                if self._names_scope(symbol):
                    # A type name in a goal is the scope of the change, not
                    # its target: it boosts the type's members instead.
                    self._credit(evidence, meta, symbol, "simple_symbol", loc.OWNER_NAMED * weight)
                    owners.add(symbol.parent_id if symbol.kind == "constructor" else symbol.symbol_id)
                    continue
                self._credit(evidence, meta, symbol, "simple_symbol", loc.SIMPLE_SYMBOL * weight)
        for literal in signals.strings:
            hits = self.store.search([" ".join(identifier_terms(literal))], loc.MAX_SIMPLE_MATCHES + 1,
                                     self._shadowed()) if identifier_terms(literal) else []
            weight = loc.specificity(len(hits))
            if weight is not None:
                for symbol in self._symbols_by_id([h[0] for h in hits]):
                    self._credit(evidence, meta, symbol, "string_literal", loc.STRING_LITERAL * weight)
        self._credit_fts(text, evidence, meta, limit=50)
        if owners:
            for symbol in self._symbols_by_id(list(evidence)):
                if symbol.parent_id in owners and not self._names_scope(symbol):
                    evidence[symbol.symbol_id].add("owner_named", loc.OWNER_NAMED)
        return loc.rank(evidence, meta, limit)

    @staticmethod
    def _names_scope(symbol: Symbol) -> bool:
        """A type, or a constructor (named exactly like its type)."""
        return symbol.is_type or symbol.kind == "constructor"

    def _file_types(self, path: str) -> List[Symbol]:
        if self.overlay and path in self.overlay.structures:
            symbols = list(self.overlay.structures[path].symbols)
        else:
            symbols = self.store.by_path(path)
        return [s for s in symbols if s.is_type and s.parent_id is None]
