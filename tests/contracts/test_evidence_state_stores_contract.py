"""The evidence and state stores contract suite (full modularization D1/D2; TEST-MATRIX §2; MODULE-CONTRACTS §5, §6).

Every store implementation registers in ``EVIDENCE_READERS`` / ``CHECKPOINT_STORES`` / ``HISTORY_STORES`` and runs these checks on a real
store in ``tmp_path``. D1 creates the skeleton with the evidence reader as it exists TODAY (``reader.open_run(state_dir, run_id)`` ->
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


# ------------------------------------------------------------------ state stores (D2)

CHECKPOINT_STORES = {"local": "kriya.control.checkpoint"}
HISTORY_STORES = {"local": "kriya.control.history.port"}
# MODULE-CONTRACTS §6 signatures that are not today's (closed; each names the slice that closes it).
STATE_ARRIVES_LATER = {
    # Re-tagged D3 -> O1: a WorkspaceContext-keyed store needs WorkspaceContext.state_dir resolved by ConfigService, whose precedence
    # (KRIYA_STATE_DIR > paths.state > default) is STATE's core.state_paths, which CONFIG may not import; it arrives with the per-request
    # configuration of the transport (the client contract's ``cfg`` parameters, O1).
    "CheckpointStore.save(ws: WorkspaceContext, checkpoint) -> str": "O1",  # today: save_checkpoint(workspace_path, run_id, data) -> None
    "HistoryStore.snapshot(ws, state_dir) -> SnapshotRef": "O1",           # today: acquire(cfg, run_active_check=...) from the resolved cfg
    "HistoryStore.detail(snapshot, run_id, include_prompt)": "O1",         # today: trace(snapshot).detail / .prompt
}


@pytest.mark.parametrize("name", sorted(CHECKPOINT_STORES))
def test_a_checkpoint_store_round_trips_lists_and_deletes(tmp_path, name):
    import importlib

    store = importlib.import_module(CHECKPOINT_STORES[name])
    workspace = str(tmp_path)
    store.save_checkpoint(workspace, "20261016T000000-aaaaaaaa", {"stage": "plan"})
    loaded = store.load_checkpoint(workspace, "20261016T000000-aaaaaaaa")
    assert loaded["stage"] == "plan" and loaded["run_id"] == "20261016T000000-aaaaaaaa" and "_workspace" in loaded
    assert [c["run_id"] for c in store.list_checkpoints(workspace)] == ["20261016T000000-aaaaaaaa"]
    assert store.find_latest_checkpoint(workspace) == "20261016T000000-aaaaaaaa"
    store.delete_checkpoint(workspace, "20261016T000000-aaaaaaaa")
    assert store.load_checkpoint(workspace, "20261016T000000-aaaaaaaa") is None and store.list_checkpoints(workspace) == []
    assert set(STATE_ARRIVES_LATER.values()) <= {"O1"}


@pytest.mark.parametrize("name", sorted(HISTORY_STORES))
def test_a_history_store_acquires_requires_lists_reads_and_prunes(tmp_path, name):
    import importlib

    from _kup_fixtures import seed_store

    from kriya.config import AppConfig
    from kriya.control.history import SnapshotError

    store, cfg = importlib.import_module(HISTORY_STORES[name]).HistoryStore(), AppConfig()
    with pytest.raises(SnapshotError):
        store.require(cfg, "20200101T000000000000Z-00000000")
    seed_store(store.location(cfg).trace_database, 3)
    snap = store.acquire(cfg)["snapshot"]
    assert store.require(cfg, snap.snapshot_id) == snap and [s.snapshot_id for s in store.published(cfg)] == [snap.snapshot_id]
    assert store.verify(snap) is None and store.summary(cfg, snap)["rows"] == 3
    assert [row["run_id"] for row in store.trace(snap).page(10, None)["runs"]] == ["run-0002", "run-0001", "run-0000"]
    assert store.prune(cfg, keep=0)["removed"] == [snap.snapshot_id] and store.published(cfg) == []
