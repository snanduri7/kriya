# PRD-010 Coding Agent Handover

## Status
READY_FOR_PYTEST_VERIFICATION (reopened; see "Reopen - independent review closure" below; batch PRD-009 + PRD-010)

## Source identity
- Base revision: `d5d8ec5` in the target checkout (PRD-009 verified; tree-equivalent coding checkout revision `003099b`).
- Production implementation revision: `aa492ab` in the target checkout.
- Evidence whitespace correction: `9cace03` in the target checkout.
- Final revision / working-tree diff ID: the handover commit following `9cace03`.
- Kriya version: 0.1.0.

## Scope implemented
- Instruction file: `KRIYA_ALL_IMPLEMENTATION_INSTRUCTIONS_v1.0.md`, PRD-010.
- Requirements completed:
  - Added `kriya doctor --production` and stable `--json` output.
  - Added 19 deterministic checks covering the effective production profile, core plugins, workspace identity/lock acquisition, checkpoint/trace writes, workspace/temp capacity, Git worktrees, manifest-declared toolchains, a real OCI containment smoke command, egress and registry policy, local LLM connectivity, digest-bound runtime fingerprint, qualification status, embedding connectivity, optional LSP, role-model independence, release integrity, and PRD-009 fixed guarantees.
  - Assigned every result a stable ID, `PASS`/`WARN`/`FAIL`/`UNAVAILABLE` status, required flag, structured evidence, and remediation.
  - Made any required `FAIL` or `UNAVAILABLE` produce a nonzero CLI exit; warnings remain nonblocking.
  - Added a Docker-enabled real integration test and a live local-model fingerprint/qualification test.
  - Kept the command diagnostic-only: it creates/removes bounded write probes and a lock probe but does not change configuration, install dependencies, or auto-fix failures.
- Requirements deliberately not implemented: PRD-013/014's persistent exact-runtime qualification registry does not exist in current source. Until those tasks land, qualification uses the existing live-measured `KNOWN_MODEL_PROFILES`, while the runtime fingerprint independently binds the OpenAI model identity to Ollama's native artifact digest and metadata.

## Files changed
- Production: `kriya/production_doctor.py`, `kriya/cli.py`.
- Tests: `tests/test_production_doctor.py`, `tests/test_live_production_doctor.py`.
- Docs/decisions: `README.md`, this handover, tracker row, `handover/evidence/PRD-010/*`. `.eie/DECISIONS.md` is absent.

## Pre-change reproduction
- Command: inspect the base revision's `kriya.cli.doctor` declaration.
- Observed failure/gap: doctor accepted no production or JSON option and had no coherent deployment decision, stable check schema, OCI smoke check, exact runtime fingerprint, or qualification gate.
- Evidence: `handover/evidence/PRD-010/pre-change-reproduction.txt`.

## Implementation summary
`kriya.production_doctor` owns the deterministic preflight and returns a typed report. The CLI only selects rendering and maps `production_ready` to its exit status. External services are probed at explicit boundaries: Docker runs a read-only, capability-dropped, no-network container; Ollama supplies the exact model digest and native metadata; the configured embedding endpoint must return a nonzero vector. Optional LSP and role-model independence remain visible warnings because no current production policy requires them.

## Tests run by coding agent
| Command | Passed | Failed | Skipped | Duration | Notes |
|---|---:|---:|---:|---:|---|
| Production/legacy doctor tests | 27 | 0 | 1 | 4.74s | Docker smoke integration skipped because Docker daemon is unavailable |
| Config, authority, policy, containment, and plugin regressions | 171 | 0 | 28 | 8.53s | Docker-dependent OCI cases skipped explicitly |

The coding agent did not run the full project suite or live-model case; independent user verification owns those gates.

## Static/lint/architecture checks
- Compileall passed for all touched Python files.
- `git diff --check` passed after the evidence-only whitespace correction.
- Ruff passed for the new production-doctor modules and tests. `kriya/cli.py` has unrelated pre-existing Ruff findings outside this change.

## Live test additions
- Required by instruction: YES.
- Test file/case: `tests/test_live_production_doctor.py::test_production_doctor_recognizes_real_runtime_fingerprint_and_qualification`.
- Environment prerequisites: local Ollama-compatible endpoint; one exact qualified model from `KNOWN_MODEL_PROFILES` pulled (`qwen3-coder:30b`, `qwen3.6:35b-a3b-q4_K_M`, or `qwen3.5:9B`); Ollama native `/api/tags` and `/api/show` endpoints reachable.
- Exact command for Live-Model Verification Agent:

```bash
KRIYA_LIVE_BASE_URL=http://localhost:11434/v1 \
KRIYA_LIVE_LLM_MODEL=qwen3-coder:30b \
.newvenv/bin/python -m pytest -m live_model -ra -s \
  tests/test_live_production_doctor.py
```

- Expected invariant/evidence: the configured model appears at the OpenAI-compatible endpoint; native metadata contains the exact artifact digest; a stable 64-character fingerprint is produced; the exact model identity resolves to `known_production_profile`.

## Known limitations / residual risks
- Qualification is model-name-bound campaign evidence until PRD-013/014 persist and bind qualification to the exact runtime fingerprint.
- Exact fingerprinting currently requires an Ollama-compatible `/v1` URL plus `/api/tags` and `/api/show`; another OpenAI-compatible server reports `model.runtime_fingerprint=UNAVAILABLE` and fails production readiness.
- OCI smoke validation requires Docker and the configured base image to be available; the coding environment had no reachable Docker daemon.
- Java LSP and role-model independence are warnings because current configuration has no policy declaring either mandatory.

## Decisions recorded
- `.eie/DECISIONS.md` entries: none; the file is absent.

## Evidence artifacts
- `handover/evidence/PRD-010/pre-change-reproduction.txt`
- `handover/evidence/PRD-010/doctor-focused.txt`
- `handover/evidence/PRD-010/config-containment-plugin-regressions.txt`
- `handover/evidence/PRD-010/lint.txt`

## Verification-agent handoff
Run:

```bash
.newvenv/bin/python -m pytest -ra \
  tests/test_production_doctor.py \
  tests/test_doctor_command.py \
  tests/test_config.py \
  tests/test_config_command.py \
  tests/test_config_extra.py \
  tests/test_autonomy_config_registry_hosts.py \
  tests/test_sec009_config_authority.py \
  tests/test_execution_policy_config.py \
  tests/test_workflow_execution_policy_config_wiring.py \
  tests/test_containment.py \
  tests/test_containment_oci.py \
  tests/test_containment_oci_registry_scoped_unit.py \
  tests/test_process_controller_containment.py \
  tests/test_ver006_distrust_containment.py \
  tests/test_plugins.py \
  tests/test_plugins_command.py

.newvenv/bin/python -m pytest -m 'not live_model' -ra \
  --junitxml=handover/evidence/PRD-010/user-full.xml
```

After pytest passes, run the live command above and, with Docker available, rerun:

```bash
.newvenv/bin/python -m pytest -ra -s \
  tests/test_production_doctor.py::test_real_oci_containment_smoke_has_network_and_root_filesystem_closed
```

Record pytest in `handover/PRD-010_PYTEST_VERIFICATION.md` and real-environment evidence in `handover/PRD-010_LIVE_VERIFICATION.md`.


## Reopen - independent review closure (2026-09-25)

**Read this first: `production_ready` is now always `false`, by design, until PRD-013/014.**

The review requires `model.runtime_fingerprint` to fail closed as UNAVAILABLE until an exact-runtime qualification
binding exists, and a model name is not a qualification. So both model-binding checks are required UNAVAILABLE on every
deployment today. On a fully healthy machine they are the only blockers; a test pins exactly that set. This is the
intended PRD-010 outcome, not a regression.

Source: `handover/PRD-001_011_INDEPENDENT_REVIEW.md` (PRD-010: partial). Implemented at `d22cb8e`, after the PRD-009
reopen `3fb4749`.

### Findings and resolutions
| Finding | Resolution |
|---|---|
| `runtime.fixed_guarantees` is required and always PASSes | Derived from the checks that verify each guarantee (`FIXED_GUARANTEE_EVIDENCE`); its status is the worst of theirs. `candidate_isolation_fail_closed` comes from `git.worktree` and `isolation.candidate_worktree`. `checkpoint_persistence` comes from `persistence.checkpoints`. `trace_persistence` comes from `persistence.traces`. `no_uncontained_host_fallback` comes from `containment.no_host_fallback` and `containment.oci_smoke`. A mapping/guarantee mismatch is a FAIL. |
| `toolchain.required` uses host `which` and `sys.executable` | The stack comes from `PolymorphicValidator` and the identity from the PRD-011 resolver. The versioned image is attested in place with `allow_pull=False`. An absent image is UNAVAILABLE with a `docker pull <image>` remediation. An unsupported declared toolchain (e.g. Java 11) is a FAIL. An unattested Gradle build tool is a visible WARN (a PRD-011 residual, not claimed). A workspace with no detectable stack is a WARN. |
| `containment.oci_smoke` is a raw `docker run debian` | Runs through `resolve_containment_backend(cfg.autonomy.containment_backend)` and `ProcessController.run` (the one spawn point) on a scratch directory, using the attested toolchain image when there is one. The image must already be present (`docker run` would pull it). |
| `model.qualification` is name-based | A campaign model name is UNAVAILABLE `RUNTIME_QUALIFICATION_BINDING_UNAVAILABLE`, with `name_based_profile_is_authority: false`. An uncampaigned model is FAIL `MODEL_NOT_QUALIFIED`. |
| `model.runtime_fingerprint` only checks existence | Always UNAVAILABLE. A computed fingerprint is kept as evidence (`RUNTIME_QUALIFICATION_BINDING_UNAVAILABLE`); otherwise the reason is `RUNTIME_FINGERPRINT_NOT_COMPUTABLE`. |
| Unwrapped exceptions crash `--json` | Checks are table-driven (`_CHECKS`). `_run_check` turns any exception, or a result with the wrong ID or required flag, into that row's own FAIL (required) or WARN (optional) with `CHECK_RAISED`. The report always carries `PRODUCTION_DOCTOR_CHECK_IDS`. If config load fails, `doctor --production [--json]` emits one `config.load` FAIL. Plain `doctor` keeps its old stderr and exit-1 behaviour. |
| The doctor takes the real run lock and creates `.kriya/checkpoints` | The lock is probed with `probe_run_lock`, which is read-only, and a holder is FAIL with `held_by`. `_store_probe` writes a transient fsynced (flocked, for the lock store) file only inside an already-existing directory. Otherwise it checks write access on the nearest existing ancestor and reports `created_on_first_use_under`. A test proves a fresh workspace holds only `.git` after a run. |
| `egress.policy` doesn't check `llm.base_url` locality | Every model endpoint must pass `is_local_url`: `llm`, `embedding`, each `llm_chain` entry, and each `agent_llms` role `llm` and `llm_chain` entry. |
| PRD-009's precision-boundary report is missing | `semantic.precision_boundary`: optional, always WARN, with the scope taken from `SEMANTIC_REGION_SUPPORTED_SCOPE`. |

### Finding made while fixing: the old smoke certified something production never has
The old smoke started its own container with `--read-only` and asserted `test ! -w /`. Production containment
(`OCIContainmentBackend.prepare`) never passes `--read-only`, so the doctor certified a property the real runtime lacks.
That is false assurance, fixed locally.

The smoke now asserts only what the backend enforces for a DENIED profile:
- zero IPv4 routes;
- no non-loopback IPv6 routes;
- no non-loopback interface up;
- `CapEff` equal to 0;
- `NoNewPrivs` equal to 1;
- no leftover `kriya-oci-*` container.

"Only `lo` exists" was also wrong: a `--network none` namespace still lists the kernel's always-down fallback tunnel
devices (gre0, sit0, tunl0, ...). This was found on the real Docker run. The backend was **not** changed; adding
`--read-only` would alter a security boundary, which is out of scope. The renamed Docker test
`test_real_oci_containment_smoke_proves_network_capabilities_and_cleanup` replaces
`..._has_network_and_root_filesystem_closed`. `test_real_smoke_assertions_detect_an_uncontained_container` proves the
script really discriminates.

### Check IDs (pinned; the ordered tuple is `PRODUCTION_DOCTOR_CHECK_IDS`)
- Added: `isolation.candidate_worktree`, `containment.no_host_fallback`, `semantic.precision_boundary`, and `config.load`
  (only in the config-failure report).
- Unchanged: the original 19.

### Test changes (direction)
Nothing weakened. Old assertions changed as follows:
- **Stricter or reshaped to the new truth:**
  - "all required pass, `production_ready` True" became "blocked only by the two model-binding checks".
  - The model-qualification PASS became UNAVAILABLE, with the name recorded as non-authority.
  - The lock-contention test now holds a **real** lock instead of mocking `acquire_run_lock`.
  - The host-`which` toolchain test became image resolution, attestation and a no-pull assertion.
- **New tests:**
  - every check raising;
  - per-check exception confinement (capability profile, validator, lock probe);
  - no mutation;
  - a guarantee derived to FAIL;
  - two ways to weaken host fallback;
  - non-local `llm`, `embedding` and `llm_chain` endpoints;
  - precision boundary;
  - config-load JSON, and plain doctor on config-load failure;
  - the two real-Docker tests.

The live test is renamed to `test_production_doctor_fingerprints_the_real_runtime_and_withholds_name_based_qualification`.
It asserts a stable 64-character fingerprint computed twice from the real runtime, and that the doctor reports both
model checks UNAVAILABLE with that fingerprint as evidence.

### Coding-agent checks (not pytest)
- **Real doctor run on this machine, Docker available:**
  - Maven/JDK17 workspace: `toolchain.required` PASS (image digest attested, Maven 3.9 observed); `containment.oci_smoke` PASS; `runtime.fixed_guarantees` PASS; only the two model checks block.
  - Afterwards, the workspace held only `.git` and `pom.xml`, and zero `kriya-oci-*` containers remained.
- **Plain-Python runner (the scratchpad script, not pytest):**
  - `test_production_doctor` 47/0 (45 plus the Ruby and hung-daemon tests), including both real-Docker tests;
  - `test_doctor_command` 8/0;
  - `test_bootstrap_contract` 18/0.
- **Live test, plain runner, against local Ollama `qwen3-coder:30b`:** 1/0. This was a metadata-only probe with no generation, run by the coding agent without asking first (disclosed). It is evidence only; the live verifier still owns the live gate.
- The repo has no `conftest.py`, so the plain runner skipped no autouse fixtures. It is still not pytest, and the user's run is the gate.
- **Static:**
  - `typing.get_type_hints` resolves every function and class in `production_doctor.py` and `config.py`;
  - ruff F821 is clean on `kriya/` and `plugins/`;
  - compileall and `git diff --check` are clean.

### Known residuals
**Operator note - re-approve production configs.** Sealing `autonomy.egress_policy` adds it to the production profile's
security-field set: at `590fa16` a `runtime_profile: production` config had 9 violations, at HEAD it has 10. A SEC-009
approval (`kriya authority approve`, or a `--trust-file`) is digest-bound to the exact set, so every existing production
approval stops matching and `load_config` denies until you re-approve or regenerate the trust file. This is the
correct fail-closed behaviour. Re-approve before the live `doctor --production` step, or the doctor reports only
`config.load` FAIL.
- A Ruby workspace is now a required `toolchain.required` UNAVAILABLE (there is no production containment image for Ruby). Before, it passed on the host interpreter. This is honest under required containment and is covered by `test_a_stack_without_a_production_containment_profile_blocks`.
- A hung Docker daemon (timeout or OSError) is UNAVAILABLE, probed once per run (`test_a_hung_docker_daemon_is_unavailable_and_probed_once`).
- `production_ready` stays false until PRD-013/014 (above).
- PRD-011 (next batch) still has these open, and the doctor reports them honestly rather than working around them:
  - the `tomllib` import breaks Python 3.10;
  - Gradle build tool not attested;
  - Java 8/11 and Python 3.13+ are refused;
  - the attested digest is not pinned at run time.
- `REL-002` in the risk register is left at its current disposition until verification.

### Verification handoff (batch PRD-009 + PRD-010)
```bash
.venv/bin/pytest -ra \
  tests/test_production_doctor.py tests/test_doctor_command.py \
  tests/test_config.py tests/test_config_command.py tests/test_config_extra.py \
  tests/test_autonomy_config_registry_hosts.py tests/test_sec009_config_authority.py \
  tests/test_execution_policy_config.py tests/test_workflow_execution_policy_config_wiring.py \
  tests/test_containment.py tests/test_containment_oci.py tests/test_containment_oci_registry_scoped_unit.py \
  tests/test_process_controller_containment.py tests/test_ver006_distrust_containment.py \
  tests/test_workflow_controller.py tests/test_workflow_controller_enforce.py \
  tests/test_tool003_p2_deterministic.py tests/test_tool002_tool003_combined_closure.py \
  tests/test_dispatch_generation.py tests/test_plugins.py tests/test_plugins_command.py \
  tests/test_semantic_region_authority.py tests/test_bootstrap_contract.py
.venv/bin/pytest
```
With Docker running, the Docker tests execute inside both commands. Otherwise they skip, with the reason given.

Live (the user runs it; this is the real-environment checklist for the live verifier):
```bash
KRIYA_LIVE_BASE_URL=http://localhost:11434/v1 KRIYA_LIVE_LLM_MODEL=qwen3-coder:30b \
  .venv/bin/pytest -m live_model -ra -s tests/test_live_production_doctor.py
```
Expected results:
- the model is listed;
- native metadata carries a digest;
- the same 64-character fingerprint is computed twice;
- `model.runtime_fingerprint` is UNAVAILABLE with that fingerprint as evidence;
- `model.qualification` is UNAVAILABLE with `campaign_named: true`.

Then run `.venv/bin/kriya doctor --production --json` from a real production-configured workspace and confirm:
- the JSON parses;
- exit code is 1;
- the doctor created nothing in the workspace: `ls -a` is identical before and after (no `.kriya/`, no `logs/`).

## Live CLI verification findings (2026-09-25)

The user ran `kriya doctor --production --json` in `~/kriya-live-validation/prd010-doctor-live` (Maven/JDK 17 project,
`runtime_profile: production`, SEC-009-approved). The exit code was 1, the JSON parsed, and every infrastructure check
passed: toolchain, OCI smoke, host fallback, egress, connectivity, embeddings, fixed guarantees. Two real defects
surfaced that the mocked suite and the bare-`AppConfig()` live test could not see. Both are fixed in this commit.

1. **`model.qualification` was FAIL/`MODEL_NOT_QUALIFIED` for the campaign model `qwen3-coder:30b`.**
   - **Cause:** the status was keyed on the capability-profile *source*. The packaged `default_config.yaml` declares
     `llm.capabilities`, so every config built by `load_config()` resolves as `explicit_primary`. The
     `known_production_profile` branch was reachable only from a bare `AppConfig()`, which is exactly what the unit
     tests and the live test built.
   - **Fix:** campaign membership is now an identity lookup, `model_capabilities.is_campaign_named_model()`, using the
     same case-folded exact match as `KNOWN_MODEL_PROFILES`. Evidence gains `campaign_named`, and
     `name_based_profile_source` stays as diagnostics.
   - **Safety:** not false success. Both statuses are required-blocking, so the defect misreported *why* the doctor
     blocked, not *whether* it blocked.
2. **The doctor left `logs/kriya.log` in the workspace.**
   - **Cause:** `main()` called `configure_logging()` before any subcommand. The packaged `logging.file:
     ./logs/kriya.log` is never canonicalized (the SEC-009 `logging.file` realpath step only rewrites user-supplied
     values), so `os.path.abspath` anchors it to the CWD.
   - **Fix:** `main()` no longer configures logging for `doctor`. `doctor --production` configures console-only logging
     (`configure_logging(cfg, file_logging=False)`), and the plain doctor keeps file logging.
   - The earlier live checklist only looked for `.kriya/`, which was too narrow. It is now "nothing created".

**Regression tests,** each confirmed to fail on the pre-fix code:
- `test_a_loaded_configs_campaign_model_is_unavailable_not_failed` builds `llm` through `load_config()`.
- `test_a_loaded_configs_unknown_model_still_fails`.
- `test_production_doctor_cli_never_opens_a_log_file_in_the_workspace` clears the root handlers, because
  `configure_logging()` is a no-op when handlers exist and pytest installs its own, which would otherwise make the
  test pass vacuously.
- `test_plain_doctor_keeps_file_logging`.

**Assertion-direction log:**
- `test_live_production_doctor.py` now builds its config with `load_config()` and asserts `campaign_named is True`
  instead of source `known_production_profile`.
- `test_a_campaign_model_name_is_not_a_qualification` additionally asserts `campaign_named`.
- The required status is unchanged (UNAVAILABLE, blocking). Nothing was weakened.

**Pre-existing, out of scope, reported to the user:** for every other command (`generate`, `fix`, `ask`, ...), the
packaged `logging.file` still resolves against the process CWD, so those commands write `logs/kriya.log` into the
target repository. This contradicts CLAUDE.md, which says packaged-default relative paths resolve against the install
dir (`paths.*` and `plugins.directory` do). Changing it moves log output for every command and touches the SEC-009
`logging.file` closure code, so it was not changed in this batch.

Plain-runner: `test_production_doctor` 51/0 (parametrized cases expanded, real-Docker tests included),
`test_doctor_command` 8/0.
