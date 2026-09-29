# PRD-036 Production Release Certification

**Certified executable release candidate: `prd-036-rc9` at `7bc686f37383e057dda3143db5e1e6193262c01f`.**

That exact revision underwent the deterministic certification, the production canary, the three live matrices and hosted CI recorded below. The later commit that adds this document, the evidence and the registry closure is evidence-only (`handover/`, `docs/`, `evidence/`, `*.md`, `*.csv`). It is **not** a release candidate and was not what those gates executed. Every gate result here refers to `prd-036-rc9` / `7bc686f`.

**Final gate:** `scripts/prd036_gate.py --hosted …` → **VERIFIED**, with all 11 criteria passing (`evidence/PRD-036/gate.json`). The same gate without `--hosted` failed only `hosted_ci_at_candidate` (`evidence/PRD-036/gate-local.json`).

## Release-candidate identity

`evidence/PRD-036/release-candidate.json`, candidate digest `edce0229d590f13e7457d746d2c96778dbdc552c2f35ab7f638c3ff404308d7f`. It was CURRENT, with no changes, at every gate (`release_candidate.py check`).

| Component | Value |
|---|---|
| Kriya revision | `7bc686f` (clean tree) |
| Release identity digest | `5a569dc8…` (Kriya revision, platform providers and capabilities, OCI containment on docker 27.5.1, darwin/arm64, Python 3.14.6) |
| Operator production config | `~/.kriya/operator/prd036-production.yaml`, content `a5be2e83…`, security field set `f50f0a05…` (D1: `runtime_profile: production`, static analysis enabled and required, rule-pack content bound) |
| Developer primary (and Planner, Architect, Reviewer, verifiers) | `qwen3-coder:30b`, runtime `ea90552d…`, settings `sha256:ed7bfc09…`, QUALIFIED, exact |
| Developer fallback | `qwen3.6:35b-a3b-q4_K_M`, runtime `64e12eef…`, settings `sha256:0f1e6b5c…`, QUALIFIED, exact |
| Execution environment | `sha256:7b3ce83b…`: Apple M1 Max, Metal, 64 GiB unified memory, Ollama 0.34.2 |
| Case set | v1, table `02a34217…` |

## Gates

**1. Deterministic certification** (`scripts/certify.sh`), in `evidence/PRD-036/certification/`: **CERTIFIED**.

| Stage | Result |
|---|---|
| release identity | recorded, equal to the candidate's |
| static | ruff and pylint at zero |
| pytest | 7219 passed, 0 failed, 0 errors, 0 skipped (certification mode) |
| images | the pinned image is present with its exact digest |
| scanner | the real Semgrep 1.178.0 tier, 27 passed |
| release | sdist/wheel build, integrity and clean install |
| doctor | recorded (a minimal config; the canary's doctor is the evidence) |

**2. Production canary** (`scripts/prd036_canary.sh`, the D4 demo-03 brownfield fix, two runs), in `evidence/PRD-036/canary/`: **PASS**.
- Each run:
  - exits 0, and the run record is SUCCESS with one commit;
  - exercises the enforce controller;
  - makes only the authorised write (`DefaultDriverService.java`), with HEAD unchanged;
  - passes a separate compile and test afterwards;
  - binds static-analysis evidence into the commit;
  - leaks no containers, networks or processes, and releases the workspace lock;
  - keeps the candidate CURRENT.
- The reset left only the main worktree. Run 1 started from it alone; run 2 started from exactly run 1's three worktrees (main, plan, one candidate sibling). Both ended with that same set, with no nesting.
- Run records: `20260929T025439-7b70d64b`, `20260929T025854-61b086e9`.

**3. Production doctor:** `PRODUCTION_READY=true` on the D1 config before and after both canary runs.

**4. Live streak** (PRD-035 matrix, `scripts/certify_model.sh` in release mode): **3 of 3 consecutive complete matrices, 11/11 each**, all on candidate `edce0229…`, revision `7bc686f`.

| Trial | Evidence | Result | Streak after |
|---|---|---|---|
| 1 | `evidence/PRD-036/matrix/trial-20260928T213254Z` | 11/11 | 1 |
| 2 | `evidence/PRD-036/matrix/trial-20260928T214232Z` | 11/11 | 2 |
| 3 | `evidence/PRD-036/matrix/trial-20260928T215303Z` | 11/11 | 3 |

The sealed streak log is copied at `evidence/PRD-036/streak-edce0229.json`.

**5. Hosted CI at the candidate:** run **36500271929** (`workflow_dispatch` on tag `prd-036-rc9`, head `7bc686f`): **success**, in `evidence/PRD-036/hosted/ci-36500271929.json`.

| Job | Conclusion |
|---|---|
| Production certification (Linux) | success |
| Platform import and architecture guard (Windows, blocking) | success |
| Primary live model regression (blocking) | success |
| Test, Python 3.10 to 3.14 | success (5 jobs) |
| Static check (pylint) | success |
| Lint (ruff) | success |
| Verify dependency lock file | success |
| Source distribution and clean wheel install | success |
| Nightly live model matrix | skipped by design (runs on schedule) |

**6. Registry:** no open P0 or P1.

**7. Tracked tree:** clean at the gate. Every change after the candidate is evidence-only.

## Scope statement

This certifies Kriya at `prd-036-rc9` / `7bc686f` for the D1 operator production configuration, with the model identities, execution environment and case set above, on macOS arm64. Hosted Linux CI covers the deterministic suite, the production certification tier and the blocking Windows import/architecture guard. Windows runtime support is not claimed (`PORTABILITY_ARCHITECTURE_DEFINED / RUNTIME_NOT_SUPPORTED`). A different model runtime, inference settings, environment, config or case set is a different identity, and needs its own streak. For how rc4–rc8 were found and fixed, see `handover/PRD-036_FINAL_CLOSURE.md`.
