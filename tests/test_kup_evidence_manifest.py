"""KUP ``evidence.manifest`` (Run Cockpit v1.1, 04 section 4.1): store metadata, capture mode, seal, gap counts and the DERIVED
absence reason - read-only, run-id validated before any lookup, nothing created. In-process against stores written by the real
recorder (tests/_kup_evidence_fixtures.py) in the test's isolated state directory."""
from __future__ import annotations

import json
import os

import pytest
from _kup_evidence_fixtures import acquire_snapshot_id, seed_trace_run, state_dir, write_store
from _strict_doubles import strict_config

from kriya.core.attempt_evidence import model
from kriya.core.attempt_evidence.writer import store_root
from kriya.kup import policy
from kriya.kup.cli_ops import run_kup_operation


def _manifest(run_id, snapshot_id=None, cfg=None):
    request = {"operation": "evidence.manifest", "run_id": run_id}
    if snapshot_id is not None:
        request["snapshot_id"] = snapshot_id
    return run_kup_operation(cfg or strict_config(), request)


def _tree(directory):
    return sorted((os.path.relpath(os.path.join(root, name), directory), os.lstat(os.path.join(root, name)).st_mtime_ns, os.lstat(os.path.join(root, name)).st_size)
                  for root, dirs, files in os.walk(directory) for name in dirs + files)


def test_sealed_full_store_reports_manifest_seal_and_sizes_verbatim():
    directory = write_store("run-a", records=3)
    env = _manifest("run-a")
    assert env["error"] is None and env["operation"] == "evidence.manifest"
    data = env["data"]
    assert data["run_id"] == "run-a" and data["store"] == "present" and data["absence"] is None
    with open(os.path.join(directory, "manifest.json"), encoding="utf-8") as handle:
        assert data["manifest"] == json.load(handle) and data["manifest"]["capture"] == "full"
    with open(os.path.join(directory, "seal.json"), encoding="utf-8") as handle:
        assert data["seal"] == json.load(handle)
    assert data["seal"]["final_seq"] == 3 and data["seal"]["complete"] is True and data["seal"]["gaps"] == 0
    assert data["line_count"] == 3 and data["blob_file_count"] == 3
    expected_bytes = sum(os.lstat(os.path.join(root, name)).st_size for root, _d, files in os.walk(directory) for name in files)
    assert data["bytes_on_disk"] == expected_bytes > 0
    assert env["consistency"] == {"kind": "live_observation", "live_stream": False}
    assert env["source"]["state_directory"] == state_dir() and env["source"]["evidence_directory"] == store_root(state_dir())
    assert "trace_database" in env["source"]


def test_unsealed_store_is_reported_unsealed_without_claiming_a_run_is_active():
    write_store("run-b", seal=False)
    data = _manifest("run-b")["data"]
    assert data["store"] == "present" and data["seal"] is None and data["line_count"] == 3
    assert "active" not in json.dumps(data).lower() and "running" not in json.dumps(data).lower()


def test_digest_only_capture_has_a_manifest_capture_and_no_blob_files():
    write_store("run-c", capture=model.CAPTURE_DIGEST_ONLY)
    data = _manifest("run-c")["data"]
    assert data["manifest"]["capture"] == "digest_only" and data["blob_file_count"] == 0 and data["line_count"] == 3


def test_recorder_gaps_are_reported_from_the_seal_verbatim():
    write_store("run-d", gaps=2)
    seal = _manifest("run-d")["data"]["seal"]
    assert seal["gaps"] == 2 and seal["complete"] is False and seal["degraded"] == []


def test_the_operation_creates_and_touches_nothing():
    write_store("run-e")
    before = _tree(state_dir())
    for _ in range(2):
        assert _manifest("run-e")["error"] is None
        assert _manifest("run-missing")["error"] is None
    assert _tree(state_dir()) == before


def test_absent_store_without_a_snapshot_is_store_absent_with_unknown_reason():
    env = _manifest("no-such-run")
    data = env["data"]
    assert env["error"] is None and data["store"] == "absent" and data["manifest"] is None and data["seal"] is None
    assert data["line_count"] is None and data["bytes_on_disk"] is None and data["blob_file_count"] is None
    assert data["absence"]["reason"] == "unknown" and "snapshot" in data["absence"]["detail"]


@pytest.mark.parametrize("details,reason", [
    (None, "pre_recorder"),
    ({"run_id": "r", "status": "DISABLED", "capture": "off", "reason": "capture off"}, "capture_off"),
    ({"run_id": "r", "status": "RECORDER_UNAVAILABLE", "capture": "full", "reason": "disk full"}, "recorder_unavailable"),
    ({"run_id": "r", "status": "OPEN", "capture": "full", "seq_at_row": 4, "path": "/state/attempt-evidence/r"}, "pruned"),
    ({"run_id": "r", "status": "CLOSED", "capture": "full", "seq_at_row": 9, "path": "/state/attempt-evidence/r"}, "pruned"),
    ({"run_id": "r", "status": "PENDING", "capture": "full"}, "unknown"),
    ({"run_id": "r", "status": "PENDING", "capture": "full", "path": "/state/attempt-evidence/r"}, "unknown"),  # only OPEN/CLOSED establish a store existed
    ({"status": "NO_RUN_SCOPE"}, "unknown"),
    ({"run_id": "r", "status": "OPEN", "capture": "full"}, "unknown"),
])
def test_absence_is_derived_from_the_recorded_event_and_labelled_derived(details, reason):
    seed_trace_run("run-f", details, with_event=details is not None)
    snapshot_id = acquire_snapshot_id()
    env = _manifest("run-f", snapshot_id)
    absence = env["data"]["absence"]
    assert env["data"]["store"] == "absent" and absence["reason"] == reason
    assert absence["derived_from"] == "history.detail run_events[evidence.attempt_store]" and absence["snapshot_id"] == snapshot_id
    assert absence["recorded_event"] == details  # the event details verbatim (None when the event is absent)
    assert env["source"]["snapshot_id"] == snapshot_id


def test_absence_for_a_run_not_in_the_snapshot_is_unknown_never_pre_recorder():
    seed_trace_run("run-g", None, with_event=False)
    absence = _manifest("run-other", acquire_snapshot_id())["data"]["absence"]
    assert absence["reason"] == "unknown" and "not in snapshot" in absence["detail"]


def test_a_present_store_has_no_absence_even_when_the_snapshot_says_pruned():
    write_store("run-h")
    seed_trace_run("run-h", {"run_id": "run-h", "status": "CLOSED", "capture": "full", "path": "/gone"})
    data = _manifest("run-h", acquire_snapshot_id())["data"]
    assert data["store"] == "present" and data["absence"] is None


def test_unknown_snapshot_is_the_v1_typed_error_not_a_guess():
    env = _manifest("run-x", "20260101T000000000000Z-00000000")
    assert env["error"]["code"] == policy.SNAPSHOT_MISSING and env["data"] is None


@pytest.mark.parametrize("bad", ["", ".hidden", "../x", "a/b", "a b", "x" * 300, "; rm -rf /", None, 7])
def test_run_id_is_validated_before_any_lookup(bad):
    os.makedirs(os.path.join(store_root(state_dir()), ".hidden"), exist_ok=True)
    with open(os.path.join(store_root(state_dir()), ".hidden", "manifest.json"), "w", encoding="utf-8") as handle:
        handle.write('{"schema": "kriya.attempt_evidence/1"}')
    env = _manifest(bad)
    assert env["error"]["code"] == policy.INVALID_REQUEST and env["data"] is None


def test_a_valid_kup_run_id_the_recorder_would_never_have_stored_is_simply_absent():
    env = _manifest("a:b")  # RUN_ID_RE allows ':' (trace rows), the recorder's own pattern does not
    assert env["error"] is None and env["data"]["store"] == "absent"


def test_a_corrupt_manifest_is_a_typed_unreadable_error_never_a_partial_body():
    directory = write_store("run-i")
    with open(os.path.join(directory, "manifest.json"), "wb") as handle:
        handle.write(b"{not json")
    env = _manifest("run-i")
    assert env["error"]["code"] == policy.EVIDENCE_STORE_UNREADABLE and env["error"]["path_kind"] == "manifest" and env["data"] is None


def test_an_unsupported_manifest_schema_is_refused_with_the_schema_seen():
    directory = write_store("run-j")
    with open(os.path.join(directory, "manifest.json"), "wb") as handle:
        handle.write(b'{"schema": "kriya.attempt_evidence/99"}')
    env = _manifest("run-j")
    assert env["error"]["code"] == policy.EVIDENCE_SCHEMA_UNSUPPORTED and env["error"]["schema_seen"] == "kriya.attempt_evidence/99"


def test_an_unreadable_directory_is_a_typed_error(monkeypatch):
    write_store("run-k")
    real_open = open

    def deny(path, *args, **kwargs):
        if str(path).endswith("manifest.json"):
            raise PermissionError(13, "denied")
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr("builtins.open", deny)
    env = _manifest("run-k")
    assert env["error"]["code"] == policy.EVIDENCE_STORE_UNREADABLE and env["error"]["path_kind"] == "manifest"
