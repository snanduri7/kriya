# 23 - Final bounded architecture cycle (owner decision 2026-10-12): A plan edit-feasibility preflight, B module-aware goal-example enforcement

Authorized: A and B only. Not authorized: semantic-safety feedback continuity (registered SEMANTIC-SAFETY-REPAIR-CONTINUITY-001), compile-symbol context, partial commit, atomicity/guard weakening, T2 task-specific rules, prompt heuristics, retry budgets, models, tasks, thresholds. Closing cohort NOT authorized. Parent analysis: doc 22.

## A - implementation (product commit `aafe352`, on top of doc 22 `f5c7d1b`)

PLAN-EDIT-FEASIBILITY-001: `kriya/workflow/edit_feasibility.py` (decision), `evaluate_plan_edit_feasibility` + wiring in `workflow_controller.py` (structured plan validation loop), reason code `PLANNED_EDIT_INFEASIBLE` in `planner_repair.py` (policy-rejection class + neutral targeted guidance). Outcomes FEASIBLE / INFEASIBLE / UNKNOWN; only INFEASIBLE contributes an error + reason code + invalid-subtask id + structured evidence to the EXISTING bounded plan repair (`STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS` unchanged; nothing dropped, rewritten or widened; commit atomicity untouched).

Same-semantics derivation (TRACED, reviewed): the executor offers an operation only from (a) the whole file, admitted by `build_known_target_context` iff `estimate_tokens(content) <= mandatory_room()`; the preflight bounds that room above by `_reserve_graph_context_budget(allocation_window(config, primary))` (the formula both `known_target_limit` and `exact_member_budget` use, with no skills/learned/design/graph text subtracted); (b) a goal-named member (`_goal_named_member_candidates` + member boundaries); (c) a locus from quoted goal code (`goal_code_fragments`/`locate_fragments`, window-feasibility via `build_edit_capability`); (d) a structural insertion locus (`resolve_insertion_locus`); (e) the unit's own retrieval (`retrieve_graph_context` with the unit's goal text, the same limits; paths in member hints / symbol loci / verified grounding / hypotheses / localization candidates). INFEASIBLE only when (a) is impossible AND (b)-(e) are all proven absent. Otherwise FEASIBLE (positive derivation or whole file fits) or UNKNOWN (member could ground, quoted code windows exceed budget, retrieval grounds the file or was not/could not be evaluated, an earlier unit of the plan also edits the file, unreadable file, any failure while deciding, no config). Routing: the first invocation of a unit runs on the primary Developer binding only (attempt 1 never starts on a fallback; the stop is non-retryable), so only the primary's room is used.

Independent review (fresh reviewer, before commit): no blocker; MAJOR M1 (earlier unit of the plan edits the same file -> run-time bytes differ -> false-INFEASIBLE risk) and M2 (fallback chain made the preflight over-permissive) fixed; minors fixed (failure guard -> UNKNOWN, vacuous assertion, controller wiring test with the real evaluator, nine extra mutants). Production profile requires `workflow_controller.mode=enforce` (config seal), so the enforce loop is the production path; the legacy non-enforce planner path has no per-file validation hook and is not changed.

Verification so far (MEASURED): `tests/test_plan_edit_feasibility_001.py` 43 passed; 26 mutants (inverted comparison, UNKNOWN as INFEASIBLE, fallback used, minimum unit ignored, repair evidence/reason code skipped, auto-drop instead of repair, preflight omitted, grounding ignored, each source ignored, earlier-unit rule removed, failure guard removed, config-None check removed, position, per-file retrieval, second pass, file-count division, method/modify filters, plan mutation, goal/workspace swap) all killed; adjacent suites (539 tests) green; ruff and pylint zero. Original symptom re-measured offline on the stored T2 plans (both the first and the repaired plan) with the real frozen config, real workspace and real unit retrieval (no model call): `s2` `ElementTest.java` INFEASIBLE (35,994 tokens required vs 4,210 available; the executor's observed protected room was 4,186 <= 4,210), `s1` `StringUtil.java` UNKNOWN (a goal-named member could ground it, correctly not refused).

## A8 - focused T2 run: PRE-REGISTERED PREDICTION (written and committed before the run)

Run: exactly one `run_focused_stageD.sh T2` with label `-a1` on the clean committed tree; no rerun for variance.

If the Planner's plan (initial or repaired) contains an edit of an existing file whose whole source cannot fit the primary's room and which nothing grounds (the T2 shape; it did in 4 of 4 prior runs), then: (1) a `structured_plan_edit_feasibility` ledger record lists it as INFEASIBLE with its required/available capacity; (2) `structured_plan_validation valid=false` with `PLANNED_EDIT_INFEASIBLE` is recorded BEFORE any `unit.opened`/Developer call of the production unit; (3) the repair prompt to the Planner carries the contradiction and no chosen file; (4) the repaired plan is validated and preflighted again; (5) no `CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE` stop occurs for a planned edit. Outcome classes (owner A8): A repaired feasible plan -> GENUINE; B repaired feasible plan -> safe failure (model coding); C the Planner stays infeasible through the existing budget -> `STRUCTURED_PLAN_REPAIR_EXHAUSTED` before any unit runs (architecture works; Planner limitation); D an infeasible unit is not caught before execution (implementation failure); E a new Kriya architecture defect -> STOP, return to owner. Additional honest class F: the Planner's plan contains no infeasible edit (stochastic), so the new path is NOT exercised by this run; stated now so it is not mistaken for a pass of the path.

## A8 - focused T2 run RESULT (label `-a1`, run `20261012T185038-8bd352c8`, Kriya `b2cb85f`; evidence `~/kriya-m1-live/backend-readiness-v2/primary/P5-T2-a1/`, driver log `stageA_T2.log`)

Trajectory (MEASURED from the sealed store `records.jsonl`, seq = index + 1):
1. Planner plan 1 (seq 5): s1 modify `Node.java`; **s2 modify `ElementTest.java`** (the T2 shape); s3 verification-only.
2. `structured_plan_validation` valid=false (seq 8): reason codes `PLAN_VERIFICATION_SCOPE_UNJUSTIFIED`, `MISSING_GROUNDED_PRODUCTION_ARTIFACT`, **`PLANNED_EDIT_INFEASIBLE`**, invalid subtask `s2`; `structured_plan_repair_requested` repair_attempt 1 (seq 9). No `unit.opened` exists before it: the infeasible edit was caught **before any Developer execution** (prediction 1, 2 confirmed).
3. Planner plan 2 (seq 11): s2 changed from modify `ElementTest.java` to **create** `ElementAbsUrlTest.java` (feasible; Kriya named no file and no action; the Planner chose). Validation valid=true after one repair round (seq 14); `plan_repair_attempts=1`, budget unchanged (prediction 3, 4 confirmed).
4. Units s1, s2, s3 all RETURNED with gates passed (seq 108, 166, 200); no `CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE` anywhere in the run (prediction 5 confirmed).
5. Terminal gates failed: `terminal_obligations` and `original_requirements` (REQ-1/2/3/5 VIOLATED by the hidden external authority: `StringUtilTest.resolvesRelativeUrls` expected `...?three#four` but was `...?three`); run FAILED, `NOT_COMMITTED`; the real workspace holds no change (oracle on the workspace: accept REJECT, regress PASS). Leak check CLEAN.

Classification (owner A8): **B - repaired feasible plan -> SAFE_FAILURE**. The preflight architecture worked exactly as designed; the remaining failure is downstream and is the known T2 planning/coding limitation: the Planner again targeted `Node.java` (as in runs v2/v3a/v4a) and its Node-level fix drops the fragment; the terminal gate correctly refused to commit it (FALSE_SUCCESS 0). No new Kriya architecture defect was exposed (A9: proceed to B). The formal FALSE_NEGATIVE of the cohort is unchanged evidence; this run is a focused run, not a cohort result. Not rerun for variance (one run, as authorized).

Limit stated honestly: a Planner that picks the right production file (`StringUtil.java`) and then plans an infeasible test edit is the exact v6f shape; this run did not take that branch (it chose `Node.java`), so the end-to-end "infeasible test unit no longer discards a valid production candidate" benefit is demonstrated by mechanism (plan-stage refusal + Planner revision) but not by a GENUINE T2 outcome.

## B - implementation (product commit `248154b`)

JVM-MODULE-OWNERSHIP-001. `kriya/workflow/jvm_module_ownership.py` resolves the build module owning the declaring main file from the repository's own declared-module metadata (`repository_topology.declared_build_modules`, extracted from `detect_repository_topology` so the parsers are shared; Gradle `include` only - `includeBuild`/`includeFlat` declare no module; project directories from `tools.validate.gradle_project_dirs`; Maven `<modules>` + `pom.xml`); `example_oracle_java._scope_to_owning_module` places the one sealed class in that module's test source set (`java["module"]`, prefixed `injection_path`) and moves every enforced example outside the chosen module to the shadow list (`MODULE_NOT_ENFORCEABLE`, with the reason); `acceptance_jvm.run_java_acceptance` passes `module=` to `PolymorphicValidator.run_tests` only when set (Gradle adapter `:module:test --tests X`, Maven `-pl module -am` + both no-matching-test flags; an adapter without `supports_modules` refuses; the judgment records the module). Root-only goals and builds that declare no module are unchanged (an example whose type has no single declaring file in such a build keeps its legacy root placement). The intermediate gate (`contract_feedback`, via `workflow._run_contract_acceptance`), the terminal closer and the baseline all call the same `run_java_acceptance`. No parser change: the only graduation is `FORM_MATCHER_PASS` in `goal_example_forms.ENFORCED_FORMS`. Tests: `tests/test_jvm_module_ownership_001.py` (36), mutation 25/25 killed, independent review applied (no blocker; the single-project graduation evidence, the legacy-placement rule, the `includeBuild` parser edge, a Maven surefire flag and per-example resolution cost fixed or documented).

Replay-before-enforce (owner B5; `module-replay/module_replay.json`, harness `gate-certification/module_replay.py`): the production derive -> `run_java_acceptance` -> judge path with the matcher form enabled for the process only, real Gradle under the production OCI containment with the sanitized git metadata (OD-1), recorded T3 subjects. 8 subjects x 3 repeats = 24 runs on the final code: correct subjects (control good, unrelated change, recorded GENUINE candidate) PASS 9/9 = **zero false rejection**; wrong subjects (base, both near-misses, the recorded v2f and the latest v6f `Character.isWhitespace` candidates) VIOLATED 15/15; the verdict is identical on every repeat. Honest history kept beside it: `attempt0-no-git` and `attempt1-host-mode` ran on the host without containment, where the project's `git describe --tags` at configure time fails - a HARNESS artifact (OD-1 provides sanitized git metadata under containment; a `git clone` into the scratch copy that I tried first was rejected by the file-integrity tripwire and by the owner's metadata boundary, and reverted before any commit); `attempt2-contained-pre-review` is the contained run before the review fixes. Evidence is single-project (T3); a project whose test source set lacks Jupiter or the matcher library yields HARNESS_COMPILE_FAILED/INDETERMINATE, which blocks closure but is never a rejection. Limit: Maven module runs and nested Gradle projects are unit-tested, not exercised live.

## B9 - focused T3 run: PRE-REGISTERED PREDICTION (written and committed before the run)

Run: exactly one `run_focused_stageD.sh T3`, label `-b1`, clean committed tree, no rerun for variance.

If the first candidate of a unit that touches `IsEqualCompressingWhiteSpace.java` violates an explicit matcher example (the `Character.isWhitespace` family fails example 3, U+00A0): (1) a `contract_feedback.evaluated` record shows the sealed class with 4 compiled examples, relevant to the unit, status VIOLATED (not UNAVAILABLE), with the judgment evidence `module: hamcrest` and `injection_path: hamcrest/src/test/java/kriya/examples/...`; (2) a typed `deterministic_contract_violation` failure carrying the exact example (`FAILED EXAMPLE: assertThat("price<NBSP>10 EUR", ...)`, expected/observed) is recorded and enters the existing retry (budget unchanged); (3) the terminal gate re-evaluates every example on the final tree (an intermediate pass never bypasses it). If the first candidate satisfies all four examples: status PASSED, no repair evidence, terminal recheck PASSED. Outcome classes (owner B9): A violation -> repair -> GENUINE; B violation -> repair -> SAFE_FAILURE; C first candidate passes all four -> GENUINE (contract path executed safely, no repair evidence); D passes all four but the frozen oracle rejects (examples insufficient; do not broaden from oracle content); E the contract cannot execute despite deterministic module ownership (implementation defect); F a new Kriya architecture defect -> STOP. Not predicted: whether the Developer produces a correct implementation (variance, 1 of 3 earlier T3 runs GENUINE).

## B9 - focused T3 run RESULT (label `-b1`, run `20261012T195956-f3314bcf`, Kriya `d60bd62`; evidence `~/kriya-m1-live/backend-readiness-v2/primary/P5-T3-b1/`, driver log `stageB_T3.log`)

What worked (MEASURED, sealed store `records.jsonl`, seq = index + 1):
1. The sealed goal-example class was derived with all 4 matcher examples ENFORCED and placed in the owning module (`contract_feedback.evaluated` seq 53/135/189: `compiled_examples: 4`).
2. It executed live under the production OCI containment in the `hamcrest` module: Gradle `:hamcrest:test` output in the sealed blobs, reports `hamcrest/build/test-results/test/TEST-kriya.examples.KriyaGoalExamplesTest.xml` (COMPLETE, runner gradle, 4 cases). On the final candidate (`replaceAll("^\\p{javaWhitespace}+|...")`, the same `Character.isWhitespace` family for the third T3 run) the report is `failed 1, passed 3`: example3 (`price<U+00A0>10 EUR`) FAILED, exactly the shadow-replay prediction; a second report (4 failed) is the unfixed baseline. Terminal gate: `original_requirements` failed, run FAILED, `NOT_COMMITTED`; the real workspace holds no change (oracle on the workspace: accept REJECT, regress PASS); leak check CLEAN. No false success.

What did not work, and it is NOT the Developer: **the intermediate gate never fired.** Every attempt of s1 (seq 53, 135, 189) recorded `status: NOT_RELEVANT, relevant: 0, reason: "no example targets a file this unit touched"` although s1's only authorized and staged file is `hamcrest/src/main/java/org/hamcrest/text/IsEqualCompressingWhiteSpace.java`. Cause (TRACED, `contract_feedback.relevant_examples`): an example is relevant only when the unit touched the main file that DECLARES the example's target type; for the matcher form that type is the FACTORY (`Matchers`, declared in `Matchers.java`), while the behaviour the example exercises is implemented by a different class the unit edits. So the typed `deterministic_contract_violation` with the exact failing example never reached a repair attempt; the unit's retries (seq 80, 112, 162: `regression_test`, `operation_contract`) came from other gates. The same rule is why doc 22 section 4 found "no repair attempt" for T3.

Classification (owner B9): **F - a new Kriya architecture defect** (secondarily D/E-adjacent: the contract executes and discriminates, but its early, repair-reaching use is excluded by the relevance rule for this form). Per the owner's rule the cycle STOPS here: nothing was changed, no rerun, no fourth feature. The model outcome is SAFE_FAILURE (Developer chose the Java-whitespace API again; terminal gate refused to commit).

Candidate generic correction for the owner to rule on (NOT implemented, NOT authorized): extend `relevant_examples` so a matcher-form example is also relevant to a unit that touches a main file in the same build module as its factory type (module ownership now gives that fact deterministically), or to the class the factory returns (needs a code-intelligence link). It is deterministic, generic across matcher-style goals, adds no retry budget, and would only change WHEN an already-judged contract reaches repair; the terminal recheck is unchanged. Risk: running the class for a unit that touched an unrelated file of the module (cost: one extra batched run per attempt; a PASS remains feedback-only).

## Final product validation

Product code: `248154b` (B) over `aafe352` (A); one own defect found by the canonical run and fixed separately in `6636daf`.
- Canonical full run 1 on `31805e9` (product code `248154b`): 6 failed, 10644 passed (`final_full_pytest.log`). Five were MY defect: `run_tests` always passed `module=` to `_run_tests`, breaking validator doubles that define the original one-argument `_run_tests` (4 tests in `test_junit_counts_inconsistent_gate_success_001.py` + the signature pin `test_validation_baseline.py::test_19`). The sixth, `test_provider_contract_001a.py::test_a_deadline_stop_is_never_resent_without_response_format`, is the registered `TEST-DEADLINE-TIMING-001` (0.5 s run budget under 8-worker load), passes in isolation, not branch-caused.
- Fix `6636daf` (reported and committed separately): the kwarg is passed only for a module-qualified run; test_19 pins the keyword-only defaulted parameter; the junit tests fail without the fix (they did) and pass with it.
- The one additional canonical run allowed for a branch-caused repair, on `6636daf`: **2 failed, 10648 passed** (`final_full_pytest_run2.log`); both failures are in `tests/test_provider_contract_001a.py` (the two deadline tests of the registered `TEST-DEADLINE-TIMING-001`; the file passes 2/2 in isolation). No branch-caused failure remains. Not rerun to obtain a prettier result.
- ruff: zero findings; pylint (`kriya plugins/core_tools tests`): exit 0.
- Mutation: A 26/26 killed (incl. 9 added by the review); B 25/25 killed (+3 for the review fixes); one equivalent mutant noted and removed with the reverted git-clone code.

---

# B relevance completion (owner decision 2026-10-12, HEAD 7477c90 before this work)

Owner ruling: the T3 intermediate-relevance defect (class F above) is fixed INSIDE the bounded cycle as a correctness completion
of Change B (not a new slot); same-module relevance is forbidden (module ownership decides WHERE the class runs, never WHETHER
an example is relevant); no cohort; no C; no new retry budget.

## Offline proof of the T3 target -> implementation relationship (read-only, before any product change)

| Field | Value | Status |
|---|---|---|
| PUBLIC TARGET | `org.hamcrest.Matchers.equalToCompressingWhiteSpace(String)` (the factory the four enforced examples call) | MEASURED (goal text, compiled example `qualified_type`/`method`) |
| DECLARING FILE | `hamcrest/src/main/java/org/hamcrest/Matchers.java` (exactly one declaration of that method; overload count 1) | MEASURED (`parse_file`) |
| DIRECT CALLEE | the method's last `return` is `IsEqualCompressingWhiteSpace.equalToCompressingWhiteSpace(expectedString)` | MEASURED: `delegation.delegate_call` -> `('IsEqualCompressingWhiteSpace', 'equalToCompressingWhiteSpace')` |
| IMPLEMENTATION FILE | `hamcrest/src/main/java/org/hamcrest/text/IsEqualCompressingWhiteSpace.java` (the only main unit of that name; `.kriya/` copies are excluded by the resolver) | TRACED (`resolve_java_type` / `main_source_path` skip `.kriya`) |
| SOURCE OF RELATIONSHIP | source text of the target method, read by the structural parser + the delegate reader already shared by the Planner (`goal_facts.delegate_facts`) and the Developer context (`packing`) | TRACED |
| EXISTING FACT THAT REPRESENTS IT | `code_intel.delegation.delegate_call` (Type.method( on the last return) | TRACED; no new analyzer |
| NOT followed | `Matchers.equalToIgnoringWhiteSpace` ends in an UNQUALIFIED `equalToCompressingWhiteSpace(` (same class) - not a one-hop project-type reference, so it is correctly not followed (bounded rule) | MEASURED |

A deterministic relationship exists without a new heuristic, so the cycle continues (the stop condition was not met).

## Architecture (`kriya/workflow/contract_relevance.py`, wired in `contract_feedback.py`)

Per compiled example the relevance is one of RELEVANT_DIRECT_TARGET, RELEVANT_DIRECT_DEPENDENCY, NOT_RELEVANT, UNKNOWN (UNKNOWN
is never promoted; the example waits for the terminal gate). The dependency relation is keyed on the example's FACTORY METHOD,
never on its type (a facade type has many methods, so "type delegates to the touched file" would be same-module relevance under
another name), one hop, direction target -> callee:
1. the target type declares that method EXACTLY once (else UNKNOWN: an overload does not say which declaration the goal means);
2. its last `return` calls `Type.method(` (DELEGATES_TO) or directly constructs `new Type(` (CONSTRUCTS) a project type that
   resolves to exactly one main unit (ambiguous -> UNKNOWN; not a project type -> no relation) declared in a TOUCHED file (that
   file must declare the type, and for DELEGATES_TO the called method);
3. or a touched type DIRECTLY extends/implements the target type AND declares a method of the same name (OVERRIDES; an explicit
   import of a different type with that simple name shadows a same-package one).
A relevant example's `source_path` becomes the touched file (what repair should look at), and the relation (target, relationship,
related type, declaring file, touched file, provenance) is carried in the outcome summary, the ledger event details, the repair
message (`RELATION:` line) and the failure diagnostics. `ContractOutcome.status` keeps its closed set. Only the INTERMEDIATE gate
consults relevance (a structural test pins that no other module imports it); terminal acceptance is untouched. Reuse: structural
parser, `delegate_call` (+ the 8-line sibling `constructed_type` for the `return new T(` reading - the one small generic
addition, because CONSTRUCTS had no existing reader), `resolve_java_type`/`main_source_path`. The report dicts gained a `method`
key (the sealed class source and its digest are unchanged; the key is report-only).

Same-module alone is INSUFFICIENT (CONFIRMED by test): touching BImpl/ChainA/ChainB/an unrelated class of the same package or a
class of another module leaves an example on `Facade.a` NOT_RELEVANT.

## Tests / mutation / replay

- `tests/test_contract_relevance_001.py` (23 tests): all 15 owner matrix rows (direct target; constructs; delegates; override;
  unrelated same-module; different module; undeterminable -> UNKNOWN not run; one hop only; comment/string "claims" ignored;
  module ownership independent; a violation reaches `_run_contract_feedback_gate` as `deterministic_contract_violation` with the
  relation; terminal untouched; no task/repository identity in the code). Rows 11/12 are in the real-Gradle replay below.
- Mutation (scratch `mutate_c.py`, 19 + 2 mutants): dependency disabled, same-directory sufficient, any touched file overrides,
  UNKNOWN promoted, override direction inverted, provenance dropped (both sites), keyed on type not method, source_path not the
  touched file, override ignoring the method, import-shadow rule removed, direct rule removed, candidate root not bound in the
  workflow, evaluate ignoring the root, ambiguity not UNKNOWN, summary/diagnostics/message relation dropped, CONSTRUCTS removed,
  overload resolved by first: ALL KILLED. One survivor (`path == declaring` guard in the callee check) was PROVEN redundant (the
  direct rule runs first and returns for that case) and the dead clause was removed; one provenance site was unasserted and a
  test assertion was added (then killed).
- Replay on recorded T3 subjects (8 subjects, real Gradle under OCI + OD-1 metadata, production path derive -> relevance -> run
  -> judge; `module-replay/module_replay_relevance.json`, earlier 24-run evidence kept): recorded GENUINE candidate (v2 regex):
  all 4 examples RELEVANT_DIRECT_DEPENDENCY/DELEGATES_TO and PASS; recorded v2f and v6f `Character.isWhitespace` candidates:
  all 4 RELEVANT and VIOLATED; near-miss subjects VIOLATED; the unchanged base has no relevant example (NOT_RELEVANT x4).

## Independent review of the relevance completion (fresh subagent, before the live run) and fixes

No BLOCKER; verdict "OK to run one focused live T3 attempt". Two MAJORs and four MINORs, all MEASURED by the reviewer on scratch trees:
- MAJOR-1 OVERRIDES was not behavioral: every compiled example form calls the named type itself (`Type.m(..)` / `new Type(..).m(..)`),
  so a subtype is never invoked. FIXED by REMOVING the relation (owner matrix row 4 is deliberately NOT represented: a touched
  implementor/subclass is NOT_RELEVANT; test pins it). This narrows, not widens, the owner's allowed set.
- MAJOR-2 DELEGATES_TO/CONSTRUCTS were too loose (`return wrap(Util.f())`, `Util.f(a).g()`, callee overloads, a library type
  shadowed by a same-named project type). FIXED: the call must be the WHOLE returned expression (balanced, nothing after it), the
  callee must declare the called name with a compatible arity (varargs; implicit no-argument constructor), and an explicit import
  of a different type of that simple name in the target's file wins. `constructed_type` (added earlier) was replaced by a
  whole-expression reader built on the new `delegation.last_return_expression`.
- MINOR-2 tree walks cached per assessment (`_Structures.resolve`). MINOR-4 the broken-parse test now pins UNKNOWN; tests added for
  every MAJOR scenario. MINOR-3 (ternary/early-return bodies, deleted callee) stays NOT_RELEVANT: a false negative that only
  defers the example to the terminal gate; documented, not widened. MINOR-1 (`TARGET:` shows the touched path) is mitigated by the
  `RELATION:` line; the facade path stays in `relevance.declaring_path`.
Post-review: 27 tests; mutation `mutate_d.py` 22 mutants ALL KILLED (3 initial survivors, D2 D8 D9, killed by added tests for a
call that is only the head of the returned expression, a callee type lacking the named method, and varargs); ruff 0, pylint 0;
real-Gradle replay re-run on the final code: identical verdicts (good + recorded GENUINE PASS and relevant; v2f/v6f
isWhitespace VIOLATED and relevant; near misses VIOLATED; unchanged base NOT_RELEVANT).

## B-relevance focused T3 run: PRE-REGISTERED PREDICTION (written and committed before the run; label `-b2`)

Run: exactly one `run_focused_stageD.sh T3` with `LABEL=-b2 TAG=ws-b2`, on the clean committed tree (product HEAD = the review-fix commit
`d1eed1d`), no rerun for variance. Earlier `-b1` evidence is untouched.

Prediction: for a unit that edits `hamcrest/src/main/java/org/hamcrest/text/IsEqualCompressingWhiteSpace.java`, every
`contract_feedback.evaluated` record shows `relevant: 4` (not 0) with `relevance[*].status = RELEVANT_DIRECT_DEPENDENCY`,
`relationship = DELEGATES_TO`, `related_type = org.hamcrest.text.IsEqualCompressingWhiteSpace`, `target = org.hamcrest.Matchers.
equalToCompressingWhiteSpace`, `module = hamcrest`. If the candidate repeats the `Character.isWhitespace` family (example 3, U+00A0,
fails): status VIOLATED, a typed `deterministic_contract_violation` failure with the exact example, the `RELATION:` line and
`likely_files = [IsEqualCompressingWhiteSpace.java]`, entering repair inside the EXISTING retry budget (no extra attempt). Outcome
classes per owner: A violation -> repair -> GENUINE; B violation -> repair -> SAFE_FAILURE; C first candidate passes -> GENUINE;
D passes examples, oracle rejects; E still NOT_RELEVANT despite the proven relation (implementation defect); F new architecture/
safety defect -> STOP. Anything else is recorded as measured, not rationalised.

## B-relevance focused T3 run RESULT (label `-b2`, run `20261012T215725-b61335ed`, Kriya a41a311 = product code of d1eed1d; evidence `~/kriya-m1-live/backend-readiness-v2/primary/P5-T3-b2/`, driver log `stageB2_T3.log`)

MEASURED (sealed store, `contract_feedback.evaluated` seq 53, s1 attempt 1): `compiled_examples 4, relevant 4, status PASSED, violations 0, 20.6 s`; every
relevance record `RELEVANT_DIRECT_DEPENDENCY / DELEGATES_TO`, target `org.hamcrest.Matchers.equalToCompressingWhiteSpace`, related type
`org.hamcrest.text.IsEqualCompressingWhiteSpace`, touched `hamcrest/.../text/IsEqualCompressingWhiteSpace.java`, provenance recorded. The
prediction held (relevance fired; E did not occur; no F). s1 closed on attempt 1: the candidate
(`replaceAll("\\p{IsWhite_Space}+", " ").trim()`) satisfied all four explicit examples, so the first candidate passed (owner class C): Kriya
`success`, quality gates passed, plan_repair_attempts 1 (plan-level, within the existing budget), frozen oracle accept PASS (hidden gradle 2/2,
signatures identical) and regress PASS (489 tests, 0 failures). Classification: **C - first candidate passes all explicit examples -> GENUINE**.
The repair-delivery path (violation -> `deterministic_contract_violation` -> existing retry) was NOT exercised live by this candidate; it is
proven by the deterministic tests (`test_a_dependency_violation_reaches_the_existing_repair_path_with_its_relation`) and by the replay
(recorded `Character.isWhitespace` candidates are relevant and VIOLATED). Not rerun for variance. No new architecture defect appeared.

## Final product validation (relevance completion)

- Product HEAD: `9de5404` (the rename below) over `d1eed1d` (review fixes) over `197a3fa`. The live run executed `d1eed1d` code; `9de5404` changes only
  a function name (`assess` -> `assess_relevance`).
- Full pytest run 1 (on `a41a311`): 3 failed / 10674 passed: two registered TEST-DEADLINE-TIMING-001 tests, and
  `tests/test_qual_config_001.py::test_every_production_assessment_passes_the_configured_policy` - MY defect (the model-qualification tripwire matches
  any production call named `assess`; my new function had that name). Fixed in `9de5404` (rename; test passes).
- Full pytest run 2 (the one extra run, after the repair): 1 failed / 10676 passed - `tests/test_launch_approval.py::test_if_the_approval_cannot_be_recorded_nothing_is_approved`
  (a 0.5 s `APPROVAL_WAIT_SECONDS` async test under -n 8 load; passes 2/2 in isolation; unrelated to this branch; already registered as TEST-LAUNCH-APPROVAL-EXPIRY-TIMING-001; disclosed, not rerun for a
  prettier result). `test_provider_contract_001a.py` 47/47 in isolation.
- ruff 0 findings; pylint exit 0; relevance mutation suite 22/22 killed; replay 8/8 expected verdicts.
