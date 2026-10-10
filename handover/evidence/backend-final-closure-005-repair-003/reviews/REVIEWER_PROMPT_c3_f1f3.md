# Focused independent review - F1 and F3 follow-up, scope `6d97558..6169745` (Kriya-main-demo, branch repair/c3-p1)

Authoritative reviewer handoff (prepared 2026-10-10, owner-specified scope). The operator-run canonical gates on
`6169745` are GREEN (full suite, repository-wide ruff 0, pylint 0); `6169745` is the current certification candidate.

## Your role and constraints
You are a third party who did not author these changes. Review as an adversary of the change, not an explainer of it.
- Repository: `~/WorkingDirectory/AI/ClaudeCode/Kriya-main-demo`, branch `repair/c3-p1` == `6169745`; base of THIS review
  is `6d97558` (the previously reviewed candidate), NOT main. Review exactly `git diff 6d97558..6169745`.
  Product commits under review: `3d5f6db` (F1) and `8e09cc1` (F3). The records-only commit `6169745` (design doc
  13.2/13.3/13.8 notes, one registry row) may be checked for consistency only; it contains no product behaviour.
- Do NOT reopen the previously reviewed P1-1 / P1-2 / P2-2 / workspace-hash slices (`main..6d97558`, reviewed in
  `REVIEW_RESULT_c3.md`, dispositioned in `REVIEW_DISPOSITION_c3.md`) unless you find CONCRETE interaction evidence
  involving F1 or F3. An interaction finding must name the interacting path file:line on both sides.
- Read first: `handover/ENGINEERING_RULES.md`; `handover/BACKEND_FINAL_CLOSURE_005_DESIGN.md` sections 13.2 (suite
  preservation closure, incl. the F5 note), 13.5 (P2-2 typed environment failure), 13.6 (F1), 13.7 (F3), 13.8
  (dispositions); registry rows RUNTIME-VERIFICATION-ENV-STOP-001, SUITE-PRESERVATION-DIRECT-BASELINE-POLICY-001,
  ENFORCE-CLOSURE-STABILITY-CACHE-REUSE-001 in `handover/BACKLOG_REGISTRY.csv`; the original findings F1/F3 in
  `~/kriya-m1-live/backend-final-closure-005/repair-003/reviews/REVIEW_RESULT_c3.md`; pre-/post-fix measurements
  `repair-003/prefix/F1_reproducer_*.txt`, `F3_reproducer_*.txt`; mutant outputs `repair-003/mutations/F1-M*.txt`,
  `F3-M*.txt` and the scripts `run_f1_mutations.sh`, `run_f3_mutations.sh`; the F1 probe `repair-003/scratch/f1_runtime_probe*`.
- Allowed executions: read-only git, grep, reading files, small throwaway probe scripts written ONLY under a scratch
  directory outside the repository (e.g. `/tmp/f1f3-review/`), and targeted pytest on single test modules or node ids
  (`.venv/bin/pytest tests/<module>.py -q`, seconds to a few minutes). Do NOT run the full suite, `scripts/run_full_suite.py`,
  ruff/pylint repository-wide, live models, the cohort driver, `kriya generate`/`fix`, or any background agent.
- Do NOT modify, create or delete any file inside the repository (production code, tests, registry, design doc,
  evidence under the repo). Do not commit, stash, checkout, reset or touch the index. If a probe needs a mutated
  copy of a module, copy the file OUT to the scratch directory; never edit it in place.
- Every claim must be labelled MEASURED (you ran it), TRACED (you read the exact producing path, file:line) or
  INFERRED (consistent with evidence, not proven). Never present INFERRED as a finding of fact; an INFERRED finding
  must state the discriminating check that would settle it.

## The diff (6d97558..6169745)
| Commit | Slice | Production | Tests |
|---|---|---|---|
| 3d5f6db | F1 RUNTIME-VERIFICATION-ENV-STOP-001 (P2) | kriya/workflow/attempt.py (+22): `_raise_runtime_verification_infrastructure_failure` (~4495-4530) now calls `_stop_on_environment_gate_result(state, run_result, "runtime")` (line ~4517) BEFORE the `deterministic_sequence_kind(commands) is not None: return` early return; `_execute_managed_service_verification` (~5582-5620) after `if invalid_reason:` reads `validator.python_environment_error` and, when set, calls `_stop_on_environment_gate_result(state, {"environment_reason_code": code, "output": msg}, "managed_service")` before raising MANAGED_SERVICE_CONTRACT_INVALID | tests/test_repair_003_f1_runtime_environment_stop.py (A, 216 lines) |
| 8e09cc1 | F3 SUITE-PRESERVATION-DIRECT-BASELINE-POLICY-001 (P3) | kriya/workflow/workflow.py (+34/-?): constants `SUITE_BASELINE_FROM_RUN="run"`, `SUITE_BASELINE_LAZY_CAPTURE="lazy_capture"`, `_SUITE_BASELINE_SOURCES` (~1596-1601); `close_requirements_by_suite_preservation` gains required kw-only `baseline_source` and optional `run_baseline_policy`, refuses unknown source with ValueError (~1646), and in its baseline resolver returns `(None, "...policy ... captured no PRE-mutation baseline, and the direct terminal closure may not capture one")` when `baseline is None and baseline_source == SUITE_BASELINE_FROM_RUN` (~1677); run start records `state.validation_baseline_policy = full_regression_policy` (~4921); the direct terminal call site passes `baseline_source=SUITE_BASELINE_FROM_RUN, run_baseline_policy=state.validation_baseline_policy` (~5431). kriya/workflow/state.py: new field `validation_baseline_policy: Optional[str] = None` (~528). kriya/workflow/terminal_gate_service.py: imports `SUITE_BASELINE_LAZY_CAPTURE` and passes `baseline_source=SUITE_BASELINE_LAZY_CAPTURE` at the enforce closure call (~592) | tests/test_repair_003_f3_suite_preservation_baseline_policy.py (A, 184 lines); tests/test_repair_003_p1_2_suite_preservation_attribution.py (M, +18/-?) |
| 6169745 | records only | handover/BACKEND_FINAL_CLOSURE_005_DESIGN.md (13.2 F5 note, 13.3 F4 note, 13.8), handover/BACKLOG_REGISTRY.csv (+1 DEFERRED P3 row) | none |

Related, NOT under re-review but in the trace scope: `_stop_on_environment_gate_result` (attempt.py ~2604) and its
compile/test/targeted_test consumers (~4308, ~9408, ~9638, ~9659, ~9773); `runtime_verification_infrastructure_reason`
(text/steps based classifier) and its callers; P2-2's producer of `environment_reason_code` /
`validator.python_environment_error` in kriya/tools/validate.py (`_resolve_python_interpreter`, `run_app`,
`run_app_sequence`) and kriya/workflow/verification_coordinator.py; `effective_baseline_policy`
(kriya/workflow/baseline_policy.py:143), `_capture_single_baseline`, `captured_baseline`, the LR-R1-P4 verification-only
admission (a verification-only unit's genuine failure ends on `VERIFICATION_RETRY_NO_CHANGE_POSSIBLE`),
`suite_attribution.attribute_suite_result` (the D1/REG-R2 attribution owner) and `requirements.close_suite_preservation_requirements`.

## F1 review questions - answer every one, with evidence status and file:line
1. Can `PYTHON_ENVIRONMENT_UNAVAILABLE` (or any other structured `environment_reason_code`) still reach runtime GRADING
   as candidate behaviour on ANY `run_app`, `run_app_sequence`, managed-service or related runtime-verification path?
   Enumerate every consumer of a runtime/app result in attempt.py and the verification coordinator (verification-only
   path, mutating inline runtime block, managed-service admission, deterministic-sequence path, any gate that reads
   `run_result` fields such as `success`/`exit_code`/`output` before the shared raiser is reached) and say for each
   whether the structured code is consulted before any grading, retry classification or evidence recording that could
   treat the failure as the candidate's.
2. Does the fix stop BEFORE Developer retry, self-correction and owner recovery? Trace the raised
   `verification_infrastructure_failure` with `diagnostics.reason_code` to its retry-strategy consumer and show the
   STOP_ENVIRONMENT classification is keyed on `reason_code`, not on the shared type string, and that no recovery
   coordinator / verification-owner reopen / no-progress terminal runs first or afterwards.
3. Is the decision based ONLY on structured environment evidence (`environment_reason_code`,
   `validator.python_environment_error`), never on output text? Check that `runtime_verification_infrastructure_reason`
   (text/steps based) cannot pre-empt or override the structured stop, and that the managed-service branch does not
   consult the model's judgment/contract text to decide the environment stop.
4. Is the deterministic-sequence early return still correct ONLY for commands that genuinely executed with valid
   process authority? Can a deterministic sequence whose environment failed (nothing executed) still return early
   (i.e. is the structured check reached for every shape of `run_result` the sequence path can produce - dict,
   object, None, missing key)? What does `_stop_on_environment_gate_result` do with a result that has no
   `environment_reason_code` attribute/key?
5. Can the new logic accidentally convert a REAL candidate runtime failure into an environment failure? Enumerate
   every producer that can set `environment_reason_code` / `python_environment_error` on a runtime result and confirm
   each is a structured environment-preparation failure (venv creation, interpreter resolution, containment) and never
   an app exit status, timeout, readiness failure or output pattern. Can a stale `validator.python_environment_error`
   from an EARLIER command (e.g. a previous attempt or a previous unit sharing the validator) leak into the
   managed-service admission of a later command?
6. Is there any false-success path through fallback / default-interpreter behaviour after F1 + P2-2 interact: a
   runtime command that runs under a substitute interpreter and passes, or a managed service that starts under the
   wrong interpreter after `python_environment_error` was set? Confirm the resolver refuses before any process spawn.

## F3 review questions - answer every one, with evidence status and file:line
1. Can the direct suite-preservation boundary (workflow.py direct terminal call site, `SUITE_BASELINE_FROM_RUN`)
   still lazily acquire a baseline when the run policy explicitly disabled, or `auto`-resolved to disabled, baseline
   attribution? Trace every path by which `captured_baseline`/`suite_baseline`/`baseline_suite_run`/stability cache
   could be populated for the direct call and whether any of them runs the pre-mutation suite at closure time.
2. Does the direct path correctly REUSE an existing captured baseline (policy required, baseline captured at run
   start) without recapture? Count suite executions at closure.
3. Does an INDETERMINATE baseline (captured but unusable / stability envelope failed) remain fail-closed
   (REGRESSION_UNATTRIBUTED) and avoid recapture on BOTH boundaries?
4. Does the enforce terminal-closure path (`terminal_gate_service.py` ~592, `SUITE_BASELINE_LAZY_CAPTURE`) retain its
   explicitly authorized lazy-capture behaviour exactly as 13.2 states (at most once per closure, same validator /
   untouched workspace / autonomy / toolchain as `_capture_single_baseline`, never the candidate tree)?
5. Is the caller/source distinction explicit and stable rather than inferred from `None`? Is `baseline_source`
   required (no default) at the wrapper and at `requirements.close_suite_preservation_requirements` if it is
   forwarded? Are there any OTHER callers of `close_requirements_by_suite_preservation` (grep the repo, incl. plugins
   and scripts) that now fail or pass an unexpected source? Is `state.validation_baseline_policy` set on EVERY run
   path that can reach the direct closure (resume/checkpoint restore, milestone sequence, `fix` vs `generate`), and
   what does the reason text say when it is None?
6. Can a GREEN suite cause an unnecessary baseline capture on either boundary (order of checks: suite outcome first,
   baseline resolution only for a failing suite)?
7. Can ANY path close a failing REGRESSION_PRESERVATION requirement without the existing D1/REG-R2 attribution owner
   (`suite_attribution.attribute_suite_result`) returning a non-blocking verdict? Confirm the F3 change adds no
   second attribution site and that the new refusal branch reaches the same REGRESSION_UNATTRIBUTED closure detail.

## Cross-cutting (inspect the whole 6d97558..6169745 diff)
- no benchmark/cohort-specific logic (nothing keyed on the frozen twelve repositories, task ids, file names, goal text);
- no authority weakening (no decision moved to a second site, none reading model text, descriptions or names);
- no duplicated attribution semantics;
- no retry-budget expansion (attempt/retry counters, no-progress terminal, owner recovery budget unchanged);
- no new false-success path;
- no hidden environment fallback (silent default, substitute, warning-and-continue);
- no recursion or unbounded replay (capture -> gate -> closure -> capture; managed-service stop loops);
- no supported-backend regression (pytest host, contained Python, Maven, Gradle, managed service) caused by F1/F3
  interaction;
- rule-1 quality: unbound variables on early-exit paths, broad `except` hiding a coding error, bare MagicMock doubles
  in the two new test modules; each recorded mutant F1-M1..M3 / F3-M1..M3 - would the named tests really fail under it,
  and name any decision branch of F1/F3 with NO killing mutant.
- records consistency: do 13.6/13.7/13.8 and the three registry rows state what the code actually does?

## Output contract (follow exactly)
1. Verdict line first: `VERDICT: APPROVE | APPROVE WITH CHANGES | REJECT`.
2. Findings, most severe first. For each:
   `F<n> | severity P0/P1/P2/P3 | commit | file:function:line | claim | evidence status MEASURED/TRACED/INFERRED |
   exact consequence | how to reproduce (exact command or trace) | smallest safe correction (what it must preserve)`.
   Only findings with evidence.
3. Per question F1.1-F1.6, F3.1-F3.7 and each cross-cutting bullet: SETTLED (one-line answer with file:line) or
   NOT SETTLED (why, and what would settle it).
4. Anything you ran: the exact commands and their summary lines, verbatim.
Do not apply fixes. Do not restate the design doc. Do not soften findings to reach APPROVE.
