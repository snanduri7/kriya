#!/usr/bin/env python
"""Deterministic evidence_records fixture in the shape Kriya's OWN writer records, read back through the KUP adapter,
plus the attribution section exactly as the adapter serializes it.

Writer inventory (TRACED 2026-10-04):
  * kriya/workflow/evidence.py::EvidenceRecord - fields kind, source, attempt, payload, sensitivity ("local_only"),
    created_at; ``to_dict`` is ``dataclasses.asdict``. There is NO identifier field.
  * kriya/workflow/state.py::GenerationState.record_failure (line ~952): EvidenceRecord(kind="failure",
    source=failure.source, attempt=..., payload={type, message, raw_output, likely_files, failed_content_revisions
    (path -> content_revision(text)), attempted_edits}).
  * kriya/workflow/workflow.py (line ~3610): EvidenceRecord(kind="active_skills", source="skill_engine", attempt=0,
    payload={"skills": <active-skill manifest>}).
  * Persisted by TraceLogger.log_run(evidence_records=[record.to_dict() ...]) (workflow.py 4483 / 5479 / 5857).
  * Attribution: kriya/kup/inspect.py::history_detail returns the attribution section as not_persisted ("the baseline
    persists failure categories, not causal attribution"); kriya/workflow/attribution.py::AttributionResult (tier,
    files, confidence, reasoning) reaches a trace row only as Failure.attribution_* on gate outcomes and
    failure_report[].attribution_tier. No production writer records AttributionRecord.evidence_ids, and no
    identifier namespace exists for it to reference: the KUP schema types it as an array of strings only.

Run from ui/ with the checkout's interpreter: `../.newvenv/bin/python fixtures/serializer_evidence.py` (or
`npm run fixtures:serializer`); `--check` fails when the committed file differs. Fixture only: temporary roots, no
protected store, no model, no network, no bytecode written.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import Any, Dict, List

sys.dont_write_bytecode = True

from kriya.core.trace import TraceLogger  # noqa: E402 - after dont_write_bytecode, like the CLI
from kriya.kup.acquire import acquire_snapshot  # noqa: E402
from kriya.kup.inspect import history_detail  # noqa: E402
from kriya.workflow.edit_safety import content_revision  # noqa: E402
from kriya.workflow.evidence import EvidenceRecord  # noqa: E402
from kriya.workflow.failure import Failure, FileLocation  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "serializer", "evidence_records.json")
RUN_ID = "run-serializer-evidence"
COMPILE_ERROR = "src/mod1/a.py:12:5: error: name 'audit' is not defined\n1 error\n"
FAILED_SOURCE = "def run(self):\n    audit('run')\n    return 1\n"


def real_evidence_records() -> List[Dict[str, Any]]:
    failure = Failure(type="compile", message=f"COMPILATION FAILURE:\n{COMPILE_ERROR}", raw_output=COMPILE_ERROR, attempt=1,
                      likely_files=["src/mod1/a.py"], file_locations=[FileLocation("src/mod1/a.py", 12, 5)],
                      failed_content={"src/mod1/a.py": FAILED_SOURCE})
    # state.py::GenerationState.record_failure, key for key
    failure_record = EvidenceRecord(
        kind="failure", source=failure.source, attempt=failure.attempt,
        payload={
            "type": failure.type, "message": failure.message, "raw_output": failure.raw_output, "likely_files": list(failure.likely_files),
            "failed_content_revisions": {path: content_revision(content) for path, content in failure.failed_content.items()},
            "attempted_edits": list(failure.attempted_edits),
        },
        created_at=1791097202.5,
    )
    # workflow.py ~3610, key for key (the manifest entries are what SkillEngine.manifest_for returns for a loaded skill)
    skills_record = EvidenceRecord(
        kind="active_skills", source="skill_engine", attempt=0,
        payload={"skills": [{"name": "audit-log", "version": "1.0.0", "source_path": "skills/audit-log/skill.yaml", "digest": "sha256:" + "ef" * 32}]},
        created_at=1791097190.0,
    )
    return [skills_record.to_dict(), failure_record.to_dict()]


def through_the_adapter(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        store = os.path.join(tmp, "state", "traces.db")
        os.makedirs(os.path.dirname(store))
        TraceLogger(store).log_run(run_id=RUN_ID, goal="serializer evidence fixture", duration_sec=61.23, attempts=1, status="FAILED",
                                   files_modified=["src/mod1/a.py"], evidence_records=records)
        snapshot = acquire_snapshot(store, os.path.join(tmp, "state", "kup-snapshots"))["snapshot"]
        detail = history_detail(snapshot, RUN_ID)
        return {"evidence": detail["evidence_records"], "attribution": detail["attribution"]}


def main() -> int:
    records = real_evidence_records()
    sections = through_the_adapter(records)
    assert sections["evidence"]["data"] == json.loads(json.dumps(records)), "the adapter must return the writer's records verbatim"
    for record in sections["evidence"]["data"]:
        assert set(record) == {"kind", "source", "attempt", "payload", "sensitivity", "created_at"}, record.keys()
    assert sections["attribution"]["availability"] == "not_recorded" and sections["attribution"]["data"] is None
    doc = {
        "generated_by": "ui/fixtures/serializer_evidence.py: kriya/workflow/evidence.py::EvidenceRecord.to_dict (payloads as state.py::record_failure and "
                        "workflow.py build them) -> TraceLogger.log_run -> kriya.kup.acquire.acquire_snapshot -> kriya.kup.inspect.history_detail",
        "serializer_keys": ["kind", "source", "attempt", "payload", "sensitivity", "created_at"],
        "identifier_field": None,
        "note": "EvidenceRecord carries no identifier; nothing in a trace row can reference an evidence record. AttributionRecord.evidence_ids "
                "(KUP schema) has no defined namespace and no production writer; this Kriya version persists no attribution record.",
        "section": {"availability": sections["evidence"]["availability"], "provenance": sections["evidence"]["provenance"], "reason": sections["evidence"]["reason"]},
        "evidence_records": sections["evidence"]["data"],
        "attribution_section": sections["attribution"],
    }
    text = json.dumps(doc, indent=1, ensure_ascii=False, sort_keys=True) + "\n"
    if "--check" in sys.argv:
        committed = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
        if committed != text:
            print(f"{OUT} differs from a fresh generation; run fixtures/serializer_evidence.py", file=sys.stderr)
            return 1
        print(f"{OUT} is current ({len(doc['evidence_records'])} evidence records)")
        return 0
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"wrote {OUT} ({len(doc['evidence_records'])} evidence records through the adapter)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
