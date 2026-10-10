# Independent review - third repair cycle, scope `main..6d97558` (Kriya-main-demo, branch repair/c3-p1)

Authoritative reviewer handoff (prepared 2026-10-09, owner-specified scope). Supersedes the earlier draft.

## Your role and constraints
You are a third party who did not author these changes. Review as an adversary of the change, not an explainer of it.
- Repository: `~/WorkingDirectory/AI/ClaudeCode/Kriya-main-demo`, branch `repair/c3-p1` == `6d97558`; base `main` == `5407a80`.
  Review exactly `git diff main..6d97558` (six commits, none amended or squashed). Do not review anything outside it
  except to trace a call path the diff touches.
- Read first: `handover/ENGINEERING_RULES.md`; `handover/BACKEND_FINAL_CLOSURE_005_DESIGN.md` section 13 (13.1-13.5
  and the 13.3 addendum) - the design authority for what each change was SUPPOSED to do; the registry rows named below
  in `handover/BACKLOG_REGISTRY.csv`; pre-fix measurements and mutant outputs under
  `~/kriya-m1-live/backend-final-closure-005/repair-003/{prefix,mutations,scratch}/`.
- Do NOT run live models, the full suite, the cohort driver, or any `kriya generate`/`fix`. Do NOT modify production
  code, tests, the registry or the design doc; do not commit. Targeted single-module pytest and read-only git / probe
  scripts in a scratch directory are the only executions allowed. If the operator running you has not permitted
  pytest at all, settle every question by trace and say so.
- Every claim you make must be labelled MEASURED (you ran it), TRACED (you read the exact producing path, file:line),
  or INFERRED (consistent with evidence, not proven). Never present INFERRED as a finding of fact.

## The six commits
| Commit | Slice | Production files | Tests |
|---|---|---|---|
| 03a1a4d | P1-1 ENFORCE-PARTIAL-NO-CHANGE-COVERAGE-FREE-TEXT-001 | kriya/workflow/attempt.py | tests/test_backend_final_closure_005_partial_no_change.py (M), tests/test_backend_final_closure_005_verification_owner_reopen.py (M), tests/test_repair_002_d4_identical_write.py (M) |
| 9bc06d2 | WORKSPACE-CONTENT-HASH-IGNORED-KRIYA-DIR | kriya/workflow/checkpoint.py | tests/test_workspace_content_hash_kriya_dir.py (A) |
| 589d89a | P1-2 SUITE-PRESERVATION-CLOSURE-BASELINE-ATTRIBUTION-001 | kriya/workflow/requirements.py, kriya/workflow/workflow.py | tests/test_repair_003_p1_2_suite_preservation_attribution.py (A) |
| 77ece89 | P2-1 REGRESSION-ATTRIBUTION-UNAVAILABLE-DETAIL-001, BY_DESIGN | records only (design 13.4, registry: row closed BY_DESIGN + two DEFERRED follow-ups SUREFIRE-RENDERER-FAILURE-DETAIL-001, SUREFIRE-STABILITY-ENVELOPE-001) | none |
| c24bcce | P2-2 VERIFICATION-UNIT-ENV-FALLBACK-001 | kriya/tools/validate.py, kriya/tools/dependency_execution.py, kriya/capabilities/pip.py, kriya/workflow/verification_coordinator.py | tests/test_repair_003_p2_2_venv_environment_failure.py (A), tests/test_prd011_toolchain_evidence.py (M) |
| 6d97558 | hash addendum (own defect of 9bc06d2: modified candidate worktree under .kriya) | kriya/workflow/checkpoint.py | tests/test_workspace_content_hash_kriya_dir.py (M) |
Production scope in total: 8 files under kriya/, +364/-104. The records commit 77ece89 is in scope for ONE question:
is the BY_DESIGN disposition's stated premise consistent with the code it cites (validation_baseline.classify_level2_delta,
parse_surefire_structured_outcomes, suite_attribution.confirmed_regressions)?

## Questions - answer every one, in this order, each with evidence status and file:line

### 1. P1-1 (attempt.py) - untouched planned files are "delivered unchanged" when the unit has an effective mutation
a. Is there ANY path where a unit with ZERO effective mutation now escapes the deterministic no-change contract
   (`_settle_no_change_proposal` -> `verified_no_change.verify_no_change_unit` -> criterion coverage)? Enumerate the
   ways `state.all_files_written` can be non-empty without an effective authorized mutation (e.g. a write that is
   later reverted, rejected, identical, or outside the planned set) and say for each whether it wrongly unlocks the
   delivered-unchanged path.
b. Can an unchanged planned file now incorrectly SATISFY a mandatory claim / acceptance criterion / requirement, i.e.
   does anything downstream (candidate gates, terminal regression, terminal requirement gate, milestone completion,
   claims closure) read `planned_files_delivered_unchanged` or the identical-rewrite set as evidence of work done?
c. The reopened-owner special case was removed as "subsumed". Show by trace that both reopened-owner shapes (owner
   changes another file; owner changes nothing) end where design 13.1 says they end.
d. Can a NO-CHANGE-answered planned file that does not exist on disk be "delivered unchanged"? What happens to the
   completeness check and to `unchanged_files` reporting in that case?
e. Does the change alter retry/attempt budgeting, the D4 commit batch, or the result shape in any way not stated in 13.1?

### 2. P1-2 (requirements.py / workflow.py) - suite-preservation closure defers to the attribution owner
a. Can a failing suite close a REGRESSION_PRESERVATION requirement WITHOUT `suite_attribution.attribute_suite_result`
   explicitly returning a non-blocking verdict? Check: attribution returns None; attribution raises; the report is
   incomplete or empty; the baseline is indeterminate; `attribute_suite` callable absent; the verdict object's blocking
   predicate is the one the D1 consumers use (name the predicate and show it is the same semantics as the terminal
   regression check and candidate gates - no duplicated or re-derived attribution semantics in the closer).
b. Is an unavailable / indeterminate baseline, replay or attribution ALWAYS fail-closed as `REGRESSION_UNATTRIBUTED`?
   Show the exact string/reason the closure detail carries in each unavailable shape.
c. Baseline capture and replay: is the lazy capture at most once per closure attempt (not per requirement, not per
   call), never for a green suite, never re-captured when the run's own baseline is indeterminate? Can the enforce
   terminal gate path recurse (capture -> gate -> closure -> capture ...) or run the suite an unbounded number of
   times? Count the suite executions per closure on both boundaries (direct generate boundary reusing the run's
   captured baseline/stability cache/`baseline_suite_run`; enforce terminal gate capturing lazily).
d. Does the enforce-path lazy capture use the same validator class, same original (untouched) workspace, same autonomy
   config and same toolchain identity as the run-start baseline (`_capture_single_baseline`)? Could it ever capture the
   CANDIDATE tree as the baseline?
e. Are raw suite result and attribution decision both preserved in the entry / closure detail (nothing reinterpreted)?

### 3. Workspace content hash (checkpoint.py, 9bc06d2 + 6d97558) - root `.kriya/` always excluded from identity
a. Verify the contract across the shapes: `.kriya` absent; present and gitignored; present and NOT ignored (untracked);
   tracked at HEAD; a MODIFIED embedded repository (candidate worktree) under `.kriya/worktrees`. For each: does the
   identity stay available (non-None) and stable when repository content is unchanged?
b. Does excluding `.kriya` ever suppress UNRELATED repository content (e.g. a path that merely starts with `.kriya`,
   such as `.kriyarc` or `.kriya-notes/`, or a top-level file named `.kriya`)? Is the `git rm --cached` pathspec
   top-anchored exactly as the old `':!.kriya'` was?
c. Nested `.kriya` paths (e.g. `src/.kriya/`, `tests/fixtures/.kriya/file`) must remain repository content per the
   root-only contract. Confirm by trace of the pathspec semantics (and by probe if pytest/git probes are permitted).
d. Do real dirty-worktree changes (modified tracked file, new untracked file, deleted file, mode change, symlink
   target change) still change the identity? Does `-f --cached` ever touch the working tree or the real index?
e. Other shapes: an empty repository; a submodule the repository ITSELF declares; symlinks; a workspace that is not a
   git repository. Any shape where the identity changes although content did not, or stays although content changed?
f. The sibling dirty check in `compute_workspace_fingerprint` still uses the old pathspec for `git status` - is that
   consistent with the new identity contract, or is there a shape where fingerprint says clean and hash says changed
   (or vice versa)?

### 4. P2-2 (validate.py / pip.py / verification_coordinator.py / dependency_execution.py) - required venv failure is typed
a. Can a FAILED required project-venv creation still fall through to ANY interpreter capable of producing an
   authoritative PASS - through `run_tests`, `run_acceptance`, `run_app`, `run_app_sequence`,
   `_substitute_python_interpreter`, the candidate gates, the verification coordinator, the acceptance oracles, or any
   caller of `_resolve_python_interpreter` the slice did not touch? List every caller and its post-fix behaviour.
b. Does the typed failure (`PYTHON_ENVIRONMENT_UNAVAILABLE` -> `environment_reason_code` ->
   `verification_infrastructure_failure` -> STOP_ENVIRONMENT) reach the stop WITHOUT a Developer retry, a fix attempt,
   or a verification-owner recovery being started? Is that the same path GRADLE-WRAPPER-CONTAINMENT-001 uses?
c. Is dependency-free host behaviour (no dependency declaration, Kriya's own interpreter) still legitimate and
   unchanged? Is "required" determined by a declaration / containment policy and never by model text or by the
   presence of a venv on disk?
d. Is the dependency-INSTALL error path (venv created, install fails, repair-eligible) unchanged?
e. Is `python_interpreter_provenance` evidence-only? Grep every reader of `python_interpreter`, `fallback`,
   `python_environment_error` and `python_interpreter_provenance`; confirm none authorizes, selects or gates anything.
f. The test_prd011_toolchain_evidence fake now materializes the venv interpreter it was asked to create. Confirm that
   this is harness realism (matches what real containment does) and not a weakening of the PRD-011 evidence contract.

### 5. Reproducer and mutant quality (all slices)
For each new/updated test module: does it exercise the REAL production mechanism (the measured failure path: real git
workspace, real wrapper, real gate in host mode, real subprocess except the one stubbed step) rather than only the
patched implementation detail? Can any assertion pass vacuously (conditional assertions, mocks that bypass the defect
path, over-short timeouts)? For each recorded mutant (M1-M3, H1-H2, P1-P6, V1 under repair-003/mutations/): would the
stated test really fail under that mutation, and does the mutant set cover the important branch of each fix (name any
decision branch with NO killing mutant)?

### 6. Cross-cutting - inspect the whole diff for
- benchmark-/cohort-specific logic (anything keyed on the frozen twelve repositories, task ids, file names, goal text);
- authority weakening (any decision that moved from a single owner to a second site, or now reads model text,
  descriptions or names);
- hidden fallback behaviour (a silent default, substitute, "closest match" or warning-and-continue anywhere new);
- false-success paths (any way a unit, gate, requirement or run reports success it did not earn);
- duplicated attribution semantics (the closer or any new code re-deriving PRE_EXISTING / CHANGED / NEW instead of
  consuming the owner's verdict);
- changed retry / recovery / budget behaviour not stated in design section 13;
- supported-backend correctness regressions (pytest host, contained Python, Maven, Gradle paths) introduced by the six
  commits;
- rule-1 quality: unbound variables on early-exit paths, broad `except` that hides a coding error, bare MagicMock
  doubles in the new tests.

## Output contract (follow exactly)
1. Verdict line first: `VERDICT: APPROVE | APPROVE WITH CHANGES | REJECT`.
2. Findings, most severe first. For each:
   `F<n> | severity P0/P1/P2/P3 | commit | file:line | claim | evidence status MEASURED/TRACED/INFERRED | how to
   reproduce (exact command or trace) | what a fix would have to preserve`.
   Only findings with evidence. A finding that rests on INFERRED must say what discriminating check would settle it.
3. Per question 1-6: SETTLED (with the one-line answer) or NOT SETTLED (with why and what would settle it).
4. Anything you ran: the exact commands and their summary lines, verbatim.
Do not propose or apply fixes. Do not restate the design doc. Do not soften findings to reach APPROVE.
