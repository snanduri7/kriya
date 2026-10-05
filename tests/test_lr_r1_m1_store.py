"""LR-R1-M1.1: attempt evidence store - schema, chain, blobs, seal, verify.

Design §3, §4, §6, §7, §9; tests T1, T2, T10 (unit part), T5 (writer part).
"""
import ast
import gzip
import json
import os
import stat
from pathlib import Path

import pytest

from kriya.core.attempt_evidence import model, reader
from kriya.core.attempt_evidence.writer import AttemptEvidenceWriter, RecorderUnavailable, run_directory

ROOT = Path(__file__).resolve().parents[1]


def _writer(tmp_path, capture=model.CAPTURE_FULL, run_id="run-1"):
    return AttemptEvidenceWriter(str(tmp_path), run_id, capture=capture, manifest={"run_kind": "generate"})


def _files(directory):
    return sorted(str(p.relative_to(directory)) for p in Path(directory).rglob("*") if p.is_file())


# -- T1 schema -------------------------------------------------------------

def test_round_trip_every_kind_and_envelope(tmp_path):
    writer = _writer(tmp_path)
    for i, kind in enumerate(sorted(model.KINDS)):
        assert writer.append(kind, {"i": i}, identity={"unit_id": "u", "attempt_number": 2}) == i + 1
    assert writer.seal("closed")
    run = reader.open_run(str(tmp_path), "run-1")
    records = list(run.records())
    assert [r["kind"] for r in records] == sorted(model.KINDS)
    assert all(tuple(r) == tuple(sorted(r)) or set(r) == set(model.ENVELOPE_KEYS) for r in records)
    assert set(records[0]) == set(model.ENVELOPE_KEYS)
    assert records[0]["run_id"] == "run-1" and records[0]["unit_id"] == "u" and records[0]["call_seq"] is None
    assert run.manifest()["capture"] == model.CAPTURE_FULL
    assert run.verify().status == reader.VERIFIED


def test_unknown_kind_or_provenance_is_refused(tmp_path):
    writer = _writer(tmp_path)
    with pytest.raises(ValueError):
        writer.append("model.guess", {})
    with pytest.raises(ValueError):
        writer.append("model.request", {}, provenance="TRACED")


def test_reader_refuses_an_unsupported_schema(tmp_path):
    writer = _writer(tmp_path)
    writer.append("run.opened", {})
    writer.seal("closed")
    path = Path(run_directory(str(tmp_path), "run-1")) / "records.jsonl"
    record = json.loads(path.read_bytes().splitlines()[0])
    record["schema"] = "kriya.attempt_evidence/9"
    path.chmod(0o600)
    path.write_bytes(model.canonical_bytes(record) + b"\n")
    with pytest.raises(reader.UnsupportedEvidenceSchema):
        list(reader.open_run(str(tmp_path), "run-1").records())


def test_a_record_for_another_run_is_never_written(tmp_path):
    writer = _writer(tmp_path)
    with pytest.raises(ValueError, match="offered to the store"):
        writer.append("run.opened", {}, identity={"run_id": "other"})
    assert writer.seq == 0


# -- T2 chain ---------------------------------------------------------------

def _sealed_store(tmp_path, n=4):
    writer = _writer(tmp_path)
    for i in range(n):
        writer.append("mirror.event", {"i": i}, content={"text": f"content {i}"})
    writer.seal("closed")
    return Path(run_directory(str(tmp_path), "run-1"))


def test_intact_store_verifies(tmp_path):
    _sealed_store(tmp_path)
    result = reader.open_run(str(tmp_path), "run-1").verify()
    assert result.status == reader.VERIFIED and result.record_count == 4 and result.sealed


def test_tampered_middle_record_breaks_the_chain_at_the_next_record(tmp_path):
    directory = _sealed_store(tmp_path)
    path = directory / "records.jsonl"
    lines = path.read_bytes().split(b"\n")
    record = json.loads(lines[1])
    record["payload"]["i"] = 99
    lines[1] = model.canonical_bytes(record)
    path.write_bytes(b"\n".join(lines))
    result = reader.open_run(str(tmp_path), "run-1").verify()
    assert result.status == reader.CHAIN_BROKEN and result.broken_seq == 3


def test_partial_last_line_is_reported_and_never_repaired(tmp_path):
    directory = _sealed_store(tmp_path)
    path = directory / "records.jsonl"
    data = path.read_bytes() + b'{"schema":"kriya.attempt_evidence/1","seq":5'
    path.write_bytes(data)
    result = reader.open_run(str(tmp_path), "run-1").verify()
    assert result.status == reader.TRUNCATED_TAIL and result.record_count == 4
    assert path.read_bytes() == data
    assert [r["seq"] for r in reader.open_run(str(tmp_path), "run-1").records()] == [1, 2, 3, 4]


def test_missing_seal_is_unsealed(tmp_path):
    writer = _writer(tmp_path)
    writer.append("run.opened", {})
    result = reader.open_run(str(tmp_path), "run-1").verify()
    assert result.status == reader.UNSEALED and not result.sealed


def test_blob_byte_flip_is_blob_corrupt(tmp_path):
    directory = _sealed_store(tmp_path)
    ref = next(r for r in reader.open_run(str(tmp_path), "run-1").records())["blobs"]["text"]
    hexdigest = ref.split(":", 1)[1]
    (directory / "blobs" / hexdigest[:2] / f"{hexdigest}.gz").write_bytes(gzip.compress(b"forged", mtime=0))
    result = reader.open_run(str(tmp_path), "run-1").verify()
    assert result.status == reader.BLOB_CORRUPT and len(result.blob_problems) == 1
    with pytest.raises(reader.BlobCorrupt):
        reader.open_run(str(tmp_path), "run-1").blob(ref)


def test_seal_naming_a_different_head_is_a_mismatch(tmp_path):
    directory = _sealed_store(tmp_path)
    seal = json.loads((directory / "seal.json").read_text())
    seal["final_seq"] = 3
    (directory / "seal.json").write_text(json.dumps(seal))
    assert reader.open_run(str(tmp_path), "run-1").verify().status == reader.SEAL_MISMATCH


def test_blob_round_trips_exact_bytes_and_is_deduplicated(tmp_path):
    writer = _writer(tmp_path)
    payload = "λ exact\r\nbytes\x00".encode("utf-8")
    writer.append("model.request", {}, content={"messages": payload})
    writer.append("model.request", {}, content={"messages": payload})
    writer.seal("closed")
    run = reader.open_run(str(tmp_path), "run-1")
    first, second = list(run.records())
    assert first["blobs"]["messages"] == second["blobs"]["messages"] == model.digest(payload)
    assert first["content_digests"]["messages"] == {"digest": model.digest(payload), "bytes": len(payload)}
    assert run.blob(first["blobs"]["messages"]) == payload
    assert run.seal()["blob_count"] == 1


# -- T10 capture modes and file modes (unit part) ----------------------------

def test_digest_only_writes_no_blob_and_no_content_bytes(tmp_path):
    canary = "CANARY-SOURCE-7f3a"
    writer = _writer(tmp_path, capture=model.CAPTURE_DIGEST_ONLY)
    writer.append("model.request", {"model": "m"}, content={"messages": canary})
    writer.seal("closed")
    directory = Path(run_directory(str(tmp_path), "run-1"))
    record = next(reader.open_run(str(tmp_path), "run-1").records())
    assert record["blobs"] == {}
    assert record["content_digests"]["messages"]["digest"] == model.digest(canary.encode())
    for path in directory.rglob("*"):
        if path.is_file():
            assert canary.encode() not in path.read_bytes(), path
    assert not any((directory / "blobs").iterdir())


def test_store_is_private_regardless_of_umask(tmp_path):
    old = os.umask(0o277)  # strips owner write: only an explicit chmod yields 0700/0600
    try:
        writer = _writer(tmp_path)
        writer.append("run.opened", {}, content={"goal": "g"})
        writer.seal("closed")
    finally:
        os.umask(old)
    directory = Path(run_directory(str(tmp_path), "run-1"))
    for path in [directory.parent, directory, *directory.rglob("*")]:
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == (0o700 if path.is_dir() else 0o600), (path, oct(mode))


def test_off_and_unknown_capture_open_no_store(tmp_path):
    for capture in (model.CAPTURE_OFF, "partial"):
        with pytest.raises(ValueError):
            _writer(tmp_path, capture=capture)
    assert not Path(tmp_path, "attempt-evidence").exists()


# -- open failures are typed and observational (D5) ---------------------------

def test_existing_run_directory_is_recorder_unavailable(tmp_path):
    _writer(tmp_path).seal("closed")
    with pytest.raises(RecorderUnavailable) as error:
        _writer(tmp_path)
    assert error.value.reason_code == "RECORDER_UNAVAILABLE"


def test_unwritable_state_dir_is_recorder_unavailable(tmp_path):
    blocked = tmp_path / "state"
    blocked.write_text("a file, not a directory")
    with pytest.raises(RecorderUnavailable):
        AttemptEvidenceWriter(str(blocked), "run-1", capture=model.CAPTURE_FULL, manifest={})


def test_unsafe_run_id_is_refused(tmp_path):
    with pytest.raises(RecorderUnavailable):
        _writer(tmp_path, run_id="../escape")


# -- degradation never raises an Exception, never swallows BaseException ------

def test_record_returns_seq_on_the_normal_path(tmp_path):
    writer = _writer(tmp_path)
    assert writer.record("run.opened", {"a": 1}) == 1
    assert writer.gaps == 0 and writer.degraded == []


def test_record_degrades_on_a_records_write_failure(tmp_path, monkeypatch):
    writer = _writer(tmp_path)
    writer.record("run.opened", {})

    def fail(*_args):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(os, "write", fail)
    assert writer.record("model.request", {}) is None
    monkeypatch.undo()
    assert writer.record("model.response", {}) is None   # stopped writing after the failure
    assert writer.gaps == 2 and not writer.active
    assert writer.seal("closed") is True
    seal = reader.open_run(str(tmp_path), "run-1").seal()
    assert seal["complete"] is False and seal["gaps"] == 2 and seal["final_seq"] == 1


def test_blob_failure_keeps_records_and_falls_back_to_digests(tmp_path, monkeypatch):
    writer = _writer(tmp_path)

    def fail(*_args, **_kwargs):
        raise OSError(13, "Permission denied")
    monkeypatch.setattr(os, "link", fail)
    assert writer.record("model.request", {}, content={"messages": "a"}) == 1
    monkeypatch.undo()
    assert writer.record("model.request", {}, content={"messages": "b"}) == 2
    writer.seal("closed")
    run = reader.open_run(str(tmp_path), "run-1")
    assert [r["blobs"] for r in run.records()] == [{}, {}]
    assert run.seal()["complete"] is False
    assert run.verify().status == reader.VERIFIED


@pytest.mark.parametrize("signal", [KeyboardInterrupt, SystemExit])
def test_base_exceptions_propagate_through_record(tmp_path, monkeypatch, signal):
    writer = _writer(tmp_path)

    def interrupt(*_args):
        raise signal()
    monkeypatch.setattr(os, "write", interrupt)
    with pytest.raises(signal):
        writer.record("run.opened", {})


# -- T5 writer structure -----------------------------------------------------

def test_writer_opens_records_append_only_and_never_rewrites_them():
    source = (ROOT / "kriya/core/attempt_evidence/writer.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    names = {getattr(c.func, "attr", getattr(c.func, "id", None)) for c in calls}
    assert not names & {"truncate", "ftruncate", "seek", "lseek", "write_text", "write_bytes"}
    replaces = [c for c in calls if ast.unparse(c.func) == "os.replace"]
    assert len(replaces) == 1 and "_SEAL" in ast.unparse(replaces[0])
    records_open = [c for c in calls if getattr(c.func, "attr", None) == "open" and "_RECORDS" in ast.unparse(c)]
    assert len(records_open) == 1 and "O_APPEND" in ast.unparse(records_open[0])


def test_store_package_names_no_benchmark_gold_and_has_no_network_client():
    for path in (ROOT / "kriya/core/attempt_evidence").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "gold" not in text.lower(), path
        for client in ("httpx", "urllib", "requests", "socket", "AsyncOpenAI"):
            assert f"import {client}" not in text and f"from {client}" not in text, (path, client)
