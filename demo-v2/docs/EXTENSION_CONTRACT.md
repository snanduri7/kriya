# Skills and Tools: V1 services and the intended extension points

Status: V1 is local and read-only. This page documents the extension points so V1 does not need to be redesigned later. **Only the
parts marked IMPLEMENTED exist.** Everything else is a statement of intent and has no code.

## IMPLEMENTED in Demo V2

| Contract | Methods | Where |
|---|---|---|
| `SkillService` | `list(workspace)`, `describe(workspace, skill_id)` | `kriya/skills/service.py` |
| `ToolService` | `list(workspace)`, `describe(workspace, tool_id)`, `approval_state(workspace, tool_id)` | `kriya/tools/service.py` |
| `SkillDescriptor`, `ToolDescriptor` | frozen, JSON-serialisable | same modules; wire shape in `ui/kup/schema/catalog.schema.json` |
| KUP operations | `skills.list`, `skills.describe`, `tools.list`, `tools.describe`, `tools.approval_state` | `kriya/kup/catalog_ops.py` |

Identity is the skill name / tool id, never a filesystem path. `workspace` is the existing KUP scope argument (as for every workspace-scoped operation);
it selects the context, it is not part of an item's identity.

**Unknown is explicit.** A descriptor field the backend cannot determine is `null` and is named in `unknown_fields`; a status that cannot be
determined is the literal `UNKNOWN`. Values are never invented. Examples: a skill has no version of its own (only the technology range it supports), so
`skill_version` is unknown; an MCP server's real version is only known after it is started, so `version` is unknown.

**No secret material.** A tool descriptor carries the server name, the command's executable basename, the argument COUNT and the environment variable NAMES.
It never carries arguments or values. A test plants secret-shaped strings in arguments and environment and asserts they appear nowhere in the serialised response.

**Tool approval state is read-only and honest.** MCP invocation approvals bind to the exact tool schema digest, which exists only after the server is
started and its tools are listed. The service reads the durable approval store (outside the workspace) without starting anything, so it can state:
`NOT_APPROVED` (no record for the server: it provably cannot be approved), or `UNKNOWN` with a reason (records exist but their validity needs the live tool identity;
the store is unreadable or invalid; the store path was refused). It never reports `APPROVED`.

## INTENDED, NOT IMPLEMENTED (extension points)

| Extension point | Purpose | Notes |
|---|---|---|
| `SkillSourceProvider` | where skills come from (local directory, git repository, remote catalogue) | V1 has one implicit provider: the local skills directories the engine already scans |
| `ToolProvider` | where tools come from (configured MCP servers, plugins, a catalogue) | V1: configured MCP servers and enabled plugins |
| `CatalogProvider` | browse and search available skills/tools | none in V1 |
| `DependencyResolver` | resolve a skill's/tool's dependencies and version conflicts | none in V1 |
| `SecretProvider` | hold credentials for tools outside configuration | none in V1; descriptors must never carry secrets |
| `AuthProvider` | OAuth or other flows for tools | none in V1 |

Future lifecycle, to which V1 descriptors are compatible: `discover -> resolve -> validate -> install -> activate`. V1 implements only the read side of `discover`.
Descriptors already carry `id`, `source`, `scope`, `digest` (where known) and version-bearing fields so a provider can fill them without changing the wire shape.

## Not extension points

The kernel stays non-plugin: workflow, authority, evidence, domain state and transactions. Mutation of skills or tools (enable, disable, install, remove, update,
approve, unverify, add or remove an MCP server) is not part of V1 and, when added, goes through the existing authority paths (SEC-009 configuration authority,
TOOL-002 invocation approval), never around them.
