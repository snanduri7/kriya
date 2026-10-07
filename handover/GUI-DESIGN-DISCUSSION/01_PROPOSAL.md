# Kriya GUI — initial proposal

Baseline: `codex/fix-demo1-attribution` @ `61a867fc31a7b5ff5d7f21d1ea15e4eac43f2e03`.
Author: ChatGPT (proposer). Status: proposed, awaiting Claude review 1.
Evidence references below are repository-relative baseline source locations. INFERRED means a proposed choice or expectation, not an implemented or measured capability. No live model runs were performed.

## Problem and users

**P-1 — INFERRED:** Engineers operating Kriya need an accessible local view of recorded outcomes and interrupted-workspace state. The initial GUI should help them inspect those results without remembering command syntax. Settle usefulness through an owner walkthrough of successful, failed, empty-history and interrupted-run fixtures.

## Scope and authority boundary

**P-2 — INFERRED:** Milestone 1 is a local, read-only run viewer: workspace selection, installed Kriya identity, recorded history output, and recovery assessment. It offers no generation, editing, recovery, pruning, migration, permission approval, configuration editing, arbitrary terminal, or model access. Verify the complete command allowlist and filesystem effects in acceptance tests.

**P-3 — INFERRED:** Every Kriya operation is an existing `kriya` command executed in a separate process. The GUI never imports workflow/model/tool implementation modules and never reads or writes Kriya databases, control records, configuration or source files directly. Native folder selection supplies an explicit workspace; the GUI is a presentation adapter, not a new authority. Verify through dependency and process-boundary inspection and forbidden-operation tests.

**P-4 — TRACED:** Existing inspection surfaces differ: `kriya version --json` reports installed provenance without loading configuration (`kriya/cli.py:152`, `kriya/cli.py:183`); `kriya runs status --workspace PATH --json` assesses recovery without taking a lock or writing (`kriya/cli.py:3823`); `kriya traces -n N` prints a text table, not JSON (`kriya/cli.py:4555`, `kriya/cli.py:4640`). Consequently milestone 1 displays trace output verbatim as text, without inventing structured run detail or parsing table columns.

## Architecture and command contract

**P-5 — INFERRED:** Use a native Python desktop application with a presentation layer, a small allowlisted subprocess adapter and output-only view models. Only these argument vectors are allowed in milestone 1: `[kriya, version, --json]`, `[kriya, runs, status, --workspace, selected_absolute_path, --json]`, and `[kriya, traces, -n, bounded_integer]`. Set subprocess cwd explicitly to the selected workspace. Use an owner-selected absolute installed executable, argument arrays, no shell, closed stdin, separate stdout/stderr, bounded buffers and one active inspection per panel. Never retry with broader permissions, alternate commands or trust flags. Tests must capture actual executable, cwd, arguments, environment and process lifecycle.

**P-6 — INFERRED:** Use manual refresh initially. A workspace change invalidates prior requests; a late response is discarded using a request token. Display observation time, command, workspace, installed identity and errors alongside each result. Set a 60-second timeout and 4 MiB limit per output stream; truncation is explicit and never parsed as complete JSON. Stop only GUI-owned inspection subprocesses on timeout or close; do not send cancellation to existing Kriya runs. Verify timeout, close, rapid workspace switching and oversized-output fixtures.

**P-7 — TRACED:** Recovery status exits nonzero for RUN_ACTIVE and action-required assessments despite emitting valid JSON (`kriya/cli.py:3840`). Treat those payloads as assessments, preserving their state and exit code; distinguish them from malformed JSON, launch failures and command errors. The trace table abbreviates goals and does not expose run identifiers (`kriya/cli.py:4640`, `kriya/cli.py:4650`); it cannot support a reliable clickable run-detail view.

## Data and evidence

**P-8 — TRACED:** The existing history source is SQLite `runs` and `milestone_plans`; run fields include JSON-encoded events, evidence, gate outcomes, model hops, generation metrics and failure reports (`kriya/core/trace.py:31`, `kriya/core/trace.py:88`, `kriya/core/trace.py:132`). The canonical database path is derived from state-directory configuration/environment (`kriya/core/state_paths.py:64`, `kriya/core/state_paths.py:76`). These describe the command's underlying sources; they are not permission for the GUI to query them.

**P-9 — TRACED:** Events carry explicit authority, source, attempt, details and creation time (`kriya/workflow/run_events.py:12`, `kriya/workflow/run_events.py:20`). A trace row does not necessarily have a lifecycle RunRecord (`kriya/workflow/run_trace.py:15`). Therefore history status and workspace recovery state stay in separate panels; the GUI never equates auxiliary metrics with verification, or absence of history with absence of work.

**P-10 — INFERRED:** Milestone 1 consumes only CLI text and the two existing JSON outputs. Missing fields appear as unknown, unknown enum values remain visible, and malformed responses show a diagnostic with raw text available. No inferred success, guessed timezone, computed evidence verdict or fabricated chronology. Test against legacy, missing-field, unknown-field and contradictory-output fixtures.

## Screens and flows

**P-11 — INFERRED:** The window contains: (a) workspace/executable selection and identity; (b) History with a limit selector, Refresh and a selectable monospace output panel; (c) Workspace assessment with reported status, findings and advice plus original JSON; (d) command diagnostics with stderr, exit code and observation time. Flow: choose executable and workspace → inspect identity → explicitly refresh either panel → read outcomes/advice → perform any required action outside the viewer using Kriya. An empty result, failed refresh or changed workspace must never retain an apparently current success badge. Verify keyboard access, screen-reader labels, long paths and error states in a local walkthrough.

## Technology choice

**P-12 — INFERRED:** Prefer Python Tkinter/ttk for the first milestone: a native window and subprocess adapter without a browser server or JavaScript bridge. Availability of Tk is a prerequisite, not assumed from Python version; perform a packaging/startup smoke check on the owner's target OS before building. PySide is an alternative if accessibility or layout testing demonstrates Tk is inadequate; Electron/Tauri add packaging and privileged bridge work; a local web app adds listener/origin/session protections. These are architectural judgments, not measured comparisons. Decide by a small offline fixture prototype and owner usability review; no dependency installation during this discussion.

## Local safety and writes

**P-13 — TRACED:** “Read-only viewer” cannot mean guaranteed zero disk effects at this baseline. `traces` loads configuration and bootstraps logging (`kriya/cli.py:155`, `kriya/cli.py:164`); logging can create directories and file handlers (`kriya/core/logging_setup.py:97`, `kriya/core/logging_setup.py:127`). Its SQLite connection sets WAL pragmas and may create directories (`kriya/core/db.py:9`, `kriya/core/db.py:40`). Milestone 1 permits existing CLI-owned logging/SQLite housekeeping, but no GUI-owned persistent writes or source/control/configuration mutations. If the owner requires strict zero-write inspection, traces must remain disabled until a separately authorized command-contract change exists; the GUI must not bypass the command to achieve it.

**P-14 — INFERRED:** The GUI has no network listener, telemetry, external assets, browser rendering, automatic links or executable markup. All output is untrusted literal text; sanitize terminal control sequences while retaining newlines. Read output only into bounded memory; do not persist history, workspace paths or diagnostics. Clipboard copying requires an explicit user gesture. The child inherits existing operator credentials/authority; do not inject trust approval, permission overrides or model settings. Verify hostile filenames, ANSI/control characters, secret-bearing errors and command-injection strings. Local presentation does not independently prove the complete configured CLI stack has no network side effects; settle that using configuration-loading inspection and an offline process/network test before acceptance.

## Test plan and acceptance

**P-15 — INFERRED:** Build tests use a fake Kriya executable and sanitized recorded outputs; no live model runs are needed. Acceptance requires: exact allowlist enforcement; no shell or arbitrary arguments; no imports of execution/model APIs; literal rendering; correct valid-JSON/nonzero-exit handling; bounded output and process cleanup; stale-response rejection; explicit unknown/error states; usable keyboard navigation; and no viewer-owned persistent writes. A sandboxed offline integration check with existing CLI inspection commands must inventory source, config, control, logging and SQLite side effects before/after and confirm no model invocation. Passing fixture tests alone does not establish the integration claim.

**P-16 — INFERRED:** Owner acceptance is conditional on command identity/provenance being displayed accurately, history being labelled as the selected CLI configuration's history rather than workspace-filtered history, and recovery advice remaining informational. The trace query shown at `kriya/cli.py:4610` is not workspace-filtered (TRACED support for this condition). Unknown installed provenance remains unknown. Settle through fixtures with unrelated workspace history and an installed executable different from this checkout.

## Milestones and open questions

**P-17 — INFERRED:** M1 builds only the viewer described here after the owner's gate. M2, in a new topic folder, may propose structured history/detail only if an existing command supports it or the owner separately authorizes extending a command's read-only output. M3, also separately gated, may propose command launching; it must preserve existing approval and execution semantics and demonstrate interactive-terminal compatibility before any mutating button exists. These later milestones are not authorized by approval of M1.

**P-18 — INFERRED:** Owner decisions needed at the gate: target OS/distribution; whether existing CLI logging/SQLite housekeeping is acceptable or strict zero-write inspection is required; whether text history is sufficient for M1; acceptance of Tk pending its startup/accessibility check; and choice of installed Kriya executable/configuration. Resolve these through explicit gate conditions, not silent defaults.

**P-19 — INFERRED:** Next artifact is Claude's `02_REVIEW_1.md`. The proposer will respond to every finding in `03_PROPOSAL_v2.md`; Claude then writes the restricted second review. Sriram alone writes `05_GATE.md`. No third review, implementation, live model run, commit or push occurs as part of this proposal. Verification: inspect this folder and Git changes at each handoff.
