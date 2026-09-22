# PRD-010 Coding Agent Handover

## Status
READY_FOR_PYTEST_VERIFICATION

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
