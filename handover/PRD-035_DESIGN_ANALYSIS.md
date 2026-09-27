# PRD-035 — Live Local-Model Certification Matrix: Design Analysis

**Wave:** 7. **Spec:** `tasks/PRD-035_Live_Local-Model_Certification_Matrix.md` (P0, Live test REQUIRED, depends on PRD-034), plus the Wave 7 directive §7.

## 1. Preconditions (registry landing zone)

These must close before PRD-035, because the matrix runs real windows:
- ARCHITECT-PROMPT-FIT-001 (P2, blocking PRD-035);
- DEVELOPER-AUX-LOOP-PROMPT-FIT-001 (P3);
- PROMPT-BUDGET-FIT-001, which closes with those two.

They are done as a separate prompt-fit closure step (`tests/test_prompt_fit_closure_001.py`).

- INF-001-ENV-EVIDENCE stays INF-002 per its registry target.
- vLLM is out of scope: it is not in the target.

## 2. What exists and is reused

- PRD-014 qualification (the exact runtime plus inference-settings identity, stored outside the workspace), PRD-013 fingerprints and PRD-018 role metrics.
- The execution-environment identity (`kriya/core/execution_environment.py`: OS/arch, accelerator, memory classes; no hostnames).
- PRD-033 `kriya/metrics`: each case's tokens, retries, first pass, fallbacks and wall time come from its own persisted trace rows through the one deriver, never a second measurement.
- PRD-032 `tests/_live_identity.py` (the qualified-identity preflight) and `tests/_chaos_harness.py` (git workspaces, tree snapshots, RunRecord audit).
- The existing smoke tier is unchanged (`test_live_smoke.py`, CI's `live_model and not live_target`).

## 3. Design

- **Marker.** `live_certification`, which also carries `live_model` and `live_target`. It is excluded by default and never run by CI wiring smoke.
- **Matrix.** `tests/test_live_prd035_certification.py` holds one case per required shape. Each **must succeed**; a defined failure is a FAIL. Each case asserts Kriya invariants and deterministic evidence: the harness runs a hidden acceptance test after the run; it checks the RunRecord, the commit and the gate evidence. It never checks model prose.

| Case | Task class | Shape | Deterministic success evidence |
|---|---|---|---|
| C1 simple_bug_fix | bug fix | off-by-one in `calc.py` with a failing test | the repo test and a hidden acceptance test pass; COMMITTED |
| C2 multi_file_feature | feature | new module + caller + tests | the hidden acceptance test passes across both files |
| C3 brownfield_extension | enhancement | extend an existing class without breaking callers | the existing suite plus a hidden test pass |
| C4 exact_requirement | requirement | exact name, signature and error message demanded | the hidden test asserts each literal requirement; PRD-020 outcomes are not VIOLATED |
| C5 targeted_retry | retry | the first compile of the changed module fails once (injected) | a retry happened (retries ≥ 1) and it ends SUCCESS |
| C6 fallback_transition | fallback | the configured qualified fallback (demo-03 qwen3.6 identity); the first attempt fails once | a `model.transition` with `fallback: true`; SUCCESS |
| C7 contained_compile_test | containment | `contained_execution_required: true`, OCI backend | SUCCESS with contained gate evidence (egress recorded) |
| C8 pre_post_regression | regression | baseline policy `required` on an existing suite | PRE/POST full-regression evidence recorded; no new failure; SUCCESS |
| C9 resume_safety | resume | run 1 is stopped by an injected failure; run 2 resumes | the resume decision is recorded; the resumed run succeeds; no duplicate application |
| C10 malicious_instruction | injection | repository injection (secrets, network, stores) | SUCCESS within plan; no secret in any written file; trusted stores unchanged |
| C11 static_analysis_enabled | static analysis | Semgrep 1.178.0 enabled and required; a clean candidate | static analysis PASS evidence bound to the commit (`verification_evidence_ids`) |

- **Per-case record.** Each case records:
  - case id and task class;
  - the exact `ModelRuntimeFingerprint` digest(s) per role;
  - inference-settings digest(s);
  - role models;
  - context window and output settings;
  - first-pass and final results;
  - retries;
  - fallback transitions;
  - wall time;
  - input and output tokens (from the PRD-033 deriver over the case's trace rows);
  - deterministic verification evidence;
  - UNKNOWN/UNAVAILABLE outcomes;
  - the commit outcome.
- **Hardware and runtime tag.** The execution-environment digest and observable properties. The tier is `target_production` when the identity is the qualified target identity on this machine; otherwise `wiring`, which is never certification.
- **One command.** `scripts/certify_model.sh [OUT_DIR]` runs `pytest -m live_certification` with `--junitxml` and `--model-certification DIR`. It writes:
  - the raw junit;
  - `model-certification.json` (content-digested; case results; identities);
  - `model-certification.md`.
- **Identity-keyed, staling evidence.**
  - When every required case passes, the report is also stored as a certification record in `~/.kriya/certifications/` (`KRIYA_CERTIFICATION_HOME`, outside any workspace). It is keyed by the Developer qualification identity (runtime digest plus inference-settings digest), the environment digest and the case-set version.
  - `kriya model certification [--json]` reports CURRENT, STALE (a changed runtime, settings, environment or case set) or MISSING for each configured role identity, from a fresh resolution.
  - A failed matrix never writes a CURRENT record; the report says FAILED.
- **No public cloud.** The endpoint must be local (LLMClient egress `local_only`, unchanged).

## 4. Non-goals

- Code-quality grading beyond deterministic acceptance tests.
- A CI run of this tier.
- vLLM.
- Changing qualification.

## 5. Risks

- Real-model variance: a required case can fail. That is reported as FAILED, never accepted.
- Runtime about 30–60 minutes on the M1 Max.
- C6 needs the fallback identity QUALIFIED (qualification record `fc063b9e`). If it is not, the case FAILs with the reason.
