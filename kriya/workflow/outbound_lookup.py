"""Fail-closed public-technology lookup requests.

This is the only value accepted by the outbound search broker. It cannot carry
canonical evidence, source code, goals, paths, or raw diagnostics.
"""
from dataclasses import dataclass
import re
from typing import Iterable, Tuple


_PUBLIC_TERM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:+@-]{0,127}$")
_SOURCE_SUFFIXES = (
    ".java", ".py", ".js", ".ts", ".xml", ".yaml", ".yml", ".properties",
    ".cs", ".go", ".rb", ".kt", ".scala",
)


class UnsafeLookupTerm(ValueError):
    pass


def sanitize_public_technology_term(term: str) -> str:
    if not isinstance(term, str):
        raise UnsafeLookupTerm("lookup terms must be plain public-technology strings")
    normalized = " ".join(term.strip().split())
    if not normalized or not _PUBLIC_TERM_RE.fullmatch(normalized):
        raise UnsafeLookupTerm("lookup term contains path, code, or diagnostic characters")
    if len(normalized.split()) > 6:
        raise UnsafeLookupTerm("lookup term contains too much free-form text")
    if normalized.lower().endswith(_SOURCE_SUFFIXES):
        raise UnsafeLookupTerm("source filenames are never valid outward lookup terms")
    return normalized


@dataclass(frozen=True)
class OutboundLookupRequest:
    terms: Tuple[str, ...]
    origin: str

    @classmethod
    def from_extracted_terms(cls, terms: Iterable[str], *, origin: str) -> "OutboundLookupRequest":
        safe = []
        for term in terms:
            normalized = sanitize_public_technology_term(term)
            if normalized not in safe:
                safe.append(normalized)
        return cls(terms=tuple(safe), origin=origin)

    def queries(self) -> Tuple[str, ...]:
        return tuple(f"{term} example" for term in self.terms)
