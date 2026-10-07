# 06 — Implementation instructions: M1, the standalone Kriya run inspector

**For:** the implementing agent.
**Authority:** `05_GATE.md` (binding, **including its amendments A-1 Electron and A-2 plugin-ready**), then `03_PROPOSAL_v2.md`. Cite claim numbers (`P-n`) and decision numbers (`D-n`) in commit messages and reports.
**Workspace:** `/Users/sriramnanduri/WorkingDirectory/AI/AntiGravity/Kriya-By-Antigraviry/tmp/kriya-demo1-attribution-fix-2`.
**Branch:** `codex/fix-demo1-attribution`.
**Baseline:** `61a867fc31a7b5ff5d7f21d1ea15e4eac43f2e03`.

## 0. Rules (for the whole of M1)

1. **Never push to GitHub.** Commits are local only (D-10).
2. **Matrix protection (D-9), until the owner lifts it in writing:**
   - fixtures and fake child processes only;
   - no real `kriya` invocation;
   - no live model;
   - no paths under `~/kriya-cagc-v2/` or `~/.kriya`;
   - full test suite at `-n 2` at most;
   - no Maven, Gradle or Docker builds.
3. **Kriya-side changes are limited to D-3:** the read adapter and the KUP CLI additions. Nothing else in `kriya/`. Keep them in separate, self-contained commits whose messages start with `KUP:`.
4. **UI code lives only under `ui/`** (P-34). `kriya/` never imports `ui/`, and a test checks that.
5. **Shells have no authority (P-31):**
   - no shell commands built from strings;
   - no reading of Kriya state;
   - no network listener;
   - no external resources;
   - only the allowlisted command lines.
6. **Every claim in a report is tagged** MEASURED, TRACED (with `file:line`) or INFERRED (with the check that would settle it). Nothing is called done without evidence.
7. **Stop points (§ "Stop points" at the end):** stop there and report to the owner. Do not continue past one on your own judgement.

## Repository strategy (owner decision, 2026-10-04)

All phases are built in this AntiGravity checkout. The Kriya-side work is merged into the main Kriya repository (`/Users/sriramnanduri/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode`) **after Phase C**. The `ui/` work stays here until the owner decides otherwise.

1. **Sync at each stop point.**
   - Bring the main repository's latest `origin/main` into `codex/fix-demo1-attribution` through a **local fetch from that folder**. Never use GitHub, and never add a GitHub remote.
   - Resolve conflicts while they are small.
   - Report the synced commit id and any conflicts.
2. **Merge the Kriya-side commits early.**
   - After Phase C passes, hand the `KUP:` commits (adapter and CLI additions only) to the owner, as a list of commit ids or a patch series, for merging into the main repository.
   - Do not wait for the end of M1. The adapter must join main before the planned telemetry work changes the trace schema.
3. **Timing and coordination.**
   - That merge happens only **after the CAGC 80-run matrix has finished**, and only when the owner has coordinated with the other agent session working in the main repository.
   - Do not write to the main repository yourself unless the owner explicitly asks.
4. **Re-verify after the merge, in the main repository:**
   - the KUP tests;
   - the byte-identical CLI golden check;
   - the `sandbox-exec` zero-write test;
   - the full suite at low parallelism.

   Green results in this checkout are not evidence for the merged code.
5. **No GitHub push, anywhere, at any step.**

## Phase A — Feasibility, fixtures only (timebox: 3 days)

### A1. Zero-write SQLite measurement (D-4, P-29), about half a day

**Fixture stores (synthetic, under a temporary directory):**
- a rollback-journal database;
- WAL mode with no side files;
- WAL with `-wal` and `-shm` present;
- WAL with an **active fixture writer process**, a small script and not Kriya;
- a read-only directory;
- the file disappearing while it is being read.

**Read candidate:** Python `sqlite3` with `file:…?mode=ro` and `uri=True`. No WAL pragma, no `db.wal_connect`.

**Write detection:** run each read under **`sandbox-exec` with `(deny file-write*)`** on macOS, plus `fs_usage`/`opensnoop` or an equivalent tracer. Inventories alone are not enough (P-29).

**Record (MEASURED):**
- the outcome per case: read OK, or the SQLite error;
- any attempted write;
- the macOS version and the `sqlite3.sqlite_version` on the owner's Mac.

**Outcome rule (D-4):** any case that cannot be read without writing → `STORE_BUSY` / `READ_ONLY_UNAVAILABLE`. Do not loosen this.

### A2. UI prototype

- React/TypeScript panels running in an **Electron** shell (gate A-1 hardening) on macOS arm64. Fixtures only.
- The same panels must also render in the plain-browser **test host** with a fake `HostAdapter` (gate A-2, P-R3).
- Measure the Electron app's idle and active memory (MEASURED), and report it. Repeat the measurement with a local model loaded **only after** the owner lifts matrix protection (D-9). Until then, do not touch Ollama.
- The layout follows P-21 and `02a` L-1 to L-5:
  - trust strip;
  - Runs column;
  - Timeline;
  - Inspector (Context / Prompt / Output / Gates / Evidence);
  - bottom drawer (Diff / Why).
- **Exercise:**
  - a 2,000-line diff;
  - a 4 MiB event-JSON payload (with escaping);
  - unknown event fields;
  - incomplete context;
  - every availability state (`recorded`, `not_recorded`, `unreadable`, `excluded`, `unsupported`).
- **Measure against the P-35 targets:**
  - VoiceOver;
  - keyboard-only selection;
  - focus;
  - contrast;
  - resizing;
  - no main-thread freeze longer than 200 ms;
  - memory stays flat over 100 selection cycles;
  - a selected fixture appears within 2 s.
- **Open in IDE spike:**
  - verify each editor's real command-line or URL form on the owner's Mac (VS Code `code -g file:line`, IntelliJ `idea --line N file`, Eclipse);
  - record which work (MEASURED);
  - no hard-coded assumptions.

**→ STOP POINT 1:** report A1 and A2 with measurements and shortfalls. Proceed only with owner approval.

## Phase B — KUP v1 contract

1. `ui/kup/`: JSON Schema for the envelope (P-24) and the five operations (P-25).
   - **Error codes:**
     - `UNSUPPORTED_SCHEMA_VERSION`;
     - `INVALID_RESPONSE`;
     - `STORE_BUSY`;
     - `READ_ONLY_UNAVAILABLE`;
     - `RESPONSE_TOO_LARGE`;
     - `CONFIG_AUTHORITY_REFUSED` (D-7).
   - Each optional panel has an availability field (P-24).
2. Generate the **TypeScript** types. Also run a CI check that **Java** types can be generated from the same schema; this keeps the contract usable by a future Java host (gate A-2, P-R4). No Java host is built.
3. Add the **host contract** schema (`HostAdapter` messages) to `ui/kup` (gate A-2, P-R2).
4. Golden fixtures that round-trip through TypeScript and the generated Java types. Unknown event fields are preserved, never dropped (P-24).

## Phase C — Kriya inspection adapter and CLI additions (D-3)

1. **Command grammar, exactly as P-25:**
   - `traces --capabilities --json`;
   - `traces --json -n N [--cursor C]`, default 50, maximum 200;
   - `traces --json --run-id ID`;
   - `traces --json --run-id ID --include-prompt`;
   - `runs status --workspace PATH --json --kup-version 1`.
2. **The KUP path, per P-28/P-29 and the result of A1, must not:**
   - bootstrap logging;
   - migrate or create schemas;
   - run `wal_connect`;
   - create directories;
   - copy legacy data;
   - initialize plugins or models;
   - write bytecode.

   It does preserve SEC-009 config loading and state-path resolution.
3. **Data handling:**
   - `prompt_rendered` only with `--include-prompt`;
   - stored values verbatim;
   - timestamps exactly as stored;
   - `files_modified` as the raw string (P-30).
4. **Tests:**
   - **Byte-identical:** existing CLI output with no KUP flags, as a golden test against the baseline.
   - Each error code.
   - Pagination with equal timestamps.
   - Injection inputs to `--run-id` and `--cursor`.
   - The response-size limit.
   - **Zero writes:** every KUP command run under `sandbox-exec` deny-write against fixture stores.
   - A mutation campaign on the adapter (at least 5 meaningful mutants, all killed).
   - The full suite at `-n 2`, with 0 failures.

## Phase D — Shared panels (`ui/shared/`)

- Selection reducers, normalization and availability rules live here, and **only** here, so any future host can reuse them.
- **No Electron or Node imports in `ui/shared`.** Every host capability goes through the typed `HostAdapter` (gate A-2, P-R1). A lint or dependency check enforces this.
- **Rendering rules:**
  - output is rendered as text, with control characters sanitized;
  - no external navigation;
  - stale responses are discarded by request generation (P-32);
  - a failed refresh clears the "current" status.
- **The Context panel** shows recorded paths, tiers, member ids and omissions with reasons, and token accounting, with estimated and provider-reported counts kept separate (P-23).
- **The Prompt, Output, Diff and Why panels** show "not recorded" wherever P-30 says the data is not persisted (D-5). Nothing is reconstructed.

## Phase E — Electron standalone shell (`ui/standalone/`)

- The user chooses the Kriya executable. It is spawned with argument arrays, no shell, stdin closed, the Phase C grammar only, a 60 s timeout, and limits of 8 MiB stdout and 1 MiB stderr (P-31/P-32).
- **Electron hardening (gate A-1):**
  - `contextIsolation`, `sandbox`, no `nodeIntegration`;
  - a strict CSP; no remote content;
  - navigation and new windows blocked;
  - a preload that exposes only the typed `HostAdapter` messages;
  - process spawning only in the main process.
  - A test checks every one of these settings.
- **Packaging:** a macOS arm64 build, local only. No auto-update server, no publishing.
- **"Open in IDE":**
  - only for paths that exist inside the selected workspace;
  - only the editors verified in A2;
  - the editor choice is a user setting;
  - no command text ever comes from run data.
- **Trust strip:**
  - Kriya identity;
  - history-store path;
  - workspace;
  - RUN_ACTIVE;
  - recorded model and qualification, or "unknown";
  - observation time.

## Phase F — Acceptance

1. **Fixture acceptance (allowed now):** every item in P-15 and P-35, plus the Phase C tests, in the Electron shell **and** the plain-browser test host (P-R3).
2. **Real-CLI acceptance (only after the owner lifts D-9):**
   - in a **disposable** workspace with its own state, log and authority roots;
   - with network denied;
   - with the write-detection harness from A1;
   - never against operator data.

**→ STOP POINT 2:** the M1 acceptance report. M1 is done only when both F1 and F2 pass (P-37).

## Reporting (at each stop point and at the end)

- What was built, with commit ids (local only).
- Tests: counts, pass/fail, and mutation results.
- Measurements against each target.
- Deviations from `03_PROPOSAL_v2.md` / `05_GATE.md`, each with a reason. A deviation that changes design needs owner approval **before** you implement it.
- Explicit lines: "No GitHub push." "No live model run." "Matrix protection respected."

## Stop points

- **Stop point 1:** after Phase A, the feasibility measurements and the prototype. Includes a sync with main (Repository strategy, item 1).
- **Phase C hand-off:** the `KUP:` commits handed to the owner for merging into main (Repository strategy, items 2 to 4).
- **Stop point 2:** after Phase F, M1 acceptance. Includes a sync with main.
- **Also stop immediately and report if:**
  - a gate condition cannot be met as written;
  - a Kriya change outside D-3 seems necessary;
  - a test has to be weakened to pass.
