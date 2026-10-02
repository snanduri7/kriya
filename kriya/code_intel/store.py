"""The persisted structural index (BaselineIndex storage).

Lives in the repository's existing structural database
(``<paths.memory>/dependency_graph.db``) next to the dependency graph, whose
manifest binds it to one parser/schema identity. Every file row carries the
raw sha256 its structure was parsed from and the parser digest; a row under
another parser identity is never returned. No daemon, no index server, no
vector service: SQLite tables plus an FTS5 identifier index.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from kriya.code_intel.model import FileStructure, ParseState, Span, Symbol
from kriya.code_intel.parsing import parser_identity
from kriya.core.db import get_connection

STRUCTURAL_TABLES = ("ci_fts", "ci_symbols", "ci_files")

_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")


def identifier_terms(text: str) -> List[str]:
    """Identifiers and their camel/snake parts, lower-cased:
    ``abbreviateMiddle`` -> abbreviatemiddle, abbreviate, middle."""
    terms: List[str] = []
    for identifier in _IDENTIFIER_RE.findall(text):
        lowered = identifier.lower()
        terms.append(lowered)
        parts = [p.lower() for chunk in identifier.split("_") for p in _CAMEL_RE.findall(chunk)]
        if len(parts) > 1:
            terms.extend(parts)
    return terms


def _span_columns(span: Optional[Span]) -> Tuple:
    if span is None:
        return (None, None, None, None)
    return (span.start_line, span.end_line, span.start_byte, span.end_byte)


def _span(row: sqlite3.Row, prefix: str) -> Optional[Span]:
    if row[f"{prefix}_start_line"] is None:
        return None
    return Span(row[f"{prefix}_start_line"], row[f"{prefix}_end_line"], row[f"{prefix}_start_byte"],
                row[f"{prefix}_end_byte"])


class StructuralStore:
    def __init__(self, db_path: str) -> None:
        self.conn = get_connection(db_path)
        self.conn.row_factory = sqlite3.Row
        self.parser_digest = parser_identity().digest
        self.fts_available = True
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS ci_files (path TEXT PRIMARY KEY, language TEXT, source_digest TEXT"
                " NOT NULL, state TEXT NOT NULL, parser_digest TEXT NOT NULL, namespace TEXT, imports TEXT,"
                " error_count INTEGER)")
            spans = ", ".join(f"{p}_{c} INTEGER" for p in ("decl", "sig", "body")
                              for c in ("start_line", "end_line", "start_byte", "end_byte"))
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS ci_symbols (symbol_id TEXT PRIMARY KEY, path TEXT NOT NULL,"
                " language TEXT, kind TEXT, name TEXT, lookup_key TEXT, parent_id TEXT, source_digest TEXT,"
                f" {spans}, modifiers TEXT, annotations TEXT, parameter_types TEXT, return_type TEXT,"
                " signature_text TEXT, doc_summary TEXT, extends TEXT, implements TEXT)")
            for column in ("path", "name", "lookup_key"):
                self.conn.execute(f"CREATE INDEX IF NOT EXISTS idx_ci_symbols_{column} ON ci_symbols({column})")
        try:
            with self.conn:
                self.conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS ci_fts USING fts5(symbol_id UNINDEXED, path UNINDEXED,"
                    " name_terms, body_terms)")
        except sqlite3.OperationalError:
            self.fts_available = False

    # -- writes -----------------------------------------------------------

    def publish(self, structure: FileStructure, data: bytes) -> None:
        """Replace ``structure.path``'s rows in one transaction. ``data`` are
        the raw bytes the structure was parsed from (identifier terms)."""
        with self.conn:
            self._delete(structure.path)
            self.conn.execute(
                "INSERT INTO ci_files VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (structure.path, structure.language, structure.source_digest, structure.state.value,
                 structure.parser_digest, structure.namespace, json.dumps(list(structure.imports)),
                 structure.error_count))
            for symbol in structure.symbols:
                self.conn.execute(
                    "INSERT INTO ci_symbols VALUES (" + ",".join("?" * 28) + ")",
                    (symbol.symbol_id, symbol.path, symbol.language, symbol.kind, symbol.name, symbol.lookup_key,
                     symbol.parent_id, symbol.source_digest, *_span_columns(symbol.declaration),
                     *_span_columns(symbol.signature), *_span_columns(symbol.body),
                     json.dumps(list(symbol.modifiers)), json.dumps(list(symbol.annotations)),
                     json.dumps(list(symbol.parameter_types)), symbol.return_type, symbol.signature_text,
                     symbol.doc_summary, json.dumps(list(symbol.extends)), json.dumps(list(symbol.implements))))
                if self.fts_available:
                    body = data[symbol.declaration.start_byte:symbol.declaration.end_byte].decode("utf-8", "replace")
                    names = " ".join(identifier_terms(f"{symbol.lookup_key} {symbol.doc_summary}"))
                    # A type's body is its members: they carry their own terms.
                    body_terms = "" if symbol.is_type else " ".join(identifier_terms(body))
                    self.conn.execute("INSERT INTO ci_fts VALUES (?, ?, ?, ?)",
                                      (symbol.symbol_id, symbol.path, names, body_terms))

    def remove(self, path: str) -> None:
        with self.conn:
            self._delete(path)

    def _delete(self, path: str) -> None:
        self.conn.execute("DELETE FROM ci_symbols WHERE path = ?", (path,))
        self.conn.execute("DELETE FROM ci_files WHERE path = ?", (path,))
        if self.fts_available:
            self.conn.execute("DELETE FROM ci_fts WHERE path = ?", (path,))

    # -- reads ------------------------------------------------------------

    def file_digest(self, path: str) -> Optional[str]:
        """The raw digest of ``path``'s current-identity structure, or None."""
        row = self.conn.execute("SELECT source_digest FROM ci_files WHERE path = ? AND parser_digest = ?",
                                (path, self.parser_digest)).fetchone()
        return row[0] if row else None

    def file_states(self) -> Dict[str, Tuple[str, ParseState]]:
        rows = self.conn.execute("SELECT path, source_digest, state FROM ci_files WHERE parser_digest = ?",
                                 (self.parser_digest,)).fetchall()
        return {r["path"]: (r["source_digest"], ParseState(r["state"])) for r in rows}

    def _select(self, where: str, params: Sequence, exclude_paths: Iterable[str] = ()) -> List[Symbol]:
        excluded = tuple(exclude_paths)
        clause = f"({where}) AND f.parser_digest = ?"
        args: List = [*params, self.parser_digest]
        if excluded:
            clause += f" AND s.path NOT IN ({','.join('?' * len(excluded))})"
            args.extend(excluded)
        rows = self.conn.execute(
            f"SELECT s.* FROM ci_symbols s JOIN ci_files f ON f.path = s.path WHERE {clause}", args).fetchall()
        return [_symbol(r) for r in rows]

    def by_name(self, name: str, exclude_paths: Iterable[str] = ()) -> List[Symbol]:
        return self._select("s.name = ?", (name,), exclude_paths)

    def by_lookup_key(self, key: str, exclude_paths: Iterable[str] = ()) -> List[Symbol]:
        return self._select("s.lookup_key = ?", (key,), exclude_paths)

    def by_lookup_suffix(self, suffix: str, exclude_paths: Iterable[str] = ()) -> List[Symbol]:
        """Symbols whose qualified key ends with ``.suffix`` (``Class.method``
        for a fully qualified ``pkg.Class.method``); the last segment is an
        indexed exact name match first."""
        last = suffix.rsplit(".", 1)[-1]
        return [s for s in self._select("s.name = ?", (last,), exclude_paths)
                if s.lookup_key == suffix or s.lookup_key.endswith("." + suffix)]

    def by_return_type(self, value: str, kind: Optional[str] = None) -> List[Symbol]:
        """Symbols whose recorded type text is ``value`` (a bean's class)."""
        if kind is None:
            return self._select("s.return_type = ?", (value,))
        return self._select("s.return_type = ? AND s.kind = ?", (value, kind))

    def by_kind(self, kind: str) -> List[Symbol]:
        return self._select("s.kind = ?", (kind,))

    def by_lookup_prefix(self, prefix: str, kind: Optional[str] = None) -> List[Symbol]:
        escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where = "s.lookup_key LIKE ? ESCAPE '\\'" + (" AND s.kind = ?" if kind else "")
        return self._select(where, (escaped + "%", kind) if kind else (escaped + "%",))

    def by_path(self, path: str) -> List[Symbol]:
        return self._select("s.path = ?", (path,))

    def by_id(self, symbol_id: str) -> Optional[Symbol]:
        found = self._select("s.symbol_id = ?", (symbol_id,))
        return found[0] if found else None

    def by_ids(self, symbol_ids: Sequence[str]) -> Dict[str, Symbol]:
        found: Dict[str, Symbol] = {}
        ids = list(dict.fromkeys(symbol_ids))
        for start in range(0, len(ids), 500):
            batch = ids[start:start + 500]
            for symbol in self._select(f"s.symbol_id IN ({','.join('?' * len(batch))})", batch):
                found[symbol.symbol_id] = symbol
        return found

    def paths_with_suffix(self, suffix: str) -> List[str]:
        suffix = suffix.lstrip("./")
        rows = self.conn.execute(
            "SELECT path FROM ci_files WHERE parser_digest = ? AND (path = ? OR path LIKE ? ESCAPE '\\')",
            (self.parser_digest, suffix, "%/" + suffix.replace("%", "\\%").replace("_", "\\_"))).fetchall()
        return [r[0] for r in rows]

    def containing(self, path: str, line: int) -> List[Symbol]:
        return self._select("s.path = ? AND s.decl_start_line <= ? AND s.decl_end_line >= ?", (path, line, line))

    def body_mentions(self, term: str, limit: int, exclude_paths: Iterable[str] = ()) -> Optional[List[str]]:
        """Ids of the symbols whose body terms contain the identifier term
        ``term`` (lower-cased); None when more than ``limit`` do (not
        specific) or FTS5 is unavailable."""
        if not self.fts_available or not term:
            return None
        excluded = set(exclude_paths)
        rows = self.conn.execute("SELECT symbol_id, path FROM ci_fts WHERE ci_fts MATCH ? LIMIT ?",
                                 (f'body_terms:"{term}"', limit + 1 + len(excluded) * 50)).fetchall()
        ids = [r[0] for r in rows if r[1] not in excluded]
        return None if len(ids) > limit else ids

    def search(self, terms: Sequence[str], limit: int, exclude_paths: Iterable[str] = ()) -> List[Tuple[str, float]]:
        """BM25 over identifier terms (name terms weighted above body terms);
        (symbol_id, score>0) best first. Empty when FTS5 is unavailable."""
        if not self.fts_available or not terms:
            return []
        query = " OR ".join(f'"{t}"' for t in dict.fromkeys(terms))
        excluded = set(exclude_paths)
        rows = self.conn.execute(
            "SELECT symbol_id, path, bm25(ci_fts, 0.0, 0.0, 4.0, 1.0) AS rank FROM ci_fts WHERE ci_fts MATCH ?"
            " ORDER BY rank LIMIT ?", (query, limit + len(excluded) * 50)).fetchall()
        return [(r[0], -r[2]) for r in rows if r[1] not in excluded][:limit]


def _symbol(row: sqlite3.Row) -> Symbol:
    return Symbol(
        symbol_id=row["symbol_id"], language=row["language"], kind=row["kind"], name=row["name"],
        lookup_key=row["lookup_key"], path=row["path"], source_digest=row["source_digest"],
        declaration=_span(row, "decl"), signature=_span(row, "sig"), body=_span(row, "body"),
        parent_id=row["parent_id"], modifiers=tuple(json.loads(row["modifiers"])),
        annotations=tuple(json.loads(row["annotations"])), parameter_types=tuple(json.loads(row["parameter_types"])),
        return_type=row["return_type"], signature_text=row["signature_text"] or "",
        doc_summary=row["doc_summary"] or "", extends=tuple(json.loads(row["extends"])),
        implements=tuple(json.loads(row["implements"])),
    )
