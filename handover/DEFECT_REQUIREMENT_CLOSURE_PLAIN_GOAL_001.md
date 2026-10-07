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
- Adjacent: PRD-020, GR-R1A/R0/R1B, FS-1/1C0/1C1, B2-a/B2-COV/B3, model-evidence hardening, failure reporting,
  AUTH-GOAL-CONTAMINATION, D8, JVM acceptance: green. ruff + pylint 0.
