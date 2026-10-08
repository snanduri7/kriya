"""VERIFICATION-CONTRACT-003: the baseline authority run and NO_MUTATION_REQUIRED.

After the contract is sealed and before the first model call, every bound
behaviour authority - the operator's sealed external oracle, the B2
acceptance file, the goal's compiled examples - runs once against the
untouched baseline tree. The result is deterministic evidence recorded on
the run (event ``verification_contract.baseline``):

- for a defect-fix goal the authority is expected to FAIL at baseline:
  ``discriminating`` - the oracle can tell the fix apart from the base;
- when the baseline already satisfies every mandatory claim - every
  behaviour claim PASSes under its authority, every preservation /
  immutability / scope / API claim holds by identity (a zero mutation
  changes nothing), nothing asks for a new artifact (a test addition, a
  migration, an unconditional documentation entry) - the honest result is
  ``NO_MUTATION_REQUIRED``: a legitimate success without a model call. A
  false premise never forces a mutation (owner decision, 2026-10-08);
- when the baseline passes but an artifact claim remains ("add a test for
  it"), the run proceeds; the baseline facts stay recorded.

Nothing here consults a model; nothing here weakens a claim: a claim with no
bound authority is simply unsatisfied at baseline (the contract refused it
earlier under the blocking policy).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional

from kriya.workflow.contract_compilation import (
    CLOSER_ACCEPTANCE,
    CLOSER_ACCEPTANCE_APPROVAL,
    CLOSER_DERIVED_EXAMPLES,
    CLOSER_DOCUMENTATION_LIST_ENTRIES,
    CLOSER_DOCUMENTATION_NOT_APPLICABLE,
    CLOSER_EXTERNAL_ACCEPTANCE,
    CLOSER_MIGRATION_GATE,
    CLOSER_TEST_ADDITION,
    STATUS_DISPOSITIONED,
    STATUS_NOT_A_CLAIM,
    VerificationContract,
)
from kriya.workflow.requirement_scopes import MIGRATION
from kriya.workflow.requirements import (
    API_PRESERVATION,
    BEHAVIOR,
    DOCUMENTATION_CLAIM,
    FILE_IMMUTABILITY_CLAIM,
    REGRESSION_PRESERVATION,
    TEST_ADDITION_CLAIM,
    TEST_IMMUTABILITY_CLAIM,
    RequirementOutcome,
    RequirementSet,
)

NO_MUTATION_REQUIRED = "NO_MUTATION_REQUIRED"
# Owner decision D2: an oracle that cannot presently execute under containment
# is reported, never admitted - a typed stop before any model call.
VERIFICATION_AUTHORITY_UNAVAILABLE = "VERIFICATION_AUTHORITY_UNAVAILABLE"
_ENVIRONMENT_REASON_CODES = frozenset({"AUTHORITY_EXECUTION_UNAVAILABLE", "AUTHORITY_PREPARE_FAILED", "AUTHORITY_TIMEOUT"})
BASELINE_PASS = "PASS"
BASELINE_FAIL = "FAIL"
BASELINE_INDETERMINATE = "INDETERMINATE"
BASELINE_IDENTITY = "IDENTITY"  # holds for a zero mutation by definition
BASELINE_MUTATION_REQUIRED = "MUTATION_REQUIRED"  # the claim asks for a change the baseline cannot carry
BASELINE_UNBOUND = "UNBOUND"  # no authority bound to the claim
# OD-3: a frozen named file holds at the untouched baseline by definition, like an existing test's immutability.
_IDENTITY_CLAIMS = frozenset({TEST_IMMUTABILITY_CLAIM, API_PRESERVATION, FILE_IMMUTABILITY_CLAIM})
_AUTHORITY_CLOSERS = frozenset({CLOSER_EXTERNAL_ACCEPTANCE, CLOSER_ACCEPTANCE, CLOSER_ACCEPTANCE_APPROVAL,
                                CLOSER_DERIVED_EXAMPLES})


@dataclass
class BaselineAuthorityReport:
    base_revision: Optional[str]
    authorities_run: List[Dict[str, Any]] = field(default_factory=list)  # kind, digest, verdict/evidence
    claims: Dict[str, Dict[str, Dict[str, Any]]] = field(default_factory=dict)  # rid -> claim -> {state, authority, why}
    no_mutation_required: bool = False
    discriminating: bool = False
    mutation_required: Dict[str, List[str]] = field(default_factory=dict)
    unsatisfied: Dict[str, List[str]] = field(default_factory=dict)

    def unavailable_authorities(self) -> List[Dict[str, Any]]:
        """The bound authorities whose baseline run was an environment outcome
        (containment or toolchain unavailable, acquisition failed, timeout):
        they cannot judge any candidate either, so the run must not proceed."""
        return [entry for entry in self.authorities_run if entry.get("reason_code") in _ENVIRONMENT_REASON_CODES]

    def outcomes(self) -> Dict[str, str]:
        """Per requirement, the outcome the baseline establishes when nothing
        needs to change (HUMAN_ACCEPTED for operator sufficiency, else
        CLOSED_BY_EVIDENCE); meaningful only when ``no_mutation_required``."""
        outcomes: Dict[str, str] = {}
        for rid, claims in self.claims.items():
            if any(entry["state"] == "NOT_A_CLAIM" for entry in claims.values()):
                outcomes[rid] = RequirementOutcome.NOT_A_CLAIM.value
            elif any(entry["state"] == "DISPOSITIONED" for entry in claims.values()):
                outcomes[rid] = RequirementOutcome.DISPOSITIONED.value
            elif any(entry.get("authority") in ("external_acceptance_command", "acceptance_approval")
                     for entry in claims.values()):
                outcomes[rid] = RequirementOutcome.HUMAN_ACCEPTED.value
            else:
                outcomes[rid] = RequirementOutcome.CLOSED_BY_EVIDENCE.value
        return outcomes

    def to_dict(self) -> Dict[str, Any]:
        return {"base_revision": self.base_revision, "authorities_run": list(self.authorities_run),
                "claims": {rid: dict(claims) for rid, claims in self.claims.items()},
                "no_mutation_required": self.no_mutation_required, "discriminating": self.discriminating,
                "mutation_required": dict(self.mutation_required), "unsatisfied": dict(self.unsatisfied)}


class VerificationAuthorityUnavailable(Exception):
    """A bound authority cannot execute in this environment (D2: fail closed, never admit)."""

    reason_code = VERIFICATION_AUTHORITY_UNAVAILABLE

    def __init__(self, contract: VerificationContract, report: BaselineAuthorityReport) -> None:
        self.contract = contract
        self.report = report
        unavailable = report.unavailable_authorities()
        listed = "; ".join(f"{e.get('kind')} {str(e.get('digest'))[:12]}: {e.get('reason_code')} ({e.get('reason')})"
                           for e in unavailable)
        self.message = (f"{VERIFICATION_AUTHORITY_UNAVAILABLE}: a bound verification authority cannot execute under "
                        f"Kriya's containment in this environment, so no candidate could ever be judged - {listed}")
        super().__init__(self.message)

    @property
    def failure_category(self) -> str:
        return VERIFICATION_AUTHORITY_UNAVAILABLE.lower()

    def result(self) -> Dict[str, Any]:
        return {"status": "failure", "quality_gates_passed": False, "files": [], "failure_category": self.failure_category,
                "reason_codes": [VERIFICATION_AUTHORITY_UNAVAILABLE], "error": self.message,
                "environment_failure": self.message, "verification_contract": self.contract.report(),
                "baseline_authority": self.report.to_dict()}


class NoMutationRequired(Exception):
    """The baseline already satisfies every mandatory claim: a success without a model call."""

    def __init__(self, contract: VerificationContract, report: BaselineAuthorityReport) -> None:
        self.contract = contract
        self.report = report
        super().__init__(f"{NO_MUTATION_REQUIRED}: the baseline satisfies every mandatory requirement of the goal")

    def result(self) -> Dict[str, Any]:
        return {"status": "success", "quality_gates_passed": True, "files": [], "no_mutation_required": True,
                "reason_codes": [NO_MUTATION_REQUIRED],
                "requirements": {"digest": self.contract.requirement_set_digest, "outcomes": self.report.outcomes()},
                "verification_contract": self.contract.report(), "baseline_authority": self.report.to_dict()}


def _verdict_from_judgment(judgment: Any) -> str:
    if getattr(judgment, "passed", False):
        return BASELINE_PASS
    if getattr(judgment, "violated", False):
        return BASELINE_FAIL
    return BASELINE_INDETERMINATE


def run_baseline_authorities(
    contract: VerificationContract, requirement_set: RequirementSet, *, base_revision: Optional[str],
    run_bundle: Optional[Callable[[], Any]] = None, bundle_digest: Optional[str] = None,
    judge_acceptance: Optional[Callable[[], Mapping[str, Any]]] = None, acceptance_digest: Optional[str] = None,
    judge_examples: Optional[Callable[[], Mapping[str, Any]]] = None, examples_digest: Optional[str] = None,
    judge_suite: Optional[Callable[[], str]] = None,
) -> BaselineAuthorityReport:
    """Run every bound behaviour authority against the baseline (each callable
    executes its authority on the untouched tree and returns the judgment)
    and decide, claim by claim, what the baseline establishes."""
    from kriya.workflow.authority_bundle import VERDICT_FAIL, VERDICT_PASS

    report = BaselineAuthorityReport(base_revision=base_revision)
    verdicts: Dict[str, Dict[str, str]] = {}  # authority digest -> rid -> PASS/FAIL/INDETERMINATE
    bound_closers = {closer for entry in contract.entries for closer in entry.closers}
    if run_bundle is not None and bundle_digest and CLOSER_EXTERNAL_ACCEPTANCE in bound_closers:
        run = run_bundle()
        verdict = {VERDICT_PASS: BASELINE_PASS, VERDICT_FAIL: BASELINE_FAIL}.get(run.verdict, BASELINE_INDETERMINATE)
        covered = [rid for rid, b in contract.binding_closers(CLOSER_EXTERNAL_ACCEPTANCE) if b.authority_digest == bundle_digest]
        verdicts[bundle_digest] = {rid: verdict for rid in covered}
        report.authorities_run.append({"kind": "external_acceptance_command", "digest": bundle_digest,
                                       "verdict": verdict, **run.evidence()})
    for closer, judge, digest, kind in ((CLOSER_ACCEPTANCE, judge_acceptance, acceptance_digest, "acceptance_file"),
                                        (CLOSER_ACCEPTANCE_APPROVAL, judge_acceptance, acceptance_digest, "acceptance_approval"),
                                        (CLOSER_DERIVED_EXAMPLES, judge_examples, examples_digest, "goal_examples")):
        if judge is None or not digest or closer not in bound_closers or digest in verdicts:
            continue
        judgments = judge()
        verdicts[digest] = {rid: _verdict_from_judgment(j) for rid, j in judgments.items()}
        report.authorities_run.append({"kind": kind, "digest": digest, "verdicts": dict(verdicts[digest])})
    # Review VC3-R9: a regression-preservation claim is not assumed by identity -
    # the baseline suite must execute and pass (``judge_suite`` -> PASS/FAIL/
    # INDETERMINATE, run once, only when such a claim is bound).
    suite_verdict: Optional[str] = None
    suite_bound = any(REGRESSION_PRESERVATION in entry.required_claims for entry in contract.mandatory_entries())
    if suite_bound and judge_suite is not None:
        suite_verdict = judge_suite()
        report.authorities_run.append({"kind": "baseline_suite", "digest": None, "verdict": suite_verdict})
    all_satisfied = True
    for entry in contract.entries:
        rid = entry.requirement_id
        claims: Dict[str, Dict[str, Any]] = {}
        if entry.status == STATUS_NOT_A_CLAIM:
            claims["NON_CLAIM"] = {"state": "NOT_A_CLAIM", "authority": "contract", "why": entry.scope.non_claim_reason}
            report.claims[rid] = claims
            continue
        if entry.status == STATUS_DISPOSITIONED:
            # BACKEND-READINESS-004 (D3): removed from the obligation set by the operator, never judged.
            claims["STATEMENT"] = {"state": "DISPOSITIONED", "authority": "operator_disposition",
                                   "why": (entry.disposition or {}).get("reason")}
            report.claims[rid] = claims
            continue
        for claim in entry.required_claims or ((MIGRATION,) if MIGRATION in entry.scope.scopes else ()):
            # BACKEND-READINESS-004: a claim may carry several bindings (the repository closer beside the sealed
            # oracle); the closer-type checks read the first, the authority verdict considers every judged one.
            claim_bindings = [b for b in entry.bindings if b.claim == claim]
            binding = claim_bindings[0] if claim_bindings else None
            judged = [(b, verdicts[b.authority_digest].get(rid, BASELINE_INDETERMINATE)) for b in claim_bindings
                      if b.closer in _AUTHORITY_CLOSERS and b.authority_digest in verdicts]
            if claim in (TEST_ADDITION_CLAIM, MIGRATION) or (binding is not None and binding.closer in (CLOSER_TEST_ADDITION, CLOSER_MIGRATION_GATE)):
                claims[claim] = {"state": BASELINE_MUTATION_REQUIRED, "authority": None,
                                 "why": "the claim asks for a change the baseline cannot carry"}
                report.mutation_required.setdefault(rid, []).append(claim)
                all_satisfied = False
            elif claim == REGRESSION_PRESERVATION:
                if suite_verdict is None:
                    claims[claim] = {"state": BASELINE_UNBOUND, "authority": None,
                                     "why": "the baseline suite was not executed"}
                    all_satisfied = False
                    report.unsatisfied.setdefault(rid, []).append(claim)
                else:
                    claims[claim] = {"state": suite_verdict, "authority": "repository",
                                     "why": f"the baseline suite ran: {suite_verdict}"}
                    if suite_verdict != BASELINE_PASS:
                        all_satisfied = False
                        report.unsatisfied.setdefault(rid, []).append(claim)
            elif claim in _IDENTITY_CLAIMS:
                claims[claim] = {"state": BASELINE_IDENTITY, "authority": "repository",
                                 "why": "a zero mutation preserves it by definition"}
            elif claim == DOCUMENTATION_CLAIM and binding is not None and binding.closer == CLOSER_DOCUMENTATION_NOT_APPLICABLE:
                claims[claim] = {"state": BASELINE_PASS, "authority": "repository", "why": "the referent is absent"}
            elif claim == DOCUMENTATION_CLAIM and binding is not None and binding.closer == CLOSER_DOCUMENTATION_LIST_ENTRIES:
                # BACKEND-READINESS-004 (owner decision 2): the sealed predicate judged the baseline at compile time.
                if binding.detail.get("baseline_satisfied"):
                    claims[claim] = {"state": BASELINE_PASS, "authority": "repository",
                                     "why": "every subject is already an entry of the named list"}
                else:
                    claims[claim] = {"state": BASELINE_MUTATION_REQUIRED, "authority": "repository",
                                     "why": "the named list lacks an entry for a subject the goal adds"}
                    report.mutation_required.setdefault(rid, []).append(claim)
                    all_satisfied = False
            elif judged:
                # One FAIL from any bound authority is the determinate signal (discriminating); PASS needs every
                # judged authority to pass; anything else is INDETERMINATE.
                states = [state for _b, state in judged]
                verdict = (BASELINE_FAIL if BASELINE_FAIL in states
                           else BASELINE_PASS if all(state == BASELINE_PASS for state in states) else BASELINE_INDETERMINATE)
                claims[claim] = {"state": verdict, "authority": judged[0][0].authority_kind,
                                 "digest": judged[0][0].authority_digest,
                                 "authorities": [{"kind": b.authority_kind, "digest": b.authority_digest, "verdict": state}
                                                 for b, state in judged],
                                 "why": f"the bound authorities judged the baseline {verdict}"}
                if verdict == BASELINE_FAIL:
                    report.discriminating = True
                if verdict != BASELINE_PASS:
                    all_satisfied = False
                    report.unsatisfied.setdefault(rid, []).append(claim)
            else:
                claims[claim] = {"state": BASELINE_UNBOUND, "authority": None,
                                 "why": "no authority judged this claim at baseline"}
                all_satisfied = False
                report.unsatisfied.setdefault(rid, []).append(claim)
        if entry.status != STATUS_NOT_A_CLAIM and not entry.required_claims and MIGRATION not in entry.scope.scopes:
            # a whole-statement closer (mutation scope): a zero mutation is in scope
            claims[entry.scope.scopes[0] if entry.scope.scopes else BEHAVIOR] = {
                "state": BASELINE_IDENTITY, "authority": "repository", "why": "a zero mutation changes no file"}
        report.claims[rid] = claims
    report.no_mutation_required = bool(contract.mandatory_entries()) and all_satisfied and contract.refusal() is None
    return report
