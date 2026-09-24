# PRD-001..011 Independent Review (2026-09-24)

Scope: the Codex implementation of PRD-001..011 (commits `e24ba04`..`91cc2b3`, base `af6220e` == `1891ba0` tree),
fast-forwarded onto `milestone-decomposition`. Baseline tag before it: `pre-prd-baseline` (`1891ba0`).

Method: three independent reviewer agents (no authorship, no pytest, no edits) audited defects and
completeness against every mandatory requirement / test requirement / acceptance criterion in
`tasks/PRD-0xx_*.md`. The three blockers marked (*) were re-confirmed first-hand in source.

This file is the authoritative reopened backlog for PRD-001..011. Each task below is reopened and must be
closed to its own spec, in sequence, before PRD-012 starts. Line numbers refer to `91cc2b3`.

## Verdict summary

| Task | Review verdict | Tracker status after review |
|---|---|---|
| PRD-001 | minor issues | READY_FOR_PYTEST_VERIFICATION (fixed, see below) |
| PRD-002 | minor issues | IMPLEMENTING |
| PRD-003 | good | VERIFIED (unchanged) |
| PRD-004 | BLOCKING | IMPLEMENTING |
| PRD-005 | minor issues | IMPLEMENTING |
| PRD-006 | BLOCKING | IMPLEMENTING |
| PRD-007 | partial | IMPLEMENTING |
| PRD-008 | BLOCKING | IMPLEMENTING |
| PRD-009 | minor issues | IMPLEMENTING |
| PRD-010 | partial | IMPLEMENTING |
| PRD-011 | BLOCKING | IMPLEMENTING |

No pre-existing test assertion was weakened in any of the 35 commits (`d6b775e` made its test stricter).

## Findings by task

### PRD-001
- 11 F821 undefined names remained (attempt.py `ContextItem`/`EngineeringPlan`/`PolymorphicValidator`,
  context_budget.py `SourceDerivationCache`, workflow_controller.py `Iterable`). All were in string or
  `from __future__` annotations - lint and `get_type_hints()` failures, not runtime import failures on any
  Python version. The only real bootstrap crash was authority.py `Any`, which Codex fixed.
- The F821 guard test covered 2 files only.
- **Fixed in the PRD-001 reopening commit**: `TYPE_CHECKING` imports for all 11; guard test now covers every
  tracked `kriya/` + `plugins/` Python file (`test_production_code_has_no_undefined_names`).

### PRD-002
- CI `release-integrity` installs `setuptools` unpinned (conflicts with req 4 single-lock policy).
- `MANIFEST.in` includes only `tests *.py`; tracked JSON fixtures (`tests/incidents/fixtures/*.json`) are
  omitted from the sdist.
- Wheel installs a top-level `plugins` package into site-packages (name-collision risk).

### PRD-004 (BLOCKING)
- Gates correctly moved pre-commit (all five read `plan_workspace_path`; one commit on success; registry
  persisted only post-commit).
- (*) Commit-stage exceptions escape raw: candidate-construction `RuntimeError`s (wc ~6166/6181) and
  `BatchCommitError`/`FileRevisionConflict`/`UncertainCommitError` from `commit_revision_grounded_batch`. The
  enforce caller only catches `_UnsafeStructuredPlan`/`_StructuredPlanUnavailable`, so the sandbox-only stage
  reset to `pending`, `save_approved_plan`, final `save_control_state` and any `WorkflowResult` are skipped.
  Persisted ControlState can say subtasks completed whose output was discarded. Violates AC3.
- Tests: single-file `read_text()` check, not a whole-tree byte snapshot; no CREATE/DELETE case;
  preserved-reference failure forced via exception not a real candidate change; migration-gate input path not
  asserted.

### PRD-005
- In-progress evidence persisted (edit_safety ~480) after staging but outside the try/finally that removes
  staged files: an evidence-write failure leaks `.kriya-stage-<txid>-*` files beside user sources and escapes a
  raw `OSError`. Staged files also are not listed in evidence for post-SIGKILL cleanup.
- Committed bytes != verified bytes: candidate read with `errors="replace"` + universal newlines (CRLF -> LF,
  non-UTF-8 -> U+FFFD); new files get umask mode, not candidate mode (executable `mvnw` loses +x). Pre-existing
  but now inside the PRD-005 transaction contract.
- A permanent `.kriya/control/commits/*.json` is written on every commit incl. zero-write batches, never pruned.
- Symlink targets replaced by regular files; rollback restores a regular file.
- Tests inject faults only after each syscall succeeds; none during staging/evidence write; rollback-failure
  (UNCERTAIN) path is `pragma: no cover` and untested; conflict tested only at the last item.

### PRD-006 (BLOCKING)
- (*) Every real enforce-mode structured subtask fails: controller calls
  `run_generation_workflow(workspace_path=plan_workspace_path)` (`.kriya/worktree`, wc:4650); that method is
  `@coordinated_mutation`; nested `begin_mutating_run` does `require_mutating_run(canonical, existing)`
  (run_coordinator.py:176-178) and the outer context is bound to the real workspace -> `InvalidRunContextError`.
  Tests are green only because fixtures map `create_git_worktree` to identity or replace the engine with an
  undecorated fake.
- The "adapter" (`coordinated_mutation` -> `_use_or_begin`) is the default production path, not a testing
  adapter; low-level primitives (`commit_revision_grounded_batch`, `AuthorizedFileWriter`) never call
  `require_mutating_run`. `kriya tools execute` writes are ungated (scope to be decided).
- `begin_mutating_run` finally: if `_fail_active_run` raises, lock is released but the ContextVar still holds
  an active context; a same-process REPL `generate` would reuse it without the lock. (unreproduced)

### PRD-007 (partial)
- Only goal_hash, commit_intent, commit_result, terminal_status are ever populated; config fingerprint, model
  runtime IDs, plan hash, ledger hash, candidate hash, verification evidence IDs, `store_revisions` never set.
- Commit-intent hooks fire only at `mutation_depth==1`; skipped for controller legacy/shadow and milestones.
  `_complete_successful_run` back-fills RUNNING->...->SUCCESS after return; lifecycle does not reflect real
  stages; SIGKILL mid-commit on nested paths leaves RUNNING, not uncertain.
- `save_run_record` read-check-then-CAS window; `list_run_records` raises on a stray `*.json` or schema
  mismatch (crashes every enforce run); corrupt record treated as absent (uncertainty gate fails open);
  `RUNNING` may jump to almost any state.
- Integration tests use a probe coroutine, not the real engine/controller commit path.

### PRD-008 (BLOCKING)
- 7 of 9 matrix fingerprints (approved plan, obligation ledger, skills, model runtime, containment, toolchain,
  verification policy) are never computed or written; `checkpoint.py:507-518` skips any missing value (fails
  open). The only production caller (workflow.py ~1093) passes config+goal, already compared before PRD-008.
  Enforce resume (wc ~4418) passes neither `current_fingerprints` nor `run_record`. `invalidated_stages` are
  labels only.
- (HIGH, with PRD-007) An `UNCERTAIN` record, or `commit_intent` with no `commit_result`, refuses enforce in the
  workspace permanently; `UNCERTAIN` is terminal and no CLI/API resolves it. A crash between persisting intent
  and the first byte written blocks forever.

### PRD-009
- Doctor semantic-region precision-boundary report claimed in `config.py` docstring and `default_config.yaml`
  but not implemented (new stale claim - PRD-001's own concern).
- Exact-equality seal rejects a stricter `generation_time_budget_seconds`; `autonomy.egress_policy` not sealed.
- Isolation/persistence/no-host-fallback are declared constants (`PRODUCTION_FIXED_RUNTIME_GUARANTEES`), not
  verified.

### PRD-010 (partial)
- `runtime.fixed_guarantees` required and always PASS.
- `toolchain.required` checks host `java`/`mvn`/`gradle` via `shutil.which`, not PRD-011 OCI images; Python is
  always `sys.executable`.
- `containment.oci_smoke` uses `debian:bookworm-slim`, not `OCIContainmentBackend`/toolchain images.
- `model.qualification` is name-based; `model.runtime_fingerprint` only checks existence (full binding is
  PRD-013/014 - must fail closed as UNAVAILABLE until then, not PASS).
- Unwrapped exceptions (capability profile, `find_jdtls`, role loop) crash `--json`; doctor takes the real run
  lock and creates `.kriya/checkpoints`; `egress.policy` doesn't check `llm.base_url` locality.

### PRD-011 (BLOCKING)
- (*) `toolchain_identity.py:15` unconditional `import tomllib` (3.11+); `requires-python >=3.10`; imported by
  validate.py/containment_oci.py -> `import kriya.cli` fails on 3.10.
- (*) Toolchain resolved in `PolymorphicValidator.__init__`; production callers set `java_home_override` after
  construction (workflow.py:3425, attempt.py:3986, attempt.py:7053) -> goal-stated JDK ignored under
  containment, mismatch refusal never fires. Test passes override via constructor only.
- Toolchain identity/digest not persisted (callers rebuild result dicts with success/output only).
- Common Python specs fail closed (`>=3.9,<4`, `!=3.9.*`, patch-level `.python-version`); Java 8/11, Python
  3.13+ refused.
- Runs the floating tag after attesting a digest (not pinned); Gradle build tool never attested.
