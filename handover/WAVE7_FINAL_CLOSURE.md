# Wave 7 Final Closure

**Directive:** "Wave 7 Final Closure" (2026-09-28).

The four Wave 7 defect fixes (`91e474d`, `2f66763`, `16bc723`, `ddfa3b8`) were architecturally approved. `CANDIDATE-VERIFIED-DIGEST-BINDING-001` was reclassified P3 → P1 and blocked the push until fixed.

## CANDIDATE-VERIFIED-DIGEST-BINDING-001 (P1) — CLOSED by `580625a`

**Defect.** With static analysis disabled, neither terminal commit tied the batch it wrote to the batch that terminal verification judged. Both re-materialized the candidate at commit time, so a candidate changed after its gates would have been committed as if it were verified. This is a verification/commit TOCTOU on a correctness invariant, and it must not depend on static analysis being enabled or on the production containment seal.

**Design.** A provider-independent `CandidateVerificationBinding` (`kriya/workflow/verification_binding.py`). It is not an LLM artifact and not a static-analysis field. The chain is:

`terminal verification → bind → commit_verified_candidate / direct commit → recompute → transactional commit`

1. **Contents.** One entry per file of the exact commit batch:
   - normalized workspace-relative path (real paths, posix separators);
   - operation: `add`, `modify` or `delete`, or `write` when the batch declares only a base revision (the direct path cannot tell an empty existing file from a missing one; the base revision is bound either way);
   - sha256 of the exact bytes, or a deletion marker;
   - mode;
   - expected base revision and base existence.

   It is serialized as canonical sorted JSON with a version field and digested with sha256.
2. **Binding points.**
   - Enforce: `TerminalGateService.run` binds `TerminalGateRequest.commit_batch()` (now a required field) before its first gate, and stores it in `TerminalGateReport.verified_candidate`. `commit_service` passes it through.
   - Direct/milestone: `run_generation_workflow` binds `_direct_terminal_writes(...)` right after `run_attempt` returns with the candidate gates passed (`GenerationState.verified_candidate_binding`, reset at every attempt). Requirements, static analysis, approval and terminal regression all judge the bound candidate.
3. **Commit invariant.** `commit_terminal_candidate(verified_candidate=)` is a required keyword, checked first: before the empty-batch shortcut, before the static-analysis guard and before any intent.
   - A missing binding is refused with `VERIFIED_CANDIDATE_EVIDENCE_MISSING`.
   - A different binding is refused with `VERIFIED_CANDIDATE_EVIDENCE_STALE`, and the detail names the changed paths.
   - Nothing is written: no source byte, no RunRecord intent, no commit evidence.

   On both paths the refusal terminates through the existing deterministic `workspace_commit_failed` stop, and the Developer is never retried. There is no scanner-specific branching.
4. **Relation to static analysis.** The binding and the static-analysis authorization are independent requirements. Valid static-analysis evidence never rescues a stale binding, and a valid binding never bypasses blocking static-analysis evidence.
5. **Layering.** `CandidateMaterializationError` moved to `edit_safety` (re-exported by `terminal_commit`), so the gate service still imports nothing from the commit module (`test_the_gate_service_has_no_commit_path`). Our own first cut violated this; the adjacent suite caught it before the commit.

**Required tests** (`tests/test_candidate_verification_binding.py`, 24 tests, plus chaos):

| # | Requirement | Test |
|---|---|---|
| 1 | SA disabled, direct candidate changed after the gates → refused, zero writes | `test_a_candidate_changed_after_the_gates_is_never_committed[direct]`; chaos **D10** |
| 2 | Same for enforce | `test_enforce_refuses_a_candidate_changed_after_its_gates_bound[True]` (real `WorkflowController`, a gate after the binding changes the sandbox) |
| 3 | Same for milestone | `test_a_candidate_changed_after_the_gates_is_never_committed[milestone]` (real `run_milestones`) |
| 4 | Added file changed | `test_a_candidate_changed_after_verification_is_refused_with_nothing_written[added file changed]` |
| 5 | Modified file changed | `…[modified file changed]` |
| 6 | Deletion set changed | `…[deletion set changed]` (also `…[operation changed]`) |
| 7 | Path set changed | `…[path set changed]` |
| 8 | Unchanged candidate commits normally | `test_an_unchanged_candidate_commits_normally`, `test_the_control_run_without_tampering_commits`, `…bound[False]` |
| 9 | SA enabled requires both | `test_static_analysis_enabled_needs_both_the_binding_and_the_static_analysis_authorization` |
| 10 | Stale binding not rescued by valid SA evidence | `test_a_stale_binding_is_not_rescued_by_valid_static_analysis_evidence` (the SA evidence judged exactly the swapped bytes and is itself valid) |
| 11 | Valid binding cannot bypass blocking SA | `test_a_valid_binding_cannot_bypass_blocking_static_analysis_evidence` |
| 12 | Typed failure reaches the RunRecord and CLI, not retried | direct/milestone tests assert: 1 Developer request, RunRecord FAILURE with no commit cycle, result `workspace_commit_failure.reason_code`; enforce asserts `reason_codes` and the `workspace_commit_failed` event; `test_the_cli_names_the_refusal` |

Additional tests:
- every entry component is bound: bytes, mode, delete, base revision, base existence, path;
- the binding is canonical: independent of batch order and of workspace spelling through a symlink;
- a missing binding is refused even for an empty batch;
- a structural pin that the only production call sites pass `report.verified_candidate` and `state.verified_candidate_binding`.

Existing chaos scenarios:
- **D09** (enforce, SA enabled) and **E02** (direct, SA enabled) are now refused by the binding first (`VERIFIED_CANDIDATE_EVIDENCE_STALE`).
- The static-analysis guard's own batch staleness stays covered at the seam: `test_prd031a_static_analysis.py::test_stale_candidate_bytes_are_refused` binds the batch it commits.

**Fails without the fix.** With the seam check removed, 15 tests fail. Tests 8 and 11 are controls and pass either way by design.

**Mutations (13/13 caught):**
- the seam check skipped;
- the digest comparison always equal, or comparing only the entry count;
- path, operation, bytes, mode or base excluded from the entry;
- a missing binding accepted;
- direct re-binding at commit, or binding late;
- enforce re-binding at commit, or binding after the gates.

## Reverification (in the required order)

| # | Gate | Result | Evidence |
|---|---|---|---|
| 1 | New targeted binding tests | 24 passed | pytest (in-session) |
| 2 | Adjacent suites (commit, PRD-004/005/007/008/008A/029/030/031/031A/032, enforce controller, strict doubles, registry, state location) | **1227 passed, 0 failed** | `certification-out/wave7-binding/adjacent2.txt` (local) |
| 3 | Deterministic chaos report ×2 | **48 passed / 0 failed / 4 NOT_RUN (tier)**, content digest `322ad6b594f646da…` both runs | `evidence/WAVE7/final-closure/chaos-det-{1,2}/` |
| 4 | Every-tier chaos report | **52/52 passed** (51 + D10), digest `5b6175f80b59a2a0…` | `evidence/WAVE7/final-closure/chaos-all/` |
| 5 | `PATH=.venv/bin:$PATH scripts/certify.sh certification-out/wave7-final` | **CERTIFIED** at `580625a`: static PASS; pytest (certification mode) **6857 passed, 0 failed, 0 skipped, 0 unexpected skips** (28:14); scanner 27 passed; release PASS; doctor recorded (minimal canonical config PRODUCTION_READY=false: `model.qualification`, `context.recall_certification` - see the PRD-036 note below) | `evidence/WAVE7/final-closure/certification/` |
| 6 | One PRD-035 live matrix | **CERTIFIED 11/11** (9:27), tier `target_production`, runtime `ea90552d…` (`qwen3-coder:30b`), report digest `71b3d53b8800268c…`; `kriya model certification` → CURRENT | `evidence/WAVE7/final-closure/matrix/` |

## LIVE-CERTIFICATION-REPEATED-TRIALS-001 (P2, PRD-036)

The registry now carries this rule, `target_scope=PRD-036`, `blocking=PRD-036 final release`:
- An exact unchanged runtime digest, inference-settings digest, execution-environment digest and case-set version.
- **3 consecutive complete PRD-035 matrices, each 11/11.**
- Any required-case failure resets the streak.
- Every trial, passing or failing, is preserved as evidence.

Today's PRD-035 CURRENT record is **not** redefined: it still means "the latest full matrix passed on this identity".

## Recorded for PRD-036: what "PRD-034 CERTIFIED" means

**"PRD-034 CERTIFIED" means canonical CI/test certification only**: static, deterministic pytest in certification mode, the pinned scanner tier and release integrity, with the doctor recorded as reported. It is never equivalent to `PRODUCTION_READY=true`. The canonical minimal config reports PRODUCTION_READY=false: an unqualified packaged-default fallback identity, and no context certification for that exact config.

**PRD-036 must independently require** `kriya doctor --production` to return `PRODUCTION_READY=true` on the actual operator-approved production config, in addition to the repeated-trials rule above.

**All required gates green:** 0 failed, 0 unexpected skips; scanner green; release green; no new open P0/P1 (the one P1 of this closure is CLOSED); tracked tree clean.

**Live-matrix note.** This is the third full matrix on this identity. The record is run 1 FAILED 9/11 at `e987789` (C7 was a case-design error, since fixed, and C8 was a genuine model repair failure), run 2 CERTIFIED 11/11, and run 3 (this one) CERTIFIED 11/11. Under the PRD-036 rule above, the streak is 2 consecutive passing matrices, not 3. That rule does not apply to Wave 7.

## Push and after

Push to `milestone-decomposition` is approved only if every gate above is green, with no new P0/P1 and a clean tracked tree.

After the push:
- The hosted `production-certification` job must pass before STATIC-ANALYSIS-CI-LIVE-JOB-001 is closed or PRD-034 is treated as fully verified.
- **PRD-036 is not started until the hosted job is green.**
