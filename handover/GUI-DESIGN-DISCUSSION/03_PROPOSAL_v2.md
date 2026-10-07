# 03 — Kriya GUI proposal v2

## Response to review 1

| Finding | Severity | Response | Change / disposition |
|---|---|---|---|
| R1-1 | MAJOR | ACCEPTED | P-24–P-27: M1 proposes a versioned JSON inspection contract in Kriya, subject to owner authorization. No direct database access from either shell. |
| R1-2 | MAJOR | ACCEPTED | P-28–P-29: remove permission for logging/SQLite housekeeping. Use a verified write-denying inspection path or typed refusal; no immutable live-read fallback. |
| R1-3 | MAJOR | ACCEPTED | P-33–P-35: owner requirements supersede Tk. Shared web components, Eclipse native integration and standalone Tauri; compare local-file browser delivery and define fixture prototype criteria. |
| R1-4 | MINOR | ACCEPTED | P-22, P-30: separate History store and Workspace contexts. No guessed workspace association. |
| R1-5 | MINOR | ACCEPTED | P-36: fixture/fake-process testing only while the matrix is protected. No live CLI invocation or protected-store inspection in this discussion. |
| R1-6 | MINOR | ACCEPTED | P-24, P-27: version every KUP envelope and negotiate capabilities; package version alone is insufficient. |
| R1-7 | MINOR | ACCEPTED | P-34: separate UI packages, Eclipse feature/update site and standalone distribution, enforced dependency direction. |
| R1-8 | MINOR | ACCEPTED | P-32, P-35: sanitization is defense in depth, not an assertion that Click emits styling to pipes. Bound response sizes and exercise large fixtures. |

Baseline: `codex/fix-demo1-attribution` @ `61a867fc31a7b5ff5d7f21d1ea15e4eac43f2e03`.
Author: ChatGPT, proposer. Date: 2026-10-04. Status: proposed, awaiting final review.
This proposal supersedes `01_PROPOSAL.md`. Claim numbering continues from P-19. TRACED references describe baseline code; INFERRED identifies proposed behavior requiring the named verification. External documentation supports API feasibility only, not measured compatibility. No implementation or live model execution is included.

## Response to owner requirements

| Requirement | Response | Implementation in this proposal |
|---|---|---|
| OR-1 | ACCEPTED | P-20, P-33–P-35: both delivery forms in M1, Eclipse prototype first; common contract, presentation logic and rich panels. |
| OR-2 | ACCEPTED | P-21–P-23: run timeline, exact recorded context and evidence inspection, native recorded comparisons. |
| OR-3 | ACCEPTED | P-20, P-31: Kriya alone owns execution and authorization. |
| A-1 | ACCEPTED | P-24–P-27: one JSON Schema contract, generated TypeScript and Java types. |
| A-2 | ACCEPTED | P-31, P-37: child-process pipes; M2 JSON-RPC over stdio. No network listener. |
| A-3 | ACCEPTED | P-31, P-34: no Kriya imports or direct Kriya state reads in shells. |
| A-4 | ACCEPTED | P-23, P-33: native Runs, Compare and problem markers; shared embedded timeline/inspector. Data and metadata-write qualifications are explicit below. |
| A-5 | ACCEPTED | P-34: Tauri preferred; local-file browser alternative evaluated; Eclipse update site with a verified Java/Eclipse target. |
| L-1 | ACCEPTED | P-21, P-30: run timeline is primary; missing stages are visibly unrecorded. |
| L-2 | ACCEPTED | P-23, P-30: exact recorded context first-class; summaries are not represented as complete model input. |
| L-3 | ACCEPTED | P-23, P-30: recorded causal attribution and categories only; otherwise attribution unavailable. |
| L-4 | ACCEPTED | P-22, P-30: visible trust strip with provenance, scope and unknown states. |
| L-5 | ACCEPTED | P-33: same inspector component and behavior in both shells. |

## Product and scope

**P-20 — INFERRED:** Build an engineering run inspector for developers diagnosing code-generation results and reviewers checking evidence. M1 delivers both an independent app and an Eclipse plugin. Shared logic covers normalization, selection, filtering, timeline projection, inspector and availability rules; hosts supply native rendering/navigation and process transport. M1 cannot generate, edit, recover, prune, migrate, approve, revoke or change configuration. Later actions need a separate gate and existing CLI-equivalent checks. Settle through command/dependency inspection and end-to-end fixture tests in both hosts.

**P-21 — INFERRED:** The default layout has a compact context/trust strip, a resizable Runs column (about 22%), the main run timeline (about 48%), and an Inspector (about 30%). A collapsed bottom drawer holds diagnostics and the why-panel; selecting a file opens a comparison in the main area, or Eclipse's Compare editor. On narrow widths the inspector becomes a tab beside the timeline. Use restrained colors, text plus icons for states, keyboard navigation, searchable/virtualized lists and progressive disclosure. The selected run header shows full goal, recorded outcome, attempts and duration. Verify with owner tasks: find a failed run, identify an omitted target, inspect its evidence, and compare recorded changes without opening raw logs.

```text
History store: <resolved path>   Workspace: <explicit path>   Kriya: <identity>
Run observation: <time>    Model / qualification / approval: recorded or unknown
┌ Runs / filters ┬ Timeline: goal → recorded stages / subtasks / attempts ┬ Inspector ┐
│ outcome, time  │ Select event → inspect its source and evidence        │ Context   │
│ full-goal hint │ Gates → Review → Commit, where actually recorded      │ Prompt    │
│                │ Unrecorded stages remain unknown                       │ Output    │
│                │                                                        │ Gates     │
│                │                                                        │ Evidence  │
└────────────────┴────────────────────────────────────────────────────────┴───────────┘
Recorded comparison / diagnostics       Why: recorded failure → attribution → evidence
```

**P-22 — INFERRED:** History store and workspace are independent selectors. History results are labelled by the resolved store returned by Kriya; workspace status is a separate observation. Do not display a selected historical run as belonging to the selected workspace without an exact stored ownership link. Keep store identity, installed provenance, observed RUN_ACTIVE, recorded model/runtime identity, qualification and approval status visible, with unknown/unavailable labels and observation times. Never run qualification or approval commands to populate the strip. Test unrelated workspace/store combinations and historical-versus-current qualification mismatches.

**P-23 — INFERRED:** Inspector selection is run → recorded subtask → attempt → event/evidence. Context shows recorded paths, tiers, member ids, omissions/reasons and recorded token accounting, with estimated versus provider-measured counts distinguished. Prompt is an explicit sensitive-data request and is labelled with its recorded role/scope; Output does not recreate missing model responses. Why displays first incorrect state/cause/evidence only when recorded attribution supports those relationships. The owner's PROTOCOL_v2 vocabulary (PLANNING / CONTEXT / DEVELOPER_OUTPUT / VERIFICATION / FALLBACK / BENCHMARK / INFRASTRUCTURE) is a display vocabulary for recorded classifications, not a new classifier or a remapping of Kriya's failure categories. Unsupported attribution reads “not recorded.” Native markers require an exact recorded path/line/severity and verified workspace/revision association; no parsing prose to manufacture locations. Compare requires recorded before/after text with provenance, and is never writable. Verify synthetic complete/missing/conflicting records in both hosts.

## KUP v1: one inspection contract

**P-24 — INFERRED:** Define Kriya UI Protocol (KUP) v1 as checked-in JSON Schema with generated TypeScript and Java types, plus runtime validation in both shells. Every new response uses this envelope:

```json
{
  "schema_version": 1,
  "operation": "history.list",
  "request_id": "opaque-client-id",
  "observed_at": "UTC observation time",
  "source": {"state_directory": "absolute path", "trace_database": "absolute path"},
  "consistency": {"kind": "sqlite_transaction_snapshot", "live_stream": false},
  "data": {},
  "error": null
}
```

Kriya sets source and observation fields. Error envelopes preserve schema_version/operation/request_id and use nullable source/consistency when resolution fails. A stored timestamp is preserved verbatim in payloads, with timezone unknown when not recorded. Each optional panel has availability (`recorded`, `not_recorded`, `unreadable`, `excluded`, `unsupported`) plus provenance and reason. Unknown event values are preserved as literal data, not treated as success. Generate/validate round-trip golden fixtures in Java and TypeScript; schemas cannot silently discard unknown raw event details.

**P-25 — INFERRED:** Propose these narrowly scoped CLI inspection additions, all subject to the owner's gate. They share a Kriya-owned read adapter; no workflow/model runtime is constructed.

| Operation | Proposed invocation | Payload |
|---|---|---|
| capabilities | `kriya traces --capabilities --json` | KUP versions, supported operations, identity/provenance, limits and feature availability |
| history.list | `kriya traces --json -n N [--cursor C]` | Full run id/goal, timestamp as stored, duration, attempts, status, failure category, milestone linkage; bounded page and next cursor |
| history.detail | `kriya traces --json --run-id ID` | One exact stored row: run_events, evidence_records, gate_outcomes, model_hops, generation_metrics, failure_report, retrieved_chunks, active_skills and files_modified in recorded form; field availability and provenance |
| history.prompt | `kriya traces --json --run-id ID --include-prompt` | Explicitly requested stored prompt_rendered, labelled with known role/scope and availability; no claim of per-attempt completeness |
| workspace.status | `kriya runs status --workspace PATH --json --kup-version 1` | Envelope around existing recovery assessment; preserve status and semantic exit codes |

Use an extension of `traces` rather than introducing a second history source under `runs show`. `--run-id` cannot combine with list pagination. IDs/cursors are opaque values passed as arguments, validated and bound as query parameters. Default list limit 50, maximum 200. Cursors use recorded timestamp plus run id and store binding; pagination is observational, not a stable snapshot across multiple command invocations. Prompt content is excluded from list and default detail, and has no automatic prefetch. Data fields may still contain source-sensitive content: exclusion of prompt_rendered is not a general redaction guarantee. Verify command grammar, pagination with equal timestamps and altered stores, sensitive-data exclusion and injection tests.

**P-26 — INFERRED:** Define optional, typed `context`, `attribution`, `diagnostics` and `comparisons` sections in detail responses, with provenance pointers into persisted records. V1 starts by exposing trace-row events; it does not add new workflow telemetry. Optional reading of already persisted workspace records belongs only inside Kriya's adapter and requires exact workspace/run ownership and authorized path checks. Never attach a workspace ledger using time/goal similarity. If no exact persisted association or before/after content is proven, return not_recorded/unsupported. These optional sections must be fixture-tested now; availability in real histories is not promised. Additional telemetry persistence or arbitrary file-reading commands need a later topic/gate. Settle available adapters through a baseline source-to-writer inventory before implementation scope is finalized.

**P-27 — INFERRED:** Shells accept only explicitly supported schema versions and capabilities. Package identity is displayed but cannot substitute for KUP compatibility. Refuse incompatible payloads with `UNSUPPORTED_SCHEMA_VERSION`; malformed compatible JSON is `INVALID_RESPONSE`, never a guessed rendering. Existing unversioned `version --json` is used only as a bounded bootstrap identity response; capabilities establishes KUP compatibility. Existing unversioned status remains unchanged for CLI users; the opt-in KUP envelope supplies the version. Valid nonzero workspace-status responses remain assessments; transport errors remain errors. Test future versions, absent fields, unknown states and each exit-code mapping.

## Strict inspection behavior

**P-28 — TRACED:** Current `traces` is not a zero-write implementation: CLI initialization loads configuration and bootstraps logging (`kriya/cli.py:155–166`), connection code creates directories and sets WAL pragmas (`kriya/core/db.py:9–20`, `kriya/core/db.py:40–42`), and TraceLogger creates/migrates schemas (`kriya/core/trace.py:16–26`). Config authority violations fail closed when approvals do not cover them (`kriya/config/config.py:2090–2102`); they are not silently stripped. The KUP inspection path must preserve SEC-009 and state-path resolution while avoiding logging setup, migrations, legacy copying, plugin/model initialization and automatic bytecode writes. This is a proposed CLI initialization/read-adapter change, not a claim that the baseline already meets it.

**P-29 — INFERRED:** Choose typed refusal over `immutable=1` for unsafe WAL reads. The adapter uses SQLite read-only URI access without the globally patched WAL setup, and only a connection implementation verified to deny filesystem writes, including creation/modification of WAL/SHM sidecars. `mode=ro` alone is insufficient proof. No checkpoint, lock creation, scratch database, permissions change, directory creation or copying to obtain a read. Missing stores return a typed empty/unavailable result without creation; unreadable, locked or unsupported WAL conditions return `READ_ONLY_UNAVAILABLE`/`STORE_BUSY`. Never silently omit WAL or claim a stale immutable read is current. Implementation of the write-denying boundary is an acceptance prerequisite: if standard connection behavior cannot meet it on the target, refuse that state or return the limitation to the owner; do not loosen the rule. Fixture verification covers rollback journal, WAL with/without WAL/SHM, read-only directories, active writers, disappearance races and bounded lock timeout. Inventory plus filesystem-write interception must establish no writes to Kriya state, logs, source, control records or operator state; inventories alone cannot detect transient writes.

## Persisted-data inventory and panel limits

**P-30 — TRACED:** The following inventory establishes what baseline writers persist. It does not assert every run reaches every writer or has complete stage coverage. All source paths are relative to the baseline repository.

| Panel / signal | Persisted source and writer | Exact limits / v1 behavior |
|---|---|---|
| Runs, summary, milestone linkage | runs columns in `kriya/core/trace.py:32–74`; serialization/upsert at `:129–155` | No workspace column. timestamp has no timezone; files_modified is comma-joined (`:130`), so preserve raw value rather than claim a lossless file list. |
| Timeline, attempts, gate outcomes, evidence, hops | `workflow.py:5463–5482` writes gate_outcomes/model_hops/run_events/evidence_records; `_trace_run_events` at `:1280–1297` serializes recorded events | Event source/authority/attempt/time from `run_events.py:20–41`. No guarantee of a complete analyze→commit sequence. Preserve recorded order; no fabricated stage completion. Trace rows can exist without RunRecords (`run_trace.py:15–17`). |
| Context tiers / omissions | `attempt.py:6995–7014` records context.known_target_package; `:1067–1082` records retry member-hint package; events flow through trace writer above | Actual path/member/tier/omission/package hash summaries on these paths; no full rendered source and no exhaustive per-request visibility guarantee. |
| Retry source provenance | `attempt.py:1088–1100` emits context.retry_target_source | Recorded targets and mode only; cannot recover all model input text. |
| Token composition | `attempt.py:2632–2642` emits developer.prompt_composition; `prompt_composition.py:36–74` builds estimates/provider totals | Section estimates explicitly labelled; no exact budget percentage if capacity/usage for that request is absent. |
| Package summaries and other omissions | `control/telemetry.py:83–114`; `control/decisions.py:105–135` persists explicitly requested ledger writes with ownership tags | Builder is not proof of persistence: ledger can remain in memory (`workflow_controller.py:3704–3707`). Only expose persisted, exactly owned records; do not call builders to recreate history. |
| Prompt | `workflow.py:5472` stores plan_prompt as prompt_rendered; trace column at `core/trace.py:42` | This path supplies a planning prompt, not each Developer request. Prompt tab labels its scope; complete per-attempt prompts and rendered guidance are unavailable unless a separate persisted record is demonstrated. |
| Failures / why | `core/trace.py:67–73`, `:139–154` stores failure_report; baseline category vocabulary at `failure_reporting.py:43–79` | These categories are not automatically PROTOCOL_v2 causal attribution. Full causal chain is unavailable unless explicitly recorded with evidence. |
| Model identity / qualification | `_trace_run_events` emits model.role_metrics (`workflow.py:1291–1295`); retry refusal records qualification (`attempt.py:2162–2189`) | Show recorded identity/qualification only where present. A refusal event is not proof of all models' qualification or current status. |
| Active workspace / recovery | `RecoveryAssessment.to_dict` at `control/recovery.py:222–232`; CLI at `cli.py:3824–3843` | Status belongs to selected workspace at observation time, not automatically to a selected trace row. |
| Approval / SEC-009 | configuration enforcement at `config/config.py:2071–2102` | Enforced on load; complete historical approval decisions are not guaranteed in trace schema. Unknown until an owned recorded fact is available. |
| Historical diffs / output | trace stores modified paths, not before/after text (`core/trace.py:32–44`); CommitEvidence stores operations/revisions (`workflow/edit_safety.py:107–126`) | These declarations do not establish retained full before/after text or complete Developer output. M1 demonstrates fixture comparisons; real unavailable comparisons/output stay unavailable. No comparison against today's file presented as historical. |

The remaining data gaps are an explicit gate issue: if the owner requires complete context, prompts, causal attribution or diffs for every real run in M1, new telemetry work must be separately scoped before acceptance. A read-only serializer cannot create missing historical facts.

## Host boundaries and Eclipse integration

**P-31 — INFERRED:** Shells launch the explicitly selected installed Kriya executable as a child using argument arrays, no shell, and only the P-25 command grammar. No arbitrary terminal, command string, environment override, trust injection or imports of Kriya internals. M1 closes stdin and receives one response on stdout plus stderr diagnostics. Kriya remains the sole reader of its stores and sole authorization/execution authority. Native IDE opening of an explicitly selected real file is navigation through Eclipse's existing editor, not a Kriya-generated edit path; the plugin provides no apply/merge/save operation. Embedded web panels cannot access state or native APIs except the validated host contract. Verify hostile strings, unknown bridge messages, dependency direction and all forbidden command families.

**P-32 — INFERRED:** Keep manual refresh for M1. Requests carry selection/context generations; stale results are discarded, and failed refresh visibly invalidates currentness. Commands time out after 60 seconds including bounded SQLite waiting. Set an 8 MiB stdout envelope limit and 1 MiB stderr limit, then validate against a 4 MiB raw-event fixture after JSON escaping. Oversized responses are typed `RESPONSE_TOO_LARGE`, never partial valid data; prompt reads may refuse rather than stream in M1. Only GUI-owned child processes can be terminated. Render output as text, block external navigation/resources, and sanitize control characters as defense in depth. Host bridge messages have strict schemas/limits and no unrestricted file or process APIs. Verify timeout/close/switching, escaping, injection and oversized-output cases.

**P-33 — INFERRED:** Eclipse uses a native JFace Runs table, a workbench ViewPart with SWT Browser panels for the shared React timeline/inspector, and read-only in-memory Compare inputs. Selection reducers/normalization are common TypeScript assets; the native table is a projection and must not implement its own status/causality rules. Java and TypeScript message types derive from KUP plus one host-interaction schema. Eclipse API feasibility is supported by [ViewPart](https://help.eclipse.org/latest/topic/org.eclipse.platform.doc.isv/reference/api/org/eclipse/ui/part/ViewPart.html), [SWT Browser](https://help.eclipse.org/latest/topic/org.eclipse.platform.doc.isv/reference/api/org/eclipse/swt/browser/Browser.html), [BrowserFunction](https://help.eclipse.org/latest/topic/org.eclipse.platform.doc.isv/reference/api/org/eclipse/swt/browser/BrowserFunction.html) and [CompareEditorInput](https://help.eclipse.org/latest/topic/org.eclipse.platform.doc.isv/reference/api/org/eclipse/compare/CompareEditorInput.html). Settle runtime/macOS accessibility through P-35, not API existence.

Eclipse gate failures use plugin-owned non-persistent problem markers with recorded provenance; missing/foreign/stale locations remain in the inspector. Never alter other tools' markers. Eclipse may write its own metadata when creating markers or opening views: the zero-write Kriya guarantee cannot promise zero host metadata writes. Owner gate must permit ordinary IDE metadata with no source/Kriya state writes, or require in-memory annotations instead. Validate marker lifetime, cleanup, revision matching, disabled Compare editing/merge and no source writes in an isolated Eclipse fixture workspace. [Eclipse marker API](https://help.eclipse.org/latest/topic/org.eclipse.platform.doc.isv/reference/api/org/eclipse/core/resources/IMarker.html) supports marker operations; persistence/lifecycle behavior must be tested.

## Technology and packaging

**P-34 — INFERRED:** Proposed future layout: `ui/kup/` for schemas, `ui/shared/` for TypeScript presentation logic/components, `ui/eclipse/` for Java plugin/feature/update-site build, and `ui/standalone/` for Tauri launcher. No package is imported by `kriya/`, no UI dependency enters Kriya's required runtime dependencies, and install/build verification checks that direction. The read adapter lives inside Kriya and is the only inspection implementation. Eclipse target release and its actual JDK requirement must be pinned after checking the owner's installation; “Java 17+” is a lower-bound goal, not a claim that any current Eclipse runs on Java 17. Standalone is a separate macOS arm64 distribution first; installer signing/update-site publication are later explicit delivery actions, never GitHub pushes.

| Alternative | M1 / Eclipse fit | M2 / M3 implications | Decision |
|---|---|---|---|
| Tk | Independent desktop widgets; no shared embedded web inspector | Requires separate IDE renderer and presentation investment | Ruled out by owner requirements |
| Static local HTML | Useful offline snapshot/export; can show fixture report | Browser page cannot independently launch a stdio child; live refresh/actions need an additional host | Optional export later, not primary shell |
| Local web server | Rich shared rendering | Adds listener/origin/session surface; violates A-2 | Ruled out |
| Shared React + SWT Browser + Tauri | Shared panels, native Eclipse integration, standalone child-process host | Can reuse KUP payloads when transport evolves | Recommended, conditional on prototype |
| Electron standalone | Shared web assets and process host | Additional runtime/packaging burden | Fallback only if Tauri fails required checks; owner decision |

Tauri uses a core process and webview architecture, and provides scoped capabilities ([architecture](https://v2.tauri.app/concept/architecture/), [capabilities](https://v2.tauri.app/security/capabilities/)). This supports the proposed thin launcher; it does not prove safety or comparative package size. Implement only narrow validated host commands, no generic shell/filesystem plugin permissions. A local-file browser delivery would require manually importing a KUP snapshot or a separate native integration; neither provides the same standalone workflow without additional work, so Tauri is preferred. Verify package startup, process cleanup and IPC allowlist after gate.

## Prototype, tests and milestones

**P-35 — INFERRED:** After gate, timebox the Eclipse-first feasibility prototype to two engineering days, using fake child processes and synthetic/sanitized fixtures only. Produce the same timeline and Context inspector in SWT Browser on macOS M1 Max and standalone Tauri. Show a recorded fixture diff in Eclipse Compare with merge/edit disabled, and a fixture gate failure as a plugin-owned marker with navigation. Exercise a 2,000-line diff, a 4 MiB event JSON payload (including escaping), unknown event fields and incomplete context. Verify VoiceOver, focus movement between native/web controls, keyboard-only selection, search, contrast and readable resizing. Proposed targets: bounded memory (no monotonic growth after 100 selection cycles), no main-thread freeze longer than 200 ms during interactions, and populated selected fixture within 2 seconds after receipt. Record hardware, measurements and shortcomings; these are targets, not current measurements. If either shell fails shared behavior/accessibility or packaging viability, bring the concrete result to the owner before continuing that milestone.

**P-36 — INFERRED:** Respect the matrix protection in R1-5 without assuming its current live state. During this discussion: documents only in this folder, no implementation, real Kriya invocation, model run, protected-store inspection, commit or push. After gate, while protection remains: fake-process/fixture tests only and no paths under `~/kriya-cagc-v2/` or operator `~/.kriya`. Once the owner confirms protection has ended, offline integration uses a disposable workspace, state/log/authority roots and explicit environment isolation—never live operator data. Test actual inspection code under filesystem-write denial and network denial, prove no model/plugin construction, and audit source/config/control/state/log files plus attempted writes. Test adapters must never migrate, initialize or recover fixtures during inspection. Live model runs are unnecessary for viewer acceptance.

**P-37 — INFERRED:** M1 sequence is (1) approve the KUP/read-adapter scope and data limits, (2) fixture Eclipse-first prototype including standalone shared panels, (3) schema/types and CLI inspection implementation, (4) both shells and protected fixture acceptance, (5) isolated real-CLI inspection acceptance after matrix protection ends. M1 is complete only when both hosts meet read-only inspector criteria; a fixture-only demo is not production acceptance. M2, separately gated, adds `kriya ui-server --stdio` with JSON-RPC request ids, bounded framing, initialization/version/capability handshake and live progress notifications. It reuses KUP types/read services, starts no socket and preserves inspection's no-write property. M3, separately gated, routes approvals/actions through the same Kriya command/application code paths and checks; no permission decisions in a shell and no new model/edit path. This proposal does not implement or authorize either later milestone.

## Owner gate decisions

**P-38 — INFERRED:** The owner should record these concrete conditions in `05_GATE.md` after final review:

1. Authorize the P-25 CLI output additions and the minimal initialization/read-adapter changes needed for strict no-write inspection, with existing non-KUP CLI behavior preserved.
2. Accept typed refusal for unsafe WAL states, with zero-write behavior a measured acceptance prerequisite; no silent immutable fallback.
3. Confirm Eclipse-first plus standalone Tauri/shared React, target Eclipse/JDK installation and macOS arm64 distribution.
4. Accept honest unavailable panels for baseline data gaps, or separately scope the telemetry needed for complete per-attempt context/prompts, causal chains and retained historical diffs. No invented data.
5. Confirm history remains store-scoped in M1; workspace-specific history requires proven ownership, not an inferred filter or unapproved schema change.
6. Permit ordinary Eclipse metadata for non-persistent markers/navigation, or require annotation-only integration. Kriya/source/config/state remain write-free during inspection.
7. Confirm when matrix protection ends so isolated integration can proceed. Until then only fixtures/fakes.

If JSON inspection additions are declined, this proposal cannot deliver its required rich M1; the owner should reject or explicitly reduce scope at the gate, rather than silently ship the v1 terminal-output viewer.

**P-39 — INFERRED:** Next is Claude's `04_REVIEW_2.md`, restricted to R1 findings and v2 changes; new findings must satisfy the README's blocker/evidence rule. Every finding receives RESOLVED, ACCEPTED-AS-DEFERRED or UNRESOLVED. Sriram owns `05_GATE.md` and disputed-item decisions. No third review. After gate the topic closes; subsequent design changes start a new topic folder. Verify artifact sequence and local Git changes at handoff. Never push to GitHub.
