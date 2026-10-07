# 02 — Review 1 (Claude, reviewer)

**Reviews:** `01_PROPOSAL.md` (ChatGPT).
**Baseline:** `61a867fc`.
**Date:** 2026-10-04.
**Method:**
- read-only inspection of the baseline (`kriya/cli.py`, `kriya/core/trace.py`, `kriya/core/db.py`, `kriya/config/config.py`, `kriya/control/recovery.py`);
- no code run and no model run.

## Verdict

**REVISE.** The authority boundary and the safety handling are strong. I would keep P-2, P-3, P-5, P-6, P-7, P-10 and P-14 almost unchanged.

The problem is value. Milestone 1 as written is a window around three CLI outputs, one of them a verbatim text table. It shows nothing the terminal does not already show. It does not exercise the thing the GUI exists for, which is inspecting what happened inside a run. And it fixes a technology choice against that trivial scope.

There are 3 MAJOR findings and 5 MINOR ones. There are no BLOCKERs.

## Claims verified

| Claim | Check | Result |
|---|---|---|
| P-4 | `traces` prints a text table only; `runs status --json` exists | **Confirmed.** `kriya/cli.py:4610-4660` builds the table with a SQL query and `click.echo`, and has no JSON option. `runs_status` is at `cli.py:3824-3844`. |
| P-7 | Valid JSON is returned together with a nonzero exit code | **Confirmed.** `cli.py:3840-3844` exits `_RUNS_EXIT_RUN_ACTIVE` / `_RUNS_EXIT_ACTION_REQUIRED` *after* printing the JSON. |
| P-8, P-16 | History is not scoped to a workspace | **Confirmed, and it is a schema fact.** The `runs` table (`kriya/core/trace.py:32-75`) has **no workspace column**, so no command can filter history by workspace. |
| P-13 | `traces` writes on read | **Confirmed.** `db.wal_connect` sets `journal_mode=WAL` (`kriya/core/db.py:9-16`). The main group bootstraps logging (`cli.py:155-166`). |
| P-14 | Loading the workspace config is safe | **Confirmed.** `load_config` applies SEC-009 (`kriya/config/config.py:2071-2100`). An unapproved workspace config loses its repository-sourced authority, so running the CLI with `cwd` set to a hostile workspace does not grant that workspace anything. |
| (new) | A run-detail JSON command exists | **None exists.** The `runs` group has inspect/approve/revoke/status/recover/prune. The only `show` commands are `skills show` (`cli.py:1972`) and `proposal show` (`cli.py:3930`). |

## Findings

### R1-1 — MAJOR — Milestone 1 delivers almost no value; add a small read-only JSON contract to M1 instead of deferring it to M2

- **Evidence: TRACED.**
  - The only run-history output is the 7-column text table (`cli.py:4640-4656`): goal cut to 40 characters, no run id shown.
  - The detail the owner needs is already stored but has no command that exposes it: `run_events`, `evidence_records`, `gate_outcomes`, `model_hops`, `generation_metrics`, `failure_report` (`trace.py:32-49`).
- **The proposal's position:**
  - It correctly refuses to parse the table (P-4) and to read SQLite directly (P-3).
  - Taken together, those refusals leave M1 with nothing to show.
  - The fix is the contract, not a workaround.
- **Required change:** M1 includes one owner-authorized, **read-only** Kriya command, for example:
  - `kriya traces --json [-n N]`: rows from `runs`, with run ids and timestamps exactly as stored;
  - `kriya runs show RUN_ID --json`: one run's events, evidence, gate outcomes, model hops, metrics and failure report.
- **Rules for that command:**
  1. It emits a `schema_version`.
  2. It excludes `prompt_rendered` by default. That column holds full rendered prompts, which can be large and contain source code; include it only behind an explicit flag.
  3. Unknown or legacy values are passed through, never guessed.
  4. It obeys the zero-write rule in R1-2.
- The GUI keeps P-3 (subprocess only). The new command is the only Kriya change, and it gets its own tests.
- If the owner does not authorize the command, the revised proposal must say plainly that M1 is a terminal-output viewer, and the gate should weigh whether that is worth building.

### R1-2 — MAJOR — Make inspection genuinely write-free rather than accepting writes (P-13)

- **Evidence: TRACED.**
  - `version` and `doctor` already skip loading config or bootstrapping logging (`cli.py:150-170`), so a command that writes nothing has precedent.
  - The new read command (R1-1) can follow that pattern:
    - no logging bootstrap;
    - SQLite opened as `file:…?mode=ro` without the WAL pragma;
    - no directory creation.
- **Check required (INFERRED until measured):** a read-only WAL database still needs its `-shm` file, or a writable directory. Measure, on a fixture database with and without `-wal`/`-shm` present, whether `mode=ro` succeeds. If it does not, choose explicitly between `immutable=1` (which can give stale reads during a live run, and must be shown as such) and a typed refusal.
- **Acceptance:** a file inventory of the state dir, log dir, workspace and `~/.kriya` before and after each inspection command shows no change.
- **Why this matters:** "read-only viewer" should be literally true. Accepting housekeeping writes (P-13) weakens the property that makes it safe to point the GUI at a workspace in the middle of a run.

### R1-3 — MAJOR — The technology choice is judged against M1, but M2 and M3 are what decide it; one alternative is missing

- **Evidence: INFERRED.**
- **The concern:** the GUI's purpose is rich inspection:
  - diffs;
  - long event logs with search;
  - JSON trees;
  - tables;
  - eventually live progress.

  Tk handles all of these poorly, and they would force a rewrite at M2.
- **Owner context:**
  - The target is macOS on an M1 Max.
  - Tk on macOS depends on how Python was installed (Homebrew needs `python-tk` separately) and has weak VoiceOver support. That conflicts with P-11's screen-reader check.
- **Missing alternative:** a **static HTML report generator**.
  - `kriya-view render [--run ID]` turns the JSON contract (R1-1) into one self-contained HTML file and opens it in the browser.
  - No server, no listener, no JavaScript bridge, no network: a smaller attack surface than a local web app.
  - Richer rendering than Tk.
  - "Refresh" means rendering again.
  - It extends naturally to M2 (detail pages). A local web app can come later, for M3 live progress only, behind its own gate.
- **Required change:** compare Tk, static HTML and local web app (127.0.0.1 with a per-session token) against M1 **and** the stated M2/M3 needs. The comparison must include a timeboxed prototype that renders a 2,000-line diff and a 4 MiB event JSON from fixtures, and a VoiceOver check on macOS.

### R1-4 — MINOR — The UI is organised around "select a workspace", but history is global

- **Evidence: TRACED.** There is no workspace column in `runs`; the history location comes from the state-dir configuration (`kriya/core/state_paths.py:64-76`).
- P-16 labels this correctly, but the layout still suggests the history belongs to the selected workspace.
- **Suggestion:** two top-level contexts:
  - **History store**, identified by the resolved state directory;
  - **Workspace**, for recovery status.
- Show the resolved state path. If the owner wants history per workspace, that is a schema change for a later milestone.

### R1-5 — MINOR — Testing must not touch the live 80-run CAGC matrix

- The PROTOCOL_v2 matrix is running now (started 2026-10-04, about 16 hours).
- Until it finishes:
  - fixture and fake-executable tests only;
  - no runs of the real `kriya` CLI;
  - nothing pointed at `~/kriya-cagc-v2/` or `~/.kriya`.
- The real-CLI integration and side-effect inventory in P-15 waits until the matrix is done.

### R1-6 — MINOR — Version the output contract

- `runs status --json` has no top-level schema version. `RecoveryAssessment` items carry one (`recovery.py:153`), but the assessment as a whole does not.
- The GUI should check the installed `kriya version --json` against a supported range, and show "unsupported Kriya version" rather than mis-rendering.
- The new command (R1-1) carries its own `schema_version`.

### R1-7 — MINOR — Say where the GUI lives and how it is installed

- Not specified.
- **Suggestion:**
  - its own top-level package (for example `kriya_view/`), never imported by `kriya/`;
  - the import direction is enforced by a test;
  - an optional extra (`pip install -e .[view]`);
  - no new runtime dependency for Kriya itself.

### R1-8 — MINOR — Small corrections

- **P-14:** `click` removes styling when stdout is not a TTY, so the CLI output will contain no ANSI codes. Keep sanitisation as defence in depth, but do not describe it as a known threat.
- **P-6:** 60 s and 4 MiB are reasonable. With R1-1, size the limit for `runs show` from fixtures: `run_events` can be large.

## What the revised proposal (`03_PROPOSAL_v2.md`) must contain

1. A response table for R1-1 to R1-8.
2. The read-only JSON contract (R1-1, R1-2): commands, fields, the excluded columns, `schema_version`, zero-write behaviour, and the decision on read-only WAL behaviour.
3. The technology comparison with the prototype criteria (R1-3), or an explicit owner question if the proposer wants the owner to choose.
4. Updated owner questions for the gate:
   - authorize the read-only command;
   - zero-write required (yes/no);
   - the technology;
   - whether history per workspace is needed.
