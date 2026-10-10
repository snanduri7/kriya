# 02 — Kriya Run Cockpit, GUI design v1.1 (authoritative UX specification)

**Status:** proposed for the owner gate (`01_README_AND_GATE.md`). **Supersedes** `inputs/KRIYA_ELECTRON_GUI_DESIGN_SPEC_v1_0_SUPERSEDED.md`.
**Visual reference (frozen):** `inputs/KriyaJavaHamcrestRepairDashboard.png` for visual character only; every control or
value it depicts is governed by `03_EVIDENCE_AVAILABILITY_MATRIX.md` (cited below as *M-row n*).
**Data authority:** GUI M1 (`handover/GUI-DESIGN-DISCUSSION/05_GATE.md`) for everything KUP v1 exposes; `04_UI_READ_API_CONTRACT.md` for everything new.
**Scope of this document:** product identity, information architecture, layout, responsive rules, selection model,
every screen, component ownership, interaction contracts, states, design system, accessibility, security, and the
acceptance criteria. Implementation order is in `05_IMPLEMENTATION_PLAN.md`.

---

## 1. Product identity (frozen)

Kriya is an **Explainable Agent Execution Cockpit**: run-centric and evidence-centric. It is not a chat-first
assistant, not an IDE, not a monitoring dashboard, and never a second authority on whether a run succeeded.

Primary experience: **Goal → Run → Phase/Unit → Attempt → Evidence → Outcome.**
Promise: *Give Kriya an engineering goal; watch it plan, execute, validate, recover and prove what happened.*
Every screen answers: **what happened, what evidence exists, why Kriya decided this, and where the underlying fact is.**

## 2. Principles (binding on every surface)

1. **Evidence before decoration.** A value is rendered as observed only when a durable source in the matrix records it.
2. **Compose, never normalize.** Each displayed object keeps its source operation, schema version, consistency kind,
   provenance and verbatim value. Two sources that disagree are shown side by side, never reconciled by choosing one.
3. **Backend vocabulary is authoritative.** Status literals, gate `type` strings, reason codes, consistency kinds,
   verification states and evidence classes are displayed as recorded; a friendly label exists only where §13 maps it.
4. **Absence is explicit.** Loading, empty, unavailable and error are four distinct states with distinct copy (§12).
5. **No fabricated attribution, telemetry, phases, live state or controls.** Tokens per second, a full phase rail,
   running timers, pending steps, Pause, Stop and New Run do not exist in v1 (M-rows 9, 67, 77–80).
6. **Derived means badged.** Anything computed from recorded inputs (DERIVED phases, context percentage) carries a
   DERIVED badge naming the inputs.
7. **Capability-gated, hidden not disabled.** A destination, tab, control or column appears only when the read
   capability it needs is advertised (§4.3). Unfinished areas are not shown greyed out.
8. **Progressive disclosure.** First glance: goal, state, phase summary, latest outcome. One level: attempt, candidate,
   validation, context. Deep: structured evidence, provenance, raw records, model I/O. Deep evidence is reachable in at
   most two interactions from the run.
9. **Readable density.** Dense engineering layout, 32–36 px interactive rows, no colour-only semantics.
10. **Cohort vocabulary never appears in run status.** GENUINE, SAFE_FAILURE, FALSE_NEGATIVE, FALSE_SUCCESS and hand
    classifications are experiment evidence only (M-row 91).

## 3. Information architecture

### 3.1 Destinations

| Destination | v1 | Gate (capability flag, §4.3) | Secondary pane |
|---|---|---|---|
| **Runs** (default) | visible | `history` (KUP v1) | Runs pane (§6) |
| **Workspace** | visible | `workspace` (KUP v1) | workspace selector + sections list |
| **Settings** | visible | host | settings sections |
| Knowledge | hidden | `knowledge` (future) | — |
| Models | hidden | `qualification` (role metrics are S1 and always present) (future surface) | — |
| Tools | hidden | `tools` (future) | — |
| Experiments | hidden | `experiments` (future) | — |

Hidden destinations have no rail slot. When a flag appears, the slot appears. No "coming soon".

### 3.2 Desktop shell

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ TOOLBAR  Kriya ▸ <config dir> ▸ <workspace>        [Search runs, files, evidence ⌘K]  Trust ● ⚙ │
├──────┬────────────────┬───────────────────────────────────────────────┬──────────────────┤
│ RAIL │ SECONDARY PANE │ CENTRAL RUN WORKSPACE                         │ EXECUTION        │
│ Runs │ Runs: search,  │ Run header (goal, status, duration, attempts) │ TIMELINE +       │
│ Work │ filters,       │ Trust strip (compact ▸ expandable)            │ INSPECTOR        │
│ Sett │ milestone      │ Phase rail (status/filter, DERIVED-badged)    │ tree: unit ▸     │
│      │ groups, runs   │ Tabs: Overview Context Plan Changes           │ attempt ▸ call / │
│      │ by day         │       Validation Attempts Evidence            │ gate / decision  │
│      │                │ <run-scoped document>                         │ ───────────────  │
│      │                │                                               │ Summary|Evidence │
│      │                │                                               │ |Raw   + WHY?    │
├──────┴────────────────┴───────────────────────────────────────────────┴──────────────────┤
│ DRAWER (collapsed by default)  Build | Tests | Git | Raw Events  [Model I/O when exposed] │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

### 3.3 Three-surface rule (binding)

| Surface | Scope | Contains | Never contains |
|---|---|---|---|
| Central tabs | the selected **run** | run-scoped documents (Overview, Context, Plan, Changes, Validation, Attempts, Evidence) | per-node detail panes, log streams |
| Right inspector | the selected **timeline node** | hierarchy tree; Summary / Evidence / Raw of the current node; WHY | run-wide documents |
| Bottom drawer | **streams** for the current selection | Build, Tests, Git, Raw Events, (Model I/O) | structured documents, controls |

### 3.4 One navigator

The **timeline tree is the only hierarchical navigator.** The phase rail is a status summary and filter. Attempts is a
table and comparison surface over the same nodes. All three read and write the single selection (§5).

## 4. Layout, responsive rules, gating

### 4.1 Regions

| Region | Default | Range | Notes |
|---|---|---|---|
| Toolbar | 44 px | 44–48 | always visible |
| Rail | 56 px | fixed | icons + labels on hover/focus; ⌘1/⌘2/⌘3 |
| Secondary pane | 280 px | 240–360, collapsible | resizable |
| Central workspace | flexible | min 620 | always wins width |
| Inspector | 340 px | 300–460, collapsible | collapsed by definition at ≤1180 px |
| Drawer | collapsed | 220–360 expanded | opens only on user request |

Target 1440–1728 px. Minimum 1180 px. At ≤1180 the inspector is collapsed (56+240+620+300 = 1216 > 1180); at <1360
the secondary pane may collapse to an icon-triggered pane. Code and evidence are never compressed below readability to
keep a panel open. **Focus mode** (⌘⇧F) hides secondary pane, inspector and drawer; the rail stays.

### 4.2 Toolbar

Left: product mark; configuration directory and selected workspace (from M1 settings; M-row 24). Centre: search
(runs by id/goal; files by recorded path; evidence by reason code) scoped to loaded data; never labelled "ask Kriya".
Right: trust indicator (summary of §7), settings. **No run actions** (M-rows 78–80).

### 4.3 Capability gating

The host asks `capabilities` once per session and once after any settings change. Flags (existing KUP `features` plus
those in `04 §3`): `snapshot`, `prompt`, `evidence`, `evidence_content`, `qualification`, `authority`, `knowledge`,
`tools`, `experiments`, `live`, `actions`. Every destination, tab, column and control declares the flags it needs in
the shared package (`availability.ts`); a missing flag hides it. The test host exercises every flag combination.

## 5. Selection model (single reducer, shared package)

Extend `ui/shared/src/state/selection.ts` **additively** (owner decision Q-4, 2026-10-10): the M1 fields and actions
(`inspectorTab`, `drawerTab`, `drawerOpen`, `comparisonPath`, `selectComparison`, ...) are kept and keep working for the
legacy M1 panels until cockpit parity is accepted; the cockpit fields below are added beside them. The new shell never
dispatches the M1 `selectComparison` action (it auto-opens the drawer on the `diff` tab, `selection.ts:50`); it uses
the cockpit's own `selectComparisonPath`-style action that sets `comparisonPath` only. One reducer, one state object;
the cockpit fields are:

```
Selection {
  runId          : string | null        // S1 run_id
  unitId         : string | null        // S2 unit_id (null when S2 absent)
  attempt        : number | null        // attempt number (S1 and S2 agree on this key)
  callSeq        : number | null        // selected model call (S2 call_seq); null when S2 absent
  eventRef       : { source: 's1'|'s2', index|seq } | null   // selected timeline node
  gateRef        : { attempt, type } | null                  // selected gate (S1 key); S2 seq when exposed
  phaseFilter    : string | null        // DERIVED phase value or sentinel group; filters only
  comparisonPath : string | null        // selected file in Changes
  centralTab     : 'overview'|'context'|'plan'|'changes'|'validation'|'attempts'|'evidence'
  inspectorLevel : 'summary'|'evidence'|'raw'
  drawer         : { open: boolean, tab: 'build'|'tests'|'git'|'raw_events'|'model_io' }
  whyOpen        : boolean
}
```

Rules: selecting a run resets everything below it; selecting a unit resets attempt and below; selecting an attempt
resets call, event and gate; selecting a call sets `eventRef` to that `model.request` record. The attempt and call
selectors inside the Context and Changes tabs are views over this state: they dispatch `selectAttempt` / `selectCall`
and hold no local copy, so "selected attempt" has exactly one owner; `phaseFilter` never changes selection, only visibility; the central tab never changes as a side
effect of a timeline click (the inspector offers *Open in Changes / Validation / Context* links instead). Electron keeps
an in-memory back/forward stack of Selection; the test host may mirror Selection to the URL hash. No router library.

## 6. Runs pane

- Search (id, goal substring). Filter chips keyed to recorded status literals: **All · Finished (success) · Failed
  (failure, error) · Stopped (knowledge_gap, baseline_indeterminate, architect_file_list_rejected) · Unfinished
  (in_progress)**. Chips are generated from the literals present in the loaded list; unknown literals get their own chip.
- Grouping order: **milestone group** (rows sharing `milestone_group_id`, ordered by `milestone_index` of
  `milestone_total`; the `<group>.milestone-plan` row (status `milestone_plan_<status>`) heads the group; `<run>.enforce`
  rows (status literal or `unknown`) attach under their run when `enforce_run_id` is exposed, M-rows 3, 4) → single runs
  → day (Today, Yesterday, date). Timestamps are shown as stored, with "timezone not recorded".
- Row: run id (short), goal (one line), status badge (icon + literal), duration if `duration_sec` present, attempts,
  `failure_category` literal when present. Nothing else. One accent highlight for the selected row.
- Never "Active": workspace RUN_ACTIVE lives in the trust strip (M-row 5).

## 7. Run header and trust strip

**Run header:** run id, status literal with icon, goal (full, wraps), duration, attempts, model hops (names), milestone
position when grouped. Up to four chips; overflow into a details popover. No "CAGC" or harness labels.

**Trust strip** (compact line, expandable):

| Field | Source | Shown when |
|---|---|---|
| snapshot id, acquisition time, source-metadata-changed | KUP consistency (M-row 22) | always |
| consistency kind literal | M-row 23 | always |
| state-store path, trace db, configuration directory | M-row 24 | always |
| workspace active state (RUN_ACTIVE …) for the selected workspace | M-row 5 | workspace selected |
| model identity digests per role (runtime_digest, runtime_exact, inference_settings_digest) | M-row 17 | recorded |
| Developer qualification **recorded at run start** (status, missing, failed, reasons) | M-row 19a | recorded |
| **current** qualification per model identity (QUALIFIED / STALE / no record), labelled *observed now* | M-row 19b, `qualification` flag | flag present |
| SEC-009 approval state | M-row 21, `authority` flag | flag present |
| attempt-evidence capture mode and store status at run start (from the S1 event) | M-row 25 | always |
| attempt-evidence verification state, record count, gaps/degraded, seal closed_reason; absence reason (DERIVED) | M-rows 26, 27, 28, `evidence` flag | flag present |
| base/run revision | M-row 29 | recorded |

Compact form example: `snapshot 20261010T…a4 · snapshot_copy · evidence VERIFIED (full) · qualification: unavailable`.
Missing values render **Not recorded** (durable source, nothing written) or **Unavailable** (no read capability).

## 8. Phase rail

Shows, in recorded order of first appearance, the DERIVED phase groups present in S2 records for the run
(planning, architect, attempt, review, requirement_verification, localization, milestone_planning; M-row 10) and the
S1 sentinel groups (planning.failed, review.refused, workspace_commit.failed, terminal_regression.passed; M-row 11).
Every group carries the badge **DERIVED** with a tooltip *derived from the role of recorded model calls; Kriya does not
record phase boundaries*. No KnowledgeGuard, no Commit, no "completed" state. Clicking a group sets `phaseFilter`.
When S2 is absent the rail shows only sentinel groups, or *Phase information not recorded for this run*.

Explicit `phase.opened`/`phase.closed` events are a future enhancement; their schema and producer points are specified
in `04 §7` and are **not** a prerequisite for anything in this document.

## 9. Central tabs

### 9.1 Overview (understand the run in ten seconds)

Four blocks, no more: **Outcome** (status literal, failure_category, terminal_status from generation_metrics, stop
reason event if any); **Progress** (attempts, units if S2, phase groups present, duration); **Work** (files_modified
count and list, candidate files per attempt when S2); **Gates** (per attempt: gate `type` → success boolean, first
failed gate in recorded order highlighted). Requirements block appears only when requirement.* events exist (M-row 53).
A failed or stopped run shows a **Why?** button that opens §10.

### 9.2 Context — signature surface

Scope selector: attempt → model call (S2) or attempt (S1 only). Sections, each with source badge:

1. **Request fit** (S1 context.request_fit: tiers, truncated_files, known_target_files, batches).
2. **Prompt sections** (S2 prompt.sections: section name, kept_chars, kept_digest, fitted, unlocated_sections,
   request_tokens / capacity_tokens).
3. **Omissions** (S1 details where recorded: path, reason literal such as budget_exhausted; otherwise *Omissions not
   recorded for this request*).
4. **Guidance and skills** (S1 active_skills names, retrieved_chunks per `ui/docs/RETRIEVED_CHUNKS_AND_FIELDS.md`; S2
   section names).
5. **Budget** (estimated per-section tokens vs provider_reported total from developer.prompt_composition, in two visibly
   distinct columns; context percentage only under M-row 66, DERIVED-badged).
6. **Request content** (S2 blob, `evidence_content` flag, loaded on explicit click, capped; §11.4).

Never derived from the current repository; never claims a file was shown because it exists.

### 9.3 Plan

Hierarchical rows: plan validation/repair events with their reason codes (M-row 68); executed units in invocation order
(M-row 69); unit verdict events (M-row 71). The declared plan (M-row 70, the workspace control store's `RunRecord.execution_plan`)
appears only when a future `observe.run_record` operation exists, a workspace is selected and the record survived
control retention; until then the tab states *Declared plan not available in recorded evidence*. No graph in v1; no "planner
confidence".

### 9.4 Changes

Attempt selector → file list (left) → content (centre).
- **Mode A** (S2 candidate.change with before/after/diff blobs): unified or side-by-side diff rendered from the recorded
  `diff` blob, or computed in the renderer from the recorded `before` and `after` blobs only; file provenance
  (before_digest, after_digest, kind, proposal_kind, decision); *Open in IDE* on the path.
- **Mode B** (digest_only, store absent, pre-recorder): paths, kind/operation, digests, and the banner **Candidate
  content was not retained.** S1-only runs show files_modified as the raw string, split on commas, labelled raw.
- Never reads the working tree.

### 9.5 Validation

Sub-tabs generated from the gate `type` literals present (compile, test, targeted_test, regression_test,
goal_spec_compliance, test_selection, runtime types, static analysis, terminal gates when S2 exposes them). Each gate
row: attempt, type literal (friendly label from §13 if mapped), success boolean (absent → *Result not recorded*;
conflicting legacy `passed` → *ambiguous*, both shown), writer-specific fields verbatim, output in the drawer.
Per-test tables only from persisted structured rows, loaded through the blob operation on explicit click: pytest
cases (all statuses) from the S2 `gate.result` `test_execution` blob or the `regression.decision` case blobs (M-row 46);
Surefire failure and error rows only (M-row 47), never a pass list and never "N more passed"; Gradle shows the badge
**Structured per-test evidence unavailable for Gradle** (M-row 48). Without the `evidence` flag the tab shows the
status-only S1 field and *Structured per-test evidence not available*. Baseline attribution events (M-row 49)
listed under the regression gate.

### 9.6 Attempts and Compare

Table: attempt, role/model (from the S2 `model.request` of that attempt only; S1 role metrics are per run, so an
S1-only run shows *run-level model: ‹names›, not per attempt*), trigger (previous attempt's first failed gate or retry event),
candidate files (S2) or *not recorded*, gate outcomes (icons per type), retry/recovery reason (S1 retry.* / S2
recovery.decision), disposition (attempt.concluded.succeeded when S2). **Compare** picks A and B: S2 retry.delta fields
(request_digest, sections, targets, scope, model, temperature, blocking reasons, ledger revision; M-row 58) plus S1
differences in gate outcomes (role metrics are per run in S1 and are not compared). Dimensions without a record show *Not recorded*.

### 9.7 Evidence

Structured catalogue grouped by attempt (and unit when S2): gate outcomes, evidence_records, requirement events,
authority snapshots, obligations, diagnosis, explain answers (RECORDED / NOT_APPLICABLE / NOT_RECORDED). Each item shows
source, seq or index, provenance and digests; raw payload opens in the inspector Raw level. Unresolved
`attribution.evidence_ids` stay unresolved (M-row 54).

## 10. WHY (signature interaction)

Available on any failed, rejected, retried or repaired node. Opens as an inspector overlay; never replaces evidence.

1. **What happened** — the recorded gate outcome or event, verbatim.
2. **First failed gate in recorded order** — a fact from gate_outcomes order; never "first incorrect state".
3. **Recorded classification** — failure_category, failure_report entry, attribution_tier / kind / confidence /
   reasoning as written on the gate outcome.
4. **Evidence statements** — S2 diagnosis statements, each prefixed with its evidence class **MEASURED ·
   DERIVED_DETERMINISTIC · MODEL_CLAIMED · UNKNOWN**; MODEL_CLAIMED is styled as a claim, never as a fact.
5. **What Kriya did next** — the next retry/recovery/fallback/refusal/stop event.
6. **Unknown** — every item above that has no record, listed by name.

Without S2: items 4 reads *Diagnosis not available (no attempt-evidence read)*. If nothing establishes cause:
*Cause not established. Kriya recorded the failure and recovery evidence above; it does not prove a causal attribution.*

## 11. Inspector and drawer

### 11.1 Timeline tree

Primary source S2 (unit → attempt → model call / gate / decision / event), degraded to S1 (attempt → event) when S2
is absent or unverified. Sources are never interleaved by time; an S1 event and an S2 record are siblings under the
same attempt, each with a source badge. Nodes show icon + literal + time as stored. Keyboard: ↑↓ move, → expand,
Enter select, Esc close overlay.

### 11.2 Detail levels

**Summary**: recorded facts in sentences, each with its source. **Evidence**: reason codes, digests, references,
provenance. **Raw**: the stored payload (JSON) for this node only. Links: *Open in Changes / Validation / Context /
Evidence* set `centralTab` explicitly.

### 11.3 Drawer

Build (compile outputs), Tests (test gate outputs), Git (S1 commit-related events, M-row 11; workspace commit evidence has no read operation in this version, M-row 42), Raw
Events (S1 run_events; S2 records when exposed), Model I/O (S2 request/response, `evidence_content` flag only).
Filtered by run → attempt → gate. Read-only text, virtualized, copy allowed, no input, no "run" control.

### 11.4 Blob loading

Any S2 content (prompt text, response, before/after, diff, gate output) loads only on an explicit click, through the
blob operation with its size cap (`04 §4.4`); the UI shows digest and byte size first, then *Load (n KB)*. Over-cap
blobs offer ranged loading. Content is never prefetched and never cached across sessions.

## 12. States and errors

| State | Copy pattern | Example |
|---|---|---|
| Loading | spinner + operation name | *Reading history.detail…* |
| Empty | operation succeeded, nothing recorded | *No validation evidence was recorded for this attempt.* |
| Not recorded | durable source has no field | *Omissions not recorded for this request.* |
| Unavailable | no read capability | *Qualification state unavailable (no qualification read).* |
| Degraded | S2 state ≠ VERIFIED | *Unsealed: run active or crashed* / verification literal |
| Error | typed KUP error code + message + database_state, stale-data flag, retry button | `STORE_BUSY (locked)` |

Never a generic "No data"; never a silent fallback to a weaker source.

## 13. Friendly labels (the only permitted mappings)

| Literal | Label |
|---|---|
| success / failure / error / in_progress | Succeeded / Failed / Error / Unfinished (no terminal record) |
| knowledge_gap, baseline_indeterminate, architect_file_list_rejected | Stopped: ‹literal› |
| milestone_plan_‹status› / unknown (enforce rows) | Milestone plan: ‹status› / shown literally |
| gate types compile, test, targeted_test, regression_test, goal_spec_compliance, test_selection | Compile, Tests, Targeted tests, Regression tests, Goal spec compliance, Test selection |
| phases planning, architect, attempt, review, requirement_verification, localization, milestone_planning | Planning, Architect, Development attempt, Review, Requirement verification, Localization, Milestone planning (all DERIVED) |
| verification VERIFIED / UNSEALED / NOT_FOUND / CHAIN_BROKEN / TRUNCATED_TAIL / BLOB_CORRUPT / SEAL_MISMATCH | shown literally with icon |

Every label shows the literal in a tooltip. Unknown literals are shown as-is.

## 14. Workspace and Settings

**Workspace:** workspace selector (native folder dialog, M1); recovery assessment (status literal, run_active id,
records, evidence, unreadable_records, evidence_error; M-row 72); toolchain identity as recorded per gate (M-row 74);
*Open in IDE*. Repository identity, branch, cleanliness, containment and approved roots are hidden until a
workspace-observation operation exists (a future `observe.workspace_identity` flag; M-rows 73, 75).

**Settings:** Kriya executable, configuration directory, workspace path, snapshot inventory (ids, acquisition times, bytes, retained count and limit, verify on request; M-row 92), appearance (theme,
density, reduced motion), evidence content visibility (host-side narrowing only; content appears only when Kriya's own
`content_visibility` field is on, 04 §3), diagnostics. Nothing that bypasses Kriya policy; each setting states whether it
affects future reads only.

## 15. Component ownership

```
ui/shared (host-independent, no Electron/Node imports)
  state/selection.ts         Selection reducer (§5; EXTENDED additively, M1 fields/actions kept until parity retirement, see §19.1)  owner: shared
  model/availability.ts      capability flags → visibility              owner: shared
  model/kup.ts, evidence.ts  generated types from ui/kup/schema         owner: kup
  panels/Shell/*             Toolbar, Rail, SecondaryPane, Focus mode
  panels/Runs/*              RunsPane, RunRow, MilestoneGroup
  panels/Run/*               RunHeader, TrustStrip, PhaseRail, RunTabs
  panels/tabs/*              Overview, Context, Plan, Changes, Validation, Attempts, Evidence
  panels/inspector/*         TimelineTree, DetailLevel, WhyPanel, EvidenceLinks
  panels/drawer/*            BuildStream, TestStream, GitStream, RawEvents, ModelIo
  render/*                   diff (from recorded blobs only), sanitize, virtual list
  host/HostAdapter.ts        runKup(op, params) · openInIde · clipboard · settings (unchanged contract)
ui/kup                       JSON Schemas (history, workspace, snapshot, capabilities, + evidence, qualification,
                             authority), generated TS/Java types, validators, offline tools
ui/standalone (Electron)     main: kriya child spawn (argv arrays, fixed env), typed IPC proxy, response cache keyed
                             (op, params, snapshot_id | head_digest); preload: allowlisted messages only
ui/test-host                 fake HostAdapter over fixtures; renders every panel and every flag combination in CI
kriya/kup (Python)           the only reader of S1/S2/S3 (04)
```

Shared components: StatusBadge (icon + literal), AvailabilityBadge (recorded / not_recorded / unavailable /
derived / unsealed), SourceBadge (S1 / S2 / S3 + operation), EvidenceReference (seq/index + digest), ReasonCode (literal
+ tooltip), GateOutcomeRow, AttemptRow, BlobLoader (digest, size, load button, cap handling), EmptyState, OpenInIde.

## 16. Interaction contracts

| Trigger | Effect | Must not |
|---|---|---|
| select run | load history.detail; if `evidence` flag: evidence.manifest + evidence.verify; reset lower selection; central tab stays | prefetch blobs or records |
| select timeline node | set unit/attempt/event/gate; inspector shows node | change central tab |
| click phase group | set phaseFilter | change selection |
| select attempt row (Attempts) | same as timeline attempt node | open compare |
| select gate | gateRef; inspector Summary; links to Validation | auto-open drawer |
| select file (Changes) | comparisonPath; render Mode A/B | read the worktree |
| attempt / call selector in Context or Changes | dispatches selectAttempt / selectCall on the single reducer; inspector follows | keep a local copy of the selection |
| WHY | whyOpen overlay in inspector | hide raw evidence |
| Open in … link | sets centralTab explicitly | — |
| drawer tab | sets drawer.tab; content filtered by selection | open on failure automatically |
| Load content | one blob op with cap; result shown with digest match indicator | cache across sessions |
| capability change | re-evaluate visibility; selection kept if still valid | show disabled surfaces |
| KUP error | error state in the affected surface only; other surfaces keep data with stale flag | fall back to another source |

Keyboard: ⌘K search; ⌘1–3 destinations; ↑↓ list/tree; Enter inspect; Esc close; ⌘⇧F focus mode; visible focus rings;
reduced-motion honoured.

## 17. Design system

Dark theme primary (light theme tokens defined but not required for gate). Tokens: background 0/1/2 levels, border
subtle, text primary (≥ 7:1), text secondary/metadata (**≥ 4.5:1, measured**), accent blue (restrained), semantic
success/warning/danger used only for state and never alone. Typography: bundled Inter (UI) and JetBrains Mono (code),
13–14 px body, 12 px metadata at AA contrast, 16–18 px section titles, 22 px run title. Spacing 4/8 px grid. No remote
fonts, images or scripts (CSP as in M1). Status = icon + literal + colour. At most three coloured badges in one local
area; selection uses accent border and low-saturation background.

## 18. Security

Electron: contextIsolation on, sandbox on, nodeIntegration off, strict CSP, no remote content, navigation blocked;
preload exposes the typed allowlist only. Only Electron main spawns the `kriya` child, with argv arrays, the fixed
child environment and the configuration-directory cwd from M1. The renderer never receives a filesystem path it can
open; Open in IDE and blob reads are validated in main. Model I/O and prompt content are privacy-sensitive: off by
default, local-only, capped, never logged, never transmitted. No secrets in any envelope.

## 19. Acceptance criteria (gate checklist evidence)

1. M1 **KUP contracts and host boundary** unchanged; every existing KUP operation and fixture still passes its tests. The M1 `ui/shared` reducer is EXTENDED additively (M1 fields and actions are kept; the new shell never dispatches the M1 `selectComparison` auto-open path). The M1 inspector panels keep functioning until cockpit parity is demonstrated in the test host and the owner accepts it; they are then retired from the shipped UI and navigation, with no permanent hidden legacy route (owner decision Q-4).
2. The PNG is in `inputs/` and referenced as visual character only.
3. Both S1 and S2 appear in the matrix, the timeline, Changes, Context and WHY, with source badges.
4. The matrix classifies every datum any screen in this document displays.
5. No Electron process reads traces.db, snapshot db, JSONL, gzip blobs or evidence directories.
6. Every displayed object carries source operation, schema version, consistency kind and provenance.
7. The timeline tree is the only hierarchical navigator; rail and Attempts write only filter/selection.
8. Context, Changes and WHY render only recorded values and degrade by §12 and matrix §3.
9. Not recorded / Unavailable / Unsealed / DERIVED states are distinct and used.
10. No phase boundaries, timers, token rates, pending steps, Pause, Stop, New Run, terminal or "ask Kriya".
11. No cohort vocabulary in any run status, badge or filter.
12. Rail shows Runs, Workspace, Settings only until flags appear.
13. From a selected run, any gate output, diagnosis statement or candidate diff is reachable in two interactions.
14. Layout rules of §4.1 hold at 1180, 1360, 1440 and 1728 px in the test host.
15. Implementation phases (`05`) have no step that depends on a later step.
