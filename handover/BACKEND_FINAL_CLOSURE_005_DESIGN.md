# BACKEND-FINAL-CLOSURE-005 - design authority (2026-10-08)

Owner instruction: "KRIYA BACKEND FINAL CLOSURE - AUTONOMOUS BATCH 005" with two clarifications (no separate push of
the Batch-004 local commits; OD-3 reproducer first) and the OD-1 security boundary (commits/tags/refs only, no
historical blobs, fail closed on stale or wrong-workspace metadata). Core principle unchanged: LLM OUTPUT CAN AUTHORIZE
NOTHING. Evidence root: `~/kriya-m1-live/backend-final-closure-005/`. Branch: `feature/backend-final-closure-005` off
local main 0ff6552 (the six unpushed Batch-004 commits preserved as they are).

Every claim is labelled MEASURED / TRACED / INFERRED / CONFIRMED (handover/ENGINEERING_RULES.md).

## 1. Start state and reconciliation (Phase 1)

- MEASURED: Kriya-main-demo on main 0ff6552, origin/main ea68e9b, six commits ahead (the instruction's "7" counted the
  records commit twice), tree clean except the owner's untracked `ui/standalone/test/host_mode_launchability.test.ts`.
  Registry: 139 rows, 127 CLOSED, 12 DEFERRED, 0 OPEN; every row the instruction's sweep list names is already CLOSED.
- TRACED: the two Batch-004 live fixes are systemic. PLAN-TEST-IMMUTABILITY-SCOPE-001 derives the immutable set from
  the sealed contract's TEST_IMMUTABILITY claim and the workspace HEAD; KNOWN-TARGET-GOAL-NAMED-MEMBER-HINT-001 reads
  goal-qualified names and validates every one against the file's real member boundaries, as a capacity fallback
  only. No repository-specific string in either. Positive and negative controls exist in
  `tests/test_backend_readiness_004_rows.py`; mutants m93/m94 recorded KILLED (`backend-readiness-004/mutations/run19-20`).
- The OD-3 instruction text and the registered row described two different semantics (a goal-frozen file versus a
  planned file the Developer leaves unchanged). Both were reproduced before any change (section 2).

## 2. OD-3: ENFORCE-PARTIAL-NO-CHANGE-001 (Phase 3) - commit 8bb7228

Reproducer `tests/test_backend_final_closure_005_partial_no_change.py` (end to end through `run_generation_workflow`,
every deterministic producer real: the goal's examples under the B2-a runner, the baseline and candidate suites as
the workspace's own pytest; only the compile gate stubbed). MEASURED before the fix:

| Reading | Shape | Pre-fix result |
|---|---|---|
| 1. B frozen by the goal ("Do not modify README.md.") | direct run, A = calc.py fixed, B byte-identical | refused at admission, VERIFICATION_AUTHORITY_REQUIRED, 0 model calls (the statement compiled to a BEHAVIOR claim with no closer) |
| 2. B a planned file of the unit, Developer answers NO CHANGE for B | enforce unit s1 plans A+B, A rewritten | INCOMPLETE GENERATION on all 8 attempts, quality_gates_exhausted (the registered row's live shape) |
| control: existing test immutability | correct fix / changed test | accepted / refused (already correct, unchanged) |

Fix (smallest systemic):
- Reading 1: a **FILE_IMMUTABILITY** claim. `requirements.frozen_file_statement` recognizes a PURE freeze of exact
  tracked paths (a negated change verb or a kept/unchanged state, the paths, closed connective filler; one word outside
  the vocabulary - "unless", "instead", a command span - keeps the BEHAVIOR claim). The compiler binds the frozen paths
  in the claim's binding (`CONTRACT_COMPILER_VERSION` 2 -> 3; every /2 contract stale by design); the baseline holds
  it as an identity claim; `validate_plan` refuses any plan action on a frozen file (`PLAN_EDITS_FROZEN_FILE`, repair
  guidance, same sealed set on every revision); the pre-apply boundary and the enforce terminal gate close it from the
  run's own mutation record (`close_requirements_by_file_immutability`: changed / deleted / renamed -> VIOLATED; a
  candidate not at the run's base -> evidence unavailable, never a closure).
- Reading 2: `_verified_no_change_proposal` accepts the NO CHANGE answers that explain exactly the never-written
  planned files whether or not the unit wrote its other files (never a file the unit wrote, never a protocol error);
  `_settle_no_change_proposal` keeps VERIFIED_NO_CHANGE for a unit that wrote nothing and records a partial
  verification (`partial`, `written_paths`) for a unit that did. A refusal still blocks the attempt's own writes.
- MEASURED: none of the six frozen primary goals compiles to the new claim - cohort identity unchanged.
- Verification: 17/17 module (both readings, five negative controls: changed, deleted, renamed, stale base, retry),
  187 adjacent contract/closer/baseline/admission tests, 26 enforce no-change tests, 10 reason-code table tests;
  mutants m95-m103 all KILLED (`mutations/run1`); ruff 0, pylint 0.

## 3. OD-1: sanitized Git metadata for contained builds (Phase 2)

Measured need (BACKEND-READINESS-004 T3): `gradle/versioning.gradle` runs `git describe --tags` at configuration time;
the candidate tree is a worktree whose gitfile dangles inside the container (masked as "no repository"), the authority
copy has no `.git` at all. Owner boundary: minimum metadata/object graph for the measured operations, no historical
blobs, no remotes/credentials/hooks/reflogs/stash/alternates/index, bound to workspace identity + exact baseline,
content-addressed, read-only, typed refusal when stale or wrong, the no-Git mask as the fallback, a kill switch.

Design (`kriya/tools/git_metadata.py`, `kriya/tools/containment.py::GitMetadataMount`, `containment_oci.py`,
`validate.py::_bound_git_metadata`, `autonomy.git_metadata_export`):
- Export = a fresh bare-initialized repository (empty template, Kriya-written config `core.bare=false`,
  `logallrefupdates=false`) holding exactly the COMMIT objects reachable from the base (`rev-list base`, transferred
  with `pack-objects` of that explicit list and `unpack-objects`) and the TAG objects of the annotated tags reachable
  from it (`for-each-ref --merged base refs/tags`); refs `HEAD -> refs/heads/kriya-base -> base` plus those tags. No
  tree or blob: the export is deliberately not fsck-clean; `git show HEAD:path`, `git diff`, `git status` and
  `git describe --dirty` fail inside the container (MEASURED) - historical content needs its own authority.
- Identity: stored at `<state>/git-metadata/<sha256(realpath(identity workspace))[:16]>/<base sha>/` with a
  `kriya.git_metadata/1` manifest; `digest` = sha256 over HEAD, every ref and every object (sha, type), recomputed
  from the repository at every verification, never trusted from the file. Refusals: `GIT_METADATA_STALE` (base),
  `GIT_METADATA_WORKSPACE_MISMATCH` (identity), `GIT_METADATA_CORRUPT` (digest, HEAD, gitfile, a hook, a remote, an
  alternates file, any non-commit/tag object), `GIT_METADATA_EXPORT_FAILED`; all `ContainmentSetupError`s, so the
  gate refuses rather than runs unverified.
- Mount (`git_metadata_mount_args`): a worktree's dangling gitfile is masked by the Kriya-owned gitfile
  (`gitdir: /kriya/gitmeta`) with the export mounted read-only at `/kriya/gitmeta`; a real `.git` directory is hidden
  under the export mounted read-only at `/kriya/workspace/.git`; a Kriya-owned copy without `.git` gets the export as
  `.git`. Without a bound export: exactly the pre-existing `dangling_gitfile_mask`. The host's `.git` is never
  mounted. `safe.directory` for the two Kriya paths through `GIT_CONFIG_COUNT` (git >= 2.35.2 ownership check), never
  a wildcard, never written into the export. Network stays denied.
- Binding at the validator (`PolymorphicValidator._bound_git_metadata`): resolved once per mounted tree from the
  mounted checkout's HEAD (a worktree) or the run's original workspace (an authority copy); a mounted checkout at
  another revision than the original is STALE. Off switch or a non-git tree: None (unaffected). D2B tooling-only
  directories are non-git: unaffected.
- Observation (MEASURED, pre-existing): a plain checkout mounted as a workspace exposed its full `.git` (the mask
  only hid dangling pointers); `run_baseline_authorities`'s `exported()` copy includes `.git`. With the export bound
  by default that directory is now hidden under the sanitized export; with the switch off the old behaviour remains
  (`test_containment_gitfile_mask.py::test_an_ordinary_repository_keeps_its_git_directory`).
- Verification: `tests/test_backend_final_closure_005_git_metadata.py` - host level (export contents and boundary,
  describe/rev-parse through a gitfile, stale/wrong-workspace/altered refusals, the binding, the OCI argument shapes,
  the validator wiring, SEC-009 classification) and the real contained shape in the pinned `gradle:8-jdk8` image the
  T3 cohort used: `git describe --tags`, `rev-parse`, no remote, no stash, no hooks, read-only, tree walks fail closed,
  for the worktree / copy / directory shapes, plus the exact T3 mechanism (a Gradle script that execs `git describe`
  while configuring) - all PASS. Adjacent containment/validator/OCI suites 284 passed. Mutants m104-m112 (section 6).
- T3 baseline re-run after implementation: section 6.

## 4. Phase 4 registry sweep

(pending)

## 5. Phases 5-7: model-execution reliability, retry quality, rejected candidates

(pending)

## 6. Mutations, certification, T3 re-run, cohorts

(pending)
