"""BACKEND-READINESS-004 (owner decision D3): the operator's sealed requirement disposition -
kriya/workflow/requirement_disposition.py and its contract, ledger, baseline and resume bindings.

Section 7 of the owner instruction, each a test: a model cannot disposition; a disposition after candidate
generation without a new contract is refused (the contract digest changes, both resume identities fail closed);
another goal, another workspace/base, another requirement text are refused; a disposition altered after sealing is
another contract; a silent requirement deletion is impossible (the statement stays in the set, in the report, in the
ledger with its kind, reason and operator). Positive: the T6 shape (false premise, whole statement -> DISPOSITIONED,
the rest closes at baseline -> NO_MUTATION_REQUIRED), the T2 shape (historical remark) and a claim-level disposition
that leaves the statement's other claims mandatory.
"""
import hashlib
import json

import pytest

from kriya.workflow import requirement_disposition as rd
from kriya.workflow.contract_baseline import run_baseline_authorities
from kriya.workflow.contract_compilation import (
    CLOSER_OPERATOR_DISPOSITION,
    STATUS_AUTHORITY_REQUIRED,
    STATUS_CLOSABLE,
    STATUS_DISPOSITIONED,
    ExternalAuthority,
    compile_verification_contract,
    record_contract_dispositions,
)
from kriya.workflow.obligations import ObligationLedger
from kriya.workflow.requirements import (
    BEHAVIOR,
    DOCUMENTATION_CLAIM,
    RequirementOutcome,
    derive_requirements,
    disposition_record,
    record_requirement_verdicts,
    requirement_evidence,
    requirement_outcomes,
    seed_requirement_obligations,
    statement_origins,
)
from kriya.workflow.resume_fingerprints import generation_resume_fingerprints
from tests._strict_doubles import strict_config
from tests.test_verification_contract_003_authority import _git, _workspace

FALSE_PREMISE = ("CSVFormat.EXCEL produces spurious empty records for blank lines.\n"
                 "Every existing test must keep passing unchanged.\n")
HISTORICAL = ("absUrl('?page=2') returns 'https://example.com/catalog/?page=2' but should be "
              "'https://example.com/catalog/list.html?page=2'.\nBoth cases worked in the version we used before.\n")
STYLE = "Add the new functions in the same style as the existing built-ins and document them in the README's function list if there is one.\n"


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _document(reqs, goal, base, entries, operator=("owner", "2026-10-08T10:00:00Z")):
    return {"format": rd.DISPOSITION_FORMAT, "operator": {"identity": operator[0], "issued_at": operator[1]},
            "dispositions": [{"requirement_id": rid, "requirement_text_sha256": _sha(reqs.get(rid).text),
                              "goal_sha256": reqs.goal_digest, "requirement_set_sha256": reqs.digest,
                              "base_revision": base, "claim": claim, "disposition": kind, "reason": reason,
                              "evidence": ["docs/user-guide.md#excel"]} for rid, claim, kind, reason in entries]}


def _bind(tmp_path, goal, entries, mutate=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    ws, base = _workspace(tmp_path)
    reqs = derive_requirements(goal)
    document = _document(reqs, goal, base, entries)
    if mutate:
        mutate(document)
    path = tmp_path / "disposition.json"
    path.write_text(json.dumps(document))
    loaded = rd.load_requirement_dispositions(str(path), reqs, goal, state_root=str(tmp_path / "state"),
                                              workspace=str(ws), base_revision=base)
    return ws, base, reqs, loaded, path


def _compile(reqs, goal, dispositions=None, **kw):
    return compile_verification_contract(reqs, origins=statement_origins(goal), test_files=["tests/test_a.py"],
                                         project_language="java", dispositions=dispositions, **kw)


def _ledger(reqs, contract, candidate="cand"):
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    record_contract_dispositions(ledger, reqs, contract, source="t")
    record_requirement_verdicts(ledger, reqs, {r.id: (RequirementOutcome.UNVERIFIED, "x") for r in reqs.requirements},
                                revision=1, evidence_fingerprint=candidate, source="test")
    return ledger


# ---------------------------------------------------------------- whole-statement disposition (T6 / T2 shapes)
def test_01_a_false_premise_statement_is_dispositioned_reported_and_never_deleted(tmp_path):
    ws, base, reqs, loaded, path = _bind(tmp_path, FALSE_PREMISE, [
        ("REQ-1", None, rd.REJECTED_FALSE_PREMISE, "the documented, tested behaviour is the opposite")])
    assert loaded.whole("REQ-1").disposition == rd.REJECTED_FALSE_PREMISE and loaded.operator["identity"] == "owner"
    assert loaded.stored_path.startswith(str(tmp_path / "state")) and loaded.digest == _sha(path.read_text())
    without = _compile(reqs, FALSE_PREMISE)
    assert without.entry("REQ-1").status == STATUS_AUTHORITY_REQUIRED and without.refusal() is not None
    contract = _compile(reqs, FALSE_PREMISE, loaded)
    entry = contract.entry("REQ-1")
    assert entry.status == STATUS_DISPOSITIONED and entry.closers == [CLOSER_OPERATOR_DISPOSITION]
    assert entry.disposition["disposition"] == rd.REJECTED_FALSE_PREMISE and entry.disposition["digest"] == loaded.digest
    assert entry.text == reqs.get("REQ-1").text  # the statement is still there, with its words
    assert contract.refusal() is None and contract.entry("REQ-2").status == STATUS_CLOSABLE
    report = contract.report()
    assert report["totals"]["dispositioned"] == 1 and report["admission"] == "ADMITTED"
    assert report["requirements"][0]["disposition"]["reason"] == "the documented, tested behaviour is the opposite"
    assert report["dispositions"]["digest"] == loaded.digest and reqs.digest == derive_requirements(FALSE_PREMISE).digest
    # ledger: an audit record, outcome DISPOSITIONED, never satisfied/verified; the other statement is untouched
    ledger = _ledger(reqs, contract)
    outcomes = requirement_outcomes(ledger, reqs)
    assert outcomes["REQ-1"] is RequirementOutcome.DISPOSITIONED and outcomes["REQ-2"] is RequirementOutcome.UNVERIFIED
    evidence = requirement_evidence(ledger, reqs)["REQ-1"]
    assert evidence["reason_code"] == "OPERATOR_DISPOSITIONED" and evidence["operator"]["identity"] == "owner"
    assert evidence["disposition_digest"] == loaded.digest and evidence["evidence"] == ["docs/user-guide.md#excel"]
    assert record_contract_dispositions(ledger, reqs, contract, source="t") == []  # idempotent


def test_02_the_t6_shape_reaches_no_mutation_required_once_the_rest_passes_at_baseline(tmp_path):
    ws, base, reqs, loaded, _p = _bind(tmp_path, FALSE_PREMISE, [
        ("REQ-1", None, rd.REJECTED_FALSE_PREMISE, "documented behaviour")])
    contract = _compile(reqs, FALSE_PREMISE, loaded)
    report = run_baseline_authorities(contract, reqs, base_revision=base, judge_suite=lambda: "PASS")
    assert report.no_mutation_required and report.outcomes() == {"REQ-1": "dispositioned", "REQ-2": "closed_by_evidence"}
    assert report.claims["REQ-1"] == {"STATEMENT": {"state": "DISPOSITIONED", "authority": "operator_disposition",
                                                     "why": "documented behaviour"}}
    # without the disposition the same baseline never reaches it (REQ-1 is refused before baseline anyway)
    bare = run_baseline_authorities(_compile(reqs, FALSE_PREMISE), reqs, base_revision=base, judge_suite=lambda: "PASS")
    assert not bare.no_mutation_required


def test_03_a_historical_remark_is_dispositioned_and_the_defect_statements_stay_mandatory(tmp_path):
    ws, base, reqs, loaded, _p = _bind(tmp_path, HISTORICAL, [("REQ-2", None, rd.HISTORICAL_CONTEXT, "a remark about an older version")])
    contract = _compile(reqs, HISTORICAL, loaded)
    assert contract.entry("REQ-2").status == STATUS_DISPOSITIONED
    assert contract.entry("REQ-1").status == STATUS_AUTHORITY_REQUIRED and contract.refusal() is not None
    assert [row["id"] for row in contract.refusal().residual] == ["REQ-1"]


# ---------------------------------------------------------------- claim-level disposition
def test_04_a_claim_level_disposition_keeps_the_other_claims_of_the_statement_mandatory(tmp_path):
    ws, base, reqs, loaded, _p = _bind(tmp_path, STYLE, [("REQ-1", BEHAVIOR, rd.INFORMATIONAL_CONTEXT, "'same style' is a preference no oracle judges")])
    bare = _compile(reqs, STYLE)
    assert set(bare.entry("REQ-1").required_claims) == {DOCUMENTATION_CLAIM, BEHAVIOR}
    contract = _compile(reqs, STYLE, loaded)
    entry = contract.entry("REQ-1")
    # no README in this repository: the conditional documentation clause closes vacuously, the style clause is gone
    assert entry.status == STATUS_CLOSABLE and entry.required_claims == (DOCUMENTATION_CLAIM,)
    assert entry.dispositioned_claims == (BEHAVIOR,) and entry.disposition is None and entry.residual == ()
    assert "documentation_not_applicable" in entry.closers
    assert [(b.claim, b.closer) for b in entry.bindings if b.closer == CLOSER_OPERATOR_DISPOSITION] == [(BEHAVIOR, CLOSER_OPERATOR_DISPOSITION)]
    ledger = _ledger(reqs, contract)
    assert disposition_record(ledger, "REQ-1") is None  # not a whole-statement disposition
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED
    # every claim dispositioned -> the statement is DISPOSITIONED as a whole
    ws2, base2, reqs2, both, _q = _bind(tmp_path / "all", STYLE, [
        ("REQ-1", BEHAVIOR, rd.INFORMATIONAL_CONTEXT, "style"), ("REQ-1", DOCUMENTATION_CLAIM, rd.OUT_OF_SCOPE, "no README")])
    whole = _compile(reqs2, STYLE, both).entry("REQ-1")
    assert whole.status == STATUS_DISPOSITIONED and whole.disposition["disposition"] == "MULTIPLE"
    assert whole.disposition["reason"] == "DOCUMENTATION: no README; BEHAVIOR: style"  # the statement's claim order


# ---------------------------------------------------------------- refusals (section 7)
@pytest.mark.parametrize("mutate, code", [
    (lambda d: d["dispositions"][0].update(goal_sha256="0" * 64), rd.DISPOSITION_GOAL_MISMATCH),
    (lambda d: d["dispositions"][0].update(requirement_set_sha256="0" * 64), rd.DISPOSITION_REQUIREMENT_SET_MISMATCH),
    (lambda d: d["dispositions"][0].update(base_revision="0" * 40), rd.DISPOSITION_BASE_MISMATCH),
    (lambda d: d["dispositions"][0].update(requirement_text_sha256="0" * 64), rd.DISPOSITION_TEXT_MISMATCH),
    (lambda d: d["dispositions"][0].update(requirement_id="REQ-9"), rd.DISPOSITION_INVALID),
    (lambda d: d["dispositions"][0].update(requirement_id="REQ-*"), rd.DISPOSITION_INVALID),
    (lambda d: d["dispositions"][0].update(disposition="SATISFIED"), rd.DISPOSITION_INVALID),
    (lambda d: d["dispositions"][0].update(reason="  "), rd.DISPOSITION_INVALID),
    (lambda d: d["dispositions"][0].update(claim="STYLE"), rd.DISPOSITION_INVALID),
    (lambda d: d["dispositions"][0].update(evidence="docs"), rd.DISPOSITION_INVALID),
    (lambda d: d["operator"].update(identity=""), rd.DISPOSITION_INVALID),
    (lambda d: d.update(format="kriya.requirement_disposition/0"), rd.DISPOSITION_INVALID),
    (lambda d: d.update(extra=1), rd.DISPOSITION_INVALID),
    (lambda d: d["dispositions"].append(dict(d["dispositions"][0])), rd.DISPOSITION_INVALID),
    (lambda d: d["dispositions"].append({**d["dispositions"][0], "claim": BEHAVIOR}), rd.DISPOSITION_INVALID),
])
def test_05_every_binding_mismatch_is_a_typed_refusal(tmp_path, mutate, code):
    with pytest.raises(rd.RequirementDispositionError) as caught:
        _bind(tmp_path, FALSE_PREMISE, [("REQ-1", None, rd.REJECTED_FALSE_PREMISE, "x")], mutate=mutate)
    assert caught.value.reason_code == code


def test_06_a_disposition_inside_the_workspace_or_for_another_workspace_is_refused(tmp_path):
    ws, base, reqs, loaded, path = _bind(tmp_path, FALSE_PREMISE, [("REQ-1", None, rd.REJECTED_FALSE_PREMISE, "x")])
    inside = ws / "disposition.json"
    inside.write_text(path.read_text())
    with pytest.raises(rd.RequirementDispositionError, match="outside the workspace"):
        rd.load_requirement_dispositions(str(inside), reqs, FALSE_PREMISE, state_root=str(tmp_path / "s"),
                                         workspace=str(ws), base_revision=base)
    (tmp_path / "other").mkdir()
    other_ws, _same_content = _workspace(tmp_path / "other")
    (other_ws / "extra.py").write_text("X = 1\n")  # identical content commits to the same sha: move HEAD
    _git(other_ws, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(other_ws, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "other")
    other_base = _git(other_ws, "rev-parse", "HEAD")
    assert other_base != base
    with pytest.raises(rd.RequirementDispositionError) as caught:
        rd.load_requirement_dispositions(str(path), reqs, FALSE_PREMISE, state_root=str(tmp_path / "s"),
                                         workspace=str(other_ws), base_revision=other_base)
    assert caught.value.reason_code == rd.DISPOSITION_BASE_MISMATCH


def test_07_a_model_cannot_disposition_and_a_disposition_for_other_words_binds_nothing(tmp_path):
    ws, base, reqs, loaded, _p = _bind(tmp_path, FALSE_PREMISE, [("REQ-1", None, rd.REJECTED_FALSE_PREMISE, "x")])
    # a verdict channel saying "dispositioned" is UNKNOWN: only the sealed record derives the outcome
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    record_requirement_verdicts(ledger, reqs, {"REQ-1": (RequirementOutcome.DISPOSITIONED, "model says so"),
                                               "REQ-2": (RequirementOutcome.UNVERIFIED, "x")},
                                revision=1, evidence_fingerprint="cand", source="model")
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNKNOWN
    # a dict shaped like a disposition on the engine is not one
    class _Engine:
        requirement_dispositions = {"format": rd.DISPOSITION_FORMAT, "dispositions": []}
    assert rd.bound_dispositions(_Engine()) is None
    # a sealed record read against altered words (a resumed or edited goal) derives nothing
    contract = _compile(reqs, FALSE_PREMISE, loaded)
    ledger2 = _ledger(reqs, contract)
    altered = derive_requirements(FALSE_PREMISE.replace("blank lines", "empty lines"))
    assert requirement_outcomes(ledger2, altered)["REQ-1"] is RequirementOutcome.UNVERIFIED


def test_08_a_disposition_added_or_altered_after_sealing_is_another_contract_and_another_resume_identity(tmp_path):
    ws, base, reqs, loaded, _p = _bind(tmp_path, FALSE_PREMISE, [("REQ-1", None, rd.REJECTED_FALSE_PREMISE, "x")])
    ws2, base2, reqs2, altered, _q = _bind(tmp_path / "b", FALSE_PREMISE, [("REQ-1", None, rd.HISTORICAL_CONTEXT, "x")])
    bare, sealed, changed = _compile(reqs, FALSE_PREMISE), _compile(reqs, FALSE_PREMISE, loaded), _compile(reqs2, FALSE_PREMISE, altered)
    assert len({bare.digest, sealed.digest, changed.digest}) == 3
    assert sealed.identity_payload()["dispositions"]["digest"] == loaded.digest
    cfg = strict_config()
    identities = {json.dumps(generation_resume_fingerprints(cfg, str(tmp_path), goal="g", verification_contract_digest=c.digest),
                             sort_keys=True, default=str) for c in (bare, sealed, changed)}
    assert len(identities) == 3  # a candidate generated under one never resumes under another


def test_09_the_template_is_unsealed_and_a_non_claim_statement_needs_no_disposition(tmp_path):
    ws, base = _workspace(tmp_path)
    reqs = derive_requirements(FALSE_PREMISE)
    template = rd.disposition_template(reqs, FALSE_PREMISE, [("REQ-1", None)], base_revision=base, operator_identity="me")
    assert template["dispositions"][0]["disposition"] == "" and template["dispositions"][0]["reason"] == ""
    assert template["dispositions"][0]["requirement_text_sha256"] == _sha(reqs.get("REQ-1").text)
    heading = "Reproducer 2:\n\n    cache = TTLCache(maxsize=2)\n\nEvery existing test must keep passing unchanged.\n"
    ws2, base2, reqs2, loaded, _p = _bind(tmp_path / "h", heading, [("REQ-1", None, rd.OUT_OF_SCOPE, "x")])
    entry = _compile(reqs2, heading, loaded).entry("REQ-1")
    assert entry.status == "NOT_A_CLAIM" and entry.disposition is None  # structure decides first; nothing to remove
    assert ExternalAuthority  # imported for parity with the coverage module's surface


def test_10_a_goal_whose_every_statement_is_dispositioned_is_refused_never_a_no_mutation_success(tmp_path):
    """m49: a dispositioned statement is not mandatory - and with nothing mandatory left there is nothing to verify,
    so the goal is refused (GOAL_INSUFFICIENT_FOR_VERIFICATION) rather than succeeding with zero obligations."""
    goal = "Both cases worked in the version we used before.\n"
    ws, base, reqs, loaded, _p = _bind(tmp_path, goal, [("REQ-1", None, rd.HISTORICAL_CONTEXT, "a remark")])
    contract = _compile(reqs, goal, loaded)
    assert contract.mandatory_entries() == [] and contract.entry("REQ-1").status == STATUS_DISPOSITIONED
    refusal = contract.refusal()
    assert refusal is not None and refusal.reason_code == "GOAL_INSUFFICIENT_FOR_VERIFICATION"
    assert "operator-dispositioned" in refusal.residual[0]["why"]
    report = run_baseline_authorities(contract, reqs, base_revision=base, judge_suite=lambda: "PASS")
    assert not report.no_mutation_required
