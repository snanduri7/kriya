# BACKEND-READINESS-004 - backend closure design record (2026-10-08)

Branch `feature/backend-readiness-004` off main a83e344 (product f7a0bc0). Owner instruction: "KRIYA BACKEND CLOSURE +
READINESS PROOF - AUTONOMOUS BATCH 004" plus the completion addendum (ZERO KNOWN BACKEND DEFECTS, SECTION Q).
Core principle, unchanged everywhere below: **LLM OUTPUT CAN AUTHORIZE NOTHING.** Model output can propose authority;
only the operator can grant it, and only from pre-existing, sealed truth.

## 1. Verification contract: coverage per (requirement, claim)  [AUTHORITY-REGRESSION-CLAIM-COVERAGE-001]

- A requirement statement makes one or more claims (`CLAIM_KINDS`: BEHAVIOR, REGRESSION_PRESERVATION, API_PRESERVATION,
  TEST_IMMUTABILITY, TEST_ADDITION, DOCUMENTATION, ...). The compiler (`kriya/workflow/contract_compilation.py`,
  `CONTRACT_COMPILER_VERSION = 2`) binds every applicable closer to every claim; an external authority's coverage is
  `rid -> claim -> {accepted_strength, why}` (`kriya/workflow/authority_bundle.py`, `AuthorityBundle.covers`,
  `coverage_entry(rid, claim)`; a duplicate (rid, claim) entry is refused; REGRESSION_PRESERVATION is coverable).
- Per-producer claim judgments (`kriya/workflow/requirements.py`, `requirement_claim_records`): VIOLATED is sticky;
  an INDETERMINATE producer revokes only its own method or the methods it declares (`revokes_methods`); the
  acceptance family declares its revocations. A claim closes only when every bound producer reports SATISFIED.
- Baseline aggregation (`contract_baseline.py`): FAIL if any authority fails, PASS only if all pass, else INDETERMINATE.
- Invariant kept: operator sufficiency closes claims, never whole requirements; the base revision is re-checked at
  closure; the contract digest is part of the resume identity.

## 2. Operator disposition (D3) and typed authority requests (D2)

- `kriya/workflow/requirement_disposition.py`: `kriya.requirement_disposition/1`, `--requirement-disposition`.
  Entries name one requirement (or one claim of it), its exact text digest and a reason (REJECTED_FALSE_PREMISE,
  HISTORICAL_CONTEXT, INFORMATIONAL_CONTEXT, OUT_OF_SCOPE) with evidence; bound to goal, requirement set and base
  revision; sealed before any model call; immutable after sealing; its digest joins the contract identity.
  A dispositioned statement is `STATUS_DISPOSITIONED` / outcome `DISPOSITIONED` - reported, never satisfied. A goal
  whose every statement is dispositioned is refused (GOAL_INSUFFICIENT), never a zero-obligation success.
  Events: `verification_contract.dispositioned`.
- `kriya/workflow/authority_request.py`: on a contract refusal, one `kriya.authority_request/1` per requirement is
  sealed (which claim, which authority kinds would be acceptable, the exact text digest); carried on the
  `AdmissionRefusal` (`authority_requests`), the result and the event `verification_contract.authority_requested`.
  The request is a form the operator fills from frozen truth; nothing in it is derived from model output.

## 3. Documentation list-entries predicate (owner decision 2)

`documentation_subjects` (`requirement_scopes.py`) + `documentation_entries_predicate` (`contract_compilation.py`) +
closer `documentation_list_entries` (`contract_closers.py`): a statement "document X, Y and Z in the README section S"
is a deterministic predicate over the named section's entries (list items, table rows, code spans, bold lines or
lines opening with the subject); PASS/MUTATION_REQUIRED on the baseline, DOCUMENTATION_ENTRIES_MISSING /
LIST_REMOVED on the candidate. Never dispositioned OUT_OF_SCOPE.

## 4. Java and Gradle closers

- `api_preservation.py`: the public/protected Java surface from the code-intel structural model (signatures,
  modifiers, sorted supertypes), compared base vs candidate; `API_PREDICATE_LANGUAGES = {python, java}`.
- `acceptance_jvm.py`: the B2-c JUnit 5 acceptance class runs through the detected runner (Maven or Gradle,
  `build.gradle(.kts)`), `runner_source_digest` in the runner contract; same injection, collision and integrity rules.
- `example_oracle_java.py`: a goal's Java `expression -> literal` lines compile into a sealed class
  `kriya.examples.KriyaGoalExamplesTest`; other Java example forms are not claims.

## 5. Deterministic relocalization  [PLAN-TARGET-LOCALIZATION-MISMATCH-001]

`kriya/workflow/relocalization.py`: at the CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE stop, goal-named symbols are resolved
through the code-intel structural index to definition sites (`ATTRIBUTION_TIER = code_intelligence_definition`); the
result is a typed plan-scope conflict `PLAN_TARGET_RELOCALIZATION_REQUIRED` with grounded file locations - never a
silent retarget, never model text. No symbol or an ambiguous symbol keeps the typed stop.

## 6. Evidence tools  [REJECTED-CANDIDATE-RETENTION-001, review F1]

`kriya/core/attempt_evidence/leak_check.py` (`kriya evidence leak-check`): blob-level grep of every model-facing
record for lines present only in the hidden files plus authority markers, with the public counterparts as the
positive control (CLEAN / LEAKED / INCONCLUSIVE / UNAVAILABLE). `candidate_export.py` (`kriya evidence candidate`):
the staged candidate of an attempt rebuilt byte-for-byte outside the workspace with `CANDIDATE_MANIFEST.json`.

## 7. Policy change and boundary

- `acquisition_registry_hosts` += `plugins-artifacts.gradle.org` (owner decision 1: one exact official host, measured
  403 on the Plugin Portal redirect, acquisition phase only). Acquisition evidence now travels with every two-phase
  Gradle/Maven result (`acquisition_evidence`).
- GRADLE-GIT-AT-CONFIGURATION-BOUNDARY-001: a build that needs git metadata at configuration time cannot be gated;
  candidate trees are presented without a repository by design.

## 8. Platform and containment

- OCI bind mounts use `--mount type=bind,src=,dst=[,readonly]` at every site; a path with ',' is
  MOUNT_PATH_NOT_EXPRESSIBLE.
- Every remaining lexical containment check routes through `filesystem_semantics.path_relation` /
  `canonical_spelling`; workspace identity is case-stable (version 2) with the legacy id still readable.
- jdtls is a process tree the platform port owns (`spawn_subprocess_exec_fail_closed`, `terminate_process_tree`).

## 9. Registry sweep fixes (one commit, b14f31c; reproducers in tests/test_backend_readiness_004_rows.py)

OBS-2 segmenter bound (provider refusals only), Semgrep severity map /2, wheel release identity, worktree sync
without interpreter caches, contract provenance label, scope-conflict reason typing, capacity-refusal evidence, MCP
CLI typed refusal, final-review backend error typed, enforce subtask trace linkage (`enforce_run_id`,
`by_enforce_run`), Architect file-list escape partition (`partition_file_list_escapes`,
`plan.file_list_entries_rejected`, typed stop `architect_file_list_rejected`), Maven cache proof.

## 10. Verification discipline

- Every slice: targeted pytest, `ruff check .` and `pylint kriya plugins/core_tools tests` at zero findings.
- Mutation campaign `~/kriya-m1-live/backend-readiness-004/mutations/run_mutations_004.py` (m30-m88, exact-once
  substitution, restore, KILLED/SURVIVED): every registered mutant KILLED; four first survived and were killed by a
  strengthened test in a separate commit (m37a 7acdbc7, m49 93cb657, m50 b20c38b, m71 aaf9be4).
- Each registry fix: original symptom reproduced at the measured shape before the fix (the mutant is the reverse
  substitution), fix, regression, mutation, adjacent suites.
- Batch end: one full suite (`scripts/run_full_suite.py`), one independent same-class review, then the live
  cohorts under the production profile. Nothing here changes model profiles, qualification records or oracles.
