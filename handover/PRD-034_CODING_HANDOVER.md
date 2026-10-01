# PRD-034 Coding Agent Handover — Full Pytest and CI Production Certification

## Status

**VERIFIED** (hosted production-certification run 36380966601, 2026-09-28). Tracker: `VERIFIED`.

The hosted GitHub run of the new job is **NOT_EXECUTED (pending the user's push)**. This batch forbids pushing, so this is not a CI service limitation. The executed evidence is the canonical local run of the same `scripts/certify.sh` on the target machine.

## Scope implemented

**Instruction:** `tasks/PRD-034_Full_Pytest_and_CI_Production_Certification.md`, Wave 7 directive §6, and the design in `handover/PRD-034_DESIGN_ANALYSIS.md`.

1. **Non-root.** `scripts/certify.sh` refuses root (exit 2), and the CI job asserts `id -u != 0`. No test validates root refusal, so there is no exception.
2. **Explicit toolchains.** The CI job installs:
   - Temurin JDK 17;
   - Maven 3.9.9 (from the Apache archive);
   - Python with the lock-file dependencies in a venv;
   - `semgrep==1.178.0` (pipx);
   - the pinned Semgrep image by digest.

   Docker is the runner's own. `environment.json` records exact versions (python, pip-freeze digest, java, mvn, gradle, docker client/server, semgrep, git, revision, OS).
3. **Separated jobs.** Deterministic non-live pytest (certification mode), the pinned scanner tier, release/packaging, and lint/static run as stages of the certification job, while the existing `test`, `lint`, `static-check`, `lock-file` and `release-integrity` jobs are kept. The live jobs stay separate.
   - **Live selection bug fixed:** the live jobs now select `live_model and not live_target`. Before, `-m live_model` pulled in every target-identity tier (PRD-012…033), which needs the qualified `qwen3-coder:30b` and fails on the CI model.
   - The smoke job sets `KRIYA_LIVE_LLM_MODEL` to the model it pulls.
4. **Unexpected skips fail.** In certification mode (`--certification` or `KRIYA_CERTIFICATION=1`), every skip or xfail must match `tests/certification/skip_allowlist.yaml` (id, nodeid pattern, reason, owner, expires). An expired entry allows nothing. Skips are always audited (`skips.json`).
5. **Lint/static:** ruff and pylint as the first stage.
6. **Archived artifacts:**
   - junit XML per tier;
   - `skips.json` per tier;
   - `environment.json`;
   - `doctor.json` (the production doctor, recorded as reported: its PRODUCTION_READY is never turned into a pass);
   - `certification-summary.{json,md}`: CERTIFIED only when static, pytest, scanner and release are PASS, the doctor produced JSON, and no unexpected skip remains.
7. **Environment semantics.** `tests/conftest.py` puts the running interpreter's directory first on PATH for the session. The environment under test supplies `python`/`pip`, never the operator's shell. This removes the PRD-031A "tool shell without pip" contamination class.

**Backlog:**
- **STATIC-ANALYSIS-CI-LIVE-JOB-001** is implemented. It stays OPEN until the first hosted run, because its closure evidence is that run.

## Files changed

- `tests/conftest.py`: the certification options, one merged `pytest_runtest_makereport` hook (skip audit, then chaos bookkeeping), and the interpreter-PATH fixture.
- `tests/_certification.py`, `tests/certification/skip_allowlist.yaml`, `tests/certification/sample_skips.py`, `tests/test_prd034_certification.py`.
- `scripts/certify.sh`, `scripts/certification_summary.py`.
- `.github/workflows/ci.yml`: the `production-certification` job and the live selection.
- `pyproject.toml`: the `live_target` marker.
- `live_target` added to the target-identity live files and to the PRD-032 chaos live tier.

## Tests

`tests/test_prd034_certification.py` (8 tests):
- a real pytest subprocess showing that skips are recorded outside certification and fail inside it (setup-phase skip → error, xfail → failure);
- allowlisted skips pass and expired entries do not;
- pattern and reason matching;
- a malformed allowlist is refused;
- the repository allowlist is valid;
- the summary is CERTIFIED only when every mandatory stage passes and there is no unexpected skip, and the doctor is reported as is.

## Canonical local certification run

See `handover/evidence/PRD-034/`.

## Known limitations

- The hosted run is pending the push.
- Gradle is not installed locally or in CI. Tests that need it and skip are listed in `skips.json` and must be resolved or allowlisted with an owner and expiry (see the evidence).
- The doctor's `model.*` rows FAIL wherever no model is qualified (CI). This is recorded as is and never gated as a pass.

## Canonical local certification: executed evidence

**Run 1 at `016caba`: NOT_CERTIFIED** (`handover/evidence/PRD-034/certification-local-run1/`). It found three things:
1. **A pre-existing defect.** `scripts/release_smoke.py` still set `paths.logs`/`logging.file`, which PRD-010 (`e89ccb3`, 2026-09-25) removed. The PRD-002 clean-install smoke, and therefore CI's `release-integrity` job, had been failing since. **Fixed in `ddfa3b8`:** state and logs are isolated through `KRIYA_STATE_DIR`/`KRIYA_LOG_DIR`, and the removed-field repository guard now also scans `scripts/`. The guard fails on the old script.
2. **The doctor stage failed `config.load`.** A `runtime_profile: production` config is (correctly) denied by SEC-009 without operator approval. **Fixed in `3903dea`:** the canonical config is approved the operator's way (`kriya authority approve --out … --confirm`, a trust file outside the workspace) and run in a real git workspace.
3. **`test_maven_two_phase_acquire_then_offline_execute` timed out at 180s.** It makes a real Maven Central acquisition through the scoped proxy under load. It passed twice right after (38s each), and the timeout was not changed. Registry: CERT-NETWORK-DEPENDENCY-001 (P3).

**Run 2 at `3903dea`: CERTIFIED** (`handover/evidence/PRD-034/certification-local/`):

| Stage | Result |
|---|---|
| static (ruff + pylint) | PASS |
| pytest, certification mode (`not live_model and not live_static_analysis`) | **6816 passed, 0 failed, 0 skipped, 0 unexpected skips** (28:29). Every Docker, JDK and Maven test executed. |
| scanner (`live_static_analysis`, Semgrep 1.178.0 + pinned image) | 27 passed, 0 skipped |
| release (sdist/wheel integrity + clean-install smoke) | PASS |
| doctor (`--production --json`, recorded as reported) | PRODUCTION_READY=false. Required FAILs: `model.qualification` (the packaged-default fallback `qwen3.6` at packaged settings has no qualification record; every role on the primary is QUALIFIED) and `context.recall_certification` (no certification for this minimal config's exact embedding and retrieval identity). Both are environment facts, reported and never turned into a pass. PRODUCTION_READY=true with the operator's full production config was shown at PRD-031A (demo-03). |

**Environment:** macOS arm64 (M1 Max), Python 3.14, JDK 17.0.10, Maven 3.9.16, Docker 27.5.1, Semgrep 1.178.0, uid 501 (non-root). No Gradle, and nothing skipped for it.

## Final closure (2026-09-28)

**Re-run at `580625a`: CERTIFIED** (`handover/evidence/WAVE7/final-closure/certification/`):
- pytest in certification mode: 6857 passed, 0 failed, 0 skipped, 0 unexpected skips;
- scanner: 27 passed;
- release: PASS;
- doctor: recorded as reported.

**What CERTIFIED means (recorded for PRD-036).** "PRD-034 CERTIFIED" is canonical CI/test certification only, and never equivalent to `PRODUCTION_READY=true`. PRD-036 must separately require `kriya doctor --production` to return `PRODUCTION_READY=true` on the actual operator-approved production config.

**Still open.** STATIC-ANALYSIS-CI-LIVE-JOB-001 stays OPEN, and PRD-034 is not fully verified, until the hosted `production-certification` job passes after the push.

## First hosted run (2026-09-28) and Linux closure

The first hosted `production-certification` run (36368006232) FAILED:
- static, scanner (27/0) and release passed;
- 15 pytest tests failed, all of them Linux-only defects hidden by macOS semantics.

Every failure was classified and fixed, and reproduced on Linux before and after its fix. See `handover/LINUX_CERTIFICATION_CLOSURE.md`.
- Local certification at `99fd3ee`: CERTIFIED, including the new mandatory `images` stage.
- Linux full suite at `1ef6a5e`: 6897/0.

PRD-034 stays not VERIFIED until a hosted run is green.

**Hosted run 36380966601 at `6cbb614`: CERTIFIED.**
- pytest 6897/0 with 0 unexpected skips;
- images PASS;
- scanner 27/0;
- release PASS.

**PRD-034 VERIFIED; STATIC-ANALYSIS-CI-LIVE-JOB-001 CLOSED.**
