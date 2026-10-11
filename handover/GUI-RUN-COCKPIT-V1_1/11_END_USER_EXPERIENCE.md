# Desktop end-user experience (branch gui/end-user-experience-v1_1)

Baseline: tag `kriya-gui-v1.0.0-hardened` = main = `5be638e`. Everything below was TRACED in code on that baseline; names were not trusted.

## Capability matrix (Phase 3)

| CAPABILITY | BACKEND EXISTS | READ API | ACTION API | AUTHORITY OWNER | GUI TODAY | REQUIRED UX / DECISION |
|---|---|---|---|---|---|---|
| Open workspace | yes | `workspace.describe` | native chooser in Main | Kriya (describe), host registry (approved folders) | yes | unchanged |
| Create workspace | **no** Kriya operation; `generate` runs in any folder, git or not (greenfield matrix: empty non-git folder passes) | - | - | - | none: user needs mkdir | NEW Kriya-owned `workspace.create` (validated name, `os.mkdir`, never parents, never overwrites) behind a Main proxy; no `git init` (not required, would add an unrequested side effect and needs the git binary) |
| Goal / Start / Review / Approve | yes | `execution.preflight`, `launch.status` | `kriya generate --launch-id`, `kriya launch-decision` | Kriya | yes | unchanged |
| Readiness | partial: preflight has workspace, configuration + authority, state dir, models, recovery, review mode, toolchains | `execution.preflight` | - | Kriya | compact key/value list | add provider/model availability (NEW read op `execution.model_probe`, same local-only rule as `kriya doctor`), per-role routing table, egress policy; blockers keep their typed reasons |
| Skills: list / metadata | yes (`SkillEngine`, folder-based) | CLI only (`skills list/show`) | - | Kriya (`paths.skills`) | none | NEW read op `skills.list`: name, description, category, tags, verified, verified context/date, staged-rule count, scope (project / shared install); rule TEXT not shown (it may carry proprietary documentation) |
| Skills: selection | automatic only (`fact_match` of tags/terms against the repository model inside the run); no manual selection, no CLI option | - | - | Kriya workflow | none | show "Skills are selected automatically by Kriya"; actual selection is shown from the run's recorded evidence (Context module) - NO manual picker invented |
| Skills: create / approve / promote / unverify | CLI only: skeleton create (writes into `paths.skills`; warns when that is the shared install dir), approve/promote of staged rules, unverify, interactive gap answers | - | CLI | operator (git-committed skill files) | none | NOT exposed: each mutates skill files with git commits and judgement prompts; a GUI action would be a second skill system. Registered `GUI-SKILLS-MUTATION-001` |
| Skills from local document / folder / URL | **no Skill build/compile operation exists.** `kriya learn --file/--url/--text` indexes text into the local RAG memory (a different store, embedding server required; an explicit URL is deliberately not egress-gated) | - | `kriya learn` | operator | none | NOT presented as Skill building. Registered `GUI-SKILLS-BUILD-001` (backend gap) |
| KnowledgeGuard / skill gaps | yes, in the workflow; desktop runs use knowledge policy `strict`: a gap stops the run (no terminal to answer) | `execution.preflight.interaction` | - | Kriya | note in preflight | unchanged (existing `GUI-EXEC-APPROVAL-001`) |
| Tools (native plugins) | yes (`PluginManager`, `plugins/core_tools`) | CLI `tools list` starts a kernel and MCP servers | `tools execute` (CLI) | Kriya / operator config (`plugins.enabled`) | toolchain visibility only | NEW read op `tools.describe` from CONFIGURATION ONLY (no process spawned): enabled plugins, configured MCP server names + transport, and "Managed by operator configuration" |
| MCP servers / tool authority / health | yes: `mcp inspect/approve/revoke` (durable per-tool approval, TOOL-002/003) | CLI `mcp inspect` (spawns every configured server) | CLI `mcp approve/revoke` | operator | none | read-only visibility; NO command/args/env/secret ever shown; approval and health stay operator/terminal actions (health needs spawning servers: not done from the GUI). Registered `GUI-TOOLS-HEALTH-001` |
| Credentials / secrets | config `llm.api_key` etc. | never | never | operator | never shown | never exposed |
| Model routing | yes: `llm`, `llm_chain` (fallback), `agent_llms.<role>`, evidence routing (`model routes`) | `execution.preflight.model` | - | operator config | one line | table Planner / Architect / Developer (= default model) / Reviewer + other roles with override, fallback chain, availability |
| Autonomy / review mode | yes | `execution.preflight.interaction` | - | operator config | text | unchanged (no selector) |
| Egress / config / security authority | yes | `preflight.configuration.authority` | `kriya authority approve` (CLI) | operator | shown | add egress policy to the readiness view; unchanged otherwise |

## The 14 questions, answered from the trace (before this branch)

1 create a project without Terminal: **no** (open yes) - 2 goal: yes - 3 readiness: partly - 4 inspect/select Skills: **no** - 5/6 build a Skill from documents / URL: **no (backend has no such operation)** - 7 tools a run can see: toolchains only - 8 configure a tool: **no, operator-managed by design** - 9 model routing: one line - 10 Start: yes - 11 leave Kriya in the background: yes, but no signal - 12 return when approval is needed: **no notification** - 13 review/approve/reject: yes - 14 outcome/evidence: yes.

## Plan (priority order of the owner's instruction)
workspace.create + New Project -> readiness (model probe, routing, egress) -> Skills (read-only) -> Tools (read-only) -> notifications + Return to Active Run + approval indicator -> Runs search/filter -> productivity actions -> reproof. Run Report: deferred to v1.2 unless it proves narrow.
