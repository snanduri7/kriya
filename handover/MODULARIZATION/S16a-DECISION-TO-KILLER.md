# S16a decision-to-killer table (correction unit S16a, TYPE C: autonomy.mode closed vocabulary; OI-130, AUTONOMY-MODE-FAIL-OPEN)

Commits: a81d9e1 characterization (16 pins + 158 strict-xfail expected-to-flip), 068e5f4 test-only "autonomous" -> "guardrails"
(behaviour-neutral at base), d00f82d own test defect (malformed KUP fingerprint), 7257ab0 correction, 5462ca0 model_construct killer,
then docs. L1 = tests/test_s16a_autonomy_mode_vocabulary.py; L2 = test_sec009_config_authority, test_sec009_p2_authority_approval,
test_s3_config_authority_enabling_fields, test_config, test_kup_config_approval, test_strict_doubles, test_kup_execution.

## 1. Preflight derivation (value | where produced | where consumed | what it means)

| value | where produced | where consumed | what it means |
|---|---|---|---|
| `human-in-the-loop` | schema default `AutonomyConfig.mode`; packaged `default_config.yaml`; `kriya init` / desktop Project Setup profile (`application/project_init._AUTONOMY`); 447 of 472 user configs under ~/kriya-* (MEASURED); 26 of 26 repo YAMLs; docs | every production comparison (TRACED): `stage_apply` need_human_approval + PRD-033 trigger (204, 212), `spec_compliance` contract escalation (648), `runtime_verification` command approval (800) and managed-service block (1105), `policy.approval.defer_execution_until_source_review` (Gap B; via attempt 2507 and owner_recovery 997), `subtask_executor._hitl_blocks_tool_without_confirmation` (149), `tools.tool.ModelToolInvocation.reviews_every_change` (constant `HUMAN_IN_THE_LOOP`), `application.run._interaction` (change_review); KUP/GUI display only (`autonomy_mode`, `approvals`) | every change held for review; no model-authored content executed before source approval; runtime commands not authored by the user reviewed; managed service refused; planned tool steps without review refused; contract escalation possible |
| `guardrails` | docs (README, user_guide: "any other value behaves as guardrails"); 225 test assignments; 19 user configs (MEASURED) | NO positive comparison anywhere: it is the else branch of every site above | approval only on sensitive path, risk_threshold_lines or process profile |
| `full-autonomy` | nowhere (repo, docs, tests, user configs; MEASURED by git grep and the config scan) | nowhere | FINDING: not a mode; refused as AUTONOMY_MODE_UNKNOWN |
| `autonomous` | tests only (20 assignments + 3 kernel-golden harness assignments + 2 parametrize values + test_kup_execution display assertion), UI fixture `ui/shared/src/cockpit/providers/fixture/fixtureExecution.ts:282` (display only) | nowhere in production | FINDING: test shortcut for "not human-in-the-loop"; tests changed to `guardrails` (068e5f4); the UI fixture is left (display fixture, UI test not run) |
| `full-auto` | tests only (test_s1_toolbox_authority MODES, test_s3 parametrizations) | nowhere | FINDING: test shortcut; dropped (7257ab0) |
| environment variable | none (kriya/config/environment.py closed vocabulary has no mode variable; TRACED) | - | N/A |
| CLI option | none (no `--mode`/autonomy option; `kriya init` writes the profile above; TRACED) | - | N/A |
| KUP / GUI write | none (KUP reports `autonomy_mode` as `string|null`; the app has no selector, `application.run` docstring; TRACED) | - | N/A |

Migration impact (MEASURED, READ-ONLY): 472 YAML files with a top-level `autonomy:` under ~/kriya-live-demo and ~/kriya-*:
447 human-in-the-loop, 19 guardrails, 6 absent, 0 outside the vocabulary. Repository YAMLs: 26 human-in-the-loop, 0 outside.

## 2. Reproduction (MEASURED, scratch s16a/{before,after}.txt, scratch s16a/repro.py)

Before: a workspace kriya.yaml `autonomy: {mode: "human-in-the-loop "}` (trailing space) approved with `kriya authority approve`
LOADED as `'human-in-the-loop '`; so did `Human-in-the-loop`, `human-in-the-lop`, `guardrail`, `autonomous`, `""`; null/1/true/list
were refused only as an untyped pydantic ValidationError AFTER an approval had been written for them; programmatic construction,
attribute assignment (even None, 1) and model_copy(update=) accepted anything. Consequences of the accepted typo:
defer_execution_until_source_review False, ModelToolInvocation.reviews_every_change False, the stage_apply human-in-the-loop clause
False, the desktop preflight said configured_mode. After: every one of those is AutonomyModeError with its reason code, at
resolution (nothing approvable) and at every programmatic ingress; absent key still human-in-the-loop.

## 3. Decisions and killers (mutants: campaign 20261019_170655_bf36f753 at 5462ca0, VALID, 21/21 killed at L1, 76 s)

| decision | symbol | risk | killer test | mutation | result | evidence |
|---|---|---|---|---|---|---|
| only the two exact values are modes | `parse_autonomy_mode` membership | an unknown string accepted (the defect) | test_resolution_refuses_an_invalid_mode_before_any_authority_comparison | S16aM01 always accept | KILLED L1 | c1 |
| null is an error, not the default | same, NULL branch | `mode: null` silently becomes the default | same [None] | S16aM02 null -> default | KILLED L1 | c1 |
| no whitespace normalization | same | `"human-in-the-loop "` repaired | same [whitespace ids] | S16aM03 strip | KILLED L1 | c1 |
| case-sensitive | same | `Human-in-the-loop` repaired | same [case ids] | S16aM04 casefold | KILLED L1 | c1 |
| exact `str` type | same, `type(value) is not str` | a str subclass/bool passes | test_a_str_subclass_is_not_a_mode | S16aM05 isinstance | KILLED L1 | c1 |
| non-strings refused | same, NOT_A_STRING | an int/list becomes the mode | test_resolution_refuses_... [1, 0, ...] | S16aM06 return value | KILLED L1 | c1 |
| resolution validates before classification | `resolve_config_state` | invalid value approvable; inspect/approve/desktop paths open | test_resolution_refuses_..., test_the_cli_refuses_typed_and_approves_nothing | S16aM07 skip | KILLED L1 | c1 |
| construction validates | `AutonomyConfig._closed_mode_vocabulary` | AppConfig(...)/strict_config accept it | test_programmatic_construction_refuses_an_invalid_mode | S16aM08 skip | KILLED L1 | c1 |
| assignment validates (mode only) | `AutonomyConfig.__setattr__` | `cfg.autonomy.mode = x` | test_assignment_refuses_an_invalid_mode_and_keeps_the_old_value | S16aM09 skip | KILLED L1 | c1 |
| model_copy(update) validates | `AutonomyConfig.model_copy` | update bypasses pydantic | test_model_copy_updates_refuse_an_invalid_mode | S16aM10 skip | KILLED L1 | c1 |
| AppConfig.model_copy, dict autonomy | `AppConfig.model_copy` | replaced section unvalidated | same | S16aM11 skip | KILLED L1 | c1 |
| AppConfig.model_copy, object autonomy | same | a model_construct'd AutonomyConfig swapped in | test_an_unvalidated_autonomy_object_cannot_be_swapped_in_through_model_copy | S16aM12 skip | KILLED L1 | c1 |
| schema default unchanged | `AutonomyConfig.mode` Field | absent key no longer human-in-the-loop | test_absent_key_is_the_documented_default_everywhere | S16aM13 guardrails | KILLED L1 | c1 |
| packaged default unchanged | default_config.yaml | same, through load_config | same | S16aM14 guardrails | KILLED L1 | c1 |
| vocabulary closed | `AUTONOMY_MODES` | a mode with no semantics admitted | test_resolution_refuses_...['full-autonomy'], test_the_vocabulary_and_the_reason_table_are_closed | S16aM15 + full-autonomy | KILLED L1 | c1 |
| empty reported as EMPTY | EMPTY branch | wrong reason code | test_resolution_refuses_...['', '   '] | S16aM16 | KILLED L1 | c1 |
| variants reported as NOT_CANONICAL | NOT_CANONICAL branch | wrong reason code | same [variant ids] | S16aM17 | KILLED L1 | c1 |
| variant check on the stripped value | `stripped.casefold()` | leading/trailing whitespace reported UNKNOWN | same [whitespace ids] | S16aM21 | KILLED L1 | c1 |
| `kriya authority` types it | `_authority_user_errors()` | traceback instead of an operator error | test_the_cli_refuses_typed_and_approves_nothing | S16aM18 drop | KILLED L1 | c1 |
| typed error is not a ValueError | `AutonomyModeError(KriyaError)` | pydantic wraps it into an untyped ValidationError | test_programmatic_construction_refuses_an_invalid_mode | S16aM19 + ValueError | KILLED L1 | c1 |
| closed reason table | `AutonomyModeError.__init__` | an unlisted code | test_the_vocabulary_and_the_reason_table_are_closed | S16aM20 unchecked | KILLED L1 | c1 |

c1 = campaign 20261019_170655_bf36f753 (scratch s16a/mut_run.txt). Survivors: none.

## 4. Remaining bypass (honest)
`AutonomyConfig.model_construct(...)` / `AppConfig.model_construct(...)` and `object.__setattr__` validate nothing by pydantic's
contract; no production caller (MEASURED, grep). The consumers keep their current reading of a string (owner question in the S16a
report: whether consumers should also map a non-vocabulary string to the strictest reading per site).
