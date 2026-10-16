# DEPENDENCY-RULES

## 1. Layers and the one-way matrix

Layer order (a module may import only modules in the same or a LOWER layer, and only through the target module's public contract modules):

```
L6  ADAPTER-KUP, ADAPTER-CLI, METRICS, (Electron host: TypeScript, out of the Python graph)
L5  CLIENT
L4  APPLICATION, QUALIFICATION
L3  KERNEL (kriya/workflow + kriya/agents + core/kernel.py, core/events.py)
L2  AUTHORITY, STATE, EVIDENCE, PROVIDER, REPOSITORY, EXECUTION, TOOLBOX, SKILLS, KNOWLEDGE, RETRIEVAL
L1  CONTEXT, CONFIG
L0  FOUNDATION, PLATFORM
```

Allowed edges (row imports column). `pub` = through public contract modules only; `-` = forbidden; `self` = inside the module anything goes.

| from \ to | FOUND | PLAT | CTX | CONFIG | AUTH | STATE | EVID | PROV | REPO | EXEC | TOOLBOX | SKILLS | KNOW | RETR | KERNEL | QUAL | APP | CLIENT | KUP | CLI | METRICS |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| FOUNDATION | self | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - |
| PLATFORM | pub | self | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - |
| CONTEXT | pub | pub | self | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - |
| CONFIG | pub | pub | pub | self | pub¹ | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - |
| AUTHORITY | pub | pub | pub | pub | self | pub² | pub | - | pub | - | - | - | - | - | - | - | - | - | - | - | - |
| STATE | pub | pub | pub | pub | pub | self | pub | - | pub | - | - | - | - | - | - | - | - | - | - | - | - |
| EVIDENCE | pub | pub | pub | pub | - | pub³ | self | - | pub | - | - | - | - | - | - | - | - | - | - | - | - |
| PROVIDER | pub | pub | pub | pub | pub⁴ | - | pub | self | - | - | - | - | - | - | - | - | - | - | - | - | - |
| REPOSITORY | pub | pub | pub | pub | pub | - | pub | - | self | pub⁵ | - | - | - | - | - | - | - | - | - | - | - |
| EXECUTION | pub | pub | pub | pub | pub | - | pub | - | pub | self | - | - | - | - | - | - | - | - | - | - | - |
| TOOLBOX | pub | pub | pub | pub | pub | pub | pub | - | - | pub | self | - | - | - | - | - | - | - | - | - | - |
| SKILLS | pub | pub | pub | pub | pub | - | - | - | pub | - | - | self | - | pub⁶ | - | - | - | - | - | - | - |
| KNOWLEDGE | pub | pub | pub | pub | pub | - | - | pub⁷ | - | - | - | pub | self | pub | - | - | - | - | - | - | - |
| RETRIEVAL | pub | pub | pub | pub | - | - | - | pub⁷ | pub | - | - | - | - | self | - | - | - | - | - | - | - |
| KERNEL | pub | pub | pub | pub | pub | pub | pub | pub | pub | pub | pub | pub | pub | pub | self | - | - | - | - | - | - |
| QUALIFICATION | pub | pub | pub | pub | pub | pub | pub | pub | pub | pub | pub | pub | pub | pub | pub | self | - | - | - | - | - |
| APPLICATION | pub | pub | pub | pub | pub | pub | pub | pub | pub | pub | pub | pub | pub | pub | pub | pub | self | - | - | - | - |
| CLIENT | pub | - | pub | pub | - | - | - | - | - | - | - | - | - | - | - | - | pub | self | - | - | - |
| ADAPTER-KUP | pub | pub | pub | pub | - | - | - | - | - | - | - | - | - | - | - | - | - | pub | self | - | - |
| ADAPTER-CLI | pub | pub | pub | pub | pub⁸ | pub⁸ | pub⁸ | pub⁸ | pub⁸ | pub⁸ | pub⁸ | pub⁸ | pub⁸ | pub⁸ | pub⁸ | pub⁸ | pub | pub | pub | self | pub |
| METRICS | pub | pub | pub | pub | - | pub | pub | - | - | - | - | - | - | - | - | - | - | - | - | - | self |

Footnotes (each is a deliberate, tested exception, not a loophole): ¹ CONFIG -> AUTHORITY only `config.authority`/`authority_approval` which are AUTHORITY-owned files living in the config package (SEC-009 runs inside `load_config`); ² AUTHORITY -> STATE only `control.launch_approval`/`control.persistence` read-side for review records (AUTHORITY-owned file in the control package); ³ EVIDENCE -> STATE only `core.state_paths` (state directory); ⁴ PROVIDER -> AUTHORITY only `policy.egress` (PRD-012 egress is checked at the client); ⁵ REPOSITORY -> EXECUTION only `tools.process` (process spawning for git) and `platform`; ⁶ SKILLS -> RETRIEVAL only `analyzer.RepositoryModel` (fact matching); ⁷ KNOWLEDGE/RETRIEVAL -> PROVIDER only `core.llm.LLMClient`/`memory.embedding` (model calls for extraction/embeddings); ⁸ ADAPTER-CLI keeps direct module access ONLY for composition (`Kernel`, `LLMClient`, `WorkflowEngine`, `load_config`: the patched names) and for commands not yet routed through APPLICATION; each such import is listed in the allowlist with its O2 removal entry, so the CLI converges on CLIENT + composition only.

Hard forbiddens inside the matrix (named because the plan names them): KERNEL -> {ADAPTER-KUP, ADAPTER-CLI, CLIENT, APPLICATION, METRICS, QUALIFICATION}; KERNEL -> any module that names a provider (`core.model_runtime` adapters, `memory.embedding.OllamaEmbeddingClient`), a concrete skill name, a concrete plugin/MCP tool; PROVIDER -> {STATE, KERNEL}; REPOSITORY -> KERNEL; AUTHORITY -> KERNEL; STATE -> KERNEL; EVIDENCE -> KERNEL; any L0-L2 -> `kriya.cli`, `kriya.kup`, `kriya.client`; SKILLS/TOOLBOX/EVIDENCE -> renderer (not expressible in Python; the TypeScript side is covered by the existing `ui/` tests and the KUP visibility policy).

## 2. Mechanical enforcement (`tests/architecture/`, slice A1/A2, finalized Q1)

Style: the existing `tests/test_platform_architecture_guard.py` (AST walk over `kriya/` + `plugins/`, closed forbidden tables, a `TEMPORARY_ALLOWLIST` that may only shrink and must name an open item). Extended, not replaced.

Files:
- `tests/architecture/module_map.py`: `MODULES: Dict[str, Module]` where `Module(owner_paths: Tuple[str, ...], public: Tuple[str, ...], layer: int)`. Every `kriya/**.py` file must be owned by exactly one module (test fails on an unowned or doubly-owned file; new files must be classified: the fail-closed rule of the plan).
- `tests/architecture/test_import_direction.py`: for every import edge (top-level AND function-local; `ast.walk` over `Import`/`ImportFrom`, relative imports resolved as in `depmap.py`), resolve both modules to their logical module, check the matrix, check that a cross-module target is in the target's `public` list. Violations not in `ALLOWLIST` fail. `ALLOWLIST: Dict[Tuple[str, str], Tuple[str, str]]` maps `(from_module_path, to_module_path) -> (removal slice id, reason)`; a stale entry (edge no longer present) fails, so the list only shrinks.
- `tests/architecture/test_cycles.py`: Tarjan SCC over the module graph, (a) top-level imports: the only allowed SCCs are those in `CYCLE_BASELINE` (initially the two 2-cycles), (b) all imports: the SCC size per package may not exceed the committed baseline (`baseline.json`, regenerated by the slice that shrinks it; a growth fails). Q1 sets both to empty / size 1 except documented intra-module cycles (e.g. `tools.*` containment group).
- `tests/architecture/test_direct_access_owners.py`: closed tables, same style as the platform guard: `subprocess.*`/`Popen`/`create_subprocess_*` only in EXECUTION, REPOSITORY, PLATFORM, `mcp.lifecycle`; argv literal `"git"` only in REPOSITORY (+ `policy.execution` classification); `os.environ`/`os.getenv`/`Path.home()`/`expanduser` only in `config/environment.py` (+ `tools/sandbox.py` builds child environments, `mcp/mcp.py` builds MCP envs: listed as owners of CHILD environments, not readers of Kriya homes); `os.getcwd()`/`Path.cwd()` only in ADAPTER-CLI/KUP/`context` (workspace resolution at the edge); `sqlite3` only in STATE, EVIDENCE, RETRIEVAL, KNOWLEDGE stores, `core.db`; `import httpx`/`requests`/`urllib.request` only where `test_prd012_network_inventory.py` lists them (that test stays authoritative; this one only forbids a new site outside PROVIDER/TOOLBOX providers/KNOWLEDGE/ADAPTER); the words `ollama`/`vllm`/`openai` only in `core/model_runtime.py`, `memory/embedding.py`, `static_analysis/adapters/`, config defaults, CLI help; skill directory reads (`os.listdir`/`glob` over a `skills` path) only in SKILLS; `Kernel.registry.get("tool"` only in TOOLBOX and `workflow.subtask_executor` (the one consumer, through `ToolRegistry` after K2); `.kriya/` path construction only in STATE (`control.persistence`, `control_store`) and REPOSITORY (worktree). Each table has its own shrink-only `TEMPORARY_ALLOWLIST` keyed by path with the removal slice.
- `tests/architecture/test_kernel_purity.py`: KERNEL modules import nothing from {`kriya.cli`, `kriya.kup`, `kriya.client`, `kriya.application`, `kriya.metrics`, `kriya.qualification`}; no KERNEL module contains the string literal of a provider name, a plugin tool name (`"filesystem"`, `"shell"`, `"git"` as registry keys), an MCP server name, or a skill name (`"ignite-java17"` etc.: the list is the names found under `tests/fixtures` + `demo-v2` skills); KERNEL never reads `os.environ`/`Path.home()`; KERNEL never imports `subprocess`.
- `tests/architecture/test_adapters_thin.py` (O1/O2): every function in `kriya/kup/*_ops.py` and `kriya/cli_commands/*` calls at most `LocalKriyaClient`/`KriyaClient` services, envelope/formatting helpers and click/json; it imports nothing from L2/L3 (list of forbidden prefixes), so business logic cannot creep back into adapters.
- `tests/architecture/test_patch_points.py` (E-slices): the documented patch points exist as module-level names on their facade modules: `kriya.core.llm.LLMClient._request_once`, `kriya.tools.validate.PolymorphicValidator._run_maven_cmd`/`_run_cmd_with_timeout`/`_resolve_python_interpreter`, `kriya.workflow.attempt.{run_attempt,_run_developer_generation,run_managed_service_verification,_run_coordinated_repair_generation,find_runnable_test_files,extract_target_test,_resolve_java_home_override,_build_targeted_retry_prompt,_check_java_toolchain_mismatch}`, `kriya.workflow.workflow_controller.{parse_planner_structured_output,build_engineering_plan_from_planner_output,validate_plan,model_capacities,collect_unit_grounding,subtask_executor,create_git_worktree,remove_git_worktree,StaticAnalysisService}`, `kriya.workflow.workflow.{RepositoryAnalyzer,save_checkpoint,delete_checkpoint,derive_contract_transition,_ensure_repository_indexed,_run_contract_acceptance}`, `kriya.cli.{load_config,Kernel,LLMClient,WorkflowEngine,_dispatch_generation,ReviewerAgent,_learned_reference_context,_get_global_skills_dir,_model_cfg}` AND the extracted owner module reaches them through an injected `*Deps`, never by importing the facade (checked: owner modules in `KERNEL_OWNER_MODULES` do not import `workflow.attempt`/`workflow.workflow`/`workflow.workflow_controller`/`kriya.cli`).
- `tests/architecture/test_module_selectors.py` (Q1): every logical module has >= 1 test file carrying its marker (see TEST-MATRIX) and every test file under `tests/` maps to at least one module marker or `integration`/`architecture`.
- Speed: the whole `tests/architecture/` directory parses `kriya/` once per session (module-scoped fixture, cached AST); target < 5 s.

## 3. Initial ALLOWLIST (every entry is a MEASURED current edge; the slice named removes it; the list only shrinks)

Cross-layer back-edges into KERNEL (`kriya.workflow.*`, `kriya.agents.*`):

| from -> to | removal slice |
|---|---|
| control.persistence -> workflow.edit_safety, workflow.file_integrity | M1 |
| control.control_store -> workflow.edit_safety | M1 |
| control.decisions -> workflow.edit_safety | M1 |
| control.commit_state -> workflow.edit_safety | M1 |
| control.recovery -> workflow.edit_safety | M1 |
| control.recovery -> workflow.contract_lifecycle (lazy) | D2 (kernel passes the transition callback into recovery) |
| control.retention -> workflow.edit_safety | M1 |
| control.retention -> workflow.checkpoint; control.telemetry -> workflow.checkpoint; control.run_coordinator -> workflow.checkpoint (lazy) | D2 |
| control.retention -> workflow.milestone_completion | D2 (kernel supplies `milestone_ledger_run_references` as a `RetentionSources` parameter) |
| control.telemetry -> workflow.context_package | D2 (`ContextPackage` summary type moves to `control.telemetry`'s own typed input, kernel adapts) |
| control.launch_approval -> workflow.edit_safety | M1 |
| control.launch_approval -> workflow.runtime_command_approval (lazy) | C3 |
| control.state -> workflow.process_profile, workflow.triage | C2 |
| policy.filesystem -> workflow.edit_safety | M1 |
| policy.model -> workflow.process_profile, workflow.triage | C2 |
| policy.execution -> workflow.triage | C2 |
| policy.execution -> core.llm (lazy) | C3 (egress check receives the client's identity as a parameter) |
| static_analysis.operator_scan -> workflow.edit_safety, workflow.file_integrity | M1 |
| static_analysis.scope -> workflow.file_integrity | M1 |
| static_analysis.service -> workflow.terminal_commit (lazy) | M1 (`CandidateMaterializationError` moves to `repository.commit`) |
| kup.runtime_command_ops -> workflow.runtime_command_approval | B3 |
| kup.application_outcome -> workflow.verification_authority | B2 |
| kup.execution -> workflow.checkpoint (lazy) | B3 |
| knowledge.staging -> workflow.skill_extraction | J1 |
| core.model_qualification -> workflow.file_integrity, agents.agent, agents.response_protocol (lazy) | N1 (QUALIFICATION is L4: the edge becomes legal when the file moves) |
| analyzer.analyzer -> workflow.checkpoint (lazy) | D2 |
| capabilities.gradle -> workflow.jvm_module_ownership (lazy) | L1 (`jvm_module_ownership` is EXECUTION-owned, declared so in the map; the file moves to `capabilities/`) |
| agents.agent -> workflow.context_budget, generation_manifest, milestone_normalization, requirements (lazy) | E13 (agents receive these through `AgentDeps`; agents and workflow are both KERNEL so the edge is intra-module, but the function-local cycle is removed) |

Reversed edges out of lower layers:

| from -> to | removal slice |
|---|---|
| core.llm -> control.run_coordinator (clock, runtime use) (lazy) | H2 (deadline from `InferenceRequest.deadline`; runtime use recorded by the kernel from `ChatResponse.provider_metadata`) |
| tools.validate -> control.run_coordinator (lazy) | H2 (`ExecutionRequest.deadline`) |
| tools.validate -> capabilities (top) and capabilities.* -> tools.validate/resolver/containment (lazy) | none: intra-EXECUTION (declared same module) |
| tools.service -> mcp (top) | K1 (file moves to TOOLBOX where mcp is a provider) |
| core.logging_setup -> control.persistence (lazy) | D3 (run-log path given by STATE through CONFIG's resolved paths, not looked up) |
| core.trace -> control.launch (lazy) | D2 (trace.py moves to STATE) |
| core.release_candidate -> control.workspace_identity; config.authority_approval -> control.workspace_identity; kup/mcp/static_analysis -> control.workspace_identity | B1 (moves to CONTEXT) |
| config.config -> core.state_paths, core.logging_setup (lazy) | D3 |
| config.config -> static_analysis.registry (lazy) | D3 (static-analysis config validation moves next to the registry; config calls a validator passed in) |
| core.kernel -> mcp (lazy) | K1 (Kernel composes `ToolRegistry` providers through `toolbox.compose`; the import moves to TOOLBOX) |
| core.attempt_evidence.scope -> build_info; core.release_identity -> build_info (lazy) | A1 (build_info moves to FOUNDATION) |
| skills.skill -> analyzer.analyzer (top) | none: footnote 6 |
| memory.embedding -> control.run_coordinator (lazy) | H2 |
| metrics.evidence -> control.persistence; metrics.thresholds -> config.authority_approval (lazy) | none: METRICS may read STATE/CONFIG |
| workflow.context_certification -> kriya.__version__ | A1 (version from FOUNDATION) |
| kup.* -> control.*, core.*, config.*, tools.*, skills.*, policy.*, platform.* (every current direct edge) | B2/B3/B4 (replaced by CLIENT), residual listed per file in O1 |
| cli -> everything | O2 (per-command entries; composition names stay) |

## 4. Rules beyond imports

1. Reason codes: every `reason_code` string in a new contract is a member of a frozenset in the owning module and a tripwire test asserts the set equals the documented table (existing convention: `diagnosis_codes.py`, `kup/policy.py`).
2. Patch coupling: a slice that moves a module lists the patch targets it rewrites (from CURRENT-DEPENDENCY-MAP §8) and the guard paths it updates; `test_patch_points.py` fails if a documented patch point disappears.
3. Facades: a C-type facade module contains ONLY `from <owner> import X as X` lines and a module docstring naming its removal slice; `tests/architecture/test_facades.py` parses every file listed in `FACADES` and fails on any other statement; P1 empties `FACADES`.
4. Registries: the only registries are `core.inference_runtime` (providers), `toolbox.registry` (tools; wraps `Kernel.registry` category "tool"), `skills.registry`, `static_analysis.registry`, `capabilities` (closed table), `platform.services`. A new module-level `dict` registry elsewhere fails `test_direct_access_owners.py` (table of allowed module-level registries).
5. Context: a module in L2-L4 reads the principal, workspace, run and scopes from `RequestContext`/`RunScope`; `current_run_context()` (the lease) may be consulted only inside KERNEL, STATE, EVIDENCE (ContextVar scope) and `tools/validate.py` until H2; after H2 only KERNEL/STATE/EVIDENCE.
6. Provider names: `kriya/workflow` and `kriya/agents` never contain `"ollama"`/`"vllm"`; adding a provider = a new adapter module registered with `register_runtime_adapter` + a `static_analysis`-style tripwire that nothing else changed (I4 proves it with a fake adapter).
7. `kriya/workflow` never imports `kriya/metrics` (existing tripwire, kept).
8. Coordinators/services never import the controller/engine facades (existing `test_prd030_terminal_services.py`, `test_prd031_coordinators.py` kept and generalized by `test_patch_points.py`).
