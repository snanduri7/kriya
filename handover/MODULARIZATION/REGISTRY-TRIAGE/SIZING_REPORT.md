# Registry classification - SIZING REPORT v2 (2026-10-19; PROVISIONAL per owner; snapshot 8e0c90a; scratch, nothing written to the repo)

Status: AFTER the 4 classifier batches, my review, the 3 blind adversarial batches and the owner decisions of 2026-10-19. BEFORE: the E14 final report, the ONE combined allowlist simulation, the final owner-cohesive routing and the plan/ledger mutation.

| Class | 76 registry rows | + derived/new rows (5 A, 1 B) |
|---|---|---|
| A - defect, fix in this workstream | 36 | 41 |
| B - future feature (DEFERRED_BY_OWNER/FUTURE_FEATURE) | 26 | 27 |
| C - obsolete/fixed | 5 | 5 |
| D - disproved | 0 | 0 |
| E - intentional contract (4 CONDITIONAL on pins) | 9 | 9 |
| needs owner decision | 0 | 0 |

Verification depth on the 76 rows: {'EVIDENCE_READ': 51, 'VERIFIED_BY_CODE_READ': 15, 'VERIFIED_BY_PROBE': 10} (PROBE = reproduced by me; CODE_READ = I read code/contract; EVIDENCE_READ = I read classifier evidence + the blind adversarial verdict, no own code check). Every B/C/E row also had one BLIND adversarial review (row + definition only).

## Class A (41 incl. 5 derived) by closing unit

| ID | Verified | Closing unit | Size | Prerequisite | Auth/Sec |
|---|---|---|---|---|---|
| ATTRIBUTION-SELF-DIAGNOSIS-MENTION-001 | EVIDENCE_READ | F4 | MEDIUM | F1-F3 | yes |
| ATTEMPT-ATOMIC-CANDIDATE-DISCARDS-VALID-EDIT-001 | EVIDENCE_READ | G4 | LARGE | G1-G3 |  |
| OPEN-FINDINGS-ORDER-RESUME-001 | PROBE | G4 | SMALL | F2 (FindingState) |  |
| SEMANTIC-SAFETY-REPAIR-CONTINUITY-001 | PROBE | G4 | SMALL | G1-G3 | yes |
| GENERATION-BUDGET-COUNTS-HUMAN-WAIT-001 | EVIDENCE_READ | H4 | LARGE | H1-H3; C3 human-wait hook done |  |
| TRIAGE-ON-ORCHESTRATOR-NOTICE-001 | EVIDENCE_READ | H4 | SMALL | H1-H3, F2 |  |
| KNOWLEDGEGUARD-SKILL-IDENTITY-001 | EVIDENCE_READ | J3 (new, correction after the J2 move) | SMALL | J1, J2 | yes |
| MANAGED-SERVICE-ENV-STATE-BINDING-001 | EVIDENCE_READ | L1 | SMALL | E9 done | yes |
| MVN-VALIDATE-FAIL-OPEN-NEW | CODE_READ | L1 if L1 is demonstrably the validator owner at its preflight, else a  | SMALL | none | yes |
| LEAK-SIGKILL-RECOVERY-GAP-001 | CODE_READ | L3 (new, OCI object-ID ownership + residue reconciliation) | MEDIUM | L1/L2 | yes |
| PLAT-WINDOWS-FS-GIT-001 | PROBE | M4 (new, git invocation profile on the single runner) | MEDIUM | M2, M3 | yes |
| GUI-REVIEW-APPLY-BINDING-001 | EVIDENCE_READ | R4 | SMALL | E10/G2 for the equality check location (G4 owns Ca | yes |
| GUI-TRACE-ATTEMPTS-SEMANTICS-001 | EVIDENCE_READ | R5 | SMALL | none |  |
| TOOL-SUBTASK-DESKTOP-REVIEW-001 | PROBE | S1 (new, EARLY, TOOLBOX authority) | MEDIUM | none; K2 finishes the ToolRegistry delegation | yes |
| APP-RESULT-STDERR-HIDDEN-NEW | CODE_READ | S10 (application-result presentation truth: runtime line + stdout/stde | SMALL | none | yes |
| RUNTIME-LINE-EXPECTED-NONZERO-001 | EVIDENCE_READ | S10 (application-result presentation truth: runtime line + stdout/stde | SMALL | B2 done |  |
| HISTORY-SNAPSHOT-STALE-ON-REOPEN-001 | EVIDENCE_READ | S11 (new, UI session snapshot) | SMALL | B2 done |  |
| GOAL-EXAMPLE-LITERAL-BACKSLASH-001 | PROBE | S12 (new, example-compiler grammar) | SMALL | none (bump JAVA_EXAMPLE_COMPILER_VERSION) | yes |
| GREENFIELD-RUNTIME-VERIFICATION-DROPPED-001 | PROBE | S13 (new, runtime-obligation detection, generic typed criterion) | MEDIUM | E14 accepted baseline; E3/E4 done | yes |
| SUREFIRE-RENDERER-FAILURE-DETAIL-001 | PROBE | S14 (new, canonical Maven test identity in validation_baseline) | SMALL | none |  |
| PLAN-FORM-NORMALISATION-001 | CODE_READ | S15 (new, Planner contract matches validator for REFACTOR; one fix wit | SMALL | none |  |
| EGRESS-POLICY-TYPO-FAILS-OPEN-NEW | PROBE | S16 (new, closed vocabulary for security-authority string fields; egre | SMALL | none | yes |
| BASELINE-JDK-PARITY-NEW | PROBE | S17 (new, baseline capture runs under the same JDK override and record | SMALL | none | yes |
| SEC-CLI-URL-CREDENTIAL-REDACTION-001 | PROBE | S2 (new, EARLY, one redaction owner) | SMALL | none | yes |
| OWNER-RECOVERY-SELF-CORRECTION-REVIEW-001 | CODE_READ | S3 (new, EARLY, config authority classification) | SMALL | E11 done | yes |
| MILESTONE-REPLAY-UNAPPROVED-COMMANDS-001 | EVIDENCE_READ | S4 (new, EARLY, replay through the typed runtime-command review) | MEDIUM | C3 done | yes |
| PLAT-WINDOWS-ENTRY-REFUSAL-NEW | EVIDENCE_READ | S5 (new, EARLY, platform entry refusal) | SMALL | none | yes |
| GUI-REVIEW-LOCAL-TRUST-001 | CODE_READ | S6 | MEDIUM | R4 candidate identity; O-slices touch the KUP surf | yes |
| GUI-LAUNCH-RETENTION-001 | EVIDENCE_READ | S7 (new, STATE retention) | SMALL | D2 done |  |
| REVIEW-DIFF-SIDECAR-RETENTION-001 | EVIDENCE_READ | S7 (new, STATE retention) | SMALL | D2 done | yes |
| EVIDENCE-HELD-GATES-001 | EVIDENCE_READ | S8 (held-gate records: attempt.closed + checkpoint ledger snapshot, on | SMALL | E5 done |  |
| HITL-RESUME-DEFERRED-GATES-001 | PROBE | S8 (held-gate records: attempt.closed + checkpoint ledger snapshot, on | SMALL | E5 done |  |
| LEAK-CHECK-SHARED-LINE-FALSE-POSITIVE-001 | EVIDENCE_READ | S9 (new, EVIDENCE leak-check provenance) | SMALL | D1 done | yes |
| TEST-RESOURCEWARNING-HYGIENE-001 | CODE_READ | T1 | MEDIUM | none |  |
| TEST-LAUNCH-APPROVAL-EXPIRY-TIMING-001 | CODE_READ | T1 (new, test-harness hygiene before Q1) | SMALL | none | yes |
| GUI-NATIVE-MEMORY-DRIFT-001 | CODE_READ | T1 (soak threshold harness fix) then final UI qualification | SMALL | final candidate |  |
| RECOVERY-UPSTREAM-OWNER-ROUTING-001 | CODE_READ | TR1 (new, ONE terminal-recovery slice after H4 and X1) | LARGE | F1/F4 attribution, H4, X1 (terminal decision conve | yes |
| TERMINAL-REQUIREMENT-FAILURE-NO-REPAIR-001 | CODE_READ | TR1 (new, ONE terminal-recovery slice after H4 and X1) | LARGE | F1/F4 attribution, H4, X1 (terminal decision conve | yes |
| ENFORCE-UPSTREAM-WORK-PRESERVATION-001 | EVIDENCE_READ | X1 | MEDIUM | X1 design (atomic-plan vs per-unit commit) | yes |
| ENFORCE-EXECUTE-PLAN-CONVERGENCE-001 | CODE_READ | X1 (new, Type D, one execution path) | LARGE | E14 resumed, F/G/H corrections | yes |
| ENFORCE-FALSE-PREMISE-MUTATION-GUARD-001 | CODE_READ | X2 (new, Type C, sealed premise as scoped constraint) | MEDIUM | F-series plan validation, G4 repair scope | yes |

## Closing units (Class A rows per unit)
- F4: 1
- G4: 3
- H4: 2
- J3: 1
- L1: 2
- L3: 1
- M4: 1
- R4: 1
- R5: 1
- S1: 1
- S10: 2
- S11: 1
- S12: 1
- S13: 1
- S14: 1
- S15: 1
- S16: 1
- S17: 1
- S2: 1
- S3: 1
- S4: 1
- S5: 1
- S6: 1
- S7: 2
- S8: 2
- S9: 1
- T1: 3
- TR1: 2
- X1: 2
- X2: 1

## Conditional E (closure work first)
- GUI-NOTIFICATION-VISUAL-001: CONDITIONAL: the pinning test ids must be verified before closing (classifier did not)
- HITL-OTHER-APPROVAL-TRIGGERS-DEFERRAL-001: CONDITIONAL: add the user-guide statement and a test for sensitive-path/diff-size approval outside human-in-the-loop (closure work)
- HISTORY-STATE-DIR-PER-PROJECT-001: CONDITIONAL: add the user-guide sentence that history follows the state directory, not the folder (closure work)
- INTEGRATION-PASS-REQUIREMENT-POLICY-001: CONDITIONAL on adding the missing milestone record-vs-block test and passing it

## B (27)
APPLICATION-OUTPUT-PROGRAM-STREAM-001, ENFORCE-CLOSURE-STABILITY-CACHE-REUSE-001, FIX-UNIT-BEHAVIOURAL-GATE-001, GOAL-EXAMPLE-COMPILER-COVERAGE-001, GUI-APPEARANCE-001, GUI-EVIDENCE-LARGE-RUN-LOAD-001, GUI-EVIDENCE-POLL-COST-001, GUI-EXEC-STOP-001, GUI-M1-STORED-PROMPT-001, GUI-PHASE-OBSERVABILITY-001, GUI-REASON-CODES-IN-ERROR-STATES-001, GUI-RECOVERY-ACTION-001, GUI-RUN-ATTRIBUTION-001, GUI-RUN-REPORT-001, GUI-SKILLS-BUILD-001, GUI-SKILLS-MUTATION-001, GUI-TOOLS-HEALTH-001, LOCALIZATION-DEVELOPER-PATH-OVERLOAD-001, PLAT-PORTABILITY-P3-001, PLAT-WINDOWS-FS-GIT-WINDOWS-ONLY-NEW, PLAT-WINDOWS-POLICY-PARITY-001, PLAT-WINDOWS-PROVIDERS-001, REPAIR-EQUIVALENCE-001, REQ-LEDGER-RUNTIME-CLOSURE-001, RUN-ATTEMPT-GATE-EXTRACTION-001, STATIC-RULE-ID-IN-EVIDENCE-001, SUREFIRE-STABILITY-ENVELOPE-001

## C (5)
DIFF-APPLIED-BYTES-BINDING-001, GUI-RUNTIME-COMMAND-APPROVAL-001, PLAN-EXTERNAL-PACKAGE-CAPABILITY-001, TECHNICAL-VERIFICATION-BARE-ROWS-001, TEST-DEADLINE-TIMING-001
