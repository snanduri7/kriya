"""PRD-025: bounded, deterministic runtime-verifier evidence.

The Run Verification grader (``RunVerifierAgent.grade``) never receives raw
captured output. It gets a ``VerifierEvidencePackage``, built from the
retained capture and sized for the model that will read it. Building one
does three things:

* **Deterministic facts first.** Every step's command, exit status and
  timeout state is always shown.
* **One scan of the whole retained capture.** A single compiled pass looks
  for Kriya verification markers, assertions, exceptions, tracebacks and
  fatal/error signatures. A signature in the middle of a huge log is found
  exactly like one at either end.
* **Explicit truncation.** Two kinds are never conflated:
  - CAPTURE_TRUNCATION: ProcessController dropped bytes before Kriya
    retained them. Those bytes were never scanned, and the package says so.
  - PACKAGE_TRUNCATION: Kriya retained the bytes but omitted some of them
    from the package. The omitted ranges are listed.

Authority stays deterministic. A grader PASS becomes UNKNOWN when a decisive
window could not fit (``finalize_semantic_verdict``), or when capture loss
could hide decisive evidence. ``apply_runtime_disposition`` keeps a nonzero
exit, a timeout or a deterministic failure authoritative over any semantic
grade. There is one exception: the process launched normally, and the
user's own goal text declares a nonzero exit as the expected behaviour
(e.g. "invalid input exits non-zero").
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

EVIDENCE_EXTRACTION_VERSION = "verifier-evidence/1"


class RuntimeVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class TruncationKind(str, Enum):
    CAPTURE_TRUNCATION = "CAPTURE_TRUNCATION"
    PACKAGE_TRUNCATION = "PACKAGE_TRUNCATION"


# Semantic (grader) reason codes.
VERIFIER_CONFIRMED = "VERIFIER_CONFIRMED"
VERIFIER_REPORTED_FAILURE = "VERIFIER_REPORTED_FAILURE"
VERIFIER_REPORTED_UNKNOWN = "VERIFIER_REPORTED_UNKNOWN"
VERIFIER_CALL_FAILED = "VERIFIER_CALL_FAILED"
VERIFIER_RESULT_MALFORMED = "VERIFIER_RESULT_MALFORMED"
DECISIVE_EVIDENCE_OMITTED = "DECISIVE_EVIDENCE_OMITTED"
CAPTURE_LOSS_UNRESOLVED = "CAPTURE_LOSS_UNRESOLVED"
# Deterministic (runtime) reason codes.
TIMEOUT_AUTHORITATIVE = "TIMEOUT_AUTHORITATIVE"
NONZERO_EXIT_AUTHORITATIVE = "NONZERO_EXIT_AUTHORITATIVE"
EXPECTED_NONZERO_EXIT_GROUNDED = "EXPECTED_NONZERO_EXIT_GROUNDED"
DETERMINISTIC_PROCESS_EXIT = "DETERMINISTIC_PROCESS_EXIT"

# Signature kinds, in window-priority order. Every kind except ``error`` is
# decisive: failing to show one to the grader forbids a PASS. Generic error
# log lines are routine in passing applications, so they are shown when
# room allows but their omission is not decisive.
_DECISIVE_KINDS = ("kriya_marker", "assertion", "traceback", "exception", "fatal")
_KIND_PRIORITY = {kind: index for index, kind in enumerate(_DECISIVE_KINDS + ("error",))}

_SIGNATURE_RE = re.compile(
    r"(?P<kriya_marker>\[VERIFICATION\]\s*(?:PASS|FAIL)\b)"
    r"|(?P<assertion>\bAssertionError\b|\bassert(?:ion)?\s+failed\b|\bexpected:\s*<[^\n]*>\s*but\s+was:\s*<)"
    r"|(?P<traceback>^Traceback \(most recent call last\):)"
    r"|(?P<exception>^Exception in thread \"|^\s*Caused by: "
    r"|^(?:[a-z_$][\w$]*\.)+[A-Z][\w$]*(?:Exception|Error)\b|^[A-Z]\w*(?:Error|Exception): )"
    r"|(?P<fatal>^\s*(?:FATAL\b|panic:|Segmentation fault|Fatal error)|\bOutOfMemoryError\b|\bBUILD FAILURE\b)"
    r"|(?P<error>^\s*(?:\[ERROR\]|ERROR[:\s]|error:))",
    re.MULTILINE,
)
# Lines of context kept (before, after) around a match, per kind.
_WINDOW_LINES = {
    "kriya_marker": (2, 2), "assertion": (3, 8), "traceback": (1, 30),
    "exception": (2, 15), "fatal": (3, 6), "error": (1, 2),
}
_WINDOW_MAX_CHARS = 2400
# Distinct signatures considered per stream, so a pathological log cannot
# make the scan's output unbounded. A decisive signature beyond the cap is
# counted as omitted.
_MAX_DISTINCT_SIGNATURES = 64
_GAP_MARKER_CHARS = 96
_HEAD_SHARE = 1 / 3
_NORMALIZE_RE = re.compile(r"0x[0-9a-fA-F]+|\d+")


@dataclass(frozen=True)
class SignatureWindow:
    step: int
    stream: str
    kind: str
    start: int
    end: int
    occurrences: int

    @property
    def decisive(self) -> bool:
        return self.kind in _DECISIVE_KINDS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step": self.step, "stream": self.stream, "kind": self.kind,
            "start": self.start, "end": self.end, "occurrences": self.occurrences,
        }


@dataclass(frozen=True)
class RetainedStream:
    step: int
    name: str
    text: str
    lost_chars: int
    windows: Tuple[SignatureWindow, ...]
    signature_counts: Tuple[Tuple[str, int], ...]
    distinct_overflow: int


@dataclass(frozen=True)
class RetainedStep:
    index: int
    command: Tuple[str, ...]
    exit_code: Optional[int]
    timed_out: bool
    streams: Tuple[RetainedStream, ...]


@dataclass(frozen=True)
class RetainedRuntimeEvidence:
    """Everything Kriya retained from one runtime-verification execution,
    scanned exactly once. Immutable, so the package for a fallback grader is
    rebuilt from the very same evidence the primary's package came from."""

    steps: Tuple[RetainedStep, ...]
    returncode: Optional[int]
    timed_out: bool

    @property
    def capture_lost_chars(self) -> int:
        return sum(stream.lost_chars for step in self.steps for stream in step.streams)

    @property
    def retained_chars(self) -> int:
        return sum(len(stream.text) for step in self.steps for stream in step.streams)

    @classmethod
    def from_run_result(cls, run_result: Dict[str, Any]) -> "RetainedRuntimeEvidence":
        steps = run_result.get("steps") or []
        has_stream_text = any("stdout" in step or "stderr" in step for step in steps)
        if not steps or not has_stream_text:
            return cls.from_output(
                run_result.get("output") or "", run_result.get("returncode"),
                bool(run_result.get("timed_out")),
            )
        return cls(
            steps=tuple(
                _retain_step(
                    index, step.get("command") or (), step.get("exit_code"), bool(step.get("timed_out")),
                    (
                        ("stdout", step.get("stdout") or "", int(step.get("stdout_lost_chars") or 0)),
                        ("stderr", step.get("stderr") or "", int(step.get("stderr_lost_chars") or 0)),
                    ),
                )
                for index, step in enumerate(steps, 1)
            ),
            returncode=run_result.get("returncode"),
            timed_out=bool(run_result.get("timed_out")),
        )

    @classmethod
    def from_output(
        cls, output: str, returncode: Optional[int], timed_out: bool = False,
    ) -> "RetainedRuntimeEvidence":
        """For a caller that only has the combined output string."""
        return cls(
            steps=(_retain_step(1, (), returncode, timed_out, (("output", output or "", 0),)),),
            returncode=returncode, timed_out=timed_out,
        )


def _line_start(text: str, position: int, lines_before: int) -> int:
    start = text.rfind("\n", 0, position) + 1
    for _ in range(lines_before):
        if start == 0:
            break
        start = text.rfind("\n", 0, start - 1) + 1
    return start


def _line_end(text: str, position: int, lines_after: int) -> int:
    end = position
    for _ in range(lines_after + 1):
        newline = text.find("\n", end)
        if newline == -1:
            return len(text)
        end = newline + 1
    return end


def _signature_key(kind: str, text: str, start: int) -> Tuple[str, str]:
    line_end = text.find("\n", start)
    line = text[start: line_end if line_end != -1 else len(text)].strip()
    return kind, _NORMALIZE_RE.sub("#", line)[:200]


def _scan_stream(step: int, name: str, text: str) -> Tuple[Tuple[SignatureWindow, ...], Tuple[Tuple[str, int], ...], int]:
    """One pass over ``text``. Repeats of the same normalized signature line
    collapse into their first and last occurrence with a count, so a looping
    log does not flood the package with copies."""
    counts: Dict[str, int] = {}
    first: Dict[Tuple[str, str], Tuple[int, int]] = {}
    last: Dict[Tuple[str, str], int] = {}
    overflow = 0
    for match in _SIGNATURE_RE.finditer(text):
        kind = match.lastgroup or "error"
        counts[kind] = counts.get(kind, 0) + 1
        line_start = text.rfind("\n", 0, match.start()) + 1
        key = _signature_key(kind, text, line_start)
        if key in first:
            position, seen = first[key]
            first[key] = (position, seen + 1)
            last[key] = match.start()
            continue
        if len(first) >= _MAX_DISTINCT_SIGNATURES:
            # Only a decisive signature that could not even be considered
            # blocks a PASS; surplus routine error lines do not.
            if kind in _DECISIVE_KINDS:
                overflow += 1
            continue
        first[key] = (match.start(), 1)
    windows: List[SignatureWindow] = []
    for key, (position, occurrences) in first.items():
        positions = [position]
        if key in last:
            positions.append(last[key])
        for at in positions:
            before, after = _WINDOW_LINES[key[0]]
            start = _line_start(text, at, before)
            end = min(_line_end(text, at, after), start + _WINDOW_MAX_CHARS)
            windows.append(SignatureWindow(step, name, key[0], start, end, occurrences))
    windows.sort(key=lambda window: (_KIND_PRIORITY[window.kind], window.start))
    return tuple(windows), tuple(sorted(counts.items())), overflow


def _retain_step(
    index: int, command: Sequence[str], exit_code: Optional[int], timed_out: bool,
    streams: Sequence[Tuple[str, str, int]],
) -> RetainedStep:
    retained = []
    for name, text, lost in streams:
        windows, counts, overflow = _scan_stream(index, name, text)
        retained.append(RetainedStream(index, name, text, lost, windows, counts, overflow))
    return RetainedStep(index, tuple(command), exit_code, timed_out, tuple(retained))


@dataclass(frozen=True)
class VerifierEvidencePackage:
    rendered: str
    model: str
    budget_bytes: int
    package_truncated: bool
    included_windows: Tuple[SignatureWindow, ...]
    omitted_windows: Tuple[SignatureWindow, ...]
    omitted_ranges: Tuple[Dict[str, Any], ...]
    head_tail: Tuple[Dict[str, Any], ...]
    capture_truncation: Tuple[Dict[str, Any], ...]
    distinct_signature_overflow: int

    @property
    def decisive_windows_omitted(self) -> bool:
        return (
            any(window.decisive for window in self.omitted_windows)
            or self.distinct_signature_overflow > 0
        )

    @property
    def capture_truncated(self) -> bool:
        return bool(self.capture_truncation)

    def to_dict(self) -> Dict[str, Any]:
        truncations = []
        if self.capture_truncated:
            truncations.append(TruncationKind.CAPTURE_TRUNCATION.value)
        if self.package_truncated:
            truncations.append(TruncationKind.PACKAGE_TRUNCATION.value)
        return {
            "extraction_version": EVIDENCE_EXTRACTION_VERSION,
            "model": self.model,
            "budget_bytes": self.budget_bytes,
            "rendered_bytes": len(self.rendered.encode("utf-8")),
            "rendered_chars": len(self.rendered),
            "rendered_sha256": hashlib.sha256(self.rendered.encode("utf-8")).hexdigest(),
            "truncation": truncations,
            "capture_truncation": list(self.capture_truncation),
            "package_truncated": self.package_truncated,
            "omitted_ranges": list(self.omitted_ranges),
            "included_windows": [window.to_dict() for window in self.included_windows],
            "omitted_windows": [window.to_dict() for window in self.omitted_windows],
            "decisive_windows_omitted": self.decisive_windows_omitted,
            "distinct_signature_overflow": self.distinct_signature_overflow,
            "head_tail": list(self.head_tail),
        }


def _step_header(step: RetainedStep, total: int) -> str:
    command = " ".join(step.command) if step.command else "(combined output)"
    lines = [
        f"=== Step {step.index}/{total}: {command} ===",
        f"exit_code={step.exit_code} timed_out={str(step.timed_out).lower()}",
    ]
    for stream in step.streams:
        if stream.lost_chars:
            lines.append(
                f"[{TruncationKind.CAPTURE_TRUNCATION.value}: {stream.lost_chars} earlier {stream.name} "
                "characters were lost before Kriya retained this output; they were never scanned "
                "and are not evidence of anything]"
            )
        if stream.signature_counts:
            summary = ", ".join(f"{kind}={count}" for kind, count in stream.signature_counts)
            lines.append(f"[{stream.name} signatures in all retained output: {summary}]")
    return "\n".join(lines) + "\n"


def _merge(ranges: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    merged: List[Tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _covered(ranges: List[Tuple[int, int]]) -> int:
    return sum(end - start for start, end in _merge(ranges))


def _render_stream(stream: RetainedStream, ranges: List[Tuple[int, int]]) -> Tuple[str, List[Dict[str, Any]]]:
    text = stream.text
    if not text:
        return "", []
    parts = [f"--- {stream.name} ---\n"]
    omitted: List[Dict[str, Any]] = []
    cursor = 0
    for start, end in _merge(ranges):
        if start > cursor:
            omitted.append({"step": stream.step, "stream": stream.name, "start": cursor, "end": start})
            parts.append(
                f"\n[{TruncationKind.PACKAGE_TRUNCATION.value}: Kriya omitted {stream.name} characters "
                f"{cursor}-{start} ({start - cursor} chars) from this package]\n"
            )
        parts.append(text[start:end])
        cursor = end
    if cursor < len(text):
        omitted.append({"step": stream.step, "stream": stream.name, "start": cursor, "end": len(text)})
        parts.append(
            f"\n[{TruncationKind.PACKAGE_TRUNCATION.value}: Kriya omitted {stream.name} characters "
            f"{cursor}-{len(text)} ({len(text) - cursor} chars) from this package]\n"
        )
    parts.append("\n")
    return "".join(parts), omitted


def build_verifier_evidence_package(
    evidence: RetainedRuntimeEvidence, budget_chars: int, *, model: str,
) -> VerifierEvidencePackage:
    """Pack ``evidence`` into at most ``budget_chars`` characters: step
    headers, then decisive signature windows in priority order, then head
    and tail samples of every stream in proportion to its length. When
    everything fits, the whole retained capture is sent verbatim."""
    budget = max(0, int(budget_chars))
    total_steps = len(evidence.steps)
    headers = {step.index: _step_header(step, total_steps) for step in evidence.steps}
    streams = [stream for step in evidence.steps for stream in step.streams]
    capture_truncation = tuple(
        {"step": stream.step, "stream": stream.name, "lost_chars": stream.lost_chars}
        for stream in streams if stream.lost_chars
    )
    overflow = sum(stream.distinct_overflow for stream in streams)
    header_cost = sum(len(header) for header in headers.values())
    stream_label_cost = sum(len(stream.name) + 10 for stream in streams if stream.text)
    full_cost = header_cost + stream_label_cost + sum(len(stream.text) for stream in streams)

    selected: Dict[Tuple[int, str], List[Tuple[int, int]]] = {
        (stream.step, stream.name): [] for stream in streams
    }
    included: List[SignatureWindow] = []
    omitted_windows: List[SignatureWindow] = []
    head_tail: List[Dict[str, Any]] = []
    truncated = full_cost > budget
    if not truncated:
        for stream in streams:
            selected[(stream.step, stream.name)].append((0, len(stream.text)))
            included.extend(stream.windows)
    else:
        used = header_cost + stream_label_cost
        windows = sorted(
            (window for stream in streams for window in stream.windows),
            key=lambda window: (_KIND_PRIORITY[window.kind], window.step, window.start),
        )
        for window in windows:
            key = (window.step, window.stream)
            before = _covered(selected[key])
            after = _covered(selected[key] + [(window.start, window.end)])
            cost = after - before + (_GAP_MARKER_CHARS if after > before else 0)
            if used + cost <= budget:
                selected[key].append((window.start, window.end))
                used += cost
                included.append(window)
            else:
                omitted_windows.append(window)
        # Head/tail samples share the remaining room in proportion to each
        # stream's length; the tail gets the larger share because a process
        # usually reports its outcome last.
        remaining = max(0, budget - used - _GAP_MARKER_CHARS * 2 * len(streams))
        total_text = sum(len(stream.text) for stream in streams) or 1
        for stream in streams:
            if not stream.text:
                continue
            share = int(remaining * len(stream.text) / total_text)
            head = min(len(stream.text), int(share * _HEAD_SHARE))
            tail = min(len(stream.text) - head, share - head)
            ranges = selected[(stream.step, stream.name)]
            if head > 0:
                ranges.append((0, head))
            if tail > 0:
                ranges.append((len(stream.text) - tail, len(stream.text)))
            head_tail.append({"step": stream.step, "stream": stream.name, "head_chars": head, "tail_chars": tail})

    parts: List[str] = []
    omitted_ranges: List[Dict[str, Any]] = []
    for step in evidence.steps:
        parts.append(headers[step.index])
        for stream in step.streams:
            rendered, omitted = _render_stream(stream, selected[(stream.step, stream.name)])
            parts.append(rendered)
            omitted_ranges.extend(omitted)
    return VerifierEvidencePackage(
        rendered="".join(parts),
        model=model,
        budget_bytes=budget,
        package_truncated=bool(omitted_ranges),
        included_windows=tuple(included),
        omitted_windows=tuple(omitted_windows),
        omitted_ranges=tuple(omitted_ranges),
        head_tail=tuple(head_tail),
        capture_truncation=capture_truncation,
        distinct_signature_overflow=overflow,
    )


def build_package_for_budget(
    evidence: RetainedRuntimeEvidence, budget_bytes: int, *, model: str,
) -> VerifierEvidencePackage:
    """``build_verifier_evidence_package`` sized in UTF-8 bytes (the unit
    of the PRD-016 dispatch check). Non-ASCII output packs fewer
    characters, so the character budget shrinks until the rendered package
    fits (bounded to three passes)."""
    limit = max(0, int(budget_bytes))
    budget = limit
    package = build_verifier_evidence_package(evidence, budget, model=model)
    for _ in range(3):
        size = len(package.rendered.encode("utf-8"))
        if size <= limit or budget == 0:
            break
        budget = max(0, budget - (size - limit) - _GAP_MARKER_CHARS)
        package = build_verifier_evidence_package(evidence, budget, model=model)
    return package


def finalize_semantic_verdict(
    reported: RuntimeVerdict, package: VerifierEvidencePackage,
) -> Tuple[RuntimeVerdict, str]:
    """Deterministic ceiling on the grader's own verdict. A reported PASS
    stands only when the grader saw every decisive window and no capture
    loss could be hiding decisive evidence. Package truncation of
    non-decisive text alone never demotes a PASS."""
    if reported is RuntimeVerdict.FAIL:
        return RuntimeVerdict.FAIL, VERIFIER_REPORTED_FAILURE
    if reported is RuntimeVerdict.UNKNOWN:
        return RuntimeVerdict.UNKNOWN, VERIFIER_REPORTED_UNKNOWN
    if package.decisive_windows_omitted:
        return RuntimeVerdict.UNKNOWN, DECISIVE_EVIDENCE_OMITTED
    if package.capture_truncated:
        return RuntimeVerdict.UNKNOWN, CAPTURE_LOSS_UNRESOLVED
    return RuntimeVerdict.PASS, VERIFIER_CONFIRMED


def parse_reported_verdict(parsed: Dict[str, Any], passed: bool) -> RuntimeVerdict:
    """The grader's ``verdict`` field, falling back to its legacy ``passed``
    boolean. An unrecognized verdict string is UNKNOWN, never PASS."""
    raw = parsed.get("verdict")
    if raw is None:
        return RuntimeVerdict.PASS if passed else RuntimeVerdict.FAIL
    try:
        return RuntimeVerdict(str(raw).strip().upper())
    except ValueError:
        return RuntimeVerdict.UNKNOWN


_EXPECTED_NONZERO_EXIT_RE = re.compile(
    r"\bnon-?zero\s+(?:exit|return|status)"
    r"|\bexit(?:s|ed|ing)?\s+(?:with\s+)?(?:an?\s+)?(?:non-?zero|error|failure)\b"
    r"|\b(?:exit|return)\s+(?:code|status)\s+(?:of\s+|is\s+|=\s*)?(?:[1-9]\d*|non-?zero)\b"
    r"|\bexit(?:s|ed|ing)?\s+(?:with\s+)?(?:code|status)\s+[1-9]\d*\b",
    re.IGNORECASE,
)
_NEGATION_RE = re.compile(r"\b(?:not|never|without|no)\b|n't\b", re.IGNORECASE)


def goal_declares_expected_nonzero_exit(goal_text: str) -> bool:
    """Whether the user's own goal text states that a nonzero exit is the
    expected behaviour. A negated mention nearby ("must not exit non-zero")
    does not count."""
    for match in _EXPECTED_NONZERO_EXIT_RE.finditer(goal_text or ""):
        clause_start = max(
            goal_text.rfind(".", 0, match.start()), goal_text.rfind("\n", 0, match.start()),
            goal_text.rfind(";", 0, match.start()), goal_text.rfind(",", 0, match.start()),
        ) + 1
        if not _NEGATION_RE.search(goal_text[max(clause_start, match.start() - 30): match.start()]):
            return True
    return False


def apply_runtime_disposition(
    grade: Dict[str, Any], run_result: Dict[str, Any], *, goal_text: str, verification_authority: str,
) -> Dict[str, Any]:
    """Final, deterministic runtime disposition, applied to ``grade`` in
    place. A timeout, or a nonzero exit, makes the result FAIL whatever the
    grade says. The one exception is a nonzero exit the goal itself declares
    as expected, from an application that launched normally. The semantic
    verdict decides only when no deterministic failure exists. Idempotent."""
    from kriya.workflow.acceptance import runtime_application_step_started

    deterministic: Optional[str] = None
    reason: Optional[str] = None
    if verification_authority == "process_exit":
        deterministic = RuntimeVerdict.PASS.value if run_result.get("success") else RuntimeVerdict.FAIL.value
        reason = DETERMINISTIC_PROCESS_EXIT
    elif run_result.get("timed_out"):
        deterministic, reason = RuntimeVerdict.FAIL.value, TIMEOUT_AUTHORITATIVE
    elif not run_result.get("success"):
        if runtime_application_step_started(run_result) and goal_declares_expected_nonzero_exit(goal_text):
            reason = EXPECTED_NONZERO_EXIT_GROUNDED
        else:
            deterministic, reason = RuntimeVerdict.FAIL.value, NONZERO_EXIT_AUTHORITATIVE
    semantic = grade.get("verdict") or (
        RuntimeVerdict.PASS.value if grade.get("passed") else RuntimeVerdict.FAIL.value
    )
    if deterministic == RuntimeVerdict.FAIL.value:
        final = RuntimeVerdict.FAIL.value
    elif deterministic == RuntimeVerdict.PASS.value:
        final = RuntimeVerdict.PASS.value if grade.get("passed") else semantic
    else:
        final = semantic if semantic in (RuntimeVerdict.FAIL.value, RuntimeVerdict.UNKNOWN.value) else (
            RuntimeVerdict.PASS.value if grade.get("passed") else RuntimeVerdict.FAIL.value
        )
    if grade.get("passed") and final != RuntimeVerdict.PASS.value:
        grade["passed"] = False
        grade["reasoning"] = (
            f"Deterministic runtime evidence overrides the semantic grade ({reason}): the process "
            "did not complete successfully and the goal does not declare that exit as expected. "
            f"Semantic evidence: {grade.get('reasoning', '')}"
        )
    disposition = {
        "deterministic": deterministic or "NONE",
        "deterministic_reason": reason,
        "semantic": semantic,
        "semantic_reason": grade.get("reason_code"),
        "final": final,
        "authority": verification_authority,
    }
    grade["disposition"] = disposition
    return disposition


def runtime_evidence_outcome_fields(grade: Dict[str, Any]) -> Dict[str, Any]:
    """Gate-outcome fields recording what the grader saw and how the result
    was decided. Present only for a grade that carries them."""
    fields: Dict[str, Any] = {}
    if grade.get("disposition") is not None:
        fields["runtime_disposition"] = grade["disposition"]
    if grade.get("evidence_packages"):
        fields["verifier_evidence"] = grade["evidence_packages"]
    return fields


def verifier_evidence_budget_bytes(config: Any, binding: Any, fixed_prompt: str) -> int:
    """UTF-8 bytes left for the evidence package in one grader request to
    ``binding``: the PRD-016 allocation window of that model (allocator
    units of four bytes) minus the fixed prompt around the package."""
    from kriya.workflow.context_budget import allocation_window

    return max(0, allocation_window(config, binding) * 4 - len(fixed_prompt.encode("utf-8")))
