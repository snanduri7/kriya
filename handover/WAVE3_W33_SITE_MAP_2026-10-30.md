# W3-3 site map and first-owner design (KCEP-1 / KCEP-2 / KCEP-3) - 2026-10-30

Status: DESIGN RECORD, no executable W3-3 change yet. Rows: DIRECTORY-NAME-PRUNING-SOURCE-MEMBERSHIP-RESULT-TRUTH-001 (P2), JVM-TYPE-RESOLUTION-OUTPUT-DIR-NAMES-001 (P2, folded in).
Start point: HEAD f5b8e96, tree clean, executable fingerprint 6b57128eadab8aa9, refs/kriya/verified 7eddc5d6e255 unmoved, nothing pushed.
Evidence (outside the repo): `~/kriya-fixforward-2026-10-22/closure/exec_layer/d2_rebuild/WAVE3_W33_2026-10-30/` - `probes/` (the three W3-0 reproducers repointed at this repo), `prefix_outputs/` (their output at f5b8e96, PRE-FIX, kept), `t1_probes_at_HEAD/` (T1 probes p2-p4 repointed, output at f5b8e96).
Labels: MEASURED / TRACED / INFERRED / UNKNOWN as in ENGINEERING_RULES section 2. The T1 census was taken at 1985f18 (an older archive); a row is called "re-measured at f5b8e96" only where this session re-ran it.

## KCEP-1 - reproduced on the current HEAD (MEASURED, f5b8e96)

| Case | Reproducer | Result at f5b8e96 | Matched control |
|---|---|---|---|
| Four `file_resolution.py` guards fail open | `probes/probe_file_resolution_guards.py` | with the only owner / caller / entrypoint under `build/`, `dist/` or `target/`: entrypoint guard `[]`, public-API guard `[]`, test-redirection guard `[]`, owner preference keeps the invented parallel path | same fixture under `core/`, `app/`, `bin/` fires every guard |
| Hidden duplicate Java type, confident wrong answer | `probes/probe_jvm_duplicate_hidden_flip.py` | duplicate under `build/` or `target/`: `resolve_java_type` -> `('com.acme.core.Gen','')` | duplicate under `util/` or `dist/`: `type Gen is ambiguous` (note: `dist` is NOT in this resolver's list, so the control and the defect differ by name only) |
| Python runtime grounding runs a different program | `probes/probe_python_runtime_target_substitution.py` | proposed tracked entrypoint `pkg/build/runner.py`: entrypoints seen `[pkg/core/other.py]`, grounded `python -m pkg.core.other` | proposal under `pkg/core2/`: grounded to the proposed `pkg.core2.runner` |
| Legitimate source under `build/`, `target/`, `dist/` is invisible | same probes (the `build`/`target`/`dist` rows) | not found / not counted | the `core/` rows find it |
| T1 rows 6, 7, 11-13, 15-21 (failure attribution, contract consumers, migration importers, evidence hash, runtime facts, nested control, analyzer, ...) | `t1_probes_at_HEAD/p2..p4` | output IDENTICAL to the 1985f18 archive-time output (diff: one temp path, one requirement-id label) | matched `core/` controls inside the same probes |

Non-reproduced / scope corrections: none. The only wording correction: row JVM-TYPE-RESOLUTION-OUTPUT-DIR-NAMES-001 already carries the duplicate-flip reclassification.
Fixture honesty (TRACED): `probe_file_resolution_guards.py` and `probe_jvm_duplicate_hidden_flip.py` use NON-git temporary directories. They prove the pruning, not git membership; the W3-3 regression tests must `git init` + `git add` the fixture and assert membership (`git ls-files`) BEFORE invoking Kriya.

## KCEP-2 - site map (the 37 numbered T1 sites, T1 numbering kept)

Actions: FIX = FIX IN W3-3; RETAIN = RETAIN AS INTENTIONAL FILTER; DISPLAY = TEST/DISPLAY ONLY; SEP = SEPARATE FUTURE ITEM; UNREACH = UNREACHABLE.
"Result-truth impact" is the T1 classification, re-measured at f5b8e96 where the column "RM" is yes. Canonical owner for every FIX row = Git's bound-tree membership (`kriya/repository/snapshot.visible_repository_paths`, the owner `copy_bound_tree` already uses) - never a directory basename.

### The 10 suspected P2 result-truth sites (each explicitly dispositioned)

| # | Site | Root type | Semantic question | Authoritative / result-truth | Generated-output filter? | Current behaviour | RM | Canonical owner | Action |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `file_resolution.resolve_planned_artifact_owners` (+ `prefer_existing_artifact_owners`) | design stage: raw workspace (git OR non-git); Developer stage: real workspace (git after bootstrap) | which existing file owns a planned artifact | changes the write scope / creates a parallel owner | no | owner under pruned name -> "no owner"; C2 `out/gen` manufactured as owner | yes | bound-tree membership | FIX (unit A) |
| 3 | `find_brownfield_test_redirections` | real workspace (git) | existing production owner of an established test | guard (rejects a candidate) | no | FAILS OPEN | yes | bound-tree membership | FIX (unit A) |
| 4 | `find_brownfield_public_api_changes` | real workspace (git) | callers/tests naming a removed signature | guard | no | FAILS OPEN | yes | bound-tree membership | FIX (unit A) |
| 5 | `find_unrequested_architectural_surfaces` | real workspace (git) | baseline entrypoints | guard | no | FAILS OPEN (whole gate off) | yes | bound-tree membership | FIX (unit A) |
| 6 | `failure_grounding.resolve_repository_locator_files` | CANDIDATE worktree (git, or a scoped-snapshot sandbox, see KCEP-3 Q1) | resolve a compiler locator to the unique repo file | failure attribution; also a duplicate-hidden flip | no | `[]` for a real file; duplicate hidden -> confident pick | T1 only (p2 identical at f5b8e96) | bound-tree membership | FIX (unit C, after Q1) |
| 8 (+9) | `example_oracle_java.resolve_java_type`, `main_source_path` | candidate root: workspace at contract compile (can be non-git), candidate later | which unit declares a goal-named type | derives / refuses a SEALED example artifact | no | not found; duplicate hidden -> confident unique type | yes (probe) | bound-tree membership | FIX (unit B) |
| 11 | `contract_lifecycle._reference_scan` | real workspace (git) | consumers of a changed public API | contract invalidation / authority | no | FAILS OPEN (transition derived, no consumer recorded) | T1 only (p3 identical) | bound-tree membership | FIX (unit C) |
| 12 | `migration._scan_java_files` | real workspace (pre-mutation baseline) | which dependency is imported vs dormant | obligation identity (requirement truth) | no | `NOT_APPLICABLE`, obligation never created | T1 only (p3 identical) | bound-tree membership | FIX (unit C) |
| 18 | `runtime_verification._collect_python_runtime_grounding_facts` (+ `attempt._build_python_runtime_grounding`) | candidate worktree | validate an LLM-proposed Python run target against the real repo | runtime verification target | no | substitutes a DIFFERENT program, or silently skips | yes (probe) | bound-tree membership of the candidate | FIX (unit C, after Q1) |
| 20 | `milestone_completion.workspace_evidence_hash` (git pathspec `:(exclude,glob)**/{GENERATED_OUTPUT_DIRS}/**`) | real workspace (git) | identity a VERIFIED_NO_CHANGE proof is bound to | evidence binding | YES, documented and test-pinned: "except under a directory the repository analyzer already treats as generated output" | a new untracked source file under `build/`... leaves the hash unchanged | T1 only (p4 identical) | git `.gitignore`, not names | SEP - OWNER DECISION (STOP condition 3: it is an intended generated-output contract; reconcile, do not weaken; tests pin it) |

### Other result-truth / authority sites

| # | Site | Purpose | Action | Reason |
|---|---|---|---|---|
| 2 | `discover_response_construction_owners` | Architect write scope (scope under-inclusion, C2 over-inclusion) | FIX (unit A, same walker as row 1) | same helper; P3 |
| 7 | `compile_regression._tree_index` | attribution of a compile regression (fails closed with the wrong reason) | FIX (unit C) | same owner as row 6; P3 |
| 10 | `repository_license_header` | cosmetic text copy | FIX (unit B, same module) | same file as rows 8/9 |
| 13 | `requirement_closers._candidate_test_files` | TEST_ADDITION closer; C2 counts an ignored file (fail open) | SEP | P3; own registry row |
| 14 | `requirement_closers` documentation referent | over-inclusion only, fail closed | RETAIN | hygiene |
| 15 | `baseline_policy._existing_tests` | baseline capture decision; fires only when EVERY test is under a pruned name | SEP | P3 |
| 16a/16b | `repository_topology._find_java_main_entrypoints` / `_discover_child_build_roots` | milestone plan validation; fails closed with the wrong reason | SEP | P3; non-git design stage |
| 17 | `retry_strategy.compute_effective_workspace_hash` walk branch | near-unreachable (INFERRED) | RETAIN | hygiene |
| 19 | `control/persistence.nested_control_state` | legacy nested control store; control dirs are gitignored BY DESIGN | SEP | git is the WRONG owner here; needs a name-free bounded walk |
| 21 | `analyzer.analyze/index_repository/is_ignored` | retrieval/context; hand-rolled third membership implementation | SEP | P3; not authoritative |
| 22 | `static_analysis/scope._walk_sources` | opposite direction (over-inclusion) | SEP | P3 |
| 23, 24 | `capabilities/java.py`, `capabilities/python.py` `has_sources` | wrong failure-class label / stack detection; `PYTHON.source_files` UNREACHABLE | SEP (24b: UNREACH) | P3 |
| 25 | `validate._project_dirs`, `gradle_project_dirs` | generated-output LAYOUT (module dirs literally named `build`/`target`) | RETAIN (edge case -> SEP) | marker-based discovery, not repo membership |
| 26 | `validate._compiled_class_exists` | locates generated `target/classes` | RETAIN - MUST NOT change | generated output is the thing it finds |
| 36 | `worktree._snapshot_all_files_without_git` | cleanup-only | RETAIN - MUST NOT change | never a manifest |
| 37 | `named_test_oracle._candidate_files` | trust surface; the candidate can write `.gitignore` | RETAIN - MUST NOT change to git membership (MEASURED p5 C) | intentional contract |

### Display / context / performance (DISPLAY, no change in W3-3)
27 (`cli review/ask`), 28 (`mcp search_code`, `SearchTool` - model-facing, P3 capability -> SEP), 29 (`planning_request` planner candidates, P3 -> SEP), 30 (`triage._workspace_appears_empty`, P3 -> SEP), 31 (`milestones._list_workspace_files`), 32 (`goal_facts.manifest_files`), 33 (`review_context`), 34 (`production_doctor`/`static_analysis.doctor`), 35 (`code_intel.discover_source_files`: the PRECEDENT; git first, dot-dir fallback).

Counts: 10 P2 sites dispositioned (FIX 9, SEP-owner-decision 1); P3 fix-with-the-unit: 2, 7, 10 (+9); P3 SEPARATE FUTURE ITEM: 13, 15, 16, 19, 21, 22, 23/24, 28, 29, 30 -> to be registered as ONE new row with target_scope before W3-3 closes (a batch may not close with a newly found P2/P3 missing target_scope).

## KCEP-3 - first wrong owner, the proposed owner, and open questions

First wrong owner (TRACED, repeated at every FIX row): a decision "which files belong to the bound/source repository" is answered by `dirs[:] = [n for n in dirs if n not in {...build, target, dist...}]`, i.e. by a directory basename, instead of the bound-tree membership owner.

Proposed owner (no new abstraction): the existing `visible_repository_paths` (tracked + untracked non-ignored). `copy_bound_tree` already converts its failure into the EXISTING typed result `BoundTreeError(BOUND_TREE_UNAVAILABLE)` (bound_tree.py:76-78). W3-3 would extract that one conversion into a small public function of the same module (mechanical owner reuse, change type A/B: `copy_bound_tree` keeps identical behaviour) and make the FIX sites call it.

Open questions that need a measurement or an owner decision BEFORE the first executable slice (STOP condition 1/6 applies if unresolved):
- Q1 (MEASURED by T1 p5 B, to re-measure at HEAD): candidate roots come in two shapes - a real `git worktree` and a "scoped snapshot sandbox" (an empty synthetic `.git` plus copied files). In the sandbox `visible_repository_paths` returns gitignored outputs of the enclosing repo (`target/classes/...`, `build/out/...`) and the `.kriya-scoped-snapshot` sentinel. So for the candidate-worktree sites (6, 7, 18) bound-tree membership does NOT exclude generated output in that root type. Proof of correct membership is unavailable there until this is resolved (which root types can reach those sites, and may the sandbox be listed by tracked paths only?).
- Q2: what do the callers do with a `BoundTreeError`? The guard call sites in `attempt.py`, `development_loop.py` and `stage_architecture.py` have no local handler (TRACED); some may sit under a broad `except` that would turn a typed refusal into silence (fail OPEN again). Each caller must be traced and given an explicit typed stop (precedent: `workflow.py:1285`, `authority_bundle.py:461`, `deterministic_failure_diagnostic.py:269` map it to AUTHORITY_EXECUTION_UNAVAILABLE).
- Q3: design-stage rows 1 and 2 (`stage_architecture.py:218/256`) run on the RAW workspace BEFORE `create_git_worktree` bootstraps git, so a brownfield non-git directory reaches them. Owner decision already recorded: fail closed with an existing typed result, no basename guess, no sealed manifest. If a real production non-git root needs supported membership there, STOP and report.
- Q4: row 20 is an intended documented generated-output contract pinned by tests; moving it to `.gitignore` semantics changes an unrelated intended contract -> owner decision (STOP condition 3).

Proposed implementation order (each unit = ONE coherent owner per executable commit, bookkeeping separate): **A** the five `file_resolution.py` walkers (rows 1-5, and 2) behind one membership helper; **B** `example_oracle_java.py` (rows 8, 9, 10); **C** the candidate/real-workspace attribution and runtime-facts sites (6, 7, 11, 12, 18) once Q1 and Q2 are answered. Unit A has the single clean root type (post-bootstrap git for 3/4/5; Q3 for 1/2) and is the smallest safe start.

## Fool-proofing (answered before any implementation)

- A wrong implementation that still passes: removing `build` from one blacklist while a sibling site keeps its own list. Control: bounded sibling census (this table) + a structural test that fails when any FIX-scope module still prunes `os.walk` dirs by a basename set + cross-path mutants (restore the list at each site).
- Stale state that looks green: a test repo that does not actually track the file under `build/`. Control: every fixture is a `git init` repo and asserts `git ls-files` membership BEFORE invoking Kriya; the existing non-git `tmp_path` fixtures of these functions are rewritten with a note, not silently kept.
- A bypass: another guard recomputing membership independently (rows 11, 12, 18 do). Control: the structural census above and a semantic mutant per site.
- An overcorrection that looks green: including genuine generated output. Control: gitignored `build/`/`target/`/`out/` negative cases per site (C1 and C2 in the T1 vocabulary) and the MUST-NOT-change rows 25/26/36/37 stay untouched and pinned.

## Stop status
No stop condition has FIRED yet; Q1-Q4 are the conditions that would, and they are open. No executable W3-3 change was made; the first executable slice is unit A, after Q2/Q3 are traced.
