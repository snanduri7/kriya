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

## 11. Cohort 2 results and the readiness decision (Phase 15-17, 2026-10-08/09)

Run from clean local main d81e46b (executable b185bd6), serial, six tasks, 61 model calls, 67 min of Kriya runs (74 min end to end); every store
VERIFIED and sealed, every workspace restored, 0 false success, 0 authority violation, 0 hidden-oracle leak (three LEAKED
checker verdicts traced unit-by-unit to goal text or shown workspace lines). Tally 0/6 genuine, 4 safe failures, 2 false
negatives -> cohort-2 gate FAIL, combined readiness FAIL (4/12 genuine, 2 FN on the current executable): backend
readiness NO (`~/kriya-m1-live/backend-final-closure-005/cohort-002/COHORT2_GATE.md`, FINAL_REPORT.md K-O).

Four Kriya defects measured live on the unchanged executable, none model- or benchmark-specific, each TRACED to its
producer, two CONFIRMED by deterministic reproduction on the frozen workspaces (`COHORT2_KRIYA_DEFECTS.md`):
- D1 CANDIDATE-GATE-BASELINE-POLICY-001: the per-attempt candidate test gate (attempt.py, declared-test-verification
  branch) judges the raw suite result; PRD-024's PRE/POST baseline attribution exists only at the terminal
  full-regression check. One pre-existing failure, two verdicts in the same run; a correct one-line fix discarded (S2_A).
- D2 STRUCTURAL-EVIDENCE-SELF-CALL-EDGE-001: build_planning_structural_evidence resolves a file's own-method calls to a
  twin class defining the same names (owner lookup excludes only the caller), producing a false mutual edge that makes a
  two-unit plan over TextStringBuilder/StrBuilder unplannable (S3_A).
- D3 FILE-RESOLUTION-SCOPE-ESCAPE-001: prefer_existing_artifact_owners redirects a planned new test to an existing file
  outside the unit's validated scope (and ignores action=create); the write authority refuses the resolver's own target
  and the run dies, discarding a correct fix (S4_A).
- D4 ENFORCE-IDENTICAL-WRITE-COMPLETION-001: an implementation unit whose writes are byte-identical to the baseline
  completes as a mutation; the OD-3 no-change contract treats an identical rewrite as a write (S4_B).
Shared pattern (ENFORCE-UPSTREAM-WORK-PRESERVATION-001, P2): a later unit's out-of-scope trouble ends the run and
restores the base, throwing away earlier units' accepted work; the Batch-005 owner-reopen covers verification units only.
Also recorded: ENFORCE-FALSE-PREMISE-MUTATION-GUARD-001 (P2; S6_A rewrote a correct function and only D1 prevented a
false success - INFERRED), LEAK-CHECK-SHARED-LINE-FALSE-POSITIVE-001 (P3, tooling).

Decision discipline: the one authorized repair cycle was spent on the primary cohort (section 9); D1-D4 are OPEN P1 rows
awaiting the owner's authorization of a second cycle, which turns `tests/test_backlog_registry.py::test_an_open_p0_or_p1_
is_never_parked_in_the_backlog` red by design on the records commit. The certified executable b185bd6 (9785/0) is
unchanged. Recommendation: authorize the second cycle; its fixes are local and reproducible, and the shared-pattern rule
would have made both false negatives safe failures with a preserved candidate.

## 12. Second repair cycle: D1-D4 (owner-authorized 2026-10-09, branch repair/c2-d1-d4 off main 08a57e6)

Pre-fix records (ENGINEERING_RULES section 3), written before any production change. Evidence of the live
symptoms: `~/kriya-m1-live/backend-final-closure-005/cohort-002/C2-*/CLASSIFICATION.md`; deterministic pre-fix
remeasurements on the frozen workspaces: `~/kriya-m1-live/backend-final-closure-005/repair-002/prefix/`.

### 12.1 D1 CANDIDATE-GATE-BASELINE-POLICY-001 (+ D1b)
- Observation (MEASURED, C2-S2_A generate.log): one pre-existing failing test is PRE_EXISTING_FAILURE / not blocking
  for unit s1's terminal full-regression check and blocks all five attempts of unit s2's candidate gate in the same
  run; C2-S6_A: the same failure blocks a verification unit's declared test gate and, at the contract baseline, leaves
  REQ-5 REGRESSION_PRESERVATION unsatisfied so NO_MUTATION_REQUIRED is unreachable (D1b).
- Producer (TRACED): `kriya/workflow/attempt.py` candidate gate (`validator.run_tests()["success"]` judged raw at the
  full-suite branch, the test-selection fallback and the FS-1A covering run), `kriya/workflow/verification_coordinator.py`
  (`result["success"]` raw), `kriya/workflow/workflow.py::run_contract_baseline.judge_suite` (any failing case -> FAIL).
  The PRD-024 attribution (`classify_with_baseline_stability`) is consumed only by the terminal check in workflow.py.
- Alternatives considered: the candidate caused the failure (no: attempt 1 fails before any effective change, and the
  terminal check of the previous unit attributed the identical failure PRE_EXISTING); environment drift (no: same
  container, same test, same run).
- Root cause: CONFIRMED (one failure, two verdicts in one run; the two gates read different deciders).
- Fix: one attribution owner (`kriya/workflow/suite_attribution.py`) consumed by the terminal check, every candidate-gate
  full-suite run and the verification coordinator; the contract baseline records per-case pre-existing failures of the
  untouched base as pre-existing (a zero mutation introduces none) instead of FAIL, while an aggregate/indeterminate
  suite result stays FAIL/INDETERMINATE (VC3-R9 preserved).
- Predicted result: a brownfield repository with one pre-existing failing test and a unit with a declared test
  verification passes its candidate gate when its own change introduces no new failure; a NEW/CHANGED failure still
  blocks; mutation (judge raw again) -> the reproducer fails again.

### 12.2 D2 STRUCTURAL-EVIDENCE-SELF-CALL-EDGE-001
- Observation (MEASURED, repair-002/prefix/D2_structural_evidence_before.txt, executable 08a57e6 == b185bd6 product
  tree, frozen S3_A workspace 0234610): `build_planning_structural_evidence` on exactly TextStringBuilder.java and
  StrBuilder.java returns the mutual edge although neither file imports, constructs or references the other.
- Producer (TRACED): `kriya/workflow/workflow_controller.py` calls relation: `unique_owners = [o for o in owners if o != rel_path]`
  resolves a bare call to the only OTHER definer, so a file's calls to its own methods resolve to a twin class.
- Root cause: CONFIRMED by the discriminating run above (no textual reference; the function still emits the edge).
- Fix: a callee the calling file itself defines resolves to that file (no edge); otherwise the existing unique-other-owner
  rule. Predicted: the same call returns no edge for the twin pair; a real cross-class unique call keeps its edge; an
  ambiguous name (two other definers) keeps resolving to nothing; mutation (drop the self-definition check) -> the
  twin edge returns.

### 12.3 D3 FILE-RESOLUTION-SCOPE-ESCAPE-001
- Observation (MEASURED, repair-002/prefix/D3_file_resolution_before.txt, frozen S4_A workspace b39727b): with the
  controller-built s2 goal text, `prefer_existing_artifact_owners` redirects the planned, action=create
  MalformedPathTest.java to json-path-assert/.../HasNoJsonPathTest.java (scored tier); with the raw goal it does not.
- Producer (TRACED): `kriya/workflow/workflow.py` Architect-stage call (no check against the unit's validated scope,
  the plan's action=create ignored) and `kriya/workflow/attempt.py` Developer-path call (same resolver, same gap).
- Root cause: CONFIRMED by the reproduction (the redirect target is outside the unit's write scope by construction;
  the write authority then refuses the resolver's own choice).
- Fix: the resolver takes the unit's authorized scope and its explicit new-artifact paths; a redirect whose target is
  outside the scope is refused (planned path kept, authoritative event recorded); an action=create path is never
  redirected. Scope is passed at call time (never cached), so a revised scope is honoured and a retry never widens it.
  Predicted: the reproducer keeps the planned path; a redirect to an in-scope existing owner still happens;
  mutation (remove the scope check) -> the reproducer redirects again.

### 12.4 D4 ENFORCE-IDENTICAL-WRITE-COMPLETION-001
- Observation (MEASURED, C2-S4_B sealed store seq 62/63): both candidate.change records of s1 attempt 1 carry identical
  before/after digests; the unit completed quality_gates_passed=true and was "applied".
- Producer (TRACED): `kriya/workflow/attempt.py` commit batch adds every staged non-delete path to
  `state.all_files_written` regardless of content; the completeness check, the partial no-change contract (OD-3) and the
  apply step all read that set, so an identical rewrite discharges the unit's obligation.
- Root cause: CONFIRMED (the write path never compares the staged bytes with the captured baseline bytes).
- Fix: a staged write whose bytes equal the captured original bytes of that path is recorded as unchanged
  (`candidate.change` unchanged=true), never enters `all_files_written`, and the planned file is settled through the
  verified no-change contract (deterministic evidence or a typed refusal naming the identical rewrite). Byte authority
  only: a line-ending change is a change. Predicted: the S4_B shape ends VERIFIED_NO_CHANGE or VERIFIED_NO_CHANGE_REFUSED,
  never "applied"; mutation (count the write) -> the unit is applied again.

### 12.5 Outcome of the repair cycle (branch repair/c2-d1-d4, commits 51390e0, 3d9f2c3, 6fc8d71, 6650611, b7824e6)

Correction to 12.2 (ENGINEERING_RULES section 5 applied): the first D2 fix (self-declared callee only) left the
frozen pair's mutual edge in place. The discriminating check (repair-002/prefix/D2_discriminating_check.txt) found
three producers, not one: self-calls resolving to the only other declarer; the constructor regex matching
`new StrBuilder()` inside Javadoc examples; the JDK static call `CharBuffer.wrap(...)` resolving by bare name to the
twin's own `wrap()`. The frozen classification's mechanism was INFERRED, not CONFIRMED; the record is corrected in
repair-002/D2_ROOT_CAUSE_CORRECTION.md (the frozen files are untouched). The shipped D2 fix removes all three.

| Defect | Commit | Reproducer (pre-fix failure recorded) | Post-fix remeasurement | Mutation controls |
|---|---|---|---|---|
| D2 | 51390e0 | tests/test_repair_002_d2_structural_evidence.py (8) | frozen S3_A pair: EDGES {} (repair-002/D2_structural_evidence_after.txt) | self-declared check, type-mention requirement, raw constructor scan: each fails 2 tests |
| D3 | 3d9f2c3 | tests/test_repair_002_d3_file_resolution_scope.py (7, incl. the enforce loop end to end) | frozen S4_A: planned path kept under the scope rule and under the create rule (repair-002/D3_file_resolution_after.txt) | scope check, create exemption, cached scope: 2 / 1 / 2 failures |
| D4 | 6fc8d71 + 6650611 | tests/test_repair_002_d4_identical_write.py (7; prefix/D4_reproducer_before.txt: 5/5 failed pre-fix) | S4_B shape ends VERIFIED_NO_CHANGE (tool coverage) or VERIFIED_NO_CHANGE_REFUSED naming the identical rewrite; nothing applied | count the write / drop the proposal: 5 / 5 failures |
| D1 | b7824e6 | tests/test_repair_002_d1_candidate_gate_baseline.py (7; prefix/D1_reproducer_before.txt: the S2_A shape dies no_progress pre-fix) | S2_A shape: candidate gate PRE_EXISTING not blocking, unit passes; S6_A shape (verification unit) same; D1b NO_MUTATION_REQUIRED reachable with recorded pre-existing failures | raw gate / never-blocking attribution / unsatisfied pre-existing / FAIL for failing cases: 3 / 2 / 1 / 1 failures |

D4 follow-up (6650611, own defect, reported): the first D4 change made the legacy/milestone path call an identical
rewrite "never written" (INCOMPLETE GENERATION) - found by tests/test_prd024_baseline_auto_policy.py's milestone-reuse
test (the integration unit returns the committed files unchanged). Outside the enforce no-change contract an identical
rewrite is now delivered unchanged and never a mutation; the completion is decided by the milestone no-change
verification. Two existing fixtures that pre-wrote the Developer's candidate into the pristine workspace (making it an
identical rewrite by construction) were changed to real writes: tests/test_workflow.py prv12_share10 (scratch copy
for the direct scan) and the future-owner end-to-end s2 answer (a behaviour-neutral comment line).
Adjacent suites run during the cycle: 17 test files touching the attribution / coordinator / contract-baseline
paths (1289 passed before the last fixture corrections), the no-change / candidate-gate / file-integrity / ownership
suites (419), the attempt-evidence explain and T6 suites (59). Targeted certification @ b7824e6: 145 passed, ruff
clean, pylint exit 0 (repair-002/TARGETED_CERTIFICATION.txt). Independent review, full suite, merge, certification
and the 12-task rerun follow in 12.6.

### 12.6 Independent review and reconciliation (commit 3a62740)

Review (a fresh same-class agent; verbatim in repair-002/INDEPENDENT_REVIEW.md): APPROVE WITH CHANGES - D1/D2/D3
correct and fail-closed, no false success constructible through the attribution owner, the covering run or the
resolver; one MAJOR finding on D4's follow-up: a direct `kriya generate` whose every file came back byte-identical
ended PASSED with files [] and no decider (model bytes alone completing a run). Reconciliation (repair-002/
REVIEW_RECONCILIATION.md): F1 FIXED - typed stop `unverified_no_change` / NO_CHANGE_UNVERIFIED through the repair path
unless the caller has a downstream decider (the milestone driver's no_change_verification, flagged typed on the
attempt context); F2 FIXED - the raw suite verdict (`suite_success`) stays on every attributed gate outcome, failures
included; F3 FIXED - the no-change proposal unions only the unit's planned identical paths (a restored unplanned file
is never "proposed"); F4 FIXED - one `artifact_resolution_scope` helper for both resolver sites; F5 FIXED - a
Developer-path refusal test (measured first: the structured protocol rejects an answer naming another path before
resolution, so the invented path enters through the Developer's result list); F6 FIXED - non-vacuous assertion; F7-F10
documentation. Found during reconciliation (own defect, reported): under D4 the enforce controller's reopened owner
regenerating identical bytes became a repairable refusal and burned the repair budget before the controller's
`after == before` rejection; it now declares the typed VERIFICATION_RETRY_NO_CHANGE_POSSIBLE stop on its Failure
(diagnostics[NO_PROGRESS_STOP_KEY]) which the retry strategy - the flag's single owner - applies after the
workspace-progress classification: one attempt, then the controller rejects as before. Two tests pinning the old
evidence shape (one terminal event; attempt.failed == [(1,"test")]) were updated to the typed shape. A regression
during reconciliation (an import edit lost to an earlier sort: F821, 36 tests failing with the NameError swallowed into
no_progress) was caught by ruff before any commit. After reconciliation: 233 tests across the repair, reopen,
retry-admission, no-change, T6, PRD-024, milestone and tripwire files pass; ruff clean; pylint exit 0. The canonical
full suite, merge, certification and the 12-task rerun follow in 12.7.
Full-suite fallout (commit 740d8d8; repair-002/REVIEW_RECONCILIATION.md, trailing section): the first canonical run
@3a62740 failed 30 tests, all own defects of the reconciliation - the `reopened_owner` keyword inserted into the
resume-fingerprint call instead of the executor forwarding call (20 resume tests), the direct-goal stop placed
before the deterministic gates (3 run_attempt tests, now at the terminal point), the real-engine no-change milestone
test whose convergence rested on the identical COMMIT the model-decided completion S4c-1 forbids (now asserts:
completes without a commit, not reused on resume, NO_COMMITTED_OUTPUT; a no-change unit's unchanged planned files
are reported and established for later units), an FS-1A state double and the gate-outcome inventory pin. A second,
scoped independent review covers the two post-review commits (3a62740, 740d8d8) before the merge.
Second scoped independent review (commits 3a62740, 740d8d8; verbatim repair-002/INDEPENDENT_REVIEW_2.md): APPROVE WITH
CHANGES; reconciled in commit 431fd9f (repair-002/REVIEW_RECONCILIATION.md, second table). F1 FIXED: a reopened owner
that changed one planned file and returned another identical is decided by the controller's own byte-change acceptance
(no identical path proposed, no typed stop; two-planned-file reopen test). F2 is an OWNER DECISION, not changed: a
milestone that commits nothing without a deterministic no-change proof completes with the refusal recorded on its
proof and is never reused (PRD-008 S4c-1's tested contract); failing it typed (option ii) was implemented, measured to
contradict 14 S4c tests, and reverted - registry row MILESTONE-ZERO-COMMIT-COMPLETION-001 (P2, DEFERRED). F3-F5 FIXED
(failure-report categories for the two no-change failure types, the scope helper's documented UNRESTRICTED reading,
direct guard tests for the gate-declared no-progress stop). Canonical full suite @740d8d8 (before the second review's
fixes): 9816 passed / 0 failed (repair-002/FULL_SUITE_740d8d8.log); the certifying run is the one on 431fd9f.

### 12.7 Owner decision: MILESTONE-ZERO-COMMIT-COMPLETION-001 -> option (ii), before merge (2026-10-09)

The owner chose option (ii): "A milestone must not report success when it committed no effective output and
deterministic no-change verification refused to prove the goal was already satisfied." Required invariant on every
execution path: effective mutation committed and verified -> may complete; OR deterministic no-change authority proves
the required state already satisfied -> may complete; otherwise a typed failure/refusal. Developer/model output alone
never establishes milestone completion. Implementation: `_complete_milestone` (kriya/workflow/milestones.py) returns a
typed error for a zero-commit milestone whose `no_change_verification` refused; `complete_unit` turns it into the
unit's typed failure (`status: no_change_unverified`, `reason_codes: [NO_CHANGE_UNVERIFIED]`, `no_change_refusal`),
nothing persisted as complete (not in `completed_milestone_ids`, no completion proof, hence never reusable). The PRD-008
S4c handover's S4c-1 section records the superseded contract (handover/PRD-008_S4C_CODING_HANDOVER.md). The S4c tests
written against the superseded contract are re-pinned to the typed stop with positive controls (a committed verified
mutation; a deterministically verified no-change with sufficient coverage -> VERIFIED_NO_CHANGE) and negative controls
(all-identical Developer output; empty/no-effective mutation; refused verification; retry/resume; no path converting a
refusal into success). No deterministic authority is invented for free-text criteria. The Option-1 full-suite run on
431fd9f is historical evidence only; the Option-2 revision is re-reviewed, re-certified and becomes the FINAL
EXECUTABLE for the 12-task rerun.
Third scoped independent review of the option-(ii) commit 42b59a9 (verbatim repair-002/INDEPENDENT_REVIEW_3.md):
APPROVE WITH CHANGES, reconciled (repair-002/REVIEW_RECONCILIATION.md, third table). F1 FIXED: the decision runs
before any side effect and without a `files` guard - zero committed cycles plus a refused verification fail typed
whatever the result reported; a result that reports files without a committed cycle is its own typed code
COMMIT_EVIDENCE_MISSING (since renamed REPORTED_OUTPUT_UNCOMMITTED; the RunRecord is the only authority for committed output); ten mocked driver call sites now commit
a real cycle through the terminal-commit seam (two capability-bookkeeping tests are re-pinned to the typed stop). F2 DISPOSITION (b): the integration pass is not a mutation unit - its
completion authority is the plan-level original-requirement verification (PRD-020) plus its gates; a zero-change
integration pass is the normal shape when the milestones did the work (documented at the decider and the
discriminator; no deterministic authority invented). F3/F5/F6/F8 FIXED (decision before side effects; registry row
CLOSED; vacuous assertions dropped; docstring). The full suite on 42b59a9 (1 failed / 9821 passed) exposed one more
fixture whose second milestone rewrote the first's file byte-identically (tests/test_prd020_mutation_scope.py): each
unit's write now names its writer. MEASURED: the twelve frozen tasks run through the enforce controller (every
C2-*/P5-* generate.log carries workflow_controller "Current subtask" lines), never the milestone driver.
Fourth scoped independent review (commit eecb6f3; verbatim repair-002/INDEPENDENT_REVIEW_4.md): APPROVE WITH CHANGES,
reconciled in the next commit (repair-002/REVIEW_RECONCILIATION.md, fourth table). F1 FIXED: the reported-but-uncommitted
branch has its own typed code REPORTED_OUTPUT_UNCOMMITTED (the previous name collided with milestone_completion's reuse
reason COMMIT_EVIDENCE_MISSING), a direct completion-step control (no side effect), a driver-level regression (status,
reason code, nothing completed or established, a rerun fails the same way) and mutation control D5-M2 (the files guard
reintroduced). F2 CAVEAT RECORDED, owner decision raised: the integration pass's PRD-020 authority blocks an unverified
original requirement only under the "block" requirement policies, which the production runtime profile seals - the
twelve frozen tasks run under `runtime_profile: production` (MEASURED in their configs); under the default "record"
policies a milestone plan can end success with every requirement UNVERIFIED (pre-existing; registry row
INTEGRATION-PASS-REQUIREMENT-POLICY-001, P2, DEFERRED, owner decision). F4 FIXED: M2's invocation carries its own
capabilities (PRD-029 bookkeeping test extended to two committing milestones). F5/F6/F7 FIXED (helper cleanup; "ten
call sites"; the handover records committed with the slice). The full suite on eecb6f3: 9822 passed / 0 failed; the
certifying run is the one on the reconciled commit.
Fifth scoped independent review (commit 4f593fe; verbatim repair-002/INDEPENDENT_REVIEW_5.md): APPROVE WITH CHANGES,
all MINOR/NOTE, reconciled in the next commit (fifth table in repair-002/REVIEW_RECONCILIATION.md): the two "no side
effect" controls now hold an uncommitted copy of the reported file, so they discriminate the decision's position (mutation
D5-M3: the decision moved behind the established-context loop establishes it; both tests fail there); the driver test
reruns on one workspace through the resume path; the PRD-020 caveat states the full rule (VIOLATED always blocks; an
unverified requirement blocks under the "block" policies or, whatever the policy, when the verifier reported it missing -
GR-R0, TRACED); registry/record wording. The full suite on 4f593fe: 9823 passed / 0 failed; the certifying run is the
one on this reconciled commit, re-run with the mutation controls re-recorded on it.

## 12.8 Certification of the second repair cycle and the final executable

Certified on 557035d (branch repair/c2-d1-d4 fast-forwarded into main 2026-10-09, branch deleted): full suite 9823
passed / 0 failed / 0 errors, root pollution none, exit 0 (certification/full_suite_run6_557035d.log); mutation controls
re-recorded on the same commit, 15/15 killed (repair-002/MUTATION_CONTROLS.md); ruff 0, pylint exit 0; five scoped
independent reviews reconciled. Models, qualification, budgets and the frozen task/oracle content are unchanged
(`git diff 08a57e6..557035d` touches kriya/, tests/ and handover/ only). FINAL_EXECUTABLE_SHA = 557035d. The complete
frozen primary six and cohort-2 six are rerun from it (repair-002/run_final_12.sh, `-final` output directories; the
earlier evidence is never touched); their classification and the final readiness verdict follow in 12.9. Not pushed.

## 12.9 The final twelve from 557035d and the final readiness verdict (2026-10-09)

Run 11:58-14:36 local by repair-002/run_final_12.sh (serial, one run per task, `-final` directories, false-success guard
never fired); classification by hand under cohort-002/CLASSIFICATION_RULES.md with the exported candidates judged by the
frozen external oracles (repair-002/final_candidate_judge.sh). Table, tally and gate: repair-002/FINAL_GATE.md.
GENUINE 6/12 (primary T1, T4, T5, T6 = 4/6; cohort 2 S1_A, S6_A = 2/6), SAFE_FAILURE 5 (T2, T3, S3_A, S4_A, S4_B - every
staged candidate wrong or none produced; refusals correct), FALSE_NEGATIVE 1 (S2_A), FALSE_SUCCESS 0, authority 0,
corruption 0, leaks 0. **BACKEND READINESS: NO** (cohort 2 2/6, combined 6/12, two unresolved P1 defects).
Live confirmation of the repair cycle: D1 attributed the pre-existing failure non-blocking at twelve gate sites of S2_A;
D1b made NO_MUTATION_REQUIRED reachable (S6_A GENUINE); D2 let S3_A's plan validate (no MISWIRED edge); D4 typed-refused
S4_B's identical rewrite (T2's attempt 5 was an explicit NO_CHANGE answer refused by the existing OD-3 path); D3's live symptom was not reached (S4_A died in planning - its closure rests on the
deterministic reproducer and frozen-workspace remeasurement).
New defects (recorded, not repaired - the authorized cycle is spent): ENFORCE-PARTIAL-NO-CHANGE-COVERAGE-FREE-TEXT-001
(P1 OPEN: D4 made an identical planned file a no-change proposal, and OD-3 demands deterministic coverage of the unit's
free-text criteria for it even though the unit's other file carries a gate-verified mutation; S2_A's correct attempt-1
candidate was refused) and SUITE-PRESERVATION-CLOSURE-BASELINE-ATTRIBUTION-001 (P1 OPEN: third baseline-attribution
site, requirements.py close_suite_preservation_requirements). Observations REGRESSION-ATTRIBUTION-UNAVAILABLE-DETAIL-001
and VERIFICATION-UNIT-ENV-FALLBACK-001 (P2 DEFERRED, owner decisions). The registry tripwire is RED on this records commit
by design while P1 rows are OPEN; the certified executable 557035d is unchanged and green.

## 13. Third repair cycle - operator-controlled (2026-10-09, branch repair/c3-p1 from 5407a80)

Mode: the operator runs every pytest / live execution and returns the output; one repair branch; one logical defect at a
time; no commit before the operator's verification; no push; readiness bar unchanged.

### 13.1 ENFORCE-PARTIAL-NO-CHANGE-COVERAGE-FREE-TEXT-001 (P1) - owner decision and fix

- Observation (MEASURED, C2-S2_A-final on 557035d): s1 planned pyproject.toml + MANIFEST.in; attempt 1 changed
  pyproject.toml correctly (frozen external oracle ACCEPT 0 / REGRESS 0) and returned MANIFEST.in byte-identical;
  candidate gates PASSED; then VERIFIED_NO_CHANGE_REFUSED for MANIFEST.in (ACCEPTANCE_COVERAGE_INCOMPLETE); attempt 4
  the same; attempt 6 mutated MANIFEST.in and broke the package list -> applied (the FALSE_NEGATIVE). The same
  attempt-1 candidate PASSED at b185bd6, before D4 made an identical rewrite reach the proposal.
- Producer (TRACED): attempt.py commit batch (D4) -> `state.identical_rewrites`; `_verified_no_change_proposal`
  returns the identical planned file as the partial proposal (OD-3 reading 2); after the terminal regression
  `workflow._settle_no_change_proposal` -> `verified_no_change.verify_no_change_unit` ->
  `milestone_completion.criterion_coverage` / `coverage_refusal` over the UNIT's acceptance criteria; a free-text
  (judgment) criterion is UNCOVERED by construction ("No production verifier emits this map yet") -> refusal, the
  attempt's own writes not applied. Contrast: a unit that writes BOTH planned files is never held to per-criterion
  coverage - its mandatory claims close at the candidate gates, the terminal regression and the run's terminal
  requirement gate (S2_A: REQ-1..REQ-9 judged there). The reopened-owner rule of the second review (F1) already
  delivered an identical planned file unchanged when the owner changed another file.
- Root cause (CONFIRMED by the trace and the b185bd6-vs-557035d discriminator): the OD-3 partial contract treated an
  untouched planned file as an obligation of its own requiring deterministic coverage of the whole unit's criteria -
  stricter than any mutation of that file faces and unsatisfiable with free-text criteria - so a correct candidate
  that over-approximated its planned files was refused and the Developer was driven to mutate a file that needed no
  change. Not model behaviour.
- Owner decision (2026-10-09, P1-1 semantics): a planned-file list is execution intent, not verification authority.
  If the unit carries at least one effective authorized mutation and every mandatory claim closes at its bound gates /
  terminal authorities, the unit may complete with another planned file byte-identical; an untouched planned file is
  never independently an unsatisfied obligation. A unit with zero effective mutation keeps the deterministic no-change
  contract; model output never establishes no-change success.
- Fix (smallest, `kriya/workflow/attempt.py` only): `_verified_no_change_proposal` keeps computing the untouched
  planned paths (NO CHANGE answers + identical rewrites that explain exactly the never-written planned files; the
  reopened-owner special case is subsumed and removed); the call site proposes them as a no-change ONLY when
  `state.all_files_written` is empty. Otherwise they are delivered unchanged: excluded from the completeness check,
  recorded as `unit.planned_files_delivered_unchanged` (subtask, paths, identical_rewrites, answered_no_change,
  written_paths), never applied (not in all_files_written; identical rewrites still reported as `unchanged_files`),
  and the unit runs the ordinary mutation path. `_settle_no_change_proposal`, `verified_no_change.py`,
  `milestone_completion.py`, the D4 commit batch and the result shape are unchanged.
- Reproducer first (`tests/test_backend_final_closure_005_partial_no_change.py`, reading-2 harness, both untouched
  shapes: identical rewrite = the measured mechanism, explicit NO CHANGE = T2 attempt 5), MEASURED pre-fix by the
  operator on repair/c3-p1 (2026-10-09): both shapes refused on every attempt with ACCEPTANCE_COVERAGE_INCOMPLETE
  (identical: quality_gates_exhausted; no_change: no_progress), the gate-bypass controls PASS, 2 failed / 29 passed.
- Predicted post-fix: both shapes complete with A applied and B byte-identical, one delivered-unchanged event, no
  proposal / refusal events; the zero-mutation control (A identical + NO CHANGE for B, judgment criterion) is still
  refused with ACCEPTANCE_COVERAGE_INCOMPLETE and nothing delivered; a failing compile gate still applies nothing;
  D4 t1/t2 (S4_B shape) unchanged; the owner-reopen two-file case delivers HELPER unchanged on both runs. The S2_A
  shape ends unit s1 at attempt 1 with pyproject.toml applied - and still blocks at REQ-9 until 13.2 (P1-2).
- Assertions changed by the decision (the completion/bytes assertions stay; only the event shape moved):
  partial_no_change r2 "verifies no-change for B" (now delivered unchanged) and the judgment-criterion refusal (now
  the zero-mutation control); D4 t3 (`partial` -> delivered); owner-reopen two-file case (`proposed == [[HELPER]]`
  -> nothing proposed, delivered twice); D4 t5 (the retry that restores B to baseline bytes: `verified_no_change`
  -> delivered on attempt 2) - missed in the first flip list, found by the operator's post-fix step-1 run (1 failed /
  30 passed: its completion, applied-set and byte assertions held; only the event unpack failed).
- Verification (operator runs, 2026-10-09): step 1 (partial_no_change + D4 modules) 31 passed / 0 failed after the
  t5 assertion update; step 2 (enforce no-change, owner reopen, failure reporting, S4b/S4c milestone completion and
  resume, plan executability, LR-R1 M1/P4/P5 modules) reported all green. Mutation controls: repair-003/mutations
  (operator run 2026-10-09, attempt.py restored byte-identical): M1 restores the old partial proposal -> KILLED, 6 failed
  (p1 x2, r2 delivered, D4 t3/t5, owner-reopen two-file); M2 never proposes -> KILLED, 4 failed (zero-mutation control,
  D4 t1/t2, owner-reopen no-change case); M3 drops the delivered event -> KILLED, 6 failed (the same six as M1).
  Registry row ENFORCE-PARTIAL-NO-CHANGE-COVERAGE-FREE-TEXT-001 -> CLOSED with this slice.

### 13.3 WORKSPACE-CONTENT-HASH-IGNORED-KRIYA-DIR (P1, found by the P1-2 reproducer) - fix

- Observation (MEASURED, 2026-10-09): P1-2 reproducer case 3 (a pre-existing failure whose text the candidate changed)
  blocked STABILITY_UNRESOLVED with level1 CHANGED_FAILURE, no confirmed regression and exactly ONE base run - the
  owner's two bounded stability replays never ran. Probe 1 (operator, repair-003/scratch/p1_2_revision_probe.py): the
  toy workspace's content hash is a value before one gate run and None after it, git status clean both times. Probe 2
  (Claude, p1_2_revision_probe2.py): cwd, tempdir and os.environ unchanged; of the hash's four git steps only
  `git add -A -- . ':!.kriya'` fails (rc=1, "The following paths are ignored by one of your .gitignore files: .kriya").
- Producer (TRACED + MEASURED on git 2.54.0, repair-003/prefix/WORKSPACE-CONTENT-HASH-IGNORED-KRIYA-DIR_measurements.txt):
  `kriya/workflow/checkpoint.py::compute_workspace_content_hash` names `.kriya` in its exclusion pathspec; git rejects a
  pathspec naming an ignored path that exists on disk, whatever the staged set (correct in every variant);
  `advice.addIgnoredFile=false`, `--ignore-errors` and `:(exclude)` all still exit 1. `.kriya` does not exist before the
  first gate run, so the first identity read works and every later one returns None. The sibling dirty check in
  `compute_workspace_fingerprint` (git status with the same pathspec) exits 0 and is unaffected.
- Root cause: CONFIRMED. Classification: KRIYA_PRODUCT, supported-backend correctness, brownfield-configuration shape
  (a repository that ignores Kriya's runtime directory - the natural thing to do). The twelve frozen repositories do
  not ignore .kriya/ (C2-S2_A: untracked in git status), so the final twelve never reached it. Blast radius: every
  consumer of the identity fails closed - checkpoint identity, PRD-024 baseline capture (indeterminate -> hard stop
  under `required`), REG-R1 stability guard (BASELINE_REVISION_CHANGED -> STABILITY_UNRESOLVED, no replay), the
  attribution owner's `current_revision`.
- Owner decision (2026-10-09): fix now as its own slice (it blocks the P1-2 attribution/stability validation); never
  work around it by un-ignoring .kriya/ in the P1-2 toy workspace; contract: the root `.kriya/` is ALWAYS excluded
  from the identity (absent, present and ignored, present and untracked, tracked - the folded-in base commit keeps
  tracked `.kriya` content at HEAD bound); a nested directory named `.kriya` stays repository content; unrelated
  ignored files keep their semantics; real content changes still change the hash.
- Fix (one function): stage `git add -A -- .`, then `git rm -r -q --cached --ignore-unmatch -- .kriya` drops the root
  runtime directory from the scratch index (top-anchored like the pathspec was); the docstring states the contract.
  Measured equivalent on the throwaway repository: rc=0 and the identical tree with .kriya ignored, present-not-
  ignored and absent.
- Reproducer first (`tests/test_workspace_content_hash_kriya_dir.py`), MEASURED pre-fix by the operator
  (repair-003/prefix/WORKSPACE-CONTENT-HASH_reproducer_before.txt): the three None-hash cases failed exactly as
  predicted (measured shape, present_ignored, real-content); two further failures (present_not_ignored, tracked) were
  a status-filter bug in the test helper (column of the path in porcelain output), fixed with the fix stage, not the
  product; absent / nested / non-git controls passed.
- Mutation control: repair-003/mutations/run_hash_mutation.sh restores the old pathspec (no removal step) - the
  reproducer must fail on the three None-hash cases.
- Verification (operator runs, 2026-10-09): reproducer 8 passed; mutant H1 (old exclusion pathspec restored) KILLED -
  3 failed / 5 passed, checkpoint.py restored byte-identical; adjacent checkpoint / workspace-identity / resume /
  subtask-checkpoint / validation-baseline / analyzer-cache / proposal-promotion / REG-R1 / workflow modules: 1134
  passed. Registry row CLOSED. Then back to P1-2 case 3 (13.2).
