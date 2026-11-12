"""ENFORCE-OWNER-RECOVERY-DEPENDENT-VERIFIER-REGENERATED-001 (owner rulings 2026-11-12; Scenario-A run 7, finding R7-B): the REAL unit.

The enforce loop's side is tests/test_enforce_owner_recovery_verifier_replay_001.py (unit boundary scripted). Here the real WorkflowController and
the real ``run_generation_workflow`` run the run-7 shape with only the model transport and the toolchain gates scripted
(tests/_enforce_unit_harness.py): an owner unit writes ``lib.py``; a consumer unit that depends on it writes ``tests/test_lib.py``; the consumer's
tests fail INSIDE the owner's file until the owner is repaired.

Pinned on the real path:
  * a failed consumer hands its exact staged candidate back (captured before its sandbox is removed) and the loop replays it;
  * a replay makes NO Developer request; the carried bytes are staged by candidate authority (the candidate.change / write path of model output)
    and their staged digests are the carried ones;
  * the person is asked to approve the source again, the gates run again against the repaired owner, and the result is committed normally;
  * a replay that fails without naming the owner stops the unit typed (no Developer repair of the verifier) and reopens the owner.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import patch

import pytest
from _enforce_unit_harness import PASSING, run_enforce

from kriya.repository.snapshot import raw_digest
from kriya.workflow import verifier_replay
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.plan_schema import EngineeringPlan
from kriya.workflow.verifier_replay import CarriedFile, RefusedCandidate, ReplayHandle, capture_refused_candidate

LIB, TEST = "src/main/java/com/example/Lib.java", "src/test/java/com/example/LibTest.java"
POM = ('<project xmlns="http://maven.apache.org/POM/4.0.0">\n  <modelVersion>4.0.0</modelVersion>\n  <groupId>com.example</groupId>\n'
       "  <artifactId>lib</artifactId>\n  <version>1.0</version>\n  <properties>\n    <maven.compiler.release>17</maven.compiler.release>\n"
       "  </properties>\n</project>\n")
TEST_SOURCE = ("package com.example;\n\nclass LibTest {\n    @org.junit.jupiter.api.Test\n    void testRoundTrip() {\n"
               "        byte[] wire = new Lib().encode(1L);\n        org.junit.jupiter.api.Assertions.assertEquals(9, wire.length);\n    }\n}\n")


def _lib(body: str) -> str:
    return f"package com.example;\n\npublic class Lib {{\n    {body}\n}}\n"


BROKEN = _lib("public byte[] encode(long time) { return java.nio.ByteBuffer.allocate(9).putInt(0).putLong(time).array(); }")
STILL_BROKEN = _lib("public byte[] encode(long time) { return java.nio.ByteBuffer.allocate(9).put((byte) 0).putLong(time).array(); }  // length fixed")
FIXED = _lib("public byte[] encode(long time) { return java.nio.ByteBuffer.allocate(9).put(new byte[5]).putInt((int) time).array(); }  // FIXED")
RENAMED = _lib("public byte[] encodeValue(long time) { return new byte[9]; }")


def _plan() -> EngineeringPlan:
    def unit(sid: str, path: str, tool: str, **more: Any) -> Dict[str, Any]:
        return {"id": sid, "description": f"write {path}", "execution_method": "model", "planned_files": [{"path": path, "action": "create"}],
                "relevant_global_invariant_ids": ["gi1"], "verification": [{"type": "tool", "tool_name": tool, "description": tool}], **more}
    return EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}], "acceptance_criteria": [],
        "subtasks": [unit("s3", LIB, "compile", provides=["encoder"]),
                     unit("s6", TEST, "test", depends_on=["s3"], requires=["encoder"], provides=["encoder-test"])]})


def _owner_located(tree: Path) -> Dict[str, Any]:
    """The run-7 failure shape: the test's own gate output, with the owner's frame under the JDK's."""
    return {"success": False, "output": (
        "[ERROR] Tests run: 1, Failures: 0, Errors: 1, Skipped: 0 <<< FAILURE! -- in com.example.LibTest\n"
        "[ERROR] com.example.LibTest.testRoundTrip -- Time elapsed: 0.01 s <<< ERROR!\n"
        "java.nio.BufferOverflowException\n"
        "\tat java.base/java.nio.HeapByteBuffer.putLong(HeapByteBuffer.java:498)\n"
        "\tat com.example.Lib.encode(Lib.java:4)\n"
        "\tat com.example.LibTest.testRoundTrip(LibTest.java:6)\n"
        "[ERROR] Tests run: 1, Failures: 0, Errors: 1, Skipped: 0\n")}


def _import_error(tree: Path) -> Dict[str, Any]:
    """A failure that names no owner frame: the carried test no longer compiles against the owner."""
    return {"success": False, "output": (
        "[ERROR] COMPILATION ERROR :\n"
        f"[ERROR] /kriya/workspace/{TEST}:[6,33] cannot find symbol\n"
        "  symbol:   method encode(long)\n  location: class com.example.Lib\n")}


def _tests_gate(validator: Any) -> Dict[str, Any]:
    tree = Path(validator.workspace_path)
    if not (tree / TEST).exists():
        return PASSING                         # the owner's own unit: nothing of its own to run
    lib = (tree / LIB).read_text()
    if "FIXED" in lib:
        return PASSING
    return _import_error(tree) if "encodeValue" in lib else _owner_located(tree)


def _run(tmp_path: Path, owner_sources: List[str], **kwargs: Any) -> Any:
    sources = iter(owner_sources)
    with patch("kriya.tools.lsp.find_jdtls", return_value=None):       # no language server: the gates are scripted, nothing here starts a JVM
        return run_enforce(tmp_path, plan=_plan(), goal="Write an encoder and its test",
                           units={"s3": (LIB, lambda: next(sources)), "s6": (TEST, TEST_SOURCE)}, on_tests=_tests_gate,
                           base_files={"pom.xml": POM, ".gitignore": "target/\n"}, **kwargs)


def _events(run: Any, kind: str) -> List[Any]:
    return [event for event in run.events if event.kind == kind]


def test_real_run7_shape_the_consumers_refused_candidate_is_replayed_without_the_developer(tmp_path):
    run = _run(tmp_path, [BROKEN, STILL_BROKEN, FIXED])
    legacy = run.result.legacy_result
    assert legacy["status"] == "success", legacy.get("reason_codes")
    # ONE Developer request for the consumer in the whole run: its two later invocations replayed the first candidate
    assert run.developer_calls.count("s6") == 1 and run.developer_calls.count("s3") == 3
    replays = _events(run, "verifier.replayed")
    assert len(replays) == 2
    for event in replays:
        assert event.details["paths"] == [TEST]
        assert event.details["digests"] == {TEST: raw_digest(TEST_SOURCE.encode("utf-8"))}
    # the committed verifier is byte for byte the candidate that first disproved the owner
    assert (run.workspace / TEST).read_bytes() == TEST_SOURCE.encode("utf-8")
    assert (run.workspace / LIB).read_text() == FIXED
    assert [event["owner_recovery_passed"] for event in legacy["plan_recovery_events"]] == [False, True]
    assert {item.subtask_id: item.status.value for item in run.result.subtask_results} == {"s3": "completed", "s6": "completed"}


def test_real_replay_reruns_the_approval_and_the_gates_and_reuses_neither(tmp_path):
    """Items 22-26, 36-38: the earlier source approval is not reused (it was attempt-scoped and bound to a base the owner changed), and no
    gate result is carried: the person is asked again for the same bytes and the tests run again on the repaired tree."""
    run = _run(tmp_path, [BROKEN, STILL_BROKEN, FIXED])
    consumer_approvals = [(unit, paths) for unit, paths, _reason in run.approvals if unit == "s6"]
    assert consumer_approvals == [("s6", [TEST])] * 3            # the generated candidate, then each of the two replays
    tests_runs = [kind for kind, _ in run.builds if kind == "tests"]
    assert len(tests_runs) >= 3 + 3                                # every s6 invocation and every s3 invocation ran its tests gate
    refused = [outcome for outcome in run.gate_outcomes if outcome.get("success") is False]
    assert len(refused) == 2 and all("BufferOverflowException" in str(outcome.get("output")) for outcome in refused)


def test_real_replay_that_fails_without_naming_the_owner_stops_typed_and_reopens_the_owner(tmp_path):
    """Items 22-29, 65: the first repair renames the function; the carried test no longer imports. The unit makes no Developer request to
    'fix' its test; it stops typed, and the loop gives the new evidence to the owner, whose next repair passes the SAME test."""
    run = _run(tmp_path, [BROKEN, RENAMED, FIXED])
    legacy = run.result.legacy_result
    assert legacy["status"] == "success", legacy.get("reason_codes")
    assert run.developer_calls.count("s6") == 1                   # the verifier was never rewritten
    stops = [event for event in _events(run, "retry.no_progress_terminal")
             if event.details.get("reason_code") == verifier_replay.VERIFIER_REPLAY_FAILED]
    assert len(stops) == 1 and stops[0].details["declared_by"]
    assert (run.workspace / TEST).read_bytes() == TEST_SOURCE.encode("utf-8")
    assert [event["owner_recovery_passed"] for event in legacy["plan_recovery_events"]] == [False, True]


def test_real_run_whose_owner_is_never_repaired_ends_at_the_unchanged_cap_with_one_consumer_generation(tmp_path):
    """Items 35-40, 50-54: three owner recoveries, as before; SAFE FAILURE; the consumer was generated once."""
    run = _run(tmp_path, [BROKEN, STILL_BROKEN, STILL_BROKEN + "// again\n", STILL_BROKEN + "// and again\n", FIXED])
    legacy = run.result.legacy_result
    assert legacy["status"] != "success"
    assert run.developer_calls.count("s6") == 1
    assert run.developer_calls.count("s3") == 4                   # the planned run and exactly three recoveries
    assert len(_events(run, "verifier.replayed")) == 3
    assert [event["owner_recovery_passed"] for event in legacy["plan_recovery_events"]] == [False, False, False]
    assert {item.subtask_id: item.status.value for item in run.result.subtask_results}["s3"] == "needs_review"
    assert not (run.workspace / TEST).exists() and not (run.workspace / LIB).exists()     # nothing committed


def test_real_replay_whose_staged_bytes_are_not_the_carried_ones_is_refused_before_any_gate_and_the_consumer_regenerated(tmp_path):
    """Items 9, 46, 62 on the real path: the check sits between candidate authority's staging and the first gate. Here it is told the staged
    bytes differ (once); the replay unit runs no gate and stops typed; the loop discards the handle and generates the consumer again."""
    real = verifier_replay.staged_bytes_mismatch
    answers = iter([[TEST]])

    def mismatch_once(handle, staged, written):
        return next(answers, None) or real(handle, staged, written)

    with patch.object(verifier_replay, "staged_bytes_mismatch", side_effect=mismatch_once):
        run = _run(tmp_path, [BROKEN, FIXED])
    assert run.result.legacy_result["status"] == "success"
    refusals = [outcome for outcome in run.gate_outcomes if outcome.get("type") == "verifier_replay_refused"]
    assert len(refusals) == 1 and verifier_replay.REPLAY_BYTES_MISMATCH in str(refusals[0].get("output"))
    assert run.developer_calls.count("s6") == 2          # the planned generation, then a regeneration after the refused replay
    assert len(_events(run, "verifier.replayed")) == 1


# ----------------------------------------------------------------------------- the capture and the staged-bytes check

def _state(tree: Path, files: Dict[str, bytes], **over: Any) -> SimpleNamespace:
    for rel, data in files.items():
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        (tree / rel).write_bytes(data)
    return SimpleNamespace(
        all_files_written=set(files), candidate_digests={rel: raw_digest(data) for rel, data in files.items()},
        last_failure=SimpleNamespace(type="targeted_test", raw_output="boom"), verified_candidate_binding=SimpleNamespace(digest="b"), **over)


def test_capture_keeps_exactly_the_staged_bytes(tmp_path):
    data = TEST_SOURCE.encode("utf-8")
    refused = capture_refused_candidate(_state(tmp_path, {TEST: data}), str(tmp_path))
    assert refused is not None and refused.paths == (TEST,)
    assert refused.files[0].content.encode("utf-8") == data and refused.files[0].raw_sha256 == raw_digest(data)
    assert (refused.binding_digest, refused.failure_type) == ("b", "targeted_test")
    assert refused.failure_digest == verifier_replay.failure_identity("targeted_test", "boom")


@pytest.mark.parametrize("case", ["not_utf8", "crlf_kept", "changed_after_staging", "missing", "nothing_written", "no_staged_digest"])
def test_capture_carries_nothing_unless_every_file_is_exact(tmp_path, case):
    data = b"\xff\xfe not utf-8" if case == "not_utf8" else b"a = 1\r\nb = 2\r\n"
    state = _state(tmp_path, {TEST: data})
    if case == "changed_after_staging":
        (tmp_path / TEST).write_bytes(data + b"# touched\n")
    elif case == "missing":
        (tmp_path / TEST).unlink()
    elif case == "nothing_written":
        state.all_files_written = set()
    elif case == "no_staged_digest":
        state.candidate_digests = {}
    refused = capture_refused_candidate(state, str(tmp_path))
    if case == "crlf_kept":                # exact means exact: no newline translation on the way in or out
        assert refused is not None and refused.files[0].content.encode("utf-8") == data and refused.files[0].intact()
    else:
        assert refused is None


def _carried(text: str = TEST_SOURCE) -> ReplayHandle:
    candidate = RefusedCandidate(files=(CarriedFile(TEST, text, raw_digest(text.encode("utf-8"))),), binding_digest=None,
                                 failure_type="targeted_test", failure_digest="d")
    return ReplayHandle(run_id="r", plan_hash="p", unit_id="s6", goal_digest="g", verification_contract_digest="c", candidate=candidate)


@pytest.mark.parametrize("staged,written,wrong", [
    ({TEST: raw_digest(TEST_SOURCE.encode("utf-8"))}, {TEST}, []),
    ({TEST: raw_digest(b"other bytes")}, {TEST}, [TEST]),
    ({}, set(), [TEST, TEST]),
    ({TEST: raw_digest(TEST_SOURCE.encode("utf-8")), "extra.py": "x"}, {TEST, "extra.py"}, ["extra.py"]),
])
def test_the_attempt_refuses_a_staged_candidate_that_is_not_the_carried_one(staged, written, wrong):
    from kriya.workflow.attempt import _require_the_staged_candidate_is_the_carried_one

    outcomes: List[Any] = []
    state = SimpleNamespace(candidate_digests=staged, all_files_written=written, attempt_number=1, record_gate_outcome=outcomes.append)
    ctx = SimpleNamespace(carried_candidate=_carried())
    assert verifier_replay.staged_bytes_mismatch(ctx.carried_candidate, staged, written) == wrong
    if not wrong:
        _require_the_staged_candidate_is_the_carried_one(state, ctx)
        assert outcomes == []
        return
    with pytest.raises(QualityGateFailure) as refused:
        _require_the_staged_candidate_is_the_carried_one(state, ctx)
    assert refused.value.failure.type == "verifier_replay_refused"
    assert refused.value.failure.diagnostics["reason_code"] == verifier_replay.REPLAY_BYTES_MISMATCH
    assert len(outcomes) == 1
