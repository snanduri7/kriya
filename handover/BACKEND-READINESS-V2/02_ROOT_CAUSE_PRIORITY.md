# BACKEND-READINESS-V2 / 02 - Root-cause priority and the three selected changes

Input: `01_4_OF_12_ARCHITECTURE_ROOT_CAUSE.md`. Rules: general fixes only, no task identity in product code, no gate weakened without external-oracle
evidence, at most three architectural changes, every change has a deterministic reproducer that fails on `c98b18d`.

## 1. Ranking

Criteria: A cases affected (of 8 SAFE_FAILURE) / B evidence confidence / C generic / D safety risk / E complexity / F expected genuine gain.

| Rank | Family | A | B | C | D risk | E | F expected gain |
|---|---|---|---|---|---|---|---|
| 1 | **Plan-time mirror of the named-test rule** (part of family A) | 1 (S1_A) + stabilises a flipper | high: MEASURED correct fix, TRACED gap (`immutable_test_files`) | yes | very low: strengthens an existing plan gate to match the terminal rule already enforced | small | +0.6 (S1_A GENUINE in 1 of 3 runs now; the failing mechanism is removed, the base-run mechanism (incomplete candidate) is not) |
| 2 | **Target grounding for the Planner** (family A): localization overload collapse + inherited-member resolution, a bounded provenance-labelled "goal-named facts" block, a pass-through-shim plan check | 2 (T2, T4) | high for mechanisms (MEASURED T4 shim + T2 rank 17/limit 12); outcome gain medium | yes (symbols, manifests, shims are language-neutral concepts; implemented per language family where a parser exists) | low: adds evidence; the only new rejection is a plan whose sole target is a delegating shim whose delegate exists | medium | +0.5..1.0 (T4 was GENUINE in 2 of 3 runs with the right file; T2 needs the right file AND a correct fix) |
| 3 | **Gradle structured regression evidence and retry** (family B) | 1 (T3) + every Gradle regression (S4_A/S4_B class) | high (TRACED + MEASURED: gate message "not a code-fixable defect" while the candidate broke 3 tests) | yes (all Gradle projects) | low-medium: only consumes the runner's own JUnit XML that FS-1A already treats as authoritative; unattributed stays fail-closed | medium | +0.2..0.4 (T3 needs a second attempt that also gets Unicode whitespace right) |
| 4 | S3_A ERROR->FAIL baseline flip (family B) | 1 (S3_A) | mechanism TRACED; **cause INFERRED** | yes | **medium: it is a gate** | unknown until the parity check | +0.5 if it is a parity defect |
| 5 | Plan form normalisation / over-decomposition (family C) | 1 (S2_A) + rounds in 4 others | high | yes | low | medium | +0.2 |
| 6 | Fix-unit behavioural gate / reproducer-first ordering (family D) | 1-2 (S4_A, S4_B?) | medium | yes | medium | large (architecture) | uncertain |

## 2. Decisions

IMPLEMENT (order = dependency-free, smallest first; each its own commit after its reproducer):
1. **PLAN-NAMED-TEST-IMMUTABILITY-001** - `immutable_test_files` also returns the existing test files that a *pure regression-preservation* requirement names; the plan rule `PLAN_EDITS_IMMUTABLE_TEST` then covers them. Same predicate as the terminal closure (`close_unverified_requirements_with_named_tests`), moved earlier. Repair guidance already says "new tests go in a new file".
2. **PLAN-TARGET-GROUNDING-001** -
   (a) localization: one candidate per `lookup_key` (overloads folded, count recorded) so distinct definitions are not crowded out; a goal token `Class.member` whose class does not declare `member` resolves up the class's supertypes to the declaring class (provenance: DERIVED_DETERMINISTIC);
   (b) a bounded block in the Planner request, "GOAL-NAMED FACTS (deterministic)", listing for goal-named identifiers where they are defined and for goal-named package/dependency tokens which tracked manifests contain them, plus delegating-shim notes; absent facts are stated as absent ("not found"), never invented;
   (c) plan check: a plan whose only edit target for a manifest/config change is a delegating shim whose delegate exists is returned for repair with the exact delegate named.
3. **GRADLE-REGRESSION-DELTA-PER-TEST-001** - the regression outcome for a Gradle (or any JUnit-XML) run is built from the structured `TestExecutionReport` when the console has no Surefire/pytest block; the failing test names and the exception line reach the retry feedback; with no complete report the existing fail-closed `REGRESSION_UNATTRIBUTED` remains, and its text stops asserting replays that did not run.

HOLD (decision recorded, not implemented now):
- **S3_A ERROR->FAIL**: run the section-6 parity check first (deterministic, no model). Change the delta classification only if the check shows PRE and POST ran under different conditions (then fix the parity) or shows genuine baseline volatility with the oracle evidence already in hand (a2/a3/a5: external regression 0, Text half passes). A gate edit needs its own reproducer and mutation set; it is a candidate 4th change and will be reported as such, not folded in silently.
- **Plan form normalisation (S2_A)**: derivable `depends_on` edges and `kind` are safe to canonicalise, but the S2_A trace shows three different errors in three attempts, so normalisation alone would not have rescued it. Recorded for a later cycle.
- **Reproducer-first decomposition (family D)**: architecture-sized; the evidence (two cases) does not yet justify it.

NOT IMPLEMENTED, with reason:
- Phase 4 failure ledger and Phase 12 canonical cross-role state object: the evidence shows no case lost a constraint across roles; the existing `ObligationLedger` / MUST PRESERVE repair already carries them. Adding a state object would be speculative.
- Phase 9 constraint-aware repair rewrite: plan repair already separates MUST PRESERVE from MUST FIX; the code-repair gaps are feedback quality (changes 2-3 and the S3_A hold), not forgotten constraints.
- Model/role routing changes (H8): no single-variable evidence; recorded for a controlled experiment.
- Retry/budget increases and context-window increases: not indicated by any case.

## 3. Honest expectation

Run-to-run variance dominates the headline number: the same frozen tasks scored 6/12 and 4/12 on adjacent runs. Expected score after changes 1-3 is roughly 5.5-6.5 of 12 with wide spread; >=8/12 needs one or two never-solved tasks (T2, T3, S3_A) to convert in the single final run. This is stated now so the final report does not over-claim. Readiness will be declared only on the predefined gate.

## 4. Validation design (Phase 17)

- Deterministic first: reproducer tests fail on `c98b18d`, pass after; mutation set per change; ruff + pylint; affected suites; one full pytest at the end.
- Live A/B on the four highest-information cases, **serial**, same model/profile/config as BACKEND-FINAL-CLOSURE-005: **T2** (target grounding), **T3** (Gradle retry), **S3_A** if the hold clears (otherwise S4_A as a control for "no regression"), and **S1_A** (the named-test mechanism; judged on whether the plan now avoids the protected file and the exported candidate passes the external oracle, not on one GENUINE). Baselines are the existing c3 artifacts; no baseline re-run.
- Success signal restated: >=2 of {T2, T3, S3_A} GENUINE (never GENUINE in any earlier run), or one of them plus the S1_A/T4 mechanism fixed with the external oracle agreeing. Zero improvement -> stop adding architecture and reopen the analysis.
