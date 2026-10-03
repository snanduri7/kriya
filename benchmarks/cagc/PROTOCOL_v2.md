# CAGC A/B acceptance protocol v2 (prospective)

**Status: PROPOSED (amended 2026-10-03: amendments 1-4 of the owner's decision; freeze pending consistency
checks).** After freezing, any
change - to a task, a count, the order, a model, a judge, a metric or a rule below - is a new protocol version
with a written reason, committed before the matrix it governs. No live run of this experiment starts before the
freeze. This protocol supersedes the CAGC-0 matrix-40 gate (judge-verified B >= A at n=2) **for future decisions
only**; matrix-40's result (A 5/16, B 4/16: FAIL) stays recorded as it was.

## 0. What this experiment answers - and what it does not

Experiment B (this protocol): *does CAGC-0, on top of the same Kriya revision, satisfy the acceptance rules
below?* It is separate from Experiment A, *does the known-target starvation fix remove its deterministic
defect?*, which is settled by its own deterministic evidence (fix branch; replay, regression, mutation) and is
never inferred from this matrix. Neither result is evidence for the other:
- "starvation fixed, therefore CAGC passed" is not a valid inference;
- "the CAGC matrix failed, therefore the starvation fix failed" is not a valid inference.

## 1. Prerequisites (all recorded in the run manifest before the first model call)

1. **Frozen common base (amendment 1).** Both arms derive from exactly one commit,
   `61a867fc31a7b5ff5d7f21d1ea15e4eac43f2e03` (tree `f7724626e4fcda3ecd0c55bba70a44578b19ba4c`) on `origin/main`, which already contains: JavacBuildAdapter
   (`feature/javac-build-adapter-r1 9f722e7, merged as 29eca8f`), the starvation fix KNOWN-TARGET-MULTI-TARGET-STARVATION-001 (`582ab6e, merged as 2816cc7`), the corrected
   A/B analyzer (`11cc08f + descriptive metrics 9f8ee44, in the base as 2e3602d`) and the repaired benchmark definitions (bench_v2, `0528b65 + 90818d8, merged as 35fb1c8`). Arm A **is**
   the base commit. Arm B = the base + the CAGC-0 commits only (cherry-picked, no other change). After the
   freeze no merge or change of any kind enters the base or either arm; any change voids the experiment and
   needs a new protocol version. Both arms' commits and trees are recorded in the pre-run manifest, and the
   machine-verified A-vs-B diff must equal the CAGC-0 file set.
2. Per-arm isolation as in matrix-40 (venv, state, logs, authority, static-analysis and MCP-approval homes,
   `paths.memory`); qualification records shared; dependency caches seeded from one snapshot per task.
3. SEC-009: every workspace approved by the digest-bound script for the reviewed config digest; independently
   verified; never copied between workspaces.
4. Model identity: Developer and primary `qwen3-coder:30b-kriya-e52213655394` (runtime digest
   `0769fe62...`), Planner `qwen3.6:35b-a3b-q4_K_M-kriya-620d4d5ce36a` (runtime `26cb2deb...`), every role
   QUALIFIED for its exact inference identity; each arm's `model_runtime` fingerprint recorded before its first
   model call. Any paired A/B fingerprint mismatch voids that pair (it is reported, never re-run silently).
5. Every task's judge qualified (section 3) with the exact run judge (section 9) on the prepared Arm A
   workspaces, recorded in the experiment package (`REVIEW/BENCHMARK_MANIFEST/qualification/`) before the run.
6. The deterministic pre-flight (`scripts/preflight_80_run.sh`) passes and the driver's dry run maps all 80 runs
   to the right task, arm, workspace, configuration and evidence path; the owner's explicit authorization for
   run 1 is recorded (`AUTHORIZED_TO_RUN`, naming this protocol's frozen commit).

## 2. Fixed task set (`bench_v2/tasks.json`, keyed by stable task id)

Every task is one consistent tuple: `id -> goal, workspace source + base commit, gold targets, reference fix
(commit or patch), judge`. The driver reads every field from the manifest by `id`; nothing is looked up by a
workspace directory name. A task whose judge does not qualify is removed **before** the run, recorded, and never
reported in any rate.

| id | change |
|---|---|
| java-behavior-accents | unchanged |
| java-symbol-chop | unchanged |
| java-symbol-fraction | **re-mined** from `a0ffef035` (the real Fraction.getFraction(double) overflow fix); the matrix-40 workspace was the chop base |
| spring-boot-pagesize | reference **patch** preserved (`bench_v2/reference/spring-boot-pagesize.patch`) |
| spring-xml-pettypes-cache | reference **patch** preserved (matrix-40 A r1's judged diff) |
| python-behavior-maxsplit | unchanged |
| python-symbol-valuechain | **replaces python-symbol-one**: the workspace, fix (`7847d43`) and judge were value_chain; now the goal is too |
| python-symbol-ichunked | unchanged |
| python-symbol-zipb | unchanged |
| python-error-invalidurl | unchanged (gold `httpx/_urlparse.py` confirmed) |

## 3. Judge qualification

A judge contributes to judge-verified success only if, on fresh trees before the run:
`judge(untouched base) = FAIL` and `judge(base + reference fix) = PASS` (DISCRIMINATING). Held-out judge files
are never present in a workspace during a run. A judge that passes on the base is a regression check only and is
never presented as proof that the requested change was achieved. AMBIGUOUS judges count nowhere.

## 4. Fixed design

- Replicates: **4 per arm per task** (80 runs), order per task **A, B, B, A, A, B, B, A**; tasks in manifest order.
- Every replicate starts from the task base (tracked reset, untracked non-ignored files removed), same goal text.
- **Stopping condition: exactly the 80 planned runs.** No early stop, no extension, no extra replicates for
  any task. The only re-run allowed: a pair voided by a fingerprint mismatch, or a run whose first incorrect
  state is classified INFRASTRUCTURE with evidence (model server crash, disk full, host OOM) - at most once per
  run, recorded beside the voided run, which is preserved.
- No production code, rule, task, config, model, qualification record, judge or acceptance rule changes during
  the matrix. A defect found during the matrix is recorded and classified; it is fixed after the matrix, never
  during it.

## 5. Metrics (computed only by the analyzer at a recorded revision)

- Kriya SUCCESS; judge-verified SUCCESS (DISCRIMINATING judges only, section 3); false success (Kriya SUCCESS,
  judge NOT_SOLVED); SUCCESS not independently verifiable.
- `gold_target_recall` = runs whose plan contains every known gold target / runs of tasks **with** known gold
  targets (unknown-gold runs are excluded from the denominator, never counted as misses);
  `exact_target_set_match` and `extra_target_rate` over the same eligible runs, in the gold's scope.
- Guidance: leakage, cap/fit drops, median Developer guidance, prompt tokens per role (per call and per task
  reported separately).

**Descriptive metrics (amendment 2), reported per arm and never acceptance gates** (unless a rule in §7 names
one independently - none does):
- `exact_T0_rate` = runs whose known-target package shows at least one `member_exact` unit / all runs of the arm.
- Capacity refusals: `minimum_authority_unfit` - runs with at least one such omission, the number of such
  omissions, and their rate over all runs of the arm.
- `multi_existing_target_subtask_rate` = runs whose last approved plan has at least one subtask owning more
  than one EXISTING target (planned_files with action `modify` or `delete`) / runs that reached an approved
  plan. On matrix-40 this definition gives A 0/20, B 2/20 (spring-xml r3, invalidurl r3) - the historical
  observation it is meant to measure prospectively over 40 runs per arm.

## 6. Failure causality classification (every failed run)

First incorrect state (one of): PLANNING (file selection / grouping), CONTEXT (packaging, edit authority),
DEVELOPER_OUTPUT, VERIFICATION (gates, regression attribution), FALLBACK (model capability / qualification),
BENCHMARK, INFRASTRUCTURE.

Cause (one of), with the evidence each requires:
- PRE_EXISTING_KRIYA_DEFECT - reproduced deterministically on Arm A's code.
- CAGC_ATTRIBUTABLE - the request that went wrong carried guidance (or a CAGC prompt change on its path), and a
  deterministic reproduction or a paired ablation shows that text changes the outcome.
- MODEL_VARIANCE (amendment 4) - never a generic bucket such as "the Developer produced different output". Each
  classification names the concrete observed state: stage, attempt number, failure/outcome code, target(s),
  the deterministic evidence available, what differed, and why no deterministic harness cause was established
  - e.g. "`regression_unattributed` at Developer attempt 1 on more_itertools/more.py; the same state occurs in
  Arm A on this task (runs ...); the recorded inputs to that attempt were identical; no Kriya decision differed".
  It is allowed only when (a) the same concrete state (same stage, attempt and outcome code) occurs in Arm A on
  the same task, or (b) identical recorded inputs produced different outputs.
- UNKNOWN - anything else. **UNKNOWN is never re-labelled variance.**

## 6a. Starvation contamination (amendment 3)

Invariant: **no planned existing mutation target whose minimum authoritative editable context is
collectively feasible may be silently omitted or deprived of its minimum authority because of target ordering,
prior enrichment, or budget allocation.** No planned target may simply disappear because the budget was
exhausted.

- A correctly emitted `minimum_authority_unfit` (the target's smallest safe authoritative unit genuinely does
  not fit the protected capacity) is **not** a violation: it is recorded separately as a capacity outcome.
- Detection: the analyzer flags every known-target package in which a planned existing target is shown in no
  tier and omitted `budget_exhausted` without a `minimum_authority_unfit` record (a *starvation suspect*). The
  package does not record minimum sizes or the protected room, so each suspect is decided by a deterministic
  replay of that package on the run's preserved bytes (`evidence/known-target-starvation/replay.py` method):
  if the minima were collectively feasible, the invariant is violated.
- A violation in either arm = **KNOWN-TARGET starvation fix regression -> that run's evidence is contaminated**
  and is not used as evidence about CAGC behaviour (reported, excluded from §7 rules 3-6, the regression is
  registered P1). Contamination is reported per arm; it never changes the other runs' classification.

## 7. Acceptance decision (applied once, after all 80 runs)

All of the following must hold for **ACCEPT**:
1. False successes = 0 in both arms.
2. No B failure classified CAGC_ATTRIBUTABLE.
3. **Non-regression must be causally supported:** for every task where B has fewer judge-verified successes
   than A, every B failure in that task is classified with a non-UNKNOWN cause other than CAGC_ATTRIBUTABLE.
   A candidate regression whose cause is UNKNOWN means CAGC has **not** passed non-regression (decision:
   INCONCLUSIVE, not ACCEPT).
4. Pooled judge-verified SUCCESS: B >= A - 2 (over the qualified tasks' runs; a backstop, not the primary rule).
5. No task where A has >= 3/4 judge-verified successes and B has 0/4.
6. `gold_target_recall` B >= A - 1 run.
7. Deterministic CAGC gates: leakage 0; Planner fit-drop rate <= 5%; median non-empty Developer guidance
   <= 150 estimated tokens; per-request guidance within the role caps.

**REJECT** if rule 1 or 2 fails, or rule 5 fails with a CAGC_ATTRIBUTABLE cause. Otherwise, when any rule fails,
**INCONCLUSIVE**: CAGC-0 stays on HOLD, the evidence is preserved, and a further matrix needs a new protocol
version with a written reason - never a rerun until it passes.

## 8. Unexercised rules (owner decision 2026-10-03, closed)

Greenfield generation is **not in R1 scope**. `maven/gradle .plan.greenfield_minimal_topology` and
`maven/gradle .plan.greenfield_manifest_required` (greenfield) and `spring_xml.plan.extend_existing_context`
(Architect only; the production profile's enforce controller has no Architect - MEASURED, 0 Architect calls in
matrix-40's 40 production-profile runs) are **DEFERRED from R1** and not claimed R1-certified. No live task is
created to exercise them. `maven.review.manifest_care` stays an R1 production-path rule; its deterministic
selection/injection coverage is separate work and does not change this protocol.

## 9. Judge implementation

The run judge is `judge_run_v2.sh` in the experiment package: the arm's workspace as Kriya left it (without
`.kriya/` and build output); Java/Spring through the COMMON BASE's `PolymorphicValidator` (Arm A's venv,
installed from the base revision) with the manifest's target test and held-out file; Python through the
bench's pinned judge venvs over the manifest's test files, SOLVED iff no FAILED/ERROR entry and at least one
test passed. Every judge is qualified with this exact implementation on the prepared workspaces before the run.
