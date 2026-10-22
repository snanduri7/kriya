"""N1 + F4 (Wave 1 closure delta): the general approval callback has ONE typed answer, ``ApprovalOutcome``, resolved at ONE owner (kriya/policy/approval.py).

MEASURED before this change (6406ec7):
  * F4 - every approval site wrote ``asyncio.iscoroutine(x)`` then ``if not x``. An asyncio Task / Future / custom awaitable is not a coroutine and is truthy, so it was read as
    APPROVAL before its result was known - at the change gate, Stage 2A, contract escalation, the policy REQUIRE_APPROVAL path, the live-lookup egress approval and the legacy
    runtime-command fallback (which then bound the subject's fingerprint to the "approval").
  * N1 - a desktop launch that could not put a decision to anyone answered a plain ``False``, so "nobody was asked" and "the person said no" were the same answer: the run ended
    ``human_rejected`` and contract escalation reported CONTRACT_ESCALATION_DECLINED. A review that expired or whose host vanished was also a plain False.

Behaviour is asserted at every site, not only the resolver, and the dangerous lexical pattern is pinned by a tripwire (a regression alarm, not a proof).
"""
import asyncio
import inspect
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kriya.policy import approval
from kriya.policy.approval import (
    APPROVAL_DECISION_NOT_RECORDED,
    APPROVAL_DECLINED,
    APPROVAL_GRANTED,
    APPROVAL_LAUNCH_CANNOT_ASK,
    APPROVAL_REASON_CODES,
    APPROVAL_RESULT_UNRECOGNIZED,
    APPROVAL_REVIEW_EXPIRED,
    ApprovalDecision,
    ApprovalOutcome,
    RuntimeCommandDecision,
    TypedApprovalCallback,
    ask,
    build_subject,
    classify_approval_answer,
    resolve_approval,
    resolve_approval_result,
)

APPROVED = ApprovalOutcome.approve()
DECLINED = ApprovalOutcome.decline()
UNAVAILABLE = ApprovalOutcome.unavailable_because(APPROVAL_LAUNCH_CANNOT_ASK)

# value the callback finally resolves to -> the decision it must mean
RESOLVED = [(True, APPROVED), (False, DECLINED), (APPROVED, APPROVED), (DECLINED, DECLINED), (UNAVAILABLE, UNAVAILABLE)]
RESOLVED_IDS = ["True", "False", "APPROVED", "DECLINED", "UNAVAILABLE"]
# shapes that are neither a bool nor an ApprovalOutcome: never read through truthiness
UNRECOGNIZED = [None, 1, 0, "yes", "", [], [True], {"approved": True}, object(), MagicMock(), MagicMock(return_value=True)]


def _wrap(kind, value):
    """The awaitable shapes a callback may return, built inside the running loop."""
    async def coroutine():
        return value

    if kind == "sync":
        return value
    if kind == "coroutine":
        return coroutine()
    if kind == "task":
        return asyncio.ensure_future(coroutine())
    if kind == "future":
        future = asyncio.get_running_loop().create_future()
        future.set_result(value)
        return future

    class Custom:
        def __await__(self):
            return coroutine().__await__()
    return Custom()


KINDS = ["sync", "coroutine", "task", "future", "custom-awaitable"]


# --------------------------------------------------------------------------- the resolver

@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("value,expected", RESOLVED, ids=RESOLVED_IDS)
def test_every_awaitable_shape_resolves_to_its_decision(kind, value, expected):
    async def go():
        return await resolve_approval_result(_wrap(kind, value))
    assert asyncio.run(go()) == expected


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("value", UNRECOGNIZED, ids=lambda v: type(v).__name__)
def test_an_unrecognized_answer_is_unavailable_and_never_approves_through_truthiness(kind, value):
    async def go():
        return await resolve_approval_result(_wrap(kind, value))
    outcome = asyncio.run(go())
    assert outcome == ApprovalOutcome.unavailable_because(APPROVAL_RESULT_UNRECOGNIZED) and not outcome.approved and not outcome.declined


def test_an_unresolved_task_or_future_is_never_approval_by_truthiness():
    async def go():
        async def never():
            await asyncio.sleep(3600)
        task = asyncio.ensure_future(never())
        future = asyncio.get_running_loop().create_future()
        try:
            assert bool(task) and bool(future), "the premise: an unresolved awaitable object IS truthy"
            return classify_approval_answer(task), classify_approval_answer(future)
        finally:
            task.cancel()
            future.cancel()
    assert asyncio.run(go()) == (ApprovalOutcome.unavailable_because(APPROVAL_RESULT_UNRECOGNIZED),) * 2


def test_an_awaitable_that_resolves_to_another_awaitable_is_not_approved():
    async def go():
        async def inner():
            return True

        async def outer():
            return inner()
        return await resolve_approval_result(outer())
    assert asyncio.run(go()) == ApprovalOutcome.unavailable_because(APPROVAL_RESULT_UNRECOGNIZED)


class _Boom(Exception):
    pass


@pytest.mark.parametrize("kind", ["raises", "coroutine-raises", "task-raises", "future-raises"])
def test_a_callback_failure_propagates_and_is_never_an_approval(kind):
    """The resolver keeps each caller's own fail-closed policy for a failing callback: the exception is not swallowed into a decision of any kind."""
    async def go():
        async def failing():
            raise _Boom("callback failed")

        def callback(files, reason):
            if kind == "raises":
                raise _Boom("callback failed")
            if kind == "coroutine-raises":
                return failing()
            if kind == "task-raises":
                return asyncio.ensure_future(failing())
            future = asyncio.get_running_loop().create_future()
            future.set_exception(_Boom("callback failed"))
            return future
        return await resolve_approval(callback, [], "reason")
    with pytest.raises(_Boom):
        asyncio.run(go())


def test_resolve_approval_passes_the_files_and_reason_through():
    seen = []

    def callback(files, reason):
        seen.append((files, reason))
        return True
    assert asyncio.run(resolve_approval(callback, [{"filepath": "a.py"}], "why")) == APPROVED
    assert seen == [([{"filepath": "a.py"}], "why")]


def test_an_outcome_has_no_truth_value_so_no_site_can_read_it_as_approval():
    for outcome in (APPROVED, DECLINED, UNAVAILABLE):
        with pytest.raises(TypeError):
            bool(outcome)
        with pytest.raises(TypeError):
            assert not not outcome


@pytest.mark.parametrize("decision", list(ApprovalDecision), ids=[d.value for d in ApprovalDecision])
def test_a_decision_member_has_no_truth_value_so_no_site_can_read_it_as_approval(decision):
    """1-F (Wave-1 final fix-forward): ``ApprovalOutcome.decision`` IS the decision (its ``approved`` / ``declined`` / ``unavailable`` are `is` comparisons on it), and a str Enum member is
    truthy by its text, so ``if outcome.decision:`` would have read DECLINED and UNAVAILABLE as approval. Same fail-loud rule as the outcome itself."""
    with pytest.raises(TypeError):
        bool(decision)
    with pytest.raises(TypeError):
        if decision:  # noqa: SIM108 - the truthiness test is the thing under test
            pass
    for outcome in (APPROVED, DECLINED, UNAVAILABLE):
        with pytest.raises(TypeError):
            bool(outcome.decision)


def test_the_documented_ways_to_read_a_decision_still_work():
    """Identity, equality, mapping keys, the closed lookup and ``.value`` are untouched: only truthiness was closed."""
    assert APPROVED.decision is ApprovalDecision.APPROVED and DECLINED.decision is ApprovalDecision.DECLINED and UNAVAILABLE.decision is ApprovalDecision.UNAVAILABLE
    assert ApprovalDecision("declined") is ApprovalDecision.DECLINED and ApprovalDecision.UNAVAILABLE.value == "unavailable"
    assert {ApprovalDecision.APPROVED: "a", ApprovalDecision.DECLINED: "d"}[DECLINED.decision] == "d"
    assert APPROVED.decision != DECLINED.decision and APPROVED.decision == ApprovalDecision.APPROVED
    assert [(o.approved, o.declined, o.unavailable) for o in (APPROVED, DECLINED, UNAVAILABLE)] == [(True, False, False), (False, True, False), (False, False, True)]


def test_decision_and_reason_code_can_only_be_paired_legally():
    assert [o.reason_code for o in (APPROVED, DECLINED, UNAVAILABLE)] == [APPROVAL_GRANTED, APPROVAL_DECLINED, APPROVAL_LAUNCH_CANNOT_ASK]
    assert [(o.approved, o.declined, o.unavailable) for o in (APPROVED, DECLINED, UNAVAILABLE)] == [(True, False, False), (False, True, False), (False, False, True)]
    for decision, code in ((ApprovalDecision.APPROVED, APPROVAL_DECLINED), (ApprovalDecision.DECLINED, APPROVAL_GRANTED), (ApprovalDecision.UNAVAILABLE, APPROVAL_GRANTED),
                           (ApprovalDecision.UNAVAILABLE, APPROVAL_DECLINED), (ApprovalDecision.APPROVED, "MADE_UP"), ("approved", APPROVAL_GRANTED)):
        with pytest.raises(ValueError):
            ApprovalOutcome(decision, code)
    with pytest.raises(ValueError):
        ApprovalOutcome.unavailable_because("MADE_UP")


def test_the_reason_code_table_is_closed():
    assert APPROVAL_REASON_CODES == ("APPROVAL_GRANTED", "APPROVAL_DECLINED", "APPROVAL_LAUNCH_CANNOT_ASK", "APPROVAL_REVIEW_EXPIRED",
                                     "APPROVAL_DECISION_NOT_RECORDED", "APPROVAL_RESULT_UNRECOGNIZED")
    assert {APPROVAL_REVIEW_EXPIRED, APPROVAL_DECISION_NOT_RECORDED} <= set(APPROVAL_REASON_CODES)


# --------------------------------------------------------------------------- the runtime-command legacy fallback (HIGH PRIORITY: a fingerprint is bound to an "approval")

def _subject():
    return build_subject(unit_id="s4", criterion="prints hello", working_directory="/work", commands=[["echo", "hello"]], input_channel="none", stdin_payload=None,
                         reason="run the app")


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("value,expected_approved", [(True, True), (False, False), (APPROVED, True), (DECLINED, False), (UNAVAILABLE, False)], ids=RESOLVED_IDS)
def test_a_legacy_callback_binds_the_fingerprint_only_to_a_resolved_approval(kind, value, expected_approved):
    subject = _subject()

    async def go():
        return await ask(lambda files, reason: _wrap(kind, value), subject)
    decision = asyncio.run(go())
    assert decision.approved is expected_approved
    assert decision.approved_fingerprint == (subject.fingerprint if expected_approved else None)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("value", UNRECOGNIZED, ids=lambda v: type(v).__name__)
def test_a_legacy_callback_with_an_unrecognized_answer_is_never_bound_to_the_subject(kind, value):
    async def go():
        return await ask(lambda files, reason: _wrap(kind, value), _subject())
    assert asyncio.run(go()) == RuntimeCommandDecision(None)


def test_the_typed_runtime_channel_resolves_a_task_to_its_typed_decision():
    subject = _subject()

    def run(answer):
        async def go():
            return await ask(TypedApprovalCallback(lambda files, reason: False, lambda s: _wrap("task", answer(s))), subject)
        return asyncio.run(go())
    assert run(lambda s: RuntimeCommandDecision(s.fingerprint)) == RuntimeCommandDecision(subject.fingerprint)
    assert run(lambda s: RuntimeCommandDecision(None)) == RuntimeCommandDecision(None)
    assert run(lambda s: True) == RuntimeCommandDecision(None), "the typed channel answers only with a RuntimeCommandDecision: a bare True is not an approval"


# --------------------------------------------------------------------------- the launch callback (N1)

def test_the_resolver_reads_a_launch_unavailable_answer_as_unavailable_not_a_decline():
    import kriya.cli as cli
    from kriya.control import launch as launch_module

    session = MagicMock()
    session.request_change_approval.return_value = None
    # an active launch that can record the requirement: every non-change decision is UNAVAILABLE, with the launch's reason code
    original_active = launch_module._ACTIVE
    launch_module._ACTIVE = session
    try:
        outcome = cli._approval_decision([], "Knowledge Guard found gaps", False)
        with_files = cli._approval_decision([{"filepath": "a.py", "content": "x"}], "review policy", True)
    finally:
        launch_module._ACTIVE = original_active
    assert outcome == UNAVAILABLE and with_files == UNAVAILABLE and not outcome.declined
    session.note_approval_required.assert_any_call("other_approval", "Knowledge Guard found gaps", {"files": []})


def test_outside_a_launch_a_terminal_no_is_a_decline_and_yes_is_an_approval(monkeypatch):
    import kriya.cli as cli
    monkeypatch.setattr(cli.click, "prompt", lambda text, **kw: "n")  # the terminal: an explicit n
    assert classify_approval_answer(cli._approval_decision([{"filepath": "a.py", "content": "x"}], "r", False)) == DECLINED
    assert classify_approval_answer(cli._approval_decision([{"filepath": "a.py", "content": "x"}], "r", True)) == APPROVED


# --------------------------------------------------------------------------- structural tripwire (a regression alarm for the obvious reintroduction - not a proof)

KRIYA = Path(__file__).resolve().parent.parent / "kriya"
OWNER = KRIYA / "policy" / "approval.py"


def _production_sources():
    return [(path, path.read_text(encoding="utf-8")) for path in sorted(KRIYA.rglob("*.py")) if path != OWNER]


def test_no_site_resolves_an_awaitable_with_iscoroutine_outside_the_approval_owner():
    """``asyncio.iscoroutine(x)`` then a truthiness test is the F4 pattern: a Task/Future is not a coroutine. Resolution of a callback's answer belongs to kriya/policy/approval.py."""
    offenders = [f"{path.relative_to(KRIYA.parent)}:{number}" for path, text in _production_sources() for number, line in enumerate(text.splitlines(), 1)
                 if re.search(r"\biscoroutine\(", line) and not line.lstrip().startswith("#")]
    assert offenders == []


AUTHORITY_MODULES = ("workflow/stage_apply.py", "workflow/stage_architecture.py", "workflow/stage_analysis.py", "workflow/spec_compliance.py", "workflow/workflow.py", "cli.py")


def test_no_approval_callback_answer_is_read_directly():
    """A call of an approval/lookup callback is passed straight to the one resolver in the same expression (``resolve_approval*(...)`` / ``await_if_awaitable(...)``, possibly
    wrapped over the previous two lines) - never assigned and then tested - and ``bool()`` is never applied to such an answer in the approval-authority modules."""
    call = re.compile(r"\b(?:approval_callback|web_lookup_query_callback|web_lookup_callback|skill_gap_callback|skill_conflict_callback)\(")
    resolvers = ("resolve_approval(", "resolve_approval_result(", "await_if_awaitable(")
    offenders = []
    for path, text in _production_sources():
        lines = text.splitlines()
        authority_module = path.relative_to(KRIYA).as_posix() in AUTHORITY_MODULES
        for number, line in enumerate(lines, 1):
            code = line.split("#", 1)[0]
            if call.search(code) and not re.search(r"\b(?:def|class)\b|:\s*(?:Optional|Callable)|^\s*[\"'`]", code):
                if not any(resolver in "\n".join(lines[max(0, number - 3):number]) for resolver in resolvers):
                    offenders.append(f"{path.relative_to(KRIYA.parent)}:{number}: {line.strip()}")
            if authority_module and re.search(r"\bbool\((?:approved|answer|proceed|supplied)\)", code):
                offenders.append(f"{path.relative_to(KRIYA.parent)}:{number}: {line.strip()}")
    assert offenders == [], "\n".join(offenders)


def test_the_resolver_is_what_the_authority_sites_import():
    sites = {"workflow/stage_apply.py": "resolve_approval", "workflow/stage_architecture.py": "resolve_approval", "workflow/spec_compliance.py": "resolve_approval",
             "workflow/workflow.py": "resolve_approval", "workflow/stage_analysis.py": "resolve_approval_result"}
    for relative, name in sites.items():
        assert name in (KRIYA / relative).read_text(encoding="utf-8"), relative
    assert inspect.iscoroutinefunction(approval.resolve_approval) and inspect.iscoroutinefunction(approval.resolve_approval_result)


# --------------------------------------------------------------------------- the policy REQUIRE_APPROVAL path (WorkflowEngine._authorize_action)

def _engine():
    from kriya.config import AppConfig
    from kriya.core.kernel import Kernel
    from kriya.core.llm import LLMClient
    from kriya.workflow.workflow import WorkflowEngine
    cfg = AppConfig()
    return WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("value,approves", [(True, True), (False, False), (APPROVED, True), (DECLINED, False), (UNAVAILABLE, False), (None, False), ("yes", False)],
                         ids=["True", "False", "APPROVED", "DECLINED", "UNAVAILABLE", "None", "str"])
def test_the_policy_approval_path_acts_only_on_a_resolved_approval(kind, value, approves):
    from kriya.policy.errors import PolicyDeniedError
    from kriya.policy.model import ActionRequest, ActionType, PolicyDecision, PolicyResult

    engine = _engine()
    engine.execution_policy.evaluate = MagicMock(return_value=PolicyResult(decision=PolicyDecision.REQUIRE_APPROVAL, reason_code="X", explanation="needs a human"))
    request = ActionRequest(action_type=ActionType.INSTALL_PACKAGE, target="some-package")

    async def go():
        return await engine._authorize_action(request, approval_callback=lambda files, reason: _wrap(kind, value), enforce=True)
    if approves:
        assert asyncio.run(go()).decision == PolicyDecision.REQUIRE_APPROVAL
    else:
        with pytest.raises(PolicyDeniedError):
            asyncio.run(go())


# --------------------------------------------------------------------------- the network-egress approval (WorkflowEngine._approve_web_lookup)

def _lookup_engine():
    engine = _engine()
    engine.kernel.config.autonomy.web_lookup_auto_approve = False
    return engine


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("value,approves", [(True, True), (False, False), (APPROVED, True), (DECLINED, False), (UNAVAILABLE, False), (None, False), ("yes", False), (1, False)],
                         ids=["True", "False", "APPROVED", "DECLINED", "UNAVAILABLE", "None", "str", "int"])
def test_a_live_lookup_query_leaves_the_machine_only_on_a_resolved_approval(kind, value, approves):
    engine = _lookup_engine()

    async def go():
        return await engine._approve_web_lookup(["requests"], "https://search.example", lambda terms, base_url: _wrap(kind, value))
    assert asyncio.run(go()) is approves


def test_a_failing_live_lookup_callback_denies_the_query():
    engine = _lookup_engine()

    def failing(terms, base_url):
        raise _Boom("prompt failed")

    async def go():
        return await engine._approve_web_lookup(["requests"], "https://search.example", failing)
    assert asyncio.run(go()) is False


def test_no_live_lookup_callback_and_no_opt_in_denies_the_query():
    assert asyncio.run(_lookup_engine()._approve_web_lookup(["requests"], "https://search.example", None)) is False


def test_the_egress_policy_vocabulary_is_still_closed():
    """The network-egress approval sits behind the closed vocabulary of autonomy.egress_policy (EGRESS-POLICY-TYPO-FAILS-OPEN-NEW): a typo is refused, not read as unrestricted."""
    from pydantic import ValidationError

    from kriya.config import AppConfig
    with pytest.raises(ValidationError):
        AppConfig(autonomy={"egress_policy": "LOCAL_ONLY"})
