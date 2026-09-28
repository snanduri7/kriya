# Hosted Linux Certification Failure Closure

**Directive:** "Hosted Linux Certification Failure Closure" (2026-09-28).

**Trigger:** the first hosted `production-certification` run (**36368006232**, `workflow_dispatch` on `milestone-decomposition` at `47b8d44`) failed.
- static, scanner (27 passed), release and lint all passed;
- the same 15 pytest failures occurred on every Python version;
- the live-model job failed on 4 tests.

Every failure is classified below and every product defect is fixed. Nothing is pushed until every local and Linux gate is green.

## Commit series (over `47b8d44`, none rewritten)

| # | Commit | Id | Class |
|---|---|---|---|
| 1 | `3711adb` | OCI-DAEMON-PROBE-001 (P1) | PRE_EXISTING_PRODUCT_DEFECT |
| 2 | `bcc90ae` | PRD-034 test counted a stdout message (own bug) | WAVE7_REGRESSION (test) |
| 3 | `f456701` | LINUX-JVM-RLIMIT-AS-001 (P1) | PRE_EXISTING_PRODUCT_DEFECT |
| 4 | `fb8812a` | LINUX-OCI-WORKSPACE-IDENTITY-001 (P1), closes OCI-NONROOT-TMPFS-WORKDIR-001 | PRE_EXISTING_PRODUCT_DEFECT |
| 5 | `1e5e4a1` | LINUX-OCI-VENV-INTERPRETER-001 (P1) | PRE_EXISTING_PRODUCT_DEFECT (masked by #4) |
| 6 | `f7bb315` | CI-PINNED-IMAGE-PREFLIGHT-001 (P2) | CI_ENVIRONMENT |
| 7 | `de0ca0f` | Live-tier timeout evidence (no timeout changed) | pending evidence, LIVE-SMOKE-CPU-TIMEOUT-001 |
| 8 | `13421a6` | FILE-STAMP-RACY-CACHE-001 (P1) | PRE_EXISTING_PRODUCT_DEFECT (Linux reproduction) |
| 9 | `1ef6a5e` | Own bug in #3: a pinned step-dict test | regression in this closure, fixed |
| 10 | `99fd3ee` | Linux reproduction harness + docs | — |
| 11 | this commit | evidence, registry, handover | — |

Acceptance: zero new failures; zero unexpected certification skips; no new open P0/P1; no authority, egress or containment weakened (cap-drop, no-new-privileges, network decisions and fail-closed paths all kept, each one test-pinned); tracked tree clean.

## Hosted failures → cause → fix

**A. JVM under RLIMIT_AS** (`test_workflow` ×3, live `test_deterministic_failure_diagnostic`)
- *Symptom:* "There is insufficient memory for the Java Runtime Environment to continue".
- *Cause:* the host sandbox budget (4096 MB) was applied as RLIMIT_AS, and a JVM reserves more address space than that. RLIMIT_AS is enforced on Linux and advisory-only on macOS.
- *Fix:* a typed `ResourcePlan` in `kriya/tools/sandbox.py`.
  - Ordinary commands keep RLIMIT_AS.
  - JVM-backed commands (typed toolchain, or an argv[0] launcher; never shell text) get `-Xmx`, `MaxMetaspaceSize` and `MaxDirectMemorySize` from the same budget in `JAVA_TOOL_OPTIONS`, so forked JVMs are bounded too.
  - CPU, timeout and containment are unchanged.
  - An unenforceable budget is `ResourceLimitSetupError`.
  - The strategy is recorded as execution evidence.
- *Required tests:* 1–7 are in `tests/test_linux_jvm_resource_strategy.py`, including a real parent JVM and the JVM it forks both observing `-Xmx512m`, and a JVM timeout killing its child.

**B. OCI as root** (`test_containment_oci` ×2, `test_prd011_toolchain_identity_oci` ×2, `test_validate_oci` ×5, `test_dependency_execution` ×1, live `test_live_prd011_toolchain_parity`)
- *Symptom:* "can't cd to /kriya/workspace" / "cannot create out.txt: Permission denied" / a `clean` unable to delete an acquisition-owned `target/`.
- *Cause:* the container ran as root with `--cap-drop ALL`, so it had no CAP_DAC_OVERRIDE on host-owned mounts.
- *Fix:* a container writing a host mount runs `--user <host uid:gid>` via SEC-008's rule (`resolve_host_writer_identity`, fail-closed on root or foreign ownership).
  - Kept: `--cap-drop ALL`, no-new-privileges and the network decision, with no chmod/chown and no root or privileged fallback.
  - The container owns its scratch tmpfs.
  - HOME, `MAVEN_CONFIG` and `-Duser.home` point into that tmpfs.
- *Required tests:* 1–8 are in `tests/test_linux_oci_workspace_identity.py`.
- *OCI-NONROOT-TMPFS-WORKDIR-001* is **closed** by the same change: a nobody container without a workspace now has a writable working directory. The test for it fails without the fix on Linux.

**B′. Contained venv interpreter** (`test_contained_python_validation…`; masked on the hosted runner by B)
- *Cause:* the venv's `bin/python` links to the container's own interpreter, and the host checked it with `os.path.exists`. That held on macOS only because Homebrew installs `/usr/local/bin/python3`.
- *Fix:* `lexists` in contained mode.

**C04. Daemon probe** (`test_docker_disappearing_never_falls_back_to_the_host`)
- *Cause:* `docker info --format` exits 0 with an unreachable daemon.
- *Fix:* the probe now requires a reported server version.

**PRD-034 test** (`test_certification_fails_every_unexpected_skip_and_xfail`)
- *Cause:* it counted a stdout message whose occurrences depend on terminal width.
- *Fix:* it now asserts `skips.json`.

**C. "Unable to find image … locally"**
- This line is Docker's own auto-pull notice; the real failure in those tests was B. **CI_ENVIRONMENT**, not a missing image.
- The directive's preflight is implemented anyway:
  - `tests/certification/pinned_images.txt`;
  - `scripts/pinned_images.py --pull` in CI and `--verify` in `certify.sh`, both requiring an exact `RepoDigests` match;
  - a mandatory `images` stage, where a missing image is FAIL, never a skip.

**D. Live `generate` timeouts** (`test_live_smoke`, `test_live_generate_json`)
- These are the tests' own 600 s subprocess limits on a CPU-only `qwen2.5-coder:1.5b`. There is no evidence of progress or hang, and no green baseline exists (every `main` run since 2026-08-17 failed, and scheduled runs never fire).
- Per the directive, **no timeout is changed**.
- The tests now fail with the elapsed time and the output tail. LIVE-SMOKE-CPU-TIMEOUT-001 (P2) decides the limit from the next hosted run.

**Found by the Linux reproduction (not in the hosted list, timing-dependent):**
- FILE-STAMP-RACY-CACHE-001. The current-source read cache (mtime only) and the qualification record cache could serve stale content, because Linux's coarse timestamps keep the mtime across a same-tick, same-size rewrite.
- *Fix:* git's racy-clean rule, `kriya/core/file_stamp.py`.

## Linux reproduction

`scripts/linux-repro/run.sh <rev>` runs the suite on a clean clone in:
- Ubuntu 24.04.5, kernel 6.12.5 aarch64;
- non-root `runner` uid 1001;
- its own Docker 29.1.3 daemon (overlayfs, cgroup v2);
- OpenJDK 17.0.20.1, Maven 3.9.9, Python 3.12.3.

The environment and exact image digests are in `evidence/LINUX-CLOSURE/linux-environment.txt`. Differences from the hosted runner: aarch64 rather than x86-64, and a Docker VM with less RAM (the third JVM test needs the runner's 16 GB).

| Defect | Before (`bcc90ae`) | After |
|---|---|---|
| A (JVM) | 2 failed, 1 passed | 17/17 (with the new tests) |
| B (OCI) | 10 failed, 4 passed (`oci-before.txt`) | 13/14 at `fb8812a` (the remaining one is B′), 49/49 at `1e5e4a1` |
| B tests without the fix | 5 failed (2 controls pass) | 7/7 |
| **Full deterministic suite** | — | **6897 passed, 0 failed** at `1ef6a5e` |

## Verification before push

| # | Gate | Result |
|---|---|---|
| 1 | JVM resource tests | 14 passed; 12/12 mutations |
| 2 | OCI identity tests | 7 passed (macOS + Linux); 9/9 mutations |
| 3 | Linux reproduction | as above; full suite 6897/0 |
| 4 | PRD-011/012 containment + egress, SEC-003..008, TOOL-001..003, service runtime | 602/0 macOS; 601/1 → 49/49 Linux after B′ |
| 5 | SEC-008 identity suites | included in 4 (`test_containment_oci_registry_scoped_unit`) |
| 6 | PRD-030/031A/032 commit + chaos-adjacent | full suite (below) |
| 7 | Deterministic chaos ×2 | 48/0/4 NOT_RUN (tier) ×2, identical digest `322ad6b594f646da…` (unchanged since Wave 7) |
| 8 | Every-tier chaos | **52/52**, digest `f140261a14f00ee7…` |
| 9–12 | `scripts/certify.sh certification-out/linux-fix-final` (full suite, images, pinned scanner tier, release) | **CERTIFIED** at `99fd3ee`: static PASS; pytest (certification mode) **6897 passed, 0 failed, 0 skipped, 0 unexpected skips** (28:48); images PASS (1 present, exact digest); scanner 27 passed; release PASS (the first run at `1ef6a5e` correctly failed release on the then-untracked harness files, which were committed in `99fd3ee` before this rerun); doctor recorded (`evidence/LINUX-CLOSURE/certification/`) |

## Registry (`handover/BACKLOG_REGISTRY.csv`)

**CLOSED:**
- LINUX-JVM-RLIMIT-AS-001 (P1)
- LINUX-OCI-WORKSPACE-IDENTITY-001 (P1)
- LINUX-OCI-VENV-INTERPRETER-001 (P1)
- OCI-DAEMON-PROBE-001 (P1)
- FILE-STAMP-RACY-CACHE-001 (P1)
- CI-PINNED-IMAGE-PREFLIGHT-001 (P2)
- OCI-NONROOT-TMPFS-WORKDIR-001 (P3)

**OPEN:**
- LIVE-SMOKE-CPU-TIMEOUT-001 (P2): target is the next hosted run's evidence.
- STATIC-ANALYSIS-CI-LIVE-JOB-001: until a hosted `production-certification` job is green.

**No open P0/P1.**

## Push and hosted validation

Only after every gate above is green:
1. Push the series to `milestone-decomposition`.
2. Dispatch CI. CI triggers only on `main`, pull requests, dispatch and the schedule.
3. Inspect each job.

Then:
- PRD-034 is not VERIFIED, and STATIC-ANALYSIS-CI-LIVE-JOB-001 is not closed, until the hosted `production-certification` job is green.
- PRD-036 does not start before that.
- Any hosted failure is classified as WAVE7_REGRESSION | PRE_EXISTING_PRODUCT_DEFECT | CI_ENVIRONMENT | EXTERNAL_TRANSIENT | EXPECTED_NON_PRODUCTION_CONDITION, and every product defect is fixed first.
