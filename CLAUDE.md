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
.venv/bin/pytest                     # full suite (asyncio_mode = strict, see pyproject.toml) - entirely mocked, no live model calls
.venv/bin/pytest tests/test_workflow.py            # single file
.venv/bin/pytest tests/test_workflow.py::test_workflow_fallback_chain   # single test
.venv/bin/pytest -m live_model       # tests/test_live_smoke.py only - needs a real local Ollama running, excluded by default
```

There is no separate lint/format command configured in this repo — don't invent one.

## Live-model CI tier (`tests/test_live_smoke.py`, `.github/workflows/ci.yml`'s `live-model-smoke` job)
Every other test in this repo runs against mocks - zero live LLM/embedding calls. `pyproject.toml`'s `addopts = '-m "not live_model"'` excludes this tier by default (both locally and in the `test`/`lint`/`lock-file` CI jobs); pass `-m live_model` explicitly to run it. CI runs it in a separate, `continue-on-error: true` job that installs Ollama fresh and pulls two small models (`qwen2.5-coder:1.5b`, `all-minilm`) - free on GitHub-hosted runners since this repo is public. The bar is deliberately narrow: "did the real pipeline complete without crashing on a real API response shape," not code-generation quality - a small CI-pulled model isn't held to the same bar as whatever model a real project configures. Note `SkillEngine` always loads Kriya's own global skill library (`load_global=True`, no config override) in addition to any project-local skills, which meaningfully inflates prompt size regardless of goal relevance - confirmed live to matter for a small model's throughput, hence the generous 600s timeout on the `generate` smoke test.

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
5. **Developer + Quality Gates loop**: runs inside an isolated, reused git worktree (`.kriya/worktree`, created/reset by `create_git_worktree`) so compile caches survive across retries. The developer agent (`kriya/agents/agent.py::DeveloperAgent`) either returns full file contents or anchored search/replace `edits` (applied via `apply_anchored_edits`, which requires the search block to match **exactly once**, normalized-whitespace, and to not straddle skeletonized/elided content). `PolymorphicValidator` (`kriya/tools/validate.py`) auto-detects the target stack from real markers (Java via pom.xml/build.gradle, Ruby via Gemfile/Rakefile/spec, Python via requirements.txt/pyproject.toml/setup.py/setup.cfg/Pipfile/any .py file present) and runs compile checks then targeted tests before a full regression run. Anything matching none of those markers is `"unknown"`, not a silent Python fallback — `run_compile_check`/`run_tests` return `success: True` (so the retry loop doesn't fail forever on a gate that can never run) but say plainly that nothing was actually validated, rather than falsely claiming a Python check passed against zero matching files. On failure the loop retries (bounded by `max(4, 1 + len(llm_chain))`), escalating through `config.llm_chain` fallback models and re-running the context budget allocator against each fallback's own `context_window`.
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
Click-based command group (`kriya` entry point defined in `pyproject.toml`). Key subcommands: `doctor`, `config`, `plugins`, `repl` (interactive session, see below), `prompt render|generate`, `tools list|execute`, `analyze <path>` (indexes + prints repo model), `skills list|show|create|approve`, `generate <goal>` (the full workflow), `review <path>` (LLM code review, git-status-aware for directories), `ask <question>` (repo Q&A with RAG), `learn` (ingest URL/file/text into the RAG store), `fix` (diagnostic repair workflow from an error log/stdin), `traces`. Most commands build their own `Kernel`/`AppConfig`/`LLMClient` per-invocation rather than sharing global state — follow that pattern for new subcommands.

### Interactive session (`kriya/repl.py`)
`kriya repl` loops reading a line and dispatches it straight into the same `main` Click group via `main.main(args=tokens, standalone_mode=False)` — deliberately no separate command parser; `generate "goal" -y` inside the session is the exact same code path as `kriya generate "goal" -y` from a shell, so there's nothing REPL-specific to keep in sync when a subcommand's options change. `--config` is captured once at session start and auto-prepended to every dispatched line unless the line supplies its own. Uses `prompt_toolkit` for the boxed prompt and a `Completer` (`_SlashCommandCompleter`) that lists every non-hidden top-level command live when the line starts with `/` — commands enumerated straight from `cli_main.commands`, not a hardcoded list. `main` is `@click.group(invoke_without_command=True)`; bare invocation (`ctx.invoked_subcommand is None`) starts the session too, but only when `sys.stdin.isatty()` — non-TTY bare invocation (piped/CI) falls through to Click's own help text instead, matching the TTY-safety pattern already used for `generate`/`fix`'s approval gates.

### Natural-language routing (`kriya/routing.py`, off by default — `routing.enabled`)
Lets a `kriya repl` line be plain English instead of an explicit command. `Router.route()` runs two independent checks concurrently: an embeddings centroid classifier (`routing.embed_model`, default `embeddinggemma:latest` — deliberately separate from `embedding.model`, which stays tuned for the RAG index, a different task) ranks the six routable commands by cosine similarity against curated exemplars; a narrow LLM gate (reuses `llm.model`) answers only "is this in scope at all," fails closed on any error. Below `routing.reject_threshold` or gate=no → `UNROUTABLE`. Top-2 candidates within `routing.ask_margin` of each other → `CLARIFY` (ask the user, don't guess — `kriya/repl.py::_resolve_clarify`). `build_dispatch_tokens()` maps the resolved command to real CLI argv — not uniform passthrough: `fix` needs `--error` not a positional arg, `review`/`analyze` need an *existing path* (`click.Path(exists=True)`) so natural language routes to `.` (repo root) rather than the raw text, `skills` defaults to `list`. Production port of `spikes/version_b_routing/` (not packaged/shipped — verify before trusting any specific number quoted from it, re-run against current local models if it matters) — see that spike's README.md for the full feasibility investigation and why this specific architecture (embeddings + gate + ask-when-uncertain) was chosen over pure-embeddings or pure-LLM routing.

## Notes on the docs directory

`docs/design.md` and `docs/user_guide.md` were reconciled against the actual implementation (2026-07-29) — mismatches like `analyze --graph/--vectors`, multi-profile `llm.profiles`, tree-sitter/sqlite-vec/GBNF claims, etc. were corrected in place. They should now match the code; if you find a new mismatch (e.g. after a future feature change), treat the actual code as ground truth and update the docs rather than assuming the docs are still authoritative.
