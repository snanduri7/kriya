# Wave-2 reconciliation ledger (evidence only; nothing implemented)

Date 2026-10-28. Repo `Kriya-rebuild-v2`, branch `arch/containment-rebuild-v2`, HEAD `2aaf1a7`, tree `fee49d2`. Nothing pushed or tagged; `refs/kriya/verified` = `7eddc5d` (unmoved).
This is the owner-sanctioned single ledger document. It changes no production code, no tests and no registry schema. The only registry edit that accompanies it is closing `D1-DURABILITY-TARGET-NO-FULLFSYNC-001` as an INTENTIONAL CONTRACT (owner sign-off 2026-10-28).

## 0. How to read this, and its limits

* **There is no single Wave-2 ledger and the registry has no wave field.** The Wave-2 set below was ASSEMBLED from: `~/kriya-fixforward-2026-10-22/closure/CLOSURE_MAP.md` (Parts 10-14), `closure/WAVE2_CHARACTERIZATION.md` (section 0, 12, 13), `closure/D2_ACCEPTANCE.md`, `~/kriya-residual-confidence/intake/PROCESSED.csv` (RDCG dispositions + `closure_wave`), `campaigns/RDCG-01/A_DETECTION_SENSITIVITY.md` / `seeded-mutants/key/KEY.csv` (Campaign-A gaps), this repo's `handover/BACKLOG_REGISTRY.csv` and `git log`.
* **Evidence vocabulary.** `MEASURED@4b4760e` = an RDCG/characterization measurement made on the frozen SHA `4b4760e`, NOT re-run here. `TRACED@HEAD` = this reconciliation read the code at `2aaf1a7`. `INFERRED` = consistent with evidence, not proven. `PROPOSED` = a step a slice's characterization must perform. **No reproducer was re-run at HEAD in this reconciliation.** A `TRACED@HEAD` "still present" therefore means "the guarding code is absent/unchanged"; its reachability under the S7/S8 mandatory-containment migration is established only by the slice's own characterization.
* **State vocabulary.** `PRESENT@HEAD` (code traced), `FIXED` (cited commit + cited evidence), `DECISION-PENDING` (an owner decision is recorded as needed), `UNKNOWN@HEAD` (this reconciliation did not establish the state; NOT a claim of a defect).
* **Expected historical items.** Found: D-002/model identity, B-006 (uncertain-commit candidate deletion), B-002/B-003/B-004/B-005 (malformed durable state), F9. **"Contract/retry correctness": historical item not located.** It is NOT aliased to B-002 or F9 (owner ruling 2026-10-28). Canonical mapping: retry/disposition correctness -> W2-01/W2-02/W2-03; B-002 -> malformed/non-UTF-8 durable-state contract; F9 -> JVM/result truth.

## 1. Already resolved or disproved (not part of the remaining inventory)

| item | state | evidence |
|---|---|---|
| RDCG-D-003 / D-003 amendment (EOF/Enter/Ctrl-C at a change approval) | FIXED | `937f564`, `eabf930` (16/16 mutants, CLOSURE_MAP Part 10); regression files `tests/test_rdcg_d003_eof_approval.py`, `tests/test_n1_f4_approval_outcome.py` |
| RDCG-B-001 (success claim over a RunRecord that did not end SUCCESS) | FIXED | `6486c61` (17/17 mutants) |
| Wave-2 item 1(b) (replay executes under the run's autonomy config) | FIXED | `ecd8c87` (6/6 mutants); the approval half is W2-04 below |
| OI-142 (every gate runs in a fresh Kriya-owned view) | FIXED | `3be8ff1`, `4090dd6` (48/49 mutants, 1 equivalent); OCI + managed-service views `f6a4c5c` (S3), `229cfe7` (S4.1) close residuals R1 and R4 (INFERRED from commit subjects; not re-read) |
| RDCG-D-001 / OI-148 (one repository, one workspace authority) | FIXED | `3b1acdb` (25/25 mutants) |
| D2 M1a/M2/M3/M4 (control-state authenticity, human-decision store, private review channel) incl. item 2 / OI-102 / `GUI-REVIEW-LOCAL-TRUST-001` | FIXED within the owner's narrow claim | `closure/D2_ACCEPTANCE.md` (3,725 passed). Registry row `GUI-REVIEW-LOCAL-TRUST-001` is still TRACKED_IN_OPEN_ITEMS: **bookkeeping owed**. Documented limits: same-UID display forgery; Linux `/proc` measurement is MANDATORY before final production qualification (Wave 4 / certification) |
| OI-63 | MOOT (superseded by the mandatory ownership contract) | D2_ACCEPTANCE item 7; registry row untouched: bookkeeping owed |
| Wave-2 item 3 (`autonomy.egress_policy` read-time re-check) | LIKELY FIXED | `93fe356` (`parse_egress_policy`, TRACED@HEAD `kriya/config/authority.py:471`) is the single check on validator, assignment, `model_copy` and authority resolution. Confirm against the original characterization mechanism before closing (PROPOSED) |

## 2. Remaining Wave-2 inventory

Fields per row: severity (registry or RDCG intake, as labelled), invariant, first wrong owner, evidence, state, prerequisites, smallest likely correction, shared root, stop condition, slice.

### Group A - TOOL EXECUTION DISPOSITION (owner-approved slice shape)

**W2-01 `D1-TOOL-REPORTED-FAILED-STAYS-RETRYABLE-001`** (registry row 303; P3 in registry, owner-elevated to a contract)
* Invariant: a started external effect is never re-executed under the same execution identity; a retry is a new attempt with fresh authority.
* First wrong owner: `kriya/workflow/resume_support.py::classify_tool_resume` - it leaves a TOOL subtask in `failed` with "existing semantics", and the enforce loop re-runs it on explicit resume (docstring + row 303).
* Evidence: TRACED@HEAD. State: PRESENT@HEAD.
* Prerequisites: W2-02's transition design; the identity characterization (section 4). Smallest correction: a tool-reported failure after `STARTED` persists a terminal state that `classify_tool_resume` classifies as not-retryable, while a typed pre-effect refusal (NOT_STARTED, row text) keeps today's semantics. Changes the contract pinned by `tests/test_d1_tool_lifecycle.py` deliberately (old assertions replaced with mutation-checked ones, never deleted).
* Shared root: W2-02 (proven: same classifier, same persisted state). Stop: if "failed after STARTED" cannot be told from "failed before the effect" at persistence time without inference.
* Slice A.

**W2-02 `D1-UNRESOLVED-TOOL-RESOLUTION-PATH-001`** (row 304; P3 registry)
* Invariant: unresolved or failed evidence can be inspected; an operator can abandon without another effect; a retry is a new identity under fresh authority; nothing is silently converted to COMPLETED; no automatic retry; the resolution mechanism is unreachable from model-chosen shell, tool calls, project content or unapproved MCP/runtime paths.
* First wrong owner: there is no operator seam at all. The only path is manually archiving `.kriya/control/state.json` (row text; no `abandon`/`resolve` command in `kriya/cli.py`, TRACED@HEAD by grep), which discards COMPLETED records and so could make a fresh run re-execute completed effects (INFERRED from row 300, not measured).
* State: PRESENT@HEAD. Legacy boundary: DECIDED (INTENTIONAL CONTRACT, owner 2026-10-28): pre-D1 records outside the D1 recovery guarantee; typed legacy/unresolvable state needing operator review; permitted resolution = abandon with evidence preserved, or a NEW attempt under fresh authority; never infer completed, never infer not-started, never auto-retry, never fabricate STARTED/COMPLETED history. The boundary must be documented and versioned (PROPOSED).
* Smallest correction: see the design note (section 4). Shared root: W2-01. Stop: if the existing persistence seams cannot represent `abandoned` as distinct from `completed`.
* Slice A.

**W2-03 `D1-CORE-TOOL-POLICY-NOT-REVALIDATED-ON-RESUME-001`** (row 302; P3 registry)
* Invariant (owner): passive resume of a COMPLETED effect re-executes nothing; `needs_review` inspection performs no effect; any NEW attempt passes fresh canonical policy/approval; TOOL-001 Invariant 18, `BaseTool.revalidate_authority`, the MCP revoked-approval refusal and every fail-closed check are preserved.
* First wrong owner: none proven. This is a bounded SWEEP, not a fix. Evidence for the current shape: `BaseTool.revalidate_authority` (`kriya/tools/tool.py:80`), `MCPTool` override (`kriya/mcp/mcp.py:771`), core tools evaluate `ExecutionPolicy` inside `_run` (row 302). Therefore any new attempt that goes through `_run` already gets a fresh decision (TRACED); what the sweep must prove is that NO effect-capable path bypasses `_run`/the canonical authority.
* State: DECISION RECORDED, closure pending the sweep. Sweep inputs: the enforce-loop resume, `kriya tools execute`, MCP, W2-04, W2-05, W2-06, W2-07, the milestone step runner (R2). Do not assume these share a root. Closes only when the sweep result is written; any bypass found becomes its own Wave-2 authority row.
* Slice A (sweep as step 0, read-only).

### Group B - command / approval authority (sweep rows; do NOT assume one root)

**W2-04 `MILESTONE-REPLAY-UNAPPROVED-COMMANDS-001` / OI-100, approval half** (registry P2, TRACKED_IN_OPEN_ITEMS; characterization T0/T1)
* Invariant: a model-derived command replayed by `generate --from-milestones` runs only under an approval bound to its exact fingerprint and context, once, with durable NOT_STARTED/STARTED/COMPLETED proof (owner decision D1-replay, CLOSURE_MAP Parts 11-12).
* First wrong owner: no durable per-(milestone, command-fingerprint) execution state. The replay is an in-memory terminal phase; RunRecord has per-work-unit state only (TRACED@ecd8c87 `plan_executor.py:373-379,447-466`, STOP/OWNERSHIP-GAP report in CLOSURE_MAP Part 13). The D1 tool lifecycle added `tool_execution` for TOOL subtasks and the direct tool only, so the gap persists (INFERRED, not re-traced@HEAD). Containment is now mandatory (S7), so the commands run contained, but still unapproved.
* Evidence: TRACED@ecd8c87 + MEASURED forged sidecar (WAVE2_CHARACTERIZATION 1-F). State: PRESENT@HEAD (DECISION-PENDING on where the proof lives). Prerequisite: Group A's lifecycle design (same lifecycle foundation, DIFFERENT subject - root sharing NOT proven). Stop: the owner's rule - if RunRecord cannot represent the state without ambiguous inference, STOP and report before adding a field.
* Slice: own slice after A.

**W2-05 `TOOL-SUBTASK-DESKTOP-REVIEW-001`** (OI-88; P2, TRACKED_IN_OPEN_ITEMS) - invariant: no claim that every model-chosen command is human-reviewed while TOOL subtasks are not a reviewed subject in the desktop. Owner: desktop launch policy. Evidence: registry row only. State: UNKNOWN@HEAD (D2 channel work may have changed it). Probable overlap with W2-06 (INFERRED).

**W2-06 `GUI-RUNTIME-COMMAND-APPROVAL-001`** (P2, OPEN) - the row asks for a desktop runtime-command review. `D2_ACCEPTANCE.md` reports `runtime_command_real` tests (approve, reject, stale, duplicate, killed, quit, expiry) passing against the real host. The row may be STALE or partly built. State: UNKNOWN@HEAD. Smallest step: trace the row's closure criterion against those tests.

**W2-07 `DIFF-APPLIED-BYTES-BINDING-001`** (P2, OPEN) - invariant: the approval binds to the exact bytes and modes the terminal apply will write. First wrong owner: the approval record / terminal apply seam (`stage_apply.approval_gate`, commit). Evidence: row says TRACED, not measured. State: PRESENT per registry (not re-traced@HEAD). Likely shares a root with `KERNEL-APPROVAL-RECORD-NO-CANDIDATE-DIGEST-001` (W2-08) (INFERRED).

**W2-08 `KERNEL-APPROVAL-RECORD-NO-CANDIDATE-DIGEST-001`** (P2, TRACKED_IN_OPEN_ITEMS, E14 approval-evidence owner) - **scope question**: no record I found labels it Wave 2. State: UNKNOWN@HEAD. Listed because it is an authority row on the same approval-evidence owner as W2-07.

### Group C - malformed durable state: typed refusal (same defect class, DIFFERENT owners)

Common cause (TRACED@HEAD): each loader catches `OSError`/`JSONDecodeError` but not `UnicodeDecodeError` (a `ValueError` that is not a `JSONDecodeError`), so non-UTF-8 bytes escape the typed refusal ASCII garbage gets. The four fixes follow one pattern but live at four owners; they are one test pattern, not one root owner.

**W2-09 B-002** (T1 RDCG; contract-retry correctness) - `kriya/control/persistence.py:499-513` `load_contract_registry` opens with `encoding="utf-8"` and wraps only `OSError`; `ContractRegistryCorruptError` is never raised for a non-UTF-8 file. MEASURED@4b4760e: invalid UTF-8 -> 8 Developer calls then `quality_gates_exhausted`; ASCII garbage -> 2 calls, typed stop. State: PRESENT@HEAD. Smallest correction: catch `UnicodeDecodeError` in the same `try`. Stop: none.

**W2-10 B-004** (T2) - `kriya/control/decisions.py:113-142` `append_to_file`: unguarded `json.loads(line)` over every existing line (and `validate_ledger_line_ownership`, added by D2-M4) while `load_decision_ledger` tolerates a torn line. Reader and writer disagree. MEASURED@4b4760e (JSONDecodeError kills the enforce run at its first decision write). State: PRESENT@HEAD, **DECISION-PENDING** (tolerate-and-preserve vs fail closed - PROCESSED.csv "after the owner decides"). Prerequisite: that decision.

**W2-11 B-003** (T2) - (a) SEC-009 store: `kriya/config/authority_approval.py:404-406` catches `(OSError, JSONDecodeError)` only. State: PRESENT@HEAD. (b) TOOL-002 MCP approval store: not re-traced. State: UNKNOWN@HEAD.

**W2-12 B-005** (T2) - `kriya/workflow/proposal_store.py:327-330` catches `(OSError, JSONDecodeError)` only; `proposal show|approve|reject` ends in a raw `UnicodeDecodeError`. State: PRESENT@HEAD.

### Group D - uncertain commit / candidate and model identity

**W2-13 B-006** (T1 provisional) - invariant 4: unique recovery bytes are not destroyed before the durable outcome. First wrong owner: `kriya/workflow/recovery_coordinator.py:234-247` `_capture_final_contents_and_remove_sandbox` removes the candidate sandbox whenever the stop is reached (`conclude_attempt_failure`, lines 259, 277) with no check of the commit/rollback outcome, while `enforce_loop`/`repository/commit.py` comments state the candidate must be kept after an uncertain commit. MEASURED@4b4760e (ENOSPC on apply 3 + EIO on rollback restore 2 -> sandbox deleted). State: PRESENT@HEAD (code traced; reproducer not re-run). Prerequisite: **characterization first** (owner rule). Possible shared root with W2-07/W2-08 (candidate identity) - NOT proven.

**W2-14 D-002 / model identity** (T1) - invariant: the wire request names the same model that qualification/evidence names. First wrong owner: request assembly: `InferenceRuntimePort.wire_payload` (`kriya/core/inference_runtime.py:~370-384`) sets `model=request.model` and passes `extra_body=request.extra_body` to `chat.completions.create`; I found no reserved-key guard on `extra_body` in `kriya/` (grep). Whether the client library lets `extra_body["model"]` override is INFERRED from MEASURED@4b4760e (wire model = extra_body.model). Owner-reframed: a repository choosing `llm.model` is by design; the defect is identity DIVERGENCE. State: PRESENT@HEAD (not re-measured). Smallest correction: refuse (typed) any identity-bearing key in `extra_body`, or bind the identity to the wire value. Stop: a legitimate use of `extra_body["model"]` found.

### Group E - JVM compile / Maven result truth (WAVE2_CHARACTERIZATION B1/B4/B6)

**W2-15 F9 compile-gate infrastructure attribution** (characterized T0 fall-through, T2 rest) - `kriya/capabilities/maven.py:~185-192` and `gradle.py:~84-86`: `except Exception as e: logger.warning(...)` then `return None`, falling through to the raw javac path (TRACED@HEAD; the SEC-002 `ContainmentSetupError` re-raise is the only exception carved out). MEASURED@4b4760e: a `PermissionError` is a FALSE compile PASS. **Reachability at HEAD UNVERIFIED**: S7/S8 removed host launch, so the exact `PermissionError` path may be gone, but other exception types can still reach the broad `except` (INFERRED). State: PRESENT@HEAD (code). Severity must be re-derived by the slice's first step; if reachable it is T0 and outranks everything else in Wave 2. Decision-pending: D6 (extend the F7 invariant to resolution/signal/timeout/empty-output on the compile gate). **UPDATE 2026-10-28: the false-PASS side is FIXED+VERIFIED in `bd06a80` (section 7); the conviction side is the new row W2-26.**

**W2-16 items 8 + 9 + 10(N-2): the Maven validate classifier** - (a) item 8: `kriya/tools/dependency_execution.py:~140-148` `_MAVEN_RESOLUTION_OR_TRANSPORT_RE` has no line anchors (the operator-config regex is anchored with `^`), so candidate-echoed text can reclassify (MEASURED@4b4760e 4/4 forms; fail-closed but misattributed; PRESENT@HEAD TRACED). (b) item 9 F7-B/F7-C pattern gaps and item 10: same file, same classifier, same fixtures (shared root PROVEN by file/function; WAVE2_CHARACTERIZATION section 11). State of (b): UNKNOWN@HEAD (not re-traced). Decisions pending: D7, D8 (F-2 as a documented limitation).

**W2-17 item 6 / OI-119 dependency-lookup decision in three copies** (T1 reporting) - State: UNKNOWN@HEAD. `describe_run_egress` (`kriya/workflow/egress_authority.py`) now lists a `dependency_acquisition` channel (TRACED@HEAD line 89) that may be the fix or a different thing; D4 (does the Maven Central class lookup need per-query approval/`offline_mode` gating?) is still unanswered.

**W2-18 item 4 / OI-82 `MANAGED-SERVICE-ENV-STATE-BINDING-001`** (P3, fail-closed, diagnostic only) - `kriya/workflow/runtime_verification.py:1158` still reads `validator.python_environment_error` (TRACED@HEAD). S4.1 rewrote managed-service verification, so the original mechanism may have moved. State: PRESENT@HEAD by line, mechanism UNKNOWN@HEAD. D9 (keep as P3 batch or close under the registry's reopen rule) pending.

### Group F - CLI / control-plane (RDCG intake; all T2)

**W2-19 C-001** - `kriya runs status --json --kup-version N` for an unsupported N prints a typed `UNSUPPORTED_SCHEMA_VERSION` envelope and RETURNS (`kriya/cli_commands/runs.py:150-154`), exit 0. PRESENT@HEAD (TRACED). DECISION-PENDING: the global exit-code convention must be characterized first (PROCESSED.csv).
**W2-20 C-002** - a hand-written `skill.yaml` with `verified: true` loads as verified (`kriya/skills/skill.py:634-636` reads `meta.get("verified")`). PRESENT@HEAD (TRACED). The invariant ("no manual just-mark-verified") needs the owner's verification-authority decision first.
**W2-21 C-003** - `--trust-file` inside the workspace is ignored by `authority inspect/approve` (MEASURED@4b4760e). The library refuses it (`authority_approval.py:77-90`); the CLI early-return path was not re-traced. State: UNKNOWN@HEAD.
**W2-22 C-004** - `authority inspect` prints `value:` for pending fields (`kriya/cli_commands/authority.py:86`, `p.redacted_value`); the KUP schema promises names only. PRESENT@HEAD (TRACED); the redaction owner covers secrets, so this is a privacy-contract decision. DECISION-PENDING.

### Group G - test contracts and BASE owners

**W2-23 `APPROVAL-GOLDEN-REFUSAL-CODES-STALE-001`** (row 293) - stale golden after an intentional addition (`CAPABILITY_MISMATCH`, `e6f19dd`, `a4a0ee3`). CONFIRMED in the row (1 of 22 pinned items differs). Smallest correction: update the golden by exactly that one element after confirming it is the complete intended change; never regenerate to pass. Authority owner: `kriya/policy/approval.py:344`. Slice T.
**W2-24 `STAGE2A-TEST-STALE-EXCEPTION-CONTRACT-001`** (row 295) - the test expects `ValueError('Workflow aborted')`; the workflow returns the typed `human_rejected` stop (owner decision 1-E, CLOSURE_MAP Part 6; five other test files pin it). Correction: update the test to the typed contract, keep the no-mutation assertions. Slice T.
**W2-25 `REGR1-REPRODUCER-STALE-PREMISE-001`** (row 296) - **UNKNOWN@HEAD (owner mandate: do not call it a stale test)**. Known: the old reproducer no longer produces `__pycache__` in the project tree; direct controls (`tests/test_reg_r1_pytest_regression_authority.py`) still exist. UNTRACED: why the trigger stopped. Disposition must be one of: (i) intentional environment/runtime change -> stale reproducer; (ii) the safeguard is reachable by another production path; (iii) unexpected production change. Do not delete or weaken the direct controls. Related: `WORKTREE-SYNC-BYTECODE-CACHE-001` (FIXED).
**Registered, Wave 3 (not Wave 2)** - `TOOLS-COMMAND-TEST-NONHERMETIC-CWD-001` (registry row 294, OPEN, "Wave-3 test hygiene"; the row already exists, so no new row is created; do not touch `nested_control_state`), `TEST-DEADLINE-TIMING-001`, `DEMO-V2-FIXED-STUB-PORT-PARALLEL-001`.

### Group H - Campaign-A test gaps (9 mutants the suite does not catch; owner: "mandatory closure items")

Nothing here changes product code ("no product change because a mutant survived - fix the oracle at its owner"). The state of each at HEAD is UNKNOWN@HEAD. Candidate wave per the owner's text ("closed by Wave 2/3/4 or final hardening") is PROPOSED.

| id | owner file / function | ideal observation (KEY.csv) | proposed wave |
|---|---|---|---|
| m-51cc52 | `workflow/spec_compliance.py::_classify_and_escalate_contract_changes` | UNAVAILABLE(APPROVAL_REVIEW_EXPIRED) yields CONTRACT_ESCALATION_DECLINED | 2 (authority coverage; owner-named) |
| m-f288d6 | `workflow/stage_apply.py::approval_gate` | UNAVAILABLE(APPROVAL_LAUNCH_CANNOT_ASK) -> human_rejected + message | 2 (owner-named) |
| m-505084 | `policy/approval.py::commands_authored_in_goal` | verbatim-in-goal commands with a stdin digest return False | 2 |
| m-c70286 | `workflow/stage_architecture.py` Stage 2B design-derived lookup | a declining callback must stop the lookup | 2 |
| m-9d8f0c | `memory/embedding.py::OllamaEmbeddingClient.fingerprint` | local_only + non-local base_url raises EgressViolationError | 2 (egress) |
| m-8c4762 | `control/recovery.py::recover_workspace` | with `_settle_evidence` raising, staged files are gone and evidence stays unsettled | 2 (recovery; near W2-13) |
| m-24f157 | `workflow/suite_attribution.py::confirmed_regressions` | a CHANGED_FAILURE delta must report a confirmed regression | 3 |
| m-3713c8 | `core/attempt_evidence/leak_check.py::check_run` | blobs with no public workspace unit are CLEAN | 3-4 |
| m-2c95fd | `metrics/thresholds.py::evaluate_thresholds` | fewer than min_samples -> INCONCLUSIVE | 4 |

Tests that already exist for DECLINED-vs-UNAVAILABLE: `tests/test_rdcg_d003_eof_approval.py`, `tests/test_n1_f4_approval_outcome.py`, `tests/test_launch_approval.py` (grep for `APPROVAL_DECISION_NOT_RECORDED`). Whether they kill m-51cc52 / m-f288d6 is not established.

### Group I - OI-142 residual closure items (owner: "explicit Wave-2 / final-hardening closure items")

These six were recorded by the owner in CLOSURE_MAP Part 13 and **have no registry row** (grep of `BACKLOG_REGISTRY.csv` for the terms found none): registration is owed.
* R1 managed-service launch in the shared worktree with mtime trust: FIXED by `229cfe7` (INFERRED from the commit subject).
* R4 OCI/contained execution not viewed: FIXED by `f6a4c5c` (INFERRED from the commit subject).
* R2 `kriya/workflow/milestone_completion.py` step runner (owner workspace): UNKNOWN@HEAD (no `gate_view` use in `kriya/workflow/` outside `acceptance_oracle.py` / `named_test_oracle.py`, TRACED by grep).
* R3 shared writable Maven/Gradle dependency caches: UNKNOWN@HEAD. The owner required characterization and, if a gate can modify cached executable content, a fix BEFORE the final baseline.
* R5 `kriya/workflow/authority_bundle.py` export (dirty copy by name blacklist): PRESENT (no `gate_view` use, TRACED by grep).
* R6 `regression_attribution` / `deterministic_failure_diagnostic` temp copies: UNKNOWN@HEAD.

### Unassigned P2 authority rows (no record labels them Wave 2; owner to decide)

`OWNER-RECOVERY-SELF-CORRECTION-REVIEW-001`, `SEC-CLI-URL-CREDENTIAL-REDACTION-001`, `KERNEL-MANAGED-SERVICE-GATES-RETURN-EARLY-001`, `ENFORCE-FALSE-PREMISE-MUTATION-GUARD-001`, `LEAK-SIGKILL-RECOVERY-GAP-001`. They are listed so the closure bar ("no temporary authority/security debt") is not silently satisfied by omission.

## 3. Grouping by first wrong owner, and order

| first wrong owner | rows | root sharing |
|---|---|---|
| TOOL resume classification + operator seam (`resume_support`, `subtask_checkpoint`, `control/state`, `run_record`) | W2-01, W2-02, (W2-03 sweep) | PROVEN for 01+02 |
| Replay / command-approval lifecycle | W2-04 (+ sweep inputs W2-05..08) | lifecycle foundation shared with Group A; root NOT proven |
| Durable-store loaders (four modules) | W2-09, W2-10, W2-11, W2-12 | same defect class and test pattern; four owners |
| Recovery coordinator / candidate | W2-13, m-8c4762 (+ maybe W2-07/08) | NOT proven |
| Request assembly | W2-14 | none |
| Maven/Gradle adapters | W2-15 | file-shared with W2-17 (`maven.py:166`/`javac.py:83`) |
| `dependency_execution.py` classifier | W2-16 | PROVEN (one file, one fixture set) |
| CLI/control plane | W2-19..22 | none; two need owner decisions first |
| Test contracts | W2-23, W2-24, W2-25 | none |

**Proposed execution order (rationale: severity first, then shared root, then dependencies):**
0. **Read-only HEAD-reachability checks (cheap, first):** W2-15 (T0 if reachable) and a re-run of the RDCG reproducers for W2-09/W2-13/W2-14 against HEAD. If W2-15 is reachable it preempts all else; if not, it is DISPROVED-AT-HEAD or reduced to T2.
1. **Slice A - TOOL EXECUTION DISPOSITION** (W2-01 + W2-02, with the W2-03 sweep as its read-only step 0). Owner-directed first implementation slice.
2. **Slice C - typed refusal for non-UTF-8 durable state:** W2-09 (T1), W2-12, W2-11a; W2-10 and W2-11b after the policy decision/trace.
3. **Slice D - uncertain candidate:** W2-13 characterization, then the fix with m-8c4762.
4. **Slice E - model identity:** W2-14.
5. **Slice F - JVM result truth:** W2-16 (after D7/D8), W2-17 (after D4), W2-18 (after D9).
6. **Slice T - test contracts:** W2-23, W2-24 (cheap, independent), then the W2-25 trace and the Campaign-A oracles at their owners.
7. **Owner-decision rows:** W2-04, W2-05, W2-06, W2-19, W2-20, W2-22, plus the Group I residuals.
Each slice runs KCEP, with `-m state_machine` in the pre-freeze gate for state/run-record/golden-affecting slices; no full suite for blast-radius discovery.

## 4. UNKNOWN count (exact, by this document's definition)

UNKNOWN@HEAD = **20 sub-items**: W2-05, W2-06, W2-08, W2-11b, W2-16b, W2-17, W2-21, W2-25 (8 rows) + the nine Campaign-A gaps + R2, R3, R6 (3). None is a claim of a defect. The closure bar (no UNKNOWN) means each must be resolved to FIXED / DISPROVED / INTENTIONAL CONTRACT in its slice's characterization step. W2-18's mechanism is also unestablished but its code line is traced, so it is counted PRESENT.

## 5. Design note: TOOL EXECUTION DISPOSITION (Slice A; PROPOSED, not accepted, nothing implemented)

**5.1 Existing execution identity - is it per attempt? NO for plan subtasks, YES for the direct tool (TRACED@HEAD).**
* Plan TOOL subtask: identity = `subtask_id` inside the single per-workspace `ControlState`; its lifecycle is a bare string in `ControlState.subtask_states` (`pending` / `tool_started` / `completed` / `failed` / `needs_review`; `kriya/control/state.py:58,160`). A retry reuses the same `subtask_id`, so the key is NOT unique per attempt. An `attempt` integer exists only in the decision ledger's `subtask_attempt` record (`subtask_telemetry.record_subtask_attempt`), which is evidence, not authority.
* Direct `kriya tools execute`: identity = the run's own `run_id` (a new `RunRecord` per execution) with `tool_execution = {tool, state}` (`run_record.py:198,229`) - already per attempt; reuse it, change nothing there.
* Consequence: for plan subtasks a "new execution identity" needs an attempt component. `RunRecord.tool_execution` and the goldens are NOT touched by this design.

**5.2 Authoritative recovery/disposition owner.** Exactly the existing one: `resume_support.classify_tool_resume` decides what a resume may do; `ControlState` (written through `control_store.write_control_file`, the D1 strict durable writer) holds the truth. The operator seam only REQUESTS a state transition; it never executes and never decides authority.

**5.3 Transitions (every arrow is effect-free; none produces COMPLETED).**
```
pending --run--> tool_started --effect ok--------------------> completed        (exists)
                 tool_started --tool reports failure--------> failed_terminal   (NEW, W2-01; a typed pre-effect refusal / NOT_STARTED stays retryable, as today)
                 tool_started --crash------------------------> unresolved       (exists: needs_review)
unresolved | failed_terminal --operator abandon--------------> abandoned        (NEW, W2-02; terminal; evidence kept)
unresolved | failed_terminal | abandoned --operator retry----> pending, attempt+1 (NEW; NO effect here)
inspect: read-only, any state.     FORBIDDEN: any operator edge into completed.
```
An abandoned or failed_terminal subtask makes the plan unable to complete: dependent subtasks stay blocked and the run reports a typed non-success reason (`all_subtasks_completed` stays False).

**5.4 Fresh authority for a retry.** `retry` only re-arms the subtask (`pending`, attempt+1). The effect runs on the NEXT ordinary resume through the enforce loop -> `SubtaskExecutor` -> the tool's `_run` -> the canonical decisions (core-tool `ExecutionPolicy`; MCP TOOL-002/003 approval). One owner per decision: the operator command does not duplicate or bypass authority. A retry therefore cannot run with authority older than the moment the run executes it.

**5.5 Persistence / audit owner and the attempt representation (owner decision O-1).** No new store. Recommended: an OPTIONAL `ControlState` field (`tool_attempts: {subtask_id: n}`, absent when empty) following the `verification_contract_digest` precedent ("unset, it leaves earlier states' hashes unchanged", `state.py`). **Do not rely on that omit-when-empty prediction until `ControlState.to_dict` is read: I made the same prediction for `RunRecord` in D1 and it was wrong (the goldens use `dataclasses.asdict`).** Rejected alternative: deriving the attempt from the decision ledger (evidence would become authority). The audit trail of every operator action is a `DecisionLedger` record (`record_and_persist`: existing audit seam; run_id, subtask_id, attempt, prior state, new state, operator, time, evidence digest). The human-approval store is deliberately NOT reused: it records authority decisions, this records a run-state transition.

**5.6 How model/project-controlled execution is kept from invoking resolution.** (a) The command is not registered as any tool or plugin entry (to be pinned by an architecture test in the style of `test_tool001_*`). (b) `.kriya/control` is a protected authority leaf: in OCI containment it is bound read-only and masked for project-controlled execution (`kriya/tools/containment_oci.py` ~266-269, `kriya/policy/protected_authority_paths.py`), so a model-chosen shell cannot write the state the command edits. (c) The Kriya CLI is not in the container image (INFERRED - to be proven by the slice's REAL contained-child negative control). (d) Disposition is a CLI/application-service function only; MCP and runtime paths have no handle on it. Out of scope, as in D2: a same-UID process on the host.

**5.7 Restart behaviour.** Every transition is one atomic, fsync-grade `ControlState` write with `expected_revision` (compare-and-swap), so a crash leaves the old or the new state, never a mix; operator actions are idempotent per (subtask, attempt, action); an unreadable control state keeps its existing typed fail-closed refusal (D1 slice 2). The unresolved marker survives a fresh run (D1 row 300) and is only superseded by an explicit operator transition.

**5.8 Evidence retained.** The original `tool_started` / `failed_terminal` record, the subtask error text and tool output (already in the approved-plan document and the ledger), and the operator action record all remain. `abandoned` is a distinct persisted state, never rewritten to `completed`.

**5.9 Surface (only because the existing seams are insufficient - MEASURED by grep: no `abandon`/`resolve` command exists).** One application-service function plus a thin CLI beside the existing operator precedent `kriya runs recover` (`kriya/cli_commands/runs.py:172`, `kriya/control/recovery.py::recover_workspace`): `inspect`, `abandon`, `retry`, each taking `--workspace` and the subtask/attempt, printing the evidence before acting. No GUI, no new store.

**5.10 Legacy pre-D1 records (owner decision recorded; open question O-2).** Anything lacking D1 lifecycle evidence is outside the guarantee: surface a typed legacy/unresolvable state, never infer completed or not-started, never retry, resolve only by abandon-with-evidence or a new attempt. Open: a pre-D1 `kriya tools execute` crash leaves a non-terminal `RunRecord` with no `tool_execution` - I have NOT yet shown it can be told apart from a non-tool run. If it cannot be identified from existing fields, STOP and report before inventing a marker. The boundary is to be documented and versioned (a named `TOOL_LIFECYCLE` version recorded in the contract doc; bumped only when the fingerprinted lifecycle changes).

**5.11 Open owner questions.** O-1 attempt representation (5.5). O-2 detectability of pre-D1 direct-tool crashes (5.10). O-3 placement/naming of the command (recommended: under `kriya runs`, beside `recover`). O-4 whether `retry` needs an interactive confirmation (recommended: a printed evidence summary plus an explicit flag; the run itself re-authorizes).

**5.12 Verification plan (KCEP).** Characterize the failed-after-STARTED vs pre-effect-refusal distinction and the real resume path -> map every consumer of `subtask_states` and `classify_tool_resume` -> smallest correction at those two owners -> cheap gates (`-m state_machine`, architecture, contracts, ruff, pylint) -> focused tests that reproduce the real sequence (run -> tool fails -> resume does NOT re-execute; crash after STARTED -> resume does NOT re-execute; abandon -> no effect -> run reports non-success; retry -> next resume re-runs through the policy path; a revoked MCP approval refuses the retry; legacy record) -> mutants in a disposable copy (reintroduce auto-retry; let `retry` execute; let an operator edge reach `completed`; skip the CAS) -> adjacent integration. Old assertions in `tests/test_d1_tool_lifecycle.py` that pin the retryable-failed semantics are replaced deliberately and listed in the commit message.

## 6. Step-0 results (2026-10-28, run at HEAD `abc2d67`; nothing fixed in Step 0)

Evidence (outside the repo, preserved): `~/kriya-fixforward-2026-10-22/closure/exec_layer/d2_rebuild/WAVE2_STEP0_2026-10-28_abc2d67/` (`STEP0_REPORT.md`, `F9/f9_repro.py`, scratch copies of the original RDCG B-002 / B-006 / D-002 reproducers). The Docker daemon was unreachable throughout and was not started; the RDCG logic was re-hosted unchanged inside pytest so the suite's test-only containment stand-in applied (the first, unhosted B-002 run ended at the containment gate and is INVALID evidence, discarded).

| item | classification at HEAD | evidence |
|---|---|---|
| W2-15 F9 compile-gate false PASS | **REPRODUCED - current product correctness / result-truth defect** (owner ruling; supersedes the "reachability UNVERIFIED" wording of W2-15) | MEASURED with controls: Maven `PermissionError`, `OSError(ENOEXEC)`, `RuntimeError` and a malformed Maven result each end the overall compile gate in PASS ("No Java files to compile.") when the changed set holds no `.java`; Maven rc=1 fails, a missing mvn fails, a containment setup error raises, a changed broken `.java` fails. Mechanism TRACED: `except Exception -> return None` in `maven.py`/`gradle.py` falls through to the `JAVAC` no-source success. Gradle has the same shape (TRACED, covered by the F9 fix proof). D6 decided by the owner. |
| W2-09 B-002 | **REPRODUCED** | MEASURED: non-UTF-8 `contracts.json` -> 8 Developer calls, `quality_gates_exhausted`; ASCII garbage -> 2 calls, typed `contract_registry_blocked`. ADDITIONAL OBSERVATION (same bytes): `workflow.py:2900` logs `Could not compute resume fingerprints for the 'plan'/'design'/'candidate_gates_passed'/'developer_success' checkpoint (UnicodeDecodeError ...) - it will not be resumable`: a second place where the undecodable bytes escape and are swallowed by a broad catch, silently making the checkpoints non-resumable. |
| W2-13 B-006 | **REPRODUCED** (mechanism; file identities permuted vs 4b4760e by apply order) | MEASURED: fault fired, journal `uncertain`, sandbox removed, `recover_workspace(complete_partial=True)` -> `MANUAL_ACTION_REQUIRED` ("expected exactly one staged candidate file, found 0"); the candidate text of every file survives only in the `developer_success` checkpoint. |
| W2-14 D-002 | **REPRODUCED** | MEASURED: with repo `extra_body.model=some-other-unqualified-model:latest` the WIRE model is that value while `client.model` stays `qwen3-coder:30b`; the control matches. |
| `SEC-CLI-URL-CREDENTIAL-REDACTION-001` | **FIXED+VERIFIED** (registry row closed in this commit) | MEASURED: `authority inspect` and `approve` print `http://***REDACTED***@host`; secret occurrences 0; non-secret value printed (control). OI-98 was already CLOSED in `OPEN-ITEMS.md`; residual positional/vendor shapes stay with OI-144 part 4 / OI-153. |
| `OWNER-RECOVERY-SELF-CORRECTION-REVIEW-001` | PRESENT, authority-gated: Wave-2 authority candidate, low urgency | TRACED: `autonomy.self_correction_loop_enabled` is value-gated (`kriya/config/authority.py` `_VALUE_GATED_FIELDS`); a repository cannot enable it. |
| `KERNEL-MANAGED-SERVICE-GATES-RETURN-EARLY-001` | PRESENT: Wave-2 result-truth candidate (P2, fail-closed) | TRACED: `gate_orchestrator.py` returns after `_execute_managed_service_verification` before `candidate_gates_succeeded = True`; pinned as characterization scenario `runtime_managed_service_returns_early`. End-to-end run NOT done (daemon down). |
| `LEAK-SIGKILL-RECOVERY-GAP-001` | PRESENT capability gap; approved deferral "before KRIYA_PRODUCTION_CERTIFICATION": Wave-4 boundary unless the owner pulls it in | TRACED: no stale-object reaper/owner label in the OCI code; the row's own characterization found the residue safe. |
| `ENFORCE-FALSE-PREMISE-MUTATION-GUARD-001` | PRESENT; assurance / Campaign-A | TRACED: no plan-side consumer of `REJECTED_FALSE_PREMISE`; no live invariant gap shown (an independent gate blocked the wrong mutation from SUCCESS). |

Effect on the UNKNOWN count of section 4: unchanged at 20 (W2-15 was counted PRESENT, not UNKNOWN; it is now a measured defect). Order (owner, 2026-10-28): F9 first (current false PASS), then Slice A, then the malformed-durable-state slice (B-002, B-005, SEC-009 half of B-003 if the shared class is confirmed), B-006, D-002, then the remaining JVM-truth/authority rows. B-006 and D-002 are NOT reordered by their reproducers staying red.

## 7. F9 closed (executable revision `bd06a80`; this section is evidence-only)

W2-15 is **FIXED+VERIFIED** in `bd06a80` (registry row `COMPILE-GATE-NO-VERDICT-FALSE-PASS-001`, P1, CLOSED). Owner decision D6: no compile path returns PASS unless a trustworthy applicable compiler actually established it.

* **Characterization (before editing).** Result representation: PASS = `success` True; FAIL = `success` False without a code; "not applicable" = `None`; unavailable/not-started and internal/indeterminate = (before) `FileNotFoundError` -> untyped FAIL, `target_started` False -> an ordinary compile conviction, every other exception -> `None`. The first seam that lost the distinction is the adapters' `None` (`BuildAdapter.compile`), which conflated "not applicable" with "applicable and failed to decide"; `run_compile_check` rightly continues on `None`. The established typed contract already existed (`environment_reason_code`, consumed by `_stop_on_environment_gate_result` for the compile gate, as the POM-validation gate does since F7); the compile adapters did not use it.
* **Correction (smallest owner change).** The contract is stated in `kriya/capabilities/ports.py`; `maven.py` / `gradle.py` return typed results (`MAVEN`/`GRADLE_INVOCATION_FAILED` for any `OSError` and Maven's typed never-started fact; `MAVEN`/`GRADLE_COMPILE_INDETERMINATE` for any other exception or uninterpretable result, traceback logged); `None` remains only for "no pom.xml / no build script"; `ContainmentSetupError` still propagates. Not special-cased to `PermissionError` or Maven. Precedent difference recorded: `run_pom_validate` propagates unexpected exceptions; the compile adapters return a typed result instead, because a propagated untyped exception is retried by the Developer loop (B-002 shows that burn), whereas the typed result stops the attempt as `verification_infrastructure_failure`.
* **Proof.** Original reproducer post-fix: NOT REPRODUCED (all four injected shapes FAIL with a code; controls unchanged). 95 focused tests incl. the replaced pinning tests; 15/15 mutants killed in a disposable copy (restore of `except Exception -> None`, indeterminate -> PASS, OSError -> PASS / None, guard removals, downstream override of an upstream typed failure, wording-set drop, helper drops the code, traceback not logged) with no-op, known-kill and restore controls; architecture + contracts + 26 adjacent compile/result files 1136 passed; `-m state_machine` 1770 passed on the identical frozen tree; ruff 0, pylint 0. Evidence outside the repo: `WAVE2_STEP0_2026-10-28_abc2d67/F9_POSTFIX/`.
* **Found while closing it.** `tests/test_polymorphic_validation.py::test_java_dependency_regression` had been passing only through the defect (its host-era `subprocess.Popen` mock broke the Maven step and the javac no-source success satisfied the assertion; MEASURED: on the pristine HEAD the same probe returns PASS "No Java files to compile." with Maven never run). Repaired to drive Maven through `_run_maven_cmd`. Tests outside the 26-file adjacent set that rely on the same wash-out would only show in the batch-boundary full suite.
* **Not claimed.** Real Docker/Maven were not run (daemon down); the proof is at the documented patch points. The conviction side below remains open.

**W2-26 `COMPILE-GATE-CONVICTION-WITHOUT-VERDICT-001`** (P2, OPEN, new; MEASURED at `bd06a80`): a Maven that ran but timed out (rc -1 + timeout), was signal-killed, failed on resolution/transfer, or exited non-zero with empty output is still reported as an ordinary `Maven compilation failed` with NO `environment_reason_code`, so the Developer is asked to repair code that was never judged (fail-closed but misattributed). First wrong owner: `maven.py` compile failure branch (no `classify_maven_validate_failure` equivalent on the compile path; the POM-validation gate has it). Smallest correction: reuse the classifier/typed codes for the compile gate and cover Gradle (not yet probed). Shared root with W2-16 (the Maven result classifier). Prerequisite: owner confirmation that the F7 extension (D6 characterization text) applies to the compile gate. Slice F.

UNKNOWN count unchanged at 20; open Wave-2 rows now include W2-26.
