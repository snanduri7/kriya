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

## Logging location closure (2026-09-25)

Closes the residual recorded above: the packaged `logging.file: ./logs/kriya.log` resolved against the process CWD, so
every command wrote `logs/kriya.log` into the directory it ran from. This follows the user's "PRD-010 Logging
Location Closure" and the "Logging Config Addendum".

**Design** (`kriya/core/logging_setup.py`, the one owner of log locations):

**Log directory,** first match wins; each value is canonicalized and a relative one is a typed `LogDirectoryError`,
never CWD-anchored:
1. `KRIYA_LOG_DIR`;
2. `logging.directory`: absolute, with `~` expanded and realpath'd once in `resolve_config_state()`, so the value
   SEC-009 digests is exactly the directory opened;
3. `~/.kriya/logs`.
   - This matches the existing `~/.kriya/authority` and `~/.kriya/mcp_approvals` home.
   - `platformdirs` is not installed, and adding a dependency would change the lock file.

**What gets written, and when:**
- **Application log:** `<dir>/kriya.log`, controlled by `logging.file_enabled`.
- **Run log:** `<dir>/runs/<run_id>/kriya.log`, controlled by `logging.run_file_enabled`.
  - It is attached by `begin_mutating_run()` on the new-run branch only, after the RunRecord is saved.
  - It is detached after the terminal-record and retention lines, so they land in it.
  - Its first line is `# Kriya run <run_id> workspace <path>`, so runs from different workspaces are distinguishable.
  - `run_id` is validated with `persistence._valid_run_id` before it becomes a path segment.
  - A run log that cannot be opened is warned and skipped; it never alters the run.
- **Invalid or unwritable directory:** `LogDirectoryError` becomes the clean CLI error "Error configuring logging" and
  exit 1. There is never a fallback to `./logs`.
- **Console and `--json`:** console logging is unchanged, and `--json` stdout is unaffected; logs go to stderr and
  files only.
- **Commands without a run:** `doctor` (plain) writes only the application log. `runs`/`authority` return before
  logging is configured. `doctor --production` stays console-only and gets a new required check, `persistence.logs`:
  resolve, then probe without creating, then check the app log file is writable. It WARNs when both log files are
  disabled.

**SEC-009 classification:**
- `logging.directory`: any string is SECURITY_AUTHORITY (a filesystem write target); `null` is overridden to
  REPOSITORY_SAFE.
- `logging.file_enabled` and `logging.run_file_enabled` are REPOSITORY_SAFE (turning logging off removes a
  capability, the `logging.file: null` precedent).
- This follows CLAUDE.md's new-field rule. No existing boundary changed.
- The existing production approval in `~/kriya-live-validation/prd010-doctor-live` was re-checked after the change and
  still loads (the packaged default is trusted and not in the security-field set).

**`logging.file`** is deprecated and never opened.
- It stays in the schema with its SEC-009 classification unchanged, so the existing user configs and the SEC-009
  containment tests keep working. A warning names the real location.
- Honoring it would have failed the closure's own required behaviour: an auto-discovered repo config's
  `./logs/kriya.log` anchors to that repo, which is the CWD.
- The two `~/kriya-live-validation` configs that set it now log to `~/.kriya/logs` instead.
- Legacy `./logs` directories are never migrated or deleted.

**Tests:**
- `tests/test_logging_location.py` (23):
  - precedence env > config > default, and the same path from four CWDs;
  - relative/empty config and env values are typed errors; load-time canonicalization through a symlink;
  - load rejects a relative directory with nothing created; a repository `logging.directory` needs security authority;
  - null/disabled fields are repository-safe; the packaged default names no relative file;
  - the app log goes to the canonical dir with the CWD left untouched;
  - an unwritable directory is a typed error, and exit 1 through the CLI;
  - each run gets its own run log keyed by `run_id` (two workspaces, detached after the run, lines also in the app log);
  - `run_file_enabled: false` writes no run log, and a run with logging never configured attaches nothing;
  - generate bootstrap creates no `cwd/logs` (only its own `.kriya/` run state; the `--json` stdout contract is intact;
    the run log is keyed to the run record's id);
  - `doctor --production` from two CWDs;
  - the plain doctor, `runs status` and `authority inspect` leave the workspace byte tree unchanged, including `.git`.
- The root logger is cleared in each test: `configure_logging()` is a no-op when handlers exist, and pytest installs
  its own.
- `tests/test_production_doctor.py` (+4): the log check passes without creating the directory; a relative env value
  fails and blocks; an unwritable directory fails; both files disabled is WARN. The pinned check-ID list gains
  `persistence.logs`.
- `tests/conftest.py` (new): a session-scoped autouse fixture points `KRIYA_LOG_DIR` at a temp dir, so the suite,
  including CLI subprocesses, never writes the real `~/.kriya/logs`.

**Assertion-direction log:**
- `test_cli_logging_file_approval_reaches_configure_logging_end_to_end` was replaced by
  `test_cli_logging_directory_approval_reaches_configure_logging_end_to_end`.
  - The same two-direction SEC-009 proof now runs on the field that actually decides the log location: denied means
    nothing is created, approved means the target is written.
  - It adds "no `ws/logs`".
- New `test_cli_deprecated_logging_file_is_never_opened`: an in-workspace `logging.file` loads but writes nothing into
  the workspace.
- This is stricter, not weaker. The `logging.file` containment tests in `test_sec009_config_authority.py` are
  unchanged.

**Evidence:**
- **Mutation checks,** each caught:
  - accepting a relative directory;
  - removing the run-log attachment (2 tests fail);
  - restoring a CWD-relative `logging.file` handler (2 tests fail).
- **Plain-runner** (HOME and KRIYA_LOG_DIR in the scratchpad):

| Suite | Result |
|---|---|
| `test_logging_location` | 23/0 |
| `test_production_doctor` | 53/0, plus the 2 real-Docker tests 2/0 with the real HOME |
| `test_doctor_command` | 8/0 |
| `test_sec009_config_authority` | 51/0 |
| `test_sec009_p2_authority_approval` | 36/0 |
| `test_config` | 31/0 |
| `test_generate_json_contract` | 17/0 |
| `test_prd008_recovery` | 23/0 |
| `test_bootstrap_contract` | 18/0 |
| `test_cli_smoke` | 65/0 |
| `test_run_ownership` | 33/0 |
| `test_repl` | 19 run; 12 need pytest's `capsys` and were not run here |

- Pre-existing and not touched: ruff F401 (an unused `compute_violations` import in `authority_inspect`).

**Focused command:**
```bash
.venv/bin/pytest -ra tests/test_logging_location.py tests/test_production_doctor.py tests/test_doctor_command.py tests/test_sec009_config_authority.py tests/test_sec009_p2_authority_approval.py tests/test_config.py tests/test_config_command.py tests/test_generate_json_contract.py tests/test_prd008_recovery.py tests/test_run_ownership.py tests/test_bootstrap_contract.py tests/test_cli_smoke.py tests/test_repl.py tests/test_distribution_integrity.py
```

**`traces.db` still follows `paths.logs`** (resolved by the storage follow-up below):
- An auto-discovered repo `kriya.yaml` with `paths.logs: ./logs` resolves against the repo, which is the CWD. So
  `traces.db` still lands in `<repo>/logs/`. `~/kriya-live-validation/ma6_generate_check/logs/traces.db` is a real
  example.
- It was not moved in this change: `paths.logs` has its own SEC-009 path classification, and `traces.db` is persistent
  run history, not the logging service.
- The README, user guide and CLAUDE.md now say precisely that the guarantee covers `kriya.log` and the run logs.
- Whether `traces.db` should come under the same rule is left to the user as a follow-up decision.

**Other notes:**
- **Real-config check.** The verbatim `logging` block from `~/kriya-live-validation/ignite_qpid_protocol/kriya.yaml`
  (`file: ./logs/kriya.log`) ran with `kriya plugins` from a scratch folder:
  - exit 0 and the deprecation warning;
  - the application log was written to the configured `KRIYA_LOG_DIR`;
  - the folder held only `kriya.yaml`.
  - The real `ignite_qpid_protocol` config itself is unapproved under SEC-009, so it is denied before logging starts,
    and its existing `logs/kriya.log` was verified unchanged (same mtime and size).
- **`--json`:** an invalid or unwritable log directory fails before any subcommand, with exit 1 and stderr only. That
  is the same shape as the existing config-load failure, so `--json` stdout is never partially written.
- **Plain-runner disclosure:** `tests/conftest.py` now exists. Its only effect is setting `KRIYA_LOG_DIR`. The plain
  runner sets HOME and `KRIYA_LOG_DIR` itself to compensate, so the earlier "no conftest" note is superseded.
- **PRD-002:** `tests/test_distribution_integrity.py` 30/0 on the plain runner, with the new module and conftest in
  place.

## traces.db storage follow-up (2026-09-25)

The user decided that `traces.db` is persistent run history, not log output, so it moves out of `paths.logs`.

**Design** (`kriya/core/state_paths.py`, the one owner of the trace location):

**State directory,** first match wins:
1. `KRIYA_STATE_DIR` (absolute);
2. `paths.state` (new, default null);
   - Any relative value resolves once, in `resolve_config_state()`, against the setting config file's directory,
     never the CWD. `~` expands.
   - It is SEC-009-classified like the other `paths.*`: REPOSITORY_SAFE inside the workspace, SECURITY_AUTHORITY on an
     escape, REPOSITORY_SAFE when null.
   - A relative value reaching the resolver outside config loading is a typed `StateDirectoryError`.
3. `~/.kriya/state`.

**Consumers:** the trace database is always `trace_db_path(cfg)` = `<state>/traces.db`. That covers all 6
`workflow.py` sites, `_mark_run_in_progress`, `kriya traces`, and milestone planning, whose `logs_path` parameter is
renamed `trace_db`.

**`paths.logs` no longer controls anything.**
- File logs are `logging.directory`; run history is `paths.state`. The field stays so existing configs load.
- It was deliberately not made a second log-location setting: `paths.logs` is REPOSITORY_SAFE inside a workspace,
  while `logging.directory` is always SECURITY_AUTHORITY, so honoring it would let a repository route logs into itself
  without approval. It would also bring logs back into the scratch repos that set `paths.logs: ./logs`.
- This is the reversible reading of "paths.logs controls only file logs" (the advisor concurred). It is flagged to the
  user.

**Legacy database:** `<paths.logs>/traces.db`, for an absolute `paths.logs` only. A relative one is never probed
against the CWD. The user's real legacy database is the packaged-default install-dir `logs/traces.db`, about 106 MB.
- **Detected** when it exists and is not the current database.
- **Reported:**
  - `kriya traces` prints a note on stderr while the new history is absent or empty;
  - `doctor --production` gives `persistence.traces` WARN, with the migration command as remediation.
- **Copied** only by the explicit `kriya traces --migrate-legacy`:
  - it uses the SQLite online backup (a WAL-safe consistent copy) into `<target>.migrating`, then an atomic replace;
  - it refuses when the target already exists, so it never merges;
  - it never moves or deletes the legacy file.
- **Read-only:** `kriya traces` never creates the database.

**Doctor:** `persistence.traces` now checks the state directory: resolve, probe without creating, and an existing
database must be readable and writable. It is separate from `persistence.logs` (the log directory). Trace
retention/security code is unchanged and independent of logging.

**Scope:** workspace run control state (`.kriya/`: run lock, RunRecords, checkpoints) is unchanged and stays in the
workspace, because PRD-008 recovery depends on it. The docs say so explicitly.

**Tests:**
- `tests/test_state_location.py` (22):
  - the default is stable across CWDs;
  - `paths.logs`, `logging.directory` and `KRIYA_LOG_DIR` changes leave the trace database unchanged;
  - an explicit directory and the env override work;
  - relative config and env values are typed errors;
  - a relative `paths.state` resolves against the config file's directory from two CWDs;
  - a repository state directory outside the workspace needs security authority, and null is safe;
  - writes land in the state directory, not `paths.logs` or the CWD;
  - legacy detection works: present and distinct only, and a relative `paths.logs` is never CWD-probed;
  - `kriya traces` reports the legacy database and creates nothing;
  - migration copies and keeps the original, refuses to merge into an existing database, and refuses when there is no
    legacy database; the CLI migration is explicit and a second run is refused;
  - the doctor check is independent of logs, WARNs on a legacy database, and FAILs on invalid or unwritable
    directories;
  - read-only `traces` from a repository creates no database anywhere.
- `tests/conftest.py`: each test gets its own `KRIYA_STATE_DIR`, because tests read trace rows back and a shared
  database would leak between tests. Log isolation is unchanged.

**Assertion-location log.** Every change moves where the database is found; no assertion is weakened.
- About 40 test sites that located the database as `cfg.paths.logs/traces.db` now use `trace_db_path(cfg)`: in
  `test_workflow.py` (the `_latest_trace_row(cfg)` helper plus direct sites), `test_cli_smoke.py`,
  `test_validation_baseline.py` (`_latest_trace_run_events(cfg)`), `test_milestone3_4.py` and
  `test_traces_command.py`.
- `test_milestones.py` now passes `trace_db=`. The no-I/O test was renamed.

**Evidence:**
- Mutation checks, each caught:
  - the trace path derived from `paths.logs`;
  - migration allowed to merge;
  - `kriya traces` creating the database (2 tests fail).
- Plain-runner, with a per-test `KRIYA_STATE_DIR` emulating the conftest:

| Suite | Result |
|---|---|
| `test_state_location` | 22/0 |
| `test_workflow` (all 28 trace-reading tests) | 28/0 |
| `test_traces_command` | 7/0 |
| `test_cli_smoke` | 65/0 |
| `test_validation_baseline` | 54/0 |
| `test_milestones` | 68/0 |
| `test_production_doctor` | 53/0 |
| `test_logging_location` | 23/0 |
| `test_config` | 31/0 |
| `test_sec009_config_authority` | 51/0 |
| `test_distribution_integrity` | 30/0 |
| `test_run_events` | 5/0 |

- `test_milestone3_4::test_staged_skill_accrual` fails under the plain runner at an unrelated line (a staged-knowledge
  file), identically at HEAD in a clean worktree. It is a runner limitation, so its trace line is verified by pytest
  only.

**Focused command:**
```bash
.venv/bin/pytest -ra tests/test_state_location.py tests/test_logging_location.py tests/test_traces_command.py tests/test_production_doctor.py tests/test_doctor_command.py tests/test_sec009_config_authority.py tests/test_sec009_p2_authority_approval.py tests/test_config.py tests/test_config_command.py tests/test_milestones.py tests/test_milestone3_4.py tests/test_validation_baseline.py tests/test_cli_smoke.py tests/test_run_events.py tests/test_generate_json_contract.py tests/test_prd008_recovery.py tests/test_run_ownership.py tests/test_bootstrap_contract.py tests/test_repl.py tests/test_distribution_integrity.py tests/test_workflow.py
```

## Final state and logging cleanup: `paths.logs` removed (2026-09-25)

The user's decision replaces the inert `paths.logs` of the previous step. The final model is:
- `logging.directory` for file logs (`KRIYA_LOG_DIR` > `logging.directory` > `~/.kriya/logs`);
- `paths.state` for persistent state and `traces.db` (`KRIYA_STATE_DIR` > `paths.state` > `~/.kriya/state`);
- `<workspace>/.kriya` for locks, RunRecords, checkpoints and recovery data.

**Removal:**
- `PathsConfig.logs` is gone, from the packaged default and from the SEC-009 static table.
- Any config naming it fails with a typed error, never reinterpreted:
  - `RemovedConfigFieldError` (a `ValueError`) with `REMOVED_PATHS_LOGS_MESSAGE`: "paths.logs was removed; use
    logging.directory for logs or paths.state for trace state."
  - A config file hits it in `resolve_config_state()`, before SEC-009. It is passed through untyped-wrapping and
    becomes the CLI's "Error loading configuration" with exit 1.
  - Programmatic `AppConfig(paths={"logs": ...})` hits a `PathsConfig` before-validator.
  - Attribute assignment is rejected by pydantic.
- No production code reads it. A repository guard test fails if `paths.logs` or `logs_path` reappears anywhere in
  `kriya/` beyond the removal message.

**Workspace-local state:**
- `require_workspace_local_state_under_kriya_dir()` runs at load, after canonicalization. A `paths.state` resolving
  inside the workspace (the config loader's `workspace_root`) must be strictly beneath `<workspace>/.kriya/`.
  Otherwise it raises `StateDirectoryError`.
- Valid: `.kriya/state`. Rejected: `./state`, `./logs`, `state`, `.kriya` itself, `src/.kriya/state`.
- Outside the workspace, SEC-009 path authority applies: denied until `kriya authority approve`, then it works.
- No analysis-ignore rules were added. The pre-existing static `logs`/`memory` ignore entries in triage, analyzer and
  workflow_controller stay: they skip `logs/` folders that older Kriya runs left in real workspaces, and removing them
  would change repository analysis there. Only the wording that named `paths.logs` changed.

**Legacy migration without `paths.logs`:** `kriya traces --migrate-legacy [--legacy-path <abs traces.db>]`.
- `--legacy-path` migrates exactly that file; it must be absolute and exist.
- Without it, only the one well-defined historical default is auto-detected: the packaged `./logs` against the
  install dir, i.e. `<install>/logs/traces.db`, via `historical_default_trace_db()`.
- It uses the SQLite online backup, refuses an existing destination, never merges, and never deletes the source.
- `--legacy-path` without `--migrate-legacy` is a usage error.
- `kriya traces` and the doctor stay read-only.
- The user's real database is exactly that historical default. `kriya doctor --production` reports it as
  `persistence.traces` WARN, which also makes `runtime.fixed_guarantees` WARN (non-blocking) until it is migrated.

**Test isolation:** `tests/conftest.py` also points `historical_default_trace_db` at a non-existent per-test path.
Without that, the developer's real `<install>/logs/traces.db` made the doctor healthy-run test machine-dependent;
this was found on the plain runner.

**Other repository callers:**
- `spikes/eval_harness` now shares `paths.state` (`runs/<batch>/state`) and `logging.directory` (`runs/<batch>/logs`)
  per batch.
- `report.py` gains `--state-dir` and still reads older batches' `logs/traces.db`.
- The README is updated.

**Not edited:** 15 kriya.yaml files outside the repository still set `paths.logs` and will now fail to load with the
actionable error. They were not edited automatically; one is the deliberately hostile `mcp-security-probe` fixture.
- `~/kriya-live-validation/{delete, ma6_generate_check, ma6_milestone_check, milestone_task_cli}`
- `~/kriya-live-validation/sec001-adversarial-live-01..04`, `sec006-live-01`
- `~/kriya-live-validation/{ver005-py-multipkg-live-01, ver005-py-multipkg-live-02-run4, ver005-recv002-live-01,
  ver006-adversarial-e2}`
- `~/kriya-live-validation/graphify-poc/spring-boot-application-example`
- `~/kriya-live-validation/mcp-security-probe/hostile_repo`

**Tests:**
- `tests/test_state_location.py` was rewritten (34):
  - schema; actionable error through load, CLI and programmatic paths;
  - repository guard;
  - default stable across CWDs; each setting controls only its own location; env overrides independent;
  - typed errors for relative values;
  - `.kriya/*` valid (3 cases, relative to the config file from a sub-CWD); arbitrary workspace-local rejected
    (5 cases, nothing created);
  - external state denied, then approved and working, with nothing created;
  - historical default formula; only the historical default auto-detected;
  - `--legacy-path` migration copies and keeps the source; destination-exists refuses without merging; bad legacy
    paths refused; CLI flow plus misuse;
  - read-only `traces` and doctor create no database or state directory; doctor independent of logs, WARN on a
    historical database, FAIL on invalid or unwritable.
- 61 test lines that set `cfg.paths.logs = ...` were removed. They existed only for trace isolation, which the conftest
  now provides. `paths` dict entries were removed in `test_tools` and `test_live_smoke`.
- No new ruff F findings in any touched file versus HEAD.
- **Mutation checks,** each caught:
  - dropping the `.kriya/` rule;
  - silently dropping `paths.logs`;
  - ignoring `--legacy-path` (3 tests fail).
- **Plain-runner:**

| Suite | Result |
|---|---|
| `test_state_location` | 34/0 |
| `test_logging_location` | 23/0 |
| `test_traces_command` | 7/0 |
| `test_production_doctor` | 53/0, 2 Docker-skipped under the fake HOME |
| `test_doctor_command` | 8/0 |
| `test_config` | 31/0 |
| `test_config_command` | 4/0 |
| `test_sec009_config_authority` | 51/0 |
| `test_sec009_p2_authority_approval` | 36/0 |
| `test_tools` | 24/0 |
| `test_generate_json_contract` | 17/0 |
| `test_cli_smoke` | 65/0 |
| `test_validation_baseline` | 54/0 |
| `test_milestones` | 68/0 |
| `test_distribution_integrity` | 30/0 |
| `test_bootstrap_contract` | 18/0 |
| `test_run_events` | 5/0 |
| `test_prd008_recovery` | 23/0 |
| `test_run_ownership` | 33/0 |
| `test_workflow` (trace tests plus the fallback chain) | 29/0 |

- `test_d1_operation_mode_authority` is class-based, so it was not collected by the plain runner; it had only a
  line deletion.

**Focused command:**
```bash
.venv/bin/pytest -ra tests/test_state_location.py tests/test_logging_location.py tests/test_traces_command.py tests/test_production_doctor.py tests/test_doctor_command.py tests/test_sec009_config_authority.py tests/test_sec009_p2_authority_approval.py tests/test_config.py tests/test_config_command.py tests/test_tools.py tests/test_d1_operation_mode_authority.py tests/test_milestones.py tests/test_milestone3_4.py tests/test_validation_baseline.py tests/test_cli_smoke.py tests/test_run_events.py tests/test_generate_json_contract.py tests/test_prd008_recovery.py tests/test_run_ownership.py tests/test_bootstrap_contract.py tests/test_repl.py tests/test_distribution_integrity.py tests/test_workflow.py
```
