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
