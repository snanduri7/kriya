# 05 - Integration / bypass map

Canonical runtime baseline: commit `3eded93c0cfb50f8855d04510e3f9aa2cd5208f7`, tree
`97a3da6b2ef664e2b7e25c4c6fe3fc3f42f253a0`. AS-IS.

A "bypass" here is any route by which work proceeds without passing through the owner a reader would expect. A row
records the route, whether it is bounded, and by what. A route being listed does not make it a defect; the gap
ledger says which ones are.

Routes required by the closure instruction and the slice that maps each:

| Route | Slice | Status |
|---|---|---|
| Ordinary Developer retry | A1 | below |
| Owner recovery | A1 (accounting), A3 (apply) | A1 part below |
| Targeted-test self-correction | A1 (accounting), A3 | A1 part below |
| Generated-test rewrite | A2 | pending |
| Replay | A1 (accounting), A3 | A1 part below |
| MCP / tool writes | A5 | pending |
| Checkpoint / resume | A1 | below |
| Fallback model | A1 (accounting), A6 | A1 part below |
| CLI / Desktop approval | A3 | pending |
| Direct / non-enforce execution | A1 (retry), A4 | A1 part below |
| Recovery coordinator | A1 | below |
| Final-result aggregation | A3 | pending |

## Slice A1 - routes around or beside retry accounting

| ID | Route | What it does to retry accounting | Bounded by | Label |
|---|---|---|---|---|
| BYP-A1-01 | **Enforce re-invocation of a unit.** `workflow/enforce_loop.py` calls `_invoke_bounded_subtask` for the first execution of a stage and again for scope revision, owner recovery, dependent reverification, verifier replay and verifier repair. Each call is a new `run_generation_workflow` call | Every call starts a new `GenerationState`: all budgets zero, no open finding, no seen families, attempt number 0. The unit ceiling (`AUTH-A1-07`) applies per call. The blocker that caused the re-invocation is carried as recovery context (required files, a failure identity, a recovery contract block), not as retry state | 3 scope-revision passes per stage; 3 owner-recovery cycles per stage; `len(plan.subtasks) * 3` cycles per run; one owner reopen per verification-only unit; the run's generation clock | `TRACED` |
| BYP-A1-02 | **Three executors, one unit loop.** Direct (`cli.py` -> `run_generation_workflow`), milestone (`workflow/milestones.py`, one call per milestone) and structured enforce (`workflow/enforce_loop.py`, one or more calls per subtask) | The retry loop, the policy and the recorder are the same code in all three. What differs is the number of invocations and therefore how many times budgets start at zero | Per executor | `TRACED` |
| BYP-A1-03 | **Resume.** An operator resumes a direct or milestone unit, or an enforce run | Retry state is not restored (`AUTH-A1-09`, `AUTH-A1-10`). A resumed unit has a full fresh ceiling and the run has a new generation clock. Repeated resume is not counted anywhere. A resumed production enforce run reuses no completion at all unless the plan had already committed (A1b, `AUTH-A1-10`) | Operator action only. No counter of resumes was found (search of the checkpoint and control modules; `UNKNOWN` whether the run record's `resume_decision` annotation is used as one) | `TRACED` |
| BYP-A1-04 | **Best-of-N first attempt.** `workflow/best_of_n.py` | Resets the full-set counter, the family history, the open finding and the progress history for each independent candidate; keeps the attempt number; adds each discarded candidate to the ceiling. Its failures are recorded by the same `handle_attempt_failure` and handed back as already recorded | `autonomy.best_of_n_first_attempt` (default 1, so inert by default); first attempt only; sandbox required | `TRACED` |
| BYP-A1-05 | **Verification-only attempt.** A unit with no write scope and a directly executable verifier runs `gate_orchestrator` `_run_verification_only_attempt` instead of a Developer attempt | Increments the attempt number; a failure goes through the same recorder and charge; `_admit_verification_only_retry` stops before an equivalent retry when nothing can change | The ordinary budgets plus the declared no-progress stop | `TRACED` |
| BYP-A1-06 | **API-contract recovery phase advance.** A verified transition raises `RecoveryPhaseAdvanced` | Not recorded as a failure: no finding, no family, no progress classification. Charged to the recovery counter only, then the loop continues | 3 recovery attempts; the ceiling counts the attempt number | `TRACED` |
| BYP-A1-07 | **In-attempt repair loops.** The targeted-test self-correction tool loop (`workflow/self_correction.py`, entered from `gate_orchestrator` at two sites) makes further model calls inside one attempt | Not charged to `RetryBudgets` and not an attempt. Skipped when a source review guard is active, during a verifier repair, and on a failing run-authored verifier subject | `autonomy.self_correction_loop_enabled` (default off) and `self_correction_loop_max_turns` (default 4) | `TRACED` for the entry conditions and bounds; the loop body was not read in A1 |
| BYP-A1-08 | **Fallback model.** A fallback-targeted attempt and an escalated full-set attempt run on another model | Stays inside the same accounting: one-shot flag, `fallback_attempts_used`, reserved allowance in the ceiling. A fallback proven unable to serve a patch-only attempt is routed around and can end the unit with a typed stop | The policy (`AUTH-A1-05`) | `TRACED` |
| BYP-A1-09 | **Stops that skip the policy.** `conclude_attempt_failure` ends the loop on a plan-scope conflict or a no-progress stop before `decide_for_state` is asked | No accounting effect; the policy would not have been able to continue either way in the scope-conflict case because the unit must hand over | - | `TRACED` |
| BYP-A1-10 | **Exceptions that leave the loop unrecorded.** `RepositoryContentFilterRefused`, `BoundTreeError`, and anything that is not an `Exception` | Not recorded as an attempt failure and not charged | - | `TRACED` |
| BYP-A1-11 | **Verifier semantic ledger.** `workflow/verifier_semantic.py::VerifierSemanticLedger` holds the run's VALID decisions and the per-subject regeneration allowance | A separate allowance beside `RetryBudgets`, memory only, shared by the units of one process run, lost on resume | One regeneration per subject | `TRACED` (owner and persistence only; semantics belong to A2) |

### Slice A1b additions

**BYP-A1-01, characterized.** The call-site census of `_invoke_bounded_subtask` in `workflow/enforce_loop.py` found
ten call sites and ten roles. Budget each role starts with (`TRACED`):

| Role | When | Unit budget | One-attempt by construction? |
|---|---|---|---|
| `planned` | First execution of a stage | Full ceiling | No |
| `dependent_reverification` | Convergence re-entry of a completed stage | Full ceiling | An identical regeneration is the unit's typed one-attempt stop (`reopened_owner`) |
| `plan_scope_recovery` | After a validated scope revision | Full ceiling | No |
| `owner_recovery` | A `MUST_CHANGE` owner group | Full ceiling | No |
| `may_change_owner_recovery` | A `MAY_CHANGE` owner group | Full ceiling | Identical regeneration stops after one attempt |
| `consumer_retry` | The consumer step when no refused candidate can be replayed | Full ceiling | No |
| `verifier_replay` | The consumer step with its carried refused candidate | Full ceiling object, but no Developer call; a failed replay is a declared no-progress stop | Yes |
| `verifier_repair` | After a person authorized it | Full ceiling object; "one Developer answer" | Yes |
| `verification_owner_recovery` | An ancestor of a failed verification-only unit | Full ceiling | Identical regeneration stops after one attempt |
| `verification_rerun` | The verification-only unit after an accepted owner change | Full ceiling object; nothing to generate | Yes in effect |

Arithmetic for one blocker (`MEASURED` invocation count, `TRACED` ceiling; A1b probe P12): a stage whose upstream
requirement is never satisfied runs 1 planned + 3 x (owner recovery + consumer retry) = **7 unit invocations**
(`tests/test_workflow_controller_enforce.py::test_enforce_bounds_recovery_attempts_per_downstream_owner_pair`,
passed on this baseline). The unit ceiling is 10 without a fallback and 11 with one, so up to **77 Developer
attempts** can be spent on one unresolved blocker before the stage stops, plus up to 3 scope-revision passes. The
run-wide cap multiplies by the number of stages. The only bound expressed in time is
`autonomy.generation_time_budget_seconds`, whose default is `null` (`AUTH-A1-08`, `GAP-A1-06`). In practice the
no-progress stop and `RECOVERY_NO_PROGRESS` end most loops much earlier; neither is a bound on a blocker that keeps
producing different bytes or different failure text. `GAP-A1-04`.

| ID | Route | What it does to retry accounting | Bounded by | Label |
|---|---|---|---|---|
| BYP-A1-12 | **The `resume` flag reaches enforce units.** `_invoke_bounded_subtask` forwards the caller's keywords, `resume` and `resume_id` included, to `run_generation_workflow` | A unit of a resumed enforce run looks up the latest stage checkpoint under its workspace authority (`control/checkpoint.py` `_checkpoints_dir` -> `authority_root`) and may reuse a plan, a design or a gate-passed candidate when every fingerprint matches. Retry state is not restored. A1 said this route did not exist; corrected in `AUTH-A1-10` | The fingerprint validation of `AUTH-A1-09` (goal, workspace content, config, authority context, obligation ledger, candidate bytes). Which checkpoint a unit of a fresh plan sandbox finds, and whether a refused checkpoint (`UNCERTAIN_COMMIT_STATE`) can stop a stage, is `NEEDS_CHARACTERIZATION`: it depends on how `authority_root` resolves a plan worktree (slice A5) | `TRACED` for the forwarding (three sites: the controller's `**legacy_kwargs`, the resume block reading `legacy_kwargs.get("resume")`, the forwarding expression); outcome not exercised |
| BYP-A1-13 | **Owner-recovery self-correction.** Before a `MUST_CHANGE` owner without declared verification is re-invoked as a unit, `owner_recovery._attempt_owner_recovery_self_correction` runs the self-correction tool loop on the owner's files with the Developer model | Model calls and file writes outside any unit invocation: no `GenerationState`, no `RetryBudgets`, no attempt number. If it returns a result the governed unit invocation is skipped for that cycle | The recovery cycle that contains it (3 per stage). Its own turn limit and write path were not read in A1b: mapped by slice A5 (mutation authority) and A3 (which gates accept its result) | `TRACED` for the entry condition and placement only |
| BYP-A1-14 | **A unit that stops for a reason that is not a scope conflict.** | No enforce-level retry or recovery; the stage fails and the plan does not commit (`FR-A1-22`, `FR-A1-21`) | - | `TRACED` (call-site census: no re-invocation site is reachable from such a result, except the verification-only no-change stop) |

### Reading of the map for the WS4 question

The unit loop itself has no route that grants a targeted or fallback-targeted allowance other than a new failure
family (`AUTH-A1-03`). The routes that give a blocker more attempts than one unit ceiling are outside the unit:
re-invocation by the enforce loop (`BYP-A1-01`) and resume (`BYP-A1-03`). Both are bounded by something other than
the blocker, and neither knows whether the blocker is the same one.

## Slice A2 - routes around semantic authority

"Semantic authority" here means two different controls: the person's decision about an existing verifier
(`AUTH-A2-07`) and a canonical contract before generation (`AUTH-A2-05`, which does not exist).

| ID | Route | What it bypasses | Bounded by | Label |
|---|---|---|---|---|
| BYP-A2-01 | A run-authored pytest file under a `src/` layout root. pytest reports `src.pkg.test_x.test_y`; the structural parser drops the leading `src/` and declares `pkg.test_x.test_y`; the two never match | The verifier decision: the file is never a subject and no unknown is raised | The unit's retry ceiling; terminal requirement closure still refuses to close a claim on a model-written test | `MEASURED` (P5: real pytest run from the project root, Kriya's JUnit parser, the real derivation). Not measured: the working directory Kriya's own test gate uses |
| BYP-A2-02 | A run-authored test in a language the structural parser has no grammar for (anything but `.py` and `.java`; Kotlin or Groovy tests of a Gradle project produce JUnit reports) | Same | Same | `MEASURED` at the derivation (P7, hand-built report in the documented identity form); no Kotlin run was executed |
| BYP-A2-03 | A test gate failure with no COMPLETE report | Same; nothing executed, so there is no expectation to judge yet. The Developer may rewrite the test and the code under full budgets until a gate executes it; from then on it is a subject | The unit's retry ceiling | `TRACED`. The registry row's "one self-repair only for non-semantic construction errors" is not implemented and was superseded by the owner's correction (`DOC-A2-02`) |
| BYP-A2-04 | Policy `record`: a goal whose every behaviour claim is `AUTHORITY_REQUIRED` runs | A canonical contract before generation | The terminal requirement outcome stays UNVERIFIED; reported success policy belongs to slice A3 | `TRACED` |
| BYP-A2-05 | Policy `block`, a statement closed by a structural closer: "Include a pytest test for it" is `CLOSABLE` by `test_addition`, which proves a test was added, not what it asserts | A canonical contract for the content of that test | The behaviour statement beside it still needs its own authority (P8: the goal as a whole is refused). A goal made only of structural and anchored statements is admitted with a model-written test | `MEASURED` (P8, P10) |
| BYP-A2-06 | Targeted-test self-correction loop in the gate orchestrator | Would rewrite files from a failing test's output | Closed for subjects: `UnitSemantics.autonomous_rewrite_permitted` refuses when the result identifies a failing run-authored verifier or unknown provenance. Open for unmapped tests (`BYP-A2-01`, `BYP-A2-02`) | `TRACED`; pinned by committed tests (not re-run) |
| BYP-A2-07 | Compile-gate self-correction loop | Not guarded by the semantic owner. Its docstring gives the reason: a semantic decision exists only in an attended run, whose executing gates run behind the source review with the micro-loops off (`gate_orchestrator.py`: the loop requires `post_source_review_guard is None`) | The condition at the loop head | `TRACED` for the condition; that every attended run sets the guard is `INFERRED` here and is checked in slice A3 |
| BYP-A2-08 | Owner-recovery self-correction body (`BYP-A1-13`) | Model calls and writes outside any unit, so outside `UnitSemantics` | See `BYP-A1-13` | Carried to slices A3 / A5, unchanged |
| BYP-A2-09 | Direct run (the desktop path) | Not a bypass: a stricter path. No plan, so no regeneration and no production owner; a failing mapped run-authored test always ends the run | - | `MEASURED` (committed tests); `GAP-A2-03` |
