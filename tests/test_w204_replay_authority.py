"""W2-04 (MILESTONE-REPLAY-UNAPPROVED-COMMANDS-001, owner decision R-A, 2026-10-28): a milestone replay is ONE synchronous authority transaction -
derive the ordered set from the persisted verification commands, bind it to the replay context (the owner workspace), the canonical typed approval, execute exactly the
approved set once through the contained validator, return the report. Nothing is persisted in between, so an interrupted replay never resumes by itself and a later
invocation asks again.

MEASURED before the change: in human-in-the-loop mode the replay executed every persisted command with no approval parameter and no ask (it never called policy/approval.py).
These tests drive the real phase path (`_MilestonePlanDriver.run_phase`) with a recording validator in place of the container, so the executed argv is observable.
"""
from __future__ import annotations

import asyncio
import inspect
import sys

import pytest
from _strict_doubles import strict_config

from kriya.policy.approval import MAX_REVIEW_ROUNDS, RuntimeCommandDecision, TypedApprovalCallback, fingerprint_of
from kriya.workflow import milestones
from kriya.workflow.execution_plan import TerminalPhase
from kriya.workflow.milestones import (
    MILESTONE_REPLAY_NOT_EXECUTED,
    REPLAY_APPROVAL_NOT_GRANTED,
    REPLAY_COMMANDS_CHANGED,
    MilestoneRunState,
    _MilestonePlanDriver,
    replay_prior_milestone_report,
)

PLAN = {"group_id": "g1", "original_goal": "build the thing", "milestones": [
    {"id": "M1", "goal": "g", "depends_on": []}, {"id": "M2", "goal": "g2", "depends_on": []}]}
HITL = {"mode": "human-in-the-loop"}
GUARDRAILS = {"mode": "guardrails"}


class RecordingValidator:
    """Stands in for the contained validator: records exactly which argv lists were asked to execute."""

    executed: list = []

    def __init__(self, workspace, original_workspace_path=None, autonomy_cfg=None):
        self.workspace = workspace

    def run_app_sequence(self, commands):
        RecordingValidator.executed.append([list(command) for command in commands])
        return {"success": True, "timed_out": False, "output": "[VERIFICATION] PASS", "returncode": 0, "steps": []}


@pytest.fixture(autouse=True)
def recording_validator(monkeypatch):
    RecordingValidator.executed = []
    monkeypatch.setattr("kriya.tools.validate.PolymorphicValidator", RecordingValidator)
    monkeypatch.setattr(milestones, "_resolve_run_command", lambda command, workspace: list(command))  # the resolver is not the subject; identity keeps the argv readable


def _state(**commands):
    state = MilestoneRunState.from_dict(PLAN)
    state.verification_commands.update(commands)
    return state


TWO = {"M1": [["echo", "a"]], "M2": [["echo", "b"], ["echo", "c"]]}


class Answers:
    """A plain approval callback that records every ask and answers from a script."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.reasons: list = []

    def __call__(self, files, reason):
        self.reasons.append(reason)
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


class Typed:
    """A typed (desktop-shaped) approval callback: records the subjects and answers with a decision built by ``decide(subject, index)``."""

    def __init__(self, decide):
        self.subjects: list = []
        self._decide = decide
        self.callback = TypedApprovalCallback(lambda files, reason: False, self._review)

    def _review(self, subject):
        self.subjects.append(subject)
        return self._decide(subject, len(self.subjects))


def _driver(workspace, state, callback, autonomy=HITL, with_callback_key=True):
    driver = object.__new__(_MilestonePlanDriver)
    driver.workspace_path = str(workspace)
    driver.run_state = state
    driver.total = 2
    driver.engine_config = strict_config(autonomy=autonomy)
    driver.generation_kwargs = {"approval_callback": callback} if with_callback_key else {}
    driver._replay_evidence = None
    return driver


def _phase(driver):
    return asyncio.run(driver.run_phase(TerminalPhase.REPLAY_PRIOR_VERIFICATIONS))


def _flat(executed):
    return [tuple(command) for sequence in executed for command in sequence]


# ------------------------------------------------------------------------------------------- one approval for the exact ordered set, then exactly that set

def test_the_approval_receives_the_exact_command_list_that_later_executes_once(tmp_path):
    typed = Typed(lambda subject, n: RuntimeCommandDecision(subject.fingerprint))
    assert _phase(_driver(tmp_path, _state(**TWO), typed.callback)) is None
    assert len(typed.subjects) == 1, "ONE approval transaction for the complete replay set"
    (subject,) = typed.subjects
    assert subject.commands == (("echo", "a"), ("echo", "b"), ("echo", "c"))
    assert _flat(RecordingValidator.executed) == list(subject.commands), "the executed argv is exactly the approved argv, once each, in order"
    assert RecordingValidator.executed == [[["echo", "a"]], [["echo", "b"], ["echo", "c"]]]  # per milestone, in milestone-id order
    for text in ("Milestone M1", "Milestone M2", "echo a", "echo b", "echo c"):
        assert text in subject.reason, "the complete set is shown, milestone by milestone"


def test_the_approval_context_is_the_replay_workspace_not_an_isolated_worktree(tmp_path):
    typed = Typed(lambda subject, n: RuntimeCommandDecision(subject.fingerprint))
    _phase(_driver(tmp_path, _state(**TWO), typed.callback))
    (subject,) = typed.subjects
    assert subject.working_directory == str(tmp_path)
    assert subject.fingerprint == fingerprint_of(str(tmp_path), subject.commands, "none", None)
    assert subject.fingerprint != fingerprint_of("/isolated/worktree", subject.commands, "none", None)


def test_an_approval_given_for_an_attempt_cannot_authorize_the_replay(tmp_path):
    """The same commands approved for the isolated worktree (a different fingerprint) are refused: nothing executes."""
    attempt_fingerprint = fingerprint_of("/isolated/worktree", [["echo", "a"], ["echo", "b"], ["echo", "c"]], "none", None)
    typed = Typed(lambda subject, n: RuntimeCommandDecision(attempt_fingerprint))
    result = _phase(_driver(tmp_path, _state(**TWO), typed.callback))
    assert RecordingValidator.executed == []
    assert len(typed.subjects) == MAX_REVIEW_ROUNDS
    assert result["status"] == "milestone_replay_not_executed" and result["reason_codes"] == [MILESTONE_REPLAY_NOT_EXECUTED]
    assert all(entry["reason"].startswith(REPLAY_COMMANDS_CHANGED) for entry in result["not_executed"])


# -------------------------------------------------------------------------------------------------------------------- no approval -> nothing runs

@pytest.mark.parametrize("callback", [Answers(False), None], ids=["declined", "no-approval-channel"])
def test_a_declined_or_unavailable_approval_executes_nothing_and_is_the_typed_outcome(tmp_path, callback):
    result = _phase(_driver(tmp_path, _state(**TWO), callback))
    assert RecordingValidator.executed == []
    assert result["status"] == "milestone_replay_not_executed" and result["quality_gates_passed"] is False
    assert result["reason_codes"] == [MILESTONE_REPLAY_NOT_EXECUTED]
    assert {entry["milestone_id"] for entry in result["not_executed"]} == {"M1", "M2"}
    assert all(entry["reason"].startswith(REPLAY_APPROVAL_NOT_GRANTED) for entry in result["not_executed"])
    assert result["replay"]["state"] == "not_executed" and result["replay"]["passed"] == []


def test_a_driver_with_no_approval_callback_key_does_not_execute(tmp_path):
    result = _phase(_driver(tmp_path, _state(**TWO), None, with_callback_key=False))
    assert RecordingValidator.executed == [] and result["status"] == "milestone_replay_not_executed"


def test_an_unreviewable_set_is_not_offered_to_a_typed_callback_and_executes_nothing(tmp_path):
    many = {f"M{index}": [["echo", str(index)]] for index in range(1, 30)}  # over the reviewable command count
    plan = {"group_id": "g1", "original_goal": "x", "milestones": [{"id": mid, "goal": "g", "depends_on": []} for mid in many]}
    state = MilestoneRunState.from_dict(plan)
    state.verification_commands.update(many)
    typed = Typed(lambda subject, n: RuntimeCommandDecision(subject.fingerprint))
    result = _phase(_driver(tmp_path, state, typed.callback))
    assert RecordingValidator.executed == [] and typed.subjects == [] and result["status"] == "milestone_replay_not_executed"


# ----------------------------------------------------------------------------------- a changed command after approval is never executed under it

def test_a_set_that_changes_while_it_is_being_approved_is_asked_again_and_only_the_new_approved_set_runs(tmp_path):
    state = _state(**TWO)

    def decide(subject, index):
        if index == 1:
            state.verification_commands["M1"] = [["echo", "CHANGED"]]  # the persisted command moved while the person decided
        return RuntimeCommandDecision(subject.fingerprint)

    typed = Typed(decide)
    assert _phase(_driver(tmp_path, state, typed.callback)) is None
    assert len(typed.subjects) == 2
    assert ("echo", "a") in typed.subjects[0].commands and ("echo", "CHANGED") in typed.subjects[1].commands
    assert _flat(RecordingValidator.executed) == list(typed.subjects[1].commands), "only the set that was approved for what runs now executed"
    assert ("echo", "a") not in _flat(RecordingValidator.executed)


def test_a_regrouping_of_the_same_commands_across_milestones_is_a_changed_set(tmp_path):
    """The flattened argv list (all the canonical fingerprint sees) is identical, but the ordered structure is not: a milestone's commands run as one sequence sharing state, so
    moving a command to another milestone is a different replay and the approval of the first grouping does not cover it."""
    state = _state(**TWO)

    def decide(subject, index):
        if index == 1:
            state.verification_commands["M1"] = [["echo", "a"], ["echo", "b"]]
            state.verification_commands["M2"] = [["echo", "c"]]  # same a, b, c in the same order, different milestones
        return RuntimeCommandDecision(subject.fingerprint)

    typed = Typed(decide)
    assert _phase(_driver(tmp_path, state, typed.callback)) is None
    assert typed.subjects[0].fingerprint == typed.subjects[1].fingerprint, "the canonical fingerprint cannot tell: this is what the ordered structure binds"
    assert len(typed.subjects) == 2 and typed.subjects[0].reason != typed.subjects[1].reason
    assert RecordingValidator.executed == [[["echo", "a"], ["echo", "b"]], [["echo", "c"]]], "only the regrouped set that was shown in the second round ran"


def test_a_set_that_keeps_changing_is_never_executed(tmp_path):
    state = _state(**TWO)

    def decide(subject, index):
        state.verification_commands["M1"] = [["echo", f"CHANGED{index}"]]
        return RuntimeCommandDecision(subject.fingerprint)

    typed = Typed(decide)
    result = _phase(_driver(tmp_path, state, typed.callback))
    assert RecordingValidator.executed == [] and result["status"] == "milestone_replay_not_executed"
    assert all(entry["reason"].startswith(REPLAY_COMMANDS_CHANGED) for entry in result["not_executed"])


def test_a_persisted_command_that_changes_after_the_approval_is_not_what_runs(tmp_path):
    """The approval answered; the replay is executing. The persisted set moving now must not reach the validator: what runs is the approved argv, never a re-derived one."""
    state = _state(**TWO)

    class DriftingValidator(RecordingValidator):
        def run_app_sequence(self, commands):
            state.verification_commands["M2"] = [["echo", "SUBSTITUTED"]]  # the drift lands while M1 runs, before M2's turn
            return super().run_app_sequence(commands)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("kriya.tools.validate.PolymorphicValidator", DriftingValidator)
        typed = Typed(lambda subject, n: RuntimeCommandDecision(subject.fingerprint))
        assert _phase(_driver(tmp_path, state, typed.callback)) is None
    assert len(typed.subjects) == 1
    assert _flat(RecordingValidator.executed) == [("echo", "a"), ("echo", "b"), ("echo", "c")], "M2 ran the approved argv, not the substituted one"


# --------------------------------------------------------------------- the canonical policy is preserved: modes, the terminal's exact-words exemption

def test_an_autonomous_mode_replays_without_asking_exactly_as_attempt_time_verification_does(tmp_path):
    callback = Answers(False)
    assert _phase(_driver(tmp_path, _state(**TWO), callback, autonomy=GUARDRAILS)) is None
    assert callback.reasons == [] and len(RecordingValidator.executed) == 2


def test_the_terminal_exemption_for_the_users_own_exact_words_applies_to_a_replay_too(tmp_path):
    state = _state(M1=[["echo", "hello", "world"]])
    state.original_goal = "Run `echo hello world` to verify the greeting"
    callback = Answers(False)
    assert _phase(_driver(tmp_path, state, callback)) is None
    assert callback.reasons == [] and RecordingValidator.executed == [[["echo", "hello", "world"]]]


def test_a_typed_callback_always_asks_even_for_the_users_own_words(tmp_path):
    state = _state(M1=[["echo", "hello", "world"]])
    state.original_goal = "Run `echo hello world` to verify the greeting"
    typed = Typed(lambda subject, n: RuntimeCommandDecision(None))
    result = _phase(_driver(tmp_path, state, typed.callback))
    assert len(typed.subjects) == 1 and RecordingValidator.executed == [] and result["status"] == "milestone_replay_not_executed"


def test_a_milestone_with_no_runnable_commands_asks_nothing(tmp_path):
    callback = Answers(False)
    assert _phase(_driver(tmp_path, _state(), callback)) is None
    assert callback.reasons == [] and RecordingValidator.executed == []


# ---------------------------------------------------------------------------- lifecycle: nothing persisted, an interruption never resumes by itself

def test_a_replay_persists_nothing_so_nothing_can_resume_it(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("the replay wrote durable state")

    for name in ("write_control_file", "annotate_run", "save_control_state", "save_contract_registry", "save_artifact_registry"):
        monkeypatch.setitem(milestones.__dict__, name, forbidden)  # the module's globals: how milestones.py resolves each name at call time
    typed = Typed(lambda subject, n: RuntimeCommandDecision(subject.fingerprint))
    assert _phase(_driver(tmp_path, _state(**TWO), typed.callback)) is None
    assert list(tmp_path.iterdir()) == [], "and left nothing in the workspace"


def test_an_interrupted_replay_terminates_and_a_later_invocation_starts_at_the_authority_boundary_again(tmp_path):
    state = _state(**TWO)
    answers = Answers(True)
    calls = {"count": 0}

    class Interrupted(RecordingValidator):
        def run_app_sequence(self, commands):
            calls["count"] += 1
            if calls["count"] == 2:
                raise KeyboardInterrupt  # the process is interrupted during the second milestone's commands
            return super().run_app_sequence(commands)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("kriya.tools.validate.PolymorphicValidator", Interrupted)
        with pytest.raises(KeyboardInterrupt):
            _phase(_driver(tmp_path, state, answers))
    assert len(answers.reasons) == 1 and RecordingValidator.executed == [[["echo", "a"]]]
    # the interrupted invocation left no pending / in-progress record: nothing re-executes by itself
    assert RecordingValidator.executed == [[["echo", "a"]]]
    # a later operator invocation derives the set again and must obtain fresh authority; declined, it executes nothing
    later = Answers(False)
    result = _phase(_driver(tmp_path, state, later))
    assert len(later.reasons) == 1, "the later invocation asked again"
    assert result["status"] == "milestone_replay_not_executed"
    assert RecordingValidator.executed == [[["echo", "a"]]], "no command ran on the later invocation without approval"
    # and approved, it runs the whole set once
    approving = Answers(True)
    assert _phase(_driver(tmp_path, state, approving)) is None
    assert len(approving.reasons) == 1 and RecordingValidator.executed[1:] == [[["echo", "a"]], [["echo", "b"], ["echo", "c"]]]


# ------------------------------------------------------------------------------------------------------------------------------- shape guards

def test_the_replay_has_no_default_approval_channel_so_a_caller_cannot_forget_it():
    parameters = inspect.signature(replay_prior_milestone_report).parameters
    for name in ("autonomy_cfg", "approval_callback"):
        assert parameters[name].default is inspect.Parameter.empty and parameters[name].kind is inspect.Parameter.KEYWORD_ONLY


def test_problem_milestones_are_reported_in_order_and_never_asked_about(tmp_path):
    state = _state(M1=[["echo", "a"]], M2="not a list")
    callback = Answers(True)
    report = asyncio.run(replay_prior_milestone_report(str(tmp_path), state, autonomy_cfg=strict_config(autonomy=HITL).autonomy, approval_callback=callback))
    assert [(entry["milestone_id"], entry["outcome"]) for entry in report.entries] == [("M1", "passed"), ("M2", "not_executed")]
    assert "echo a" in callback.reasons[0] and "M2" not in callback.reasons[0]
    assert RecordingValidator.executed == [[["echo", "a"]]]


def test_a_python_error_inside_the_authorization_is_not_swallowed(tmp_path, monkeypatch):
    monkeypatch.setattr(milestones, "build_subject", lambda **kwargs: (_ for _ in ()).throw(NameError("coding error")))
    with pytest.raises(NameError):
        _phase(_driver(tmp_path, _state(**TWO), Answers(True)))
    assert RecordingValidator.executed == []


def test_python_executable_argv_is_shown_and_executed_verbatim(tmp_path):
    command = [sys.executable, "-c", "print(1)"]
    callback = Answers(True)
    assert _phase(_driver(tmp_path, _state(M1=[command]), callback)) is None
    assert " ".join(command) in callback.reasons[0] and RecordingValidator.executed == [[command]]
