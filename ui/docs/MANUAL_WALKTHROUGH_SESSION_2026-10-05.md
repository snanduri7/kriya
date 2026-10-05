# Owner's manual walkthrough on the real store (prepared 2026-10-05; every check below is PENDING until the owner performs it)

## Identities of this window (prepared by the session; no snapshot acquired, no model started)

| item | value |
|---|---|
| executable | `/Users/sriramnanduri/WorkingDirectory/AI/AntiGravity/Kriya-By-Antigraviry/tmp/kriya-demo1-attribution-fix-2/.newvenv/bin/kriya` (this checkout; the only install with the KUP adapter) |
| configuration directory | `/Users/sriramnanduri` (operator HOME; setting left blank; no `kriya.yaml` there, so packaged defaults) |
| state directory and source store | `/Users/sriramnanduri/.kriya/state`, `traces.db` (10,039,296 bytes, sha256 `60fb4539…` unchanged through the acceptance run) |
| published snapshots to pin | `20261005T020254001807Z-bb97d302` (acquired 02:02:54 UTC) and `20261005T020824136023Z-a253a850` (02:08:24 UTC); both digest-verified; same content digest `027635a1…` |
| recovery workspace | unset (held until the owner names one or chooses to leave it unset) |
| host settings file | `~/Library/Application Support/@kriya-ui/standalone/kriya-ui-settings.json` (written by the session with the executable above, editor vscode, configuration directory and workspace null) |
| launch | `KRIYA_UI_ALLOW_REAL_KRIYA=1 npm start -w @kriya-ui/standalone` from `ui/`, after `npm run build -w @kriya-ui/standalone` so the renderer carries the current shared code |

On launch the window reads `capabilities` and `snapshot.list` only (live observations). **Do not press "Acquire new snapshot"**:
choose one of the two published snapshots from the "displayed snapshot" select; the host verifies its digest and pins it.

## A. Pin an existing snapshot (procedure steps 4 and 7)

| step | do | expect | result |
|---|---|---|---|
| A1 | Read the trust strip before pinning | Host `electron` (no "fixtures" wording); Configuration directory `/Users/sriramnanduri` "default: operator HOME …"; History store `/Users/sriramnanduri/.kriya/state/traces.db`; Kriya `0.1.0`; Displayed snapshot `none` | pending |
| A2 | Choose `…-a253a850` in "displayed snapshot" | status says "verifying snapshot digest…" then "snapshot acquired at 2026-10-05T02:08:24.…"; "Snapshot integrity" reads "digest verified at pin (<time>); metadata checked per query" with `sha256 027635a14de47e40…`; runs list fills (50 rows, "Load more") | pending |
| A3 | Choose `…-bb97d302`, then `…-a253a850` again | each switch verifies first, the runs list reloads, the selected run clears; Displayed snapshot shows "chosen from the published list" | pending |
| A4 | Press "Refresh displayed snapshot" | the runs list reloads; the status returns to the same headline; the "Snapshot" item reads either "no metadata change detected (not a freshness guarantee)" or "Source metadata differs since acquisition. Metadata alone does not establish a content change or a new run." with the stat detail line beneath; NO new snapshot id appears in the select (no acquisition) | pending |

## B. Panels (step 8, visual confirmation of what jsdom already rendered)

| step | do | expect | result |
|---|---|---|---|
| B1 | Filter runs by `1df27f7a`, open it | header shows the goal; Outcome `failure (no_progress)`; Attempts 4; events list has 54 rows named by kind; attempt chips 1..4 | pending |
| B2 | Gates tab | 8 records: `compile`/`test` with `failure`/`success` only, outputs shown, no "not recorded" | pending |
| B3 | Evidence tab | 5 records "#n failure/active_skills · source · attempt n · time"; note that records carry no identifier; payload below | pending |
| B4 | Why tab | "Recorded failure category: no_progress"; failure report rows; "Recorded causal attribution: not recorded - the baseline persists failure categories, not causal attribution" | pending |
| B5 | Open `280bd867` | Outcome `success`; Attempts 0 (literal); 21 events; 3 gates (`compile`, `test`, `regression_test` all success) | pending |
| B6 | Runs list colours | `success` green, `failure` red, `approval_required` and `needs_review` amber, `planner_output_*` neutral; text always literal | pending |
| B7 | Prompt tab on either run | nothing fetched until "Load prompt (sensitive)"; then "role: planner · scope: plan_prompt …" and the prompt text (do not copy it anywhere) | pending |

## C. Keyboard and VoiceOver (the full script is `A11Y_MANUAL_WALKTHROUGH.md`; the minimum here)

| step | do | expect | result |
|---|---|---|---|
| C1 | Tab from the window start through the toolbar | Settings, Acquire new snapshot, Refresh, the snapshot select, then the runs filter and list | pending |
| C2 | In the runs list: ArrowDown / ArrowUp | selection moves and the run opens; focus stays on the list | pending |
| C3 | Tab to the events list; ArrowDown twice | second event selected; Inspector switches to Evidence | pending |
| C4 | Type `model` in "Search recorded events"; then Tab to "Clear filter" and press Enter | the list narrows while typing; clearing restores it | pending |
| C5 | Inspector tabs: ArrowRight/Left/Home/End | panels follow the focused tab | pending |
| C6 | Open Settings with Enter, Escape to close | focus lands on the "Settings" title, returns to the Settings button | pending |
| V1 | VoiceOver on, repeat C4 | the count is not announced per keystroke; one announcement after a pause | pending |
| V2 | VoiceOver, Why tab of `run-negative-evidence-links` is NOT available on the real store (fixture only); instead read B4 | "not recorded" statement read as plain text | pending |
| V3 | VoiceOver, Gates tab | "attempt N · compile · failure" etc. | pending |

## D. Record

Write the result column above (pass / fail with what was seen / pending), the date, macOS and VoiceOver versions, and any
observation; a failed criterion stops the walkthrough and becomes a Fix-Now item.
