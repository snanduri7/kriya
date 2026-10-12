# 11 - Qualification gate certification (Stage A, Phase 1)

Executable under test: none (no Kriya run, no model call). Harness: `gate-certification/gate_controls.py` (this commit). Evidence: `~/kriya-m1-live/backend-readiness-v2/gate-cert/` (`results.jsonl` 160 oracle runs, `logs/`, `env_disturbance.json`). Frozen oracles, task files and the S-cohort manifest were not modified (S manifest drift: 0; `oracle/` files in the T manifest: 0 mismatches; the 11 T-manifest mismatches are runtime `memory/*.db` and `logs/` files, not gates). **Side effect (disclosed):** the frozen T5, S1_A and S2_A oracle scripts `mktemp -d` scratch directories inside the frozen evidence trees (`backend-readiness-001/scratch/T5-hidden-*`, `backend-readiness-004/cohort-002/scratch-S{1,2}_A-hidden-*`) and never remove them; this batch created 20 such directories (list: `gate-cert/oracle_scratch_created_by_batch.txt`; no content of any frozen file changed). Removal was not performed by this session; cleanup command is in the hand-off.

## 0. Pre-registered rules (owner decision 2026-10-12, fixed BEFORE any control was run)
- **Certified object = the COMPLETE ACCEPTANCE PREDICATE**: frozen external oracle (accept + regress) plus, where named, the task-general semantic-safety guard.
- **VALID-WITH-LIMITS** only when a known oracle blind spot is covered by a deterministic, task-general guard that always runs before acceptance, uses no hidden-test knowledge, has positive/negative controls and passes mutation/regression tests. Blind spot not reliably covered => **INVALID**.
- **Known-good** = independently sourced upstream fix (reversed mutation patch = the upstream fix diff); pristine baseline for the two genuine no-op tasks (T6, S6_A). Never derived from hidden-test contents. No control is invented where none exists (marked N/A with the reason).
- Acceptance semantics are not changed to make Kriya candidates pass. Nothing in the oracles was edited.
- Three properties are reported separately: GATE VALIDITY (here), GATE STRENGTH (doc 13), SUITE COVERAGE (doc 12).

## 1. Result summary (MEASURED; a = ACCEPT exit, r = REGRESS exit, 0 = pass)

| Task | Baseline (bad) a/r | Known-good a/r, 3 reps | Alt-good | Near-miss (a) | Masking | Unrelated change a/r | Repeat flakes | Validity |
|---|---|---|---|---|---|---|---|---|
| T1 cachetools | 1 / 0 | 0/0 x3 | `>=` rewrite 0/0 | N/A (single-token fix) | N/A | 0 / 0 | 0 | VALID |
| T2 jsoup | 1 / 0 | 0/0 x3 | - | query-only 1, fragment-only 1 | N/A | 0 / 0 | 0 | VALID |
| T3 Hamcrest | 1 / 0 | 0/0 x3 | - | Unicode-only 1, trim-only 1 | N/A | 0 / 0 | 0 | VALID |
| T4 python-slugify | 1 / 1 (suite fails: it is the symptom) | 0/0 x3 | dependency-only 0/0 | extra-removed-only 1 (bad) | N/A | 0 / 0 | 0 | VALID |
| T5 jmespath | 1 / 0 | 0/0 x3 (**not independent**, see below) | - | no-`trim` 1 | N/A | 0 / 0 | 0 | VALID |
| T6 commons-csv (no-op) | **0 / 0 (correct answer)** | 0/0 x3 (= baseline) | - | tempting `EXCEL` fix 1/1 | tempting fix = the masking-shaped wrong fix, rejected | prod-source change **1** (contract: src/main must be unchanged) | 0 | VALID |
| S1_A xmltodict | 1 / 0 | 0/0 x3 | - | partial `#text` 1/1 | N/A (none constructed) | 0 / 0 | 0 | VALID |
| S2_A toolz packaging | 1 / 0 | 0/0 x3 | - | N/A (one-line fix) | N/A | 0 / 0 | 0 | VALID |
| S3_A commons-text | 1 / 0 | 0/0 x3 | - | StrBuilder-only 1, TextStringBuilder-only 1; `apibreak` (true fix + public method) rejected: PRIOR evidence 2026-10-08 (`remediation/VALIDATION_SUMMARY.md`), not re-run here | N/A | 0 / 0 | 0 | VALID |
| S4_A JsonPath | 1 / 0 | 0/0 x3 | - | N/A | **real final-12 `catch (Throwable)` candidate: oracle ACCEPTS 0/0** | 0 / 0 | 0 | **VALID-WITH-LIMITS** |
| S4_B JsonPath | 1 / 0 | 0/0 x3 | - | N/A (fix is one atomic unit) | N/A | 0 / 0 | 0 | VALID |
| S6_A toolz (no-op) | **0 / 0 (correct answer)** | 0/0 x3 (= baseline) | - | tempting eager `interpose` 1/1 | tempting fix rejected | prod-source change **1** (contract) | 0 | VALID |

Repeatability (H): every known-good arm ran 3 times (accept + regress) and every baseline accept 2 times with identical exit codes and wall times within about +-1 s (e.g. S3_A good 27/27/27 s). **Flaky gate executions: 0 of 36 known-good pairs.** A baseline S4_B accept shows 3 failures where the fixed arm has 0 (see R2-F8: JVM cache pollution by the hidden test, already documented; the fixed arm is 930/0).

Reading notes (MEASURED unless marked):
- Every rejection was verified to be for the intended reason, not an environment failure: no log contains `INSTALL_FAILED`/`ORACLE_ENVIRONMENT`; rejecting arms show failed hidden/upstream tests (T3 Unicode-only: 2 hidden failures, trim-only: 1; T2/T3 near-misses fail the named tests; T6 tempting fix: 4 upstream failures).
- T6 and S6_A: any production-source change fails by design (NOOP check). The "unrelated change" control is therefore replaced by a contract control (exit 1 is the specified behaviour), and the correct-answer baseline passes.
- "Unrelated change" = a comment line appended to a production file; for every task except S1_A it is a file the fix does not touch. For S1_A it is a trailing comment in `xmltodict.py`, the fixed file itself (behaviour-neutral, but not a different file). Accepted in all cases.
- **T5 known-good caveat (disclosed exception):** T5 has no upstream fix (it is a feature task). Its known-good is the preparer's reference solution (`controls/T5_reference_solution.patch`), written alongside the hidden tests to prove the oracle satisfiable. It is therefore NOT independent of the oracle and the T5 "known-good accepted" result is partly circular. Evidence that the T5 gate discriminates still stands independently: the no-op baseline fails and the no-`trim` near-miss is rejected with 15 hidden cases failing (reviewer-verified). Owner approved 'UNAVAILABLE rather than invented'; this one exception should be ratified or the T5 known-good marked UNAVAILABLE.

## 2. S4_A: certifying the complete acceptance predicate
- **Blind spot (MEASURED):** the real final-12 candidate (`catch (Throwable e)` around `PathCompiler.compile`, StackOverflowError still raised and swallowed) is accepted by the external oracle: hidden 870 tests / 0 failures, upstream suite 0, accept exit 0. Stored as a permanent negative control: `gate-certification/controls/S4_A_masking_final12.patch`.
- **Guard** (`kriya/workflow/exception_masking.py`, called from `attempt.py:8848` (anchored-edit staging) and `:8992` (full-file staging) before the compile gate, every attempt including repairs; fails closed; no permission input):
  - Positive control (MEASURED): fires on exactly this candidate ("newly catches Throwable without rethrowing it as an Error").
  - False-positive control (MEASURED, persisted in `gate-cert/guard_replay.json`, script `guard_replay.py`): 26 Java control arms replayed (19 carry Java diffs: known-good, near-miss, unrelated, tempting) across T2, T3, T6, S3_A, S4_A, S4_B; exactly one fires, the S4_A masking candidate. The guard and its tests were written from that single observed instance by the same author, so false-positive and mutation evidence is not independent.
  - Task-general / no hidden knowledge: it reads only candidate vs run-start file text (TRACED).
  - Coverage of writers: the guard is TRACED at the two staging branches only. Other writers were looked at: the `pom.xml` correction (`attempt.py:340`) cannot introduce a Java catch; `terminal_commit.py` commits the already-verified candidate bytes (digest-bound, not a new writer); autonomous TOOL-subtask writes and shell writes were NOT traced (UNKNOWN). The guard lives in Kriya's pipeline, not in the external oracle: re-evaluating an exported candidate through `accept.sh` alone still accepts the masking candidate.
  - Regression + mutation: 32 targeted tests pass now (13.6 s); mutation record 15/15 + 4/4 killed (doc 08, unchanged code at HEAD 051e982). No live S4_A re-measurement.
- **Verdict: VALID-WITH-LIMITS.** The pre-registered conditions hold. Limits: the guard is Java-only and covers the Error/Throwable class; other symptom-masking forms (for example a Python `except BaseException`, or swallowing an Exception subclass that hides a logic defect) are not covered by it, and no live re-measurement of S4_A under the guard exists (the class is blocked deterministically, not yet observed live).

## 3. Environment disturbance (G; MEASURED, script `env_disturbance.py`, result `env_disturbance.json`; reproduced a second time with the committed script)
| Task | Disturbance on a KNOWN-GOOD tree | Result |
|---|---|---|
| T1 (Python) | package index unreachable | ACCEPT exit **2**, `INSTALL_FAILED`: environment distinguished from product failure (1) |
| T2 (Maven) | empty `maven.repo.local`, offline | exit **1** in 1 s: indistinguishable by exit code from a rejected candidate |
| T3 (Gradle) | empty `GRADLE_USER_HOME`, offline | exit **1**, `BUILD FAILED`: same |
| S3_A / S4_A | empty Maven repo / Gradle home | still ACCEPT 0 (73-83 s / 36-37 s vs 27 s / 10 s: dependencies re-resolved; these oracles are not offline-hermetic) |

A first attempt using an empty `HOME` was discarded as a flawed control (the T2/T3 oracle scripts build their own paths from `$HOME`, so it broke the harness, not the build cache); the redo above isolates the cache.
Conclusion: the T-Java gates (T2, T3) report an environmental breakage as a plain exit 1. The direction is conservative (never a false accept) but it can masquerade as a SAFE_FAILURE or hide a FALSE_NEGATIVE; the cohort rules already require reading logs and an exported-candidate oracle run before such a classification. Recorded as a gate-strength limit, not a validity defect; no oracle was changed.

Host-environment note (gate strength): the oracles do not pin the host. The T-Python gates use `fresh_venv.sh` with bare `python3` (the logs show 3.14) while the S-Python oracles pin 3.12; JDK 17 comes from the oracle scripts; network and cache state are ambient.

## 4. Blind spots, per task (everything not covered above is NOT claimed)
| Task | Blind spot / limit |
|---|---|
| T1, T4, T5, S2_A | No masking control constructed; none judged credible, not proven impossible. T4's accept uses 20 pristine upstream tests in a fresh venv: strong. |
| T2, T3 | Environment failure and product rejection share exit 1 (section 3). Two hidden methods each; near-miss controls reject both partial fixes. |
| T6, S6_A | Genuine no-op tasks: gate validity is about refusing the tempting change; passing says nothing about code-writing ability (zero model calls). |
| S1_A | Hidden = the fix commit's `test_streaming_*` tests; one near-miss rejected; a masking form in Python is untested. Leak checker has a documented provenance false positive (R2-F7). |
| S3_A | COMPAT javap diff is stricter than the goal for pure public additions (R2-F5): classification needs the documented manual override; Kriya's own API predicate does not fail on additions. |
| S4_A | Section 2. |
| S4_B | Baseline-with-hidden JVM cache pollution (R2-F8) means "base fails 3" is not "3 distinct defects". |

## 5. Phase 1 conclusion
No gate is INVALID. Eleven gates are VALID; S4_A is VALID-WITH-LIMITS under the pre-registered rule. No qualification semantics, threshold or oracle were changed. This clears Stage A to continue to Stage B (see 12 and 13 for the other two properties; 15 for the suite decision).
