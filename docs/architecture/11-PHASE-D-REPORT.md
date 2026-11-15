# 11 - Phase-D report: architecture closure of Kriya at `3eded93`

Canonical runtime baseline: commit `3eded93c0cfb50f8855d04510e3f9aa2cd5208f7`, tree
`97a3da6b2ef664e2b7e25c4c6fe3fc3f42f253a0` (`main` = `origin/main`). Written on the branch
`architecture/closure-3eded93`, which changes documentation and evidence only. Nothing was pushed, merged or tagged.
No runtime code, test, golden or registry row was changed. No live model, qualification, certification, container,
MCP server or full suite ran. The implementation freeze is in force and this report does not lift it.

This is the one routine stop of the closure program. It asks the owner for the decisions of section O and for the
ruling of section S.

## Summary

**Is Kriya architecture-coherent now? No, and the report says exactly where.** The core that protects the user's
workspace is coherent and was confirmed by trace and measurement: one workspace identity, a write gate, a candidate
sandbox, a commit that refuses anything but the verified batch and blocks on an uncertain state, configuration
authority, tool governance, the provider contract. Around that core, eleven truths have no single owner or the wrong
one. They are the root issues R-01 to R-11. Nine of them can produce a wrong result, a wrong approval, a wrong
mutation or a broken trust boundary and are proposed as FIX_REQUIRED_BEFORE_PRODUCTION.

**The three findings that matter most:**

1. A run can report success with requirements that were never verified, and the two execution shapes disagree on
   seven result-bearing facts (R-02, on top of the absence of any semantic contract before generation, R-01).
2. Three writers reach a workspace or a repository outside the candidate transaction (a planned MCP call with a
   write grant, skill learning, and the run's own ability to rewrite Kriya's configuration and skills), and an
   approval is bound to no identity that the commit checks (R-04, R-03).
3. State derived from one project is read, overwritten and trusted by another, and a folder name closes the
   knowledge gate (R-05, R-06).

**One correction of this closure's own work, which changes an earlier statement made to the owner.** Slices A5 and
A6 said the generation time budget bounds each unit invocation and that nothing bounds the run. That was wrong. The
clock is one per top-level run and is shared by every unit (measured, probe B-P2). Under the production profile a
run is bounded to an hour of generation time. The owner's ruling 4 on accepting A6 was given on the wrong premise;
the budget root (R-08) is narrower and is now proposed as P2. Details in section H.

**One explicit blocker** remains that the freeze prevents resolving: whether every container refusal reaches a
typed environment verdict (R-13).

## A. AS-IS architecture

`00-END-TO-END-LIFECYCLE.md` (stage index, all 24 stages filled, flows per slice), `08-STAGE-DOCUMENTS.md` (each
stage with 21 fields), `10-DIAGRAMS.md` (nine diagrams). In one paragraph: a goal becomes a deterministic
requirement set and a sealed verification contract before any model call; the workspace is identified, classified
and copied into a sandbox; a knowledge gate may stop the run; a Planner proposes a plan that one of two validators
accepts; units run a Developer loop whose answers are staged through a write gate, verified by deterministic gates,
optionally reviewed by a person, and committed by a guarded transaction; a retry policy decides further attempts and
an enforce loop repairs across units; a terminal owner reconciles the result with the run record; four records keep
what happened.

## B. Authority matrix

`01-AUTHORITY-MATRIX.md`: 83 rows (`AUTH-A1-01` to `AUTH-A6-13`). Rows of A3 to A6 carry the full field set; the A1
and A2 rows are completed at the end of the file. The audit over the truths the documentation addendum lists is
`06-GAP-AUTHORITY-AUDIT.md` section 5: every truth has an AS-IS owner or names the root whose contract supplies one;
none is left UNKNOWN.

| Truths with a sound single owner | Truths owned by a root issue |
|---|---|
| Goal; root and workspace identity; membership; candidate identity at commit; candidate transaction; test construction; attribution as a decision; apply; replay; configuration; tool execution; semantic approval where a channel exists | Semantic contract, ambiguity, verifier identity and expectation (R-01); plan validity, requirement closure, final result (R-02); source approval (R-03); workspace mutation (R-04); skill relevance and knowledge coverage (R-06); blocker identity, resolution, retry allowance, recovery episode, checkpoint state (R-07); model binding and qualification admission (R-09); evidence truth (R-11) |

## C. Mutation map

`03-MUTATION-AUTHORITY.md`: 80 rows, each as caller, decision authority, mutation owner, transaction, rollback,
audit, with the alternate-writer searches per slice. Every mutation of a candidate goes through the write gate and
the commit. The exceptions are root R-04 (MCP in place, eleven skill writers, the missing path class) and the
greenfield bootstrap of an empty folder (`MUT-A5-01`, by design).

## D. State machine

`02-STATE-MACHINE.md`: 22 machines. The authoritative one is the RunRecord lifecycle (diagram D-2, corrected against
the code's transition table). Retry and blocker state, recovery state and the verifier semantic ledger are memory
only.

## E. Failure and recovery matrix

`04-FAILURE-RECOVERY-MATRIX.md`: 114 rows and an UNKNOWN table per slice. Kriya fails closed almost everywhere. The
places where UNKNOWN does not fail safely are listed in audit section 4 and each belongs to a root.

## F. Bypass map

`05-INTEGRATION-BYPASS-MAP.md`: 93 rows, the route table (all routes mapped), the MCP transaction comparison and the
two extensibility seams. Each row states what bounds the route. Rows that are gaps are attached to a root in the
audit; the rest are bounded as their row says. A per-row disposition column was not added (quality gate, item 6).

## G. Registry coverage and dispositions

`REGISTRY-COVERAGE.md` (per-row evidence) and `REGISTRY-ROOT-MAP.md` (class and root, generated and checked): 141 of
141 non-closed rows, each once.

| Class | Rows |
|---|---|
| Confirmed, under a root issue | 71 |
| Stale bookkeeping (already fixed at the baseline, or green in the last full suite on an ancestor) | 11 |
| Duplicate or superseded | 5 |
| Disproved | 2 |
| Intentional current contract | 13 |
| Genuine future feature | 36 |
| Phase-D blocker | 1 |
| Phase-D item, non-blocking | 2 |

No registry row was edited. The 11 stale and 2 disproved rows can be closed in the registry on the owner's word.

## H. Stale and contradictory documents, and corrections

Documents classified: 41 `DOC-*` rows (README and gap ledger). Which stay authoritative:

| Document | Status |
|---|---|
| `handover/claude-context/*.md` (the subsystem notes) | Current and consistent for what was read, with the exceptions listed as `DOC-A5-02` (planned writes), `DOC-A6-02` (two version constants), `DOC-A6-03` (qualification coverage) |
| `handover/MODULARIZATION/MODULE-CONTRACTS.md` section 12, `OPEN-ITEMS.md` OI-77 / OI-78 | Design not implemented; not authoritative (`DOC-A1-02`, `DOC-A1-03`) |
| `docs/architecture/SEC001_...`, `CORR016_...` | Historical proposals; CORR016's contract-evolution rule is reused by C-01 |
| Project instructions (`CLAUDE.md`) | Two stale statements: the fencing invariant for fetched text (`DOC-A6-07`), the suite duration (`DOC-A6-05`) |
| This set (`docs/architecture/`) | The architecture specification from here on |

Contradictions found inside this set by the Phase-B integration audit and resolved (`06` section 11, X-01 to X-10).
Two were errors of this closure's own:

- **X-01, the generation clock.** Stated wrongly in A5 and repeated in A6 and in the first Phase-B checkpoint; found
  by comparing `AUTH-A1-08` with `AUTH-A5-12`; the code was read and measured (B-P2). Corrected in place in every
  row, recorded in the gap ledger.
- **X-10, the store classification.** The set said no contract states which record is authoritative. The code has
  one (`control/run_record.py::STORE_CLASSIFICATION`); it does not list the attempt-evidence store or the run logs.
  Narrowed in place.

## I. Confirmed gaps

The root issues, with severity and proposed disposition. Evidence, children, bypasses and registry rows per root:
`06-GAP-AUTHORITY-AUDIT.md` sections 1 and 2. Integration findings INT-01 to INT-13: section 12.

| Root | One sentence | Severity | Proposed disposition |
|---|---|---|---|
| R-01 Semantic contract | Nothing states required behaviour before a model writes code or a test; ambiguity cannot be represented | P1 | FIX_REQUIRED_BEFORE_PRODUCTION |
| R-02 Result truth and executor convergence | Success does not mean requirements verified; two executors decide seven truths differently | P1 | FIX_REQUIRED_BEFORE_PRODUCTION |
| R-03 Approval subject | An approval has no identity the commit checks; under enforce the subject is a unit, never the change | P1 | FIX_REQUIRED_BEFORE_PRODUCTION |
| R-04 Mutation transaction | A planned MCP call with a write grant, skill learning, and the run's own configuration and skills bypass or blur the candidate transaction | P1 | FIX_REQUIRED_BEFORE_PRODUCTION |
| R-05 Project-state namespace | The code index, the skills directory and run history have no project identity | P1 | FIX_REQUIRED_BEFORE_PRODUCTION (learned knowledge and the release cache: intentional once declared global) |
| R-06 Knowledge authority | A folder name closes the knowledge gate; skill relevance is decided twice | P1 | FIX_REQUIRED_BEFORE_PRODUCTION |
| R-07 Retry, blocker, durability | The allowance is granted on a text difference; nothing about retry or recovery survives an interruption | P2 | FIX_REQUIRED_BEFORE_PRODUCTION (never a false success) |
| R-08 Run resource budget | The run clock is sound; its value is repository-settable with no default outside production; nothing bounds the run's work or consumption across resumes | P2 | FIX_REQUIRED for the value's authority class; the rest an owner priority call |
| R-09 Runtime identity and qualification | A binding is resolved by model name, so a role can execute on another binding than the one qualified (measured); a production run never checks its primary model's qualification | P1 | FIX_REQUIRED_BEFORE_PRODUCTION |
| R-10 Prompt trust provenance | Text fetched during a run reaches the Developer unfenced, the Reviewer's system prompt, a test selection and lesson extraction | P1 security (opt-in feature, off in production) | FIX_REQUIRED_BEFORE_PRODUCTION |
| R-11 Evidence and diagnostic truth | The record the desktop shows as the outcome is unbound and written before reconciliation; several labels and counts do not say what happened; "verified" means self-consistent | P2 | Binding and labels FIX_REQUIRED; authenticity an owner choice |
| R-12 Certification harness determinism | Fifteen test-only hazards can turn a certification run red for reasons outside the product | P3 | Fix before the next certification run |
| R-13 Verification environment verdict | Not established that every container refusal is a typed environment verdict | Unknown | **PHASE-D BLOCKER** |

## J. Disproved

| Item | Evidence |
|---|---|
| "The Kriya Log is not persisted" (`GUI-KRIYA-LOG-NOT-PERSISTED-001`) | The run's log file exists in the log directory and holds the warning (measured) |
| "2,400 versus 3,600 is a conflict" | A scenario value under the production ceiling (A5 probe P6) |
| "The time budget is per unit invocation" (this closure's own statement) | B-P2 |
| "The Runtime verification line fails a grounded non-zero exit" at the kernel projection (`RUNTIME-LINE-EXPECTED-NONZERO-001`) | A6 probe P6 |
| `GUI-REVIEW-LOCAL-TRUST-001` as written | `DOC-A3-04` |
| `GAP-A1-06` (effective budget unknown) | Established: the configuration value, one clock per run |
| Eleven test rows registered as failing | Fixed at the baseline or green in the full suite on `82436eb`, an ancestor |

## K. Intentional current contracts

Thirteen registry rows (`REGISTRY-ROOT-MAP.md`), and: the native adapter as the only qualifiable runtime (the
OpenAI-compatible path truncates silently); the doctor's two warnings (role independence, semantic precision
boundary); per-read verification of evidence; pre-effect denial as the only proven-safe tool refusal; the closed
desktop child environment; atomic candidate per attempt; Planner integration decisions binding units (owner ruling);
learned knowledge and the release cache as global stores (once declared).

## L. Genuine future features

Thirty-six registry rows, and: an MCP candidate mount; an authenticated evidence store; a project-ownership
classifier for dependency defects; Windows support; a second inference runtime; mutating Skills and Tools UX; a
run report; phase events for the desktop.

## M. Implementation slices in dependency order

`07-REQUIRED-CONTRACTS.md` sections 3 and 4. Nine slices, each one repair batch with its original symptom as the
first post-fix gate.

| Step | Slice | Contracts | Why here |
|---|---|---|---|
| 1 | S0 Certification base | C-12, C-13 | Tests only; makes certification deterministic; produces the evidence for the blocker |
| 2 | S1 Runtime identity and admission; S2 Prompt trust | C-09; C-10 | Independent, small, both P1 |
| 3 | S3 Project state | C-05 | Base of S4 |
| 4 | S4 Knowledge and mutation | C-06, C-04 | Needs S3 |
| 5 | S5 Semantic contract | C-01 | Base of S6 and S8; can run in parallel with steps 3 and 4 |
| 6 | S6 Result contract and evidence | C-02, C-11 | Needs S5 |
| 7 | S7 Approval subject | C-03 | Needs S6 and S4 |
| 8 | S8 Retry, durability and budget | C-07, C-08 | Needs S6 and S0; changes the state machine last |

Readiness: S0 to S4 and S7 are specified to the level of owner, inputs, state, forbidden path, acceptance test and
negative control and can start from the documents alone. S5, S6 and S8 are specified at contract level with stop
conditions; each needs a design step at its start (the schema of the semantic state; the terminal vocabulary and
golden regeneration; the scripted run-10 reproducer), which the contracts name.

## N. Security and authority production blockers

| Blocker | Root | Reach today |
|---|---|---|
| A planned MCP call with a write grant changes the owner workspace with no transaction | R-04 | After two operator approvals, for a server given a write grant |
| A run may write `kriya.yaml` and workspace skills, which are authority inputs of the next run; in guardrails mode nobody sees it below the size threshold | R-04, R-03 | Any run in guardrails mode |
| The size trigger of guardrails mode can be split by the Planner's decomposition | R-03 | Enforce shape, guardrails mode |
| Cross-project read, overwrite and trust of derived state; a scaffold of one project closes another's knowledge gate | R-05, R-06 | Any installation at the packaged defaults once an index or a scaffold exists; the desktop product runs on those defaults |
| A role executes on a binding other than the one qualified | R-09 | A configuration with two bindings of one model name |
| A production run executes on an unqualified primary | R-09 | Any production configuration whose doctor was not run or is stale |
| Fetched web text in the Reviewer's system prompt and unfenced in the Developer task | R-10 | Only with web lookup enabled; off by default, off in the Setup profile, offline sealed in production |
| Success reported with open requirements | R-02 | Default policies; the production profile blocks on open requirements |

## O. Decisions needed from the owner

| # | Decision | Recommendation (label) |
|---|---|---|
| 1 | Accept the corrected facts about the time budget and the resulting severity of R-08 (P2) | Accept; the measurement is B-P2 |
| 2 | Final review: advisory on every executor, or deciding on every executor | Advisory everywhere: the Reviewer has no authority over a committed, verified unit (`TRACED`, `AUTH-A3-12`) |
| 3 | A terminal state for "applied, requirements open", its name and exit code | Add it; non-zero exit; needs review (`MEASURED` A3 P8 shows today's success) |
| 4 | Is per-unit approval intentionally sufficient under enforce | No: the apply subject is the commit transaction (`TRACED`, exhaustive negative search) |
| 5 | Do CI workflow and Git attribute files join the protected project-input path class | Not now: they change what runs elsewhere, not what Kriya decides (`INFERRED`) |
| 6 | Evidence: diagnostic history of a local single-user tool, or tamper-evident | Diagnostic history, stated in documentation and on screen; authenticity as a future feature (`MEASURED` A6 P4, P5 for what exists) |
| 7 | Production admission for qualification: the run checks (A) or the doctor is a bound precondition (B) | A (`TRACED`: both would call the same assessment) |
| 8 | Ambiguity outcome: ask when attended, refuse when unattended under production, record elsewhere | As stated (`PROPOSED`) |
| 9 | Durable accepted unit work under enforce (`GAP-A1-09`): resumable candidate completions, or keep discard | Resumable, with the atomic commit kept (`PROPOSED`) |
| 10 | Work ceiling and resume ledger (R-08, level 5): before production, or later | Later, unless Kriya is to run outside the production profile on repositories it does not trust |
| 11 | MCP: operator-only for planned write-granted tools now; candidate mount later | As stated (`TRACED`) |
| 12 | Scenario 04's reliance on the current knowledge gate with offline mode | Re-decide before C-06 changes the gate for it |
| 13 | Close in the registry the 11 stale, 2 disproved and 5 duplicate rows | Yes |
| 14 | The three Phase-D characterization items (section "Blocker and characterization items") | Accept two as non-blocking; authorize S0 for the blocker |

## P. vLLM seam

`CURRENT PORT SUFFICIENT; NEW ADAPTER/QUALIFICATION WORK REQUIRED, NO KERNEL REDESIGN EXPECTED.` `TRACED`, not
empirical; vLLM is not supported. Production conditions that must each be shown for that server: exact provider and
model identity (artifact digest and server version); served context capacity equal to the requested window;
refusal of an over-window prompt with no silent truncation; a local endpoint under the egress contract;
qualification of every role's exact identity; role mapping through bindings made exact by C-09. Table:
`05-INTEGRATION-BYPASS-MAP.md`, "Extensibility seams".

## Q. Skills and Tools extensibility

`Richer read-side UX appears structurally compatible; mutating Skills/Tools require closure of canonical
mutation/transaction authority first.` The read side exists (five read-only desktop operations). Every mutating
feature lands on R-04, R-05 or R-06. `TRACED`.

## R. Is Kriya architecture-coherent now?

No. The closure gate of the owner's directive, item by item:

| Gate | State |
|---|---|
| Every truth has one owner | No: R-01 to R-11 |
| Every mutation has one authority path | No: R-04 |
| Every registry row reconciled, no PENDING | Yes: 141 of 141 |
| A semantic contract precedes verifier semantics | Not in the code; defined as C-01 |
| Blocker identity, retry continuity, checkpoint authority, final-result truth defined | Defined as C-07 and C-02; not in the code |
| UNKNOWN fails safely | Not everywhere (audit section 4) |
| Model prose is never implicit authority | Not for fetched text (R-10) or a generated verifier's expectation without a contract (R-01) |
| Side routes represented | Yes |
| Known gaps explicit | Yes |
| vLLM and Skills at `TRACED` confidence | Yes |

## S. Conditions to lift the freeze

The freeze should not be lifted as a whole. Recommended ruling: lift it **per slice, in the order of section M**,
each slice under the engineering rules (original symptom reproduced first, fix at the root owner, regression,
mutation, adjacent tiers, lint), with one full suite after S0 and one on the final tree. Conditions before the first
slice: (1) the owner's decisions of section O that the slice depends on; (2) for S0 nothing else; (3) for every
later slice, S0 complete and the blocker resolved. Production readiness is a statement to make after S8 and a
certification on that tree, not before.

## Blocker and characterization items

| Item | Missing evidence | Why not obtainable under the freeze | Can it change production readiness | Status |
|---|---|---|---|---|
| `WAVE8-DOCKER-REFUSAL-BECOMES-FALSE-VERDICT-001` (R-13) | The typed result of each launch site when the container daemon refuses a launch | Needs a real loaded daemon, or a new scripted test; tests are frozen | Yes: a refusal read as a failed gate charges the candidate; read as passed it would be a false success | **PHASE-D BLOCKER**; resolved by S0's scripted refusal at every launch site |
| `RUNTIME-GATE-VENV-LACKS-PROJECT-TEST-DEPENDENCY-001` (R-13) | The install log and environment listing of the one gate that lacked a dependency | Needs the same contained run | No: same owner and same typed outcome whichever cause | Non-blocking; closes with S0 |
| `TECHNICAL-VERIFICATION-BARE-ROWS-001` (R-11) | The rendered DOM of one bare row | Only the owner's look at the app can show it | No: a rendering of recorded gate types | Non-blocking; closes on the owner's observation |
| Eight class-D setup errors of 2026-11-14 | The error text of one reproduction | A 30-minute loaded parallel run is not a focused test | No: test harness either way | Non-blocking; under R-12 |

## Final quality gate of the documentation addendum

| # | Item | State |
|---|---|---|
| 1 | Every stage documented | Yes: 24 stages, `08` |
| 2 | Every important integration documented | Yes: 16 contracts in `09`; 33 boundaries traced in `06` section 10 |
| 3 | Every global map complete | Yes, with the limits each slice states |
| 4 | Every truth one owner or an explicit blocker | Yes (`06` section 5) |
| 5 | Every mutation an authority path | Documented for all 80; three exceptions are R-04 |
| 6 | Every bypass a disposition | **Partly**: each of the 93 rows states what bounds it and the gap rows are attached to roots; no per-row disposition column exists |
| 7 | All registry rows reconciled | Yes |
| 8 | Stale and historical documents classified | Yes: 41 rows; section H |
| 9 | AS-IS and TO-BE separated | Yes: `07` is the only TO-BE home; field 20 of each stage |
| 10 | Every FIX_REQUIRED implementation-ready | **Partly**: S0 to S4 and S7 yes; S5, S6, S8 at contract level with a named design step |
| 11 | Nothing depends on chat history | Yes with the appendix below (the owner's rulings are restated there) |
| 12 | Citations resolve at the baseline | Yes: per-slice citation scripts and the Phase-B script, 0 problems; row references checked by `crossmap_check.py`, 0 dangling |
| 13 | Diagrams and tables agree | Built from the rows; D-2 was checked against the code and corrected; the others were not mechanically checked beyond row existence |
| 14 | No placeholder or unexplained unknown | Yes: three declared characterization items, one blocker |
| 15 | Sufficient to implement without another discovery round | For S0 to S4 and S7; S5, S6 and S8 need the design step named in item 10 |

## What this closure did not establish

Stated once here; each is also in the limitation field of its row. The OCI backend and mask code; the native
adapter's request and response code; the qualification cases; routing internals; the fitting internals of the
context budget; `plan_revision.py`; RunRecord storage; the release scripts and the certification matrix; the UI
code beyond locating consumers; anything needing a live model, a container or an MCP server. The state-machine
tables and the A1 to A4 failure rows were cross-read where an integration boundary cites them, not line by line.
The audit found two errors in this closure's own earlier text; others of the same kind may remain where no boundary
forced a comparison.

## Appendix: owner rulings in force (restated so the set stands without chat history)

- Program: slices A1b to A6, Phase B and Phase C as one authorized program; the only routine stop is this report.
  Freeze: only `docs/architecture/` changes; no runtime, test, golden or registry change; no live model, full suite,
  qualification, merge, push or tag.
- A1: a blocker registry and a typed blocker key are rejected as blocker authority (WS4, 2026-11-14); the old
  `Finding / FindingState` design is not resurrected; "fresh full budget per enforce re-invocation" is not an
  accepted contract.
- A3: `GAP-A3-04` is split into runtime authorization binding and audit binding; `GAP-A3-05` is P1 and its approval
  subject is defined before any measure; `quality_gates_passed` must never silently mean requirements verified.
- A4: `GAP-A4-01` P1 with the seven truths reconciled into one contract; `GAP-A4-03` and `GAP-A4-04` P1; isolation
  is proven by identity key, write namespace, read namespace and fallback behaviour, never by separate directories.
- A5: `GAP-A5-01` assessed per state class under the principle "project-derived mutable or semantic state must be
  bound to the project identity unless explicitly promoted into a governed global namespace"; configuration and
  skill mutation is not ordinary source mutation; `GAP-A5-03` and `GAP-A5-05` P1; a capability grant is not a
  mutation approval; the container rows are blockers only if their unknown can change a classification.
- A6: root-cause collapse in Phase B; an unknown or ambiguous model identity must refuse or resolve through an
  explicit mapping; production qualification admission must be a real contract, not "the doctor should have been
  run"; run-result truth and audit integrity are separate; one resource-budget root (given on a premise this report
  corrects); the wording of sections P and Q.
- Phase-B method: the audit reads document content and traces boundaries; two documents describing one truth
  differently is a finding.
