# PRD-025 Coding Agent Handover: Bounded Runtime-Verifier Evidence Package

## Status
READY_FOR_PYTEST_VERIFICATION. This is part of the Batch 6 stop. The user runs the pytest suites and the live tests.

## Source identity
- Base revision: 510fd98 (branch `milestone-decomposition`, level with origin).
- Final revision: the PRD-025 commit on top of 510fd98 (see `git log --grep PRD-025`).
- Directives: handover/BATCH6_DIRECTIVES.md (the user's approval with corrections).

## Scope implemented
- Instruction file: tasks/PRD-025_Bounded_Runtime-Verifier_Evidence_Package.md.
- Requirements 1–6 are all implemented, with the user's directive corrections:
  - facts first;
  - one scan of the retained capture;
  - head/tail samples plus marker windows;
  - a budget taken from the SELECTED model's PRD-016 window, rebuilt per fallback;
  - truncation metadata that separates CAPTURE_TRUNCATION from PACKAGE_TRUNCATION;
  - UNKNOWN in place of a false PASS;
  - deterministic exit/timeout authority.
- Not implemented: a bound on how much of the output lands in the Developer retry prompt (`Failure.message` still embeds the captured output). That prompt is sized by the retry package (PRD-016 retry evidence share 0.15), not by the grader, and it is out of this PRD's scope.

## Files changed
- **Production:**
  - `kriya/workflow/verifier_evidence.py`: new, pure module.
  - `kriya/agents/agent.py`:
    - `call_with_escalation` takes a per-candidate prompt callable;
    - `RunVerifierAgent.grade` builds and records packages and returns a tri-state verdict.
  - `kriya/tools/process.py`: `ProcessResult.stdout_lost_chars` / `stderr_lost_chars`, and `_bounded_tail_counted`.
  - `kriya/tools/validate.py`: `run_app_sequence` steps carry the lost-character counts.
  - `kriya/workflow/attempt.py`:
    - `_resolve_runtime_verification_grade(run_result=)` builds the evidence lazily;
    - `apply_runtime_disposition` runs at the admission point and at the final point on both paths (the verification-only subtask path and the main `run_attempt` path, including after self-correction);
    - gate outcomes carry `runtime_disposition` and `verifier_evidence`.
- **Tests:**
  - `tests/test_prd025_verifier_evidence.py` (44 tests, new);
  - `tests/test_workflow.py`: 3 new end-to-end tests; 2 pinned tests changed, see below.
- **Live:** `tests/test_live_prd025_029_batch6.py` (the PRD-025 section).
- **Docs:** `docs/design.md` §2.7, a new "Bounded evidence and deterministic authority" paragraph.

## Pre-change reproduction (confirmed defects)
1. **Suspected P0: nonzero exit overridden by an LLM PASS. Confirmed, in a narrower form than suspected.**
   - Before this change, a nonzero-exit run with no deterministic command kind was admitted as PASS whenever the grader said PASS and the process had launched (`runtime_application_step_started`).
   - That was deliberate: expected invalid-input rejections were pinned by `test_verification_only_packaged_java_uses_grounded_runtime_and_launches_application[invalid]` and `test_run_attempt_accepts_expected_nonzero_only_after_application_started`.
   - The defect: only the judge's `success_criteria` and the grader (both LLM) decided that the exit was expected.
   - Repro (fails on the base revision and passes after the fix):
     - `test_prd025_llm_pass_cannot_override_undeclared_nonzero_exit` (main path);
     - the new `[invalid-...-False]` parameter of the Java verification-only test.
   - Fix: a nonzero exit is admitted only when the USER's goal text (`ctx.grounding_goal or ctx.goal`) declares it (`goal_declares_expected_nonzero_exit`, which is negation-aware), and the application launched.
   - Pinned-test change (disclosed): both pinned tests now give a goal that declares the nonzero exit. The behaviour they protect is kept.
2. **Real gap, confirmed by reading the source.**
   - `ProcessController` keeps the last 2 000 000 characters of each stream, and the grader received all of it, up to about 4 MB, unbounded.
   - The PRD-016 dispatch check then refused the request. `grade()` caught that refusal and reported a failure, so a verbose correct program failed.
   - The truncation flags never reached `run_app_sequence`.
3. **Found during implementation.** After self-correction, re-verification replaced the grade, and the admission check never ran again. The final-point `apply_runtime_disposition` closes this.

## Implementation summary
- **Retained evidence.**
  - `RetainedRuntimeEvidence.from_run_result` holds per-step, per-stream text plus the lost-character counts.
  - One `_SIGNATURE_RE.finditer` pass per stream finds `kriya_marker`, `assertion`, `traceback`, `exception`, `fatal` and `error`.
  - Occurrences are normalized (digits and hex removed) and deduplicated to the first and last window with a count.
  - At most 64 distinct signatures are kept per stream. A decisive signature beyond that cap counts as omitted.
  - A run result without per-step text falls back to the combined output.
- **Package** (`build_verifier_evidence_package`, `build_package_for_budget`):
  - Always shows each step's header: command, exit code, timeout state, the CAPTURE_TRUNCATION note and signature counts.
  - Then decisive windows in priority order, then head (1/3) and tail (2/3) samples in proportion to each stream's length.
  - Gaps are rendered as explicit `PACKAGE_TRUNCATION` markers.
  - The size limit is UTF-8 bytes of `allocation_window(config, binding) * 4 - fixed prompt`, which is PRD-016's unit.
  - When the whole capture fits, it is sent verbatim.
- **Per-model rebuild.** `grade()` passes `prompt_for(candidate)` to `call_with_escalation`, so each candidate, including a fallback, gets its own package built from the same retained evidence. `evidence_packages` lists every package that was sent, and `answered` marks the one whose answer produced the verdict.
- **Verdict.**
  - The grader returns `verdict` (PASS/FAIL/UNKNOWN); a legacy `passed`-only answer is still accepted.
  - `finalize_semantic_verdict` turns PASS into UNKNOWN on `DECISIVE_EVIDENCE_OMITTED` or `CAPTURE_LOSS_UNRESOLVED`. Truncation of non-decisive text alone never demotes a PASS.
  - A call failure is UNKNOWN (`VERIFIER_CALL_FAILED`); an unparseable answer is UNKNOWN (`VERIFIER_RESULT_MALFORMED`).
  - Each of these leaves `passed` False.
- **Disposition.** `apply_runtime_disposition` is idempotent. Its deterministic reasons are `DETERMINISTIC_PROCESS_EXIT`, `TIMEOUT_AUTHORITATIVE`, `NONZERO_EXIT_AUTHORITATIVE` and `EXPECTED_NONZERO_EXIT_GROUNDED`; the final verdict is PASS, FAIL or UNKNOWN.
- **Persistence.** It is recorded on the gate outcome, which reaches `traces.db` through the existing `gate_outcomes` path.

## Tests run by coding agent (targeted only, per the standing rule)
| Command | Passed | Failed | Notes |
|---|---:|---:|---|
| `.venv/bin/pytest -q tests/test_prd025_verifier_evidence.py` | 44 | 0 | 0.6 s |
| `.venv/bin/pytest -q tests/test_workflow.py -k "prd025 or verification_only_packaged_java_uses_grounded or accepts_expected_nonzero_only"` | 7 | 0 | |
| `... -k "missing_runtime_entrypoint_stops_without_source_repair or timeout_grades_captured_output_as_succeeded_then_hung"` + `tests/test_agents.py -k run_verifier` | 46 | 0 | Existing tests that combine a nonzero exit with a grader PASS |

**Mutation checks.** All 11 were killed:
- disabling the decisive-omission check;
- disabling the capture-loss check;
- admitting any nonzero exit;
- dropping the application-launched requirement;
- ignoring timeouts;
- dropping windows;
- breaking the negation guard;
- counting routine error overflow as decisive;
- sizing every candidate for the primary;
- bypassing `finalize_semantic_verdict`;
- zeroing the lost-character count.

## Static/lint/architecture checks
`.venv/bin/ruff check .` reports "All checks passed" and `.venv/bin/pylint kriya plugins/core_tools tests` exits 0.

## Live test additions
- Required: YES.
- File: `tests/test_live_prd025_029_batch6.py`:
  - `test_live_prd025_bounded_package_keeps_middle_failure`;
  - `test_live_prd025_capture_loss_can_never_pass`.
- Prerequisites: a local Ollama serving `KRIYA_LIVE_LLM_MODEL` (default `qwen3-coder:30b`) with an 8192-token window.
- Command:
  ```
  KRIYA_BATCH6_EVIDENCE_DIR=handover/evidence/BATCH6/user-live \
  KRIYA_LIVE_BASE_URL=http://localhost:11434/v1 KRIYA_LIVE_LLM_MODEL=qwen3-coder:30b \
  .venv/bin/pytest -m live_model -ra -s tests/test_live_prd025_029_batch6.py -k prd025
  ```
- Expected:
  - The package stays within the window, is PACKAGE_TRUNCATION-marked, and includes the middle `AssertionError` window.
  - The grader's verdict is not PASS.
  - With a 20 000-character capture limit, CAPTURE_TRUNCATION is recorded and the grade is not PASS.

## Known limitations / residual risks
- Signature coverage is pattern-based. A failure that prints no recognized signature, in a range that was omitted, still relies on the head/tail samples. For that case the grader is instructed to answer UNKNOWN; the result is not enforced.
- `allocation_window` uses the Developer role's inference settings for the byte-ratio lookup. It is the same served window, but a verifier-specific tokenizer ratio would be more exact.
- Capture loss in ManagedProcess (managed services) is not counted. That output is never graded by `grade()`.

## Decisions recorded
- A nonzero exit is admissible only on the user's own goal text (never on judge or grader text). The rationale is in handover/BATCH6_DIRECTIVES.md and in this handover.

## Verification-agent handoff
- Pytest: run the Batch 6 focused command (in the Batch 6 summary), then the full suite.
- Live: the command above.
