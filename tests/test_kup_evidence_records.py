"""KUP ``evidence.records`` (Run Cockpit v1.1 slice 1A-2, 04 sections 4.3 and 5): paged, filtered, verbatim records of a verified
store; cursor bound to run, filters and chain head; refusal on a store whose chain or blobs do not verify; reader-computed
``phase_provenance`` that never claims more than was recorded."""
from __future__ import annotations

import json
import os

import pytest
from _kup_evidence_fixtures import rewrite_record_line, rich_store, state_dir
from _strict_doubles import strict_config

from kriya.core.attempt_evidence import model, reader
from kriya.core.attempt_evidence import scope as evidence_scope
from kriya.kup import policy
from kriya.kup.cli_ops import run_kup_operation

TOTAL = 15


def _records(run_id, **params):
    return run_kup_operation(strict_config(), {"operation": "evidence.records", "run_id": run_id, **params})


def _all(run_id, **params):
    """Every record by following the cursor; returns (records, pages)."""
    out, pages, cursor = [], 0, None
    while pages < 50:  # a paging bug must fail the test, not hang it
        env = _records(run_id, **({"limit": 4, **params} | ({"cursor": cursor} if cursor else {})))
        assert env["error"] is None, env
        out += env["data"]["records"]
        pages += 1
        cursor = env["data"]["next_cursor"]
        if cursor is None:
            return out, pages
    pytest.fail("paging did not terminate")


def _stored_lines(directory):
    with open(os.path.join(directory, "records.jsonl"), "rb") as handle:
        return [json.loads(line) for line in handle.read().split(b"\n") if line]


def test_paging_is_complete_ordered_and_stops_with_a_null_cursor():
    rich_store("r-page")
    records, pages = _all("r-page")
    assert [r["seq"] for r in records] == list(range(1, TOTAL + 1)) and pages == 4


def test_records_are_verbatim_apart_from_the_reader_computed_phase_provenance():
    directory, _writer = rich_store("r-verb")
    got = _records("r-verb", limit=1000)["data"]["records"]
    for stored, served in zip(_stored_lines(directory), got, strict=True):
        assert {k: v for k, v in served.items() if k != "phase_provenance"} == stored
        assert "phase_provenance" not in stored  # never stored


def test_sealed_store_is_a_sealed_store_observation_bound_to_the_head():
    directory, _writer = rich_store("r-seal")
    env = _records("r-seal")
    with open(os.path.join(directory, "seal.json"), encoding="utf-8") as handle:
        head = json.load(handle)["head_digest"]
    assert env["data"]["head_digest"] == head and env["consistency"] == {"kind": "sealed_store", "live_stream": False, "head_digest": head}
    assert env["operation"] == "evidence.records" and env["data"]["truncated_tail"] is False and env["data"]["run_id"] == "r-seal"


@pytest.mark.parametrize("filters,expected", [
    ({"unit": "u1"}, [4, 5, 6, 7, 8, 9, 10, 11]),
    ({"unit": "u2"}, [12, 13]),
    ({"phase": "planning"}, [2, 3]),
    ({"attempt": 1}, [5, 6, 7, 8, 13]),
    ({"attempt": 2}, [9, 10]),
    ({"role": "developer"}, [6, 7]),
    ({"kind": ["gate.result"]}, [8, 10]),
    ({"from_seq": 14}, [14, 15]),
    ({"unit": "u1", "attempt": 1, "kind": ["gate.result"]}, [8]),   # conjunctive
    ({"unit": "u1", "role": "reviewer"}, []),                       # conjunctive, empty
])
def test_filters_are_conjunctive_and_exact(filters, expected):
    rich_store("r-filt")
    env = _records("r-filt", **filters)
    assert [r["seq"] for r in env["data"]["records"]] == expected
    assert env["data"]["filters_applied"] == filters


def test_kind_filter_matches_exact_kinds_only():
    rich_store("r-kind")
    seqs = [r["seq"] for r in _records("r-kind", kind=["model.request", "model.response"])["data"]["records"]]
    stored = [r["seq"] for r in _records("r-kind", limit=1000)["data"]["records"] if r["kind"] in ("model.request", "model.response")]
    assert seqs == stored and len(seqs) == 6


@pytest.mark.parametrize("bad", [
    {"limit": 0}, {"limit": policy.EVIDENCE_PAGE_MAX + 1}, {"limit": "5"}, {"limit": True}, {"limit": 1.5},
    {"attempt": -1}, {"attempt": "1"}, {"attempt": True}, {"from_seq": 0}, {"from_seq": "3"},
    {"unit": ""}, {"unit": 5}, {"unit": "x" * 300}, {"phase": ["a"]}, {"role": "x" * 300},
    {"kind": "gate.result"}, {"kind": ["not.a.kind"]}, {"kind": [5]}, {"kind": []},
    {"cursor": "!!!"}, {"cursor": 7}, {"cursor": "a" * 2000}, {"cursor": "e30"},
])
def test_invalid_parameters_are_refused_before_any_io(bad, monkeypatch):
    rich_store("r-bad")
    monkeypatch.setattr(reader.EvidenceRun, "verify", lambda *a, **k: pytest.fail("verified before validating"))
    env = _records("r-bad", **bad)
    assert env["error"]["code"] == policy.INVALID_REQUEST and env["data"] is None


def test_cursor_is_bound_to_the_run_and_the_filters():
    rich_store("r-cur-a")
    rich_store("r-cur-b")
    cursor = _records("r-cur-a", limit=2, unit="u1")["data"]["next_cursor"]
    assert cursor
    assert _records("r-cur-b", limit=2, unit="u1", cursor=cursor)["error"]["code"] == policy.INVALID_REQUEST        # another run
    assert _records("r-cur-a", limit=2, unit="u2", cursor=cursor)["error"]["code"] == policy.INVALID_REQUEST        # other filters
    assert _records("r-cur-a", limit=2, cursor=cursor)["error"]["code"] == policy.INVALID_REQUEST                   # dropped filters
    assert _records("r-cur-a", limit=2, unit="u1", cursor=cursor)["error"] is None


def test_unsealed_store_is_a_live_observation_and_a_grown_head_invalidates_the_cursor():
    _directory, writer = rich_store("r-live", keep_open=True)
    first = _records("r-live", limit=5)
    assert first["error"] is None and first["consistency"] == {"kind": "live_observation", "live_stream": False}
    assert first["data"]["head_digest"] and first["data"]["records"][-1]["seq"] == 5
    writer.append("model.request", {"n": 99}, identity={"phase": "planning", "role": "planner", "call_seq": 9})
    again = _records("r-live", limit=5, cursor=first["data"]["next_cursor"])
    assert again["error"]["code"] == policy.EVIDENCE_HEAD_CHANGED and again["data"] is None
    assert again["error"]["head_then"] == first["data"]["head_digest"] and again["error"]["head_now"] != again["error"]["head_then"]
    assert "active" not in json.dumps(first["data"]).lower()


def test_a_partial_tail_on_an_unsealed_store_is_reported_not_served_as_a_record():
    directory, _writer = rich_store("r-tail", keep_open=True)
    with open(os.path.join(directory, "records.jsonl"), "ab") as handle:
        handle.write(b'{"schema": "kriya.attempt')
    env = _records("r-tail", limit=1000)
    assert env["error"] is None and env["data"]["truncated_tail"] is True and len(env["data"]["records"]) == TOTAL
    assert env["consistency"]["kind"] == "live_observation"


def test_a_partial_tail_on_a_sealed_store_is_a_refusal():
    directory, _writer = rich_store("r-tail2")
    with open(os.path.join(directory, "records.jsonl"), "ab") as handle:
        handle.write(b'{"partial')
    env = _records("r-tail2")
    assert env["error"]["code"] == policy.EVIDENCE_VERIFY_FAILED and env["error"]["status"] == "TRUNCATED_TAIL" and env["data"] is None


def test_a_broken_chain_serves_no_records():
    directory, _writer = rich_store("r-broken")
    rewrite_record_line(directory, 3, lambda record: model.canonical_bytes({**record, "payload": {"tampered": True}}))
    env = _records("r-broken")
    assert env["error"]["code"] == policy.EVIDENCE_VERIFY_FAILED and env["error"]["status"] == "CHAIN_BROKEN" and env["error"]["broken_seq"] == 5
    assert env["data"] is None


def test_a_corrupt_blob_serves_no_records_and_names_the_problem():
    directory, _writer = rich_store("r-blob")
    blobs = sorted(os.path.join(root, name) for root, _d, files in os.walk(os.path.join(directory, "blobs")) for name in files)
    with open(blobs[0], "wb") as handle:
        handle.write(b"not gzip")
    env = _records("r-blob")
    assert env["error"]["code"] == policy.EVIDENCE_VERIFY_FAILED and env["error"]["status"] == "BLOB_CORRUPT" and env["error"]["blob_problems"]


def test_a_seal_naming_another_head_serves_no_records():
    directory, _writer = rich_store("r-sm")
    path = os.path.join(directory, "seal.json")
    with open(path, encoding="utf-8") as handle:
        seal = json.load(handle)
    seal["head_digest"] = "sha256:" + "e" * 64
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(seal, handle)
    assert _records("r-sm")["error"]["status"] == "SEAL_MISMATCH"


def test_a_missing_store_is_a_typed_not_found_error():
    env = _records("r-none")
    assert env["error"]["code"] == policy.EVIDENCE_STORE_NOT_FOUND and env["data"] is None


@pytest.mark.parametrize("bad", ["", ".hidden", "../x", None])
def test_run_id_is_validated_before_any_lookup(bad):
    assert _records(bad)["error"]["code"] == policy.INVALID_REQUEST


def test_a_page_stops_at_the_byte_budget_and_the_cursor_continues_without_loss(monkeypatch):
    rich_store("r-budget")
    one = len(json.dumps(_records("r-budget", limit=1)["data"]["records"][0], separators=(",", ":"), ensure_ascii=False).encode())
    monkeypatch.setattr(policy, "EVIDENCE_PAGE_MAX_BYTES", one * 3)
    env = _records("r-budget", limit=100)
    assert env["data"]["truncated"] is True and 1 <= len(env["data"]["records"]) < 100 and env["data"]["next_cursor"]
    records, _pages = _all("r-budget", limit=100)
    assert [r["seq"] for r in records] == list(range(1, TOTAL + 1))


def test_a_single_record_larger_than_the_budget_is_still_served():
    rich_store("r-one")
    import unittest.mock as mock
    with mock.patch.object(policy, "EVIDENCE_PAGE_MAX_BYTES", 10):
        env = _records("r-one", limit=100)
    assert env["error"] is None and len(env["data"]["records"]) == 1 and env["data"]["truncated"] is True


def test_default_limit_and_advertised_limits():
    rich_store("r-lim")
    assert len(_records("r-lim")["data"]["records"]) == TOTAL
    caps = run_kup_operation(strict_config(), {"operation": "capabilities"})["data"]["limits"]
    assert caps["evidence_page_default"] == policy.EVIDENCE_PAGE_DEFAULT == 200 and caps["evidence_page_max"] == policy.EVIDENCE_PAGE_MAX == 1000
    assert caps["evidence_page_max_bytes"] == policy.EVIDENCE_PAGE_MAX_BYTES <= policy.MAX_RESPONSE_BYTES // 2


def test_the_operation_creates_and_touches_nothing():
    rich_store("r-ro")
    def tree():
        return sorted((os.path.relpath(os.path.join(r, n), state_dir()), os.lstat(os.path.join(r, n)).st_mtime_ns)
                      for r, d, f in os.walk(state_dir()) for n in d + f)
    before = tree()
    _all("r-ro")
    assert tree() == before


# ------------------------------------------------------------------ phase_provenance (04 section 4.3)

def _provenance(run_id, **params):
    return {r["seq"]: r["phase_provenance"] for r in _records(run_id, limit=1000, **params)["data"]["records"]}


def test_phase_provenance_never_claims_more_than_was_recorded():
    rich_store("r-prov")
    got = _provenance("r-prov")
    assert got[1] is None and got[4] is None and got[15] is None                      # run.opened / unit.opened: no phase was recorded
    assert got[2] == got[3] == "derived_from_role"                # planner: role in the role -> phase map, no attempt scope
    assert got[5] == got[6] == got[8] == got[13] == "derived_from_attempt_scope"
    assert got[14] == "role_name_fallback"                        # an unmapped role is shown as its raw name
    assert set(got.values()) <= {None, "explicit", "derived_from_attempt_scope", "derived_from_role", "role_name_fallback"}


def test_explicit_phase_events_mark_only_the_records_between_them():
    rich_store("r-expl", phase_events=True)
    got = _provenance("r-expl")
    kinds = {r["seq"]: r["kind"] for r in _records("r-expl", limit=1000)["data"]["records"]}
    opened = next(s for s, k in kinds.items() if k == "phase.opened")
    closed = next(s for s, k in kinds.items() if k == "phase.closed")
    assert all(got[s] == "explicit" for s in range(opened, closed + 1))
    assert all(got[s] != "explicit" for s in got if not opened <= s <= closed)


def test_the_readers_role_phase_map_is_a_copy_that_cannot_drift():
    assert reader.ROLE_PHASES == evidence_scope._ROLE_PHASES
