# GUI v1 final hardening / bug-elimination pass

Branch `gui/run-cockpit-v1_1` from the certified lineage `e8ac25d`. No feature was added, nothing was redesigned. Local only: not pushed, not merged. Solver readiness is untouched (BACKEND-FINAL-CLOSURE-005: 4/12, NOT READY). Backend cohorts, model qualification and Ollama/profile settings were not run or changed.

## Bugs found and fixed

| Sev | Finding | Fix | Proof |
|---|---|---|---|
| P2 | **The shipped Kriya.app carried the development drivers** (screenshot/DOM-step capture with `executeJavaScript`, the scripted folder chooser, measurement, soak, tracing, the fixture stand-in), reachable by environment variables of the launching process. (Phase A item 6) | Moved to `dev_drivers.ts` (+capture/measure/soak), loaded by one guarded `require`; the product package omits the files; `scripts-check-bundle.mjs --package` fails the build if any file or env-var name reaches a product package; evidence packages (`package:mac:evidence`, `out-evidence/`) keep them. | Negative control: the product app, launched with KRIYA_UI_CAPTURE / TRACE / ALLOW_REAL set, produced no capture and no trace. `test/final_audit.test.ts`, `hardening.test.ts`; mutants killed. |
| P2 | **A store copied under another run id was served as that run, VERIFIED** (its manifest and every record named a different run). (Phase D) | `evidence_ops._require_identity`: manifest, verify, records and content refuse (typed `EVIDENCE_STORE_UNREADABLE`, reason `run_identity_mismatch`) unless the manifest and every served record name the requested run. | `tests/test_kup_evidence_corruption_matrix.py` (37 tests, including a store whose manifest was rewritten to match); both mutants killed. |
| P3 | **Raw stderr (Node/Python tracebacks) was appended to user-visible error messages** (the owner's original Open Folder screenshot). (Phase K) | The message is the typed reason; the bounded stderr tail travels in a separate `detail` field, shown only behind a collapsed "Technical detail". | `shell/error_audit.test.tsx`, `final_audit.test.ts`; mutants killed. |
| P3 | The approval confirmation had no Escape handling and undefined focus. (Phase J) | Focus opens on Cancel (the safe choice); Escape cancels; nothing is sent. | `shell/approval.test.tsx`. |
| owner rule | Content visibility: hidden-diff wording and an explicit confirmation. | "Diff hidden by Kriya content-visibility policy." in the review and the confirmation; the approving button is "Approve without viewing diff" when hidden, "Confirm approval" when the diff is authorized; with content ON the Changes module shows the real authorized diff. The GUI has no control that can change content visibility. | `shell/approval.test.tsx`; `GUI-REVIEW-CONTENT-VISIBILITY-001` closed as accepted design / operator-controlled. |

Measured limits, registered not changed: loading an extremely large run (see Phase E).

## Phase results

**A. Static architecture/security audit** - 15 items: guards for items 1-5, 7, 10, 14 added or extended (`final_audit.test.ts`: main parses no recorder vocabulary; the IPC surface equals the typed allowlist with exactly one handler per channel and no `ipcMain.on`; no shell/exec/eval; no Kriya config field named in main; the product package carries no driver; no greenfield/brownfield in main; `boundaries.test.ts`: layout files interpret no evidence, no Node/network/eval/innerHTML in modules/shell/core). Items 6 (found a violation, fixed), 8, 9, 11-13, 15 are covered by the existing and new tests listed elsewhere.

**B/G. State machine, lifecycle, races** - `lifecycle_hardening.test.tsx`: a run that dies while its review is pending (never a success, no decision control), looking at another run never moves the active run or the decision target (the decision goes to the ACTIVE launch), return to the live run, workspace switch after a terminal outcome clears everything, a late run list for workspace A is dropped after switching to B, a slow detail answer never replaces a newer selection, a failing read child is a typed error that Refresh recovers, rapid tab switching and repeated refresh. Real processes (`execution_real.test.ts`): quit while a review is pending (SIGINT, sealed failure, review closed undecided, a late decision refused), a fresh window can decide a still-pending review, plus the earlier approve/reject/stale/duplicate/died tests and the 12-way concurrent decision race. Selection invariants: 4000 random sequences (`state/selection_properties.test.ts`).

**C. Greenfield + brownfield** - 14 real end-to-end cases through the SAME launch path (empty folder, Git-initialized empty folder, compile failure, test failure, approval, rejection; clean repo with a modified and an added file, a DIRTY repo with the user's own uncommitted work (left intact), a deleted file, a regression failure, approval and rejection of a modification, existing files with NO baseline). Each asserts: the RunRecord decides (never the process exit), the review state, VERIFIED evidence with exactly the recorded change kinds, the recorded baseline (`base_revision` null when none), the folder untouched, and that every folder kind produced the identical field set from Kriya. Electron never infers the kind (guard).

**D. Evidence corruption** - 24 damages (malformed/non-object/empty/missing manifest and seal, garbage/non-object/wrong-chain/edited records, physical tail truncation, missing/tampered/emptied blobs, missing blob directory, empty/missing records) x 5 operations: always a typed answer, never an exception, nothing served from a store that does not verify; plus malformed/foreign/stale cursors, an oversized blob, unclassified and hostile part names, trace rows with no link / a dangling link, and the identity binding above.

**E. Scale** - Real recorder-written state through the REAL app (evidence package, no screenshots): 3000-run history + one run of ~20.5k records (30 attempts, 240 changed files, hostile/Unicode/very long paths, a very long goal).

| | 5.2k-record run | 20.5k-record run |
|---|---|---|
| Open folder -> Runs list | 208 ms | 120 ms (200 rows/page; Load more 41-62 ms) |
| Select the large run -> Overview | 0.7 s | 5.8 s |
| Tab switches | 21-62 ms | 21-62 ms |
| Max event-loop lag | 126 ms | 491 ms (one sample, the Timeline render) |
| Heap over 100 navigation cycles | flat (14 MB) | flat (43 MB), 0 slow switches |

Root cause of the load time (MEASURED + TRACED): every `evidence.records` page re-verifies the whole store (~220 ms at 20k records, 21 pages = 4.6 s). The window stays responsive; typical runs (hundreds to low thousands of records) are fast. Registered `GUI-EVIDENCE-LARGE-RUN-LOAD-001` (deferred with data) rather than weakening per-read verification. Permanent `shell/scale.test.tsx`: history paging and Load more, filter, hostile content as plain text, bounded Evidence window, lazy content, bounded evidence reads over 40 navigation cycles.

**F. Selection / cross-module consistency** - invariants property-tested (above); compare-attempt cannot survive a run or unit change; a live selection is always the selected run.

**H/I. macOS QA and responsive/visual** - packaged product app (no environment variable): identity PASS via Accessibility (Kriya menu bar, About/Hide/Quit Kriya, window "Kriya", no Electron text, no orphans after Quit). Real packaged app at 1536/1440/1360/1180/1100 over a real store: 61 DOM audits (Welcome, Workspace, Runs, Overview, Validation, Changes, Context, Plan, Attempts, Evidence, bottom drawer, unsealed / tampered / digest-only runs): no horizontal page scroll, no controls clipped outside the window, no unnamed controls, no text clipped by overflow, no page-height overflow; the inspector collapses below 1360; screenshots at 1536, 1360 and 1100 in `evidence/m9/responsive-run/`. Not automated (macOS native dialogs): the Open Folder chooser (owner-verified) and the quit-during-run confirmation (host logic covered by real tests).

**J. Keyboard / accessibility** - existing tests (names, no positive tabindex, switcher Escape, status never by colour alone, focus ring) plus the approval confirmation (focus on Cancel, Escape, native named buttons in document order, no trap) and a reduced-motion safety rule (no animation exists today).

**K. Error messages** - audit test: every unavailable/error state has its own typed plain title; eight distinct outcome labels (succeeded, failed, refused, approval required, rejected, review expired, did not finish, no run recorded); no generic wording or `[object Object]` in any production source; raw detail only behind "Technical detail".

**M. Fuzz** - 80,000 generated IPC inputs (KUP requests, run ids, cursors, workspace paths, start-run, decision, open-in-editor, settings): never throws, accepted values satisfy an independent statement of the grammar, argv allowlisted and option-safe. 1,350 generated KUP requests through every read operation: always a typed envelope from the closed vocabulary.

**L. Performance and soak** (all on the evidence package of the real app, no screenshot at any point; the soak uses the CONTROLLED workflow body: real launch, records and polling, no model)

*Clean event-loop baseline* (previous, `evidence/m9/lag-clean`): p95 2 ms, max 6 ms. *30-minute soak* (`evidence/m9/soak-30min`, 1807 s wall): 359 cycles; each cycle switched workspace A<->B through the real switcher and the scripted chooser, opened history, selected runs, stepped through every tab, opened a diff, toggled the drawer; 120 controlled executions through the real launch path with a review each (60 Approved, 60 Rejected, none missed); **0 errors**.

| Measure | Result |
|---|---|
| Event-loop lag (36,123 samples) | p95 **1 ms**, p99 3 ms, max 55 ms, **0 samples over 100 ms** (no material regression against the 2 / 6 ms baseline) |
| JS heap | flat, 10 MB throughout |
| DOM nodes | flat (141-362 after each cycle's settle) |
| Live timers / duplicate pollers | intervals 0; timeouts flat 19-24; no growth |
| Processes | 6-8 throughout (Electron main + helpers + 1-2 read processes + at most one run); no growth |
| File descriptors | ~300, flat (quiet samples slope -7.7/h) |
| Orphans after Quit | none (kup-stdio 0, launch 0, app 0) |
| Total resident memory | quiet samples 606 -> 637 MB over 30 min (+31 MB, 360 switches + 120 runs); |

Memory attribution (`evidence/m9/soak-12min-attribution`, same workload, per-process): Electron main 83 -> 89 MB, renderer 102 -> 113 MB, read process 107 -> 111 MB, run processes transient; the Python read process was also driven in isolation with 1500 mixed requests and plateaued at 86-87 MB after warm-up. Classification under the engineering rules: **UNKNOWN, not a leak**: slow native-memory drift in Electron main + renderer (JS heap, DOM, timers and descriptors all flat), about 0.09 MB per full cycle, from a workload far harsher than real use (~12 cycles/minute). Whether it plateaus is not proven by 30 minutes: registered `GUI-NATIVE-MEMORY-DRIFT-001` (P3, deferred; the existing two-hour soak driver is the confirming instrument).

*First soak (preserved, `soak-30min-run1-throttled`)*: ran to cycle 240 with the same flat metrics, then the owner's screen was no longer shown and Chromium throttled the hidden window's timers to once a minute (a 60,059 ms lag sample): a measurement-environment artifact, not product behaviour. The evidence build therefore disables background throttling (evidence packages only) and the runner keeps the display awake (`caffeinate`); the clean run above is the one reported.

*Large data* (Phase E): see the scaling table above.
