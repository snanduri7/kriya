# 12 - D0 closure hardening: owner ruling, cross-map sweep, slice specifications

Canonical runtime baseline: commit `3eded93c0cfb50f8855d04510e3f9aa2cd5208f7`. Written on `architecture/closure-3eded93`
on 2026-11-15, after the owner's ruling on `11-PHASE-D-REPORT.md`. Documentation and evidence only: no runtime code,
test, golden or registry row was changed, nothing ran but read-only scripts over the documents and greps of the code.

What this document is the home of:

1. the owner's Phase-D ruling (section 1), so that no accepted contract depends on chat or memory;
2. the D0 cross-map sweep and what it found (sections 2 to 5);
3. the implementation specifications of slices S5, S6 and S8 (section 6), which `11` reported as "contract level";
4. the D0 exit check (section 7).

AS-IS facts stay in the six maps; TO-BE stays in `07-REQUIRED-CONTRACTS.md`, which this pass amended in place where a
ruling or a finding changed a clause. Section 6 is TO-BE (`PROPOSED` until its slice is implemented) and refines `07`;
where the two differ, section 6 wins for S5, S6 and S8 because it was written against the code.

## 1. Owner ruling on the Phase-D report (2026-11-15)

The fourteen decisions of `11` section O, as ruled. "Reading" marks a place where the ruling needed one; each reading
is mine and stands until the owner corrects it.

| # | Ruling | Effect on the set |
|---|---|---|
| 1 | The corrected fact is accepted: one generation clock per top-level run; production caps it at 3,600 s. R-08 stays P2. The defect is the authority and configuration semantics of the value, not "no run bound" | No change (`06` R-08, `07` C-08) |
| 2 | Final review is advisory on every executor. A post-generation review never redefines accepted runtime truth. Any deciding approval belongs before apply and commit, in the canonical approval authority | `07` C-02 truth (5) is ruled, no longer "recommended"; C-03 is the only deciding approval |
| 3 | Applied with open requirements is an explicit non-success with a non-zero exit, in a typed state. `quality_gates_passed=true` and SUCCESS are never reported merely because bytes were applied | `07` C-02; section 6 (S6) fixes the name, the record state and the payload. **Reading:** the name stays `APPLIED_REQUIREMENTS_OPEN` (the owner asked for a state "equivalent to" `APPLIED_WITH_OPEN_REQUIREMENTS / INCOMPLETE`) |
| 4 | Per-unit approval is not sufficient under enforce. The approval subject is the exact final mutation: the commit transaction's cumulative candidate bytes and digest. Unit approvals are supporting evidence only | `07` C-03: ruled |
| 5 | CI workflow and Git attribute files do not join the protected path class. Reopened only with measured bypass evidence | `07` C-03, C-04: the path class is the Kriya configuration names and the workspace skills root, nothing else |
| 6 | Evidence is diagnostic and audit history, not cryptographic or tamper-evident evidence, and the UI and the documentation say so. Runtime final truth binds to the authoritative RunRecord; diagnostic history never redefines result truth. Tamper-evident evidence is future scope | `07` C-11: ruled; `BYP-A6-13` is `intentional` |
| 7 | The run owns production admission. The doctor is a preflight, not sufficient authority. Every exact provider, model and binding actually dispatched in a production run is qualified before first use; an unknown or unqualified binding fails closed | `07` C-09: alternative A ruled, with a dispatch-time clause added. **Reading:** "binding dispatched" means every generation binding a run can dispatch (each role's primary, each chain entry, each routing candidate); embeddings keep their own contract (EMBEDDING-CONTRACT-001) |
| 8 | Ambiguity: an attended run asks and resolves explicitly; an unattended production run refuses. No silent default | `07` C-01. **Reading:** the ruling names two cases. For the third (unattended, not production) the ambiguity is recorded, the requirement cannot close without a person, and the result is a non-success by ruling 3; that is the only reading in which nothing is defaulted silently |
| 9 | Accepted unit work under enforce is resumable, only when durably bound to the exact plan, semantic-contract and candidate identities and revalidated before reuse; never because a checkpoint exists | `07` C-07; section 6 (S8) |
| 10 | The work ceiling and the resume ledger are done in S8, before any production-ready declaration | `07` C-08: no longer an owner priority call |
| 11 | Planned write-granted MCP tools are operator-only for now. Model-planned read-only MCP stays under the existing policy. Candidate-mounted MCP writes may be designed later. A capability grant alone is not a mutation approval | `07` C-04: ruled |
| 12 | Scenario 04's reliance on the present knowledge gate is not architectural authority. Scenario 04 is migrated and revalidated when the knowledge authority is corrected; a defective gate is not preserved to keep it green | `07` C-06: the stop condition is replaced by a migration step |
| 13 | The 11 stale, 2 disproved and 5 duplicate registry rows are closed, with dispositions that preserve the evidence | **Reading:** the registry is outside `docs/architecture/`, so the edit is the first step of S0 on the implementation branch, with `tests/test_backlog_registry.py` |
| 14 | The two non-blocking characterization items are accepted as such. S0's scripted refusal characterization for R-13 is authorized. R-13 is resolved before any final verification-truth claim | `11` blocker table |

Program ruled with it: one bounded docs-only pass (D0, this document), then the freeze is lifted **per slice** in the
order S0, S1, S2, S3, S4, S5, S6, S7, S8, then final integration and certification. Per slice: reproduce, confirm the
root owner, implement the largest safe coherent fix, focused tests, negative controls, targeted mutants, adjacent
integration, architecture and static checks, checkpoint. The full suite runs once after S0 and once on the final
tree. Stops only for: new measured evidence that invalidates an accepted contract; a product, security or authority
choice not ruled above; a root fix that needs unrelated redesign; an acceptance proof that cannot be constructed; a
new P1 root; an unexpected change of the canonical baseline.

Owner items that remain, none of which blocks a slice before the one named:

| Item | Needed before | Proposal (label) |
|---|---|---|
| Which approval triggers hold execution outside human-in-the-loop mode (`BYP-A3-06`, `HITL-OTHER-APPROVAL-TRIGGERS-DEFERRAL-001`) | S7 | Keep the AS-IS rule and state it: execution is held only in human-in-the-loop mode; in every other mode the operator's choice of mode is the standing consent to contained execution, and the apply approval still precedes the apply (`PROPOSED`; section 5) |
| May a model-inferred optional runtime check fail a run (`OPTIONAL-RUNTIME-GRADED-FAILURE-POLICY-001`) | Final review | Not decided by C-01; unchanged by S5; stays an open P3 row with that target scope |

## 2. Cross-map consistency sweep

Method. The two errors this closure made (X-01 clock scope, X-10 store classification) were both one concept stated
differently in two maps. So the sweep is by **concept**, over the content of every document, not by heading:
`evidence/d0/concept_sweep.py` retrieves every statement of a concept with its row ID; the statements were read side
by side; a disagreement was resolved against the code. `evidence/d0/root_rows.py` gives, per root, the rows of each
map that its audit section, its contract, its boundaries and its child gaps cite (the correlation set).

### 2.1 Correlation set per root

| Root | AUTH | MUT | SM | FR | BYP | GAP | Cited again in stage documents / integration contracts / diagrams | Disagreements found (section 2.3) |
|---|---|---|---|---|---|---|---|---|
| R-01 | 15 | 1 | 0 | 6 | 3 | 6 | 21 / 12 / 10 | X-12 |
| R-02 | 20 | 0 | 2 | 1 | 12 | 10 | 33 / 24 / 17 | X-15 |
| R-03 | 7 | 1 | 0 | 0 | 7 | 3 | 11 / 9 / 9 | X-16 |
| R-04 | 4 | 2 | 0 | 0 | 8 | 4 | 10 / 6 / 7 | none |
| R-05 | 2 | 0 | 0 | 0 | 5 | 1 | 3 / 1 / 2 | X-17 |
| R-06 | 4 | 1 | 0 | 1 | 7 | 2 | 13 / 2 / 2 | X-17 |
| R-07 | 11 | 7 | 3 | 2 | 3 | 8 | 19 / 15 / 16 | X-13, X-18 |
| R-08 | 6 | 0 | 0 | 0 | 2 | 3 | 5 / 1 / 4 | X-14 |
| R-09 | 2 | 0 | 0 | 2 | 3 | 2 | 3 / 2 / 2 | none (X-03 residue is in X-18) |
| R-10 | 2 | 0 | 0 | 0 | 6 | 1 | 5 / 1 / 1 | none |
| R-11 | 4 | 2 | 1 | 1 | 6 | 4 | 10 / 6 / 3 | X-11 |
| R-12 | 0 | 0 | 0 | 0 | 0 | 0 | 0 / 0 / 0 | none: registry rows only, by construction (test-only) |
| R-13 | 1 | 0 | 0 | 2 | 0 | 0 | 3 / 1 / 1 | none: the open question is the declared blocker |

A zero is read, not skipped: R-03, R-04, R-05, R-08, R-09 and R-10 have no state machine because nothing about them
is a persisted state today (that absence is the gap for R-03 and R-08 and is stated in their contracts); R-05 and
R-10 have no failure row because their defect is a silent success, not a failure.

### 2.2 Concepts compared

| Concept | Statements compared | Verdict |
|---|---|---|
| Scope of the generation clock and of the time budget (class of X-01) | 68, in 9 documents | Consistent everywhere after the Phase-B correction: one clock per top-level run, a resumed run a new one. The remaining "per unit invocation" sentences are the correction history and the attempt budgets, which are per invocation |
| Which record is the outcome of record (class of X-10) | 26, in 6 documents | Two residues of the overstatement: X-11 |
| What is durable, what is memory only, what a resume restores | 112, in 11 documents | Consistent. Four rows still carry a question that a later row answers: X-18 |
| Final-review authority per executor | 19 | Consistent (AS-IS: decides on direct and milestone, advisory under enforce; TO-BE: advisory everywhere, ruled) |
| Qualification: which identities a run checks | 28 | Consistent (AS-IS: fallback and retry identity only) |
| Project versus installation scope of a store | 52 | Consistent with the class table of `06` R-05 |
| Approval subject and candidate digest | 45 | Consistent; one registry row has no clause in its contract: X-16 |
| Plan identity | 32 | Consistent with X-09 / INT-02 |
| Requirement truth and the success word | 85 | Consistent AS-IS. The TO-BE was vague about the record state: X-15 |
| Trust class of prompt text | 35 | Consistent with X-02 and X-08 |
| MCP write route | 21 | Consistent |
| Fresh budget per re-invocation | 26 | Consistent AS-IS. The TO-BE named a symptom that is already fixed: X-13 |

Not swept mechanically: statements that use none of the lexicon's words. The limit of `11` ("the state-machine tables
and the A1 to A4 failure rows were cross-read where a boundary cites them") is narrowed by this sweep to: a
contradiction that is phrased without any of the twelve concept lexicons could remain.

### 2.3 Contradictions found in D0, and how each is resolved

Numbering continues `06` section 11.

| ID | Contradiction | Resolution (label) |
|---|---|---|
| X-11 | `06` section 1, row R-11, and `07` section 2, row C-11, still said "no contract states which record is the outcome of record" and "no evidence contract", after X-10 had established that `control/run_record.py::STORE_CLASSIFICATION` exists | Residue of X-10: both rows narrowed in place to what is true (the classification omits the attempt-evidence store and the run logs, and no rule says what a surface may present as the outcome or what "verified" proves). `TRACED` |
| X-12 | `06` R-01, `GAP-A4-07` and `11` say ambiguity "has no representation" and nothing states required behaviour before generation. `contract_compilation.py` already seals, per requirement, a status (`CLOSABLE`, `AUTHORITY_REQUIRED`, `AMBIGUOUS`, `NOT_A_CLAIM`, `DISPOSITIONED`), the residual claims with their strength, and an `ambiguity` reason; `VerificationContract.refusal()` already returns a typed `GoalAdmissionError` for an ambiguous entry and `VerificationAuthorityRequired` for an unbound mandatory claim. Both executors consult it **only** when `autonomy.requirement_unverified_policy` is `block` (`workflow.py::_requirement_policy_blocks`; `enforce_loop.py` at the contract compilation) | The audit's seam field and `GAP-A2-01` had it right; the one-sentence summaries overstated. Narrowed in `07` C-01: the states exist for the two ambiguity patterns the compiler proves (both about mutation scope) and for every claim's authority; what is absent is (a) any consequence of them under the default `record` policy, (b) a question to an attended person, (c) the state reaching the Developer, the verifier review and requirement closure as one named value. R-01 is unchanged in severity and disposition; S5 is **smaller** than `11` implied (section 6). `TRACED` (code read in D0) |
| X-13 | `07` C-07 (acceptance proof and stop condition), `07` section 3 (S8) and `11` section M name "the run-10 reproducer" as S8's original symptom and say: if it cannot be reproduced pre-fix, stop. `GAP-A1-07` and the code say the run-10 mechanism (a `context_budget_unsatisfiable` refusal replenishing the scoped budgets) was fixed at the baseline by NON-CANDIDATE-FAILURE-SCOPED-RESET-001 and is pinned by nine committed tests | The contract contradicted the gap ledger and would have stopped S8 at its first step. Corrected in `07`: S8's original symptoms are the three that are still reproducible: `GAP-A1-07` (an unlisted type, `no_progress_retry`, replenishes: A1 probe P10, to be shown at loop level), `BYP-A1-01` (seven invocations with a fresh scoped allowance each: A1b probe P12), and the loss of charges at an interruption. The nine run-10 tests stay as the negative control that the earlier fix is not regressed. `TRACED` |
| X-14 | `07` C-08 says the value-bounded field class "already exists" and that a repository "may lower it below the trusted value". The class that exists (`config/authority.py::_VALUE_BOUNDED_FIELDS`) bounds a field against a **packaged constant ceiling**, not against another layer's value: a positive integer at most the ceiling is `REPOSITORY_SAFE`, anything else is `SECURITY_AUTHORITY` (it then needs the operator's approval) | C-08 corrected to the mechanism that exists: the budget joins that table with ceiling 3,600; a repository value of 1 to 3,600 is safe, a larger value or `null` needs the operator's approval. No new mechanism. `TRACED` |
| X-15 | `07` C-02 defines the terminal word `APPLIED_REQUIREMENTS_OPEN` but not the RunRecord state under it, while `06` section 3.5 says the coordinator fails the record for any payload that does not claim success. Read together, a committed change with open requirements would end as lifecycle `FAILURE` with no typed word, or would need a new lifecycle state nobody specified | Resolved in section 6 (S6): no new lifecycle state; the record ends in the `FAILURE` lifecycle with `commit_result` COMMITTED and the typed `terminal_status = APPLIED_REQUIREMENTS_OPEN` (the transition already accepts a terminal status); surfaces read the terminal status. `PROPOSED`, against code read in D0 |
| X-16 | `REGISTRY-ROOT-MAP.md` maps `HITL-OTHER-APPROVAL-TRIGGERS-DEFERRAL-001` to R-03 as "part of the approval-subject contract"; `07` C-03 says the execution approval is "unchanged in scope" and has no clause for it | C-03 gains the explicit clause (the AS-IS rule, stated); widening it is the owner item of section 1. `TRACED` |
| X-17 | Three registry rows mapped to a root had no clause in that root's contract: `SKILL-RULE-PROVENANCE-PROJECT-SUPPLIED-001` (R-06), `DIRECTORY-NAME-PRUNING-P3-SIBLING-SITES-001` (R-05), `ENFORCE-FALSE-PREMISE-MUTATION-GUARD-001` (R-02) | Clauses added to C-06, C-05 and C-02 (`07` section 7) |
| X-18 | Slice rows that still state a question a later slice or the audit answered, or an `UNKNOWN` the code answers | Register of section 3. Three were answered against the code in D0 (`BYP-A1-03`, `BYP-A1-12`, `GAP-A4-06`) |

None of X-11 to X-18 creates a root, changes a root's severity, invalidates a ruling of section 1 or changes the
order of the slices. X-12 and X-13 change the **content** of S5 and S8 (smaller, and with the right first gate).

## 3. Open statements in the maps: resolution register

`evidence/d0/open_statements.py --register` lists every TODO, TBD, PENDING, NEEDS_CHARACTERIZATION, literal
`` `UNKNOWN` `` label, "not established" and "open question" in the maps and derivative documents (66 hits, six of them the
disposition table of `05` quoting the statement it answers) and fails
when one is not covered here. Statements of an evidence limit ("not executed", "not measured", "was not read") are
labels of how a fact is known, not open statuses; the code that was not read is listed once in `11`, "What this
closure did not establish".

The slice rows are not rewritten (they are the record of what each slice knew); this register is the answer.

| Document | Lines | Row | Open statement | Resolution |
|---|---|---|---|---|
| `01-AUTHORITY-MATRIX.md` | 136 | `AUTH-A1-08` | Which value a real launch runs with: NEEDS_CHARACTERIZATION | Answered: `AUTH-A5-12` (A5 probe P6: packaged default none, Project Setup 900, production at most 3,600); `GAP-A1-06` disproved as a gap (`06` section 6) |
| `01-AUTHORITY-MATRIX.md` | 1186 | `AUTH-A5-01` | Whether a unit checkpoint survives the sandbox reset | Answered: `06` section 3.4 (only when the repository ignores `.kriya/`); INT-11 |
| `01-AUTHORITY-MATRIX.md` | 1909 | `AUTH-A6-11` | Whether a resumed run keeps its evidence store | Answered: `06` section 3.3 and D0: the CLI passes `resume_id` (a checkpoint id) and never an earlier run id on `generate` or `fix`, so a resumed top-level run has a new id and a new store (`TRACED`: `cli.py` call sites). A resumed direct unit takes the checkpoint id as its unit run id (`workflow.py`, the resume block), one of the identifiers of `KERNEL-RESULT-RUNID-DIVERGES-001`. INT-10 |
| `05-INTEGRATION-BYPASS-MAP.md` | 33 | `BYP-A1-03` | Whether `resume_decision` is a counter of resumes | Answered in D0: no (disposition table of `05`) |
| `05-INTEGRATION-BYPASS-MAP.md` | 73 | `BYP-A1-12` | Which checkpoint a sandbox unit finds; whether a refused one stops a stage | Answered in D0 (disposition table of `05`) |
| `05-INTEGRATION-BYPASS-MAP.md` | 154 | `BYP-A4-18` | Which entry points wire no approval callback | Answered: `BYP-A5-10` |
| `05-INTEGRATION-BYPASS-MAP.md` | 185 | `BYP-A5-14` | Whether anything survives the sandbox reset | Answered: `06` section 3.4 |
| `05-INTEGRATION-BYPASS-MAP.md` | 245 | vLLM seam | Whether such a server reports an artifact digest | Declared limit of a future feature (`11` section P); no slice depends on it |
| `08-STAGE-DOCUMENTS.md` | 321 | STAGE-GATES | Whether every environment refusal reaches a typed verdict | The declared Phase-D blocker (R-13); resolved by S0 |
| `GAP-LEDGER.md` | 20, 103 | `GAP-A1-01` | The opposite error was not established | Answered in the same ledger (line 103, A1b) |
| `GAP-LEDGER.md` | 86, 92, 108 | `GAP-A1-06` | The effective budget of a launch | Answered: A5 and B-P2; disproved as a gap |
| `GAP-LEDGER.md` | 199 | `GAP-A2-01` | Whether Planner criteria reach the Developer; whether the desktop runs under `block` | Answered: `AUTH-A4-12` (enforce only) and A5 probe P10 (Project Setup profile); the production profile seals `block` (`config/resolution.py`) |
| `GAP-LEDGER.md` | 214 | `GAP-A2-02` | That a live run produced the unmapped case | Declared limit: no live run is needed, the derivation is measured (A2 P5, P7); S5 fixes the derivation |
| `GAP-LEDGER.md` | 276 | `GAP-A3-01` | Which policy a production and a desktop launch run under | Answered: production seals both policies to `block`; a Project Setup folder and the packaged default run `record` (A5) |
| `GAP-LEDGER.md` | 511, 880 | `GAP-A4-06` | Whether a resumed enforce run keeps the run id | Answered: `06` section 3.3 and the D0 trace above |
| `GAP-LEDGER.md` | 793, 912 | `GAP-A6-02` | Reachability not established | Answered in the same ledger (line 912: `MEASURED` B-P1) |

Exempt lines (a state name, a vocabulary definition, or a row that reports how the product treats an unknown; none is
an open status of this set):

| Document | Lines | Why exempt |
|---|---|---|
| `01-AUTHORITY-MATRIX.md` | 164, 227, 510, 1463, 1942 | Stage states `pending`; the requirement outcomes PENDING and UNKNOWN; the desktop's `UNKNOWN` outcome value |
| `02-STATE-MACHINE.md` | 98, 107, 118, 130, 133, 134, 214, 219, 221, 222, 223, 224, 268, 272, 273, 483, 484, 485 | State names |
| `03-MUTATION-AUTHORITY.md` | 35, 37, 61, 82, 84, 86 | State names |
| `04-FAILURE-RECOVERY-MATRIX.md` | 93, 104, 225 | State names and the product's handling of an unknown |
| `GAP-LEDGER.md` | 7, 274, 275 | The vocabulary definition; requirement outcome names |
| `05-INTEGRATION-BYPASS-MAP.md` | 279, 289, 295, 304, 350, 365 | The D0 disposition table: the vocabulary definition and the notes that quote the statement they answer |

## 4. Execution variants per root

For each root, what each variant does today (AS-IS, from the rows of section 2.1) and whether the root's contract
covers it. "Same" means the variant runs the same code as the cell to its left. A variant the contract does not name
would be a finding; none is left.

| Root | Direct unit | Milestone | Enforce | Desktop launch | CLI | Resume | Tool / MCP | Contract covers every variant? |
|---|---|---|---|---|---|---|---|---|
| R-01 | Contract compiled and sealed in `workflow.py`; refusal only under `block`; no plan, so no Planner criteria | One contract per milestone unit | Compiled once in `enforce_loop.py`; refusal only under `block`; criteria rendered to units | Same kernel entry; can answer a verifier review, cannot be asked an ambiguity question today | `--yes` cannot answer a semantic decision | The contract digest is a resume fingerprint; the semantic ledger is lost | Not a consumer | Yes: C-01 (one compiled value, read by both executors; the typed ambiguity question is a fourth desktop review kind, see S5) |
| R-02 | Status from the unit; final review decides | Commits per unit, then replay and integration; final review decides | Gates, then one commit; final review advisory; two closers cannot fire | History row shown; last-event truth | Reconciled payload, exit 0 only on `quality_gates_passed` | A resumed run is a new run | A TOOL subtask's status only | Yes: C-02 with clauses INT-04, INT-05 (`kriya fix`) and the entry-shape census of S6 |
| R-03 | Unit diff approved, digest not compared | Same per unit | Per unit only; no approval of the terminal commit | Launch record carries a digest of the presented diffs | Event only | The approval is not persisted with the candidate | No approval of an MCP change (R-04) | Yes: C-03 (one apply subject on every shape; enforce at the terminal commit) |
| R-04 | Skill writes and Git commits during analysis | Same as direct | Skill writes deferred by the run shadow | No skill mutation operation | `kriya tools execute` (operator) | - | Planned write-granted MCP call writes in place | Yes: C-04 (ruling 11) |
| R-05 | Unkeyed default locations | Same | Same; unit checkpoints keyed by the sandbox | Runs on the unkeyed defaults (no `paths` section) | Same; explicit `paths.*` possible | Control state keyed by identity (sound) | An MCP server is bound to the process directory | Yes: C-05; the MCP binding is C-04 |
| R-06 | Gate before planning; skips it on resume | Decided once for the sequence | Controller check and per-unit selection | Strict policy, no confirmation | `--ack-knowledge-gap`, permissive policy | Gate skipped from a checkpoint's existence | - | Yes: C-06 with INT-06 |
| R-07 | Retry state in memory | Same per milestone | Fresh allowance per re-invocation; recovery state in loop locals | Same | Same | Nothing restored; enforce reuses no completion unless committed | - | Yes: C-07 (S8) |
| R-08 | One clock per run | One clock for the sequence | One clock for the plan; work multiplies | Project Setup sets 900 | Any configured value | A new clock and new counts | Tool calls are outside the generation clock (own timeouts) | Yes: C-08; tool time stays under its own bounds (`AUTH-A5-06`), stated as unchanged |
| R-09 | Binding by name | Same | Same; roles differ per unit | Same | Same; `kriya model status` uses the role-first lookup | A resumed unit starts on the primary | - | Yes: C-09 (ruling 7) |
| R-10 | Fetched text into the error context | Same | Same per unit | Lookup decisions are declined, so no fetched text | Per-query approval | Error context not persisted | - | Yes: C-10 |
| R-11 | Four records | Same | Same plus enforce run id | History and detail read the history row | `launch.status` reads the RunRecord | New store, no parent | `tool.execution` record without files | Yes: C-11 with INT-10 |
| R-12 | - | - | - | - | - | - | - | Test-only |
| R-13 | Candidate gates, baseline | Same, plus replay | Same, plus terminal gate and requirement closers | Same | Same | - | Tool containment refusals are typed pre-effect (`AUTH-A5-06`) | To be proven at every launch site (S0) |

## 5. Registry rows against contract clauses

For the roots whose slices this pass makes implementation-ready, and for the three clauses X-16 and X-17 added: each
root-gap row and the clause that closes it. A row is closed in the registry only by its slice, with its own test.

| Root | Registry row | Clause that closes it |
|---|---|---|
| R-01 | `GENERATED-VERIFIER-CONTRACT-AUTHORITY-001`, `REQUIREMENT-BEHAVIOR-CLAIM-NO-DETERMINISTIC-CLOSER-001` | C-01 semantic state and closure rule (S5), C-02 result word (S6) |
| R-01 | `WAVE5-O2-GENERATED-TEST-WEAKENING-001` | C-01: an unmapped executed identity is held (S5) |
| R-01 | `FIX-UNIT-BEHAVIOURAL-GATE-001` | C-02 clause INT-05: a fix run's one requirement (S6) |
| R-01 | `GOAL-EXAMPLE-COMPILER-COVERAGE-001` | Not closed by S5: a wider example grammar is an additive input authority. Stays open with target scope "after S8" |
| R-01 | `OPTIONAL-RUNTIME-GRADED-FAILURE-POLICY-001` | Not decided by C-01 (owner item, section 1) |
| R-02 | `ENFORCE-EXECUTE-PLAN-CONVERGENCE-001`, `PLAN-FORM-NORMALISATION-001`, `REQUIREMENT-VERIFIER-ENFORCE-PATH-NO-PROJECT-BEFORE-001`, `INTEGRATION-PASS-REQUIREMENT-POLICY-001`, `REQUIREMENT-CLOSURE-UNVERIFIED-RECORDED-AS-SUCCESS-001` | C-02, truths (1), (2), (6), (7) (S6) |
| R-02 | `MILESTONE-UNIT-REVIEW-REFUSAL-FOLD-UNMEASURED-001`, `KERNEL-FINAL-REVIEW-REUSES-PRE-BUILD-REVIEW-001` | C-02 truth (5) (S6) |
| R-02 | `TERMINAL-REQUIREMENT-FAILURE-NO-REPAIR-001` | C-02, repair after a terminal gap: second step, in S8 because it needs the ledger |
| R-02 | `ENFORCE-FALSE-PREMISE-MUTATION-GUARD-001` | C-02 clause added in D0: a guard that exists on one executor is called by both (S6) |
| R-02 | `GUI-REASON-CODES-IN-ERROR-STATES-001` | C-11 clause INT-01: typed reason codes reach the surface (S6) |
| R-03 | `HITL-OTHER-APPROVAL-TRIGGERS-DEFERRAL-001` | C-03 clause added in D0 (S7); owner item if it is to be widened |
| R-05 | `DIRECTORY-NAME-PRUNING-P3-SIBLING-SITES-001` | C-05 clause added in D0 (S3) for the two traced sites; the other sites of the registry's census stay with the row |
| R-06 | `SKILL-RULE-PROVENANCE-PROJECT-SUPPLIED-001` | C-06 clause added in D0 (S4) |
| R-07 | `RETRY-BLOCKER-IDENTITY-AUTHORITY-001`, `SEMANTIC-SAFETY-REPAIR-CONTINUITY-001`, `TRIAGE-ON-ORCHESTRATOR-NOTICE-001` | C-07 continuity rule, the predicate split (S8) |
| R-07 | `RETRY-STATE-RESUME-CONTINUITY-001` | C-07 persisted ledger (S8) |
| R-07 | `ENFORCE-UPSTREAM-WORK-PRESERVATION-001` | C-07 resumable candidate completions (ruling 9, S8) |
| R-07 | `HITL-RESUME-DEFERRED-GATES-001` | C-07: a resume never reuses a candidate whose held gates did not run (AS-IS `FR-A3-20`, pinned by a scripted crash during the review in S8); preserving an unreviewed candidate is not required |
| R-07 | `RECOVERY-MAY-CHANGE-ANSWERED-NO-CHANGE-REFUSED-IN-UNIT-001` | C-07 persisted recovery state with one typed vocabulary for a recovery unit's outcome (S8); the row's own reproducer is the first gate |
| R-07 | `RETRY-PROGRESS-SAME-BLOCKER-001`, `OPEN-FINDINGS-ORDER-RESUME-001`, `RECOVERY-UPSTREAM-OWNER-ROUTING-001` | Duplicate or superseded (`REGISTRY-ROOT-MAP.md`); closed in S0 by ruling 13. Corrected on the implementation branch: the D0 checkpoint listed the last two as S8 work, against the root map |
| R-11 | The sixteen rows of `REGISTRY-ROOT-MAP.md` under R-11 | C-11 "diagnostic truth" and "retention": each label or count fix carries its own regression (S6); `TECHNICAL-VERIFICATION-BARE-ROWS-001` closes on the owner's observation |

Not rechecked row by row in D0: the rows under R-04 (one), R-12 (fifteen) and R-13 (seven), whose slices `11` already
reported implementation-ready and whose mapping is one-to-one with their contract.

## 6. Implementation specifications: S5, S6, S8

Each specification is complete for an implementer who has this set and the code: no further architecture discovery.
Names in *italics* do not exist yet. "Verified in D0" marks a code fact read during this pass; everything else is
cited from the maps. Every slice follows the engineering rules: reproduce the original symptom first, state the
prediction, fix at the root owner, regression, mutation, adjacent tiers, `pylint` and `ruff`, no full suite.

### S5 Semantic contract authority (C-01, R-01)

| Field | Specification |
|---|---|
| Root owner | `kriya/workflow/contract_compilation.py`: `ContractEntry` and `compile_verification_contract` (pure, never consults a model; verified in D0). No second owner: `requirements.py` keeps deriving statements and strength, `verifier_semantic.py` keeps the person's decision |
| What exists (verified in D0) | Per entry: `status` in {`CLOSABLE`, `AUTHORITY_REQUIRED`, `AMBIGUOUS`, `NOT_A_CLAIM`, `DISPOSITIONED`}, `bindings` (claim, closer, authority), `residual` (claim, strength, why, acceptable authorities), `ambiguity`. `VerificationContract.digest` over `identity_payload()` including every entry's `to_dict()`. `refusal()` typed. Consulted by `workflow.py` and `enforce_loop.py` only under `_requirement_policy_blocks` |
| Contract changes | (1) *`ContractEntry.semantic_state`*, a derived property serialized in `to_dict()` (so inside the digest), from one closed table: `AMBIGUOUS` when `status` is `AMBIGUOUS`; `NOT_APPLICABLE` when `NOT_A_CLAIM` or `DISPOSITIONED`; `GENERAL_UNSPECIFIED` when any required claim has a residual; `SPECIFIED` when the entry requires the BEHAVIOR claim and every binding of it has an authority in {acceptance file, acceptance approval, goal examples, external command, runtime observation}; `STRUCTURAL` otherwise (only repository-authority claims: regression, immutability, mutation scope, migration, test addition, file creation, API preservation, documentation predicates). An entry the table does not classify is a programming error raised at compile time. `CONTRACT_COMPILER_VERSION` 4 to 5; `CONTRACT_FORMAT` stays `/1` (additive field). `STRUCTURAL` and `NOT_APPLICABLE` complete the three states of `07` so that every entry has exactly one. (2) **Admission by state, not by policy alone.** `AMBIGUOUS`: attended (an approval channel that can ask) asks one typed question per ambiguous entry, *`AmbiguitySubject`* in `policy/approval.py` (a fifth kind beside the four of `KINDS`); the answer is an operator artifact (a disposition or a mutation-scope statement) sealed through the existing disposition path, and the contract is recompiled; unattended under the production profile: `GoalAdmissionError` before planning (AS-IS under `block`); unattended elsewhere: the run proceeds, the entry stays open, the result is a non-success (S6). `GENERAL_UNSPECIFIED` under `block`: AS-IS refusal with authority requests. Under `record`: admitted, and the entry can never be `CLOSED_BY_EVIDENCE` by anything a model wrote. (3) **Closure rule.** `requirements.py` closure reads the entry's state: a `GENERAL_UNSPECIFIED` entry closes only as operator acceptance (`RESOLVED_WITH_OPERATOR_ACCEPTANCE` counting, never "verified") when a person's VALID verifier decision exists for a verifier of a unit that declares that requirement id in the validated plan **and** that verifier passed in the terminal verification of the committed candidate; otherwise it stays UNVERIFIED. On the direct shape no unit declares requirement ids, so it stays UNVERIFIED. (4) **Unmapped verifier.** `verifier_semantic.py::derive_executed_verifiers`: an executed failing identity that maps to no file and is absent from the pre-run baseline report is `undetermined` (the result that already exists), never "pre-existing". (5) **Planner and Developer.** `planning_request.py` renders each entry's state beside its text; `validate_plan` refuses an integration decision that contradicts a binding of a `SPECIFIED` entry (the binding detail is the clause); rendering a unit's semantic context refuses an unresolved criterion id, typed (INT-08). (6) **Pattern miss (`GAP-A4-05`).** The "requires execution" reading returns three values; *unknown* keeps the Planner's runtime verification |
| Files and modules | `kriya/workflow/contract_compilation.py`, `requirements.py` (closure only), `verifier_semantic.py` (derivation only), `workflow.py` and `enforce_loop.py` (the two admission sites), `planning_request.py`, the plan validator (`AUTH-A4-10`), `kriya/policy/approval.py` (`KINDS`), `control/launch.py` (the kind's desktop frame), `resume_fingerprints` (version only). Tests: `tests/test_requirement_contract*.py`, the verifier-semantic tests, `tests/state_machine/`, the kernel goldens (contract digest changes: regenerated once) |
| Allowed scope | The modules above; the A2 probes P5, P7, P8, P9 turned into tests; one golden regeneration for the digest and the new field |
| Forbidden scope | A model-written clause or specification; a new specification language; widening the ambiguity patterns beyond what the compiler proves; changing what any existing closer accepts; the retry machine; the result word (S6); `CONTRACT_FORMAT` |
| Invariants | A generated verifier is never the first creator of required semantics that closes a requirement. Every entry has exactly one semantic state. The state is inside the contract digest. Nothing a model produced is an input of the compiler. Unknown fails closed (an unclassified entry, an unmapped identity, an unresolved criterion) |
| Acceptance tests | (a) A general behaviour sentence compiles to `GENERAL_UNSPECIFIED`; a goal example to `SPECIFIED`; "Include a pytest test" alone to `STRUCTURAL`; the two mutation-scope ambiguities to `AMBIGUOUS`. (b) Table-driven: every status and binding combination of the compiler's own branches maps to one state. (c) A `GENERAL_UNSPECIFIED` entry with a passing model-written test and no person's decision stays UNVERIFIED on both executors. (d) With a VALID decision on a unit that declares the requirement id and a terminal pass, it counts as accepted, not verified. (e) A `src/`-layout run-authored failing test is `undetermined` and the unit is held. (f) An ambiguity: attended asks once and the recompiled contract has a new digest; unattended production refuses before the Planner; unattended non-production proceeds and the result is not SUCCESS (asserted after S6 lands; until then asserted as UNVERIFIED in `requirement_truth`). (g) A plan fixture with a dangling criterion id is refused at validation and at rendering |
| Negative controls | The contract of a goal with only `STRUCTURAL` and `SPECIFIED` entries keeps every closure it has today (existing requirement-contract tests green without edits other than the digest). A resume across the compiler version is refused as `VERIFICATION_CONTRACT_CHANGED` (the existing resume decision) |
| Mutants | Removing the state from `to_dict()` (digest test fails); mapping `GENERAL_UNSPECIFIED` to `SPECIFIED` (closure test fails); restoring "unmapped = not run-authored" (held-unit test fails); answering the ambiguity question with a default (attended test fails); letting the decision close as verified (counting test fails) |
| Architecture checks | `tests/architecture`: the compiler imports no agent, no LLM client; `policy/approval.py::KINDS` tripwire updated with the fifth kind; the desktop frame vocabulary test |
| Prerequisites | S0 complete. Independent of S1 to S4. S6 depends on this |
| Stop conditions | A verifier file cannot be bound to requirement ids through the validated plan's unit declaration (then no decision closes anything and the rule is recorded as "stays UNVERIFIED"; do not invent a binding). More than the closed pattern set would be needed to classify an ambiguity. A closer's acceptance would have to change |

### S6 Result contract, RunRecord truth, evidence and UI truth (C-02, C-11; R-02, R-11)

| Field | Specification |
|---|---|
| Root owner | `kriya/control/run_coordinator.py::coordinated_mutation` at the outermost boundary (the one result-truth owner; verified in D0: it terminates the record from the payload's claim limited by the record's proofs, then `_reconcile_result`). `kriya/control/run_record.py` for the record's vocabulary and `STORE_CLASSIFICATION`. Each evidence record keeps its writer |
| What exists (verified in D0) | `_claims_success`: `quality_gates_passed is True` or status `success`. `_complete_successful_run`: SUCCESS only with no unverified work unit and a legal transition. `_fail_active_run`: FAILURE or UNCERTAIN. A terminal transition accepts a `terminal_status` (default: the lifecycle's name). `_ANNOTATABLE_FIELDS` is a closed set (it has `retry_counters` and `resume_decision`, no requirement truth). The CLI exits `0 if quality_gates_passed else 1` at three sites (`generate`, the proposal execution, `fix`). `requirements.py::requirement_truth` has the closed vocabulary `ALL_VERIFIED`, `RESOLVED_WITH_OPERATOR_ACCEPTANCE`, `OPEN_REQUIREMENTS`, CONTRADICTED. The history row is one `INSERT OR REPLACE INTO runs` in `control/trace.py` |
| Contract changes | (1) **Requirement truth on the record.** *`requirement_truth`* joins `_ANNOTATABLE_FIELDS` and the RunRecord schema (status, counts, the contract digest it was evaluated against, the candidate hash). Written by the coordinator's `annotate_run` from the terminal evaluation over the committed tree (enforce: the terminal gate service; direct and milestone: the terminal step of the unit or sequence), not from a per-attempt event. (2) **The result contract**, one function *`evaluate_result_contract(record, payload)`* in `run_coordinator.py`, called where `_claims_success` is called today: SUCCESS requires the payload's claim, the record's proofs **and** `requirement_truth.status` in {`ALL_VERIFIED`, `RESOLVED_WITH_OPERATOR_ACCEPTANCE`}. Otherwise, with a settled COMMITTED cycle: the record ends in lifecycle `FAILURE` with `terminal_status = APPLIED_REQUIREMENTS_OPEN` and `commit_result` unchanged; with no commit: `FAILURE`. No new lifecycle state: `TERMINAL_LIFECYCLES`, recovery and the commit-state assessment read the lifecycle and are untouched. (3) **The payload.** `status: "applied_requirements_open"`, `quality_gates_passed: False` (ruling 3), and the facts kept beside it: *`technical_gates_passed`*, `commit_result`, `requirement_truth`, the retained commits. `_reconcile_result` writes these. (4) **Exit codes.** One closed table in `cli.py`: 0 success; 1 failed; the existing refusal and knowledge-gap codes unchanged; a dedicated non-zero code for `applied_requirements_open` (the first value the table does not use, pinned by a test), the same at the three exit sites. (5) **A missing or malformed requirement truth is not `ALL_VERIFIED`** (C-02 UNKNOWN row). An entry shape with no requirement set gets an explicit one before this lands: `kriya fix` by INT-05; the others by the census below. (6) **Seven truths.** Plan validity: both executors call `validate_plan`; `classify_plan_completeness` stays as the direct shape's operational check and never a second verdict. Requirement truth and closure: one closer set; the two closers that cannot fire under enforce are called from the terminal gate service. Final review: `plan_executor.py::_passed` stops reading the review; `final_review_refusal` is recorded and shown on every executor; `RunState.final_workflow_quality_passed` no longer mixes the review in. A guard that exists on one executor (the false-premise mutation guard) is called by both. (7) **Records (C-11).** `STORE_CLASSIFICATION` gains the attempt-evidence store ("integrity against damage, diagnostic") and the run logs ("diagnostic"). The history row is written after reconciliation, with the reconciled status, the workspace identity and the RunRecord revision; a reader reports `consistent`, `differs` or `record unavailable`. The desktop reads requirement truth from the RunRecord annotation, never the last event. "VERIFIED" for an evidence chain becomes "intact" in the KUP projection and the UI, and the evidence page states "diagnostic history, not tamper-evident" (ruling 6); `docs/design.md` and `docs/user_guide.md` state the classification. (8) **Typed stop reason (INT-01).** A stop carries a reason code from one closed table plus a message; `outcome.py::terminal_failure_category` reads the code; an unregistered code is its own category, never "environment". (9) **Labels** (the sixteen R-11 rows): attribution kind written at the decision with `unknown` as its own value; `attempts` and retries-triggered counted by the retry owner; gate purpose declared by every orchestrating entry; the role of a knowledge-channel call; held-gate evidence written after the gates; a parent run id on a resumed run's RunRecord, evidence manifest and history row (INT-10); retention owners for launch records, review sidecars and run logs |
| Files and modules | `kriya/control/run_coordinator.py`, `run_record.py`, `control/trace.py` (history row), `application/history.py`, `kriya/kup/` projections, `kriya/workflow/outcome.py`, `plan_executor.py`, `enforce_loop.py` (acceptance reader, terminal tail), `terminal_gate_service.py`, `development_loop.py` (the `requirement.truth` emission stays as an event), `milestones.py`, `kriya/cli.py` (exit table, `fix` requirement), `control/retention.py`, `ui/` wording, `docs/design.md`, `docs/user_guide.md`. Tests: `tests/test_prd007_run_lifecycle.py` (the classification pin), `tests/test_outcome.py`, `tests/state_machine/` goldens, `tests/test_workflow_controller_enforce.py`, the KUP contract tests |
| Allowed scope | The modules above. One regeneration of the kernel goldens, the state-machine goldens and the outcome goldens, in this slice only, reviewed as a diff: every changed golden line must be explained by a clause above |
| Forbidden scope | Merging the executors. A new lifecycle state. Any change to the commit transaction, `kriya recover` or the commit-state assessment. The approval subject (S7). The retry machine and the repair route after a terminal gap (S8). An authenticated evidence store. Weakening a golden to pass |
| Invariants | SUCCESS implies requirement truth resolved. Applied never implies verified. One owner writes the terminal state. Every surface that states an outcome reads the reconciled result or the RunRecord. A diagnostic record never changes behaviour. The record's lifecycle machinery is unchanged |
| Entry-shape census (first step) | `grep -rn "requirements_from_goal=False\|coordinated_mutation" kriya`: for every mutating entry, which requirement set it carries. Known from the maps: `generate` direct, `generate` enforce, milestone sequence, `fix` (none today), proposal execution |
| Acceptance tests | (a) A3 probe P8 inverted, under every profile and on all three executors: committed with an open requirement is `applied_requirements_open`, `quality_gates_passed` False, the dedicated exit code, RunRecord `FAILURE` / `APPLIED_REQUIREMENTS_OPEN` / COMMITTED. (b) The same run with every requirement closed is SUCCESS. (c) One table-driven test runs a scripted plan through direct and enforce and compares the seven verdicts. (d) A refused final review on a committed, verified unit leaves the unit accepted on all three executors (A3 P1, P2, P3 inverted for direct and milestone). (e) A scripted contradiction (payload claims success, record does not): the history row holds the reconciled status and the RunRecord revision. (f) A6 probe P5: a row that differs from its record is reported `differs`; a row of another workspace is not attributed. (g) A scripted run whose last attempt differs from the applied candidate shows the applied candidate's truth (INT-04). (h) A `fix` whose failure persists is not SUCCESS; one whose failure is gone ends SUCCESS with that requirement verified. (i) Every stop producer yields its category by code; an unregistered code fails a tripwire |
| Negative controls | A run with a missing `requirement_truth` annotation is not SUCCESS. A record whose commit is UNCERTAIN still ends `needs_review` (unchanged). `kriya recover` on an `APPLIED_REQUIREMENTS_OPEN` record changes nothing (it is terminal and settled). The production profile's early stop under `block` still fires |
| Mutants | Dropping the requirement-truth proof from `evaluate_result_contract`; leaving `quality_gates_passed` True in the applied-open payload; writing the history row before reconciliation; reading the last `requirement.truth` event; restoring the review into `_passed`; mapping an unknown stop code to "environment". Each must fail a test above |
| Architecture checks | `tests/architecture` and the platform guard: `control/` does not import `workflow/`; the coordinator stays the only caller of the terminal transition (a structural test over `transition_mutating_run` call sites); `_ANNOTATABLE_FIELDS` pin; the KUP contract version if a projection field changes |
| Prerequisites | S5 (the entry's state decides what can close). S0. The census. Independent of S3 and S4 |
| Stop conditions | The transition from a settled COMMITTED cycle to `FAILURE` with a typed terminal status is refused by the legal-transition table in a way that needs more than adding that transition. An entry shape with no requirement set other than `fix` for which no explicit requirement can be stated from operator input (owner decision). Making the two enforce-missing closers callable needs a change to how the plan sandbox is built (then record the closer set per executor as the contract). A golden change that no clause explains |

### S8 Retry continuity, durable resume ledger, run resource budget (C-07, C-08; R-07, R-08)

| Field | Specification |
|---|---|
| Root owner | `kriya/workflow/retry_policy.py` (the one grant path: `observe_failure_family`, `reset_scoped_budgets_for_new_family`, `charge_failed_attempt`; verified in D0) for the allowance. `kriya/workflow/open_findings.py` for what a finding is. A new small module *`kriya/control/retry_ledger.py`* for durability, writing through `control_store.write_control_file` under the **owner workspace's** control state. `kriya/control/state.py::ControlState` for plan-level recovery state. `kriya/config/authority.py` and the run lease (`run_coordinator.py`) for the budget |
| What exists (verified in D0) | `open_findings.replaces_open_finding` decides both "replace the open finding" and "may open a family", through `is_candidate_finding`, a deny-list (`_NON_CANDIDATE_CATEGORIES`, `_NON_CANDIDATE_TYPES`, `DELIVERY_FAILURE_TYPES`): an unlisted type is a candidate finding. `RetryBudgets` (`workflow/state.py`): `retry_count`, `targeted_retry_count`, `api_contract_recovery_count`, the fallback-targeted flags, `seen_failure_signatures`, `last_failure_signature`. `ControlState` has subtask states, written files, plan and contract hashes, `subtask_completion_scope`; no retry or recovery state. `_VALUE_BOUNDED_FIELDS` maps six fields to packaged ceilings. `enforce_loop.py::_discard_sandbox_completions`; a resume refuses `COMPLETIONS_NOT_IN_WORKSPACE` |
| Original symptoms (first gates; X-13) | (1) `GAP-A1-07` at loop level: after a staging rejection, a `no_progress_retry` replaces the open finding and replenishes the scoped budgets (A1 probe P10 on the pure owners; the scripted loop is new). (2) `BYP-A1-01`: one unresolved blocker runs seven unit invocations, each with a fresh scoped allowance (A1b probe P12, the committed enforce test). (3) A process killed after N charged attempts resumes with zero charges. (4) A5 probe P6: a repository sets the budget to 999,999 or `null` with no approval. (5) Scenario-04 run 1's shape: three accepted units discarded when the fourth stops. Each is reproduced before any change; one that cannot be reproduced is a stop for that part only |
| Contract changes | (1) **Predicate split.** *`may_open_failure_family(failure)`* in `open_findings.py`: an allow-list of candidate-failure types (the types the validators and gates produce today, enumerated from the code at implementation and pinned by a tripwire test); an unlisted type inherits the previous family. `replaces_open_finding` keeps its present meaning for memory. `retry_strategy._record_attempt_failure` calls the new predicate for the family. (2) **Allowance ledger across invocations.** *`RetryLedger`*: per (workspace identity, plan identity or, on the direct shape, contract digest; unit id): the family history (`seen_failure_signatures`, `last_failure_signature`), `targeted_retry_count`, the fallback-targeted flags, `api_contract_recovery_count`, the open finding, the fallback state, the attempt count, schema version. One constructor *`retry_ledger.budgets_for(unit)`* is the only way a `GenerationState` gets its `RetryBudgets`: it continues the recorded scoped allowance and family history; the full-set counter and the per-invocation ceiling stay per invocation (the run's work ceiling bounds their sum). A declared reset (a plan revision that changes the unit's content hash; a person's recovery decision; best-of-N's independent candidate) restarts the unit's ledger with a recorded reason. (3) **Durability.** The ledger is written at every recorded failure and every grant, atomically, outside the plan sandbox; so it does not depend on an ignore rule (INT-11). `ControlState` gains the enforce recovery state of `AUTH-A1-14`: cycle counters, reopened owners, the verifier semantic ledger's decisions and regeneration allowance, replay handles, human recovery decisions (schema version raised; an older state loads with these empty and says so). (4) **Allowance record.** Every grant, charge and declared reset is a typed run event and evidence record with the counters before and after (`GAP-A1-03`). An internal Kriya error charges nothing and is labelled internal (INT-12). (5) **Unreadable ledger.** The unit runs with no scoped allowance for families it cannot account for, and says so; never a silent full grant. (6) **Resumable accepted unit work (ruling 9).** A completion of scope `candidate` is reusable by a resume only when the recorded plan identity (INT-02: the content hash of the validated plan, chained across revisions), the verification-contract digest and, per unit, the batch digests of its accepted candidate all match and the sandbox bytes are re-hashed equal; otherwise the completion is discarded as today. The terminal commit stays atomic. A resume reads the approved plan document by identity and carries the earlier run's id as parent; it does not ask the Planner for a byte-identical plan (`GAP-A4-06`). (7) **Budget value (X-14).** `("autonomy", "generation_time_budget_seconds")` joins `_VALUE_BOUNDED_FIELDS` with ceiling 3,600: a repository value of 1 to 3,600 is `REPOSITORY_SAFE`; a larger value or `null` is `SECURITY_AUTHORITY`. A run with no bound says "unbounded" in the preflight and the run events. (8) **Work ceiling (ruling 10).** Held on the run lease beside the clock: a count of unit invocations and of Developer attempts for the top-level run. The ceiling is not a new tuning number: unit invocations at most 7 per planned unit (the bound the enforce loop's own caps already imply: one planned plus three cycles of owner recovery and consumer retry, A1b), counted run-wide and persisted as a RunRecord annotation at each unit boundary; an operator may set a lower value (a value-bounded field). Reaching it is a typed stop. (9) **Resume ledger.** A resumed run records its parent and the time and work the parent consumed; by default it continues that ledger; a new budget is an explicit operator flag, recorded on the RunRecord. (10) **Repair after a terminal requirement gap** (`GAP-A3-02`): the gap names its owning unit through requirement ownership (`AUTH-A4-15`) and opens one recovery episode for it under the ledger; without an owner the run ends as S6 defines. (11) **Small corrections.** The no-progress limit is validated to its effective range (`GAP-A1-05`); the per-requirement cap that cannot fire is removed with its comment (`GAP-A1-08`); one typed vocabulary for a recovery unit's outcome |
| Files and modules | `kriya/workflow/open_findings.py`, `retry_policy.py`, `retry_strategy.py`, `state.py` (`RetryBudgets` construction), `best_of_n.py` (declared reset), `enforce_loop.py` (recovery state, completions, resume block, the dead cap), `owner_recovery.py`, `verifier_semantic.py` (ledger persistence), `kriya/control/state.py`, *`control/retry_ledger.py`*, `control/checkpoint.py` (nothing moves; the unit checkpoint keeps its content), `run_coordinator.py` (lease counts), `run_record.py` (annotations), `kriya/config/authority.py`, `config/config.py` (the lower-only work field, classified), `cli.py` (resume flag). Tests: `tests/state_machine/` (retry model, trajectories, failure families, budget transitions), `tests/test_workflow_controller_enforce.py`, the nine NON-CANDIDATE-FAILURE-SCOPED-RESET tests, `tests/test_s3b_resource_bound_value_gating.py` |
| Allowed scope | The modules above; the state-machine goldens regenerated once for the new events and the ledger |
| Forbidden scope | A blocker registry or a typed blocker key as authority (owner ruling, WS4); the old `Finding / FindingState` design; exact-output identity as the allowance authority; a second clock; committing unit work before the terminal gates; changing any retry constant or ceiling value; tuning timeouts |
| Invariants | An unknown failure type never widens a budget. One grant path. An allowance is never silently full after an interruption or a re-invocation. Accepted work is reused only by identity and re-hash, never because a file exists. A repository cannot raise or remove the run's time bound without the operator. An environment refusal charges nothing (S0's verdict). The terminal commit is atomic |
| Acceptance tests | (a) The loop-level `no_progress_retry` scenario: the scoped budgets are unchanged. (b) A type added to a validator without the allow-list fails the tripwire. (c) The seven-invocation scenario: the second invocation starts with the first's family history and scoped charges; total targeted attempts for the one blocker equal one allowance. (d) Kill after N charges, resume: N charges, the same open finding. (e) A person's recovery decision is asked once across a restart. (f) The ledger file deleted or corrupted: no scoped allowance, a stated reason. (g) An enforce run whose fourth unit stops: a resume with identical plan, contract and bytes reuses three completions; changing one byte of one unit's files, the contract or the plan discards them. (h) The resume test passes in a repository that does not ignore `.kriya/`. (i) A5 P6 inverted: 999,999 and `null` need approval; 2,400 is safe. (j) A scripted plan whose re-invocations exceed the work ceiling stops typed at the ceiling; a resumed run continues the parent's counts unless the flag is given. (k) B-P2 kept as the pin of the clock's scope |
| Negative controls | The nine run-10 tests green without edits. Every existing state-machine trajectory golden changes only by the new events. A new failure family (a genuinely different assertion) still earns one fresh scoped allowance. A completed and committed plan resumes as today |
| Mutants | Reverting the predicate split; dropping the ledger read in `budgets_for`; writing the ledger inside the sandbox; granting the full allowance on an unreadable ledger; reusing a completion without the re-hash; removing the budget from `_VALUE_BOUNDED_FIELDS`; not counting a re-invocation on the lease; starting a new budget on resume without the flag. Each must fail a test above |
| Architecture checks | `tests/architecture`: `control/` does not import `workflow/`; the ledger writes only through `control_store`; `test_platform_architecture_guard` and the file-write inventory classify the new writer; the `AppConfig` field is classified in `config/authority.py`; `ControlState` schema-version pin |
| Prerequisites | S0 (environment verdict), S5 (the contract digest and plan-declared requirement ids), S6 (the terminal vocabulary and the RunRecord annotation pattern). Last because it changes the state machine everything else is verified with |
| Stop conditions | The allow-list of candidate-failure types cannot be enumerated from the producers without changing a validator's vocabulary. Carrying the scoped allowance stops a kernel-golden scenario that converges today (the constants are then wrong for the new accounting: report, do not tune). The work ceiling of 7 per planned unit stops a scenario that passes today. A completion's batch digests are not recorded anywhere that survives the run (then the binding has to be designed with S7's commit record; report) |

## 7. D0 exit check

| Condition | State | Evidence |
|---|---|---|
| Unexplained cross-map contradictions | 0: X-11 to X-18 resolved in place | Sections 2.3, 3 |
| Bypass rows without a disposition | 0 of 93 | `05`, "Dispositions of every row"; `evidence/d0/d0_check.py` |
| S5, S6, S8 implementation-ready | Yes: twelve required fields each, against code read in D0 | Section 6; `d0_check.py` |
| Registry mapping | 141 of 141 intact; no row moved | `d0_check.py`; `evidence/b/registry_root_map.py` unchanged |
| Diagrams and tables agree | No diagram node changed meaning: X-11 to X-18 touch no node of `10`; D-5 and D-8 describe AS-IS, which did not change | `evidence/b/crossmap_check.py`: 0 dangling. It now reports 126 rows "defined more than once": the 27 A1 / A2 completions (X-07), the A1b characterization rows and the 93 disposition rows of `05`, each a second appearance by construction |
| Code facts cited in D0 | 68 citations and negative controls, 0 problems | `evidence/d0/check_citations_d0.py` |
| Unexplained TODO, TBD, PENDING, UNKNOWN | 0: the register covers every open statement | `evidence/d0/open_statements.py --register` |
| A new P1 root | None | Section 2.3 |
| A ruling invalidated | None | Section 1 |
| Dependency order changed | No. S5 is smaller than reported (X-12); order unchanged | `07` section 4 |

Limits, stated plainly. The sweep compares statements that share a lexicon; a contradiction phrased with none of the
twelve could remain. Section 5 rechecked the registry rows of eight roots clause by clause, not the rows of R-04, R-12
and R-13. Section 6 was written from the owners' code (the compiler, the coordinator, the record, the retry policy,
the finding predicate, the authority table, the admission sites, the exit sites); the enforce loop's recovery block,
`retry_strategy.py`, `terminal_gate_service.py` and the KUP projections were located, not read, so the first step of
each slice (reproduce the original symptom) is also the check that the named call sites are as the maps say. That is
the reproduce step of the engineering rules, not a new discovery round; if it shows a named owner is wrong, it is a
stop.

## 8. Owner rulings given with the S0 authorization (2026-11-15, after D0 was accepted)

Recorded on the implementation branch (`implementation/closure-s0`, cut from the closure head `ef89dcd`; the closure
branch stays frozen and documentation-only). They settle the two owner items of section 1 and correct the S0 sequence.

| # | Ruling | Carried into |
|---|---|---|
| 15 | **Implementation base.** The implementation branch and worktree are cut from the closure head `ef89dcd`, not from `main`; the invariant "`main` and `ef89dcd` differ only under `docs/architecture/`" is re-checked before branching (checked: empty diff). The architecture documents are not copied by hand | Done |
| 16 | **Execution hold.** Only human-in-the-loop mode creates a blocking human review or approval wait by default. Invariant: *if policy says a human approval is REQUIRED for this mutation, execution MUST hold, however the request was surfaced.* In guardrails and every other non-HITL mode no pseudo-pending human approval is created while execution continues; a non-blocking review or event is explicitly typed ADVISORY; no event is labelled or treated as a required approval if execution proceeds without it | S7 (C-03 encodes the distinction: required approvals hold, advisory events are typed advisory). Supersedes the proposal of section 1 and the D0 clause X-16 where they differ |
| 17 | **Model-inferred optional runtime check.** It may not independently make a run fail. It may give diagnostic evidence, advise further verification, and become mandatory only when an authoritative requirement, semantic-contract or technical-gate owner promotes it. It may not create a required product semantic, close or open a requirement, or turn an otherwise valid run into a terminal failure. Rule: *only authoritative requirement and semantic-contract checks and mandatory technical and safety gates may determine terminal verification failure* | S5 (C-01: a model-inferred check is never a clause) and S6 (C-02: the result contract reads requirement truth and mandatory gates only). Closes the owner item on `OPTIONAL-RUNTIME-GRADED-FAILURE-POLICY-001` |
| 18 | **S0 certifies an immutable snapshot.** Reproduce and classify, implement, focused tests with negative controls, mutants and static checks, an exact S0 checkpoint commit, then the one authorized full suite on a separate clean worktree pinned to that commit. S1 and S2 may proceed in the implementation worktree once the certification has started. S0 is not VERIFIED or CLOSED until the certification passes; a material S0 defect found by it stops reliance on S0; no rerun for luck | S0 |
| 19 | **R-12.** Each of the fifteen harness rows is reproduced first and classified CONFIRMED, STALE, DISPROVED or INTENTIONAL before any edit; only a confirmed defect is fixed, at its root owner; harness behaviour is not changed to satisfy old registry wording | S0 |
| 20 | **Probes become tests sparingly.** For each materially relied-on behaviour: the smallest durable owner-level characterization or acceptance test, a negative control and a regression; equivalent probes are merged; no duplicate suite | S0 and every later slice |
| 21 | **R-13 invariant.** At every container launch and admission site: *container or environment unavailable -> a typed ENVIRONMENT verdict -> zero candidate and retry charge -> never a gate or product failure.* Proven with a deterministic scripted refusal; a path that cannot be proven without a real daemon is reported with the exact missing proof, the invariant is not weakened | S0 |

## 9. Slice S0: what was reproduced, fixed and proven (2026-11-15, implementation branch)

State: S0 is **implemented but not certified and not closed** until the one authorized full suite passes on a clean
worktree pinned to the S0 snapshot commit. The last R-13 launch site (the managed service launch) was fixed in a second
part under the owner's ruling of the same day (below); the snapshot is the commit that contains it.

**Registry (ruling 13).** The 11 stale, 2 disproved and 5 duplicate rows are closed with their disposition and their earlier
text kept. Ten more rows were closed by S0 itself and three rows were added (below).

**R-13, the Phase-D blocker: a confirmed product defect, fixed where a typed fact exists.**

| Launch site | Before (`MEASURED` with a scripted backend refusal, no daemon) | After | Proof |
|---|---|---|---|
| Python compile, pytest, acceptance runner, Ruby compile / tests / bundle install (`tools/validate.py::_validation_result`) | A refusal other than the host-present bind-source case returned an ordinary failed gate with no `environment_reason_code`: a compile or test failure of the candidate | Any never-started tool is a typed environment verdict (`BACKEND_TARGET_NOT_STARTED`, or the backend's own diagnostic code) | `tests/test_r13_environment_verdict.py`, 7 gate cases; mutants M1, M2, M3, M5, M6, M7 |
| `pip install` of the project environment | Reported as `install_error`, a dependency problem of the candidate | `PYTHON_ENVIRONMENT_UNAVAILABLE` | Same file; mutant M4 |
| Artifact build before a runtime or service check (`tools/service_runtime.py::_prepare_required_artifact`) | Return code 125 kept, so the caller raised a `compile` failure of the candidate; the validator-result conversion dropped the never-started fact | No return code: the caller's `verification_infrastructure_failure` | Same file, both routes; mutants M12, M13 |
| The retry machine (`open_findings.py`, `retry_policy.py`, `retry_strategy.py`) | The environment stop replaced the open finding, could open a failure family (replenishing scoped budgets) and was charged | `ENVIRONMENT_VERDICT_FAILURE_TYPES` (`workflow/failure.py`): no finding, no family, no charge | Same file, incl. the real recorder over run 10's attempts; mutants M8 to M11 |
| Java adapters, POM validation, venv creation, the application run | Already typed | Unchanged | Pinned by the gate cases |
| Static-analysis scanner | A scan with no output and exit 125 is `MALFORMED_OUTPUT`, never clean, no finding (`MEASURED`) | Unchanged | One test |
| Baseline, terminal gate, requirement closers | They call the validator's gates and stop on any `environment_reason_code` (`TRACED`: `validation_baseline.py`, `verification_coordinator.py`, `acceptance_jvm.py`) | Covered by the seam fix | Their existing tests |
| **Managed (long-running) service launch** (second part, owner ruling "S0 MANAGED-SERVICE R13 RULING") | A managed launch had no launch stage, so the fact did not exist. `MEASURED` with the scripted refusal: `SERVICE_EXITED_BEFORE_READY`, return code 125, "Service process exited with code 125 before becoming ready"; the backend's text was then offered to the classifiers of application output | The managed launch goes through the same launch stage as a finite command (`ProcessController.start_managed`); `ManagedProcess` reads the two records (`target_started`, `launch_refusal_diagnostic`) and never returns them as output; `service_runtime` consumes the fact: `SERVICE_START_FAILED`, no return code, `environment_reason_code`; `runtime_verification` hands the code to the existing environment stop before any text is classified. Started-then-exited stays `SERVICE_EXITED_BEFORE_READY` | `tests/test_r13_environment_verdict.py` (9 managed cases, incl. the real recorder), `tests/test_runtime_verification.py` (3 workflow cases); 13 mutants killed; registry row closed |

First part: sixteen targeted mutants, sixteen killed; adjacent tiers green: state-machine tier and never-started suites (892), gate
orchestrator and runtime verification (420), architecture tier (200); `pylint` and `ruff` clean. Second part: thirteen targeted
mutants, thirteen killed; the affected suites and static checks were re-run before the snapshot commit (results in the commit message).

**Owner ruling on the managed-service site (2026-11-15, "S0 MANAGED-SERVICE R13 RULING"), as implemented.** Reuse the existing
launcher stage and its two records; no daemon-text matching, no container-existence polling, no second launch protocol, no
backend-specific heuristic. The process layer (`kriya/tools/process.py`) owns the observation; `service_runtime.py` consumes the
typed fact and never reads raw stderr for it. Mapping: never started -> the existing environment path; started then exited before
ready -> `SERVICE_EXITED_BEFORE_READY`; ready -> unchanged. Readings, mine, standing until the owner corrects them:

- *A service whose binary the launcher could not exec is "never started"* (the launcher's own not-started record), exactly as
  for a finite command since REPLAY-CONTAINED-NOT-STARTED-001. Before, it was `SERVICE_EXITED_BEFORE_READY` with status 127.
- *The launch-stage execution semantics now cover a managed service*, including the intentional contract FINITE-LAUNCH-ENOEXEC-001
  (an executable text script without a shebang is run by the launch-stage shell). The earlier note "deliberately unwrapped:
  managed services" is superseded by the ruling; MCP persistent stdio and pinned `image_reference` profiles stay unwrapped.
- *The outcome kind is the existing `SERVICE_START_FAILED`*; no new kind. The environment verdict is keyed on the new typed
  field `environment_reason_code`, never on the kind or on text.
- *No relaunch.* The validator's one relaunch for the host-present bind-source code (BIND-SOURCE-RELAUNCH-001) is not extended
  to a managed launch: the ruling asks for the typed verdict, not for new recovery behaviour.

Limits, stated plainly: a real daemon *refusal* of a managed launch is proven with the scripted backend only (the launcher
contract is the production one; the real-OCI managed-service tests prove the stage on a real daemon for a service that starts).
A service Kriya terminates at its readiness timeout while the backend is still creating the container is `READINESS_TIMEOUT`
as before: the fact is read only for a process that exited on its own, as a finite command's is not read after a timeout.

**The pre-commit gate of the second part, and a gap in the first part's proof (reported, not folded in).** The state-machine
tier (1,786) and the affected and architecture suites (1,650), run once on the working tree before the snapshot commit, were
both red: nine failures, none in the managed-service change, each classified and fixed at its owner.

| Failures | Class | Disposition |
|---|---|---|
| Five workflow tests (`test_workflow.py` x4, `test_prd017_fallback_transition.py` x1) | Harness defect exposed by the first part: the test stand-in's launcher imported the candidate's `math.py` in place of the stdlib module and died before its reached record; the untyped compile gate used to misread that as a compile failure, and the tests passed on the accident | Stand-in launcher runs with `-P`. Registry `STANDIN-LAUNCHER-CWD-IMPORT-SHADOW-001` (closed) |
| `test_handle_attempt_failure_classifies_containment_setup_error_deterministically` | Stale expectation of the first part's own rule (it pinned one retry charged for `containment_setup_failed`) | Expectation is zero, ruling 21 cited |
| `test_inventory_counts_the_design_relies_on` | Stale pin at the baseline (99 sites on `main` too; `9323129` added one) | Pin corrected. Registry `LR-R1-M1-INVENTORY-PIN-STALE-001` (closed) |
| Two real-daemon leak checks (`test_s4_1_managed_service_view.py`, `test_finite_launch_target_start.py`) | Containers listed right after the run, gone seconds later, only under parallel load; their state was not captured (`UNKNOWN`), daemon removal in progress is `INFERRED` | The checks read the daemon's own removal completion and fail at once on a running container (`tests/_oci_leftovers.py`). Registry `OCI-LEAK-CHECK-READS-DAEMON-REMOVAL-IN-PROGRESS-001` (open: the certification log classifies it) |

The first six were already failing at `ec2bec1`: the first part's "adjacent tiers green" covered a subset that did not include
`tests/test_workflow.py` or the whole state-machine tier. That statement was too strong; this gate is the correction. After the
fixes the nine tests pass (`MEASURED`, targeted run); the two tiers were **not** re-run as a whole, because the one full suite on
the pinned snapshot is the certification.

**R-12, the fifteen harness rows, reproduced first (ruling 19).**

| Class | Rows |
|---|---|
| CONFIRMED and fixed | `FINDER-ALIAS-BLIND-001` (10 planted aliases evaded the finders; fixed in `tests/architecture/access_sites.py`, 3 mutants killed), `WAVE8-TRACKED-BYTECODE-001` (untracked, tripwire test), `TEST-SLICE-A-OCI-STALE-NESTED-COMMAND-STRING-001` (text) |
| STALE: already fixed or not reproducible at the baseline, closed with the measurement | `TEST-DEADLINE-TIMING-001`, `TEST-LAUNCH-APPROVAL-EXPIRY-TIMING-001`, `KERNEL-GOLDEN-RUNID-PATH-NORMALIZATION-001`, `KERNEL-RUNTIME-TRACE-PATH-LENGTH-DEPENDENT-001`, `DEMO-V2-FIXED-STUB-PORT-PARALLEL-001`, `CERT-DEMO-V2-IDLE-STUB-XDIST-001`, `TEST-KERNEL-GOLDEN-ORDER-DEPENDENCE-IN-AD-HOC-MIX-001` (the last three on one run each) |
| CONFIRMED, root owner is R-05: closes with S3 | `KERNEL-TESTS-CHECKOUT-MEMORY-MORE-FILES-001` (reproduced: a fresh worktree gained `./memory/dependency_graph.db`), part (a) of `KERNEL-GOLDEN-HARNESS-VOLATILITY-001` |
| Not reproducible here, unchanged | `CERT-CI-PLAIN-JOBS-LOCKFILE-GROUP-001` (hosted CI), `DEMO-RESET-RM-WORKSPACE-RACES-DOCKER-RELEASE-001` (needs a real daemon), `TEST-RESOURCEWARNING-HYGIENE-001` (not a certification hazard: the suite does not make that warning an error) |

**New rows found by S0:** `R13-MANAGED-SERVICE-LAUNCH-REFUSAL-NOT-TYPED-001` (P2, closed by the second part), `REGISTRY-CSV-ROW-WIDTH-001` (P3: ten registry rows
do not have the header's width), `ACCEPTANCE-ENVIRONMENT-ERROR-FLAG-NO-READER-001` (P3: a result field with a writer and no reader).

**A finding for S8, measured here:** `is_candidate_finding` is a deny-list, and before this slice it classed the environment stop
as a statement about the candidate. S0 fixed the environment types only; the general fail-closed allow-list stays S8's predicate
split.
