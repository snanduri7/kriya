# 09 - Integration contracts (the contract between stages)

Canonical runtime baseline: commit `3eded93c0cfb50f8855d04510e3f9aa2cd5208f7`. A derivative view: every AS-IS
statement cites the row that holds it; the AS-IS trace of each boundary is in `06-GAP-AUTHORITY-AUDIT.md` section 10
(boundary numbers in brackets). The last field of each contract is `PROPOSED` and points to `07-REQUIRED-CONTRACTS.md`.

Fields of every contract: producer; consumer; payload / state; identity / fingerprint binding; serialization /
persistence boundary; trust boundary; validation at handoff; allowed transformations; forbidden recomputation;
failure propagation; retry / replay semantics; stale-state protection; bypasses; evidence; REQUIRED contract.

## IC-01 Goal -> Semantic contract [1, 2]

| Field | Content |
|---|---|
| Producer | The operator's goal; `requirement_contract.py::requirement_set_for` (`AUTH-A4-01`, `AUTH-A4-02`) |
| Consumer | `contract_compilation.py::compile_verification_contract` (`AUTH-A2-02`) |
| Payload / state | The requirement set: ids, verbatim sentences, kinds |
| Identity binding | `RequirementSet.digest` (and `goal_digest`) joins the sealed contract, verdicts, approvals, `ControlState.requirement_contract_digest` |
| Persistence boundary | Content-addressed files under the state directory (`MUT-A2-01`, `MUT-A4-09`) |
| Trust boundary | User words and operator files only; no model text crosses |
| Validation at handoff | Format, ids, goal binding (`FR-A4-01`) |
| Allowed transformations | Splitting the goal into sentences; classifying claim kinds and strength |
| Forbidden recomputation | A later stage deriving requirements again from a unit's composed goal |
| Failure propagation | Typed refusal before any model call (`FR-A4-01`, `FR-A4-04`) |
| Retry / replay | None |
| Stale-state protection | Digest drift refuses resume |
| Bypasses | `BYP-A4-12`, `BYP-A4-13` (`kriya fix`) |
| Evidence | The sealed files |
| REQUIRED | C-01 (semantic state per requirement); C-02 clause for a fix run (INT-05) |

## IC-02 Semantic contract -> Planner [3]

| Field | Content |
|---|---|
| Producer | The sealed contract |
| Consumer | The Planner request builder; `validate_plan(known_requirement_ids=...)` (`AUTH-A4-10`); `classify_plan_completeness` (`AUTH-A4-11`) |
| Payload / state | Requirement ids and text; the runtime-required reading (`AUTH-A4-13`) |
| Identity binding | Requirement ids; the plan hash once a plan is accepted |
| Persistence boundary | The approved-plan document; the `plan` checkpoint |
| Trust boundary | The Planner's answer is model text: evidence, validated, never authority |
| Validation at handoff | Enforce: structural validation with repairs. Direct: completeness only (`SM-A4-01`, `SM-A4-02`) |
| Allowed transformations | Deterministic normalization and canonicalization (create or modify from file existence) |
| Forbidden recomputation | The Planner redefining a requirement; a second validator giving another verdict |
| Failure propagation | `FR-A4-06..10`: repair, then a typed stop |
| Retry / replay | Two repairs; three deterministic revisions during execution (`BYP-A4-11`) |
| Stale-state protection | The plan hash on resume (`GAP-A4-06`) |
| Bypasses | The direct shape validates no structure |
| Evidence | `structured_plan_*` ledger records |
| REQUIRED | C-02 truth (1); C-01; INT-02 (one executed-plan identity) |

## IC-03 Semantic contract -> Developer [4]

| Field | Content |
|---|---|
| Producer | The plan's criteria, invariants and decisions (`AUTH-A4-12`); the goal |
| Consumer | The Developer request (`AUTH-A6-09`) |
| Payload / state | The unit goal, the semantic context block, the constraint context |
| Identity binding | Criterion ids and exact text (they also enter the verifier semantic subject) |
| Persistence boundary | The plan document; rebuilt per request |
| Trust boundary | The system prompt states that the Planned Implementation Strategy is approach, not requirement |
| Validation at handoff | `validate_plan`; rendering omits an id that does not resolve (INT-08) |
| Allowed transformations | Rendering to text; fitting optional sections |
| Forbidden recomputation | The Developer's answer defining what the requirement is |
| Failure propagation | An oversized request is refused (`FR-A6-08`) |
| Retry / replay | The retry builders re-compose the task |
| Stale-state protection | Rebuilt from the plan each time |
| Bypasses | The direct shape has no semantic context: prose plan and design only |
| Evidence | `prompt.sections`, the request blob |
| REQUIRED | C-01 (clauses reach the Developer as the contract's, not the Planner's); INT-08 |

## IC-04 Semantic contract -> Verifier [9, 10]

| Field | Content |
|---|---|
| Producer | Nothing before generation (`AUTH-A2-05`); after it the run-authored test |
| Consumer | `derive_executed_verifiers` (`AUTH-A2-06`); the person's review (`AUTH-A2-07`) |
| Payload / state | The verifier subject |
| Identity binding | `semantic_authority_fingerprint` (run, plan hash, unit, bytes, criteria) |
| Persistence boundary | The ledger, memory only |
| Trust boundary | A model wrote the expectation; only a person gives it authority |
| Validation at handoff | A complete report, identity mapping, the write journal |
| Allowed transformations | One regeneration after an invalid decision |
| Forbidden recomputation | Treating a passing generated test as proof of the requirement |
| Failure propagation | Unattended or undecided: needs review (`FR-A2-01..11`) |
| Retry / replay | `AUTH-A2-13` |
| Stale-state protection | A decision for other bytes is stale |
| Bypasses | `BYP-A2-01..03` (unmapped identities), `BYP-A2-07` |
| Evidence | The review record |
| REQUIRED | C-01: a contract exists first; unmapped is held; the person decides only what the contract leaves open |

## IC-05 Candidate -> Review [16, 17, 18]

| Field | Content |
|---|---|
| Producer | `CandidateTransaction` (`AUTH-A3-01`) |
| Consumer | `approval_gate` (`AUTH-A3-03`), the person (`AUTH-A3-04`) |
| Payload / state | A diff rebuilt from the sandbox; the Reviewer's text |
| Identity binding | Desktop: a digest of the presented diffs. CLI: none. Held execution: the candidate binding (`AUTH-A3-05`) |
| Persistence boundary | The launch record (desktop); an event (CLI) |
| Trust boundary | The Reviewer's text is advisory (`AUTH-A3-12`) |
| Validation at handoff | Four triggers; an invalid pattern is skipped (INT-09) |
| Allowed transformations | Text rendering of the diff |
| Forbidden recomputation | Rebuilding the subject instead of using the bound batch (what happens today) |
| Failure propagation | `FR-A3-06`: no decision is never approval |
| Retry / replay | A resumed candidate is asked about again |
| Stale-state protection | `policy/approval.py::stale` during held execution |
| Bypasses | `BYP-A3-01..06`, `BYP-A3-16` |
| Evidence | `approval.decision` |
| REQUIRED | C-03 |

## IC-06 Candidate -> Gates [8, 20]

| Field | Content |
|---|---|
| Producer | The staged candidate and its digests |
| Consumer | `gate_orchestrator.py`; `TerminalGateService` (`AUTH-A3-07`, `AUTH-A3-08`) |
| Payload / state | Sandbox files; the tree binding |
| Identity binding | `VerificationTreeBinding`; the verified candidate binding (`AUTH-A3-02`) |
| Persistence boundary | Memory; gate outcomes in the trace row and evidence |
| Trust boundary | Model-written code is executed only inside containment |
| Validation at handoff | The worktree must match the candidate before a gate runs |
| Allowed transformations | Deterministic gate-side corrections through the write gate (`BYP-A3-10`) |
| Forbidden recomputation | A gate reading files from anywhere but the bound tree |
| Failure propagation | A `Failure` to the retry stage; an environment result stops the run |
| Retry / replay | The retry stage; terminal regression always re-runs on resume |
| Stale-state protection | The binding is cleared at each attempt start |
| Bypasses | `BYP-A3-06`, `BYP-A3-09` |
| Evidence | `gate.result` |
| REQUIRED | C-13 (every refusal typed); C-11 (purpose) |

## IC-07 Verifier -> Attribution [11]

| Field | Content |
|---|---|
| Producer | A failing accepted verifier; the semantic ruling (`AUTH-A2-08`, `AUTH-A2-09`) |
| Consumer | `retry_strategy.py` (`MUT-A2-06`); `attribute_failure` when no subject is involved (`AUTH-A2-10`) |
| Payload / state | The ruling: stop, one regeneration, or one production owner |
| Identity binding | The unique locator artifact and the validated plan's owner |
| Persistence boundary | Memory |
| Trust boundary | The ruling replaces model attribution |
| Validation at handoff | A unique non-verifier workspace member and exactly one owner |
| Allowed transformations | None |
| Forbidden recomputation | A model tier overriding the ruling |
| Failure propagation | No unique owner: needs review |
| Retry / replay | `FR-A1-13` |
| Stale-state protection | N/A - per failure |
| Bypasses | `BYP-A2-09` (a direct run has no plan, so no owner) |
| Evidence | `diagnosis` |
| REQUIRED | C-01; C-11 (labels) |

## IC-08 Attribution -> Retry / Recovery [12, 13, 14]

| Field | Content |
|---|---|
| Producer | The `Failure`, its attribution and files |
| Consumer | `open_findings.py`, `retry_policy.py` (`AUTH-A1-01..07`); the enforce loop for a scope conflict (`SM-A1-04`) |
| Payload / state | The open finding; the family signature; the stop reason as a prefixed string (INT-01) |
| Identity binding | None for the blocker (`AUTH-A1-02`); a text signature for the family (`AUTH-A1-12`) |
| Persistence boundary | Memory only |
| Trust boundary | Validator text only; fetched text is concatenated into the same string (R-10) |
| Validation at handoff | `replaces_open_finding`; `observe_failure_family` |
| Allowed transformations | Normalizing the failure text for the signature |
| Forbidden recomputation | A consumer decoding a stop reason from message text (what happens today) |
| Failure propagation | `FR-A1-01..22` |
| Retry / replay | `SM-A1-01` |
| Stale-state protection | None: nothing is persisted |
| Bypasses | `BYP-A1-01..14` |
| Evidence | `retry.progress_vector`, `recovery.decision` |
| REQUIRED | C-07; INT-01 clause (typed stop reason) |

## IC-09 Recovery -> Candidate [15]

| Field | Content |
|---|---|
| Producer | The recovery execution plan |
| Consumer | A new unit invocation; the owner-recovery self-correction |
| Payload / state | Owner per artifact; obligations (`MUT-A1-18`) |
| Identity binding | Recovery plan id and generation (loop locals) |
| Persistence boundary | None |
| Trust boundary | The reopened owner writes through the same write gate |
| Validation at handoff | The plan-level outcome of the cycle, never the owner's own result (`MUT-A1-16`) |
| Allowed transformations | Invalidation of dependents (`MUT-A1-15`) |
| Forbidden recomputation | N/A - no upstream truth is recomputed |
| Failure propagation | `FR-A1-17..21` |
| Retry / replay | Three cycles per stage |
| Stale-state protection | A repeated convergence state stops typed |
| Bypasses | `BYP-A1-13` |
| Evidence | `plan_recovery_events` |
| REQUIRED | C-07 (recovery state persisted) |

## IC-10 Approval -> Mutation [18, 19]

| Field | Content |
|---|---|
| Producer | The person's decision (`AUTH-A3-04`) |
| Consumer | `commit_terminal_candidate` (`AUTH-A3-14`) |
| Payload / state | Approved or not |
| Identity binding | **None links the approved subject to the committed batch** (`GAP-A3-04`): three identities |
| Persistence boundary | Launch record or event; commit evidence |
| Trust boundary | Only a recorded decision is honoured |
| Validation at handoff | The commit checks the verified batch, not the approved digest |
| Allowed transformations | None |
| Forbidden recomputation | N/A - the gap is a missing comparison |
| Failure propagation | Unrecorded decision: nothing applied |
| Retry / replay | A resumed run asks again |
| Stale-state protection | Held-execution guard only |
| Bypasses | `BYP-A3-01`, `BYP-A3-02`, `BYP-A6-18` (MCP), `BYP-A5-07` (skill writers) |
| Evidence | No record names both |
| REQUIRED | C-03, C-04 |

## IC-11 Apply -> Replay [20]

| Field | Content |
|---|---|
| Producer | A milestone's commit and its persisted verification commands (`MUT-A3-14`) |
| Consumer | `replay_prior_milestone_report` (`AUTH-A3-15`) |
| Payload / state | Commands and expected results |
| Identity binding | The milestone run-state sidecar |
| Persistence boundary | The sidecar |
| Trust boundary | Persisted commands are re-executed under containment |
| Validation at handoff | A replay that did not run is a typed terminal |
| Allowed transformations | None |
| Forbidden recomputation | N/A |
| Failure propagation | `FR-A3-18` |
| Retry / replay | This is the replay |
| Stale-state protection | Runs against the current workspace |
| Bypasses | Enforce has no such phase; `BYP-A3-13` |
| Evidence | The replay report |
| REQUIRED | C-02 |

## IC-12 Replay -> Final result, and requirement closure -> final result [21, 22, 23]

| Field | Content |
|---|---|
| Producer | `requirement_truth` (`AUTH-A3-09`); gate and replay results |
| Consumer | The status rules (`AUTH-A3-10`, `AUTH-A3-11`); `run_coordinator.py` reconciliation |
| Payload / state | A result field and a run event |
| Identity binding | None between the truth and the applied candidate (`BYP-A3-08`) |
| Persistence boundary | Result dict, trace row, launch record |
| Trust boundary | Deterministic only |
| Validation at handoff | **The consumer does not read it** (`GAP-A3-01`) |
| Allowed transformations | None |
| Forbidden recomputation | A display recomputing the outcome (the desktop does, as a labelled projection: `AUTH-A6-12`) |
| Failure propagation | Under `block` a blocking requirement fails the unit |
| Retry / replay | None after an enforce terminal gate failure |
| Stale-state protection | None: the last event wins |
| Bypasses | `BYP-A3-08`, `BYP-A3-12` |
| Evidence | `requirement.truth` |
| REQUIRED | C-02 |

## IC-13 Checkpoint -> Resume [24]

| Field | Content |
|---|---|
| Producer | Stage code; the enforce loop (`MUT-A1-12`, `MUT-A1-13`) |
| Consumer | `validate_resume_against_reality`, `build_resume_plan` (`AUTH-A1-09`); the enforce resume block (`AUTH-A1-10`) |
| Payload / state | Stage artifacts; `ControlState` |
| Identity binding | Workspace, configuration, goal, contract and plan fingerprints |
| Persistence boundary | Under the authority root (the plan sandbox for an enforce unit) |
| Trust boundary | The checkpoint's own claim is never trusted |
| Validation at handoff | Every fingerprint recomputed |
| Allowed transformations | None |
| Forbidden recomputation | Inferring a decision from a checkpoint's existence (`BYP-A4-08`) |
| Failure propagation | `resume_refused`; fresh run with a warning |
| Retry / replay | N/A |
| Stale-state protection | Fingerprints; completion scope |
| Bypasses | `BYP-A1-03`, `BYP-A1-12`, `BYP-A5-14` |
| Evidence | Checkpoint files |
| REQUIRED | C-07; INT-02, INT-06, INT-10, INT-11 clauses |

## IC-14 Tool / MCP -> Workspace mutation [27, 28]

| Field | Content |
|---|---|
| Producer | A planned TOOL subtask (`SM-A5-02`); skill learning (`MUT-A5-08`) |
| Consumer | The workspace or a repository |
| Payload / state | The call's arguments; extracted rules |
| Identity binding | The tool identity and profile digest (TOOL-002); none for the change |
| Persistence boundary | RunRecord `tool_execution`; skill files |
| Trust boundary | A model selects the call and the arguments |
| Validation at handoff | Governance and approval of the tool; none of the mutation |
| Allowed transformations | N/A |
| Forbidden recomputation | N/A |
| Failure propagation | May-have-effected is terminal (`FR-A5-08..11`) |
| Retry / replay | Never retried |
| Stale-state protection | Approval re-read at each call |
| Bypasses | This contract is the bypass (`BYP-A5-03`, `BYP-A5-07`, `BYP-A6-18`) |
| Evidence | `tool.execution`, `PolicyDecisionRecord` |
| REQUIRED | C-04 |

## IC-15 Runtime binding -> Model dispatch [31, 32]

| Field | Content |
|---|---|
| Producer | The role or Developer choke point |
| Consumer | `LLMClient` (`AUTH-A6-01..05`) |
| Payload / state | A model name and some override values |
| Identity binding | Runtime fingerprint and settings digest on the result; the qualification identity is computed from another binding in the same-name case |
| Persistence boundary | Fingerprint files; qualification records outside the workspace |
| Trust boundary | The configuration is operator authority |
| Validation at handoff | Owned request fields; adapter registry; provider plan; window state; dispatch budget |
| Allowed transformations | Per-request window where the adapter takes one; adaptive tier |
| Forbidden recomputation | `LLMClient` re-deriving the binding by name (what happens today) |
| Failure propagation | `FR-A6-01..15` |
| Retry / replay | Role chain; Developer fallback |
| Stale-state protection | Qualification staleness by digest |
| Bypasses | `BYP-A6-01..04` |
| Evidence | `model.request`, `model.result`, `model.transition` |
| REQUIRED | C-09 |

## IC-16 Evidence -> Terminal result [23]

| Field | Content |
|---|---|
| Producer | The run (`AUTH-A6-11`) |
| Consumer | Desktop history and inspector (`AUTH-A6-12`); metrics; release tooling |
| Payload / state | RunRecord, history row, evidence store, run log |
| Identity binding | RunRecord: workspace identity and revision. Evidence: manifest and `run.closed`. History row: none |
| Persistence boundary | Control state; `traces.db`; the evidence directory; the log directory |
| Trust boundary | Records never decide |
| Validation at handoff | Chain verification per read; closed truth vocabulary |
| Allowed transformations | Redaction for display; the application-outcome projection |
| Forbidden recomputation | Stating an outcome from a record that is not the outcome of record |
| Failure propagation | Missing is shown as missing |
| Retry / replay | N/A |
| Stale-state protection | None for the history row |
| Bypasses | `BYP-A6-12..16` |
| Evidence | This is the evidence |
| REQUIRED | C-11, C-02 |
