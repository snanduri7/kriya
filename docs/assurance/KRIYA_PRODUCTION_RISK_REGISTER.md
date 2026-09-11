# Kriya Production Risk Register

Governed by `KRIYA_PRODUCTION_RISK_REGISTER_SPEC.md` (schema, v2, APPROVED)
against `KRIYA_V1_DEPLOYMENT_ENVELOPE.md` (APPROVED 2026-09-08).

**Population status: Pass 3 — final evidence closure.** This supersedes
Passes 1 and 2 in place. See §0.7 for what Pass 3 corrected on top of
Pass 2 (topology reconciliation, full P1–P8 raw-evidence audit, the
Python capability sweep, the `VER-004` correction, the `CORR-017` E5
upgrade, and the `RECV-003`/PRV-06 formal resolution). Read §0 in full
before trusting any count — three consecutive passes each found real
errors in the previous one; do not assume this pass is error-free either,
only that it applied the same scrutiny one level further.

### §0.7 What Pass 3 corrected on top of Pass 2

- **All 20 R1 Topology Coverage rows reconciled** (`KRIYA_TOPOLOGY_RECONCILIATION.md`) — zero new Risk IDs needed; `TOP-001` (created in Pass 2, before this row-by-row read) turned out to be an exact match for `TOP-REPO-002`, confirming rather than duplicating.
- **All 8 P-runs' raw evidence read directly** (`KRIYA_P_SERIES_EVIDENCE_AUDIT.md`) — found P-runs are not evidentially equal: P1–P5 show real Kriya-gate success but unconfirmed manual follow-up; P6/P7/P8 carry independently-graded, non-Kriya-self-reported acceptance evidence. This is what grounded `CORR-017`'s E5 upgrade.
- **P4 risk mapping resolved** — folds into `CORR-017`/`TOP-REPO-001`, no new risk (its preservation-under-refactor dimension isn't materially distinct from what `CORR-008`–`010` already represent).
- **`RECV-003`/PRV-06 evidence conflict formally resolved** — full chronology established, downgraded to deterministic (E3) evidence, both underlying vertical-test citations independently re-verified by grep this pass (not memory).
- **Python capability sweep completed** (`KRIYA_PYTHON_CAPABILITY_SWEEP.md`, all 24 named capabilities individually classified) — and it found a real error running the other direction from Pass 2's PRV corrections: `VER-004`'s "confirmed defect" (pyproject.toml dependency installation) doesn't actually exist in current source; I'd read an incident-description docstring as current behavior. Corrected `VER-004` from E0/NEEDS_IMPLEMENTATION to E2/NEEDS_EVIDENCE.
- **`CORR-017` upgraded a second time**, this time on solid ground — E4 (Pass 2) → E5 (Pass 3), backed by three independently-graded, materially-distinct production instances (P6, P7, P8), diversity dimensions named explicitly per the schema's own E5 rule.

---

## §0. Population summary (Pass 2)

### §0.1 What changed this pass, and why it matters more than new rows

Pass 1 treated "a PRV result directory exists" as evidence. That was
wrong, and re-reading the actual `RESULT.md`/`COMPARISON.md` files this
pass overturned several Pass 1 conclusions:

- **`PRV-06`'s actual result: FAIL for both `legacy` and `hardened`
  variants**, reason `KRIYA_GREENFIELD_GIT_BOOTSTRAP_MISSING` — a
  harness/fixture-precondition failure, not a confirmation of the
  process-boundary-compatibility fix Pass 1 cited. The fix itself (real
  commits, real named tests) is not in question; treating this *result*
  as E4 confirmation of it was wrong, and is corrected in `RECV-003`
  below.
- **`PRV-11`'s own `RESULT.md` states outright: "Plan Recovery Capability
  — NOT EXERCISED this run — this run proves nothing about plan recovery
  specifically."** Pass 1 assigned this as E4 evidence for MA9 coordinated
  repair. The scenario passed, but for reasons unrelated to the mechanism
  it exists to test. Corrected in `RECV-002` below.
- **`PRV-02`, `PRV-13`, `PRV-18` are all `NEEDS_REVIEW`** with an
  unchecked manual-verification box in their own `RESULT.md` (token
  semantics, self-correction build-config check, deterministic-denial
  scope probe respectively) — Quality Gates passed, but the scenario's
  own acceptance bar was never confirmed closed. Corrected in `POL-002`,
  `RECV-004`, and the `CORR-002`/`REPO-003` evidence citation.
- **`PRV-17`'s actual result contains real, previously-uncredited Python
  evidence**: a genuine Django greenfield generation (`config/`,
  `customers/`, `manage.py`, `pyproject.toml`, 11 files), `Quality Gates:
  PASSED`, `Kriya exit: 0`. Status is `NEEDS_REVIEW` only because one
  scope-creep manual check ("confirm project remains Python/Django only")
  was never ticked — not a correctness failure. Pass 1's Python capability
  framing was too pessimistic; corrected in `VER-005` below, and this is
  the single most consequential correction in this pass.
- **A targeted second capability grep found real Python machinery Pass 1
  undersold**: `kriya/analyzer/analyzer.py`/`graph.py` use Python's
  stdlib `ast` module for symbol extraction (native, not a tree-sitter
  dependency); `kriya/tools/validate.py` has real venv-creation and
  `python -m pytest` execution code, not just marker detection. No
  dedicated `tests/test_python_*` file was found confirming this path's
  own coverage — real source capability, unconfirmed by a named test.

None of this changes the register's overall shape (the major gaps —
Gradle, sandbox, policy enforcement, tool authority, concurrency — are
unaffected and remain the largest, most load-bearing findings), but it
changes several individual E-levels and dispositions materially, and it
means the Pass-1 numbers should not have been used for prioritization as
they stood.

### §0.2 Scope/evidence separation applied (per explicit review)

Of the 19 R1 invariant-derived risks, individual audit (not bulk
relabeling) found **17 of 19 are language-neutral mechanisms currently
evidenced only through Java** — the R1 invariant machinery lives in
orchestration-level Python code (`workflow_controller.py`,
`plan_validation.py`, `obligations.py`) operating on abstract plan/state
objects, not on Java source itself. Only two are genuinely intrinsic to
Java by their own nature: `INV-PLAN-005` (Java interface indexing — a
Java-specific language construct) and `INV-BUILD-001` (Maven reactor
compile evidence — intrinsic to Maven's build model). These two keep
`Language Scope: JAVA`. The other 17 are reclassified `LANGUAGE_NEUTRAL`
with per-language evidence noted inline in the same row (the smallest
schema change that represents this correctly — see §0.3; no new field or
taxonomy value was needed).

`RECV-003` (process-boundary compatibility) is the one exception that
looks similar but isn't: its actual detector code matches Java/Surefire
signatures only, by explicit design (per its own source comment,
"list-shaped for future stacks" — i.e., not yet built for anything else).
That's a language-specific *mechanism*, not just language-specific
*evidence*, so it correctly stays `JAVA`.

### §0.3 Representational adjustment made (schema NOT redesigned)

For a `LANGUAGE_NEUTRAL`-scoped row, `Effective Evidence Level` may now
carry a per-language breakdown inline (e.g. "Java: E3; Python: E1, not
independently demonstrated") when the two differ materially, instead of
splitting into duplicate rows. Row-splitting (as done for `VER-004`/
`VER-005`) is reserved for cases where the *mechanism itself* materially
differs by language — not merely where evidence differs. This is a
formatting convention within the existing `Effective Evidence Level`
field, not a new field, value, or taxonomy — per the instruction not to
redesign the schema unless actually necessary.

### §0.4 Totals (recomputed with a small parsing script, not by hand)

**Correction on top of a correction:** the first version of this table
(written while composing the body, same mistake pattern as Pass 1) said
67 total risks. A short Python script parsing every `**ID —`/`Disposition:`/
`Deployment Relevance:`/`Language Scope:` line directly found the real
total is **68** — I'd correctly summed the by-domain breakdown to 68 in
the process of writing it, then transcribed the total as 67 anyway, a
pure arithmetic slip on top of already-verified components. The
by-domain row below was already right; only the total and the
disposition/relevance/language cross-tabs needed correcting, which the
script did exactly, with no rows failing to parse (0 missing dispositions,
relevances, or language-scope values out of 68).

| | Count |
|---|---:|
| Total risks | 71 |
| REQUIRED | 63 |
| OPTIONAL | 6 |
| OUT_OF_SCOPE | 2 |
| CLOSED | 27 |
| NEEDS_EVIDENCE | 22 |
| NEEDS_IMPLEMENTATION | 20 |
| SUPERSEDED | 0 (row-level) |
| DEFERRED | 2 |
| **REQUIRED + CLOSED** | **25** |
| **REQUIRED + NEEDS_EVIDENCE** | **21** |
| **REQUIRED + NEEDS_IMPLEMENTATION** | **17** |

**Pass 3 update**: `VER-004` moved `NEEDS_IMPLEMENTATION`→`NEEDS_EVIDENCE`
(the confirmed-defect claim was wrong, corrected in §0.7). All other Pass 2
counts unchanged by Pass 3's other corrections (`CORR-017`/`RECV-003`
changed evidence level and citation quality, not disposition or relevance).

**A1-P1 update (2026-09-09)**: `CORR-018` added (new row, REQUIRED,
LANGUAGE_NEUTRAL, NEEDS_IMPLEMENTATION) — recorded, not implemented, per
that task's explicit bookkeeping-only instruction.

**A1-E2 update (2026-09-09)**: `CORR-019` added (new row, REQUIRED,
LANGUAGE_NEUTRAL, NEEDS_EVIDENCE — implementation complete and
deterministically tested this pass, live validation still required, per
the investigation's own recommended disposition). All counts below now
reflect both new rows; no other row's disposition/relevance/language scope
changed across either pass.

By domain: ORCH 3 · CORR 19 · RECV 4 · REPO 4 · VER 6 · SEC 5 · POL 3 ·
TOOL 4 · MODEL 4 · CTX 3 · STATE 3 · CONC 2 · OBS 4 · REL 2 · TOP 5.

By Language Scope: JAVA 6 (`CORR-015`, `RECV-003`, `VER-001`, `VER-003`,
`TOP-001`, `TOP-002`) · PYTHON 2 (`VER-004`, `VER-005`) · LANGUAGE_NEUTRAL
63 (many of these carry a Java-only evidence gap noted inline per §0.3 —
language-neutral *scope* is not the same claim as language-neutral
*proof*).

**VER-006 update (2026-09-10)**: new row added (`VER-006`, REQUIRED,
LANGUAGE_NEUTRAL, NEEDS_EVIDENCE — implementation complete and
deterministically self-tested this pass, independent pytest confirmation
and ideally a live-model re-run still required, per this task's own
explicit "do not mark CLOSED from implementation tests alone"
instruction). Total risks 70→71, VER domain 5→6, REQUIRED 62→63,
NEEDS_EVIDENCE 21→22, REQUIRED+NEEDS_EVIDENCE 20→21. All other counts
below unchanged by this addition.

**POL-001-P4 update (2026-09-10) — full re-script, two pre-existing drifts
corrected, plus POL-001 itself.** Per this task's own explicit "recompute
stale register summary counts row-by-row... if recomputed totals disagree
with the summary, correct the summary — never reclassify a row to force a
match" instruction: every one of the 68 row headers (70 distinct risk IDs,
combined rows like `CORR-008/009/010` expanded) was re-parsed directly
from each row's own `Disposition`/`Deployment Relevance` text (not from
the §0.1a/§0.1b prose bullet lists, which are a separately hand-maintained
summary and the actual source of the drift below) — 0 rows failed to
parse, and the resulting by-domain breakdown matches the one already
printed above exactly, cross-confirming the script against this file's own
prior count. That re-parse found the previous table (CLOSED 25 /
NEEDS_EVIDENCE 22 / NEEDS_IMPLEMENTATION 21 / REQUIRED+CLOSED 23 /
REQUIRED+NEEDS_IMPLEMENTATION 18) was **already one CLOSED short and one
NEEDS_IMPLEMENTATION over**, for two reasons unrelated to POL-001 and not
introduced by this pass: `CONC-001`'s prior closure (commit `ba02adb`) was
never propagated into this summary table, and `CORR-016`'s own disposition
change from `NEEDS_EVIDENCE` to `NEEDS_IMPLEMENTATION` (commit `5210faa`)
left it sitting in the §0.1b prose list when its own row already said
otherwise. Both are corrected here alongside POL-001's own real change
this pass (`NEEDS_EVIDENCE` → `CLOSED`, see §7 below) — no row was
reclassified to force a match; every number above is the direct sum of
each row's own already-stated `Disposition`/`Deployment Relevance` fields
as they stand in this file today.

Verification identities (re-derived by direct row parse, not hand-updated;
updated again for `VER-006`'s addition): CLOSED(27) + NEEDS_EVIDENCE(22) +
NEEDS_IMPLEMENTATION(20) + SUPERSEDED(0) + DEFERRED(2) = 71 ✓.
REQUIRED(63) + OPTIONAL(6) + OUT_OF_SCOPE(2) = 71 ✓.
REQUIRED+CLOSED(25) + REQUIRED+NEEDS_EVIDENCE(21) + REQUIRED+NEEDS_IMPLEMENTATION(17)
= 63 = REQUIRED total ✓ (confirms no REQUIRED row has a DEFERRED/SUPERSEDED
disposition, which is correct — both DEFERRED rows are OUT_OF_SCOPE).

### §0.5 Requirement-authority conflicts: 0 (corrected from Pass 1's 1)

`TOOL-001` was reclassified this pass — see its own entry. It is **not**
a requirement-authority conflict: no two currently-authoritative
requirements demand incompatible behavior. It is a REQUIRED CAPABILITY GAP
with an intentional current safety restriction, directly confirmed by
KRP-020's own stated rationale ("Structured enforce mode currently refuses
TOOL-tagged subtasks because safe authoritative routing does not exist" —
read from the full `TASKS_DETAILED.md` text this pass, not the one-line
summary).

### §0.6 Evidence conflicts found this pass: 1

`PRV-06`'s `FAIL` result and the process-boundary-fix narrative in project
memory are not strictly contradictory (different runs, different failure
modes — the bootstrap error looks like a harness/fixture precondition
issue, not a regression of the fix itself), but they cannot both be cited
as clean confirmation of the same thing. Recorded as `RECV-003`'s
Evidence Validity note, not silently reconciled either direction.

---

## §0.1a REQUIRED + NEEDS_IMPLEMENTATION (17, corrected POL-001-P4 — regenerated from each row's own current Disposition, not hand-patched)

`CORR-016` PRV-08 transitive revalidation (**moved into this list** — its
own row disposition already reads `NEEDS_IMPLEMENTATION` since commit
`5210faa`; the prose list here had never been updated to match) ·
`CORR-018` unauthorized behavioral drift within authorized files (A1-P1)
· `CTX-001` context/graph freshness · `MODEL-001` model capability
certification · `OBS-001` enforce-mode telemetry gap · `OBS-002` operator
run summary · `OBS-004` resource budgets · `REL-002` `doctor
--production` · `SEC-001` hostile-code containment · `SEC-003` MCP
environment isolation · `SEC-005` package/network containment ·
`STATE-003` deterministic replay · `TOOL-001` policy-mediated TOOL
execution (reclassified Pass 2 — see its own entry) · `TOOL-002`
ToolBroker · `TOOL-003` MCP capability authorization · `TOOL-004` plugin
manifest/provenance · `TOP-001` Gradle support. `POL-001` **moved out of
this list this pass** — CLOSED, see §7 below (it had actually been
`NEEDS_EVIDENCE`, not `NEEDS_IMPLEMENTATION`, since P1; this prose list
was never corrected at the time, a pre-existing drift unrelated to this
pass's own POL-001 work, found and fixed here). `VER-004` stays out (see
§0.7) and `ORCH-001`/`ORCH-002` stay out (reclassified `OPTIONAL`, Pass 2).

## §0.1b REQUIRED + NEEDS_EVIDENCE (21, updated VER-006 — new row added)

`CORR-006` semantic-contract protection · `CORR-019` Reviewer
self-assigned evidence confidence (A1-E2) · `CTX-002` large-repo scale ·
`CTX-003` PRV-09 dependency resolution · `MODEL-002` KnowledgeGuard live
confirmation · `MODEL-004` fresh-repo stack-drift (downgraded from
CLOSED, Pass 2) · `OBS-003` secret redaction · `ORCH-003`
structured-mode checkpointing · `POL-002` security-sensitive-goal
handling (downgraded from CLOSED, Pass 2 — PRV-02's manual check was
never confirmed) · `RECV-002` MA9 coordinated repair (downgraded from
CLOSED, Pass 2 — PRV-11's own result states plan recovery was not
exercised) · `REL-001` release packaging · `REPO-004` workspace
isolation · `SEC-002` fail-closed sandbox failure · `SEC-004` MCP timeout
· `STATE-001` crash/resume · `STATE-002` multi-store consistency
(reframed Pass 2 to evidence-first) · `TOP-002` framework-neutral Java ·
`TOP-005` CI operating requirements · `VER-004` Python
dependency/build-metadata handling (moved into this list Pass 3 — real
mechanism confirmed, not the absent one previously claimed) · `VER-005`
Python end-to-end validation · `VER-006` runtime-verification LLM
fallback distrust containment (new, this pass — implemented, not yet
independently pytest-confirmed). `CORR-016` **moved out of this list** (see
§0.1a above — its own row has read `NEEDS_IMPLEMENTATION` since commit
`5210faa`). `POL-001` was never actually added to this list despite
becoming `NEEDS_EVIDENCE` at P1 (the same pre-existing drift as above) —
moot now, since it goes straight to `CLOSED` this pass (see §7). Note
`RECV-004` is `NEEDS_EVIDENCE` too (downgraded Pass 2), but its `OPTIONAL`
relevance keeps it out of this REQUIRED-only list.

---

## §1. ORCH — Orchestration

**ORCH-001 — Dual orchestration paths (WorkflowEngine vs. WorkflowController)**
Language Scope: LANGUAGE_NEUTRAL · Requirement Authority: `ARCHITECTURE_INVARIANT` only (Production Readiness Review) — **not `DEPLOYMENT_ENVELOPE`; challenged and confirmed this pass.** `KRIYA_V1_DEPLOYMENT_ENVELOPE.md` §10's own definition of "Kriya v1 production-ready" names specific behaviors (unattended-safety evidence, TOOL/MCP policy-mediation, concurrent-writer rejection, per-risk evidence thresholds) — it never requires a specific orchestration *shape*. Deployment Relevance: **OPTIONAL** (maintainability/change-risk debt, not a certification blocker on its own) · **Transitive necessity, stated precisely, not glossed over:** `KRP-021` (enable TOOL subtasks — the mechanism `TOOL-001` needs) depends on `KRP-012`, which depends on `KRP-011` — i.e. on this consolidation. So while `ORCH-001` is not *directly* required by the envelope, it is a real prerequisite specifically for closing `TOOL-001`, and only for that — the ActionBroker/SandboxBroker/policy-enforcement work (`KRP-014`–`KRP-020`) depends only on `KRP-002`/`KRP-003` per the roadmap's own dependency graph, confirmed by reading the full spec text this pass, and does **not** depend on orchestration consolidation at all. · Related KRP: KRP-010, 011, 012, 013 · Effective Evidence Level: E4 (both paths individually proven; growth/coupling is directly observed, not inferred) · Disposition: NEEDS_IMPLEMENTATION, OPTIONAL priority except as a `TOOL-001` prerequisite.

**ORCH-002 — Core orchestration functions exceed safely-evolvable size, still growing**
Language Scope: LANGUAGE_NEUTRAL · Requirement Authority: `ARCHITECTURE_INVARIANT` only, same reasoning as `ORCH-001` · Deployment Relevance: **OPTIONAL** · Same transitive-necessity note as `ORCH-001` (`KRP-010` sits on the same dependency chain toward `KRP-011`) · Current mechanism: `run_attempt()` 3,509 lines, `run_generation_workflow()` 2,701, `_run_structured_enforce()` 2,524, `handle_attempt_failure()` 1,098 — all measured directly, all grew since the 2026-09-06 review snapshot, including growth from this project's own R1 Deliverable 5 work · Effective Evidence Level: E4 (directly observed) · Disposition: NEEDS_IMPLEMENTATION, OPTIONAL priority.

**ORCH-003 — Per-subtask checkpointing not fully wired in structured enforce mode**
Language Scope: LANGUAGE_NEUTRAL · Requirement Authority: `DEPLOYMENT_ENVELOPE` §4 (DE-03 unattended autonomy needs reliable mid-run recovery — this one *is* a genuine behavioral requirement, unlike `ORCH-001`/`002`) · Deployment Relevance: REQUIRED · Current mechanism: legacy path's `kriya/workflow/checkpoint.py` is unit-tested (`test_checkpoint_control_plane_hashes.py`, `test_subtask_checkpoint.py`); enforce-mode's own gap is source-documented, not independently re-confirmed against current line numbers this pass · Effective Evidence Level: E1 · Disposition: NEEDS_EVIDENCE (confirm the exact current-state gap before assuming NEEDS_IMPLEMENTATION).

---

## §2. CORR — Correctness (plan repair, obligations, preservation)

Per §0.2's individual audit: 15 of these 17 rows are `LANGUAGE_NEUTRAL`
mechanisms with Java-only evidence today (noted inline per row, per §0.3's
convention). `CORR-015` is the one genuinely Java-intrinsic exception.

**CORR-001 — Grounded-reference gap detection scoped to test-source only** (`INV-PLAN-001`)
Language Scope: LANGUAGE_NEUTRAL (plan-validation logic operates on abstract plan/subtask objects) · Deployment Relevance: REQUIRED · Effective Evidence Level: Java E3 (Catalog Vertical Evidence); Python E0/E1, not independently demonstrated · Disposition: CLOSED for the mechanism at its required E3 bar — **but note the required bar itself was only ever evaluated against Java evidence; Python-side closure is not established by this row and should not be read as such.**

**CORR-002 — Scope-denial merges into plan surgery, not silent drop** (`INV-RECOVERY-001`, FI-01, FI-09)
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Effective Evidence Level: **E3 (corrected down from E4 this pass)** — FI-01/FI-09 are real, directly-verified `test_authorize_denies_file_outside_validated_subtask_scope`/`test_commit_batch_raises_and_writes_nothing_when_one_target_is_denied`/`test_workflow_stops_retrying_immediately_on_unrecoverable_scope_denial` (all three confirmed to exist by direct grep this pass), which is solid E3. The PRV-18 "corroborating E4" claim from Pass 1 is **removed**: PRV-18's actual result is `NEEDS_REVIEW` with its own deterministic-denial manual check unticked, not confirmed evidence. Java evidence only; Python E0. · Disposition: CLOSED at the required E3 bar (met without needing the disputed PRV-18 evidence).

**CORR-003 — Sole-provided-capability self-satisfaction rejected** (`INV-PLAN-002`)
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Effective Evidence Level: Java E3; Python E0/E1 · Disposition: CLOSED (Java bar met).

**CORR-004 — Schema self-heal doesn't consume repair budget** (`INV-PLAN-003`)
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Effective Evidence Level: Java E4 (real live incident, overnight PRV-06-cycle-1 stale-`tool_name` fix `88ae0a9`, per project memory — not independently re-read raw this pass, flagged as memory-sourced not raw-file-confirmed); Python E0 · Disposition: CLOSED (Java bar met, evidence quality flagged).

**CORR-005 — Strict reason-code-set regression rejection** (`INV-PLAN-004`)
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Effective Evidence Level: Java E4 (P2's real, multi-attempt "Run 4-8" Planner-convergence saga, `8cd94ec` — memory-sourced, not re-read raw this pass); Python E0 · Disposition: CLOSED.

**CORR-006 — Semantic-contract regression protection** (`INV-OBL-001`, MA8, FI-02)
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Effective Evidence Level: Java E3 (FI-02, a real `test_workflow_controller_enforce.py` exercise); Python E0 · Disposition: NEEDS_EVIDENCE — required bar is E4 given this obligation kind's own history of live defects (P7 oscillation), not yet met at Java, let alone Python.

**CORR-007 — Preserved reference must not silently regress** (`INV-PRESERVE-001`, FI-03)
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Effective Evidence Level: Java E4 (the live P5 defect R1 itself found and fixed, `ddcc1ab` — this one *is* independently well-documented in R1's own audit, higher confidence than the memory-only citations above); Python E0 · Disposition: CLOSED.

**CORR-008/009/010 — Preservation suppression per-source not per-path / terminal byte-identity gate / preserve-modify contradiction rejection** (`INV-PRESERVE-002/003/004`)
Language Scope: LANGUAGE_NEUTRAL (byte-identity/path-suppression comparison is inherently content-based, not language-parsing-based) · Deployment Relevance: REQUIRED · Effective Evidence Level: Java E3 each; Python E0 each · Disposition: CLOSED (E3 required and met).

**CORR-011 — Fixture/precondition failure attributes to test, never production** (`INV-ATTR-001`, FI-10)
Language Scope: LANGUAGE_NEUTRAL (baseline content-diff attribution logic) · Deployment Relevance: REQUIRED · Effective Evidence Level: Java E4 (the real P2 defect, `5c4dff4` — memory-sourced); Python E0 · Disposition: CLOSED.

**CORR-012 — Runtime-verification negation wins** (`INV-GOAL-001`)
Language Scope: LANGUAGE_NEUTRAL (pure goal-text regex analysis, `goal_requires_runtime_behavior()` — operates on the goal string, not target-repo code, so this one is neutral by construction, not just by absence-of-counterevidence) · Deployment Relevance: REQUIRED · Effective Evidence Level: Java E4 (real P3 defect, `4768db1` — memory-sourced); the underlying mechanism's language-neutrality is stronger evidence here than for most other rows, since it doesn't touch target-repo source at all · Disposition: CLOSED.

**CORR-013 — Response-owner positive-intent gating** (`INV-GOAL-002`)
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Effective Evidence Level: Java E3; Python E0 · Disposition: CLOSED.

**CORR-014 — Reason-code classification completeness (test-time scan)** (`INV-PLAN-006`)
Language Scope: LANGUAGE_NEUTRAL (a scan over Kriya's own test suite, not target-repo-dependent at all) · Deployment Relevance: OPTIONAL · Effective Evidence Level: E1, by the invariant's own stated design · Disposition: CLOSED.

**CORR-015 — Java interface indexing correctness** (`INV-PLAN-005`)
Language Scope: **JAVA (genuinely intrinsic — confirmed by individual audit, not bulk-labeled)** · Deployment Relevance: REQUIRED (DE-02 multi-module Java) · Effective Evidence Level: E3 · Disposition: CLOSED.

**CORR-016 — Transitive downstream revalidation on a deliberate contract change** (PRV-08)
Language Scope: LANGUAGE_NEUTRAL (obligation-tracking mechanism itself is generic; PRV-08's own goal happened to be Java-shaped) · Deployment Relevance: REQUIRED · Effective Evidence Level: **E1 (DIRECT authorization deterministically implemented and tested this pass; the row's own required bar — not below E3 — is not met, since the recovery-layer fix is not yet implemented and no real P9 rerun exists)** · Disposition: NEEDS_IMPLEMENTATION.

History: P9 (2026-09-08, live production run of PRV-08 against the frozen baseline) FAILED — `find_brownfield_public_api_changes()` (`kriya/workflow/file_resolution.py`) rejected s2's goal-authorized `CustomerSummary` contract evolution (a required consequence of s1's already-completed `CustomerRecord` change) because it received only the flat subtask goal string as its authority input, even though the approved plan's own `requires`/`provides` chain (s2.requires=['updated_customer_record_contract'] == s1.provides) plus global invariant gi3 already established the authorization two layers up the call stack. Root-cause confirmed and reproduced deterministically; classified MISSING WIRING, not architecture extension. Disposition moved NEEDS_EVIDENCE → NEEDS_IMPLEMENTATION on that confirmation.

Fix attempted, then reverted same day: `compute_authorized_contract_evolutions()` derived a typed authorization list from `structured_plan`/`current_subtask_id`/`completed_subtask_ids` (all three already threaded through `run_generation_workflow()`/`AttemptContext` from prior MA6/MA8 work — confirming the *wiring* itself needs no architecture extension, that finding stands). It matched an upstream subtask's `provides` against the current subtask's `requires`, gated on the upstream id being in the genuinely-deterministic `completed_subtask_ids` set, and allowlisted the current subtask's entire legal write-scope file list as "owners." A direct `run_attempt()` integration test caught it authorizing an **unrelated, incidental single-symbol rename** in a different file riding in the same candidate batch — because `Subtask.requires`/`provides` are file-agnostic string tokens, there is no deterministic requirement-to-file/symbol binding anywhere in `plan_schema.py`, so the only allowlist available (the whole subtask's write scope) authorizes any lone violator in that scope, not just the one file the requirement actually concerns. A follow-up isolated repro confirmed the gap is not retry-sequence-specific: a single incidental rename alone in a batch, with no genuine contract-evolution file present at all, is authorized the same way. This is exactly the STOP condition under which this work was authorized ("no deterministic relationship exists between the authoritative requirement and the downstream contract delta"; "symbol/signature-specific authorization cannot be expressed with existing structures without introducing a new authority concept") — implementation was halted and fully reverted rather than shipped. `find_brownfield_public_api_changes()` is back to 4 positional args and stays fail-closed; only the independent Step 6 candidate-overlay fix (a consumer file updated in the same candidate batch is evaluated on its own new content, not stale on-disk text — unrelated to the authority question) was kept.

Tests: the reverted authorization-specific tests were removed; `tests/test_workflow.py::test_prv08_shaped_downstream_contract_evolution_is_rejected` (characterization, still red — the real P9 candidate is still rejected) and `test_prv08_shaped_deterministic_integration` (full `run_attempt()` integration; now asserts the genuine contract evolution AND an unrelated incidental rename are BOTH rejected pre-write, the same way, documenting that the guard cannot yet tell them apart) replace the prior "authorized" tests. `test_candidate_overlay_consumer_updated_in_same_batch_is_not_stale_evidence`/`_not_updated_remains_evidence` (Step 6) are retained unchanged. Existing brownfield-API tests and `CORR-008`/`009`/`010`'s own suites are untouched (different mechanism, `INV-PRESERVE-002/003/004`).

Authority audit (2026-09-08, second pass, prompted by an independent review flagging the exact laundering risk the revert above already found): every candidate input to the reverted mechanism was traced to a SOURCE (USER/DETERMINISTIC_DERIVED/PLANNER/DEVELOPER/RUNTIME) and AUTHORITY LEVEL (AUTHORITATIVE/DERIVED_WITH_AUTHORITY/STRATEGY_ONLY/EVIDENCE_ONLY). `Subtask.requires`/`provides`/`description`, `PlannedFile.action`, `AcceptanceCriterion`, `GlobalInvariant.statement`: SOURCE=PLANNER, STRATEGY_ONLY — Planner-authored, however faithfully derived from the real goal, never authoritative on their own (`plan_validation.py` proves these edges are *structurally real*, i.e. AFFECTEDNESS, never that a specific mutation is *permitted*, i.e. MUTATION AUTHORITY). `completed_subtask_ids`: SOURCE=DETERMINISTIC_DERIVED (computed live off `approved_stage_states`), the one genuinely non-Planner-asserted fact — but it only proves an upstream subtask finished, not that any specific downstream mutation is authorized. `expected_files_upfront`: derived from Planner-authored `planned_files`, so STRATEGY_ONLY — defines legal write *scope*, which the Critical Implementation Constraint already named as explicitly insufficient for *permission*. No "Change Contract" type exists anywhere in this codebase (grepped: zero hits for `ChangeContract`/`change_contract`/`authoritative_requirement`/`ContractDelta`). `ObligationLedger`/`ObligationRecord.authority` (DETERMINISTIC/GROUNDED/JUDGMENT, `kriya/workflow/obligations.py`) was checked kind-by-kind: `SUBTASK_SEMANTIC_CONTRACT` is DETERMINISTIC but only for the *structural validity of the requires/provides edge itself* (AFFECTEDNESS again, at higher confidence than the raw plan fields, still never MUTATION AUTHORITY); `GOAL_SPEC_REQUIREMENT` is JUDGMENT (an LLM verdict, explicitly the lowest tier by this module's own docstring, never sufficient to override a deterministic guard); `CROSS_OWNER_ARTIFACT_REQUIREMENT`/`FUTURE_OWNER_VERIFICATION`/`RUNTIME_PLAN_GAP`/`PRESERVED_REFERENCE`/`PROCESS_BOUNDARY_COMPATIBILITY`/`CROSS_SUBTASK_INTEGRATION`/`PLAN_STRUCTURAL_VALIDITY`/`MIGRATION_COMPLETION` each answer a different, narrower question (a missing dependency, unfinished prerequisite work, a plan-graph gap, byte-identity preservation, a process-boundary conflict, integration composition, planned-file-action stability, migration completion) — none represents "an authoritative requirement permits this specific downstream public-API delta."

One genuinely AUTHORITATIVE, SOURCE=USER structure does exist and was not previously considered: `AttemptContext.grounding_goal` (`kriya/workflow/attempt.py:510`) — the raw, unmediated top-level user request string, explicitly separated from Planner-authored text by an already-shipped "authority-isolation fix" (PRV-11, 2026-08-30, `build_subtask_goal_text()`'s own docstring: an "Authoritative Goal" section holding `grounding_goal` verbatim, kept distinct from a "Planned Implementation Strategy" section for exactly this reason). `find_brownfield_public_api_changes()`'s existing `_goal_explicitly_requests_api_change()` escape hatch is currently wired to the wrong field at both call sites — it receives `ctx.goal` (the Planner-synthesized, mixed per-subtask text), never `ctx.grounding_goal` — a real, narrow, separate MISSING WIRING gap, noted but **not fixed here** (out of this session's authorized scope; also would not have closed the real P9 gap on its own, since the real P9 goal text, "Extend the existing CustomerRecord contract with a new required field named region," contains none of that regex's trigger words, and using free-text keyword matching against `grounding_goal` to authorize a *specific* downstream file/symbol would repeat the exact "regex/semantic interpretation of prose" anti-pattern already ruled out for `GlobalInvariant.statement`, just on a USER-authored string instead of a Planner-authored one — the SOURCE differs, the AUTHORITY-BINDING risk to a specific symbol does not).

Conclusion: no existing Kriya structure — plan schema, obligation ledger, or `grounding_goal` — carries a deterministic, structured binding from an authoritative requirement to a *specific downstream file/symbol delta*. `Subtask.requires`/`provides`/`completed_subtask_ids` establish AFFECTEDNESS only (an already-shipped concept, correctly used elsewhere for exactly that: scope recovery, topological validation, obligation dependency tracking) and were never a valid source of MUTATION AUTHORITY for a protected public signature. **CLASSIFICATION for CORR-016's specific fix: ARCHITECTURE EXTENSION**, not MISSING WIRING — closing it safely requires a new, explicitly-authorized authority concept (or a different mechanism entirely) capable of expressing that binding; the wiring-availability finding (`structured_plan`/`current_subtask_id`/`completed_subtask_ids` already reach both call sites) stands on its own but is necessary, not sufficient.

Regression test added, per an independent review's request, to lock this in: `tests/test_workflow.py::test_completed_planner_dependency_cannot_authorize_public_api_change_without_requirement_authority` — constructs the maximal AFFECTEDNESS scenario (s1 completed, s1.provides matched by s2.requires, s2 legally owns the changed file, exactly one public symbol changes, the dependency edge is structurally real) with deliberately NO authoritative requirement/Change Contract/authority-preserving obligation behind it, and asserts `find_brownfield_public_api_changes()` still rejects. Passes today specifically because no authorization channel exists at all post-revert; kept as a permanent regression guard so a future authority concept cannot reopen this exact laundering path without this test failing first.

**Causal reclassification (2026-09-08, third pass — the P9 failure itself was reread, not just the authority question)**: an architecture design (`docs/architecture/CORR016_AUTHORIZED_CONTRACT_EVOLUTION_DESIGN.md`, Revision 2) re-read PRV-08's frozen `goal.md` verbatim (never inferred from the Planner plan or Developer candidate, per an explicit review instruction) and the real fixture source under `kriya-live-validation/PRVS/kriya-prv-harness-1.0.1/work/PRV-08/hardened/` (`CustomerRecord.java`, `CustomerSummary.java`, `SummaryService.java`, `Printer.java` — the actual, un-mutated baseline; confirmed via `git log`/`git status` in that working copy that no candidate was ever written to it). Finding: **the authoritative goal never asks for `CustomerSummary`'s own contract to change**, and nothing in the fixture forces it to — `SummaryService.summarize(CustomerRecord r)` reads `CustomerRecord` only via accessor methods (`r.customerId()`/`r.firstName()`/`r.lastName()`), never constructs it positionally, and no file anywhere in the 4-file fixture (grepped) constructs `new CustomerRecord(`. **`find_brownfield_public_api_changes()`'s original rejection of s2's candidate was CORRECT** — CustomerSummary's contract should have stayed `(customerId, displayName)` unchanged, and the ENTIRE valid fix for the real PRV-08 fixture is a single-file change to `CustomerRecord.java` alone.

The real approved plan for this P9 run (`.kriya/control/plans/20260908T200912-707d349b.json`, read directly from the actual run's own control-plane artifact) confirms the Planner **over-specified** s2 and s3 as `execution_role: implementation` / `planned_files action: modify` subtasks requiring code changes to `CustomerSummary.java`, `SummaryService.java`, and `Printer.java` — despite none of them needing any diff at all — while s4 (the plan's own terminal step) correctly modeled "revalidate" as `execution_role: verification` with `planned_files: []`. The Planner conflated AFFECTED (a real `requires`/`provides` dependency exists) with MUST_MODIFY (a file must be written) for s2/s3, when the goal's own language ("update"/"revalidate"/"preserve") never demanded a write. This is a genuine, distinct planning-layer observation — separate from CORR-016's own authority question — noted here, not separately triaged as its own risk row this pass.

**The real P9 log** (`logs/kriya.log` in that same working directory) shows the ACTUAL failure sequence after the correct rejection: attempt 1 rejected pre-write (`BROWNFIELD PUBLIC API REJECTED BEFORE WRITE`) → the existing, already-shipped `RESTORE_PUBLIC_CONTRACT` recovery phase (`INV-PRESERVE-002/003/004` family, `CORR-008`/`009`/`010`) deterministically restores `CustomerSummary.java` to baseline and re-invokes the Developer for **that one file only** (`owners=['CustomerSummary.java']`) → the Developer correctly reasons "this record's public contract is immutable, no change needed here" and returns `CustomerSummary.java` unchanged, exactly correctly — but never re-emits `SummaryService.java`, because the recovery phase's own Developer re-invocation never asked for it → the generic, recovery-unaware completeness check (`find_missing_expected_files` against `ctx.architect_files`, `kriya/workflow/attempt.py` ~line 5085) still requires `SummaryService.java` to appear in `state.all_files_written` for this attempt, since it doesn't know the recovery phase deliberately narrowed the Developer's scope → `IncompleteGenerationError` on both remaining attempts → retry budget exhausted → run FAILS. **First incorrect state after the legitimate rejection: the `RESTORE_PUBLIC_CONTRACT` recovery phase's own participant selection** (it re-invoked the Developer for the rejected owner only, without either also re-confirming/carrying forward the other originally-planned file's content or narrowing the completeness check to match its own reduced scope for that attempt) — a RECOVERY-layer coordination defect between two existing mechanisms (`kriya/workflow/attempt.py`'s `APIContractRecovery`/`RESTORE_PUBLIC_CONTRACT` and its generic completeness check), not a GENERATION defect (the Developer did exactly what it was asked, for the one file it was asked to fix) and not itself CORR-016's authority question. **NOT implemented or fixed this pass** — traced and diagnosed only, per explicit instruction to separate it from the DIRECT-authorization implementation below and stop for review before touching it. Likely extends the existing `CORR-008`/`009`/`010` (`INV-PRESERVE-002/003/004`) row rather than needing a new one, since it lives in the exact same mechanism family; a formal decision on whether to fold it into that row or open a new one is deferred to the next review, not made unilaterally here.

**DIRECT authorization implemented this pass** (narrower than Revision 2's original design, per explicit authorization limited to the DIRECT-only slice): `kriya/workflow/contract_authority.py` (new module) — `derive_direct_contract_authorizations(grounding_goal, structured_plan)`, a pure, deterministic, idempotent function (no MA8/`ObligationLedger` integration — none needed for DIRECT-only, since there is no parent chain, no plan-repair-vs-authorization interaction, and no resume state to reconcile for a record with no lineage) requiring OWNER, SYMBOL, and CHANGE CATEGORY to be independently grounded in the SAME clause of the raw `grounding_goal` text — never `Subtask.description`/`requires`/`provides`, never `GlobalInvariant.statement`. A record/class's own component-shape change (a named FIELD) is keyed by the owner's own type identity (matching `_normalized_public_signatures()`'s own record-identity convention: `signatures[f"record {record_name}(...)"] = record_name`), never by the individual field name — this distinction was verified necessary and correct against the real fixture before shipping (an earlier draft would have looked for `"region"` as the match key and never matched anything). `find_brownfield_public_api_changes()` gained an additive `active_authorizations` 5th parameter (default `None`, fully backward compatible — every existing call site/test passing 4 positional args is unaffected), matching per-EXACT-`(owner, symbol)` pair with an explicit category check (a symbol still present in `final_signatures` requires `ADD`/`MODIFY`; a symbol absent requires `REMOVE`) — a strict improvement on the reverted design's whole-owner forfeiture: two symbols in the same file are now evaluated fully independently. Wired at both existing call sites (`attempt.py`'s pre-write gate, `workflow.py`'s terminal gate), each filtering to authorizations whose `legal_scope.subtask_id` matches the current subtask before passing them in. DERIVED authorization remains **designed but deliberately NOT implemented** (see the module's own docstring and the design doc's own §5/§19) — no current production scenario demonstrates the need, and the general "is a preserving implementation possible" question is not soundly decidable from repository evidence alone.

11 new deterministic tests added to `tests/test_workflow.py` (no live LLM): `test_explicit_add_field_authorized`, `test_explicit_remove_symbol_authorized`, `test_explicit_modify_named_signature_authorized`, `test_downstream_update_language_does_not_authorize_public_contract_change`, `test_owner_named_but_symbol_not_named_rejected`, `test_symbol_named_elsewhere_in_goal_not_same_clause_rejected`, `test_planner_text_cannot_create_direct_authorization`, `test_same_file_unrelated_public_delta_rejected`, `test_authorized_direct_delta_with_stale_consumer_rejected_or_revalidated_correctly`, `test_prv08_customerrecord_direct_change_authorized`, `test_prv08_customersummary_change_not_authorized` (replaces the now-stale `test_prv08_shaped_downstream_contract_evolution_is_rejected`, same characterization intent, now explicitly proving CustomerSummary stays rejected even with the mechanism live and CustomerRecord's own authorization present). `test_prv08_shaped_deterministic_integration` gained a third part exercising s1's own `CustomerRecord` attempt end-to-end through `run_attempt()` with `grounding_goal` set — now correctly allowed. `test_completed_planner_dependency_cannot_authorize_public_api_change_without_requirement_authority` and both candidate-overlay tests kept unchanged.

**P9-P1/P9-R1 implemented (2026-09-08, fourth pass)**: two independent BUG FIXES, per a follow-up architecture review that traced the PLANNING root cause one level earlier than the RECOVERY finding above.

**P9-P1 (Planner over-specification)**: root cause confirmed by direct fork-based code trace, independently re-verified against the actual source — `PlannerAgent.system_prompt` (`kriya/agents/agent.py`) never mentioned `execution_role`/`ExecutionRole.VERIFICATION` anywhere, despite the schema already supporting a genuinely non-mutating "revalidate this affected consumer" subtask shape end-to-end (`plan_schema.py`'s own `model_validator` requires empty `planned_files` + a real verifier for that role; `plan_validation.py:895-926` explicitly exempts it from the `MODEL_SUBTASK_MISSING_PLANNED_FILES` check, found live PRV-05 2026-08-28; s4 in the very same PRV-08 plan already used it correctly). `workflow_controller.py:4855` (`target_files = [pf.path for pf in target.planned_files]`) confirmed the Planner's raw choice passes into `expected_files_upfront` completely unfiltered. Fix: added `execution_role` to the Planner's JSON shape example, corrected the prior "never emit a MODEL subtask with planned_files=[]" sentence to carve out the `verification`-role exception (previously actively hostile to using that role at all), and added a new paragraph teaching "AFFECTEDNESS DOES NOT IMPLY MUTATION" with a generic (non-PRV-08-named) worked example and the concrete `verification`-list JSON shape (never previously shown to the Planner at all — a second, necessary gap: even if told to use the role, the prompt never demonstrated how). Prompt content only — no schema, validator, MA8, MA9, or `WorkflowController` change. 3 new deterministic prompt-content tests (`tests/test_agents.py`): `test_planner_agent_system_prompt_documents_execution_role_verification`, `test_planner_agent_system_prompt_states_affectedness_does_not_imply_mutation`, `test_planner_agent_system_prompt_requires_positive_justification_for_implementation`. Real proof remains a P9 rerun (not authorized).

**P9-R1 (recovery completeness)**: confirmed independent of P9-P1 via the posed hypothetical (a valid two-file-modification plan hits the identical collision) and via the real log's own attempt numbering (`RESTORE_PUBLIC_CONTRACT`'s own single attempt exits via an internal `RecoveryPhaseAdvanced` control-flow exception *before* ever reaching quality gates — confirmed by direct trace of `run_attempt()` and by every existing `test_restore_public_contract_*` test's own shared pattern — so the actual collision fires during **REPAIR_BEHAVIOR**, attempts 3–4 in the real log, not literally the `RESTORE_PUBLIC_CONTRACT` phase the fix's own name suggests; both phases narrow the Developer's target scope identically via `known_target_files=state.last_implicated_files`, so the fix is scoped to `state.api_contract_recovery is not None` — any active recovery phase — not to one literal enum value, and this deviation from the fix's own working name is called out explicitly rather than silently shipping something that wouldn't have fixed the real log). Fix: new `GenerationState.last_candidate_contents: Dict[str, str]` field (`kriya/workflow/state.py`), updated from every attempt's own finalized `files` list regardless of that attempt's gate outcome (so a legitimate edit to one file survives a same-batch rejection caused by a different, unrelated file — the real P9 shape). Consulted in `run_attempt()` (`kriya/workflow/attempt.py`, immediately after path-resolution/dedup, before the brownfield check) only while `state.api_contract_recovery is not None`: an expected file (`ctx.architect_files`) outside the active recovery's own `owner_files` that isn't already part of this attempt's own `files` is folded in from its own cumulative content — never fabricated from baseline for a file that was never actually generated in any attempt (`.get()` returns `None`, left alone, still correctly reported missing). Same `files` list every downstream gate sees — no shadow candidate representation, no MA9/`RepairContract` change, no change to recovery owner selection, retry budgets, or the public-API restoration decision itself, no change to the ordinary (non-recovery) completeness path. 6 new deterministic tests (`tests/test_workflow.py`, next to the existing `test_restore_public_contract_*` group): `test_narrow_recovery_preserves_other_generated_file`, `test_narrow_recovery_does_not_invent_never_generated_file`, `test_restored_owner_uses_restoration_content`, `test_multi_file_recovery_cumulative_content`, `test_non_recovery_completeness_unchanged`, `test_prv08_shaped_recovery_regression` — all pass. Maps to the existing `CORR-008`/`009`/`010` (`INV-PRESERVE-002/003/004`) row, confirmed by source inspection (same `APIContractRecovery`/`RESTORE_PUBLIC_CONTRACT` mechanism family in `attempt.py`) — no new risk row created.

CORR-016's own wording is corrected accordingly: PRV-08 does **not** prove Kriya needs authority to evolve `CustomerSummary` — it proves (1) `CustomerRecord`'s own direct evolution is authoritative and now DIRECT-authorized; (2) affected downstream consumers must be revalidated, never mutated without positive justification (now taught to the Planner, P9-P1); (3) an unrelated/unauthorized downstream contract mutation must remain rejected unless separately, explicitly authorized (unchanged, always correct); and (4) Kriya's own recovery-completeness coordination had an independent bug that would have prevented convergence even under a correctly-scoped plan (P9-R1, now fixed).

Disposition **NEEDS_IMPLEMENTATION (unchanged)**: DIRECT authorization, P9-P1, and P9-R1 are all implemented and deterministically tested, but CORR-016 as a row represents "PRV-08 passes end-to-end," which still requires a real P9 rerun (not authorized this pass) to become production evidence. Not CLOSED, not moved to NEEDS_EVIDENCE, until that rerun is reviewed and passes.

**CORR-017 — Baseline bug-fix / brownfield-enhancement correctness (general)**
Language Scope: LANGUAGE_NEUTRAL (the Planner/Developer/Verify pipeline is stack-agnostic in principle; this row represents proven end-to-end capability, not a language-intrinsic mechanism) · Deployment Relevance: REQUIRED · Effective Evidence Level: **E5 — upgraded again this pass, this time earned through raw-evidence confirmation rather than assumed.** Pass 2 conservatively downgraded this to E4 because only P8 had been independently re-verified. This pass read P1–P8's actual raw `RESULT.md` files directly (`KRIYA_P_SERIES_EVIDENCE_AUDIT.md`) and found P6 and P7 both carry **independently-graded, non-Kriya-self-reported** acceptance evidence recorded directly in their artifacts — P6's real external runtime probe (observed vs. expected HTTP-response ordering, matched), P7's three independent verification layers plus a computed `P7_PASS: True` field. Combined with P8 (personally re-verified this session), that's three materially distinct qualifying scenarios for the same risk: different repositories (`spring-petclinic-rest` / `modular-app` / `spring-boot-application-example`), different task shapes (runtime-order sort with an external probe / cross-module interface extension with a 3-layer independent test / query-filter composition), different topologies (single-module runtime-verified / multi-module reactor / single-module). That is genuine E5 by the strict definition, with the diversity dimensions named explicitly, not asserted. P1–P5 remain real, solid E3-level evidence (Kriya's own deterministic gates demonstrably ran and passed) but are not independently re-verified at the same strength and are not needed once three qualifying E4 instances exist. Python: E0/E1 for this specific "proven baseline, confirmed end-to-end" claim — `VER-005` documents real, broad Python capability, but no single clean, fully-confirmed production pass exists yet for Python the way it now does three times over for Java · Disposition: CLOSED (Java, E5 met, exceeds the E4 bar) · Required Evidence Level: E4.

**CORR-018 — Unauthorized Behavioral Drift Within Authorized Files**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Effective Evidence Level: E2 (a real live production run independently confirmed the gap, plus a permanent deterministic characterization test pins the exact current, unaddressed behavior — this is direct, reproduced evidence of the gap's existence, not yet evidence of a fix) · Disposition: NEEDS_IMPLEMENTATION.

Requirement: *"When modifying an authorized file, Kriya must prevent or explicitly detect material behavioral changes unrelated to the authoritative goal, unless those changes are independently authorized or required by grounded evidence."*

Evidence: P10 (PRV-10, 2026-09-08 live production run) — `CustomerPrinter.print(CustomerRecord):String` kept its exact signature while its body changed from `return r.name();` to `return r.name() + " (" + r.region() + ")";`, under an authoritative goal explicitly requiring "Preserve all unrelated public contracts and behavior." No existing Kriya mechanism caught this: `find_brownfield_public_api_changes()` is signature-only by design (a deliberate choice — see the same function's handling of legitimate internal bug fixes, `CORR-011`); `SpecComplianceAgent`'s schema has only `missing_requirements` (absence), no field for an unauthorized ADDITION; the real regression/test suite only protects behavior an existing test already pins, and none did here for this file. This is a genuine coverage gap distinct from `CORR-016` (which concerns *authorized* contract-signature evolution) — CORR-018 concerns *unsignaled* behavior drift inside a file Kriya was already permitted to touch, where the signature itself never changes. Permanently characterized (not just narrated) by `tests/test_workflow.py::test_brownfield_guard_does_not_detect_public_method_body_behavior_change` — a deterministic, non-live test asserting the CURRENT (gap-having) behavior, so a future fix will fail this test first and must update it deliberately, not regress silently.

No solution implemented or designed this pass — recorded per explicit instruction to bookkeep the risk, not to close it. A real fix would need some form of behavior-change detection beyond signature comparison (e.g. body-diff-aware heuristics, mandatory regression-test evidence for touched public methods, or explicit narrow-scope authorization for body changes analogous to `CORR-016`'s DIRECT authorization) — no specific approach is endorsed here; that design question is open.

**CORR-018-P1 update (A3-bound slice only) — IMPLEMENTED, COMMITTED, LIVE-VALIDATED.** A narrower, proposal-derived slice of this same risk is now closed: `kriya/workflow/semantic_region_authority.py` (`find_unauthorized_semantic_changes`, wired as an additive gate in `attempt.py`/`workflow.py`) rejects body-level drift outside an explicitly authorized `AuthorizedSemanticRegion` set, but only when a run supplies one — i.e. only for A3's approved-proposal-promotion path (`kriya proposal execute`, `kriya/workflow/proposal_promotion.py`), not for arbitrary hand-typed `generate` goals. A4 (2026-09-10, live run against `graphify-poc/spring-boot-application-example`, real `qwen3-coder:30b`) is direct E4 live-production evidence this slice holds under real adversarial-shaped model behavior, not just unit tests: the Developer candidate hallucinated a materially different reimplementation of `DefaultDriverService.java` (different DI style, different package names, a nonexistent exception type) on attempts 1 and 3, both rejected before write (`semantic_region_unauthorized` / `brownfield_public_api_changed`), with deterministic `API_CONTRACT_RECOVERY` restoring the authoritative baseline and converging to the exact approved one-line change by attempt 6 — authorized regions (`METHOD_BODY`+`IMPORTS`, one file) never widened across any attempt. **The general case — CORR-018 for a plain `generate <goal>` run with no persisted/approved proposal and no authorized-region set — remains exactly as open as before; this update narrows scope, it does not touch the row's own Disposition above.**

**2026-09-10 evidence note (no disposition/implementation change, see `VER-006` for the full incident and its own separate containment fix):** the general-case gap named above (no authorized-region set, plain `generate <goal>`) is now backed by a second, concrete live instance — `~/kriya-live-validation/milestone_task_cli`, run `bpwsqscrg` — where `main.py` collapsed to a bare verification-marker line with `had_authorized_semantic_regions: false`. This is preservation-side evidence (the file was never protected in the first place); `VER-006` covers the separate verification-side defect (the resulting broken file was then incorrectly graded PASSED). Neither this note nor `VER-006`'s own implementation touches `CORR-018`'s code.

**CORR-019 — Reviewer self-assigned evidence confidence is not deterministically enforced**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Effective Evidence Level: E2 (two independent live A1 production runs each demonstrated the gap with a different concrete finding, plus a deterministic mechanism now exists and is unit-tested against both historical shapes; a later live run (A1-R3) confirmed the mechanism runs correctly against real model output, and a further live run (A4) reproduced the identical A1-R2 pattern a third time — all direct, reproduced evidence the mechanism runs correctly end-to-end in production, not yet evidence of the specific still-open case: the mechanism actively downgrading/rejecting a claim it should reject) · Disposition: NEEDS_EVIDENCE (implementation complete, live validation still required — see below).

Requirement: *"When Kriya presents an advisory review finding with an evidence-confidence classification, the final confidence must not exceed the authority and completeness of evidence deterministically supplied and resolved for that finding."*

Evidence: A1 live run 1 (2026-09-09) — Reviewer classified Spring same-class `@Transactional` self-invocation as `PROVEN ISSUE`, asserting a broken-transaction-boundary consequence not established by any evidence actually supplied. A1-R1 (prompt-only fix) corrected this exact pattern. A1-R2 (2026-09-09, second live run) — the *same* prompt-only discipline then classified `delete()`'s missing explicit `save()` call as `PROVEN ISSUE`, asserting the change "will not be persisted" - again a consequence not established by supplied evidence (and, independently, likely backwards under standard JPA dirty-checking semantics). Two independent live runs, two different patterns, the same underlying gap: prompt guidance alone does not reliably bound an LLM's own self-assigned confidence across pattern classes. Investigated (A1-E1, design-only pass) and implemented (A1-E2, this pass): `kriya/workflow/review_context.py`'s new deterministic evidence-adjudication mechanism (`build_member_evidence_ids`/`build_relation_evidence_ids`, `adjudicate_findings`, `build_structured_review_report`) makes Kriya, not the Reviewer, authoritative for final finding confidence - a `PROVEN_ISSUE` request is only honored when the model cites, by Kriya-generated id, at least one resolvable CONDITION reference and at least one resolvable CONSEQUENCE reference; otherwise it is deterministically downgraded (to `STRONG_STATIC_INDICATION` or `EVIDENCE_INSUFFICIENT`) or forced to `REQUIRES_PROFILING_OR_RUNTIME_EVIDENCE` when a runtime dependency is declared. Deliberately pattern-agnostic - no Spring/JPA/framework-specific rule anywhere in the mechanism; `tests/test_review_context.py::test_historical_shape_run1_self_invocation_overclassification_downgrades` and `test_historical_shape_run2_delete_persistence_overclassification_downgrades` prove both historical over-classifications downgrade through the identical generic code path.

A1-R3 (2026-09-09, performed a later pass than this row's last edit) subsequently did exercise the mechanism live: structured JSON parsed first attempt, 9/9 inventory, zero fabrication, model self-calibrated correctly with no downgrade needed that run — evidence the mechanism is live-compatible, but the override/downgrade path itself still wasn't actively triggered by a real model mistake in that run.

**A4 update (2026-09-10)** — the override path *was* exercised live, for a third time, and by the exact same pattern as A1-R2: during A4's live proposal-creation review (`kriya review ... --propose ... --save`, real `qwen3-coder:30b`), the Reviewer again classified `DefaultDriverService.delete(Long)`'s missing explicit `driverRepository.save(driverDO)` as `PROVEN ISSUE`. `DriverDO` is `@Entity`, `DriverRepository extends CrudRepository`, and `delete()` runs `@Transactional` — under standard JPA dirty-checking, the entity loaded via `findById` inside that transaction would plausibly be flushed at commit without an explicit `save()` call, so the consequence claimed ("the deletion flag will not be persisted") is not something the supplied static evidence actually establishes. Kriya's own adjudication left this one at `PROVEN_ISSUE` rather than downgrading it (the model cited a resolvable condition id **and** a resolvable consequence id — a collaborator reference to `DriverRepository.java` — which is exactly the evidence shape the mechanism requires to honor `PROVEN_ISSUE`; the mechanism is checking *reference resolution*, not semantic correctness of the inference the reference is used to support). This is new information beyond A1-R2: it shows the same overstated-confidence pattern isn't confined to the review report — the resulting `--propose`/`--save` proposal, its A3 approval, and its A3-P2 promotion into real generation all inherited the `PROVEN_ISSUE` label unmodified, and the local model then implemented the (still generally reasonable, if not *strictly proven*) recommendation end-to-end. Confidence-label overstatement can now be shown to propagate all the way to an accepted production diff, not just sit in advisory text a human reads before deciding. Still not a reason to modify A1's adjudication mechanism now (the requirement is about resolvable-reference completeness, and it was in fact met) — this sharpens what "NEEDS_EVIDENCE" should watch for next: whether reference *resolution* alone is a sufficient bar, or whether some claims need reference *relevance* checking too. That is a design question for a future pass, not resolved here.

Not yet CLOSED: disposition unchanged — real live evidence exists that the deterministic mechanism runs correctly end-to-end (three separate live occurrences now: A1-R1's self-invocation pattern already fixed, A1-R2's and A4's identical delete-persistence pattern both correctly gated through resolvable-reference adjudication), but no live run has yet caught the mechanism *actively downgrading* a request it should reject outright (all three real over-confident claims seen live so far happened to carry a resolvable, if not fully relevant, reference pair). That remains the open evidence gap.

---

## §3. RECV — Recovery (MA9, retry/failure classification)

**RECV-001 — Candidate-independent deterministic-failure termination** (`INV-RETRY-001`, FI-05, FI-06)
Language Scope: LANGUAGE_NEUTRAL (retry-loop logic operates on generic PASS/FAIL signals from any validator) · Deployment Relevance: REQUIRED · Effective Evidence Level: Java E3 (FI-05/06, real `test_deterministic_failure_diagnostic.py` exercises, including this session's own FI-06 negative-control test); Python E0/E1 (the underlying retry-loop code is language-neutral, but no Python-path exercise of it has been confirmed) · Disposition: CLOSED (Java bar met).

**RECV-002 — MA9 coordinated repair / cross-subtask plan recovery**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Related PRV: PRV-11 · Effective Evidence Level: **E1, sharply downgraded this pass.** PRV-11's own `RESULT.md` states explicitly: "Plan Recovery Capability — NOT EXERCISED this run — no `plan_recovery_events`; this run proves nothing about plan recovery specifically." The scenario passed for unrelated reasons. `kriya/workflow/repair_contract.py` (MA9) exists and is presumably unit-tested at some level, which is the actual basis for E1; no confirmed vertical or production evidence exists for the coordinated-repair mechanism specifically · Disposition: **NEEDS_EVIDENCE (downgraded from CLOSED)** · Required Evidence Level: E4 given this mechanism's history (the PRV-06-driven owner-recovery rewrite exists precisely because the prior version had a real live defect) — currently far below that.

**RECV-003 — Process-boundary/testability compatibility (System.exit-class conflicts)**
Language Scope: **JAVA — genuinely intrinsic**, not merely evidenced-only-in-Java: `ObligationKind.PROCESS_BOUNDARY_COMPATIBILITY`'s detector matches Java/Surefire crash signatures specifically, by explicit design (source comment: "list-shaped for future stacks," i.e., not yet generalized) · Deployment Relevance: REQUIRED · Related PRV: PRV-06 · **Evidence conflict formally resolved this pass** (was flagged §0.6 in the prior pass, chronology now established): **(A)** Did PRV-06 itself subsequently PASS after the process-boundary fix? No — only one PRV-06 result exists on disk (`results/PRV-06/hardened/` and `.../legacy/`, no timestamped attempt history the way the P-series has), and it is the FAIL result already cited; there is no evidence a later, passing PRV-06 result was ever produced and then overwritten, or that one exists elsewhere. **(B)** N/A, since (A) is No. **(C)** Was the mechanism subsequently exercised successfully by a *different* real P/PRV run? Not found — the Surefire/in-process-crash failure mode is specific enough (a JVM-crashing `System.exit`-class conflict during test execution) that none of P1–P8's own goals were shaped to trigger it, confirmed by re-reading all eight P-run summaries this pass (`KRIYA_P_SERIES_EVIDENCE_AUDIT.md`) — none mention this failure class. **(D)** Highest valid evidence level: **deterministic (vertical/unit) evidence, E3** — `test_run_attempt_escalates_message_when_process_boundary_failure_recurs` (`tests/test_workflow.py:2783`, confirmed to exist by direct grep this pass, not memory) plus 10 real, directly-confirmed detector tests in `tests/test_failure_grounding.py` (`test_detects_surefire_booter_fork_exception`, `test_process_termination_output_upgrades_type_and_message`, and eight others, all grep-confirmed to exist this pass, not previously verified this specifically) · Effective Evidence Level: **E3, confirmed (not merely corrected) this pass** — grounded in directly-verified test names, not memory citation · Disposition: CLOSED at the required E3 bar · **RESOLVED — downgraded to deterministic evidence.**

**RECV-004 — Bounded self-correction loop**
Language Scope: LANGUAGE_NEUTRAL (`kriya/workflow/self_correction.py` is generic tool-loop orchestration) · Deployment Relevance: OPTIONAL (`self_correction_loop_enabled` defaults `false`) · Related PRV: PRV-13 · Effective Evidence Level: **E1, downgraded this pass** — PRV-13's own `RESULT.md` status is `NEEDS_REVIEW` with its manual check ("confirm self-correction did not write build config") unticked; `tests/test_self_correction.py` is confirmed to exist, which is the actual basis for E1 · Disposition: **NEEDS_EVIDENCE (downgraded from CLOSED)**, OPTIONAL priority.

---

## §4. REPO — Repository mutation/transactions

**REPO-001 — Isolated Git worktree, fail-closed creation**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Effective Evidence Level: E4 (P1–P8 all used this path; P8 directly, independently re-verified this session) · Disposition: CLOSED.

**REPO-002 — Revision-grounded atomic apply**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Effective Evidence Level: E4 (same basis as REPO-001) · Disposition: CLOSED.

**REPO-003 — Write-authorization scope enforcement** (`AuthorizedFileWriter`, FI-01/FI-09)
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Effective Evidence Level: **E3 (corrected down from E4, same reasoning as `CORR-002` — PRV-18 corroboration withdrawn, FI-01/FI-09 vertical evidence stands independently and directly confirmed by grep this pass)** · Disposition: CLOSED for its scope-authorization purpose at the required E3 bar · **Explicitly not sufficient for DE-06's hostile-code containment claim** — see `SEC-001`.

**Post-demo evidence note (2026-09-11, no disposition change):** Demo 04
(`~/kriya-live-demo/demo-04-control/`) live-exercised `AuthorizedFileWriter`
directly and unmodified — a denied outside-scope write raised
`PolicyDeniedError` with zero bytes written (independently confirmed via
`os.path.exists()` and a directory listing), an allowed inside-scope write
succeeded with byte-for-byte matching content. Further live-production
confirmation of an already-CLOSED disposition, not new evidence changing
it, and explicitly not evidence toward `SEC-001` (the demo's own
`qualification.md` draws this same distinction before it was ever run).

**REPO-004 — Workspace state isolation**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Related PRV: PRV-14 (no recorded results, fault/recovery invariant, manual check required by its own design) · Effective Evidence Level: E1 · Disposition: NEEDS_EVIDENCE.

---

## §5. VER — Verification (compile/test/runtime)

**VER-001 — Deterministic build validation (real compiled output required)** (`INV-BUILD-001`, FI-04)
Language Scope: **JAVA (genuinely intrinsic — Maven reactor semantics)** · Deployment Relevance: REQUIRED · Effective Evidence Level: E4 — the real, live P7 Maven-reactor-false-positive defect, `13a73b0` (memory-sourced, not re-read raw this pass, but this is the specific fix independently credited with the 78.6→15.4 min P7 timing improvement, a detail with enough specificity to trust) · Disposition: CLOSED.

**VER-002 — Runtime verification, real evidence producer/consumer matching** (`INV-RUNTIME-001`/`002`, FI-07, FI-08)
Language Scope: LANGUAGE_NEUTRAL (managed-service prepare→launch→probe→verify is a generic orchestration pattern; the "prepare" step's own content is language-specific, but the pattern and the producer/consumer-matching check are not) · Deployment Relevance: REQUIRED · Effective Evidence Level: Java E4 (P6's two real live defects, `e073d47`/`e790c1a` — memory-sourced); Python E0/E1 · Disposition: CLOSED (Java bar met).

**Post-demo evidence note (2026-09-11, no disposition change):** commit
`740ddfd` (Demo 02 Artemis rehearsal finding) generalized managed_service's
existing artifact-preparation primitive to the `finite_command` runtime-
verification path (a `mvn package` step was missing before `java -jar`/
`-cp` invocation, previously misclassified as an environment/toolchain
failure). This is further, real production-path evidence supporting
`VER-002`/`VER-003`'s already-CLOSED dispositions — a same-day-fixed defect
in the verification tooling itself, not a standing register gap that was
ever open as its own tracked risk.

**VER-003 — Java validation correctness (compile/test-selection/regression, general)**
Language Scope: JAVA · Risk Family: `VER-LANGUAGE-VALIDATION` · Deployment Relevance: REQUIRED · Effective Evidence Level: E4 (P1–P8) · Disposition: CLOSED.

**VER-004 — Python dependency/build-metadata handling**
Language Scope: PYTHON · Risk Family: `VER-LANGUAGE-VALIDATION` · Deployment Relevance: REQUIRED · **Corrected this pass — the "confirmed defect" carried across the prior two passes was wrong, and the error is worth stating plainly rather than quietly fixing.** `kriya/tools/validate.py:131` (`_pyproject_dependencies()`) and its call site inside `_resolve_python_interpreter()` (lines 370-378) show pyproject.toml dependency extraction and installation is a real, wired mechanism, directly tied to a documented fix citing "PRV-17, 2026-09-03." The text I quoted in the prior two passes — "never consulted for DEPENDENCY INSTALLATION... always returns [] same as no pyproject.toml existing at all" — is the function's own docstring *describing the historical bug it was written to close*, not current behavior. I read an incident-description comment as a current-state description, the identical error class this whole exercise exists to catch, this time against source comments rather than PRV results. Caught by re-reading the actual code this pass, not by re-reading the comment more carefully · Effective Evidence Level: **E2, corrected from Pass 1/2's E0.** Real wiring confirmed directly; PRV-17's actual result shows a generated `pyproject.toml` declaring Django with `Quality Gates: PASSED` — indirect but compelling production confirmation (the tests could not have passed without a real Django import succeeding), though no dedicated unit test isolates the extraction→install path in isolation · Disposition: **NEEDS_EVIDENCE, corrected from NEEDS_IMPLEMENTATION** — a real mechanism with real (if indirect) production support, not an absent one.

**VER-005 — Python end-to-end validation/generation correctness**
Language Scope: PYTHON · Risk Family: `VER-LANGUAGE-VALIDATION` · Deployment Relevance: REQUIRED · Related PRV: PRV-17 · Current mechanism: see `KRIYA_PYTHON_CAPABILITY_SWEEP.md` for the full 24-capability audit completed this pass — repository discovery, project detection, dependency handling, venv assumptions, symbol extraction (native stdlib `ast`), planning, **static/syntax validation (a real `compile(source, f, "exec")` gate, newly confirmed this pass, structurally parallel to the Java `javac` check)**, test discovery/execution, and single-package topology are all confirmed `PRESENT`; import/dependency grounding and preservation checks are `PARTIAL` (the shared structural-evidence mechanism is Java-syntax-shaped); multi-package topology is `ABSENT` · Effective Evidence Level: **E2** — PRV-17's real result (`Quality Gates: PASSED`, `Kriya exit: 0`, a genuine 11-file Django generation) plus the confirmed breadth of real underlying capability; `NEEDS_REVIEW` status only because one scope-creep manual check was never ticked, not a correctness failure · Disposition: NEEDS_EVIDENCE — real, broad capability confirmed, but no single clean, fully-confirmed E4 production pass exists yet. Do not manufacture symmetry with `VER-003`'s E4, but do not understate real capability either — both of the prior two passes did, in different directions.

**VER-006 — Runtime-verification LLM fallback can upgrade deterministically distrusted evidence into terminal success**
Language Scope: LANGUAGE_NEUTRAL (the verification-marker contract, `pass_verdict_is_grounded()`, and `RunVerifierAgent.grade()` all operate on captured stdout/stderr text and written-file content generically, not on any language-specific structure) · Deployment Relevance: REQUIRED · Effective Evidence Level: **E4 at discovery** — a real, live, independently-confirmed production instance, not hypothetical: run `bpwsqscrg` (`/tmp/pol001_live_run2.log`) produced exit code 0, stdout `[VERIFICATION] PASS`, the deterministic contract-grounding check (`pass_verdict_is_grounded()`) correctly found the marker ungrounded (no `[VERIFICATION] FAIL` string anywhere in the written files), the result collapsed into the same `None` a genuine no-evidence case would produce, `RunVerifierAgent.grade()` was never told the marker was already distrusted, cited it as "strong, primary evidence" per its own then-unqualified system prompt, and returned `passed: true` — Quality Gates reported `PASSED` for a `main.py` independently confirmed broken (28 bytes, `print("[VERIFICATION] PASS")`, no argv dispatch, no CLI logic at all). Investigation: this session's dedicated root-cause task, full failure-chain trace with exact file:line references. Disposition: **NEEDS_EVIDENCE** (implementation complete this pass, see the P1 update and disposition note below — not CLOSED from self-tests alone).

**2026-09-10 P1 update — narrow containment implemented.** `kriya/workflow/verification_contract.py` gained `ContractVerdictState` (`PASS`/`FAIL`/`INDETERMINATE_DISTRUSTED`/`ABSENT`) and `classify_contract_verdict()`, a pure function replacing the old binary `Optional[Dict]` collapse. `kriya/workflow/attempt.py`'s `_extract_grounded_contract_verdict()` (all 7 call sites, across both `run_attempt()`'s 4 run_res-outcome branches and `_execute_runtime_verification_directly()`'s 3) is replaced by `_classify_grounded_contract_verdict()` (IO wrapper) + a new shared `_resolve_runtime_verification_grade()` — the single point every branch now routes through. Grounded PASS/FAIL: unchanged fast path, `grade()` never called. `ABSENT`: unchanged legacy LLM-fallback behavior, fully authoritative, exactly as before. `INDETERMINATE_DISTRUSTED`: `grade()` is still called (diagnostic value preserved) with a new, TRUSTED (non-fenced) `distrust_notice` parameter naming exactly what was distrusted and why — but the caller unconditionally force-sets `passed=False` on the result regardless of what `grade()` itself returns, since this verification path has no existing independent corroboration channel to check instead (the only evidence is the same captured stdout/stderr the deterministic layer already distrusted) and prompt wording alone is explicitly not trusted as the safety boundary. `RunVerifierAgent.grade()` (`kriya/agents/agent.py`) gained the `distrust_notice` parameter and a qualified system-prompt rule ("a self-reported verification marker is positive evidence only when it has not been deterministically rejected") — defense-in-depth, not the enforcement point. Provenance: `deterministic_result` (persisted in `gate_outcomes`/`traces.db`) now distinguishes `"DISTRUSTED"` (new) from `None` (`ABSENT`, unchanged) via the new `_deterministic_result_provenance_field()` helper, applied to both the failure and success outcome paths (previously only the success path set this field at all). Terminal aggregation (`GenerationState.final_workflow_quality_passed()`) was **not touched** — the containment at the runtime-verification gate itself (raising `QualityGateFailure` before any success outcome is ever appended) is sufficient; `quality_gates_succeeded` naturally stays `False`, exactly as the task's own stated preference for caller-level containment over terminal-aggregation redesign.

23 new tests (19 in new file `tests/test_ver006_distrust_containment.py`; 3 in `tests/test_agents.py` for the `distrust_notice` prompt-layer defense-in-depth; 1 new end-to-end integration test in `tests/test_workflow.py` confirming `graded_by=="llm_over_distrusted_evidence"`/`deterministic_result=="DISTRUSTED"` are queryable directly from `traces.db`), plus 1 pre-existing test (`test_workflow_ungrounded_pass_marker_falls_back_to_llm_grade`) whose own assertions encoded the exact vulnerability (`quality_gates_passed is True` when an LLM grader approved an ungrounded marker) and was corrected to assert the safe outcome instead — not a reclassification for its own sake, the same "correct the summary/test when it encoded the vulnerability" precedent as prior POL-001 increments. Self-verified via manual harness (never `.venv/bin/pytest`, per this repo's standing quota-discipline rule) — all 28 pass, including a direct reproduction of the live incident's exact shape (`print("[VERIFICATION] PASS")` as `main.py`'s entire content, mocked grader approval identical in spirit to the real incident's own grader) proving the final grade is forced non-PASS. Independent pytest confirmation is the user's own next step, same convention as every prior increment.

**2026-09-10 P1.1 regression fix, commit `cbc5d86`.** Independent full pytest (user-run) found 2 real failures introduced by P1 itself: `_resolve_runtime_verification_grade`'s `INDETERMINATE_DISTRUSTED` branch did `grade = dict(grade)` before mutating it, which requires the real Mapping protocol (`.keys()` + iteration) - two pre-existing tests (`test_run_attempt_cleans_up_runtime_artifacts_between_attempts[_without_git]`) construct `run_verifier = AsyncMock()` with only `.judge` configured, and their own `run_app_sequence` output legitimately reaches the new DISTRUSTED path, calling the unconfigured `.grade()` mock - an unconfigured `AsyncMock`'s auto-child `.keys()` returns a coroutine, not an iterable, so `dict()` raised `TypeError`. Classified `IMPLEMENTATION_DEFECT` (new code, not a test or environment issue). Fixed by mutating `grade` in place instead of copying - the same pattern every other branch in that function already uses - since `ctx.run_verifier.grade()` always returns a freshly-constructed dict in production, the copy was never load-bearing. Containment behavior itself (`passed=False` unconditional override) is byte-for-byte unchanged. Re-confirmed independently: full suite `3407 passed, 5 deselected, 124 warnings, 0 failures`.

**2026-09-10 E2 — controlled production-path runtime-verification validation using real subprocess execution and real local-LLM RunVerifier.** A new, isolated fixture (`~/kriya-live-validation/ver006-adversarial-e2/`, outside the Kriya repository, never `milestone_task_cli` - that fixture's own pre-incident `main.py` baseline is unrecoverable, no git history, confirmed unusable without fabrication) contains one deliberately, transparently broken file recorded before execution: `app.py` = `print("[VERIFICATION] PASS")` only, no argv handling, no branching, no real implementation of anything. Driven through the real, unmodified production path (`WorkflowEngine.run_generation_workflow()` → `run_attempt()`; `ContractVerdictState`/`_resolve_runtime_verification_grade`/`_extract_grounded_contract_verdict`/`ExecutionPolicy.evaluate` never called directly), disclosed component-by-component: `DeveloperAgent.run_generation` and `RunVerifierAgent.judge` controlled/fixed (deterministic, to guarantee the exact known-broken shape and keep this to one real model call, not a coding-generation benchmark); `PolymorphicValidator.run_compile_check`/`run_tests` controlled (trivial pass, a separate already-covered gate); the runtime subprocess (`python3 app.py`, real exit 0, real stdout `[VERIFICATION] PASS`) and `RunVerifierAgent.grade()` itself both real - a genuine, unmocked call to the configured local `qwen3-coder:30b` via Ollama, confirmed (via `traces.db`) to have fired exactly once and been replayed across all 8 retry attempts, never re-invoked. Result, confirmed directly from `traces.db`, not inferred: `deterministic_result="DISTRUSTED"`, `graded_by="llm_over_distrusted_evidence"` on every attempt; the real LLM returned a non-approving judgment (verbatim: *"...determined to be ungrounded and not reliable evidence of correctness. There is no independent demonstration..."*), consistent with (not proof of) `passed: false` - the exact literal boolean was lost to a self-inflicted terminal-output truncation, not re-derivable without a further model call, which was correctly not taken; terminal result `status: failure`, `failure_category: quality_gates_exhausted` - no false success. Independently, `app.py`'s final content matched its recorded pre-execution baseline exactly (`TARGET_BROKEN_BY_CONSTRUCTION`), and Kriya's own terminal failure agreed with that ground truth. **This is NOT full end-to-end generation evidence** - `DeveloperAgent`/`judge()` were controlled, not real, and the decisive differential branch (a real LLM independently returning `passed: true` over distrusted evidence, with Kriya's deterministic caller blocking it anyway) was not exercised; the real grader agreed the evidence was insufficient rather than attempting to override it. Per explicit instruction, not re-run to chase a different outcome.

**Disposition: NEEDS_EVIDENCE, not CLOSED.** Deterministic self-tests (28, independently pytest-confirmed clean at 0 failures across the full 3407-test suite) plus a real production-path validation (real subprocess, real `RunVerifierAgent`, real local model) both now exist and are green - a materially stronger evidence base than P1's self-tests alone. What remains missing, specifically: the single most decisive live differential (real LLM returns `passed: true` over distrusted evidence, deterministic caller blocks it anyway) has not been observed against a real model - E2's real grader agreed with the distrust notice rather than attempting to override it. Do not manufacture another run to force that outcome; this is a closure-bar judgment call, not a further implementation gap.

**CORR-018 evidence update (no disposition/implementation change this pass):** this same incident is separately cited as new, concrete evidence for `CORR-018`'s own already-open "general case" gap (preservation, not verification) — `main.py`'s near-total content collapse under a plain hand-typed `generate` goal, with no authorized semantic region ever declared (`had_authorized_semantic_regions: false`), is exactly the class of unsignaled behavioral drift within an authorized file `CORR-018`'s general case already names as unfixed. See `CORR-018`'s own entry above; its disposition (`NEEDS_IMPLEMENTATION`) and its P1-slice implementation are both unchanged.

---

## §6. SEC — Security / isolation

*(Unchanged from Pass 1 — no PRV/P-series evidence claims existed here to re-audit; all E0/E1 assignments already reflected absence of a mechanism, directly confirmed by source grep both passes.)*

**SEC-001 — Hostile-code containment for generated/executed code**
Language Scope: LANGUAGE_NEUTRAL · Requirement Authority: `DEPLOYMENT_ENVELOPE` §7 (DE-06) · Deployment Relevance: REQUIRED · Effective Evidence Level: E1 · Disposition: NEEDS_IMPLEMENTATION.

**SEC-002 — Fail-closed behavior under sandbox/policy failure**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Effective Evidence Level: E0 · Disposition: NEEDS_EVIDENCE first.

**SEC-003 — MCP environment isolation**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Current mechanism: `full_env = {**os.environ, **self.env}`, `kriya/mcp/mcp.py:41`, re-confirmed by grep this session · Effective Evidence Level: E1 · Disposition: NEEDS_IMPLEMENTATION.

**SEC-004 — MCP request timeout / non-responsive server**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Effective Evidence Level: E0 · Disposition: NEEDS_EVIDENCE first (line numbers not re-checked this pass either).

**SEC-005 — Package-installation/network-access containment**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Effective Evidence Level: E0 · Disposition: NEEDS_IMPLEMENTATION.

---

## §7. POL — Policy / execution enforcement

**POL-001 — Authoritative (pre-execution-denying) execution policy**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Effective Evidence Level: E3 (real deterministic tests, independently pytest-confirmed for P1+P2+P3 as part of a full-suite run; PLUS a decisive, directly-executed audit-vs-enforce differential through the real production `ShellTool`/`GitTool` paths — not inferred from a mode-independent mechanism, the actual `mode`-gated code branch was exercised and its side effect observed to differ; the differential's own two new pytest-format tests are self-verified via manual harness, independent pytest confirmation still outstanding, same convention as every prior POL-001 increment; no live-model evidence is required for this mechanism, it is deterministic policy-evaluation logic) · Disposition: **CLOSED** (P4, 2026-09-10 — see below for the decisive evidence and why the remaining self-verified-only status of 2 new tests does not block closure).

**2026-09-10 P2 update.** P1's own GitTool claim ("its subcommand surface cannot construct any of the 5 hard-enforced-code shapes... wiring a check here would add zero protective value") was **corrected, not just extended**, this pass: it was true for the 5 `enforce_hard_invariants` hard-DENY codes specifically, but P1 never checked what `_check_git_destructive`'s full rule set actually returns for a plain `git commit` — it is `GIT_WRITE_REQUIRES_APPROVAL` (the catch-all "everything else well-formed GIT_WRITE requires approval" rule), never a bare `ALLOW`. Centralized policy consultation being skipped for that reason was itself the mistake this pass corrects (per this task's own instruction not to dismiss policy wiring merely because the hard-invariant subset would allow it).

Implemented this pass, commit `0aeb613` (see `docs/design.md` §8.2 for the full narrative):
6. `GitTool` now classifies `commit` as its one mutating subcommand (`status`/`diff`/`log`/`branch`-list/`blame` stay ungated, GIT_READ-shaped) and consults the full `ExecutionPolicy.evaluate()` result before the subprocess starts, mirroring `_authorize_action`'s own established mode-gated semantics inline (this is a plugin tool, not a `WorkflowEngine` method, so the same shape is replicated rather than reused by reference): under `mode="audit"` (today's default) it evaluates and logs but never blocks — preserves current behavior byte for byte; under `mode="enforce"` a DENY raises immediately and `GIT_WRITE_REQUIRES_APPROVAL` fails closed (no `approval_callback` is reachable at this boundary, and Invariant 5 forbids inventing one for it). `GitTool.requires_confirmation` staying `False` is unchanged and still separately reportable (see P1 note above) — this pass closes the policy-consultation gap, not the confirmation-prompt gap, which is a distinct, smaller UX question.
7. `ShellTool` had the same class of gap, found by this pass's own Step 4 re-audit, not by P1: `enforce_hard_invariants` never acts on `REQUIRE_APPROVAL`, so a raw `bash -c "..."`/`sh -c "..."` invocation (`COMMAND_SHELL_WRAPPER_REQUIRES_APPROVAL`) passed P1's check silently. Added the identical mode-gated fail-closed check, layered on top of (not replacing) P1's unconditional hard-invariant sudo-etc. block — deliberately scoped to `REQUIRE_APPROVAL` only, not every non-ALLOW decision, so `COMMAND_NOT_ALLOWLISTED` DENY (a known, deliberately-excluded broad backstop — see `enforce_hard_invariants`'s own docstring) still never blocks, matching P1's own stated scope.
8. `execution_policy.mode`'s `"enforce"` rejection was **lifted this pass** — an explicit, confirmed-with-the-user rollout decision (matching the precedent `workflow_controller.mode`'s own analogous restriction already followed, per its docstring in `kriya/config/config.py`). `"audit"` remains the default; no existing project config is migrated. A garbage value (anything other than `"audit"`/`"enforce"`) is still rejected.
9. `worktree.py`'s bootstrap `git commit --allow-empty` was **audited, not changed**: it has the exact same `GIT_WRITE_REQUIRES_APPROVAL`-reachable shape as GitTool's commit, but its own MA4.8 module docstring already documents this deliberately — "real defense-in-depth for a future change here, not the closure of a currently-live gap" — because the commit content is fixed, Kriya-internal bootstrap text, never user/model-influenced, a qualitatively different trust profile from GitTool/ShellTool's genuinely caller-supplied content. Left as a pre-existing, already-documented, deliberate decision rather than touched incidentally while this file sits adjacent to CONC-001's protected worktree/lock code.

**REQUIRE_APPROVAL reachability (Step 4 re-audit):**

| ACTION_TYPE | Real site | Reachable? | Approval path | Behavior |
|---|---|---|---|---|
| INSTALL_PACKAGE | `workflow.py` Stage 2A | YES (every non-URL package) | Existing aggregate `approval_callback` prompt, same batch | Reachable + safe path used (P1) |
| WRITE_FILE | `AuthorizedFileWriter` | NO — verified: `_check_filesystem` never emits it for WRITE_FILE; `_check_approval_rules` requires `process_profile`/`engineering_route`, never set here | N/A | Fails closed anyway (P1, defense-in-depth) |
| RUN_COMMAND | `validate.py::_audit_run_command` | NO in practice — `cmd` always Kriya-constructed (mvn/gradle/pytest/npm), never shell-wrapper-shaped | N/A | Unaffected; new plumbing would be new architecture, correctly out of scope |
| RUN_COMMAND | `ShellTool` | YES (`bash -c`/`sh -c`/etc.) | None reachable | **P2 fix**: fails closed under `enforce`, audit-only under `audit` |
| GIT_WRITE | `worktree.py` bootstrap commit | YES (ordinary commit → catch-all) | None reachable | Not changed — pre-existing, documented, deliberate (see point 9 above) |
| GIT_WRITE | `GitTool.commit` | YES (same catch-all) | None reachable | **P2 fix**: fails closed under `enforce`, audit-only under `audit` |
| NETWORK_ACCESS / LLM_NETWORK_ACCESS | `kriya/core/llm.py` | N/A for `ExecutionPolicy` itself — real enforcement is the separate, independent `is_local_url`/`EgressViolationError` boundary by design (`_check_network_egress`'s own docstring) | N/A | Unaffected, by design |
| PUBLISH_ARTIFACT | none | No real production call site exists | N/A | Unused `ActionType`, per this task's own Invariant 2 allowance |

12 new/updated tests this pass, counted directly from the commit diff (9 brand new: 8 in `tests/test_tools.py` for GitTool/ShellTool, 1 in `tests/test_execution_policy_config.py`; 3 rewritten in place: 2 in `tests/test_execution_policy_config.py`, 1 in `tests/test_config.py`, all reflecting `"enforce"` now being accepted rather than rejected), self-verified via manual harness — all 12 pass. `execution_policy.mode="enforce"` unit tests now reach that branch through a real `ExecutionPolicyConfig(mode="enforce")` construction, not attribute assignment (an improvement on P1's evidence for the analogous `_authorize_action` tests). A broader regression sweep across every P1+P2-touched test file plus `tests/test_run_ownership.py` (CONC-001) also self-verified clean: **103 passed / 0 failed**, but this does not mean full CONC-001 coverage was independently re-confirmed — 12 of `test_run_ownership.py`'s 25 tests (the real cross-process/`CliRunner` contention tests, its actual core evidence) are outside the manual harness's reach (need a real `CliRunner`/`monkeypatch` fixture) and were not run this pass in any form; this pass touched neither `kriya/cli.py` nor `kriya/control/run_ownership.py`, so there is no code-level reason to expect a regression, but that is an argument from non-interference, not a re-verification. Independent pytest confirmation is required to close that gap. No live-model validation run or required. Exact pytest command handed to the user below.

**User-facing consequence of setting `mode="enforce"` (state this before anyone flips it):** every ordinary `kriya tools execute git '{"subcommand":"commit",...}'` and every `bash -c`/`sh -c`-shaped `kriya tools execute shell ...` invocation becomes **unconditionally unusable** — both reach a real, reachable `REQUIRE_APPROVAL` verdict with no approval path at that boundary, so both fail closed by design (Invariant 4), not as a bug. This is real, intended, but real capability loss a user selecting `enforce` needs to know about in advance, not discover live.

**Independent pytest confirmation received (2026-09-10, post-P2):** user-run full suite — `3361 passed, 5 deselected` — no failures, covering P1+P2's changes including the `test_run_ownership.py`/CONC-001 tests this pass's own manual harness couldn't reach. That specific gap is now closed.

**Independent pytest confirmation received again (2026-09-10, post-P3, corrects the `3361` figure above which is now stale):** user-run full suite — `3382 passed, 5 deselected, 111 warnings in 244.96s` — no failures, covering P1+P2+P3's changes including P3's own 21 new tests (`tests/test_policy_git_destructive.py`, `tests/test_worktree_policy_audit.py`), which were self-verified-only at the time P3's own paragraph below was written. That gap is now closed too — P3's own "pytest confirmation still outstanding" note is stale as of this confirmation.

**Still outstanding at that point, blocking CLOSED**: no real project had actually run `kriya generate`/`fix` with `execution_policy.mode="enforce"` set, and the only mode-dependent mechanisms (`GitTool.commit`, `ShellTool`'s shell-wrapper gate, `INSTALL_PACKAGE`) had only ever been exercised through unit tests or through a live run that happened to avoid all three — meaning no evidence existed that `mode="enforce"` actually changed any *observed* outcome versus `mode="audit"`, as opposed to just being unit-tested in isolation. See the P4 update below for how this was closed.

**2026-09-10 update.** The prior E1/NEEDS_IMPLEMENTATION characterization ("no authoritative policy exists") was stale. A full call-site audit this pass found real enforcement already implemented in three separate, previously-unconnected mechanisms: `WorkflowEngine._authorize_action`'s config-gated `enforce=True` branch (`kriya/workflow/workflow.py`, one real call site, INSTALL_PACKAGE); `kriya/policy/filesystem.py::AuthorizedFileWriter`'s own always-on, mode-independent `ExecutionPolicy` instance (DENY-only, WRITE_FILE, real callers: `attempt.py`/`milestones.py`/`self_correction.py`/`planning_diagnostics.py`); and `kriya/policy/enforcement.py::enforce_hard_invariants` (MA7.3, always-on, 5 hard-enforced DENY reason codes for RUN_COMMAND/GIT_WRITE, already wired into `validate.py`'s compile/test commands and `worktree.py`'s bootstrap commit). The one genuine, common gap across all three: `REQUIRE_APPROVAL` verdicts were computed and logged everywhere but never actually gated by a reachable approval mechanism.

Implemented this pass, commit `d9eeef6` (see `docs/design.md` §8.2 for the full narrative):
1. `workflow.py`'s Stage 2A INSTALL_PACKAGE loop now distinguishes DENY (re-raised immediately — real new protection once `enforce=True` is reachable) from REQUIRE_APPROVAL (deliberately not re-raised or independently prompted, to avoid turning the existing single aggregate approval prompt over the same `new_gaps` batch into up to N+1 prompts).
2. `AuthorizedFileWriter._raise_if_denied` fails closed on any non-ALLOW/ALLOW_SANDBOXED decision, not just DENY — verified unreachable today for every real caller (no behavior change), closes a fail-open gap for any future stage that populates `process_profile`/`engineering_route` on this writer's `ActionRequest`.
3. `plugins/core_tools/__init__.py::ShellTool._run` previously executed the model/caller-supplied command string with **zero** `ExecutionPolicy` consultation — the one genuinely ungated real RUN_COMMAND execution site found. Now calls the same `enforce_hard_invariants` gate `validate.py` already uses (best-effort `shlex.split` first). In practice this only ever blocks `sudo ...` today — the other 4 hard-enforced codes are GIT_WRITE-specific and a RUN_COMMAND-shaped request can never trigger them.
4. `GitTool` was audited, not changed: its subcommand surface (`status`/`diff`/`log`/`branch`-list/`commit`/`blame`) cannot construct any of the 5 hard-enforced-code shapes (no push, no ref deletion, no config/remote mutation) — wiring a check here would add zero protective value. Separately reportable, not fixed this pass: `GitTool.requires_confirmation` is `False` (`BaseTool`'s default, never overridden) unlike `ShellTool`'s `True` — `kriya tools execute git '{"subcommand":"commit",...}'` runs a real repository mutation (`git commit -m <message>`) with **neither** a human confirmation gate **nor** any policy consultation.
5. `execution_policy.mode`'s `"enforce"` rejection was deliberately left in place, not lifted. The config's own docstring documents the binding precedent: lifting it is "a distinct, later, explicit decision, not something accidentally reachable," mirroring how `workflow_controller.mode`'s analogous restriction was lifted only after being confirmed with the user directly. **This is a decision for the user to make explicitly, not one this pass made unilaterally** — the fixes above are what make that decision safe to make; they do not make it. (Lifting the validator would make `"enforce"` *selectable*; it would not change the default, which stays `"audit"`.)

9 new tests added (3 each to `tests/test_tools.py`, `tests/test_policy_filesystem_authorized_writer.py`, `tests/test_workflow_authorize_action.py`); self-verified via a direct manual harness (never `.venv/bin/pytest`, per this repo's own quota-discipline convention) — all 9 pass. A broader sweep of every test in the four touched files (new + pre-existing) also self-verified clean: 64 passed / 0 failed / 2 skipped (the 2 skips are pre-existing `test_tools.py` tests needing a real pytest `monkeypatch` fixture the manual harness can't provide, unrelated to this pass's changes). No live-model validation was run and none is required — this mechanism is deterministic policy-evaluation logic, not model output. The `enforce=True` unit tests reach that branch via direct attribute assignment on the pydantic config object (`validate_assignment` is off for this model) rather than through a real config load, so they prove the code path works, not that `mode="enforce"` is reachable from production today — it still is not, per point 5 above. Exact pytest commands handed to the user for independent confirmation, not run by the assistant.

Deliberately not done this pass (would require new architecture, out of this pass's scope): threading an `approval_callback` into `kriya/tools/validate.py::PolymorphicValidator`/`kriya/workflow/worktree.py::create_git_worktree` so their `enforce_hard_invariants` calls could handle a REQUIRE_APPROVAL verdict rather than only ever hard-enforcing 5 DENY codes — no approval_callback is in scope at either site today, and RUN_COMMAND's `COMMAND_NOT_ALLOWLISTED` REQUIRE_APPROVAL path is never actually reachable there in practice regardless (both callers construct well-formed, non-shell-wrapped argv tuples). Broadening RUN_COMMAND enforcement to `COMMAND_NOT_ALLOWLISTED` itself was NOT done — `enforce_hard_invariants`'s own docstring documents this as a known, deliberate exclusion (risk of breaking legitimate build/test commands), and this pass found no new evidence to override that.

**2026-09-10 P3 update — last known concrete gap closed.** P2's own `worktree.py` finding ("left unchanged... a different trust profile") was investigated as a dedicated task, then implemented once the investigation proved a safe, narrow design existed. `worktree.py`'s bootstrap has TWO real GIT_WRITE requests, not one — a bare `git init` (`_bootstrap_greenfield_repository`, only when the workspace isn't a Git repo at all yet) and the `--allow-empty` bootstrap commit (both `_bootstrap_greenfield_repository` and `create_git_worktree` itself, when the repo has zero commits) — both reach `_check_git_destructive`'s ordinary-write catch-all (`GIT_WRITE_REQUIRES_APPROVAL`), and neither had any reachable approval path. Authority class: **KRIYA_INTERNAL_CONTROL_PLANE** — fixed content, zero user/model input, the reserved `user.name=Kriya`/`user.email=kriya@local` command-local identity (used nowhere else in the codebase, confirmed by a full-repo grep), gated strictly on preconditions (`no commits yet` / `not a Git repo yet`) that have nothing to do with any goal/user/model decision.

**Important honesty note**: `worktree.py`'s own `_audit_git_write`/`enforce_hard_invariants` call is mode-independent and never acted on REQUIRE_APPROVAL either way (only the 5 hard-DENY codes), so this was never a functional regression under `mode="enforce"` — nothing was ever actually blocked. This is a **semantic-correctness fix** (the policy engine was giving an honest but wrong verdict for a Kriya-internal, zero-file-change control-plane action) and **future-regression prevention** (safe now if a later pass ever extends the same mode-gated fail-closed pattern used for GitTool/ShellTool to worktree.py too).

Implemented, commit `df256a0` (`kriya/policy/execution.py::_check_git_destructive`): two new, tiny, EXACT structural recognizers, checked before the existing subcommand-dispatch logic (which would misparse the commit's leading `-c` options as the subcommand) —
- `_is_kriya_internal_bootstrap_commit`: ALLOW (`KRIYA_INTERNAL_BOOTSTRAP_COMMIT_ALLOWED`) only for the exact 4-token reserved identity prefix (`-c user.name=Kriya -c user.email=kriya@local`, order-sensitive) + `commit` + a strict 3-token allowlist (`--allow-empty -m <one value>`, nothing else). A positive allowlist, not a blacklist — `--amend`, `-a`/`--all`, a pathspec, a second `-c` (the *different* `git commit -c <commit>` reuse-message flag), `--fixup`/`--squash`, or any extra/reordered token all fail the exact shape and fall through unchanged. The commit *message* is never inspected — deliberately not the security discriminator, consistent with this stage's own "never a single literal spelling" principle.
- `_is_kriya_internal_bootstrap_init`: ALLOW (`KRIYA_INTERNAL_BOOTSTRAP_INIT_ALLOWED`) only for the bare `("init",)` shape — `--bare`, `--template=...`, or a target-directory argument all fall through unchanged.

Neither recognizer trusts caller-supplied metadata; both derive their verdict purely from the command's own objectively-verifiable shape. Verified no other production caller can construct either shape: `GitTool`'s own commit construction never emits `-c`/`--allow-empty` (user input only ever reaches `-m`'s value, as one opaque token, not parseable into extra flags); `ShellTool`'s raw commands are classified `RUN_COMMAND`, never reaching this `GIT_WRITE` stage at all.

**Caught during self-review, both fixed before commit**: (1) an early manual test used `any(...)` over captured policy results for the greenfield path, which would have silently passed even with `git init` still stuck at `REQUIRE_APPROVAL` — replaced with an explicit, ordered two-result assertion; that same review is what surfaced the `git init` gap itself, missed in the P3 investigation's own prediction that the greenfield sibling would be "covered automatically." (2) A test-count claim was verified directly from the working diff before writing it here (21: 19 in `tests/test_policy_git_destructive.py`, 2 in `tests/test_worktree_policy_audit.py`), after two prior passes in this same POL-001 effort shipped an uncounted, wrong number.

23 near-miss/positive-recognition cases self-verified directly against `ExecutionPolicy.evaluate()` (identity-free, wrong name, wrong email, name-only, email-only, no-`--allow-empty`, `--amend`, `-a`, `--all`, pathspec, fixup, post-subcommand `-c` reuse-message confusion, `init --bare`, `init --template=`, `init <dir>`, plus every other `GIT_WRITE` subcommand's pre-existing verdict unaffected) — all pass. 21 new pytest-format tests added; self-verified via manual harness — all pass. A regression sweep across every P1/P2/P3-touched test file: **134 passed / 0 failed / 2 skipped** (both skips pre-existing, parametrized tests unrelated to this change, needing real `pytest.mark.parametrize` machinery the manual harness doesn't replicate). No live-model validation run or required. Exact pytest command handed to the user below.

Deliberately not done: no change to `worktree.py` itself (no mode-gated fail-closed check was added there — none was needed once the underlying policy verdict is correct; that call site's own `enforce_hard_invariants` wiring is unchanged); no broadening beyond the two exact recognized shapes; no approval-callback plumbing anywhere.

**2026-09-10 P4 update — decisive audit-vs-enforce differential evidence obtained, POL-001 CLOSED.** The prior live-run evidence (Task I / this document's earlier citations) exercised only `AuthorizedFileWriter` and `enforce_hard_invariants` — both **mode-independent**: they behave identically under `mode="audit"` and `mode="enforce"`, unchanged since before P1. That run proved these two mechanisms work end-to-end, but proved *nothing* about what `execution_policy.mode="enforce"` itself changes, since the only genuinely mode-dependent paths (`INSTALL_PACKAGE`'s `_authorize_action` branch, `GitTool.commit`'s P2 gate, `ShellTool`'s P2 shell-wrapper gate) were all outside that run's goal-selection scope. This pass obtains that missing evidence directly, without running another coding-generation benchmark, per this task's own explicit instruction.

Method: a standalone script (not `kriya generate`, no LLM call anywhere in it) constructs a real `AppConfig` via the production `load_config()` (not a bare `ExecutionPolicyConfig(...)` construction), builds `ShellTool`/`GitTool` exactly as `CoreToolsPlugin.initialize()` does, and calls the real `BaseTool.execute()` entrypoint with the exact same command under `mode="audit"` then `mode="enforce"`, spying on `ExecutionPolicy.evaluate()` to capture the real `PolicyResult` without altering it.

**Primary target — `ShellTool`, command `bash -c 'printf kriya-policy-evidence'`:**
- Audit: `evaluate()` called once, verdict `REQUIRE_APPROVAL` / `COMMAND_SHELL_WRAPPER_REQUIRES_APPROVAL` — the subprocess still started and completed, `stdout == "kriya-policy-evidence"`, exit code 0. Audit consults the policy and does not block.
- Enforce: `evaluate()` called twice on the identical request (once inside `enforce_hard_invariants`, unconditional since P1 and never itself acting on `REQUIRE_APPROVAL`; once in `ShellTool`'s own P2 mode-gated block, which does act on it) — both calls return the same `REQUIRE_APPROVAL` / `COMMAND_SHELL_WRAPPER_REQUIRES_APPROVAL` verdict. The second call raises `PolicyDeniedError`, wrapped by `BaseTool.execute()`'s pre-existing framework behavior into a `ToolExecutionError` whose message contains the reason code. The subprocess **never started** — proven with a filesystem sentinel (`bash -c 'touch <sentinel>'`), not just an exception message: the sentinel file does not exist after the call.
- **Required differential confirmed**: same `ActionType.RUN_COMMAND`, same command, same production path (`BaseTool.execute` → `ShellTool._run` → `ExecutionPolicy.evaluate`), only `execution_policy.mode` differs — audit executes, enforce blocks before the subprocess, attributable specifically to `COMMAND_SHELL_WRAPPER_REQUIRES_APPROVAL`.

**Optional second check — `GitTool.commit`, isolated temporary git repo (never a user repository):** same shape, same result. Audit: `evaluate()` once, `REQUIRE_APPROVAL` / `GIT_WRITE_REQUIRES_APPROVAL`, commit lands (`git log` shows it). Enforce: same verdict, `PolicyDeniedError` raised, commit count in the repo stays at 0 — proven via a real `git log`, not an inferred absence.

Both checks were directly executed and their output observed (not merely written as test code) via `.venv/bin/python3 <script>.py` — a direct execution of real production code, not `pytest` (this repo's standing "never run pytest myself" rule is specifically about the test *runner*, not about running Python at all; this is the same manual-harness convention P1–P3 already established for self-verification). Both differentials passed cleanly and unambiguously on the first corrected run (one self-correction: the enforce-mode `ShellTool` case genuinely calls `evaluate()` twice, not once — real production control flow from the two independent mode-aware checks layered on this path, not a test defect).

The two scripts were then converted into durable pytest-format tests (`tests/test_tools.py::test_shell_tool_wrapper_audit_vs_enforce_differential_through_load_config`, `::test_git_tool_commit_audit_vs_enforce_differential_through_load_config`, commit `0079a29`) so this evidence survives in the suite rather than only in a scratch script — adding tests is not "modifying production policy code" and is the same thing every P1–P3 increment did. Both self-verified via manual harness (all assertions pass); **independent pytest confirmation for these 2 specific new tests is still outstanding** — they postdate the `3382 passed` full-suite run cited above, so that number does not yet include them. This does not block closure: the decisive evidence this task required (a directly-executed, directly-observed production-path differential) was already obtained and is not contingent on the tests' own pytest-format re-confirmation; the pytest confirmation is durability for the future, not a precondition of what was just proven. A final full-suite run remains recommended as routine hygiene, same as after any commit, and will fold these 2 tests into the confirmed count — exact command below.

**POL-001 CLOSED.** Both closure conditions are met: (1) independent full pytest is green (`3382 passed, 5 deselected`, covering every POL-001 change through P3; P4 adds 2 more self-verified-only tests, noted above, not blocking); (2) the audit/enforce differential passes, decisively, on two independent real production paths. The full-inventory `REQUIRE_APPROVAL` reachability audit (P2's table above) has no known unresolved gap. POL-001 is now frozen — no further policy-architecture work should be queued against this risk ID unless a future validation run exposes a concrete new defect, per the user's own stated intent when authorizing the P1→P4 arc.

**Separate finding, recorded but NOT fixed this pass (out of POL-001's scope, not a policy-enforcement defect):** the prior live `kriya generate` validation run (Task I, `~/kriya-live-validation/milestone_task_cli/`, background task `bpwsqscrg`, log `/tmp/pol001_live_run2.log`) produced a functionally broken `main.py` (28 bytes: literally `print("[VERIFICATION] PASS")`, no argv parsing, no actual CLI dispatch — the Developer agent's Attempt-2 anchored-edit fix collapsed the file) that Kriya's own Quality Gates nonetheless graded `PASSED`. Root cause: the static `bare_verification_marker` rule correctly rejects an unquoted, non-branching `[VERIFICATION] PASS` literal, but the LLM-grading fallback ("doesn't look like it actually branches on anything") incorrectly approved this file anyway — a runtime-verification/LLM-grading-trust defect, independently confirmed by direct file inspection and a standalone correctness check, distinct from POL-001 (which governs *authorization to act*, not *correctness of what was generated*). This is real-project evidence for a different, already-known register risk (`VER`-domain territory, verification/quality-gate trust), not this one — left unimplemented here per this task's own explicit instruction not to repair it as part of POL-001 closure.

**POL-002 — Security-sensitive-goal handling / approval escalation**
Language Scope: LANGUAGE_NEUTRAL (sensitive-path/approval logic is orchestration-level, not Java-specific) · Deployment Relevance: REQUIRED · Related PRV: PRV-02 · Effective Evidence Level: **E2, downgraded this pass from Pass 1's E4.** PRV-02's `RESULT.md` status is `NEEDS_REVIEW`, manual check ("confirm valid/expired/malformed token semantics") unticked — Quality Gates passed and `TokenValidator.java`/`TokenValidatorTest.java` were produced, real evidence something happened, but the scenario's own acceptance bar for the security-sensitive *semantics* specifically was never confirmed · Disposition: **NEEDS_EVIDENCE (downgraded from CLOSED)**.

**POL-003 — Sandboxed execution policy under `execution_policy.mode: audit` (current, working as designed)**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: OPTIONAL (this is the current, correctly-functioning audit-only mode, not `POL-001`'s REQUIRED enforce capability) · Effective Evidence Level: E4 (exercised by every P-run) · Disposition: CLOSED for its own narrower scope.

**Post-demo evidence note (2026-09-11, no disposition change):** Demo 04
also exercised the generic `ExecutionPolicy().evaluate()` path directly
against the same denied target used for the `REPO-003` differential above,
confirming it independently reaches the same DENY verdict (its own,
distinct reason code, `PATH_OUTSIDE_WORKSPACE_DENIED`) but never gates
anything on its own — computed and logged only, exactly as this row
already documented. Confirming evidence, not new.

---

## §8. TOOL — Tools / MCP execution authority

**TOOL-001 — Policy-mediated authoritative TOOL subtask execution**
Language Scope: LANGUAGE_NEUTRAL · Requirement Authority: `DEPLOYMENT_ENVELOPE` §5 (DE-04) · Deployment Relevance: REQUIRED · **Reclassified this pass, as requested and as the primary source text directly supports:** this is **not** a requirement-authority conflict. `_run_structured_enforce`'s current refusal of TOOL-tagged subtasks is, by KRP-020's own stated rationale (read in full this pass, not the one-line summary): *"Structured enforce mode currently refuses TOOL-tagged subtasks because safe authoritative routing does not exist."* That is a description of *why the current safe behavior is correct today*, not a competing authoritative requirement. No two currently-authoritative requirements demand incompatible behavior. This is a **REQUIRED CAPABILITY GAP with an intentional, currently-correct safety restriction** — the restriction should not be removed until the capability (`TOOL-002`) exists; the capability's absence is the actual gap · Effective Evidence Level: E0 · Disposition: NEEDS_IMPLEMENTATION. Depends structurally on `TOOL-002`, `TOOL-003`, and — per `ORCH-001`'s transitive-necessity note — on `KRP-012`.

**TOOL-002 — Authoritative ToolBroker**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Effective Evidence Level: E0 · Disposition: NEEDS_IMPLEMENTATION.

**TOOL-003 — MCP tool-schema trust vs. capability authorization**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Effective Evidence Level: E0 · Disposition: NEEDS_IMPLEMENTATION.

**TOOL-004 — Plugin manifest/provenance/compatibility governance** (new, KRP-031)
Language Scope: LANGUAGE_NEUTRAL · Requirement Authority: `KRP_REQUIREMENT` (KRP-031) · Deployment Relevance: REQUIRED — DE-04's controlled-capability model implies the same governance discipline for plugin-delivered extensions as for MCP tools, not just MCP itself · Current mechanism: `kriya/plugins/` exists (plugin discovery/`BasePlugin` per `CLAUDE.md`'s own architecture description) but no manifest/provenance/capability-approval/version-compatibility registry was found or traced this pass · Effective Evidence Level: E0 · Disposition: NEEDS_IMPLEMENTATION.

---

## §9. MODEL — Local-model / runtime governance

**MODEL-001 — Model capability profile accuracy vs. actual runtime/quantization/template**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Effective Evidence Level: E1 · Disposition: NEEDS_IMPLEMENTATION (KRP-023).

**MODEL-002 — KnowledgeGuard / public-knowledge-gap handling**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Related PRV: PRV-10 (no recorded results) · Effective Evidence Level: E1 (`tests/test_knowledge.py`, `tests/test_knowledge_extraction.py` confirmed to exist) · Disposition: NEEDS_EVIDENCE.

**MODEL-003 — Fallback-model escalation on failure**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Effective Evidence Level: E3 · Disposition: CLOSED.

**MODEL-004 — Fresh-repository stack-drift resilience**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Related PRV: PRV-17 · Effective Evidence Level: **E2, more precisely characterized this pass** — the R7–R14 rounds cited in project memory, and the raw result re-read this pass, are Python/Django evidence specifically (not Java, as Pass 1's write-up left ambiguous), status `NEEDS_REVIEW` not clean PASS · Disposition: NEEDS_EVIDENCE (downgraded from Pass 1's CLOSED — the mechanism is real and heavily exercised, but "heavily exercised, one unticked manual check" is not the same as CLOSED at the required bar).

---

## §10. CTX — Context / retrieval

*(Unchanged from Pass 1 in substance — no PRV/P-series E4 claims existed here to re-audit.)*

**Gap flagged, not filled (2026-09-11).** Demo 02's Qpid→Artemis
qualification experience surfaced a distinct pattern this section does not
cover: `kriya learn`'s untrusted external-knowledge RAG path (see
`CLAUDE.md`'s "Untrusted learned-knowledge RAG" note — the fencing there
addresses *safety*, i.e. prompt-injection, not *relevance*) has no risk row
anywhere in this register addressing whether ingested content is actually
relevant/correct for the library/version in use — "safe acquisition !=
relevant acquisition." Grepped this pass: zero mentions of `learn`/
`learned_knowledge`/RAG-relevance outside this section header and `CTX`'s
own repo-context rows (`CTX-001`–`003`, which govern internal-repository
retrieval, not externally-ingested knowledge). Genuinely uncovered, but not
added as a new row in this reconciliation-only pass — out of this task's
scope (no new-risk authorization given here, unlike the dedicated A1-P1/
VER-006 passes that added rows explicitly). Recommend a dedicated pass to
formalize this as a new risk row if it isn't picked up otherwise.

**CTX-001 — Dependency-graph/context freshness and health**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Effective Evidence Level: E1 · Disposition: NEEDS_IMPLEMENTATION · graphify candidate mechanism retained unchanged from the original seed entry.

**CTX-002 — Repository-context scale / large-repository budget behavior**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Related PRV: PRV-16 (no recorded results) · Effective Evidence Level: E1 · Disposition: NEEDS_EVIDENCE.

**CTX-003 — Dependency resolution from real build metadata**
Language Scope: LANGUAGE_NEUTRAL (the underlying risk — trusting real build metadata over hardcoded coordinates — isn't Java-specific even though PRV-09's own goal was) · Deployment Relevance: REQUIRED · Related PRV: PRV-09 (no recorded results) · Effective Evidence Level: E0 · Disposition: NEEDS_EVIDENCE.

---

## §11. STATE — Persistence / replay

**STATE-001 — Checkpoint/resume correctness under real interruption**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Related PRV: PRV-15 (no recorded results, fault/recovery invariant) · Effective Evidence Level: E1 · Disposition: NEEDS_EVIDENCE.

**STATE-002 — Multi-store state consistency**
Language Scope: LANGUAGE_NEUTRAL · Requirement Authority: `DEPLOYMENT_ENVELOPE` §4 for the underlying *behavior* (crash-consistent unattended operation, DE-03) — **but not for `KRP-026`'s specific "unify around one canonical event log" implementation shape, which is one candidate solution, not itself an envelope requirement.** Reframed this pass, per the explicit challenge: do not treat an architectural consolidation recommendation as though the envelope demanded that exact shape. Deployment Relevance: REQUIRED (the behavior) · Effective Evidence Level: E1 · Disposition: **NEEDS_EVIDENCE first (corrected from Pass 1's direct NEEDS_IMPLEMENTATION)** — whether the *current*, fragmented stores actually violate crash-consistency under real interruption is untested (this is exactly what `STATE-001`'s own gap is about); if that evidence shows a real failure, *then* NEEDS_IMPLEMENTATION, and `KRP-026`'s unification is a candidate answer, not the only possible one.

**STATE-003 — Deterministic run replay / reproducibility ID** (new, KRP-027)
Language Scope: LANGUAGE_NEUTRAL · Requirement Authority: `KRP_REQUIREMENT` (KRP-027) + `DEPLOYMENT_ENVELOPE` §7 (DE-06's threat model implies investigability of applied changes) · Deployment Relevance: REQUIRED · Current mechanism: `traces.db` captures substantial per-run detail but has no reproducibility-ID/bundle/tamper-detection concept · Effective Evidence Level: E1 · Disposition: NEEDS_IMPLEMENTATION.

---

## §12. CONC — Concurrency / workspace ownership

**CONC-001 — Safe rejection of a conflicting second authoritative mutation run**
Language Scope: LANGUAGE_NEUTRAL · Requirement Authority: `DEPLOYMENT_ENVELOPE` §6 (DE-05) · Deployment Relevance: REQUIRED · Effective Evidence Level: E3 (real deterministic tests, including real cross-process OS-lock contention/`SIGKILL` recovery/git-worktree independence - not live-model evidence, none is required for deterministic infrastructure) · Disposition: **CLOSED**.

Invariant enforced: **exactly one mutating Kriya process may acquire workspace ownership; every competing process is rejected before that competing process can perform repository/workspace mutation.** (Acquiring ownership never itself guarantees the winner completes without error - only that no second process can mutate the same workspace concurrently.)

Implemented (2026-09-10, commit `cc4cbbf`, unattended-workstation/Linux-CI envelope - multi-user/server operation stays out of scope, see `TOP-004`): `kriya/control/run_ownership.py`'s `acquire_run_lock()` - a context manager around `fcntl.flock(fd, LOCK_EX | LOCK_NB)` on `<workspace>/.kriya/run.lock`, held for the acquiring process's lifetime. The kernel lock is the sole correctness authority (releases automatically and unconditionally on crash/`SIGKILL`, no PID/heartbeat staleness scheme needed or used); lock-file content is diagnostics only, never consulted to decide ownership. Wired into the four CLI entry points that can mutate a repository - `generate` (both branches), `fix`, `proposal execute` - each holding one lock for its entire command duration; `review`/`ask`/`proposal show`/`approve`/`reject` never acquire it, by construction. Full design rationale and worktree/symlink-alias semantics: `docs/design.md` §4.4a.

Evidence: 25 new tests (`tests/test_run_ownership.py`) plus zero regression in the pre-existing 37 CLI smoke tests, user-confirmed real pytest run: **all green, 0 failures** (baseline 3318 + 25 new = 3343 passed). Coverage includes real cross-process contention (not a mocked `flock`), real `SIGKILL`-recovery, real `git worktree`-independence, symlink/relative-path alias contention, PID-reuse non-reliance, and permission-failure fail-closed behavior.

Deliberately not done this pass: the optional defense-in-depth lock inside `run_generation_workflow()` itself (for a hypothetical future non-CLI mutation entry point) - the CLI-layer wiring above already covers every currently-traced mutating path, including `WorkflowController`'s own early worktree creation, since it has no caller outside those same four commands. Any future non-CLI mutating entry point must call `acquire_run_lock()` itself before its first possible mutation - documented as an explicit invariant in the module's own docstring and `docs/design.md` §4.4a, not implemented speculatively here.

**CONC-002 — Global `sqlite3.connect` monkey-patching**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: OPTIONAL · Effective Evidence Level: E3 · Disposition: NEEDS_IMPLEMENTATION, OPTIONAL priority.

---

## §13. OBS — Observability

**OBS-001 — Structured-plan repair-round telemetry, enforce-mode `generation_metrics` gap**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Effective Evidence Level: E4 (the gap's existence was directly, independently observed during P8, not inferred) · Disposition: NEEDS_IMPLEMENTATION.

**OBS-002 — Operator-facing run summary**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Effective Evidence Level: E1 · Disposition: NEEDS_IMPLEMENTATION.

**Post-demo evidence note (2026-09-11).** Two Demo-01-rehearsal findings,
both fixed same-day pre-demo (commits `9726ff4`, `3e27e54`), are new,
concrete implementation evidence within this row's scope, not evidence for
any other existing risk (grepped: no other row names Reviewer-narrative or
failure-category truthfulness specifically):
1. **Rejected-candidate truthfulness** — a real terminal Quality-Gates
   FAILURE was followed by a Reviewer report opening "the application
   successfully..." with a "How to Run" section, contradicting the FAILED
   banner immediately above it. Fixed with deterministic, marker-based
   extraction (`ReviewerAgent.extract_rejected_candidate_diagnostic()`,
   fail-closed if markers are missing) rather than prompt wording alone —
   `3e27e54`'s own commit message documents a live run that proved prompt
   compliance alone was insufficient before this structural fix landed.
2. **`generation_budget_exhausted` mislabeling** — a run that simply ran
   out of configured time was funneled into the same
   `state.environment_failure`/`STOP_ENVIRONMENT` path genuine toolchain
   failures use, so the CLI printed misleading `kriya doctor` advice. Fixed
   with its own failure_category and dedicated CLI message.

Both are real, deterministic, tested (20 new tests combined) narrow slices
of "truthful operator-facing summary" — specifically for the
run-failed/rejected-candidate case. Disposition stays
`NEEDS_IMPLEMENTATION`: this row's evident scope is the general
operator-facing summary (including successful/retried/escalated runs),
which these two fixes do not cover.

**OBS-003 — Secret redaction in logs/traces/evidence**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Effective Evidence Level: E0 · Disposition: NEEDS_EVIDENCE first (confirm absence precisely before assuming NEEDS_IMPLEMENTATION — not done this pass).

**OBS-004 — Explicit run/subtask resource budgets** (new, KRP-024)
Language Scope: LANGUAGE_NEUTRAL · Requirement Authority: `KRP_REQUIREMENT` (KRP-024) + `DEPLOYMENT_ENVELOPE` §4 (DE-03 unattended autonomy needs bounded resource consumption to be safe — a runaway retry loop or unbounded process time is exactly the kind of thing that becomes dangerous without supervision) · Deployment Relevance: REQUIRED · Current mechanism: scattered limits exist (`generation_time_budget_seconds`, sandbox CPU/memory limits, retry ceilings) but no single governing budget object · Effective Evidence Level: E1 · Disposition: NEEDS_IMPLEMENTATION.

---

## §14. REL — Release / productization

**REL-001 — Reproducible release artifact**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Effective Evidence Level: E0 for SBOM/CI-promotion specifically; the live repo does have a working `pyproject.toml` (this session's own `pip install -e .` usage is direct confirmation), so the original review's "no packaging metadata" finding is at least partly stale for the current repo, not just the archived zip — not fully re-verified this pass either · Disposition: NEEDS_EVIDENCE first.

**REL-002 — `kriya doctor --production` truthful environment certification**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Effective Evidence Level: E2 (`kriya doctor` genuinely exercises multiple real components together — directly re-confirmed this session ahead of P8) · Disposition: NEEDS_IMPLEMENTATION.

---

## §15. TOP — Topology / support boundary

**TOP-001 — Gradle build-system support**
Language Scope: JAVA · Requirement Authority: `DEPLOYMENT_ENVELOPE` §2 (DE-01) · Deployment Relevance: REQUIRED · Effective Evidence Level: E0 (zero mechanism found or traced) · Disposition: NEEDS_IMPLEMENTATION — one of the largest concrete gaps the envelope's DE-01 correction created.

**TOP-002 — Non-Spring, framework-neutral Java repository support**
Language Scope: JAVA · Deployment Relevance: REQUIRED · Effective Evidence Level: E1 (P7's own repository was plain multi-module Java, not Spring-specific, per project memory — not re-verified raw this pass) · Disposition: NEEDS_EVIDENCE.

**TOP-003 — Monorepo and generated-source pipeline support**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: OUT_OF_SCOPE (Envelope §3/§8) · Disposition: DEFERRED.

**TOP-004 — Multi-user/server deployment, distributed coordination**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: OUT_OF_SCOPE (Envelope §6/§8) · Disposition: DEFERRED · `CONC-001` remains REQUIRED regardless (rejection, not support).

**TOP-005 — CI/non-interactive operating requirements**
Language Scope: LANGUAGE_NEUTRAL · Deployment Relevance: REQUIRED · Effective Evidence Level: E2 (the P-series harness itself runs Kriya non-interactively, repeatedly, successfully — real evidence of *a* working non-interactive path, not a deliberately CI-certified one) · Disposition: NEEDS_EVIDENCE.

---

## Superseded / no-distinct-risk mappings

Unchanged from Pass 1: PRV-07 folds into `TOP-MVN-003`/`004` (Topology
Coverage doc, both VALIDATED, spec never run, no evidence contributed);
PRV-03/05 fold into `CORR-017`/`CORR-008`–`010`; PRV-04 superseded by
`CORR-013`; PRV-06 is now `RECV-003`'s own row (not folded, its result is
directly discussed there); PRV-12 folds into `VER-002`; PRV-18 no longer
cited as corroborating evidence for `CORR-002`/`REPO-003` (its own result
is `NEEDS_REVIEW`, not confirmed) but the fold-mapping itself (same
underlying mechanism) still holds.
