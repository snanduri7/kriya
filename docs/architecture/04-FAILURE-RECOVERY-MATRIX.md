# 04 - Failure / recovery matrix

Canonical runtime baseline: commit `3eded93c0cfb50f8855d04510e3f9aa2cd5208f7`, tree
`97a3da6b2ef664e2b7e25c4c6fe3fc3f42f253a0`. AS-IS.

Columns: where the failure comes from, the typed evidence it carries, who decides what it means, whether it may be
retried, who repairs, what a person may decide, what may be mutated, and how the unit ends if nothing else changes.

## Slice A1 - how each failure class enters retry accounting

A1 covers the accounting columns only (open finding, family, charge, stop). Which file is repaired and by whom
(the attribution, owner-recovery and verifier columns) is filled by slices A2 and A3. All rows are `TRACED` from
`workflow/open_findings.py`, `workflow/recovery_coordinator.py::classify_attempt_exception`,
`workflow/retry_strategy.py::_record_attempt_failure` and `workflow/retry_policy.py`.

| ID | Failure source (types) | Typed evidence | Replaces the open finding? | Failure family | Charge | Retry eligibility | Terminal outcome if it persists |
|---|---|---|---|---|---|---|---|
| FR-A1-01 | Validator finding about the candidate: compile, tests, targeted test, runtime verification, static rule, goal-spec, regression, and any validator type not listed below | `Failure` with type, raw validator output, file locations | Yes, when authoritative | Its own signature; a new one resets scoped budgets and the exposing targeted attempt is not charged | The mode it ran in | Targeted while implicated files and targeted budget remain, then fallback-targeted once, then full-set | `STOP_EXHAUSTED` at a budget or the ceiling, or a no-progress stop |
| FR-A1-02 | Delivery failure: `anchored_edit`, `attribution_rejected`, `diagnosis_mismatch`, `misdirected_edit`, `no_op_edit`, `operation_contract`, `structural_corruption`, `unaddressed_error_location` | `Failure` with a reason code | No | Inherits the previous family | The mode it ran in | Same routes; a pre-inference capability refusal (`ANCHOR_CONTEXT_NOT_ESCALATED`) forces a strategy change at once | Same |
| FR-A1-03 | Advisory failure (authority not "authoritative") | `Failure.authority` | No; the previous primary failure stays the active one | Inherits | The mode it ran in | Same routes | Same |
| FR-A1-04 | Resource refusal before inference: `context_budget_unsatisfiable`, `output_budget_unsatisfiable` | Reason code and the budget decision | No | Inherits (the run-10 correction) | The mode it ran in | Continues; a fallback with another window may be tried | `STOP_EXHAUSTED` |
| FR-A1-05 | Unclassified exception: `general_error` | Raw text only | No | Inherits | Full-set unless it ran in another mode | Continues | `STOP_EXHAUSTED` |
| FR-A1-06 | Internal Kriya error (`UnboundLocalError`, `TypeError`, `KeyError`, `AssertionError`, `AttributeError`, `NameError`) | Exception class | No | Inherits | Charged, then stopped | None: sets `environment_failure` | `STOP_ENVIRONMENT` on the first occurrence |
| FR-A1-07 | Typed stops listed in `_record_attempt_failure` (time budget, verification infrastructure, containment setup, unattributed regression, fallback incompatible, edit protocol unsatisfiable, provider contract, unqualified retry identity, unresolved requirements, contract registry, static analysis blocked / unknown / unavailable, workspace commit, unsupported file integrity, verification tree mutated, verifier semantic authority required) | Typed failure, usually with a reason code | By the general rule of `AUTH-A1-01` for its type | By the general rule | Charged, then stopped | None | `STOP_ENVIRONMENT` |
| FR-A1-08 | Policy denial that cannot be grounded to an existing owner (`PolicyDeniedError`) | The policy result's reason code | No (`general_error` type) | Inherits | **Not charged** | None | `STOP_ENVIRONMENT` with `UNAUTHORIZED_GENERATION_TARGET` |
| FR-A1-09 | Scope denial grounded to an existing owner, and every attribution result that needs a file outside the unit's write scope | `plan_scope_conflict` with the required files | No (`plan_scope_conflict` is a non-candidate type) | Inherits | **Not charged** | Not a retry: the unit stops and the enforce loop may revise scope or reopen the owner (`BYP-A1-01`) | Unit ends with a scope conflict |
| FR-A1-10 | Candidate-independent deterministic failure (same failure after a materially different candidate, reproduced on the pre-candidate baseline) | Baseline replay output | As its underlying type | As its underlying type | Charged, then stopped | None | `STOP_ENVIRONMENT` with `CANDIDATE_INDEPENDENT_DETERMINISTIC_FAILURE` |
| FR-A1-11 | Consecutive attempts without progress reach the limit; or a declared no-progress stop (verification-only retry with no possible change, failed verifier replay, failed verifier repair) | Progress vector digest; reason code | - | - | Charged | None | No-progress stop |
| FR-A1-12 | API-contract violation detected | Violations, owners, removed signatures | By the type of the failure that carried it | By that failure | Its own counter (3) | Recovery mode until restored or its budget is spent; then hand-back to the ordinary routes under the same ceiling, or stop if not restored | `STOP_EXHAUSTED` when unrestored |
| FR-A1-13 | Generated-verifier ruling (a failing run-authored verifier) | The verifier semantic ruling | Slice A2 | Slice A2 | Charged by the ordinary rule | Replaces attribution: stop, one regeneration, or an owner selected by the strict predicate | Needs-review stop or the ordinary routes |
| FR-A1-14 | Repository content filter refused; membership authority could not list the tree (`RepositoryContentFilterRefused`, `BoundTreeError`) | Typed exception | Not recorded as an attempt failure | - | Not charged | None: the exception leaves the loop | Typed stop of the run |
| FR-A1-15 | Cancellation or interruption (not an `Exception`) | - | Not recorded | - | Not charged | None | Propagates; the run record is closed by the run lifecycle (slice A3) |

Human authority in the retry loop: none. A person acts only at the approval gate, the verifier semantic review
and the resume request, which belong to slices A2 and A3 and to `AUTH-A1-09` / `AUTH-A1-10`.
