# PRD-024 Coding Agent Handover

## Status
READY_FOR_PYTEST_VERIFICATION (batch 5: PRD-020 to PRD-024, one pytest stop for the whole batch).

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
4. **Trigger breadth (please decide).** "A changed source that an existing test names" triggers on its own. In a well-tested repository nearly every brownfield change, even a LOW/LIGHT docstring edit, therefore pays one full PRE suite run (the live case shows exactly that), and milestone units and enforce subtasks pay it per unit. The PRD warns against a full baseline for every trivial task. If that cost is unwanted, drop this signal and keep risk, weight, refactor and impact, which is a one-line change.
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
