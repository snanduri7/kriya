# GR-R1: explicit requirement contract (A) and attempt spec-gate model authority (B)

**Branch:** `feature/lr-r1-gr1`, from the certified GR-R0 tip (`c0a3e91` code, `05de1b3` evidence).

| Commit | Content |
|---|---|
| `4cc8951` + `b85c4de` | GR-R1B |
| `6ea2514` + `39fb8d1` | GR-R1A |
| `d9de0f6` | mutation-survivor test and Graphify proposal artifacts |
| `1725efc` | evidence only |
| `a049c02` | M1 inventory pin 88 -> 86 (the two failure sites GR-R1B removed). **Certified executable revision**; production code identical to `d9de0f6` |
| this report | evidence only |

No live model. No Graphify run. Nothing pushed or merged.

**Labels:** MEASURED, TRACED, INFERRED, CONFIRMED.

## GR-R1B: attempt spec-gate model authority (CONFIRMED, fixed)

**Pre-fix (MEASURED, `evidence/gr0/gr1b_spec_gate_prefix_c0a3e91.txt`).** The verifier verdict was scripted, with no model. A correct candidate that passed every deterministic gate was failed by a model "missing" alone:
- 4 Developer calls;
- the run ended `no_progress`;
- 4 of 7 controls failed.

**Producer (TRACED).** `attempt.run_attempt`'s Goal Spec Compliance block raised `goal_spec_compliance` for a non-compliant verdict naming requirements, and `spec_compliance_indeterminate` for a verdict contradictory twice in a row.

**Fix.**
- Both are now advisory: no attempt failure, no retry, no fallback.
- The gate outcome carries status `model_reported_missing` or `model_indeterminate`, reason code `SPEC_COMPLIANCE_MODEL_ADVISORY`, and `model_missing_requirements`.
- Run event `spec_compliance.model_advisory` (ADVISORY), mirrored into M1 by `GenerationState.record_event`.
- GOAL_SPEC_REQUIREMENT is recorded INDETERMINATE (never settled) with the model's words.
- The terminal gate decides under GR-R0: no trusted closure means UNVERIFIED and blocked; B2 means CLOSED_BY_EVIDENCE; B3 means HUMAN_ACCEPTED; counter-evidence means VIOLATED.

**Unchanged:**
- compile, parse, static analysis, test, acceptance counterexample, scope and file-integrity gates;
- the strict-mode `verification_infrastructure_failure` when the verifier is unavailable. That is about availability, not a verdict.

**Controls** (`tests/test_gr1b_spec_gate_model_authority.py`, 7):
1. model missing: 1 Developer call, gate status `model_reported_missing`, terminal UNVERIFIED and blocked, nothing applied;
2. B2/B3 FAIL + model positive is VIOLATED (GR-R0 controls);
3. no closure + model satisfied: UNVERIFIED, blocked (production);
4. no closure + model missing: UNVERIFIED, blocked, 1 call;
5. compile failure + model satisfied: retry unchanged;
6. failing test gate + model missing: deterministic failure unchanged, and the semantic check never runs;
7. and 8. B2 exact PASS or B3 HUMAN_ACCEPTED + model missing: closure (GR-R0 controls, plus R8 below);
9. the model negative is recorded: advisory event, `requirement.verdicts` with `model_outcome` "violated".

A contradictory INDETERMINATE verdict is advisory too.

**Trade-off (stated, owner-directed).** A candidate that only the model judges incomplete is no longer retried on the model's feedback. Example: the 2026-08-21 `protocolVersion` case in `test_run_attempt_rejects_output_missing_a_goal_named_field`. Such a candidate now reaches the terminal gate, where the unrefuted "missing" blocks it: NO_SUCCESS, never a false success.

**Four tests that pinned the model veto were updated, with intent kept:**
- three in `test_workflow.py`;
- the PRD-020 paraphrasing-plan test, which now proves the terminal gate holds REQ-3 by id and user text.

## GR-R1A: explicit requirement contract (implemented)

**Usage.** `kriya generate -f <goal> --requirements <contract.json>`. The format is `kriya.requirements/1`; the module is `kriya/workflow/requirement_contract.py`.
- **Closed set:** the contract replaces the goal-derived set, never merged. The raw goal is unchanged; it stays the planning, localization and Developer context and the goal identity (`goal_identity`).
- **Refused before any model call:**
  - empty set, duplicate id, unknown kind (only `requirement`/`constraint`), bad id, format or fields (`REQUIREMENT_CONTRACT_INVALID`);
  - a contract inside the workspace;
  - another goal (`REQUIREMENT_CONTRACT_GOAL_MISMATCH`).
- **Authority:** the file must live outside the workspace. It is read and stored content-addressed (`<state>/requirement-contracts/<sha256>.json`) before any model call, and held fixed in memory.

**Bindings:**
- goal digest;
- contract digest, joined into `RequirementSet.digest` as `contract_digest`. Derived sets are byte-identical: digests pinned from `c0a3e91` re-checked;
- direct resume goal inputs (`requirement_contract_digest`);
- enforce `ControlState.requirement_contract_digest`. A change gives `REQUIREMENT_CONTRACT_CHANGED`, and old hashes are unchanged;
- acceptance artifacts, through the set digest;
- B3 `kriya.acceptance_approval/2` with `requirement_set_sha256`. It is required with a contract, so a /1 approval made against the derived set is refused. /1 stays valid in auto mode, so the A3-A5 approvals are unaffected.

**Single seam.** All four derivation sites (CLI acceptance/approval binding, direct workflow, enforce controller) go through `requirement_set_for`.

**Controls** (`tests/test_gr1a_requirement_contract.py`, 29):

| Control | Result |
|---|---|
| R1 | exactly the supplied obligations |
| R2 | issue with title, summary, bug narrative, code fence, command, table, version notes and expected: exactly 2 obligations, the direct workflow judges and reports exactly those, and the raw goal reaches the Planner |
| R3 | "Currently double(5) returns 7 / Expected 10": a correct candidate is CLOSED_BY_EVIDENCE, with no contradiction |
| R4 | contract inside the workspace refused; the bound set is unaffected by edits to the source file; the stored copy matches the digest |
| R5 | resume identity changes, never silently upgraded; ControlState binding; an enforce resume refuses `REQUIREMENT_CONTRACT_CHANGED` |
| R6 | changed goal refused |
| R7 | acceptance naming an id outside the contract refused |
| R8 | /2 approval HUMAN_ACCEPTS even with a model "missing"; /1 refused; an approval for another set refused at load and at closure; set digest binds exact contract bytes |
| R9 | auto mode byte-identical; /1 approvals bind derived sets |
| R10 | model-proposed REQ-3/REQ-9 have no authority |
| Plus | 9 refusal cases and the CLI (binds before any model call; an invalid contract or an unknown acceptance id exits 1 with no LLM client built) |

**Pre-fix.** The capability did not exist (`gr1a_contract_prefix_b85c4de.txt`: ImportError). The symptom it removes was MEASURED in GR-R0: 22 derived obligations.

**Adjacent tests updated:**
- `test_prd008_resume_fingerprints`: the hand-built expected hash drops the new unset field. `content_hash()` is still the pinned `d5209ad7…`.
- The file-integrity audit classifies the new state write site.

## Certification

| Gate | Result |
|---|---|
| Focused | GR-R1A 29, GR-R1B 7 |
| Adjacent: spec-gate and workflow (57 files) | 2,820 passed + 4 updated |
| Adjacent: requirements, acceptance, B2/B3, FS-1, control, resume, integrity, CLI (38 files) | 1,318 passed + 2 updated |
| Mutation run 1 (`39fb8d1`) | 16/17; survivor `requirement-set-digest-ignores-contract`, killed by a new byte-binding test |
| Mutation run 2 (`d9de0f6`) | **17/17 killed**: raw narrative used, contract inside workspace accepted, goal digest ignored, set digest ignores contract, resume fingerprint ignores contract, enforce resume accepts change, B3 ignores set identity, B3 /1 accepted with a contract, unknown acceptance id accepted, duplicate id, unknown kind, empty set, model-generated requirement, model missing fails the attempt, model indeterminate fails the attempt, advisory not recorded, advisory settles the obligation |
| ruff | clean |
| pylint | exit 0 |
| Full suite | At `1725efc`: 9,208 passed, 1 failed. The failure was the M1.0 inventory pin of `record_gate_outcome` sites (88), which GR-R1B lowers to 86 by removing the two model-only failure records; pin updated in `a049c02`. At `a049c02`: **9,209 passed, 0 failed, 0 errors** (536.1 s) |

## Graphify: proposed explicit contract (UNAPPROVED, for owner review)

**Files** in `handover/evidence/gr0/graphify_prospective/`:
- `graphify_requirements_PROPOSED_unapproved.json` (sha256 `addc635a…`): the contract;
- `kriya_acceptance_graphify.py` (`d5afe6db…`): the suite;
- `graphify_approval_TEMPLATE_unapproved.json` (`/2`, `accept_suite_as_sufficient: false`): the B3 template.

They come only from the frozen issue (`goal.txt` `f96bf5a3…`, unchanged). No hidden evaluator, judge output, evaluator-failure knowledge or reference-patch detail was used. The diagnostic reference fix is mine, scratch-only, and never given to Kriya.

### REQ-1

> For the three C# files in the issue's reproducer (Settings.cs, Reader.cs, Local.cs), `graphify extract` produces a `calls` edge from `.A()` to `.Get()` (case 1: `Get<int>("port")`), from `.B()` to `.Get()` (case 2: `this.Get<int>("port")`) and from `.E()` to `.Make()` (case 5: `Make<int>()`).

- **Source:** §Expected, line 67 ("Cases 1, 2 and 5 should produce a `calls` edge to `.Get()` / `.Make()`"). Cases come from the §Reproducer table (lines 56, 57, 60) and files from the §Reproducer code block.
- **Why normative:** it is the issue's stated expected outcome ("should produce"), on its own concrete reproducer.
- **Kriya class:** GENERAL. The classifier reads C# `<int>` as a placeholder and finds no example in its call form. That is conservative; semantically this is a finite, exact example. B3 is therefore needed.

### REQ-2

> For the same reproducer, the non-generic calls keep their `calls` edges: `.C()` to `.GetRaw()` (case 3: `GetRaw("host")`) and `.D()` to `.GetRaw()` (case 4: `this.GetRaw("host")`).

- **Source:** §Expected, lines 67-68 ("exactly as the non-generic controls 3 and 4 do"), and table lines 58-59.
- **Why normative:** the expected behaviour is defined relative to the controls, so they must keep their edges (preservation).
- **Kriya class:** EXACT. B2 closes it; no approval is needed.

### REQ-3

> In C#, a call whose method name carries an explicit type-argument list, with no receiver or through `this`, produces the same `calls` edge as the same call without the type-argument list: the type-argument list is not part of the called method's identity.

- **Source:** §Expected, line 68 ("The type-argument list is not part of the method's identity"), scoped by §Summary lines 5-8 (no receiver, or through `this`).
- **Why normative:** the issue's stated general rule, in §Expected.
- **Kriya class:** GENERAL. B3 is needed.

### Excluded as descriptive or contextual

| Excluded text | Reason |
|---|---|
| The title and §Summary "never gets a `calls` edge" (lines 1, 5) | Describe the defect |
| "Measured on v0.9.56 … v0.9.55" | Version notes |
| "Three files, no corpus…" and `dotnet build` | Reproducer logistics |
| The command (line 51) and the table's v0.9.56 column | Reproducer and observed bug state |
| "2 of 5", "All five target member nodes exist…", "Case 5 shows inheritance is not involved" | Diagnosis |
| §Where it happens (lines 70+) | Implementation hints, not obligations |
| "references[generic_arg]" | Existing behaviour context |

The owner may decide whether "all five target member nodes exist" and "`method`/`inherits` edges are correct" should become preservation requirements. They are not proposed.

**Acceptance rebinding.** Only the markers changed:
- cases 1, 2 and 5 now serve REQ-1 and REQ-3 (they served REQ-15);
- controls 3 and 4 now serve REQ-2.

Assertions are unchanged. The digest changed (`ed8b90b1…` to `d5afe6db…`), so the template was regenerated.

Precheck with the contract (Kriya's real B2-a runner):
- **at base:** REQ-1 and REQ-3 VIOLATED (3 failed), REQ-2 PASSED (controls already work);
- **on the diagnostic reference:** all three PASSED, 5/5.

## Final no-model Graphify preflight (`graphify_preflight_gr1_d9de0f6.json`)

| Item | Value |
|---|---|
| RAW GOAL UNCHANGED | YES (sha256 `f96bf5a3…` before and after; its text reaches the Developer request) |
| AUTHORITATIVE REQUIREMENT COUNT | 3 (set `42961329…`, contract `addc635a…`) |
| NARRATIVE/HEADING REQUIREMENTS | 0 |
| DESCRIPTIVE BUG SENTENCE AS OBLIGATION | NO |
| FULL FILE REQUIRED | NO |
| AUTHORIZED EDIT OPERATION | anchored_edit (windows 5357-73, 5381-97, 5401-17) |
| FINAL REQUEST FITS | YES: 12,799 of 16,096 at the default ratio; about 15,149 at qwen3-coder's qualified ratio |
| NEW MEMBER REQUIRED | NO |
| NEW IMPORT REQUIRED | NO (MEASURED for one valid fix; INFERRED in general) |
| ACCEPTANCE RUNNER | SUPPORTED |
| B3 | PREPARED_UNAPPROVED (REQ-1, REQ-3) |
| MODEL NEGATIVE CAN CONSUME RETRY BY ITSELF | NO |

## Known-issue table (GR-R0 items retained)

| Item | Graphify relevance | Blocks | Status |
|---|---|---|---|
| REQ-DERIVE-ISSUE-NARRATIVE | YES | NO once the owner approves a contract | Resolved generically (GR-R1A); the Graphify contract is proposed, unapproved |
| attempt-level model-negative spec gate | YES | NO | Fixed (GR-R1B) |
| Graphify contract and B3 suite owner review | YES | **YES** | Pending owner review |
| RETRY-NO-INFORMATION-GAIN | YES | NO | Fixed (GR-R0) |
| negative-model-authority (terminal) | YES | NO | Fixed (GR-R0) |
| PLAN-R1 | NO | NO | Unchanged |
| P3-D Python insertion | NO | NO | No new member needed |
| P3-D new-import | NO | NO | No new import needed |
| B3-UX-1 | YES | NO | Use absolute `-f` |
| CLI-UX-1 | YES | NO | Use absolute `-f` |
| ENV-JVM-COLD-CACHE-1 | NO | NO | |
| A5 null-input | NO | NO | |
| unattributed lessons telemetry | NO | NO | |
| FS-1A in-process residual | YES | NO | Disclosed |
| Python deps from pyproject.toml, not uv.lock | YES | NO | Measured working; not lockfile-pinned |
| Classifier reads C# `<int>` as a placeholder (new observation) | YES | NO | Conservative: GENERAL, not EXACT; B3 covers it |

**GRAPHIFY READY = NO.** The explicit Graphify requirement contract and the B3 suite still need owner review.
