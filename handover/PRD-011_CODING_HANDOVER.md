# PRD-011 Coding Agent Handover

## Status
READY_FOR_PYTEST_VERIFICATION (reopen closure below; supersedes the original record)

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
.venv/bin/pytest -m live_model -ra -s \
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


## Reopen closure (2026-09-25)

`PRD-001_011_INDEPENDENT_REVIEW.md` found the original implementation BLOCKING. Each finding is closed as follows.

| Review finding | Closure | Commit |
|---|---|---|
| An unconditional `import tomllib` breaks `import kriya.cli` on Python 3.10 | `kriya/core/tomlcompat.py`: stdlib, else the declared `tomli; python_version < '3.11'`. Guard tests: no direct import, no 3.11-only syntax or stdlib names, and a subprocess import with `tomllib` blocked | 0b4913c |
| A goal-stated JDK is set after construction, so it is ignored and the mismatch refusal never fires | `java_home_override` is a property whose setter re-resolves the identity. Tests follow the production shape (construct, then assign). An end-to-end run stops as `containment_setup_failed` with nothing executed | b6bdbe4, b2bd88f |
| Identity and digest are not persisted (callers rebuild `{success, output}`) | `toolchain_evidence(result)` is added to every gate outcome built from a validator or process result. An end-to-end test reads the digest back from `traces.db` | 1996255 |
| Common Python specs fail closed; Java 8/11 and Python 3.13+ are refused | Java 8/11/17/21 (Maven and Gradle), Python 3.10-3.14, a PEP 440 subset, and same-minor patch bounds checked at attestation | b2bd88f |
| The floating tag runs after the digest is attested; Gradle is never attested | Probes and all runs (finite, managed, acquisition) use the image ID. Gradle major 8 is attested | b2bd88f |
| (owed to PRD-008 S4c) The toolchain fingerprint is UNAVAILABLE | Bound under contained execution for direct resume and milestone `VERIFIED_NO_CHANGE` | e1dfe21, 12ce81d |

**The contained toolchain fingerprint**
- **How it is computed:** a digest of the declared versioned profile, including a goal-stated JDK, plus the local image content digest (`docker image inspect`, never pull or run).
- **When it is UNAVAILABLE**, which means UNVERIFIED and never a match: host mode, an unknown stack, an unresolvable requirement, or an image that is not present locally.
- **Behaviour change:** a contained run can now reuse candidate gate outcomes, and a milestone no-change proof, when the image and requirement are unchanged. Host mode behaves exactly as before.

**Residual (disclosed; not a blocker)**
- **Scope of the fingerprint:** it covers the toolchain the base workspace declares. If a candidate edits its build file's Java version, its gates ran on the image resolved from the edited worktree, which the fingerprint does not describe.
- **Why this is bounded:** terminal full regression always re-runs on the resumed path before anything is applied, with a fresh attestation. Resume reuses evidence but never skips that gate.
- **Out of scope:** Java 25 and Python 3.9 are refused. Pinning an operator registry reference by digest is still outside this task; the local content digest is pinned.

**Test changes (none weaken a safety assertion)**
- `test_unsupported_java_requirement_fails_closed` and `test_an_unsupported_declared_toolchain_blocks` now use Java 7, because 11 is supported.
- `test_patch_specific_python_requirement_is_carried_to_attestation` replaces the fail-closed-on-patch test. The patch bound is now enforced against the observed runtime; the mismatch case is tested.
- The prepare test now requires the image ID in the command and forbids the tag.
- The real-Docker test accepts the full observed Python `X.Y.Z`.
- The S4c `toolchain_bound` fixture accepts the new arguments.
- The PRD-008 host-mode assertion now names the host basis.

**Evidence** (plain runner, no pytest; Docker available locally):

| Suite | Result |
|---|---|
| `test_prd011_toolchain_identity` | 42/0 |
| `test_prd011_toolchain_identity_oci` (real Docker: JDK 17, JDK 8, Gradle/JDK 17, Python 3.12) | 4/0 |
| `test_prd011_toolchain_evidence` | 6/0 |
| `test_prd011_toolchain_fingerprint` | 9/0 |
| `test_prd011_python310_compat` | 4/0 |
| `test_prd008_s4c_milestone_resume` | 47/0 |
| `test_prd008_resume_fingerprints` | 97/1 |
| `test_prd008_s4b_milestone_completion` | 28/0 |
| `test_milestones` | 68/0 |
| `test_production_doctor` | 55/0 |
| `test_containment_oci` (real) | 27/0 |
| `test_service_runtime_oci` | 5/0 |
| `test_validate_oci` | 7/0 |
| `test_sec005_shell_acquisition_network` | 49/0 |
| `test_tool003_p2_oci_enforcement` | 19/0 |
| `test_polymorphic_validation` | 81/0 |
| `test_sec002_fail_closed_evidence` | 15/0 |
| `test_service_runtime` | 45/0 |
| `test_workflow` | 862/6 |
| `test_workflow_controller_enforce` | 219/53 |

The `test_prd008_resume_fingerprints` failure (`tmp_path_factory`), and every `test_workflow` and
`test_workflow_controller_enforce` failure (caplog, the linux JDK heuristic), are plain-runner limitations that fail
identically at HEAD. Mutation checks were each caught:
- the constraint check removed (DID NOT RAISE);
- the tag run instead of the ID;
- the old Java regex;
- a reverted `tomllib` import (2 tests);
- no setter re-resolve (3 tests);
- gate evidence dropped (the trace test);
- the milestone config not threaded (2 tests).

**First pytest run:** the real-Docker test pulls `maven:3.9-eclipse-temurin-8` and `gradle:8-jdk17` if they are
absent.


## Final-review correction: toolchain conflict semantics (2026-09-25)

The batch 2 final review corrected one PRD-011 rule. A repository-declared version that differs from the goal is not
a failure when the run is authorized to change the declaration.

**Model** (`kriya/tools/toolchain_identity.py::resolve_toolchain_selection`)
- The **baseline** is the pre-mutation workspace declaration (the validator's `original_workspace_path`).
- The **target** is the candidate's declaration. Under authority, it can also be a goal-stated JDK that differs from
  it; the migration may not have landed yet.
- The **candidate verification toolchain** is the target. Its attested identity carries
  `selection = {baseline, target, basis, declaration_mutable}`.
- **Bases:**
  - `repository_declaration`;
  - `authorized_declaration_change`;
  - `authorized_goal_requirement`;
  - `goal_requirement_undeclared` (nothing declares a version; unchanged behaviour).
- **Conflict:** without authority, either of these is `ToolchainRequirementConflictError` (reason code
  `TOOLCHAIN_REQUIREMENT_CONFLICT`, a ContainmentSetupError):
  - a declaration change;
  - a goal requirement that contradicts the declaration.

  It is raised before any candidate command runs. The failure keeps the reason code in
  `Failure.diagnostics`, and the run's `environment_failure` starts
  `CONTAINMENT_SETUP_FAILED: TOOLCHAIN_REQUIREMENT_CONFLICT:`.
- **Python:** the same model. A `requires-python`/`.python-version` edit that changes the resolved
  interpreter or constraint is a migration; one that resolves to the same toolchain is not. Python has no goal-stated
  requirement channel.

**Authority** (`kriya/workflow/toolchain.py::toolchain_declaration_mutable`)
- The authority is structured and reused, never inferred from goal wording. It is the run's write scope (the value
  AuthorizedFileWriter enforces) plus the approved plan's planned files.
- An unrestricted direct run is mutable.
- An allowlist or planned run is mutable only if the root declaration file is in its allowed files, in some subtask's
  planned files, or in the owner's authorized files (owner recovery).
- DENY_ALL scope doesn't count; only the plan does.
- **Wired into:** the attempt validators, terminal regression, owner-recovery self-correction, and the resume
  fingerprint.
- No new keyword detection was added. The goal-stated JDK is the pre-existing host-JDK mechanism. It can only confirm
  the declaration, select the target under authority, or fail closed.

**Resume**
- `toolchain_fingerprint(..., candidate_files, declaration_mutable)` overlays a candidate checkpoint's own
  declaration files on the workspace. The fingerprint therefore describes the target that the candidate's gates ran
  under: selection plus the target image digest.
- Save and check use the same checkpointed `final_files`.
- A migration's gates are reused only while the target image and selection are unchanged. A change to the baseline
  image is irrelevant. An unauthorized selection is UNAVAILABLE and never a match.
- This closes the earlier disclosed residual, where the fingerprint described only the base workspace.

**Tests** (`tests/test_prd011_toolchain_migration.py`, 11 cases)
- authority derivation;
- an ordinary task keeps the repository version;
- an authorized 17 → 21 migration, both by declaration and by goal before the edit lands;
- an end-to-end direct run whose contained gates all run in the Java 21 image with a recorded 17 → 21 selection;
- the typed conflict, from a goal requirement and from a declaration change;
- an end-to-end scoped run that stops with `TOOLCHAIN_REQUIREMENT_CONFLICT` and nothing executed;
- Python migration and conflict, plus a spec edit that keeps the toolchain;
- migration resume: the target image matches, the baseline image is irrelevant, a target image change is caught,
  and an unauthorized selection is unavailable.

Mutation checks, each caught:
- always authorized;
- a goal never authorized;
- the fingerprint ignoring the candidate;
- a direct run not authorized.

The old end-to-end test `test_goal_stated_jdk_contradicting_the_pom_stops_the_run_before_any_execution` (direct
run) encoded the superseded rule. A direct run may change `pom.xml`, so it is now an authorized migration. The typed
stop is proven by the scoped-run test instead.

Unchanged:
- supported-version validation;
- the observed patch check;
- execution by image ID;
- Gradle/JDK/Python attestation;
- evidence persistence;
- containment fingerprinting.
