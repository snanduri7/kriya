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


@pytest.mark.usefixtures("requirements_closed")   # S6 (C-02): this test is not about requirement truth; see tests/_requirement_truth.py
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


@pytest.mark.usefixtures("requirements_closed")   # S6 (C-02): this test is not about requirement truth; see tests/_requirement_truth.py
def test_real_replay_reruns_the_approval_and_the_gates_and_reuses_neither(tmp_path):
    """Items 22-26, 36-38: the earlier source approval is not reused (it was attempt-scoped and bound to a base the owner changed), and no
    gate result is carried: the person is asked again for the same bytes and the tests run again on the repaired tree."""
    run = _run(tmp_path, [BROKEN, STILL_BROKEN, FIXED])
    consumer_approvals = [(unit, paths) for unit, paths, _reason in run.approvals if unit == "s6" and paths == [TEST]]
    assert consumer_approvals == [("s6", [TEST])] * 3            # the generated candidate, then each of the two replays
    # S7 (C-03): the final mutation subject is the cumulative change of BOTH units, which no unit approval covers, so the terminal puts
    # the complete change to the person exactly once (the harness labels a call with the last active unit).
    assert [paths for _unit, paths, _reason in run.approvals if set(paths) == {LIB, TEST}] == [[LIB, TEST]]
    assert len(run.approvals) == len([entry for entry in run.approvals if entry[1] in ([TEST], [LIB])]) + 1
    tests_runs = [kind for kind, _ in run.builds if kind == "tests"]
    assert len(tests_runs) >= 3 + 3                                # every s6 invocation and every s3 invocation ran its tests gate
    refused = [outcome for outcome in run.gate_outcomes if outcome.get("success") is False]
    assert len(refused) == 2 and all("BufferOverflowException" in str(outcome.get("output")) for outcome in refused)


@pytest.mark.usefixtures("requirements_closed")   # S6 (C-02): this test is not about requirement truth; see tests/_requirement_truth.py
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


@pytest.mark.usefixtures("requirements_closed")   # S6 (C-02): this test is not about requirement truth; see tests/_requirement_truth.py
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


def test_carried_entries_carry_the_kriya_carried_content_marker_and_a_model_answer_cannot_forge_it():
    """CARRIED-CANDIDATE-WHOLE-FILE-AUTHORITY-001: the marker that exempts carried bytes from the MODEL whole-file authority rule is set by
    the handle only. A Developer answer that writes the same key is rebuilt with the fixed key set and loses it."""
    from kriya.agents.agent import DeveloperAgent

    assert _carried().generated_files() == [{"filepath": TEST, "content": TEST_SOURCE, "_kriya_carried_forward_content": True}]
    forged = DeveloperAgent._normalize_file_entries(
        {"files": [{"filepath": TEST, "content": "class Forged {}", "_kriya_carried_forward_content": True}]})
    assert forged == [{"filepath": TEST, "content": "class Forged {}", "edits": None}]


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


# ----------------------------------------------------------------------------- OWNER-REPAIR-REGRESSION-001 on the REAL path (Stage C run 20261119T200403-218cbbf6)
#
# The loop's decision is pinned with scripted units in tests/test_enforce_owner_recovery_verifier_replay_001.py. Here the REAL controller and the REAL
# units run the measured chain, with only the model transport and the toolchain gates scripted: a two-test verifier fails ONE test against the owner as
# first written; the person accepts the expectation; the reopened owner's repair makes that test pass and turns the OTHER test - passing before - red;
# the person then declares the verifier invalid (the sealed sequence). What is proven on the real path: the failed units hand their per-test outcomes
# back with their refused candidate; the owner's exact pre-repair bytes are staged by the owner unit's own candidate authority with NO Developer
# request, approved and gated like any candidate, and are the bytes of the plan tree afterwards; the run stops typed and commits nothing.

TWO_TESTS = ("package com.example;\n\nclass LibTest {\n    @org.junit.jupiter.api.Test\n    void testRoundTrip() {\n"
             "        org.junit.jupiter.api.Assertions.assertEquals(9, new Lib().encode(1L).length);\n    }\n\n"
             "    @org.junit.jupiter.api.Test\n    void testEmpty() {\n"
             "        org.junit.jupiter.api.Assertions.assertEquals(9, new Lib().encode(0L).length);\n    }\n}\n")
PRE_REPAIR = _lib("public byte[] encode(long time) { return java.nio.ByteBuffer.allocate(9).putInt(0).putLong(time).array(); }  // PRE-REPAIR")
REGRESSED = _lib("public byte[] encode(long time) { return time == 0 ? null : new byte[9]; }  // REGRESSED")
ROUND_TRIP, EMPTY = "com.example.LibTest.testRoundTrip", "com.example.LibTest.testEmpty"


def _two_test_report(failed: str) -> Dict[str, Any]:
    return {"completeness": "COMPLETE", "runner": "maven", "test_cases": [
        {"identity": identity, "classname": "com.example.LibTest", "name": identity.rsplit(".", 1)[1],
         "status": "failed" if identity == failed else "passed", **({"detail": "java.lang.AssertionError: refused"} if identity == failed else {})}
        for identity in (ROUND_TRIP, EMPTY)]}


def _two_test_gate(validator: Any) -> Dict[str, Any]:
    tree = Path(validator.workspace_path)
    if not (tree / TEST).exists():
        return PASSING                                           # an owner invocation: no verifier in its tree
    if "REGRESSED" in (tree / LIB).read_text():                  # the repair: testRoundTrip passes now, testEmpty - passing before - fails
        return {"success": False, "test_execution": _two_test_report(EMPTY), "output": (
            "[ERROR] Tests run: 2, Failures: 0, Errors: 1, Skipped: 0 <<< FAILURE! -- in com.example.LibTest\n"
            "[ERROR] com.example.LibTest.testEmpty -- Time elapsed: 0.01 s <<< ERROR!\njava.lang.NullPointerException\n"
            "\tat com.example.LibTest.testEmpty(LibTest.java:11)\n")}
    return {**_owner_located(tree), "test_execution": _two_test_report(ROUND_TRIP)}     # the owner as first written: testRoundTrip fails inside it


def test_real_a_regressive_owner_repair_is_refused_and_the_owners_pre_repair_bytes_are_restored_by_the_owner_unit(tmp_path):
    from kriya.policy.approval import TypedApprovalCallback, VerifierSemanticDecision
    from kriya.workflow.workflow import WorkflowEngine

    owner_sources, words, subjects = iter([PRE_REPAIR, REGRESSED]), iter(["expectation_valid", "expectation_invalid"]), []
    restore_results: List[Dict[str, Any]] = []
    tree_after_restore: List[str] = []

    def semantic(subject: Any) -> Any:
        subjects.append(subject)
        word = next(words)
        return VerifierSemanticDecision.decided_as(word, subject.fingerprint, "testEmpty is the expectation the goal supports" if word == "expectation_invalid" else None)

    real_unit = WorkflowEngine.run_generation_workflow

    async def observed_unit(engine: Any, *args: Any, **kwargs: Any) -> Any:
        result = await real_unit(engine, *args, **kwargs)
        if str(kwargs.get("execution_scope") or "").endswith("role=" + verifier_replay.OWNER_REPAIR_RESTORE_ROLE):
            restore_results.append(result)
            tree_after_restore.append((Path(kwargs["workspace_path"]) / LIB).read_text())
        return result

    callback = TypedApprovalCallback(lambda files, reason: True, lambda subject: None, None, semantic)
    with patch("kriya.tools.lsp.find_jdtls", return_value=None), patch.object(WorkflowEngine, "run_generation_workflow", new=observed_unit):
        run = run_enforce(tmp_path, plan=_plan(), goal="Write an encoder and its test", callback=callback, on_tests=_two_test_gate,
                          units={"s3": (LIB, lambda: next(owner_sources)), "s6": (TEST, TWO_TESTS)},
                          base_files={"pom.xml": POM, ".gitignore": "target/\n"})
    legacy = run.result.legacy_result
    why = {key: legacy.get(key) for key in ("status", "failure_type", "terminal_cause", "reason_codes", "owner_repair_regression", "verifier_repair")}

    # the sealed sequence of reviews: each names the test that failed, and the second one - a new failing set of the same bytes - was asked
    assert [[identity for identity, _detail in subject.failing_expectations] for subject in subjects] == [[ROUND_TRIP], [EMPTY]], why
    record = legacy.get("owner_repair_regression")
    assert record is not None, why
    assert record["regressed_tests"] == [f"{TEST}::{EMPTY}"] and (record["subtask_id"], record["owner_subtask_id"]) == ("s6", "s3"), why
    assert record["restored"] is True and record["reason_code"] == verifier_replay.OWNER_REPAIR_REGRESSED, why
    assert record["owner_files_after"] == record["pre_repair_owner_files"] == {LIB: raw_digest(PRE_REPAIR.encode("utf-8"))}, why
    # the restoration ran through the owner's REAL unit: no Developer request, the carried bytes staged by candidate authority, gates passed
    assert run.developer_calls.count("s3") == 2 and run.developer_calls.count("s6") == 1, run.developer_calls
    assert len(restore_results) == 1 and restore_results[0].get("quality_gates_passed") is True, restore_results
    assert tree_after_restore == [PRE_REPAIR]                                    # the plan tree holds the exact pre-repair bytes again
    staged = [event.details for event in _events(run, "verifier.replayed")]
    assert staged and staged[-1]["paths"] == [LIB] and staged[-1]["digests"] == {LIB: raw_digest(PRE_REPAIR.encode("utf-8"))}, staged
    assert [details["paths"] for details in staged] == [[TEST], [LIB]], staged   # the verifier replay, then the owner restoration: nothing else carried
    # a refused repair is never success, and no verifier repair was offered for it
    assert legacy["status"] != "success" and "verifier_repair" not in legacy, why
    assert (legacy.get("terminal_cause") or {}).get("failure_category") == verifier_replay.OWNER_REPAIR_REGRESSION_FAILURE_CATEGORY, why
    assert not (run.workspace / LIB).exists() and not (run.workspace / TEST).exists()        # nothing committed
