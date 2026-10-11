# GUI final: human change review (Approve / Reject) and certification

Branch `gui/run-cockpit-v1_1`. Local only: not pushed, not merged. Owner decision: explicit human review; NO auto-approve, NO `-y`, NO "always approve", NO GUI autonomy selector (the resolved configuration's autonomy mode is shown and respected, never changed).

## 1. The one trace (what Kriya already had)

| Question | Answer (TRACED in the code) |
|---|---|
| What is approved | The change gate (`kriya/workflow/workflow.py`, "4.5 Pre-Apply Human Approval Gate"): `diffs_to_show` = a list of `{filepath, unified diff}` computed from the sandbox candidate against the baseline, plus the reason string, which carries the pre-approval Reviewer's report, ownership findings and static-analysis banner. |
| Existing durable state | None for the decision itself. The only durable trace is the `approval.decision` event (approved / rejected / unavailable) written to the evidence store and the trace row AFTER the callback returns. There is no pending-review record, review id, candidate digest, or CLI/API that performs an approval (`kriya authority` / `kriya mcp` approvals are for other authorities). |
| How the workflow resumes | In process: the gate calls `approval_callback(diffs, reason)` and awaits it if it is a coroutine. True -> regression gates, apply; False -> `_abort_without_applying("human_rejected")`. |
| Other callers | Every other `approval_callback` call site passes `[]` (knowledge gap, runtime verification command, policy action). The knowledge-gap site does NOT await the result. |

Design consequence: the callback stays the single continuation point. A launch has no terminal, so instead of prompting it records a PENDING review and waits for a durable decision (below). No GUI-only authority exists.

## 2. Architecture

```
Renderer (Approve / Reject)
  -> typed preload IPC  decideApproval {workspace, launch_id, review_id, candidate_digest, decision}
  -> Electron main: ApprovalHost (validate; open folder only; one in flight; fixed child env; no shell)
  -> kriya launch-decision --launch-id --review-id --candidate-digest --decision   (cwd = folder)
  -> kriya/control/launch_approval.decide(): validates against the DURABLE launch record, writes ONE exclusive decision file
  -> the waiting `kriya generate --launch-id` process (await_decision) re-validates the file, records APPROVED/REJECTED in the
     launch record, THEN returns True/False to the workflow's own callback site
```

- **Pending review (durable, written before waiting):** `launch record.pending_approval`: `review_id` (sha256 over launch id, workspace identity, sequence, candidate digest, reason digest), `candidate_digest` (over the per-file diff digests, in order), files with `diff_sha256` and line counts, Kriya's reviewer text VERBATIM (bounded), `status`, `expires_at`.
- **Binding:** a decision carries the launch id, review id, candidate digest and (implicitly) the folder; Kriya checks each against its record and the folder's workspace identity. A retry that produces a new candidate creates a NEW review (older ones go to `approval_history`, EXPIRED); a decision for the old one is `STALE_REVIEW`. Never "whatever is current".
- **Refusals (closed codes):** `INVALID_REQUEST`, `LAUNCH_NOT_FOUND`, `LAUNCH_RECORD_UNREADABLE`, `WRONG_WORKSPACE`, `LAUNCH_ENDED`, `LAUNCH_NOT_ACTIVE` (the process holding the folder is gone), `NOT_PENDING`, `STALE_REVIEW`, `ALREADY_DECIDED`, `REVIEW_EXPIRED`.
- **Fail closed everywhere:** no durable record -> nothing is approved; unreadable/forged decision file (any identity not matching) is not a decision; a review not decided within an hour, or whose host vanished (parent process changed), or whose run was interrupted (SIGINT) is closed EXPIRED and declined; a launch that ends with a PENDING review closes it EXPIRED.
- **Only the change gate can wait:** the callback returns an awaitable only for a non-empty change set (pinned by a source tripwire: exactly one call site passes non-empty files); every other approval is declined synchronously and recorded as `approval_required` (kind `other_approval` / knowledge gap), because those call sites would read an unawaited coroutine as approval.
- **Persistence / reload:** the review lives in Kriya's launch record. A renderer reload re-attaches to the host's running launch (`port.state()`) and reads the pending review from Kriya. (An Electron main restart ends the child with SIGINT; a crashed host is detected by the child, which declines.)
- **UI:** `ApprovalReview` in the active-run banner: headline "Approval required", the reviewer text as recorded, files with +/- counts and digests, "Review changes" / "Review validation" (the EXISTING Changes and Validation modules on the run's live evidence via `selectLiveRun`; no diff or validation implementation was added), "Approve changes" (confirmation step) and "Reject changes". Buttons vanish while a decision is in flight; Kriya's refusal is shown verbatim. The outcome (`Run succeeded` + "approved by you", `Changes rejected`, `Review expired`, a failure after approval) is derived only from Kriya's records; the RunRecord wins over any note.
- **Content visibility off (packaged default):** the diff text is not shown (the Changes module says content is not authorized); the review states that and who controls it. See `GUI-REVIEW-CONTENT-VISIBILITY-001` (owner decision).

## 3. Other changes in this slice

- **Attempt-count presentation:** the trace row's `attempts` is a retry counter (`retry_count + best_of_n_candidates_tried`); Runs now shows `N observed attempt(s)` for the selected run only (derived from its loaded evidence), nothing for other rows, and Overview shows the stored value with its meaning. Stored trace evidence is untouched. `GUI-TRACE-ATTEMPTS-SEMANTICS-001` records the discrepancy.
- **Preflight wording:** `interaction.approvals` = `change_review` | `configured_mode` | `unknown` with the resolved `autonomy_mode`.
- **Event-loop lag:** see section 5.

## 4. Evidence

- Tests: `tests/test_launch_approval.py`, `tests/test_kup_execution.py` (Python); `ui/shared/.../shell/approval.test.tsx`; `ui/standalone/test/approval_host.test.ts`, `hardening.test.ts`, `execution_real.test.ts` (real launch + real action: approve, reject, stale/foreign/malformed, duplicate/late, process killed while pending).
- Packaged dry run (controlled body, no model): `evidence/m9/review-dryrun/`.
- Final live smoke: `evidence/m9/live-review-smoke/` - run `20261011T094647-bc1ca4c4`: real `qwen3-coder:30b`, one attempt, gates passed, review pending at 0:40, Changes/Validation opened on the live store, Approve + confirm recorded in 52 ms, Kriya resumed, RunRecord SUCCESS / `COMMITTED`, trace row `6977f438` linked, 66 sealed evidence records, all tabs populated, the scratch `hello.py` now returns `"Hello, " + name + "!"`.

## 5. Clean event-loop measurement

`evidence/m9/lag-clean/results.json`: the packaged app, NO screenshots at any step, a sampler inside the page every 50 ms: all 1096 samples p95 2 ms, max 6 ms; idle p95 2; navigation p95 2; active run (432 samples) p95 2, max 3; no sample over 100 ms. The ~900 ms p95 of the earlier smokes disappears without the capture harness: CLASSIFIED capture-harness contamination (discriminating check: same app, same launch/polling path, no capture). `GUI-EVENT-LOOP-LAG-001` CLOSED. (The clean run used the controlled workflow body; the renderer's polling path is identical.)

## 6. Native Open Folder dialog

The owner reported performing the manual check (File -> Open Folder..., Cmd+O, Cancel, then a disposable folder) and that it was done (2026-10-11). Recorded as owner-verified; not independently automated (the macOS session was not available to automation).

## 7. Known limits (unchanged by this work)

No Stop button (`GUI-EXEC-STOP-001`); other approvals are declined, not offered; PATH is not widened (`GUI-EXEC-TOOLCHAIN-PATH-001`); content visibility is an operator setting; solver readiness is separate (BACKEND-FINAL-CLOSURE-005: 4/12, NOT READY).
