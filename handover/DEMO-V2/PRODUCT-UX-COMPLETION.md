# Demo V2 product UX completion + greenfield Hello World

Branch `demo/v2`. Starting HEAD `a083ecb7f52b4baef7346ac89b95a07c9fe16688` (== `origin/demo/v2`, tracked tree clean). The task text named `89a6e17` as the last owner-reported HEAD; it is stale: `a083ecb` already contains the main-process native confirmation for configuration approval (see `GUI-CONFIG-APPROVAL.md` section 8).

Evidence labels: MEASURED (observed by running something), TRACED (read in the exact code), DESIGN (a decision made here), INFERRED.

## 1. Phase 0: current implementation, traced

| Area | Finding |
|---|---|
| New Project | `workspace.create` (`kriya/kup/project_ops.py`): `os.mkdir` of ONE validated folder name. **No git init, nothing scaffolded, no configuration written.** (TRACED, docstring + code) |
| Open Folder | Native chooser in main; `WorkspaceRegistry` approves the folder; `workspace.describe`/`workspace.status` read it. (TRACED `ui/standalone/src/main/workspace_host.ts`) |
| Project initialization / config creation | **Does not exist.** No KUP operation writes `kriya.yaml`. The demo scenarios' `kriya.yaml` is a template rendered by `demo-v2/scripts/prepare_demo.sh`. (TRACED: no writer in `kriya/kup/*`; `serve.py` operation table) |
| Readiness of a brand-new empty folder | **MEASURED (scratch HOME, real `kriya kup-stdio`): `ready: true`, `blockers: []`, `configuration.source: packaged_default`, `file: null`, authority `no_findings`, model `qwen3-coder:30b`, fallback `qwen3.6:35b-a3b-q4_K_M`, `approvals: change_review`.** A greenfield folder needs no setup to be runnable. |
| What a `kriya.yaml` adds beyond the defaults | The demo template sets `llm.capabilities` (native tool calls, `preferred_edit_protocol: small_native_tools`), `workflow_controller: enforce`, isolated `paths.state/memory/skills`, `extra_body.options.num_ctx`. Those are tuning and isolation choices, not requirements. (TRACED `demo-v2/scenarios/01-normal-coding/config`) |
| Config approval | Already in product: KUP `configuration.approval_status` / `configuration.approve`, review fingerprint, dedicated IPC channel, `ConfigApproval.tsx`, main-process native confirmation (`a083ecb`), security review "NO" (a compromised renderer cannot approve). Reused unchanged. |
| Start Run / current run | `ExecutionHost` (main) -> `kriya generate --launch-id`; `core/runPhase.ts` derives the user-facing state from launch status + evidence tallies; `CurrentRun.tsx`; `ActiveRunBanner.tsx` strip on other pages. (TRACED; see `UX-CORRECTION.md`) |
| Change approval | `kriya/control/launch_approval.py`: PENDING review in the launch record bound to launch id, workspace identity, `review_id`, **`candidate_digest`** (sha256 over the per-file sha256 of the unified diffs the gate was given) and the reviewer text. `decide()` refuses a stale `review_id`/digest (`STALE_REVIEW`), a duplicate, an expired or ended review. (TRACED) |
| Where the exact diff exists | The gate builds it: `difflib.unified_diff(all_original_contents[f], worktree file)` per file (`kriya/workflow/workflow.py:5628-5645`) and passes `[{filepath, content}]` to `approval_callback`. **`build_pending` persists only per-file `diff_sha256` and line counts, never the text.** (TRACED `launch_approval.py:95-107`) |
| Content visibility | `evidence.attempt_recorder.content_visibility` (default `off`) gates `evidence.change_content` / `evidence.call_content`; with capture `full` the bytes exist in the sealed store but KUP refuses to serve them. The renderer's "Diff hidden by Kriya content-visibility policy" and "Approve without viewing diff" come from `core/ApprovalReview.tsx` lines 40-101. (TRACED) |
| Run history / run title | `history.list` projects `runs` rows of `traces.db` (`LIST_COLUMNS` includes `goal`). **MEASURED in the Scenario A state: the unit row (`a84846b6`) has `goal` = the 1635-char compiled "=== Authoritative Goal === ..." text; the terminal row `20261014T222738-ab97cfaa.enforce` has `goal` = the clean 506-char original request; the unit row names it through the structured column `enforce_run_id`.** `enforce_run_id` is not in `LIST_COLUMNS`. (MEASURED sqlite, TRACED `kriya/core/trace.py`) |
| Original request elsewhere | The launch record keeps only `goal_digest` = `{sha256, chars}` (`kriya/control/launch.py:58-59,86`). Not usable for display. |
| Run completion / failure | `deriveOutcome` from launch record + RunRecord; `CurrentRun.tsx`; Overview module. |
| Timeline / evidence audit / validation tabs | `modules/timeline`, `modules/evidence`, `modules/validation`, `modules/overview`, `modules/changes`, `modules/why`, `modules/context`, `modules/plan`, `modules/attempts` over `evidence.records` / `evidence.manifest`. Record-kind vocabulary is rendered directly today. |
| Final validation records | `runs.gate_outcomes` JSON: `{attempt, type, success, output, ...}`; kinds seen: `compile`, `goal_spec_compliance`, `regression_test`. (MEASURED) |
| Terminal obligations | `kriya/workflow/obligations.py` is MA8's cross-revision *requirement* ledger. It is **not** a record of commands run. |
| Command execution evidence | **Kriya has a Runtime Verification Gate** (`kriya/tools/validate.py` `run_app`/`run_runtime_sequence`, `attempt.py`): runs a resolved command in the sandboxed workspace and persists in the gate outcome `type` (runtime gate), `success`, `commands`, and `steps: [{command, exit_code, stdout, stderr, timed_out}]` (TRACED `attempt.py:6276-6287,10663-10686`; `run_verification_enabled` default true). Whether it runs for a given goal depends on the verification contract / planner (live-model dependent: NOT MEASURED for the Hello World goal). The working directory is the sandbox worktree, not the user's folder. |
| Start instructions | **No authoritative source.** Nothing in Kriya produces "how to start this application". |
| Open in editor | `openInIde` host port (`open_in_ide.ts`): files inside the open workspace only. Exists. |
| Skills / Tools pages | Exist (`modules/skills`, `modules/tools`), read-only. Unchanged. |
| Stop Run | No host operation (UX-CORRECTION.md section 4). Unchanged: not offered. |

## 2. Where the task text conflicts with measured reality (raised per the standing review-first rule)

1. **"Kriya identifies that project setup is needed" (state A, a blocker).** MEASURED: an empty new folder is `ready: true` on packaged defaults. Setup is therefore not a prerequisite. DESIGN: the Project page shows a *recommended* setup card when `configuration.file` is null and the folder has no `kriya.yaml`; Start Run stays available (a card that blocks something that is not blocked would be a lie). The setup writes a vetted local-model profile (the demo-validated capability block) through a narrow backend operation; its protected fields then go through the existing approval, exactly as required.
2. **Greenfield READY_TO_RUN / "Copy Command".** No authoritative source for a start command exists, and inventing one (parsing `pom.xml`) is forbidden by the same task. Only `EXECUTED` is implemented, as a read-only projection of the persisted runtime-gate outcome. A run whose goal never triggered the gate shows no Application section (`NOT_APPLICABLE`), never a guessed command. Stop condition 5 does not fire for EXECUTED; for READY_TO_RUN it would, so it is reported rather than worked around.
3. **"Run Again ... backend-authorized execution path".** None exists; not implemented (the task says "if supported").
4. **"Stop Run".** Still no host operation; not offered (the task says "if stoppable and authoritative").
5. **Approvals with no files.** In a desktop launch only a non-empty change set is held for a human (`_approval_decision`: every other approval is declined without prompting), so the diff review always has at least one file.

## 3. Design decisions (target)

* **Original user request (display_goal).** Narrow read-only backend projection: `history.list`/`history.detail` rows gain `display_goal` and `display_goal_source`. Resolution, no parsing: a row with `enforce_run_id` takes the `goal` of that enforce row; a terminal `.enforce` row and a plain row (no `enforce_run_id`, no `milestone` wrapper) take their own `goal`; an unresolvable link yields `null` and the UI shows a neutral "Engineering task" with the technical id under Details. The renderer never inspects prompt text.
* **Approval-scoped diff.** In the *launch process* (the one that holds the exact diff), `request_change_approval` writes the diff text of each file to an ownership-stamped sidecar next to the launch record BEFORE the pending record, with the same per-file `diff_sha256` the pending record carries. A write failure declines the review (fail closed). New KUP read `change_approval.review {workspace, launch_id, review_id}`: loads the launch record, requires the review to exist (pending or decided), re-hashes every file's text against `diff_sha256`, recomputes `candidate_digest`, refuses on any mismatch, and returns typed hunks. General `content_visibility` is not touched and `evidence.change_content` stays fail-closed. The renderer decides with the `review_id` + `candidate_digest` the backend returned; the existing `decide()` refuses a changed candidate (`STALE_REVIEW`), which the UI reports as "The proposed change changed while you were reviewing it".
* **No informed-consent action without inspectable subject matter.** "Approve without viewing diff" is removed. Approve is enabled only after the verified diff is loaded; otherwise "Unable to display the proposed change" with Open in Editor and Reject.
* **Presentation view-model.** `core/presentation.ts`: pure functions from authoritative facts (display goal, run view, gates, evidence records, application outcome) to `RunPresentation`. Components render it; they do not translate.
* **Application outcome.** Backend read-only projection over the persisted runtime-gate `steps` (`status` EXECUTED/NOT_APPLICABLE/UNKNOWN; command, exit code, bounded stdout/stderr, redacted by the existing secret redaction). No renderer inference, no new execution.
* **Project setup.** KUP `project.initialize {workspace, model?, fallback?}`: writes one schema-validated `kriya.yaml` from a vetted template, refuses an existing file, never writes `paths.*` outside the folder, and does NOT approve anything.

## 4. Informed-consent product rule

NO INFORMED-CONSENT ACTION WITHOUT INSPECTABLE SUBJECT MATTER. Configuration approval shows the authority requested (names, sources, fingerprint); source-change approval shows the exact patch. Future (not built): tool approval = tool + arguments + capability; lookup approval = query + destination; skill installation = source + provenance + capabilities.

## 5. Implementation record

(Appended as each slice lands.)
