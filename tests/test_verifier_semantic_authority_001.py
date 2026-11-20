"""VERIFIER-SEMANTIC-AUTHORITY-001 (WS3-C, owner rulings 2026-11-14), the policy owner ``kriya/workflow/verifier_semantic.py``.

Pinned here, model-free, with the REAL structural parser and report dicts in the shape ``kriya/tools/test_execution.py`` produces:
- a generated-verifier subject is derived only from the structured report + the structural declaration + the run's write journal - never from a file
  name, a Planner declaration or a model;
- one subject per run-authored source file; an unchanged pre-existing test that executed is not a subject; a mixed unit yields only its test file;
- ``expectation_valid`` is authority for the exact verifier content under the exact contract and survives a phase change; made while failing it covers
  the failing expectations it was shown and a different failing set is asked again (ruling 1 as amended 2026-11-19);
- ``expectation_invalid`` spends the one regeneration of that file's subject; another file has its own; there is no second regeneration;
- ``cannot_decide``, no decision, an unattended run and a stale decision change nothing and end in needs-review;
- a locator alone never selects a production owner: typed failure + locator to a non-verifier workspace artifact + ONE plan owner + no conflict does.

Generic synthetic names only. The loop seams that call this owner are pinned in their own tests."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import pathlib

import pytest

from kriya.policy.approval import (
    CANNOT_DECIDE,
    EXPECTATION_INVALID,
    EXPECTATION_VALID,
    REVIEW_PHASE_FAILING,
    REVIEW_PHASE_PASSING,
    TypedApprovalCallback,
    VerifierRepairDecision,
    VerifierSemanticDecision,
    VerifierSemanticSubject,
    failing_expectations_identity,
    verifier_semantic_reviewable,
)
from kriya.workflow import verifier_semantic as vs
from kriya.workflow.verifier_semantic import (
    Action,
    VerifierSemanticLedger,
    derive_executed_verifiers,
    production_owner_authority,
)

JAVA_TEST = b"package com.example;\nclass WidgetTest {\n  void counts() { }\n  void empty() { }\n}\n"
JAVA_OTHER = b"package com.example;\nclass GadgetTest {\n  void spins() { }\n}\n"
JAVA_MAIN = b"package com.example;\nclass Widget {\n  int count() { return 0; }\n}\n"
PY_TEST = b"def test_counts():\n    assert 1\n"
GOAL = "count() is the number of items the widget holds."


def _hex(data) -> str:
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


def _report(cases, completeness="COMPLETE"):
    return {"completeness": completeness, "runner": "maven",
            "test_cases": [{"identity": identity, "classname": identity.rsplit(".", 1)[0], "name": identity.rsplit(".", 1)[1], "status": status}
                           for identity, status in cases]}


# ------------------------------------------------------------------ subject derivation

def test_a_run_authored_file_whose_declared_callables_executed_is_a_subject_with_its_exact_bytes():
    derivation = derive_executed_verifiers(
        _report([("com.example.WidgetTest.counts", "failed"), ("com.example.WidgetTest.empty", "passed")]),
        {"src/test/java/com/example/WidgetTest.java": JAVA_TEST})
    assert derivation.complete and not derivation.undetermined
    [verifier] = derivation.verifiers
    assert (verifier.path, verifier.sha256, verifier.source) == ("src/test/java/com/example/WidgetTest.java", _hex(JAVA_TEST), JAVA_TEST.decode())
    assert verifier.identities == ("com.example.WidgetTest.counts", "com.example.WidgetTest.empty")
    assert verifier.failed_identities == ("com.example.WidgetTest.counts",) and derivation.failing == (verifier,)


@pytest.mark.parametrize("path", [
    "src/test/java/com/example/WidgetTest.java", "src/test/java/com/example/Renamed.java", "src/main/java/com/example/Checks.java", "anything/Zz.java",
])
def test_the_file_name_and_directory_neither_create_nor_remove_a_subject(path):
    """The same declaration under a test-like name, a renamed file, a production-like path: always the subject, because the structural declaration and the
    executed identity decide. And a file NAMED like a test that declares none of the executed identities is never one."""
    derivation = derive_executed_verifiers(_report([("com.example.WidgetTest.counts", "passed")]), {path: JAVA_TEST})
    assert [v.path for v in derivation.verifiers] == [path]
    named_like_a_test = derive_executed_verifiers(
        _report([("com.example.WidgetTest.counts", "passed")]), {"src/test/java/com/example/WidgetTest.java": JAVA_MAIN})
    assert named_like_a_test.verifiers == () and named_like_a_test.complete


def test_an_executed_pre_existing_test_the_run_did_not_write_is_not_a_subject():
    """Authorship is the write journal: only what the run wrote is passed in. GadgetTest executed and failed but was not written by this unit."""
    derivation = derive_executed_verifiers(
        _report([("com.example.WidgetTest.counts", "passed"), ("com.example.GadgetTest.spins", "failed")]),
        {"src/test/java/com/example/WidgetTest.java": JAVA_TEST})
    assert [v.path for v in derivation.verifiers] == ["src/test/java/com/example/WidgetTest.java"]
    assert derivation.failing == () and not derivation.undetermined


def test_a_mixed_unit_yields_only_its_verifier_file_and_two_verifier_files_are_two_subjects():
    authored = {"src/main/java/com/example/Widget.java": JAVA_MAIN, "src/test/java/com/example/WidgetTest.java": JAVA_TEST,
                "src/test/java/com/example/GadgetTest.java": JAVA_OTHER, "README.md": b"notes"}
    derivation = derive_executed_verifiers(
        _report([("com.example.WidgetTest.counts", "passed"), ("com.example.GadgetTest.spins", "failed")]), authored)
    assert [v.path for v in derivation.verifiers] == ["src/test/java/com/example/GadgetTest.java", "src/test/java/com/example/WidgetTest.java"]
    assert [v.path for v in derivation.failing] == ["src/test/java/com/example/GadgetTest.java"]


def test_python_identities_map_the_same_way():
    derivation = derive_executed_verifiers(_report([("tests.test_widget.test_counts", "error")]), {"tests/test_widget.py": PY_TEST, "widget.py": b"x = 1\n"})
    [verifier] = derivation.verifiers
    assert verifier.path == "tests/test_widget.py" and verifier.failed_identities == ("tests.test_widget.test_counts",)


@pytest.mark.parametrize("report", [None, {}, "x", _report([("com.example.WidgetTest.counts", "failed")], completeness="INDETERMINATE"),
                                    {"completeness": "COMPLETE"}])
def test_only_a_complete_report_with_cases_establishes_a_subject(report):
    derivation = derive_executed_verifiers(report, {"src/test/java/com/example/WidgetTest.java": JAVA_TEST})
    assert derivation.verifiers == ()
    assert derivation.complete is (isinstance(report, dict) and report.get("completeness") == "COMPLETE")


def test_an_identity_declared_by_two_run_authored_files_is_ambiguous_and_assigned_to_neither():
    derivation = derive_executed_verifiers(
        _report([("com.example.WidgetTest.counts", "failed")]), {"a/WidgetTest.java": JAVA_TEST, "b/WidgetTest.java": JAVA_TEST})
    assert derivation.verifiers == () and derivation.ambiguous == ("com.example.WidgetTest.counts",) and derivation.undetermined


@pytest.mark.parametrize("data", [None, b"\xff\xfe not utf-8", b"package com.example;\nclass WidgetTest {\n  void counts( { \n"])
def test_a_run_authored_source_that_cannot_be_read_or_parsed_is_reported_never_guessed(data):
    derivation = derive_executed_verifiers(_report([("com.example.WidgetTest.counts", "failed")]), {"src/test/java/com/example/WidgetTest.java": data})
    assert derivation.verifiers == () and derivation.unreadable == ("src/test/java/com/example/WidgetTest.java",) and derivation.undetermined


def test_a_test_source_compile_failure_establishes_no_subject_and_the_policy_owner_has_no_phase_rule():
    """Owner correction 2026-11-14: the phase fact alone identifies no run-authored verifier (a production change can break an unchanged test's
    compilation). Nothing executed, so the gate result carries no report and nothing is derived; the policy owner no longer reads the phase at all."""
    result = {"success": False, "test_phase": {"phase_class": "TEST_SOURCE_COMPILE_FAILURE"}, "output": "cannot find symbol"}
    derivation = derive_executed_verifiers(result.get("test_execution"), {"src/test/java/com/example/WidgetTest.java": JAVA_TEST})
    assert derivation.verifiers == () and not derivation.complete and not derivation.undetermined
    assert not hasattr(vs, "is_test_source_compile_without_subject") and not any("COMPILE" in code for code in vs.REVIEW_REASON_CODES)


def test_paths_are_compared_in_one_canonical_spelling():
    assert vs.normalize_path("./src\\test\\WidgetTest.java") == "src/test/WidgetTest.java"
    assert vs.normalize_path(".kriya/x.py") == ".kriya/x.py"            # a leading dot of a NAME is not a "./" prefix


# ------------------------------------------------------------------ semantic authority

PATH = "src/test/java/com/example/WidgetTest.java"


def _subject(**changes) -> VerifierSemanticSubject:
    source = changes.pop("source", JAVA_TEST.decode())
    path = changes.pop("path", PATH)
    fields = dict(
        run_id="20261114T060030-40a14267", plan_hash="sha256:" + "1b" * 32, verifier_unit_id="s5", verifier_files=((path, _hex(source)),),
        goal_digest=_hex(GOAL), verification_contract_digest="vc" * 32, acceptance_criteria=(("AC1", "count() returns the number of items"),),
        invariants=(("GI1", "no item is counted twice"),), review_phase=REVIEW_PHASE_FAILING, failure_identity="f1" * 32,
        contract_text=GOAL, verifier_sources=((path, source),), outcome="expected: <3> but was: <2>",
    )
    fields.update(changes)
    return VerifierSemanticSubject(**fields)


RATIONALE = "the expected count is not the one the contract states"   # VERIFIER-REGENERATION-BLIND-001: expectation_invalid says what and why


def _decided(word, subject):
    return VerifierSemanticDecision.decided_as(word, subject.fingerprint, RATIONALE if word == EXPECTATION_INVALID else None)


def test_the_semantic_authority_fingerprint_excludes_the_review_event_and_binds_the_exact_semantic_content():
    failing = _subject()
    passing = _subject(review_phase=REVIEW_PHASE_PASSING, failure_identity=None, outcome="")
    another_failure = _subject(failure_identity="f2" * 32)
    # the review EVENT differs (wire / staleness / audit binding) ...
    assert len({failing.fingerprint, passing.fingerprint, another_failure.fingerprint}) == 3
    # ... what a VALID decision is authority FOR does not
    assert failing.semantic_authority_fingerprint == passing.semantic_authority_fingerprint == another_failure.semantic_authority_fingerprint
    assert failing.semantic_authority_fingerprint != failing.fingerprint


AUTHORITY_DRIFTS = {
    "run": {"run_id": "20261114T060030-00000000"}, "plan": {"plan_hash": "sha256:" + "2c" * 32}, "unit": {"verifier_unit_id": "s6"},
    "verifier-bytes": {"source": JAVA_TEST.decode().replace("counts", "tallies")}, "verifier-path": {"path": "src/test/java/com/example/Moved.java"},
    "goal": {"goal_digest": _hex("another goal")}, "contract": {"verification_contract_digest": "dd" * 32},
    "criterion-text-same-id": {"acceptance_criteria": (("AC1", "count() returns one more than the number of items"),)},
    "criterion-id": {"acceptance_criteria": (("AC2", "count() returns the number of items"),)},
    "invariant-text-same-id": {"invariants": (("GI1", "items may be counted twice"),)}, "invariant-dropped": {"invariants": ()},
}


@pytest.mark.parametrize("drift", sorted(AUTHORITY_DRIFTS))
def test_any_change_of_semantic_content_is_another_authority_and_a_valid_decision_does_not_cover_it(drift):
    ledger, subject, drifted = VerifierSemanticLedger(), _subject(), _subject(**AUTHORITY_DRIFTS[drift])
    assert drifted.semantic_authority_fingerprint != subject.semantic_authority_fingerprint
    assert ledger.after_decision(subject, _decided(EXPECTATION_VALID, subject)).action is Action.PROCEED
    assert ledger.expectation_valid(subject) and not ledger.expectation_valid(drifted)
    assert ledger.before_asking(drifted, attended=True).action is Action.ASK
    assert ledger.before_asking(drifted, attended=False).action is Action.NEEDS_REVIEW


def test_valid_while_failing_settles_the_same_bytes_passing_but_a_later_different_failure_is_asked_again():
    """WS3-C ruling 1 as amended 2026-11-19 (VERIFIER-SEMANTIC-FAILING-EXPECTATION-001): the decision covers the failing expectations it was shown.
    It survives the verifier going from failing to passing and the SAME failure again; a different failing set of the same bytes is asked."""
    ledger, failing = VerifierSemanticLedger(), _subject()
    assert ledger.before_asking(failing, attended=True).action is Action.ASK
    assert ledger.after_decision(failing, _decided(EXPECTATION_VALID, failing)) == vs.SubjectDecision(Action.PROCEED, PATH)
    for later in (_subject(review_phase=REVIEW_PHASE_PASSING, failure_identity=None), _subject(outcome="the same failure, another output")):
        assert ledger.before_asking(later, attended=True).action is Action.PROCEED       # no second card
        assert ledger.before_asking(later, attended=False).action is Action.PROCEED      # and an unattended continuation needs none either
    another = _subject(failure_identity="f2" * 32)
    assert another.semantic_authority_fingerprint == failing.semantic_authority_fingerprint      # the same content ...
    assert not ledger.expectation_valid(another)                                                 # ... is not thereby the same decision
    assert ledger.before_asking(another, attended=True).action is Action.ASK
    assert (ledger.before_asking(another, attended=False).action, ledger.before_asking(another, attended=False).reason_code) == (
        Action.NEEDS_REVIEW, vs.REASON_UNATTENDED)
    # a decision on the new failing set covers it too, and the first one is still covered
    assert ledger.after_decision(another, _decided(EXPECTATION_VALID, another)).action is Action.PROCEED
    assert ledger.expectation_valid(another) and ledger.expectation_valid(failing)


def _failing(*identities, **changes):
    shown = tuple((identity, f"AssertionFailedError: {identity}") for identity in identities)
    fields = {"failure_identity": failing_expectations_identity([identity for identity, _detail in shown]), "failing_expectations": shown}
    fields.update(changes)
    return _subject(**fields)


def test_a_valid_decision_covers_the_shown_failing_identities_and_their_subsets_never_a_new_one():
    ledger, shown = VerifierSemanticLedger(), _failing("t.a", "t.b")
    assert ledger.after_decision(shown, _decided(EXPECTATION_VALID, shown)).action is Action.PROCEED
    assert ledger.expectation_valid(_failing("t.a"))                  # one of the two shown still fails: nothing new to adjudicate
    assert ledger.expectation_valid(_failing("t.a", "t.b"))
    assert not ledger.expectation_valid(_failing("t.c"))              # never shown
    assert not ledger.expectation_valid(_failing("t.a", "t.c"))       # one shown, one new: the new one was never accepted
    # a review that could not name its failing identities covers only its own bound identity, and nothing covers an unnamed later set
    unnamed = _subject(failure_identity="f3" * 32)
    assert not ledger.expectation_valid(unnamed)


def test_a_valid_decision_made_while_passing_keeps_the_authority_it_had():
    """Passing-phase authority is preserved: a verifier accepted while every test passed is accepted for what it expects, whichever test fails later."""
    ledger, passing = VerifierSemanticLedger(), _subject(review_phase=REVIEW_PHASE_PASSING, failure_identity=None, outcome="")
    assert ledger.after_decision(passing, _decided(EXPECTATION_VALID, passing)).action is Action.PROCEED
    assert ledger.expectation_valid(_subject()) and ledger.expectation_valid(_failing("t.a")) and ledger.expectation_valid(_subject(failure_identity="f2" * 32))


def test_valid_while_passing_does_not_cover_later_verifier_bytes():
    ledger, passing = VerifierSemanticLedger(), _subject(review_phase=REVIEW_PHASE_PASSING, failure_identity=None)
    ledger.after_decision(passing, _decided(EXPECTATION_VALID, passing))
    later = _subject(source=JAVA_TEST.decode().replace("counts", "tallies"))
    assert ledger.before_asking(later, attended=True).action is Action.ASK


def test_unattended_without_authority_is_needs_review_and_nobody_is_asked():
    ledger, subject = VerifierSemanticLedger(), _subject()
    decision = ledger.before_asking(subject, attended=False)
    assert (decision.action, decision.reason_code, decision.path) == (Action.NEEDS_REVIEW, vs.REASON_UNATTENDED, PATH)
    message = vs.needs_review_message(decision)
    assert message.startswith("VERIFIER_SEMANTIC_AUTHORITY_REQUIRED: VERIFIER_SEMANTIC_UNATTENDED: ") and PATH in message


def test_invalid_spends_the_one_regeneration_of_that_file_and_never_a_second():
    ledger, v0 = VerifierSemanticLedger(), _subject()
    assert ledger.after_decision(v0, _decided(EXPECTATION_INVALID, v0)) == vs.SubjectDecision(Action.REGENERATE_VERIFIER, PATH, rationale=RATIONALE)
    assert not ledger.expectation_valid(v0)                                           # INVALID never establishes validity
    v1 = _subject(source=JAVA_TEST.decode().replace("counts", "tallies"), failure_identity="f9" * 32)
    assert v1.subject_key == v0.subject_key and v1.semantic_authority_fingerprint != v0.semantic_authority_fingerprint
    assert ledger.before_asking(v1, attended=True).action is Action.ASK               # V1 needs fresh semantic authority
    again = ledger.after_decision(v1, _decided(EXPECTATION_INVALID, v1))
    assert (again.action, again.reason_code) == (Action.NEEDS_REVIEW, vs.REASON_REGENERATION_SPENT)     # no V2
    assert ledger.after_decision(v1, _decided(EXPECTATION_VALID, v1)).action is Action.PROCEED          # V1 VALID may proceed


def test_another_verifier_file_of_the_same_unit_and_another_unit_have_their_own_allowance():
    ledger, a = VerifierSemanticLedger(), _subject()
    assert ledger.after_decision(a, _decided(EXPECTATION_INVALID, a)).action is Action.REGENERATE_VERIFIER
    for other in (_subject(path="src/test/java/com/example/GadgetTest.java", source=JAVA_OTHER.decode()), _subject(verifier_unit_id="s6"),
                  _subject(run_id="20261114T060030-00000000")):
        assert other.subject_key != a.subject_key
        assert ledger.after_decision(other, _decided(EXPECTATION_INVALID, other)).action is Action.REGENERATE_VERIFIER
    # a renamed V1 is ANOTHER subject key by construction, so the regeneration's write scope (exactly the original path) is what forbids the rename
    assert _subject(path="src/test/java/com/example/WidgetTest2.java").subject_key != a.subject_key


@pytest.mark.parametrize("decision,reason", [
    (lambda s: _decided(CANNOT_DECIDE, s), vs.REASON_CANNOT_DECIDE),
    (lambda s: VerifierSemanticDecision.none("APPROVAL_REVIEW_EXPIRED"), vs.REASON_NO_DECISION),
    (lambda s: VerifierSemanticDecision.none("APPROVAL_CHANNEL_UNAVAILABLE"), vs.REASON_NO_DECISION),
    (lambda s: None, vs.REASON_NO_DECISION), (lambda s: True, vs.REASON_NO_DECISION), (lambda s: "expectation_valid", vs.REASON_NO_DECISION),
    (lambda s: VerifierRepairDecision(s.fingerprint), vs.REASON_NO_DECISION),            # a replay authorization is never semantic authority
    (lambda s: VerifierSemanticDecision.decided_as(EXPECTATION_VALID, _subject(plan_hash="sha256:" + "2c" * 32).fingerprint), vs.REASON_DECISION_STALE),
    (lambda s: VerifierSemanticDecision.decided_as(EXPECTATION_INVALID, _subject(failure_identity="f2" * 32).fingerprint), vs.REASON_DECISION_STALE),
])
def test_everything_but_a_bound_valid_or_invalid_decision_changes_nothing_and_is_needs_review(decision, reason):
    ledger, subject = VerifierSemanticLedger(), _subject()
    result = ledger.after_decision(subject, decision(subject))
    assert (result.action, result.reason_code) == (Action.NEEDS_REVIEW, reason)
    assert not ledger.expectation_valid(subject) and not ledger.allowance.regeneration_spent(subject)


def test_the_closed_reason_tables():
    assert len(set(vs.REVIEW_REASON_CODES)) == len(vs.REVIEW_REASON_CODES) == 13   # VERIFIER-REGENERATION-BLIND-001: +1 (RATIONALE_REQUIRED); slice 2: -1 (the compile-phase rule), +4 (regeneration, full set)
    assert len(set(vs.OWNER_REASON_CODES)) == len(vs.OWNER_REASON_CODES) == 9   # +1: a locator that matches more than one workspace member
    assert [a.value for a in Action] == ["proceed", "ask", "regenerate_verifier", "needs_review"]


# ------------------------------------------------------------------ settle: one review at most, applied to the subject as it is after the wait

def _callback(answer, asked):
    return TypedApprovalCallback(lambda files, reason: pytest.fail("the change channel is never asked a semantic question"), lambda s: None, None,
                                 lambda s: asked.append(s) or answer(s))


@pytest.mark.asyncio
async def test_settle_asks_once_when_attended_and_never_when_authority_exists_or_the_run_is_unattended():
    ledger, subject, asked = VerifierSemanticLedger(), _subject(), []
    callback = _callback(lambda s: VerifierSemanticDecision.decided_as(EXPECTATION_VALID, s.fingerprint), asked)
    assert (await ledger.settle(lambda: subject, callback, attended=False)).reason_code == vs.REASON_UNATTENDED and asked == []
    assert (await ledger.settle(lambda: subject, callback, attended=True)).action is Action.PROCEED and asked == [subject]
    later = _subject(review_phase=REVIEW_PHASE_PASSING, failure_identity=None)
    assert (await ledger.settle(lambda: later, callback, attended=True)).action is Action.PROCEED and asked == [subject]       # no second card
    assert (await ledger.settle(lambda: later, callback, attended=False)).action is Action.PROCEED


@pytest.mark.asyncio
async def test_settle_applies_the_answer_to_the_subject_as_it_is_after_the_wait():
    ledger, shown = VerifierSemanticLedger(), _subject()
    changed = _subject(source=JAVA_TEST.decode().replace("counts", "tallies"))
    views = iter([shown, changed])                                    # the verifier bytes changed while the person decided
    result = await ledger.settle(lambda: next(views), _callback(lambda s: VerifierSemanticDecision.decided_as(EXPECTATION_VALID, s.fingerprint), []),
                                 attended=True)
    assert (result.action, result.reason_code) == (Action.NEEDS_REVIEW, vs.REASON_DECISION_STALE)
    assert not ledger.expectation_valid(shown) and not ledger.expectation_valid(changed)
    gone = iter([shown, None])
    assert (await ledger.settle(lambda: next(gone), _callback(lambda s: VerifierSemanticDecision.decided_as(EXPECTATION_INVALID, s.fingerprint), []),
                                attended=True)).reason_code == vs.REASON_DECISION_STALE
    vanished = await ledger.settle(lambda: None, _callback(lambda s: pytest.fail("a vanished subject is never put to a person"), []), attended=True)
    assert (vanished.action, vanished.reason_code) == (Action.NEEDS_REVIEW, vs.REASON_DECISION_STALE)


@pytest.mark.asyncio
async def test_settle_without_a_semantic_channel_or_with_an_unshowable_subject_is_no_decision():
    ledger, subject = VerifierSemanticLedger(), _subject()
    plain = lambda files, reason: pytest.fail("never asked")  # noqa: E731 - a one-line callback double
    assert (await ledger.settle(lambda: subject, plain, attended=True)).reason_code == vs.REASON_NO_DECISION
    assert (await ledger.settle(lambda: subject, TypedApprovalCallback(plain, lambda s: None), attended=True)).reason_code == vs.REASON_NO_DECISION
    unshowable = _subject(contract_text="")
    asked = []
    assert (await ledger.settle(lambda: unshowable, _callback(lambda s: None, asked), attended=True)).reason_code == vs.REASON_NO_DECISION
    assert asked == []


# ------------------------------------------------------------------ the production owner after expectation_valid

MAIN = "src/main/java/com/example/Widget.java"
OWNERS = {MAIN: ["s3"], "src/main/java/com/example/Gadget.java": ["s4"], "src/main/java/com/example/Shared.java": ["s3", "s4"], PATH: ["s5"]}


def _owner(**changes):
    arguments = dict(executed_failure=True, located_paths=[MAIN], verifier_paths=[PATH], is_workspace_artifact=lambda p: p in OWNERS or p == "orphan.java",
                     owners_of=lambda p: OWNERS.get(p, []), conflicting_owner_ids=())
    arguments.update(changes)
    return production_owner_authority(**arguments)


def test_typed_failure_plus_a_locator_to_a_production_artifact_with_one_plan_owner_selects_that_owner():
    selected = _owner()
    assert selected.selected and (selected.reason_code, selected.owner_id, selected.files) == (vs.OWNER_SELECTED, "s3", (MAIN,))
    assert _owner(located_paths=[PATH, MAIN, "./" + MAIN]).owner_id == "s3"          # the verifier's own frame beside it does not change the owner
    assert _owner(conflicting_owner_ids=["s3"]).selected                              # other deterministic evidence that agrees is no conflict


@pytest.mark.parametrize("changes,reason", [
    ({"executed_failure": False}, vs.OWNER_NO_EXECUTED_FAILURE),
    ({"located_paths": []}, vs.OWNER_LOCATOR_ABSENT), ({"located_paths": ["", None]}, vs.OWNER_LOCATOR_ABSENT),
    ({"located_paths": [PATH]}, vs.OWNER_LOCATOR_VERIFIER_ONLY),
    ({"located_paths": ["org/junit/jupiter/api/AssertEquals.java"]}, vs.OWNER_LOCATOR_EXTERNAL),
    ({"located_paths": ["orphan.java"]}, vs.OWNER_UNOWNED),
    ({"located_paths": ["src/main/java/com/example/Shared.java"]}, vs.OWNER_AMBIGUOUS),                       # one artifact, two owners
    ({"located_paths": [MAIN, "src/main/java/com/example/Gadget.java"]}, vs.OWNER_AMBIGUOUS),                 # two artifacts, two owners
    ({"conflicting_owner_ids": ["s4"]}, vs.OWNER_CONFLICTING),
])
def test_a_locator_alone_or_any_weaker_evidence_selects_nobody(changes, reason):
    authority = _owner(**changes)
    assert (authority.reason_code, authority.owner_id, authority.selected) == (reason, None, False)


def test_a_locator_with_no_plan_ownership_never_selects_whatever_the_file_is_called():
    """The locator is grounding only: with no plan owner behind it nothing is selected, and a production-looking name changes nothing."""
    assert not _owner(owners_of=lambda p: []).selected
    assert not _owner(located_paths=["src/main/java/com/example/Service.java"], is_workspace_artifact=lambda p: True, owners_of=lambda p: []).selected


# ------------------------------------------------------------------ the sealed Stage C run 20261119T200403-218cbbf6 (VERIFIER-SEMANTIC-FAILING-EXPECTATION-001)

RUN5 = pathlib.Path(__file__).parent / "fixtures" / "stage_c_run5"
RUN5_TEST = "src/test/java/com/example/ProtocolParserTest.java"
RUN5_PARSER = "src/main/java/com/example/ProtocolParser.java"
RUN5_BYTES = (RUN5 / "ProtocolParserTest.java").read_bytes()
RUN5_GOAL = "an empty non-null body is valid; behavior for a null body is not part of this contract."


def _run5_report(name):
    return json.loads((RUN5 / name).read_text())


def _run5_unit(answer, asked):
    return vs.UnitSemantics(ledger=VerifierSemanticLedger(), run_id="r", plan_hash="p", unit_id="s5", goal=RUN5_GOAL, regenerable=True, attended=True,
                            callback=_callback(answer, asked), read_bytes=lambda path: RUN5_BYTES if path == RUN5_TEST else None)


async def _run5_failure(unit, report_name):
    unit.results = []
    unit.observe({"type": "tests", "success": False, "test_execution": _run5_report(report_name)})
    return await unit.on_gate_failure(
        written=[RUN5_TEST], failure_type="targeted_test", outcome="[ERROR] Tests run: 3, Failures: 1",
        locate=lambda: ([RUN5_PARSER], []), is_workspace_artifact=lambda path: True, owners_of=lambda path: ["s3"] if path == RUN5_PARSER else ["s5"])


def test_the_sealed_run5_report_names_the_one_failing_test_with_the_gates_own_detail():
    derivation = derive_executed_verifiers(_run5_report("s5_tests_before_owner_repair.test_execution.json"), {RUN5_TEST: RUN5_BYTES})
    (verifier,) = derivation.verifiers
    assert verifier.failed_identities == ("com.example.ProtocolParserTest.testNullBody",)
    assert verifier.failure_details == (("com.example.ProtocolParserTest.testNullBody", "org.opentest4j.AssertionFailedError: expected: <null> but was: <[]>"),)
    assert sorted(verifier.identities) == ["com.example.ProtocolParserTest.testEmptyBody", "com.example.ProtocolParserTest.testNullBody",
                                           "com.example.ProtocolParserTest.testRoundTrip"]


@pytest.mark.asyncio
async def test_run5_the_review_is_about_testNullBody_and_a_valid_answer_still_selects_the_one_production_owner():
    asked = []
    unit = _run5_unit(lambda s: VerifierSemanticDecision.decided_as(EXPECTATION_VALID, s.fingerprint), asked)
    ruling = await _run5_failure(unit, "s5_tests_before_owner_repair.test_execution.json")
    (subject,) = asked
    assert subject.failing_expectations == (("com.example.ProtocolParserTest.testNullBody", "org.opentest4j.AssertionFailedError: expected: <null> but was: <[]>"),)
    assert subject.failure_identity == failing_expectations_identity(["com.example.ProtocolParserTest.testNullBody"])
    assert verifier_semantic_reviewable(subject)
    # a genuinely valid failing expectation keeps its repair path: the one owner the locator and the plan name
    assert ruling.owner is not None and ruling.owner.selected and (ruling.owner.owner_id, ruling.owner.files) == ("s3", (RUN5_PARSER,))


@pytest.mark.asyncio
async def test_run5_a_valid_answer_about_testNullBody_does_not_cover_testEmptyBody_failing_later_on_the_same_bytes():
    """The measured chain: the same verifier bytes, first failing testNullBody, then (after the owner repair) testEmptyBody. The first decision
    was shown testNullBody only; the second failing set is put to the person again, and its answer decides - nothing is carried silently."""
    answers, asked = [EXPECTATION_VALID, EXPECTATION_INVALID], []

    def answer(subject):
        word = answers.pop(0)
        return VerifierSemanticDecision.decided_as(word, subject.fingerprint, "testNullBody is outside the contract" if word == EXPECTATION_INVALID else None)

    unit = _run5_unit(answer, asked)
    first = await _run5_failure(unit, "s5_tests_before_owner_repair.test_execution.json")
    second = await _run5_failure(unit, "s5_replay_after_owner_repair.test_execution.json")
    assert [[identity for identity, _detail in subject.failing_expectations] for subject in asked] == [
        ["com.example.ProtocolParserTest.testNullBody"], ["com.example.ProtocolParserTest.testEmptyBody"]]
    assert asked[0].semantic_authority_fingerprint == asked[1].semantic_authority_fingerprint and asked[0].fingerprint != asked[1].fingerprint
    assert first.owner is not None and second.owner is None and second.regenerate == RUN5_TEST      # no production owner from the unseen failure
    # negative control: the SAME failing set again is not asked again
    counted = []
    unit_same = _run5_unit(lambda s: VerifierSemanticDecision.decided_as(EXPECTATION_VALID, s.fingerprint), counted)
    await _run5_failure(unit_same, "s5_tests_before_owner_repair.test_execution.json")
    again = await _run5_failure(unit_same, "s5_tests_before_owner_repair.test_execution.json")
    assert len(counted) == 1 and again.owner is not None and again.owner.selected


def test_run5_the_per_test_outcomes_are_exported_for_exactly_the_executed_bytes_and_nothing_when_provenance_is_unknown():
    unit = _run5_unit(lambda s: pytest.fail("not asked"), [])
    unit.observe({"type": "tests", "success": False, "test_execution": _run5_report("s5_tests_before_owner_repair.test_execution.json")})
    assert unit.executed_outcomes([RUN5_TEST]) == [{
        "path": RUN5_TEST, "sha256": _hex(RUN5_BYTES),
        "identities": ["com.example.ProtocolParserTest.testEmptyBody", "com.example.ProtocolParserTest.testNullBody", "com.example.ProtocolParserTest.testRoundTrip"],
        "failed_identities": ["com.example.ProtocolParserTest.testNullBody"]}]
    unreadable = vs.UnitSemantics(ledger=VerifierSemanticLedger(), run_id="r", plan_hash="p", unit_id="s5", goal=RUN5_GOAL, read_bytes=lambda path: None)
    unreadable.observe({"type": "tests", "success": False, "test_execution": _run5_report("s5_tests_before_owner_repair.test_execution.json")})
    assert unreadable.executed_outcomes([RUN5_TEST]) == []            # what the file declares is unknown: no outcome is reported for it
    assert unit.executed_outcomes([]) == []                           # nothing this run wrote executed


def test_a_shown_failing_expectation_must_be_the_one_the_decision_is_bound_to():
    assert verifier_semantic_reviewable(_failing("t.a", "t.b"))
    assert not verifier_semantic_reviewable(_failing("t.a", failure_identity="f1" * 32))                      # shown and bound differ
    assert not verifier_semantic_reviewable(_subject(review_phase=REVIEW_PHASE_PASSING, failure_identity=None,
                                                     failing_expectations=(("t.a", ""),)))                    # a passing verifier fails nothing
    assert not verifier_semantic_reviewable(dataclasses.replace(_failing("t.a"), failing_expectations=(("t.a", "line one\nline two"),)))   # not the gate's bounded one-line detail
    assert verifier_semantic_reviewable(_subject())                    # no identity available: still reviewable, and disclosed by the prompt
