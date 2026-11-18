"""VERIFIER-REGENERATION-SPENT-BY-REFUSAL-001 (Stage B run 5, 20261118T123353-7817f100), scripted and model-free through the real enforce controller:

    a run-authored verifier fails its gate  ->  the person answers expectation_invalid (ONE regeneration of exactly that verifier)
    ->  the primary refuses the regeneration request before any provider request (CONTEXT_BUDGET_UNSATISFIABLE)
    ->  MEASURED in the live run: 5 ms later the unit stopped VERIFIER_SEMANTIC_REGENERATION_NOT_EXECUTED; the fallback was never offered the request.

The invariant: expectation_invalid authorizes exactly ONE ACTUAL regeneration of the exact bound verifier subject. A request Kriya refused before any
provider saw it is not a regeneration: the same pending regeneration is dispatched once on the next eligible candidate, the person is not asked again,
and when no candidate can serve it the unit stops with the candidate's own typed reason. A regeneration a provider answered stays the only one.

The refusal is raised by the real refusal type at the client's own pre-provider hook (``_dispatch_budget``, before ``_request_once``); everything else
(the Developer agent, the attempt, the retry policy, fallback selection, the verifier-semantic owner, the write scope) is the product's.
"""
from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from test_verifier_semantic_loop_001 import GOAL, LIB, LIB_20, TEST, Scene, _stopped_for_review, _test

import kriya.workflow.attempt as attempt_module
import kriya.workflow.retry_strategy as retry_strategy_module
from kriya.config.config import FallbackModelConfig
from kriya.core import token_budget as tb
from kriya.core.llm import LLMClient
from kriya.policy.approval import VerifierSemanticAllowance
from kriya.workflow import verifier_semantic as vs
from kriya.workflow.retry_policy import RetryAction, RetryDecision
from kriya.workflow.state import GenerationState

PRIMARY, FALLBACK = "dev-model", "fallback-model"
V0, V1 = _test(10), _test(20, "V1")


def _context_refusal(model):
    with pytest.raises(tb.ContextBudgetUnsatisfiableError) as refused:   # run 5's arithmetic: 32694 + 1024 + 256 > 32768
        tb.plan_dispatch(
            messages=[{"role": "system", "content": "s" * 1434}, {"role": "user", "content": "u" * 80220}],
            requested_max_tokens=16384, context_window=32768, window_source="requested_per_request")
    assert "32694 prompt tokens" in str(refused.value) and "33974 tokens in all" in str(refused.value)
    refused.value.model = model            # as LLMClient._dispatch_budget names the refused candidate
    return refused.value


def _with_fallback(cfg):
    cfg.llm_chain = [FallbackModelConfig(model=FALLBACK, base_url=cfg.llm.base_url, context_window=32768,
                                         capabilities=cfg.llm.capabilities.model_copy())]


def _run(tmp_path, *, test_answers, semantic, refuse, fallback=True):
    """``refuse``: the models whose Developer request is refused before the provider once the person has answered (= the regeneration request)."""
    scene = Scene(lib=[LIB_20], test=test_answers, semantic=semantic, configure=_with_fallback if fallback else None)
    seen = {"refused": [], "provider": [], "grants": 0, "charges": [], "takes": 0}
    real_budget, real_take = LLMClient._dispatch_budget, VerifierSemanticAllowance.take_regeneration
    real_grant, real_charge = attempt_module.grant_developer_attempt, retry_strategy_module.charge_failed_attempt

    def developer(messages):
        return "Developer Agent" in str(messages[0].get("content") or "").splitlines()[0]

    def dispatch_budget(self, *, model, messages, **kwargs):
        if developer(messages):
            if scene.subjects and model in refuse:
                seen["refused"].append(model)
                raise _context_refusal(model)
            seen["provider"].append((model, "regeneration" if scene.subjects else "planned"))
        return real_budget(self, model=model, messages=messages, **kwargs)

    def take(allowance, decision, subject_now):
        taken = real_take(allowance, decision, subject_now)
        seen["takes"] += int(taken)
        return taken

    def grant(required, unit):
        seen["grants"] += 1
        return real_grant(required, unit)

    def charge(budgets, **kwargs):
        before = dataclasses.asdict(budgets)
        real_charge(budgets, **kwargs)
        after = dataclasses.asdict(budgets)
        seen["charges"].append((kwargs.get("candidate_unavailable"), {k: after[k] for k in after if after[k] != before[k]}))

    with patch.object(LLMClient, "_dispatch_budget", new=dispatch_budget), \
         patch.object(VerifierSemanticAllowance, "take_regeneration", new=take), \
         patch.object(attempt_module, "grant_developer_attempt", new=grant), \
         patch.object(retry_strategy_module, "charge_failed_attempt", new=charge):
        scene.run(tmp_path)
    return scene, seen


def _events(scene, kind):
    return [event.details for event in scene.result.events if event.kind == kind]


def _regeneration_scopes(scene):
    return [record for record in scene.scopes if record["during"][0] == [TEST]]


# ------------------------------------------------------------------ the run 5 sequence

def test_run5_sequence_the_refused_primary_does_not_spend_the_regeneration_and_the_fallback_performs_it_once(tmp_path):
    scene, seen = _run(tmp_path, test_answers=[V0, V1], semantic=["expectation_invalid", "expectation_valid"], refuse={PRIMARY})
    result = scene.stop()
    assert result.get("quality_gates_passed") is True, (result.get("failure_category"), result.get("environment_failure"))
    assert scene.applied() == {LIB: LIB_20, TEST: V1}
    # provider requests: the two planned files on the primary, then exactly ONE regeneration request, on the fallback
    assert seen["refused"] == [PRIMARY]
    assert [entry for entry in seen["provider"] if entry[1] == "regeneration"] == [(FALLBACK, "regeneration")]
    assert sorted(entry for entry in seen["provider"] if entry[1] == "planned") == [(PRIMARY, "planned")] * 2
    assert scene.requested().count(TEST) == 2 and scene.requested().count(LIB) == 1
    # the person: one decision on the failing bytes, one on the regenerated bytes; the candidate change asked nothing
    assert [(s.review_phase, s.verifier_files[0][0]) for s in scene.subjects] == [("failing_verifier", TEST), ("passing_verifier", TEST)]
    # semantic regeneration: one allowance taken, one ruling, never judged "not executed"
    assert seen["takes"] == 1
    assert [r["action"] for r in scene.rulings()] == ["regenerate_verifier"]
    carried = _events(scene, "verifier_semantic.regeneration_carried")
    assert [(d["path"], d["refused_candidate"], d["reason_code"]) for d in carried] == [(TEST, "primary", tb.CONTEXT_BUDGET_UNSATISFIABLE)]
    # the write scope of both attempts of the one regeneration was exactly the verifier, and every scope was restored
    assert len(_regeneration_scopes(scene)) == 2
    assert all(record["after"] == record["before"] and sorted(record["before"][0]) == [LIB, TEST] for record in scene.scopes)
    # the gates: V1 ran beside the kept production candidate
    assert [tree for tree in scene.gate_trees if tree[TEST] == V1] and all(tree[LIB] == LIB_20 for tree in scene.gate_trees)
    # work grants: one per attempt (initial, refused, fallback); the refused attempt keeps its grant (S8)
    assert seen["grants"] == 3
    # retry families: the gate failure charged the full-set family; the refusal charged nothing (F3)
    assert [who for who, _changed in seen["charges"]] == [None, "primary"]
    assert seen["charges"][0][1].get("retry_count") == 1 and seen["charges"][1][1] == {}
    unavailable = _events(scene, "model.candidate_unavailable")
    assert [(d["model"], d["reason_code"], d["terminal"], d["retry_family_charged"]) for d in unavailable] == [
        (PRIMARY, tb.CONTEXT_BUDGET_UNSATISFIABLE, False, False)]


def test_every_candidate_refused_before_dispatch_stops_with_the_candidate_reason_and_judges_no_regeneration(tmp_path):
    scene, seen = _run(tmp_path, test_answers=[V0, V1], semantic=["expectation_invalid"], refuse={PRIMARY, FALLBACK})
    result = scene.stop()
    assert result.get("quality_gates_passed") is not True and scene.applied() == {LIB: None, TEST: None}
    assert result.get("failure_category") == "model_candidate_unavailable", (result.get("failure_category"), result.get("environment_failure"))
    failure = str(result.get("environment_failure"))
    assert failure.startswith("MODEL_CANDIDATE_UNAVAILABLE:") and tb.CONTEXT_BUDGET_UNSATISFIABLE in failure
    assert "VERIFIER_SEMANTIC" not in failure
    assert list(dict.fromkeys(seen["refused"])) == [PRIMARY, FALLBACK] and seen["refused"].count(PRIMARY) == 1
    assert [entry for entry in seen["provider"] if entry[1] == "regeneration"] == []          # provider requests of the regeneration: 0
    assert scene.requested().count(TEST) == 1
    assert len(scene.subjects) == 1 and seen["takes"] == 1                                    # one decision; nobody was asked again
    assert [r["action"] for r in scene.rulings()] == ["regenerate_verifier"]                  # no needs_review ruling: nothing was judged
    assert [(d["candidate"], d["terminal"]) for d in _events(scene, "model.candidate_unavailable")] == [("primary", False), ("fallback", True)]
    assert seen["grants"] == 3
    assert [who for who, _changed in seen["charges"]] == [None, "primary", "fallback"]
    # no retry family charged for either refusal; the refused fallback's unsent attempt is un-counted (MODEL-CANDIDATE-UNAVAILABLE-001)
    assert seen["charges"][1][1] == {} and seen["charges"][2][1] == {"fallback_attempts_used": 0, "fallback_targeted_attempted": False}


def test_a_refused_primary_with_no_fallback_stops_with_the_candidate_reason(tmp_path):
    scene, seen = _run(tmp_path, test_answers=[V0, V1], semantic=["expectation_invalid"], refuse={PRIMARY}, fallback=False)
    result = scene.stop()
    assert result.get("failure_category") == "model_candidate_unavailable", (result.get("failure_category"), result.get("environment_failure"))
    assert "VERIFIER_SEMANTIC" not in str(result.get("environment_failure"))
    assert seen["refused"] == [PRIMARY] and scene.requested().count(TEST) == 1 and len(scene.subjects) == 1


# ------------------------------------------------------------------ the one-regeneration rule is not weakened

def test_a_regeneration_the_fallback_answered_with_the_same_bytes_is_spent_and_stops(tmp_path):
    """After the carried dispatch a provider DID answer: the existing rule judges it, and there is no further request on any candidate."""
    scene, seen = _run(tmp_path, test_answers=[V0, V0], semantic=["expectation_invalid"], refuse={PRIMARY})
    _stopped_for_review(scene, vs.REASON_REGENERATION_NOT_EXECUTED)
    assert [entry for entry in seen["provider"] if entry[1] == "regeneration"] == [(FALLBACK, "regeneration")]
    assert scene.requested().count(TEST) == 2 and len(scene.subjects) == 1 and seen["takes"] == 1


def test_a_carried_regeneration_that_fails_again_gets_no_second_regeneration(tmp_path):
    scene, seen = _run(tmp_path, test_answers=[V0, _test(11, "V1"), _test(20, "V2")], semantic=["expectation_invalid"], refuse={PRIMARY})
    _stopped_for_review(scene, vs.REASON_REGENERATION_SPENT)
    assert [entry for entry in seen["provider"] if entry[1] == "regeneration"] == [(FALLBACK, "regeneration")]
    assert scene.requested().count(TEST) == 2 and _test(20, "V2") not in [tree[TEST] for tree in scene.gate_trees]
    assert seen["takes"] == 1


def test_a_provider_failure_of_the_regeneration_still_spends_it(tmp_path):
    """Negative control for the carry: a request that REACHED the provider and failed there is the one regeneration (unchanged behaviour)."""
    scene, seen = _run(tmp_path, test_answers=[V0, RuntimeError("the model transport failed")], semantic=["expectation_invalid"], refuse=set())
    assert scene.stop().get("quality_gates_passed") is not True and scene.applied() == {LIB: None, TEST: None}
    assert scene.requested().count(TEST) == 2 and seen["refused"] == []
    assert _events(scene, "verifier_semantic.regeneration_carried") == []
    assert len(_regeneration_scopes(scene)) == 1


# ------------------------------------------------------------------ the owner's transition and the seam, directly

def _unit(read=None):
    return vs.UnitSemantics(ledger=vs.VerifierSemanticLedger(), run_id="r", plan_hash="p", unit_id="s1", goal=GOAL, regenerable=True, read_bytes=read)


def _sha(text):
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()


def test_the_undispatched_regeneration_is_pending_again_only_for_the_same_verifier_bytes():
    unit = _unit(read=lambda path: V0.encode())
    assert unit.regeneration_not_dispatched() is None, "nothing was in flight"
    unit.regenerating = (TEST, _sha(V0))
    assert unit.regeneration_not_dispatched() == TEST
    assert unit.pending == (TEST, _sha(V0)) and unit.regenerating is None and unit.denied == set()
    assert unit.regeneration_not_dispatched() is None, "a pending regeneration is not in flight"
    for read in (lambda path: V1.encode(), lambda path: None, None):          # the subject changed, is gone, or cannot be read
        changed = _unit(read=read)
        changed.regenerating = (TEST, _sha(V0))
        assert changed.regeneration_not_dispatched() is None
        assert changed.pending is None and changed.regenerating == (TEST, _sha(V0)), "left for the existing judgment"

    def unreadable(path):
        raise OSError("gone")

    broken = _unit(read=unreadable)
    broken.regenerating = (TEST, _sha(V0))
    assert broken.regeneration_not_dispatched() is None and broken.pending is None


def _seam_state(implicated, **budgets):
    state = GenerationState()
    state.attempt_number = 2
    state.last_implicated_files = implicated
    for name, value in budgets.items():
        setattr(state.budgets, name, value)
    return state


def test_the_seam_carries_only_after_a_candidate_refusal():
    unit = _unit(read=lambda path: V0.encode())
    unit.regenerating = (TEST, _sha(V0))
    ctx = SimpleNamespace(verifier_semantic=unit)
    state = _seam_state([LIB])
    assert retry_strategy_module._carry_undispatched_verifier_regeneration(state, ctx, None, None) is False
    assert unit.regenerating == (TEST, _sha(V0)) and unit.pending is None and state.last_implicated_files == [LIB]
    classified = SimpleNamespace(candidate_refusal_reason=tb.CONTEXT_BUDGET_UNSATISFIABLE)
    assert retry_strategy_module._carry_undispatched_verifier_regeneration(state, ctx, "primary", classified) is True
    assert unit.pending == (TEST, _sha(V0)) and state.last_implicated_files == [TEST] and state.last_missing_files is None
    assert retry_strategy_module._carry_undispatched_verifier_regeneration(state, SimpleNamespace(verifier_semantic=None), "primary", classified) is False


@pytest.mark.parametrize("action, primary_unavailable, implicated, admitted", [
    (RetryAction.FALLBACK_TARGETED, True, [TEST], True),          # the carried regeneration: the fallback's targeted attempt for exactly the verifier
    (RetryAction.FALLBACK_TARGETED, False, [TEST], False),        # the primary was not refused: only its own targeted attempt regenerates (unchanged)
    (RetryAction.FALLBACK_TARGETED, True, [LIB, TEST], False),    # not exactly the verifier
    (RetryAction.FULL_SET, True, [TEST], False),                  # a whole-unit attempt is never the one regeneration
    (RetryAction.TARGETED, False, [TEST], True),                  # the historical admission
])
def test_the_carried_regeneration_is_admitted_only_as_the_fallbacks_targeted_attempt_for_exactly_the_verifier(action, primary_unavailable, implicated, admitted):
    state = _seam_state(implicated)
    state.primary_candidate_unavailable = tb.CONTEXT_BUDGET_UNSATISFIABLE if primary_unavailable else None
    before = dataclasses.asdict(state.budgets)
    unit = _unit()
    unit.pending = (TEST, "0" * 64)
    ctx = SimpleNamespace(verifier_semantic=unit, max_retries=4, targeted_max_retries=3)
    with patch.object(retry_strategy_module, "fallback_routing_for_context", return_value=SimpleNamespace(available=True)), \
         patch.object(retry_strategy_module, "decide_for_state", return_value=RetryDecision(action, "scripted")):
        retry_strategy_module._close_unadmitted_verifier_regeneration(state, ctx)
    assert dataclasses.asdict(state.budgets) == before, "no counter was reset or replenished"
    if admitted:
        assert state.environment_failure is None and unit.pending == (TEST, "0" * 64)
    else:
        assert state.environment_failure.startswith(f"VERIFIER_SEMANTIC_AUTHORITY_REQUIRED: {vs.REASON_REGENERATION_NOT_ADMITTED}:")
        assert unit.pending is None and unit.denied == {TEST}
