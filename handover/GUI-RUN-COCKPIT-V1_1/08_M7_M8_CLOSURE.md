# M7 / M8 - Productization, validation and closure (autonomous run)

Branch `gui/run-cockpit-v1_1`, starting from accepted M6 `9767f7d`. This document is filled in as the work is done; every number in it was measured in this run.

## M7.14 - M1 inspector parity table (TRACED from the M1 panels and tests)

| # | Accepted M1 capability | Cockpit | Class |
|---|---|---|---|
| 1 | Trust strip: capabilities, snapshot id, state/trace path, configuration directory + source, workspace recovery status | Trust module (store health), Workspace page (state dir + source, recovery status, run-active), Settings (configuration directory) | cockpit equivalent |
| 2 | Acquire a snapshot (explicit); verify digest at pin; never read an unverified snapshot | Refresh runs (explicit acquisition); same pin-time verification (session); refused snapshot never read | equivalent |
| 3 | Choose another published snapshot | Pinned-snapshot picker on the Workspace page (`session.choose`, re-verifies, clears selection) | **gap closed in M7** |
| 4 | Runs column, status text literal, status filter | Runs module (literal status text), filter box for long lists | equivalent (filter added M7) |
| 5 | Timeline with search/authority/attempt filters | Timeline (unit/attempt/phase) + Evidence module server-side filters (kind, role, phase, attempt, unit) | stronger replacement |
| 6 | Inspector tabs Context / Output / Gates / Evidence | Context, Plan, Validation, Attempts, Evidence modules, each with recorded availability | stronger replacement |
| 7 | Stored prompt text (`history.prompt`, explicit, sensitive) | Context module shows what the model was shown per call through `evidence.call_content` (authorized, per call, digest-verified) | stronger replacement for runs WITH an evidence store; **missing for pre-recorder trace rows** (no evidence store; M1 could show the trace row's stored prompt) and, **by design, whenever Kriya's `content_visibility` is off (the packaged default)**: Context and Changes then state "denied by Kriya's content visibility policy" instead of showing prompt/diff text, which M1's `history.prompt` did not gate. This is the M3 security narrowing (Kriya keeps content authority; a host may only narrow it), not a regression. Recorded as GUI-M1-STORED-PROMPT-001 (P3) |
| 8 | Drawer: diff, why, raw events | Changes (authorized diff), WHY, Raw Events drawer | equivalent / stronger |
| 9 | Comparison / attribution | Attempts A/B comparison, WHY attribution | stronger replacement |
| 10 | Open file in IDE | Changes: Open in editor (host `openInIde`, resolved against the open workspace) + editor choice in Settings | **gap closed in M7** |
| 11 | Copy payloads to the clipboard | Changes: Copy diff / Copy content (after Kriya served it) | **gap closed in M7** (diff/content; raw record JSON is selectable text) |
| 12 | Settings dialog (editor, executable, configuration directory, recovery workspace) | Settings: editor, configuration directory, Kriya executable. The recovery workspace is chosen with Open Folder | equivalent (workspace by design) |
| 13 | Recovery assessment of a typed workspace | Open Folder + `workspace.describe` (recovery status, run-active) | stronger replacement |
| 14 | Availability states stated literally (never invented) | Same vocabulary, shared core | equivalent |
| 15 | Keyboard/a11y patterns (tabs, listbox, dialog focus) | Cockpit shell tests (rail labels/tooltips, Escape, no positive tabindex), module tests | equivalent |

**Verdict:** parity reached except item 7 for pre-recorder rows, which is a documented legacy-data limitation, not a workflow capability (new runs always have an evidence store unless the operator turns capture off, and then the Context module says so). The inspector is retired from the shipped UI.

## M7.14 / M8.12 - Retirement (done)
- The product build is the cockpit: `npm run build` / `package:mac` produce it; `ui-mode.json` says `cockpit`.
- The M1 inspector exists only behind the explicit legacy build `npm run build:inspector-legacy` (`VITE_KRIYA_UI=inspector`) used by the M1 evidence tests. There is no runtime route (no URL, setting or storage switch; a test pins this).
- `scripts-check-bundle.mjs` now fails a product bundle that contains M1 inspector UI strings (proven: the legacy build fails it, the product build passes).
- The M1 read contracts, `ui/test-host` and the M1 panel tests are kept. The inspector-build compatibility for the typed Recovery workspace remains only for that legacy build.

## M7.1 - native Open Folder: NATIVE_DIALOG_CLICK=UNVERIFIED_PERMISSION_BLOCKED
The packaged `Kriya.app` was launched with a throw-away user-data directory and its `File > Open Folder…` item was invoked through macOS Accessibility (the item exists, is enabled, and is the only way the chooser opens). Clicking INSIDE the macOS open panel was not possible: during the whole autonomous run the macOS session was locked (`CGSSessionScreenIsLocked = true`, display black, `windows = 0` for every app), and unlocking requires the owner's password. Nothing was faked: the screenshots of earlier milestones replace only `dialog.showOpenDialog`; every step after it is the production path; the packaged app's native-dialog click remains for the owner to do once (File > Open Folder…, Cmd+O, Cancel, then choose a disposable folder).

## M7.2 / M8.8 - product identity (PASS, via Accessibility on the packaged app)
`ui/standalone/verify-identity-ax.sh out/Kriya.app` launches the PACKAGED application and reads what a person sees (evidence/m7/identity-ax.txt):
- bundle `Kriya.app`, executable `Kriya`, `CFBundleName`/`CFBundleDisplayName` = Kriya, identifier `com.kriya.app`;
- processes: `Kriya`, `Kriya Helper` (x2), `Kriya Helper (Renderer)`; no process or file named Electron;
- menu bar: Apple | Kriya | File | Edit | View | Window; Kriya menu: About Kriya, Hide Kriya, Quit Kriya; File: Open Folder…; Window menu lists the window "Kriya";
- the script fails on any "Electron" text; after "Quit Kriya" no process and no orphan `kup-stdio` remains.
`node mac-app.mjs verify` passes with one warning only: no approved icon (below). The window title and menu come from the native layer (`identity.ts`), not from HTML.

## M7.3 - icon (unchanged, nonblocking)
One repository search (tracked files, `ui/standalone/assets`): no approved Kriya icon exists. Not invented. `GUI-APP-ICON-001` stays DEFERRED.

## M8.4 - the one independent review (verdict and disposition)
One fresh reviewer (read-only) on the whole GUI branch at `bfff398`. Verdicts: 1 YES, 2 YES, 3 YES, 4 YES, 5 PARTIAL (fixed), 6 NO unsafe convenience added, 7 YES, 8 PARTIAL (documented), 9 YES, 10 PARTIAL (icon only), 11 one P1 and one P2 (both fixed). No second broad review was run.
| Finding | Disposition |
|---|---|
| P1-1 "Approval required" shown for a run that did not stop (skill gap / lookup callbacks abort but the workflow continues) | FIXED (45b3789): those four decisions are declined and recorded informationally; a RunRecord SUCCESS wins over any note; only a decision that stops the run is "Approval required". Tests: python tripwire + records, shared outcome table, fixtures |
| P2-1 closing the window during a run left no window and no way back | FIXED: close asks the interrupt question and keeps the window until answered |
| P3-1 launch stuck in STARTING polled forever | FIXED, plus a race the reviewer's finding exposed (a status read and a process-exit read straddling the process's own final write could read as "interrupted"): conclusions drawn from silence need two agreeing polls |
| P3-2 parity table understated prompt/diff gap | DOCUMENTED (content_visibility default off is the M3 narrowing) |
| P3-3 quit during the preflight round trip could orphan a run | FIXED (`closing` flag) |
| P3-4 renderer-settable executable | FIXED: absolute path required (host-only settings remain a design item) |
| P3-5 `git status` could run a repository filter driver | FIXED and proven (test fails without the fix) |
| P3-6 polling re-verifies the whole evidence chain | DEFERRED `GUI-EVIDENCE-POLL-COST-001` (no verification weakening allowed) |
| P3-7 packaging did not enforce the bundle check | FIXED |
| P3-8 stray `.bak`, unpruned launch records | `.bak` removed; `GUI-LAUNCH-RETENTION-001` DEFERRED |

## M8.6 - the one live GUI smoke (run `20261010T224052-e0941ceb`): PARTIAL - real workflow reached, stopped by the HOST's git
- Setup: the PACKAGED `Kriya.app`, packaged-default configuration (planner/architect/developer `qwen3-coder:30b`, fallback `qwen3.6:35b-a3b-q4_K_M`; no profile or Ollama change; Ollama was idle), a disposable brownfield project `hello-project` (`hello.py` with `greet`, two pytest tests, one commit) outside every repository, `KRIYA_STATE_DIR` a scratch directory (nothing added to the owner's history). The scripted chooser replaced only the native dialog call (session locked, see M7.1).
- What the GUI did (evidence/m8/live-smoke/): welcome -> workspace with Git facts (branch main, revision, clean) -> preflight (config source, state dir, models, tools) -> goal entered -> Start Run: `startRun launched 20261010T224052-e0941ceb in 151 ms` -> banner with the run id, elapsed time and observed record counts while running -> terminal banner **Run failed**, `process exit code 1`, source Kriya RunRecord, `liveness ended (DERIVED)`; workspace facts re-read (trace database and .kriya now present; 1 changed entry).
- What Kriya recorded (durable): launch record phase history STARTING -> RUNNING -> ENDED, workspace identity + goal digest (no goal text); RunRecord FAILURE / NOT_COMMITTED; evidence store **VERIFIED, sealed, 25 records**: `run.opened`, two `unit.opened`, **real planner and architect model calls** (`model.request/response/result`, qwen3-coder:30b, finish_reason stop), `unit.closed` x2 (EXCEPTION), `run.closed FAILURE`.
- Why it stopped (Kriya's own typed words): `Failed to create an isolated generation sandbox: Git repository detection/bootstrap failed ... You have not agreed to the Xcode license agreements`. On this Mac `/usr/bin/git` is the Xcode stub (exit 69) and is FIRST on the host's fixed child PATH. This is the host environment, not a model result and not a GUI defect; no false success was shown.
- GUI pass criteria: responsive (the capture drove 27 status polls of 17-93 ms), workspace correct, goal admitted, real workflow launched, launch/run id durable and workspace-linked, evidence appeared, terminal status honest, no authority violation, no corruption: PASS. Not exercised because of the stop: recorded gates/changes/diagnosis, Timeline updates beyond the 25 records, and the selected-run tabs (no trace row exists for a run that stops before the workflow writes one: `trace_run_id` is null, so nothing is auto-selected).
- Two GUI defects the smoke exposed, FIXED: (1) the preflight judged `git` by the first WORKING copy on PATH although a run executes the FIRST: it now judges the first, reports the reason and the working alternative, and BLOCKS the start (git is required by every run's sandbox); (2) the failed banner did not show Kriya's recorded error text: it now does ("Kriya reported: ...").
- No second live attempt: the first did not fail because of a GUI integration defect that a fix could remove; with the fixed PATH decision untouched, the preflight now refuses to start on this Mac. `GUI-EXEC-GIT-SHIM-001` records the owner action (accept the Xcode license) or decision (reorder the same approved PATH directories).

## M8.9 - performance, final (real Electron, persistent read process; ms)
| Step | M5 | M6 | M8 final |
|---|---|---|---|
| Cold backend (first request, once) | 707-712 | - | 669-695 |
| Open Folder -> first Runs (real safe copy) | 182 | - | 211 |
| Open Folder -> Workspace page (controlled data) | - | 132 | 170 |
| Open empty folder / dirty repo (describe + render) | - | - | 168 / 136 |
| Run selection -> Timeline | 61 | - | 62 |
| Validation / Context / Plan / Evidence first render (real) | 16-18 | - | 17 / 17 / 18 / 17 |
| Changes metadata first render (real) | 16-18 | - | 18 |
| Authorized diff retrieval | 16 | - | 33 |
| Attempts first render (controlled data) | - | - | 21 |
| Switch workspace A -> B | 153 | - | 155 |
| Execution preflight (server) | - | not measured | 94-138 (tool check 355 -> 94 after running the `--version` probes concurrently); 366 with the broken git stub |
| Start click -> acknowledgement (banner) | - | 61 | 81 (controlled); `startRun` 151 incl. the host's own preflight (live) |
| Acknowledgement -> durable launch record visible | - | ~800 (Python startup of the run process) | 882 |
| Polling reads during a run: launch.status / evidence.records | - | 16-83 / 6-7 | live smoke: median 61 (17-93) / 8 |
| Terminal outcome -> fully refreshed cockpit | - | 38 | 41 |
Repeated ~0.78 s read spawns did not return: every read above went through the one persistent process. Differences from M5 are within run-to-run noise except the authorized diff (16 -> 33 ms), still far below the one-shot 796 ms.

## M8.1 / M8.2 / M8.3 - totals
- **Full canonical pytest (owner-authorized, once):** `ulimit -n 256; PYTHONPATH=$PWD .venv/bin/pytest -q -n 8 --dist loadgroup -p no:cacheprovider` on revision `1ebe6cb`: **10171 passed, 0 failed, 15 warnings (multiprocessing fork deprecation), exit 0, 1421.53 s (23:41)**. Docker was up.
- **UI (`npm run check:integration`, plus the serializer check with `KRIYA_PYTHON`):** kup 66, shared 321, test-host 46 (+2 skipped), standalone 221 (including 13 end-to-end tests against real Kriya with the controlled workflow body); typecheck 0 errors, eslint 0, generated schema/TS/Java consistent (104 files), shared-deps OK, Java round-trip 289 files / 0 failures (102 sources compiled), serializer fixtures current, production-bundle check OK (no fixture code and no retired-inspector UI).
- **Python static:** ruff clean; pylint exit 0 (no findings).
- **Mutations (all KILLED, none surviving; 51 mutants over the autonomous run):** 28 (launch-id claim, goal never persisted, allowlist, no -y / strict policy, workspace revalidation, state observability, authority blocker, busy gate, workspace lock, durable outcome vs process exit, quit escalation, legacy compatibility scope, ...) + 13 (approval recording incl. a fake Approve button, tool visibility not widened, a missing tool not a blocker, retirement defaults, post-run refresh, navigation, open-in-editor, filter-driver override) + 10 (first-git rule, git blocker, closing race, window-close guard, executable setting, RunRecord precedence, confirmation polling, stuck STARTING, recorded error).

## A. GUI PRODUCT READINESS
**Verdict: READY, WITH ONE HOST PREREQUISITE THAT IS NOT MET ON THIS MAC.** The desktop workflow is functionally complete and every part that does not depend on the host's git is proven; the preflight refuses to start (with the exact reason) while the host's first `git` does not run, which is the case on this Mac today (`GUI-EXEC-GIT-SHIM-001`).
| Requirement | State |
|---|---|
| Kriya.app package | PASS: `ui/standalone/out/Kriya.app` (ad-hoc signed, `com.kriya.app`, 288 MB), packaging refuses fixture/inspector bundles |
| OS/menu identity | PASS via Accessibility (identity-ax.txt); icon is the one known gap |
| Open Folder | native dialog click UNVERIFIED (session locked, then the owner was at the keyboard); menu item and production path verified; scripted-chooser captures replace only the dialog call |
| Workspace validation / goal / preflight / Start Run | PASS (e2e with real Kriya; live smoke to the first workflow step) |
| Real workflow path; no second orchestration | PASS (review Q2; live smoke made real model calls) |
| Deterministic new-run workspace linkage | PASS (launch record before workflow; manifest identity; tests + live record) |
| Evidence-backed active observation; honest terminal outcome | PASS (RunRecord is the authority; interrupted / not recorded / approval required / refused all distinct; no false success anywhere) |
| Runs, Timeline, Overview, Trust, Validation, Changes, WHY, Context, Plan, Attempts, Evidence, Workspace, Settings | PASS (module suites, M7 capture of every tab, real-data re-measurement) |
| Greenfield and brownfield | PASS (one path; deterministic e2e on an empty folder incl. an ADDED file; live smoke on a brownfield project) |
| Modular architecture; no fixture contamination; no Electron persistence access | PASS (boundary tests, bundle check, review Q7, Q9) |
| Authority/security fail closed | PASS (review Q1, Q6; 51 mutants) |
| Performance | PASS (table above; read spawns did not return) |
| Independent review; test gates | PASS (all findings fixed or registered; 10171 python + 654 UI tests) |
Documented limitations (not blockers): approval-requiring runs stop honestly (`GUI-EXEC-APPROVAL-001`); no Stop/Pause/Retry (`GUI-EXEC-STOP-001`); tools outside the fixed PATH invisible (`GUI-EXEC-TOOLCHAIN-PATH-001`); no approved icon (`GUI-APP-ICON-001`); historical rows unattributed (`GUI-RUN-ATTRIBUTION-001`); retired-inspector stored-prompt view (`GUI-M1-STORED-PROMPT-001`).

## B. CURRENT LOCAL-MODEL SOLVER READINESS (separate; unchanged by this work)
BACKEND-FINAL-CLOSURE-005 stands: **4/12 GENUINE, solver NOT READY under the >= 8/12 gate.** The live GUI smoke above is a desktop-integration check, not solver qualification: it neither supports nor contradicts that result. No cohort, qualification or Ollama change was run.

## Owner actions (one minute each)
1. **Run `sudo xcodebuild -license`** (accept once) so `/usr/bin/git` works; the cockpit's preflight then clears. Or decide on `GUI-EXEC-GIT-SHIM-001`'s PATH alternative.
2. **Native dialog check:** launch `ui/standalone/out/Kriya.app`; File > Open Folder... (and Cmd+O) must show the macOS folder chooser; Cancel must leave the current workspace unchanged; choosing a disposable folder must open it (its name appears in the toolbar).
3. Supply an approved icon (`ui/standalone/assets/Kriya.icns`) when one exists.
