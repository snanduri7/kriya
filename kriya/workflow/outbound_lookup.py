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
_PUBLIC_COORDINATE_PREFIXES = (
    ("org.apache.ignite", "ignite-"),
    ("org.apache.qpid", "qpid-"),
    ("org.apache.maven.plugins", "maven-"),
    ("org.codehaus.mojo", "exec-maven-plugin"),
    ("org.springframework", "spring-"),
    ("org.junit.jupiter", "junit-"),
    ("org.hibernate", "hibernate-"),
    ("org.slf4j", "slf4j-"),
    ("com.fasterxml.jackson", "jackson-"),
    ("io.netty", "netty-"),
    ("io.micrometer", "micrometer-"),
)
_PUBLIC_TECHNOLOGY_WORDS = {
    "apache", "ignite", "qpid", "spring", "maven", "gradle", "java",
    "python", "django", "pytest", "junit", "hibernate", "jackson", "netty",
    "postgresql", "mysql", "mongodb", "redis", "kafka", "rabbitmq", "artemis",
}
_VERSION_WORD_RE = re.compile(r"v?\d+(?:\.\d+)*(?:[-._][a-z0-9]+)*$")


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


def is_known_public_term(term: str, extra_public_terms: Iterable[str] = ()) -> bool:
    normalized = sanitize_public_technology_term(term)
    lowered = normalized.lower()
    if lowered in {value.strip().lower() for value in extra_public_terms}:
        return True
    coordinate = lowered.split(":")
    if 2 <= len(coordinate) <= 3:
        group, artifact = coordinate[:2]
        return any(
            group == public_group and artifact.startswith(artifact_prefix)
            for public_group, artifact_prefix in _PUBLIC_COORDINATE_PREFIXES
        )
    words = lowered.split()
    return bool(words) and all(
        word in _PUBLIC_TECHNOLOGY_WORDS or _VERSION_WORD_RE.fullmatch(word)
        for word in words
    )


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
