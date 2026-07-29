# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Kriya is a local-first, multi-agent AI engineering platform (Python, `kriya/` package). It runs entirely against local LLM/embedding endpoints (Ollama or any OpenAI-compatible server) and coordinates Planner → Architect → Developer → Reviewer agents to plan, generate, fix, and review code inside a target repository, backed by a hybrid AST/vector/lexical index stored in SQLite.

## Commands

```bash
# Setup (Python 3.10+)
python3 -m venv .venv
source .venv/bin/activate
pip install -e .                    # installs `kriya` and `kriya-mcp` console scripts

# Run the CLI (editable install puts these on PATH inside the venv)
.venv/bin/kriya doctor               # checks dirs + LLM/embedding connectivity
.venv/bin/kriya --config kriya.yaml <command>   # -c/--config points at a project config

# Tests
.venv/bin/pytest                     # full suite (asyncio_mode = strict, see pyproject.toml)
.venv/bin/pytest tests/test_workflow.py            # single file
.venv/bin/pytest tests/test_workflow.py::test_workflow_fallback_chain   # single test
```

There is no separate lint/format command configured in this repo — don't invent one.

## Configuration resolution

`kriya/config/config.py::load_config()` layers config in this order (later wins, dicts are shallow-merged one level deep):
1. `kriya/config/default_config.yaml` (packaged defaults)
2. `kriya.yaml`/`kriya.yml` — first checked in CWD, then in the Kriya install directory, unless `--config` is passed explicitly
3. `AppConfig` (pydantic model in the same file) validates the merged result

Relative `paths.*` and `plugins.directory` values are resolved against the directory the config file was loaded from (or the install dir for the packaged default). `autonomy.sensitive_paths` always has a baseline set of regexes (`.env`, secrets, `.github/workflows/`, `Jenkinsfile`, credentials, password) force-appended regardless of user config — don't remove that expectation when touching `load_config`.

## Architecture

### Kernel / registry / plugins (`kriya/core`, `kriya/plugins`)
`Kernel` (`kriya/core/kernel.py`) is a thin async lifecycle object holding a `ComponentRegistry` (category → name → instance, e.g. `registry.get("tool", "filesystem")`) and an `EventSystem` (pub/sub, supports sync and async handlers, emits `kernel_starting`/`kernel_started`/`plugin_initialized`/etc.). `PluginManager` (`kriya/plugins/plugin.py`) discovers plugin packages under `plugins.directory`, imports each as a top-level module (so plugin folder names must be valid, unique module names), finds `BasePlugin` subclasses, and calls `initialize()`/`shutdown()` on them — plugins register `BaseTool` instances into the kernel registry (see `plugins/core_tools/__init__.py` for the filesystem/shell/git/search/ast/validate_refactor tools).

### The generation pipeline (`kriya/workflow/workflow.py`)
`WorkflowEngine.run_generation_workflow` is the core orchestration and is long/stateful by design — read it end-to-end before modifying, don't assume a stage is isolated:
1. **KnowledgeGuard stage 0** (`kriya/tools/knowledge.py`) — scans the goal text for library/version mentions that postdate the configured knowledge cutoff and can short-circuit the whole run with a `knowledge_gap` status requiring explicit confirmation (`--yes`, `--knowledge-policy`, `--ack-knowledge-gap` in the CLI).
2. **Repository analysis** via `RepositoryAnalyzer` (`kriya/analyzer/analyzer.py`) plus **skill matching** via `SkillEngine` (`kriya/skills/skill.py`) — skills activate based on repo facts (dependencies/frameworks detected in analysis) or goal-text/tag matches, not just keywords, and respect `supported_versions` ranges.
3. **Graph RAG retrieval**: hybrid vector+lexical query (`kriya/memory/vector.py::LocalVectorStore.query_hybrid`, backed by SQLite + `sqlite-vec` + FTS5 in `kriya/core/db.py`) finds seed files, then `kriya/analyzer/graph.py::DependencyGraph` expands to a bounded neighborhood; results are assembled into a token-budgeted context string via `build_code_context`, which degrades matched/related files through skeletonization tiers (full → skeleton → signatures) when over budget.
4. **Untrusted learned-knowledge RAG**: anything ingested via `kriya learn` is queried separately and wrapped in explicit "Begin/End Untrusted Reference Context" fencing with an instruction not to treat it as commands — preserve this fencing if you touch that code path, it's a prompt-injection mitigation, not incidental formatting.
5. **Developer + Quality Gates loop**: runs inside an isolated, reused git worktree (`.kriya/worktree`, created/reset by `create_git_worktree`) so compile caches survive across retries. The developer agent (`kriya/agents/agent.py::DeveloperAgent`) either returns full file contents or anchored search/replace `edits` (applied via `apply_anchored_edits`, which requires the search block to match **exactly once**, normalized-whitespace, and to not straddle skeletonized/elided content). `PolymorphicValidator` (`kriya/tools/validate.py`) auto-detects the target stack (Java/Maven via pom.xml, Ruby, else Python) and runs compile checks then targeted tests before a full regression run. On failure the loop retries (bounded by `max(4, 1 + len(llm_chain))`), escalating through `config.llm_chain` fallback models and re-running the context budget allocator against each fallback's own `context_window`.
6. **Human approval gate**: triggered by `autonomy.mode == "human-in-the-loop"`, a sensitive-path match, or diff size exceeding `autonomy.risk_threshold_lines`; only after approval are worktree changes copied into the real workspace.
7. **Lesson extraction**: when a fallback-model escalation resolves an error, a one-sentence rule is extracted and staged into `skills/auto-<repo-slug>/staged_rules.txt` (promoted to `rules.txt` via `kriya skills approve`).
8. **Reviewer + trace log**: final review pass, then a run trace (plan, retries, gate outcomes, retrieved chunks, model hops) is persisted via `kriya/core/trace.py::TraceLogger` to `<paths.logs>/traces.db` (viewable with `kriya traces`).

### Agents (`kriya/agents/agent.py`)
`BaseAgent` wraps `LLMClient.complete`. `PlannerAgent`/`ArchitectAgent`/`ReviewerAgent` are single-shot prompt agents; `DeveloperAgent.run_generation` first asks the model for a file list, then either accepts a full JSON file-object array in one shot or falls back to iterative per-file generation (one completion per filepath) when the model only returns paths — there's also a raw-JSON-extraction fallback for models that wrap output in markdown or partial JSON. Any change to the developer agent's output contract needs to keep both the "batch JSON" and "iterative per-file" paths working, plus the `path`→`filepath` key normalization.

### Egress control (`kriya/core/llm.py`)
`LLMClient.complete` enforces `autonomy.egress_policy == "local_only"` by resolving the target base URL's hostname and rejecting anything not loopback/private/`.local` (`is_local_url`), raising `EgressViolationError`. This is a safety boundary, not a convenience check — don't bypass it silently when adding new LLM call sites.

### Storage (`kriya/core/db.py`, `kriya/memory/`)
All persistent state lives in SQLite databases under `paths.memory` (`vector_index.db`, `web_knowledge.db`, `dependency_graph.db`, `knowledge_cache.db`) and `paths.logs` (`traces.db`), opened with WAL journal mode and a busy timeout for concurrent-safe access. `vector_index.db` mixes code-index vectors and a separate `learned_knowledge` table (from `kriya learn`) — keep those namespaces distinct, the workflow relies on querying them separately with different trust levels.

### CLI (`kriya/cli.py`)
Click-based command group (`kriya` entry point defined in `pyproject.toml`). Key subcommands: `doctor`, `config`, `plugins`, `prompt render|generate`, `tools list|execute`, `analyze <path>` (indexes + prints repo model), `skills list|show|create|approve`, `generate <goal>` (the full workflow), `review <path>` (LLM code review, git-status-aware for directories), `ask <question>` (repo Q&A with RAG), `learn` (ingest URL/file/text into the RAG store), `fix` (diagnostic repair workflow from an error log/stdin), `traces`. Most commands build their own `Kernel`/`AppConfig`/`LLMClient` per-invocation rather than sharing global state — follow that pattern for new subcommands.

## Notes on the docs directory

`docs/design.md` describes the target architecture and mostly matches the implementation above. `docs/user_guide.md` describes some CLI flags/config shapes (e.g. `analyze --graph/--vectors`, multi-profile `llm.profiles`) that are **not** what's currently implemented in `cli.py`/`config.py` — treat the actual code as ground truth over the user guide when the two disagree.
