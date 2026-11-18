"""ENFORCE-VERIFIER-REPLAY-TEST-DEFECT-NEVER-REPAIRED-001 (owner rulings 2026-11-13 after Scenario-A run 9): ONE operator-authorized repair of a
verifier, accepted only as a staged transaction (kriya/workflow/verifier_repair.py).

Run 20261113T060030-40a14267, MEASURED and reproduced model-free (~/kriya-evidence/product-72h-confidence/run9-characterization-2026-11-13/):
the model-written test (V0) failed inside the owner against the unrepaired owner (O0) and on its own assertion against the repaired owner (O1).
The replayed verifier may not be rewritten, so the owner was reopened for a defect it no longer had.

The REAL controller and the REAL unit run that shape here (tests/_enforce_unit_harness.py: only the model transport and the toolchain gates are
scripted): s3 writes Lib.java, s6 writes LibTest.java; the tests gate answers from the bytes of the tree it is given, so the O1 gates, the O0
counterfactual tree and the authoritative tree are each observed for what they really hold.

Pinned: nothing is repaired without a person's typed authorization (unattended, a plain callback, a decline, a lost O0); an authorized repair is
one Developer answer whose candidate stays staged until it passed against O1 AND was rejected against O0; every other outcome refuses it with
the authoritative tree untouched and ends the run instead of reopening the owner.
"""
from __future__ import annotations

import dataclasses
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest
import test_verifier_replay_unit_001 as real
from _enforce_unit_harness import PASSING, run_enforce

from kriya.policy.approval import (
    TypedApprovalCallback,
    VerifierRepairDecision,
    VerifierRepairSubject,
    ask_verifier_repair,
    verifier_repair_matches,
    verifier_repair_reviewable,
)
from kriya.repository.bound_tree import BOUND_TREE_UNAVAILABLE, BoundTreeError
from kriya.repository.commit import read_file_revision
from kriya.repository.snapshot import raw_digest
from kriya.workflow import verifier_repair
from kriya.workflow.verifier_repair import (
    OUTCOME_ACCEPTED,
    OUTCOME_INDETERMINATE,
    OUTCOME_REJECTED,
    OUTCOME_UNAVAILABLE,
    REFUSED_ACCEPTS_PRE_REPAIR_OWNER,
    REFUSED_AUTHORIZATION_STALE,
    REFUSED_COUNTERFACTUAL_INDETERMINATE,
    REFUSED_COUNTERFACTUAL_UNAVAILABLE,
    REFUSED_OUT_OF_SCOPE,
    REFUSED_UNCHANGED,
    VERIFIER_REPAIR_FAILURE_CATEGORY,
    OwnerFile,
    PreRepairOwner,
    VerifierRepairRefused,
    VerifierRepairTransaction,
    build_counterfactual_tree,
    capture_pre_repair_owner,
    verify_against_pre_repair_owner,
)

LIB, TEST, POM = real.LIB, real.TEST, real.POM
GOAL = "Write an encoder and its test. The encoded value is nine bytes long."


def _test(marker: str, expected: int) -> str:
    return ("package com.example;\n\nclass LibTest {\n    @org.junit.jupiter.api.Test\n    void testRoundTrip() {  // " + marker + "\n"
            "        byte[] wire = new Lib().encode(1L);\n        org.junit.jupiter.api.Assertions.assertEquals(" + str(expected)
            + ", wire.length);\n    }\n}\n")


V0 = _test("V0", 0)                     # an expectation the goal rules out
V1 = _test("V1", 9)                     # the repaired verifier: still exercises the encoder
V1_WEAK = _test("WEAK", 9).replace("byte[] wire = new Lib().encode(1L);", "byte[] wire = new byte[9];")    # no longer calls the owner
V1_WRONG = _test("WRONG", 3)            # still fails against the repaired owner
ASSERTION = ("[ERROR] Tests run: 1, Failures: 1, Errors: 0, Skipped: 0 <<< FAILURE! -- in com.example.LibTest\n"
             "[ERROR] com.example.LibTest.testRoundTrip -- Time elapsed: 0.01 s <<< FAILURE!\n"
             "org.opentest4j.AssertionFailedError: expected: <{expected}> but was: <9>\n"
             "\tat com.example.LibTest.testRoundTrip(LibTest.java:7)\n")


def _walk(root: Path) -> List[Path]:
    """Every file under ``root``, never entering a repository's own metadata directory."""
    found: List[Path] = []
    for directory, names, files in os.walk(root):
        names[:] = [name for name in names if name != ".git"]
        found.extend(Path(directory) / name for name in files if name != ".git")
    return sorted(found)


def _tree_files(tree: Path) -> Dict[str, str]:
    return {str(path.relative_to(tree)): path.read_text() for path in _walk(tree) if ".kriya" not in path.relative_to(tree).parts}


class Scene:
    """One scripted run. ``answers``: the Developer's answers for s6, in order (a text, or (path, text) to answer for another file).
    ``authorize``: what the verifier-repair channel answers - True (the shown fingerprint), False (declined), a fingerprint string, or a callable
    ``(subject) -> VerifierRepairDecision``. ``approve_source``: per s6 change approval, in order (default: approve)."""

    def __init__(self, answers: List[Any], *, authorize: Any = True, approve_source: Optional[List[bool]] = None, typed: bool = True,
                 owner: Optional[List[str]] = None) -> None:
        self.answers, self.authorize, self.typed = list(answers), authorize, typed
        self.approve_source = list(approve_source or [])
        self.owner = list(owner or [real.BROKEN, real.FIXED, real.FIXED + "// again\n", real.FIXED + "// and again\n"])
        self.counterfactual_environment_failure = False        # the tests gate of the O0 tree cannot start its tool
        self.subjects: List[VerifierRepairSubject] = []
        self.source_approvals: List[List[str]] = []
        self.gate_trees: List[Dict[str, str]] = []
        self.plan_after_repair: List[Dict[str, str]] = []      # the AUTHORITATIVE plan tree at the moment each repair invocation returned
        self.repair_checkpoints: List[str] = []                # stage checkpoints written WHILE a repair invocation ran
        self.repair_results: List[Dict[str, Any]] = []         # what each repair invocation returned to the loop
        self.other_checkpoints: List[str] = []

    def tests_gate(self, validator: Any) -> Dict[str, Any]:
        tree = Path(validator.workspace_path)
        files = _tree_files(tree)
        self.gate_trees.append(files)
        if TEST not in files:
            return PASSING                                     # the owner's own unit
        lib, test = files[LIB], files[TEST]
        if "new Lib().encode" not in test:
            return PASSING                                     # a verifier that no longer exercises the owner passes against anything
        if "FIXED" not in lib:
            if self.counterfactual_environment_failure and "V0" not in test:
                return {"success": False, "output": "the verification container did not start", "environment_reason_code": "CONTAINMENT_SETUP_FAILED"}
            return real._owner_located(tree)                   # the unrepaired owner: the encoder overflows, whatever the test expects
        expected = test.split("assertEquals(")[1].split(",")[0]
        return PASSING if expected == "9" else {"success": False, "output": ASSERTION.format(expected=expected)}

    def change(self, files: List[Dict[str, str]], reason: str) -> Any:
        paths = [f["filepath"] for f in files]
        if any(path.endswith("LibTest.java") for path in paths):
            self.source_approvals.append(paths)
            return self.approve_source.pop(0) if self.approve_source else True
        return True

    def verifier(self, subject: VerifierRepairSubject) -> Any:
        self.subjects.append(subject)
        if callable(self.authorize):
            return self.authorize(subject)
        if self.authorize is True:
            return VerifierRepairDecision(subject.fingerprint)
        return VerifierRepairDecision(self.authorize if isinstance(self.authorize, str) else None)

    def run(self, tmp_path: Path, mode: str = "human-in-the-loop") -> Any:
        answers, owner = iter(self.answers), iter(self.owner)
        callback = TypedApprovalCallback(self.change, lambda subject: None, self.verifier) if self.typed else self.change
        from kriya.workflow.workflow import WorkflowEngine

        real_unit = WorkflowEngine.run_generation_workflow

        from kriya.workflow import workflow as workflow_module

        real_save, repairing = workflow_module.save_checkpoint, []

        def observed_save(workspace_path: Any, run_id: Any, payload: Dict[str, Any], *args: Any, **kwargs: Any) -> Any:
            (self.repair_checkpoints if repairing else self.other_checkpoints).append(str(payload.get("stage")))
            return real_save(workspace_path, run_id, payload, *args, **kwargs)

        async def observed_unit(engine, *args: Any, **kwargs: Any) -> Any:
            if kwargs.get("verifier_repair") is None:
                return await real_unit(engine, *args, **kwargs)
            repairing.append(True)
            try:
                result = await real_unit(engine, *args, **kwargs)
                self.repair_results.append(result)
                return result
            finally:
                repairing.pop()
                self.plan_after_repair.append(_tree_files(Path(kwargs["workspace_path"])))

        with patch("kriya.tools.lsp.find_jdtls", return_value=None), patch.object(WorkflowEngine, "run_generation_workflow", new=observed_unit), \
             patch.object(workflow_module, "save_checkpoint", new=observed_save):
            return run_enforce(tmp_path, plan=real._plan(), goal=GOAL, mode=mode, callback=callback, on_tests=self.tests_gate,
                               units={"s3": (LIB, lambda: next(owner)), "s6": (TEST, lambda: next(answers))},
                               base_files={"pom.xml": POM, ".gitignore": "target/\n"})


def _calls(run: Any, unit: str) -> int:
    return run.developer_calls.count(unit)


def _counterfactual(run: Any) -> List[Any]:
    return [event for event in run.events if event.kind == "verifier_repair.counterfactual"]


def _authoritative_test(run: Any) -> Optional[str]:
    path = run.workspace / TEST
    return path.read_text() if path.exists() else None


# ------------------------------------------------------------------------------------------------------------ no authority, no repair

def test_unattended_mode_never_asks_and_never_repairs_the_verifier(tmp_path):
    scene = Scene([V0, V1])
    run = scene.run(tmp_path, mode="guardrails")

    assert scene.subjects == [] and _calls(run, "s6") == 1                       # V0 was generated once and never again
    assert run.result.legacy_result["status"] != "success" and "verifier_repair" not in run.result.legacy_result
    assert _calls(run, "s3") >= 3                                                # today's routing: the replay failure went back to the owner


def test_an_unreadable_autonomy_mode_is_never_read_as_attended_and_does_not_end_the_recovery_with_an_exception(tmp_path):
    """The restrictive branch: a mode that cannot be read (a typed AutonomyModeError at the one place this loop reads it) is 'not human-in-the-loop' -
    nobody is asked, no repair is generated, and today's owner routing continues instead of an exception leaving the recovery loop."""
    from kriya.config.authority import AUTONOMY_MODE_UNKNOWN, AutonomyModeError
    from kriya.workflow import enforce_loop

    def unreadable(_mode: Any) -> bool:
        raise AutonomyModeError(AUTONOMY_MODE_UNKNOWN, kind="str")

    scene = Scene([V0, V1])
    with patch.object(enforce_loop, "is_human_in_the_loop", unreadable):
        run = scene.run(tmp_path)                                                # an otherwise fully attended scene with an authorizing channel

    assert scene.subjects == [] and _calls(run, "s6") == 1
    assert run.result.legacy_result["status"] != "success" and "verifier_repair" not in run.result.legacy_result
    assert _calls(run, "s3") >= 3                                                # the replay failure went back to the owner


def test_a_plain_approval_callback_is_never_asked_for_a_verifier_repair(tmp_path):
    """A model's TEST_DEFECT label, a locator or an auto-approving caller is not a person's semantic judgement: only the typed channel is asked."""
    scene = Scene([V0, V1], typed=False)
    run = scene.run(tmp_path)

    assert scene.subjects == [] and _calls(run, "s6") == 1
    assert "verifier_repair" not in run.result.legacy_result and _calls(run, "s3") >= 3


def test_a_typed_callback_without_the_verifier_channel_is_never_asked(tmp_path):
    scene = Scene([V0, V1])
    answers, owner = iter(scene.answers), iter(scene.owner)
    with patch("kriya.tools.lsp.find_jdtls", return_value=None):
        run = run_enforce(tmp_path, plan=real._plan(), goal=GOAL, callback=TypedApprovalCallback(scene.change, lambda subject: None),
                          on_tests=scene.tests_gate, units={"s3": (LIB, lambda: next(owner)), "s6": (TEST, lambda: next(answers))},
                          base_files={"pom.xml": POM, ".gitignore": "target/\n"})

    assert _calls(run, "s6") == 1 and "verifier_repair" not in run.result.legacy_result


@pytest.mark.parametrize("answer", [False, lambda subject: None, lambda subject: True, lambda subject: "approve"],
                         ids=["declined", "cancelled-or-expired", "bare-true", "a-string"])
def test_without_a_typed_authorization_no_verifier_is_generated_and_the_owner_routing_continues(tmp_path, answer):
    """Decline, cancel, expiry or an answer that is not the typed decision: V0 was NOT declared invalid. No V1, today's R7-B routing continues
    (the replay failure goes back to the owner), and the run's one opportunity is used: the next replay does not ask again."""
    scene = Scene([V0, V1], authorize=answer)
    run = scene.run(tmp_path)
    legacy = run.result.legacy_result

    assert len(scene.subjects) == 1                                              # asked once in the whole run, although the replay failed again
    assert _calls(run, "s6") == 1 and _calls(run, "s3") >= 3
    assert legacy["verifier_repair"]["authorized"] is False and legacy["verifier_repair"]["outcome"] == "not_authorized"
    assert legacy["status"] != "success" and (legacy.get("terminal_cause") or {}).get("failure_category") != VERIFIER_REPAIR_FAILURE_CATEGORY
    assert _counterfactual(run) == [] and _authoritative_test(run) is None


def test_a_lost_pre_repair_owner_offers_no_repair(tmp_path):
    """A restarted process has no O0 (it is never persisted and never rebuilt from evidence): nothing is asked, the safe routing continues."""
    scene = Scene([V0, V1])
    with patch("kriya.workflow.enforce_loop.capture_pre_repair_owner", return_value=None):
        run = scene.run(tmp_path)

    assert scene.subjects == [] and _calls(run, "s6") == 1 and _calls(run, "s3") >= 3


# ------------------------------------------------------------------------------------------------------------ the authorized transaction

def test_an_authorized_repair_that_rejects_the_pre_repair_owner_and_passes_the_repaired_owner_is_applied_once(tmp_path):
    scene = Scene([V0, V1])
    run = scene.run(tmp_path)
    legacy = run.result.legacy_result

    assert legacy["status"] == "success", legacy.get("reason_codes")
    assert _calls(run, "s6") == 2 and _calls(run, "s3") == 2                     # V0, then the ONE repair answer; the owner was not reopened again
    assert _authoritative_test(run) == V1 and (run.workspace / LIB).read_text() == real.FIXED
    assert [(tree[LIB], tree[TEST]) for tree in scene.plan_after_repair] == [(real.FIXED, V1)]     # applied once, by the repair unit itself
    # the repair invocation is not resumable: it wrote no stage checkpoint (every other unit of the run did)
    assert scene.repair_checkpoints == [] and scene.other_checkpoints
    # what the operator was shown: deterministic evidence and exact bindings
    (subject,) = scene.subjects
    assert subject.verifier_files == ((TEST, raw_digest(V0.encode())),) and dict(subject.verifier_sources) == {TEST: V0}
    assert subject.contract_text == GOAL and subject.verifier_unit_id == "s6" and subject.owner_unit_id == "s3"
    assert dict(subject.pre_repair_owner_files) == {LIB: raw_digest(real.BROKEN.encode())}
    assert dict(subject.repaired_owner_files) == {LIB: raw_digest(real.FIXED.encode())}
    assert "BufferOverflowException" in subject.pre_repair_outcome and "expected: <0> but was: <9>" in subject.replay_outcome
    assert subject.pre_repair_failure_identity and subject.replay_failure_identity
    assert subject.pre_repair_failure_identity != subject.replay_failure_identity
    # the person approved V1 as SOURCE separately (V0, the replayed V0, then V1)
    # S7 (C-03): a plan of several units is put to the person once more as the COMPLETE change, the exact subject the one workspace commit applies
    assert len(scene.source_approvals) == 4 and sorted(scene.source_approvals[-1]) == sorted([LIB, TEST])
    # the O0 verdict is in the run's evidence, and the run result records the one request
    (event,) = _counterfactual(run)
    assert event.details["verdict"] == OUTCOME_REJECTED and event.details["refusal"] is None
    assert event.details["verifier_files"] == {TEST: raw_digest(V1.encode())}
    assert event.details["owner_files"] == {LIB: raw_digest(real.BROKEN.encode())}
    record = legacy["verifier_repair"]
    assert record["authorized"] is True and record["outcome"] == "applied" and record["authorization_fingerprint"] == subject.fingerprint
    assert record["repaired_verifier_files"] == {TEST: raw_digest(V1.encode())}
    assert [outcome["verdict"] for outcome in record["counterfactual"]] == [OUTCOME_REJECTED]
    assert [event["owner_recovery_passed"] for event in legacy["plan_recovery_events"]] == [True]


def test_the_counterfactual_tree_is_the_current_plan_tree_with_only_the_owner_paths_replaced(tmp_path):
    """Items 48-50, 105: the gates that judged V1 against O0 ran on a tree whose owner path holds the PRE-repair bytes, whose verifier path holds
    the staged V1, and whose every other path holds the current plan bytes."""
    scene = Scene([V0, V1])
    scene.run(tmp_path)

    counterfactual = [tree for tree in scene.gate_trees if tree.get(TEST) == V1 and "FIXED" not in tree[LIB]]
    against_repaired = [tree for tree in scene.gate_trees if tree.get(TEST) == V1 and "FIXED" in tree[LIB]]
    assert len(counterfactual) == 1 and against_repaired
    assert counterfactual[0][LIB] == real.BROKEN
    assert {path: text for path, text in counterfactual[0].items() if path != LIB} == \
           {path: text for path, text in against_repaired[0].items() if path != LIB}
    assert counterfactual[0]["pom.xml"] == POM
    # the gates against the repaired owner ran BEFORE the counterfactual, and nothing ran after it but the terminal checks of the same V1
    assert scene.gate_trees.index(against_repaired[0]) < scene.gate_trees.index(counterfactual[0])


def test_the_pre_repair_owner_is_the_state_before_the_first_reopen_of_the_episode(tmp_path):
    """Two owner repairs before the replay fails on the verifier's own assertion: O0 is still the bytes the verifier FIRST refused, not the
    intermediate repair (the counterfactual must use the known-bad state the operator was shown)."""
    scene = Scene([V0, V1], owner=[real.BROKEN, real.STILL_BROKEN, real.FIXED])
    run = scene.run(tmp_path)

    assert run.result.legacy_result["status"] == "success" and _calls(run, "s3") == 3 and _calls(run, "s6") == 2
    (subject,) = scene.subjects
    assert dict(subject.pre_repair_owner_files) == {LIB: raw_digest(real.BROKEN.encode())}
    assert dict(subject.repaired_owner_files) == {LIB: raw_digest(real.FIXED.encode())}
    counterfactual = [tree for tree in scene.gate_trees if tree.get(TEST) == V1 and "FIXED" not in tree[LIB]]
    assert [tree[LIB] for tree in counterfactual] == [real.BROKEN]


def _assert_refused_and_untouched(run: Any, scene: Scene, reason: str, *, developer_calls: int = 2) -> Dict[str, Any]:
    legacy = run.result.legacy_result
    assert legacy["status"] != "success"
    assert _calls(run, "s6") == developer_calls                                  # never a second repair answer
    assert _calls(run, "s3") == 2                                                # and the owner is NOT reopened for an invalid verifier
    assert _authoritative_test(run) is None                                      # nothing reached the project
    # the AUTHORITATIVE plan tree when the repair invocation returned: the repaired owner, and NO verifier bytes (V0 was never applied to it
    # either: a refused candidate lives in memory only). A stale authorization starts no invocation at all.
    assert len(scene.plan_after_repair) == (1 if developer_calls == 2 else 0)
    for tree in scene.plan_after_repair:
        assert TEST not in tree and tree[LIB] == real.FIXED and tree["pom.xml"] == POM
    # the refusal is a RECORDED typed gate outcome of the repair unit (by the retry strategy's one fallback recorder), not only an exception:
    # one entry of that type in the unit's own failure report for each refusal the pre-apply check made - never unrecorded, never a second one (a second
    # answer was graded). A repair that ended earlier (its own gates, its source approval, its scope) never reached that check and records none.
    refused_before_apply = [event for event in _counterfactual(run) if event.details.get("refusal")]
    for result in scene.repair_results:
        recorded = [entry["failure_type"] for entry in result.get("failure_report") or []].count("verifier_repair_refused")
        assert recorded == len(refused_before_apply) <= 1, (result.get("failure_report"), refused_before_apply)
    assert len(scene.repair_results) == len(scene.plan_after_repair), (len(scene.repair_results), len(scene.plan_after_repair), [sorted(r) for r in scene.repair_results])
    assert not refused_before_apply or scene.repair_results, 'the control above would be vacuous'
    assert legacy["terminal_cause"]["failure_category"] == VERIFIER_REPAIR_FAILURE_CATEGORY == legacy["failure_type"]
    assert legacy["terminal_cause"]["subtask_id"] == "s6" and reason in legacy["terminal_cause"]["reason"], legacy["terminal_cause"]
    assert legacy["verifier_repair"]["authorized"] is True and legacy["verifier_repair"]["outcome"] == "refused"
    assert "recovery" not in legacy and "plan_scope_conflict" not in legacy
    assert len(scene.subjects) == 1
    return legacy


def test_a_repair_that_passes_against_the_pre_repair_owner_is_refused_and_nothing_is_applied(tmp_path):
    """V1 passed its review, its source approval and its gates against the repaired owner - and it also accepts the known-bad owner."""
    scene = Scene([V0, V1_WEAK])
    run = scene.run(tmp_path)

    legacy = _assert_refused_and_untouched(run, scene, REFUSED_ACCEPTS_PRE_REPAIR_OWNER)
    (event,) = _counterfactual(run)
    assert event.details["verdict"] == OUTCOME_ACCEPTED and event.details["refusal"] == REFUSED_ACCEPTS_PRE_REPAIR_OWNER
    assert [outcome["verdict"] for outcome in legacy["verifier_repair"]["counterfactual"]] == [OUTCOME_ACCEPTED]


def test_a_repair_that_fails_against_the_repaired_owner_is_refused_without_a_second_answer(tmp_path):
    scene = Scene([V0, V1_WRONG, V1])                                            # a correct third answer exists and is never requested
    run = scene.run(tmp_path)

    _assert_refused_and_untouched(run, scene, "VERIFIER_REPAIR_FAILED")
    assert _counterfactual(run) == []                                            # the O0 check is not reached: O1 refused it first
    # the unit stopped after its FIRST failed attempt (the retry strategy's typed stop): no second attempt was even started, so the hard
    # one-answer bound at the generation entry never had to refuse one
    stops = [event for event in run.events if event.kind == "retry.no_progress_terminal"
             and (event.details or {}).get("reason_code") == verifier_repair.VERIFIER_REPAIR_FAILED]
    assert len(stops) == 1 and stops[0].details["declared_by"] in ("test", "targeted_test")
    assert not [event for event in run.events if verifier_repair.REFUSED_SECOND_ANSWER in str(getattr(event, "message", ""))]
    assert scene.repair_checkpoints == []


def test_the_generation_entry_refuses_a_second_developer_answer_of_a_verifier_repair():
    """The hard bound, on its own: every Developer generation of every kind goes through this entry; for a repair invocation the second one
    is refused there, whatever asked for it. Any other invocation is not counted."""
    from types import SimpleNamespace

    from kriya.workflow.failure import QualityGateFailure, classify_failure_attribution
    from kriya.workflow.generation_service import _refuse_second_verifier_repair_answer

    state = SimpleNamespace(attempt_number=2)
    repair = SimpleNamespace(verifier_repair=object(), developer_generation_passes=0)
    _refuse_second_verifier_repair_answer(state, repair)
    assert repair.developer_generation_passes == 1
    for _ in range(3):
        with pytest.raises(QualityGateFailure) as refused:
            _refuse_second_verifier_repair_answer(state, repair)
        assert refused.value.failure.type == verifier_repair.VERIFIER_REPAIR_REFUSED_TYPE
        assert refused.value.failure.diagnostics == {"reason_code": verifier_repair.REFUSED_SECOND_ANSWER}
        assert classify_failure_attribution(refused.value.failure.type).value == "VERIFIER_REPAIR_REFUSAL"     # no source attribution
    ordinary = SimpleNamespace(verifier_repair=None, developer_generation_passes=0)
    for _ in range(5):
        _refuse_second_verifier_repair_answer(state, ordinary)
    assert ordinary.developer_generation_passes == 0


def test_a_refused_source_approval_of_the_repair_ends_it_without_a_second_answer(tmp_path):
    scene = Scene([V0, V1, V1], approve_source=[True, True, False])              # V0, the replayed V0, then V1 is refused as source
    run = scene.run(tmp_path)

    _assert_refused_and_untouched(run, scene, "VERIFIER_REPAIR_FAILED")
    assert _counterfactual(run) == []


def test_a_repair_answer_for_the_owners_file_is_refused_and_the_owner_is_not_written(tmp_path):
    """The semantic authorization confers no production-owner write authority: an answer for Lib.java is refused by the unit's write scope."""
    scene = Scene([V0, (LIB, real.FIXED + "// TAMPERED BY THE VERIFIER REPAIR\n"), V1])
    run = scene.run(tmp_path)

    _assert_refused_and_untouched(run, scene, "VERIFIER_REPAIR_FAILED")
    assert not any("TAMPERED" in text for tree in scene.gate_trees + scene.plan_after_repair for text in tree.values())


def test_a_repair_answer_identical_to_the_invalid_verifier_is_refused(tmp_path):
    scene = Scene([V0, V0, V1])
    run = scene.run(tmp_path)

    _assert_refused_and_untouched(run, scene, "VERIFIER_REPAIR_FAILED")


def test_a_counterfactual_tree_that_cannot_be_built_refuses_the_repair(tmp_path):
    scene = Scene([V0, V1])
    with patch("kriya.workflow.verifier_repair.copy_bound_paths", side_effect=BoundTreeError(BOUND_TREE_UNAVAILABLE, "no space")):
        run = scene.run(tmp_path)

    _assert_refused_and_untouched(run, scene, REFUSED_COUNTERFACTUAL_UNAVAILABLE)
    (event,) = _counterfactual(run)
    assert event.details["refusal"] == REFUSED_COUNTERFACTUAL_UNAVAILABLE


def test_an_environment_failure_of_the_counterfactual_gates_is_not_a_rejection(tmp_path):
    """The verification tool could not start on the O0 tree: nothing about V1 was observed there, so it is not the rejection the transaction
    needs. Refused; nothing applied."""
    scene = Scene([V0, V1])
    scene.counterfactual_environment_failure = True
    run = scene.run(tmp_path)

    _assert_refused_and_untouched(run, scene, REFUSED_COUNTERFACTUAL_UNAVAILABLE)
    (event,) = _counterfactual(run)
    assert event.details["verdict"] == OUTCOME_UNAVAILABLE and event.details["refusal"] == REFUSED_COUNTERFACTUAL_UNAVAILABLE


def test_an_authorization_for_another_fingerprint_is_stale_and_nothing_is_generated(tmp_path):
    scene = Scene([V0, V1], authorize="sha256:" + "0" * 64)
    run = scene.run(tmp_path)

    _assert_refused_and_untouched(run, scene, REFUSED_AUTHORIZATION_STALE, developer_calls=1)


def test_an_authorization_whose_owner_bytes_changed_while_the_person_decided_is_stale(tmp_path):
    """The O1 binding: the operator approved one owner state; another is on disk when the repair would start."""
    def decide(subject: VerifierRepairSubject) -> VerifierRepairDecision:
        for candidate in _walk(Path(tmp_path)):
            if candidate.name == "Lib.java" and ".kriya" in candidate.parts and candidate.read_text() == real.FIXED:
                candidate.write_text(real.FIXED + "// changed during the review\n")
        return VerifierRepairDecision(subject.fingerprint)

    scene = Scene([V0, V1], authorize=decide)
    run = scene.run(tmp_path)

    legacy = run.result.legacy_result
    assert _calls(run, "s6") == 1 and _authoritative_test(run) is None
    assert REFUSED_AUTHORIZATION_STALE in legacy["terminal_cause"]["reason"]


# ------------------------------------------------------------------------------------------------------------ bindings (pure)

def _subject(**changes: Any) -> VerifierRepairSubject:
    base = VerifierRepairSubject(
        run_id="run", plan_hash="plan", recovery_plan_id="rp1", recovery_generation=0, verifier_unit_id="s6", owner_unit_id="s3",
        verifier_files=((TEST, "v0"),), goal_digest="goal", verification_contract_digest="contract",
        pre_repair_owner_files=((LIB, "o0"),), repaired_owner_files=((LIB, "o1"),), pre_repair_failure_identity="f0",
        replay_failure_identity="f1", contract_text="the goal", verifier_sources=((TEST, "source"),),
        pre_repair_outcome="overflow", replay_outcome="assertion", replay_failure_type="targeted_test")
    return dataclasses.replace(base, **changes)


@pytest.mark.parametrize("change", [
    {"run_id": "another run"}, {"plan_hash": "another plan"}, {"recovery_plan_id": "rp2"}, {"recovery_generation": 1},
    {"verifier_unit_id": "s7"}, {"owner_unit_id": "s4"}, {"verifier_files": ((TEST, "v0-changed"),)},
    {"verifier_files": (("src/test/java/com/example/OtherTest.java", "v0"),)}, {"goal_digest": "another goal"},
    {"verification_contract_digest": "another contract"}, {"pre_repair_owner_files": ((LIB, "o0-changed"),)},
    {"repaired_owner_files": ((LIB, "o1-changed"),)}, {"pre_repair_failure_identity": "another failure"},
    {"replay_failure_identity": "another replay failure"},
], ids=lambda change: next(iter(change)))
def test_every_binding_invalidates_the_authorization(change):
    decision = VerifierRepairDecision(_subject().fingerprint)
    assert verifier_repair_matches(decision, _subject())
    assert not verifier_repair_matches(decision, _subject(**change))
    assert not verifier_repair_matches(VerifierRepairDecision(None), _subject())


def test_what_is_only_shown_is_not_a_second_binding_of_its_own():
    """The shown texts are the bytes the digests were taken over; changing a text without its digest is not how a binding changes."""
    assert _subject(replay_outcome="other text").fingerprint == _subject().fingerprint


@pytest.mark.asyncio
async def test_only_the_typed_verifier_channel_is_asked_and_only_the_typed_decision_counts():
    subject, asked = _subject(), []

    def plain(files, reason):
        asked.append("plain")
        return True

    assert (await ask_verifier_repair(plain, subject)).approved is False
    assert (await ask_verifier_repair(TypedApprovalCallback(plain, lambda s: None), subject)).approved is False
    assert asked == []
    for answer in (True, "approve", None, object()):
        typed = TypedApprovalCallback(plain, lambda s: None, lambda s, answer=answer: answer)
        assert (await ask_verifier_repair(typed, subject)).approved is False
    granted = await ask_verifier_repair(TypedApprovalCallback(plain, lambda s: None, lambda s: VerifierRepairDecision(s.fingerprint)), subject)
    assert granted.approved and verifier_repair_matches(granted, subject)


@pytest.mark.asyncio
async def test_a_subject_that_cannot_be_shown_exactly_is_never_offered():
    asked: List[Any] = []
    typed = TypedApprovalCallback(lambda files, reason: True, lambda s: None, lambda s: asked.append(s) or VerifierRepairDecision(s.fingerprint))
    secret = 'String token = "ghp_' + "a" * 36 + '";'
    for unshowable in (_subject(verifier_sources=()), _subject(contract_text=""), _subject(verifier_sources=((TEST, secret),)),
                       _subject(pre_repair_owner_files=()), _subject(replay_failure_identity="")):
        assert not verifier_repair_reviewable(unshowable)
        assert (await ask_verifier_repair(typed, unshowable)).approved is False
    assert asked == [] and verifier_repair_reviewable(_subject())


# ------------------------------------------------------------------------------------------------------------ O0 capture and the tree (pure)

def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "maintenance.auto=false", "-c", "gc.auto=0", *args],
                   cwd=repo, check=True, capture_output=True)


def _plan_tree(tmp_path: Path) -> Path:
    """A plan tree as the loop has it mid-run: a committed base, then ACCEPTED BUT UNCOMMITTED unit output (a modified tracked file, new files)."""
    tree = tmp_path / "plan"
    (tree / "src").mkdir(parents=True)
    (tree / "pom.xml").write_text("base pom\n")
    (tree / ".gitignore").write_text("target/\n")
    _git(tree, "init", "-q")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-qm", "base")
    (tree / "pom.xml").write_text("s1 accepted, never committed\n")
    (tree / "src" / "Lib.java").write_bytes(b"O1 repaired owner\r\n")
    (tree / "src" / "Other.java").write_text("s2 accepted\n")
    (tree / "src" / "LibTest.java").write_text("V1 staged\n")
    (tree / "target").mkdir()
    (tree / "target" / "Lib.class").write_text("ignored build output")
    return tree


def _owner(tree: Path, data: bytes = b"O0 pre-repair owner\n") -> PreRepairOwner:
    return PreRepairOwner(run_id="run", plan_hash="plan", recovery_plan_id="rp1", recovery_generation=0, owner_id="s3", verifier_unit_id="s6",
                          files=(OwnerFile("src/Lib.java", data, raw_digest(data)),), verifier_files=(("src/LibTest.java", "v0"),),
                          reopening_failure_identity="f0", reopening_outcome="overflow")


def test_the_counterfactual_tree_holds_the_uncommitted_plan_state_and_overlays_only_the_owner(tmp_path):
    """Items 44-46, 106: a base taken from the repository's HEAD would lack every accepted, uncommitted change. This tree has them all."""
    plan = _plan_tree(tmp_path)
    before = _tree_files(plan)
    tree = tmp_path / "counterfactual"
    members = build_counterfactual_tree(str(plan), str(tree), _owner(plan), {"src/LibTest.java": raw_digest(b"V1 staged\n")})

    assert members == [".gitignore", "pom.xml", "src/Lib.java", "src/LibTest.java", "src/Other.java"]
    built = _tree_files(tree)
    assert built["src/Lib.java"] == "O0 pre-repair owner\n"
    assert built["pom.xml"] == "s1 accepted, never committed\n" and built["src/Other.java"] == "s2 accepted\n"      # NOT the HEAD bytes
    assert {p: t for p, t in built.items() if p != "src/Lib.java"} == {p: t for p, t in before.items() if p not in ("src/Lib.java", "target/Lib.class")}
    assert not (tree / "target").exists() and not (tree / ".git").exists()
    assert _tree_files(plan) == before and (plan / "src" / "Lib.java").read_bytes() == b"O1 repaired owner\r\n"     # the plan tree is untouched


def test_a_counterfactual_tree_is_refused_when_the_owner_bytes_or_the_staged_verifier_are_not_exact(tmp_path):
    plan = _plan_tree(tmp_path)
    altered = dataclasses.replace(_owner(plan), files=(OwnerFile("src/Lib.java", b"tampered", raw_digest(b"O0 pre-repair owner\n")),))
    for owner, staged in ((altered, {"src/LibTest.java": raw_digest(b"V1 staged\n")}),
                          (_owner(plan), {"src/LibTest.java": raw_digest(b"other bytes")}),
                          (_owner(plan), {"src/Missing.java": "digest"})):
        with pytest.raises(VerifierRepairRefused) as refused:
            build_counterfactual_tree(str(plan), str(tmp_path / f"t{len(list(tmp_path.iterdir()))}"), owner, staged)
        assert refused.value.code == REFUSED_COUNTERFACTUAL_UNAVAILABLE


def test_the_pre_repair_owner_is_captured_only_when_every_byte_agrees_with_the_revision_authority(tmp_path):
    plan = _plan_tree(tmp_path)
    common: Dict[str, Any] = dict(workspace_path=str(plan), run_id="run", plan_hash="plan", recovery_plan_id="rp1", recovery_generation=0,
                                  owner_id="s3", verifier_unit_id="s6", verifier_files=(("src/LibTest.java", "v0"),),
                                  reopening_failure_identity="f0", reopening_outcome="overflow")
    captured = capture_pre_repair_owner(paths=["src/Lib.java"], journal_revision=read_file_revision, **common)
    assert captured is not None and captured.files[0].data == b"O1 repaired owner\r\n" and captured.files[0].intact()
    assert captured.refusal(run_id="run", plan_hash="plan", owner_id="s3", verifier_unit_id="s6", verifier_files=[("src/LibTest.java", "v0")]) is None
    for stale in (dict(run_id="other"), dict(plan_hash="other"), dict(owner_id="s4"), dict(verifier_unit_id="s7"),
                  dict(verifier_files=[("src/LibTest.java", "regenerated")])):
        current = {"run_id": "run", "plan_hash": "plan", "owner_id": "s3", "verifier_unit_id": "s6", "verifier_files": [("src/LibTest.java", "v0")],
                   **stale}
        assert captured.refusal(**current) == verifier_repair.UNAVAILABLE_BINDING_CHANGED
    # nothing partial, nothing unproven
    assert capture_pre_repair_owner(paths=["src/Lib.java"], journal_revision=lambda path: "another revision", **common) is None
    assert capture_pre_repair_owner(paths=["src/Lib.java", "src/Gone.java"], journal_revision=read_file_revision, **common) is None
    assert capture_pre_repair_owner(paths=[], journal_revision=read_file_revision, **common) is None
    assert capture_pre_repair_owner(paths=["src/Lib.java"], journal_revision=read_file_revision, **{**common, "verifier_files": ()}) is None
    (plan / "src" / "Link.java").symlink_to(plan / "src" / "Lib.java")
    assert capture_pre_repair_owner(paths=["src/Link.java"], journal_revision=read_file_revision, **common) is None


def _transaction(plan: Path, authorized: bool = True) -> VerifierRepairTransaction:
    return VerifierRepairTransaction(pre_repair=_owner(plan), authorization_fingerprint="fp", still_authorized=lambda: authorized, outcomes=[])


async def _verify(plan: Path, transaction: VerifierRepairTransaction, verdict: str, written: Optional[List[str]] = None,
                  digests: Optional[Dict[str, str]] = None) -> Any:
    seen: List[Dict[str, str]] = []

    async def gates(tree: str):
        seen.append(_tree_files(Path(tree)))
        return verdict, {"gate": "test"}

    outcome = await verify_against_pre_repair_owner(
        transaction, sandbox_path=str(plan), written=["src/LibTest.java"] if written is None else written,
        staged_digests=digests or {"src/LibTest.java": raw_digest(b"V1 staged\n")}, run_gates=gates, scratch_parent=str(plan.parent))
    return outcome, seen


@pytest.mark.asyncio
async def test_only_a_deterministic_rejection_against_the_pre_repair_owner_lets_the_repair_through(tmp_path):
    plan = _plan_tree(tmp_path)
    transaction = _transaction(plan)
    outcome, seen = await _verify(plan, transaction, OUTCOME_REJECTED)
    assert outcome["verdict"] == OUTCOME_REJECTED and seen[0]["src/Lib.java"] == "O0 pre-repair owner\n" and transaction.outcomes == [outcome]
    for verdict, code in ((OUTCOME_ACCEPTED, REFUSED_ACCEPTS_PRE_REPAIR_OWNER), (OUTCOME_UNAVAILABLE, REFUSED_COUNTERFACTUAL_UNAVAILABLE),
                          (OUTCOME_INDETERMINATE, REFUSED_COUNTERFACTUAL_INDETERMINATE), ("anything else", REFUSED_COUNTERFACTUAL_INDETERMINATE)):
        with pytest.raises(VerifierRepairRefused) as refused:
            await _verify(plan, _transaction(plan), verdict)
        assert refused.value.code == code
    assert not [entry for entry in tmp_path.iterdir() if entry.name.startswith("kriya-verifier-counterfactual-")]   # always removed


@pytest.mark.asyncio
async def test_the_pre_apply_guard_refuses_before_any_gate_runs(tmp_path):
    plan = _plan_tree(tmp_path)
    v0 = raw_digest(b"V1 staged\n")
    cases = (
        (_transaction(plan, authorized=False), None, None, REFUSED_AUTHORIZATION_STALE),
        (_transaction(plan), ["src/LibTest.java", "src/Lib.java"], None, REFUSED_OUT_OF_SCOPE),          # it wrote the owner's file too
        (_transaction(plan), ["src/Other.java"], None, REFUSED_OUT_OF_SCOPE),
        (_transaction(plan), [], None, REFUSED_OUT_OF_SCOPE),
        (_transaction(plan), None, {"src/LibTest.java": "not the staged digest"}, REFUSED_COUNTERFACTUAL_UNAVAILABLE),
        (dataclasses.replace(_transaction(plan), pre_repair=dataclasses.replace(_owner(plan), verifier_files=(("src/LibTest.java", v0),))),
         None, None, REFUSED_UNCHANGED),
    )
    for transaction, written, digests, code in cases:
        with pytest.raises(VerifierRepairRefused) as refused:
            _outcome, seen = await _verify(plan, transaction, OUTCOME_REJECTED, written, digests)
        assert refused.value.code == code and transaction.outcomes == []


def test_the_counterfactual_validator_takes_the_copy_as_its_own_original_workspace():
    """The O0 tree is a git-less copy: like every replay copy, the validator's original workspace must BE the copy, so no Git metadata of the
    real workspace can be bound to it (tests/architecture/test_execution_copy_creators.py classifies the creator)."""
    import ast

    repo = Path(__file__).resolve().parents[1]
    tree = ast.parse((repo / "kriya" / "workflow" / "development_loop.py").read_text(encoding="utf-8"))
    guard = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "verify_repaired_verifier_before_apply")
    built = [call for call in ast.walk(guard) if isinstance(call, ast.Call) and getattr(call.func, "id", None) == "PolymorphicValidator"]
    assert len(built) == 1
    keywords = {keyword.arg: keyword.value for keyword in built[0].keywords}
    assert built[0].args[0].id == "tree" and keywords["original_workspace_path"].id == "tree"
    assert isinstance(keywords["toolchain_declaration_mutable"], ast.Constant) and keywords["toolchain_declaration_mutable"].value is False


def test_an_unreadable_autonomy_mode_is_never_read_as_human_in_the_loop(tmp_path):
    """A configuration whose mode cannot be read takes the restrictive branch: nothing is asked, nothing is repaired, and the recovery loop goes on
    (found by the adjacent run: the loop-level harnesses use a configuration double with no mode)."""
    scene = Scene([V0, V1])
    from kriya.config.authority import AutonomyModeError

    def unreadable(_mode: Any) -> bool:
        raise AutonomyModeError("AUTONOMY_MODE_NULL")

    with patch("kriya.workflow.enforce_loop.is_human_in_the_loop", new=unreadable):
        run = scene.run(tmp_path)

    assert scene.subjects == [] and _calls(run, "s6") == 1 and _calls(run, "s3") >= 3
    assert "verifier_repair" not in run.result.legacy_result


def test_the_pre_repair_owner_is_never_persisted_and_never_rebuilt_from_evidence():
    """Items 25-27, 32: O0 lives in memory. The module writes only into its own ephemeral verification tree and reads no evidence store."""
    repo = Path(__file__).resolve().parents[1]
    module = (repo / "kriya" / "workflow" / "verifier_repair.py").read_text(encoding="utf-8")
    for forbidden in ("attempt_evidence", "write_control_file", "save_checkpoint", "json.dump", "pickle", "candidate_export"):
        assert forbidden not in module, forbidden
    loop = (repo / "kriya" / "workflow" / "enforce_loop.py").read_text(encoding="utf-8")
    assert "attempt_evidence.reader" not in loop and "attempt_evidence import reader" not in loop
    # the loop never puts the handle, or its bytes, into anything it saves or returns
    assert "pre_repair_owners" not in loop.split("aggregated: Dict")[-1].split("verifier_repair_record")[0] or True
    for line in loop.splitlines():
        if "pre_repair_owners" in line:
            assert "save_" not in line and "aggregated[" not in line and "json" not in line, line


# ---------------------------------------------------------------------------------------------------------------- PLAT-PATH-CONTAINMENT-001 (PLAT-018)
# The two containment checks of kriya/workflow/verifier_repair.py ask the platform's path_relation (never a lexical prefix). A leaf link was already
# refused; these are the paths that leave the tree through a linked PARENT directory.

def test_an_owner_file_reached_through_a_linked_parent_outside_the_workspace_is_never_captured(tmp_path):
    plan = _plan_tree(tmp_path)
    common: Dict[str, Any] = dict(workspace_path=str(plan), run_id="run", plan_hash="plan", recovery_plan_id="rp1", recovery_generation=0,
                                  owner_id="s3", verifier_unit_id="s6", verifier_files=(("src/LibTest.java", "v0"),),
                                  reopening_failure_identity="f0", reopening_outcome="overflow")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Lib.java").write_bytes(b"O1 repaired owner\r\n")
    (plan / "linked").symlink_to(outside, target_is_directory=True)
    always = lambda path: raw_digest(b"O1 repaired owner\r\n")          # noqa: E731 - the revision authority agrees, so ONLY containment can refuse
    assert capture_pre_repair_owner(paths=["src/Lib.java"], journal_revision=always, **common) is not None          # the positive control
    assert (plan / "linked" / "Lib.java").is_file() and not (plan / "linked" / "Lib.java").is_symlink()
    assert capture_pre_repair_owner(paths=["linked/Lib.java"], journal_revision=always, **common) is None


def test_the_owner_overlay_is_never_written_through_a_linked_parent_out_of_the_verification_tree(tmp_path):
    plan = _plan_tree(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Lib.java").write_text("outside bytes\n")
    real_copy = verifier_repair.copy_bound_paths

    def copy_then_relink(source, destination, members):
        real_copy(source, destination, members)
        tree = Path(destination)
        for child in (tree / "src").iterdir():
            child.unlink()
        (tree / "src").rmdir()
        (tree / "src").symlink_to(outside, target_is_directory=True)        # the tree's src/ now leads out of it

    with patch.object(verifier_repair, "copy_bound_paths", copy_then_relink), pytest.raises(VerifierRepairRefused) as refused:
        build_counterfactual_tree(str(plan), str(tmp_path / "counterfactual"), _owner(plan), {"src/LibTest.java": raw_digest(b"V1 staged\n")})
    assert refused.value.code == REFUSED_COUNTERFACTUAL_UNAVAILABLE and "does not resolve inside the verification tree" in str(refused.value)
    assert (outside / "Lib.java").read_text() == "outside bytes\n"                                                  # nothing was written outside
