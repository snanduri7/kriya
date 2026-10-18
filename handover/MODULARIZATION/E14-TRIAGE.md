# E14 preflight / triage (orchestrator, 2026-10-18, written after E13's PARTIAL at 7a43961)

Status: PROPOSAL for owner sign-off. Nothing here is implemented. Evidence classes: MEASURED / TRACED / INFERRED / CONFIRMED.
Sources: handover/MODULARIZATION/OPEN-ITEMS.md rows (authoritative text; the descriptions below are shortened) and the E13 report.

## Rules this table applies
1. E14 is a PURE EXTRACTION (owner, 2026-10-18). A defect, a behaviour change, a typing redesign or a test-harness change does not ride in an E14 move commit.
2. Every candidate move is simulated on the all-import graph BEFORE it is written (E13 lesson, OI-67): a move that enlarges the 67-module SCC is not started; it is reported as an OI-67-class decision.
3. An INFERRED possible defect gets a discriminating characterization first. It is then CONFIRMED (fixed in E14-C with its original-symptom test and a mutant) or REFUTED (closed with the evidence). It is never fixed from the inference alone (ENGINEERING_RULES 2, 3).
4. A pre-existing test failure (OI-47, OI-53) is never "fixed" by changing a golden; no existing golden is modified.

## Table

| Item | What it is | Evidence | Owner after E13 | Category | Closing unit | Reason | Required proof |
|---|---|---|---|---|---|---|---|
| OI-50 | helpers that GenerationDeps still reaches back into attempt.py for | MEASURED (call graph in the row) | `generation_service.py` / `fallback_decision.py` | PENDING MOVE | E14 (extraction) | slice text named them; nothing else blocks | comparator equivalence, SCC simulation first, patch-point retargets, gate chain |
| OI-52 | helpers RepairDeps still reaches back into attempt.py for | MEASURED | `repair_orchestrator.py` | PENDING MOVE | E14 (extraction) | same | same |
| OI-54 | runtime seams GateDeps still reaches back into attempt.py for | MEASURED | `gate_orchestrator.py` | PENDING MOVE | E14 (extraction) | same | same |
| OI-57 | runtime-contract helpers RuntimeDeps still reaches back for | MEASURED | `runtime_verification.py` | PENDING MOVE | E14 (extraction only; owner decision 2026-10-18) | same | same |
| OI-58 | E10 items left in attempt.py | MEASURED | `candidate_transaction.py` / `spec_compliance.py` / `decision_evidence.py` | PENDING MOVE | E14 (extraction) | same | same |
| OI-62 | EstablishedProvenance etc. left in workflow_controller.py | MEASURED | `owner_recovery.py` / `enforce_loop.py` | PENDING MOVE | E14 (extraction) | blocked only by the cycle through context_projection: SCC simulation decides | SCC simulation; if it grows the SCC, STOP and report (OI-67 class) |
| OI-46 | AttemptContext stays in attempt.py | MEASURED (moving it grows the SCC) | attempt.py | PLAN DEVIATION | NOT E14: the cycle-removal work (R1/R2 or a later slice) | the move itself is what OI-67 forbids until the cycle shrinks | the SCC after the removal slice |
| OI-48 | StageDeps.engine is the engine itself; facade names resolved once at stage 4 | MEASURED | stage modules | ARCHITECTURE LEFTOVER (Type B) | after E14, a separate small cleanup slice, NOT inside an extraction | removing the back-reference changes call shapes | characterization of the patch-after-stage-4 behaviour first |
| OI-33 | `RunState = Any` in owner modules | MEASURED | kernel owner modules | TYPING | E14 report: accept explicitly, OR one Type B commit with a Protocol | a Protocol touches no behaviour but the owner modules sit near the SCC; recommend ACCEPT (no behaviour or safety gain) | pylint/ruff 0; if changed, import-time and SCC unchanged |
| OI-34 | order-dependent first-import patches; a golden reuses another test's `_enforce` | MEASURED | tests | TEST HAZARD | E14 test-hygiene commit (test-only, no production) | independent of the extraction | the golden scenario still passes with the helper copied into the golden's own module |
| OI-43 | golden embeds interpreter patch version and an untracked build file | MEASURED | tests | TEST HAZARD | E14 test-hygiene commit | no golden byte is changed; isolate the environment in the harness | fresh archive copy at another path passes |
| OI-45 | 17 test files create `./memory/*.db` in the checkout | MEASURED | tests | TEST HAZARD | E14 test-hygiene commit | harness isolation only | before/after listing of the checkout after a run is identical |
| OI-53 | `legacy_lessons_staged` golden depends on `date.today()` | CONFIRMED | harness | TEST HAZARD / known failing | E14 test-hygiene commit: freeze the date in the harness, golden unchanged | the preservation gate carries one known failure every day until this is fixed | the test passes on two different dates (fake clock) and fails if the freeze is removed |
| OI-64 | mutrun sandbox directory name matches the golden run-id pattern | CONFIRMED (TRACED) | my tool `mutrun.py` | TOOL DEFECT (outside the repo) | fix the tool in E14 preflight (campaign directory name) and drop the `--deselect-k` workaround | the product test is right; the sandbox name is the artifact (ENGINEERING_RULES 10) | a campaign with the unmodified tree reports 0 failures without the deselect |
| OI-05 | result `run_id` differs from the RunRecord run id on both paths | INFERRED | kernel | POSSIBLE DEFECT | characterize in preflight; if CONFIRMED -> E14-C | cannot fix from inference | a pin that records both ids on both entry paths, shown against the pre-move bytes |
| OI-06 | legacy `human_rejected` result lacks `failure_category` | INFERRED (E4 MEASURED the shared dict) | kernel | POSSIBLE DEFECT | same | same | same |
| OI-40 | design-stage stops lack `failure_category` / `quality_gates_passed` | INFERRED | stage modules | POSSIBLE DEFECT (same class as OI-06) | same, decided together with OI-06 | one owner decision for the class | same |
| OI-42 | the final review reuses the pre-approval review after a terminal-regression failure | MEASURED by probe | attempt / approval | POSSIBLE DEFECT (behaviour) | E14-C if CONFIRMED | a person approved a review of an earlier candidate: a safety-relevant question | original-symptom reproduction, fix, inverse case, mutant |
| OI-59 | edit of a workspace-only file prepares a write outside the writer's root | MEASURED | `candidate_transaction.py` | POSSIBLE DEFECT (untyped BatchCommitError) | E14-C if CONFIRMED | fail-closed rule: a typed refusal, not an untyped error | the real failure reproduced through the staging loop |
| OI-63 | the legacy-ownership migration never records the milestone sidecar | MEASURED (unreachable branch) | milestone code | POSSIBLE DEFECT | E14-C if CONFIRMED | evidence is omitted from a recorded decision | reproduce through execute_milestones |
| OI-65 | PLAN_REPAIR round 0 regression seeds from a `None` baseline (TypeError) | TRACED; reach INFERRED unreachable | `enforce_loop.py` | POSSIBLE DEFECT | decide in preflight: prove unreachable (pin + document) or E14-C | a guard may be dead code | an attempt to reach it with a real round-0 regression |
| OI-55 | managed-service verification returns early from the candidate gates | MEASURED | `gate_orchestrator.py` | DEFECT | E14-C (owner decision) | ten controls and the old early return as a mutant (owner decision) | per the owner's list |
| OI-56 | `process_exit` provenance says PASS for a FAILED outcome | MEASURED | runtime verification | EVIDENCE DEFECT | E14-C before Q1 (owner decision) if ownership confirms | the GUI displays a wrong word; no decision reads it | goldens attempt_d_* show the pre-fix value; fix changes provenance goldens only by owner approval |
| OI-61 | CLI startup +85 ms vs a91157f | MEASURED | `import kriya.cli` chain | REGRESSION | E14-P (own slice, Type D) | the call-time accessor pattern of 21034e4 | median of >= 9 warm runs <= baseline +3%, else report to the owner |

## Decision needed from the owner
1. Approve this assignment (in particular: OI-46 and OI-48 leave E14; the test-hygiene items become one test-only commit group inside E14; the seven INFERRED/MEASURED possible defects are characterized first and fixed only in E14-C).
2. OI-33: accept explicitly (recommended) or spend a Type B commit.
