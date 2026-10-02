"""Deterministic localization signals and fusion.

A localization query (a goal, a compiler error, a stack trace) is reduced to
typed signals by regular extraction, never by a model. Each signal is
answered by an exact structural channel; fusion is a fixed tiered score, so
exact evidence (a failing line inside a member, a qualified name) always
outranks lexical similarity. Every hit carries the channels that produced it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# Channel weights. The tiers are separated so a weaker channel can never
# outvote an exact one: the best possible lexical score (FTS_MAX) is below a
# single exact symbol hit.
FILE_LINE = 100.0
QUALIFIED_EXACT = 80.0
QUALIFIED_SUFFIX = 70.0
SIMPLE_SYMBOL = 40.0
STRING_LITERAL = 30.0
OWNER_NAMED = 15.0
PATH_MENTIONED = 12.0
FTS_MAX = 10.0
# The vector channel (semantic similarity of the indexed chunk holding a
# member) shares the similarity tier with FTS: together they stay below a
# unique exact symbol, so agreement of the two weak legs can promote a member
# but never outvote exact evidence.
VECTOR_MAX = 10.0
# The whole similarity tier (BM25 + vector agreement); evidence from outside
# the structural index (the legacy hybrid leg) is scaled into it.
SIMILARITY_MAX = FTS_MAX + VECTOR_MAX
# Vector rank decay: rank r (0-based) of the query's chunk hits weighs
# VECTOR_MAX * VECTOR_RANK_K / (VECTOR_RANK_K + r) - cosines of one model are
# comparable only within one query, so the rank, not the raw cosine, counts.
VECTOR_RANK_K = 10.0
# Chunks the vector channel reads per query.
SEMANTIC_CHUNKS = 40
# An identifier matching more symbols than this is not specific evidence.
MAX_SIMPLE_MATCHES = 25
# A plain prose word matching a callable name counts this fraction of a
# code-shaped identifier.
PROSE_FACTOR = 0.5
# Lexical credit for test code when the query does not talk about tests:
# a described behavior change targets product code first.
TEST_CODE_FACTOR = 0.5
_TEST_PATH_RE = re.compile(r"(^|/)(src/test|tests?)/|(^|/)test_[^/]*\.py$|Tests?\.java$")
_TEST_WORD_RE = re.compile(r"(?i)\btests?\b|\btest[A-Z_]|Test\b|assert")
# A type the query names scopes its own members (OWNER_NAMED); the members
# that construct it (``new T(``, ``T(``/``raise T``) decide what its instances
# carry - an exception's message, a value object's fields - and share that
# scope tier, divided among the construction sites (specificity).
CONSTRUCTION_SITE = OWNER_NAMED
# Members whose bodies mention a named type that are read to find its
# construction sites; a type mentioned more widely is not specific evidence.
MAX_TYPE_MENTIONS = 200
# A top hit at or above this score rests on exact evidence.
EXACT_EVIDENCE = QUALIFIED_SUFFIX

_EXTENSIONS = r"(?:java|py|xml|properties|ya?ml|kt|scala|groovy)"
_PY_TRACE_RE = re.compile(r'File "([^"]+)", line (\d+)')
_JAVA_FRAME_RE = re.compile(r"\bat\s+((?:[\w$]+\.)+)([\w$<>]+)\(([\w$]+\.java):(\d+)\)")
_PATH_LINE_RE = re.compile(r"([\w./\\-]+\." + _EXTENSIONS + r")(?::\[?|\[|\()(\d+)")
_PATH_RE = re.compile(r"(?<![\w.])((?:[\w-]+/)*[\w$-]+\." + _EXTENSIONS + r")\b")
_QUALIFIED_RE = re.compile(r"\b([A-Za-z_][\w$]*(?:(?:\.|#|::)[A-Za-z_][\w$]*)+)(?:\(\))?")
_WORD_RE = re.compile(r"`?\b([A-Za-z_][\w$]{2,})\b`?")
_STRING_RE = re.compile(r'"([^"\n]{6,120})"')
_STOP = frozenset("""
the and for with from that this when into are not but can has have was were will should would could
fix fixes fixed add adds added use uses used update updates updated remove removes removed make makes
support supports new old more less test tests javadoc doc docs comment comments typo typos minor code
method methods class classes function functions file files value values return returns null true false
""".split())


@dataclass(frozen=True)
class Signals:
    file_lines: Tuple[Tuple[str, int], ...] = ()
    frames: Tuple[Tuple[str, str, int], ...] = ()  # (qualified class, method, line)
    paths: Tuple[str, ...] = ()
    qualified: Tuple[str, ...] = ()
    words: Tuple[str, ...] = ()
    strings: Tuple[str, ...] = ()


def extract_signals(text: str) -> Signals:
    file_lines: List[Tuple[str, int]] = [(p, int(n)) for p, n in _PY_TRACE_RE.findall(text)]
    frames = [(owner.rstrip("."), method, int(line)) for owner, method, _file, line in _JAVA_FRAME_RE.findall(text)]
    framed = {m.span() for m in _JAVA_FRAME_RE.finditer(text)}
    for match in _PATH_LINE_RE.finditer(text):
        if not any(a <= match.start() < b for a, b in framed):
            file_lines.append((match.group(1).replace("\\", "/"), int(match.group(2))))
    paths = [p for p in _PATH_RE.findall(text)]
    qualified = []
    for match in _QUALIFIED_RE.finditer(text):
        name = re.sub(r"#|::", ".", match.group(1))
        if re.search(r"\." + _EXTENSIONS + r"$", name) or any(a <= match.start() < b for a, b in framed):
            continue
        qualified.append(name)
    words = [w for w in _WORD_RE.findall(text) if w.lower() not in _STOP]
    return Signals(tuple(dict.fromkeys(file_lines)), tuple(dict.fromkeys(frames)), tuple(dict.fromkeys(paths)),
                   tuple(dict.fromkeys(qualified)), tuple(dict.fromkeys(words)),
                   tuple(dict.fromkeys(_STRING_RE.findall(text))))


@dataclass
class Evidence:
    """Accumulates channel scores for one symbol."""

    score: float = 0.0
    channels: Dict[str, float] = field(default_factory=dict)

    def add(self, channel: str, weight: float) -> None:
        # One contribution per channel: repeated mentions are not stronger evidence.
        if weight > self.channels.get(channel, 0.0):
            self.score += weight - self.channels.get(channel, 0.0)
            self.channels[channel] = weight


@dataclass(frozen=True)
class LocateHit:
    symbol_id: str
    path: str
    lookup_key: str
    kind: str
    score: float
    channels: Tuple[Tuple[str, float], ...]

    @property
    def exact(self) -> bool:
        return self.score >= EXACT_EVIDENCE


def rank(evidence: Dict[str, Evidence], meta: Dict[str, Tuple[str, str, str]], limit: int) -> List[LocateHit]:
    """Deterministic order: score, then symbol id."""
    ordered = sorted(evidence.items(), key=lambda item: (-item[1].score, item[0]))[:limit]
    return [LocateHit(symbol_id, *meta[symbol_id], round(ev.score, 4),
                      tuple(sorted(ev.channels.items(), key=lambda c: (-c[1], c[0]))))
            for symbol_id, ev in ordered]


def is_test_path(path: str) -> bool:
    return bool(_TEST_PATH_RE.search(path))


def mentions_tests(text: str) -> bool:
    return bool(_TEST_WORD_RE.search(text))


def is_prose_word(word: str) -> bool:
    """All lower-case letters: an English word as much as an identifier."""
    return word.isalpha() and word.islower()


def constructs(language: str, type_name: str, body: str) -> bool:
    """``body`` constructs ``type_name``: Java ``new [pkg.]T(`` / ``new T<``;
    Python a call ``[mod.]T(`` or ``raise [mod.]T`` (never its ``class``/``def``
    line, an ``isinstance``/``except`` reference or an annotation)."""
    name = re.escape(type_name)
    if language == "java":
        return re.search(r"\bnew\s+(?:[\w$]+\.)*" + name + r"\s*[(<]", body) is not None
    if language == "python":
        for match in re.finditer(r"(?<![\w])(?:raise\s+)?(?:\w+\.)*" + name + r"\b(\s*\()?", body):
            line_start = body.rfind("\n", 0, match.start()) + 1
            before = body[line_start:match.start()]
            if re.search(r"\b(?:class|def)\s+$", before):
                continue
            if match.group(1) or match.group(0).startswith("raise"):
                return True
    return False


def semantic_weight(rank: int) -> float:
    return VECTOR_MAX * VECTOR_RANK_K / (VECTOR_RANK_K + rank)


def specificity(count: int) -> Optional[float]:
    if count == 0 or count > MAX_SIMPLE_MATCHES:
        return None
    return 1.0 / count
