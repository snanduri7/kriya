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
  another revision than the original is STALE. Only a tree that is itself a checkout ROOT binds (`rev-parse
  --show-toplevel` must equal the path - independent review F4): a plain directory, a sub-directory of an enclosing
  repository, a state root or scratch directory inside one never inherits that repository's metadata. Off switch
  or no checkout root: None (unaffected). Storage: one pack per export, the earlier base's export of the same
  workspace pruned (F7); a tag that points at a tag is exported with its chain (F9); a symlinked `.git` is a typed
  refusal (F6).
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

Start: 139 rows, 0 OPEN, 12 DEFERRED. Every row the instruction's sweep list names was already CLOSED (static-analysis
in-place baseline, OCI mount syntax, path containment, Python live reliability, plan-target localization, Java API
preservation, Java examples, Gradle JVM acceptance, Gradle plugin acquisition and evidence, false-premise authority,
rejected-candidate retention and restaging, Kotlin DSL, Gradle wrapper and project root). Of the 12 DEFERRED:
- resolved in this batch: ENFORCE-PARTIAL-NO-CHANGE-001 (section 2), ENFORCE-VERIFICATION-UNIT-STOP-BEFORE-TERMINAL-
  GATE-001 (section 5);
- classified non-defects that stay DEFERRED with their reason: ENFORCE-EXECUTE-PLAN-CONVERGENCE-001 and
  RUN-ATTEMPT-GATE-EXTRACTION-001 (architecture convergence, no behavioural gap; rule 9 substantial redesign), the four
  Windows rows (not a supported backend platform), LEAK-SIGKILL-RECOVERY-GAP-001 (crash-recovery capability, measured
  safe, owner-approved deferral), TEST-RESOURCEWARNING-HYGIENE-001 (harness hygiene), DEMO-TIME-BUDGET-001 (operator
  configuration choice), GUI-CI-GATE-001 (GUI tail, section 7).
New rows this batch, all CLOSED_FIXED with reproducers: GRADLE-VERIFY-PHASE-CACHE-READONLY-001 (P1),
RETRY-CONTEXT-GOAL-MEMBER-LOSS-001 (P1), PLANNER-REPAIR-VERIFIER-SHAPE-001 (P2). GRADLE-GIT-AT-CONFIGURATION-
BOUNDARY-001 marked SUPERSEDED by OD-1 (its evidence: the T3 prepare phase exits 0).

## 5. Phases 5-7: model-execution reliability, retry quality, rejected candidates

Each Batch-004 "model behaviour" classification was re-traced from the recorded evidence (attempt-evidence store,
generate logs) rather than accepted:

| Run | Batch-004 label | Re-trace | Finding |
|---|---|---|---|
| P4-T2-r3 (jsoup) | anchored-edit protocol, model | the model's reasoning at attempt 2: "only cssSelector, wholeTextOf, shallowClone are shown, absUrl is not among them"; `_prepare_retry_context` built the retry's member set from failure loci and rejected SEARCH text only | **Kriya mechanism** RETRY-CONTEXT-GOAL-MEMBER-LOSS-001 (fixed 9b010cd): goal-named members of a planned target stay in every retry's member set; an unhinted unfit target gets attempt 1's capacity fallback |
| P4-T5-r2 (jmespath) | planner non-convergence, model | decoded repair prompts: attempt 0 used type=application_runtime (schema enum error shown, legal shape never named); attempt 1 switched to judgment (EVIDENCE_PATH_MISSING), attempt 2 SCOPE_UNJUSTIFIED; must_preserve was present and specific | **Kriya feedback gap** PLANNER-REPAIR-VERIFIER-SHAPE-001 (fixed): the schema-invalid guidance names type 'tool' + tool_name when the rejected value is a check kind. Feedback content only; no protocol-adapter change, qualification identity untouched. The remaining non-convergence (two bounded repairs) is the model's; budgets unchanged by owner instruction |
| P4-T4 (python-slugify) | wrong candidate, correctly refused; P3 reliability gap | s2 (verification-only) stopped typed VERIFICATION_RETRY_NO_CHANGE_POSSIBLE; s1's Developer never saw s2's gate output although it was deterministic new information for exactly that unit | **Kriya orchestration** ENFORCE-VERIFICATION-UNIT-STOP-BEFORE-TERMINAL-GATE-001 (fixed): the enforce controller reopens the nearest completed mutating upstream owner ONCE per verification unit with the unit's own gate output; only a candidate that changed the owner's files, passed its gates, had its final review performed, met its declared verification and stayed in scope is folded forward, then the verification unit runs once more; anything else leaves the original typed failure standing (GR-R0 preserved: no retry without new information). The decision is recorded in the attempt-evidence store (`verification.owner_recovery_accepted/_rejected`; a rejected reopen is the run's terminal cause). Only a Kriya-run gate's own output (compile/test/runtime/static analysis) is handed to the owner; a stop carrying a sealed authority's verdict names its type only (review F2). The bound is per verification unit per process (a resumed run may reopen once more; the fold is persisted before the rerun - review F8) |
| P4-T1 / P4-T6 | genuine | - | unchanged |

Phase 6 audit of "new information": the three retry paths above now either carry new deterministic evidence (goal
members, the legal plan shape, the verification output) or stop as before. Phase 7: `kriya evidence candidate` (byte-
for-byte rejected-candidate export, content-addressed, attempt/base bound, local) and `kriya evidence leak-check`
(blob-level, positive control) are unchanged and remain the standing tools (REJECTED-CANDIDATE-RETENTION-001, OBS-1).

## 6. Mutations, the T3 re-runs and the oracle correction, certification, cohorts

Mutation campaign (`~/kriya-m1-live/backend-final-closure-005/mutations/run_mutations_005.py`, same protocol as
batches 003/004): m95-m103 (OD-3), m104-m112 (OD-1), m113-m119 (verify-phase cache, retry grounding, planner guidance,
verification-owner reopen incl. the explain terminal cause) - every mutant KILLED (m118 only after the broken-owner
negative control was added, rule 8).

T3 bundle baseline re-runs on the frozen base (`reproducers/`), each preserved:
| Kriya | Bundle | Prepare | Verify | Reading |
|---|---|---|---|---|
| 2f381ee (OD-1) | 004 | **exit 0** (git describe at configuration answered from the export) | exit 2, ORACLE_ENVIRONMENT_PROBLEM: every `./gradlew` died on `gradle-8.10.1-bin.zip.lck (Read-only file system)` | GRADLE-VERIFY-PHASE-CACHE-READONLY-001 (Kriya) |
| 9b010cd (cache fix) | 004 | exit 0 | exit 1, but HIDDEN without counts (junit engine unavailable offline), COMPAT FAIL on the base itself | the frozen oracle's own defects (harness) |
| 9b010cd | 005 v2 | exit 0 | exit 1: HIDDEN tests="2" failures="2", COMPAT PASS, REGRESS fails only through the two injected hidden tests | the expected discriminating baseline |

Oracle correction (T3-ORACLE-BASELINE-TOOLCHAIN-001, harness): `base_signatures.txt` had been dumped with the host
JDK 17 (javac 9+ no longer marks anonymous classes `final`: 43/43 differing lines, `reproducers/t3-compat/`), its sort
was locale-dependent, and prepare never warmed the test runtime classpath (Gradle's `dependencies` report resolves
metadata only - MEASURED). v2 assets: the baseline dumped by the container's own toolchain on the frozen base,
`LC_ALL=C sort`, prepare runs one stable existing test class (`org.hamcrest.core.IsTest`) online so the junit engine is
cached. Disclosed consequences: a candidate that breaks `IsTest` itself ends INDETERMINATE (prepare) rather than FAIL
(the pre-existing `compileJava`/`compileTestJava` prepare already had this shape); the regression phase counts the two
hidden tests. The Batch-004 bundle is untouched (`backend-readiness-004/primary/bundles/T3`); the primary cohort runs
from `backend-final-closure-005/primary/bundles` through `primary/p5_run.sh` (p4_run.sh with the 005 bundle and output
roots, otherwise identical: same repositories, bases, goals, dispositions, config, model profile, leak check).

Certification, cohorts: section 8 (pending).

## 7. GUI tail (Phase 18)

Backend first, no M2 features. Done after the backend slices: `npm run check` green (10 test files / 55 tests, java
round-trip 270 files, exit 0; `certification/npm_check_786f9c1.log`); the owner's GUI-M1 launchability regression test
committed (`ui/standalone/test/host_mode_launchability.test.ts`, 3/3 under vitest); GUI-CI-GATE-001 decided: the
cross-language integration gate is the explicit `npm run check:integration` (= `check` + `fixtures:serializer:check`),
`npm run check` stays pure-node, the serializer step fails closed without a Kriya-capable interpreter
(`KRIYA_PYTHON`). Measured with the repository venv: every serializer fixture current. No roadmap/accessibility work.

## 8. Certification and cohort records

Independent review (`reviews/INDEPENDENT_REVIEW.md`): APPROVE WITH CHANGES, no BLOCKER; every finding reconciled in
c48d8de / 03a3ee2 (`reviews/RECONCILIATION.md`). Full suite: run 1 @e37a8f1 9773/3 (tripwire classifications), run 2
@03a3ee2 9777/2 (two counts moved by the reconciliation), run 3 @1e2d0ed **9779 passed / 0 failed / 0 errors, root
pollution none** - the certified run. ruff 0, pylint 0, mutants 30/30 KILLED. **BACKEND_FINAL_EXECUTABLE_SHA = 1e2d0ed.**
Registry after certification: 0 OPEN, 10 DEFERRED classified non-defects. One authorized push: main fast-forwarded to
the records commit on top of 1e2d0ed. Primary cohort (`primary/run_all.sh`, from the certified main) and cohort 2:
`~/kriya-m1-live/backend-final-closure-005/FINAL_REPORT.md` sections I-L (records after the runs stay local until
the owner authorizes a further push).

## 9. Phase 14 systemic repair cycle (after the primary cohort from 41c5b10)

Primary cohort (`~/kriya-m1-live/backend-final-closure-005/primary/P5-*`): T1, T4, T6 GENUINE; T2 SAFE_FAILURE; T5
SAFE_FAILURE; T3 FALSE_NEGATIVE (the sealed oracle closed REQ-1 on Kriya's candidate and the exported candidate passes
the frozen external oracle 2/2 + COMPAT + 489, yet the suite-preservation requirement could not close). Gate missed by
one genuine success. One systemic failure analysis found three Kriya mechanisms, none benchmark-specific:
- GRADLE-SUBPROJECT-BUILD-FILE-001 (T3): `validate.gradle_project_dirs` recognises `<dir>.gradle[.kts]` (a settings-
  renamed subproject build file); report roots and Gradle output roots use it. A name predicate, not a settings parse.
- PLAN-DOCUMENTATION-UNIT-VERIFICATION-001 (T5, and P4-T5-r2 identically): a unit editing only the documentation the
  sealed contract judges (the files the documentation-entries predicate's headings name - never a code file under a
  docs directory, review finding 1) declares no verification; the evidence-path error and the repair guidance name
  that shape; `documentation_paths` flows to every `validate_plan` of the enforce run including the scope-revision
  revalidation (review finding 2). The earlier verifier-shape guidance was corrected to the schema's canonical
  runtime form (judgment + verifier_kind application_runtime + requires_runtime_execution, no tool_name).
- PROTOCOL-FEEDBACK-EDIT-OPENING-001 (T2): the structured parser's diagnostic names the missing EDIT opening line;
  parse outcome and wire protocol unchanged.
Independent review of the repair diff (`reviews/INDEPENDENT_REVIEW_REPAIR.md`): APPROVE WITH CHANGES - the two MAJORs
above fixed (judged-files keying; the fourth revalidation site), the one-shot-iterable hazard and the missing producer
tests fixed, docstrings reworded; no widening of model-output authority. Mutants m123-m127. Re-certification: the
clean full run at the fix-up commit; then reruns of T3, T5, T2 and the T1 control from local main (a further push needs
the owner's authorization).


### 9.1 Rerun outcomes from b185bd6 (2026-10-08) and the final primary gate
Re-certification at b185bd6: full suite 9785/0, ruff 0, pylint 0, mutants 36/36. Reruns (`primary/P5-<T>-r2/`):
T3 SAFE_FAILURE - the suite-preservation evidence is now `gradle COMPLETE 489/489` (the false negative's mechanism is
gone, measured live) and the candidate itself was wrong (ASCII-only `Character.isWhitespace`; the sealed authority
failed hidden 1/2, the frozen external oracle agrees on the exported candidate). T5 GENUINE - one plan repair, the
documentation-only unit ran with no verification entry and closed by the sealed predicate. T2 SAFE_FAILURE - planner
non-convergence: the model declared five preserved references on the modifying production source instead of the
referencing test file in both repair drafts, although every error line and the correction rule named the test file;
Kriya refused and applied nothing. Recorded as an observation, not a defect: PLAN-PRESERVED-REFERENCE-SOURCE-
ATTRIBUTION-001 (P3, DEFERRED, owner decision) - accepting a plan-wide preservation of an unowned target would be a
plan-validation architecture extension (PRV-11), outside the one authorized repair cycle. T1 control GENUINE,
unchanged. Final primary gate (best run per task): 4/6 genuine, 0 false success, 0 authority violation, 0 corruption,
1 historical false negative (retired by measurement) -> PASS (`primary/PRIMARY_GATE.md`).

## 10. Cohort-2 freeze review and remediation (Phase 15, 2026-10-08)

The independent freeze reviewer returned NOT-READY (`reviews/COHORT2_FREEZE_REVIEW.md`): the cohort-2 assets prepared
in Batch 004 had never been validated to the frozen state. Blockers, each MEASURED and TRACED by the reviewer and
re-measured here: F1 the task workspaces carried the full upstream history with the fix commit directly beneath the
import commit (`git log -n 5 --oneline`, which Kriya's GitTool runs, named the fix); F2 the S2_A bundle could not PASS
on any candidate (toolz's git-derived version is 0.0.1 in the `.git`-less authority export, failing test_has_version);
F3 the S3_A bundle ran four DNS/HTTP test classes under a denied network; F4/F5 the Gradle bundles had only
environment-failed runs and no validation was bound to the frozen digests; F6 S3_A's API-preservation clause was not
independently detectable by the external oracle; F7 two hidden oracles asserted less than the goals.

Remediation is harness-only (no product change; the executable stays b185bd6), scripted and recorded under
`~/kriya-m1-live/backend-final-closure-005/cohort-002/remediation/` (REMEDIATION.md, VALIDATION_SUMMARY.md):
single-commit workspace snapshots with identical tree hashes; `.git_archival.txt` for the toolz version (the
PRETEND env form does not exist in setuptools-git-versioning 3.2.0, measured); network-class exclusions; javap compat
in both oracles with per-toolchain baselines (the T3 lesson: host and container javap differ on 926 of 1540 lines) and
explicit status capture; goal-example hidden classes; S4_B's prepare resolving the test runtime classpath. Every
bundle was then run base + fixed (+ S6_A tempting, + S3_A apibreak negative control) through Kriya's own authority
runner under production containment in the images Kriya resolves, each record bound to the manifest digest: 13/13
expectations met, and the frozen manifest now covers the external oracles' hidden copies and the shared run scripts.
Found on the way: the frozen T3 v2 bundle's in-run COMPAT status was vacuous (POSIX `| tail -1; COMPAT=$?`), recorded
as T3-ORACLE-COMPAT-PIPELINE-STATUS-001 (harness; the external bash oracle carried the real check; no primary
classification depended on it). Run precondition: SEC-009 durable approval of the six cohort-2 configs (same security
configuration as the approved primary config) under the cohort authority home. Cohort 2 runs only after the
reviewer's FROZEN-OK on the remediated freeze (`reviews/COHORT2_FREEZE_REVIEW_R2.md`).
