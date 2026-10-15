"""GUI-RUNTIME-COMMAND-APPROVAL-001, backend: the typed runtime-command subject and its binding to what EXECUTES (kriya/workflow/runtime_command_approval.py, attempt.py).

TRACED before the fix: the approval text listed the judge's raw argv while the executor ran the RESOLVED commands (interpreter/PATH substitution, the runtime contract), and one
boolean ("approved once") skipped every later review, so a retry that re-judged different commands ran them unreviewed. Now the subject is built from the final commands, the
approval is bound to their fingerprint, and a changed set is never executed."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import _enforce_unit_harness as harness
import pytest
from test_greenfield_runtime_verification_dropped import GOAL, JUDGE, UNITS, _plan

from kriya.workflow import attempt
from kriya.workflow.runtime_command_approval import (
    RuntimeCommandDecision,
    RuntimeCommandSubject,
    TypedApprovalCallback,
    approval_matches,
    ask,
    build_subject,
    reviewable,
)

CWD = "/work/tree"


def _subject(commands=(("mvn", "-q", "test"),), cwd=CWD, channel="none", stdin=None):
    return build_subject(unit_id="s4", criterion="prints Hello", working_directory=cwd, commands=[list(c) for c in commands], input_channel=channel, stdin_payload=stdin, reason="legacy text")


def test_the_fingerprint_binds_exactly_what_runs():
    base = _subject()
    assert base.fingerprint == _subject().fingerprint
    assert base.fingerprint != _subject(commands=(("mvn", "-q", "verify"),)).fingerprint          # argv
    assert base.fingerprint != _subject(commands=(("mvn", "-q", "test"), ("java", "-jar", "x"))).fingerprint   # an extra command
    assert _subject(commands=(("a",), ("b",))).fingerprint != _subject(commands=(("b",), ("a",))).fingerprint   # order
    assert base.fingerprint != _subject(cwd="/other").fingerprint                                  # working directory
    assert base.fingerprint != _subject(channel="stdin", stdin="x").fingerprint                    # input channel / stdin
    assert _subject(channel="stdin", stdin="x").fingerprint != _subject(channel="stdin", stdin="y").fingerprint
    assert RuntimeCommandSubject.__dataclass_fields__["environment_names"].default == ()           # names only; never a value


def test_an_unreviewable_command_set_is_not_offered():
    assert reviewable(_subject()) is True
    assert reviewable(_subject(commands=())) is False
    assert reviewable(_subject(cwd="")) is False
    assert reviewable(_subject(commands=[("x" * 5000,)])) is False
    assert reviewable(_subject(commands=[("a",)] * 21)) is False
    asked = []
    typed = TypedApprovalCallback(lambda f, r: True, lambda s: asked.append(s))
    assert asyncio.run(ask(typed, _subject(commands=()))).approved is False and asked == []


def test_a_legacy_callback_is_asked_the_legacy_way_and_bound_to_what_it_was_shown():
    calls = []
    decision = asyncio.run(ask(lambda files, reason: calls.append((files, reason)) or True, _subject()))
    assert calls == [([], "legacy text")] and decision.approved_fingerprint == _subject().fingerprint
    assert asyncio.run(ask(lambda f, r: False, _subject())).approved is False

    async def awaited(_f, _r):
        return True
    assert asyncio.run(ask(awaited, _subject())).approved is True                                  # an awaitable answer is awaited, not read as truthy


def test_a_typed_callback_gets_the_subject_not_text_and_its_answer_is_bound_to_a_fingerprint():
    seen = []
    typed = TypedApprovalCallback(lambda f, r: pytest.fail("the legacy channel must not be used for a runtime subject"),
                                  lambda s: seen.append(s) or RuntimeCommandDecision(s.fingerprint))
    decision = asyncio.run(ask(typed, _subject()))
    assert seen == [_subject()] and approval_matches(decision, _subject())
    assert not approval_matches(decision, _subject(commands=(("mvn", "-q", "verify"),)))           # approval of A never matches B
    assert not approval_matches(RuntimeCommandDecision(None), _subject())
    assert asyncio.run(ask(TypedApprovalCallback(lambda f, r: True, lambda s: True), _subject())).approved is False   # a malformed answer is not an approval


def _ctx(callback):
    return SimpleNamespace(worktree_path=CWD, current_subtask_id="s4", approval_callback=callback)


JUDGMENT = {"should_run": True, "run_commands": [["mvn", "-q", "test"]], "success_criteria": "prints Hello", "input_channel": "none", "command_source": "inferred"}


def test_the_approval_is_recorded_as_a_fingerprint_and_a_retry_with_different_commands_is_not_covered():
    state = SimpleNamespace(run_verification_approved_fingerprint=None)
    with patch.object(attempt, "_resolve_run_command", side_effect=lambda cmd, _wt: list(cmd)):
        ok = asyncio.run(attempt._approve_runtime_commands(_ctx(lambda f, r: True), state, JUDGMENT, "intro"))
        assert ok is True and state.run_verification_approved_fingerprint == attempt._derive_runtime_command_subject(_ctx(None), JUDGMENT, "").fingerprint
        changed = {**JUDGMENT, "run_commands": [["mvn", "-q", "verify"]]}
        # the call sites ask again exactly when the approved fingerprint differs from the derived one:
        assert state.run_verification_approved_fingerprint != attempt._derive_runtime_command_subject(_ctx(None), changed, "").fingerprint


def test_a_command_set_that_drifts_while_the_person_decides_is_reviewed_again_and_never_executed():
    state = SimpleNamespace(run_verification_approved_fingerprint=None)
    drift = iter(range(1000))
    shown = []

    def resolve(cmd, _wt):                      # PATH/worktree state moving while a human decides: each derivation yields a different executable
        return [f"/bin/mvn-{next(drift)}", *cmd[1:]]

    typed = TypedApprovalCallback(lambda f, r: True, lambda s: shown.append(s) or RuntimeCommandDecision(s.fingerprint))
    with patch.object(attempt, "_resolve_run_command", side_effect=resolve):
        ok = asyncio.run(attempt._approve_runtime_commands(_ctx(typed), state, JUDGMENT, "intro"))
    assert ok is False and state.run_verification_approved_fingerprint is None
    assert len(shown) == 2                                                                         # reviewed again once (MAX_REVIEW_ROUNDS), then declined


def test_reject_is_not_an_approval_and_records_nothing():
    state = SimpleNamespace(run_verification_approved_fingerprint=None)
    typed = TypedApprovalCallback(lambda f, r: True, lambda s: RuntimeCommandDecision(None))
    with patch.object(attempt, "_resolve_run_command", side_effect=lambda cmd, _wt: list(cmd)):
        assert asyncio.run(attempt._approve_runtime_commands(_ctx(typed), state, JUDGMENT, "intro")) is False
    assert state.run_verification_approved_fingerprint is None


def test_the_subject_shows_the_RESOLVED_commands_and_exactly_those_execute(tmp_path):
    """Through the real controller: the toolchain resolves to absolute paths. The reviewer sees those paths; the executor runs those paths. (Kriya's own Java entrypoint grounding has
    already replaced the judge's `mvn exec:java` with javac + java -cp, which is itself why the subject must come from the FINAL commands, not the judge's.)"""
    subjects = []
    typed = TypedApprovalCallback(lambda f, r: True, lambda s: subjects.append(s) or RuntimeCommandDecision(s.fingerprint))

    def resolve(cmd, _wt):
        return [f"/opt/jdk/bin/{cmd[0]}", *cmd[1:]] if cmd[0] in ("java", "javac") else list(cmd)

    with patch.object(attempt, "_resolve_run_command", side_effect=resolve):
        run = harness.run_enforce(tmp_path, plan=_plan(), units=UNITS, goal=GOAL, run_verification=True, judge_reply=JUDGE, app_output="Hello from Kriya\n", callback=typed)
    assert subjects and [c[0] for c in subjects[0].commands] == ["/opt/jdk/bin/javac", "/opt/jdk/bin/java"]
    assert subjects[0].unit_id == "s4" and "Hello from Kriya" in subjects[0].criterion and subjects[0].working_directory
    assert [list(c) for c in subjects[0].commands] == [c for sequence in run.executed for c in sequence]      # displayed == executed
    assert run.result.legacy_result["status"] == "success"


def test_a_rejected_typed_review_stops_the_run_without_executing(tmp_path):
    typed = TypedApprovalCallback(lambda f, r: True, lambda s: RuntimeCommandDecision(None))
    run = harness.run_enforce(tmp_path, plan=_plan(), units=UNITS, goal=GOAL, run_verification=True, judge_reply=JUDGE, app_output="Hello from Kriya\n", callback=typed)
    assert run.executed == []
    assert run.result.legacy_result["status"] == "failed"


def test_when_a_human_must_approve_is_decided_by_the_exact_command_set():
    state = SimpleNamespace(run_verification_approved_fingerprint=None)
    ctx = _ctx(None)
    with patch.object(attempt, "_resolve_run_command", side_effect=lambda cmd, _wt: list(cmd)):
        approved_fp = attempt._derive_runtime_command_subject(ctx, JUDGMENT, "").fingerprint
        need = lambda judgment, mode="human-in-the-loop": attempt._runtime_commands_need_approval(ctx, state, judgment, mode)    # noqa: E731 - local shorthand
        assert need(JUDGMENT) is True                                                              # nothing approved yet
        state.run_verification_approved_fingerprint = approved_fp
        assert need(JUDGMENT) is False                                                             # the same exact set: not asked again on a retry
        assert need({**JUDGMENT, "run_commands": [["mvn", "-q", "verify"]]}) is True               # a retry that re-judged DIFFERENT commands is asked again
        assert need({**JUDGMENT, "input_channel": "stdin"}) is True                                # a different input channel is a different command set
        assert need({**JUDGMENT, "command_source": "goal_explicit"}, ) is False                    # the user's own explicit command is not Kriya's inference
        state.run_verification_approved_fingerprint = None
        assert need(JUDGMENT, "guardrails") is False                                               # policy unchanged outside human-in-the-loop


def test_nobody_is_asked_to_approve_a_command_set_the_contract_refuses_to_run():
    """Independent review of 52b89ea: a person could approve a set that then raised RUNTIME_VERIFICATION_CONTRACT_INCOMPLETE without running. An unsatisfiable input channel asks nothing."""
    asked = []
    typed = TypedApprovalCallback(lambda f, r: True, lambda s: asked.append(s) or RuntimeCommandDecision(s.fingerprint))
    state = SimpleNamespace(run_verification_approved_fingerprint=None)
    unsatisfiable = {**JUDGMENT, "run_commands": [["somebinary"]], "input_channel": "argv"}      # an argv input is required but the command has no place for it
    with patch.object(attempt, "_resolve_run_command", side_effect=lambda cmd, _wt: list(cmd)):
        subject = attempt._derive_runtime_command_subject(_ctx(typed), unsatisfiable, "intro")
        assert subject.incomplete_reason, "the fixture must be a command set the contract cannot satisfy"
        assert asyncio.run(attempt._approve_runtime_commands(_ctx(typed), state, unsatisfiable, "intro")) is True
    assert asked == [] and state.run_verification_approved_fingerprint is None
