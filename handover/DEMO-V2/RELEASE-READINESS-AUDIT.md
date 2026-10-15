# Demo V2 release-readiness audit (read-only, 2026-10-15, candidate `b93f0d9`)

Method: each invariant is tied to the tests/records that enforce it. PASS = enforced by a deterministic test or a measured live record; KNOWN LIMIT = true boundary of the current build, documented; BACKLOG = registered in `handover/BACKLOG_REGISTRY.csv`; BLOCKER = would stop a tag. **No BLOCKER found.** The owner's visual check of the rebuilt app is still outstanding and is not something this audit can supply.

| # | Invariant | Verdict | Evidence / limit |
|---|---|---|---|
| A1 | Source review precedes build, tests, runtime in Desktop HITL | PASS | `tests/test_greenfield_direct_plan_runtime.py` (`gap_b_strong_nothing_is_built_tested_or_run_before_the_source_review_is_decided`, `..pom_validation_also_waits..`, reject = nothing runs), structured plan `test_greenfield_runtime_end_to_end.py`; live run 20261015T200748-08db1057 (review -> approval -> build -> 1/1 tests -> runtime review -> execution) |
| A2 | reviewed = built = tested = executed = applied bytes; drift fails closed | PASS | `..reviewed_built_tested_executed_and_applied_bytes_are_identical`, `..changed_by_the_build/tests/pom validation/closers/execution is never applied`, `CANDIDATE_CHANGED_AFTER_SOURCE_REVIEW`; real JDK17/Maven check `tests/test_direct_plan_real_maven.py` (tier `real_toolchain`) |
| A3 | Deterministic pom corrections are inside the reviewed candidate | PASS | `..deterministic_a_pom_correction_is_in_the_reviewed_candidate..` |
| A4 | Same ordering for other approval triggers, resume of held gates | KNOWN LIMIT / BACKLOG | `HITL-OTHER-APPROVAL-TRIGGERS-DEFERRAL-001`, `HITL-RESUME-DEFERRED-GATES-001`, `EVIDENCE-HELD-GATES-001` |
| B1 | Required runtime Reject cannot succeed or apply | PASS | `gap_a_rejecting_the_required_runtime_review_is_not_success_and_applies_nothing`; typed `REQUIRED_RUNTIME_VERIFICATION_DECLINED`; optional checks keep declined semantics (tested) |
| B2 | Desktop runtime commands need review; native confirmation separate from source approval | PASS | `..a_decision_of_the_wrong_kind_cannot_approve_the_runtime_review`, runtime-approval smoke evidence `handover/DEMO-V2/evidence/runtime-approval/` |
| B3 | Managed-service verification in HITL | KNOWN LIMIT | fails closed (`MANAGED_SERVICE_APPROVAL_UNSUPPORTED`), stated to the user |
| C1 | Planned tool subtasks needing confirmation do not execute in HITL | PASS | 435277d; `test_tool_subtask_hitl_confirmation.py`; `test_prd012_egress.py::..never_runs_in_human_in_the_loop_mode` (nothing prepared for execution) |
| C2 | Egress stays fail-closed | PASS | PRD-012 tests incl. the Docker-tier probe (now executes the hostile command under containment: DENIED network) |
| C3 | A planned tool step has no per-run review UI | KNOWN LIMIT | it stops `NEEDS_REVIEW / TOOL_CONFIRMATION_UNSUPPORTED`; `OWNER-RECOVERY-SELF-CORRECTION-REVIEW-001` related |
| D1 | Build/test/runtime evidence comes from authoritative records | PASS | `ui/shared` presentation built from gate records; `liveRun.test.ts` pins the real run's records |
| D2 | A model grader cannot manufacture deterministic success | PASS (with a noted edge) | runtime line passes only when every recorded side passed (mutation-tested); the grader can only withhold. Edge: `RUNTIME-LINE-EXPECTED-NONZERO-001` (a goal that expects non-zero exit reads "failed"; fail-closed) |
| D3 | The formal requirement ledger stays distinct and honest | PASS / BACKLOG | disclosure "Requirement ledger: not fully verified"; "Requested behavior verified" only for a recorded positive verification; no closer (`REQ-LEDGER-RUNTIME-CLOSURE-001`, owner decision) |
| D4 | Engineering evidence is not hidden behind ledger wording | PASS | primary list = Build / Tests / Runtime verification + Application + Output |
| E1 | History is scoped to the active state directory; same state restores runs after restart | PASS | `tests/test_reopened_project_history.py` (fresh `kriya traces` processes); measured on a copy of the owner's state: HelloWorld -> `['43c4c2a0']` |
| E2 | Separate state directories stay isolated; scenario state is not owner state | PASS (by design) | an empty state yields the typed unavailable error; `launch_gui.sh --scenario` uses its own state, `--state` is explicit and validated |
| E3 | A window pins the newest already-published snapshot; a run recorded elsewhere after it needs a refresh | KNOWN LIMIT / BACKLOG | `HISTORY-SNAPSHOT-STALE-ON-REOPEN-001`; `HISTORY-STATE-DIR-PER-PROJECT-001` |
| E4 | The owner's reported "no history" | UNRESOLVED BY THE OWNER | cause measured: scenario window on an empty state directory and/or the PARENT folder `~/delete/2` opened (settings file); needs the owner's visual check |
| F1 | Generic code does not branch on a concrete provider | PASS | `test_qual_environment_identity` green; `project_init_ops.py` now uses `model_runtime.with_context_window` (profile byte-identical) |
| G1 | The renderer reads no state DB, evidence, authority, config, skills or project internals directly | PASS | all reads go through the typed host operations (`kriya_argv.ts`, `ipc_contract.ts`); `npm run check:shared-deps`; no filesystem API in `ui/shared`; this work added none |
| G2 | Packaged app carries no dev driver/fixture | PASS | `check-bundle --package` OK at the rebuild |
| H | Visual quality (typography, spacing, Project Details wording) | NOT AUDITED | no spec; owner has not seen the rebuilt app (deliberately deferred) |
| I | Full canonical suite at the candidate | PASS | `b93f0d9`: **11275 passed, 15 warnings, 2381.66 s (0:39:41)** (owner baseline at `a743d9f`: 11269 passed; +6 = the new history tests) |

Known non-blocking backlog (all registered): `REQ-LEDGER-RUNTIME-CLOSURE-001`, `OPEN-FINDINGS-ORDER-RESUME-001`, `STATIC-RULE-ID-IN-EVIDENCE-001`, `OWNER-RECOVERY-SELF-CORRECTION-REVIEW-001`, `HITL-OTHER-APPROVAL-TRIGGERS-DEFERRAL-001`, `EVIDENCE-HELD-GATES-001`, `HITL-RESUME-DEFERRED-GATES-001`, `APPLICATION-OUTPUT-PROGRAM-STREAM-001`, `TECHNICAL-VERIFICATION-BARE-ROWS-001`, `RUNTIME-LINE-EXPECTED-NONZERO-001`, `HISTORY-STATE-DIR-PER-PROJECT-001`, `HISTORY-SNAPSHOT-STALE-ON-REOPEN-001`.
