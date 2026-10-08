"""BACKEND-READINESS-004 (owner decision D1): multi-claim, multi-authority coverage -
AUTHORITY-REGRESSION-CLAIM-COVERAGE-001, measured twice in batch 003 (T4-v2 FALSE_NEGATIVE: a named-test statement
whose REGRESSION_PRESERVATION claim was bound only to the repository oracle, which refuses by design when the fix
changes the test dependency declaration; T3 SAFE_FAILURE: a compound API + behaviour statement that one coverage
entry per requirement could never cover).

Claim identity is the existing one: (requirement id, claim kind) from the closed CLAIM_KINDS vocabulary - the ledger
record id and the contract's ``required_claims``. A bundle declares one coverage entry per (requirement, claim); the
compiler binds every applicable producer to a claim (the repository closer beside the sealed oracle); judgments are
kept per producer: any VIOLATED is counter-evidence, any SATISFIED closes, a producer's INDETERMINATE withdraws only
its own earlier judgment (or the producers it explicitly names). The required cases of the owner instruction (section
5) are each a test below; the mutation campaign in ~/kriya-m1-live/backend-readiness-004/mutations targets them.
"""
import hashlib
import json

import pytest

from kriya.workflow import api_preservation as api
from kriya.workflow import authority_bundle as ab
from kriya.workflow.contract_baseline import BASELINE_FAIL, run_baseline_authorities
from kriya.workflow.contract_compilation import (
    CLOSER_ACCEPTANCE,
    CLOSER_API_PRESERVATION,
    CLOSER_EXTERNAL_ACCEPTANCE,
    CLOSER_NAMED_TESTS,
    STATUS_AUTHORITY_REQUIRED,
    STATUS_CLOSABLE,
    ExternalAuthority,
    compile_verification_contract,
)
from kriya.workflow.obligations import ObligationStatus
from kriya.workflow.requirements import (
    API_PRESERVATION,
    BEHAVIOR,
    CLAIM_REVOKES,
    EXTERNAL_ACCEPTANCE_METHOD,
    REGRESSION_PRESERVATION,
    RequirementOutcome,
    derive_requirements,
    record_requirement_claim,
    requirement_claim_record,
    requirement_claim_records,
    requirement_outcomes,
    statement_origins,
)
from tests.test_verification_contract_003_authority import _bundle_dir, _ledger, _load, _workspace

COMPOUND = "Do not change the public API; keep the behaviour of all other cache classes unchanged.\n"
NAMED = "Fix expire() so that entries leave at the boundary; tests/test_a.py must still pass.\n"
TESTS = ["tests/test_a.py"]


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _bundle(tmp_path, goal, covers, language="python"):
    """A loaded bundle whose coverage entries are (rid, claim, strength, why)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    ws, base = _workspace(tmp_path)
    reqs = derive_requirements(goal)
    bundle_dir = _bundle_dir(tmp_path, reqs, base, goal=goal)
    manifest = json.loads((bundle_dir / "manifest.json").read_text())
    manifest["toolchain"]["language"] = language
    manifest["covers"] = [{"requirement_id": rid, "requirement_text_sha256": _sha(reqs.get(rid).text), "claim": claim,
                           "accepted_strength": strength, "accept_as_sufficient": True, "why": why}
                          for rid, claim, strength, why in covers]
    (bundle_dir / "manifest.json").write_text(json.dumps(manifest))
    return ws, reqs, _load(tmp_path, bundle_dir, reqs, base, ws, language=language, goal=goal), bundle_dir


def _compile(reqs, goal, bundle, language="python", **kw):
    return compile_verification_contract(reqs, origins=statement_origins(goal), test_files=TESTS,
                                         external_authorities=[bundle.authority()], project_language=language, **kw)


def _run(verdict):
    return lambda: ab.AuthorityRun(verdict=verdict, reason_code="x", reason="scripted")


# ---------------------------------------------------------------- one requirement / two claims / same authority (T3)
@pytest.mark.parametrize("verdict, outcome", [(ab.VERDICT_PASS, RequirementOutcome.HUMAN_ACCEPTED),
                                              (ab.VERDICT_FAIL, RequirementOutcome.VIOLATED),
                                              (ab.VERDICT_INDETERMINATE, RequirementOutcome.UNVERIFIED)])
def test_01_two_claims_of_one_statement_covered_by_one_oracle_close_together(tmp_path, verdict, outcome):
    """T3 shape: a Java compound API + behaviour constraint, one bundle entry per claim; the Java predicate
    (BACKEND-READINESS-004) binds beside the bundle on the API claim."""
    ws, reqs, bundle, _dir = _bundle(tmp_path, COMPOUND, [
        ("REQ-1", API_PRESERVATION, None, "compat_check.sh diffs the public/protected signatures"),
        ("REQ-1", BEHAVIOR, "GENERAL", "the full offline suite covers every other class")], language="java")
    assert {c.claim for c in bundle.covers["REQ-1"]} == {API_PRESERVATION, BEHAVIOR}
    contract = _compile(reqs, COMPOUND, bundle, language="java")
    entry = contract.entry("REQ-1")
    assert set(entry.required_claims) == {API_PRESERVATION, BEHAVIOR}
    assert entry.status == STATUS_CLOSABLE and contract.refusal() is None
    assert entry.closers == [CLOSER_API_PRESERVATION, CLOSER_EXTERNAL_ACCEPTANCE]
    assert sorted(b.claim for b in entry.bindings) == [API_PRESERVATION, API_PRESERVATION, BEHAVIOR]
    ledger = _ledger(reqs)
    entries = ab.close_requirements_with_authority_bundle(ledger, reqs, bundle, contract, execute=_run(verdict),
                                                          source="t", revision=1)
    assert sorted((e["requirement"], e["claim"]) for e in entries) == [("REQ-1", API_PRESERVATION), ("REQ-1", BEHAVIOR)]
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is outcome
    assert all(e["closed"] is (outcome is RequirementOutcome.HUMAN_ACCEPTED) for e in entries)


# ---------------------------------------------------------------- one requirement / two claims / two authorities (T4)
def test_02_named_test_statement_closes_through_the_oracle_when_the_repository_oracle_refuses(tmp_path):
    """T4 shape: BEHAVIOR by the bundle, REGRESSION_PRESERVATION by the bundle beside the named-test closer; the
    named-test closer refusing (ORACLE_DEPENDENCY_CHANGED records nothing) leaves the bundle's judgment standing."""
    ws, reqs, bundle, _dir = _bundle(tmp_path, NAMED, [
        ("REQ-1", BEHAVIOR, "GENERAL", "twenty hidden tests in a fresh venv"),
        ("REQ-1", REGRESSION_PRESERVATION, None, "the oracle runs tests/test_a.py in the fresh venv")])
    contract = _compile(reqs, NAMED, bundle)
    entry = contract.entry("REQ-1")
    assert set(entry.required_claims) == {BEHAVIOR, REGRESSION_PRESERVATION} and entry.status == STATUS_CLOSABLE
    assert sorted((b.claim, b.closer) for b in entry.bindings) == [
        (BEHAVIOR, CLOSER_EXTERNAL_ACCEPTANCE), (REGRESSION_PRESERVATION, CLOSER_EXTERNAL_ACCEPTANCE),
        (REGRESSION_PRESERVATION, CLOSER_NAMED_TESTS)]
    ledger = _ledger(reqs)
    ab.close_requirements_with_authority_bundle(ledger, reqs, bundle, contract, execute=_run(ab.VERDICT_PASS),
                                                source="t", revision=1)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.HUMAN_ACCEPTED
    regression = requirement_claim_record(ledger, "REQ-1", REGRESSION_PRESERVATION, "cand")
    assert regression.status is ObligationStatus.SATISFIED and regression.evidence["method"] == EXTERNAL_ACCEPTANCE_METHOD


def test_03_an_uncovered_mandatory_claim_leaves_the_requirement_open(tmp_path):
    """The same statement with BEHAVIOR covered only: regression preservation stays with the repository oracle."""
    ws, reqs, bundle, _dir = _bundle(tmp_path, NAMED, [("REQ-1", BEHAVIOR, "GENERAL", "hidden tests")])
    contract = _compile(reqs, NAMED, bundle)
    entry = contract.entry("REQ-1")
    assert entry.status == STATUS_CLOSABLE  # the named-test closer binds the regression claim
    assert sorted((b.claim, b.closer) for b in entry.bindings) == [(BEHAVIOR, CLOSER_EXTERNAL_ACCEPTANCE),
                                                                   (REGRESSION_PRESERVATION, CLOSER_NAMED_TESTS)]
    ledger = _ledger(reqs)
    [only] = ab.close_requirements_with_authority_bundle(ledger, reqs, bundle, contract, execute=_run(ab.VERDICT_PASS),
                                                         source="t", revision=1)
    assert only["claim"] == BEHAVIOR and only["closed"] is False
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED
    assert requirement_claim_record(ledger, "REQ-1", REGRESSION_PRESERVATION, "cand") is None
    # a Java compound statement with only its behaviour clause covered: the API claim keeps the Java predicate
    # (BACKEND-READINESS-004), the bundle never covers it
    ws2, reqs2, api_less, _d = _bundle(tmp_path / "j", COMPOUND, [("REQ-1", BEHAVIOR, "GENERAL", "suite")], language="java")
    contract2 = _compile(reqs2, COMPOUND, api_less, language="java")
    assert contract2.entry("REQ-1").status == STATUS_CLOSABLE
    assert sorted((b.claim, b.closer) for b in contract2.entry("REQ-1").bindings) == [
        (API_PRESERVATION, CLOSER_API_PRESERVATION), (BEHAVIOR, CLOSER_EXTERNAL_ACCEPTANCE)]


# ---------------------------------------------------------------- one claim / several complementary authorities
def test_04_every_applicable_authority_binds_to_one_claim(tmp_path):
    exact = "lower('ABC') -> 'abc'\n"
    ws, reqs, bundle, _dir = _bundle(tmp_path, exact, [("REQ-1", BEHAVIOR, "EXACT", "the hidden test asserts it")])
    contract = _compile(reqs, exact, bundle, acceptance_ids=["REQ-1"])
    entry = contract.entry("REQ-1")
    assert [(b.claim, b.closer) for b in entry.bindings] == [(BEHAVIOR, CLOSER_ACCEPTANCE), (BEHAVIOR, CLOSER_EXTERNAL_ACCEPTANCE)]
    assert entry.closers == [CLOSER_ACCEPTANCE, CLOSER_EXTERNAL_ACCEPTANCE] and entry.status == STATUS_CLOSABLE
    # a Python API constraint: the predicate AND a covering oracle both bind
    ws2, reqs2, api_bundle, _d = _bundle(tmp_path / "p", COMPOUND, [("REQ-1", API_PRESERVATION, None, "signature dump")])
    bindings = _compile(reqs2, COMPOUND, api_bundle).entry("REQ-1").bindings
    assert [(b.claim, b.closer) for b in bindings if b.claim == API_PRESERVATION] == [
        (API_PRESERVATION, CLOSER_API_PRESERVATION), (API_PRESERVATION, CLOSER_EXTERNAL_ACCEPTANCE)]


# ---------------------------------------------------------------- per-producer judgments
def test_05_one_producers_indeterminate_never_withdraws_another_producers_judgment(tmp_path):
    ws, reqs, bundle, _dir = _bundle(tmp_path / "p", COMPOUND, [("REQ-1", API_PRESERVATION, None, "signature dump"),
                                                              ("REQ-1", BEHAVIOR, "GENERAL", "suite")])
    contract = _compile(reqs, COMPOUND, bundle)
    ledger = _ledger(reqs)
    ab.close_requirements_with_authority_bundle(ledger, reqs, bundle, contract, execute=_run(ab.VERDICT_PASS),
                                                source="t", revision=1)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.HUMAN_ACCEPTED
    # the Python predicate cannot read the base: INDETERMINATE for ITS judgment only
    api.close_api_preservation_requirements(ledger, reqs, contract_claims=contract.required_claims_by_requirement(),
                                            comparison=api.ApiComparison(False, "base unreadable"), source="t", revision=1)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.HUMAN_ACCEPTED
    methods = {r.evidence["method"]: r.status for r in requirement_claim_records(ledger, "REQ-1", API_PRESERVATION, "cand")}
    assert methods == {EXTERNAL_ACCEPTANCE_METHOD: ObligationStatus.SATISFIED,
                       api.API_PRESERVATION_METHOD: ObligationStatus.INDETERMINATE}
    # the same producer judging again withdraws its own earlier judgment
    record_requirement_claim(ledger, reqs, "REQ-1", API_PRESERVATION, evidence_id="cand", method=EXTERNAL_ACCEPTANCE_METHOD,
                             detail={"reason_code": "AUTHORITY_TIMEOUT"}, source="t", revision=2,
                             status=ObligationStatus.INDETERMINATE)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED
    # an INDETERMINATE naming the producers it supersedes withdraws them (the acceptance family)
    record_requirement_claim(ledger, reqs, "REQ-1", BEHAVIOR, evidence_id="cand", method="acceptance_oracle",
                             detail={"reason_code": "ACCEPTANCE_SUPERSEDED", CLAIM_REVOKES: [EXTERNAL_ACCEPTANCE_METHOD]},
                             source="t", revision=3, status=ObligationStatus.INDETERMINATE)
    assert requirement_claim_record(ledger, "REQ-1", BEHAVIOR, "cand").status is ObligationStatus.INDETERMINATE


def test_06_counter_evidence_from_any_producer_stands(tmp_path):
    ws, reqs, bundle, _dir = _bundle(tmp_path, NAMED, [("REQ-1", BEHAVIOR, "GENERAL", "hidden"),
                                                      ("REQ-1", REGRESSION_PRESERVATION, None, "fresh venv")])
    contract = _compile(reqs, NAMED, bundle)
    ledger = _ledger(reqs)
    ab.close_requirements_with_authority_bundle(ledger, reqs, bundle, contract, execute=_run(ab.VERDICT_FAIL),
                                                source="t", revision=1)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.VIOLATED
    record_requirement_claim(ledger, reqs, "REQ-1", REGRESSION_PRESERVATION, evidence_id="cand", method="named_test_oracle",
                             detail={"tests": TESTS, "required_claims": [BEHAVIOR, REGRESSION_PRESERVATION]}, source="t",
                             revision=2, status=ObligationStatus.SATISFIED)
    assert requirement_claim_record(ledger, "REQ-1", REGRESSION_PRESERVATION, "cand").status is ObligationStatus.VIOLATED
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.VIOLATED


# ---------------------------------------------------------------- manifest rules
def test_07_duplicate_or_stale_or_foreign_coverage_is_refused_and_distinct_claims_accepted(tmp_path):
    with pytest.raises(ab.AuthorityBundleError, match="duplicate coverage for REQ-1 claim BEHAVIOR"):
        _bundle(tmp_path / "dup", COMPOUND, [("REQ-1", BEHAVIOR, "GENERAL", "a"), ("REQ-1", BEHAVIOR, "GENERAL", "b")])
    ws, reqs, bundle, bundle_dir = _bundle(tmp_path / "ok", COMPOUND, [("REQ-1", BEHAVIOR, "GENERAL", "a"),
                                                                       ("REQ-1", API_PRESERVATION, None, "b")])
    assert [c.claim for c in bundle.covers["REQ-1"]] == [BEHAVIOR, API_PRESERVATION]
    manifest = json.loads((bundle_dir / "manifest.json").read_text())
    manifest["covers"][1]["requirement_text_sha256"] = _sha("other words")  # stale second entry
    (bundle_dir / "manifest.json").write_text(json.dumps(manifest))
    base = bundle.base_revision
    with pytest.raises(ab.AuthorityBundleError, match="does not match the requirement's exact text"):
        _load(tmp_path / "ok", bundle_dir, reqs, base, ws, goal=COMPOUND)
    manifest["covers"][1] = {**manifest["covers"][0], "claim": "MIGRATION"}
    (bundle_dir / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ab.AuthorityBundleError, match="claim must be one of"):
        _load(tmp_path / "ok", bundle_dir, reqs, base, ws, goal=COMPOUND)


def test_08_coverage_of_a_claim_the_statement_does_not_make_binds_nothing(tmp_path):
    """REGRESSION_PRESERVATION declared for a statement naming no test: no binding, no record, no closure."""
    ws, reqs, bundle, _dir = _bundle(tmp_path, COMPOUND, [("REQ-1", REGRESSION_PRESERVATION, None, "suite")])
    contract = _compile(reqs, COMPOUND, bundle)
    assert CLOSER_EXTERNAL_ACCEPTANCE not in contract.entry("REQ-1").closers
    assert contract.entry("REQ-1").status == STATUS_AUTHORITY_REQUIRED
    assert ab.close_requirements_with_authority_bundle(_ledger(reqs), reqs, bundle, contract, execute=_run(ab.VERDICT_PASS),
                                                       source="t", revision=1) == []


def test_09_coverage_added_after_sealing_changes_the_contract_and_the_compiler_is_pure(tmp_path):
    ws, reqs, one, _d = _bundle(tmp_path / "one", COMPOUND, [("REQ-1", BEHAVIOR, "GENERAL", "suite")])
    ws2, reqs2, two, _d2 = _bundle(tmp_path / "two", COMPOUND, [("REQ-1", BEHAVIOR, "GENERAL", "suite"),
                                                               ("REQ-1", API_PRESERVATION, None, "dump")])
    first, again = _compile(reqs, COMPOUND, one), _compile(reqs, COMPOUND, one)
    assert first.digest == again.digest and first.to_dict() == again.to_dict()  # retry/fallback: same bindings
    assert first.digest != _compile(reqs2, COMPOUND, two).digest  # a bundle with more coverage is another contract
    assert first.identity_payload()["compiler_version"] == 2


# ---------------------------------------------------------------- baseline with several bindings on one claim
def test_10_baseline_fail_from_any_bound_authority_is_the_determinate_signal():
    exact = "lower('ABC') -> 'abc'\n"
    reqs = derive_requirements(exact)
    bundle = ExternalAuthority("external_acceptance_command", "b" * 64, {"REQ-1": {BEHAVIOR: {"accepted_strength": "EXACT"}}})
    examples = ExternalAuthority("goal_examples", "e" * 64, {"REQ-1": {BEHAVIOR: {"accepted_strength": "EXACT"}}})
    contract = compile_verification_contract(reqs, origins=statement_origins(exact), test_files=[],
                                             external_authorities=[bundle, examples], project_language="python")
    assert [b.closer for b in contract.entry("REQ-1").bindings] == ["derived_examples", CLOSER_EXTERNAL_ACCEPTANCE]

    class _Judgment:
        passed, violated = True, False

    report = run_baseline_authorities(
        contract, reqs, base_revision="base", run_bundle=lambda: ab.AuthorityRun(verdict=ab.VERDICT_FAIL),
        bundle_digest="b" * 64, judge_examples=lambda: {"REQ-1": _Judgment()}, examples_digest="e" * 64)
    claim = report.claims["REQ-1"][BEHAVIOR]
    assert claim["state"] == BASELINE_FAIL and report.discriminating and not report.no_mutation_required
    assert {a["kind"]: a["verdict"] for a in claim["authorities"]} == {"goal_examples": "PASS", "external_acceptance_command": "FAIL"}
    # m37a: one authority passing while the other could not judge is not a baseline PASS
    partial = run_baseline_authorities(
        contract, reqs, base_revision="base", run_bundle=lambda: ab.AuthorityRun(verdict=ab.VERDICT_INDETERMINATE),
        bundle_digest="b" * 64, judge_examples=lambda: {"REQ-1": _Judgment()}, examples_digest="e" * 64)
    assert partial.claims["REQ-1"][BEHAVIOR]["state"] == "INDETERMINATE" and not partial.no_mutation_required
    assert not partial.discriminating and partial.unsatisfied == {"REQ-1": [BEHAVIOR]}
