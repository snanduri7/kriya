# PRD-011 Coding Agent Handover

## Status
READY_FOR_PYTEST_VERIFICATION

## Source identity
- Base revision: `6b91072aac92b6ef5c8cc7a30f10b71af44ce1a5`
- Final revision / working-tree diff ID: `e2781e7022452df272becbc9a2cca482405b9663`
- Coding revision: `54ad7983478bbb89ea7ef3cdcb0f0dd8058b8df3`
- Full-suite compatibility follow-up: `d6b775ee9538da072d33814a8d5816e37e06239b`
- Kriya version: `0.1.0`

## Scope implemented
- Instruction file: `KRIYA_ALL_IMPLEMENTATION_INSTRUCTIONS_v1.0.md`, PRD-011.
- Requirements completed:
  - Added a single `ToolchainIdentity` resolved from `PolymorphicValidator`'s existing stack decision.
  - Resolves Maven/Gradle JDK 17 or 21 and compatible Python 3.10-3.12 versioned OCI profiles from repository policy and the already-selected `JAVA_HOME` where applicable.
  - Rejects contradictory, unsupported, patch-specific/unprovable, or host/project-mismatched requirements before execution.
  - Selects prebuilt versioned images, inspects their content digest, and verifies the actual runtime (plus Maven version) before running target code.
  - Carries attested identity/digest evidence through finite processes, compile/test gate results, artifact preparation, and managed-service results.
  - Keeps managed-service readiness and probes inside the existing `docker exec` boundary and removes stale comments claiming the launched service is host-probed.
  - Routes contained Python compile checks through the selected container runtime.
- Requirements deliberately not implemented: none.

## Files changed
- Production:
  - `kriya/tools/toolchain_identity.py`
  - `kriya/tools/containment.py`
  - `kriya/tools/containment_oci.py`
  - `kriya/tools/process.py`
  - `kriya/tools/validate.py`
  - `kriya/tools/service_runtime.py`
  - `kriya/config/config.py`
- Tests:
  - `tests/test_prd011_toolchain_identity.py`
  - `tests/test_prd011_toolchain_identity_oci.py`
  - `tests/test_live_prd011_toolchain_parity.py`
- Docs/decisions:
  - `handover/PRD-011_CODING_HANDOVER.md`
  - `handover/TASK_STATUS_TRACKER.csv`
  - No `.eie/DECISIONS.md` exists in this repository.

## Pre-change reproduction
- Command: `rg -n "_MAVEN_IMAGE|_PYTHON_IMAGE|java_home_override.*NOT|whichever JDK" kriya/tools/containment_oci.py kriya/tools/validate.py`
- Observed failure/gap: Maven containment always selected `maven:3.9-eclipse-temurin-21`, Python always selected `python:3.12-slim`, and validation results did not retain the image digest/runtime identity.
- Evidence: prior source at `kriya/tools/containment_oci.py::_select_image_and_cache_mount` and the removed residual-limitation block in `PolymorphicValidator.build_containment_profile_and_backend`.

## Implementation summary
Contained verification now resolves one versioned profile before subprocess creation. The OCI backend either proves that the selected image content contains the requested runtime/build tool or raises a typed containment setup failure. Successful finite and managed execution results include the exact image content digest, declared runtime/build tool, and observed versions. Generic containment callers without a project toolchain retain the legacy command-based image selection behavior.

## Tests run by coding agent
| Command | Passed | Failed | Skipped | Duration | Notes |
|---|---:|---:|---:|---|---|
| `.newvenv/bin/python -m pytest -q tests/test_prd011_toolchain_identity.py tests/test_polymorphic_validation.py` | 90 | 0 | 0 | 6.59s | Resolver, fail-closed behavior, existing validator regression |
| `.newvenv/bin/python -m pytest -q tests/test_containment.py tests/test_process_controller.py tests/test_process_controller_containment.py tests/test_process_profile.py tests/test_dependency_execution.py tests/test_prd011_toolchain_identity.py` | 48 | 0 | 9 | 4.88s | Core containment/process/dependency suites; skips are existing platform guards |
| `.newvenv/bin/python -m pytest -q tests/test_containment_oci.py tests/test_containment_oci_registry_scoped_unit.py tests/test_service_runtime_oci.py tests/test_validate_oci.py` | 12 | 0 | 40 | 0.90s | Docker-dependent cases skipped because the coding sandbox cannot reach Docker |
| `.newvenv/bin/python -m pytest -q tests/test_prd011_toolchain_identity_oci.py` | 0 | 0 | 2 | 0.23s | New real-Docker runtime/digest tests; Docker unavailable in coding sandbox |
| `.newvenv/bin/python -m pytest -q tests/test_sec002_fail_closed_evidence.py tests/test_prd011_toolchain_identity.py` | 24 | 0 | 0 | 0.53s | Updated the stale SEC-002 Python test to require fail-closed contained compilation |

The complete `tests/test_service_runtime.py` suite was also attempted. Its non-network cases passed, while 19 cases could not bind a loopback socket in the coding sandbox (`PermissionError: Operation not permitted`). This was an environment restriction, so the independent verifier must rerun the suite in the target workspace.

The first user full-suite run reached `4759 passed` with one failure in the older SEC-002 regression that asserted Python syntax compilation never uses containment. PRD-011 deliberately changes that contract. The follow-up replaces the stale assertion with proof that a Python containment setup failure propagates and can never fall back to a host-side PASS.

## Static/lint/architecture checks
- `.newvenv/bin/python -m ruff check` on all changed production/test files: PASS.
- `python3 -m py_compile` on all changed production modules: PASS.
- `git diff --check`: PASS after the whitespace-only follow-up commit.

## Live test additions
- Required by instruction: YES
- Test file/case: `tests/test_live_prd011_toolchain_parity.py::test_live_java17_and_python_projects_record_exact_container_toolchains`
- Environment prerequisites: reachable Docker daemon; ability to pull `maven:3.9-eclipse-temurin-17` and `python:3.12-slim` if absent.
- Exact command for Live-Model Verification Agent:

```bash
set -o pipefail
mkdir -p handover/evidence/PRD-011/user-live
KRIYA_PRD011_EVIDENCE_DIR=handover/evidence/PRD-011/user-live \
.newvenv/bin/python -m pytest -m live_model -ra -s \
  tests/test_live_prd011_toolchain_parity.py \
  2>&1 | tee handover/evidence/PRD-011/user-live/toolchain-parity.log
```

- Expected invariant/evidence: Java 17 compiles/runs only in an attested JDK 17 image; Python 3.12 compiles/runs only in an attested CPython 3.12 image; `toolchain-parity.json` records matching compile/runtime image digests and observed versions. No LLM endpoint is used by this runtime-boundary live test.

## Known limitations / residual risks
- Production profiles intentionally support JDK 17/21 and Python 3.10/3.11/3.12 only; unsupported or patch-exact requirements fail closed.
- An image tag may move between runs, but every accepted result records and verifies its immutable local content digest. Pinning an operator registry reference by digest is outside this task.
- Generic OCI/MCP profiles without a project `ToolchainIdentity` retain existing command-based image selection for backward compatibility.

## Decisions recorded
- `.eie/DECISIONS.md` entries: none; file is absent.

## Evidence artifacts
- Coding test output is summarized above.
- Live verification will write `handover/evidence/PRD-011/user-live/toolchain-parity.json` and `toolchain-parity.log`.

## Verification-agent handoff
The independent Pytest verifier must run the full project suite and specifically rerun the validate, containment, service-runtime, and dependency-execution suites with Docker and loopback sockets available. After pytest verification, the live verifier must run the exact command above and confirm both project runs, observed runtime versions, and image digests before PRD-011 can advance to `VERIFIED`.
