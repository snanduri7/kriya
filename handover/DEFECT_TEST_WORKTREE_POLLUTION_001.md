# TEST-WORKTREE-POLLUTION-001: the test suite wrote npm artifacts into the repository root

## Status

**FIXED** (2026-10-07) on `fix/backend-readiness-harness` from main 8a01987. Severity P3 (test-suite side effect; no
product behaviour changed). Registry row `TEST-WORKTREE-POLLUTION-001` (CLOSED). Pre-fix evidence (the original
artifacts, their birth times and the single-test reproductions) is preserved outside Git in
`~/kriya-m1-live/backend-readiness-001/pollution/`.

## Observation (MEASURED)

After every full suite run the repository root held `package.json` (`{"dependencies": {"left-pad": "^1.3.0"}}`),
`package-lock.json` (resolved from registry.npmjs.org) and `node_modules/left-pad`. In Kriya-main-demo the three were
born 2026-10-07 16:29:09 IST, during the full suite at 518f5ed; `package-lock.json` was rewritten at 17:22, by a later
targeted run. The suite's venv also holds `requests 2.34.2`, declared by no requirement file and required by no
installed package.

## Producer (TRACED)

`plugins/core_tools/__init__.py::ShellTool._run` always spawns `["/bin/sh", "-c", <command>]` through
`ProcessController.run_async` with `cwd=os.getcwd()`. The profile-selection tests captured the `ContainmentProfile`
through `kriya.tools.containment.DummyContainmentBackend`, whose `prepare()` only records the profile and returns an
environment built by `build_restricted_env`, which always keeps `PATH`. Nothing is contained: the command runs on the
host, in the pytest cwd (the repository root), with the venv's `bin` first on PATH (`tests/conftest.py::
_interpreter_tools_on_path`). Four non-Docker tests pass `npm install left-pad` to such a tool:
`tests/test_sec005_shell_acquisition_network.py` (`test_unmapped_package_manager_denied_network_when_contained`,
`test_compound_command_narrows_whole_process_network`, `test_execution_policy_mode_does_not_influence_network_authority`)
and `tests/test_tool001_autonomous_tool_execution.py::test_scenario_g_shelltool_unmapped_manager_denied_when_contained`,
whose comment assumed npm was missing on the host. The same tests also run `pip install requests`,
`python3 -m pip install requests`, `gem install rails`, `bundle install` and `mvn clean install` on the host;
`tests/test_prd012_egress.py` runs `curl <attacker>` the same way. `tests/test_policy_package_supply_chain.py` only
calls the pure classifier (control: writes nothing).

## Root cause (CONFIRMED)

Discriminating check (`pollution/discriminate/`): with a clean root, the TOOL-001 test alone recreates all three
artifacts (`run1_root_after.txt`); the SEC-005 compound-command test alone recreates them (`run2_root_after.txt`);
the supply-chain file leaves the root clean (`run3_root_after.txt`). `requests` in the venv is INFERRED to be the same
mechanism (`pip install requests` with the venv first on PATH); it was not uninstalled to re-measure.

## Fix

- `tests/_strict_doubles.py::ProfileCapturingBackend`: records every prepared profile and returns a
  `PreparedContainment` whose `command_prefix` is `/usr/bin/true` - the backend contract's own mechanism (the OCI
  backend prepends `docker run ...` the same way) - so `ProcessController` spawns a no-op. The caller sees exit 0 and
  empty output; nothing it asked for executes. The SEC-005, TOOL-001 and PRD-012 profile tests use it; every assertion
  they make is about the profile, which is unchanged.
- `tests/conftest.py::_no_repository_root_package_pollution` (autouse): fails the test during which `package.json`,
  `package-lock.json` or `node_modules` appears in the repository root (nothing is deleted; under xdist the message says
  a concurrent worker can be the writer).
- `tests/test_worktree_pollution_001.py`: the double executes nothing (a `touch` never lands, the profile is still
  captured), the detector reports only what appeared, and a structural guard rejects any ShellTool profile test that
  captures through `DummyContainmentBackend` again.
- `scripts/run_full_suite.py` post-checks the repository root after the run (exit 3 on a new artifact).

## Verification

- Prediction: after the fix the four tests and the Docker-gated SEC-005/TOOL-001 tests leave the root clean. Measured:
  targeted run of the pollution, harness, SEC-005, TOOL-001, PRD-012, strict-doubles and registry files
  (161 passed, Docker up) -> `git status --short` shows no artifact.
- Mutation 1 (`pollution/mutation/mutant_tripwire.txt`): `DummyContainmentBackend` restored in SEC-005's
  `_capturing_tool` -> the compound-command test ERRORs in teardown with the TEST-WORKTREE-POLLUTION-001 message and
  the root is polluted again (`mutant_root_after.txt`).
- Mutation 2 (`pollution/mutation/mutant_structural_guard.txt`): `DummyContainmentBackend` restored in TOOL-001's
  scenario G -> `test_shelltool_profile_tests_never_capture_through_an_executing_backend` fails naming the line.
- Full suite via the canonical harness at the branch tip: see the batch report (`SECTION C`).
