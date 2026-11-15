# Kriya architecture closure

**Canonical runtime baseline described by every document in this set:** commit
`3eded93c0cfb50f8855d04510e3f9aa2cd5208f7`, tree `97a3da6b2ef664e2b7e25c4c6fe3fc3f42f253a0` (`main` = `origin/main`).

These documents are written on the branch `architecture/closure-3eded93`. That branch changes documentation and
evidence only: runtime code, tests, goldens, the registry and sealed run evidence are read-only. It does not move
`main`. The implementation freeze is in force.

## Method

- **AS-IS first.** Phase A records what the code does today. Anything that describes a desired design is marked
  `PROPOSED` and exists only where a gap cannot be understood without it.
- **One home per fact.** Primary facts live in the six global maps below, one row per fact, each with a stable ID.
  Stage and integration documents (written after the maps are stable) are derivative views: they cite row IDs and
  never redefine authority.
- **Evidence labels.**
  - `MEASURED`: deterministic behaviour executed during this closure against this baseline, or a preserved
    deterministic measurement whose baseline and evidence location are stated.
  - `TRACED`: source, control or data flow proven by reading the code or by a recorded search.
  - `INFERRED`: a conclusion consistent with the evidence but not directly proven.
  - `PROPOSED`: desired design.
  A `TRACED` claim is never promoted to `MEASURED`.
- **Citations.** `path::symbol` at the baseline commit. Line numbers are supplementary.
- **Single-owner claims** record the negative-control search that looked for another writer or decision maker.
  A search for "nobody passes X" must also look for keyword forwarding (`**kwargs`) at each call site: the A1
  checkpoint missed a forwarded `resume` flag that way.
- **Operating authority (owner, 2026-11-15).** Slices A1b to A6, Phase B and Phase C run as one authorized program
  without a stop between slices; the only routine owner stop is the Phase-D report. Registry rows that concern one
  root issue are traced once and the evidence is applied to each row individually.
- **Registry.** `handover/BACKLOG_REGISTRY.csv` is evidence being reconciled. It is not edited in Phase A.
  `REGISTRY-COVERAGE.md` carries one row per non-closed registry row.

## Row ID scheme

| Prefix | Map | Example |
|---|---|---|
| `STAGE-` | `00-END-TO-END-LIFECYCLE.md` | `STAGE-RETRY` |
| `AUTH-<slice>-nn` | `01-AUTHORITY-MATRIX.md` | `AUTH-A1-03` |
| `SM-<slice>-nn` | `02-STATE-MACHINE.md` | `SM-A1-02` |
| `MUT-<slice>-nn` | `03-MUTATION-AUTHORITY.md` | `MUT-A1-04` |
| `FR-<slice>-nn` | `04-FAILURE-RECOVERY-MATRIX.md` | `FR-A1-01` |
| `BYP-<slice>-nn` | `05-INTEGRATION-BYPASS-MAP.md` | `BYP-A1-02` |
| `GAP-<slice>-nn` | `GAP-LEDGER.md` | `GAP-A1-01` |
| `DOC-<slice>-nn` | this file, "Existing documents" | `DOC-A1-01` |

## Documents

| File | Content | Status |
|---|---|---|
| `00-END-TO-END-LIFECYCLE.md` | Stage index and the end-to-end flow | Every stage filled (A1 to A6) |
| `01-AUTHORITY-MATRIX.md` | Who decides each truth | A1 to A6 rows (A3 to A6 rows carry the full field set; A1 and A2 rows are completed in Phase B) |
| `02-STATE-MACHINE.md` | States and transitions | A1 to A6 rows |
| `03-MUTATION-AUTHORITY.md` | Every mutation and its authorization path | A1 to A6 rows |
| `04-FAILURE-RECOVERY-MATRIX.md` | Failure source to terminal outcome | A1 to A6 rows |
| `05-INTEGRATION-BYPASS-MAP.md` | Side routes and bypasses; the extensibility seams (vLLM, Skills/Tools UX) | A1 to A6 rows |
| `GAP-LEDGER.md` | Contradictions and gaps found by each slice | A1 to A6 sections |
| `REGISTRY-COVERAGE.md` | One row per non-closed registry row | 141 of 141 examined: 138 reconciled, 3 `NEEDS_CHARACTERIZATION` declared for Phase D with the exact missing evidence (one of them a blocker for the verification-truth statement), 0 pending |
| `06-GAP-AUTHORITY-AUDIT.md` | Phase B: the thirteen root issues, the facts first established in Phase B, the cross-slice checks, the authority audit over every listed truth, the three Phase-D characterization items, the extensibility conclusions, the dependency graph between roots | complete |
| `07-REQUIRED-CONTRACTS.md` | Phase C: the required contract per root issue (`C-01` to `C-13`), the corrections in the addendum's form, the implementation slices and their derived order, what stays unchanged, the clauses for the integration findings. Everything in it is `PROPOSED` | complete |
| `08-STAGE-DOCUMENTS.md` | Phase C: each of the 24 stages with the 21 fields, citing map rows only | complete |
| `09-INTEGRATION-CONTRACTS.md` | Phase C: 16 contracts between stages with the 15 fields | complete |
| `10-DIAGRAMS.md` | Nine diagrams whose nodes carry map row IDs | complete |
| `11-PHASE-D-REPORT.md` | The consolidated owner report (sections A to S, the blocker, the quality gate, the owner rulings in force) | complete; awaiting the owner |
| `REGISTRY-ROOT-MAP.md` | Every non-closed registry row with its class and root issue (generated and checked) | 141 of 141: 71 under root gaps, 11 stale, 5 duplicate, 2 disproved, 13 intentional, 36 future, 1 Phase-D blocker, 2 non-blocking Phase-D items |
| `evidence/b/` | Probe B-P1 and output, the Phase B citation check, the registry root-map generator | complete |
| `evidence/a6/` | Probes P1 to P7 and output, citation check with 47 negative controls, the A6 coverage entries | complete |
| `evidence/a5/` | Probes P1 to P8 and output, citation check with 34 negative controls, the A5 coverage entries | complete |
| `evidence/a4/` | Probes P1 to P11 and output, 37 committed tests, citation check with 18 negative controls, the A4 coverage entries | complete |
| `evidence/a3/` | Probes P1 to P8 and output, 2 committed tests, citation and negative-control check, the A3 coverage entries | complete |
| `evidence/a1/` | Probe scripts and their output for slices A1 and A1b; the coverage generator and the one overrides file it reads | complete |
| `evidence/a2/` | Probe P1 to P10 and output, 7 committed tests, citation check, the A2 coverage entries and the merge script | complete |
| `CORR016_...`, `SEC001_...` | Design documents that predate the closure | CORR016 reconciled in A3; SEC001 classified in A5 (`DOC-A5-05`: historical proposal, implemented then superseded) |

## Slice status

| Slice | Scope | Status |
|---|---|---|
| A1 | Retry, blocker identity, allowance, checkpoint/resume | Complete (A1 `82eb96c`, A1b). 21 of 21 registry rows examined: 19 reconciled, 2 `NEEDS_CHARACTERIZATION` with the exact missing evidence and the owning slice (A2, A3), 0 pending. Gaps `GAP-A1-01` to `GAP-A1-09`. Four statements of the A1 checkpoint were corrected (gap ledger, "Corrections") |
| A2 | Semantic contract, verifier identity/specification/authority, attribution | Complete (2026-11-15). 13 of 13 registry rows examined (12 reconciled, 1 stale bookkeeping) and the row A1 handed over resolved as superseded. Mandatory row `GENERATED-VERIFIER-CONTRACT-AUTHORITY-001`: decision C, the row conflates verifier authority (closed by WS3, residual `GAP-A2-02`) with semantic-contract authority (absent, `GAP-A2-01`). Gaps `GAP-A2-01` to `GAP-A2-03`. Handed on: Planner criteria rendering and direct/enforce divergence (A4); the policy a real launch runs under (A5); `BYP-A2-07` guard condition and requirement closure (A3). Documents reconciled: `DOC-A2-01..05` in the gap ledger |
| A3 | Candidate, review/approval, apply, replay, terminal gate, final result | Complete (2026-11-15). 28 of 28 registry rows examined (21 reconciled or stale, 7 handed to A4/A5/A6 as `NEEDS_CHARACTERIZATION` with the missing evidence) and the row A1 handed over characterized. Four mandatory rows decided: success is reported with open requirements under the default policies (`GAP-A3-01`, measured); no repair follows an enforce terminal gate failure (`GAP-A3-02`, traced); a refused final review fails an applied unit on the direct and milestone path and is advisory under enforce, without discarding other units' work (`GAP-A3-03`, measured); the approval record carries no candidate digest and no record links an approval to the committed bytes (`GAP-A3-04`, measured). New: under enforce the approval subject is the unit, never the plan candidate (`GAP-A3-05`). Gaps `GAP-A3-01` to `GAP-A3-07`; documents `DOC-A3-01..06`. Not traced: the Developer request itself (STAGE-GENERATION), tool and MCP writers (A5) |
| A4 | Goal intake, knowledge/Skills, planning, plan validation, direct vs enforce | Complete (2026-11-15). 18 of 18 registry rows examined (17 reconciled, 1 handed to A6 as `NEEDS_CHARACTERIZATION`) and the row A3 handed over settled as stale. Mandatory row `ENFORCE-EXECUTE-PLAN-CONVERGENCE-001`: confirmed, and larger than "orchestration only": the two executors decide seven result-bearing facts differently, among them plan validity (measured), where requirements are verified, and two requirement closers that cannot fire under enforce (`GAP-A4-01`). Project Setup turns the enforce shape on, a folder with no configuration runs direct (measured). Skills: fact authority, relevance, rendering and user experience are separate rows (`AUTH-A4-07..09`); the Planner and a unit can select different skills (`GAP-A4-02`, measured). The knowledge gate takes a folder name as skill coverage and Kriya's own scaffold satisfies it (`GAP-A4-03`, measured). Live-lookup text reaches a model request outside the untrusted fence (`GAP-A4-04`, measured). Goal ambiguity has no representation (`GAP-A4-07`). No aggregate plan-level approval or size measure exists in planning (`GAP-A3-05` update). Gaps `GAP-A4-01` to `GAP-A4-08`; documents `DOC-A4-01..08`. Not traced: the Developer request itself (STAGE-GENERATION, to A6), `milestones.py`, `plan_revision.py`, `triage.py` |
| A5 | Workspace/root, tools, MCP, mutation authority, configuration authority | Complete (2026-11-15). 26 of 26 registry rows examined and the five rows handed over by A3 and A4 settled (two of them declared not characterizable under the freeze: they need a real loaded container daemon). Mandatory row `DESKTOP-PROJECT-RETRIEVAL-STATE-ISOLATION-001`: characterized, stays open, the isolation invariant does not hold. With the packaged defaults the code index, the learned-knowledge store, the release cache and the skills directory are one installation-wide namespace with no project key; a Project Setup folder uses them too (`GAP-A5-01`, measured). The write gate allows a candidate to carry the project's own `kriya.yaml` and workspace skills (`GAP-A5-02`, measured at the gate). A planned MCP call with a write grant changes the owner workspace outside the candidate (`GAP-A5-03`, traced). The run budget is repository-settable without bound outside the production profile (`GAP-A5-04`, measured; the A5 statement that it has no run-level owner was wrong and is corrected in Phase B: the clock is one per top-level run). Skill learning writes bypass the write gate and the candidate (`GAP-A5-05`). No aggregate plan-level or run-level approval authority exists in tools, MCP, configuration or the application layer (`GAP-A3-05` trace complete). Rows `AUTH-A5-01..12`, `SM-A5-01..04`, `MUT-A5-01..17`, `FR-A5-01..19`, `BYP-A5-01..16`, `DOC-A5-01..08` |
| A6 | Runtime/model identity, qualification, fallback, the Developer request, evidence/audit, extensibility seams | Complete (2026-11-15). 35 of 35 registry rows examined and the rows handed over by A3 and A4 settled; with it the registry stands at 141 of 141 examined, 0 pending. The Developer request is two messages with no structural trust classes; text fetched by the live lookup reaches the Developer task unfenced, is placed in the Reviewer's system prompt on a rejected candidate, steers the targeted-test selection and feeds the lesson extraction (`GAP-A4-04` trace complete, measured; carried as P1 security / prompt-trust-boundary). No later authority validates the skill facts behind a closed knowledge gate (`GAP-A4-03`). A production-profile run checks qualification for a fallback and a retry identity only, never for the primary or the other roles (`GAP-A6-01`, traced). An unknown model name is served with the primary's binding (`GAP-A6-02`, measured). A run's outcome has four records; only the RunRecord is authoritative, the history row is replaceable and unbound and a consistently rewritten evidence chain verifies (`GAP-A6-03`, measured). No transaction exists for MCP workspace mutation (`GAP-A5-03`, comparison table) and no aggregate approval authority exists anywhere (`GAP-A3-05`, search exhaustive). Reconciled against the 2026-11-14 measurements: the 65K pins qualify on the native adapter only, the OpenAI-compatible path cannot qualify (silent truncation), the doctor's two warnings are intentional. Seams at `TRACED` confidence: a second runtime fits the port, its production use is an identity and qualification question; the read side of Skills/Tools fits, every mutating feature lands on a recorded gap. Rows `AUTH-A6-01..13`, `SM-A6-01..04`, `MUT-A6-01..13`, `FR-A6-01..21`, `BYP-A6-01..18`, `GAP-A6-01..03`, `DOC-A6-01..09` |

## Phase status

| Phase | Scope | Status |
|---|---|---|
| A | AS-IS, slices A1 to A6 | Complete (table above) |
| B | Consolidated gap and authority audit | Complete (2026-11-15). The 35 slice gaps collapse under eleven architecture roots and two supporting ones (`R-01` to `R-13`); each slice gap and each of the 141 registry rows is mapped once. Severity corrections after the owner's rulings are in the gap ledger's Phase B section (it wins over the slice text): `GAP-A6-02` is P1 and reachable with a valid configuration (measured); `GAP-A6-01` is P1 with the contract "the run verifies its own bindings"; `GAP-A6-03` is split into result truth and audit integrity; the budget gaps are one root. The A1 and A2 authority rows are completed to the full field set at the end of `01-AUTHORITY-MATRIX.md`. One explicit Phase-D blocker: the verification-environment verdict (`R-13`). **Second checkpoint (integration audit, by the owner's method ruling):** the audit was redone from the content of the maps: 33 boundaries traced, nine cross-map contradictions resolved (`X-01` to `X-09`), thirteen integration findings (`INT-01` to `INT-13`). It found and corrected a wrong statement of this closure's own slices A5 and A6: the generation time budget is one clock per top-level run, not one per unit invocation (measured, B-P2); root `R-08` is narrower and P2 as a result |
| C | Required contracts (TO-BE), stage and integration documents, diagrams | Complete (2026-11-15): `07` (thirteen contracts, nine slices S0 to S8, the derived order, clauses for the integration findings), `08`, `09`, `10`. `evidence/b/crossmap_check.py` over the whole set: 0 references to an undefined row, 0 map rows left uncited. While checking diagram D-2 against the code a second overstatement of this set was found and narrowed (contradiction X-10: a store classification exists in `control/run_record.py`) |
| D | The consolidated owner report | Written: `11-PHASE-D-REPORT.md`. The program stops here for the owner's decisions (its section O) and the ruling on the freeze (section S). Not architecture-coherent yet: nine roots proposed as fix-required before production, one explicit blocker (`R-13`). Two items of the documentation quality gate are met partly and are stated as such (per-row bypass dispositions; implementation readiness of S5, S6, S8) |

## Existing documents reconciled so far

Only the parts that make claims about the A1 scope were reconciled in A1. The rest of each document is reconciled by
the slice that owns its subject.

| ID | Document | A1-relevant claim | Reconciliation (label) |
|---|---|---|---|
| DOC-A1-01 | `handover/claude-context/generation-pipeline.md` (retry sections, incl. NON-CANDIDATE-FAILURE-SCOPED-RESET-001) | A failure that does not replace the open finding opens no new family | Matches the code (`TRACED`; `MEASURED` by the 9 committed tests, see `evidence/a1/`) |
| DOC-A1-02 | `handover/MODULARIZATION/MODULE-CONTRACTS.md` section 12 | A `Finding` type with `id: FindingId`, `state: FindingState` (OPEN, TARGETED, RESOLVED, SUPERSEDED, REFUSED) and `evidence_digest`, in `workflow/finding.py`, "evolves `open_findings.OpenFinding`" | **Not implemented**: `kriya/workflow/finding.py` does not exist at the baseline and `OpenFinding` has none of those fields (`TRACED`; `MEASURED` field list in `evidence/a1/a1_probe_output.txt`). The document describes a design, not the current code. It conflicts with the WS4 owner ruling of 2026-11-14 that rejected a blocker registry and a typed key as blocker authority. See `GAP-A1-01` and contradiction `H-A1-1` in the gap ledger |
| DOC-A1-03 | `handover/MODULARIZATION/OPEN-ITEMS.md` OI-77, OI-78 | Open findings are in memory and not checkpointed; resume survival "decided inside G4 with FindingState" | Matches the code as a description of the gap (`TRACED`). The closing unit it names (G4, prerequisite F2) was not built |
| DOC-A1-04 | `handover/KRIYA_ARCHITECTURE_DECISIONS.md` (identity list containing "retry state") | Retry state is listed among the identities evidence binds to | The code persists no retry state in any checkpoint (`TRACED`, `AUTH-A1-09`). The document states an intent the code does not implement. See `GAP-A1-02` |
| DOC-A1-05 | `handover/PRODUCT-72H/SPRINT_LOG.md` section 46 | "The only reset path is `_record_attempt_failure` -> `observe_failure_family` -> `reset_scoped_budgets_for_new_family`" | Confirmed for `targeted_retry_count` and `fallback_targeted_attempted` (`TRACED`, negative-control search in `AUTH-A1-06`). Best-of-N resets other budget fields but not these two |
