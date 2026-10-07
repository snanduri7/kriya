# REQUIREMENT-CLOSURE-PLAIN-GOAL-001: a plain-language goal could never reach success under the production policy

## Status
**IMPLEMENTED** (2026-10-08) on `fix/backend-reliability-closure` from main f757e6b, per the owner decision of
2026-10-07. Registry row CLOSED. Discovered by the BACKEND-READINESS-001 blind cohort (T4, T5; by trace every task);
implemented in BACKEND-RELIABILITY-CLOSURE-002 (evidence `~/kriya-m1-live/backend-reliability-closure-002/defects/requirement-closure/`).

## Observation (MEASURED, cohort T4/T5)
Every goal-derived requirement stayed MODEL_CLAIMED/UNVERIFIED; the production preset seals
`requirement_unverified_policy=block`; the only deterministic closers (operator acceptance file, named tests, "do not
modify any other file", the migration gate) could not fire, so GENUINE_SUCCESS was structurally unreachable - found
after 7 to 11 model calls and a correct candidate.

## Owner decision (2026-10-07)
A clear plain-language goal is valid requirement authority. Residual mandatory requirements remain BLOCKING;
MODEL_CLAIMED is advisory evidence only (LLM output authorizes nothing; evidence strength must match claim strength);
B2-COV unchanged (finite examples never close a general rule); improve the classifier/closers so requirements are not
unnecessarily residual; a goal with a mandatory requirement that genuinely has no deterministic closer is refused
before model execution with GOAL_INSUFFICIENT_FOR_VERIFICATION, naming the requirement, why, and the accepted forms.

## Implementation (reuses the PRD-020 / FS-1C / GR-R1A structures; no parallel framework)
- `requirements.py`: closed-vocabulary, whole-statement recognizers `is_suite_preservation_requirement` ("Every
  existing test must keep passing unchanged (<command>)", code spans/parentheses stripped) and
  `is_test_immutability_requirement` ("Do not change any existing test."); `requirement_claims` types a pure suite
  statement as REGRESSION_PRESERVATION; `mutation_path_roles` treats a path a pure preservation statement names as a
  reference. New closers: `close_suite_preservation_requirements` (the candidate's own full suite under the production
  test gate: COMPLETE structured evidence, gate passed, tests executed, no executed case failed; method
  `full_regression_oracle`, a named-test-class method so it never carries BEHAVIOR) and
  `close_test_immutability_requirements` (the run's mutation record against the tests that existed before the run;
  a changed or deleted existing test file is deterministic VIOLATED evidence). `deterministic_closers` /
  `admission_gap` classify each requirement (named tests, suite preservation, test immutability, mutation scope with
  a named target, migration gate when the repository resolves the migration, acceptance file coverage) and build the
  typed refusal `GoalAdmissionError` (residual requirements with why; ACCEPTED_GOAL_FORMS).
- `workflow.py` (direct path) and `workflow_controller.py` (enforce path): admission runs on the authoritative set
  (contract-bound or derived) before the first model call, only when `requirement_unverified_policy == "block"`
  (under `record` nothing blocks, nothing is refused); typed result `failure_category
  goal_insufficient_for_verification`, `reason_codes [GOAL_INSUFFICIENT_FOR_VERIFICATION]`, `requirements_admission`,
  event `requirement.admission_refused`, trace row with zero attempts. The two closers run beside the acceptance and
  named-test closers at the pre-apply boundary and in enforce's terminal gate.
- CLI banner `[GOAL INSUFFICIENT FOR VERIFICATION]`; failure category table; docs/user_guide.md.
- Not implemented (reported): the owner's README example ("if there is a README function list, update it") needs a
  conditional repository-state closer; descriptive sentences of an issue-style goal remain mandatory requirements (the
  derivation is unchanged), so such goals are refused at admission - the admission numbers of the reruns measure it.

## Verification
- `tests/test_requirement_closure_plain_goal_001.py` (26): recognizers on the cohort's phrasings (incl. the
  API-preservation and compound sentences that must NOT match), admission naming the residual requirement and the
  accepted forms, a goal of closable statements admitted, a named test with a behaviour claim still needing
  acceptance, the suite closer on a real pytest project (closed; failing/empty/incomplete/no-verdict never close),
  the immutability closer (closed; changed or deleted test VIOLATED; unknown reference set fails closed; git-backed
  mutation record), the run refused before the first model call under the block policy, not refused under record.
- Four existing tests moved from "blocked at the terminal after one Developer attempt" to "refused before any model
  call" (PRD-020 production run, FS-1C1 compound goal, FS-1 specimen under production, PRD-020 enforce migration
  closure now sees the resolved identities): the invariants they protect (never SUCCESS, nothing applied) hold earlier.
- Mutations (`mutations.txt`): 6/6 killed (admission silent, completeness ignored, recognizer opened, deletions
  ignored, model judgment counted as a closer, admission under every policy).
- Adjacent at c6b1981: PRD-020 lineage, GR-R1A/R0/R1B, FS-1/1C0/1C1, B2-a/B2-COV/B3, model-evidence hardening,
  failure reporting, AUTH-GOAL-CONTAMINATION, D8, JVM acceptance green - but tests/test_prd020_mutation_scope.py had
  5 failures (found by the full suite and the independent review; the first version of this record wrongly said
  "green"): its production-policy behaviour goals are now refused at admission; those tests exercise the scope closer
  under the record policy. ruff + pylint 0.

## Independent review reconciliation (2026-10-08, reviews/FIX_INDEPENDENT_REVIEW.md)
- BLOCKING 5.2 (false-success path, MEASURED by the reviewer): "Make the failing test pass." was a suite statement
  because the inherited named-test vocabulary contains "make"/"failing"; and "unchanged" closed on a green suite while
  an existing test could have been rewritten. Fixed: goal-directed words (make, fix, fail, failing, fails, failed) are
  excluded from the suite vocabulary; a suite statement with "unchanged"/"intact"/"untouched"/"unmodified" classifies
  as SUITE_PRESERVATION + TEST_IMMUTABILITY and its closer records VIOLATED when an existing test changed or
  vanished, closes nothing without the mutation record. Tests 13/14.
- BLOCKING 5.1: the five mutation-scope tests; the controller's GoalAdmissionError branch now has its own test (15).
- MATERIAL 5.3: test 16 proves the terminal backstop under production end to end (an admitted goal whose closers
  cannot bind stops REQUIREMENTS_UNRESOLVED after one Developer call, nothing applied).
- MATERIAL 5.4: the enforce path's admission moved to right after the run's evidence store opens, before retrieval
  and planning (a refused run still leaves a sealed record).
- MINOR 5.7: the immutability closer keys closability on the mapped outcome (GR-R0) like the other closers.
- Known limits recorded, not changed: 5.5 (an engine without a kernel/config has no production profile, no
  admission), 5.6 (a compound migration sentence is admitted on the migration alone, mirroring the existing gate),
  5.8 (the suite closer runs the candidate's suite at the pre-apply boundary, a second full-suite run per candidate
  beside the terminal regression gate - a cost item for the owner, kept for correctness tonight).
