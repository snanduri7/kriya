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

(Filled in as the work lands: files changed, tests, screenshots, review.)
