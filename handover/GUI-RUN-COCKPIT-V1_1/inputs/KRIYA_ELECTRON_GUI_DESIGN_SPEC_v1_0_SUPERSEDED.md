# INPUT (SUPERSEDED): Kriya Electron GUI — Detailed Design Specification v1.0

**Status:** superseded input. Preserved verbatim from the owner's text shared in the 2026-10-10 session so that
`06_REVIEW_001_EXISTING_CODE.md` (which cites its section numbers) stays readable. The authoritative design is
`../02_GUI_RUN_COCKPIT_V1_1.md`. Where the two disagree, v1.1 wins. Nothing below is evidence that a feature exists.

---

# KRIYA ELECTRON GUI — DETAILED DESIGN SPECIFICATION v1.0
## 0. STATUS AND AUTHORITY
This document defines the target information architecture, visual layout, interaction model, component hierarchy, evidence rules and phased implementation boundaries for the Kriya Electron GUI. It reconciles the accepted GUI M1 contracts, the Kriya Run Cockpit visual reference, the owner GUI direction, and the evidence-availability review. Treat this document as the GUI v1 design baseline once approved. Do not reinterpret the layout during implementation unless a repository/API constraint proves a requirement impossible or unsafe. Any material information-architecture change after approval requires an explicit design decision rather than an implementation-time redesign.
The accepted PNG is the visual north-star, not evidence that every depicted feature exists. GUI M1 remains authoritative for currently supported read-only evidence access. The Electron UI remains a consumer of Kriya evidence; it never becomes an execution, policy, verification, attribution or success authority.
## 1. PRODUCT IDENTITY
Kriya is an **Explainable Agent Execution Cockpit**.
Primary experience:
**Goal → Run → Kriya Phase → Attempt → Evidence → Outcome**
Primary promise:
**Give Kriya an engineering goal. Watch it plan, execute, validate, recover and prove what happened.**
The GUI must not become:
- a chat-first coding assistant;
- a Cursor/Cline/Roo sidebar clone;
- a full IDE;
- a generic monitoring dashboard;
- an alternative source of truth for run success.
The default visual question answered by every screen is:
**What happened, what evidence exists, why did Kriya make this decision, and where can I inspect the underlying facts?**
## 2. CORE UX PRINCIPLES
1. Evidence before decoration. Never render a value as observed unless Kriya durably records it.
2. Progressive disclosure. Default views show only the information needed to understand the run; deeper evidence is one click away.
3. One run, one mental model. Live and historical runs use the same visual structure; historical views are immutable.
4. Backend vocabulary is authoritative. Phase names, gate types, reason codes, qualification states and consistency types originate from structured Kriya evidence.
5. No fabricated attribution. If Kriya cannot establish cause, show **Cause not established** or **Insufficient recorded evidence**.
6. No fabricated telemetry. Token rate, context percentage, per-test detail, exact prompts, response content and candidate diff appear only when recorded by a supported source.
7. Actions are capability-gated. A control must not appear enabled merely because it would look useful.
8. Readability over density. High information density is acceptable only when the primary visual hierarchy remains obvious.
9. No color-only semantics. Status always uses icon + text + color.
10. Raw evidence remains accessible but never dominates the default screen.
## 3. TARGET DESKTOP SHELL
Target Electron desktop layout:
```text
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ GLOBAL TOOLBAR: Kriya | Repo | Branch | Run ID | Search | Model/Runtime Health | Trust | Settings      │
├────────┬───────────────────┬──────────────────────────────────────────────────────┬─────────────────────┤
│ NAV    │ SECONDARY PANE    │ MAIN WORKSPACE                                       │ RUN TIMELINE        │
│ RAIL   │ Runs / Local Nav  │ Goal + Status + Trust Summary                       │ / INSPECTOR         │
│        │                   │ Real Kriya Phase Rail                               │                     │
│ Runs   │ Search            │ Overview | Context | Plan | Changes | Validation    │ Summary             │
│ Worksp │ Filters           │ Attempts | Evidence                                 │ Evidence            │
│ Knowl. │ Run History       │                                                     │ Raw                 │
│ Models │                   │ Selected run/attempt/evidence content               │                     │
│ Tools  │                   │                                                     │ WHY?                │
│ Exper. │                   │                                                     │                     │
│ Settg. │                   │                                                     │                     │
├────────┴───────────────────┴──────────────────────────────────────────────────────┴─────────────────────┤
│ READ-ONLY ENGINEERING DRAWER: Build | Tests | LSP | MCP | Git | Model I/O | Raw Events               │
└─────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```
## 4. RESIZABLE LAYOUT CONTRACT
Default desktop target: 1440–1728 px wide.
Minimum supported application width: 1180 px.
Regions:
- Global toolbar: 44–48 px high.
- Navigation rail: 56–64 px fixed.
- Secondary pane: 280 px default, resizable 240–360 px.
- Main workspace: flexible, minimum 620 px.
- Right timeline/inspector: 340 px default, resizable 300–460 px.
- Bottom drawer: collapsed by default unless context requires it; expanded 220–360 px.
Responsive behavior:
- At <1360 px, secondary pane may collapse to an icon-triggered drawer.
- At <1220 px, right inspector may collapse to a slide-over panel.
- Main workspace always retains priority.
- No layout may horizontally compress code/evidence until unreadable merely to preserve all panels.
Provide a **Focus mode** that temporarily hides secondary pane, right inspector and bottom drawer while preserving navigation access.
## 5. GLOBAL TOOLBAR
Always visible.
Left:
- Kriya logo/product identity.
- Repository selector.
- Active branch.
- Current run identifier when a run is open.
Center:
- Search field: **Search runs, files, evidence…**
- Do not label it "ask Kriya" unless an explicitly authorized conversational/query capability exists.
Right:
- Local runtime connectivity state.
- Active model-routing summary only when recorded.
- Trust/provenance status indicator.
- Settings.
Capability-gated run actions may appear only when backend mechanisms exist:
- New Run: hidden/disabled until run-creation action contract is approved.
- Stop: absent until typed cancellation exists.
- Pause: absent until backend pause/resume exists.
- Rerun: absent until separately authorized action API exists.
Never render unsupported controls as aspirational UI.
## 6. PRIMARY NAVIGATION
Final information architecture:
1. Runs
2. Workspace
3. Knowledge
4. Models
5. Tools
6. Experiments
7. Settings
Navigation rules:
- Runs is the default destination.
- Workspace is repository/environment inspection, not a source-code IDE.
- Knowledge, Models, Tools and Experiments appear only when corresponding supported read operations exist; until then they are feature-gated, not fake screens.
- Experiments may remain hidden until durable experiment/cohort evidence is exposed.
- Navigation selection changes the secondary pane context.
- Avoid more than seven top-level destinations in v1.
## 7. RUNS SECONDARY PANE
Visible when Runs is selected.
Elements:
- Heading: Runs.
- Search runs.
- Filter chips: All, Active, Completed, Failed/Stopped when evidence supports those states.
- Optional grouping: Today, Yesterday, Earlier or Milestone if durable timestamps/milestone metadata exist.
Run row content:
- Run ID.
- concise goal/title.
- durable run state.
- duration only when start/end timestamps exist.
- attempt count when recorded.
- latest Kriya failure_category/reason when applicable.
Do not display cohort-only labels such as GENUINE, SAFE_FAILURE, FALSE_SUCCESS or MODEL BEHAVIOUR as product runtime verdicts unless those classifications become first-class recorded Kriya evidence.
Selected run gets one clear highlight; avoid excessive colored chips.
## 8. RUN HEADER
Main workspace header is the authoritative summary of the selected run.
Display:
- Run ID.
- durable status.
- goal/title.
- repository.
- branch/base revision if recorded.
- active/current phase for live-capable future mode.
- elapsed or total duration when recorded.
- attempt count.
- model-routing summary when recorded.
Secondary metadata appears in compact chips, maximum 4–5 visible; overflow goes into a details menu.
Never show arbitrary product-internal labels such as `CAGC-V2` unless they are meaningful, durable user-facing configuration values.
## 9. TRUST STRIP
Directly below the run header, compact and collapsible.
Required fields when available:
- model qualification status;
- SEC-009/model approval state;
- evidence/state-store identity/path;
- configuration directory/profile;
- snapshot ID;
- consistency kind;
- run/base revision;
- evidence freshness/observation time.
Collapsed form:
**Trust ✓ Qualified · SEC-009 Current · Snapshot a253… · Single observation**
Expanded form shows exact recorded values and provenance.
Missing values show **Not recorded**, never inferred.
## 10. PHASE RAIL — USE REAL KRIYA PHASES
Do not use the mock's synthetic `Goal → Context → Plan → Execute → Verify → Repair → Final Gate` as authoritative execution stages.
Goal is displayed in the run header; the phase rail represents actual Kriya workflow phases:
**KnowledgeGuard → Planning → Architecture → Development & Quality Gates → Review → Commit**
Rules:
- Use backend phase names/identifiers as the source.
- Friendly display capitalization is allowed; semantic renaming is not.
- Repair/retry is represented within Development & Quality Gates / attempts, not as a fabricated global phase.
- Final Gate is not a phase unless Kriya adds it as a durable workflow phase.
- Phase status: Completed, Active, Stopped, Not Reached, Unknown as supported.
- Historical M1 data that cannot reconstruct a phase shows **Not recorded for this run**.
Selecting a phase filters timeline and central evidence to that phase.
## 11. CENTRAL RUN TABS
Approved run workspace tabs:
1. Overview
2. Context
3. Plan
4. Changes
5. Validation
6. Attempts
7. Evidence
Seven tabs are acceptable because this is a desktop engineering product. Do not add more primary run tabs in v1; secondary detail belongs inside these surfaces or the right inspector.
## 12. OVERVIEW TAB
Purpose: understand the run in <10 seconds.
Show four compact sections:
- Goal.
- Current/final outcome.
- Phase progress.
- Key evidence summary.
Recommended cards:
- Requirements/claims status only when structured claims exist.
- Files affected count.
- Gate summary.
- Attempts/retries count.
- final/stop reason.
Avoid dashboard card proliferation. Maximum 4–6 primary summary elements.
A failed run must make the first blocking state obvious and provide **Why?**.
## 13. CONTEXT TAB — KRIYA SIGNATURE SURFACE
This is mandatory because "what did the model see?" is central to trust.
Show per model invocation or selected attempt:
- role/model;
- context budget;
- files included;
- tier/source of each included item;
- symbol/member/range when recorded;
- files omitted;
- omission reason;
- rendered guidance/rules/skills;
- Engineering Pack/skill sources;
- provider-reported context usage;
- estimated context separately if only estimated.
Rules:
- Do not reconstruct historical context from the current repository.
- Do not claim a file was shown merely because it existed in the workspace.
- If model-facing context blobs are not durably recorded, show exactly what is available and state the limitation.
- Context percentage appears only when both numerator and authoritative capacity are available.
- Provider totals and Kriya estimates must be visually distinguished.
## 14. PLAN TAB
Show structured plan as produced/accepted by Kriya:
- units/subtasks;
- declared files;
- dependencies;
- immutable/preserved targets;
- authority/scope summary;
- plan-validation outcomes;
- repair/replan history.
Use hierarchical rows rather than a graph by default.
Graph visualization may be optional for complex plans but must never replace readable structured plan details.
Plan rejection displays exact reason code and evidence.
Do not translate backend validation into invented "planner confidence".
## 15. CHANGES TAB
This tab has two modes based on evidence availability.
Mode A — content retained:
- changed-file list on left.
- baseline/candidate/repair/final selector when those artifacts are recorded.
- syntax-highlighted diff in center.
- Diff / Side-by-side / Unified toggle.
- attempt selector.
- file provenance/digest.
Mode B — content not retained:
- changed/attempted file paths.
- operation type if recorded.
- baseline/candidate digests if available.
- explicit banner: **Content diff unavailable — this run recorded modified paths but not candidate text.**
Never recreate a historical candidate diff by reading the present working tree.
Before implementing Mode A, trace and document whether attempt evidence retains baseline/candidate contents. If unavailable, implement Mode B honestly and add a backend-data requirement rather than scraping private evidence internals.
Open in IDE:
- permitted as a navigation integration when path/revision is known.
- never mutates Kriya evidence.
## 16. VALIDATION TAB
Default sub-tabs:
- Summary
- Build/Compile
- Tests
- Static Analysis
- Runtime
- API/Contract only when a corresponding structured validator exists.
Do not invent "Static Guard" or "API Guard" names.
Gate names displayed in detailed evidence must use recorded gate type strings such as:
- compile
- test
- targeted_test
- regression_test
- goal_spec_compliance
- test_selection
- static analysis
- runtime verification
Validation summary:
- gate state;
- duration when recorded;
- counts when structurally reported;
- failure reason;
- baseline attribution when available;
- link to attempt/candidate.
Per-test presentation:
- render individual tests only when the validator emits structured per-test evidence.
- Pytest/Surefire/other structured sources can show tables only when recorded.
- Gradle or any unsupported source shows aggregate/raw evidence and a badge such as **Structured per-test evidence unavailable for this validator/run**.
- Never convert console text into authoritative structured test facts in the Electron layer.
## 17. ATTEMPTS TAB
Display attempt cards or rows:
- attempt number;
- model role/model;
- trigger;
- candidate files;
- gate outcomes;
- retry/recovery reason;
- final attempt disposition.
Selecting an attempt filters Changes, Validation, Evidence and bottom drawer.
Provide **Compare Attempts**:
- choose A/B;
- prompt metadata differences if recorded;
- model/profile differences;
- context differences;
- candidate file/diff differences;
- validation differences;
- recovery/outcome differences.
Unavailable dimensions show Not recorded.
## 18. EVIDENCE TAB
This is the structured audit view.
Show:
- evidence objects grouped by phase/attempt;
- gates;
- requirements/claims if available;
- baseline and candidate evidence;
- attribution decisions;
- authority/disposition records;
- provenance;
- raw object references/digests.
Default is human-readable structured evidence.
Raw JSON/text is available through the Raw detail level or bottom drawer, not dumped by default.
## 19. RIGHT EXECUTION TIMELINE / INSPECTOR
Default right panel combines hierarchical run navigation and evidence inspection.
Timeline hierarchy:
- phase
  - unit/subtask
    - attempt
      - model call
      - gate
      - retry/recovery
      - recorded event
The panel must not invent chronology not present in evidence.
Top controls:
**Summary | Evidence | Raw**
Summary:
- short human-readable recorded facts.
Evidence:
- structured reason codes, references, attribution, gate output.
Raw:
- original recorded payload/log excerpt where supported.
Selecting a timeline node synchronizes:
- central tab context;
- attempt filter;
- bottom drawer filter.
Avoid auto-switching the user's central tab unexpectedly. Instead highlight available linked evidence and provide **Open in Changes**, **Open in Validation**, **Open in Context**, etc.
## 20. WHY? — SIGNATURE INTERACTION
Every failed, rejected, retried or repaired state should expose **Why?** when evidence exists.
Why panel format:
1. **What happened** — recorded event/gate outcome.
2. **First incorrect/blocking state** — only when established.
3. **Kriya classification** — exact stored failure_category/reason/attribution.
4. **Evidence** — linked observations.
5. **What Kriya did next** — retry, recovery, refusal, restore, stop.
6. **What is unknown** — explicitly list missing evidence.
Permitted Kriya categories are driven by recorded backend data, for example:
- VERIFICATION
- RESOURCE
- GENERATION_COMPLETENESS
- EDIT_TARGETING
- BUILD
- INTERNAL
- UNCLASSIFIED
Attribution tier/kind/confidence may be displayed only when recorded.
Never display handcrafted cohort labels such as "MODEL BEHAVIOUR" as runtime truth unless Kriya durably records that classification.
If cause cannot be established:
**Cause not established. Kriya recorded the failure and recovery evidence below, but the available evidence does not prove a causal attribution.**
## 21. BOTTOM ENGINEERING EVIDENCE DRAWER
Replace the interactive Terminal concept with a **read-only engineering evidence drawer**.
Tabs:
- Build
- Tests
- LSP
- MCP
- Git
- Model I/O
- Raw Events
Optional future tab:
- Commands / Process Output, read-only.
Do not implement an interactive shell under the current M1 contract.
Selection is contextual:
- selected run filters all;
- selected attempt further filters;
- selected gate filters to applicable evidence.
Drawer may auto-open on explicit user request, not every failure.
## 22. WORKSPACE SURFACE
Purpose: inspect the current Kriya workspace/environment, not edit code.
Potential sections when supported:
- repository identity;
- branch/base revision;
- project structure;
- workspace cleanliness;
- environment/toolchain;
- configuration;
- containment;
- approved roots;
- snapshot/state identity.
No full editor in v1.
Provide Open in IDE where permitted.
## 23. KNOWLEDGE SURFACE
Future first-class surface once read APIs exist.
Show:
- active skills;
- rules;
- Engineering Packs;
- provenance;
- version/freshness;
- source type;
- run usage;
- actual context injection where recorded.
Do not imply "knowledge used" merely because knowledge exists.
Distinguish:
- available;
- selected;
- injected;
- referenced by evidence.
## 24. MODELS SURFACE
Future read-only model control/observability surface.
Show:
- model roles;
- qualified model/profile;
- qualification status;
- runtime/backend;
- context capacity when authoritative;
- per-role usage;
- recorded latency/tokens when provider-reported;
- historical success/failure metrics only from recorded Kriya runs.
Never rank models from tiny cohorts without clearly labeling sample size.
Do not show token/sec unless provider eval_count/eval_duration support it.
## 25. TOOLS SURFACE
Future surface.
Categories:
- MCP
- LSP
- compiler/build
- test
- static analysis
- runtime/containment
- external lookup
Show connection/status/configuration/evidence availability, not arbitrary controls.
Actions require a separately approved capability contract.
## 26. EXPERIMENTS SURFACE
Future surface after durable experiment/cohort read operations exist.
Show:
- experiment/cohort identity;
- frozen manifest;
- task matrix;
- measured outcomes;
- failure categories;
- model/profile;
- executable revision;
- drill-down to runs.
Cohort classifications such as GENUINE/SAFE_FAILURE/FALSE_NEGATIVE/FALSE_SUCCESS belong here when they originate from recorded experiment evidence, not in normal Run status.
## 27. SETTINGS
Sections:
- Appearance
- Repository/Workspace
- Evidence
- Models
- Security/Network
- Advanced
Do not expose settings that bypass Kriya policy.
Critical settings must explain whether changing them affects future runs only or current state.
## 28. LIVE MODE — SEPARATE CAPABILITY
M1 remains read-only historical/snapshot based.
Live UI features require a separately designed live transport/ui-server contract.
Only after that exists may the UI display:
- Running state;
- current attempt;
- pending steps;
- streaming timeline;
- elapsed timers;
- live token rate;
- live context utilization.
A historical snapshot must never masquerade as a live stream.
Live values require source timestamp and consistency semantics.
## 29. ACTION MODE — SEPARATE CAPABILITY
Actions are not part of the read-only cockpit baseline.
Potential future actions:
- New Run
- Stop
- Retry
- Rerun validation
- Approve/deny authority
- Resume
Each action requires:
- backend mechanism;
- authorization semantics;
- state-transition contract;
- audit evidence;
- error/rollback behavior;
- UI capability advertisement.
Pause must not appear until a real pause mechanism exists.
## 30. VISUAL DESIGN SYSTEM
Theme:
- professional dark theme as primary design reference;
- restrained blue accent;
- semantic green/amber/red only for state;
- no excessive gradients/glow.
Typography:
- UI: system/Inter/Geist-class sans serif.
- code/evidence: SF Mono/JetBrains Mono-class monospace.
Recommended sizing:
- 13–14 px standard UI text.
- 12 px metadata.
- 16–18 px section titles.
- 22–26 px primary run title where space permits.
Spacing:
- 4/8 px base grid.
- dense engineering layout but minimum 32–36 px interactive row height.
Status:
- icon + label;
- avoid more than 2–3 colored badges in one local area.
Dividers:
- subtle, low-contrast.
Selected items:
- accent border/background, not full saturation.
## 31. ACCESSIBILITY AND KEYBOARD
Minimum WCAG AA contrast.
Keyboard:
- Cmd/Ctrl+K: search.
- Cmd/Ctrl+1…7: main navigation optional.
- Up/Down: timeline/run selection.
- Enter: inspect.
- Esc: close drawer/overlay.
Visible focus rings.
Tooltips for abbreviated reason codes.
No status communicated only by color.
Respect OS reduced-motion preference.
## 32. EMPTY, LOADING AND UNAVAILABLE STATES
Every evidence surface must implement three distinct states:
- Loading: data operation in progress.
- Empty: operation succeeded but no records exist.
- Unavailable: backend does not record/expose this evidence.
Examples:
**No validation evidence was recorded for this attempt.**
**Candidate contents were not retained; only modified paths are available.**
**Structured per-test evidence is unavailable for this Gradle run.**
**Provider did not report context capacity; percentage cannot be calculated.**
Do not collapse these into generic "No data".
## 33. ERROR STATES
GUI/backend read error:
- show operation name;
- typed error if provided;
- whether displayed data may be stale;
- retry read action if permitted.
Never silently fall back from durable evidence to scraped log interpretation.
## 34. UI-TO-EVIDENCE CONTRACT
Electron UI accesses Kriya through supported read APIs/CLI/server contracts only.
Forbidden:
- direct SQLite/evidence-store internal queries from renderer;
- parsing private evidence directories;
- scraping raw logs to manufacture structured state;
- reading current workspace to reconstruct historical candidate state;
- mutating Kriya state from read-only views.
Architecture:
Electron Renderer → typed IPC → Electron Main → Kriya UI/read adapter → supported KUP/read API.
All returned structures should carry:
- source operation;
- schema/version;
- run/snapshot identity;
- consistency kind;
- availability;
- provenance where applicable.
## 35. EVIDENCE-AVAILABILITY MATRIX
Before implementing each visual surface, classify it:
A. AVAILABLE NOW — existing supported M1/KUP read contract.
B. RECORDED BUT NOT EXPOSED — backend data exists; add supported read operation.
C. NOT RECORDED — backend must persist evidence before GUI can show it.
D. LIVE ONLY — requires ui-server/live transport.
E. ACTION — requires backend action contract.
Mandatory pre-implementation trace items:
- exact baseline/candidate/repair/final file content retention;
- prompt/response retention;
- model-facing context retention;
- validator-by-validator structured per-test evidence;
- requirement/claim closure evidence;
- model token/capacity provenance;
- knowledge/skill injection evidence;
- live progress availability.
No component may silently move from B/C to A through ad hoc parsing.
## 36. CURRENT IMPLEMENTATION PHASES
Phase GUI-1 — M1 Cockpit Restyle:
- preserve accepted M1 data contracts;
- implement app shell, navigation rail, run list, run header, trust strip, real phase rail, timeline hierarchy, Summary/Evidence/Raw detail levels;
- use honest unavailable states;
- no live execution;
- no actions;
- no interactive terminal.
Phase GUI-2 — Evidence Depth:
- Context view;
- supported candidate/diff view;
- structured Validation enhancements;
- WHY panel;
- attempt comparison;
- new supported read APIs required by evidence matrix.
Phase GUI-3 — Knowledge/Models/Tools:
- read-only product surfaces;
- qualification/provenance;
- skill/knowledge injection visibility.
Phase GUI-4 — Experiments:
- cohort/experiment matrix and run drill-down.
Phase GUI-5 — Live Observation:
- ui-server/live stream;
- active phases/attempts;
- timers;
- pending states;
- provider telemetry.
Phase GUI-6 — Controlled Actions:
- New Run/Stop/Retry/Rerun/approval actions only after backend contracts.
Do not combine GUI-5/GUI-6 into M1 restyling merely to reproduce the visual PNG.
## 37. COMPONENT HIERARCHY
`KriyaAppShell`
- `GlobalToolbar`
- `NavigationRail`
- `SecondaryPane`
  - `RunSearch`
  - `RunFilters`
  - `RunHistoryList`
- `MainWorkspace`
  - `RunHeader`
  - `TrustStrip`
  - `PhaseRail`
  - `RunTabBar`
  - `OverviewView`
  - `ContextView`
  - `PlanView`
  - `ChangesView`
  - `ValidationView`
  - `AttemptsView`
  - `EvidenceView`
- `ExecutionInspector`
  - `TimelineTree`
  - `DetailLevelSelector`
  - `WhyPanel`
  - `EvidenceLinks`
- `EngineeringDrawer`
  - `BuildEvidence`
  - `TestEvidence`
  - `LspEvidence`
  - `McpEvidence`
  - `GitEvidence`
  - `ModelIoEvidence`
  - `RawEventEvidence`
Shared components:
- `StatusBadge`
- `AvailabilityBadge`
- `EvidenceReference`
- `ReasonCode`
- `RunDuration`
- `ModelIdentity`
- `QualificationBadge`
- `FileEvidenceRow`
- `GateOutcomeRow`
- `AttemptRow`
- `EvidenceEmptyState`
- `OpenInIdeAction`
## 38. SYNCHRONIZATION RULES
Selecting a run:
- updates all run-scoped surfaces.
Selecting a phase:
- filters timeline and highlights relevant evidence.
Selecting an attempt:
- filters Context/Changes/Validation/Evidence and engineering drawer.
Selecting a gate:
- opens gate summary in inspector;
- provides explicit links to relevant central views.
Selecting a file:
- opens recorded diff/content if available.
Selecting Why:
- does not replace raw evidence; it overlays/opens structured explanation linked to evidence.
Back/forward navigation must preserve selected run, phase, attempt and tab in URL/router state.
## 39. PERFORMANCE
Initial run-history view should become interactive without loading raw evidence for every run.
Lazy-load:
- candidate contents;
- raw events;
- model I/O;
- large logs;
- full validation details.
Virtualize:
- long run lists;
- timeline events;
- test lists;
- raw logs.
Do not load multi-megabyte evidence blobs until requested.
Cache immutable historical read responses by run/snapshot/schema identity.
## 40. SECURITY/TRUST
Renderer must not receive secrets or unrestricted filesystem access.
Use Electron contextIsolation and minimal preload API.
No nodeIntegration in renderer.
All filesystem/open-in-IDE actions validated in Electron main/backend adapter.
External links clearly identified.
External knowledge/network state visibly distinguished from local-only state.
Trust strip should make local/model/evidence provenance inspectable without overwhelming the main screen.
## 41. DESIGN REVIEW ACCEPTANCE CRITERIA
The design is accepted only if reviewers agree:
1. The GUI is run/evidence centric, not chat centric.
2. M1 contracts remain intact.
3. No unsupported action is implied as implemented.
4. Real Kriya phase/gate vocabulary is used.
5. Context, WHY and Trust are first-class.
6. Missing evidence is explicitly represented.
7. Candidate diff/per-test detail is capability-gated.
8. No interactive terminal exists under the current contract.
9. Timeline and center workspace synchronize cleanly.
10. Default view is understandable without opening Raw evidence.
11. Deep evidence is reachable in at most two interactions from the relevant run state.
12. No cohort/harness vocabulary leaks into normal product verdicts.
13. Layout remains usable at 1180–1728 px widths.
14. The design can grow into live observation/actions without restructuring the core shell.
## 42. DESIGN FREEZE
Once this document is approved:
- the PNG becomes the visual reference;
- this specification becomes the behavioral/information-architecture authority;
- GUI M1 contracts remain data authority;
- implementation must not redesign navigation or panel structure casually;
- unsupported PNG elements are capability-gated rather than faked;
- any required backend data additions are identified through the evidence-availability matrix and implemented through supported read contracts;
- pixel-level styling may evolve during implementation, but the information architecture, evidence semantics and interaction rules above remain frozen unless explicitly revised.
## FINAL PRODUCT LAYOUT DECISION
Keep the visual character of the accepted PNG: dark professional Electron cockpit, narrow navigation rail, run history pane, large central engineering workspace, synchronized right execution timeline and a bottom evidence drawer.
Change the semantics to make it genuinely Kriya:
- real Kriya phases;
- real gate names;
- Context as a first-class tab;
- Trust strip;
- evidence-backed WHY;
- capability-gated diffs and per-test views;
- read-only engineering drawer instead of interactive terminal;
- no fabricated live state;
- no unsupported actions;
- no cohort vocabulary in ordinary run status.
The desired user experience is deliberately simple at the top and deep underneath:
**At first glance: goal, run state, phase, latest outcome.**
**One level deeper: attempt, candidate, validation, context.**
**Deep inspection: structured evidence, provenance, raw events and model I/O.**
This progressive-disclosure model is the key requirement that keeps Kriya powerful without making the UI cluttered.
