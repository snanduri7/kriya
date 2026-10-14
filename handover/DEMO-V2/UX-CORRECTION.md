# Demo V2 UX correction: Project Home and an obvious run flow

Scope: GUI information architecture only (`ui/shared`, `ui/standalone` build). No backend, KUP, authority or workflow change.
Branch `demo/v2`. Starting HEAD `ea08255325300154dfd8616a2f8083ae9149daca` (== `origin/demo/v2`), tracked tree clean.
Baselines before any edit (MEASURED, this checkout): `ui/shared` vitest 50 files / 520 tests pass; `ui/standalone` vitest started from the same tree; `npm run typecheck` exit 0; `npm run lint` exit 0.

Evidence classes below follow the engineering rules: MEASURED (observed), TRACED (read in the exact code), INFERRED, DESIGN (a decision made here).

## 1. Current state, traced (TRACED unless marked)

| Question | Answer |
|---|---|
| Component shown after Open Folder | `Shell` (`cockpit/shell/AppShell.tsx`): `pageId` starts `null`, so `page = pages[0]`. The first visible page is `runs` (order 10), layout `run`. The owner therefore lands on the three-pane Runs view. |
| Rail icons | `cockpit/composition.ts` `COCKPIT_PAGES`: Runs `▶`, Workspace `▣`, Skills `✦`, Tools `▤`, Settings `⚙`. These are navigation (`NavigationRail.tsx`, icon-only, `aria-label` = page label). The owner's "Play / Stop" reading is the `▶` of Runs and the `▣` of Workspace. |
| Component with the Engineering goal | `RunPanel` (`cockpit/modules/workspace/RunPanel.tsx`), rendered by the `Workspace` module on the `workspace` page, above the Workspace facts panel. |
| Order inside it | goal box, goal help, full Readiness checklist (6 rows), blockers, "Check again", four `<details>` (Skills, Tools, Model routing, Run configuration), refusal, then Start Run. Start Run is the last element. |
| Runs page | `runsModule` (secondary pane list), `MainWorkspace` (header + 10 center tabs: Overview, Timeline, Trust, Changes, Validation, Why, Context, Plan, Attempts, Evidence), Inspector pane (modules on surface `inspector`), Evidence drawer. With nothing selected the center says "No run selected / Choose a run from the list." Buttons: "Refresh runs", "New Run" (-> `go('workspace')`), and "Acquire snapshot" when no snapshot has been acquired yet (the normal state of a folder that has never run). |
| New Run action | `runs/index.tsx:79` navigates to the `workspace` page. |
| Start Run action | `ExecutionApi.start()` in `cockpit/core/execution.tsx`; button in `RunPanel`. |
| Active-run state source | `ExecutionProvider` (`core/execution.tsx`): `run: RunView` = host `state()` + `provider.launchStatus()` (`launch.status` KUP) + `provider.evidenceRecords()` pages, polled (1 s visible, 3 s after 60 s, 5 s hidden). |
| launch.status source | `LaunchStatus` from KUP `launch.status` (launch record + RunRecord + evidence store + workspace lock). |
| Approval waiting state | `activeReview(status)`: a `PENDING` review in a `RUNNING` launch whose liveness is `live`. Exposed as `RunView.review`. |
| Timeline source | `timelineModule` over the selected run's evidence (`useRunEvidence`). |
| Run completion / failure | `deriveOutcome(...)` -> `RunView.outcome` (kind, label, detail, source), derived only from the launch record + RunRecord. |
| Persistent run banner | Already exists: `ActiveRunBanner` is rendered by `GlobalToolbar` on every page when a folder is open. It renders the full `ApprovalReview` inline. Its title is "Run running / Approval required / Run succeeded", plus launch id, elapsed, record tallies and liveness. |
| Approval UI | `ApprovalReview` (`shell/ApprovalReview.tsx`): files, reviewer text, "Review changes" / "Review validation", "Approve changes" -> a confirmation (Cancel focused; "Approve without viewing diff" when the diff is policy-hidden) -> `ex.decide()` -> `createDecider` -> host `decideApproval` -> Kriya `launch-decision`. |
| Inspector / Drawer / Raw events | Toolbar buttons "Runs pane", "Inspector", "Drawer" on every page (the Drawer toggle is wired to nothing: `toggles.drawer` is the constant `false` and `onToggle('drawer')` has no branch). Drawer tabs: Build, Tests, Git, Raw Events (`modules/drawer`). |
| Keyboard shortcuts | Renderer `onKeyDown` in `Shell`: Ctrl/Cmd + 1..9 selects `pages[n-1]` of the visible pages. Electron menu has only Cmd+N (New Project) and Cmd+O (Open Folder) (`standalone/src/main/identity.ts`). There is no Start Run shortcut. |
| Stop / cancel | The host port has no stop operation (`ExecutionHostPort`: `startRun`, `state`, `decideApproval`, `notify`). |

Consumers that depend on the current names (must be kept or updated together): `nav[aria-label=Destinations] button[aria-label=...]`, `#ck-goal`, the `Start Run` button, `[aria-label="Active run"]` (with `data-stage`, `data-outcome`, `data-timing`), `section[aria-label="Change review"]` (`data-review-id`), "Approve changes" / "Confirm approval" / "Approve without viewing diff" / "Review changes" / "Review validation" in `demo-v2/scripts/gui/make_capture.mjs`, `skills_capture.json`, `ui/standalone/make-capture-*.mjs`, and about 15 test files under `ui/shared` and `ui/standalone/test`.

## 2. Constraints that decide where code goes (TRACED, `cockpit/boundaries.test.ts`)

- A feature module imports only itself, `core/`, `design/`, `model/`, `state/`, `render/`; never `shell/` or another module.
- Shell files must not name a module id (`'runs'`, `'workspace'`, `'settings'`, ...) and must not mention recorder record kinds (`gate.result`, `model.request`, `attempt.opened`, ...).
- Therefore: the phase view-model goes in `core/runPhase.ts`; the page ids go in `core/navigation.ts`; `ApprovalReview` moves from `shell/` to `core/` so the Project page (a module) can host it, and exactly one "Change review" section exists at a time.

## 3. Target navigation (DESIGN)

| Key | Before (label, icon) | After (label) |
|---|---|---|
| 1 | Runs `▶` | **Project** |
| 2 | Workspace `▣` | **Runs** (history) |
| 3 | Skills `✦` | **Skills** |
| 4 | Tools `▤` | **Tools** |
| 5 | Settings `⚙` | **Settings** |

The rail shows icon + text for all five; no play or stop glyph is used for navigation (a play glyph appears only on the Start Run button). Page ids: new `project` (layout `single`, order 10), `runs` (order 20, still the run-documents page), `skills`, `tools`, `settings`. The old `workspace` page id is gone from the rail; its content (the facts panel) becomes "Project details" at the bottom of the Project page. The module id `workspace` is kept (it is a module id, not a backend type).

Open Folder: `Shell` resets the selected page to Project whenever the open folder changes (a folder opened while on Skills also lands on Project).

## 4. User-facing run phase mapping (DESIGN, grounded in MEASURED record sequences)

Source: `RunView` only (launch status, `activeReview`, outcome) plus the per-kind record tallies and the latest record that `ExecutionProvider` already reads from `evidence.records`. `BriefRecord` gains the recorded `phase` and `role` fields, copied verbatim (they exist on every record). Nothing is predicted.

MEASURED sequence of a real run (scenario A, 2026-10-14, run `20261014T181340-a69de47d`, 77 records; recorded `kind` / `phase`): `run.opened`, `prompt.sections`, `model.request/response/result` (planning), `mirror.decision` x3, `unit.opened`, `mirror.*`, `gate.result` (pre-attempt, no phase: the baseline gate), `attempt.opened`, `fallback.decision`, `authority.snapshot`, `prompt.sections`, `model.request/response/result` (attempt; role developer), `developer.parse`, `candidate.change`, `gate.result` + `mirror.gate_outcome` (compile, tests, goal_spec_compliance), further `model.*` (the review call), `attempt.closed`, then after the human's approval the closing records, `run.closed`.

| UI state | Rule (first match) | Evidence behind it |
|---|---|---|
| COMPLETED | `run.stage === 'finished'` and outcome kind `success` | RunRecord `terminal_status` SUCCESS |
| STOPPED | finished and outcome kind `rejected`, `review_expired` or `approval_required` | the person's own recorded rejection / Kriya's recorded expiry / a decision the desktop cannot take |
| FAILED | finished, any other outcome (`failure`, `needs_review`, `refused`, `interrupted`, `not_recorded`) | RunRecord / launch record |
| WAITING_FOR_APPROVAL | `run.review` (`activeReview`) | launch record `pending_approval` PENDING, run live |
| STARTING | stage `starting`, or no evidence record read yet | launch record phase STARTING |
| PLANNING | no `unit.opened` read yet | records exist but no unit has opened (the Planner call, plan validation) |
| VALIDATING | latest record is a `gate.result` / `mirror.gate_outcome`, or the run is RUNNING after the person's APPROVED review | gate records / the approval record |
| REPAIRING | an attempt with number >= 2 is open and the latest record is not a gate record | `attempt.opened` count >= 2 |
| EDITING | an attempt is open (opened count > closed count), none of the above | `attempt.opened` / `candidate.change` |
| RUNNING (fallback) | RUNNING launch, none of the above | the launch record only |

Deliberately NOT shown: UNDERSTANDING (there is no record that distinguishes repository analysis from the start of planning, so it is merged into STARTING/PLANNING and no "Understanding repository" step is drawn), percentages, test counts while running. Checklist steps are drawn only for states Kriya recorded (Starting, Planning, Editing, Validating) and each is marked done only when a later state has been observed.

Stop Run: not offered. There is no stop operation in the host port; adding one would be new host semantics (a stop condition of the spec). STOPPED is mapped onto the existing recorded outcomes above.

## 5. Implementation record

Commits on `demo/v2` after `ea08255` (not tagged): `029a5f5` Project Home and run flow, `f9a9459` UX smoke + evidence + button specificity, then the review-findings commit and the docs commit (see `git log`).

What exists now (TRACED in the code, MEASURED in the smoke `handover/DEMO-V2/evidence/ux-correction/results.json`):

- **Landing.** `Shell` derives the chosen page per open folder (no effect): a new folder always shows page 1, Project. MEASURED: landing page `project`, current rail item Project, the goal box present, compact `Ready`, details closed, folder name as the heading.
- **Start Run placement.** Goal box, then one line (`Ready · model · N skills · N tools · View readiness details`), the shortcut hint and the button; Recent runs and `Project details` follow. MEASURED at content sizes 1280x800, 1400x900 (the app's default window), 1536x960, 1280x720 and 1100x700: Start Run's bottom edge is at 263 px in every case, fully visible, no page or main scroll. The owner's display is 3456x2234 native (a 1728x1117 point desktop), so the default 1400x900 window fits.
- **Shortcut.** Cmd/Ctrl+Enter in the goal box starts the run (disabled when empty or blocked; plain Enter only adds a line; a second press cannot start a second run). Shown beside the button. Cmd/Ctrl+1..5 select Project, Runs, Skills, Tools, Settings; they follow the rail order.
- **Current run.** The page becomes the run (goal composer gone). The state word, one sentence and a four-step list are all derived by `core/runPhase.ts` from Kriya's records; Technical status (run id, record tallies, DERIVED liveness, source) is one disclosure down.
- **ACTION REQUIRED.** The change review (moved to `core/ApprovalReview.tsx` so the Project page can host it; exactly one "Change review" section exists) is a boxed panel: the files, `+n -m`, the recorded validation, the reviewer text, what each button does, **Reject** and **Approve & Continue**. The confirmation step (Cancel focused, "Approve without viewing diff" when the diff text is hidden) is unchanged: approval semantics did not change.
- **Strip on every other page.** `ActiveRunBanner` is now one line: `Run active · phase · Return to Run`, or `ACTION REQUIRED · Change approval waiting · Review`; after the run `Run completed/failed/stopped · View Result · Dismiss`. It is absent on the Project page, where the run is the page.
- **Runs is history.** `Run history` list, `New Run` (goes to Project), `No runs yet.` + `Go to Project` when empty, `Select a run` + `Go to Project` with nothing selected. The side panel and bottom drawer appear only once a run is selected. Run documents: Overview (now opens with *What happened*), Changes, Validation, Details (Context, Plan, Attempts, Evidence). Timeline and WHY stay in the side panel (they drive the selection the other documents follow, so they were not moved into a tab); Build, Tests, Git and Raw Events stay as the drawer's placeholders.
- **Removed.** The toolbar's `Drawer` button (wired to nothing: `toggles.drawer` was the constant `false`); `Runs pane` / `Inspector` were renamed `Run list` / `Side panel` and now appear only on the Runs page.

### Run phase mapping (final)

As in section 4, with these results: UNDERSTANDING is not a state; STOPPED covers only the recorded `rejected`, `review_expired` and `approval_required` outcomes; there is no Stop Run control because the host port has no stop operation. The sampled states of the smoke run: `STARTING, PLANNING, EDITING, VALIDATING`, then `WAITING_FOR_APPROVAL`, then `VALIDATING` (after the person's approval) and `COMPLETED`.

### Independent UX review (fresh reviewer, screenshots only)

Verdict: FAIL, narrowly, with no BLOCKING finding. Open folder, start, progress and the approval prompt were judged clear. IMPORTANT findings and what was done:

| Finding | Disposition |
|---|---|
| Reject fires at once and says nothing about its effect | FIXED: the panel states what each decision does before it is pressed. No confirmation was added to Reject (that would be a new approval semantic). |
| "+1 -1" at approval, "+3 -0" at the end | NOT A PRODUCT DEFECT (MEASURED/TRACED): the controlled workflow body used by the smoke feeds a synthetic one-line diff to the real approval path while its evidence records 3/0 (`ui/standalone/test/fixtures/controlled_kriya.py`). In the real scenario A run both read `+1 -0`. |
| Completed card never says the change reached the folder; raw `NO_COMMIT`, `Source: Kriya RunRecord` | FIXED: "The change is applied to your folder" only when the RunRecord says `COMMITTED`; any other value is quoted with "Check your folder" (the controlled body records `NO_COMMIT`, and the screenshot shows exactly that); raw code and source moved under Technical status. |
| No way to stop a run | DEFERRED (stop condition: needs a new host operation). Tracked in the report. |
| Runs Overview is a wall of identifiers | FIXED: *What happened* (outcome, changed files, recorded validation) first; the identifiers stay below. |
| Hidden-diff notice exposes a config key and no action | DEFERRED: it is Kriya's own honest policy wording and part of the approval semantics; reworded later with the owner. |

COSMETIC, recorded for later: elapsed time after completion (FIXED anyway); "Waiting for Kriya to record the launch." is internal phrasing; the Skills page lists Java skills for a Python goal (fixture content, and Skills are chosen per run); "Ready" appears twice on the Project page; `0/16000`; no Approval step in the checklist; `Run list`/`Side panel` labels; "No runs yet." under Recent runs while the first run is active.

### Verification

`ui/shared` vitest 568 pass (baseline 520); `ui/standalone` vitest 348 pass, 1 skipped (baseline identical); `npm run typecheck` and `npm run lint` exit 0; mutants for the phase mapping, facts, landing, strip, details tab, shortcut, blocker, diff-viewed logic, applied-statement, decision help, overview summary and elapsed: all killed (one surviving mutant, M2, was a real test gap and was fixed by asserting the planning sentences). Tests changed rather than weakened: the old assertions were moved to where the feature now lives (the banner tests became strip tests; the Workspace page tests became Project tests), and the structural tripwire for evidence interpretation now also covers the files that moved out of `shell/`.

Not done by design: no Demo V2 scenario was run, no final validation, no tag. The `ui/standalone/make-capture-*.mjs` generators were renamed mechanically and not re-run (the live ones need a model). The owner's vim session holds `demo-v2/OWNER_RUNBOOK.md` open (`demo-v2/.OWNER_RUNBOOK.md.swp`, untracked, not touched); `tests/test_demo_v2_assets.py::test_nothing_machine_specific_or_heavy_is_committed` fails only while that binary swap file exists (all tracked files pass it).
