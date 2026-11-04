# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Kriya is a local-first, multi-agent AI engineering platform (Python, `kriya/` package). It runs entirely against local LLM/embedding endpoints (Ollama or any OpenAI-compatible server) and coordinates Planner → Architect → Developer → Reviewer agents to plan, generate, fix, and review code inside a target repository, backed by a hybrid AST/vector/lexical index stored in SQLite.

## Kriya Standing Engineering Rules

Obey handover/ENGINEERING_RULES.md, imported below so it loads at every session start. These rules apply to every session and override convenience/speed optimizations unless the user explicitly says otherwise.

@handover/ENGINEERING_RULES.md

## Commands

```bash
# Setup (Python 3.12+)
python3 -m venv .venv
source .venv/bin/activate
pip install -e .                    # installs `kriya` and `kriya-mcp` console scripts

# Run the CLI (editable install puts these on PATH inside the venv)
.venv/bin/kriya doctor               # checks dirs + LLM/embedding connectivity
.venv/bin/kriya --config kriya.yaml <command>   # -c/--config points at a project config

# Tests
.venv/bin/pytest                     # full suite (asyncio_mode = strict, see pyproject.toml) - entirely mocked, no live model calls
scripts/run_full_suite.py            # the canonical full suite (~8 min): preflight (venv, Docker, jdtls/mvn/java, arm64-only JVMs), ulimit -n 256, PYTHONPATH, -n 8 --dist loadgroup, root-pollution post-check; extra pytest args pass through (e.g. --ff)
.venv/bin/pytest tests/test_workflow.py            # single file
.venv/bin/pytest tests/test_workflow.py::test_workflow_fallback_chain   # single test
.venv/bin/pytest -m state_machine    # fast deterministic state-machine tier (retry/recovery/fallback, resume, verification/commit, budgets, lifecycle); members: tests/state_machine/ + tests/conftest.py STATE_MACHINE_TIER_FILES
.venv/bin/pytest -m live_model       # tests/test_live_smoke.py only - needs a real local Ollama running, excluded by default
.venv/bin/pytest -m live_static_analysis   # PRD-031A real-scanner tier - needs Semgrep 1.178.0 exactly (and Docker + the pinned image for the OCI cases), excluded by default
```

```bash
.venv/bin/pylint kriya plugins/core_tools tests   # static-check gate (~10s): unbound/undefined names only, rules in pyproject.toml [tool.pylint]; must exit 0
.venv/bin/ruff check .                             # lint gate (CI `lint` job, lock-file ruff version): must report zero findings
```

## Working practices in this repo (quota discipline)

The user is quota-conscious in this repo specifically — apply these by default, without being asked each session:

- **Pytest runs.** A very small targeted run (a few new/changed test IDs, seconds) is fine at any time. For a wide-reaching change, Claude runs the full suite itself in parallel (`scripts/run_full_suite.py`, ~8 min; it refuses to run with a missing prerequisite), started ONCE with `run_in_background` and read when the completion notice arrives - never polled, never stacked. The user's own run is the final confirmation at a batch boundary. Anything in between (a subsystem's files) goes to the user as an exact command.
- **Parallel suite contract (`tests/conftest.py`).** Tests that start real containers (any `skip`/`skipif` whose reason names docker) are pinned to one xdist worker (`xdist_group("docker")`): their leak checks list every kriya container on the daemon, so two at once see each other's. A test must never share a fixed path such as `/tmp` as a workspace (the workspace lock collides across workers); use `tmp_path`.
- **Don't launch long-running or live-model work (a `kriya generate`/`fix` run, `-m live_model` tests, eval batches) and then poll it from inside the session** (repeated Bash/Read check-ins, `ScheduleWakeup` loops) — every check-in costs a turn even while "just waiting." Either hand the command to the user's terminal, or start it with `run_in_background` and wait for the actual completion notification; only peek at interim output when there's a concrete reason (e.g. confirming it didn't fail fast), not out of curiosity.
- **Prefer fewer, well-scoped tool calls over several exploratory ones.** Batch independent reads/searches in parallel rather than trickling them out one at a time.
- **Be deliberate about spawning subagents (the `Agent` tool) — don't reach for one by default.** Each subagent runs its own requests against the model, on top of whatever this session has already spent. Only spawn one when the task genuinely needs isolated context (a review that shouldn't pollute the main thread) or real parallelism, and confirm with the user first for anything nontrivial (a full-package review, multiple parallel agents) rather than launching it unasked. When a subagent's task is simple/mechanical (a lookup, a narrow lint pass, not a judgment call), pass a cheaper `model` override (e.g. `haiku`) instead of defaulting to the parent session's model.
- **Watch context length in a long session — 73% of this account's usage has come from sessions sitting above 150k tokens.** Longer sessions cost more even with prompt caching. At a natural checkpoint (a chunk of work just committed, about to pivot to an unrelated task), suggest `/compact` to shrink the working context, or `/clear`/a fresh session when the next task doesn't need this thread's history at all — see `feedback_session_resume_hygiene.md` in this project's memory for the existing convention of resuming via explicit memory pointers rather than "continue where we left off."
- Ordinary work stays in-session as normal: writing/editing code, quick targeted greps, `git status`/`git diff`/`git log`, doc updates, single fast commands.

## Backlog registry

`handover/BACKLOG_REGISTRY.csv` is the one canonical registry for open and deferred items. Each row carries `id, priority, status, discovered_in, target_scope, blocking, closure_evidence`. `handover/TASK_STATUS_TRACKER.csv` holds tasks only and never duplicates a registry item. Other docs reference the registry for status. A batch may not close with a newly found P2/P3 missing `target_scope`, and an open P0/P1 is worked, never parked there (`tests/test_backlog_registry.py`).

## Mandatory quality bar (every change, every session — standing user directive, 2026-09-26)

Passing the happy-path tests is not "done". These rules are mandatory; don't wait to be asked. In Batch 5, two bugs got through: a variable left unbound on an early-stop path, which silently dropped a result field, and a bare-`MagicMock` fixture that turned a feature on by accident. Neither the suite nor a live success run would have caught them.

1. **Static check before every commit.** Run `.venv/bin/pylint kriya plugins/core_tools tests` and `.venv/bin/ruff check .` and fix every finding; both must exit 0 (the tree is at zero findings for both, no baseline). Never silence ruff with a broad or per-file ignore; a `# noqa` must be narrow and say why (e.g. `# noqa: F811 - pytest fixture`). Before letting `ruff --fix` remove an "unused" import, check it is not a re-export another module or test imports (mark those `name as name`). The rules are deliberately narrow: `used-before-assignment`, `possibly-used-before-assignment`, `undefined-variable`, `undefined-loop-variable`. It flags the Batch 5 unbound variable at 0c19967. It does not see a variable assigned only inside a loop body that may run zero times (only the loop variable itself), so still review every variable assigned inside a branch, `try` or loop for use on paths where it was never set.
2. **Test failure paths and result contracts, not only decisions.** For any new or changed result field, event or record, test it on every terminal path that reports it: success, stopped early, gate failed and plan invalid. Asserting pass/fail alone is not enough.
3. **Strict test doubles.** Never build a config, kernel or policy object as a bare `MagicMock`: its attributes are all truthy and silently enable features. Use `tests/_strict_doubles.py` (`strict_config(section={...})`, a validated real `AppConfig` that rejects unknown fields; `strict_kernel(config)`; `strict_engine(config)`) or `MagicMock(spec=...)`, and set every flag the code under test reads explicitly. `tests/test_strict_doubles.py` fails on any new bare config/kernel/policy double, or a bare engine double whose `kernel` is not set explicitly.
4. **Broad catches must not hide coding errors.** A new `except Exception:` that logs and continues needs a test that asserts the normal (non-exception) output of that block, so a `NameError`/`TypeError` inside it fails a test instead of becoming a warning.
5. **Honest verification.**
   - The scratchpad plain runner is for iteration only. It must apply the module's autouse fixtures, and you must never compare against a baseline of runner-artifact failures: remove the artifact first.
   - Pytest stays with the user to save usage limits (the rule above). The one exception: a very small targeted run, i.e. one or a few new or changed test IDs that finish in seconds. Claude may run that itself to confirm a new test really works under pytest. Anything larger goes in the hand-off. Do not claim "verified" until the user's real pytest run is green.
   - Mutation-check new safety or decision logic: each mutation must make a test fail.
6. **Report your own bugs proactively.** A defect found in your own earlier work gets fixed, tested (with a check that the test fails without the fix), committed separately and reported, never folded silently into other work.

## Subsystem reference (read on demand, not every session)

The detailed per-subsystem notes (design rationale, invariants, honest security statements, test file names, patch points) live verbatim in `handover/claude-context/`. They were moved out of this file on 2026-10-07 to cut the per-session load; nothing was shortened. **Before modifying a module listed in the table, read its file first** (`cat handover/claude-context/<file>`). Code is ground truth; when behaviour changes, update the matching file in the same change.

| File | Covers | Key modules |
|---|---|---|
| `live-model-tiers.md` | `live_model` / `live_static_analysis` tiers, smoke contract (`SMOKE_*`), CI jobs | `tests/test_live_smoke.py`, `tests/_live_smoke_contract.py`, `.github/workflows/ci.yml` |
| `config-resolution-and-authority.md` | `load_config()` layering, `paths.*`/`logging.directory`/state resolution, removed fields, SEC-009 P1 field classification + P2 durable approval (`kriya authority`) | `kriya/config/config.py`, `authority.py`, `authority_approval.py`, `kriya/core/logging_setup.py`, `state_paths.py` |
| `kernel-mcp-and-tool-authority.md` | Kernel/registry/plugins; MCP env (SEC-003), lifecycle (SEC-004), invocation authority + durable approval (TOOL-002 P1/P2, `kriya mcp`), capability profiles + OCI enforcement (TOOL-003 P1/P2); ShellTool package-manager network authority (SEC-005); autonomous TOOL subtasks (TOOL-001) | `kriya/core/kernel.py`, `kriya/plugins/`, `kriya/mcp/*`, `kriya/policy/execution.py`, `model.py`, `telemetry.py`, `plugins/core_tools/`, `kriya/workflow/subtask_executor.py` |
| `generation-pipeline.md` | `run_generation_workflow` stages 1-8 (KnowledgeGuard, analysis/skills, graph RAG, untrusted RAG fencing, Developer+gates loop, approval, lessons, review/trace); unified `ExecutionPlan`/`execute_plan` (PRD-008A), terminal gate + commit services (PRD-030), verification binding, recovery/verification coordinators (PRD-031); static analysis gate (PRD-031A) | `kriya/workflow/workflow.py`, `execution_plan.py`, `plan_executor.py`, `terminal_gate_service.py`, `recovery_coordinator.py`, `kriya/static_analysis/` |
| `verification-platform-and-assurance.md` | Linux semantics (JVM rlimits, OCI identity, Maven cache location, gitfile mask); runtime prerequisites/acquisition/entrypoint ownership/resource grounding (D3/D2B/D4/D5); capability adapters R1; platform services + architecture guard (ARCH-PLATFORM-001); chaos harness (PRD-032); metrics (PRD-033); attempt evidence recorder (LR-R1-M1); live model certification + release streak (PRD-035/036) | `kriya/tools/validate.py`, `sandbox.py`, `containment_oci.py`, `kriya/capabilities/`, `kriya/platform/`, `kriya/metrics/`, `kriya/core/attempt_evidence/`, `model_certification.py` |
| `developer-edit-and-file-integrity.md` | Governed Developer investigation (DEV-INV-001); edit-protocol capability + structural insertion (CONTEXT-EDIT-PROTOCOL-001, P3-D); Developer response protocol, one edit engine, raw revisions, the three bindings (FILE-INTEGRITY-CONTRACT-001/001B); agents | `kriya/workflow/investigation.py`, `attempt.py`, `edit_capability.py`, `insertion_locus.py`, `file_integrity.py`, `kriya/agents/response_protocol.py`, `agent.py`, `kriya/policy/filesystem.py` |
| `model-runtime-and-inference.md` | Egress control (PRD-012); inference runtime port (INF-001); provider contract + run deadline + served-context observation + over-context (PROVIDER-CONTRACT-001/001A); runtime identity, qualification, normalized results, allocation/dispatch budgets and request fitting (PRD-013..016, CTX-001, PROMPT-BUDGET-FIT); fallback transitions, role metrics, routing (PRD-017..019) | `kriya/core/llm.py`, `inference_runtime.py`, `model_runtime.py`, `provider_contract.py`, `model_qualification.py`, `completion.py`, `token_budget.py`, `context_budget`, `kriya/workflow/model_transition.py`, `kriya/core/role_metrics.py`, `model_routing.py`, `kriya/policy/egress.py` |
| `requirements-and-brownfield.md` | Requirement lineage, explicit contracts, claims, acceptance oracles (Python/JVM), human-bound acceptance, closure evidence (PRD-020, FS-1C*, B2/B3); ownership findings and recovery (PRD-021/022); contract change classification/escalation (PRD-023); brownfield baseline `auto` policy incl. pytest stability envelope (PRD-024, REG-R1/R2) | `kriya/workflow/requirements.py`, `requirement_contract.py`, `acceptance_*.py`, `named_test_oracle.py`, `ownership_findings.py`, `contract_classification.py`, `baseline_policy.py`, `validation_baseline.py`, `pytest_stability.py` |
| `retrieval-and-code-intelligence.md` | Embedding contract E1-E4 and degradation (EMBEDDING-CONTRACT-001); Code Intelligence R1 (structural model, BaselineIndex, service, localization, CI-6, T0 known-target packaging, index policy, benchmarks) | `kriya/memory/embedding.py`, `vector.py`, `kriya/code_intel/`, `kriya/workflow/graph_retrieval.py`, `localization_decision.py` |
| `storage-cli-repl-routing.md` | SQLite stores and trust namespaces; CLI subcommands + build identity (KRIYA-VERSION-001); `kriya repl` dispatch; natural-language routing (`routing.enabled`) | `kriya/core/db.py`, `kriya/memory/learned_knowledge.py`, `kriya/cli.py`, `build_info.py`, `repl.py`, `routing.py` |

## Cross-cutting invariants (always on)

These are the rules that bite in *any* change, distilled from the files above. The file holds the full statement.

- **Fail closed, never approximate.** An unsupported/unknown/unavailable capability, field, adapter, encoding or evidence state is a typed refusal or stop with a reason code, never a silent fallback, downgrade or "closest match". Reason codes live in closed, tripwire-tested tables; key policy on `reason_code`, not on a shared type string.
- **One authoritative owner per decision.** Security decisions (`ExecutionPolicy`, SEC-009 config authority, TOOL-002 invocation approval, TOOL-003 containment, SEC-005 network, `AuthorizedFileWriter`/`Reader`) are consulted at their single choke point and never duplicated, inferred from names/descriptions/model text, or widened by retry, resume or fallback.
- **Model text is evidence, never authority.** Planner/Architect/Reviewer/tool output never becomes file content, goal text (`AUTH-GOAL-CONTAMINATION-001`), a requirement verdict that closes anything, or a config value. Learned/retrieved text stays inside the untrusted-context fencing.
- **Evidence binds to exact identity.** Verification, qualification, approvals, checkpoints and certification bind to digests (runtime, settings, schema, capability profile, workspace, tree, case-set version); any drift invalidates, nothing is transferred by name.
- **New `AppConfig` field:** classify it in `kriya/config/authority.py` (unlisted = fails closed) and never remove the forced `autonomy.sensitive_paths` baseline.
- **New outbound client / subprocess / filesystem write / MCP call / bytes source:** an inventory or structural test fails until it is classified (`test_prd012_network_inventory`, `test_platform_architecture_guard`, `test_file_integrity_contract_001`/`001b`, `test_tool001_*`, `test_knowledge_readpath_001`). Spawn through `kriya/tools/process.py`; MCP env via `build_mcp_subprocess_env()`; Kriya state via `control_store.write_control_file`.
- **Layering tripwires:** provider names only in `kriya/core/model_runtime.py` and `static_analysis/adapters/`; tree-sitter only in `code_intel/parsing.py`; OS mechanism only in `kriya/platform/`; coordinators/services never import the controller; `kriya/workflow` never imports `kriya/metrics`. Keep documented patch points (`LLMClient._request_once`, `PolymorphicValidator._run_maven_cmd`, ...) when moving code.
- **Identity-bearing versions:** bump `MODEL_PROTOCOL_ADAPTER_VERSION`, `STRUCTURAL_PARSER_VERSION`, `embedding.PREPROCESSING_VERSION`, `CASE_SET_VERSION`, `QUALIFICATION_POLICY_VERSION` only when the thing they fingerprint changes, and expect every earlier record to go STALE.
- **Tests:** `tests/conftest.py` isolates `KRIYA_STATE_DIR`/`KRIYA_LOG_DIR`/`KRIYA_QUALIFICATION_HOME`/`KRIYA_CERTIFICATION_HOME`, sets `KRIYA_MODEL_RUNTIME_PROBE=0` and a fake embedding identity; docker tests pin to one xdist worker; never a fixed `/tmp` workspace. Tests not about static analysis pass `DISABLED_STATIC_ANALYSIS`; legacy Developer answers pin `legacy_strict` or use `tests/_protocol_responses.py::as_requested`.

## Notes on the docs directory

`docs/design.md` and `docs/user_guide.md` were reconciled against the actual implementation (2026-07-29) — mismatches like `analyze --graph/--vectors`, multi-profile `llm.profiles`, tree-sitter/sqlite-vec/GBNF claims, etc. were corrected in place. They should now match the code; if you find a new mismatch (e.g. after a future feature change), treat the actual code as ground truth and update the docs rather than assuming the docs are still authoritative.
