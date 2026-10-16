"""The evidence and state stores contract suite (full modularization D1/D2; TEST-MATRIX §2; MODULE-CONTRACTS §5, §6).

Every store implementation registers in ``EVIDENCE_READERS`` (and, from D2, the state stores) and runs these checks on a real store in
``tmp_path``. D1 creates the skeleton with the evidence reader as it exists TODAY (``reader.open_run(state_dir, run_id)`` ->
``EvidenceRun``): its surface is pinned method by method and exercised write -> read -> verify. The MODULE-CONTRACTS §5 ``EvidenceReader``
signatures that are not today's (keyed by ``run_id`` on one reader object, kind-filtered paged records) are a CLOSED list of gaps with the
slice that closes each (``ARRIVES_LATER``), never invented here.
"""
from __future__ import annotations

import inspect
from typing import Callable, Dict

import pytest

from kriya.core.attempt_evidence import decisions, model, reader
from kriya.core.attempt_evidence.writer import AttemptEvidenceWriter


def _local_reader(state_dir: str, run_id: str) -> reader.EvidenceRun:
    return reader.open_run(state_dir, run_id)


EVIDENCE_READERS: Dict[str, Callable[[str, str], reader.EvidenceRun]] = {"local": _local_reader}

# The read surface every evidence reader offers today (name -> parameter names after self).
EVIDENCE_READER_SURFACE = {
    "manifest": (), "seal": (), "measure": (), "records": (), "blob": ("ref",), "verify": ("records_out",),
}
# MODULE-CONTRACTS §5 surface that does not exist yet (closed: each names the slice that closes it).
ARRIVES_LATER = {
    "EvidenceReader.manifest(run_id)": "O1",   # one reader object keyed by run_id (today: open_run(state_dir, run_id).manifest())
    "EvidenceReader.records(run_id, kind, cursor, limit) -> RecordPage": "O1",  # the kind filter + opaque cursor still live in KUP/EvidenceService
    "EvidenceReader.content(run_id, seq, part)": "O1",
    "EvidenceReader.verify(run_id) -> VerificationReport": "O1",
}


@pytest.fixture(params=sorted(EVIDENCE_READERS))
def open_reader(request) -> Callable[[str, str], reader.EvidenceRun]:
    return EVIDENCE_READERS[request.param]


def test_the_evidence_reader_surface_is_pinned():
    for name, parameters in EVIDENCE_READER_SURFACE.items():
        member = getattr(reader.EvidenceRun, name)
        assert tuple(inspect.signature(member).parameters)[1:] == parameters, name
    assert isinstance(inspect.getattr_static(reader.EvidenceRun, "exists"), property)
    assert set(ARRIVES_LATER.values()) <= {"O1"}


def test_a_written_store_reads_back_verified_with_every_decision_kind(tmp_path, open_reader):
    writer = AttemptEvidenceWriter(str(tmp_path), "run-c", capture=model.CAPTURE_FULL, manifest={"run_kind": "generate"})
    for kind in decisions.DECISION_KINDS:
        writer.append(kind, {"decision_schema": decisions.DECISION_SCHEMA}, identity={"attempt_number": 1},
                      content={"note": kind})
    assert writer.seal("closed")
    run = open_reader(str(tmp_path), "run-c")
    assert run.exists and run.manifest()["capture"] == model.CAPTURE_FULL
    records = list(run.records())
    assert [r["kind"] for r in records] == list(decisions.DECISION_KINDS)
    assert run.blob(records[0]["blobs"]["note"]) == decisions.DECISION_KINDS[0].encode("utf-8")
    verification = run.verify()
    assert verification.status == reader.VERIFIED and verification.record_count == len(records)
    assert run.seal()["final_seq"] == len(records)
    assert run.measure()[0] == len(records)


def test_a_missing_store_is_not_found_never_an_empty_verified_one(tmp_path, open_reader):
    run = open_reader(str(tmp_path), "absent")
    assert not run.exists
    assert run.verify().status == reader.NOT_FOUND
