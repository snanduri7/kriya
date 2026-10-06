# FS-1C2 B3: human-bound acceptance authority

**Branch:** `feature/lr-r1-b2a` from the certified B2-c tip `43fad89`. Implementation `4d70723` + `5ab9d69` (certified
revision). No live model run; no approval self-authorized; nothing pushed or merged. **Labels:** MEASURED, TRACED.

## What it is (and is not)

An operator approval that ONE exact acceptance suite is SUFFICIENT evidence for ONE exact GENERAL requirement. It is
human authority, never a proof: the outcome is `HUMAN_ACCEPTED` (closure method `human_bound_acceptance`), never
`CLOSED_BY_EVIDENCE`; B2 EXACT closure is unchanged and needs no approval.

## Design (TRACED; `kriya/workflow/acceptance_approval.py`)

- Artifact `kriya.acceptance_approval/1`, passed as `--acceptance-approval` (requires `--acceptance`); refused unless
  outside the workspace (SEC-009's rule for operator authority). Each entry has exactly: requirement id (one REQ id;
  no pattern, duplicate, unknown or scope-only id), requirement-text sha256, goal digest, acceptance digest, the exact
  case list, the runner contract digest, the base revision and the literal `accept_suite_as_sufficient: true`.
  Validated, digested and stored at `<state>/acceptance-approvals/<sha256>.json` before any model call; `cli.py` is
  the only `load_approval` caller (structural test).
- Closure (`close_requirements_with_acceptance`): only a GENERAL claim whose suite executed and fully passed under
  every B2 integrity check is checked against the approval at the closure (`approval_problem`: id, text, goal,
  acceptance digest, cases, runner contract, the run's base revision). Match -> HUMAN_ACCEPTED; else
  `ACCEPTANCE_GENERAL_RULE_UNPROVEN` with the mismatch named. VIOLATED and every INDETERMINATE outcome are unchanged.
- Read time (`_effective_closure`): a human BEHAVIOR record must name this requirement and its exact text; a whole-
  requirement closure by the human method is ignored.
- Resume: the approval digest joins the goal-side resume fingerprint (planning and the candidate depend on it, so an
  approval added or changed later regenerates the candidate) and the new optional `ControlState.
  acceptance_approval_digest` (enforce resumes no subtask recorded under another approval; reason
  `ACCEPTANCE_APPROVAL_CHANGED`). Unset, the ControlState hash is byte-identical (pinned to `43fad89`'s digest).
- Found while implementing (TRACED): the candidate resume artifact did not depend on the verification-policy
  fingerprint that carries the acceptance digest, and enforce resume checked only workspace drift - so without the two
  bindings above an approval added after generation could have elevated an already-generated candidate (H6).

## Certification on `5ab9d69` (MEASURED)

| Gate | Result |
|---|---|
| focused `tests/test_b3_human_acceptance.py` | 47 passed (H1-H9 and the owner's 24; real pytest runs, real Maven for 21) |
| B3 mutation run 1 (`f333f7a`) | 20 run, 19 killed; survivor: the id lookup, masked by text/case binding -> explicit id binding added, target re-run as a double mutant (each single guard measured masked, both together killed) |
| B3 mutation run 2 (`5ab9d69`) | **20 run, 20 killed** |
| B2-a run 5 / B2-c run 3 / (B2-COV unchanged code) | 24 killed + 1 equivalent (`--noconftest`) / 18 killed |
| ruff / pylint | 0 / exit 0 |
| full suite (`full_suite_5ab9d69.txt`) | **9123 passed, 0 failed, 0 errors** |

Test changes (recorded): PRD-020 mechanics stand-ins now use a neutral whole-closure method (B3 never closes whole
requirements); the ControlState hash-stability test excludes the new optional field and pins the pre-B3 digest.

## Contained JVM acceptance (MEASURED, `evidence/b3/contained_jvm_43fad89.json`) - PASS

Deterministic B2-c fixture through the production OCI path (`contained_execution_required: true`, backend `oci`,
fresh empty Maven cache). Cold cache: offline compile fails -> registry-scoped acquisition -> offline compile passes ->
offline test fails (provider not cached) -> registry-scoped acquisition -> offline retry passes with a complete report,
both identities PASSED (31.8 s). Warm runs fully offline (~5 s); wrong candidate VIOLATED offline. Egress recorded
`denied`. Residual (existing FS-1A behaviour, not changed): on a cold cache the acquisition step also executes the
selected tests; the report read is the one the final offline retry rewrote.

## A3/A4/A5 B3 precheck (no model; `evidence/b3/a345/`, `a345_b3_precheck_4d70723.json`)

Goals unchanged; all GENERAL. Minimum suites test only what each goal states; templates are UNAPPROVED
(`accept_suite_as_sufficient: false`) - the owner must review and flip each before any live run.

| | suite (cases) | base | diagnostic reference | B3 bindable |
|---|---|---|---|---|
| A3 commons-lang `clamp` | 4: below min -> min, above max -> max, otherwise value, min > max -> IAE (Apache header for RAT) | HARNESS_COMPILE_FAILED (method absent; INDETERMINATE) | all 4 PASSED | yes |
| A4 commons-cli `hasAnyOption` | 4: one of the names present -> true, same decision as `hasOption`, none present -> false, no names -> false (Apache header) | HARNESS_COMPILE_FAILED (INDETERMINATE) | all 4 PASSED | yes |
| A5 petclinic `PetTypeFormatter` | 2: `"  bird "` -> Bird, no match -> ParseException (Mockito without `@ExtendWith`) | VIOLATED (parse throws on `"  bird "`) | both PASSED | yes |

The diagnostic reference implementations are harness-only (they show each suite compiles and can pass); they are not
part of any artifact and were never shown to a model.

## Not done / deferred

Gradle acceptance; negative-model authority; PLAN-R1; classifier expansion; goal rewriting; Graphify live validation.
