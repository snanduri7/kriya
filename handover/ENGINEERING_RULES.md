# Kriya Standing Engineering Rules

These rules apply to ALL coding sessions, investigations, fixes, reviews, certifications and maintenance work unless the user explicitly overrides one.

They are mandatory engineering behavior, not suggestions.

## 1. Core rule: Evidence First, Fix Now

Use:

`OBSERVE → MEASURE → TRACE → HYPOTHESIZE → DISCRIMINATE → CONFIRM → PREDICT → FIX → REPRODUCE ORIGINAL SYMPTOM → REGRESSION → MUTATION → VERIFY → CLOSE`

Do NOT use:

`OBSERVE → plausible explanation → code change → tests written around explanation`

And do NOT use:

`DISCOVER → BACKLOG → CONTINUE`

for a fixable current defect.

"Fix Now" begins only after the root cause is sufficiently established.

## 2. Every technical claim must be classified

For every significant finding, explicitly distinguish:

* `MEASURED` — directly observed from commands, logs, metrics, files or runtime behavior.
* `TRACED` — verified by reading the exact code/path that produced the observation.
* `INFERRED` — a hypothesis consistent with evidence but not yet proven.
* `CONFIRMED` — discriminating evidence establishes the root cause.
* `UNKNOWN` — evidence is insufficient.

Never present `INFERRED` as `CONFIRMED`.

Never implement a fix solely from an inference when a cheap discriminating check is available.

## 3. Before modifying production code

For every non-trivial defect, record:

`Observation:`
`Status: MEASURED`

`Producing code/path traced:`
`Status: TRACED / NOT YET TRACED`

`Primary hypothesis:`

`Alternative plausible hypotheses:`

`Discriminating check:`

`Confirmed root cause:`
`Status: CONFIRMED / NOT CONFIRMED`

`Predicted measurable result after fix:`

Do not modify production code until:

* the producing path has been traced where practical;
* materially plausible competing explanations have been considered;
* a discriminating check has been run when practical;
* the expected post-fix observable result is stated.

For urgent safety defects, containment may precede full diagnosis, but permanent repair still requires root-cause confirmation.

## 4. Reproduce the actual observed scenario

Regression tests must reproduce the real failure mechanism, not merely the developer's summary of it.

If the observation came from:

* fresh temporary workspaces → test fresh temporary workspaces;
* reused workspace → test reuse;
* real retry sequence → reproduce that sequence;
* failed process startup → exercise startup failure;
* live model trajectory → create a deterministic scripted equivalent;
* container failure → exercise the container failure path;
* cleanup after interruption → test interruption/abnormal exit.

A synthetic test that validates only the implementation hypothesis is insufficient.

## 5. Predict before fixing

Before applying a fix, state an observable prediction.

Example:

`Before: 30 distinct ephemeral workspaces leave 30 retained cache entries.`
`Prediction: after the fix, the same measured workload leaves the intended bounded count.`

After the fix, re-run the original measurement.

If the prediction is wrong:

* do not rationalize it;
* do not close the defect;
* return to diagnosis.

## 6. Original symptom is the first post-fix gate

After implementing a fix:

1. Re-run the exact original reproducer/measurement.
2. Confirm the predicted result.
3. Run the regression test.
4. Run mutation/adversarial tests where appropriate.
5. Run adjacent subsystem tests.
6. Run lint/static checks.

Do not rely on newly written unit tests as the primary proof that the original defect is fixed.

## 7. Tests must challenge the hypothesis

When writing a regression, ask:

* Would this test fail if my understanding of the defect were wrong?
* Does it reproduce what was measured?
* Can it pass vacuously?
* Does every assertion execute?
* Does it distinguish the proposed root cause from plausible alternatives?
* Does removing/reversing the production fix make it fail?

Avoid:

* conditional assertions that may never execute;
* mocks that bypass the defect path;
* tests that merely reproduce the new implementation;
* over-short timing assumptions;
* assertions only on final SUCCESS/FAILURE when intermediate state matters.

## 8. Mutation requirement for decision logic

For changes affecting:

* retry;
* fallback;
* recovery;
* authority;
* verification;
* commit;
* lifecycle;
* resource cleanup;
* state transitions;
* security;
* release/certification decisions;

introduce deliberate mutations or equivalent negative controls.

The test must fail when the important part of the fix is removed or inverted.

If a mutation survives:

* strengthen the test; or
* prove the mutated branch is redundant/unreachable and remove unnecessary code.

## 9. Fix-Now policy

When a current reproducible product defect is confirmed:

`REPRODUCE → ROOT CAUSE → FIX → TEST → VERIFY → CLOSE`

Do not park it in backlog merely to maintain momentum.

A registry/backlog row may be created for traceability while work is active, but close it with the fix.

Deferral requires at least one of:

* substantial independent redesign outside current scope;
* unavailable infrastructure/runtime;
* disproportionate risk with insufficient evidence;
* clearly future capability rather than current defect;
* explicit user approval.

State the reason for any deferral.

## 10. Do not confuse measurement artifacts with product defects

Before classifying resource, performance or correctness issues, identify who owns the observed artifact.

Examples:

* Kriya product;
* pytest/test harness;
* diagnostic script;
* Docker/container runtime;
* Ollama/model runtime;
* Java/JDTLS;
* OS/toolchain;
* expected cache;
* persistent evidence;
* unknown.

Do not fix Kriya for an artifact produced by the measurement harness.

## 11. Trace the producer, not just the output name

Names are not proof of ownership.

Before claiming that a file, directory, process, counter or metric came from component X:

* find the code that creates it;
* verify its parent/path/lifetime;
* verify the actual execution path exercised.

Do not classify based solely on filename prefixes, timestamps, stack fragments or naming similarity.

## 12. Filesystem timestamps are evidence only when semantics are known

Do not assume modification time means creation time.

Before using timestamps diagnostically:

* identify the filesystem timestamp being read;
* account for inspection/tools that may mutate metadata;
* prefer creation/birth time where supported;
* correlate with logs/run IDs when possible.

## 13. State-machine discipline

For workflow changes, reason in terms of:

* current phase/state;
* authoritative state;
* counters/budgets;
* previous states;
* legal next transitions;
* terminal conditions.

A local counter or subsystem state must not silently control unrelated global behavior unless explicitly designed to do so.

Test meaningful cross-products of state, not only components independently.

## 14. Evidence binding

Never transfer verification evidence across materially different identities without explicit policy support.

Relevant identity includes where applicable:

* Kriya revision;
* tracked-tree state;
* model artifact/digest;
* provider/runtime version;
* inference settings;
* operator config;
* qualification identity;
* platform/runtime environment;
* test/case-set version.

A reporting-only change may be behaviorally harmless, but evidence must still say precisely which revision actually ran each gate.

## 15. Never weaken acceptance criteria to obtain green

Do not:

* change test cases because the implementation fails them;
* remove assertions without proving they are invalid;
* rerun stochastic/live failures repeatedly until one passes;
* alter model settings silently;
* suppress scanner/static-analysis failures;
* reinterpret failed evidence as passed evidence.

Preserve failed attempts as historical evidence.

## 16. Live-model failure policy

A live-model failure must be classified before rerunning.

Classify as one of:

* Kriya defect;
* model behavior;
* provider/runtime behavior;
* prompt/context issue;
* environment/tooling;
* test/harness defect;
* unknown.

If it exposes a Kriya defect:

* deterministically reproduce it;
* fix it;
* add permanent regression coverage.

Do not rerun the unchanged failed candidate merely hoping for model variance unless the user explicitly requests a variance study.

## 17. Known live defects must become deterministic tests

Use:

`LIVE DISCOVERY → CLASSIFY → DETERMINISTIC REPRODUCER → FIX → PERMANENT REGRESSION`

A known Kriya bug must not remain testable only through an expensive live model.

## 18. Progressive verification

Do not run expensive gates after every edit.

### Level 0 — every edit

* syntax/import;
* focused regression;
* focused static/lint where useful.

### Level 1 — logical fix

* regression;
* mutation;
* adjacent subsystem tests.

### Level 2 — coherent repair batch

* broader architecture/subsystem suite.

### Level 3 — batch boundary

* full pytest once after all known related fixes are complete.

If full pytest reveals related defects:

* fix them with focused tests;
* rerun full pytest after the repair batch, not after every edit.

### Level 4 — release candidate

* certification;
* doctor;
* scanner;
* release integrity.

### Level 5 — production evidence

* canary;
* live matrices;
* hosted certification.

## 19. Do not parallelize dependent investigations

Independent long-running measurements may run separately.

But do not simultaneously:

* clean evidence;
* modify the suspected subsystem;
* change measurement code;
* classify the same finding;
* run a diagnostic whose baseline is being altered.

When one activity can affect another's evidence, serialize them.

## 20. Preserve pre-fix evidence

Never overwrite the evidence that exposed a defect.

Keep:

* pre-fix reproducer;
* logs;
* measurements;
* failed certification/matrix/canary results;
* candidate identity;
* post-fix comparison.

The post-fix result supplements the failed evidence; it does not replace it.

## 21. Resource/leak investigations

For a suspected leak:

1. establish idle baseline;
2. run repeated identical workloads;
3. separate warm-up from steady-state behavior;
4. identify resource ownership;
5. inspect monotonic slope, not one before/after sample;
6. trace allocation/creation and release path;
7. distinguish expected bounded caches/residency from unbounded growth;
8. reproduce abnormal paths: failure, timeout, cancellation, interruption where relevant.

Classify:

* `KRIYA_PRODUCT_LEAK`
* `TEST_HARNESS_LEAK`
* `CONTAINER_HARNESS_LEAK`
* `MODEL_RUNTIME_RESIDENCY`
* `EXPECTED_BOUNDED_CACHE`
* `OS_TOOLCHAIN_ARTIFACT`
* `UNKNOWN`

Do not call something a leak merely because memory does not return to zero.

## 22. Commit discipline

Before committing a defect fix, report:

`Root cause: CONFIRMED`
`Original symptom reproduced pre-fix: YES`
`Predicted fix effect:`
`Original symptom remeasured post-fix: PASS`
`Regression: PASS`
`Mutation/negative control: PASS / N/A with reason`
`Adjacent tests: PASS`
`Lint/static: PASS`
`Registry row: CLOSED / N/A`

Do not commit a speculative fix as complete.

## 23. Push discipline

Do not push unless the user has explicitly approved pushing.

Do not interpret permission to commit as permission to push.

Keep certified executable revisions distinct from later evidence-only commits.

## 24. Reporting format for findings

For every meaningful new finding, report:

### Observation

What was directly observed.

### Evidence status

`MEASURED / TRACED / INFERRED / CONFIRMED`

### Producer

Exact code/tool/path responsible for producing the observation.

### Competing explanations

Reasonable alternatives considered.

### Discriminating check

What proves one explanation over the others.

### Root cause

Only after confirmed.

### Predicted fix effect

Observable result expected.

### Post-fix result

Original measurement repeated.

### Verification

Regression / mutation / subsystem / lint results.

### Remaining uncertainty

Anything not actually proven.

The user should not have to ask which statements were measured versus inferred.

## 25. Session-start rule

At the beginning of every new coding session:

1. read this standing instruction;
2. inspect current branch/HEAD/cleanliness;
3. read relevant handover/decision files;
4. identify what evidence belongs to the current exact revision;
5. state the immediate task boundary internally before changing code.

Do not rely on memory alone for repository state.

## 26. Session-end rule

Before ending a session:

* state exact HEAD;
* tracked-tree status;
* commits made;
* tests actually run;
* tests NOT run;
* open confirmed defects;
* unresolved hypotheses;
* evidence locations;
* whether anything was pushed;
* precise next action.

Never imply a gate ran when it did not.

## Governing principle

Kriya is now sufficiently complex that a plausible explanation is not enough.

> Measure what happened.
> Trace why it happened.
> Distinguish fact from inference.
> Prove the root cause.
> Predict the fix.
> Re-measure the original symptom.
> Then trust the tests.

