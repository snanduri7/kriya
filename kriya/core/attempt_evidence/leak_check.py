"""Hidden-oracle leak check (BACKEND-READINESS-004, owner section 12; batch 003 final review F1 promoted).

Verifies, at blob level, that nothing an operator keeps hidden from the model - a hidden test file, an oracle script,
an authority marker - reached any model-facing content of a sealed attempt-evidence run. The method is the one the
review required: collect the actual model-facing blobs (``model.request`` and ``prompt.*`` records, decompressed and
re-hashed by the reader), collect the hidden-only units (every non-empty line of each hidden file that is NOT a line
of any public file the operator names - identifiers the hidden file shares with the public workspace legitimately
appear in prompts), intersect, and prove with a positive control that the blobs were really inspected (a public line
the operator names must be found, or the check is INCONCLUSIVE). Digest names, record kinds and file names are never
taken as proof: only bytes are.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from kriya.core.attempt_evidence import reader

LEAK_CHECK_VERSION = "kriya.hidden_oracle_leak_check/1"
DEFAULT_MARKERS: Tuple[str, ...] = (".kriya/authority", "HIDDEN_EXIT", "REGRESS_EXIT", "oracle-venv",
                                    "external_acceptance_command", "baseline authority", "verdict.json")
MODEL_FACING_KINDS: Tuple[str, ...] = ("model.request",)
MODEL_FACING_PREFIXES: Tuple[str, ...] = ("prompt.",)

CLEAN = "CLEAN"
LEAKED = "LEAKED"
INCONCLUSIVE = "INCONCLUSIVE"  # the blobs could not be shown to have been inspected (no positive control hit)
UNAVAILABLE = "UNAVAILABLE"  # no sealed store, no model-facing blobs, or a corrupt blob


@dataclass
class LeakCheckReport:
    run_id: str
    verdict: str
    records: int = 0
    model_facing_blobs: int = 0
    hidden_only_units: int = 0
    markers: Tuple[str, ...] = ()
    hits: List[Dict[str, Any]] = field(default_factory=list)
    positive_control: Dict[str, Any] = field(default_factory=dict)
    problems: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"format": LEAK_CHECK_VERSION, "run_id": self.run_id, "verdict": self.verdict, "records": self.records,
                "model_facing_blobs": self.model_facing_blobs, "hidden_only_units": self.hidden_only_units,
                "markers": list(self.markers), "hits": list(self.hits), "positive_control": dict(self.positive_control),
                "problems": list(self.problems)}


def _units(text: str) -> List[str]:
    """The meaningful units of a file: non-empty, stripped lines of at least 8 characters that carry a letter
    (shorter or symbol-only lines - braces, 'pass', blank - are not evidence of anything)."""
    seen: List[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if len(line) >= 8 and any(ch.isalpha() for ch in line) and line not in seen:
            seen.append(line)
    return seen


def hidden_only_units(hidden_texts: Iterable[str], public_texts: Iterable[str]) -> List[str]:
    public = {unit for text in public_texts for unit in _units(text)}
    return [unit for text in hidden_texts for unit in _units(text) if unit not in public]


def model_facing_blobs(run: reader.EvidenceRun) -> Tuple[int, Dict[str, Tuple[str, bytes]]]:
    """(record count, blob ref -> (record kind, bytes)) for every model-facing record of ``run``."""
    records = 0
    blobs: Dict[str, Tuple[str, bytes]] = {}
    for record in run.records():
        records += 1
        kind = str(record.get("kind") or "")
        if kind not in MODEL_FACING_KINDS and not kind.startswith(MODEL_FACING_PREFIXES):
            continue
        for ref in (record.get("blobs") or {}).values():
            if isinstance(ref, str) and ref not in blobs:
                blobs[ref] = (kind, run.blob(ref))
    return records, blobs


def check_run(
    state_dir: str, run_id: str, *, hidden_texts: Sequence[str], public_texts: Sequence[str] = (),
    markers: Sequence[str] = DEFAULT_MARKERS, positive_control_units: Optional[Sequence[str]] = None,
) -> LeakCheckReport:
    """Grep every model-facing blob of the sealed run for the hidden-only units and the markers. The positive control
    is a unit that MUST appear in some model-facing blob (by default: the public files' own units, i.e. workspace
    content the Developer was shown); without a control hit the verdict is INCONCLUSIVE, never CLEAN."""
    report = LeakCheckReport(run_id=run_id, verdict=UNAVAILABLE, markers=tuple(markers))
    run = reader.open_run(state_dir, run_id)
    if not run.exists:
        report.problems.append("no attempt-evidence store for this run")
        return report
    try:
        report.records, blobs = model_facing_blobs(run)
    except (reader.BlobCorrupt, reader.UnsupportedEvidenceSchema, OSError) as error:
        report.problems.append(f"{type(error).__name__}: {error}")
        return report
    report.model_facing_blobs = len(blobs)
    if not blobs:
        report.problems.append("no model-facing blob (capture mode without blobs, or no model call)")
        return report
    units = hidden_only_units(hidden_texts, public_texts)
    report.hidden_only_units = len(units)
    texts = {ref: data.decode("utf-8", errors="replace") for ref, (_kind, data) in blobs.items()}
    for ref, text in texts.items():
        kind = blobs[ref][0]
        for unit in units:
            if unit in text:
                report.hits.append({"kind": kind, "blob": ref, "what": "hidden-only unit", "unit": unit[:120]})
        for marker in markers:
            if marker in text:
                report.hits.append({"kind": kind, "blob": ref, "what": "marker", "unit": marker})
    controls = list(positive_control_units if positive_control_units is not None
                    else [unit for text in public_texts for unit in _units(text)])
    found = [unit for unit in controls if any(unit in text for text in texts.values())]
    report.positive_control = {"units": len(controls), "found": len(found), "example": found[0][:120] if found else None}
    if report.hits:
        report.verdict = LEAKED
    elif not controls or not found:
        report.verdict = INCONCLUSIVE
        report.problems.append("no positive-control unit was found in any model-facing blob: the blobs were read, "
                               "but nothing proves they carry workspace content at all")
    else:
        report.verdict = CLEAN
    return report


def read_texts(paths: Iterable[str]) -> List[str]:
    texts: List[str] = []
    for path in paths:
        with open(os.path.expanduser(path), "r", encoding="utf-8", errors="replace") as handle:
            texts.append(handle.read())
    return texts


def check_run_files(state_dir: str, run_id: str, *, hidden: Sequence[str], public: Sequence[str] = (),
                    markers: Sequence[str] = DEFAULT_MARKERS) -> LeakCheckReport:
    return check_run(state_dir, run_id, hidden_texts=read_texts(hidden), public_texts=read_texts(public), markers=markers)


def summarize(report: Mapping[str, Any]) -> str:
    return (f"{report['verdict']}: {report['model_facing_blobs']} model-facing blob(s) of {report['records']} record(s) "
            f"checked against {report['hidden_only_units']} hidden-only unit(s) and {len(report['markers'])} marker(s); "
            f"hits {len(report['hits'])}; positive control {report['positive_control'].get('found')}/"
            f"{report['positive_control'].get('units')}")
