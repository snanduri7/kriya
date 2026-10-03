# CAGC-0 matrix-40: corrections and primary-evidence checks (2026-10-03, post-closure review)

`CLOSURE_REPORT.md` is kept as written, with corrections marked inline. Nothing here changes the predeclared
gate result: judge-verified SUCCESS **A 5/16, B 4/16: FAIL** (the B >= A gate at n=2). That gate is superseded
for future decisions *prospectively* (see the next acceptance protocol), never retroactively.

Decision state recorded by the owner: CAGC-0 implementation technically validated; CAGC-0 merge **HOLD**;
CAGC-1 **DO NOT START**; known-target starvation **P1, CONFIRMED**.

## 1. Localization metric: `correct_target_rate` was mis-computed and misnamed (MEASURED, fixed)

The analyzer (`benchmarks/cagc/ab_analyzer.py` @ 9e99f56) divided gold-target hits by all 20 runs of an arm,
including the 8 runs of the four tasks with no known gold (fraction, python-symbol-one, both Spring tasks), so
every one of those counted as a miss: 10/20 = 0.50 in both arms.

Fixed in `tools/cagc-ab-analyzer` @ 11cc08f (tests: unknown-gold runs excluded from every denominator; two
mutants killed). Re-run on the unchanged evidence: `corrected-2026-10-03/` (every other summary field
byte-identical to the original `summary.json`).

| eligible runs only (6 gold tasks x 2 reps = 12/arm) | A | B |
|---|---|---|
| `gold_target_recall` (every gold target planned) | **10/12 = 0.833** | **10/12 = 0.833** |
| `exact_target_set_match` (in-scope planned set == gold; tests/docs out of scope, the gold's own rule) | 10/12 | 10/12 |
| `extra_target_rate` (an in-scope planned file not in gold) | 2/12 | 2/12 |

Both misses in each arm are python-error-invalidurl (section 2). Not measured, so not reported: target precision
over tests, member-level target correctness.

## 2. python-error-invalidurl gold target (MEASURED + TRACED: confirmed)

- Fix commit: `7c0cda153d301bde9a011e1dd7157d7e2b20889d` "Improve InvalidURL error message. (#3250)".
- Files changed: `CHANGELOG.md`, `httpx/_urlparse.py` (+14/-3), `tests/models/test_url.py`.
- Semantically required: `httpx/_urlparse.py` - both asserted messages (`..., '\n' at position 24.` and
  `... URL path component, '\n' at position 1.`) are raised inside `urlparse()`, the only place that holds the
  offending character and its index (TRACED). `InvalidURL.__init__(message)` in `_exceptions.py` receives only
  the message, so a change confined to `_exceptions.py` cannot produce them. (A catch-and-re-raise wrapper in
  `_urls.py` could in principle satisfy the behavioural judge; no run planned `_urlparse.py` or such a wrapper.)
  `CHANGELOG.md` and the test file are not mutation targets of the goal.
- Judge on fresh trees: base = FAIL (2 failed), base + fix's non-test changes = PASS (0 failed) -> DISCRIMINATING.

| run | planned production files | gold `_urlparse.py` |
|---|---|---|
| A r1 | `_exceptions.py` | miss |
| B r2 | `_exceptions.py` | miss |
| B r3 | `_exceptions.py`, `_urls.py` | miss |
| A r4 | `_exceptions.py` | miss |

`gold_target_recall` = 0/4. **Prediction recorded before any rerun: fixing known-target starvation alone is not
expected to make invalidurl succeed - its Planner never selects the required gold file.** (Measured since: the
starvation fix also leaves B r3's own shape a typed capacity refusal, section 6.)

## 3. Spring subtask grouping from the archived approved plans (MEASURED: primary evidence)

Source: each run's own `.kriya/control/planning-diagnostics/*.jsonl` (`approved_plan.subtasks[].planned_files`)
from `untracked.tar`; `runs.json` keeps only the flattened file set, not the grouping.

| task | run | production files selected | subtasks | files per subtask | multiple existing targets in one subtask |
|---|---|---|---|---|---|
| pagesize | A r1 | OwnerController.java, application.properties | 3 | s1 [application.properties], s2 [OwnerController.java], s3 [] (verification) | no |
| pagesize | B r2 | same two | 3 | s1 [application.properties], s2 [OwnerController.java], s3 [] | no |
| pagesize | B r3 | same two | 3 | s1 [application.properties], s2 [OwnerController.java], s3 [] | no |
| pagesize | A r4 | same two (+ OwnerControllerTests.java) | 3 | s1 [OwnerController.java], s2 [application.properties], s3 [OwnerControllerTests.java] | no |
| xml-pettypes | A r1 | ClinicServiceImpl.java, tools-config.xml | 3 | s1 [tools-config.xml], s2 [ClinicServiceImpl.java], s3 [] | no |
| xml-pettypes | B r2 | same two | 3 | s1 [ClinicServiceImpl.java], s2 [tools-config.xml], s3 [] | no |
| xml-pettypes | B r3 | same two | **2** | **s1 [ClinicServiceImpl.java, tools-config.xml]**, s2 [] | **yes** |
| xml-pettypes | A r4 | same two | 3 | s1 [tools-config.xml], s2 [ClinicServiceImpl.java], s3 [] | no |

Same files selected in all 8 Spring runs; same grouping in 7 of 8. Over all 40 runs (every subtask, each path
checked to exist at the task base): subtasks with more than one existing target **B 2/20 (spring-xml r3,
invalidurl r3), A 0/20** - the closure statement is **retained**, now from primary evidence.

## 4. Planner prompt diff A -> B (TRACED): classification **AMBIGUOUS**

Diffed every prompt-bearing string in `kriya/agents/agent.py`, `kriya/workflow/workflow_controller.py` and
`kriya/workflow/retry_prompts.py` between 0bea22f and b7cd737.
- No text about subtask construction, one-file vs multi-file work units, target grouping, splitting or
  combining files changed. "Each planned_files path must be owned by exactly one implementation MODEL subtask"
  and the ownership rules are byte-identical.
- One adjacent passage changed in the enforce structured Planner prompt (`AUTHORITATIVE_PLANNER_SYSTEM_PROMPT`,
  the `integration_relationships` rule): the incident narrative "(a real live incident: a storage-service subtask
  and a main-application subtask ... never actually used the storage service it depended on)" became "(each
  passing its own local checks while one never uses the other)". It concerns declaring a relationship between
  separate subtasks, not grouping files; its effect on grouping cannot be excluded.
- The rest are stack-name neutralizations (Maven/Gradle/Ignite/Qpid/pytest examples), the MINIMALISM rewording
  (build modules), and the removed BUILD MANIFEST narrative (Architect, greenfield).
- B's Planner guidance: spring-xml r2 and r3 both received exactly `maven.plan.existing_topology_preserved`;
  invalidurl r2 and r3 both received **no guidance**. Within each pair, identical B prompt inputs produced
  different groupings (r2 split, r3 grouped). This lowers, but does not exclude, the CAGC-causality hypothesis:
  LLM output can change with seemingly unrelated prompt edits.

Causal state: **starvation mechanism - CONFIRMED pre-existing Kriya defect; why B happened to produce the two
grouped subtasks - UNKNOWN.**

## 5. Python ichunked: the closure's "Developer variance" is re-classified (MEASURED + TRACED)

A r1, B r2, B r3 each stopped at attempt 1 as `regression_unattributed`: level-1 delta `CHANGED_FAILURE`
(blocking by `TERMINAL_BLOCKING_CLASSIFICATIONS`, validation_baseline.py), while level 2 showed no new failure
(`test_negative` RESOLVED_FAILURE, `test_zero_nonempty` PRE_EXISTING_FAILURE); replay confirmed nothing, so
workflow.py raised the environment stop with no Developer retry. A r4 reached the same partial state through an
ordinary targeted retry and passed. The difference between the arms is which of those two paths the first
candidate took; whether the stop itself ends recovery too early is now a separate investigation
(`handover` registry PYTHON-RELIABILITY-INVESTIGATION-001), not a CAGC finding.

## 6. Starvation mechanism, refined (CONFIRMED by deterministic replay; now P1)

The closure said the first target "filled its budget with 5 member_exact units". The replay of both runs on the
preserved bytes (`evidence/known-target-starvation/replay.py` on branch
`fix/known-target-multi-target-starvation-r1`) shows the precise mechanism: allocation window 8491; graph
context filled the 0.60 pool so the known-target limit sat at its 1000-token floor; a target not shown whole
triggers the rebuild with the 1018-token window reserve held back, leaving the limit at 0; only grounded members
have T0's protected room (`exact_member_budget`), so the second, unhinted target got nothing (`budget_exhausted`)
- or, at slightly larger limits, only a bounded excerpt, which carries no edit authority either. The recorded
package is reproduced exactly for every limit consistent with the run.

Fix (separate branch, not CAGC): every existing target's minimum authoritative unit first, against T0's room;
enrichment after. Post-fix replay: spring-xml r3 shows tools-config.xml whole in all 98 consistent budget
combinations. invalidurl r3 **remains a typed refusal** - `_urls.py` has no grounded member, so its minimum is its
whole source (5443 est. tokens), larger than T0's entire room (0.60 x 8491 = 5094); it is now recorded
`minimum_authority_unfit` (a capacity limit, not starvation).

## 7. Benchmark validity, corrected tuples (MEASURED)

- `java-symbol-fraction`: its workspace base `5cba51c7e` is "62f6edfd^ + the commit's tests" - the **chop**
  task's base; no fraction fix exists in it and its judge passes on the base. The real fix for the goal exists:
  `a0ffef035` "fix silent int overflow in Fraction.getFraction(double) (#1717)" (Fraction.java + FractionTest).
- `python-symbol-one`: the workspace `ws/python-symbol-one` was re-mined (2026-10-02 18:55) for `7847d43` "Don't
  mask TypeError raised while iterating in value_chain"; its fresh base fails exactly
  `ValueChainTests::test_type_error_while_iterating` and passes with 7847d43's change. The A/B driver looked the
  goal up by the workspace's directory name and sent the one()/only() goal. The internally consistent tuple is
  value_chain (goal, base, fix, gold `more_itertools/more.py`, judge).
- Spring tasks: no fix commit; reference patches are now preserved (next acceptance protocol, bench v2).

## 8. Registry

KNOWN-TARGET-MULTI-TARGET-STARVATION-001 is raised **P2 -> P1**. An open P1 is never parked
(`tests/test_backlog_registry.py`), and the defect is fixed on its own branch, so its canonical row (P1, CLOSED,
with the closure evidence) lives on `fix/known-target-multi-target-starvation-r1`; the P2 row this branch added in
fa8b410 is removed here so the two never collide when both reach main.
