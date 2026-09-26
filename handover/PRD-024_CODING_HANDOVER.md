# PRD-024 Coding Agent Handover

## Status
VERIFIED at `ff3e6c9` (batch 5). Focused batch-5 pytest 1962 passed at `023a9cd` (the earlier focused run's 1 failure, an enforce test fixture whose bare MagicMock kernel enabled the PRD-020 verifier, fixed in `7c9da46` together with an unbound `requirement_closure_attempts` on early-stop enforce runs); full `.venv/bin/pytest` 5829 passed, 1 failed at `bcac161` (same fixture cause in `tests/test_workflow_controller.py`, fixed in `ff3e6c9`, module re-run 32/0 under pytest); live `tests/test_live_prd020_024_batch5.py` 5/5 passed, evidence in `handover/evidence/BATCH5/user-live/`; demo-03 production-profile run PASS (REQ-4 closed_by_evidence via MUTATION_SCOPE, authorized = actual = DefaultDriverService.java only, all 4 REQs resolved, all terminal gates passed, 53/53 tests, verifier 1 call 3.19s 1685/86 tokens, baseline source captured), evidence in `handover/evidence/BATCH5/demo03-production/`. Doctor `--production` was PRODUCTION_READY=false only because fallback qwen3.6:35b-a3b-q4_K_M is NOT_QUALIFIED (hidden reasoning exhausts case budgets); the run never needed it.

## Source identity
- Base revision: PRD-023.
- Branch: `milestone-decomposition`, local and unpushed.

## Scope
Brownfield PRE/POST full-regression `auto` policy (instruction: `tasks/PRD-024_Brownfield_PRE_POST_Full-Regression_Auto_Policy.md`).

New module: `kriya/workflow/baseline_policy.py`. `kriya/workflow/validation_baseline.py` stays a pure comparison library.

| Requirement | Implementation |
|---|---|
| 1. Deterministic `auto` trigger | `decide_auto_baseline(route, planned_files, workspace, workspace_revision=)`, from signals Kriya already computed; no model. **Preconditions (all):** a brownfield route (task/enhancement/refactor); a git workspace identity; an existing test suite; a planned change to an existing non-test file. **Then any risk signal:** max observed risk ≥ MEDIUM; execution weight not LIGHT; a refactor; a risky impact component (public contract, dependency, build system, persistence, security boundary, shared entry point); or a changed source an existing test names. A LOW, LIGHT change to untested source does not pay for a full baseline. `effective_baseline_policy` turns `auto` into `required`/`disabled`; `required`/`disabled` are unchanged. The decision is a `validation_baseline.policy` run event with its reasons and signals. |
| 2. PRE baseline once, against pristine source, before the first mutation | Unchanged capture point: the same `capture_brownfield_baselines` call, now with the effective policy, before the sandbox and the first Developer call. Tested: a triggered baseline whose runner crashes stops the run with `baseline_indeterminate` naming the trigger reasons, and the Developer is never called. |
| 3. POST in a comparable environment; persist the identity | `baseline_environment_identity` records execution mode (contained/sandbox/host) plus the PRD-011 toolchain fingerprint (attested only for contained execution; otherwise its UNAVAILABLE basis is recorded as such). It is the PRE invocation's `environment_fingerprint`, persisted in the baseline and so in checkpoints, and part of reuse matching. PRE and POST are computed the same way: over the workspace's own toolchain declarations, POST overlaid with the candidate's. A dependency added to `pom.xml` is therefore the same environment, and only a real toolchain change differs. (Review correction: the first version computed PRE without the overlay, so under contained execution any `pom.xml` edit looked like a different environment. Now tested with a stubbed image digest, and the mutation is caught.) When they differ, `classify_baseline_delta(post_environment=)` returns NOT_COMPARABLE: no failure is excused as pre-existing, so any POST failure blocks, and a fully passing suite passes. An authorized PRD-011 toolchain migration under production's sealed `required` is therefore not refused merely for changing the toolchain. |
| 4. PRE_EXISTING / NEW / CHANGED / FIXED / NOT_COMPARABLE, aggregate and per test | Existing classifier, now also run under a triggered `auto`: level 1 is the whole invocation; level 2 is per test when a parser recognizes the output. RESOLVED_FAILURE is FIXED (documented). |
| 5. Blocking rules | NEW_FAILURE, CHANGED_FAILURE, an unexplained disappearance, an infrastructure failure, and now a not-comparable environment all block. PRE_EXISTING_FAILURE alone does not: the failure is not attributed to Kriya. A per-test NOT_COMPARABLE, i.e. a test that cannot be told apart from a new one, does not block by itself, since the whole-invocation comparison already decides. |
| 6. Parser/tool failure is explicit | A validator that raises or returns nothing is `baseline_indeterminate`, never "no failures". This already held; it is tested again through a triggered `auto`. New: `BaselineDeltaResult.level2_available`/`level2_unavailable_reason` state when per-test comparison was impossible, e.g. a Maven failure with no per-test parser, and appear in the delta event, so an empty per-test result is never read as "no per-test failures". |

## Decisions to review
1. **`auto` needs the engineering route.** With engineering triage disabled there is no route signal, and `auto` stays `disabled`. The packaged config enables triage; `AppConfig()` defaults it off, so the mocked suites keep their behaviour.
2. **A triggered `auto` is as binding as `required`**, including stopping before generation when the baseline cannot be captured. The trigger's preconditions (git identity, existing tests) exclude the cases where capture could never work.
3. **A toolchain change makes PRE and POST not comparable.** Nothing is excused as pre-existing across two toolchains: any failure blocks, but a green suite passes. This keeps PRD-011 migrations possible under production's sealed `required`.
4. **Trigger breadth - RESOLVED by the final review (see below).** Was: "A changed source that an existing test names" triggers on its own. In a well-tested repository nearly every brownfield change, even a LOW/LIGHT docstring edit, therefore pays one full PRE suite run (the live case shows exactly that), and milestone units and enforce subtasks pay it per unit. The PRD warns against a full baseline for every trivial task. If that cost is unwanted, drop this signal and keep risk, weight, refactor and impact, which is a one-line change.
5. **Production stays sealed to `required`**, which is stricter than `auto`.

## Tests (plain runner; you run pytest)
`tests/test_prd024_baseline_auto_policy.py`, 21 passed:
- the trigger matrix (10 cases);
- no tests never triggers; explicit policies unchanged;
- pre-existing vs changed vs fixed (per-test honesty when PRE already failed);
- a different environment is NOT_COMPARABLE: it blocks a failing POST, and a green POST passes;
- under contained execution (stubbed image digest) a dependency-only `pom.xml` edit keeps the environment, and a Java release change alters it;
- unavailable per-test comparison is stated;
- end to end through `WorkflowEngine` on a git repository:
  - a known pre-existing failing test is not misread as a regression (`auto` → `required`, PRE_EXISTING_FAILURE, run passes, PRE and POST environments recorded and equal);
  - a new failure still blocks;
  - a required baseline that cannot be captured stops before generation;
  - a trivial change does not pay for a full baseline.

Mutation checks, each caught:
- `auto` always disabled;
- the environment check dropped;
- the tested-source signal dropped;
- the no-tests precondition dropped;
- the POST environment not computed;
- level-2 availability always true;
- PRE computed without the declaration overlay (the contained test fails);
- the risk signal dropped.

Regression, plain runner:
- `test_validation_baseline` 54/0;
- `test_regression_attribution` 8/0;
- `test_d1_operation_mode_authority` 23/0 (same as the pre-batch revision);
- `test_config` 31/0;
- `test_prd008_resume_fingerprints` 97/1 (baseline).

## Live test
`tests/test_live_prd020_024_batch5.py::test_prd024_pre_existing_failure_is_not_a_new_regression`: a real small brownfield Python repository with one known pre-existing failing test, and a real local model making an unrelated change. It proves the pre-existing failure is not blamed on the change.

## Final review corrections (before pytest/live verification)
1. **Trigger.** An existing test naming a changed source is supporting evidence (`signals.supporting.tested_changed_sources`, and a "supporting:" reason when a real signal triggers), never a trigger by itself. The trigger needs a deterministic regression-risk signal, mapped onto what triage already computes:
   - API/contract → `public_contract_change`;
   - build/dependency/toolchain → `build_system_change`, `dependency_change`;
   - runtime/config → `configuration_change` (newly counted);
   - shared owner → `shared_entrypoint_change`;
   - persistence and security → as before;
   - existing high-risk classification → risk ≥ MEDIUM, a non-LIGHT weight, a refactor;
   - broad existing-code change → **new**: `BROAD_EXISTING_CHANGE_FILES` (3) or more existing sources.

   Cross-module consumers stay folded into triage's own risk class; no second signal was added for them.
2. **Reuse.** The applied candidate's terminal full-suite result is kept on the engine (`full_suite_evidence_for_reuse`), keyed by the workspace content hash *after* apply. That is the next run's exact starting state, and the same `compute_workspace_content_hash` the PRE capture compares. It is offered to `capture_brownfield_baselines(prior_full_regression=)` and reused by the existing `is_baseline_reusable` rule. It must match:
   - the content hash;
   - the command and selection (the full suite only);
   - the environment identity, which now also carries `verification_policy_identity`: the validator's containment/sandbox/limits/egress settings and `BASELINE_COMPARISON_VERSION`.

   A prior run that did not complete is never reused; resume keeps its own rule. It works under `required` too, so a production milestone plan pays M1's PRE plus one POST per unit, not two runs per unit. Engine-scoped (in memory), not persisted in the workspace, so a repository cannot ship forged "pre-existing failure" evidence. Recorded as `validation_baseline.full_regression_source` (`captured`/`resume`/`prior_full_suite`).
3. **Reuse assumption (disclosed).** The terminal full-suite run happens in the sandbox; the evidence is keyed by the workspace content *after* apply. That is exact as long as nothing test-relevant is in the sandbox without being applied:
   - run artifacts are removed after each attempt (`clean_untracked_files_since`);
   - every file the Developer wrote in any attempt stays in `all_files_written` and is applied.

   Untracked build or test junk is either ignored by the repository's `.gitignore` (and so outside both hashes), or makes the next run's starting hash differ, which only prevents reuse.
4. **Environment identity change (disclosed).** Adding the verification policy changes the environment string. A baseline checkpointed before this change is therefore captured again on resume, once (the fail-safe direction).
5. **Live test.** The PRD-024 live case uses a docstring goal, so under the new trigger it no longer takes a baseline. It now runs under `required` (what production seals) to prove pre-existing-failure handling. A new live case records how `auto` handles a docstring edit: disabled, or triggered only by a real non-supporting signal from the real route.

Tests (plain runner): `test_prd024_baseline_auto_policy.py`, 35 passed, 14 of them new or changed:
- trigger cases: API, config, shared owner, broad, and tested-source-alone (not triggered);
- supporting evidence recorded;
- exact prior result reused, not rerun;
- a changed workspace, environment or selection is not reused, nor is an incomplete run;
- the policy is part of the environment identity;
- two consecutive engine runs: one reuse;
- a changed workspace or policy between runs: captured again;
- the real CLI milestone driver under `required`: `captured`, then `prior_full_suite` twice, with 4 full-suite runs in total.

Mutations, each caught: tested source triggering again, reuse disabled, policy dropped from the environment, an incomplete prior reused, the broad signal dropped, `configuration_change` dropped.
