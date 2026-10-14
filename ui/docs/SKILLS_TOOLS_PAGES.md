# Skills and Tools pages (Demo V2)

Two read-only destinations in the cockpit rail: **Skills** and **Tools**. They show what Kriya has for the open folder and where it came from. They change nothing,
start nothing and show no secret.

## Path

```
Skills / Tools page  ->  UIProvider  ->  HostAdapter.query(KupRequest)  ->  typed IPC allowlist  ->  kriya kup-stdio  ->  SkillService / ToolService
```

The renderer reads descriptors by id. It never reads a skill file, a tool configuration or the approval store, and no descriptor carries a filesystem path.

## Operations (KUP, capability set 1.1, additive)

| Operation | Arguments | Answer |
|---|---|---|
| `skills.list` | `workspace` | `SkillDescriptor[]`, `total`, `truncated` |
| `skills.describe` | `workspace`, `skill_id` | `found`, `skill` |
| `tools.list` | `workspace` | `ToolDescriptor[]`, `total` |
| `tools.describe` | `workspace`, `tool_id` | `found`, `tool` |
| `tools.approval_state` | `workspace`, `tool_id` | `found`, `tool_id`, `approval` |

Schema: `ui/kup/schema/catalog.schema.json` (types and validators generated from it). Backend: `kriya/kup/catalog_ops.py` over `kriya/skills/service.py` and
`kriya/tools/service.py`. Served only by the persistent service (no one-shot CLI flag). `execution.context`'s Skills block is built from the same `SkillService`.

## What the pages deliberately do not do

- No enable, disable, install, import, remove, update, approve, unverify, add or remove an MCP server. Those stay in the terminal (`kriya skills ...`, `kriya mcp ...`).
- No rule text: it is acquired content and stays in the skill's folder. Provenance shows where rules came from and whether they were verified.
- No tool is started. Health and version are shown as not known. Environment variable NAMES and an argument COUNT are shown; values and arguments never are.

## Approval state

`NOT_APPROVED`, `UNKNOWN` or `NOT_APPLICABLE`, each with a reason code. There is no "approved": a durable approval binds to the tool's live schema digest, which exists only after the
server is started, so a record on file is reported as `UNKNOWN` (`RECORDS_NEED_LIVE_TOOL_IDENTITY`). The schema has no APPROVED value, so a producer that tried to send one would fail validation.

## Where MCP servers come from

MCP servers are a security-authority configuration field: a folder's own `kriya.yaml` cannot supply them. The Tools page therefore lists servers from the operator's configuration. A folder that
tries to supply one gets `available: false` with `CONFIG_AUTHORITY_REFUSED`, and nothing from its `mcp` block is returned.

## Tests

`tests/test_skill_service.py`, `tests/test_tool_service.py`, `tests/test_kup_catalog.py` (Python); `ui/standalone/test/catalog_ipc.test.ts` (request grammar), `catalog_real.test.ts` (real `kriya kup-stdio`);
`ui/shared/src/cockpit/modules/{skills,tools}/*.test.tsx` (pages) and `providers/fixture/catalog.contract.test.ts` (fixture shapes against the generated validators).
