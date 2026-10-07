# 02a — Owner requirements (input to `03_PROPOSAL_v2.md`)

**Status:** owner input from Sriram, drafted by Claude on his instruction.
**Date:** 2026-10-04.

This is **not** a third review. These are owner requirements, and they change the scope. The proposer must address every `OR-*` item in `03_PROPOSAL_v2.md`, alongside the findings R1-1 to R1-8 in `02_REVIEW_1.md`.

## Owner requirements

- **OR-1 — Two delivery forms from day one.** The GUI must work as **(a) an independent standalone app** and **(b) an Eclipse plugin**, with Eclipse first. Neither may need a fork of the UI logic or of Kriya.
- **OR-2 — The best layout for a code-generating harness**, not a wrapper around CLI output (see "Layout" below).
- **OR-3 — Kriya stays the only authority.** Front ends ("shells") render data and send requests. Every action goes through the same Kriya code paths and checks as the CLI. This keeps P-2, P-3 and P-5 from `01_PROPOSAL.md` in force.

## Consequences for review 1

| Finding | Effect of OR-1 to OR-3 |
|---|---|
| R1-1 (read-only JSON contract) | **Required.** It becomes the first version of the protocol described below. |
| R1-2 (inspection writes nothing) | Unchanged. It applies to the contract and the protocol server. |
| R1-3 (technology choice) | **Settled in part.** Tk is **ruled out**: it cannot be embedded in Eclipse. Static HTML alone is too limited for an IDE. The shape below replaces the three-way comparison. The proposer still owns the details and the prototype. |
| R1-4 to R1-8 | Unchanged. |

## Recommended architecture (protocol-first, the same approach as LSP)

```
┌───────────────────────────── Shells (no authority) ─────────────────────────────┐
│  Standalone app                       Eclipse plugin                 (later)     │
│  web UI (TypeScript/React)            native SWT/JFace views         VS Code /   │
│  in a browser or a Tauri window       + embedded web panels          IntelliJ    │
└──────────────┬──────────────────────────────┬────────────────────────────────────┘
               │      Kriya UI Protocol (KUP): versioned JSON, JSON Schema         │
               │   types generated for both TypeScript and Java from one schema    │
┌──────────────┴──────────────────────────────┴────────────────────────────────────┐
│  M1: read-only `--json` CLI commands (R1-1/R1-2).                                │
│  M2: `kriya ui-server --stdio` (JSON-RPC over stdio, like LSP; no network port). │
│  M3: actions call the same code paths and checks as the CLI (separately gated).  │
├──────────────────────────────────────────────────────────────────────────────────┤
│  Kriya core (unchanged): workflow, gates, SEC-009, state                         │
└──────────────────────────────────────────────────────────────────────────────────┘
```

**Rules for this architecture:**

- **A-1 — One contract.** KUP is defined once, as JSON Schema with a `schema_version`. The TypeScript types and the Java types are generated from that schema. A shell checks the version and refuses, with a typed message, any version it does not support.
- **A-2 — Stdio, never a network port.** The shell starts the Kriya process as a child and talks to it over stdio, the same way Eclipse LSP4E starts language servers. No socket, no login token, no cross-origin surface.
- **A-3 — Shells hold no authority.** No shell imports Kriya internals or reads Kriya's SQLite or state files directly. This is enforced by import and dependency tests in each shell.
- **A-4 — Eclipse: native and embedded parts mixed.**
  - **Native:**
    - the Runs view (JFace table);
    - diffs in **Eclipse's own Compare editor**;
    - gate failures as **problem markers** on the real files;
    - double-click jumps to file and line.
  - **Embedded** (through the SWT `Browser` widget; WebKit on macOS): the shared web components for the rich panels (run timeline, context inspector). Each panel is built once and used in both shells.
- **A-5 — Packaging.**
  - Standalone: the web front end plus a small launcher. Tauri is preferred over Electron (smaller, no Node runtime shipped), or the user's browser opened on a local file. The proposer evaluates both.
  - Eclipse: a feature plus an update site, targeting Java 17+ and a current Eclipse release.
  - Neither shell is imported by `kriya/` (R1-7).

## Layout

```
┌ Kriya ─ workspace: petclinic ─ state: ~/.kriya ─ models: coder✓ planner✓ ─ ● RUN ACTIVE ┐
├──────────────┬────────────────────────────────────────┬────────────────────────────────┤
│ RUNS         │ RUN TIMELINE  (goal, status, duration) │ INSPECTOR  [tabs]              │
│ ▸ filter     │ ● Analyze                              │ Context │ Prompt │ Output │    │
│ ✔ 10:02 chop │ ● Plan ── 2 subtasks (approved)        │ Gates │ Evidence               │
│ ✖ 09:41 xml  │   ├ ST1 ClinicServiceImpl.java         │                                │
│ ⧗ 09:10 ...  │   │  ● Context: 3 files shown,         │ Context package for attempt 2: │
│              │   │    1 omitted (minimum_authority_   │  ✔ ClinicService…  member_exact│
│              │   │    unfit) ⚠                        │  ✖ _urls.py  5,443 > 5,094 tok │
│              │   │  ● Attempt 1 ✖ regression_unattr.  │  guidance: spring_xml.dev.* 34t│
│              │   │  ● Attempt 2 ✔ gates 5/5           │  budget ████████░░ 82%         │
│              │   └ ST2 tools-config.xml               │                                │
│              │ ● Review   ● Commit ✔                  │                                │
├──────────────┴────────────────────────────────────────┴────────────────────────────────┤
│ DIFF / LOG  (Eclipse: Compare editor)   │ WHY: first incorrect state → cause → evidence│
└────────────────────────────────────────────────────────────────────────────────────────┘
```

(This is an illustration. The values come from earlier CAGC analysis, not from a real GUI.)

**Layout principles:**

- **L-1 — The run timeline is the main view, not a chat.** It follows Kriya's stages: analyze → plan → subtasks → context → attempts → gates → review → commit.
- **L-2 — "What did the model see?" is a first-class view.** For each attempt it shows:
  - files shown, with their tier (`member_exact`, full, excerpt);
  - files omitted, with the reason (`budget_exhausted`, `minimum_authority_unfit` …);
  - the rendered capability guidance;
  - the token budget.

  Context defects caused the hardest recent failures, so this view must be exact. It shows recorded values only; nothing is inferred.
- **L-3 — The why-panel shows failures causally:** first incorrect state → cause → evidence. It uses the same categories as the PROTOCOL_v2 classification (PLANNING / CONTEXT / DEVELOPER_OUTPUT / VERIFICATION / FALLBACK / BENCHMARK / INFRASTRUCTURE). It shows only what Kriya recorded and never guesses a cause.
- **L-4 — Trust signals are always visible:**
  - the model identity and qualification status;
  - the approval / SEC-009 state;
  - whether a run is active (RUN_ACTIVE);
  - **which state store** the history comes from (R1-4).
- **L-5 — The inspector behaves the same in both shells**, so moving between Eclipse and the standalone app needs no relearning.

**Data dependency:** L-1 to L-3 need fields the CLI does not expose today: the run events, evidence records, gate outcomes, model hops and failure report stored in `runs`, plus the context-package and omission records. Which of these are already persisted, and which would need new read-only output, must be TRACED in v2. No field may be inferred to fill a gap.

## Milestones

- **M1 (read-only):**
  - the KUP v1 contract as `--json` CLI commands (R1-1/R1-2);
  - the Eclipse plugin: Runs view, timeline and inspector as embedded web panels, diffs in the Compare editor, markers;
  - the standalone shell on the same web components;
  - fixture-based tests only while the CAGC 80-run matrix is running (R1-5).
- **M2:** `kriya ui-server --stdio` for live progress while a run is active.
- **M3:** approvals and actions through KUP, with exactly the CLI's checks, under a separate gate.

## Caution

Eclipse's user base is shrinking; VS Code and IntelliJ are much bigger. Starting with Eclipse is a deliberate owner choice. The protocol-first design (A-1 to A-3) is what keeps adding those IDEs cheap later. An Eclipse-only design would make that choice expensive to change.

## What `03_PROPOSAL_v2.md` must answer for this input

1. A response to OR-1 to OR-3, A-1 to A-5 and L-1 to L-5: ACCEPTED, REJECTED with a reason, or DEFERRED.
2. A TRACED inventory of the persisted data behind each panel (the data dependency above).
3. The KUP v1 schema outline: the queries, their fields and `schema_version`.
4. The Eclipse prototype criteria:
   - the SWT Browser on macOS renders the shared timeline panel;
   - the Compare editor shows a recorded diff;
   - a marker is placed from a recorded gate failure;
   - all of it from fixtures.
5. Updated owner questions for `05_GATE.md`.
