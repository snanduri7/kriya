# LR-R1-M1 Attempt Evidence Recorder - Final Certification

Date: 2026-10-05. Labels: MEASURED (observed in this certification), TRACED (read in the code), INFERRED, NOT_MEASURED.
Every result below was produced at FINAL HEAD. This report and its evidence files are committed afterwards in a
separate evidence-only commit, so the certified executable revision stays distinct (ENGINEERING_RULES §23).

```text
BASE
61a867fc31a7b5ff5d7f21d1ea15e4eac43f2e03   (= origin/main)

FINAL HEAD
58853ad3772cbc936b59904b85f44a4adbbebe56   (branch feature/lr-r1-m1; worktree ~/kriya-wt/lr-r1-m1; not pushed)
```

## COMMITS

In order. Four commits are fixes of my own earlier work, each committed separately with a regression that fails
without it.

| # | Commit | Step |
|---|---|---|
| 1 | `25ef6f7` | M1.0 design, investigation notes, executable probes |
| 2 | `41ce8c3` | M1.1 store: schema, append-only writer, reader/verify |
| 3 | `6527ccf` | M1.2 scopes, run/unit/attempt lifecycle, config section, I-2 skeleton |
| 4 | `00395e7` | M1.3a mirror existing evidence streams |
| 5 | `2949742` | fix (own M1.2): pin git commit dates in the I-2 harness |
| 6 | `5af78c2` | M1.3 prep: fake state gains `record_gate_outcome` |
| 7 | `4cd41bc` | M1.3b 85-site gate-outcome conversion (mechanical only) |
| 8 | `bb5e811` | M1.3c tripwire: one gate-outcome producer |
| 9 | `fbc7daf` | M1.4 `wire_payload` + untrimmed transport content |
| 10 | `47b2eb1` | M1.5 `model.request` / `model.response` / `model.result` |
| 11 | `e42517a` | M1.6 `prompt.sections` |
| 12 | `7303f6c` | M1.7 `authority.snapshot`, `developer.parse` |
| 13 | `012f291` | M1.8a `candidate.change`, `gate.result`, `obligations.snapshot` |
| 14 | `f3e509b` | M1.8b `diagnosis`, `recovery.decision`, `fallback.decision`, `retry.delta` |
| 15 | `f8c4376` | M1.8b D7 typed reason codes (separate commit) |
| 16 | `70d9a63` | M1.9a retention |
| 17 | `4d4f90f` | fix (own M1.2 defect): attempt identity spans the whole retry-loop iteration |
| 18 | `a9be11b` | M1.9b `kriya evidence show/explain/verify/prune` |
| 19 | `c836c11` | M1.9c doctor row |
| 20 | `1e62d5a` | M1.9d T15 layout tripwire + docs |
| 21 | `3ec3cc0` | M1.10 I-2 over enforce/fallback/resume/Planner repair; overhead |
| 22 | `09a1ccf` | fix (own M1.9c defect): two structural tests |
| 23 | `90f9c44` | M1.11 legacy importer |
| 24 | `74796bd` | D7 review: dedicated `StopReasonEvidence` carrier |
| 25 | `58853ad` | OBS-1: native request-body parity + exact pin (test-only) |

## DESIGN DEVIATIONS

Full text: `handover/LR_R1_M1_0_INVESTIGATION_NOTES.md` §7.

| ID | Deviation | Status | Why acceptable |
|---|---|---|---|
| DEV-1 | A non-attempt call's phase comes from the role the code already set (`role_metrics.model_role`), not from per-phase scopes | ACCEPTED | Identity still comes from code, never from the caller or text; no per-phase producer edits were needed |
| DEV-2 | The goal is recorded in `unit.opened`, not `run.opened` | ACCEPTED | The run scope opens before any goal is known, and the units of one run can have different goals |
| DEV-3 | The `evidence` config section is excluded from every execution-identity fingerprint | ACCEPTED, REQUIRED by I-2 | MEASURED: before this, the RunRecord fingerprint differed between capture modes (an I-2 violation). Pinned by `test_capture_mode_is_part_of_no_execution_identity`, with an ordinary setting as negative control |
| DEV-4 | The store opens lazily, at the first scope that knows the config | ACCEPTED | `begin_mutating_run` has no config; a store that cannot open is RECORDER_UNAVAILABLE (D5) |
| DEV-5 | The equivalence suite measures volatile values (two `off` runs) instead of listing them | ACCEPTED | Measured, not guessed; it masks only what differs between identical runs |
| DEV-6 | Raw content is captured inside the adapters (`ChatResponse.raw_content`) | ACCEPTED, REQUIRED by D3 | TRACED: both adapters strip content before `LLMClient` sees it. Additive field; `content` is unchanged |
| DEV-7 | D7 codes are not in `Failure.diagnostics`; they live in a dedicated evidence-only carrier | ACCEPTED, REVIEWED at certification | `diagnostics.reason_code` feeds the ProgressVector (a decision input). Renamed from `environment_failure_code` to `StopReasonEvidence` (`74796bd`), see D7 below |
| DEV-8 | `retry.delta` is emitted at the retry's first Developer request, or at attempt close as UNKNOWN | ACCEPTED | The inputs it compares do not exist when the attempt opens |
| DEV-9 | `RecoveryDecision.retry_decision` is `compare=False` | ACCEPTED | Additive field; the decision is still `stop_loop`/`action`/`budgets_exhausted`, and the PRD-031 equality tests are unchanged |

None is unresolved.

## OBS-1

```text
CLOSED
```

Proof: `tests/test_lr_r1_m1_native_wire_parity.py` (9 tests, MEASURED passing at FINAL HEAD).
- **Setup.** A real `LLMClient`, the real `OllamaNativeRuntimeAdapter` and real HTTP to a loopback `/api/chat`
  server.
- **Parity.** Every recorded `model.request` `wire_body` equals, one for one and in order, the body the server
  received, as parsed structures and as canonical bytes.
- **Request shapes covered.** Plain, streamed, JSON mode, schema-constrained and tool-call requests, plus both internal
  resends, `empty_content_floor` and `response_format_dropped` (two labelled wires each).
- **Digest mode.** `digest_only` records the digest of the exact body sent.
- **Exact pin.** The plain native body is pinned key by key. This was OBS-1's original gap: an unknown key used to
  pass every suite.
- **Negative controls.** Each of these makes the suite fail:
  - the posted body diverging, on the non-stream, stream and tool paths;
  - an unknown key added to the body;
  - the recorder using the wrong stream flag;
  - an unlabelled resend.

Test-only; no provider semantics changed.

## D7

**Final storage mechanism.** `GenerationState.stop_reason_evidence: Optional[StopReasonEvidence]`, where
`StopReasonEvidence(code, message)` is a frozen dataclass in `kriya/workflow/diagnosis_codes.py`. Neither stop is an
environment failure; both only reuse `state.environment_failure` as the stop channel. The messages are byte-identical
to before.

All readers and writers (TRACED):

| Site | Role |
|---|---|
| `kriya/workflow/state.py`: field declaration | declares only, never reads |
| `kriya/workflow/retry_strategy.py`: `StopReasonEvidence(REGRESSION_UNATTRIBUTED, ...)` | writer, set only when `failure.type == "regression_unattributed"` and a stop message exists |
| `kriya/workflow/retry_strategy.py`: `StopReasonEvidence(NO_AUTHORIZED_REPAIR_TARGET, ...)` | writer, beside the unchanged message |
| `kriya/workflow/recovery_coordinator.py::_typed_stop_reason` | the only reader: returns the code only while its message is still the current stop message |
| `_record_recovery_decision` | the only consumer: the `stop_reason_code` key of the `recovery.decision` evidence payload |

**Proof that no decision consumes it.** `tests/test_lr_r1_m1_reason_codes.py`:
- **Producers.** An AST walk over `kriya/` requires that only the producers set the field, name the codes or import
  `diagnosis_codes`.
- **Declaration.** `state.py` may only declare the carrier type.
- **Reader.** `_typed_stop_reason` is the only reader.
- **Negative control.** A planted read inside `decide_for_state` fails the walk.
- **Mutants killed.** A planted decision read; `state.py` importing a code; a stale code being reported; a dropped
  producer; the code being set on any stop.
- **Retry, recovery, fallback, progress vector, terminal outcome, planning, review, RunRecord and commit:** none
  reads the field.
- **Serialization.** `GenerationState` is not serialized wholesale (TRACED).

## FULL TEST SUITE

```text
command   cd ~/kriya-wt/lr-r1-m1; ulimit -n 256;
          PYTHONPATH=$PWD <main checkout>/.venv/bin/pytest -q -n 8 --dist loadgroup
          (the worktree has no .venv of its own; the main checkout's venv with PYTHONPATH pointed at the
          worktree is how every run in this work was made)
passed    8608
failed    0
errors    0
duration  533.33 s (wall 535 s)
```

Evidence: `handover/evidence/lr-r1-m1/final_full_suite_summary.txt`. No exclusions, single run, at FINAL HEAD.

## RUFF

`ruff 0.16.0`, `ruff check .`: **PASS** (exit 0, zero findings).

## PYLINT

`pylint kriya plugins/core_tools tests`: **PASS** (exit 0).

## MUTATION TESTS

Consolidated campaign at FINAL HEAD: `handover/evidence/lr-r1-m1/m1_mutation_campaign.py`, with results in
`m1_mutation_results.json` and `m1_mutation_campaign.log`.
- **Method.** Each mutant runs every `tests/test_lr_r1_m1_*.py` (baseline: 179 passed). The file is restored and
  checked clean after every mutant.

```text
mutants run     52
mutants killed  52
survivors       0
```

Coverage by guarantee:

| Guarantee | Mutants |
|---|---|
| hash chain | 5 |
| capture mode | 1 |
| file permissions | 1 |
| raw response / reasoning | 2 |
| identity | 2 |
| prompt / request parity | 5 |
| authority | 1 |
| parse | 1 |
| candidate diff | 3 |
| gate attribution | 3 |
| retry delta | 3 |
| fallback | 3 |
| recovery decision | 2 |
| recorder-failure non-interference | 2 |
| I-2 planted effects | 2 |
| retention | 5 |
| I-1 | 2 |
| I-3 | 4 |
| D7 | 3 |
| explain | 2 |

Earlier per-commit mutation rounds (listed in each commit message) found these survivors:
- **Weak tests, then strengthened:**
  - M1.8a terminal gates;
  - M1.8b evidence class, call substitution and refused-call record;
  - M1.9a symlink and age guard;
  - M1.9b Q5, Q6 and Q7;
  - M1.11 absence list.
- **Redundant code, then removed:**
  - M1.8b `delta_emitted`;
  - M1.9b's payload-attempt fallback.

No unclassified survivor remains.

## I-1

```text
PASS
```

- `tests/test_lr_r1_m1_layout_tripwire.py`: no code in `kriya/` (outside `attempt_evidence/`), `scripts/` or
  `benchmarks/` names the store files together with the store, or `blobs/`. Planted negative controls are included.
- `test_no_production_code_reads_the_store`: the reader's consumers are only the CLI, `explain.py` and the doctor.
- `test_metrics_never_import_the_recorder`.

5 passed (MEASURED).

## I-2

```text
PASS
```

`tests/test_lr_r1_m1_equivalence.py` and `tests/test_lr_r1_m1_equivalence_scenarios.py`, 9 passed (MEASURED).
- **Variants.** `off` ×2, `full`, `digest_only`, store open refused, every append failing.
- **Scenarios.** Direct success; retried until the no-progress stop; fallback refused; fallback substituted at the
  call; resume; enforce success; enforce refused verified-no-change; enforce Planner repair.
- **Identical across variants.** Request bytes, workspace bytes, result, trace run events (minus the pointer),
  RunRecords and the decision ledger.
- **Planted recorder effects.** A candidate byte, the prompt, and the retry budget: each is caught (also in the
  mutation campaign).

## I-3

```text
PASS
```

`tests/test_lr_r1_m1_legacy_import.py`, 7 passed (MEASURED).
- **Absences.** Every absent field (prompt, raw response, per-attempt diff, authority snapshot, recovery decision,
  retry delta, fallback decision, per-attempt outcome, and passing-gate output where absent) is an explicit
  `not_recorded` record with its reason, and nothing else is.
- **Final diff.** The final diff is run-level only.
- **Read-only.** The legacy evidence is never written.
- **Explain.** It reports legacy reasons and never overrides present evidence.
- **Real copies.** Two copied frozen runs imported to VERIFIED stores (139 / 153 records), with the originals'
  SHA-256 listings unchanged (MEASURED).

## M1 Q1-Q9 COVERAGE

Per question: answered from recorded evidence on real pipeline runs.

| Q | Status | Test proving it |
|---|---|---|
| Q1 asked | PASS | `test_lr_r1_m1_model_calls.py::test_one_call_one_wire_records_exact_messages_and_verbatim_content`; `test_lr_r1_m1_sections.py::test_developer_segments_reproduce_the_message_actually_sent`; native parity suite; `test_lr_r1_m1_cli.py::test_a_passing_run_answers_every_question` |
| Q2 authority | PASS | `test_lr_r1_m1_authority_parse.py::test_each_developer_request_is_preceded_by_its_authority_and_followed_by_its_parse` |
| Q3 returned | PASS | `test_lr_r1_m1_model_calls.py` (verbatim content, errors, cancellation, two-wire calls); `test_lr_r1_m1_authority_parse.py::test_a_malformed_answer_is_recorded_as_parsed_invalid_with_its_typed_code` |
| Q4 candidate | PASS | `test_lr_r1_m1_candidate_gates.py::test_candidate_change_binds_digests_and_its_diff_reproduces_the_candidate` (`git apply` reproduces the bytes), `::test_a_file_without_final_newline_still_diffs_exactly`; `test_lr_r1_m1_recovery.py::test_content_proposed_but_never_staged_is_recorded_refused` |
| Q5 failed check | PASS | `test_lr_r1_m1_candidate_gates.py::test_every_validator_gate_is_recorded_after_it_ran_with_its_output`, `::test_terminal_gates_are_recorded_from_the_report_the_service_returns`; `test_lr_r1_m1_attempt_iteration.py::test_the_terminal_regression_gate_belongs_to_its_attempt`; `test_lr_r1_m1_cli.py::test_a_retried_run_explains_the_failure_the_decision_and_the_delta` |
| Q6 why retry | PASS | `test_lr_r1_m1_recovery.py::test_a_failed_attempt_records_its_diagnosis_then_the_recovery_decision`, `::test_an_exhausted_run_records_the_stop_decision` |
| Q7 what changed | PASS | `test_lr_r1_m1_recovery.py::test_each_retry_records_its_delta_at_its_first_developer_request` + four pure `retry_delta` tests |
| Q8 fallback | PASS | `test_lr_r1_m1_recovery.py::test_an_escalation_records_...`, `::test_an_incompatible_fallback_records_...`, `::test_a_call_substituted_to_the_next_fallback_records_both_models`, `::test_a_call_no_fallback_can_serve_records_the_refused_call` |
| Q9 final outcome | PASS | `test_lr_r1_m1_cli.py::test_a_passing_run_answers_every_question`, `::test_an_incompatible_fallback_is_explained_with_its_rejection` |

**T6 path matrix: PARTIAL.** The design (§12 T6) asks `kriya evidence explain` to answer Q1-Q9 for each of a list of
paths.
- **Asserted through `explain`:** passing direct, retried direct, fallback-incompatible, a no-model-call attempt
  (synthetic), multiple invocations (synthetic) and legacy runs.
- **Recorded and covered by I-2 or unit tests, but not asserted through `explain`:**
  - iterative per-file Developer;
  - investigation turns;
  - output-budget lower-protocol retry;
  - the `REPEATED_VECTOR` terminal;
  - plan-scope conflict;
  - Planner repair rounds;
  - an enforce TOOL subtask (only the synthetic no-model-call shape);
  - final review refusal;
  - exception escape;
  - deadline stop.
- **Asserted at `LLMClient` level, not through `explain`:**
  - context-budget refusal before dispatch;
  - cancellation;
  - the two-wire call.

This is an open M1 item (below).

## PERFORMANCE

| Measurement | Result | Status |
|---|---|---|
| Recorder time, scripted direct run (7 attempts, 220 records) | `full` 93 ms/run, `digest_only` 9 ms/run | MEASURED |
| One `model.request`, unique 50 KB / 400 KB prompt | `full` 2.6 / 15.4 ms; `digest_only` 0.26 / 1.4 ms | MEASURED |
| Versus the 1% design threshold | about 0.2% of a live run, from the measured per-record cost and the measured live pace (10-300 s calls, ~16 min/run). Against a scripted run (instant model) the share is ~10% of ~1 s, which is not the quantity the threshold governs | INFERRED (below the threshold); not certified |
| Live overhead | — | **NOT_MEASURED** |

## SECURITY

- **Capture modes.** `full` (default, D4), `full_with_reasoning`, `digest_only` (digests only, no content blobs:
  `test_digest_only_records_no_prompt_or_response_text` canary, plus a mutation) and `off` (no store).
- **Permissions.** Store directories 0700, files 0600, set explicitly whatever the umask
  (`test_store_is_private_regardless_of_umask`, umask 0o277). Retention staging stays inside the store root;
  symlinked entries are never followed or removed.
- **Local-only guarantee.** The store is written only under `<state dir>/attempt-evidence/<run_id>/` through its own
  writer:
  - never in a workspace or sandbox, and never through `write_control_file`/`AuthorizedFileWriter` (classified in the
    FILE-INTEGRITY audit);
  - no network client in the package (the PRD-012 network inventory passes in the full suite);
  - no export path exists.
- **Reasoning handling (D3).** The transport content is recorded verbatim, before `split_reasoning`. A separately
  returned reasoning field is recorded as digest + length only, and its text only under `full_with_reasoning`
  (`test_one_call_one_wire_...` asserts no reasoning blob by default; `test_full_with_reasoning_also_keeps_the_reasoning_text`).
- **Repository cannot weaken the operator setting.** `evidence` is a blanket SECURITY_AUTHORITY key under SEC-009
  (`kriya/config/authority.py::_BLANKET_SECURITY_TOP_KEYS`).
  `test_repository_config_can_never_change_the_recorder` checks that `capture: off`, `digest_only`,
  `full_with_reasoning` and a retention change from a repository `kriya.yaml` each raise `ConfigAuthorityError`.
- **Disclosure.** In `full` mode the store contains prompts, model output and source code (local, owner-only). The
  hash chain detects corruption and casual edits; it is not a signature.

## OPEN ISSUES

1. **T6 path matrix PARTIAL** (above). `explain`-level assertions are missing for the listed paths. The records
   exist, so this is test coverage, not a known recording defect, but the design names T6 as the definition of done.
2. **Live overhead NOT_MEASURED.** It belongs to the first live run with the recorder.

Outside M1, and not open items of this milestone:
- the LR-R1-P1/P4/P5 fixes (not implemented, by instruction);
- P2 and P3;
- ANALYZER-RULE7-MEDIAN-DEFINITION;
- `node_modules/`, `package.json` and `package-lock.json` appearing untracked in the worktree root after full-suite
  runs (a test-harness artifact; they existed in the main checkout before M1; not M1 output).

## PRODUCTION BEHAVIOR CHANGED

```text
NO, except the approved additive evidence semantics
```

Those additive changes:
- the store under the state dir, plus its retention (pruning only old stores of its own);
- the `evidence` config section (SECURITY_AUTHORITY), excluded from execution fingerprints (DEV-3);
- one `evidence.attempt_store` pointer event per trace row;
- the additive `ChatResponse.raw_content`/`reasoning_text` and `RecoveryDecision.retry_decision` (compare=False);
- `GenerationState.stop_reason_evidence` (evidence only);
- the `kriya evidence` commands;
- the never-required doctor row `evidence.attempt_recorder`.

I-2 proves that request bytes, workspace bytes, results, retry/fallback behaviour and control-plane outcomes are
unchanged across all recorder modes, faults included.

## READY FOR MERGE REVIEW

```text
NO
```

Every gate run here passes:
- the full suite (8608/0/0);
- Ruff and Pylint;
- 52/52 mutants killed;
- I-1, I-2 and I-3;
- D7 isolation;
- OBS-1.

But the design names T6 as M1's definition of done, and T6 is PARTIAL. Closing it means adding the missing
`explain`-level path tests (test-only). That would make this YES, unless the owner accepts T6 as PARTIAL for merge
review.
