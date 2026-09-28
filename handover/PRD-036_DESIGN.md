# PRD-036 — Final Production Release Gate: design and closure plan

Status: decisions made (§7, all Option 1, 2026-09-28); implementation in the PRD-036 certification commit; execution follows §5.
Base: `milestone-decomposition` @ `7238740` (ARCH-PLATFORM-001 CLOSED, no open P0/P1).

PRD-036 adds certification, orchestration and evidence only. It does not change execution semantics, redesign the closed platform architecture, pull Windows P5/P6 forward, or implement unrelated P2/P3 items.

## 1. What exists today (inspected)

| Area | State | Gap for PRD-036 |
|---|---|---|
| Release identity (`kriya/core/release_identity.py`) | Binds the revision and whether the tree is dirty, the platform providers and capability statuses, the containment backend and runtime, and the environment. `compare_release_identity` returns CURRENT or STALE, naming each changed field. | It does not cover the model, the inference settings, the case set or the production config. That is by design, since it was closed under ARCH-PLATFORM-001. **Do not edit its field set.** |
| Live matrix (`scripts/certify_model.sh`, `kriya/core/model_certification.py`) | 11 cases. Each record is keyed by (model, runtime digest, settings digest, environment digest, case-set version). | **A same-key rerun overwrites the record** (`os.replace`). There are no trials and no streak, so the "preserve every run" and "3 consecutive" rules cannot be recorded. |
| Deterministic certification (`scripts/certify.sh`) | Stages: static, pytest in certification mode, images, scanner, release, and doctor (recorded only). | Last run on macOS at `960b5f9`, so the release identity is STALE against any later HEAD. It must be re-run at the frozen candidate. |
| Production doctor | Readiness means no required check is FAIL or UNAVAILABLE. certify.sh runs it against a one-line `runtime_profile: production` config in an empty repo, which gives **PRODUCTION_READY=false**. | See §2. |
| Canary | No in-repo canary exists. `~/kriya-live-demo/demo-03-brownfield/run-demo-production.sh` is a real production-profile run: a Spring Boot brownfield fix with 53 existing tests. | It lives outside the repo. It is reused only if the user approves (§7, D4). |
| Thresholds (`kriya/metrics/thresholds.py`) | "Consumed by PRD-036". None ship. | Recorded as NOT_CONFIGURED. No thresholds are invented. |

## 2. The production doctor: what blocks `PRODUCTION_READY=true` today

Source: `certification-out/macos-960b5f9/doctor.json` (minimal config, empty workspace).

- **FAIL `model.qualification`.** The packaged default `llm_chain` fallback `qwen3.6:35b-a3b-q4_K_M` is MISSING under the default chain settings (temperature 0.2, `extra_body` empty). The qualified qwen3.6 identity (18/18, policy /3) uses the demo-03 settings (temperature 0.7, `reasoning_effort: none`, `num_ctx` 32768, top_p 0.8, top_k 20). Those are the same settings C6 uses. The operator config must pin those exact settings. No new qualification is needed if the digests match.
- **FAIL `context.recall_certification`.** This is required whenever a code index exists at `paths.memory`. The fix is `kriya context certify` against the real embedding runtime of the production config, which is a live run.
- **WARN (required) `persistence.traces`.** A legacy `<repo>/logs/traces.db` exists. WARN does not block readiness. It weakens no invariant, but it is the user's file, so migrating it (`kriya traces --migrate-legacy`) is the user's call (D3).
- **WARN (required) `toolchain.required` and `runtime.fixed_guarantees`.** The doctor ran in an empty workspace. PRD-036 runs the doctor in the canary workspace, so the stack is real and the doctor evidence names that workspace.

No check is weakened to reach `true`.

## 3. Freeze semantics: the release-candidate identity

A new module, `kriya/core/release_candidate.py`, composes the identity. It does not edit `release_identity`. The identity covers:

- `release_identity(...)`, whole, with its digest: the revision, the clean tree, the platform providers and capabilities, the containment backend and runtime, and the environment.
- **Model identity per role binding**, including the C6 fallback: model name, exact runtime digest, inference-settings digest, and qualification status and digest (PRD-013/014 functions, reused).
- The execution-environment digest (PRD-013).
- `CASE_SET_VERSION` and a digest of the case table (`tests/_model_certification.CASES`).
- **Production config identity.** The SEC-009 security-field set digest of the operator config (`authority_approval.build_security_field_records` / `compute_set_digest`, reused), plus the config file's own content digest.

A single composite digest covers all of these fields. Any change to any of them resets the streak to 0.

Tests:
- one reset test per component;
- mutation check: dropping any field from the digest must fail a test;
- a tampered record is never counted.

The old 2-run streak is not carried over: the new identity has no prior trials.

## 4. Streak record (closes LIVE-CERTIFICATION-REPEATED-TRIALS-001 when it reaches 3)

These are additive changes to `kriya/core/model_certification.py` and `scripts/certify_model.sh`:

- **Its own directory per trial.** Every trial gets `evidence/PRD-036/matrix/trial-<n>-<UTC>/`, holding junit, pytest.txt, the report JSON and MD, and the release-candidate identity.
- **An append-only, digest-sealed trial log.** The log is keyed by the release-candidate digest (§3). It lives outside the workspace, beside the certification records. Each entry records:
  - start and end timestamps;
  - the revision;
  - the identity digest;
  - the case-set version;
  - the per-case verdicts;
  - the matrix content digest;
  - the outcome;
  - the streak after this trial.
- **Streak rule.**
  - An 11/11 matrix on the same identity adds 1.
  - Any failed or NOT_RUN case sets it to 0.
  - A different identity starts a new log at 0.
  - A partial rerun is never a matrix: a report missing any case is NOT_RUN, so it fails.
- **CURRENT keeps its PRD-035 meaning.** It stays "the latest full matrix passed". The streak is reported by `scripts/release_candidate.py status`: a streak is keyed to a candidate digest, which `kriya model certification` does not know.

## 5. Candidate HEAD and evidence commits after the streak

Step 4 changes executable code (`kriya/core`), so `7238740` cannot be the candidate. The sequence:

1. **Commit A:** the PRD-036 certification and gate implementation, with its tests. Lint is at zero.
2. Deterministic gates run at Commit A. **Freeze:** tag `prd-036-rc1` at Commit A, locally.
3. At the frozen tag:
   - macOS `certify.sh`;
   - the production doctor;
   - the canary;
   - matrices #1, #2 and #3.
4. **Commit B:** evidence, handover and registry only.
5. The gate script (§6) asserts `git diff --name-only prd-036-rc1..HEAD` touches only `handover/`, `docs/`, `evidence/` and `*.md`/`*.csv`. It fails on anything under `kriya/`, `plugins/`, `scripts/`, `tests/`, `pyproject.toml`, `.github/` or the lock file.
6. The closure names both revisions: the **candidate** (what was certified) and the **evidence** revision (what records it).

Hosted Linux certification and the Windows job are dispatched at the **tag** (`gh workflow run ci.yml --ref prd-036-rc1`), so they certify the exact candidate. They are dispatched again at the final HEAD as a consistency check. Pushing the tag and the commits needs the user's approval.

If any gate forces a code change, that is a new commit and a new tag (`rc2`), and the whole of step 3 is redone. The streak restarts from 0.

## 6. Final gate (`scripts/prd036_gate.py`, machine-readable under `evidence/PRD-036/`)

The script reads the evidence and decides. It runs nothing live itself, and each item below either passes or fails:

- **certify.sh summary:** CERTIFIED. Pytest has 0 failed and 0 errors, and every skip is listed with its reason. The scanner stage actually executed and the release stage passed.
- **Release identity:** CURRENT against the tag.
- **Doctor:** the JSON shows `production_ready == true` and names the config digest and workspace.
- **Streak:** the trial log shows streak == 3 on the candidate identity, with every trial present.
- **Canary:** the canary result is present with its leak checks.
- **Hosted runs:** the run IDs and per-job conclusions for Linux certification and the Windows job, read with `gh run view`.
- **Backlog:** the registry has zero open P0/P1.
- **Tracked tree:** clean.
- **Evidence diff:** the post-candidate diff is evidence-only (§5).
- **False-success cross-checks (§10 of the instruction):**
  - no case or canary with SUCCESS but failed gates;
  - no commit without a gate report;
  - no stale qualification or certification;
  - no identity mismatch across trials;
  - no containment fallback event;
  - no UNCERTAIN recovery state.

The overall result is VERIFIED only if every item passes.

## 7. Decisions (user, 2026-09-28: Option 1 for all four)

- **D1: operator production config.** `~/.kriya/operator/prd036-production.yaml` is operator-owned and outside every workspace. demo-03 is only the template; the doctor is the evidence. It sets:
  - `runtime_profile: production`;
  - `qwen3-coder:30b` at the qualified settings, with the `qwen3.6:35b-a3b-q4_K_M` fallback at its qualified 18/18 settings;
  - `nomic-embed-text`;
  - static analysis enabled and required: Semgrep 1.178.0 from the pinned image, with the rule pack in `~/.kriya/operator/prd036/`;
  - state, memory and skills under `~/.kriya/operator/prd036/`.

  It was approved once for the canary workspace (`~/.kriya/operator/prd036/canary/repo`) into `~/.kriya/operator/prd036-production.trust.json`. Its digest is bound into the candidate and it is immutable during certification.
- **D2: matrix bindings.** The PRD-035 matrix is unchanged: 11 cases, `CASE_SET_VERSION` 1. With `KRIYA_RELEASE_CONFIG` the harness takes the primary and fallback bindings from D1, merged exactly as `load_config` merges them. The matrix certifies model and coding behaviour on the direct path. It does **not** certify the enforce controller; the doctor and the canary do.
- **D3: legacy `logs/traces.db`.** Left untouched. The doctor reports `persistence.traces` as a WARN, which does not block readiness.
- **D4: canary.** `scripts/prd036_canary.sh` runs the demo-03 brownfield fix at the frozen candidate with the exact D1 config under `runtime_profile: production`. It runs non-interactively (`-y`) in a workspace reset to the baseline, so no old result is reused.
  - **Before and after snapshots:** containers, networks, worktrees, the workspace lock, processes naming the workspace, and the tracked diff.
  - **Independent re-verification:** `mvnw -o` compile and test.
  - **Archived:** the RunRecords, the release identity, and the doctor output before and after.
  - **Verdict:** `scripts/prd036_canary.py` requires every check.
  - **Retained worktrees (user decision, 2026-09-28, after the rc2 canary).** At rc2 the canary failed only `no_leaked_worktrees`. The run left two worktrees: the plan worktree, and the subtask engine's worktree nested inside it. Both were reset to the workspace HEAD, had no tracked changes, and held nothing but `target/` caches. That is the documented reuse (`remove_git_worktree` resets rather than deletes). The check was a harness defect: it counted any new worktree as a leak.
    - **Rule since rc3:** a retained worktree is acceptable only if all of these hold. Anything else is a leak.
      - It is at a documented Kriya location: `<ws>/.kriya/worktree`, or that worktree's own `.kriya/worktree`. The first path is bound to `create_git_worktree` by a test.
      - It is registered, and is not locked, prunable or broken.
      - Its HEAD is the workspace HEAD.
      - It has no tracked changes.
      - Its only untracked entry is the permitted nested worktree.
      - Its only ignored files are listed build caches.
      - It holds no `.kriya` state other than that nested worktree.
    - **Bounded reuse.** The canary runs twice on the same candidate and config. Run 2 starts from run 1's state with only the tracked files restored. The retained worktree set must be identical after both runs: same set, same size, no new nesting level.
    - **Superseded (user decision, same day).** The nested topology itself was not accepted as production architecture, and under the Fix-Now rule it was fixed, not backlogged (WORKTREE-CANONICAL-ROOT-001).
      - Candidate worktrees are now siblings under `<ws>/.kriya/worktrees/`, rooted at the run's canonical workspace.
      - The canary rejects any nested worktree. The permitted-nesting allowance above is gone.
      - The fix is a production change, so it is a new release candidate.
    - **rc4 canary (2026-09-28): FAIL, classified as a stale environment plus two defects.** Both runs failed only the worktree checks, all over `.kriya/worktree/.kriya/worktree`. `run-1/before.json` shows that worktree already registered before run 1 started; it was created at 12:15Z by the rc3 run, about two hours before run 1. rc4 created only the sibling candidate worktree. Evidence: `evidence/PRD-036/rc4-canary-failed-stale-worktree/`. The user decided to fix both defects in one rc5 batch:
      - **CANARY-FRESH-WORKSPACE-001 (own bug, harness).** The canary's "fresh workspace" was `reset --hard && clean -qfdx`, and `git clean` never removes a directory holding a `.git` file. `prd036_canary.py reset` now removes every non-main worktree, prunes, resets, runs `clean -qffdx`, and refuses unless only the main worktree is left. The verdict's `starts_from_expected_worktrees` check requires run 1 to start from the main worktree alone and run 2 from exactly run 1's final set, so a stale environment is never read as something the candidate did.
      - **WORKTREE-LEGACY-NESTED-001 (production).** Kriya kept the old layout's nested worktree forever; see docs/design.md (Persistent Worktree Sandbox).
  - **rc5 (355417d, candidate `b8d3bc86…`), 2026-09-28: certification and canary PASS, matrix streak reset at trial 3.** Certification was CERTIFIED (7190 passed, 0 skipped; scanner 27 passed). Both canary runs passed every check; the reset removed the three worktrees left by the rc4 run, run 1 started from the main worktree alone, and run 2 from exactly run 1's set. Matrix trials 1 and 2 were 11/11; trial 3 was 10/11 (C6 `quality_gates_exhausted`), and the streak went back to 0. rc5 is kept as failed historical evidence: certification and canary in `evidence/PRD-036/rc5/`, the three trials at `evidence/PRD-036/matrix/trial-20260928T154955Z`, `-160052Z` and `-161219Z` (the paths the sealed rc5 streak log records), and a copy of that streak log in `evidence/PRD-036/rc5/`.
    - **Classification: Kriya defect, WORKFLOW-RECOVERY-HANDBACK-001 (production).** The tooling behaved correctly (`certify_model.sh` exited 1, the streak reset). In C6 the primary's first candidate removed `add(a, b)`; the pre-write gate rejected it and API_CONTRACT_RECOVERY restored the contract deterministically. Its REPAIR_BEHAVIOR attempts on the primary then failed, and when recovery's own budget ran out, the retry policy stopped the run with full-set retries and the configured fallback never tried (`fallback_transitions: 0`). Trials 1 and 2 never needed recovery and reached the fallback. The C6 case was not changed and rc5 was not re-run for variance (user decision, 2026-09-28). The fix is a production change, so it is a new release candidate (rc6) with a fresh streak; see docs/design.md (retry progress) and the registry row.
  - **rc6 (29c26d2, candidate `2c0e23da…`), 2026-09-28: certification PASS, canary FAIL at run 2.** Certification was CERTIFIED (7201 passed, 0 skipped). Canary run 1 passed every check. Run 2 passed every worktree, leak and doctor check, but its `kriya` run failed (`TERMINAL OBLIGATIONS UNSATISFIED: plan.subtask.s1.requires.driver_repository_access (violated)`), and nothing was applied. Evidence: `evidence/PRD-036/rc6/`. No matrix ran on rc6.
    - **Classification: Kriya defect, PLAN-OBLIGATION-SUPERSEDED-001 (production).** The Planner's first draft declared that s1 requires `driver_repository_access` with no provider. validate_plan rejected it (SUBTASK_REQUIREMENT_UNPROVIDED) and recorded that obligation VIOLATED and terminal-required. The repaired draft dropped the requirement and validated, s1 completed, and every quality gate passed. The terminal obligations check still failed the run on the rejected draft's record, because the dropped-requirement pass only handled records that had been SATISFIED. The candidate's code change was not at fault; the trigger was Planner variance in the first draft. Fixed under the Fix-Now rule, so this is rc7 (user rule, 2026-09-28: a genuine defect in certification or the canary produces rc7).
  - **rc7 (2ab3e83, candidate `48450031…`), 2026-09-28: certification PASS, canary PASS, matrix trial 1 10/11.** Certification was CERTIFIED (7210 passed, 0 skipped). Both canary runs passed every check, with 0 Planner repair rounds. Matrix trial 1 (`evidence/PRD-036/matrix/trial-20260928T184841Z`) failed C8 (`quality_gates_exhausted`), and the streak stayed at 0. Evidence: `evidence/PRD-036/rc7/`, including a copy of the sealed streak log.
    - **Classification: Kriya defect, FAILURE-SIGNATURE-RUN-NOISE-001 (production).** In C8 the primary's candidate kept failing the same regression test the same way (`ValueError: qty must be positive`, same test, same line). Each failure's signature differed only in the object address pytest printed (`<inventory.Inventory object at 0x…>`), so every repeat read as a new failure family and reset the targeted and fallback budgets. Over 10 attempts the retry loop never escalated to the configured fallback (`fallback_transitions: 0`, full-set 2 of 4), and the global ceiling ended the run. The same class affected Java identity hash codes in exception messages and elapsed times. Fixed under the Fix-Now rule, so this is rc8; C8 was not changed, and rc7 was not re-run for variance.
- **D5: execution.** The agent runs the long jobs with no aggressive polling. The order:
  1. context certification (done, CERTIFIED; the doctor already reports `PRODUCTION_READY=true` on D1);
  2. freeze;
  3. macOS certification;
  4. the canary;
  5. matrices 1, 2 and 3 back to back, with no code, config, model or settings change between them.

## 8. Commits planned

| Commit | Contents |
|---|---|
| A | The release-candidate identity, the streak and trial log, the certify_model.sh trial directories, the gate script, and tests with mutation checks. `tests/_live_identity.py` too, if D2 is recommended. |
| — (only if found) | A separate fix commit for any real defect, then a new tag and a streak reset. |
| B | `PRD-036_CERTIFICATION.md` and `PRD-036_FINAL_CLOSURE.md`, the evidence, the registry and tracker, and the retarget of `CERT-NETWORK-DEPENDENCY-001` (P3). |

On the registry and tracker:
- LIVE-CERTIFICATION-REPEATED-TRIALS-001 closes only at streak 3.
- `CERT-NETWORK-DEPENDENCY-001` (P3) targets "PRD-036 canary/final gate" today. It is retargeted after PRD-036, not implemented.

No push without approval.
