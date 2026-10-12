"""BACKEND-READINESS-V2 (S4_A false success): the candidate-semantic safety rule for symptom-masking exception handling.

A repair that makes a crash disappear by catching the catastrophic exception class that raised it (``java.lang.Error`` /
``Throwable`` or an ``Error`` subclass such as ``StackOverflowError``) does not fix the defect: the failing path still runs
(measured: the S4_A candidate's ``catch (Throwable e)`` caught the very ``StackOverflowError`` the goal named, once per
input, then returned normally). Tests that only observe "no error escapes" accept it, so the rule is a deterministic gate
on the candidate DELTA, run on every attempt including repairs, before any compile or test gate.

Decision (language: Java; the only language with a measured instance):

- a *catch clause* is read from the comment- and string-blanked source: the caught type names, the variable, the body;
- a clause is *catastrophic* when any caught type is ``Throwable``, ``Error`` or a known/``*Error``-named ``Error`` subtype
  (``AssertionError`` is exempt in test-side files, where catching it is the standard "expected failure" idiom);
- a clause *propagates* when its body ends, at top level, in ``throw <caught variable>;`` or ``throw new <Error subtype>(...)``
  and contains no ``return``/``break``/``continue``/``yield`` anywhere (every normal exit is then a rethrow of an Error).
  Anything else - swallowing, returning, logging, or converting the Error into a recoverable ``Exception`` - masks it;
- only the delta counts: the candidate is judged by masking clauses it has MORE of, per catastrophic type-set, than the
  file before this write. An unchanged pre-existing ``catch (Throwable ...)`` is never a finding by presence.

No contract source exists today that could permit swallowing an Error, so there is no permission input (fail closed); a
future contract would have to be an operator-authority input, never model text.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Tuple

from kriya.workflow.edit_safety import StagedFileWrite, strip_java_comments_and_strings
from kriya.workflow.named_test_oracle import _is_test_side

UNSAFE_EXCEPTION_MASKING = "UNSAFE_EXCEPTION_MASKING"

_ERROR_NAMES = frozenset({
    "Throwable", "Error", "VirtualMachineError", "StackOverflowError", "OutOfMemoryError", "InternalError",
    "UnknownError", "LinkageError", "NoClassDefFoundError", "ExceptionInInitializerError", "ThreadDeath",
    "AssertionError", "UnsatisfiedLinkError", "AbstractMethodError", "IncompatibleClassChangeError",
    "NoSuchMethodError", "NoSuchFieldError", "ClassFormatError", "VerifyError", "BootstrapMethodError",
})
_CATCH_RE = re.compile(r"\bcatch\s*\(\s*(?:final\s+)?([A-Za-z_][\w.\s|]*?)\s+([A-Za-z_]\w*)\s*\)\s*\{")
_EXIT_RE = re.compile(r"\b(?:return|break|continue|yield)\b")
_THROW_NEW_RE = re.compile(r"^throw\s+new\s+([A-Za-z_][\w.]*)\s*\(", re.S)


@dataclass(frozen=True)
class MaskingClause:
    types: Tuple[str, ...]
    variable: str
    line: int
    snippet: str


def _simple(name: str) -> str:
    return name.strip().rsplit(".", 1)[-1]


def _is_error_type(name: str) -> bool:
    simple = _simple(name)
    return simple in _ERROR_NAMES or simple.endswith("Error")


def _is_catastrophic(name: str, test_side: bool) -> bool:
    """A caught type that is judged. ``AssertionError`` is exempt in test files (catching it is the "expected failure" idiom)."""
    return _is_error_type(name) and not (_simple(name) == "AssertionError" and test_side)


def _body(stripped: str, open_index: int) -> Optional[str]:
    depth = 0
    for index in range(open_index, len(stripped)):
        if stripped[index] == "{":
            depth += 1
        elif stripped[index] == "}":
            depth -= 1
            if depth == 0:
                return stripped[open_index + 1:index]
    return None


def _top_level_statements(body: str) -> List[str]:
    statements, depth, current = [], 0, []
    for char in body:
        current.append(char)
        if char in "{(":
            depth += 1
        elif char in "})":
            depth -= 1
        if depth == 0 and char in ";}":
            text = "".join(current).strip()
            if text and text != ";":
                statements.append(text)
            current = []
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def _propagates(body: str, variable: str) -> bool:
    if _EXIT_RE.search(body):
        return False
    statements = _top_level_statements(body)
    if not statements:
        return False
    last = " ".join(statements[-1].split())
    if last == f"throw {variable};":
        return True
    match = _THROW_NEW_RE.match(last)
    return bool(match and _is_error_type(match.group(1)))


def masking_clauses(path: str, source: str) -> List[MaskingClause]:
    """Every catastrophic catch clause of ``source`` that does not propagate the Error."""
    if os.path.splitext(path)[1].lower() != ".java" or not source:
        return []
    test_side = _is_test_side(path)
    stripped = strip_java_comments_and_strings(source)
    found: List[MaskingClause] = []
    for match in _CATCH_RE.finditer(stripped):
        types = tuple(sorted(_simple(t) for t in match.group(1).split("|") if t.strip()))
        if not any(_is_catastrophic(t, test_side) for t in types):
            continue
        body = _body(stripped, match.end() - 1)
        if body is None or _propagates(body, match.group(2)):
            continue
        line = stripped.count("\n", 0, match.start()) + 1
        found.append(MaskingClause(types, match.group(2), line, " ".join(source.splitlines()[line - 1].split())[:160]))
    return found


def find_unsafe_exception_masking(path: str, content: str, baseline: Optional[str] = None) -> Optional[str]:
    """Description of the first masking catch clause the candidate ADDED relative to ``baseline`` (None = no baseline: a new file, or an in-place run where the
    pre-run text is unavailable; then every masking clause counts), or None. Counted per sorted caught-type tuple so a pre-existing clause is never a finding by presence,
    while widening ``Exception`` to ``Throwable`` (a new ``Throwable`` clause) is."""
    candidate = masking_clauses(path, content)
    if not candidate:
        return None
    existing = Counter(clause.types for clause in masking_clauses(path, baseline or ""))
    for clause in candidate:
        if existing[clause.types] > 0:
            existing[clause.types] -= 1
            continue
        return (f"{UNSAFE_EXCEPTION_MASKING}: line {clause.line} newly catches {'|'.join(clause.types)} "
                f"without rethrowing it as an Error: `{clause.snippet}`")
    return None


def find_masking_in_batch(
    writes: Iterable[StagedFileWrite], baseline_for: Callable[[StagedFileWrite], Optional[str]],
) -> List[Tuple[str, str]]:
    """The rule on a FINAL batch (what a commit will write), whatever route produced its bytes: ``(target_path,
    description)`` for every non-deleted write that newly masks, against ``baseline_for(write)`` (the text the write
    replaces, None for a new file). The terminal gates of both commit boundaries use this one decision."""
    findings: List[Tuple[str, str]] = []
    for write in writes:
        if write.delete:
            continue
        found = find_unsafe_exception_masking(write.target_path, write.content, baseline_for(write))
        if found:
            findings.append((write.target_path, found))
    return findings
