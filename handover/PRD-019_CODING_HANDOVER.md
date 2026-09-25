# PRD-019 Coding Agent Handover

## Status
READY_FOR_PYTEST_VERIFICATION (batch 4: PRD-017 to PRD-019, one pytest stop for the whole batch).

## Source identity
- Base revision: PRD-018 commit `261c187`; PRD-019 is `6302ea4`.
- Branch: `milestone-decomposition`, local and unpushed.

## Scope
Evidence-based model routing, opt-in (instruction: `tasks/PRD-019_Evidence-Based_Model_Routing.md`).

**Spec anchor corrected.** The instruction points at `kriya/routing/*` and `tests/test_routing.py`. Those are the REPL's
natural-language *command* router (which CLI command a sentence means), not model selection. Model routing is a new
module, `kriya/core/model_routing.py`; the command router is untouched.

| Requirement | Implementation |
|---|---|
| 1. Allowed inputs only; no free-form LLM judgment | For each candidate, the evidence (`CandidateEvidence`) is: its exact runtime; its qualification for that role *in the routed placement* (`required_capabilities(role)`); the role's protocol needs (JSON roles need a JSON-mode profile); its served window against the role's context need (the role's configured window, or `min_context_window`); and the measured outcomes for that role on that exact runtime from the between-run table. Names, sizes and model opinions are never inputs. |
| 2. Qualified explicit override wins; otherwise configured candidates | `agent_llms.<role>.llm` enters as the explicit candidate and wins whenever it is eligible. Otherwise the ranking is: measured candidates before unmeasured; then success rate (Developer: attempts passed / attempts; other roles: calls without protocol or schema failure); then lower failure rate; then lower mean latency; then operator order; then alias. With no eligible candidate the role keeps its configured binding (`configured_default`) and the reasons are recorded. |
| 3. No online learning during a run | The table is written only by `kriya model metrics --write-table` (a deterministic aggregation of the PRD-018 `model.role_metrics` rows in `traces.db`, digest-bound; a tampered table is refused with `ROUTING_TABLE_INVALID`). Runs only read it; tested that a run does not write it. |
| 4. Record candidates, rejections and the final route | A `model.route` run event per routed role: the mode, the chosen model and runtime, the source (`explicit_override`, `evidence`, `configured_default`, `frozen_table`), the reason, every candidate with its score, every rejection with its reasons, and the table digest. `kriya model routes [--json]` shows the same decisions without running. |
| 5. Operator mode that freezes routing | `kriya model routes --freeze <path>` writes a digest-bound frozen table (each role's model and exact runtime). `mode: frozen` with `frozen_routes_path` replays exactly those runtimes. It refuses the run with `ROUTE_FROZEN_MISMATCH` if a role has no frozen route, the model is no longer a candidate, the runtime digest changed, or the runtime is no longer QUALIFIED. A role left on its configured binding cannot be frozen. |

### Where routing applies
Routing runs at the workflow command boundary (`kriya/cli.py::_workflow_config`, used by `generate`, `fix` and proposal
execution), before the `LLMClient` and engine are built, so every consumer sees one coherent configuration:
- a routed Developer becomes the primary `llm`;
- a routed role becomes `agent_llms.<role>.llm`.

The loaded configuration is never changed (a copy is routed). The PRD-018 independence policy is checked on the routed
configuration.

### Placement matters
A model's runtime identity includes its capability-profile provenance (PRD-013), so the same model as the primary and
as a role binding are different runtimes (the kind of binding matters, not which role). Candidates are therefore
assessed, qualified and measured in their routed placement.

**Qualification scope (architecture review).** Qualification stays attached to the exact runtime and its case
evidence (PRD-014; `kriya model qualify` always runs every case and saves one record per runtime). A candidate is
eligible for a role only when `required_capabilities(role)` all PASS in that record. One record is reused by every
role its evidence covers (no per-role re-qualification); a role needing a case the record failed or lacks is rejected
naming that case. The first version of this handover said "one qualification covers every role", which overstated
it; the code already assessed per role, and tests now prove it: reuse across reviewer, spec_compliance and planner;
the planner rejected on `malformed_output_recovery` from the same record the reviewer uses; the Developer rejected on
`anchored_edit_protocol` with its own placed record (so the rejection is not merely a missing record); an unqualified
runtime stays ineligible despite better metrics.

**Resume (architecture review).** Routes are sticky within a run. Each checkpoint records `model_routes` (every
routed role's model, exact runtime and source). `generate`/`fix` `--resume`/`--resume-id` replay them at the command
boundary (the same checkpoint selection as the engine: `--resume-id`, else the latest) instead of routing again on
the current table: the same model on the same exact, still QUALIFIED runtime; a role that kept its configured binding
keeps it. Anything else refuses the resume before any model call with `ROUTE_RESUME_MISMATCH`: a drifted runtime, a
candidate no longer configured, a changed routed-role set. A checkpoint without recorded routes (routing was off, or
it predates this change) is routed fresh; the resume fingerprint still refuses a different runtime. `kriya model qualify --model <candidate> --role <role>` qualifies a candidate exactly as it runs when
routed to that role.

## Config
`model_policy.routing` (SECURITY_AUTHORITY; validated):
- `mode`: `off` (default), `evidence` or `frozen`;
- `candidates`: `llm_chain`-shaped bindings;
- `roles`: `{role: [candidate aliases in preference order]}`; every alias must be a candidate;
- `min_calls` (default 5): below it, a candidate is unmeasured;
- `min_context_window`;
- `table_path` and `frozen_routes_path`: absolute; frozen mode requires `frozen_routes_path`.

## Tests (plain runner; you run pytest)
- `test_prd019_model_routing`: the pure decision tests, plus:
  - planning and applying routes on real configurations (exact probe stand-in, placement-qualified records);
  - frozen replay and drift refusal;
  - config validation and classification;
  - an end-to-end run through the CLI boundary and `WorkflowEngine`: the reviewer's calls go to the routed candidate, `model.route` lands in `traces.db`, and the table is not written.
- 30 passed originally; 39 after the review corrections (qualification scope 4, resume 5; the placement output budget and "checkpoints carry the routes" are assertions added to existing tests). The CLI resume-refusal test uses `capsys`, which the plain runner lacks; it passed through a shim.
- Review-correction mutations, each caught: the role's required cases ignored (2 tests), resume routes ignored by `plan_routes` (3) or by the command boundary (2).
- Mutation checks, each caught:
  - apply is a no-op fails 3;
  - qualification ignored fails the unqualified-runtime test;
  - frozen mode ignoring runtime drift fails the drift test (tightened so the drifted runtime is itself qualified and only the digest check can refuse it);
  - score ignored fails 1;
  - no route events fails 1;
  - protocol needs ignored fails 1.
- Regression, plain runner, all at baseline:
  - the config, authority, doctor, qualification, proposal and milestone suites;
  - `test_repl` is 19/12 with and without this change (plain-runner TTY/capsys limits).

## Decisions to review
1. **Routing requires QUALIFIED in every mode, not only production.** Routing is itself an evidence claim. Choosing an unqualified runtime because of a score is exactly what the PRD forbids.
2. **Unmeasured candidates rank after measured ones.** Until `min_calls` calls exist, a candidate is ranked by operator order after every measured candidate, so a new runtime does not displace a measured one on no evidence.
3. **The table is refreshed by an explicit command**, not at the end of each run. The operator controls when evidence changes the routes.
4. **The Developer can be routed.** This replaces the primary `llm` for the run, and the resume fingerprint binds the routed runtime. A resume replays the checkpoint's own routes (see Resume), so a changed metrics table no longer blocks it; only a route that no longer holds does. A routed candidate's unset `max_tokens` is the shared default (PRD-017 decision 1).

## Live test
`tests/test_live_prd017_019_model_roles.py` (all three PRDs):
```bash
set -o pipefail; mkdir -p handover/evidence/BATCH4/user-live
KRIYA_BATCH4_EVIDENCE_DIR=handover/evidence/BATCH4/user-live KRIYA_LIVE_BASE_URL=http://localhost:11434/v1 \
KRIYA_LIVE_LLM_MODEL=qwen3-coder:30b KRIYA_LIVE_FALLBACK_MODEL=qwen3.5:9B \
.venv/bin/pytest -m live_model -ra -s tests/test_live_prd017_019_model_roles.py 2>&1 | tee handover/evidence/BATCH4/user-live/batch4-live.log
```
Both models must already be pulled; windows stay at 8192. PRD-019's case plans a stage matrix over both real runtimes
twice and checks the route records are identical:
- reviewer and spec_compliance route to the fallback by evidence (its runtime as a role binding is qualified by record);
- the planner keeps its qualified explicit binding on the primary model;
- the Developer's route to the fallback is refused, because as the primary `llm` the fallback is a different, unqualified runtime identity.

### Found in review and fixed: alias collisions
Model bindings are looked up by alias, in the order primary, `llm_chain`, `agent_llms`. A candidate sharing its alias
with another binding, but not its settings, silently took that binding's capabilities and runtime identity:
- when routing evaluated it;
- in `qualify --role`;
- at run time.

Reproduced offline: a same-alias `llm_chain` entry without JSON mode got the routed reviewer candidate rejected.

Now a candidate may share an alias only with a binding whose identity-relevant settings are identical (base URL,
request options, window, reasoning, declared capabilities, context policy). Anything else is:
- a configuration error at load time;
- `ROUTING_CANDIDATE_ALIAS_CONFLICT` from `plan_routes` for a configuration built in code.

Tested.

## Residuals
- `kriya review` and `kriya ask` build no workflow engine and do not apply routing to the roles they use.
- Aliases shared *between existing bindings* (for example an `llm_chain` entry and an `agent_llms` role with the same model name and different settings) resolve to the first by alias. That predates this batch and is not changed.
- Metrics accumulate per exact runtime. A re-pulled tag starts unmeasured, which is intended: it is a different runtime.
