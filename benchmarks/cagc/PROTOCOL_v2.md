# CAGC A/B acceptance protocol v2 (prospective)

**Status: PROPOSED - becomes FROZEN only when the owner approves it as committed.** After freezing, any
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

1. Arm A = the main revision that contains the starvation fix (KNOWN-TARGET-MULTI-TARGET-STARVATION-001);
   Arm B = CAGC-0 rebased onto exactly that revision. Both revisions and trees are recorded; B's deterministic
   CAGC gates (unit, mutation, tripwire, neutral base prompts) re-run on the rebased tree and pass.
2. Per-arm isolation as in matrix-40 (venv, state, logs, authority, static-analysis and MCP-approval homes,
   `paths.memory`); qualification records shared; dependency caches seeded from one snapshot per task.
3. SEC-009: every workspace approved by the digest-bound script for the reviewed config digest; independently
   verified; never copied between workspaces.
4. Model identity: Developer and primary `qwen3-coder:30b-kriya-e52213655394` (runtime digest
   `0769fe62...`), Planner `qwen3.6:35b-a3b-q4_K_M-kriya-620d4d5ce36a` (runtime `26cb2deb...`), every role
   QUALIFIED for its exact inference identity; each arm's `model_runtime` fingerprint recorded before its first
   model call. Any paired A/B fingerprint mismatch voids that pair (it is reported, never re-run silently).
5. Every task's judge qualified (section 3) and recorded in `bench_v2/judge_qualification.json` before the run.

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

## 6. Failure causality classification (every failed run)

First incorrect state (one of): PLANNING (file selection / grouping), CONTEXT (packaging, edit authority),
DEVELOPER_OUTPUT, VERIFICATION (gates, regression attribution), FALLBACK (model capability / qualification),
BENCHMARK, INFRASTRUCTURE.

Cause (one of), with the evidence each requires:
- PRE_EXISTING_KRIYA_DEFECT - reproduced deterministically on Arm A's code.
- CAGC_ATTRIBUTABLE - the request that went wrong carried guidance (or a CAGC prompt change on its path), and a
  deterministic reproduction or a paired ablation shows that text changes the outcome.
- MODEL_VARIANCE - only with evidence: the same first incorrect state occurs in Arm A on the same task, or
  identical recorded inputs produced different outputs.
- UNKNOWN - anything else. **UNKNOWN is never re-labelled variance.**

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

## 8. Unexercised rules

Matrix-40 never selected `maven/gradle .plan.greenfield_minimal_topology`, `maven/gradle
.plan.greenfield_manifest_required`, `spring_xml.plan.extend_existing_context`, `maven.review.manifest_care`.
Reachability (MEASURED: 0 Architect calls in 40 production-profile runs; the production profile runs the enforce
controller, which has no Architect):
- `maven.review.manifest_care` - production path (Reviewer; selected when pom.xml is a target). R1 PRODUCT PATH.
- `*.plan.greenfield_minimal_topology` - production path (enforce Planner) for greenfield goals only. R1 status
  depends on whether greenfield Maven/Gradle is R1 functionality: **owner decision**.
- `*.plan.greenfield_manifest_required`, `spring_xml.plan.extend_existing_context` - Architect only: NOT on the
  production path (reachable only in the packaged-default direct flow). Recommended: defer (or delete) unless the
  direct flow is declared R1.
No synthetic task is created to raise coverage. Real representative tasks are added in a later protocol version
only for rules the owner classifies as R1 product path.
