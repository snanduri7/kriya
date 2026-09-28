# Overnight Wave 7 Summary — PRD-032 → PRD-035

**Directive:** "Autonomous Overnight Wave 7" (2026-09-27): local commits only, no push, hard-stop rules, with full pytest and live runs permitted.

**Start:** `4496327`, the PRD-031A closure. It was pushed first, per the closure directive; afterwards local HEAD == origin, the tracked tree was clean, and there was no open P0/P1.

**Recommendation (overnight): REVIEW_REQUIRED.** Every PRD was LOCALLY_VERIFIED, nothing was blocked, and no hard stop was hit.

**Final closure (2026-09-28, `handover/WAVE7_FINAL_CLOSURE.md`):**
- The architecture review approved the four fixes.
- It reclassified CANDIDATE-VERIFIED-DIGEST-BINDING-001 P3 → P1, blocking the push. That item is fixed and CLOSED in `580625a`.
- Every gate was re-run green at `580625a`:
  - adjacent suites 1227/0;
  - deterministic chaos ×2 with the same digest;
  - every-tier chaos 52/52;
  - canonical certification CERTIFIED (pytest 6857/0, 0 skips);
  - live matrix CERTIFIED 11/11.
- The push to `milestone-decomposition` is approved on that basis.
- After the push, the hosted `production-certification` job must pass before STATIC-ANALYSIS-CI-LIVE-JOB-001 closes or PRD-034 counts as fully verified. PRD-036 does not start before then.

## Status per PRD (tracker vocabulary: `READY_FOR_PYTEST_VERIFICATION`; none marked VERIFIED)

| PRD | Status | Core evidence |
|---|---|---|
| PRD-032 Adversarial and Crash Chaos Harness | LOCALLY_VERIFIED / READY_FOR_USER_REVIEW | 51-scenario chaos harness, all tiers 51/51 (digest `9fe5a8fb` at `ae29d0a`); deterministic digest reproducible; 3 defects found and fixed |
| PRD-033 Production Telemetry and False-Success Metrics | LOCALLY_VERIFIED / READY_FOR_USER_REVIEW | `kriya/metrics` + `kriya metrics` CLI; full 6808/0; live report re-derived with the identical digest |
| PRD-034 Full Pytest and CI Production Certification | LOCALLY_VERIFIED / READY_FOR_USER_REVIEW | `scripts/certify.sh` CERTIFIED: pytest 6816/0 with 0 skips in certification mode, scanner 27/0, release PASS; 1 pre-existing defect fixed; the hosted CI run is pending the push |
| Prompt-fit closure (before PRD-035) | LOCALLY_VERIFIED | ARCHITECT-PROMPT-FIT-001, DEVELOPER-AUX-LOOP-PROMPT-FIT-001 and PROMPT-BUDGET-FIT-001 CLOSED |
| PRD-035 Live Local-Model Certification Matrix | LOCALLY_VERIFIED / READY_FOR_USER_REVIEW | run 1 FAILED 9/11, run 2 CERTIFIED 11/11 on runtime `ea90552d` (M1 Max); record CURRENT |

## Final gate at HEAD `220af75` (every Wave 7 change included)

| Gate | Result | Evidence |
|---|---|---|
| Canonical `scripts/certify.sh` | **CERTIFIED** | `evidence/WAVE7/final-certification/` |
| — static (ruff + pylint) | PASS, 0 findings | |
| — full deterministic suite, certification mode | **6832 passed, 0 failed, 0 skipped, 0 unexpected skips** (28:42) | junit.xml.gz, skips.json |
| — pinned scanner tier (Semgrep 1.178.0 + pinned image) | 27 passed | |
| — release (sdist/wheel integrity, clean-install smoke) | PASS | |
| — production doctor | recorded as reported (minimal canonical config: PRODUCTION_READY=false; see Deviations) | doctor.json |
| Chaos report, every tier (`scripts/chaos_report.sh … all`) | **51/51 PASSED**, digest `253bbb32…` | `evidence/WAVE7/final-chaos/` |
| Live certification matrix (run 2) | **CERTIFIED 11/11**, record CURRENT | `evidence/PRD-035/matrix-run2/` |

**Environment:** Apple M1 Max, 64 GiB, macOS arm64, Python 3.14, JDK 17.0.10, Maven 3.9.16, Docker 27.5.1, Semgrep 1.178.0, non-root (uid 501). Model: `qwen3-coder:30b`, runtime `ea90552d…`, QUALIFIED.

## Defects found and fixed (each its own commit, with a regression test that fails without it)

1. **`91e474d`: a direct/milestone terminal commit that did not commit was re-raised into the generic retry path.** The Developer was re-asked, which could never succeed, and the category was wrong. It is now the deterministic stop `workspace_commit_failed`, with the enforce-parity payload. Found by chaos C08/D01; 4/4 mutations caught.
2. **`2f66763`: the Planner/Architect workspace context carried the absolute `root_path`.** The schema refuses absolute planned paths, so the model copied Kriya's own echo and was refused. Found by the live chaos tier.
3. **`16bc723`: the Planner prompt showed the closed plan vocabularies only by example.** The model invented `verifier_kind: file_check` and was refused. The vocabularies are now derived from the enums. With fixes 2 and 3, live Planner reliability went from 2 of 5 (and 2 of 3) refused to **8 of 8 planned**.
4. **`ddfa3b8` (pre-existing since PRD-010 `e89ccb3`): `scripts/release_smoke.py` still set removed config fields.** The PRD-002 clean-install smoke, and so CI `release-integrity`, failed. The removed-field guard now also scans `scripts/`.

Also corrected before commit, so not shipped defects:
- a duplicate `pytest_runtest_makereport` definition (the second would have silently replaced the first);
- two PRD-033 deriver errors (the initial `model.transition` counted as a fallback; a misleading 0.0 verification share);
- a harness artifact (the recorded request aliased the loop's live message list).

## Backlog changes (`handover/BACKLOG_REGISTRY.csv`)

**Closed:**
- PRE-APPROVAL-REVIEW-REFUSAL-001;
- ARCHITECT-PROMPT-FIT-001;
- DEVELOPER-AUX-LOOP-PROMPT-FIT-001;
- PROMPT-BUDGET-FIT-001.

**Implemented, closes on the first hosted run:** STATIC-ANALYSIS-CI-LIVE-JOB-001.

**New:**

| Id | Priority | Target |
|---|---|---|
| ARCHITECT-FILE-LIST-ESCAPE-FALLBACK-001 | P3 | plan/file-list hardening with the ENFORCE convergence |
| CANDIDATE-VERIFIED-DIGEST-BINDING-001 | ~~P3~~ **P1, CLOSED** | reclassified by the final-closure review; fixed in `580625a` (provider-independent verification binding on both commit paths) |
| TRACE-ENFORCE-SUBTASK-LINKAGE-001 | P3 | the ENFORCE convergence |
| FINAL-REVIEW-BACKEND-ERROR-001 | P3 | the operator-UX batch |
| CERT-NETWORK-DEPENDENCY-001 | P3 | PRD-036 |
| **LIVE-CERTIFICATION-REPEATED-TRIALS-001** | **P2** | **PRD-036**, blocking the PRD-036 final release. The rule is 3 consecutive complete 11/11 matrices on an exact unchanged identity, where any failure resets the streak and every trial is kept as evidence. Across the three matrices so far, C8 passed 2 of 3. |

**Untouched, by registry target:**
- MCP-APPROVAL-PATH-TRACEBACK-001;
- OCI-NONROOT-TMPFS-WORKDIR-001;
- LEGACY-TRACES-MIGRATION-001 (operator action; the metrics report names a legacy database without reading it);
- INF-001-VLLM-ADAPTER and INF-001-ENV-EVIDENCE (INF-002);
- ENFORCE-EXECUTE-PLAN-CONVERGENCE-001, RUN-ATTEMPT-GATE-EXTRACTION-001, STATIC-ANALYSIS-REMEDIATION-LOOP-001, STATIC-ANALYSIS-INPLACE-BASELINE-001, STATIC-ANALYSIS-MULTI-PROVIDER-001, STATIC-ANALYSIS-SEVERITY-MAP-V2-001.

**No open P0/P1.** The one P1 raised by the final review, CANDIDATE-VERIFIED-DIGEST-BINDING-001, is CLOSED.

**Recorded for PRD-036.** "PRD-034 CERTIFIED" means canonical CI/test certification only. It is never equivalent to PRODUCTION_READY=true. PRD-036 must separately require `kriya doctor --production` to report PRODUCTION_READY=true on the actual operator-approved production config.

## Deviations from the task specs (explicit)

- **PRD-034:** the hosted GitHub run is NOT_EXECUTED, because this batch forbids pushing. The canonical local run of the same `scripts/certify.sh` is the executed evidence.
- **PRD-034:** the production doctor stage is recorded as reported, not gated. On the minimal canonical config it is PRODUCTION_READY=false: the packaged-default fallback identity is unqualified, and there is no context certification for that exact config. PRODUCTION_READY=true with the operator's full config was shown at PRD-031A.
- **PRD-035:** the certification rule is one full passing matrix. Repeated trials are deferred to PRD-036 (LIVE-CERTIFICATION-REPEATED-TRIALS-001, P2).
- **PRD-033:** no deterministic false-success producer exists; the `deterministic` adjudication source is reserved. There are no threshold values (none approved), so the result is NOT_CONFIGURED.
- **Global Contract Stage B/C:** the coding agent ran the full suite and the live tiers itself, under this batch's explicit permission. The independent re-verification is the user's.

## Unresolved risks

- Real-model variance: C8 passed 1 of 2; the Planner's structured-output reliability should be re-measured by PRD-036 trials.
- The certification's deterministic tier depends on reaching Maven Central (CERT-NETWORK-DEPENDENCY-001).
- The P3 findings above.

## Local commits (order, origin at `4496327`)

`7ef2a1a` → `91e474d` → `b40b4da` → `2f66763` → `ae29d0a` → `f86190e` (PRD-032)
→ `16bc723` → `5562d05` → `fd40c10` (PRD-033)
→ `016caba` → `ddfa3b8` → `3903dea` → `573b13b` (PRD-034)
→ `325eb4f` (prompt-fit closure)
→ `e987789` → C7 case fix → `220af75` (PRD-035)
→ `0b1e2a3` (this summary)
→ `580625a` (final closure: CANDIDATE-VERIFIED-DIGEST-BINDING-001 fix)
→ the final-closure evidence/docs commit.

## Commands (user)

- **Full suite:** `PATH=.venv/bin:$PATH .venv/bin/pytest -q`
- **Canonical certification:** `scripts/certify.sh certification-out/user` (needs Docker, JDK 17, Maven, Semgrep 1.178.0)
- **Chaos report:** `scripts/chaos_report.sh <dir> all`, and `deterministic` twice to compare `content_digest`.
- **Metrics:** `kriya metrics report --json`
- **Live certification:** `scripts/certify_model.sh <dir>`, then `kriya model certification`.
- **Push:** only on the user's approval; nothing has been pushed in Wave 7.

## Recommendation

**REVIEW_REQUIRED.** Nothing is BLOCKED, and every local gate is green at `220af75`. Before any push:

1. The user reviews the four defect fixes and the new P2 (LIVE-CERTIFICATION-REPEATED-TRIALS-001).
2. The user re-runs the full suite or `scripts/certify.sh` (Stage B).
3. After the approved push, the hosted `production-certification` job must run green; that is what closes STATIC-ANALYSIS-CI-LIVE-JOB-001.

PRD-036 is not started.
