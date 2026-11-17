# 13 - Final architecture reconciliation

**Date:** 2026-11-17. **Branch:** `implementation/closure-s0`. **Reconciled against:** `faf1751` (S8, the last slice) and the
reconciliation commit that carries this document. Slices S0 to S8 are closed; there is no S9. This document states what the code is
at the end of the closure program, what the earlier documents of this set still say that it no longer is, and the disposition of every
known item. It adds no design.

Evidence labels are those of `README.md` (`MEASURED`, `TRACED`, `INFERRED`). Evidence of this pass: `~/kriya-evidence/final-faf1751/`.

## 1. How to read this set after S0 to S8

| Document | What it is now |
|---|---|
| `00` to `05`, `08`, `09`, `10`, `GAP-LEDGER.md`, `REGISTRY-COVERAGE.md`, `REGISTRY-ROOT-MAP.md` | The AS-IS at `3eded93`, before any slice. Unchanged on purpose (they are the evidence the contracts were derived from) and each now opens with a baseline note. A row there that a slice changed is superseded by sections 2 and 3 below |
| `06`, `07` | The audit and the required contracts (TO-BE). `07` was corrected in place where an owner ruling changed a clause (S3, S4, and at this pass the C-08 resume clause: a resume continues the ledger or is refused; there is no new-budget choice) |
| `11`, `12` sections 1 to 8 | The owner's rulings and the specifications of S5, S6, S8 as written BEFORE implementation. Where a specification and its slice record differ, the slice record wins (each says so: 17 "no new schema", 20 "corrects section 6") |
| `12` sections 9 to 20 | What each slice measured, changed and proved. Authoritative for S0 to S8 |
| this document | The state at the end, and the dispositions |

One wording error of the slice records was found and corrected in place at this pass: `12` section 18.2 called exit code 3 "needs
review"; it is the knowledge-gap exit of proposal execution and `fix`, and every other non-success exits 1 (`TRACED`:
`kriya/cli_output.py`, the table pinned by `tests/test_s6_result_contract.py`).

`docs/user_guide.md` (new section 2.0g) and `docs/design.md` (new section 12) did not state the user-visible contracts of S4 to S8
(the outcome words and exit 5, the approval subject, the work ledger and the refused resume, the unit ceiling, the bounded budget
value, knowledge coverage, the refused project-authority paths). `MEASURED`: zero occurrences of `applied_requirements_open`,
`resume_refused` or `work ledger` in either file at `faf1751`. Both now state them.

## 2. The contracts at the end: what is implemented, what is not

"Owner" is the one module that decides. "Not built" lists every clause of the contract (`07`, and `12` section 6 for S5, S6, S8) that
no slice implemented; each has a registry row (section 5).

| Contract | Slice | Owner at the end | Implemented and proven | Not built |
|---|---|---|---|---|
| C-12, C-13 certification base, environment verdict | S0 `ec2bec1`, `ebc5b92` | `kriya/tools/validate.py`, the launch sites | A verification tool that never started and a managed-service launch the backend refused are typed environment verdicts, never candidate failures (`12` section 9) | The rows that need a real container daemon (group H); the tests that open the checkout's `./memory` (group G) |
| C-09 runtime identity, admission | S1 `b3a93dd` | `kriya/core/llm.py`, `model_runtime.py` | The binding is passed by the caller; an unknown model name is refused; production admission requires qualified models; the admission is annotated on the RunRecord (S6) | An explicit binding identifier (future) |
| C-10 prompt trust | S2 `57c966e` | `kriya/workflow/untrusted_context.py` | Fetched text is a typed untrusted reference, fenced, one consumer | - |
| C-05 project state | S3 `a6fa956` | `kriya/config/resolution.py`, `kriya/memory/project_store.py` | Project-derived stores are bound to one project identity; an unbound configuration opens none; the trace row records its workspace | Per-project history ownership for the desktop; the sibling name-pruning sites (group E) |
| C-06 knowledge, C-04 mutation | S4 `4bf42c1`, `9afec0e` | `kriya/skills/`, `kriya/policy/filesystem.py` | Storage presence is not knowledge (`SkillEngine.covers_library`); one skill writer, the shipped library read-only; project-authority paths refused at the write gate; planned write-granted MCP tools operator-only; one skill selection text | Rule-level skill relevance (future); the goal-named exception (intentional: none exists) |
| C-01 semantic contract | S5 `63c04ba` | `kriya/workflow/contract_compilation.py` (derived `semantic_state`), `requirements.py` (the one closure reader) | Nothing a model wrote closes a requirement the contract holds; an unmapped run-authored failing test is held for review. Derived form: not serialized, compiler version unchanged, no golden changed (owner correction) | The attended ambiguity question; Planner rendering of the state, the contradiction check against a `SPECIFIED` binding, INT-08, `GAP-A4-05` (group D) |
| C-02 result | S6 `7370d76` | `kriya/control/run_coordinator.py::evaluate_result_contract`, `run_record.py` | SUCCESS needs a resolved requirement truth on the RunRecord; `applied_requirements_open` (exit 5); closer parity at the enforce terminal; the final review advisory on every executor; INT-05 for `kriya fix`; one reconciled history row | Plan-validity convergence (truth 1), the project BEFORE at the enforce terminal verifier (group B); the C-11 labels, retention and "intact" wording (group A); repair after a terminal gap (future) |
| C-03 approval | S7 `db7bb38`, `b6ac020` | `kriya/policy/approval.py`, `kriya/workflow/terminal_commit.py` | An approval is sufficient only when bound to the exact final mutation subject (`bind_candidate(...).digest`, `BINDING_VERSION` 2), rechecked before the durable intent; cumulative triggers at the enforce terminal; the durable record on the commit cycle. At this pass: an invalid `sensitive_paths` pattern is a configuration error (INT-09) | Showing a mode or binary change in the approval surface (group I) |
| C-07 retry, C-08 budget | S8 `faf1751` | `kriya/control/work_ledger.py` on the run lease, `kriya/workflow/retry_policy.py`, `open_findings.may_open_failure_family`, `accepted_candidate.py` | One durable ledger; grant before work; a resume continues it or is `resume_refused`; unit ceiling 7; family continuity across invocations and resumes; accepted work resumes by replay through its own gates; the budget value is value-bounded (3600) | Per-grant events (future); `GAP-A1-05`, `GAP-A1-06`, `GAP-A1-08`, `GAP-A4-06`; the typed recovery-outcome vocabulary; persisted enforce recovery counters (bounded by the durable unit grant, `TRACED`) (group C) |

`TRACED` at this pass for the "not built" column: `enforce_loop.py` still compares `current_plan_hash` of a fresh plan
(`PLAN_CHANGED`, `GAP-A4-06`) and still holds the `prior_attempts` check (`GAP-A1-08`); no "unbounded" statement exists in the
preflight (`GAP-A1-06`); `terminal_gate_service.py` receives no project view; the KUP projection and `ui/shared` carry no "intact"
wording. `GAP-A1-05` was not re-verified.

## 3. The thirty-five slice gaps

| Gap | State at the end | By |
|---|---|---|
| `GAP-A1-01` blocker continuity | Fixed: allow-listed family predicate, continuity on the ledger (the owner ruled out a blocker registry) | S8 |
| `GAP-A1-02` retry state not durable | Fixed: the work ledger | S8 |
| `GAP-A1-03` no per-grant record | Not built; the ledger holds the counts. Future feature | - |
| `GAP-A1-04` no bound across invocations | Fixed: 7 invocations per planned unit, `WORK_CEILING_EXHAUSTED` | S8 |
| `GAP-A1-05`, `GAP-A1-06`, `GAP-A1-08` | Not built (`FINAL-RESIDUAL-C07-CLAUSES-001`) | - |
| `GAP-A1-07` non-candidate failure replenishes budgets | Fixed for the allowance (the predicate split). The finding-replacement half is unchanged by design | S8 |
| `GAP-A1-09` accepted unit work not durable | Fixed: snapshot and replay | S8 |
| `GAP-A2-01` no semantic contract before the verifier | Fixed in the derived form; the run ends applied-open instead of success | S5, S6 |
| `GAP-A2-02` verifier identification fails open | Fixed: unmapped is held | S5 |
| `GAP-A2-03` unattended / direct contract unstated | Stated: `12` section 17.3, user guide 2.0g | S5, this pass |
| `GAP-A3-01` success without verified requirements | Fixed | S6 |
| `GAP-A3-02` no repair after a terminal gap | Not built: future feature (the terminal non-success is the contract) | - |
| `GAP-A3-03` final review decides on two executors | Fixed: advisory on every executor | S6 |
| `GAP-A3-04`, `GAP-A3-05` approval not bound; per-unit only | Fixed | S7 |
| `GAP-A3-06` review text of another state | Not built (group A) | - |
| `GAP-A3-07` held-mode evidence before the gates | Not built (group A) | - |
| `GAP-A4-01` two executors, different truths | Fixed for requirement truth, closure, review and the result word; plan validity not converged (group B) | S6 |
| `GAP-A4-02` skill relevance | One selection text fixed (S4); rule-level relevance is a future feature | S4 |
| `GAP-A4-03` folder name as coverage | Fixed | S4 |
| `GAP-A4-04` web text outside the fence | Fixed | S2 |
| `GAP-A4-05` pattern miss deletes verification | Not built. Fail-safe since S6: a missed verification leaves the requirement open, never success (group D) | - |
| `GAP-A4-06` resume compares a fresh Planner answer | Not built: the resume is refused `PLAN_CHANGED`, fail closed (group C) | - |
| `GAP-A4-07` ambiguity not represented | Represented (`AMBIGUOUS`), refused or held; the attended question is a future feature | S5 |
| `GAP-A4-08` Planner integration decisions unreviewed | Not built (group D) | - |
| `GAP-A5-01` no project owner of state | Fixed | S3 |
| `GAP-A5-02`, `GAP-A5-03`, `GAP-A5-05` write gate, MCP write grant, skill learning | Fixed | S4 |
| `GAP-A5-04` repository may remove the time bound | Fixed | S8 |
| `GAP-A6-01`, `GAP-A6-02` production admission, unknown model | Fixed | S1 |
| `GAP-A6-03` unbound desktop records; "verified" | Row reconciled and attributed (S3, S6); the wording and the consistency reader not built (group A) | S6 |

## 4. Items resolved at this pass

| Item | Disposition | Evidence |
|---|---|---|
| `ENFORCE-FALSE-PREMISE-MUTATION-GUARD-001`, `S8-FALSE-PREMISE-CHARACTERIZATION-001` | **False success: DISPROVED.** A disposition removes a statement from the mandatory set and verifies nothing. `requirement_truth` over zero mandatory requirements is OPEN, never `ALL_VERIFIED`; a goal whose every statement is dispositioned is refused; one whose remaining claims pass at baseline is `NO_MUTATION_REQUIRED` before any model call on both executors; otherwise S6 requires the remaining requirements' own evidence. **Mutation of "the file the premise names": INTENTIONAL CONTRACT.** A disposition binds a requirement id and statement text and no path (owner ruling: no statement-to-file mapping). The authority that withholds a path is the goal's own `FILE_IMMUTABILITY` / `MUTATION_SCOPE` statement (refused at plan validation, `VIOLATED` on breach). No reachable authority hole was found; nothing was built | `TRACED`: `requirements.py::requirement_truth`, `workflow.py` and `enforce_loop.py` (the same baseline guard), `tests/test_backend_readiness_004_disposition.py` `test_02`, `test_10`. `MEASURED`: `tests/test_final_reconciliation.py::test_a_dispositioned_statement_is_never_a_success_by_itself`. Limit: a model may still rewrite a correct function when another mandatory requirement is unmet at baseline; that candidate is judged by the regression gates and the requirement truth, it is not prevented |
| `S6-STALE-SUCCESS-EXPECTATION-TESTS-001` | FIXED at two shared roots (`tests/_requirement_truth.py`): fake results state a resolved truth (`with_resolved_truth`); real scripted pipelines close their requirements at the one outcome owner (the `requirements_closed` fixture) | 45 representatives red at `faf1751`, then green (`carried_reds_before.txt`, `carried_reds_after_run1.txt`, `focused_gate_final_run*.txt`). Three of the 45 were other stale contracts, corrected at their own: the S8 resume decision word, the S7 terminal approval of the cumulative change, an unbound configuration double |
| `CERTIFICATION-MECHANICAL-CLEANUP-S3-001` | FIXED at the fixtures of its three named families: a test configuration is bound to its project (`for_project`), a fixture index is claimed through `claim_project_store` before it is filled, one lambda accepts the keyword S1 added | The focused gate measured the named files (37 red, the families as recorded), then `focused_gate_final_run2.txt`: 535 passed. Two more stale contracts surfaced in files the gate ran for the first time and were corrected at their shared helper: the D1 fault injector numbered the S8 work-ledger write as a TOOL transition (`tests/test_d1_tool_lifecycle.py::Faults`, used by three files; 39 passed), and a milestone unit run as the whole run now asserts `applied_requirements_open` |
| `S8-RESUME-FIXTURES-WITHOUT-LEDGER-001` | FIXED at `tests/test_workflow.py::_seed_checkpoint`: the checkpoint is saved inside a mutating run and that run's ledger is seeded | The three tests pass; no product change |
| `DESKTOP-APPLIED-OPEN-EXECUTABLE-COVERAGE-001` | FIXED. The first execution of the UI checks found a `tsc` error of the S6 change (no mark for the `applied_open` card status; compile gate only) and one stale test | `ui_shared_typecheck.txt` (red), `ui_typecheck_after_fix.txt`, `ui_vitest_after_fix.txt`, `ui_lint_after_fix.txt` |
| `REGISTRY-CSV-ROW-WIDTH-001` | FIXED: ten ragged records requoted in place, a width test with negative controls | `registry_width_fix.py`, `tests/test_backlog_registry.py` |
| `ACCEPTANCE-ENVIRONMENT-ERROR-FLAG-NO-READER-001`, `SEC009-AGENT-ROLE-REASON-TEXT-001`, INT-09 | FIXED (a readerless field removed; a refusal names the right reason; an invalid sensitive-path pattern no longer protects nothing) | `tests/test_final_reconciliation.py` |
| Rows whose slice had fixed them but which were still open | CLOSED with the slice's evidence on the row: the P1 `GENERATED-VERIFIER-CONTRACT-AUTHORITY-001`, `REQUIREMENT-CLOSURE-UNVERIFIED-RECORDED-AS-SUCCESS-001`, `INTEGRATION-PASS-REQUIREMENT-POLICY-001`, `MILESTONE-UNIT-REVIEW-REFUSAL-FOLD-UNMEASURED-001`, `RETRY-BLOCKER-IDENTITY-AUTHORITY-001`, `RETRY-STATE-RESUME-CONTINUITY-001`, `ENFORCE-UPSTREAM-WORK-PRESERVATION-001`, `KERNEL-APPROVAL-RECORD-NO-CANDIDATE-DIGEST-001`, `GUI-REVIEW-APPLY-BINDING-001`, `KNOWLEDGEGUARD-SKILL-IDENTITY-001`, `GUI-RUN-ATTRIBUTION-001`, `WAVE5-O2-GENERATED-TEST-WEAKENING-001` | Each row's `closure_evidence` |
| `REQUIREMENT-BEHAVIOR-CLAIM-NO-DETERMINISTIC-CLOSER-001`, `PROJECT-AUTHORITY-GOAL-NAMED-EXCEPTION-001`, `S8-WORK-LEDGER-RESIDUALS-001` (items 1 to 4) | CLOSED as INTENTIONAL CONTRACT under the owner's S6, S7 and S8 rulings | Each row |

Mutation survivors and equivalents known to the program: one, S6 M09 (the coordinator's own check that applied-open needs a
committed cycle; the RunRecord refuses the transition itself, M08 killed), recorded as a redundant second layer in `12` section 18.5.
No other survivor is recorded in `12` for S1 to S8 (S4's first mutant run had three; the tests were strengthened and one equivalent
mutant, a table already in folded form, was replaced). No flake is recorded as open for a deterministic test; the two timing rows are in group G.

## 5. The registry

`evidence/final/registry_final_disposition.py` gives one disposition to every non-closed row and fails when a row has none; its
output is `evidence/final/registry_final_disposition.md`. The classes of the rows the Phase-D root map classified are taken from that
map (`REGISTRY-ROOT-MAP.md`, ruled on by the owner on 2026-11-15); their registry status was not rewritten in bulk.

## 6. Open, measurement required (owner ruling 2026-11-17)

About fifty registry rows are real, known items that S0 to S8 did not close. The owner ruled at this reconciliation that they are NOT
forced into FIXED+VERIFIED, DISPROVED, INTENTIONAL CONTRACT or GENUINE FUTURE FEATURE before evidence exists: each stays
**OPEN - MEASUREMENT REQUIRED**, a temporary state with a measurement target, not a terminal disposition and not a new slice (there is
no S9 unless new measured evidence proves a new root defect). Each must end in one of the four classes on evidence. The rules of the
ruling:

- A row already PROVEN to be a correctness, authority, security or reliability defect is fixed before production readiness whether or
  not a scenario reaches it. Scenario non-observation is not disproof. Only a hypothesis or an uncharacterized risk may be decided by
  scenario measurement.
- Diagnostic and harness rows are classified by impact: display wording with no runtime branching may become intentional or deferred
  cleanup; a harness item that can false-pass, false-fail or misclassify certification is fixed before final certification; a dead
  hypothesis is disproved with evidence.
- After a clean deterministic certification the statement is "S1 to S8 architecture implementation complete, production validation
  OPEN". Production-ready is not claimed until these rows have terminal dispositions.

| Group | Content | Evidence today | Measurement target |
|---|---|---|---|
| A | C-11 diagnostic truth: labels, counts, message texts, retention owners, the "intact" wording | Each row `TRACED` with no control-flow reader found; none measured to change a result | Impact classification per row: whether any runtime branch or certification verdict reads it |
| B | Plan validity per executor; the enforce terminal verifier has no project BEFORE (P2) | `TRACED`; not reproduced as a wrong verdict. Since S5 a model verdict cannot close what the contract holds, so the known direction is "left open", not "wrongly closed" (`INFERRED`) | A deterministic reproducer on both executors; a differing verdict is fixed before production readiness |
| C | The MAY_CHANGE owner's answered no-change refused inside the unit (P2); rejection text lost across a model switch; triage on an orchestrator notice; the held-gates resume; four C-07 clauses | The P2 row is `TRACED` for forms ii and iii and bounded by the measured K2/K3 characterization (a typed stop: a safe failure); no deterministic reproducer of a wrong stop exists | A deterministic reproducer per row, the P2 row first; a reproduced defect is fixed before production readiness |
| D | Planner-side use of the semantic state, INT-08, `GAP-A4-05`; a fix unit gated by compile only | `TRACED`; fail-safe since S6 | A reproducer per item |
| E | Desktop history follows the state directory; sibling name-pruning sites; index paths from two source roots | `TRACED`, one uncharacterized | One probe per row |
| F | An untyped refusal at the guarded commit; one stale sentence in the qpid skill | `MEASURED` mechanism, reach `INFERRED`; `TRACED` | One reproducer; the Qpid scenario |
| G | Harness and coverage: tests that open the checkout's `./memory`; ResourceWarning hygiene; one hosted timing observation; a demo script race; no direct or enforce kernel golden ends SUCCESS; no scripted end-to-end positive `fix` | `MEASURED` at this pass: while the focused gate ran, `memory/dependency_graph.db-shm` in the worktree was touched (2026-11-17 15:31), so tests still OPEN the checkout's store (`KERNEL-TESTS-CHECKOUT-MEMORY-MORE-FILES-001` was not closed by S3). `TRACED`: since S3 a store that is not the configuration's own project is ignored (`project_store_status`), so a result can differ only between a checkout that holds such a file and one that does not | The certification run: the script moves pre-existing `./memory` and `./.kriya` into the evidence directory first and records what the suite creates. A harness item shown to change a certification verdict is fixed before certification is declared |
| H | Container-daemon behaviours: bind-source refusal after pathname recreation (P2), the runtime-gate venv without the project's test dependency (P2), the runtime-only venv bootstrap, gate-view cleanup, mask scratch, managed-service environment state | `MEASURED` symptoms on a real daemon, mechanisms not established; not reproducible in the deterministic suite | **The first measurement target of real-scenario validation** |
| I | Whether a non-HITL approval trigger holds execution; whether an applied-open run persists learned skills; a mode or binary change is bound by the approval subject but not shown | Policy questions | An owner decision, then its test |

No row of these groups is, on the evidence held today, a proven production defect of a result (a wrong success, an unauthorized
write, a security bypass): the one fail-open found at this pass (an invalid sensitive-path pattern) was fixed at its owner. If a
measurement proves one, it is fixed at its owner before the architecture baseline is declared complete.

## 7. Certification

One pinned command, run by the owner on the exact clean revision: `~/kriya-evidence/final-faf1751/certify_final.sh <sha>
<new-evidence-dir>`. It refuses a wrong HEAD, a dirty tree or an existing evidence directory, records the revision, tree, lock and
installed-package digests, interpreter, platform, Docker and Node versions, and runs `ruff`, `pylint`, the architecture, registry and
strict-double guards, the full suite (parallel, default markers: no live model, no live static analysis) and the UI typecheck, lint
and `ui/shared` tests on an exported copy of the same revision. Triage of any red is by family (A, B, C, D, E); A, B and an ambiguous
E block the exit.

Not run by this pass: the full suite, the whole state-machine tier, any live model, the `kup`, `test-host` and `standalone` UI test
workspaces, and the real-container tests on a loaded daemon.
