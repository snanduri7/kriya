"""LR-R1-M1.11: the legacy importer (design §8, §13; invariant I-3; test T16).

A synthetic pre-M1 run (the CAGC-v2 evidence shape: traces.json rows,
run.txt, identity.pre.json, workspace.diff, untracked.tar with the control
archive) is imported. Everything the fixture holds is carried verbatim as
LEGACY_RECONSTRUCTED; everything it lacks - prompt, raw response,
per-attempt diff, authority snapshot, passing-gate output, recovery
decision - is an explicit not_recorded record with its reason, and nothing
else is. The final workspace.diff is never attributed to an attempt.
"""
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

from kriya.core.attempt_evidence import model, reader
from kriya.core.attempt_evidence.explain import explain_run

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmarks" / "reliability"))
import import_legacy  # noqa: E402 - benchmarks/ is not a package

FINAL_DIFF = "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1 +1,2 @@\n x\n+y\n"
GATES = [
    {"attempt": 1, "type": "test", "success": False, "output": "1 failed", "mode": "full_set"},
    {"attempt": 2, "type": "compile", "success": True, "output": "ok", "mode": "targeted"},
    {"attempt": 2, "type": "test", "success": True, "output": "1 passed", "mode": "targeted"},
]
EVENTS = [
    {"kind": "egress.authority", "attempt": 0, "source": "workflow", "details": {"x": 1}},
    {"kind": "attempt.started", "attempt": 1, "source": "workflow", "details": {"mode": "full_set"}},
    {"kind": "failure.recorded", "attempt": 1, "source": "quality_gate", "failure_type": "test",
     "message": "AssertionError: 2 != 3", "details": {"likely_files": ["calc.py"]}},
    {"kind": "retry.progress_vector", "attempt": 1, "source": "retry", "details": {"changed_dimensions": []}},
    {"kind": "attempt.started", "attempt": 2, "source": "workflow", "details": {"mode": "targeted"}},
]


def _fixture(root: Path) -> Path:
    run = root / "evidence" / "case.r1"
    run.mkdir(parents=True)
    rows = [
        {"run_id": "row-1", "goal": "add sub", "status": "success", "failure_category": None, "attempts": 2,
         "gate_outcomes": json.dumps(GATES), "run_events": json.dumps(EVENTS), "prompt_rendered": "PLAN PROMPT",
         "model_hops": json.dumps(["m:1", "m:1"]), "timestamp": "2026-10-04 07:10:00"},
        {"run_id": "R1.enforce", "goal": "add sub", "status": "success", "failure_category": None, "attempts": 0,
         "gate_outcomes": "[]", "run_events": "[]", "prompt_rendered": "", "model_hops": "[]",
         "timestamp": "2026-10-04 07:11:00"},
    ]
    (run / "traces.json").write_text(json.dumps(rows))
    (run / "run.txt").write_text("== A case [r1] start\n")
    (run / "identity.pre.json").write_text(json.dumps({"kriya": "61a867f"}))
    (run / "workspace.diff").write_text(FINAL_DIFF)
    with tarfile.open(run / "untracked.tar", "w") as tar:
        for name, data in ((".kriya/control/runs/R1.json", json.dumps({"run_id": "R1", "terminal_status": "SUCCESS",
                                                                       "lifecycle_state": "SETTLED"})),
                           (".kriya/control/decisions.jsonl", json.dumps({"kind": "plan.validated"}) + "\n"),
                           (".kriya/worktree/calc.py", "x\n"),
                           # A macOS archiver's AppleDouble member (measured in the CAGC-v2 archives).
                           (".kriya/control/runs/._R1.json", "\x00\x05\x16\x07\x00\x02Mac OS X")):
            info = tarfile.TarInfo(name)
            info.size = len(data.encode())
            tar.addfile(info, io.BytesIO(data.encode()))
    return run


def _import(tmp_path):
    run = _fixture(tmp_path)
    state = tmp_path / "out-state"
    run_id = import_legacy.import_run(str(run), str(state))
    store = reader.open_run(str(state), run_id)
    return state, run_id, store, list(store.records())


def test_the_import_is_a_verified_store_of_legacy_and_not_recorded_records_only(tmp_path):
    _state, run_id, store, records = _import(tmp_path)
    assert run_id == "legacy-R1" and store.verify().status == reader.VERIFIED
    assert {r["provenance"] for r in records} == {model.LEGACY_RECONSTRUCTED, model.NOT_RECORDED}


def test_every_absent_field_is_not_recorded_and_nothing_else_is(tmp_path):
    _state, _run_id, _store, records = _import(tmp_path)
    absent = {}
    for record in records:
        if record["kind"] == "not_recorded":
            absent.setdefault(record["attempt_number"], set()).add(record["payload"]["field"])
            assert record["payload"]["reason"]
    # Pinned here, never read from the importer's own table (design §13 M1.11 list).
    per_attempt = {"model.request", "authority.snapshot", "model.response", "candidate.change",
                   "recovery.decision", "retry.delta", "fallback.decision", "attempt.concluded"}
    assert absent == {
        1: per_attempt | {"gate.result(passing)"},     # attempt 1 recorded only a failing check
        2: per_attempt,                                  # attempt 2's passing checks are in the trace
    }
    # Nothing that is absent was also synthesized.
    kinds_by_attempt = {(r["attempt_number"], r["kind"]) for r in records if r["provenance"] == model.LEGACY_RECONSTRUCTED}
    for attempt in (1, 2):
        for kind in ("model.request", "model.response", "authority.snapshot", "candidate.change",
                     "recovery.decision", "retry.delta", "fallback.decision", "attempt.concluded"):
            assert (attempt, kind) not in kinds_by_attempt, (attempt, kind)


def test_the_final_diff_is_run_level_never_an_attempts(tmp_path):
    _state, _run_id, store, records = _import(tmp_path)
    changes = [r for r in records if r["kind"] == "candidate.change"]
    assert len(changes) == 1 and changes[0]["attempt_number"] is None
    assert changes[0]["payload"]["decision"] == "RUN_FINAL_DIFF"
    assert store.blob(changes[0]["blobs"]["diff"]).decode() == FINAL_DIFF


def test_what_the_trace_held_is_carried_verbatim(tmp_path):
    _state, _run_id, store, records = _import(tmp_path)
    gates = [r for r in records if r["kind"] == "gate.result"]
    assert [(r["attempt_number"], r["payload"]["gate"], r["payload"]["success"]) for r in gates] == \
        [(g["attempt"], g["type"], g["success"]) for g in GATES]
    assert [store.blob(r["blobs"]["output"]).decode() for r in gates] == [g["output"] for g in GATES]
    mirrored = [r["payload"] for r in records if r["kind"] == "mirror.event"]
    assert sorted(json.dumps(e, sort_keys=True) for e in mirrored) == sorted(json.dumps(e, sort_keys=True)
                                                                             for e in EVENTS)
    [diagnosis] = [r for r in records if r["kind"] == "diagnosis"]
    assert diagnosis["attempt_number"] == 1 and store.blob(diagnosis["blobs"]["message"]).decode() == \
        "AssertionError: 2 != 3"
    assert [r["payload"]["kind"] for r in records if r["kind"] == "mirror.decision"] == ["plan.validated"]
    [closed] = [r for r in records if r["kind"] == "run.closed"]
    assert closed["payload"]["terminal_status"] == "SUCCESS"


def test_explain_reports_legacy_absences_with_their_reasons(tmp_path):
    state, run_id, _store, _records = _import(tmp_path)
    result = explain_run(str(state), run_id)
    attempts = {a["attempt"]: a["answers"] for a in result["attempts"]}
    first = attempts[1]
    assert first["Q1"] == {"status": "NOT_RECORDED", "reason": "pre-M1 traces kept no Developer prompt or request body"}
    assert first["Q4"]["status"] == "NOT_RECORDED" and "run-level" in first["Q4"]["reason"]
    assert first["Q5"]["status"] == "RECORDED"                     # the failing check is in the trace
    assert first["Q6"]["status"] == "NOT_RECORDED" and "RecoveryDecision" in first["Q6"]["reason"]


def test_the_output_must_be_outside_the_legacy_evidence(tmp_path):
    run = _fixture(tmp_path)
    with pytest.raises(import_legacy.LegacyImportError):
        import_legacy.import_run(str(run), str(run / "state"))
    assert not (run / "state").exists()


def test_the_legacy_evidence_is_never_written(tmp_path):
    run = _fixture(tmp_path)
    before = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
    import_legacy.import_run(str(run), str(tmp_path / "out"))
    assert {p: p.read_bytes() for p in run.rglob("*") if p.is_file()} == before
