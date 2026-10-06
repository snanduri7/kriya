# LR-R1 post-P3 bounded live validation: task selection (recorded before any model call)

**Code under test:** cumulative lineage tip `7e4de32` (FS-1A/B → C0 → P2 → P3-A → P3-B → P3-C). Fresh clone
`~/kriya-m1-live/src-p3c` (branch `validation/post-p3` at `7e4de3288f…`; `git status --short` empty), installed
non-editable into `venv-p3c`; `kriya version --json`: commit `7e4de3288fa9…`, `dirty: false`, provenance embedded.
**Configuration:** the unchanged v5 production operator config; each workspace config differs only in
`paths.skills/state/memory` (diffed). Owner SEC-009 approvals for all 7 workspaces verified CURRENT.
**Rules:** 7 tasks, each run once in the order below. No rerun, no goal rewrite after execution starts, no gate,
config or model change, no fixing during validation. Stop at once on wrong candidate + SUCCESS/COMMIT or an
M1/P1/P4/P5/FS-1A/B/C0 regression.

## Admission (no model; `admission/*.json`; harness `scripts/p3v_admission.py`)

Full regression at the exact base, run the way Kriya's brownfield baseline capture runs it
(`PolymorphicValidator(...).run_tests()` under the task's production config: contained, offline Maven / venv);
admitted only with failed = 0 and errors = 0. All workspaces were fresh clones, restored pristine afterwards.

| Task | Repository @ base | Result | Gate |
|---|---|---|---|
| A1 | freezegun @ 92d61b3f5c | 143 passed, 6 skipped | GREEN |
| A2 | freezegun @ 92d61b3f5c (separate workspace) | 143 passed, 6 skipped | GREEN |
| A3 | apache/commons-lang @ 4ee346e59e | 22,042 run, 0 failures, 0 errors, 19 skipped | GREEN |
| A4 | apache/commons-cli @ d95484f57d | 994 run, 0 failures, 0 errors, 61 skipped | GREEN |
| A5 | spring-framework-petclinic @ 09351b3ee0 | 75 run, 0 failures, 0 errors | GREEN |
| B1 | apache/commons-lang @ 4ee346e59e (separate workspace) | 22,042 run, 0/0, 19 skipped | GREEN |
| B2 | Kozea/cssselect2 @ dc2690c6b4 | 446 passed, 9 xfailed | GREEN |

## C0 preconditions, Cohort A (no model; `c0-precheck/*.json`; harness `scripts/p3v_c0_precheck.py`)

With Kriya's own FS-1C0 code at the exact base: the named oracle exists at the base revision; run on an export of the
base under the production config it is a COMPLETE structured report whose every case passed (inventory captured);
the goal's change target (the mutation-scope authorized path) is not on the oracle's trust surface.

| Task | Named oracle | Runner | Cases (all passed) | Change target | On surface? | Precondition |
|---|---|---|---|---|---|---|
| A1 | tests/test_operations.py | pytest | 15 | freezegun/api.py | no | MET |
| A2 | tests/test_operations.py | pytest | 15 | freezegun/api.py | no | MET |
| A3 | NumberUtilsTest | maven | 271 | NumberUtils.java | no | MET |
| A4 | CommandLineTest | maven | 138 | CommandLine.java | no | MET |
| A5 | PetTypeFormatterTests | maven | 3 | PetTypeFormatter.java | no | MET |

Requirement derivation (Kriya's own `derive_requirements` / `named_existing_tests` / `mutation_path_roles`): every
Cohort A goal yields REQ-1 (the change, naming its oracle → C0) and REQ-2 "Do not modify any other file." (mutation
scope, authorized = exactly the change target, the oracle a reference). Cohort B: B1 names CharUtilsTest, which the
goal itself asks the candidate to change (named-test closure refused: written by the candidate); B2 names a test file
that does not exist at base. Neither has an independent closure.

**Interpretation (stated before running).** A C0 oracle must pass at the base while the feature is absent, so a C0
closure proves the named test still passes on the candidate - not that the new behaviour is right. Feature
correctness of every Kriya SUCCESS is therefore decided by the mandatory independent post-run inspection
(goal, applied diff, oracle, executed tests, closure provenance), never by Kriya's verdict.

Features confirmed absent at base (source search): `_parse_time_to_freeze` rejects numbers (dateutil `parser.parse`),
`_parse_tz_offset` has no string branch, `NumberUtils.clamp`, `CommandLine.hasAnyOption`,
`CharUtils.isAsciiHexDigit`, `Matcher.selector_count` all absent; `PetTypeFormatter.parse` compares with `equals`.
None is a no-op. None was chosen to trigger P2/P3 (the R2 P2/P3 files ArrayFill, CharSetUtils, cssselect2/tree.py are
avoided).

## Selected tasks (run order)

| ID | Cohort | Language / framework | Workspace | Expected closure |
|---|---|---|---|---|
| A1 | A | Python | p3v-a1-py-freezegun | C0 (test_operations.py) + mutation scope |
| A2 | A | Python | p3v-a2-py-freezegun | C0 (test_operations.py) + mutation scope |
| A3 | A | Java | p3v-a3-java-lang | C0 (NumberUtilsTest) + mutation scope |
| A4 | A | Java | p3v-a4-java-cli | C0 (CommandLineTest) + mutation scope |
| A5 | A | Spring (Spring MVC Formatter, Spring XML app) | p3v-a5-spring-xml | C0 (PetTypeFormatterTests) + mutation scope |
| B1 | B | Java | p3v-b1-java-lang | none → expected UNVERIFIED, no commit |
| B2 | B | Python | p3v-b2-py-cssselect2 | none → expected UNVERIFIED, no commit |

Goals verbatim: `goals/*.txt`.

## Candidates replaced before any model call (evidence in `replaced/`)

- **inflect @ 262a247 (first A1, first B2): no C0 oracle possible.** MEASURED in the real workspace and on the base
  export: any inflect test file run alone fails `ModuleNotFoundError: No module named 'inflect'` (pytest
  `--import-mode importlib`, no `tests/__init__.py`, and Kriya's pytest launcher removes the cwd from `sys.path`);
  it imports only when `inflect/__init__.py` is also a target (doctest collection). Pre-existing Kriya targeted-run
  behaviour for this layout, not C0-specific; recorded as an environment/tooling finding, not fixed. B2 also needs a
  new test file to import the package, hence moved to cssselect2 (`tests/__init__.py`, prepend import mode).
- **cssselect2 @ dc2690c (first A2): its only test file has 9 xfail cases**, reported `skipped` in JUnit; C0 requires
  every expected case passed, so that oracle can never close (correct C0 behaviour for a not-known-good oracle).
- Screened and not admitted: Python-Markdown (no runtime dependencies declared → no venv with pytest), bleach
  (dependencies only in setup.py), arrow (pytest config requires pytest-cov), httpx @ b5addb6 (RED in R2: 4 failed).
