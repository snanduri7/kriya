"""S8 (C-07, GAP-A1-07): whether a failure may OPEN a failure family is a typed, fail-closed question of its own.

Pre-fix (MEASURED on the pure owners at b6ac020, ``~/kriya-evidence/s8-b6ac020/prefix_family_predicate.txt``): the family question was answered by
``open_findings.replaces_open_finding``, a deny-list - so the orchestrator's own ``no_progress_retry`` and any type nobody had listed counted as "a different
failure" and replenished the scoped (targeted / fallback-targeted) budgets.

Now: ``may_open_failure_family`` is an allow-list of the candidate-failure types the producers emit today; an unlisted type inherits the family the repair was
already addressing. Inside an eligible type the family identity is unchanged: the normalized validator signature (C-07).
"""
import pathlib
import re
from types import SimpleNamespace

import pytest

import kriya
from kriya.workflow import open_findings, retry_policy
from kriya.workflow.failure_reporting import _FAILURE_TYPE_TO_CATEGORY
from kriya.workflow.state import RetryBudgets


def _failure(type_: str, authority: str = "authoritative"):
    return SimpleNamespace(type=type_, authority=authority)


def _record(budgets: RetryBudgets, failure, signature) -> bool:
    """retry_strategy._record_attempt_failure's family step, on the pure owners: (whether the scoped budgets were replenished)."""
    previous = budgets.last_failure_signature
    current = previous if previous is not None and not open_findings.may_open_failure_family(failure) else signature
    new_family = retry_policy.observe_failure_family(budgets, previous, current)
    if new_family:
        retry_policy.reset_scoped_budgets_for_new_family(budgets)
    budgets.last_failure_signature = current
    return new_family


def _charged_on_an_assertion() -> RetryBudgets:
    budgets = RetryBudgets()
    assert _record(budgets, _failure("test"), ("test", "assert add(1, 2) == 3")) is False      # the first failure opens nothing to replenish
    budgets.targeted_retry_count, budgets.fallback_targeted_attempted = 3, True
    return budgets


def test_an_orchestrator_no_progress_retry_does_not_replenish_the_scoped_budgets():
    failure = _failure("no_progress_retry")
    assert open_findings.replaces_open_finding(failure) is True, "the memory predicate is unchanged: the finding is still replaced"
    assert open_findings.may_open_failure_family(failure) is False
    budgets = _charged_on_an_assertion()
    assert _record(budgets, failure, ("no_progress_retry", "identical candidate")) is False
    assert (budgets.targeted_retry_count, budgets.fallback_targeted_attempted) == (3, True), "pre-fix: (0, False)"
    assert budgets.last_failure_signature == ("test", "assert add(1, 2) == 3"), "it inherited the family the repair was addressing"


@pytest.mark.parametrize("unlisted", ["a_validator_type_added_tomorrow", "verifier_replay_refused", ""])
def test_a_type_nobody_listed_inherits_the_family_and_never_widens_a_budget(unlisted):
    assert unlisted not in open_findings.FAMILY_OPENING_FAILURE_TYPES
    budgets = _charged_on_an_assertion()
    assert _record(budgets, _failure(unlisted), (unlisted, "whatever it says")) is False
    assert budgets.targeted_retry_count == 3


def test_a_genuinely_different_candidate_failure_still_earns_one_fresh_scoped_allowance():
    budgets = _charged_on_an_assertion()
    assert _record(budgets, _failure("compile"), ("compile", "cannot find symbol")) is True
    assert (budgets.targeted_retry_count, budgets.fallback_targeted_attempted) == (0, False)
    budgets.targeted_retry_count = 2
    assert _record(budgets, _failure("test"), ("test", "assert add(1, 2) == 3")) is False, "a return to a family already seen is not new"
    assert budgets.targeted_retry_count == 2


def test_an_eligible_type_still_needs_an_authoritative_statement_about_the_candidate():
    assert open_findings.may_open_failure_family(_failure("test")) is True
    assert open_findings.may_open_failure_family(_failure("test", authority="advisory")) is False


def test_the_two_tables_are_disjoint_and_agree_with_the_deny_lists():
    opening, closed = open_findings.FAMILY_OPENING_FAILURE_TYPES, open_findings.NON_OPENING_FAILURE_TYPES
    assert not opening & closed
    # every family-opening type is one the memory predicate already keeps (a subset, never a new kind of candidate statement)
    assert all(open_findings.replaces_open_finding(_failure(type_)) for type_ in opening)
    # everything the deny-lists exclude is named as non-opening
    assert open_findings.DELIVERY_FAILURE_TYPES <= closed
    assert all(type_ in closed for type_ in _FAILURE_TYPE_TO_CATEGORY if not open_findings.is_candidate_finding(type_))


_TYPE_LITERAL = re.compile(r"\b(?:type|fail_type|failure_type)\s*=\s*\"([a-z_]+)\"")
_TYPE_COMPARISON = re.compile(r"fail_type (?:==|in) \(?((?:\"[a-z_]+\"(?:, )?)+)")
# ``type="..."`` literals in the scanned sources that are not failure types (argparse / schema / event vocabulary).
_NOT_FAILURE_TYPES = frozenset({"tool", "task", "object", "string", "function", "text", "json_object", "json_schema"})


def _produced_failure_types() -> set:
    found = set(_FAILURE_TYPE_TO_CATEGORY)
    root = pathlib.Path(kriya.__file__).parent
    for folder in ("workflow", "tools", "static_analysis", "agents"):
        for path in (root / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "Failure" not in text and "fail_type" not in text:
                continue
            found.update(_TYPE_LITERAL.findall(text))
            for group in _TYPE_COMPARISON.findall(text):
                found.update(re.findall(r"\"([a-z_]+)\"", group))
    return found - _NOT_FAILURE_TYPES


def test_every_failure_type_a_producer_names_is_classified_in_one_table():
    """The tripwire: a type added to a validator, a gate or the orchestrator without deciding whether it may open a family fails here (and, unlisted, could not
    widen a budget meanwhile)."""
    produced = _produced_failure_types()
    assert len(produced) > 50, "the scan found the producers"
    classified = open_findings.FAMILY_OPENING_FAILURE_TYPES | open_findings.NON_OPENING_FAILURE_TYPES
    assert sorted(produced - classified) == [], "classify the new failure type in kriya/workflow/open_findings.py"


def test_the_scan_would_catch_a_new_type():
    assert _TYPE_LITERAL.findall('Failure(type="brand_new_gate_failure", source="x")') == ["brand_new_gate_failure"]
    assert "brand_new_gate_failure" not in open_findings.FAMILY_OPENING_FAILURE_TYPES | open_findings.NON_OPENING_FAILURE_TYPES
