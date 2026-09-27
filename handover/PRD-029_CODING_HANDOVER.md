# PRD-029 Coding Agent Handover: ContractRegistry Lifecycle Closure

## Status
**VERIFIED (Batch 6 closure, 2026-09-27).** Pytest: the 001C subset (1856) and full suite (6245) @ e34e0ee, and the PRD027 subset and full suite @ a04e8ac. Live: `handover/evidence/BATCH6/user-live-4`, every case LIVE_EXERCISED; the enforce case and the targeted case both reached a verified commit (COMMITTED, DIRECT authorization, consumers `checkout.py`/`tests/test_pricing.py` invalidated and re-verified by `terminal_full_regression`), 32K QUALIFIED qwen3-coder preflight. Production: `doctor --production` PRODUCTION_READY=true. Earlier status: READY_FOR_PYTEST_VERIFICATION. This is part of the Batch 6 stop.

## Source identity
- Base revision: 0ec07cc.
- Commits:
  - 61e4b26: the P0 fix, committed separately;
  - a0a2d0a: the PRD-029 lifecycle;
  - 7762590: my milestone bookkeeping fix;
  - b75e4ec: correction, milestone capabilities are established by the unit's own commit transaction;
  - f6f3bf0: every deterministic registry refusal is a terminal typed stop.
- Directives: handover/BATCH6_DIRECTIVES.md, the PRD-029 section.

## Suspected P0 defect: the outcome
**Confirmed and fixed separately (61e4b26).**
- **The defect.** `load_contract_registry` returned an empty registry for an unreadable or malformed `contracts.json`. The milestone path then saved that empty registry over it, and resume and commit proceeded as though no contracts existed.
- **The fix.**
  - The loader is now strict. A missing file is a valid, versioned empty registry; anything unreadable, malformed, of an unknown schema or kind raises `ContractRegistryCorruptError` (`CONTRACT_REGISTRY_CORRUPT`), and the file is never touched.
  - Both milestone load sites (`run_milestones` and the controller's milestone path) fail closed to `needs_review` before any milestone runs.
  - The commit seam refuses the commit on a corrupt registry.
  - The resume fingerprint reads a corrupt registry as UNAVAILABLE, so it never matches.
- **Evidence.** `tests/test_prd029_registry_integrity.py`: each reverted behaviour fails a test.

## Scope implemented
- **Req 1 (who creates and updates, from what evidence).**
  - Milestone capability records: registered PROPOSED by `run_milestones` at planning time. They become IMPLEMENTED only through the milestone unit's own terminal commit transaction (b75e4ec; see "Milestone capability transaction" below).
  - Public API records: only the terminal commit seam, derived from:
    - the deterministic `_normalized_public_signatures` diff, the same extractor as the PRD-023 detector;
    - exactly matching (owner, symbol) DIRECT (goal) or HUMAN (PRD-023 escalation) authorizations.
  - Record fields:
    - kind and owner;
    - public-signature shape and hash;
    - `source_revision` = `<commit txid>:<candidate hash>`;
    - consumers with provenance `name_reference_scan`, always `consumers_complete=False`;
    - authorization evidence;
    - `stale_reason`;
    - `invalidated_consumers`.
  - Registry-level fields: `schema_version`, `revision`, `source_revision`, and a digest.
- **Req 2 (derive before commit, authoritative only after it).**
  - `derive_contract_transition` runs before intent, and the after-state is serialized once.
  - The cycle intent records `before_digest`/`after_digest`, the delta and the downstream verification.
  - The after-state is staged under `.kriya/control/contracts.pending/<txid>.json` before the first source byte.
  - It is promoted by an `AuthorizedFileWriter` compare-and-swap of the staged bytes, and only if the live registry is still the before-state.
  - The cycle settles COMMITTED only after the promotion succeeds.
  - A failed or refused candidate never touches the live registry.
- **Req 3 (invalidate consumers).** Every changed or stale contract invalidates its name-scanned consumers, with a reason (`CONTRACT_REVISION_CHANGED` or `CONTRACT_STALE`). Invalidated consumers require downstream verification, meaning a terminal full suite on this candidate with tests executed:
  - direct path: `terminal_regression_succeeded` and the output confirms non-zero tests;
  - enforce: the final subtask's `regression_test` `PASS_WITH_TESTS`.
  Without it the transition is refused (`CONTRACT_CONSUMER_VERIFICATION_MISSING`). The result is a deterministic stop (`failure_category: contract_registry_blocked`), never SUCCESS and never a retry.
- **Req 4 (RunRecord and resume).**
  - The RunRecord commit cycle carries `contract_registry` (before/after digest, revision, source revision, delta), and `RunRecord.committed_contract_registry` exposes the latest committed identity.
  - A new `contract_registry` resume fingerprint (strict loader, schema-versioned digest) is a planning and candidate dependency. An absent registry reads as the empty identity, so it is CHANGED against a recorded one; a corrupt one is UNVERIFIED.
  - Both direct and milestone runs compute it through `generation_resume_fingerprints`.
- **Req 5 (no unsupported derived contracts as fact).** Only AUTHORIZED_DIRECT and AUTHORIZED_HUMAN changes become records. An unauthorized change never does, and on an established record it marks the record stale.
- **Transaction and crash semantics** (directive):
  - A promotion failure after the bytes land settles UNCERTAIN (`CONTRACT_REGISTRY_TRANSITION_INCOMPLETE`), never SUCCESS.
  - `kriya runs recover` settles each open cycle's transition from the proven cycle result, before closing the record:
    - COMMITTED: PROMOTED, or ALREADY_APPLIED.
    - Not committed: DISCARDED, and only while the registry is still the before-state.
    - Otherwise NEEDS_REVIEW: the record is left open and recover exits 1.
  - Real subprocess crash tests (`os._exit`) at the first source byte, before promotion and after promotion all recover to an exact registry.
- **Not changed:** no Planner `provides`/`consumes` fields were added.

## Milestone capability transaction (correction, b75e4ec)
- **Input.** `WorkUnitInvocation.provided_capabilities` (`"<milestone id>:<capability>"`, from the unit's own provenance; unit digests are unchanged) is passed to `derive_contract_transition(capability_contracts=)` by the unit's terminal commit.
- **The single transaction.** The capabilities, any `public_api` change and the source bytes are one transaction, in this order:
  1. derive, validate and identify the delta before commit;
  2. write the intent (`established_capabilities` included);
  3. stage the registry;
  4. commit the source;
  5. compare-and-swap promote the registry;
  6. settle the RunRecord as COMMITTED;
  7. report SUCCESS.
- **Idempotence.** An already-IMPLEMENTED capability is not transitioned twice. An id the run never registered is never invented.
- **The driver no longer marks anything.** A unit that committed source without its capability transition is an incomplete transaction: status `contract_registry_incomplete`, reason `CONTRACT_REGISTRY_TRANSITION_INCOMPLETE`, never a successful milestone. A unit that committed nothing establishes nothing. Reconstructed completions get no post-hoc marking; recovery completes their transaction.
- **Tests:**
  - `test_a_crash_at_every_boundary_recovers_to_an_exact_registry`: real `os._exit` crashes, each carrying capability `M1:Pricing`, before the source commit, after the source commit before registry promotion, and after promotion before the RunRecord settles. Each asserts the exact recorded identities, no false SUCCESS, no stale registry and no duplicate transition on a second recovery.
  - `test_capabilities_are_established_by_the_commit_transition`;
  - `test_an_already_established_capability_is_not_transitioned_twice`;
  - `test_a_committed_milestone_without_its_capability_transition_is_incomplete`;
  - `test_a_milestone_units_commit_establishes_its_capabilities` (end to end);
  - `test_milestone_completion_bookkeeping_preserves_contracts_its_unit_committed` (kept);
  - the corrupt-registry tests in `tests/test_prd029_registry_integrity.py` (kept).

## Registry refusals are terminal (f6f3bf0)
- Every deterministic refusal ends the run with the typed contract-registry failure (`failure_category: contract_registry_blocked`, `environment_failure` starting with the reason code). There is one Developer call, no retry, and the run is never `no_progress` or `quality_gates_exhausted`. The refusals:
  - `CONTRACT_CONSUMER_VERIFICATION_MISSING`;
  - `CONTRACT_REGISTRY_CORRUPT`;
  - `CONTRACT_REGISTRY_TRANSITION_INVALID` (new: an illegal registry lifecycle step inside the derivation, which previously escaped the commit seam as an untyped exception; any other exception, a coding error, still propagates);
  - `CONTRACT_REGISTRY_STAGING_FAILED`;
  - `CONTRACT_REGISTRY_TRANSITION_INCOMPLETE` (the promotion's before/after-state mismatch; the source bytes landed and the cycle stays open for `runs recover`).
- An unauthorized public API change is deliberately not in this set. PRD-023's detector names the candidate change, which the model can repair, so it stays retryable.
- Enforce's commit is the plan's last step after every subtask, so no retry exists after it; a refusal there returns its failure payload with the reason code.
- **Tests:** `test_a_registry_refusal_is_a_terminal_stop_never_a_retry` (parametrized over all five), `test_an_illegal_registry_lifecycle_step_is_a_typed_refusal` and `test_a_coding_error_inside_the_derivation_is_never_turned_into_a_refusal`.
- **Mutations**, all killed: dropping INVALID from the stop set; narrowing the typed catch; widening it to `Exception`; dropping the stop-type registration; dropping the workflow's stop branch.

## Found during implementation (my own defect, fixed before commit)
The end-to-end stop test showed a refused transition being retried until `no_progress`. `handle_attempt_failure` recomputes `environment_failure` from a fixed set of unretryable failure types, and `contract_registry` was not in it. It was registered there, with the failure-reporting category (VERIFICATION) and the pinned vocabulary test updated. `test_a_refused_contract_transition_is_a_deterministic_stop_not_a_retry` pins it; the mutation that removes the registration is killed.

## A second defect of my own, found by review after commit (fixed in 7762590)
- **The defect.** `run_milestones` held the registry it loaded at run start. After each milestone it marked capabilities on that copy and saved it, overwriting any `public_api` record the milestone unit's own commit had just promoted. The record, the registry revision and its source revision were lost, and the live digest no longer matched the cycle's `after_digest`. The control-state contract hash came from the same stale copy.
- **The fix.** Every milestone bookkeeping point builds on the live registry, loaded strictly: unit start, completion, and the control-state hash.
- **Test.** `test_milestone_completion_bookkeeping_preserves_contracts_its_unit_committed` reproduced the loss on a0a2d0a, and reverting the refresh is caught.

## Files changed
- **Production:**
  - `kriya/control/contracts.py`: schema 2, kinds, `registry_digest`, `replace_current`, and the error types.
  - `kriya/control/persistence.py`: strict loader, plus stage/read/promote/discard of pending registries.
  - `kriya/control/run_record.py`: `begin_commit(contract_registry=)` and `committed_contract_registry`.
  - `kriya/control/run_coordinator.py`: passes the transition through.
  - `kriya/control/recovery.py`: `_complete_contract_transitions` and `RecoveryReport.contract_transitions`.
  - `kriya/workflow/contract_lifecycle.py` (new).
  - `kriya/workflow/terminal_commit.py`: the transaction.
  - `kriya/workflow/workflow.py`: `_terminal_contract_authorizations` (shared with the terminal API recheck), `_raise_contract_registry_stop`, the result field `contract_registry`, and the category.
  - `kriya/workflow/workflow_controller.py`: `_enforce_contract_transition`, the result field, and a stale docstring corrected.
  - `kriya/workflow/milestones.py`: the fail-closed load.
  - `kriya/workflow/resume_fingerprints.py`: the fingerprint.
  - `kriya/workflow/retry_strategy.py`: the stop type.
  - `kriya/workflow/failure_reporting.py`.
  - `kriya/workflow/state.py`.
  - `kriya/cli.py`: the recover output and the category exclusions.
- **Tests:**
  - `tests/test_prd029_registry_integrity.py` (9);
  - `tests/test_prd029_contract_lifecycle.py` (27);
  - `tests/test_failure_reporting.py`: vocabulary pin.
- **Live:** `tests/test_live_prd025_029_batch6.py::test_live_prd029_authorized_api_change_is_bound_to_its_commit`.
- **Docs:** `docs/design.md` (ContractRegistry paragraph); `docs/user_guide.md` §3.4.2.

## Tests run by coding agent (new test files only, per the quota rule)
| Command | Passed | Failed |
|---|---:|---:|
| `.venv/bin/pytest -q tests/test_prd029_registry_integrity.py` | 9 | 0 |
| `.venv/bin/pytest -q tests/test_prd029_contract_lifecycle.py tests/test_failure_reporting.py` | 63 (+3 added later: 27 in the lifecycle file) | 0 |
| `.venv/bin/pytest -q tests/test_prd029_contract_lifecycle.py` (after b75e4ec and f6f3bf0) | 38 | 0 |

**Mutation checks.** Every one was killed:
- 4 on the P0;
- 16 on the lifecycle:
  - the consumer-verification refusal;
  - authorization matching;
  - staleness;
  - `consumers_complete`;
  - `source_revision`;
  - the discard before-state check (initially SURVIVED; test added);
  - the promotion before-state check;
  - the promotion after-digest check (initially SURVIVED; test added);
  - UNCERTAIN on promotion failure;
  - recovery leaving NEEDS_REVIEW records open;
  - the fingerprint wiring (initially SURVIVED; test added);
  - the stop-type registration;
  - the cycle intent.

## Static/lint/architecture checks
ruff: All checks passed. pylint: exit 0.

## Live test additions
- Required: YES.
- `test_live_prd029_authorized_api_change_is_bound_to_its_commit`: an enforce run on a tiny Python repo. The goal is phrased so `derive_direct_contract_authorizations` grants a DIRECT authorization for `pricing.total` (verified offline).
- On a verified commit it asserts:
  - a `public_api` record for `pricing.py`, with `checkout.py` as a consumer and not claimed complete;
  - the live registry digest equals the committed cycle's `after_digest`.
- If the model never reaches a verified commit, it skips, and the evidence file records `NOT_LIVE_EXERCISED` with the reason. A skip is never verification.

## Known limitations / residual risks
- **Milestone capability records**: closed by b75e4ec (see above).
- **Enforce uses DIRECT authorizations only.** HUMAN (PRD-023) approvals live in per-subtask state and are not aggregated to the plan-level commit. A HUMAN-approved enforce change therefore records no contract, which is fail-safe (not recorded as fact).
- **Not recorded:** explicit-migration goals, which the PRD-023 detector bypasses and so never classifies, and unreferenced public changes on owners with no record.
- **Consumers** come from a name-reference scan: never complete, and they can over-include a same-named symbol.
- **Checkpoint compatibility.** The registry payload now includes `schema_version`/`revision`/`source_revision`, so checkpoints saved before this commit fail their `contract_hash` comparison once and are not resumed. `kriya_runtime` already invalidates them on any code change, so this adds nothing in practice.
- **Enforce's downstream verification** relies on the final subtask's own full suite having run on the final candidate.
- **Stricter than before:** an authorized API change that invalidates consumers needs that suite with tests executed, so an enforce run in a repository with no tests cannot land one.
- **Non-git workspaces** never reach the commit seam, so they never record contracts.
- **The reference scan** matches each file as it is read and never holds the whole workspace in memory.

## Verification-agent handoff
Run the Batch 6 focused command, which includes the recovery, run-record, commit, milestone, controller and doctor suites, then the full suite, then the live `-k prd029` case.
