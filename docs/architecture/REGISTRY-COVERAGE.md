# Registry coverage index (architecture closure)

Canonical runtime baseline described: commit `3eded93c0cfb50f8855d04510e3f9aa2cd5208f7`, tree `97a3da6b2ef664e2b7e25c4c6fe3fc3f42f253a0`.

Generated from `handover/BACKLOG_REGISTRY.csv` at that commit (read-only; the registry is evidence being
reconciled and is not edited during Phase A). One row per non-closed registry row.

- Non-closed rows: **141** (MEASURED by this generator; the opening inventory was 141).
- Reconciled so far: **5 / 141**. Marked `NEEDS_CHARACTERIZATION`: **3**. The rest are `PENDING`.
- Per slice: A1 21, A2 13, A3 28, A4 18, A5 26, A6 35.

Column meanings:

- **Registry**: priority / status / blocking flag exactly as the registry states them.
- **Slice / class**: the Phase-A slice that reconciles the row, and whether it concerns the kernel, the test
  harness, the desktop/UI, platform portability or performance.
- **Evidence source**: the registry's own `discovered_in` field.
- **Reconciliation**: `PENDING` (slice not yet run), `RECONCILED`, `NEEDS_CHARACTERIZATION` (interim only),
  `STALE_BOOKKEEPING`, `DUPLICATE/SUPERSEDED`.
- **Disposition**: the eventual architecture disposition (`FIX_REQUIRED_BEFORE_PRODUCTION`,
  `DISPROVED_WITH_EVIDENCE`, `INTENTIONAL_CURRENT_CONTRACT`, `GENUINE_FUTURE_FEATURE`), or `-` while open.
  A disposition written here is a Phase-A proposal for the owner's Phase-D review, not a registry change.

| # | Row ID | Registry (priority / status / blocking) | Slice / class | Evidence source | Reconciliation | Disposition | Notes, duplicates, supersession |
|---|---|---|---|---|---|---|---|
| 1 | `ENFORCE-EXECUTE-PLAN-CONVERGENCE-001` | P2 / TRACKED_IN_OPEN_ITEMS / no | A4 / KERNEL | PRD-008A final review (2026-09-24); deferred again in Batch 7 (PRD-030/031, extraction ... | PENDING | - | - |
| 2 | `RUN-ATTEMPT-GATE-EXTRACTION-001` | P3 / DEFERRED / no | A3 / KERNEL | Batch 7 PRD-031 (2026-09-27) | PENDING | - | - |
| 3 | `PLAT-WINDOWS-POLICY-PARITY-001` | P2 / DEFERRED / no | A5 / PLATFORM | ARCH-PLATFORM-001 audit (2026-09-28) | PENDING | - | - |
| 4 | `PLAT-WINDOWS-FS-GIT-001` | P2 / TRACKED_IN_OPEN_ITEMS / no | A5 / PLATFORM | ARCH-PLATFORM-001 audit (2026-09-28) | PENDING | - | - |
| 5 | `PLAT-WINDOWS-PROVIDERS-001` | P2 / DEFERRED / no | A5 / PLATFORM | ARCH-PLATFORM-001 audit (2026-09-28) | PENDING | - | - |
| 6 | `PLAT-PORTABILITY-P3-001` | P3 / DEFERRED / no | A5 / PLATFORM | ARCH-PLATFORM-001 audit (2026-09-28) | PENDING | - | - |
| 7 | `LEAK-SIGKILL-RECOVERY-GAP-001` | P3 / TRACKED_IN_OPEN_ITEMS / no | A1 / KERNEL | Pre-Graphify memory/resource-leak audit, step 4 sigkill mode + next-run characterizatio... | PENDING | - | - |
| 8 | `TEST-RESOURCEWARNING-HYGIENE-001` | P3 / TRACKED_IN_OPEN_ITEMS / no | A6 / TEST-HARNESS | PROVIDER-CONTRACT-001A full-suite comparison (2026-10-01) | PENDING | - | - |
| 9 | `DEMO-TIME-BUDGET-001` | P2 / DEFERRED / no | A1 / PERFORMANCE | PROVIDER-CONTRACT-001A F-1 (2026-10-01) | RECONCILED | INTENTIONAL_CURRENT_CONTRACT | A1: the run-wide shared clock is traced (AUTH-A1-08) and matches the row. The registry already classifies the value as an operator choice. Residual question (which value a real launch uses) is GAP-A1-06, NEEDS_CHARACTERIZATION, slice A5. |
| 10 | `PLAN-PRESERVED-REFERENCE-SOURCE-ATTRIBUTION-001` | P3 / DEFERRED / no | A4 / KERNEL | BACKEND-FINAL-CLOSURE-005 primary rerun P5-T2-r2 at b185bd6 (2026-10-08) | PENDING | - | - |
| 11 | `ENFORCE-UPSTREAM-WORK-PRESERVATION-001` | P2 / TRACKED_IN_OPEN_ITEMS / no | A1 / KERNEL | BACKEND-FINAL-CLOSURE-005 cohort 2 (2026-10-09) | PENDING | - | - |
| 12 | `ENFORCE-FALSE-PREMISE-MUTATION-GUARD-001` | P2 / TRACKED_IN_OPEN_ITEMS / no | A4 / KERNEL | BACKEND-FINAL-CLOSURE-005 cohort 2 C2-S6_A at b185bd6 (2026-10-09) | PENDING | - | - |
| 13 | `LEAK-CHECK-SHARED-LINE-FALSE-POSITIVE-001` | P3 / TRACKED_IN_OPEN_ITEMS / no | A6 / KERNEL | BACKEND-FINAL-CLOSURE-005 cohort 2 (2026-10-09) | PENDING | - | - |
| 14 | `INTEGRATION-PASS-REQUIREMENT-POLICY-001` | P2 / DEFERRED / no | A3 / KERNEL | BACKEND-FINAL-CLOSURE-005 second repair cycle, fourth scoped independent review (2026-1... | PENDING | - | - |
| 15 | `SUREFIRE-RENDERER-FAILURE-DETAIL-001` | P3 / TRACKED_IN_OPEN_ITEMS / no | A2 / KERNEL | BACKEND-FINAL-CLOSURE-005 third repair cycle P2-1 investigation (C2-S3_A-final, 2026-10... | PENDING | - | - |
| 16 | `SUREFIRE-STABILITY-ENVELOPE-001` | P3 / DEFERRED / no | A2 / KERNEL | BACKEND-FINAL-CLOSURE-005 third repair cycle P2-1 investigation (C2-S3_A-final, 2026-10... | PENDING | - | - |
| 17 | `ENFORCE-CLOSURE-STABILITY-CACHE-REUSE-001` | P3 / DEFERRED / no | A3 / KERNEL | Independent review of candidate 6d97558 (repair-003/reviews/REVIEW_RESULT_c3.md F5, 202... | PENDING | - | - |
| 18 | `MANAGED-SERVICE-ENV-STATE-BINDING-001` | P3 / TRACKED_IN_OPEN_ITEMS / no | A3 / KERNEL | Focused independent review of candidate 6169745 (repair-003/reviews/REVIEW_RESULT_c3_f1... | PENDING | - | - |
| 19 | `RECOVERY-UPSTREAM-OWNER-ROUTING-001` | P3 / TRACKED_IN_OPEN_ITEMS / no | A1 / KERNEL | BACKEND-FINAL-CLOSURE-005 final twelve on 3844540 (C2-S4_A-c3, 2026-10-10) | PENDING | - | - |
| 20 | `GUI-PHASE-OBSERVABILITY-001` | P3 / DEFERRED / no | A6 / DESKTOP/UI | GUI Run Cockpit v1.1 design gate, owner decisions Q-3 (2026-10-10) | PENDING | - | - |
| 21 | `GUI-EXEC-GIT-SHIM-001` | P2 / DEFERRED / yes (on this machine only until the license is accepted) | A5 / DESKTOP/UI | GUI Run Cockpit M8 live GUI smoke | PENDING | - | - |
| 22 | `GUI-EVIDENCE-POLL-COST-001` | P3 / DEFERRED / no | A6 / DESKTOP/UI | GUI Run Cockpit M8 independent review (P3-6) | PENDING | - | - |
| 23 | `GUI-LAUNCH-RETENTION-001` | P3 / TRACKED_IN_OPEN_ITEMS / no | A5 / DESKTOP/UI | GUI Run Cockpit M8 independent review (P3-8) | PENDING | - | - |
| 24 | `GUI-M1-STORED-PROMPT-001` | P3 / DEFERRED / no | A6 / DESKTOP/UI | GUI Run Cockpit M7 parity table | PENDING | - | - |
| 25 | `GUI-RUN-ATTRIBUTION-001` | P2 / DEFERRED / no | A5 / DESKTOP/UI | GUI Run Cockpit M5 workspace trace (M5-A) | PENDING | - | - |
| 26 | `GUI-EXEC-STOP-001` | P3 / DEFERRED / no | A1 / DESKTOP/UI | GUI Run Cockpit M6 execution trace (M6-A) | PENDING | - | - |
| 27 | `GUI-EXEC-TOOLCHAIN-PATH-001` | P3 / DEFERRED / no | A5 / DESKTOP/UI | GUI Run Cockpit M6 execution trace (M6-A) | PENDING | - | - |
| 28 | `GUI-TRACE-ATTEMPTS-SEMANTICS-001` | P3 / TRACKED_IN_OPEN_ITEMS / no | A1 / DESKTOP/UI | Live GUI smoke 3, run 20261011T090021-a3a5aff8 (Runs list showed 0 attempt(s) beside an... | PENDING | - | A1: read, not traced. Related to GAP-A1-03 (attempt accounting in evidence). To be reconciled with the evidence schema in A6. |
| 29 | `GUI-REVIEW-APPLY-BINDING-001` | P3 / TRACKED_IN_OPEN_ITEMS / no | A3 / DESKTOP/UI | Independent review of the final approval workflow (F4) | PENDING | - | - |
| 30 | `GUI-REVIEW-LOCAL-TRUST-001` | P3 / TRACKED_IN_OPEN_ITEMS / no | A3 / DESKTOP/UI | Independent review of the final approval workflow (F3) | PENDING | - | - |
| 31 | `GUI-EVIDENCE-LARGE-RUN-LOAD-001` | P3 / DEFERRED / no | A6 / DESKTOP/UI | Hardening Phase E (real recorder-written 3000-run history + one 20.5k-record run) | PENDING | - | - |
| 32 | `GUI-RECOVERY-ACTION-001` | P3 / DEFERRED / no | A1 / DESKTOP/UI | Hardening Phase G (reopen after abnormal exit), real-process test | PENDING | - | - |
| 33 | `GUI-NATIVE-MEMORY-DRIFT-001` | P3 / TRACKED_IN_OPEN_ITEMS / no | A6 / DESKTOP/UI | Hardening Phase L (30-minute soak and 12-minute per-process attribution) | PENDING | - | - |
| 34 | `GUI-SKILLS-BUILD-001` | P2 / DEFERRED / no | A4 / DESKTOP/UI | End-user experience Phase 3 trace | PENDING | - | - |
| 35 | `GUI-SKILLS-MUTATION-001` | P3 / DEFERRED / no | A4 / DESKTOP/UI | End-user experience Phase 3 trace | PENDING | - | - |
| 36 | `GUI-TOOLS-HEALTH-001` | P3 / DEFERRED / no | A5 / DESKTOP/UI | End-user experience Phase 3 trace | PENDING | - | - |
| 37 | `GUI-RUN-REPORT-001` | P3 / DEFERRED / no | A6 / DESKTOP/UI | End-user experience Phase 15 | PENDING | - | - |
| 38 | `GUI-APPEARANCE-001` | P3 / DEFERRED / no | A6 / DESKTOP/UI | End-user experience Phase 17 | PENDING | - | - |
| 39 | `GUI-NOTIFICATION-VISUAL-001` | P3 / DEFERRED / no | A6 / DESKTOP/UI | End-user experience Phase 22 | PENDING | - | - |
| 40 | `TEST-DEADLINE-TIMING-001` | P3 / OPEN / no | A6 / TEST-HARNESS | End-user experience final canonical run (2026-10-11) | PENDING | - | - |
| 41 | `LOCALIZATION-DEVELOPER-PATH-OVERLOAD-001` | P3 / DEFERRED / no | A4 / KERNEL | BACKEND-READINESS-V2 PLAN-TARGET-GROUNDING-001 | PENDING | - | - |
| 42 | `PLAN-FORM-NORMALISATION-001` | P3 / TRACKED_IN_OPEN_ITEMS / no | A4 / KERNEL | BACKEND-READINESS-V2 analysis (S2_A c3, T3, S3_A) | PENDING | - | - |
| 43 | `FIX-UNIT-BEHAVIOURAL-GATE-001` | P3 / DEFERRED / no | A2 / KERNEL | BACKEND-READINESS-V2 analysis (S4_A c3) | PENDING | - | - |
| 44 | `TEST-LAUNCH-APPROVAL-EXPIRY-TIMING-001` | P3 / TRACKED_IN_OPEN_ITEMS / no | A6 / TEST-HARNESS | BACKEND-READINESS-V2 canonical full pytest (1 failed, 10358 passed) 2026-10-12 | PENDING | - | - |
| 45 | `GOAL-EXAMPLE-COMPILER-COVERAGE-001` | P2 / DEFERRED / no | A2 / KERNEL | BACKEND-READINESS-V2 Phase 7-12 shared-gap analysis (2026-10-12) | PENDING | - | - |
| 46 | `REPAIR-EQUIVALENCE-001` | P3 / DEFERRED / no | A1 / KERNEL | BACKEND-READINESS-V2 repair-quality analysis (2026-10-12) | PENDING | - | - |
| 47 | `SEMANTIC-SAFETY-REPAIR-CONTINUITY-001` | P3 / TRACKED_IN_OPEN_ITEMS / no | A1 / KERNEL | BACKEND-READINESS-V2 four-failure closure (doc 22, 2026-10-12) | NEEDS_CHARACTERIZATION | - | A1: not checked whether the staging rejection's failure type is now kept as an open finding by replaces_open_finding (AUTH-A1-01). A model-free probe on the real type is owed. |
| 48 | `TERMINAL-REQUIREMENT-FAILURE-NO-REPAIR-001` | P2 / TRACKED_IN_OPEN_ITEMS / no | A3 / KERNEL | BACKEND-READINESS-V2 four-failure closure (doc 22, 2026-10-12) | PENDING | - | - |
| 49 | `PLAN-EXTERNAL-PACKAGE-CAPABILITY-001` | P2 / DEFERRED / no (blocks only Demo V2 scenario 02's candidate technology) | A4 / KERNEL | DEMO-V2 scenario 02 exploration (handover/DEMO-V2/FINDINGS.md, 2026-10-14) | PENDING | - | - |
| 50 | `REVIEW-DIFF-SIDECAR-RETENTION-001` | P3 / TRACKED_IN_OPEN_ITEMS / no | A3 / KERNEL | PRODUCT-UX-COMPLETION independent review (2026-10-15) | PENDING | - | - |
| 51 | `GUI-REASON-CODES-IN-ERROR-STATES-001` | P3 / OPEN / no | A6 / DESKTOP/UI | PRODUCT-UX-COMPLETION independent review (2026-10-15) | PENDING | - | - |
| 52 | `GREENFIELD-RUNTIME-VERIFICATION-DROPPED-001` | P2 / TRACKED_IN_OPEN_ITEMS / Demo 00 (Greenfield) live rehearsal: with the current wor... | A3 / KERNEL | GREENFIELD-OWNER-UX-CORRECTION (2026-10-15) | PENDING | - | - |
| 53 | `MANAGED-SERVICE-APPROVAL-001` | P2 / DEFERRED / no | A3 / KERNEL | Demo V2 Phase 0 trace (2026-10-15): managed-service reached execution with no approval ... | PENDING | - | - |
| 54 | `OPEN-FINDINGS-ORDER-RESUME-001` | P3 / TRACKED_IN_OPEN_ITEMS / none | A1 / KERNEL | Demo V2 D1 review (2026-10-15) | DUPLICATE/SUPERSEDED | - | A1: both halves are covered. 'Replacement is not order-aware' is part of GAP-A1-01 (the open set is replaced wholesale, AUTH-A1-02). 'Not checkpointed' is part of GAP-A1-02. Its planned closing unit (G4 with FindingState, OPEN-ITEMS OI-77) was not built. |
| 55 | `STATIC-RULE-ID-IN-EVIDENCE-001` | P3 / OPEN / none | A3 / KERNEL | Demo V2 retry UX (2026-10-15) | PENDING | - | - |
| 56 | `HITL-OTHER-APPROVAL-TRIGGERS-DEFERRAL-001` | P3 / OPEN / none | A3 / KERNEL | Demo V2 independent review (2026-10-15) | PENDING | - | - |
| 57 | `EVIDENCE-HELD-GATES-001` | P3 / TRACKED_IN_OPEN_ITEMS / none | A3 / KERNEL | Demo V2 independent review (2026-10-15) | PENDING | - | - |
| 58 | `HITL-RESUME-DEFERRED-GATES-001` | P3 / TRACKED_IN_OPEN_ITEMS / none | A1 / KERNEL | Demo V2 independent review (2026-10-15) | NEEDS_CHARACTERIZATION | - | A1: consistent with SM-A1-03 (the candidate checkpoint is written after approval and the held gates). The row itself says resume plus deferral is untested; a deterministic resume exercise is owed. |
| 59 | `APPLICATION-OUTPUT-PROGRAM-STREAM-001` | P3 / OPEN / none | A3 / DESKTOP/UI | Post-live Demo 00 closure (2026-10-15) | PENDING | - | - |
| 60 | `TECHNICAL-VERIFICATION-BARE-ROWS-001` | P3 / OPEN / none | A3 / DESKTOP/UI | Post-live Demo 00 closure (2026-10-15) | PENDING | - | - |
| 61 | `RUNTIME-LINE-EXPECTED-NONZERO-001` | P3 / TRACKED_IN_OPEN_ITEMS / none | A3 / DESKTOP/UI | Post-live Demo 00 closure (2026-10-15) | PENDING | - | - |
| 62 | `HISTORY-STATE-DIR-PER-PROJECT-001` | P3 / OPEN / none | A5 / DESKTOP/UI | Reopened project run-history investigation (2026-10-15) | PENDING | - | - |
| 63 | `HISTORY-SNAPSHOT-STALE-ON-REOPEN-001` | P3 / TRACKED_IN_OPEN_ITEMS / none | A5 / DESKTOP/UI | Reopened project run-history investigation (2026-10-15) | PENDING | - | - |
| 64 | `KNOWLEDGEGUARD-SKILL-IDENTITY-001` | P3 / TRACKED_IN_OPEN_ITEMS / none | A4 / KERNEL | Scenario 04 preparation and independent review (2026-10-16) | PENDING | - | - |
| 65 | `ATTEMPT-ATOMIC-CANDIDATE-DISCARDS-VALID-EDIT-001` | P2 / TRACKED_IN_OPEN_ITEMS / none | A1 / KERNEL | Scenario 04 second live run 20261016T094912-e7b4804c (2026-10-16) | PENDING | - | - |
| 66 | `TRIAGE-ON-ORCHESTRATOR-NOTICE-001` | P3 / TRACKED_IN_OPEN_ITEMS / none | A1 / KERNEL | Scenario 04 second live run 20261016T094912-e7b4804c (2026-10-16) | PENDING | - | - |
| 67 | `FINDER-ALIAS-BLIND-001` | P2 / TRACKED_IN_OPEN_ITEMS / no | A5 / TEST-HARNESS | W3 orchestrator verification after M1 | PENDING | - | - |
| 68 | `KERNEL-RESULT-RUNID-DIVERGES-001` | P3 / TRACKED_IN_OPEN_ITEMS / no | A1 / KERNEL | E1 kernel characterization (2026-10-16) | NEEDS_CHARACTERIZATION | - | A1: the row is INFERRED in the registry. A1 saw that terminal apply writes retry_state_reference='checkpoint:<run_id>' (MUT-A1-14) but did not compare the ids. |
| 69 | `KERNEL-GOLDEN-HARNESS-VOLATILITY-001` | P3 / TRACKED_IN_OPEN_ITEMS / no | A6 / TEST-HARNESS | E3 kernel characterization (2026-10-16) | PENDING | - | - |
| 70 | `KERNEL-FINAL-REVIEW-REUSES-PRE-BUILD-REVIEW-001` | P3 / TRACKED_IN_OPEN_ITEMS / no | A3 / KERNEL | E4 kernel characterization (2026-10-17) | PENDING | - | - |
| 71 | `KERNEL-ATTEMPT-CONTEXT-PLACEMENT-001` | P3 / TRACKED_IN_OPEN_ITEMS / no | A4 / KERNEL | E5 kernel decomposition (2026-10-17) | PENDING | - | - |
| 72 | `KERNEL-GOLDEN-DATE-DEPENDENT-001` | P3 / TRACKED_IN_OPEN_ITEMS / no | A6 / TEST-HARNESS | E8 preservation gate (2026-10-18) | PENDING | - | - |
| 73 | `KERNEL-STAGING-WORKSPACE-BASE-ESCAPES-001` | P3 / TRACKED_IN_OPEN_ITEMS / no | A3 / KERNEL | E10 kernel decomposition (2026-10-18) | PENDING | - | - |
| 74 | `KERNEL-GOLDEN-RUNID-PATH-NORMALIZATION-001` | P3 / TRACKED_IN_OPEN_ITEMS / no | A6 / TEST-HARNESS | E12 kernel decomposition (2026-10-18) | PENDING | - | - |
| 75 | `KERNEL-APPROVAL-RECORD-NO-CANDIDATE-DIGEST-001` | P2 / DEFERRED / no | A3 / KERNEL | E14 preflight characterization (2026-10-19) | PENDING | - | - |
| 76 | `KERNEL-RUNTIME-TRACE-PATH-LENGTH-DEPENDENT-001` | P3 / TRACKED_IN_OPEN_ITEMS / no | A6 / TEST-HARNESS | E14 preflight characterization (2026-10-19) | PENDING | - | - |
| 77 | `KERNEL-TESTS-CHECKOUT-MEMORY-MORE-FILES-001` | P3 / TRACKED_IN_OPEN_ITEMS / no | A6 / TEST-HARNESS | E14 preflight characterization (2026-10-19) | PENDING | - | - |
| 78 | `LSP-GROUNDING-CONSUMER-CHAIN-DEAD-001` | P3 / DEFERRED / no: FUTURE FEATURE / DORMANT LSP INTEGRATION - NOT CONTAI... | A4 / KERNEL | containment qualification family D (2026-10-27) | PENDING | - | - |
| 79 | `DEMO-V2-FIXED-STUB-PORT-PARALLEL-001` | P3 / OPEN / no: TEST-HARNESS only; the demo guard behaved correctly | A6 / TEST-HARNESS | containment qualification frozen-tree full suite 2026-10-27 (f3b24c8) | PENDING | - | - |
| 80 | `TOOLS-COMMAND-TEST-NONHERMETIC-CWD-001` | P3 / OPEN / no: test harness; the fail-closed gate behaved correctly | A6 / TEST-HARNESS | BASE-failure triage 2026-10-27 (owner sign-off; certified frozen tree f3b24c8 full suite) | PENDING | - | - |
| 81 | `TOOL-RUNPHASE-PRE-EFFECT-DENIAL-TERMINAL-001` | P3 / DEFERRED / no: an operator retry re-arms it; no effect is lost or du... | A5 / KERNEL | Wave 2 Slice A (2026-10-28) | PENDING | - | - |
| 82 | `TEST-SLICE-A-OCI-STALE-NESTED-COMMAND-STRING-001` | P3 / OPEN / no: test text only | A6 / TEST-HARNESS | Wave 2 W2-02 characterization (2026-10-28) | PENDING | - | - |
| 83 | `PROJECT-OWNED-DEPENDENCY-DEFECT-CLASSIFICATION-001` | P2 / DEFERRED / no: fail-closed; the run stops with a typed no-verdict in... | A2 / KERNEL | Slice F follow-up (COMPILE-GATE-CONVICTION-WITHOUT-VERDICT-001, owner decision 2026-10-28) | PENDING | - | - |
| 84 | `SKILL-WORKSPACE-PERSISTENT-VERIFICATION-ATTESTATION-001` | P3 / DEFERRED / no | A4 / KERNEL | B3 owner decision (2026-10-29) | PENDING | - | - |
| 85 | `SKILL-RULE-PROVENANCE-PROJECT-SUPPLIED-001` | P3 / OPEN / no | A4 / KERNEL | B3 verification (2026-10-29) | PENDING | - | - |
| 86 | `GIT-ICASE-PATHSPECS-UNQUALIFIED-001` | P3 / OPEN / no | A5 / KERNEL | W3-2 2026-10-30 (pathspec-environment census: LITERAL confirmed and stripped; GLOB/NOGL... | PENDING | - | - |
| 87 | `CLI-ADMISSION-REFUSAL-TEXT-MIDRUN-AUTHORITY-UNAVAILABLE-001` | P3 / OPEN / no | A5 / KERNEL | W3-3 D-001 R1 2026-10-30 (consequence trace) | PENDING | - | - |
| 88 | `DIRECTORY-NAME-PRUNING-P3-SIBLING-SITES-001` | P3 / OPEN / no | A5 / KERNEL | W3-3 reconciliation 2026-10-31 (KCEP-2 census 2026-10-30, T1 numbering) | PENDING | - | - |
| 89 | `CLI-RESUME-HINT-DESIGN-STAGE-STOP-TEXT-001` | P3 / OPEN / none | A4 / KERNEL | W3-5 2026-10-31 | PENDING | - | - |
| 90 | `GREENFIELD-DETERMINISTIC-OUTPUT-CLOSER-001` | P3 / OPEN / none | A2 / KERNEL | W3-8 closure 2026-11-01 (split from REQ-LEDGER-RUNTIME-CLOSURE-001) | PENDING | - | - |
| 91 | `WAVE5-O2-GENERATED-TEST-WEAKENING-001` | P3 / OPEN / no | A2 / KERNEL | Wave 5 Gate E A1 trace (2026-11-01) | PENDING | - | - |
| 92 | `WAVE5-PRD022-DUPLICATE-OWNER-NAMED-SYMBOL-001` | P3 / OPEN / no | A2 / KERNEL | Wave 5 Gate E A4 PRD-022 live test (2026-11-01) | PENDING | - | - |
| 93 | `PRODUCT-CANONICAL-SPELLING-LISTING-001` | P3 / OPEN / none (the certification harness no longer exposes it) | A5 / PERFORMANCE | CERT-TMP-GROWTH-001 diagnosis 2026-11-02 | PENDING | - | - |
| 94 | `PRODUCT-GIT-SUBPROCESS-COST-001` | P3 / OPEN / no | A5 / PERFORMANCE | PRD-034 C3 run 4 decomposition 2026-11-03 | PENDING | - | - |
| 95 | `CERT-OWNER-DEATH-MASK-SCRATCH-001` | P3 / OPEN / no | A5 / KERNEL | PRD-034 hosted run 41758354007 mask-residue attribution (2026-11-04) | PENDING | - | - |
| 96 | `CERT-DEMO-V2-IDLE-STUB-XDIST-001` | P3 / OPEN / no | A6 / TEST-HARNESS | PRD-034 correction-batch Linux verification (2026-11-04) | PENDING | - | - |
| 97 | `CERT-CI-PLAIN-JOBS-LOCKFILE-GROUP-001` | P2 / TRACKED_IN_OPEN_ITEMS / no | A6 / TEST-HARNESS | PRD-034 hosted run 41785193323 (2026-11-04) | PENDING | - | - |
| 98 | `SEC009-AGENT-ROLE-REASON-TEXT-001` | P3 / OPEN / no | A5 / KERNEL | PRD-035 qualification authority stop (2026-11-05) | PENDING | - | - |
| 99 | `TELEMETRY-FALLBACK-ROLE-ATTRIBUTION-001` | P3 / OPEN / no | A6 / KERNEL | PRD-035 attempt 2 (2026-11-05) | PENDING | - | - |
| 100 | `CERT-HISTORICAL-FIXTURE-EVIDENCE-EXPOSURE-001` | P2 / OPEN / no (does not block PRD-036 candidate freeze once the acti... | A5 / KERNEL | PRD-036 freeze review (2026-11-05) | PENDING | - | - |
| 101 | `CROSS-UNIT-PRODUCER-RECOVERY-CAPABILITY-001` | P3 / DEFERRED / no | A1 / KERNEL | WAVE5-O1 disposition (owner 2026-11-06) | PENDING | - | - |
| 102 | `UI-SCALE-TEST-TIMEOUT-001` | P3 / OPEN / no | A6 / TEST-HARNESS | Wave-8 UI orientation (2026-11-05) | PENDING | - | - |
| 103 | `PROCESS-DIAGNOSTIC-PROBE-REAL-AUTHORITY-HOME-001` | P3 / OPEN / no | A5 / KERNEL | Wave-8 ledger reconciliation (2026-11-06) | PENDING | - | - |
| 104 | `WAVE8-S7-PYTHONPATH-INHERITANCE-001` | P3 / OPEN / no: test harness only; the product backend behaves correctly | A6 / TEST-HARNESS | Wave-8 final integration preflight (2026-11-06) | PENDING | - | - |
| 105 | `WAVE8-TRACKED-BYTECODE-001` | P3 / OPEN / no: handover/ is not part of kriya/, plugins/ or tests/ a... | A6 / TEST-HARNESS | Wave-8 final integration preflight (2026-11-06) | PENDING | - | - |
| 106 | `WAVE8-DOCKER-VIEW-CLEANUP-RACE-001` | P3 / OPEN / no: pre-existing on rc11-line baseline; cleanup is not ga... | A5 / KERNEL | Wave-8 final integration full suite (2026-11-06) | PENDING | - | - |
| 107 | `WAVE8-DOCKER-REFUSAL-BECOMES-FALSE-VERDICT-001` | P3 / OPEN / owner ruling pending (predeclared disposition A is 'candi... | A3 / KERNEL | Wave-8 final integration characterization (2026-11-06) | PENDING | - | - |
| 108 | `WAVE8-PY-RUNTIME-ONLY-VENV-BOOTSTRAP-001` | P3 / OPEN / demo-impacting for Python greenfield without a test step;... | A3 / KERNEL | product/demo-72h OI-93 real-scenario check (2026-11-06) | PENDING | - | - |
| 109 | `JAVA-DEVELOPER-WRONG-PACKAGE-PATH-001` | P3 / OPEN / not a false success; demo-impacting only for Java goals t... | A4 / KERNEL | product/demo-72h Java memory proof (2026-11-07) | PENDING | - | - |
| 110 | `REVIEWER-OUTPUT-CAP-RUNAWAY-LATENCY-001` | P3 / OPEN / not a false success; presentation-impacting only when the... | A3 / PERFORMANCE | product/demo-72h latency trace (2026-11-07) | PENDING | - | - |
| 111 | `DESKTOP-LOOKUP-QUERY-APPROVAL-001` | P3 / DEFERRED / lookup is shown Disabled; the demo states the path is inc... | A3 / KERNEL | product/demo-72h lookup trace (2026-11-07) | PENDING | - | - |
| 112 | `OPTIONAL-RUNTIME-GRADED-FAILURE-POLICY-001` | P3 / DEFERRED / not a false success: a failing graded check still blocks | A3 / KERNEL | product/demo-72h walkthrough findings (2026-11-07) | PENDING | - | - |
| 113 | `RUNTIME-GATE-VENV-LACKS-PROJECT-TEST-DEPENDENCY-001` | P2 / OPEN / a REQUIRED runtime check fails closed (typed) - never a f... | A3 / KERNEL | product/demo-72h walkthrough findings (2026-11-07) | PENDING | - | - |
| 114 | `DESKTOP-PROJECT-RETRIEVAL-STATE-ISOLATION-001` | P2 / OPEN / security/correctness risk until characterized; not a defe... | A5 / KERNEL | product/demo-72h continuity trace (2026-11-07) | PENDING | - | - |
| 115 | `REQUIREMENT-CLOSURE-UNVERIFIED-RECORDED-AS-SUCCESS-001` | P2 / OPEN / a goal's requirement can remain UNVERIFIED while the resu... | A3 / KERNEL | product/demo-72h continuity Phase B (2026-11-07) | PENDING | - | - |
| 116 | `REQUIREMENT-VERIFIER-ENFORCE-PATH-NO-PROJECT-BEFORE-001` | P2 / OPEN / the desktop path is the direct path; the enforce path is ... | A2 / KERNEL | product/demo-72h continuity Phase B (2026-11-07) | PENDING | - | - |
| 117 | `REQUIREMENT-BEHAVIOR-CLAIM-NO-DETERMINISTIC-CLOSER-001` | P2 / OPEN / OPEN_REQUIREMENTS dominates valid runs, which blocks ever... | A2 / KERNEL | product/demo-72h requirement closer characterization (2026-11-07) | PENDING | - | - |
| 118 | `TEST-BASELINE-THREE-STALE-FAILURES-AT-132884B-001` | P2 / OPEN / no (does not block the venv-source closure; blocks a gree... | A6 / TEST-HARNESS | Focused gate run of the venv-source slice (2026-11-09) | PENDING | - | - |
| 119 | `DOCKER-BIND-SOURCE-REFUSED-AFTER-PATHNAME-RECREATION-001` | P2 / OPEN / no (owner ruling 2026-11-12: the row no longer blocks a t... | A5 / KERNEL | Focused gate run of the venv-source slice (2026-11-09) | PENDING | - | - |
| 120 | `DEMO-RESET-RM-WORKSPACE-RACES-DOCKER-RELEASE-001` | P3 / OPEN / no | A5 / KERNEL | Real-Kriya gate proof run 1 (2026-11-09) | PENDING | - | - |
| 121 | `RECOVERY-MAY-CHANGE-ANSWERED-NO-CHANGE-REFUSED-IN-UNIT-001` | P2 / OPEN / no (safe failure; lowers the recovery success rate, never... | A1 / KERNEL | MAY_CHANGE pre-implementation trace and slice (2026-11-10) | PENDING | - | - |
| 122 | `MILESTONE-UNIT-REVIEW-REFUSAL-FOLD-UNMEASURED-001` | P2 / OPEN / no | A3 / KERNEL | PRODUCT-72H R2 fix, consumer search of quality_gates_passed (2026-11-11) | PENDING | - | - |
| 123 | `ATTRIBUTION-KIND-AND-AUTHORITY-LABEL-DIAGNOSTIC-TRUTH-001` | P3 / OPEN / no | A2 / KERNEL | Scenario-A run 6 consumer trace (2026-11-11) | PENDING | - | - |
| 124 | `TEST-S7-H-LEDGER-STALE-CONTROL-REFERENCE-001` | P3 / OPEN / no (blocks a green full suite) | A6 / TEST-HARNESS | PRODUCT-72H run-6 gap slice, census run (2026-11-11) | PENDING | - | - |
| 125 | `TEST-REPAIR-003-F1-RESIDUAL-VENV-CREATION-COUNT-001` | P3 / OPEN / no (blocks a green full suite) | A6 / TEST-HARNESS | product/demo-72h walkthrough findings (2026-11-07) | PENDING | - | - |
| 126 | `GATE-RESULT-PURPOSE-ABSENT-ON-TESTS-GATE-001` | P3 / OPEN / no | A3 / KERNEL | PRODUCT-72H baseline-13 correction (2026-11-11) | PENDING | - | - |
| 127 | `TEST-CERT-TMP-GROWTH-TWO-STALE-ALLOWLISTS-001` | P3 / OPEN / no (blocks a green full suite) | A6 / TEST-HARNESS | PRODUCT-72H harness pathname-reuse slice (2026-11-11) | PENDING | - | - |
| 128 | `TEST-KERNEL-GOLDEN-ORDER-DEPENDENCE-IN-AD-HOC-MIX-001` | P3 / OPEN / no (blocks a green full suite if the full suite schedules... | A6 / TEST-HARNESS | PRODUCT-72H harness pathname-reuse slice (2026-11-11) | PENDING | - | - |
| 129 | `GOAL-STATED-JAVA-VERSION-TOOLCHAIN-AUTHORITY-001` | P2 / OPEN / no | A4 / KERNEL | Scenario-A run 7, 20261112T150652-e9c5502b (2026-11-12) | PENDING | - | - |
| 130 | `TEST-S7-H-LEDGER-OI142-CONTROL-MISSING-001` | P3 / OPEN / no (blocks a green full suite) | A6 / TEST-HARNESS | PRODUCT-72H run-7 gap slice (2026-11-12), found while re-pointing two ledger controls f... | PENDING | - | - |
| 131 | `SKILL-QPID-RULE-DOCTOR-ADVICE-STALE-001` | P3 / OPEN / no | A4 / KERNEL | PRODUCT-72H run-7 gap slice (2026-11-12) | PENDING | - | - |
| 132 | `METRICS-RETRIES-TRIGGERED-COUNTS-TERMINAL-ATTEMPT-001` | P3 / OPEN / no | A1 / KERNEL | Scenario-A run 8, 20261112T203608-28d96ed5 (2026-11-12) | PENDING | - | A1: read, not traced. Related to GAP-A1-03. To be reconciled with role metrics in A6. |
| 133 | `INFRA-STOP-ATTRIBUTION-SOURCE-DEFECT-LABEL-001` | P3 / OPEN / no | A2 / KERNEL | Scenario-A run 8, 20261112T203608-28d96ed5 (2026-11-12) | PENDING | - | - |
| 134 | `GUI-KRIYA-LOG-NOT-PERSISTED-001` | P3 / OPEN / no | A6 / DESKTOP/UI | Scenario-A run 9, 20261113T060030-40a14267 (2026-11-13) | PENDING | - | - |
| 135 | `TEST-CERTIFICATION-WALL-CLOCK-001` | P3 / OPEN / no | A6 / TEST-HARNESS | Final full suite on 82436eb (2026-11-13) | PENDING | - | - |
| 136 | `GENERATED-VERIFIER-CONTRACT-AUTHORITY-001` | P1 / ACTIVE / yes | A2 / KERNEL | Scenario-A run 10 (20261113T235143-0762989b, 82436eb), sealed 2026-11-13; owner ruling ... | PENDING | - | - |
| 137 | `SKILL-RELEVANCE-CONTEXT-SCOPE-001` | P2 / OPEN / no | A4 / KERNEL | Scenario-A run 10 (20261113T235143-0762989b, 82436eb), sealed 2026-11-13; owner checkpo... | PENDING | - | - |
| 138 | `RETRY-REFERENCE-CONTEXT-DEDUP-001` | P3 / OPEN / no | A1 / KERNEL | Scenario-A run 10 (20261113T235143-0762989b, 82436eb), sealed 2026-11-13; owner checkpo... | PENDING | - | - |
| 139 | `RETRY-PROGRESS-SAME-BLOCKER-001` | P2 / OPEN / no | A1 / KERNEL | Scenario-A run 10 (20261113T235143-0762989b, 82436eb), sealed 2026-11-13; owner checkpo... | DUPLICATE/SUPERSEDED | - | A1: no independent gap. Its measured part is closed (NON-CANDIDATE-FAILURE-SCOPED-RESET-001, 9 tests pass on this baseline); its remaining question is GAP-A1-01, carried by RETRY-BLOCKER-IDENTITY-AUTHORITY-001. |
| 140 | `RETRY-BLOCKER-IDENTITY-AUTHORITY-001` | P2 / OPEN / no | A1 / KERNEL | Scenario-A run 10 (20261113T235143-0762989b, 82436eb), sealed 2026-11-13; convergence s... | RECONCILED | FIX_REQUIRED_BEFORE_PRODUCTION | A1: confirmed, GAP-A1-01 (MEASURED probe P1-P6, TRACED AUTH-A1-02/03/12). Disposition follows the owner ruling of 2026-11-14 (not a future feature, not a contract) and is a Phase-A proposal. Contradiction H-A1-1 must be ruled first. |
| 141 | `RETRY-STATE-RESUME-CONTINUITY-001` | P2 / OPEN / no | A1 / KERNEL | Convergence sprint WS4 trace, 2026-11-14; owner ruling 2026-11-14 (WS4 RULING AFTER TRACE) | RECONCILED | FIX_REQUIRED_BEFORE_PRODUCTION | A1: confirmed by trace, GAP-A1-02; not exercised. A1 found two parts (unit, enforce) that may need different dispositions: H-A1-3. The disposition shown follows the owner ruling of 2026-11-14 and is a Phase-A proposal. |
