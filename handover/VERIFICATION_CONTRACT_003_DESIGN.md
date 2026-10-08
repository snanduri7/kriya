# VERIFICATION-CONTRACT-003: the sealed verification contract (design record, 2026-10-08)

Owner decisions D1-D5 of 2026-10-08 (evidence root `~/kriya-m1-live/verification-contract-003/PRE_FLIGHT.md`).
Objective: a realistic goal is compiled, before any model call, into an explicit contract that says for every derived
statement what kind of claim it is, what deterministic evidence may close it, which authority supplies that evidence,
and whether the model could alter it. Verification is not weakened: `LLM OUTPUT CAN AUTHORIZE NOTHING` stays the hard
rule; MODEL_CLAIMED remains advisory; B2-COV (finite cases never close a general rule) is unchanged.

## 1. Composition, not a second framework
`VerificationContract` (kriya/workflow/contract_compilation.py) composes what exists:
RequirementSet (PRD-020 / GR-R1A), the claim model (FS-1C1 `requirement_claims`, B2-COV `behavior_strength`), the
closer map (`deterministic_closers`), the B2 acceptance artifact, the B3 approval, the new external authority bundle
(authority_bundle.py), the new derived example oracle (example_oracle.py), the resume identity
(resume_fingerprints.generation_resume_fingerprints) and the enforce ControlState, content-addressed state storage,
and run events persisted into the trace row (KUP). One contract per run, compiled and sealed before the first model
call on both execution paths; its digest joins both resume identities; a changed digest never reuses a candidate.

## 2. Claim scopes (kriya/workflow/requirement_scopes.py) - structural, closed-vocabulary, no model
Reconciled with existing vocabulary: BEHAVIOR (+ strength EXACT/GENERAL), REGRESSION_PRESERVATION, MUTATION_SCOPE,
TEST_IMMUTABILITY, SUITE_PRESERVATION (closers) stay; new scopes API_PRESERVATION, DOCUMENTATION, PROCEDURE,
TEST_ADDITION, MIGRATION, NON_CLAIM.
NON_CLAIM (D1, conservative): (a) a label - the statement ends with ":" and has at most eight words, no request or
constraint cue, no example call, no "=" and no quantifier; (b) a code block - every raw line of the statement's
paragraph was indented code outside any list item and the block carries no expected-value marker (`->`, `expected`,
`returns`, `should`, `#`-comment with a value, `Expected:`/`Actual:`). Origin is established by the SAME segmenter
that derives the requirements (`statement_origins`), so ids, texts and the set digest are unchanged
(REQUIREMENT_DERIVATION_VERSION stays 1). A NON_CLAIM statement stays in the set, in lineage, in the prompt block and in
the report with status NOT_A_CLAIM, reason code STRUCTURAL_NON_CLAIM and its origin; it needs no closure and never
blocks. Propositions (defect descriptions, requests, constraints, acceptance, preservation, API statements) are never
NON_CLAIM - negative tests and mutations prove it.

## 3. Deterministic closers (existing + new)
named tests (FS-1C0), full regression (suite preservation), test immutability (mutation record), mutation scope,
migration gate, acceptance (B2), approved acceptance (B3) - unchanged. New: EXACT_USER_EXAMPLE (example_oracle:
goal example lines compiled deterministically into a B2-a acceptance module; B2-COV applies, so examples close only an
EXACT statement), API_PRESERVATION (Python AST public-signature diff base vs candidate; removed or changed signature
is VIOLATED; Java stays authority-required), DOCUMENTATION conditional ("... if there is one": absent referent closes
vacuously, present referent is a content claim and stays authority-required), TEST_ADDITION (a new runnable test file
or a new test identity in the candidate's structured report versus the base inventory), EXTERNAL_ACCEPTANCE_COMMAND
(authority bundle). Each closer states what it closes, who created it, PASS/FAIL/INDETERMINATE evidence.
A compound statement closes only when every clause scope is closed (per-claim records; `required_claims`).

## 4. Admission taxonomy (D3)
`GOAL_INSUFFICIENT_FOR_VERIFICATION` (existing GoalAdmissionError): no determinate claim at all, or an AMBIGUOUS
statement (undecidable mutation-scope roles, "other" without a referent). `VERIFICATION_AUTHORITY_REQUIRED` (new
VerificationAuthorityRequired): every statement is determinate, at least one mandatory claim has no bound deterministic
closer; lists per requirement id, text, scope, why the bound closer set is insufficient, acceptable authority types.
Both refuse before any model call with zero mutation, a sealed trace and a typed result; external classification
SAFE_FAILURE.

## 5. Authority bundle (D2, containment-first) - kriya/workflow/authority_bundle.py
Operator-authored JSON `kriya.verification_authority/1` outside the workspace: goal sha, requirement-set sha, base
revision, asset digests (hidden tests, scripts, baselines), `prepare` (optional, DEPENDENCY_REGISTRY_ONLY acquisition)
and `verify` (NetworkAuthority.DENIED) argv lists, bounded timeout, verdict protocol (exit 0 PASS, 1 FAIL, other
INDETERMINATE; optional verdict.json must agree), per-requirement coverage entries (id, text sha, claim, accepted
strength, accept_as_sufficient). Loaded, validated, content-addressed into `<state>/verification-authority/<sha>/`
before any model call; bound to the engine by the CLI only (`--verification-authority`). Execution: candidate copied
to a Kriya-owned scratch under the state root, assets staged read-only with digests checked before and after, run
through PolymorphicValidator's own `_run_cmd_with_timeout` (the production containment boundary, the candidate's
toolchain identity, REGISTRY_ONLY only for `prepare`). Outcome: HUMAN_ACCEPTED-class closure (operator sufficiency,
never "verified"), VIOLATED on FAIL, INDETERMINATE otherwise; AUTHORITY_EXECUTION_UNAVAILABLE when containment cannot
provide the toolchain. Never host execution. Never mounted into the workspace; never in a prompt.

## 6. Baseline authority and NO_MUTATION_REQUIRED
After sealing, before model execution, every bound behaviour authority (bundle, acceptance file, derived examples) runs
against the baseline tree. A defect goal is expected to FAIL there (recorded `discriminating`). When every mandatory
requirement is closed at baseline (preservation/immutability/scope hold trivially for a zero mutation) and no
TEST_ADDITION/creation claim remains, the run ends `NO_MUTATION_REQUIRED` success without a model call. Otherwise the
baseline facts are deterministic context for the Developer (never authority).

## 7. Sealing, invalidation, retries
Contract digest = sha256 over requirement set digest, goal identity, derivation/compiler versions, per-requirement
scope + closers + authority references, acceptance/approval/bundle/examples digests, base revision. Stored at
`<state>/verification-contracts/<digest>.json`, event `verification_contract.sealed`. The digest joins the direct-path
resume fingerprints (goal inputs) and `ControlState.verification_contract_digest` (enforce; drift reason
VERIFICATION_CONTRACT_CHANGED). Retries and fallback within a run bind the engine's artifacts, which only the CLI sets.

## 8. Events (closed table, tripwire) and GUI classification
verification_contract.compiled / sealed / refused / baseline / no_mutation_required / invalidated,
requirement.authority_required. All BACKEND_EVENT_ADDED; persisted via the trace row (KUP), no new store.

## 9. Deferred (registry rows)
Java API-preservation predicate; Java example compiler; Gradle JVM acceptance; VENV-ADDITIVE-REUSE-001.

## 10. As built (2026-10-08, feature/verification-contract)
| Slice | Modules | Tests |
|---|---|---|
| 1 scopes + contract + taxonomy | `requirement_scopes.py`, `contract_compilation.py`, `requirements.py` (origins, NOT_A_CLAIM, claim kinds, AdmissionRefusal/GoalAdmissionError/VerificationAuthorityRequired), `resume_fingerprints.py`, `control/state.py`, both paths, `failure_reporting.py`, `cli.py` banners | `tests/test_verification_contract_003_scopes.py` |
| 2 closers | `example_oracle.py`, `api_preservation.py`, `contract_closers.py`, contract-aware `acceptance_oracle.close_requirements_with_acceptance`, named-test / suite / immutability closers, both boundaries | `tests/test_verification_contract_003_closers.py` |
| 3 authority bundle (D2) | `authority_bundle.py` (manifest `kriya.verification_authority/1`, store, contained two-phase adapter, verdict protocol, integrity, closure as operator sufficiency), `cli.py --verification-authority`, both boundaries | `tests/test_verification_contract_003_authority.py` |
| 4 baseline + NO_MUTATION_REQUIRED | `contract_baseline.py`, `workflow.run_contract_baseline`, both paths, enforce trace events, CLI banner | `tests/test_verification_contract_003_baseline.py` |

Semantic changes recorded: an acceptance file alone no longer admits a GENERAL statement (B2-COV decided before any
model call; the approval is the authority); the enforce ControlState refuses subtask reuse under another or no
verification contract (`VERIFICATION_CONTRACT_CHANGED`, fail closed for states recorded before contracts existed);
`requirement_outcomes` reports HUMAN_ACCEPTED when any claim closed by operator sufficiency (B3 or the external
oracle). Known limits recorded: the Java API predicate, the Java example compiler and Gradle JVM acceptance are
registry rows (P3); a prose parenthetical beside a clause-only constraint keeps its behaviour claim (fail closed);
the baseline authority run executes the bound authorities once per run on the untouched tree (cost accepted).
GUI/KUP: every new fact is a run event in the trace row (`verification_contract.*`, `requirement.authority_required`)
- BACKEND_EVENT_ADDED; no new store. Mutation campaign: `~/kriya-m1-live/verification-contract-003/mutations/run_mutations.py`.
