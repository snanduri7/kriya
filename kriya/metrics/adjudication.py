"""The trusted run-adjudication store (PRD-033).

False success and regression escape are facts about a run that no run can
know about itself: they come from later evidence. They are recorded here, and
only here, by ``kriya metrics adjudicate``, which is the store's only production
writer (tests/test_prd033_metrics.py). A Reviewer or any other model output is
never an adjudication source: the verdict source is ``human`` or
``deterministic`` (reserved for a deterministic producer; Kriya has none
yet).

The store lives outside any workspace (``~/.kriya/adjudications/
adjudications.json``, ``KRIYA_ADJUDICATION_HOME`` override), like the
PRD-031A waiver store. Every record carries a digest of its body. One
tampered, duplicated or unknown-schema record invalidates the whole store:
nothing from it is counted, and the report says the store is invalid.
"""

from __future__ import annotations

import getpass
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from kriya.metrics.evidence import canonical_digest

ENV_HOME_OVERRIDE = "KRIYA_ADJUDICATION_HOME"
STORE_SCHEMA_VERSION = 1
STORE_FILENAME = "adjudications.json"

VERDICT_FALSE_SUCCESS = "false_success"
VERDICT_REGRESSION_ESCAPE = "regression_escape"
VERDICT_CONFIRMED_SUCCESS = "confirmed_success"
VERDICTS = (VERDICT_FALSE_SUCCESS, VERDICT_REGRESSION_ESCAPE, VERDICT_CONFIRMED_SUCCESS)
# Verdicts that say a SUCCESS was wrong: only a SUCCESS run can carry them.
SUCCESS_CONTRADICTING = frozenset({VERDICT_FALSE_SUCCESS, VERDICT_REGRESSION_ESCAPE})

SOURCE_HUMAN = "human"
SOURCE_DETERMINISTIC = "deterministic"
SOURCES = (SOURCE_HUMAN, SOURCE_DETERMINISTIC)


class AdjudicationStoreError(ValueError):
    """The store is unreadable, tampered with or of an unknown schema."""


class AdjudicationRefused(ValueError):
    """A verdict the store refuses to record."""


@dataclass(frozen=True)
class Adjudication:
    adjudication_id: str
    run_id: str
    verdict: str
    source: str
    adjudicator: str
    evidence: str
    recorded_at: str
    recorded_by: str
    record_digest: str = ""

    def body(self) -> Dict[str, Any]:
        data = asdict(self)
        data.pop("record_digest")
        return data

    def computed_digest(self) -> str:
        return canonical_digest(self.body())


def store_path() -> str:
    home = os.environ.get(ENV_HOME_OVERRIDE) or os.path.join(os.path.expanduser("~"), ".kriya", "adjudications")
    return os.path.join(os.path.realpath(home), STORE_FILENAME)


@dataclass(frozen=True)
class AdjudicationStore:
    path: str
    status: str  # "valid" | "absent" | "invalid"
    records: Tuple[Adjudication, ...] = ()
    error: Optional[str] = None

    def by_run(self) -> Dict[str, Adjudication]:
        """The latest verdict per run (a later record supersedes an earlier one)."""
        latest: Dict[str, Adjudication] = {}
        for record in sorted(self.records, key=lambda r: (r.recorded_at, r.adjudication_id)):
            latest[record.run_id] = record
        return latest


def _validate(record: Adjudication) -> None:
    if record.verdict not in VERDICTS:
        raise AdjudicationRefused(f"unknown verdict {record.verdict!r} (one of {', '.join(VERDICTS)})")
    if record.source not in SOURCES:
        raise AdjudicationRefused(f"unknown source {record.source!r} (one of {', '.join(SOURCES)})")
    for name in ("run_id", "adjudicator", "evidence"):
        if not str(getattr(record, name) or "").strip():
            raise AdjudicationRefused(f"{name} is required")


def load_adjudications(path: Optional[str] = None) -> AdjudicationStore:
    path = path or store_path()
    if not os.path.exists(path):
        return AdjudicationStore(path=path, status="absent")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict) or payload.get("schema_version") != STORE_SCHEMA_VERSION:
            raise AdjudicationStoreError("unknown adjudication store schema")
        records = []
        for item in payload.get("adjudications", []):
            if not isinstance(item, dict):
                raise AdjudicationStoreError("an adjudication is not an object")
            record = Adjudication(**item)
            _validate(record)
            if record.record_digest != record.computed_digest():
                raise AdjudicationStoreError(f"adjudication {record.adjudication_id} digest does not match its body")
            records.append(record)
        ids = [r.adjudication_id for r in records]
        if len(ids) != len(set(ids)):
            raise AdjudicationStoreError("duplicate adjudication ids")
    except (OSError, TypeError, ValueError) as error:
        return AdjudicationStore(path=path, status="invalid", error=f"{type(error).__name__}: {error}")
    return AdjudicationStore(path=path, status="valid", records=tuple(records))


def record_adjudication(
    *, run_id: str, verdict: str, adjudicator: str, evidence: str, run_status: Optional[str],
    source: str = SOURCE_HUMAN, path: Optional[str] = None, now: Optional[datetime] = None,
) -> Adjudication:
    """Append one verdict. ``run_status`` is the adjudicated run's trace
    status (None when no such run exists: refused). Only the operator CLI
    calls this."""
    if run_status is None:
        raise AdjudicationRefused(f"no traced run {run_id!r}")
    if verdict in SUCCESS_CONTRADICTING and run_status != "success":
        raise AdjudicationRefused(f"{verdict} applies only to a run that ended SUCCESS (this one: {run_status})")
    path = path or store_path()
    store = load_adjudications(path)
    if store.status == "invalid":
        raise AdjudicationStoreError(f"refusing to modify an invalid adjudication store ({store.error}); repair or remove {path}")
    moment = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    draft = Adjudication(
        adjudication_id=f"ADJ-{len(store.records) + 1:04d}", run_id=run_id, verdict=verdict, source=source,
        adjudicator=adjudicator.strip(), evidence=evidence.strip(), recorded_at=moment, recorded_by=getpass.getuser(),
    )
    _validate(draft)
    record = Adjudication(**{**asdict(draft), "record_digest": draft.computed_digest()})
    _save(path, [*store.records, record])
    return record


def _save(path: str, records: Sequence[Adjudication]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {"schema_version": STORE_SCHEMA_VERSION, "adjudications": [asdict(r) for r in records]}
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def verdict_counts(store: AdjudicationStore, run_ids: Sequence[str]) -> Mapping[str, Any]:
    """Verdicts over the runs in the report window (valid store only)."""
    if store.status != "valid":
        return {"store_status": store.status, "store_error": store.error}
    latest = store.by_run()
    in_window = [latest[run_id] for run_id in run_ids if run_id in latest]
    counts: Dict[str, int] = {verdict: 0 for verdict in VERDICTS}
    sources: Dict[str, int] = {source: 0 for source in SOURCES}
    for record in in_window:
        counts[record.verdict] += 1
        sources[record.source] += 1
    return {"store_status": store.status, "adjudicated_runs": len(in_window), "verdicts": counts, "sources": sources}


def adjudications_for_listing(store: AdjudicationStore) -> List[Dict[str, Any]]:
    return [asdict(r) for r in sorted(store.records, key=lambda r: r.adjudication_id)]
