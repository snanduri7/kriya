"""The normalized structural symbol model (Code Intelligence R1).

Every fact here is STRUCTURAL: it comes from a concrete syntax tree of the
exact raw bytes named by ``source_digest``. Nothing is type-resolved; a
qualified name is the lexical package/module path plus the enclosing
declarations, never a claim about what a reference resolves to. Semantic
enrichment (compiler, JDTLS) is a later, separately-provenanced layer.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple


class ParseState(str, Enum):
    PARSED = "PARSED"
    # The tree has error/missing nodes: symbols outside them are still real,
    # but the file is not a complete structural account.
    PARTIALLY_PARSED = "PARTIALLY_PARSED"
    UNSUPPORTED = "UNSUPPORTED"
    PARSE_FAILED = "PARSE_FAILED"


STRUCTURAL_PROVENANCE = "tree-sitter-structural"


@dataclass(frozen=True)
class Span:
    """1-indexed inclusive lines plus the exact byte range [start, end)."""

    start_line: int
    end_line: int
    start_byte: int
    end_byte: int

    def contains_line(self, line: int) -> bool:
        return self.start_line <= line <= self.end_line

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1


@dataclass(frozen=True)
class Symbol:
    """One declaration. ``symbol_id`` is durable for unchanged structure:
    language + path + qualified name + (for callables) the lexical parameter
    types, so overloads get distinct ids. ``lookup_key`` is the qualified
    name a caller searches by (several overloads share it)."""

    symbol_id: str
    language: str
    kind: str
    name: str
    lookup_key: str
    path: str
    source_digest: str
    declaration: Span
    signature: Span
    body: Optional[Span]
    parent_id: Optional[str]
    modifiers: Tuple[str, ...] = ()
    annotations: Tuple[str, ...] = ()
    parameter_types: Tuple[str, ...] = ()
    return_type: Optional[str] = None
    signature_text: str = ""
    doc_summary: str = ""
    # Supertypes as written (type arguments removed): a Java superclass or an
    # interface's extended interfaces, a Python class's statically named bases.
    extends: Tuple[str, ...] = ()
    implements: Tuple[str, ...] = ()
    provenance: str = STRUCTURAL_PROVENANCE

    @property
    def is_callable(self) -> bool:
        return self.kind in CALLABLE_KINDS

    @property
    def is_type(self) -> bool:
        return self.kind in TYPE_KINDS


TYPE_KINDS = frozenset({"class", "interface", "enum", "record", "annotation_type"})
CALLABLE_KINDS = frozenset({"method", "constructor", "function", "annotation_element"})


@dataclass(frozen=True)
class ParserIdentity:
    """Everything that can change what the structural parser produces for
    the same bytes. A stored structure under another identity is never
    reused."""

    tree_sitter: str
    java_grammar: str
    python_grammar: str
    structural_parser: str

    @property
    def digest(self) -> str:
        text = f"{self.tree_sitter}|{self.java_grammar}|{self.python_grammar}|{self.structural_parser}"
        return hashlib.sha256(text.encode()).hexdigest()


@dataclass(frozen=True)
class FileStructure:
    path: str
    language: str
    source_digest: str
    state: ParseState
    parser_digest: str
    symbols: Tuple[Symbol, ...] = ()
    imports: Tuple[str, ...] = ()
    # Java package or Python module path ("" when there is none).
    namespace: str = ""
    # Distinct invoked method names (Java method invocations), in first-use
    # order: name-based reference evidence, never a resolved call target.
    calls: Tuple[str, ...] = ()
    error_count: int = 0
    detail: str = ""

    def symbol_at_line(self, line: int, kinds: Optional[frozenset] = None) -> Optional[Symbol]:
        """The most specific (smallest) declaration containing ``line``."""
        best: Optional[Symbol] = None
        for symbol in self.symbols:
            if kinds is not None and symbol.kind not in kinds:
                continue
            if symbol.declaration.contains_line(line) and (
                best is None or symbol.declaration.line_count < best.declaration.line_count
            ):
                best = symbol
        return best


def source_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
