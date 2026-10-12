# 19 - Slot 3 design review: intermediate deterministic contract feedback (design only)

HEAD under review `70c01b2`. Read-only: no code, no model call, no cohort. Evidence labels: MEASURED / TRACED / INFERRED / UNKNOWN.

IMPLEMENTATION AUTHORIZED: NO - OWNER DECISION REQUIRED. (Last canonical full pytest: 10,529 passed / 0 failed; three small product edits followed and were validated by targeted, state-machine and architecture suites. A canonical full pytest on the exact final product HEAD is required before any cohort or merge, not for this review.)

## 1. Measured root cause (S3_A, `C2-S3_A-v4a`)
- The goal-example closer ran once, at the enforce TERMINAL `original_requirements` gate, after s1/s2/s3 had all completed; it judged REQ-7 ACCEPTANCE_VIOLATED (examples 1-3, `expected <4> but was <3>`) and REQ-9 PASSED (MEASURED). The run ended there; no Developer attempt could follow (TRACED: `terminal_gate_service`, one `TerminalGateService.run` per plan).
- Why the subtasks never saw it (TRACED): enforce subtasks run `run_generation_workflow` with `requirement_goal=None` (`plan_executor.requirement_goal_of`: only a DIRECT plan's unit or a milestone integration unit has one), so the per-unit requirement closers in the attempt loop (`workflow.py` ~5380-5520, which include `close_requirements_with_derived_examples`) are skipped. The sealed example class is bound on the shared engine before any model call and is available to subtasks (TRACED `compile_run_contract` / `bound_derived_examples`; UNKNOWN until exercised in a unit context).
- **Second, larger finding (TRACED, `retry_strategy.py` ~645):** even where those closers do run before apply (a direct goal), a blocking requirement raises `Failure(type="requirements_unresolved")`, and that type is in the set of deterministic STOPS ("the Developer cannot supply the verifier's missing verdict"). So a VIOLATED deterministic contract is a stop, not a repair, on every path today. The design therefore needs a distinct retriable failure type; moving the call site alone would change nothing.
- What timing does and does not explain (honest): timing removed the repair opportunity (MEASURED: repair never saw the failure). It does not prove repair would have succeeded (UNKNOWN). Indirect support: three S3_A runs produced a half-fix each time, a different half each time (v3a: StrBuilder incomplete; v4a: TextStringBuilder incomplete; v2f: seven attempts), so each half is within the model's reach (INFERRED).

## 2. Proposed generic architecture
A new CANDIDATE GATE in the existing attempt pipeline, `contract_examples`, placed after the compile gate and before the candidate is returned as passing. It reuses, unchanged: the sealed example class (same digest), `run_java_acceptance`/`judge_java_acceptance` (same runner, same judgments), the `Failure` -> `record_gate_outcome` -> `QualityGateFailure` -> `conclude_attempt_failure` -> `decide_for_state` retry machinery. No new workflow, no second validation framework.
- **Relevance (deterministic, no model):** a graduated example is relevant to a unit iff the source file that declares its resolved target type (path recorded at classification; today the report keeps `qualified_type`, the path is one `resolve_java_type` walk) is in the unit's modified-file set (`state.all_files_written`) or its planned files. Otherwise it is left for terminal validation. Unsupported, ambiguous and shadow forms never participate (`ENFORCED_FORMS` only).
- **Execution granularity:** one runner invocation per attempt for ALL relevant examples of the unit (batching); the judgment is read only for the relevant case identities (the others legitimately fail until their own unit runs, so they must not be judged early).
- **Boundary:** the coherent-candidate boundary (after compile passes), not per write.

## 3. State-machine integration (answers to the owner's questions)
- Owner of the check: `run_attempt`'s candidate-gate sequence (measured order in the live run: compile gate -> model run_verifier -> spec_compliance -> attempt RETURNED, then the unit-level reviewer and test gates). The new gate sits directly after compile, before the model verifier gates and the RETURN, so a violated example never reaches them. INFERRED placement; the exact line is chosen at implementation.
- Attempt count: consumed exactly as a failed compile gate is - the failed attempt is the attempt; the next repair attempt uses the existing targeted/full-set budgets. No budget is added.
- Repair permitted: the new failure type is NOT in the stop set; it classifies like a failing targeted test (retriable, `FailureCategory.VERIFICATION`/test family, attribution tier from the example's target file and the stack frames in the JUnit trace).
- Staged candidate: stays in the unit's worktree as with a compile failure; the repair prompt carries the current file content (`failed_content`), the existing mechanism.
- Previously completed subtasks: preserved by the existing unit isolation - a unit works in its own worktree cut from the plan sandbox and commits into the plan sandbox only on success (TRACED: the per-unit `commit_terminal_candidate` at the end of `run_generation_workflow`); earlier units' results are untouched by a failing current unit.
- Evidence: a `gate.result` record (gate `contract_examples`), the structured failure in `diagnosis`, and the same acceptance judgment record the terminal closer writes.
- Terminal: the existing terminal closer runs unchanged on the final state of the whole plan, evaluating ALL examples, with the same judge. Duplication is semantic-free: same artifact digest, same function; the intermediate result is feedback only and is never reused as terminal evidence (it is bound to the unit candidate digest, not the final tree).

## 4. Repair data flow (structured failure)
`Failure(type="contract_example_violation", diagnostics={...})`: CONTRACT (artifact digest, form, requirement id), PROVENANCE (goal line, character span, exact example text - already carried by `GoalExample`), EXPECTED (the stated literal), OBSERVED (assertion text parsed from the JUnit trace of that case), AFFECTED (case identity -> example), CURRENT CANDIDATE (the failed content already attached), ORIGINAL GOAL OBLIGATIONS (the authoritative goal section already injected into every Developer request; no new text weakens it). Rendering into the repair prompt follows the existing compile/test failure template (no scanner text, no hidden content: examples come only from the goal).

## 5. Safety invariants (checked against the owner's list)
1 terminal authoritative: unchanged. 2 no hidden information: only the goal-derived class. 3 deterministic relevance: file-based, otherwise deferred. 4 candidate boundary: once per attempt. 5 structured evidence: section 4. 6 no budget increase: section 3. 7 unsupported/ambiguous non-blocking: only `ENFORCED_FORMS`. 8 not weaker: an intermediate pass changes nothing downstream. 9 no duplicate state machine: gate + existing retry. 10 generality: keyed on resolved paths and artifact identities; no task, class or repository names.

## 6. Failure and state cases
| Case | Behaviour |
|---|---|
| first fails, repair succeeds | attempt 1 rejected, structured feedback, attempt 2 passes the gate, continues to ordinary validation and terminal |
| first fails, repair also fails | budgets exhaust as for any failing gate; the unit fails; the plan transaction discards as today |
| passes examples, fails compile/regression later | ordinary failure of that gate; the examples are re-checked on each later attempt |
| runner cannot execute (harness compile error, trust surface unavailable, RAT, container) | the existing judgment codes are INDETERMINATE/refusals, never VIOLATED; the intermediate gate treats them as "no feedback" (non-blocking) and the terminal closer still decides; recorded as a gate outcome so it is visible |
| unsupported/ambiguous | not run; terminal behaviour unchanged |
| unrelated subtask | no relevant examples -> gate skipped, zero cost |
| multi-file candidate | relevance is by touched file set; one invocation |
| several examples, one fails | only the failing case identities are reported (all of them, not just the first) |
| completed earlier unit + failing current | section 3 |
| terminal differs from intermediate | terminal re-evaluates the final tree; its result is authoritative |
| restart/resume | no new persistent state: the gate re-runs for the resumed attempt; a completed unit's candidate is already committed |
| evidence replay | records are ordinary gate/diagnosis records |

## 7. Performance
MEASURED: one evaluation of the S3_A example class in Kriya's containment takes ~12 s wall including derivation (9 cases; a compile gate is ~10 s and a unit test gate ~31 s in the same run; a Developer attempt is 134-193 s). Upper bound: one evaluation per attempt per unit that has relevant examples and a compiling candidate (S3_A: 2 units x 1-3 attempts = 2-6 evaluations ~ 25-75 s on a run of ~1000 s). Batching all relevant examples into the existing class run is sufficient; no per-example runs and no full suite.

## 8. Deterministic tests required (generic, no task identities)
Relevance by touched file (and deferral when untouched); failure type is retriable and structured (all fields); repair prompt contains expected/observed and the original goal; first-fail/repair-pass and fail-fail on a scripted transport; runner-cannot-run is non-blocking; shadow/ambiguous forms never run; no budget change (retry counters identical to a compile-failure scenario); terminal still evaluates and can still reject after an intermediate pass; direct and enforce paths; JDK-less/Python project unaffected; mutants: skip relevance, treat INDETERMINATE as VIOLATED, move failure to the stop set, reuse intermediate result at terminal. Prerequisites to MEASURE first (UNKNOWN today): P1 `run_java_acceptance` works with unit-level `candidate_root`/`workspace_path`/base revision (plan sandbox); P2 the bound artifact is visible to nested subtask runs; P3 trace text yields expected/observed for the assertion shapes of the graduated forms.

## 9. T2 read-only architecture finding
1. The independently sourced reference fix (the reversed upstream mutation, `mutation_T2_B.patch`) modifies ONLY `src/main/java/org/jsoup/internal/StringUtil.java` (one hunk in `resolve`); it does not touch `Node.java` (MEASURED).
2. `StringUtil.java` is the real behavioural locus. `StringUtil.resolve` has TWO production callers: `Node.absUrl` (String overload, which delegates to the URL overload) and `HttpConnection` redirect resolution (URL overload) (MEASURED by grep). A `Node.java`-only change can satisfy the goal's literal `absUrl` examples (the v4a candidate passes `NodeTest`) but leaves the shared primitive, and its other caller, defective.
3. Why the plan authorizes only `Node.java`: the Planner (a model) chose it in v2f, v3a and v4a, with the delegate fact (both `StringUtil.resolve` locations) in its request; nothing deterministic ties a delegate fact to the plan - `plan_validation.shim_plan_errors` handles packaging shims only. Classification: **the plan was not provably wrong** - Node-only is a legitimate reading of the goal's stated examples; **deterministic dependency analysis did know the second file** (facts); **the plan did not use it** (no validator); the hidden oracle is location-coupled to `StringUtilTest` (doc 11 gate-strength limit). So this is "dependency knowledge not enforced at planning" plus "oracle location coupling", not "Planner omitted a provably required file".
4. The one-file Developer constraint is three things: a consequence of per-file generation (one `DeveloperAgent` call per planned file; prompt text is convenience), an authority boundary (`allowed_write_relpaths` -> `AuthorizedFileWriter` denial; write scope comes from the approved plan), and the plan's file list is the semantic invariant. Only the authority layer is a safety boundary.
5. A legal expansion mechanism exists: plan-scope conflict -> authoritative plan revision -> re-invocation (`state.plan_scope_conflict`, `relocalization_conflict`, `recovery_coordinator`). Its triggers are (a) an infeasible edit protocol with a goal-named symbol DEFINED outside scope and (b) a self-correction diagnosis needing an out-of-scope file. A delegate callee is neither; and no deterministic evidence PROVES another production file is required for T2 (the goal's examples are satisfiable in `Node.java`). A new trigger would therefore be a design heuristic ("shared primitive with other callers"), not a proof.
6. The finished `Node.java` subtask was discarded because the later `ElementTest.java` test-writing subtask hit `CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE` (no authoritative source for a large test file): the enforce commit is a plan-level atomic transaction (PRD-004: the real workspace stays immutable until every subtask and every terminal gate pass). That discard is **required atomic behaviour (safe), wasteful in effect**. The suspect is not the transaction boundary but plan quality: the Planner added a test-writing subtask the user never asked for and whose edit capability is infeasible before it starts - a pre-execution plan-feasibility check could refuse or drop such a subtask. Atomicity should not be weakened.

## 10. Option comparison
| Criterion | A: intermediate contract feedback | B: plan/write-scope expansion for delegates | C: preserve slot |
|---|---|---|---|
| Causal confidence | HIGH that timing removed the repair chance (MEASURED); repair success UNKNOWN | LOW: no proof a second file is required; Node-only satisfies the stated examples | - |
| Cases affected | every goal with graduated examples whose unit completes before terminal (S3_A now; any future Java instance-form goal); extends to later forms | T2-like delegation only | - |
| Generality | high (a gate over any graduated contract) | medium, heuristic trigger | - |
| Safety impact | neutral (terminal unchanged; non-blocking when it cannot run) | widens write authority on a heuristic: the risky direction | none |
| Architectural cleanliness | one gate + one retriable failure type on existing machinery; also fixes the "violated contract is a stop" hole | adds a planner-validator rule or a new conflict trigger | clean |
| Risk of compensating for model weakness | low (gives the model facts it was owed) | medium (steers around a model plan) | none |
| Implementation size | ~300-400 LOC + ~25 tests; three prerequisites (P1-P3) | ~150 LOC but needs a policy decision | 0 |
| Expected solve-rate impact | bounded by repair ability; plausible on S3_A | unclear: also needs Planner to keep the file and the Developer to fix `resolve` | 0 |
| Framework reuse | high | medium | - |

## 11. Recommendation
Authorization criteria H1-H9: H1 holds as "validation timing removed the repair chance" (not as "timing caused the model's error"); H2 yes (existing retry machinery + one new retriable failure type); H3 yes; H4 yes; H5 yes; H6 yes; H7 yes (one batched run, ~12 s measured); H8 yes (generic tests above); H9 yes (no proven T2 alternative exists; B is heuristic and unproven).
**Recommendation: APPROVE SLOT 3 - INTERMEDIATE CONTRACT FEEDBACK**, conditional on first measuring prerequisites P1-P3 offline (no model) and on retaining the rule that unsupported/INDETERMINATE results never block. Do not spend the slot on T2. Keep two cheap, separate items out of the slot: (i) a plan-feasibility preflight that refuses a planned subtask whose edit capability is already infeasible (the v3a/v4a `ElementTest` discard), (ii) a budget rule so a delegate-callee source is not displaced by generic Graph-RAG signatures - both are NOT approved and each needs its own evidence review.

IMPLEMENTATION AUTHORIZED: NO - OWNER DECISION REQUIRED.
