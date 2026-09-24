# PRD-008 Coding Agent Handover

## Status
READY_FOR_PYTEST_VERIFICATION

## Source identity
- Base revision: `ebe4994` (PRD-007 verified).
- Production implementation revision: `31d19da` in the target checkout.
- Final revision / working-tree diff ID: the handover commit following `31d19da`.
- Kriya version: 0.1.0.

## Scope implemented
- Instruction file: `KRIYA_ALL_IMPLEMENTATION_INSTRUCTIONS_v1.0.md`, PRD-008.
- Requirements completed:
  - Added an explicit fingerprint-to-stage invalidation matrix for source/workspace, config, goal, plan, obligation ledger, skills, model runtime, containment, toolchain, verification policy, and commit state.
  - Added machine-readable resume decisions with fingerprint, action, invalidated stages, and reason.
  - Preserved safe no-change and legacy-checkpoint behavior while invalidating only dependent stages for changed optional fingerprints.
  - Made uncertain RunRecord commit intent/result refuse normal resume before any model call.
  - Persisted COMMIT_ELIGIBLE before actual workspace mutation and COMMITTED afterward in both legacy WorkflowEngine and enforced WorkflowController terminal paths.
  - Added controller refusal when a prior RunRecord has uncertain mutation authority.
  - Kept PRD-005 uncertain commit-evidence refusal intact.
- Requirements deliberately deferred: computing exact model/toolchain/containment fingerprints belongs to their later owning PRDs; PRD-008 defines and enforces their resume contract when present.

## Files changed
- Production: `kriya/workflow/checkpoint.py`, `kriya/workflow/workflow.py`, `kriya/workflow/workflow_controller.py`, `kriya/control/run_coordinator.py`, `kriya/control/persistence.py`.
- Tests: `tests/test_resume_integrity.py`.
- Docs/decisions: this handover, tracker row, `handover/evidence/PRD-008/*`. `.eie/DECISIONS.md` is absent.

## Pre-change reproduction
- Observed gap: resume had no explicit invalidation matrix or machine-readable decisions and did not consult RunRecord commit intent/result.
- Evidence: `handover/evidence/PRD-008/pre-change-reproduction.txt`.

## Implementation summary
`validate_resume_against_reality` remains the shared comparison boundary and now returns deterministic structured decisions. Ordinary mismatches invalidate named dependent stages and start fresh. An uncertain commit is different: it returns REFUSED and blocks all model work until explicit recovery. The commit lifecycle is written before and after the real mutation call, so process death leaves an actionable durable state.

## Tests run by coding agent
| Command | Passed | Failed | Skipped/Deselected | Duration | Notes |
|---|---:|---:|---:|---|---|
| Required resume/checkpoint/RunRecord/controller/CLI/ownership regressions | 435 | 0 | 0 | 27.53s | Includes parameterized fingerprint matrix and uncertain-commit integration |
| `tests/test_workflow.py -k 'resume or checkpoint'` | 13 | 0 | 851 deselected | 5.54s | Existing workflow resume behavior |

The coding agent did not run the full project suite; independent user verification owns that gate.

## Static/lint/architecture checks
- Compileall and `git diff --check` passed.
- Ruff passed for the new focused PRD-008 test module.

## Live test additions
- Required by instruction: NO.
- Test file/case: deterministic local resume and commit-state tests.
- Environment prerequisites: POSIX filesystem/process behavior.
- Exact command for Live-Model Verification Agent: not applicable.
- Expected invariant/evidence: not applicable.

## Known limitations / residual risks
- Fingerprints introduced by later model-runtime and containment PRDs remain optional until those owners populate them.
- Refused uncertain commits require an explicit recovery workflow; PRD-008 intentionally does not guess whether to complete or roll back an ambiguous commit.

## Decisions recorded
- `.eie/DECISIONS.md` entries: none; the file is absent.

## Evidence artifacts
- `handover/evidence/PRD-008/pre-change-reproduction.txt`
- `handover/evidence/PRD-008/required-regressions.txt`
- `handover/evidence/PRD-008/workflow-resume-regressions.txt`
- `handover/evidence/PRD-008/lint.txt`

## Verification-agent handoff
Run:

```bash
.newvenv/bin/python -m pytest -ra \
  tests/test_resume_integrity.py \
  tests/test_checkpoint_control_plane_hashes.py \
  tests/test_state001_checkpoint_workspace_identity.py \
  tests/test_run_record.py \
  tests/test_workflow_controller_enforce.py \
  tests/test_cli_smoke.py \
  tests/test_run_ownership.py

.newvenv/bin/python -m pytest -m 'not live_model' -ra \
  --junitxml=handover/evidence/PRD-008/user-full.xml
```

Record complete counts and skip reasons in `handover/PRD-008_PYTEST_VERIFICATION.md`. No live-model verification is required.

## Reopening addendum (2026-09-24, independent review) - S1: commit-state gate, exact evidence, reference-safe retention

Status: IMPLEMENTING (S1 of S1-S5 committed locally; S2-S5 pending). Findings closed here: review §PRD-008
"uncertain record blocks forever" groundwork (the gate half; `kriya runs recover` is S4) and the retention
hole found during planning.

**One gate for every mutating entry point.** `kriya/control/commit_state.py::assess_workspace_commit_state`
(RunRecords with an unsettled/UNCERTAIN cycle, unreadable records, IN_PROGRESS/UNCERTAIN/unreadable commit
evidence) is called by `begin_mutating_run` AFTER the workspace lock is taken and BEFORE the new run's record is
created, so the new record can never contaminate the assessment. Refusal = `UncertainWorkspaceStateError`
(lock released, no record written). `coordinated_mutation` turns it into the owner's structured refusal when the
owner defines `workspace_refusal_result` (WorkflowEngine -> dict, WorkflowController -> WorkflowResult with
`control_state=None, route=None`), else re-raises (milestones, future APIs). Every CLI `begin_mutating_run` site
prints `[Recovery Required]` and exits 1 (AST test enforces this for future sites). Previously only enforce mode
checked; `generate`/`fix`/milestones/proposal execution/tool writes could run model work on a half-committed
workspace. The controller's own enforce re-check now uses the same assessment (covers a controller entered inside
an already-owned run). Reason codes unchanged; the payload now reports every unsafe store at once (was
first-tier only) and carries `recovery_command`.

**Commit evidence schema 2** (`edit_safety.py`, v1 still loads): each operation records `kind`
(CREATE/MODIFY/DELETE) and exact `before`/`after` state `{exists, sha256 (bytes), mode}` - no magic null
hashes, CRLF/non-UTF-8 visible - plus top-level `candidate_hash` (`candidate_digest`, moved from
terminal_commit.py) equal to the RunRecord cycle's `candidate_hash`, linking the two for S4's
`--complete-partial` proof. Durability order (tested): RunRecord intent -> IN_PROGRESS evidence (fsync file+dir)
-> staged temp files (fsync) -> first replace. Deliberately NOT the order proposed in review item 10 (stage
before IN_PROGRESS): staged temp files live beside source files, so staging first would leave unattributed bytes
in the workspace after a crash.

**Reference-safe retention** (`kriya/control/retention.py`). The batch no longer prunes evidence by count
(pre-fix reproduction: 50 later commits deleted the COMMITTED evidence of an unsettled cycle, which recovery would
have read as "never started"). Mark-and-sweep: protected = non-terminal, commit-state-unknown,
checkpoint-referenced (`checkpoint.list_checkpoint_run_references`), caller-named, newest N terminal; a record and
the evidence its cycles reference are pruned as a unit; IN_PROGRESS/UNCERTAIN/unreadable evidence never; nothing
at all while any record is unreadable. Runs best-effort at the end of every run, under the lock.

**Tests changed:** `test_prd005_commit_transactions.py::test_terminal_evidence_is_pruned_but_uncertain_never_is`
replaced by `test_batch_never_prunes_evidence_on_its_own` (behavior deliberately moved); 
`test_prd007_run_lifecycle.py` unreadable test now expects both codes; `test_resume_integrity.py` uncertain-resume
test now expects the earlier coordinator refusal (validator REFUSED stays covered at unit level). New:
`tests/test_prd008_commit_state_gate.py`.

**S1 verification commands (user runs):**
```bash
.venv/bin/pytest tests/test_prd008_commit_state_gate.py -ra
.venv/bin/pytest tests/test_prd005_commit_transactions.py tests/test_prd007_run_lifecycle.py \
  tests/test_resume_integrity.py tests/test_run_ownership.py tests/test_run_record.py \
  tests/test_prd004_commit_failure.py tests/test_workflow_controller_enforce.py tests/test_cli_smoke.py -ra
```
Lint (coding agent, run): `ruff check` on every touched/new file passes; `compileall`, `git diff --check` clean.

**S1 user verification (2026-09-24):** both S1 commands above all green after test-ordering fix e74f79d
(`test_prune_never_removes_uncertain_or_unreadable_evidence...` planted bad evidence before its own commit; the
batch correctly refused). S1 = 46d2a33 + e74f79d.

### S2 - resume fingerprints and the single comparison path

**New `kriya/workflow/resume_fingerprints.py`.** 13 fingerprints, each `{value | UNAVAILABLE, basis}`:
`workspace` (git worktree content + HEAD), `config`, `goal` (goal, error context, supplementary/recovery
context, execution scope, grounding goal, established files), `approved_plan` (predetermined plan/design/files,
structured-plan hash, current subtask, completed subtasks), `input_obligation_ledger`,
`effective_obligation_ledger`, `skills` (content of every skill source directory, taken from `SkillEngine`'s
own `skills_dirs`; staged rules and bytecode excluded), `model_runtime` (UNAVAILABLE until PRD-013),
`containment`, `toolchain` (UNAVAILABLE until PRD-011), `verification_policy` (config plus the run's
verification arguments), `authority_context` (allowed write paths, semantic regions without their audit-only
`source`, write-scope mode, protected file), `kriya_runtime` (every package source file, not bytecode, plus the
distribution version; computed once per process).

**Config ownership.** `CONFIG_FIELD_OWNERS` gives model identity (`llm.provider/model/base_url/api_key`,
`llm_chain`, `agent_llms`), containment and verification-policy fields to their own fingerprints; every other
field, including any added later, stays in `config` (fail closed). Tests assert every owned path exists and
every leaf lands in exactly one bucket. The name is `config`, not "effective config": PRD-007's
`RunRecord.effective_config_fingerprint` remains the full-config hash, unchanged.

**Dependency matrix.** `ARTIFACT_DEPENDENCIES` (artifact -> stage + fingerprints) is the single source;
`RESUME_INVALIDATION_MATRIX` entries for the fingerprints are derived from it. Reused artifacts are read from
checkpoint VALUES (`_save_stage_checkpoint` always writes the baseline keys, usually None). NOT_APPLICABLE
exists only when no reused artifact depends on a fingerprint; a missing/UNAVAILABLE value on either side, or
different bases, is UNVERIFIED and invalidates like CHANGED. `model_runtime` invalidates only `model_protocol`,
which no checkpoint reuses today (model_hops are trace-only), so a model change never blocks a plan resume.
`kriya_runtime` is a dependency of every artifact: a different Kriya build reuses nothing.

**One comparison path.** workflow.py's inline drift block is gone; the checkpoint is judged only by
`validate_resume_against_reality(current_resume_fingerprints=...)`. Both validation and `_save_stage_checkpoint`
build fingerprints through `generation_resume_fingerprints()` (same argument names and defaults as
`run_generation_workflow`), so they cannot diverge; test seeding helpers call it too. The flat-key loop
(`config_fingerprint`, `skills_fingerprint`, ...), which skipped any key absent on either side, is retired.
The authorized-semantic-region check is now `authority_context`: a CHANGED boundary blocks reuse, not only a
dropped one. A REFUSED payload's `reason_codes` are derived from the decisions.

**Missing run record.** A checkpoint (or, in enforce mode, the persisted ControlState via new
`load_control_state_run_reference`) whose referenced RunRecord no longer exists produces a
`run_record_provenance` decision invalidating every stage (was: check skipped, fail open). INVALIDATE, not
REFUSE: the S1 workspace gate has already established nothing is pending, and REFUSE would strand the
checkpoint. Enforce resume now passes the prior RunRecord to the validator. Retention now also protects the
run the ControlState references (otherwise pruning would itself cause that invalidation).

**Behaviour in S2 (intermediate).** Any invalidated stage still discards the whole checkpoint (fresh run);
S3 narrows this to the invalidated stages. Consequence: a candidate-stage checkpoint is never reused in S2,
because candidate-gate outcomes depend on the UNVERIFIED toolchain. The enforce ControlState path gains the
run-record check only; its "nothing reused" decision is S3 (note: completed->pending reset at wc ~6254 is in
the `try` body, so a crash can leave sandbox-only "completed" states; S3 closes it).

**Intended test semantic changes.** `test_workflow.py::test_workflow_resumes_from_candidate_gates_checkpoint_
but_still_runs_regression` -> `test_workflow_candidate_checkpoint_not_reused_while_toolchain_unverified` (S3
flips it again). `test_resume_integrity.py`: flat-key parametrized/matching tests ported to the fingerprint
block; `test_legacy_checkpoint_without_new_optional_fingerprints_remains_loadable` replaced by
`..._is_unverified_not_resumable` plus a no-request test. Seeding helpers in test_workflow.py and
test_proposal_promotion.py now add the production fingerprint block; the two region tests seed their regions.
New: `tests/test_prd008_resume_fingerprints.py`; `test_workflow_controller_enforce.py::
test_enforce_resume_skips_nothing_when_the_control_state_run_record_is_gone`.

**S2 review fixes (follow-up commit).** `kriya_runtime` hashed `.DS_Store` files under `kriya/` (Finder
rewrites them, so a real cross-process resume could read CHANGED); it now skips dotfiles and bytecode, and the
skills hash skips OS metadata files (other dotfiles such as `.skill_conflicts.json` still count). Model-runtime
ownership narrowed to identity leaves (`llm.*` and `agent_llms.<role>.llm.*` provider/model/base_url/api_key);
per-role knobs and `llm_chain` stay in `config`. The input-ledger fingerprint is fixed at entry (the caller's
ledger is the same object the run grows into its effective ledger). Fingerprint computation can no longer fail
the run: on save the checkpoint is written without the block (UNVERIFIED on resume); on resume the run starts
fresh. Region sort uses a serialized key (None vs str `successor_key` raised TypeError). Verified with a
non-pytest script: checkpoint seeded in one process, resumed in another (plan reused, 3 model calls).

**S2 verification commands (user runs).** The `-k` filter applies to every path on its command line, so it
gets a command of its own:
```bash
.venv/bin/pytest tests/test_prd008_resume_fingerprints.py tests/test_resume_integrity.py -ra
.venv/bin/pytest tests/test_proposal_promotion.py tests/test_proposal_binding.py \
  tests/test_workflow_controller.py tests/test_workflow_controller_enforce.py \
  tests/test_state001_checkpoint_workspace_identity.py tests/test_checkpoint_control_plane_hashes.py \
  tests/test_control_plane_end_to_end.py tests/test_run_record.py tests/test_prd007_run_lifecycle.py \
  tests/test_prd008_commit_state_gate.py tests/test_milestones.py -ra
.venv/bin/pytest tests/test_workflow.py -ra   # unfiltered: every successful run now fingerprints each checkpoint
```
Lint (coding agent, run): `ruff check` clean on new files; no new findings on touched files (pre-existing
F401/I001 only).

**S2 user verification (2026-09-24):** all three S2 commands above green. S2 = 72922c4 + 63a45ea.

### S3 - stage-precise invalidation, candidate rebuild, enforce completion scope

**Generation resume keeps the longest valid prefix.** `resume_fingerprints.build_resume_plan()` (pure)
turns the validator's invalidated stages into a `ResumePlan`: artifacts are dropped from the first invalidated
stage of the derivation chain `context -> planning -> candidate -> verification` onward. `model_protocol` is off
that chain (nothing downstream is derived from negotiated model state), so a model change never discards a plan
or candidate. Context invalidated = nothing reused (fresh run). The plan's `state` is the checkpoint with every
discarded artifact set to None and the stage label lowered, so it offers exactly what the plan reuses (tested
for every single-fingerprint change x plan/design/candidate checkpoints). The decision is logged, annotated on
the RunRecord (`resume_decision`, new optional annotatable field, no schema bump; v2 records without it load)
and returned by enforce runs.

**Candidate reuse split from gate skip.** `AttemptContext.resume_plan` is the only source of both flags; the
checkpoint dict cannot grant either (tested with a forged checkpoint). A reused candidate replaces generation on
attempt 1 and is written into that attempt's fresh worktree through the normal write path (ownership,
write-scope and semantic-region content checks all still run: they sit before the gate-skip block). Candidate
gates are skipped, and their old outcomes restored, only when every verification fingerprint matched; otherwise
they run again and the old outcomes are discarded. Terminal regression always runs. Until PRD-011 binds the
toolchain, verification is always UNVERIFIED, so a candidate is always re-gated (the skip path is tested with a
bound toolchain).

**Candidate integrity.** The candidate checkpoint now captures exact bytes (strict UTF-8, no newline
translation; was `errors="replace"` and silently skipped unreadable files) and stores
`candidate_snapshot_hash`; any file not captured exactly means no digest. A missing (legacy), mismatched
(tampered) or malformed candidate is a `candidate_integrity` decision (INVALIDATE candidate+verification): the
candidate is regenerated, the plan kept.

**Effective obligation ledger persisted and restored.** `ObligationLedger.to_snapshot()/from_snapshot()` (replay,
so regressions are rebuilt; fingerprint-identical round trip tested with tuples, enums, nested evidence) and
`restore_from()` (in place: enforce forwards `resume`/`resume_id` and its shared ledger into every subtask's
generation call). Checkpoint key `effective_obligation_ledger_snapshot`. The current effective-ledger
fingerprint on resume is the restored snapshot's; a missing snapshot is UNAVAILABLE (never an empty ledger), so
the candidate is not reused.

**Enforce: completions count only once they are in the real workspace.** New `ControlState.subtask_completion_scope`
("workspace" | "candidate" | None=legacy). Production enforce runs always execute in a separate plan sandbox that
is discarded unless the whole plan commits, so completions are "candidate" until the terminal commit succeeds,
then re-persisted as "workspace" (post-commit persistence failure leaves "candidate": fails closed, never undoes
the commit). A resume reuses completions only with scope "workspace" (reason `COMPLETIONS_NOT_IN_WORKSPACE`
otherwise). Pre-fix reproduction (script, recorded here): s1 completed in the sandbox, the process died in s2;
the resume skipped s1, whose output had died with the sandbox, and every resume ended `needs_review` with
`CANDIDATE_MATERIALIZATION_FAILED`. The abandoned-plan-file quarantine is gated the same way: pre-fix, a refused
resume after such a crash moved the user's own pre-existing file (a path the sandbox had modified) into
`.kriya/abandoned_plan_files/` (reproduced with the new test on the pre-fix code). This also closes the
reset-in-try defect noted in S2: sandbox-only "completed" states that survive a crash are no longer trusted.
A resume after a committed sandbox plan skips every subtask and leaves the workspace byte-identical (the new
sandbox is synced with the workspace's uncommitted changes; a planned path missing from it fails
materialization, never becomes a delete).

**Behaviour changes to know.** Enforce resume after an interrupted or failed run now reuses nothing (before: it
skipped subtasks whose work was gone). A ControlState saved before this change has no scope: one-time full
re-run, and no quarantine from it. The orchestration tests' autouse fixture runs in place (scope "workspace"),
so their existing resume expectations are unchanged.

**Also in this slice (separate commit b44f7f5, at the user's request):** `typing.get_type_hints()` raised
NameError on 26 classes/functions in 9 modules whose annotation names were TYPE_CHECKING-only imports (F821
accepts those). Now runtime imports; the three mutually importing pairs bind a module alias at the end of the
module. Guards in `tests/test_bootstrap_contract.py`.

**Intended test changes.** `test_workflow.py::test_workflow_candidate_checkpoint_not_reused_while_toolchain_
unverified` (S2) -> `test_workflow_rebuilds_checkpointed_candidate_and_reruns_its_gates_while_toolchain_
unverified` (no Planner/Architect/Developer call; gates run on the rebuilt bytes; stale outcomes gone), plus the
gate-skip and unverifiable-candidate tests. `test_prd008_resume_fingerprints.py`: two candidate fixtures gain
`gate_outcomes` (gate outcomes are now reused by value, like every other artifact).

**S3 verification commands (user runs):**
```bash
.venv/bin/pytest tests/test_prd008_resume_fingerprints.py tests/test_resume_integrity.py tests/test_bootstrap_contract.py -ra
.venv/bin/pytest tests/test_workflow_controller_enforce.py tests/test_workflow_controller.py \
  tests/test_proposal_promotion.py tests/test_proposal_binding.py tests/test_run_record.py \
  tests/test_prd007_run_lifecycle.py tests/test_control_plane_end_to_end.py \
  tests/test_state001_checkpoint_workspace_identity.py tests/test_checkpoint_control_plane_hashes.py \
  tests/test_milestones.py tests/test_obligations.py tests/test_containment.py tests/test_prd011_toolchain_identity.py tests/test_prd011_toolchain_identity_oci.py \
  tests/test_review_context.py tests/test_proposal_store.py -ra
# modules whose imports b44f7f5 changed
.venv/bin/pytest tests/test_acceptance.py tests/test_migration.py tests/test_context_budget.py \
  tests/test_corr016_planner_authority_gate.py tests/test_corr018_general_case_closure.py \
  tests/test_containment_oci_registry_scoped_unit.py tests/test_containment_oci.py -ra
.venv/bin/pytest tests/test_workflow.py -ra
```
Lint (coding agent, run): no new ruff findings on touched files. New tests were smoke-run as plain functions
(not pytest) by the coding agent.

**S3 review fixes (follow-up commit).** The knowledge-gap gate reads the skills directory (a library with no
covering skill is a gap), and before S3 its exact dependencies never mattered (any invalidation was a fresh
run); `skills` is now a `knowledge_clearance` dependency, so a skill change reopens the gate and nothing is
reused. The knowledge cache (public release dates) is deliberately not a dependency. An unset
`subtask_completion_scope` is left out of `ControlState.content_hash()`, so stored control-state hashes (e.g.
milestone checkpoints) do not all read as changed once after the upgrade. The effective-ledger restore moved
inside the try that keeps fingerprinting from ever failing a run. Reason-code buckets: the enforce completeness
test collects `reason_codes.append("...")` literals only; the three new resume reasons are decision values, not
appended reason codes. Partial generation resume keeps the checkpoint id and logs "Resuming checkpoint ...
partially" instead of "Refusing to resume"; the existing drift tests assert the regenerated plan and call count,
which hold either way.

