# Live Greenfield failure: root-cause investigation (Demo 00, first real live-model run)

Status labels follow `handover/ENGINEERING_RULES.md`: MEASURED / TRACED / INFERRED / CONFIRMED / UNKNOWN.
No product code, test or live run was touched for this document. Evidence is under `handover/DEMO-V2/evidence/live-greenfield-failure/` (read-only copies).

## RUN ID
`20261015T152407-5f87716a` (trace run `8cff9e22`). MEASURED.

## WORKSPACE
`/Users/sriramnanduri/delete/2/HelloWorld` (owner folder). State dir `/Users/sriramnanduri/kriya-demo-v2-work/scenario-00/state`. MEASURED.

## GOAL
Create a small Java 17 Maven application named hello-kriya that prints "Hello from Kriya" and includes a unit test. Make the application runnable.

## PRODUCT HEAD
`5a42377` == `origin/demo/v2` at the time of the run and of this investigation. MEASURED.

## MODEL ROUTE
Primary `qwen3-coder:30b` (attempts 1-4), fallback `qwen3.6:35b-a3b-q4_K_M` (attempts 5-8), Ollama at localhost:11434, context window 32768, config source `packaged_default` (no kriya.yaml), human-in-the-loop. MEASURED (launch record, model.response provider_metadata).

## TOTAL DURATION
About 2m50s (owner timeline 09:54:08 Run started to 09:56:58 Run ended). MEASURED (owner-observed; launch record exit_code 1, failure_category `quality_gates_exhausted`).

## PLAN SUMMARY
`direct` execution plan: ONE work unit (`direct`), no decomposition (`plan_adapters.direct_execution_plan`, TRACED). Planner produced a markdown plan; Architect produced `{"files":["pom.xml","src/main/java/hellokriya/App.java","src/test/java/hellokriya/AppTest.java"]}`. The plan text mentions `mvn compile`, `mvn test`, `mvn exec:java`. Approved plan hash is null. Number of work units: 1. Number of attempts: 8.

## ATTEMPT TABLE

| ATTEMPT | UNIT | MODEL | REQUEST TYPE | CANDIDATE SUMMARY | VALIDATION RESULT | REJECTION REASON | REPAIR FEEDBACK GIVEN | NEXT ACTION |
|---|---|---|---|---|---|---|---|---|
| 1 | direct | qwen3-coder | CREATE_FULL_FILE x3 (pom, App, AppTest) | pom (JUnit 4.13.2, exec plugin), App prints text, AppTest extends JUnit3 TestCase with `assertTrue(true)` | pom_validate OK; static rule FAIL `static_rule_violation` | `[vacuous_test_assertion]` AppTest.java constant-truth assertion | none (first attempt) | recovery `targeted` |
| 2 | direct | qwen3-coder | REPAIR AppTest.java | one EDIT block plus one FILE block in a single reply | `operation_contract` (CONFLICTING_DEVELOPER_RESPONSE) | more than one outcome block | the vacuity text, target AppTest.java | `targeted` |
| 3 | direct | qwen3-coder | REPAIR AppTest.java | same shape again | `operation_contract` | same | ONLY "malformed repair response ... more than one outcome block" (vacuity absent) | `targeted` |
| 4 | direct | qwen3-coder | REPAIR AppTest.java | same shape again | `operation_contract` | same | same protocol-only text; `retry.delta changed=[]` (identical request) | `fallback_targeted` (progress = REPEATED_VECTOR) |
| 5 | direct | qwen3.6 | REPAIR AppTest.java | EDIT to JUnit 5 imports, body still `assertTrue(true)`; analysis says "the previous error was a malformed response" | `static_rule_violation` (vacuous again) | `[vacuous_test_assertion]` | protocol-only text | `full_set`; target moved to pom.xml |
| 6 | direct | qwen3.6 | REPAIR pom.xml (full set: pom, App, AppTest) | pom now JUnit 5 + surefire; App adds a "[VERIFICATION] PASS" println; AppTest asserts `App.class != null` and getMethod("main") (still contains a nested `assertTrue(true)`) | `diagnosis_mismatch` on pom.xml, whole candidate discarded | analysis quotes `junit:junit:4.13.2`, `org.junit.jupiter:junit-jupiter`, `org.junit.jupiter.api.Test`; none "appear new" | the vacuity text, but targeted at pom.xml | `full_set` |
| 7 | direct | qwen3.6 | File List Planner prompt, then pom EDIT, App, AppTest | pom EDIT adds junit-jupiter 5.10.2; anchor `    </dependencies>` | `anchored_edit` ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT, pom refused | edit anchor not in authoritative source shown | the diagnosis_mismatch text (vacuity absent) | `full_set` |
| 8 | direct | qwen3.6 | File List Planner prompt, then pom, App, AppTest (FILE) | pom ok (pom_validate OK); AppTest rewritten to JUnit 4 imports with `assertTrue(true)` again | `static_rule_violation` vacuous | `[vacuous_test_assertion]` | the anchored_edit text (vacuity absent) | `stop_exhausted`: "all applicable retry budgets are exhausted" |

After attempt 8: reviewer call (seq 293-295), run closed. Nothing applied. MEASURED from `records.jsonl` (298 records) and decoded blobs.

## FIRST WRONG DECISION
**Attempt 1: the Developer (qwen3-coder) wrote `assertTrue(true)` as the only test body.** MEASURED (seq 39-47). Kriya's rejection of it was correct (YES): a constant-truth assertion is exactly what the static rule exists to stop, and no compiler or toolchain disagrees (pom_validate passed).

The first wrong decision **by Kriya** (the first point where a Kriya-owned layer could have kept the run converging and did not) is **attempt 3's repair request (seq 94)**: after attempt 2 failed on response shape, nothing had been applied, so the vacuous test was still the unresolved authoritative defect, yet the next request carried only the protocol failure. TRACED to `retry_strategy.py:760` (`state.error_context = raw_error_context`, overwritten per failure) and `RetryPackage.render_error()` (renders only the latest failure). The same overwrite recurs at attempts 4, 5, 7 and 8. The design already knows these "repair feedback" types should inherit the active failure family: `_REPAIR_FEEDBACK_FAILURE_TYPES` collapses the failure SIGNATURE onto the family being repaired (`retry_strategy.py:742-749`), but the model-facing EVIDENCE is not carried the same way.

## CLASSIFICATION
Multiple layers, not a single one:

- **B DEVELOPER_MODEL_DEFECT** (CONFIRMED): attempt 1 vacuous test; attempts 2-4 produced a combined EDIT+FILE reply three times; attempts 5 and 8 re-emitted `assertTrue(true)`. The two models' behaviour is a real model contribution.
- **D REPAIR_FEEDBACK_DEFECT** (CONFIRMED, Kriya-owned): the unresolved authoritative finding is not carried across response-validity failures (attempts 3, 4, 5, 7, 8 see only the latest failure text). Measured effect: across 8 attempts the vacuity reached the model with its correct target exactly once (attempt 2) and once misdirected (attempt 6).
- **C KRIYA_VALIDATION_DEFECT** (CONFIRMED by offline replay): attempt 6's `diagnosis_mismatch` is a false positive. See VALIDATOR CORRECTNESS.
- **ATTRIBUTION override** (CONFIRMED, Kriya-owned; falls under D/F): attempt 5's `static_rule_violation` named AppTest.java in its message, but the self-diagnosis tier replaced the target with `pom.xml`, so attempt 6 repaired the wrong file.
- **E RETRY_POLICY_DEFECT** (CONFIRMED as a finding only, not changed): attempts 2-4 repeated an identical failure on the same model; attempt 8 repeated the attempt-1/5 vacuity but was classified `PROGRESS` with `consecutive_no_progress_attempts: 0` because the failure TYPE changed in between (A to B to A cycle undetected). Retry counts were NOT changed.
- **A PLANNER_MODEL_DEFECT**: no evidence. Plan and file list were sound.
- **G ENVIRONMENT/BUILD DEFECT**: no evidence. pom_validate passed in attempts 1 and 8. JDK 26 on host vs Java 17 target played no part in any rejection.
- **H MODEL LIMITATION ONLY**: REJECTED. Kriya's feedback and the validator were not all correct, so this is not model-only.

## WHY THIS IS THE FIRST WRONG DECISION AND NOT THE MODEL'S ALONE
Attempt 1's rejection was right, so Kriya's gate did its job. What turned one vacuous test into 7 further failed attempts is that the repair loop stopped telling the model what was wrong. Attempt 5's own analysis states "The previous error was a malformed response": the model had never been told about the vacuity at that point. MEASURED (seq 150 request, seq 152 response).

## EVIDENCE
- Records: `attempt-evidence/records.jsonl` seq 39-47 (attempt 1 candidate), 49/56/57 (static rule, diagnosis, recovery), 67 (attempt 2 feedback includes vacuity), 71/74/81 (CONFLICTING_DEVELOPER_RESPONSE), 94/121/150 (protocol-only feedback), 122 (`retry.delta changed=[]`), 138 (REPEATED_VECTOR, fallback), 152/159/167 (attempt 5), 181/196/203/204 (attempt 6), 217-244 (attempt 7), 258-289 (attempt 8).
- Offline reproducer: `replay_diagnosis_mismatch_attempt6.py` in the evidence folder.
- Code: `kriya/workflow/retry_strategy.py` (97-107, 742-760, 925-975), `retry_package.py:36`, `attribution.py` (354-385 self-diagnosis, 1600+ `find_edits_ignoring_own_diagnosis`), `attempt.py` (2751-2770, 9065-9092, 9442-9470).

## RETRY EVOLUTION (convergence table)

| ATTEMPT | PRIMARY ERROR | CHANGED? | FEEDBACK FOLLOWED? | NEW ERROR? |
|---|---|---|---|---|
| 1 | vacuous_test_assertion | n/a | n/a | n/a |
| 2 | CONFLICTING_DEVELOPER_RESPONSE | yes (new shape) | not followed (combined EDIT+FILE) | yes (protocol) |
| 3 | CONFLICTING_DEVELOPER_RESPONSE | no | n/a (feedback no longer mentioned the vacuity) | no |
| 4 | CONFLICTING_DEVELOPER_RESPONSE | no (request identical, `changed=[]`) | no | no |
| 5 | vacuous_test_assertion | yes (model/shape) | partly (valid protocol; content still vacuous, since the model did not know) | back to attempt-1 defect |
| 6 | diagnosis_mismatch (false positive) | yes (non-vacuous test written, JUnit 5 pom) | yes, the vacuity feedback WAS followed | yes (validator) |
| 7 | anchored_edit | partly | n/a | yes |
| 8 | vacuous_test_assertion | yes (JUnit 4 pom coherent) | n/a (vacuity not in feedback) | back to attempt-1 defect |

The run did not converge. It cycled A (vacuous) to B (protocol/validator) to A.

## REPAIR-FEEDBACK QUALITY
Per retry: attempt 2 good (names defect and target); attempts 3, 4, 5 poor (protocol only, defect dropped); attempt 6 correct text but wrong target file (pom.xml); attempts 7, 8 poor (secondary failure only). One fully correct delivery in 7 retries. MEASURED.

## VALIDATOR CORRECTNESS
- Static rule `vacuous_test_assertion`, attempts 1, 5, 8: **correct**.
- `operation_contract` attempts 2-4: **correct** (the prompt says never combine outcomes).
- `anchored_edit` attempt 7: **plausibly correct** per contract (the anchor was outside the authorised source). UNKNOWN whether the authorised source view was too narrow; not investigated.
- `diagnosis_mismatch` attempt 6: **INCORRECT (false positive), CONFIRMED.** Offline replay of `find_edits_ignoring_own_diagnosis(analysis, None, new_pom, old_pom)` using the recorded blobs returns the mismatch for the model's real new pom (junit-jupiter 5.10.2, surefire). All three backtick spans in the analysis are notations that cannot occur literally in pom XML: Maven coordinates `junit:junit:4.13.2` and `org.junit.jupiter:junit-jupiter` (XML has separate `<groupId>`/`<artifactId>`) and a Java import `org.junit.jupiter.api.Test` (lives in the test file). Same class as the D7 signature-notation case already handled for calls (`_construct_removed`). Side finding: `candidate.change` seq 204 records `proposed` as the OLD pom (it logs `failed_content=prior_content`), i.e. the evidence record mislabels what was checked; the check itself did receive the new pom (replay reproduces the live result). Effect: a candidate whose pom and test were both plausible was discarded wholesale.

## ENVIRONMENT STATUS
Clean. pom_validate OK at attempts 1 and 8. Nothing reached compile or test execution, so Maven/JUnit availability and JDK 26 vs Java 17 are UNKNOWN for a successful candidate; no failure was attributed to them.

## FINAL FAILURE MECHANISM
Attempt 8 produced a coherent JUnit 4 pom and test but again with `assertTrue(true)`. The static rule rejected it, and `recovery.decision` returned `stop_exhausted` ("all applicable retry budgets are exhausted"). Configured budget values were not extracted in this pass (UNKNOWN); the number of attempts (8) is MEASURED. The vacuity was not in attempt 8's feedback.

## OWNER WORKSPACE EFFECT
None applied. The folder holds only `.git` (commit a7d41e4 "Kriya: initial commit to enable isolation") and `.kriya/` state (run lock, checkpoints, launch/run records, an empty detached worktree). No source file, no kriya.yaml. MEASURED. Runtime-command approval screen was never reached. MEASURED.

## ACTIVITY UX FINDING (recorded, not implemented)
The Activity drawer showed "Creating project files / Trying again" repeatedly with no reason. The authoritative data exists per attempt (failure type, reason code, file, recovery action in `diagnosis` and `recovery.decision` records). A deterministic humanized line, for example "Attempt 3: the model's reply was in the wrong format, retrying with the same file" or "Rejected: test only asserts true", would be supportable. Do not implement before the owner decides; wording must come from typed reason codes, never from model text.

## FAILURE-MESSAGE UX FINDING (recorded, not implemented)
"Kriya could not complete the requested change. Nothing was applied" is generic for `quality_gates_exhausted`. The failure class is known (`static_rule_violation` last, with the quoted rule), so a deterministic message such as "The generated test only asserts true, so Kriya refused it after 8 attempts. Nothing was applied" is supportable from `failure_type` and the gate outcome.

## CAN A CONTROLLED REPRODUCER BE BUILT?
- Defect C (`diagnosis_mismatch` false positive): **YES, already built** as an offline replay (no model, no network): `replay_diagnosis_mismatch_attempt6.py`. Not yet a pytest test.
- Defect D (feedback drop): **YES, feasible** deterministically: record attempt 1 static failure, then an `operation_contract` failure, then render the next retry request and assert the vacuity text is present. Not built (see decision).
- Attribution override: **YES, feasible**: a `static_rule_violation` naming AppTest.java plus a model analysis mentioning `pom.xml` currently re-targets to pom.xml. Not built.

## DECISION: NO PRODUCT FIX IN THIS PASS
Stop condition applies (multiple independent Kriya-owned root causes: D feedback drop, attribution override, C false positive; a single bounded fix is unsafe). Specifically:
- C needs a coordinate-notation matcher, which is Maven-flavoured and would risk a HelloWorld/Maven-specific fix.
- D and the attribution override both touch the heavily incident-tuned retry/attribution machinery (`retry_strategy.py`, `attribution.py`, many documented live incidents); fixing them blind to the existing tests is not a bounded change.
- None of these is a one-line, clearly generic change, and fix authority requires a failing characterization test first.

## RECOMMENDED NEXT ACTION
Owner decision, in this order:
1. **D (highest value, generic):** carry the open authoritative finding forward as typed repair evidence when a response-validity failure (`_REPAIR_FEEDBACK_FAILURE_TYPES`) hits while an earlier authoritative failure is unresolved. First write the characterization test (render the attempt-3 request; assert the vacuity and its target file are present). It fails today.
2. **Attribution:** a validator-named file in a deterministic failure must not be overridden by model self-diagnosis text.
3. **C:** `diagnosis_mismatch` must not treat notation that cannot occur literally in the target file type (coordinates in XML, imports in another file) as evidence of a missing change, defined generically (spans that tokenise into parts already new in the content), with a regression test built from the recorded blobs.
4. **E (report only):** detect A-B-A failure cycles; no change to retry counts.

## OWNER RETRY ADVICE
**Do not retry live Demo 00 now.** The same loop can recur: the first Developer draft is a coin flip on the vacuity, and the loop cannot reliably recover from it. Also UNKNOWN: whether the `direct` single-unit path reaches the runtime-command review at all. The controlled end-to-end proof (`tests/test_greenfield_runtime_end_to_end.py`) used a four-unit structured plan with a verification-only `s4`. `attempt.py` has an inline review block in `run_attempt` that a direct plan may use, but no direct-path test exercises it. Why `packaged_default` yields `direct` was traced only as far as `direct_execution_plan` (a `kriya generate` goal with no decomposition).

## NOT DONE / UNCERTAINTY
Not run: any live model, full suite, pytest. Not read: configured retry-budget values; the full request text for attempts 3/4 beyond the failure section; the attempt 7 anchor-scope question. No branch created, no tag, nothing pushed.
