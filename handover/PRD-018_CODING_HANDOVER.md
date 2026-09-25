# PRD-018 Coding Agent Handover

## Status
READY_FOR_PYTEST_VERIFICATION (batch 4: PRD-017 to PRD-019, one pytest stop for the whole batch).

## Source identity
- Base revision: PRD-017 commit `c23e22f`.
- Branch: `milestone-decomposition`, local and unpushed.

## Scope
Role-model independence visibility and per-role metrics (instruction:
`tasks/PRD-018_Role-Model_Independence_Visibility_and_Per-Role_Metrics.md`). Default model assignment is unchanged.

| Requirement | Implementation |
|---|---|
| 1. `doctor --production` shows which roles share an exact runtime; informational unless policy requires independence | `models.role_independence` rewritten: it compared model **names**; it now resolves each role's exact runtime (`role_metrics.role_runtimes`, re-probed) and groups roles by runtime digest (`shared_runtime_groups`), marking a runtime that is not exactly identified as `unverified_alias`. WARN when roles share a runtime or one is unverified, PASS when all are distinct. The row is `required` (blocking) only when `model_policy.independent_roles` is set; then FAIL on any violation with `ROLE_INDEPENDENCE_REQUIRED`. The row's `required` flag now follows the policy (the check registry accepts a per-config callable). |
| 2. Per role + fingerprint: calls, protocol failures, schema failures, first-pass contribution, retries triggered, latency, tokens | `kriya/core/role_metrics.py`. Every `LLMClient` call (success or error, via `_finish`) is counted under (role, model, exact runtime digest); a non-exact runtime is `unavailable`. Protocol failures are the normalized statuses EMPTY_CONTENT, MALFORMED_STRUCTURED_OUTPUT, OUTPUT_TRUNCATED, BACKEND_ERROR, TIMEOUT. Schema failures are responses a caller rejected against its role contract: a JSON role's unparseable answer (`call_with_escalation`'s `is_failure`) and a Developer file list that misses the `{"files": [...]}` contract. Each attempt's deterministic gate outcome is charged to the Developer runtime that generated its candidate: attempts, passed, first-pass success (attempt 1), retries triggered (failed attempts). Recorded as one `model.role_metrics` run event per run in `traces.db`; `kriya model metrics [--json]` aggregates them over finished runs. |
| 3. Policy can require an independent verifier runtime; default unchanged | `model_policy.independent_roles` (new top-level section, default `[]`, SECURITY_AUTHORITY so a repository cannot relax it; unknown roles are a config error). Enforced before any model call by `kriya/cli.py::_workflow_config` for `generate`, `fix` and proposal execution (typed `ROLE_INDEPENDENCE_REQUIRED`, exit 1), and by the doctor row. A required role on a runtime that cannot be identified exactly fails: independence cannot be shown. |
| 4. A second LLM opinion is never stronger than deterministic evidence | Nothing in this package feeds a verdict: metrics are observations. The attempt outcome counted is the deterministic gate outcome, never a model's verdict. The doctor evidence and the run event carry `second_opinion_is_verification: false`. |

### Attribution
All agents share one `LLMClient`, so the role is a context variable (`role_metrics.model_role`) set by the caller:
- `BaseAgent.run` and the six JSON-role `call_with_escalation` sites pass `role=self.name`;
- `attempt._run_developer_generation` (every Developer generation, including DEV-INV investigation) and both self-correction loops run as `developer`.

A call outside any scope is recorded as `unattributed`, never guessed. The context variable follows `await` and does not leak between concurrent tasks (tested).

### Run trace
- `model.role_independence` (run start): each role's model, runtime digest and exactness, the shared-runtime groups, the policy and any violation.
- `model.role_metrics` (once, when the trace is written): every call the engine made since its previous trace row (`RoleMetrics.take_unreported`). Calls made before a unit run starts (the enforce controller's structured planning, milestone planning) are carried by the next row, and no call is reported twice. A direct goal delegates entirely to its one unit run, so a parent and its unit never both write metrics.

## Files
- New: `kriya/core/role_metrics.py`, `tests/test_prd018_role_metrics.py`.
- Changed:
  - `kriya/core/llm.py` (per-call recording);
  - `kriya/agents/agent.py` (role scopes, schema failures);
  - `kriya/workflow/attempt.py` (developer scope, `last_developer_call_attempt`);
  - `kriya/workflow/state.py` (attempt outcomes, baseline);
  - `kriya/workflow/workflow.py` (the two run events);
  - `kriya/workflow/retry_strategy.py` (failed attempt outcome);
  - `kriya/config/config.py` and `kriya/config/authority.py` (`model_policy`);
  - `kriya/production_doctor.py` (the row, the per-config `required` flag);
  - `kriya/cli.py` (`_workflow_config`, `kriya model metrics`).

## Tests (plain runner; you run pytest)
- `test_prd018_role_metrics`: 20 passed. One is an end-to-end run through the real `WorkflowEngine` and `LLMClient` (stubbed transport, exact probe stand-in). It checks:
  - `traces.db` holds one `model.role_metrics` event with the Developer on its exact runtime, one attempt and first-pass success;
  - the helper roles are on a different runtime;
  - the `model.role_independence` groups;
  - `kriya model metrics --json` reads the rows back.
- Mutation checks, each caught:
  - `BaseAgent.run` without a role;
  - the Developer without a role scope;
  - no pass outcome;
  - no shared-runtime violation;
  - no per-call recording.

## Decisions to review
0. **Found in review and fixed: pre-unit calls were dropped.** The first version measured each run from a baseline taken at its start, so planning calls made before a unit run began were counted in no row. Replaced by the engine-level unreported cursor above (tested).
1. **The doctor row's `required` flag depends on the configuration.** It is blocking only under the policy. Otherwise a deployment would fail readiness for the default single-model setup, which the PRD says must stay supported.
2. **Policy enforcement is at the command boundary.** `generate`, `fix` and proposal execution check it before building the engine. These are the only places that build a `WorkflowEngine` (checked across `kriya/` and `plugins/`; `kriya-mcp` builds no engine or model client). A programmatic caller of `WorkflowEngine` directly is not checked, but the run still records the violation in `model.role_independence`. `kriya review` and `kriya ask` build no engine and do not apply the policy.
3. **Schema failures are counted where Kriya already rejects a response**, not re-derived. A Developer answer later rejected by a quality gate is an attempt outcome, not a schema failure.

## Live test
See the PRD-019 handover for the batch-4 live command. PRD-018's cases:
- a same-model configuration: every role on `qwen3-coder:30b`, so the doctor row WARNs with one shared exact runtime;
- an override: the verifier roles on `qwen3.5:9B` with `model_policy.independent_roles`, so the row PASSes and is required.

Both check that the evidence names the real exact fingerprints.

## Residuals
- Only calls through `LLMClient` are counted; the embedding and routing classifier clients are not model roles.
- `MilestonePlannerAgent` has no `agent_llms` entry; its calls are recorded under its own name (`milestone_planner`).
