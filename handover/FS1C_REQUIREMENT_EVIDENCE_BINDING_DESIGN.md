# FS-1C: deterministic requirement-to-evidence binding (DESIGN ONLY)

**Status:** design for owner review. Nothing here is implemented. Builds on FS-1A (`TestExecutionReport`) and FS-1B
(model verdicts authorize nothing).
**Labels:** MEASURED, TRACED, INFERRED, PROPOSED.

## 1. The gap FS-1B leaves open

- **After FS-1B (TRACED), an original requirement closes only through:**
  - the named-test closure: a pre-existing test the requirement names, unchanged, executed and passing;
  - the migration gate;
  - mutation scope.
- **Most feature goals ("add X that returns Y") have none of these.** In production they now block, which is
  correct but not useful.
- **MEASURED: the R2 T5 reference success is now UNVERIFIED and blocked.**
  - Its tests ran and passed (Rule E holds).
  - Its only requirement had just the model's "satisfied".
  - The test it names, `OwnerTests`, was changed by the candidate, so the named-test closure refuses it.
  - Test: `tests/test_fs1a_test_execution_evidence.py::test_the_t5_reference_success_is_unverified_and_blocked_under_the_production_policy`.
- **What is missing:** evidence that is
  - bound to the requirement's own words;
  - executable;
  - not authored or weakened by the candidate;
  - checked deterministically.

## 2. Principles (unchanged by this design)

1. LLM output authorizes nothing. A model may propose, describe or corroborate. Only deterministic evidence or a
   human decision closes a requirement.
2. Candidate-written tests prove those tests ran and passed, not that they express the goal. They never close an
   original requirement on their own.
3. Every closure binds to exact identities:
   - the requirement set digest and the requirement id;
   - the candidate (`evidence_id`);
   - the test source digests;
   - the `TestExecutionReport` (gate id and report sha256);
   - the runner and toolchain identity.
   Any drift invalidates the closure.
4. Deterministic contradiction always wins. VIOLATED blocks, whatever any other record says.

## 3. Proposed evidence sources, in order of authority

### B1. Identity-level named-test closure (strengthens what exists)

- **TRACED weakness.** `close_requirements_with_named_tests` judges "executed" from the console. It calls
  `output_confirms_nonzero_test_execution`, which only checks for a nonzero count in a run selecting the named file.
- **PROPOSED.** Require from the run's `TestExecutionReport`:
  - the report is COMPLETE;
  - every test identity of the named file at the base revision (or the named identity, e.g. `PricingTest#testX`)
    executed and passed;
  - the file digest equals the base.
- **Typed refusal on failure:** `NAMED_TEST_NOT_EXECUTED` / `NAMED_TEST_EVIDENCE_INDETERMINATE`.
- **Evidence binding:** the gate id and report digest are added to the closure record.

### B2. User-authored executable acceptance in the goal

- **Form.** The goal may carry a fenced, machine-readable block, for example:
  ```
  ```kriya-acceptance REQ-2
  assert contains_only("hello", "") is False
  ```
  ```
- **Parsing.** Deterministic, never by a model:
  - a closed grammar: language tag plus requirement id;
  - the block text is opaque; Kriya never rewrites it.
- **Execution.** Kriya writes the block into a Kriya-owned test file outside every candidate write scope
  (`.kriya/acceptance/<run>/...`) and runs it through the same gate. The identities come from the
  `TestExecutionReport`.
- **Authority.** The user's words. A missing, unparseable or non-executing block is UNVERIFIED with a typed reason,
  never closed.
- **Why it is safe.** The candidate can neither author nor change the check. It is bound by the block digest inside
  the requirement set digest.

### B3. Human-approved acceptance tests (human authority)

- **Command.** `kriya requirements bind <REQ-id> <test-identity...>` writes a digest-bound approval artifact outside
  the workspace (the SEC-009 / TOOL-002 pattern).
- **What it binds:** workspace id, requirement set digest, requirement id, test file path and digest, and identities.
- **When it closes.** At the terminal gate, only when:
  - those exact identities executed and passed in a COMPLETE report on the final candidate;
  - the file digests still equal the approved digests.
  Then it is recorded with `authority=HUMAN`.
- **Candidate-written tests are allowed here,** but only after a human reviewed and approved them, in
  human-in-the-loop mode. The approval is the authority, not the test's existence.
- **Revocation and drift.** Any digest change invalidates the approval, as in TOOL-002 P2.

### B4. Model-generated oracle tests: corroboration only, never closure

- A separate verifier role may write tests before generation, hidden from the Developer.
- Their results are recorded as `MODEL_CLAIMED`-class corroboration: diagnostics and retry evidence.
- They never close a requirement (Principle 1). Listed so it is explicitly ruled out as an authority.

## 4. State machine (requirement outcome; B1–B3 only add closure producers)

```
PENDING -> (verdict) -> UNVERIFIED (model claim or insufficient evidence) | VIOLATED (model or deterministic) | UNKNOWN
UNVERIFIED -> CLOSED_BY_EVIDENCE   only via record_requirement_closure from B1 / B2 / B3 / migration / mutation scope,
                                   for the same evidence_id
any -> VIOLATED                    deterministic counter-evidence (unchanged)
CLOSED_BY_EVIDENCE -> UNVERIFIED   when the candidate changes (a new evidence_id: closure never carries over - existing)
```

No new outcome values. Each closure method gets its own `method` string (`named_test_identities`,
`goal_acceptance_block`, `human_bound_tests`) and its own evidence fields.

## 5. Required tests and mutants (when implemented)

- **Tests:**
  - B1:
    - a named file whose identity did not execute → refused;
    - a stale report → refused;
    - a changed file → refused.
  - B2:
    - a block that fails, does not collect, or is malformed → UNVERIFIED;
    - a block written by a candidate (any path outside `.kriya/acceptance`) → ignored;
    - a passing block closes only its own REQ.
  - B3:
    - an approval with a drifted digest → refused;
    - approval of identity X does not close REQ-2;
    - revocation is effective without a restart.
  - For every source: model "satisfied" alone stays UNVERIFIED, and a deterministic VIOLATED wins.
- **Mutants:**
  - console count accepted as named-test evidence;
  - B2 block read from a candidate path;
  - B3 approval matched by name instead of digest;
  - closure carried over to another candidate;
  - model corroboration (B4) recorded as closure.

## 6. Decisions needed from the owner

1. Which of B1/B2/B3 to build, and in what order. Recommendation: B1 first (small; it removes a console-count
   dependency), then B2, then B3.
2. B2 syntax: a fenced block in the goal, or a separate `--acceptance <file>` operator input. The file form is easier
   to review; the block form keeps the goal self-contained.
3. Whether B3 is allowed outside `human-in-the-loop` mode (recommendation: no).
4. Whether production should keep blocking model-only requirements until B2/B3 exist (current FS-1B behaviour,
   recommended), or temporarily surface them as `NEEDS_REVIEW` without applying.

## 7. Out of scope

- P2/P3.
- The negative-model path (T1).
- Any EXECUTED_CANDIDATE_TESTS → requirement closure (rejected by the owner).
- Live runs.
