# CAGC-0 closure report (KRIYA_CAGC v0.7 §13, §17.4)

> **Corrections 2026-10-03 (post-closure review): see `CORRECTIONS.md`.** This report is kept as written; the
> corrected values are marked inline. The predeclared judge-verified gate result (A 5/16, B 4/16: FAIL) is unchanged.

Arm A = 0bea22f (tree e6f5c4c), Arm B = b7cd737 (tree d3d43ea); 10 tasks x A,B,B,A = 40 runs, 2026-10-03
12:32-20:06, all completed in the planned order. Model identity identical in both arms (MEASURED: Developer/primary
qwen3-coder runtime 0769fe62..., Planner qwen3.6 runtime 26cb2deb...; every role QUALIFIED; the same two runtime
digests in every run of both arms). SEC-009: 20/20 workspaces CURRENT for the reviewed config digests. No production
code, rule, task, config, model, qualification record or judge changed during measurement.
Per-run table: `report.md`; machine data: `runs.json`, `summary.json` (benchmarks/cagc/ab_analyzer.py @ 9e99f56,
branch tools/cagc-ab-analyzer). Evidence: ~/kriya-cagc-ab/{A,B}/evidence/<task>.<rep>/ (immutable).

## 0. Benchmark validity (MEASURED; both arms equally affected; none caused by CAGC)

| Task | Untouched base | Known-good fix | Judge | Gold targets |
|---|---|---|---|---|
| java-symbol-fraction | PASS | n/a | NON_DISCRIMINATING | unknown: the workspace is the chop task's base (62f6edfd^ + chop tests); no fraction fix exists in it |
| java-behavior-accents | FAIL | PASS (851de661c657) | DISCRIMINATING | StringUtils.java |
| java-symbol-chop | FAIL | PASS (62f6edfd) | DISCRIMINATING | StringUtils.java |
| spring-boot-pagesize | FAIL | PASS (prior reference run 2026-10-02; no fix commit) | DISCRIMINATING (prior-evidence fix) | unknown (authored task) |
| spring-xml-pettypes-cache | FAIL | PASS (prior reference run 2026-10-02; no fix commit) | DISCRIMINATING (prior-evidence fix) | unknown (authored task) |
| python-behavior-maxsplit | FAIL | PASS (ce676d2) | DISCRIMINATING | more_itertools/more.py |
| python-symbol-one | FAIL | PASS (7847d43, the value_chain fix) | AMBIGUOUS for this A/B | n/a |
| python-symbol-ichunked | FAIL | PASS (f89d7a3) | DISCRIMINATING | more_itertools/more.py |
| python-symbol-zipb | FAIL | PASS (e426d25) | DISCRIMINATING | more_itertools/more.py |
| python-error-invalidurl | FAIL | PASS (7c0cda153d30) | DISCRIMINATING | httpx/_urlparse.py |

- python-symbol-one: own harness defect. The bench re-used that workspace for the value_chain task (chain9.sh); the
  A/B driver looked the goal up by workspace name and gave all 4 runs the one()/only() goal - goal and judge disagree.
- The A/B workspaces' `.kriya/` is untracked, not ignored: each replicate started with fresh Kriya state (manifest said
  "kept"; identical in both arms). The driver's ledger tail missed the first lines of each arm's second replicate;
  the analyzer reads each run's complete control records from its own archive instead.

## 1-4. Correctness and effort

| | A | B |
|---|---|---|
| Kriya SUCCESS | 5/20 | 5/20 |
| Judge-verified SUCCESS (DISCRIMINATING judges only, 16 runs/arm) | **5/16** | **4/16** |
| SUCCESS not independently verifiable (non-discriminating judge) | 0 | 1 (fraction r2) |
| False successes | 0 | 0 |
| Correct-target rate (tasks with gold) - **as reported 0.50 was WRONG (mis-computed over all 20 runs); corrected = gold_target_recall over the 12 eligible runs, CORRECTIONS.md §1** | 0.50 → **10/12** | 0.50 → **10/12** |
| Exact T0 present | 1.00 | 1.00 |
| Planner repairs, total (distribution 0/1/2) | 19 (5/11/4) | 17 (8/7/5) |
| Developer retries, total | 46 | 45 |

By category (Kriya SUCCESS; judge-verified): Java A 2/6 (2/4 verified) vs B 4/6 (3/4 verified, + fraction unverifiable);
Spring A 2/4 vs B 1/4; Python A 1/10 vs B 0/10. Two runs per arm per task: no per-task difference is statistically
distinguishable from sampling variance.

## 5-6. Prompt cost

Static base prompts (MEASURED, chars; est tokens = len/4), A -> B: Planner 11261 -> 10632 (-629, -157 tok), Architect
2826 -> 1985 (-841, -210), Developer 2680 -> 2489 (-191, -48), Reviewer 6139 -> 5803 (-336, -84), structured Planner
system 6803 -> 6646 (-157, -39), Milestone Planner 4976 -> 4917, Run Verification Judge 10705 -> 10295. Every B
request pays the smaller base; B adds guidance only where it applies (Developer java.dev.import_style 67 est tokens,
Planner Maven/Gradle rule ~58, Reviewer Spring rule 70).

- Per call, controlled: Python requests -48 est tokens (Developer) / -39 (structured Planner), no guidance; Java
  Developer requests +19 net (67 guidance - 48 base). Matched first Developer requests (same first-subtask targets):
  B lower in 6 of 8 tasks, but within-arm spread (e.g. invalidurl A 5058 vs 6860) exceeds the CAGC delta - context
  packaging, not prompt text, dominates per-request size.
- Per call, uncontrolled (all calls): Developer 5375 (A) vs 5728 (B) tokens/call; driven by the call mix (retry
  requests carry evidence), not by CAGC text (Python B has no guidance and a shorter prompt yet +314/call).
- Total task cost: all-role prompt tokens 1,202,308 (A) vs 1,099,119 (B, -8.6%); Developer calls 81 vs 71; wall
  12,981 s vs 11,393 s (-12%); Developer prefill per run: B lower on 5 of 10 tasks. These follow the trajectories (B's two
  zero-call stops, fewer retries in some tasks), so they are not attributable to CAGC text.

## 7-8. Capability selection, leakage, drops (MEASURED from post-fit events)

- 132 capability.guidance events in B (Developer 66, Planner 37, Reviewer 29); none in A (pre-CAGC code).
- Leakage: **0**. Python tasks: only python/pip selected, no rule rendered. Non-Spring Java: no Spring. Pure Spring
  XML Developer requests: spring_xml only, no Java rule. spring-petclinic selects maven AND gradle because it ships
  both pom.xml and build.gradle at its root (§5.5: both named) - correct, not leakage.
- Rules sent: java.dev.import_style 33, maven.plan.existing_topology_preserved 12, spring.review.transactional_self_
  invocation 3, gradle.plan.existing_topology_preserved 2.
- Cap drops 0. Fit drops 3 (java.dev.import_style, Developer, java-symbol-chop). Planner fit drops 0/37 = 0% (<20%:
  no two-pass structural-fit follow-up). Median Developer guidance 33.5 est tokens (67 when non-empty; max 67) <= 150.

## 9. Spring XML

B r2: Kriya SUCCESS, held-out PetTypesCacheJudgeTests PASS (2 run, 0 failed). B r3: FAILURE (see section 10). A 2/2.

## 10. Failures by first incorrect state (30 failed runs)

| First incorrect state | Runs (A / B) | Notes |
|---|---|---|
| Developer output -> regression_unattributed (stops after the candidate breaks existing tests) | fraction A r1, B r3; ichunked A r1, B r2, B r3; zipb A r1, B r2, B r3 | same plan in both arms; no guidance on Python |
| Developer output -> fallback_model_incompatible after retries | maxsplit A r1, A r4, B r2, B r3; invalidurl A r1, A r4, B r2; one A r1, A r4, B r2, B r3 (goal-invalid task) | retries exhausted, no qualified fallback can serve |
| Developer output -> no_progress / unauthorized target / plan scope | accents A r4; chop A r1, B r3; fraction A r4; zipb A r4; pagesize A r1, A r4, B r2, B r3 | pagesize: all 4 fail at s3 (A r4 unauthorized target, the rest no_progress) |
| Prompt composition: known-target packaging starves a second target (budget_exhausted) -> CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE before any model call | spring-xml B r3, invalidurl B r3 | **pre-existing Kriya defect (KNOWN-TARGET-MULTI-TARGET-STARVATION)**, see below |

Spring XML B r3 (TRACED): the Planner put ClinicServiceImpl.java and tools-config.xml in one subtask (A r1/r4 and B r2
used two). build_known_target_context filled its budget with 5 member_exact units of ClinicServiceImpl and omitted
tools-config.xml (rank 2, 522 est tokens, budget_exhausted); the edit capability for it was empty, so the attempt
stopped before any Developer request (no guidance event exists for it: nothing was sent). The known-target budget
(_reserve_graph_context_budget(window, skills, reference, design, plan, graph)) contains no guidance text, and the
same starvation logic is in Arm A's code. invalidurl B r3: the same mechanism with _exceptions.py + _urls.py
(_urls.py 5443 tokens omitted); Python requests carry NO guidance, which excludes guidance text as the cause.
Only B produced a subtask with two existing production files (2/20 vs 0/20; Fisher p ~ 0.49): an unresolved,
statistically unsupported association with B's Planner prompt (in the httpx case B's Planner received no guidance,
only the neutral rewording) - INFERRED, not CONFIRMED.

Python ichunked A vs B: all four runs reached the identical approved plan; A r1, B r2, B r3 failed s1's first
Developer attempt (regression_unattributed), A r4 retried after diagnosis_mismatch and succeeded. Developer output
variance; no CAGC-specific difference on the Python path.

Planner repairs: Java/Spring A 7 vs B 2, Python A 12 vs B 15; the repair reasons are plan-structure validations
(preserved-reference/ownership conflicts, dependency edges, schema), which no CAGC rule addresses - INFERRED variance.
Developer retries: 46 vs 45 overall; the early "B retries more" signal (first 7 runs) did not hold.

## 11. Migrated-rule observations

Exercised: java.dev.import_style, maven.plan.existing_topology_preserved, gradle.plan.existing_topology_preserved,
spring.review.transactional_self_invocation. Never selected by this matrix (all tasks EXISTING, enforce path has no
Architect, no pom.xml review target): maven/gradle .plan.greenfield_minimal_topology, .plan.greenfield_manifest_
required, spring_xml.plan.extend_existing_context, maven.review.manifest_care. No rule is shown neutral or
regressive: with 2 runs per arm no per-rule effect is measurable, and the first ablation (§13.3) needs tasks that
exercise the unexercised rules (greenfield Maven/Gradle, the direct Architect path, a pom.xml review).

## 12. Gates and recommendation

| Gate | Result |
|---|---|
| False successes = 0 | PASS (0 / 0) |
| Judge-verified B >= A | **FAIL, 4 vs 5** - the one-run difference is Spring XML B r3, a pre-existing Kriya defect, plus ichunked variance |
| Spring XML SUCCESS + held-out judge | PASS (B r2) |
| Correct-target / exact-T0 no regression | PASS (0.50 / 1.00 both) ← **corrected: gold_target_recall 10/12 vs 10/12 (CORRECTIONS.md §1); PASS unchanged** |
| No capability leakage | PASS (0) |
| Median Developer guidance <= 150 | PASS (33.5; 67 non-empty) |
| Prompt tokens per request | PASS by construction for the CAGC text (base smaller everywhere; guidance <= 70 est tokens, within role caps); observed per-call averages are dominated by trajectory/context variance (reported, not gated) |
| Planner fit-drop rate | 0% |

Broader hypothesis (cleaner behaviour at lower cost): the cost half holds by construction for every request
(smaller role prompts everywhere, guidance only where it applies, zero leakage, zero Planner fit drops); total task
cost fell 8.6% (prompt tokens) / 12% (wall) but follows trajectories. The behaviour half is not demonstrated: correctness
is equal on Kriya SUCCESS and 4 vs 5 judge-verified, within variance at n=2.

**Recommendation: ACCEPT WITH FIX.** No CAGC-0 defect was found; the measured CAGC properties (selection, leakage, fit,
telemetry, cost) meet their gates. The strict judge-verified gate fails by one run whose first incorrect state is the
pre-existing KNOWN-TARGET-MULTI-TARGET-STARVATION defect (also present in Arm A's code). Fix that defect (Fix-Now, its
own change with a deterministic reproducer from these two runs), fix the two benchmark defects (fraction task with no
fix, python-symbol-one goal lookup), then re-run the affected paired tasks to settle the gate.

## 13. CAGC-1

Do not start yet. Start it after (a) the starvation fix and the targeted re-measure, and (b) a matrix that exercises
the four never-selected migrated rules, so their first ablation is possible.
