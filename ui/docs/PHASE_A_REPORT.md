# Phase A report - feasibility on fixtures (stop point 1)

Date: 2026-10-04. Implementing agent: Claude. Authority: `handover/GUI-DESIGN-DISCUSSION/05_GATE.md` (A-1, A-2), then
`03_PROPOSAL_v2.md`; instructions `06_IMPLEMENTATION_INSTRUCTIONS_M1.md` §Phase A.

Standing lines: **No GitHub push. No live model run. No real `kriya` command run. Matrix protection (D-9) respected**
(fixtures and the fixture `kriya` stand-in only; nothing under `~/.kriya` or `~/kriya-cagc-v2`; no Kriya pytest suite;
no Maven/Gradle/Docker; `npm install` ran serially once).

Every claim below is tagged MEASURED (run or counted here), TRACED (read in the code, with `file:line`) or INFERRED
(a judgement, with the check that would settle it).

## A1 - zero-write SQLite measurement (D-4, P-29)

Full report: `ui/spikes/a1_zero_write/A1_REPORT.md`; raw data `results/results.json`, generated table `results/results.md`.

- **Interpreter (MEASURED):** `.newvenv/bin/python` = Python 3.14.6, SQLite 3.53.4; the `kriya` console script's shebang is
  the same interpreter, so the KUP adapter will run on the same SQLite (no mismatch finding). Kriya was reinstalled with
  `.newvenv/bin/pip install -e .` (no blanket upgrade); import-only check passed; no `kriya` command was run.
- **Detectors (MEASURED):** `sandbox-exec -f '(version 1)(allow default)(deny file-write*)'` (deprecated per its man page,
  still enforcing: EPERM + kernel `Sandbox: <proc>(<pid>) deny(1) file-write-<kind> <path>` lines), the unified log read
  WITHOUT sudo (`/usr/bin/log show`), attributed to the reader PID and classified by path (STORE / CASE_DIR / RUN_TMPDIR /
  DEVICE / UNRELATED = harness bug, exit 2), plus a before/after inventory (size, mtime_ns, sha256) on an unsandboxed
  twin run. `PYTHONDONTWRITEBYTECODE=1`, `-B`, `PRAGMA temp_store=MEMORY` (connection-local), harness-owned `HOME`/`TMPDIR`.
  Zero UNRELATED or RUN_TMPDIR denials in both runs; the only noise is `/dev/dtracehelper` at process start (DEVICE).
  `fs_usage` needs root and was not run; the exact sudo command is in the A1 report.
- **Query shapes (MEASURED):** capabilities (`sqlite_master`), `COUNT(*)`, history.list page 1 (`ORDER BY timestamp DESC,
  run_id DESC LIMIT 50`), page 2 by `(timestamp, run_id)` cursor, history.detail by `run_id`, history.prompt by `run_id`.

| state | sandboxed read | write attempted (by path) | unsandboxed: would write? | D-4 verdict |
|---|---|---|---|---|
| C01 rollback journal, clean | OK | none | no | READ_OK_ZERO_WRITE |
| C02 rollback, HOT journal | `SQLITE_READONLY_ROLLBACK` (typed, from SQLite) | none | no (same refusal) | READ_ONLY_UNAVAILABLE (`STORE_BUSY`) |
| C03 WAL, clean close, no sidecars | `SQLITE_CANTOPEN` | `file-write-create -wal` | **creates `-wal` and `-shm`** | READ_ONLY_UNAVAILABLE |
| C04 WAL, orphaned `-wal`+`-shm` | OK (heap wal-index fallback) | `file-write-data -wal`, `-shm` | **modifies `-shm`** | READ_ONLY_UNAVAILABLE |
| C05 WAL, active writer (20 ms commits) | OK, no lock wait | same as C04 | same as C04 | READ_ONLY_UNAVAILABLE |
| C06 rollback in 0555 dir | OK | none | no | READ_OK_ZERO_WRITE |
| C07 clean WAL in 0555 dir | `SQLITE_CANTOPEN` | `file-write-create -wal` | `SQLITE_READONLY_DIRECTORY` | READ_ONLY_UNAVAILABLE |
| C08 store deleted mid-read | OK (open fd serves pages 1-2, detail, prompt) | none | no | READ_OK_ZERO_WRITE (label by observation time) |
| C09 store missing | `SQLITE_CANTOPEN` at connect, nothing created | none | no | READ_ONLY_UNAVAILABLE (typed "no store") |

**The finding the owner must rule on (not a request to loosen D-4; D-4 is applied as written above).**
TRACED: Kriya opens every store through `kriya/core/db.py:9-20` (`wal_connect`: `PRAGMA journal_mode=WAL` monkey-patched
onto `sqlite3.connect`), so a real `traces.db` is a WAL file; after the last connection closes SQLite removes `-wal`/`-shm`
(standard behaviour, and exactly how fixture C03 was produced). MEASURED: SQLite 3.53.4 cannot open a WAL store read-only
without creating `-wal`/`-shm` (C03), and with sidecars present a plain `mode=ro` open writes `-shm` (C04/C05) unless an
external boundary denies it. Consequence: under D-4 with an in-process `sqlite3` read-only connection, the KUP adapter
would answer `READ_ONLY_UNAVAILABLE` for every WAL state (C03/C04/C05/C07) and read only rollback-journal stores, which
Kriya never produces. INFERRED (check: `ls -la` of the real state directory once D-9 is lifted): the owner's real store
sits in C03 or C04 today. Options, each a design decision outside this phase: (a) run the adapter's read inside an OS
write-denying boundary (`sandbox-exec` on macOS - deprecated, no portable equivalent): C04/C05 then read with zero writes,
C03 still fails; (b) revisit D-4 in a new topic (C03 needs sidecar creation or `immutable=1` to be readable at all);
(c) accept the refusals. Also measured and typed: hot journal -> `SQLITE_READONLY_ROLLBACK` with no write; missing store
-> `SQLITE_CANTOPEN` with no file created. `immutable=1` was NOT measured (D-4 forbids it for the adapter).

Fixture fix reported: harness run 1's "hot journal" had a zeroed header (no cache spill before the simulated crash) and
SQLite read the store without a write; run 2 spills the cache (`cache_size=10`, 300 uncommitted rows), header verified
`d9d5 05f9 20a1 63d7`, and SQLite refuses it typed. Run 1's table was overwritten by run 2 before the commit; its
outcome is preserved in the A1 report text (rule 20).

## A2 - UI prototype (gate A-1 Electron, A-2 plugin-ready)

### What was built (all under `ui/`, nothing under `kriya/`)

- `ui/shared` (`@kriya-ui/shared`): host-independent React 19.3 / TypeScript 5.9 panels - trust strip, Runs column
  (filter + virtualized listbox), Timeline (full goal header, attempt chips, virtualized event list in recorded order),
  Inspector (Context / Prompt / Output / Gates / Evidence tabs), bottom drawer (Diff / Why); selection reducer, request
  generations (stale responses discarded, failed refresh clears "current"), envelope check (`UNSUPPORTED_SCHEMA_VERSION`,
  `INVALID_RESPONSE`), availability rules (five states; an unknown value is shown literally, never as success), control
  character sanitizer, bounded line diff. ONE host boundary: `HostAdapter` (query, openInIde, copyToClipboard,
  get/setSetting, hostInfo). No Electron/Node import - enforced twice: ESLint `no-restricted-imports` (regex over bare
  specifiers) AND `ui/scripts/check-shared-deps.mjs` (package.json dependency allowlist `[react, react-dom]` + import scan).
- `ui/fixtures/generate.mjs`: deterministic synthetic KUP v1 fixtures (120 runs, 3 pages with equal timestamps, cursor
  pagination; `run-diff-2000` = 2,000-line recorded diff; `run-big-events` = 6.37 MiB detail with heavy JSON escaping
  (target was 4 MiB); unknown event fields + unknown status; incomplete context; every availability state; empty event
  list; empty prompt; every error code envelope).
- `ui/test-host`: plain-browser host (Vite) with `BrowserFixtureHost` (a fake `HostAdapter` over the fixtures;
  `?scenario=<ERROR_CODE>|schema2|garbage`). Its CI test renders EVERY special fixture through every panel in jsdom with
  no Electron anywhere (P-R3).
- `ui/standalone`: Electron **44.5.1** (pinned exactly; Chromium 152.0.7977.130). Hardening as data
  (`src/main/hardening.ts`) applied verbatim: `contextIsolation: true`, `sandbox: true`, `nodeIntegration: false`,
  `webviewTag: false`, `devTools: false`; strict CSP (`default-src 'none'; script-src 'self'; style-src 'self'; connect-src
  'none'; ...`) both as a header and a `<meta>`; `will-navigate`/`will-redirect`/`will-attach-webview` prevented,
  `setWindowOpenHandler` denies, every permission denied, `onBeforeRequest` cancels anything that is not `file://`.
  The preload exposes exactly the six typed messages (fixed channel constants, no generic `ipcRenderer` passthrough) and
  is bundled into one file (a sandboxed preload cannot `require` local files - MEASURED: `module not found: ./ipc_contract`
  on the first run; fixed with `vite.preload.config.mts`). The main process is the only spawner: argument arrays, no
  shell, stdin closed, minimal env, 60 s timeout, 8 MiB stdout / 1 MiB stderr limits, exactly the P-25 grammar
  (`kriya_argv.ts`, allowlisted prefixes). While D-9 holds the executable is `fake-kriya/fake_kriya.mjs` (speaks the P-25
  grammar over the fixtures) unless `KRIYA_UI_ALLOW_REAL_KRIYA=1` AND a configured executable - that gate is checked by
  a test. "Open in IDE": fixed editor table, path must be an existing regular file inside the selected workspace
  (realpath + path relation, symlink escapes rejected), line a positive integer; no command text from run data.

### Tests (MEASURED, all green at the time of this report)

| package | tests | what they prove |
|---|---|---|
| shared | 26 | envelope acceptance/refusal, availability rules, unknown-field preservation, sanitizer, 2,000-line diff < 200 ms, selection reducer, stale-response discard, every panel renders with a fake host in every availability state, prompt fetched only on request, open-in-IDE goes through the host, palette contrast >= 4.5:1 (computed, both schemes) |
| test-host | 20 | every generated fixture is a valid v1 envelope; big detail > 4 MiB and < 8 MiB; every special fixture renders through all panels (<2 s each in jsdom); unknown status/fields shown literally; every error code shown as a typed, non-current list |
| standalone | 28 | every hardening setting; preload allowlist shape; no shell/exec/listener/Kriya-state access in `src/main`; D-9 gate present; request validation refuses 16 injection shapes in run_id and cursor (incl. leading dashes); argv builder = P-25 grammar, allowlist; open-in-IDE containment (.., symlink, absolute outside, directory, missing, line 0); process runner against the stand-in: valid envelopes for all five operations, >4 MiB passes, oversize -> `RESPONSE_TOO_LARGE`, garbage -> `INVALID_RESPONSE`, hang -> killed at the timeout, schema 2 refused, out-of-grammar argv refused before spawn; settings store |

Two tests failed against my first implementation and found real defects, both fixed (rule 7): a cursor starting with
`-` was accepted (would reach the CLI as `--cursor --help`), and the stand-in truncated large stdout by exiting before
the pipe drained (a fake bug, not a product one).

### P-35 measurements (MEASURED in the real Electron, fixtures; `standalone/measurements/measure-2026-10-04T04-29-17-837Z.json`)

Host: macOS 26.7 arm64; Electron 44.5.1. `app.getAppMetrics()` covers all four processes (Browser, GPU, Utility, Tab);
`ps` RSS of the same PIDs is the independent cross-check (481 MB vs 475 MB app-metrics total at the end - consistent).
Activity Monitor was not read by the agent (GUI); the owner can cross-check with `npm start -w @kriya-ui/standalone`.

| target | result |
|---|---|
| idle memory (3 s after load, before any selection) | **393 MB** total working set (Browser 158, GPU 77, Utility 46, renderer 110). Above the gate's accepted "about 100-300 MB" estimate - **shortfall to note** (A-1 accepted cost was an estimate; this is the measured number for this app) |
| memory flat over 100 selection cycles | pre-GC working set 504 / 495 / 505 MB at 10 / 50 / 100 cycles; **post forced GC (DevTools `HeapProfiler.collectGarbage`) 476 / 475 / 479 MB** and 475 MB idle afterwards: flat within 4 MB -> the first run's monotonic rise (390 -> 604 MB without GC) was GC lag, not retention. Classified `EXPECTED_BOUNDED_CACHE` (rule 21). Note `performance.memory.usedJSHeapSize` reads a constant 9.5 MB: Chromium quantizes that legacy API, so it is not evidence either way |
| no main-thread freeze > 200 ms | PerformanceObserver `longtask`: 0 entries; rAF gap monitor: 1 gap of 106 ms, none over 200 ms, across 100 selections including the 6.37 MiB payload and the 2,000-line diff |
| selected fixture populated within 2 s | max 168.9 ms (big-events), p95 86 ms, median 76 ms; 0 over 2 s |
| keyboard-only selection | focus on the runs listbox, Home + 3x ArrowDown -> `aria-posinset=4` selected and the timeline header shows that run; Tab moves to the next control with a visible 3 px solid `:focus-visible` outline |
| focus / names | 51 interactive controls, 0 without an accessible name; roles present: region, 2 listboxes, 30 options, 2 tablists, 7 tabs, 5 tabpanels, group, searchbox |
| contrast | computed WCAG ratios for text/muted/ok/bad/warn on bg/panel/selection and text on diff add/del, light and dark: all >= 4.5:1; focus ring 6.4:1 light / 8.2:1 dark (test `shared/test/contrast.test.ts`) |
| resizing | at 800 px: inspector hidden, Timeline/Inspector toggle shown, two columns, no horizontal overflow; at 1400 px: 22% / 48% / 30% columns (308 / 672 / 420 px) as P-21 |
| 2,000-line diff | exact LCS diff in < 200 ms (test) and rendered virtualized (screenshot) |
| 4 MiB event JSON with escaping | 6.37 MiB detail flows stand-in -> 8 MiB-bounded runner -> IPC -> panels; populated in 169 ms |
| unknown fields / incomplete context / availability states | rendered literally / with "not recorded" reasons; covered by tests in both hosts |
| **VoiceOver** | **NOT measured** (needs a human at the machine). Roles/names/keyboard are in place; owner check requested |

Screenshots: `standalone/measurements/screenshot-wide-*.png` (run-diff-2000 selected, Diff drawer open) and
`screenshot-narrow-*.png` (800 px).

### Open in IDE spike (MEASURED)

- VS Code: `code -g /abs/path/file.txt:7` exit 0, VS Code CLI 1.140.0, VS Code focused. The cursor LINE could not be read
  programmatically (osascript lacks assistive access) - **owner to confirm visually once**. Marked `verified: true` in the
  editor table with that note.
- IntelliJ IDEA and Eclipse: not installed on this Mac (owner-confirmed). Kept in the editor setting as
  `verified: false` with their documented forms (`idea --line N file`; `eclipse file`, no line argument) - never executed.
- Local-model repeat of the memory measurement: deferred until the owner lifts D-9 (Ollama untouched).

## Deviations from 03/05/06 (each with a reason; none changes the design)

1. The big-events fixture is 6.37 MiB rather than "4 MiB" - exceeds the target while staying under the 8 MiB limit.
2. `fs_usage` was not run (root); replaced by the unified-log denial attribution the owner asked for, and the sudo
   command is provided.
3. The A2 hardening test (06 lists it under Phase E) is already in place in Phase A because A-1 required the hardening
   in the prototype.
4. Measurement evidence (JSON + PNG) is committed under `ui/standalone/measurements/` (rule 20), not git-ignored.

## Sync with main (Repository strategy item 1)

`git fetch` of `/Users/sriramnanduri/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode` `refs/remotes/origin/main`
-> `61a867fc31a7b5ff5d7f21d1ea15e4eac43f2e03`, already the base of this branch: nothing to merge, no conflicts. No GitHub
remote was added or used; the main checkout was only read.
