# FS-1C2 B2-a: operator executable acceptance evidence (Python, flat layouts)

**Branch:** `feature/lr-r1-b2a` (from `feature/lr-r1-fs1c1` `dab3a59`). **Implementation:** `7a39256` + fix `b8809cd`
(certified executable revision: `b8809cd`).
**Owner authorization:** FS-1C1 certified; A1 live sentinel authorized and run (PASS, below); B2-a authorized;
B2-b / B2-c / B3 not authorized (deferred). No live model run after B2-a. Nothing pushed or merged.
**Labels:** MEASURED, TRACED, INFERRED.

## 0. A1 FS-1C1 live sentinel (run before B2-a)

`handover/evidence/fs1c1/a1-live-sentinel/SENTINEL.md` (`cac848f`, lint fix `dab3a59`): one run of the preserved A1
goal on a fresh non-editable install of `dfe22f8`, fresh freezegun workspace at `92d61b3`, v5 production config
(paths only), SEC-009 CURRENT. MEASURED: REQ-2 closed by MUTATION_SCOPE; REQ-1 regression claim closed by C0
(`ORACLE_PASSED`), behaviour claim UNVERIFIED (`REQUIREMENT_BEHAVIOR_UNVERIFIED`); terminal `REQUIREMENTS_UNRESOLVED`;
`NOT_COMMITTED`; workspace HEAD = base, no tracked change. The model again produced the helper-only candidate
(independent check: `freeze_time(0)` / `freeze_time(86400.5)` raise TypeError). **SENTINEL = PASS.**

## 1. What B2-a adds (TRACED)

| Piece | Where |
|---|---|
| Input, bound before generation | `kriya generate --acceptance <file>` -> `cli._bind_acceptance` -> `acceptance_oracle.load_acceptance` (before `LLMClient` is built); `WorkflowEngine.acceptance` |
| Artifact rules | every `test*` function / `Test*.test*` method carries `@pytest.mark.kriya_requirement("REQ-n", ...)` (string literals); id must be in the derived set; a mutation-scope requirement is refused; `pytest.mark.*` other than `kriya_requirement`/`parametrize` (skip, xfail, usefixtures...), module `pytestmark`, class-level markers, async tests, duplicate names, relative imports refused (`ACCEPTANCE_ARTIFACT_INVALID` / `_UNKNOWN_REQUIREMENT` / `_REQUIREMENT_NOT_BEHAVIORAL`) |
| Storage / immutability | bytes stored at `<state>/acceptance/<sha256>.py` (outside the workspace); re-verified against the digest before each run (`read_stored`); staged per run into the candidate's `.kriya/acceptance-runs/<uuid>/` (trusted control path: `AuthorizedFileWriter` refuses it), re-hashed after the run, then removed |
| Binding | requirement-set digest (a mismatch closes nothing: `ACCEPTANCE_REQUIREMENT_SET_MISMATCH`); the verdict's candidate `evidence_id`; acceptance digest; runner contract version + `RUNNER_CONTRACT_DIGEST` (runner + ini source); FS-1A report gate id + file digests; candidate digest (changed paths + imported candidate modules, before = after) |
| Resume | `generation_resume_fingerprints(acceptance_digest=)` joins the verification inputs only when present (no-acceptance fingerprints byte-identical); the ledger: the latest judgment of a claim on a candidate decides, and a run with another or no artifact supersedes an earlier acceptance judgment (`ACCEPTANCE_SUPERSEDED`) |
| Execution boundary | `PolymorphicValidator.run_acceptance` (new gate `acceptance`; `run_tests` untouched): project interpreter (`_resolve_python_interpreter`, containment via `_run_cmd_with_timeout`), `python -I -B <Kriya runner>`; the runner clears `PYTEST_ADDOPTS`/`PYTEST_PLUGINS`, sets `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, removes cwd/stage from `sys.path` and appends the project root, runs `pytest.main` with `-c <Kriya ini> --rootdir <stage> --noconftest -p no:cacheprovider --import-mode=importlib`, and records per-phase outcomes, exception types, traceback files, where each imported candidate module was loaded from, and every registered plugin |
| Judgment | `judge_acceptance` (below) -> `record_requirement_claim(BEHAVIOR, method="acceptance_oracle", status=SATISFIED/VIOLATED/INDETERMINATE, required_claims=...)` |
| Sites | `workflow.close_requirements_with_acceptance_tests`, called before the named-test closure at the direct/milestone pre-apply boundary and in enforce's `TerminalGateService._requirement_gap` (`TerminalGateRequest.acceptance`, from `bound_acceptance(engine)`) |

### Judgment (per requirement)

- **PASSED** - complete FS-1A report of runner `pytest`; pytest exit 0/1/5; runner observations present and every
  JUnit case consistent with its observed phases; no plugin outside `_pytest.*` and the runner; every imported candidate
  module loaded from the candidate root; artifact and candidate unchanged during the run; every expected case
  executed and passed.
- **VIOLATED** - same integrity, and an expected case's call phase failed with a contradiction: the traceback passes
  through a candidate source file (the candidate raised it), or an `AssertionError`/`pytest.fail` after candidate code
  was loaded from the root. Not for `ImportError`/`ModuleNotFoundError`.
- **INDETERMINATE** (claim stays open, never VIOLATED) - refusal, integrity problem, foreign plugin, missing/
  malformed/inconsistent evidence, timeout, interrupted session, case not executed, setup/collection error, failure
  not observed as a contradiction (acceptance-code error, skip, import error, candidate missing dependency).

### Claims a statement makes

`required_claims` = `requirement_claims(text, named tests)` where the named tests are resolved against every test file
that existed before the run (real workspace + the run base, `_reference_test_files`) or exists in the candidate. A
candidate deleting the named regression test therefore never turns "implement X, keeping T passing" into a pure
behaviour statement (found in my own review before commit: judged on candidate files alone, acceptance would have
closed it; regression test + mutant `claims-typed-on-candidate-files-only`). Unknown (in-place candidate, no readable
base) -> both claims required.

### Supported layouts (MEASURED on this stage's tests)

Flat package (`pkg/__init__.py`) or flat module (`pkg.py`) at the project root, with or without a `tests/` package.
Refused before anything runs, no fallback (`ACCEPTANCE_LAYOUT_UNSUPPORTED`): `src/` layout, namespace package,
test-side import (`tests`/`test`/`testing`/`conftest`/`test_*`), no candidate import. Non-Python stack:
`ACCEPTANCE_RUNNER_UNSUPPORTED` (no build runs).

## 2. A1 B2 reproducer (deterministic, real freezegun code)

`tests/fixtures/b2a/freezegun-92d61b3/` = freezegun at the A1 base, byte-identical (api.py digest = the live
`candidate.change` before-digest `f0d67017...`); `helper_only` = base + the live diff, digest = the live after-digest
`10e78782...` (asserted); `correct` = also accepts int/float in `freeze_time`. python-dateutil is not in Kriya's test
environment: a three-file stand-in provides what freezegun imports. Acceptance = `freeze_time(0)` and
`freeze_time(86400.5)` cases (REQ-1).

| Candidate | acceptance | C0 (live judgment) | scope | REQ-1 | REQ-2 | production blocking |
|---|---|---|---|---|---|---|
| helper-only (live) | VIOLATED (both cases failed: TypeError raised in `freezegun/api.py`) | - (VIOLATED is final) | closed | VIOLATED | CLOSED_BY_EVIDENCE | [REQ-1] |
| correct | PASSED | ORACLE_PASSED | closed | CLOSED_BY_EVIDENCE (BEHAVIOR + REGRESSION_PRESERVATION) | CLOSED_BY_EVIDENCE | [] |
| correct, no regression claim | PASSED | not run | - | UNVERIFIED | - | - |

## 3. Required tests (owner list) -> `tests/test_b2a_acceptance_oracle.py`

1 bound before generation (artifact digest/store/requirement-set; CLI binds it on the engine the run executes) ·
2 unknown id refused (unit + CLI: exit 1, no dispatch, no LLM client) · 3 candidate cannot mutate it (control path
denied, stored copy outside the workspace, in-run rewrite -> `ACCEPTANCE_ARTIFACT_CHANGED`, stored copy changed ->
refused) · 4 flat layouts import (package, module, package + tests package; no `__pycache__`/`.pytest_cache` written)
· 5 candidate conftest (force-pass hook; raising conftest) · 6 candidate pytest config (pytest.ini, pyproject,
setup.cfg, tox.ini addopts/python_functions) · 7 plugins (`-p` from candidate config; only `_pytest.*` registered,
measured with xdist/asyncio/anyio/testmon installed; a foreign plugin -> INDETERMINATE) · 8 pass -> closure (pure
behaviour alone; compound only with C0) · 9 assertion -> VIOLATED · 10 candidate exception -> VIOLATED (and A1) ·
11 missing case / collection error / skip / setup error / acceptance-code error / candidate missing dependency ->
never VIOLATED · 12 missing/malformed/inconsistent report or observations, timeout, environment failure ->
INDETERMINATE · 13 unsupported layouts and non-Python -> typed refusal, neither `run_tests` nor the runner called ·
14 stale report from another invocation never read; evidence for another candidate never closes · 15 model
SATISFIED never replaces missing acceptance evidence · 16 candidate-written tests never close behaviour · 17 resume
fingerprints bind the exact digest · 18 changed / withdrawn artifact supersedes; failed or raising re-run revokes an
earlier pass · 19 direct path (`run_generation_workflow`, real runner: wrong behaviour VIOLATED and not applied,
correct closed and applied) · 20 enforce path (`WorkflowController.execute(..., "enforce")`, terminal gate) ·
21 milestone path (real CLI `generate --from-milestones --acceptance`, ids from the plan's original goal; the
integration unit judges it).

## 4. Mutation (MEASURED)

`evidence/b2a/b2a_mutation_campaign.py` on `7a39256` (one exact replacement per mutant, fresh clone each, KILLED iff
the B2-a + FS-1C1 + PRD-020 lineage/milestone + FS-1 reproducer tests fail): **25 run, 24 killed, 1 survived**
(`b2a_mutation_run1_7a39256.txt`). The survivor, `candidate-conftest-trusted` (remove `--noconftest`), is EQUIVALENT,
measured (`noconftest_equivalence.md`): the rootdir/confcutdir is Kriya's own staging directory, so no candidate
conftest is in reach either way, and a loaded conftest would be a foreign plugin (killed mutant). Kept as defense in
depth.

## 4a. Certification on `b8809cd` (MEASURED)

| Gate | Result |
|---|---|
| focused B2-a | 80 tests (`tests/test_b2a_acceptance_oracle.py`), all pass |
| mutation run 2 (`b8809cd`) | 25 exercised, 24 killed, 1 equivalent/non-semantic survivor (`--noconftest`, measured), 0 meaningful survivors - not 25/25 (`b2a_mutation_run2_b8809cd.txt`) |
| ruff / pylint | 0 findings / exit 0 |
| full suite (`full_suite_b8809cd.txt`, absolute PYTHONPATH) | **8988 passed, 0 failed, 0 errors** |

Full suite run 1 on `7a39256` (`full_suite_7a39256.txt`, kept): 8984 passed, 3 failed.
- `test_tool001_autonomous_tool_execution::test_no_direct_subprocess_from_tool_subtask_orchestration` - **my defect**:
  `_reference_test_files` caught `subprocess.CalledProcessError` in `workflow.py`, which the TOOL-001 structural test
  forbids. Fixed in `b8809cd` (broad catch -> unknown = both claims; normal path now tested).
- `test_polymorphic_validation::test_run_app_sequence_multi_package_test_file_target_hits_module_not_found` -
  **measurement artifact**: I ran that suite with a relative `PYTHONPATH=.`; the test spawns `python tests/x.py` with
  cwd=`tmp_path`, so `.` put `tmp_path` on `sys.path` and the import it expects to fail succeeded. Passes with an
  absolute PYTHONPATH on the base `dab3a59` and on a clean clone of `7a39256` (measured); passes in run 2.
- `test_retry_inference_identity::test_qualify_covers_the_retry_identity_and_status_reports_it` - passed alone on
  `7a39256` and in run 2; not reproduced (same relative-PYTHONPATH run; cause not established - UNKNOWN).

Non-interference: the full suite covers M1, P1/P4/P5, P2, P3-A/B/C, I-2, FS-1A/B, C0 and FS-1C1 unchanged (all pass).

## 5. Findings and residuals

- **Correction to the B2 design (§4 there), TRACED:** the ordinary pytest launcher does not simply drop the project
  root - it removes the cwd entry and appends `v.workspace_path`. That path is the *host* path; under containment the
  command runs in the container's workdir, where that host path does not exist, so the appended root is useless there
  (INFERRED explanation of the inflect measurement, which ran contained; not re-measured). Not changed (owner: do not
  change ordinary test-gate semantics). B2-a's runner computes the root inside the process (`os.getcwd()`), so it is
  correct in both modes by construction.
- **Contained execution** (MEASURED, `evidence/b2a/contained_acceptance_7a39256.json`, harness
  `contained_acceptance_measurement.py`, no model): the A1 acceptance file on both fixture candidates under real OCI
  containment (`contained_execution_required: true`, backend `oci`, project venv built in the container from
  `requirements.txt` via registry-scoped acquisition): helper-only -> `ACCEPTANCE_VIOLATED` (both cases failed),
  correct -> `ACCEPTANCE_PASSED`; runner root `/kriya/workspace`, freezegun loaded from it, network `denied`, no plugin
  outside `_pytest.*`, no `__pycache__` in the candidate. Not a suite test (needs Docker and registry access); every
  suite test runs host-mode.
- **Residuals (disclosed, unchanged from FS-1C0):** candidate production code imported by an acceptance case runs in
  the same process and could tamper with the runner or its observations in-process; the project venv is built from the
  candidate's declared dependencies (plugins never autoload, but a dependency's `.pth` file runs at start).
- **Not done (not required by the brief):** acceptance cases are not shown to the Planner/Developer; `doctor` has no
  acceptance row; a model VIOLATED verdict still blocks even when acceptance passes (PRD-020 semantics unchanged).
- **Own mistakes, caught before commit:** my first write of the new module overwrote the existing
  `kriya/workflow/acceptance.py` (runtime-acceptance helpers) in the working tree; restored from git before any commit
  and the module named `acceptance_oracle.py`. Evidence-only commit `cac848f` added `.py` files that failed the
  repo-wide ruff gate; fixed in `dab3a59` (same independent-check output re-measured).

## 6. Recorded, not fixed (owner)

- Deferred observation: a deterministic acceptance PASS together with a model-negative VIOLATED verdict is currently
  blocked (the verdict wins). A future negative-model-authority issue, not part of B2-a or the A1 sentinel.
- Cohort B tasks keep running without acceptance files (their purpose: no independent behaviour oracle -> UNVERIFIED,
  no autonomous commit).
