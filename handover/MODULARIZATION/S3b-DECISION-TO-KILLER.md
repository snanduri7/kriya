# S3b decision-to-killer table (correction unit S3b, TYPE C: value-bounded containment resource limits; OI-132)

Mutants: `~/kriya-modularization-tools/mutrun.py` v2 (git archive of HEAD f927de5; deselect as S2b). L1 =
tests/test_s3b_resource_bound_value_gating.py + tests/test_s3_config_authority_enabling_fields.py; L2 =
test_sec009_config_authority, test_sec009_p2_authority_approval (never reached).

Campaign (MEASURED): `20261019_211207_be8d1c12` at f927de5: 14 mutants, 14 killed at L1, 0 survived, VALID (tree_diff
empty, no straggler), 53 s.

Reproduction (MEASURED, scratch `s2b/s3b/{before,after}/repro.txt`): before, a workspace kriya.yaml setting each bound
x100, half, equal or -1 LOADED with 0 pending violations (30 rows incl. run_verification_timeout_seconds). `-1` at runtime
(MEASURED on macOS, `before/negative_cpu.txt`): `setrlimit(RLIMIT_CPU, (-1, -1))` raises in the child's preexec, so the
spawn fails; on Linux `RLIM_INFINITY == -1` (INFERRED from the platform definition; not measured here), i.e. unlimited CPU.
After: every raise and -1 -> `ConfigAuthorityError` naming exactly the field, 1 pending violation; half and equal load
with the value effective. Characterization 3651aea: 45 strict-xfail flips, `--runxfail` 45 failed on the unfixed bytes.

| decision | symbol | risk | killer test | mutation | result |
|---|---|---|---|---|---|
| each bound is value-bounded (lower is safe) | `_VALUE_BOUNDED_FIELDS` entry | static SECURITY_AUTHORITY refuses every lower value | test_a_lower_or_equal_bound_from_the_repository_needs_no_approval[*-sandbox_cpu_seconds] | S3bM01 entry removed | KILLED L1 |
| the sibling run_verification_timeout_seconds likewise | same | as above | ...[*-run_verification_timeout_seconds] | S3bM14 entry removed | KILLED L1 |
| not REPOSITORY_SAFE statically | `_REPOSITORY_SAFE_FIELDS` | a skipped override fails open | test_every_repository_safe_field_was_audited | S3bM02 back in the safe table | KILLED L1 |
| static SECURITY_AUTHORITY (closed fallback) | `_SECURITY_AUTHORITY_FIELDS` | unknown-field label only | test_the_gate_is_the_s3_value_gate_with_a_ceiling | S3bM11 entry removed | KILLED L1 |
| exactly an int (no bool / coercible) | `type(value) is int` | `true` (= 1) or `"100"` judged | test_a_value_that_is_not_a_positive_int...[bool] | S3bM03 isinstance | KILLED L1 |
| at least 1 (0 is not a bound) | `1 <=` | 0 accepted | ...[zero] | S3bM04 `0 <=` | KILLED L1 |
| no negative (RLIM_INFINITY) | `1 <=` | -1 = unlimited on Linux | ...[negative] | S3bM05 lower bound removed | KILLED L1 |
| equal to the ceiling is safe | `<=` | every restated default re-asks | ...needs_no_approval[equal-*] | S3bM06 `<` | KILLED L1 |
| the ceiling is the packaged default | ceiling values | a raise up to a wrong ceiling accepted | test_a_repository_kriya_yaml_cannot_raise...[sandbox_cpu_seconds, shell_command_timeout_seconds] | S3bM07 240->2400, S3bM12 300->3000 | KILLED L1 (x2) |
| a lower value really is safe | the safe branch | everything re-asks | ...needs_no_approval[lower-*] | S3bM08 branch off | KILLED L1 |
| a raise really needs authority | the refuse branch | a raise accepted | ...cannot_raise...[acquisition_cpu_seconds, sandbox_cpu_seconds] | S3bM09 refuse -> safe | KILLED L1 |
| the gate is consulted for bounded fields | branch order / reachability | static fallback used instead | ...needs_no_approval[lower-*] | S3bM13 branch unreachable | KILLED L1 |
| resolution applies the override for these fields | `resolution.resolve_config_state` value-gate loop | lower values refused | ...needs_no_approval[lower-*] | S3bM10 override skipped for *_seconds/*_mb | KILLED L1 |

Governed controls (the unfixed-bytes result of each): lower/equal/1 load with no approval (before: pass, nothing was
gated; after: pass); the SEC live-validation shapes load (before/after pass); the durable approval makes a raise effective
for each field (before: pass vacuously - nothing was pending; after: pass with the approval covering the field); a trust
file outside the workspace likewise; the mcp_lifecycle bounds stay SECURITY_AUTHORITY and are not value-gated; the
ceilings equal the AutonomyConfig defaults and default_config.yaml does not set them.

## Sibling-field audit (every resource / timeout / budget bound in AppConfig; MEASURED by walking AppConfig with authority.classify_field)

| field | today (before S3b) | reason | decision |
|---|---|---|---|
| autonomy.sandbox_cpu_seconds | REPOSITORY_SAFE | RLIMIT_CPU of target/generated code (OCI: --cpus rate cap) | value-bounded, ceiling 240 (OI-132) |
| autonomy.sandbox_memory_mb | REPOSITORY_SAFE | RLIMIT_AS / JVM bounds / OCI --memory of target code | value-bounded, 4096 (OI-132) |
| autonomy.acquisition_cpu_seconds | REPOSITORY_SAFE | dependency-acquisition CPU bound | value-bounded, 300 (OI-132) |
| autonomy.acquisition_memory_mb | REPOSITORY_SAFE | dependency-acquisition memory bound | value-bounded, 2048 (OI-132) |
| autonomy.shell_command_timeout_seconds | REPOSITORY_SAFE | ShellTool wall clock (the only time bound under OCI) | value-bounded, 300 (OI-132) |
| autonomy.run_verification_timeout_seconds | REPOSITORY_SAFE | wall clock of running the built application (same class as the shell timeout; OCI honours cpu_seconds only as a rate cap) | value-bounded, 90 (BEYOND the measured set - owner question 1; migration impact 0) |
| mcp_lifecycle.* (9 bounds) | SECURITY_AUTHORITY | SEC-004 | unchanged (the precedent; a repository cannot even lower them - not widened here) |
| autonomy.risk_threshold_lines | SECURITY_AUTHORITY | approval threshold | unchanged |
| static_analysis.timeout_seconds / max_target_bytes | SECURITY_AUTHORITY (blanket) | PRD-031A | unchanged |
| llm.transport / llm.context_policy | SECURITY_AUTHORITY | provider timeouts / context ceilings | unchanged |
| llm.max_tokens / planner_max_tokens | REPOSITORY_SAFE | sampling; bounded by llm.context_policy (SECURITY_AUTHORITY) | unchanged |
| llm.context_window | REPOSITORY_SAFE | bounded by llm.context_policy | unchanged |
| autonomy.generation_time_budget_seconds | REPOSITORY_SAFE | default null (unbounded): a repository can only ADD a bound | unchanged |
| autonomy.generation_gate_reserve_seconds / generation_seconds_per_file_estimate | REPOSITORY_SAFE | planning estimates inside the budget; execute nothing | unchanged |
| autonomy.max_consecutive_no_progress_attempts / self_correction_loop_max_turns / developer_investigation_max_turns / best_of_n_first_attempt | REPOSITORY_SAFE | model-call budgets of governed loops (no containment effect; each attempt still sandboxed) | unchanged; noted: a repository can raise local-LLM compute use (not containment; ledger note, no OI opened) |
| knowledge.release_cache_ttl_days, search.top_k, routing thresholds | REPOSITORY_SAFE | cache age / result count / REPL routing | unchanged |
| process_profiles.enforce_* | REPOSITORY_SAFE | only add checks | unchanged |

Migration impact (read-only scan, `before/migration*.txt`): 53 kriya.yaml/kriya.yml and 8,969 YAML files under 16
`~/kriya-*` roots + this repository; 103 files set one of the six bounds (309 values), 0 above the ceiling, 0 negative/zero.
