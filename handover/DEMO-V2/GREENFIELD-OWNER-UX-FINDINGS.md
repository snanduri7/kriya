# Greenfield owner rehearsal - Phase 0 findings

Owner run: `20261015T075602-2b5a0ad7` (Scenario 00, workspace `~/Downloads/delete/HelloWorld`), analysed at HEAD `2efcb89` on `demo/v2`.
Evidence was copied read-only (outside the repository) to `~/kriya-demo-v2-owner-run-evidence/`: the scenario-00 state directory (traces.db, the sealed 185-record attempt ledger),
the project's `.kriya/` control data (launch record, three reviews and three review-diff sidecars, plan, decisions) and the app logs. They are not committed (raw ledger blobs).

Labels: **M** MEASURED (read from the recorded files), **T** TRACED (read the producing code), **I** INFERRED.

## 1. The run

| Unit | Planned | Result |
|---|---|---|
| s1 | `pom.xml` (create) | candidate +51, gates passed, human approved |
| s2 | `src/main/java/com/example/HelloKriya.java` (create) | candidate +7 -0, gates passed, human approved |
| s3 | `src/test/java/com/example/HelloKriyaTest.java` (create) | candidate +14, test gate cases=1 COMPLETE, human approved |
| s4 | verification-only, `planned_files: []`, write scope `deny_all` | compile passed, then the source-change approval gate fired with an EMPTY change set and was declined |

(M) Plan `kind: milestone`, one acceptance criterion `ac1` "The application prints 'Hello from Kriya' when executed" (method `judgment`).
(M) `decisions.jsonl` `structured_plan_normalized` removed from s4 the verifier "Confirm that the application output includes 'Hello from Kriya'"
(`PLAN_VERIFICATION_SCOPE_NORMALIZED`). The remaining s4 verifier is `compile`, `requires_runtime_execution: false`.

## 2. Every human decision in the chain

| Seq | Review | Type | Subject | Candidate digest | Why it exists | GUI handles | Resumable | Outcome |
|---|---|---|---|---|---|---|---|---|
| 1 | `ra-204b6886...` | change_approval | s1 `pom.xml` +51 -0 | `sha256:4809690336ee...` | `autonomy.mode: human-in-the-loop` | yes | yes (awaitable) | APPROVED by a person, 02:27:41Z (40 s after the review became pending) |
| 2 | `ra-9d5db1d2...` | change_approval | s2 `HelloKriya.java` +7 -0 | `sha256:17eded07f178...` | same | yes | yes | APPROVED, 02:29:40Z (95 s) |
| 3 | `ra-3d9a6b99...` | change_approval | s3 `HelloKriyaTest.java` +14 -0 | `sha256:a371a8249975...` | same | yes | yes | APPROVED, 02:30:33Z (29 s) |
| - | none (no review id) | `other_approval` | s4: EMPTY change set (`details.files = []`) | none | the same HITL clause | NO: only a non-empty change can wait | no: declined synchronously | AUTO-DECLINED 02:30:38.080Z, 13 ms after the static-analysis record |

The owner approved **three** real source changes, not one. Each was followed by the renderer's second "Confirm approval" step (`ApprovalReview.tsx`, the in-card
`alertdialog`).

## 3. Answers to the six questions

1. **Was the second "Confirm approval" merely a second renderer confirmation for the SAME decision?** Yes (T). `setConfirming(true)` then `decide('approve')`; exactly one
   `launch-decision` approve is recorded per review (M, `approval_history[*].decision`). It carries no authority of its own and is redundant once the verified diff is on screen.
2. **Or a distinct authoritative approval?** No.
3. **What is `other_approval`?** (M) `approval_required = {kind: "other_approval", details: {files: []}, noted_at: 02:30:38.079Z}` in the launch record. (T) It is what
   `kriya/cli.py::_approval_decision` records when the workflow's approval callback is called with an EMPTY file list (`request_change_approval` returns `None` for `not files`).
4. **Which class?** The SAME pre-apply source-change gate (`workflow.py`, "4.5. Pre-Apply Human Approval Gate"), not code review, static analysis, a command/tool approval or a
   policy approval. (T) `need_human_approval` is `autonomy.mode == "human-in-the-loop" or sensitive_match or diff_size or process_profile`; none of its clauses requires that there
   is anything to approve. For s4 (nothing written) the HITL clause alone fired and `diffs_to_show == []`. The reason text the owner saw ("Human-in-the-loop review policy", an EMPTY
   "Automated Code Review", the static-analysis banner) is that gate's `escalation_reason`. `STATIC_ANALYSIS_NOT_CONFIGURED` was only the banner (outcome `DISABLED`,
   `permits_commit: true`, requirement `optional`); it blocked nothing.
5. **Why did the desktop stop instead of presenting the decision?** There was nothing to present. No pending review existed. The callback declined synchronously (a desktop launch
   never prompts), the workflow recorded `approval.decision: rejected` (ledger seq 177) and aborted `human_rejected` (traces.db row `a51bf342`, failure category `human_rejected`).
6. **Was the run already terminal, or could it have resumed?** Terminal at that instant: `run.closed {terminal_status: FAILURE, commit_result: NOT_COMMITTED}` (seq 185).
   No person rejected anything, and there was nothing a person could have decided.

Outcome: `human_rejected`, FAILURE, NOT_COMMITTED.

## 4. Owner project

(M) `~/Downloads/delete/HelloWorld` contained only the untracked `kriya.yaml` written by Project Setup. The three approved candidates were committed (three commit cycles, state
`committed`) only into Kriya's private isolation worktree (`.kriya/worktree`), which was empty afterwards. None of the changes reached the owner's project.

## 5. Presentation defects found in the Greenfield approval card (defects of the Product UX Completion work)

- (M) The first approval the owner looked at was an **intermediate** unit, s2 of four.
- (M) The `tests` gate recorded `test_execution {cases: 0, completeness: INDETERMINATE, reason: STRUCTURED_REPORT_MISSING}` with `success: true`. (T) `describeGate` /
  `presentation.ts` never read `completeness`, so it rendered "Tests passed (0 tests)".
- (M) "Requested behavior verified" is the **per-unit, model-judged** `goal_spec_compliance` gate (spec_compliance role) mapped by `presentation.ts` to a whole-goal claim.
- (M) Run-level `requirement.verdicts` at the end of the run were `REQ-1`, `REQ-2` = `pending` / `NOT_YET_VERIFIED`.
- (T) The card's verification was `runFacts.validationFacts` over the WHOLE live run, reduced to the latest gate per type, not scoped to the unit under review.
- (M) The unit-test requirement was satisfied only later (s3: `cases: 1`, COMPLETE); runnable verification was never reached, and its verifier had been normalised out of the plan.

Classification: **case A**, the backend was correct for an intermediate unit and the presentation overclaimed.

## 6. Root cause of the stop

A **workflow defect**, not a GUI gap: the pre-apply source-change gate has no "there is a source mutation to review" precondition, so a verification-only unit with zero files
asks a human to approve nothing, and the desktop (correctly: it cannot hold an empty review) declines it, which the workflow records as a human rejection and ends the run.

Owner decisions after this trace (2026-10-15): a small generic fix at the source-change gate ownership point (`NO SOURCE MUTATION -> NO SOURCE-CHANGE APPROVAL`), without
changing the `approval_callback([], ...)` contract used by other callers (`attempt.py` runtime-command approvals are a different subject and must be classified independently).
Implementation and verification records follow in `GREENFIELD-OWNER-UX-CORRECTION.md`.

## 7. Empty-change source approval: reproducer, fix, verification (owner decision 2 - done, local commit, not pushed)

- **Reproducer (before the fix)** `tests/test_empty_change_source_approval.py` through the real `WorkflowController` enforce loop and workflow (only the model transport and the toolchain
  gates stubbed; harness `tests/_enforce_unit_harness.py`): a plan with a producer unit and a no-file verification unit, `autonomy.mode = human-in-the-loop`, a recording approval
  callback. Before the fix the callback was called a second time, for the verification unit, with `files == []` and the same reason text the owner saw ("Human-in-the-loop review
  policy", an empty "Automated Code Review", the static-analysis banner). 3 of 6 tests failed on exactly that; the 3 guard tests (a real change asks once; a rejection records
  `rejected` and applies nothing) passed.
- **Fix** `kriya/workflow/workflow.py`, the pre-apply source-change gate (its ownership point): `need_human_approval = has_source_mutation and (...)` with
  `has_source_mutation = bool(state.all_files_written)`. NO SOURCE MUTATION -> NO SOURCE-CHANGE APPROVAL. No empty approval is manufactured and no `rejected` decision is recorded for
  nothing. Every non-empty change is approved exactly as before. The sensitive-path and diff-size clauses derive from the same file set, so an empty set could never fire them. The process-profile clause (`control.process_profile.human_review_required`, only when `process_profiles.enabled` and `enforce_approval` are both on) does NOT derive from the files: a profile-required review of a unit with nothing to apply is now skipped too - intended (there is nothing to review) and stated here.
  **Deletions (TRACED, pinned by a test):** the gate set is `state.all_files_written`. The only unit-level deletion staging (`attempt.py` `delete=True`, "abandoned candidates") removes run-created files that are absent from the real workspace, and discards them from the set; no unit stages the deletion of a pre-existing user file. A planned `DELETE` unit reaches the gate as a non-empty change (replay: one approval for `shop/service.py`, the file is not removed, run ends `needs_review`); `plan_terminal_writes` can only commit a delete the candidate already reflects and otherwise fails closed ("did not delete approved file"). So no approval that was previously asked for a real mutation is dropped.
  The `approval_callback` contract and the `attempt.py` runtime-command call sites are untouched.
- **After:** 6 of 6 pass. Mutations (each applied in place, run, restored): always-true 3 failed; inverted 5 failed; never-ask 6 failed; `and`->`or` 3 failed; all killed.
- **Adjacent suites** (1081 tests: `test_workflow`, `test_best_of_n`, the `state_machine` tier, autonomy, events, the P4 verification-only reproducers, the D3/D4/D5 runtime-verification suites,
  B3 human acceptance, enforce-verified-no-change) pass. `test_launch_approval` passes (45). One test in it failed once under a loaded `-n 6` run and passes alone, with and without the change, and in a full-file parallel run (load-dependent review expiry; unrelated).
- **Behaviour change to be aware of:** a terminal human-in-the-loop user is no longer asked "Do you approve applying these changes?" for a unit that changed nothing.

## 8. Controlled continuation past the old stop - what Kriya does next (steps 7-9; NO live model)

`tests/test_greenfield_runtime_verification_dropped.py` replays the owner's exact goal text and plan shape (s1 `pom.xml`, s2 main class, s3 unit test, s4 verification with the Planner's
real verifiers: compile + `{"type":"judgment","verifier_kind":"application_runtime","requires_runtime_execution":true}`, read from the recorded planner response) with
`run_verification_enabled: true`, the recorded run-verification judge answer (`mvn exec:java -Dexec.mainClass=com.example.HelloKriya`, `goal_explicit`) and a recording callback.

| Goal wording | Result after the empty-approval fix |
|---|---|
| The owner's: "... prints \"Hello from Kriya\" and includes a unit test. Make the application runnable." | exactly three approvals (no fourth); all four units `completed`; the run reports **success**; the judge is never consulted at s4; **the application is never executed** (`executed == []`) |
| "... Run the application and confirm it prints \"Hello from Kriya\"." (recognised by the detector) | the three change approvals, then s4 calls `approval_callback([], "Kriya judged that this goal describes runtime behavior ... Allow Kriya to execute these command(s) inside the sandboxed worktree?")` - a **runtime-command approval**. A desktop launch declines it (other_approval) so nothing runs and s4 fails; with an approving callback the command runs |

### Why the runtime check was dropped (CONFIRMED by the replay)
1. (M) The Planner's s4 carried the runtime verifier "Confirm that the application output includes 'Hello from Kriya'" (`application_runtime`, `requires_runtime_execution: true`).
2. (T) `workflow_controller` calls `scope_verification_to_requirements(plan, goal_requires_runtime_behavior(goal))`; with `False` a runtime verifier is "a verification category the Planner
   invented" and is removed when the unit keeps another evidence-producing verifier (s4 kept `compile`). Record: `PLAN_VERIFICATION_SCOPE_NORMALIZED`, `verifier_removed`, subtask s4.
3. (M) `goal_requires_runtime_behavior(<owner goal>)` is `False`. It is a deliberately narrow regex (acceptance.py:119-134, built after live false positives P2/P3): it needs
   `run|start|launch|execute|... (the) application|app|program|...`, process-output language (exit code, stdout, stderr) or `shut down`. "prints ..." and "Make the application runnable"
   match none of it; "The application must run and print ..." and "Verify by running the application" also return `False`; "Run the application and confirm it prints ..." returns `True`.
4. (T) s2's judge did ask to run the main class (`should_run: true`), but `_runtime_verification_is_advisory_only` suppresses an undeclared inference ("a later subtask that DOES own this
   obligation still runs it"). No later subtask owns it after step 2.
5. Net: a gap in Kriya's goal-reading vocabulary (generic, not HelloWorld-specific), not a GUI problem. Registered: `GREENFIELD-RUNTIME-VERIFICATION-DROPPED-001`.

### The runtime-command approval (step 10 classification)
- **Subject:** permission to execute model-chosen shell commands, as a list of argv arrays, plus the success criterion ("Looking for: ...").
- **Where it fires:** `attempt.py` `_execute_runtime_verification_directly` (verification-only unit) and `run_attempt`'s inline block, only when `autonomy.mode == human-in-the-loop`, the
  command source is `inferred` (a `goal_explicit` claim is downgraded when the command is not grounded in the goal text) and it was not already confirmed.
- **Exact text shown in the replay** (pinned: the numbered commands are exactly what then ran):
  ```
  Kriya judged that this goal describes runtime behavior compile/test checks can't verify, and wants to actually run the generated app (verification-only subtask):
    Command(s):
      1. javac -d .kriya/runtime-verification/classes src/main/java/com/example/HelloKriya.java
      2. java -cp .kriya/runtime-verification/classes com.example.HelloKriya
    Looking for: The command output contains the text 'Hello from Kriya'
  Allow Kriya to execute these command(s) inside the sandboxed worktree?
  ```
  The recorded live judge answer was `mvn exec:java -Dexec.mainClass=com.example.HelloKriya`; in the replay the deterministic Java-entrypoint grounding replaced it with `javac`/`java`
  (INFERRED: because the replay's s4 worktree has only a stub `pom.xml`; what the live run would show is UNKNOWN without a live run). Arguments are plain argv; the working directory is the
  candidate worktree root; no environment, stdin or network is part of the shown subject.
- **Working directory / authority:** the sandboxed candidate worktree (`.kriya/worktrees/candidate-...`); execution under the existing containment/capability policy.
- **GUI representation:** none. `cli._approval_decision` records any empty-file approval as `other_approval` and declines it. The change-review card, its digest-bound decider and the
  `change_approval.review` proof are all file-diff specific.
- **Resumable:** the two runtime-command call sites already `await` an awaitable returned by the callback (the in-process workflow can wait, as it does for a change); the escalation
  site at `attempt.py:6854` does not. A safe desktop version needs a distinct typed review (argv, cwd, containment, criterion, digest-bound decision), a host path and UX - a new
  approval class, not a small fix. Registered: `GUI-RUNTIME-COMMAND-APPROVAL-001`. NOT silently skipped, NOT built.

## 9. Stop (owner decision required) - STOP CONDITIONS 8/9 of the 2026-10-15 task

Demo 00 as worded cannot produce runtime proof today: either it succeeds without ever running the application, or (with a recognised wording) it needs a desktop approval class that does
not exist. The UX correction (one-click approval, intermediate-vs-goal verification wording, zero-test wording, failure mapper, Activity drawer, Project Details, Why/Markdown, Recent
Runs, typography) is independent of that and was NOT started in this session. Options for the owner:

1. **Narrow Demo 00** to what Kriya can prove today: build + unit test, the application NOT run. Cheapest, but only honest once the presentation items land: today's UI would show
   "Run completed" and a whole-goal "Requested behavior verified" (section 5) for a run that never executed the application. The backend projection returns `NOT_APPLICABLE` when no
   runtime gate step is recorded (TRACED, `application_outcome.py`); what the renderer then shows for `NOT_APPLICABLE` is NOT verified here (INFERRED: nothing, or a neutral line).
2. **Widen the detector** generically (`runnable`, `prints` next to an application noun) using the P2/P3 corpus as the regression set. Then Demo 00 reaches the command approval and
   needs option 3.
3. **Build a distinct desktop runtime-command review** (new typed subject). Larger; needs your approval as a design.
4. Options 2 + 3 together are the full "EXECUTED output" path the Product UX task assumed for Demo 00.

## 10. Phase 0 of the Electron-host task (2026-10-15, pass 3): two pre-existing execution paths, classified BEFORE any Electron work

### 10-A. Managed-service mode: **DESKTOP-REACHABLE BYPASS** (STOP CONDITION 1)

- **Observation (MEASURED by reading the code, no run):** a runtime-verification judgment with `execution_mode == "managed_service"` is executed with NO approval of any kind. Neither
  `_runtime_commands_need_approval` nor `_approve_runtime_commands` is consulted, and `autonomy.mode == "human-in-the-loop"` is never read on that branch.
- **Producing path (TRACED):** `kriya/workflow/attempt.py` - site 1 `_execute_runtime_verification_directly` (`if execution_mode == "managed_service": await _execute_managed_service_verification(...); return`,
  ~line 6041, BEFORE the approval block at ~6136 that only the finite-command branch reaches); site 2 `run_attempt` inline block (`if judgment.get("should_run") and execution_mode == "managed_service":`,
  ~line 10105, before the approval block at ~10232). `_execute_managed_service_verification` (~5701) -> `run_managed_service_verification` (`kriya/tools/service_runtime.py`) starts the process.
- **Who chooses it (TRACED):** `RunVerifierAgent.judge()` (`kriya/agents/agent.py` ~2357-2390) - the MODEL decides `execution_mode`, `service_command`, readiness and probe. Model text selects both the mode and the
  command that runs; `command_source` is not consulted. `_resolve_execution_mode` only validates the string.
- **What bounds it today (TRACED, not a substitute for approval):** `_validate_and_convert_managed_service_contract` rejects shell-compound shapes; `ExecutionPolicy` is audit-only except hard codes; the
  service itself is NOT contained - only its artifact-preparation build step is, and only when `autonomy.contained_execution_required` (default False) is on (comment at ~5748, `service_runtime.py` docstring).
- **Why DESKTOP-REACHABLE (INFERRED from the call-site trace, discriminating check not run):** Desktop and Demo V2 run the same `run_attempt`; nothing in the Desktop path restricts the judge's
  `execution_mode`, and the new RuntimeCommandSubject is built only on the finite-command branch. Any Desktop goal that names a foreground server/service (e.g. "start a web server and respond on /health")
  can be judged `managed_service` and run with no human decision. Demo 00 itself is finite-command (INFERRED: `controlled_script.json` of scenario 03 shows `finite_command`; scenario 00/01/02 judge
  behaviour is not measured here, and the live demos 01/02 in `~/kriya-live-demo` are outside this repo, so whether they use managed_service is UNKNOWN).
- **Consequence for the plan:** any GUI approval story is bypassable by a judge that answers `managed_service`. Per the task's own rule this STOPS the Electron/renderer work.
- **Minimum generic correction (proposed, NOT implemented - needs an owner decision):**
  - **Option 1 (smallest, recommended for Demo V2):** at both call sites, under `human-in-the-loop`, a managed-service judgment that is not already approved fails CLOSED with a typed
    `verification_infrastructure_failure` reason code (e.g. `MANAGED_SERVICE_APPROVAL_UNSUPPORTED`) before any process starts; no new UI. Honest, tiny, but it makes managed-service verification
    unavailable in HITL mode (a behaviour change for any flow that relies on it; the existing managed-service tests run in non-HITL or must pin the mode explicitly).
  - **Option 2:** extend the typed runtime subject with a service kind (service_command, working dir, readiness, probe request, preparation build), fingerprinted and answered by the same launch layer
    and KUP review kind, then renderer + native confirmation. Correct, but it is exactly the "approval-platform growth" STOP CONDITION 8 warns about; it should be its own slice.
  - Not recommended: treating the judge's `command_source` as authority (model text is evidence, never authority).
- **Registry:** NOT added. An open P1 may not be parked in `BACKLOG_REGISTRY.csv`, and this one is not classified "separate from Desktop".

### 10-B. Self-correction runtime re-run omitted stdin: **REAL DIVERGENCE, FIXED**

- **Observation (MEASURED, pre-fix):** `tests/test_runtime_stdin_rerun_binding.py::test_rerun_after_self_correction_carries_the_reviewed_stdin` recorded
  `run_app_sequence` stdin `['kriya-verification-input', None]` - the first run carried the contract's stdin, the self-correction re-run carried none.
- **Producer (TRACED):** `attempt.py` plain-nonzero-exit branch, after `self_correction_result.resolved`, re-ran `validator.run_app_sequence(resolved_run_commands, timeout=...)` without
  `stdin_payload=rv_stdin_payload`; `process.py` then feeds `stdin_payload or ""`, i.e. EMPTY stdin.
- **Answers to the Phase-0 questions (TRACED):** it occurs only AFTER the first run, i.e. after any RuntimeCommandSubject was reviewed (the approved fingerprint covers `stdin_sha256`, so a non-empty reviewed
  stdin ran the second time with different input and no re-review). It cannot happen for `input_channel` `none`/`argv` (payload is None either way). Demo 00 has no stdin, so it cannot reach the divergence.
- **Fix:** pass `stdin_payload=rv_stdin_payload` on the re-run (the same resolved commands, cwd, channel and stdin the approval covered).
- **Post-fix:** the same test now sees `[X, X]`; control `input_channel=none` stays `[None, None]`; the adjacent self-correction re-run tests (`test_repair_003_f1_residual_*`) pass; ruff and pylint clean.
  Mutation: the pre-fix code IS the mutation (test failed without the fix).
- **Other surface noted, NOT traced:** `kriya/workflow/milestones.py` (~1088) replays persisted milestone verification commands via `run_app_sequence(resolved_commands)` with no approval check and no stdin.
  Reachability from Desktop is UNKNOWN; it replays commands from an earlier run rather than judge output. Needs its own trace before any claim.

### 10-A (resolution, 2026-10-15 pass 4): OWNER DECISION = OPTION 1, implemented

- In human-in-the-loop mode a managed-service verification fails CLOSED with a typed `verification_infrastructure_failure`, reason code `MANAGED_SERVICE_APPROVAL_UNSUPPORTED`, raised by
  `_refuse_managed_service_without_approval_subject` at the TOP of `_execute_managed_service_verification` - the one function both call-site families route through - so it fires before the
  contract is admitted (hence before the interpreter resolver can prepare an environment), before `run_managed_service_verification` and before any process start. Nothing is asked
  (`approval_callback` is never called with `files=[]`), `run_verification_declined` stays False (this is not a rejection), and the existing infrastructure classification stops the run with
  no Developer retry (`STOP_ENVIRONMENT`, asserted through `handle_attempt_failure`). Every other autonomy mode is unchanged.
- Evidence: `tests/test_managed_service_hitl_fail_closed.py`. PRE-FIX (MEASURED): the three A-cases failed (managed service ran to success in HITL, both call-site families); the control cases passed.
  4 mutations killed (remove the call; invert the mode test; never fail; move the guard after admission).
- Existing managed-service mechanics tests that ran under the default HITL config were pinned to a non-HITL mode on purpose (they test admission/execution mechanics, which HITL no longer reaches).
- Deferred, registered: `MANAGED-SERVICE-APPROVAL-001` (P2, DEFERRED, owner-approved). Not built: service approval card, ports/listeners, lifecycle, containment redesign.
- User-facing mapping (for the presentation slice): "Runtime verification could not continue - this goal requires Kriya to start a long-running service. This desktop version does not yet support approval
  for managed-service execution. No service was started." Raw code under "Technical reason".

### 10-C. `milestones.py` replay: classification **C - UNREACHABLE FROM THE CURRENT DESKTOP FLOW** (a CLI-only unapproved-execution finding remains, registered)

All TRACED unless marked.

1. **Callers:** `replay_prior_milestone_verifications` (milestones.py:1058) <- `_MilestonePlanDriver.run_phase(REPLAY_PRIOR_VERIFICATIONS)` (:1470) <- `run_milestones` (:1495/:1750) <- `WorkflowController.execute_milestones`
   (workflow_controller.py:3866) <- `kriya/cli.py:2827`, inside `run_milestone_sequence`, which exists only under `if from_milestones:` (`kriya generate --from-milestones <plan.json>`). No other caller exists.
2. **What is replayed:** `run_state.verification_commands[milestone_id]` = argv lists. **Who authored them:** `_capture_verification_commands` (:1411) makes its OWN `run_verifier.judge()` call after the milestone
   completes (a third judge call site, independent of attempt.py) and stores `judgment["run_commands"]` (Python-target grounding only); the commands are MODEL-DERIVED.
3. **Previously approved? Fingerprint persisted?** No. The capture is not shown to any human, no RuntimeCommandSubject is built, nothing is fingerprinted; `MilestoneRunState` persists only the argv lists.
   So there is no approved subject whose semantics the replay could contradict (the Phase-11 "stdin changes a previously approved subject" clause does not apply).
4. **Same subject?** No: replay runs `validator.run_app_sequence(resolved_commands)` on the WORKSPACE (`PolymorphicValidator(workspace_path, ...)`), not Kriya's isolated worktree, with no `stdin_payload`
   and without the argv/stdin input contract (`input_channel` is not captured), so even the judged semantics are not reproduced. Stdin is omitted because the capture stores `run_commands` only.
5. **Desktop reachable? NO.** The desktop host's argv is fixed to `generate --json --launch-id <id>` with the goal on stdin (`ui/standalone/src/main/execution_host.ts:70`; pinned by
   `ui/standalone/test/execution_host.test.ts:69`, which forbids `--from-milestones`), and `_launched_generate` (cli.py:2902-2918) raises a `UsageError` when `--from-milestones` is given
   with `--launch-id`. A plain goal never enters `run_milestones` (the MILESTONE route kind in `workflow_controller.py:3591` only attaches metadata). **Demo V2 reachable? NO** (same path).
6. **Model-derived command through it without the new boundary?** YES in CLI milestone mode, not from Desktop. That is the registered follow-up `MILESTONE-REPLAY-UNAPPROVED-COMMANDS-001` (P2, OPEN, target
   scope: before milestone mode is exposed to the desktop or described as approval-gated). INFERRED-UNKNOWN: whether any non-Desktop route (MCP, REPL) can start milestone mode; the MCP/REPL entry points were not traced.
7. **Boundary claim:** the runtime-command approval is a Desktop security boundary; it is NOT claimed to cover CLI milestone mode. No code was changed for this path.

### 10-D. What the runtime-command boundary does NOT cover: planner-emitted TOOL subtasks (TOOL-001/002/003), traced 2026-10-15 pass 4

- **Claim being avoided:** "no model-derived command can execute from Desktop without a human decision". That is NOT established, and this document does not make it.
- **TRACED (code, `kriya/agents/agent.py` ~429-497; `kriya/workflow/workflow_controller.py` 4159/4611; context note `kernel-mcp-and-tool-authority.md` TOOL-001):** the Planner may emit a subtask with
  `execution_method: "tool"`; `validate_plan` accepts any REGISTERED tool name (`kernel.registry.list_components("tool")`); `_run_structured_enforce` runs it through the one governed boundary
  `kernel.registry.get("tool", ...) -> BaseTool.execute()`. The authority there is the tool's own: `ExecutionPolicy` hard invariants + command allowlist for ShellTool/GitTool, TOOL-002 durable
  MCP invocation approval (`kriya mcp approve`, a CLI action), TOOL-003 containment, SEC-005 network. TOOL-001's own statement: exactly as safe as `kriya tools execute`.
- **What is NOT in that path:** no per-run human review, and no `RuntimeCommandSubject`. A desktop launch does not turn these into reviews. So the runtime-command approval is a boundary for
  **Kriya's runtime-VERIFICATION executor** (finite commands, plus the HITL managed-service refusal), not for TOOL subtasks.
- **Desktop reach: UNKNOWN as exercised** (a planner must choose `tool`; nothing measured here shows Demo V2 doing so), reachable in principle. Demo 00's plan uses `execution_method: model`
  with `verification` tool entries (compile/test), which are the gate runners, not TOOL subtasks.
- **Registered:** `TOOL-SUBTASK-DESKTOP-REVIEW-001` (P2, OPEN, owner decision before the product is described as "every model-chosen command is reviewed").


### 10-E. PRV-13 targeted check (2026-10-15 pass 4, REPORT ONLY - no frozen evidence touched, no live model)

- **The five goal copies** (identical, sha256 `a9c1cedde8...`): `kriya-prv-harness-1.0/scenarios/PRV-13/goal.md`, `.../hardened-self-correct/goal.md`, `kriya-prv-harness-1.0.1/scenarios/PRV-13/goal.md`,
  `.../hardened-self-correct/goal.md`, `kriya-production-readiness-pack-v3/PRV-13-bounded-self-correction/hardened-self-correct/goal.md`. The goal asks that "the application prints: CUSTOMER=<id>:<name>".
- **MEASURED (`goal_requires_runtime_behavior`, the detector at `b8cc982^` loaded from git vs. today's):** prior `False` x5, now `True` x5.
- **Prior behavior (TRACED from the detector's role):** the runtime verifier was scoped away from the plan and no unit owned the obligation, so the run could succeed without ever executing the application.
  **New:** the obligation is kept; a run on this goal needs runtime verification and executes the application (a finite command).
- **Configuration impact (MEASURED from `kriya.hardened-self-correct.yaml` and `prv.py`):** `run_verification_enabled: true` (so no `REQUIRED_RUNTIME_VERIFICATION_DISABLED`), `autonomy.mode: human-in-the-loop`, and the harness runs
  `kriya ... generate -f goal -y`; with `-y` the runtime-command callback approves the shown set automatically and the approval is bound to that set (MEASURED: `cli._runtime_command_decision(subject, True)`
  returns a decision whose fingerprint equals the subject's). The goal describes a finite command, not a long-running service, so the HITL managed-service refusal does not apply to it (INFERRED from the goal text; not run).
- **Targeted result:** detector flip proven x5; `tests/test_runtime_detector_matrix.py` (the goal row is a permanent positive) and `tests/test_runtime_command_authorization.py` pass (58). A live PRV-13 re-qualification is NOT run
  (no live model by instruction): whether the self-correction behavior the scenario certifies is unchanged with an added runtime-verification gate is UNKNOWN until the owner chooses to re-qualify. No benchmark redesign is implied.
