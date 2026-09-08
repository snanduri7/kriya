# Kriya Invariant Catalog

R1 Deliverable 1. Derived exclusively from the R1 historical-defect audit (Deliverable 3, closed 2026-09-08, commits `ddcc1ab` + `ba80965`) and the MA8/MA9 correctness mechanisms that audit materially exercised. This is not an architecture document: no invariant below was invented because it sounded desirable, and no topology this codebase has not actually run against (Gradle, Bazel, generated sources, additional languages) is claimed here.

## Field definitions

- **Invariant** — a precise, falsifiable statement.
- **Origin** — the P1–P7 defect, MA8/MA9 rule, or R1 audit finding that established it.
- **Enforcement** — the exact production component(s) enforcing it today, or a plain statement that none exists.
- **Unit Evidence** — test(s) calling the enforcing function directly with hand-built inputs, or `NONE`.
- **Vertical Evidence** — test(s) driving the real orchestration entrypoint (`WorkflowController.execute()`, `run_generation_workflow()`, or the real retry/repair loop) the way the historical defect actually manifested, or `NONE`.
- **Protection Status** — `PROTECTED`, `PARTIALLY_PROTECTED`, or `UNPROTECTED` only.
- **Failure Consequence** — what violating the invariant in production would cause.

A `PROTECTED` status requires evidence sufficient for the *actual failure class* the invariant describes, not merely a passing helper-level unit test. Every entry below was checked against that bar before being marked `PROTECTED` (see per-entry notes where the check mattered).

---

## INV-PLAN-001 — Grounded-reference gap detection is test-source-scoped, not indiscriminate

**Invariant:** A grounded structural reference from a test file to a production artifact is flagged as a missing-producer gap (`MISSING_GROUNDED_PRODUCTION_ARTIFACT`, UNOWNED shape) only when the reference does not already resolve through an owned, in-scope, or preserved relationship, and only when the referencing source is itself a test file. A production-to-production structural reference is never flagged by this check.

**Origin:** P1 — the check originally flagged any reference to an unowned production file regardless of source, a false positive against ordinary brownfield production-to-production references. Fixed `b12e58e`.

**Enforcement:** `find_missing_grounded_production_artifacts()`'s UNOWNED branch, test-source-gated (`kriya/workflow/workflow_controller.py`).

**Unit Evidence:** `test_missing_grounded_production_artifact_flags_the_exact_omitted_file` and the `test_missing_grounded_production_artifact_is_silent_when_*` family (`tests/test_workflow_controller_enforce.py`).

**Vertical Evidence:** `test_enforce_flags_missing_grounded_production_artifact_and_exhausts_on_repair` — real `WorkflowController.execute()`, confirms the positive (genuine-gap) detection case reaches the terminal report through the real loop, not just the direct-call unit case.

**Protection Status:** PROTECTED

**Failure Consequence:** False failure (a legitimate brownfield plan blocked as structurally invalid because an ordinary production-to-production or already-resolved reference is wrongly treated as a missing producer).

---

## INV-RECOVERY-001 — A grounded scope-denial merges into plan surgery, not the unrecoverable circuit breaker

**Invariant:** When `AuthorizedFileWriter` denies a write to a real, existing, already-planned downstream owner outside the writing subtask's own scope, the controller must ground the denial into plan-surgery recovery (merge ownership forward, revalidate, resume) rather than the unrecoverable-scope-denial circuit breaker. This applies identically whether the grounded owner is a test file or a production file — a real, existing test file is not excluded from grounding.

**Origin:** P2 run 1 — a blanket exclusion of every test-file target from grounding sent a legitimate, already-planned cross-subtask dependency (a stale pinned test needing an update) straight to the unrecoverable-scope-denial breaker, killing the run. Fixed `b6685cb`.

**Enforcement:** `_failure_from_validated_scope_denial()` (`kriya/workflow/retry_strategy.py`) grounding into `revise_plan_for_grounded_scope_owner()` (`kriya/workflow/workflow_controller.py`).

**Unit Evidence:** `test_handle_attempt_failure_scope_denial_with_real_existing_test_owner_is_grounded` (`tests/test_workflow.py`) — drives `handle_attempt_failure()` itself with a real `GenerationState`/`AttemptContext`, not `_failure_from_validated_scope_denial()` in isolation.

**Vertical Evidence:** `test_enforce_grounds_stale_pinned_test_scope_denial_and_merges_to_success` (`tests/test_workflow_controller_enforce.py`) — real `WorkflowController.execute()`, confirms the merge actually completes to a real success, not just that grounding sets the right dict.

**Protection Status:** PROTECTED

**Failure Consequence:** False failure (an entire otherwise-recoverable run terminates via the unrecoverable-scope-denial breaker instead of completing).

---

## INV-PLAN-002 — A subtask's sole-provided capability self-satisfies its own per-file requirement, before and after a merge

**Invariant:** A planned artifact's `requires_capabilities` entry naming a capability that the owning subtask is the *sole provider* of is self-satisfied and needs no `requires` edge. This must hold identically after a grounded-owner merge absorbs the artifact into a different subtask — the per-file validator and the merge's own subtask-level `requires` reconciliation must never disagree about which capabilities are self-provided.

**Origin:** P2 run 3 — the per-file check demanded the merged subtask's own `requires` list literally contain a capability it was already the sole provider of, even though the merge's subtask-level reconciliation correctly drops self-provided capabilities from `requires` — an unvalidatable merge for a real, correct plan shape. Fixed `1b6e727`.

**Enforcement:** `validate_plan()`'s per-file capability check (`kriya/workflow/plan_validation.py`), which must stay in sync with `revise_plan_for_grounded_scope_owner()`'s subtask-level reconciliation (`kriya/workflow/workflow_controller.py`).

**Unit Evidence:** `test_planned_artifact_prerequisite_self_satisfied_by_sole_provider_passes`, `test_planned_artifact_prerequisite_ambiguous_provider_is_not_self_satisfied` (`tests/test_plan_validation.py`).

**Vertical Evidence:** `test_enforce_merge_self_satisfies_per_file_requires_capabilities_via_real_validate_plan` (`tests/test_workflow_controller_enforce.py`) — the real, unmocked `validate_plan()` runs inside a real controller-driven merge; checked specifically because the unit test alone (calling `validate_plan()` directly on a hand-built already-merged plan) would not have caught the original defect, which lived in the *interaction* between the per-file check and the merge's own separate reconciliation.

**Protection Status:** PROTECTED

**Failure Consequence:** False failure (a valid grounded-owner merge rejected as unsatisfiable, blocking recovery `INV-RECOVERY-001` was supposed to complete).

---

## INV-PLAN-003 — Schema self-heal never consumes a semantic repair-budget slot

**Invariant:** A structurally self-heal-able Planner output defect — an unambiguous downgrade such as `acceptance_criteria[].method: "test"` with no conflicting `tool_name`, or a missing `execution_method` inferable from `planned_files`/`execution_role` — is corrected silently before any repair-budget-consuming Planner call, and never counts against the fixed 2-repair semantic budget. A genuinely ambiguous shape is left untouched and reaches real repair as before.

**Origin:** P2 runs 5 and 6 — two of three repair attempts were burned on schema noise this mechanism could have silently fixed for free, starving genuine semantic convergence of its own budget. Fixed `922cb60`, `36e8825`.

**Enforcement:** `_self_heal_structured_plan_dict()` (`kriya/agents/contracts.py`), run before the repair loop's own budget counter increments.

**Unit Evidence:** 13 tests in `tests/test_planner_structured_output.py`, including verbatim reconstructions of the real P2 run 5/6 payloads (`test_p2_run5_attempt0_schema_shape_parses_without_a_repair_round`, `test_p2_run5_attempt1_schema_shape_parses_without_a_repair_round`).

**Vertical Evidence:** `test_enforce_schema_self_heal_does_not_consume_a_real_repair_slot` (`tests/test_workflow_controller_enforce.py`) — real, unmocked `parse_planner_structured_output`/`build_engineering_plan_from_planner_output` inside the real enforce loop; asserts `plan_repair_attempts == 0` and exactly one Planner call.

**Protection Status:** PROTECTED

**Failure Consequence:** Retry amplification (real semantic repair attempts wasted on mechanically fixable formatting noise instead of the genuine plan defect).

---

## INV-PLAN-004 — A strict reason-code-set regression is rejected; the retained baseline seeds the next round and the terminal report

**Invariant:** A structured-plan repair round whose reason-code set is a **proper superset** of the retained (last accepted, non-regressed) baseline's reason-code set is rejected as a strict regression. The retained baseline, never the regressed candidate, seeds the next repair prompt and, on exhaustion, the terminal report. Because reason-code sets form only a partial order, two *incomparable* sets (neither a superset nor a subset of each other) are never flagged as a regression by this mechanism alone — see `INV-OBL-001` and `INV-PRESERVE-001` for the separate, obligation-ledger-based layer that exists specifically to catch that residual case.

**Origin:** P2 run 7 — attempt 2 reverted attempt 1's own genuine fix, reintroducing `APPLICATION_RUNTIME_OWNER_MISSING`, and was accepted as the terminal state. Fixed `8cd94ec`.

**Enforcement:** `_is_strict_regression()` (`kriya/workflow/workflow_controller.py`) — Python's own frozenset proper-subset operator, no cardinality/heuristic scoring.

**Unit Evidence:** 5 tests exercising `_is_strict_regression()` directly, including a reproduction of P2 run 7's real 3-attempt reason-code sequence.

**Vertical Evidence:** `test_enforce_rejects_terminal_regression_and_reports_retained_baseline` (`tests/test_workflow_controller_enforce.py`) — full loop-level integration test; confirms `planner.run` is called exactly 3 times (budget unchanged) and the terminal reason codes reflect the retained baseline, not the regressed final attempt.

**Protection Status:** PROTECTED

**Failure Consequence:** False success at the reporting layer (a worse, regressed candidate's state reported as though it were the best reachable state) / repair oscillation.

---

## INV-OBL-001 — A repair round must not silently regress an unrelated, already-SATISFIED semantic contract

**Invariant:** Repairing one structured-plan defect must not regress an already-`SATISFIED` `SUBTASK_SEMANTIC_CONTRACT` obligation (a subtask's `requires` or `provides` entry) unrelated to the repair. A legitimate change to a subtask's own contract is exempted only when that exact subtask is directly implicated by the round's own reported errors.

**Origin:** P7 attempt 2 — a repair round silently dropped an already-validated `requires` entry while fixing an unrelated `PRESERVED_REFERENCE_CONFLICTS_WITH_OWNERSHIP` issue; a later round then dropped a *different* subtask's `provides` while restoring the first — an oscillation invisible to `INV-PLAN-004` alone, since each round's reason-code set differed from the retained baseline's in an incomparable way. Fixed `fabf372`, `f15ebd0`.

**Enforcement:** `ObligationLedger`'s SATISFIED→VIOLATED regression detection (MA8, `kriya/workflow/obligations.py`), read by `_semantic_contract_regression_subtasks()` and OR'd into the same `treat_as_regression` redirect `INV-PLAN-004` drives (`kriya/workflow/workflow_controller.py`). A closing pass in `validate_plan()` re-records VIOLATED against any requires/provides fact silently dropped between revisions (not merely replaced), so a drop is never invisible to the ledger even when the per-round check never re-iterates an emptied field.

**Unit Evidence:** `test_semantic_contract_regression_subtasks_rejects_unimplicated_drop`, `_exempts_implicated_subtask`, `_multiple_subtasks_mixed`, `_ignores_other_obligation_kinds`; `test_requirement_silently_dropped_between_revisions_is_recorded_violated` and its provides-side/still-present counterparts (`tests/test_plan_validation.py`).

**Vertical Evidence:** `test_enforce_reproduces_p7_oscillation_and_rejects_both_silent_drops`, `test_enforce_accepts_targeted_requires_change_for_implicated_subtask`, `test_enforce_accepts_targeted_provides_change_for_implicated_subtask` (`tests/test_workflow_controller_enforce.py`) — real `WorkflowController.execute()`, reproducing the exact real 3-attempt P7 sequence.

**Protection Status:** PROTECTED

**Failure Consequence:** Repair oscillation (the loop alternates between two constraints indefinitely, never converging, while reporting apparent progress).

---

## INV-PRESERVE-001 — A SATISFIED preserved reference must not silently regress to VIOLATED during repair

**Invariant:** A `PRESERVED_REFERENCE` obligation that has become `SATISFIED` must not silently regress to `VIOLATED` during a subsequent Planner repair round, unless the new plan legitimately invalidates that preservation requirement — in which case the round's own reported errors must directly implicate that exact `(source, target)` pair (see `INV-PRESERVE-004`'s convergence case for the paradigm example of a legitimate invalidation).

**Origin:** R1 historical-defect audit / P5 reproduction. `PetTests.java` needed `BaseEntity.java` and `Visit.java` preserved simultaneously; one repair round satisfied only the first, the next satisfied only the second while silently dropping the first — both rounds reported an *identical* single-element reason-code set, invisible to `INV-PLAN-004` alone (equal sets are never a proper subset of each other). This was a genuinely **live, previously undetected defect**, not a coverage gap: `PRESERVED_REFERENCE` had never been extended into the regression-detection mechanism `INV-OBL-001` established for `SUBTASK_SEMANTIC_CONTRACT`. Found and fixed during this audit: `ddcc1ab`.

**Enforcement:** A closing pass in `find_missing_grounded_production_artifacts()` (mirrors `INV-OBL-001`'s own closing pass) re-records VIOLATED against any previously-SATISFIED preservation no longer declared this round. `_preserved_reference_regressions()` / `_preserved_reference_pairs_mentioned()` (`kriya/workflow/workflow_controller.py`) read the resulting `ObligationLedger` regression and OR it into the same `treat_as_regression` redirect `INV-PLAN-004` and `INV-OBL-001` drive. Deliberately a **separate function** from `_semantic_contract_regression_subtasks()`, not a broadened kind filter on it — the real identity here is a `(source, target)` file pair, not a subtask id, and forcing it through subtask identity would be semantically wrong.

**Unit Evidence:** Closing-pass tests `test_preserved_reference_silently_dropped_between_revisions_is_recorded_violated`, `_still_declared_is_not_spuriously_flagged_as_dropped`; direct unit tests for the two new functions — unimplicated drop, implicated-pair exemption, multiple-pairs-mixed, cross-kind isolation, never-satisfied-pair-is-not-a-regression (`tests/test_workflow_controller_enforce.py`).

**Vertical Evidence:** `test_enforce_p5_preserved_reference_oscillation_is_not_silently_accepted`, `test_enforce_p5_preserved_reference_oscillation_inverse_ordering`, `test_enforce_preserved_reference_legitimate_correction_is_not_rejected` (`tests/test_workflow_controller_enforce.py`) — real `WorkflowController.execute()`; empirically confirmed (direct invocation, before this fix existed) that the drop was silently accepted as the new retained baseline prior to the fix.

**Protection Status:** PROTECTED

**Failure Consequence:** Repair oscillation, with a materially higher-severity tail risk than `INV-OBL-001`'s: a silently-lost preservation obligation, if it survived to plan acceptance, could let generation proceed believing a file is protected when the ledger no longer actually tracks it as satisfied — a precursor to unauthorized mutation of a file the goal explicitly required untouched.

---

## INV-PRESERVE-002 — A declared preserved reference suppresses a gap per-source, never per-path

**Invariant:** A grounded structural reference from a test file to an unowned production artifact is not a plan gap when the referencing `PlannedFile` declares that target in `preserved_references`. The declaration is accepted **per source, not per path**: one artifact's correct preservation claim never suppresses a genuine, undeclared gap on the same target from a *different* source. Acceptance also records a SATISFIED `PRESERVED_REFERENCE` obligation carrying the target's real pre-generation content hash.

**Origin:** P2 runs 2–8 — the plan schema had no way to express "referenced, not modified, intentionally," the actual representational gap behind seven non-converging P2 attempts. `preserved_references` field added `cd0434f`.

**Enforcement:** `find_missing_grounded_production_artifacts()`'s PRESERVE branch (`kriya/workflow/workflow_controller.py`).

**Unit Evidence:** `test_missing_grounded_production_artifact_is_silent_when_target_declared_preserved`, `test_missing_grounded_production_artifact_preservation_is_per_source_not_path`, `test_missing_grounded_production_artifact_preserved_reference_to_a_nonexistent_edge_is_inert`, `test_missing_grounded_production_artifact_acceptance_records_satisfied_preserved_reference` (`tests/test_workflow_controller_enforce.py`).

**Vertical Evidence:** `test_enforce_preserved_reference_acceptance_and_terminal_integrity_gate_a_real_run` (`tests/test_workflow_controller_enforce.py`) — real `WorkflowController.execute()` against a real structural-evidence fixture (`_seed_structural_customer_repo`, genuine TEST→CONTROLLER import edge), not a hand-waved edges dict.

**Protection Status:** PROTECTED

**Failure Consequence:** False failure (a legitimate brownfield dependency the goal never asked to change blocks plan acceptance) if under-protected; false success (a genuine gap on an undeclared source silently suppressed) if the per-source scoping regressed — both directions are covered by the per-source unit test above.

---

## INV-PRESERVE-003 — A preserved file's byte-identity is enforced through the terminal gate, not just at plan-acceptance time

**Invariant:** A file accepted as preserved must remain byte-identical to its pre-generation content through the end of the run. Any mutation — even one outside the mutating subtask's own declared write scope — must fail the run via the terminal obligations gate; it must never silently succeed.

**Origin:** MA8/PRV-11 preservation design (P2 run 8, `9ac8202`). No P1–P7 production run ever actually exercised a live mutation of a preserved file in a real run; the negative vertical test below is this R1 audit's own constructed proof of the mechanism, not a historical-incident replay. Recorded explicitly rather than implied, per this catalog's own evidentiary standard.

**Enforcement:** `enforce_preserved_reference_terminal_integrity()` re-hashing every currently-SATISFIED record (`kriya/workflow/workflow_controller.py`), feeding the generic MA8 terminal-aggregation gate (`ObligationLedger.unresolved_terminal_obligations()`) — no separate success/failure gate was hand-wired for this obligation kind.

**Unit Evidence:** `test_enforce_preserved_reference_terminal_integrity_passes_when_target_unchanged`, `test_enforce_preserved_reference_terminal_integrity_flags_a_mutated_target` (`tests/test_workflow_controller_enforce.py`).

**Vertical Evidence:** `test_enforce_preserved_reference_acceptance_and_terminal_integrity_gate_a_real_run` (pass case) and `test_enforce_preserved_reference_terminal_integrity_fails_a_real_run_on_mutation` (fail case) — the negative test specifically confirms a real run's own `status` flips away from `"success"` when a file is mutated **outside** the mutating subtask's own `allowed_write_relpaths`, the exact shape that matters (an in-scope mutation would already be caught by ordinary scope enforcement; this test isolates the terminal gate's own, independent contribution).

**Protection Status:** PROTECTED

**Failure Consequence:** False success (a run that actually broke a promise the goal explicitly required — "do not touch X" — reports success anyway).

---

## INV-PRESERVE-004 — A preserve/modify contradiction is rejected at validation time, and correctable within budget

**Invariant:** A source declaring a target as preserved while that same target is planned for modification (by any subtask, including itself) is a plan-authoring contradiction and must be rejected at validation time, never silently resolved by favoring one claim. When given this rejection as repair guidance, the Planner must be able to converge on removing the wrong declaration within the normal 2-repair budget.

**Origin:** P6 run 1 — the reason code existed with zero dedicated repair guidance. Fixed `e073d47`.

**Enforcement:** `validate_plan()`'s conflict check against the real `file_owners` map (`kriya/workflow/plan_validation.py`), plus its targeted-correction repair guidance.

**Unit Evidence:** Conflict-accepted/conflict-rejected pair in `tests/test_plan_validation.py`; `test_preserved_reference_conflict_repair_guidance_names_the_conflict` (`tests/test_workflow_controller_enforce.py`).

**Vertical Evidence:** `test_enforce_preserved_reference_legitimate_correction_is_not_rejected` (`tests/test_workflow_controller_enforce.py`) — real `WorkflowController.execute()`; the same test also proves `INV-PRESERVE-001`'s implicated-pair exemption correctly recognizes this exact correction as legitimate rather than a silent, unrelated drop.

**Protection Status:** PROTECTED

**Failure Consequence:** Repair non-convergence if the rejection has no guidance (the historical defect); unauthorized mutation if the contradiction were ever silently resolved in favor of the modify claim instead of rejected.

---

## INV-ATTR-001 — A test's own fixture/precondition failure attributes to the test, not the production guard it happened to trip

**Invariant:** A test failure caused by the test's own unstubbed fixture/precondition triggering an existing, unmodified production guard must attribute repair ownership to the test file, not the production file the exception happened to surface in. The check operates at enclosing-method granularity against the pre-generation baseline (`state.all_original_contents`) — a genuinely new, freshly-written line throwing is real evidence of a defect in new code and is never suppressed by this check.

**Origin:** P1 — an unstubbed mock made an existing production guard throw during test fixture setup; the plain locator tier misattributed the failure to the production file, dispatching a repair that could never satisfy a missing test-fixture stub. Fixed `5c4dff4`.

**Enforcement:** `attribute_failure()`'s fixture-precondition check (`kriya/workflow/attribution.py`), invoked from `handle_attempt_failure()` (`kriya/workflow/retry_strategy.py`).

**Unit Evidence:** 5 tests in `tests/test_attribution.py`, including the opt-in-without-`original_contents` backward-compatibility case and the newly-written-line fall-through case.

**Vertical Evidence:** `test_handle_attempt_failure_redirects_fixture_precondition_to_test_not_production` (`tests/test_workflow.py`) — drives `handle_attempt_failure()` itself (the real P1 caller), not `attribute_failure()` directly; checked specifically because the unit-only coverage proved the predicate correct without ever proving the real caller assembles `known_attribution_files`/`original_contents` in the exact shape the predicate expects.

**Protection Status:** PROTECTED

**Failure Consequence:** Incorrect attribution (repair effort spent editing correct production code to satisfy a test's own fixture gap, which can never converge by construction).

---

## INV-GOAL-001 — Explicit runtime-verification negation in goal text wins unconditionally

**Invariant:** A goal that explicitly denies runtime verification is needed ("no ... run ... is required", "do not start/launch/execute", "execution is not required") must never be classified as requiring application-runtime verification — even when the same goal also contains ordinary prose using runtime-sounding bare words (`run`, `get`, `put`) with no execution context. Negation is checked first, over the whole goal text, and wins unconditionally.

**Origin:** P3 attempt 1 — the goal's own sentence explicitly *denying* runtime verification was the literal text that triggered Kriya's demand for it. The same underlying defect silently explains P2 run 4's and P2 run 8-attempt-0's own earlier `APPLICATION_RUNTIME_OWNER_MISSING` recurrences (traced retroactively once found). Fixed `4768db1`.

**Enforcement:** `goal_requires_runtime_behavior()` (`kriya/workflow/acceptance.py`), consumed as `validate_plan()`'s `runtime_verification_required` kwarg.

**Unit Evidence:** `tests/test_acceptance.py`'s `test_runtime_behavior_*` family (7 tests), using the real, verbatim false-positive sentences copied from P2's and P3's own frozen `goal.md` files.

**Vertical Evidence:** `test_enforce_wires_goal_text_runtime_negation_to_validate_plan_as_false` (`tests/test_workflow_controller_enforce.py`) — real `WorkflowController.execute()`; captures the actual `runtime_verification_required` kwarg reaching a real `validate_plan()` call for a real (unmocked) goal string, forcing `ChangeKind.TASK` routing and asserting the spy was actually invoked rather than skipped.

**Protection Status:** PROTECTED

**Failure Consequence:** False failure (a plan whose goal explicitly needs no runtime verification is blocked pending an unsatisfiable `APPLICATION_RUNTIME_OWNER_MISSING` requirement).

---

## INV-GOAL-002 — Response-owner scope widening requires positive, unnegated mutation intent

**Invariant:** An existing file that syntactically matches response-construction/owner heuristics (a naming pattern plus construction-call syntax) must not be widened into write scope unless the goal expresses positive, unnegated mutation intent toward a response/payload/endpoint shape. Vocabulary overlap alone, or an explicit preservation statement, is not sufficient — mirrors `INV-GOAL-001`'s own negation-first design for a different scope-widening decision.

**Origin:** P4 — a bare "response" mention anywhere in the goal, with no polarity awareness, activated this scope-widening heuristic. Fixed `3b4e4ec`.

**Enforcement:** `_goal_expresses_positive_response_mutation_intent()` / `discover_response_construction_owners()` / `include_response_construction_owners()` (`kriya/workflow/file_resolution.py`), consumed in `run_generation_workflow()` (`kriya/workflow/workflow.py`).

**Unit Evidence:** 6 tests in `tests/test_workflow.py`'s response-construction-owner family.

**Vertical Evidence:** `test_response_construction_owner_false_positive_never_reaches_architect_files` (`tests/test_workflow.py`) — real `run_generation_workflow()`; spies on the real function call with the real goal string and confirms it was actually invoked (not skipped because `engineering_route.kind` fell outside `(TASK, ENHANCEMENT)`), then confirms the false-positive file is never in `architect_files` and stays byte-identical on disk.

**Protection Status:** PROTECTED

**Failure Consequence:** Unauthorized mutation (write scope silently widened onto an existing file the goal never asked to change).

---

## INV-PLAN-005 — Java interface declarations are indexed as resolvable structural symbols

**Invariant:** Java `interface` declarations must be indexed as resolvable structural-evidence symbols on par with `class` declarations, so a cross-module edge that resolves through an interface boundary (a port/adapter pattern) is not silently invisible to planning-structural-evidence.

**Origin:** P7 preflight — a real multi-module Maven repo's port/adapter boundary was invisible to structural evidence because the symbol-indexing regex matched only the `class` keyword. Fixed `40ee290`.

**Enforcement:** The class/interface declaration regex in `build_planning_structural_evidence()` (`kriya/workflow/workflow_controller.py`).

**Unit Evidence:** 5 tests exercising interface resolution directly.

**Vertical Evidence:** `test_p7_reproduction_structural_evidence_resolves_interface_module_boundary` (`tests/test_workflow_controller_enforce.py`) — real `build_planning_structural_evidence()` against a real multi-module fixture.

**Protection Status:** PROTECTED

**Failure Consequence:** Incorrect attribution / false failure (a genuine cross-module dependency invisible to structural evidence produces a spurious `MISSING_GROUNDED_PRODUCTION_ARTIFACT`-family gap, or ungrounds ordering that should have been enforced).

---

## INV-BUILD-001 — Compile evidence is evaluated against the owning declared module, not the reactor root

**Invariant:** In a genuine multi-module Maven reactor, a compile-check must verify the *owning module's own* `target/classes` output before declaring compilation successful. A reactor aggregator module (`packaging=pom`, no source of its own) must not be required to produce `target/classes`, and a compile pass at the reactor root must not be accepted as proof that a specific owning module actually compiled.

**Origin:** P7 attempt 1 — the compile-check's single-module assumption checked only the reactor root's `target/classes`, producing a false-positive compile pass that drove 14 wasted Developer attempts (~78.6 minutes) before the mismatch surfaced. Fixed `13a73b0`, `61bf96a`.

**Enforcement:** `get_pom_reactor_modules()` / `_java_reactor_modules_missing_compiled_output()`, inside `PolymorphicValidator.run_compile_check()` (`kriya/tools/validate.py`).

**Unit Evidence:** 7 tests calling `validator.run_compile_check()` directly, only `subprocess.Popen` mocked, including `test_reactor_compile_check_succeeds_when_owning_modules_have_real_classes`, `test_reactor_compile_check_fails_when_owning_module_produced_no_classes`, and `test_reactor_compile_check_scans_every_owning_module_not_just_the_first` (`tests/test_polymorphic_validation.py`) for the cross-module-ownership cases.

**Vertical Evidence:** The same 7 tests, reclassified deliberately, not by default: `run_compile_check()` is the real, public entrypoint attempt.py's own compile gate calls directly, and the defect was fully contained inside this one function with no separate orchestration layer above it that could diverge from it — there is no additional boundary a "more vertical" test could cross for this specific invariant. Verified against Step 5's own bar before accepting this: attempt.py's compile-gate call site was inspected directly and confirmed to call `run_compile_check()` with no intermediate transformation of its result relevant to this invariant.

**Protection Status:** PROTECTED

**Failure Consequence:** False success (compilation reported passing when the actually-owning module never compiled), the single highest-cost failure class found in the entire P1–P7 audit by wall-clock impact.

---

## INV-RUNTIME-001 — A managed service is not launched before its packaged artifact exists

**Invariant:** A `managed_service` verification whose `service_command` targets a packaged artifact (e.g. a `-jar` launch) must not attempt to launch before that artifact is actually built/packaged; a PREPARE phase must run first.

**Origin:** P6 run 1 — the service was launched before its jar existed. This failed loud (`SERVICE_EXITED_BEFORE_READY`), not silently, but blocked an otherwise-correct run. Fixed `e790c1a`, `013d986`.

**Enforcement:** The managed-service PREPARE phase preceding launch (`kriya/workflow/workflow.py` / `kriya/workflow/attempt.py`).

**Unit Evidence:** 13 tests covering the PREPARE-phase contract, including the two new `ServiceVerificationOutcomeKind` members added by this fix's own completeness test.

**Vertical Evidence:** `test_p6_reproduction_missing_jar_is_prepared_then_service_launches_and_probes` (`tests/test_service_runtime.py`) — real `run_managed_service_verification`, only the OS process boundary mocked.

**Protection Status:** PROTECTED

**Failure Consequence:** False failure (an otherwise-correct run blocked by an avoidable launch-ordering defect).

---

## INV-RUNTIME-002 — A test-typed runtime-verification pass satisfies a judgment/runtime requirement only with real execution evidence

**Invariant:** A runtime-verification pass whose concrete command happens to be a test-runner invocation (tagged `type="test"`, not `"run_verification"`) must still satisfy a `judgment`/`requires_runtime_execution=True` requirement, **provided** it carries evidence of having actually gone through real runtime-verification execution (the `"commands"` marker, set only by `run_app_sequence()`'s own two success-path appends). An ordinary compile/test Quality Gate outcome sharing the same `type="test"` tag, produced by the unrelated ordinary test-execution path, must **not** satisfy it.

**Origin:** P2 run 4 — the auto-approved command was `mvn test`; it ran via `run_app_sequence()` and passed, and Kriya's own generated success criteria already declared that sufficient, yet the requirement still reported `REQUIRED VERIFICATION UNRESOLVED` because the evidence lookup only ever searched `type="run_verification"`. Fixed `2441419`.

**Enforcement:** `_build_required_verification_evidence()` (`kriya/workflow/workflow.py`), consuming real gate outcomes appended by `_execute_runtime_verification_directly()` (`kriya/workflow/attempt.py`), which calls `PolymorphicValidator.run_app_sequence()`.

**Unit Evidence:** `test_test_command_run_verification_satisfies_judgment_runtime_requirement`, `test_ordinary_test_gate_outcome_does_not_satisfy_judgment_runtime_requirement` (`tests/test_workflow.py`).

**Vertical Evidence:** `test_real_run_app_sequence_test_outcome_satisfies_judgment_runtime_requirement` (positive) drives the real producer (`_execute_runtime_verification_directly` → real `PolymorphicValidator.run_app_sequence` → a real spawned subprocess) into the real consumer, with only `RunVerifierAgent.judge` (model judgment) and the spawned process's own identity controlled. `test_real_ordinary_test_gate_outcome_does_not_satisfy_judgment_runtime_requirement` (negative safety property) drives a *second*, genuinely different real producer (`PolymorphicValidator.run_tests()`, never `run_app_sequence()`) and confirms its real output — appended in the exact real shape `attempt.py` uses — still does not satisfy the requirement (`tests/test_workflow.py`).

**Protection Status:** PROTECTED

**Failure Consequence:** Runtime-verification loss (real, already-produced evidence of correct application behavior is discarded, and the run reports an unresolved requirement despite the work having genuinely been done) if under-matched; false success (an unrelated ordinary test run mistaken for runtime verification) if over-matched — both directions are covered by the positive/negative pair above.

---

## INV-RETRY-001 — A candidate-independent deterministic failure terminates the retry loop instead of exhausting the attempt budget

**Invariant:** A deterministic verification failure that recurs across attempts targeting materially different candidates — different workspace content, the same underlying failure signature reproduced against a clean baseline replay — must terminate the retry loop rather than exhaust the full Developer attempt budget. Byte-identity-based no-progress detection (MA9's existing `record_workspace_progress`) is structurally unable to catch this by itself, since "different content, same deterministic failure" is, by that metric alone, workspace progress. No single piece of evidence (a workspace-hash change, or the absence of implicated files) may independently cause termination — only the full trigger conjunction may: a deterministic gate, a repeated normalized failure signature, a materially different candidate, absent/insufficient attribution, and a safe isolated baseline replay that actually reproduces the same failure.

**Origin:** P7 attempt 1's efficiency finding — 14 wasted Developer attempts (~78.6 minutes) traced to an architecture gap, not a bug in an existing mechanism. The new mechanism's own first implementation had a real defect (comparing a wrapped exception string against an unwrapped baseline string, so the two signatures almost never matched), found only once a genuine end-to-end test was written — 17 mocked-input unit tests had already passed trivially against it. Fixed `3a72b61`, `92c63ef`.

**Enforcement:** `evaluate_candidate_independent_failure()` / `DeterministicFailureDiagnosticStore` (`kriya/workflow/deterministic_failure_diagnostic.py`), wired into `handle_attempt_failure()` (`kriya/workflow/retry_strategy.py`) via the existing generic `state.environment_failure`/`RetryAction.STOP_ENVIRONMENT` terminal-control mechanism — reused, not modified. `record_workspace_progress()`'s own semantics are explicitly untouched; this is an additive, independently-gated layer beside it.

**Unit Evidence:** 19 tests in `tests/test_deterministic_failure_diagnostic.py` — orchestration (baseline reproduces/succeeds, different signatures, implicated files, byte-identical workspace, broken baseline, retry-family-switch and plan-scope-recovery persistence, cross-subtask non-contamination, bounded fail-types), `_copy_baseline_workspace` exclusion, and 4 `handle_attempt_failure`-level wiring tests.

**Vertical Evidence:** `test_real_retry_loop_stops_before_a_third_developer_call` — a genuine end-to-end test using `WorkflowEngine(kernel, llm)` with mocked `PolymorphicValidator.run_compile_check`/`developer.run_generation`, asserting the retry loop actually stops (`run_generation.call_count == 2`), not merely that the classifier function returns the right enum value. This is the test that found the wrapping-comparison defect described in Origin above, before it shipped.

**Protection Status:** PROTECTED

**Failure Consequence:** Retry amplification (wasted Developer attempts, real wall-clock/API cost, against a failure no further code regeneration could ever fix).

---

## INV-PLAN-006 — Every structured-plan reason code is classified into a known repair-guidance bucket

**Invariant:** Every reason code the structured-plan repair loop can emit must be classified into exactly one of four known buckets — has targeted correction text, is terminal/non-repairable by design, is adequately covered by the always-present generic correction rules, or is a known gap pending audit. An unclassified reason code must never silently reach the Planner with no guidance and no record that it was overlooked.

**Origin:** Proactive Repair Guidance audit, pre-P7 (2026-09-07) — closed every reason-code gap found at that time; `6e48cea`. This mechanism later caught `INV-PRESERVE-001`'s own new `PRESERVED_REFERENCE_REGRESSION_REJECTED` code as unclassified before it could ship silently — direct evidence the enforcement described below actually works, not just a design claim.

**Enforcement:** **None at runtime.** Stated plainly rather than implied: the guarantee is enforced entirely by a test-time completeness scan. Any reason code appearing in the real source (`kriya/workflow/workflow_controller.py`, `kriya/workflow/plan_validation.py`) that is not present in one of the four classification sets fails collection/assertion immediately. This is a structurally exhaustive, deterministic check over the codebase's own reason-code inventory — not a sampled runtime guard — and is recorded as a genuinely different enforcement shape from every other entry in this catalog rather than described as protected by a production mechanism that does not exist.

**Unit Evidence:** `test_every_structured_plan_reason_code_is_classified`, plus the bucket-specific verification tests confirming `_CODES_WITH_TARGETED_GUIDANCE` codes actually produce guidance text and `_CODES_ADEQUATE_VIA_GENERIC_CORRECTION_RULES` codes are still adequate, not stale (`tests/test_workflow_controller_enforce.py`).

**Vertical Evidence:** NONE — not applicable. There is no runtime call path to drive; the scan itself is the whole mechanism, exercised at test-collection time against the real source, not through a simulated run.

**Protection Status:** PROTECTED

**Failure Consequence:** Incorrect attribution / repair oscillation (an unclassified reason code reaches the Planner with zero guidance, producing effectively unguided, non-converging repair attempts) if this scan were ever removed or bypassed.

---

## Summary matrix

| Invariant | Domain | Origin | Unit | Vertical | Status |
|---|---|---|---|---|---|
| INV-PLAN-001 | Planning | P1 | Yes | Yes | PROTECTED |
| INV-RECOVERY-001 | Recovery | P2 run 1 | Yes | Yes | PROTECTED |
| INV-PLAN-002 | Planning | P2 run 3 | Yes | Yes | PROTECTED |
| INV-PLAN-003 | Planning | P2 runs 5-6 | Yes | Yes | PROTECTED |
| INV-PLAN-004 | Planning | P2 run 7 | Yes | Yes | PROTECTED |
| INV-OBL-001 | Obligation lifecycle | P7 attempt 2 | Yes | Yes | PROTECTED |
| INV-PRESERVE-001 | Preservation | R1 audit / P5 | Yes | Yes | PROTECTED |
| INV-PRESERVE-002 | Preservation | P2 runs 2-8 | Yes | Yes | PROTECTED |
| INV-PRESERVE-003 | Preservation | MA8/P2 run 8 | Yes | Yes | PROTECTED |
| INV-PRESERVE-004 | Preservation | P6 run 1 | Yes | Yes | PROTECTED |
| INV-ATTR-001 | Attribution | P1 | Yes | Yes | PROTECTED |
| INV-GOAL-001 | Goal-intent | P3 attempt 1 | Yes | Yes | PROTECTED |
| INV-GOAL-002 | Goal-intent | P4 | Yes | Yes | PROTECTED |
| INV-PLAN-005 | Planning | P7 preflight | Yes | Yes | PROTECTED |
| INV-BUILD-001 | Build verification | P7 attempt 1 | Yes | Yes | PROTECTED |
| INV-RUNTIME-001 | Runtime verification | P6 run 1 | Yes | Yes | PROTECTED |
| INV-RUNTIME-002 | Runtime verification | P2 run 4 | Yes | Yes | PROTECTED |
| INV-RETRY-001 | Retry/correctability | P7 attempt 1 | Yes | Yes | PROTECTED |
| INV-PLAN-006 | Planning (meta) | Pre-P7 audit | Yes | NONE (n/a) | PROTECTED |

**Totals:**
- Total invariants: **19**
- PROTECTED: **19**
- PARTIALLY_PROTECTED: **0**
- UNPROTECTED: **0**
- Invariants with vertical evidence: **18**
- Invariants without vertical evidence: **1** (`INV-PLAN-006` — a test-time completeness scan with no runtime call path to drive; recorded as `NONE` rather than force-fit to a fabricated vertical test, and still `PROTECTED` because the scan itself is exhaustive over its own failure class)

This 19/19-PROTECTED outcome is a direct, mechanical consequence of R1 Deliverable 3 closing every coverage gap it found before this catalog was written — not an assumption made in this document. No new test was written and no production code was touched to produce this result; this catalog is a read of the already-committed, already-pytest-confirmed state (`ddcc1ab`, `ba80965`).

**Accounting note (unchanged from the audit's own record):** R1 required one real Kriya production fix (`INV-PRESERVE-001`, `ddcc1ab`), so this work does not start the no-Kriya-fix maturity streak. The next clean production-validation task after the finalized R1 baseline is still where that streak begins.
