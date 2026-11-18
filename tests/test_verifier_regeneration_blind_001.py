"""VERIFIER-REGENERATION-BLIND-001 (Stage B run 6, 20261118T141613-04525731), scripted and model-free through the real enforce controller:

    a run-authored verifier PASSES its gate  ->  the person answers expectation_invalid
    ->  MEASURED in the live run: the one regeneration request said only "a person declared its expectation invalid", in a repair mode that offers
        "no change"; the model answered NO_CHANGE and the unit stopped VERIFIER_SEMANTIC_REGENERATION_NOT_EXECUTED.

The contract (owner ruling 2026-11-18): an expectation_invalid decision is verdict + bounded rationale + the exact review fingerprint. The rationale is
REQUIRED for a regeneration, is human semantic-review evidence about that exact verifier (never goal, requirement or Skill text), travels only in the
one request that regenerates that file, and that request offers no "no change" outcome. One provider-answered regeneration stays the limit.

The scripted model here behaves like the live one: it rewrites the verifier only when the request tells it what is invalid, else it answers NO_CHANGE.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from test_verifier_semantic_loop_001 import GOAL, LIB, LIB_10, LIB_20, TEST, Scene, _gate_result, _stopped_for_review

import kriya.policy.approval as policy
from kriya.agents.response_protocol import structured_contract
from kriya.config.config import FallbackModelConfig
from kriya.core import token_budget as tb
from kriya.core.llm import LLMClient
from kriya.policy.approval import (
    CANNOT_DECIDE,
    EXPECTATION_INVALID,
    EXPECTATION_VALID,
    VerifierSemanticAllowance,
    VerifierSemanticDecision,
    VerifierSemanticSubject,
    verifier_semantic_rationale,
)
from kriya.workflow import verifier_semantic as vs

PRIMARY, FALLBACK = "dev-model", "fallback-model"
RATIONALE = "null/empty behavior is not specified by canonical goal"
MARKER = "=== Operator semantic-review rationale for this exact verifier ==="
NO_CHANGE_LINE = f'<<<KRIYA:NO_CHANGE path="{TEST}">>>'
# V0 passes its gate and invents an expectation the goal does not state; V1 keeps the contract-supported round trip only.
V0 = ("from shop import page_size\n\n\ndef test_none_is_none():\n    assert page_size.__doc__ is None\n\n\n"
      "def test_size():  # V0\n    assert page_size() == 20\n")
V1 = "from shop import page_size\n\n\ndef test_size():  # V1\n    assert page_size() == 20\n"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _invalid(rationale=RATIONALE):
    return lambda subject: VerifierSemanticDecision.decided_as(EXPECTATION_INVALID, subject.fingerprint, rationale)


def _run(tmp_path, *, semantic, model, refuse=(), gate=None, fallback=False):
    """``model(request)``: the scripted Developer's answer for the verifier, given the request it was sent ({"system", "user", "model"}); None = it
    answers "no change". ``refuse``: models whose regeneration request is refused before the provider."""
    seen = {"requests": [], "takes": 0, "refused": []}

    def answer():
        return model(seen["requests"][-1])

    def configure(cfg):
        if fallback:
            cfg.llm_chain = [FallbackModelConfig(model=FALLBACK, base_url=cfg.llm.base_url, context_window=32768,
                                                 capabilities=cfg.llm.capabilities.model_copy())]

    scene = Scene(lib=[LIB_20], test=[V0], semantic=semantic, gate=gate, configure=configure)
    scene.units = {"s1": {LIB: lambda: scene._next(scene.lib), TEST: answer}}
    real_budget, real_take = LLMClient._dispatch_budget, VerifierSemanticAllowance.take_regeneration

    def dispatch_budget(self, *, model, messages, **kwargs):
        system, user = str(messages[0].get("content") or ""), str(messages[-1].get("content") or "")
        if "Developer Agent" in system.splitlines()[0]:
            if MARKER in user and model in refuse:
                seen["refused"].append(model)
                with pytest.raises(tb.ContextBudgetUnsatisfiableError) as refused:
                    tb.plan_dispatch(messages=[{"role": "user", "content": "u" * 90000}], requested_max_tokens=16384, context_window=32768,
                                     window_source="requested_per_request")
                refused.value.model = model
                raise refused.value
            seen["requests"].append({"system": system, "user": user, "model": model})
        return real_budget(self, model=model, messages=messages, **kwargs)

    def take(allowance, decision, subject_now):
        taken = real_take(allowance, decision, subject_now)
        seen["takes"] += int(taken)
        return taken

    with patch.object(LLMClient, "_dispatch_budget", new=dispatch_budget), patch.object(VerifierSemanticAllowance, "take_regeneration", new=take):
        scene.run(tmp_path)
    return scene, seen


def _like_the_live_model(request):
    """Run 6's model: with nothing to correct it leaves the file unchanged; told what is invalid, it rewrites the verifier."""
    if MARKER not in request["user"]:
        return V0 if "MODE: CREATE_FULL_FILE" in request["system"] else None
    return V1


def _for(seen, path=TEST):
    return [request for request in seen["requests"] if f"'{path}'" in request["system"]]


# ------------------------------------------------------------------ the run 6 sequence

def test_run6_sequence_the_rationale_reaches_the_one_regeneration_and_the_rewritten_verifier_goes_through_the_gates(tmp_path):
    scene, seen = _run(tmp_path, semantic=[_invalid(), "expectation_valid"], model=_like_the_live_model)
    result = scene.stop()
    assert result.get("quality_gates_passed") is True, (result.get("failure_category"), result.get("environment_failure"))
    assert scene.applied() == {LIB: LIB_20, TEST: V1}, "the unsupported expectation is gone and the contract-supported round trip is kept"
    planned, regeneration = _for(seen)
    # the planned request knows nothing of a rationale; the regeneration request carries the person's own line, labelled, for exactly this file
    assert MARKER not in planned["user"] and RATIONALE not in planned["user"]
    assert regeneration["user"].count(MARKER) == 1 and f"<<<OPERATOR_RATIONALE\n{RATIONALE}\nOPERATOR_RATIONALE>>>" in regeneration["user"]
    assert f"Rewrite exactly '{TEST}'" in regeneration["user"] and f"sha256 {_sha(V0)[:16]}" in regeneration["user"]
    assert V0 in regeneration["user"], "the exact verifier under review is in the request"
    # evidence, not authority: never in the system prompt, the goal or the task of any request
    for request in seen["requests"]:
        assert RATIONALE not in request["system"] and MARKER not in request["system"]
        task = request["user"].split("=== Task ===")[1].split(MARKER)[0]
        assert RATIONALE not in task
    # this operation offers no "no change" outcome and no redirect; the planned request never did either
    assert NO_CHANGE_LINE not in regeneration["system"] and "KRIYA:REDIRECT" not in regeneration["system"]
    assert "KRIYA:FILE" in regeneration["system"] or "KRIYA:EDIT" in regeneration["system"]
    # no other file's request ever carries it
    assert all(MARKER not in request["user"] and RATIONALE not in request["user"] for request in _for(seen, LIB))
    # the person: one decision with a rationale on V0, a fresh decision on the regenerated bytes; one allowance
    assert [(s.review_phase, s.verifier_files[0][1]) for s in scene.subjects] == [("passing_verifier", _sha(V0)), ("passing_verifier", _sha(V1))]
    assert seen["takes"] == 1
    rulings = scene.rulings()
    # a passing verifier's decision is recorded at the pass seam and again where the retry loop starts it: both carry the rationale as evidence
    assert [(r["seam"], r["action"], r.get("operator_rationale")) for r in rulings] == [
        ("pass_before_terminal_regression", "regenerate_verifier", RATIONALE), ("failure_before_attribution", "regenerate_verifier", RATIONALE)]
    # the regenerated verifier ran beside the kept production candidate, under the one-path write scope
    assert [tree for tree in scene.gate_trees if tree[TEST] == V1] and all(tree[LIB] == LIB_20 for tree in scene.gate_trees)
    assert [record["during"][0] for record in scene.scopes].count([TEST]) == 1


def test_a_no_change_answer_to_the_regeneration_is_not_a_success_and_there_is_no_second_request(tmp_path):
    """The live reply under the new request: the model still says "no change". Nothing was regenerated: the unit stops truthfully."""
    scene, seen = _run(tmp_path, semantic=[_invalid()], model=lambda request: V0 if MARKER not in request["user"] else None)
    _stopped_for_review(scene, vs.REASON_REGENERATION_NOT_EXECUTED)
    assert len(_for(seen)) == 2 and seen["takes"] == 1 and len(scene.subjects) == 1


# ------------------------------------------------------------------ negative controls (owner ruling, 1-8)

def test_control_1_an_invalid_verdict_without_a_rationale_dispatches_no_regeneration(tmp_path):
    """The desktop review of today (it cannot supply the field): expectation_invalid alone. Fail closed, nothing synthesized, nothing spent."""
    bare = lambda subject: VerifierSemanticDecision.decided_as(EXPECTATION_INVALID, subject.fingerprint)   # noqa: E731 - one scripted answer
    scene, seen = _run(tmp_path, semantic=[bare], model=_like_the_live_model)
    _stopped_for_review(scene, vs.REASON_RATIONALE_REQUIRED)
    assert len(_for(seen)) == 1, "only the planned request: no blind regeneration"
    assert seen["takes"] == 0 and all(MARKER not in request["user"] for request in seen["requests"])
    assert [r["action"] for r in scene.rulings()] == ["needs_review"]
    assert [record["during"][0] for record in scene.scopes].count([TEST]) == 0


def _subject(source=V0, **over) -> VerifierSemanticSubject:
    fields = dict(run_id="r", plan_hash="sha256:" + "1a" * 32, verifier_unit_id="s1", verifier_files=((TEST, _sha(source)),),
                  goal_digest=hashlib.sha256(GOAL.encode()).hexdigest(), verification_contract_digest=None, acceptance_criteria=(),
                  invariants=(("gi1", "x"),), review_phase=policy.REVIEW_PHASE_PASSING, contract_text=GOAL, verifier_sources=((TEST, source),))
    fields.update(over)
    return VerifierSemanticSubject(**fields)


def test_control_1_the_ledger_takes_no_allowance_for_an_invalid_verdict_without_a_rationale():
    ledger, subject = vs.VerifierSemanticLedger(), _subject()
    refused = ledger.after_decision(subject, VerifierSemanticDecision.decided_as(EXPECTATION_INVALID, subject.fingerprint))
    assert (refused.action, refused.reason_code, refused.rationale) == (vs.Action.NEEDS_REVIEW, vs.REASON_RATIONALE_REQUIRED, None)
    assert not ledger.allowance.regeneration_spent(subject), "nothing was spent by the refusal"
    granted = ledger.after_decision(subject, VerifierSemanticDecision.decided_as(EXPECTATION_INVALID, subject.fingerprint, RATIONALE))
    assert granted == vs.SubjectDecision(vs.Action.REGENERATE_VERIFIER, TEST, rationale=RATIONALE)
    assert vs.REASON_RATIONALE_REQUIRED in vs.REVIEW_REASON_CODES


def test_control_2_a_rationale_bound_to_verifier_a_is_not_used_for_changed_verifier_b():
    unit = vs.UnitSemantics(ledger=vs.VerifierSemanticLedger(), run_id="r", plan_hash="p", unit_id="s1", goal=GOAL, regenerable=True)
    unit.rationales[(TEST, _sha(V0))] = RATIONALE
    unit.regenerating = (TEST, _sha(V0))
    request = unit.regeneration_request([TEST])
    assert (request.path, request.sha256, request.rationale) == (TEST, _sha(V0), RATIONALE)
    unit.regenerating = (TEST, _sha(V1))                                    # the same path with other bytes: another subject of review
    assert unit.regeneration_request([TEST]) is None
    # and the decision itself is bound: made for A's fingerprint, it is stale for B
    ledger, a, b = vs.VerifierSemanticLedger(), _subject(V0), _subject(V1)
    stale = ledger.after_decision(b, VerifierSemanticDecision.decided_as(EXPECTATION_INVALID, a.fingerprint, RATIONALE))
    assert (stale.action, stale.reason_code, stale.rationale) == (vs.Action.NEEDS_REVIEW, vs.REASON_DECISION_STALE, None)


def test_control_3_candidate_failover_keeps_the_same_pending_rationale_and_asks_nobody_again(tmp_path):
    scene, seen = _run(tmp_path, semantic=[_invalid(), "expectation_valid"], model=_like_the_live_model, refuse={PRIMARY}, fallback=True)
    result = scene.stop()
    assert result.get("quality_gates_passed") is True, (result.get("failure_category"), result.get("environment_failure"))
    assert seen["refused"] == [PRIMARY]
    planned, regeneration = _for(seen)
    assert (planned["model"], regeneration["model"]) == (PRIMARY, FALLBACK)
    assert f"<<<OPERATOR_RATIONALE\n{RATIONALE}\nOPERATOR_RATIONALE>>>" in regeneration["user"] and NO_CHANGE_LINE not in regeneration["system"]
    assert [s.verifier_files[0][1] for s in scene.subjects] == [_sha(V0), _sha(V1)] and seen["takes"] == 1
    assert scene.applied() == {LIB: LIB_20, TEST: V1}


def test_control_4_the_request_is_given_only_to_the_generation_that_is_the_regeneration():
    unit = vs.UnitSemantics(ledger=vs.VerifierSemanticLedger(), run_id="r", plan_hash="p", unit_id="s1", goal=GOAL, regenerable=True)
    unit.rationales[(TEST, _sha(V0))] = RATIONALE
    assert unit.regeneration_request([TEST]) is None, "pending or idle: no generation is the regeneration yet"
    unit.pending = (TEST, _sha(V0))
    assert unit.regeneration_request([TEST]) is None
    unit.regenerating, unit.pending = unit.pending, None
    assert unit.regeneration_request([TEST]).rationale == RATIONALE
    for targets in ([LIB], [LIB, TEST], [TEST, LIB], [], None):               # another file, a wider attempt, a full-set generation
        assert unit.regeneration_request(targets) is None, targets


def test_control_5_no_change_stays_a_legal_outcome_of_a_generic_repair(tmp_path):
    """A failing gate with no run-authored verifier subject is the historical retry: its targeted repair request still offers "no change"."""
    def gate(tree: Path, _call: int):
        result = dict(_gate_result(tree))
        result.pop("test_execution", None)
        return result

    scene, seen = _run(tmp_path, semantic=[], model=lambda request: V0.replace("== 20", "== 10"), gate=gate)
    repairs = [request for request in _for(seen) if "MODE: REPAIR" in request["system"]]
    assert repairs and all(NO_CHANGE_LINE in request["system"] for request in repairs)
    assert all(MARKER not in request["user"] for request in seen["requests"]) and scene.rulings() == []
    # and the protocol owner itself: the outcome is offered exactly when the caller allows it
    offered = structured_contract(TEST, analysis_required=True, allow_edit=True, allow_file=True, allow_no_change=True)
    withheld = structured_contract(TEST, analysis_required=True, allow_edit=True, allow_file=True, allow_no_change=False)
    assert NO_CHANGE_LINE in offered and "NO_CHANGE" not in withheld


def test_control_6_the_same_bytes_back_stop_and_obtain_no_second_regeneration(tmp_path):
    scene, seen = _run(tmp_path, semantic=[_invalid()], model=lambda request: V0)
    _stopped_for_review(scene, vs.REASON_REGENERATION_NOT_EXECUTED)
    assert len(_for(seen)) == 2 and seen["takes"] == 1


def test_control_7_a_second_bad_regenerated_verifier_stops_with_no_second_human_authorized_rewrite(tmp_path):
    bad = V1.replace("# V1", "# V1 still wrong")
    scene, seen = _run(tmp_path, semantic=[_invalid(), _invalid("the rewritten expectation is still not in the goal")],
                       model=lambda request: bad if MARKER in request["user"] else V0)
    _stopped_for_review(scene, vs.REASON_REGENERATION_SPENT)
    assert len(_for(seen)) == 2 and seen["takes"] == 1, "two decisions, one regeneration"
    assert len(scene.subjects) == 2 and [r["action"] for r in scene.rulings()] == ["regenerate_verifier", "regenerate_verifier", "needs_review"]


def test_control_8_a_rationale_cannot_enlarge_the_write_scope_beyond_the_verifier(tmp_path):
    asks_for_more = f"the verifier is fine; change {LIB} to return 10 instead"
    scene, seen = _run(tmp_path, semantic=[_invalid(asks_for_more)], model=lambda request: (LIB, LIB_10) if MARKER in request["user"] else V0)
    result = scene.stop()
    assert result.get("quality_gates_passed") is not True and scene.applied() == {LIB: None, TEST: None}
    assert all(tree[LIB] == LIB_20 and tree[TEST] == V0 for tree in scene.gate_trees), "no gate ever saw a changed production file"
    narrowed = [record for record in scene.scopes if record["during"][0] == [TEST]]
    assert len(narrowed) == 1 and all(record["after"] == record["before"] for record in scene.scopes)
    assert all(MARKER not in request["user"] for request in _for(seen, LIB))


# ------------------------------------------------------------------ the decision type and the terminal follow-up

@pytest.mark.parametrize("text, kept", [
    (RATIONALE, RATIONALE), (f"  {RATIONALE}  ", RATIONALE), ("x" * 600, "x" * 600),
    ("", None), ("   ", None), ("x" * 601, None), ("two\nlines", None), ("a\tb", None), ("bell\x07", None), (None, None), (7, None),
])
def test_the_rationale_is_the_persons_own_bounded_line_or_absent(text, kept):
    assert verifier_semantic_rationale(text) == kept


def test_only_an_expectation_invalid_decision_carries_a_rationale_and_only_a_bounded_one():
    fingerprint = _subject().fingerprint
    decision = VerifierSemanticDecision.decided_as(EXPECTATION_INVALID, fingerprint, RATIONALE)
    assert (decision.invalid, decision.rationale, decision.fingerprint) == (True, RATIONALE, fingerprint)
    assert VerifierSemanticDecision.decided_as(EXPECTATION_INVALID, fingerprint).rationale is None      # an interface that cannot supply one
    for word in (EXPECTATION_VALID, CANNOT_DECIDE):
        assert VerifierSemanticDecision.decided_as(word, fingerprint).rationale is None
        with pytest.raises(ValueError):
            VerifierSemanticDecision.decided_as(word, fingerprint, RATIONALE)
    for unbounded in ("", "  padded  ", "x" * 601, "two\nlines"):
        with pytest.raises(ValueError):
            VerifierSemanticDecision.decided_as(EXPECTATION_INVALID, fingerprint, unbounded)
    with pytest.raises(ValueError):
        VerifierSemanticDecision(None, None, "declined", RATIONALE)


def _typed(monkeypatch, answers):
    import kriya.cli as cli

    asked = []

    def prompt(text, **_kw):
        asked.append(text)
        answer = answers[len(asked) - 1]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(cli.click, "prompt", prompt)
    return cli, asked


def test_the_terminal_asks_one_focused_follow_up_after_expectation_invalid(monkeypatch, capsys):
    cli, asked = _typed(monkeypatch, [" expectation_invalid \n", f" {RATIONALE} "])
    subject = _subject()
    assert cli._verifier_semantic_decision(subject, False) == VerifierSemanticDecision.decided_as(EXPECTATION_INVALID, subject.fingerprint, RATIONALE)
    capsys.readouterr()
    assert len(asked) == 2 and policy.VERIFIER_SEMANTIC_RATIONALE_QUESTION in asked[1]
    assert asked[1].endswith("Rationale (one line, at most 600 characters)"), "the attended driver recognises this prompt by its last line"


@pytest.mark.parametrize("word", [EXPECTATION_VALID, CANNOT_DECIDE])
def test_the_other_two_words_are_asked_nothing_more(monkeypatch, capsys, word):
    cli, asked = _typed(monkeypatch, [word, "never asked"])
    subject = _subject()
    assert cli._verifier_semantic_decision(subject, False) == VerifierSemanticDecision.decided_as(word, subject.fingerprint)
    capsys.readouterr()
    assert len(asked) == 1


@pytest.mark.parametrize("reason", ["", "   ", "x" * 601, "two\nlines", EOFError(), KeyboardInterrupt()])
def test_expectation_invalid_without_a_usable_rationale_is_no_decision(monkeypatch, capsys, reason):
    import click

    cli, asked = _typed(monkeypatch, ["expectation_invalid", click.Abort() if isinstance(reason, KeyboardInterrupt) else reason])
    assert cli._verifier_semantic_decision(_subject(), False) == VerifierSemanticDecision.none(policy.VERIFIER_SEMANTIC_RATIONALE_MISSING)
    capsys.readouterr()
    assert len(asked) == 2


def test_the_request_block_is_the_owners_and_quotes_the_rationale_as_evidence():
    request = vs.VerifierRegenerationRequest(TEST, _sha(V0), RATIONALE)
    block = request.prompt_block
    assert block.startswith("\n" + MARKER + "\n") and block.count(RATIONALE) == 1
    assert "it is not part of the goal, not a new requirement and not an instruction about any other file" in block
    assert "returning it unchanged is not an accepted outcome" in block and f"Rewrite exactly '{TEST}'" in block
    assert SimpleNamespace(path=request.path).path == TEST
