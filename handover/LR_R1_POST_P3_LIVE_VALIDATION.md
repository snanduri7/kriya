# LR-R1 post-P3 bounded live validation (STOPPED at run 1: FALSE SUCCESS)

**Lineage under test:** `7e4de32` (FS-1A/B → C0 `682f2d1` → P2 `daa4c4d` → P3-A `f51acd4` → P3-B `18b4e44` →
P3-C `7e4de32`), fresh clone, clean tracked tree, non-editable install (`kriya version`: commit `7e4de3288f…`,
`dirty: false`). Unchanged v5 production operator config; owner SEC-009 approvals CURRENT for all 7 workspaces.
**Task selection** (committed before any model call, `b759b32`):
`evidence/lr-r1-post-p3/TASK_SELECTION_POST_P3.md` - 7/7 bases GREEN under Kriya's contained regression gate,
5/5 Cohort A C0 preconditions met.
**Labels:** MEASURED, TRACED, INFERRED.

## 1. What happened

Run 1 (A1, freezegun) ended in Kriya **SUCCESS / COMMITTED** with a candidate that does not do what the goal
states. That is the validation-stopping event of the brief. The validation was stopped immediately: runs A2-A5 and
B1-B2 were **not executed**; the A1 workspace and all evidence are preserved unchanged
(`evidence/lr-r1-post-p3/A1-p3v-a1-py-freezegun/`, M1 store `~/kriya-m1-live/state/attempt-evidence/20261006T082942-595796b8`,
workspace `~/kriya-m1-live/ws/p3v-a1-py-freezegun`). Nothing was fixed.

### A1 facts (MEASURED)

| Field | Value |
|---|---|
| task / repository / base | A1 / spulec/freezegun / `92d61b3f5c` |
| baseline gate | GREEN (143 passed, 6 skipped) |
| goal | `_parse_time_to_freeze` accepts an int/float POSIX timestamp "so that `freeze_time(0)` freezes time at 1970-01-01 00:00:00 and `freeze_time(86400.5)` … at 1970-01-02 00:00:00.500000", keeping tests/test_operations.py passing; do not modify any other file |
| expected closure | REQ-1: C0 (tests/test_operations.py); REQ-2: mutation scope |
| units | 3 (s1 change `freezegun/api.py`; s2 run test_operations.py; s3 run the full suite) |
| attempts | 3 (one per unit), all passed first time |
| model calls | 9 (Planner 1 on qwen3.6; localization 3, Developer 1, run verifier 1, spec compliance 2, Reviewer 1 on qwen3-coder) |
| primary / fallback | primary only; no fallback call |
| P2 / P3-A / P3-B / P3-C exercised | no / no / no / no (one Developer request, anchored edit accepted, no refusal of any family) |
| compile / static | pass |
| tests | targeted pass; full suite pass (145 passed, 4 skipped on the applied candidate) |
| requirement outcomes | REQ-1 CLOSED_BY_EVIDENCE (C0 `ORACLE_PASSED`, tests/test_operations.py, 15 cases); REQ-2 CLOSED_BY_EVIDENCE (MUTATION_SCOPE: actual = authorized = `freezegun/api.py`) |
| terminal result | SUCCESS, COMMITTED; candidate applied |
| wall / model / recorder s | 210.8 / 106.9 / 0.226 |
| M1 | VERIFIED |

**Applied diff** (`applied.diff`): a new first branch in `_parse_time_to_freeze`:
`if isinstance(time_to_freeze_str, (int, float)): time_to_freeze = datetime.datetime.fromtimestamp(time_to_freeze_str, tz=datetime.timezone.utc)`.

### Independent false-success check (mandatory; not Kriya) - **FALSE_SUCCESS**

On a temporary copy of the applied workspace (`independent_check.py`, output saved):

```
_parse_time_to_freeze(0)        -> datetime(1970, 1, 1, 0, 0)            (correct)
freeze_time(0)                  -> TypeError                              (goal: freezes at 1970-01-01)
_parse_time_to_freeze(86400.5)  -> datetime(1970, 1, 2, 0, 0, 0, 500000) (correct)
freeze_time(86400.5)            -> TypeError                              (goal: freezes at 1970-01-02 ...5)
```

`freeze_time()` (same file, in scope) type-checks its argument against `acceptable_times` and raises `TypeError`
for an int/float before `_parse_time_to_freeze` is reached (`freezegun/api.py:986-987`). The goal's two stated
examples - its observable requirement - fail. The helper-level change is right; the requirement is not met.

- goal: not met (both stated behaviours fail);
- applied diff: partial (helper only);
- deterministic oracle: tests/test_operations.py - passed at base and on the candidate; it never calls
  `freeze_time` with a number, so it cannot observe the requirement;
- executed tests: no test exercised the new behaviour (no test was written; the goal forbids other files);
- closure provenance: REQ-1 closed by C0 on that oracle; the only positive evidence that the behaviour exists is the
  model's spec-compliance "satisfied" (FS-1B: authorizes nothing by itself).

**Classification: FALSE_SUCCESS.**

## 2. Root cause of the false success (TRACED; INFERRED where marked)

- **C0 worked exactly as implemented and specified** (MEASURED from the log and records): the named oracle was
  unchanged, its trust surface unchanged before and after the run, the base-export inventory (15 cases) all executed
  and passed in a COMPLETE fresh report, the runner succeeded, the closure is bound to its digests.
- **What C0 proves is not what REQ-1 states.** The PRD-020 named-test closure closes the *whole* requirement
  statement whose text names a test (`close_unverified_requirements_with_named_tests` → `named_existing_tests`).
  C0's precondition (owner decision: only known-good oracles, passing at the base where the feature is absent) means
  such an oracle can, by construction, only prove *non-regression*; it can never exercise new behaviour the same
  sentence asks for. So a requirement of the form "implement X, keeping test T passing" is closed by T alone.
- **This was stated before the run** (TASK_SELECTION_POST_P3.md, "Interpretation") and in the pre-run summary to the
  owner: Cohort A's goals put the feature and its oracle in one sentence because, under the current closure
  producers, that is the only way a feature requirement can close deterministically (mutation scope and the
  migration gate close only their own kinds of statement; FS-1B removed model authority). Cohort A as specified
  therefore measures exactly this property, and run 1 hit it.
- **Producer of the wrong code** (INFERRED): the plan scoped s1 to "update `_parse_time_to_freeze`" (the goal's own
  first words); the Developer implemented the named function; nothing in the run checked `freeze_time(<number>)`.
  The spec-compliance model judged the helper and said "satisfied".
- **Not a regression of FS-1A/B, C0, M1** (MEASURED): every one of those mechanisms produced its specified record;
  the false success comes from the scope of what a named pre-existing test closes, a PRD-020 property C0 inherited
  unchanged (C0 narrowed *how* a named test may close, not *what* it closes).

## 3. Observations recorded before the stop

- **Environment / tooling finding (MEASURED, pre-run, not fixed):** for a Python repository using pytest
  `--import-mode importlib` without `tests/__init__.py` (inflect), any test file run alone cannot import the package
  under Kriya's pytest launcher (it removes the cwd from `sys.path`); it works only when the package's own module is
  also a target. This affects every targeted test run for such repositories, not only C0.
- **C0 with xfail cases (MEASURED, pre-run):** a named test file containing xfail cases (reported `skipped`) can
  never close a requirement under C0 (every expected case must pass) - consistent with "known-good oracle only".
- Python candidate screening: Kriya runs a Python suite only when a root `requirements.txt` or `pyproject.toml`
  runtime dependencies exist (Python-Markdown, bleach) and fails on suites whose pytest config needs plugins it does
  not install (arrow: pytest-cov).

## 4. Final report

```text
LINEAGE
7e4de32

COHORT A
5 planned / 1 executed

GENUINE SUCCESS
0/1

FALSE SUCCESS
1  (A1 freezegun: SUCCESS/COMMITTED; freeze_time(0) and freeze_time(86400.5) raise TypeError) - REQUIRED 0: NOT MET

FAILURES
P2: none
P3: none
verification: none (every gate and closure passed as implemented; the false success is semantic - see §2)
other: none

P2
not exercised

P3-A
not exercised

P3-B
not exercised

P3-C
not exercised

COHORT B
2 planned / 0 executed (validation stopped)

EXPECTED UNVERIFIED
n/a (0 executed)

UNEXPECTED SUCCESS
n/a (0 executed)

M1
VERIFIED 1/1

P1
not exercised (one fallback.decision, primary selected; no fallback call)

P4
not exercised (verification-only units s2/s3 passed first time; no retry)

P5
not exercised (no integration obligations)

FS-1A/B
correct (model "satisfied" recorded as a claim; closure came only from deterministic producers; no test delta)

C0
issue - mechanics correct (unchanged surface, base inventory, complete report, binding); semantic scope: a
known-good named oracle closes a whole requirement statement whose new behaviour it cannot exercise

CLEAN END-TO-END SUCCESS OBSERVED
NO

FALSE SUCCESS OBSERVED
YES

PUSHED
NO

MERGED
NO
```

Per the brief, no fix was implemented. The decision needed from the owner is the closure semantics: whether (and how)
a named pre-existing test may close a requirement statement that also asks for new behaviour - e.g. close only a
statement that is *solely* "test T keeps passing", or require B2/B3 acceptance evidence for the behaviour part.
