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
