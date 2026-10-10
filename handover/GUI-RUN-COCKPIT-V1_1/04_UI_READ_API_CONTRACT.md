# 04 — UI read API contract (KUP v1, capability set 1.1: composed read façade over S1, S2, S3)

**Status:** design only; nothing here is implemented. Implementation is Phase 1A/1B in `05_IMPLEMENTATION_PLAN.md`
and requires the owner's authorization to add read-only modules to the frozen product (no workflow change).
**Inherits unchanged:** every KUP v1 rule in `handover/GUI-DESIGN-DISCUSSION/05_GATE.md` (D-3 byte-identical CLI
without KUP flags, D-4 typed refusal instead of unsafe reads, D-5 no invented data, D-6 history tied to the store,
D-7 `CONFIG_AUTHORITY_REFUSED`) and `handover/GUI-D4-READ-STRATEGY/03_GATE.md` (snapshot copy, verify at pin).
**Evidence basis:** `03_EVIDENCE_AVAILABILITY_MATRIX.md`; code paths named below were traced at 1a89bc5.

---

## 1. Architecture (binding)

```
Renderer (ui/shared, typed UI contracts)
   │  typed IPC, allowlisted messages (preload)
Electron main (ui/standalone)  = typed proxy + cache + argv builder + child spawner. Never opens a store file.
   │  one child process per request, argv array, fixed env, cwd = configuration directory (M1)
kriya CLI  → kriya/kup/cli_ops.run_kup_operation → kriya/kup/{inspect,evidence_ops,observe_ops}.py
   │                 │                                     │
   S1 traces.snapshot.db (read-only URI)   S2 attempt_evidence/reader.py   S3 recovery / qualification / authority
```

Rules. (1) **Kriya is the only reader** of S1, S2 and S3; Electron main and the renderer never parse SQLite, JSONL,
gzip or evidence directories (M1 P-3/P-31 extended to S2). (2) **Compose, never normalize**: a composed response is a
map of per-source envelopes; records are never merged across sources. (3) **One operation, one envelope**: every
response is the KUP v1 envelope (`schema_version`, `operation`, `request_id`, `observed_at`, `source`, `consistency`,
`data`, `error`) and `encode_response` applies the 8 MiB `RESPONSE_TOO_LARGE` rule to every operation including blobs.
(4) **No new write path**: `evidence.*` and `observe.*` operations open files read-only and create nothing; the only
writes in KUP remain snapshot acquire/prune (unchanged). (5) **Strict read of S2** (§5) mirrors D-4: when a strictly
write-free, consistent read is impossible the operation returns a typed error, never a partial or reconstructed body.

## 2. Versioning

`KUP_SCHEMA_VERSION` **stays the integer 1** (`policy.py`, the JSON Schema `const 1`, `--kup-version` is `type=int`,
the M1 renderer refuses any other value, `tests/test_kup_cli.py` pins `[1]`). The extension is additive inside v1:
new operations, feature flags, error codes and one consistency kind are discoverable through `capabilities`, and a host
that only knows the M1 set ignores them. The package is named "capability set 1.1" (`capabilities.features.capability_set:
"1.1"`); every argv below uses `--kup-version 1`. Moving to integer 2 with hosts accepting `[1, 2]` is the fallback if a
non-additive change ever becomes necessary. New JSON Schemas:
`ui/kup/schema/evidence.schema.json`, `observe.schema.json`; `capabilities.schema.json` and `common.schema.json`
extended. Generated TypeScript and Java types regenerate; the Java round-trip check covers the new golden fixtures.

## 3. Capabilities (extension of `inspect.capabilities()`)

```
features: {
  snapshot: true, prompt: true,                       # unchanged
  comparisons: false, attribution: false, diagnostics: false, output: false,   # unchanged S1 statements
  evidence: true,              # evidence.* operations exist in this build
  evidence_content: true,      # evidence.blob may return bytes (false when the host policy forbids content)
  qualification: true,         # observe.qualification exists
  authority: true,             # observe.authority exists
  knowledge: false, tools: false, experiments: false, live: false, actions: false,
  consistency_kinds: [snapshot_copy, live_observation, not_applicable, sealed_store],
  error_codes: [... v1 ..., EVIDENCE_* and OBSERVE_* below],
  evidence_capture_modes: [full, digest_only, full_with_reasoning, off],
  evidence_verification_states: [VERIFIED, UNSEALED, NOT_FOUND, CHAIN_BROKEN, TRUNCATED_TAIL, BLOB_CORRUPT, SEAL_MISMATCH],
  evidence_kinds: <model.KINDS as a list>,
  evidence_provenances: [OBSERVED, DERIVED, MODEL_CLAIMED, MIRRORED, NOT_RECORDED, LEGACY_RECONSTRUCTED],
  diagnosis_evidence_classes: [MEASURED, DERIVED_DETERMINISTIC, MODEL_CLAIMED, UNKNOWN]
}
limits: { ...v1, evidence_page_default: 200, evidence_page_max: 1000, evidence_blob_max_bytes: 1048576,
          evidence_blob_range_max_bytes: 262144, evidence_verify_max_bytes: <reader bound> }
```

The UI hides every surface whose flag is false (02 §4.3). **`evidence_content` is owned by Kriya, not the host:** a
new config field `evidence.attempt_recorder.content_visibility: off | on` (default `off`), classified SECURITY_AUTHORITY
in `kriya/config/authority.py` (unlisted fields fail closed; a repository config can never widen it, exactly like
`capture`). `capabilities.features.evidence_content` reports that field. The host setting `evidenceContentVisible` can
only **narrow** (the host may decline to request content; it cannot request it when Kriya says off). There is no
argv flag that enables content; `evidence blob` returns model-facing classes only when the Kriya field is `on`.
Owner decision Q-2 in `01 §6` fixes the default.

## 4. Operations

Transport: argv arrays only, under the existing `evidence` and `traces` groups, with `--json --kup-version 1`
(the pattern `runs status --json --kup-version 1` already uses). Illustrative argv per operation is given; exact flags
are fixed in Phase 1A tests. Every operation takes `run_id` validated by `RUN_ID_RE`; `--state-dir` is never a
parameter (the store lives under the configured state directory, SECURITY_AUTHORITY).

### 4.1 `evidence.manifest` — store metadata (M-rows 25, 27, 28)

The `absence` block is DERIVED (matrix row 27): Kriya reads the S1 `evidence.attempt_store` event for the run from the
snapshot the host names (`--snapshot-id`), and reports which case applies; without a snapshot id the reason is `unknown`.

`kriya evidence manifest <run_id> --json --kup-version 1`

```
data: { run_id, store: "present" | "absent",
        manifest: <verbatim as written: schema, writer_version, redaction_policy_version, capture, run_id, + run manifest keys> | null,
        seal: <verbatim as written: schema, final_seq, head_digest, record_count, blob_count, complete, gaps (int), degraded[], closed_reason> | null,
        line_count (a cheap count, not a verify), bytes_on_disk, blob_file_count,
        absence: null | { reason: "pruned" | "capture_off" | "recorder_unavailable" | "pre_recorder" | "unknown",
                          derived_from: "history.detail run_events[evidence.attempt_store]" , detail } }
consistency.kind: live_observation (a manifest read establishes no trust state; see §5 for `sealed_store`)
```

### 4.2 `evidence.verify` — chain and blob verification (M-row 26)

`kriya evidence verify <run_id> --json --kup-version 1` (existing command gains the KUP envelope)

```
data: { run_id, status: VERIFIED|UNSEALED|NOT_FOUND|CHAIN_BROKEN|TRUNCATED_TAIL|BLOB_CORRUPT|SEAL_MISMATCH,
        record_count, sealed, broken_seq, detail, seal, blob_problems[], head_digest, verified_at }
```
Cost note: full verify reads every record and blob; the host calls it once per (run, head_digest) and caches by
head_digest only for VERIFIED. The UI pins S2 views to the verified head_digest exactly as it pins S1 to the snapshot
id (C-2 analogue): a later manifest whose head_digest differs invalidates the cache.

### 4.3 `evidence.records` — paged, filtered records (M-rows 10, 12, 15, 18, 30, 37, 38, 40, 44, 50, 56–59)

`kriya evidence records <run_id> --json --kup-version 1 [--unit U] [--phase P] [--attempt N] [--role R] [--kind K ...] [--from-seq S] [-n LIMIT] [--cursor C]`

```
data: { run_id, head_digest, records: [ envelope-as-stored minus nothing: schema, seq, prev, kind, run_id, unit_id,
        unit_kind, invocation_seq, phase, attempt_number, call_seq, wire_seq, role, t_wall, t_mono_ms, provenance,
        payload, content_digests, blobs ], next_cursor, filters_applied, truncated: bool }
```
Rules: records are returned verbatim (payload untouched; unknown fields preserved); `blobs` are references only
(`sha256:…`), never inlined; the cursor encodes (run_id, head_digest, seq) and is refused with `EVIDENCE_HEAD_CHANGED`
if the head moved (unsealed store grew); ordering is `seq` ascending only; filters are conjunctive; `--kind` accepts
only values in `model.KINDS` (`INVALID_REQUEST` otherwise). `phase` filter matches the stored value verbatim. The reader
adds `phase_provenance` per record, computed by the reader (never stored): `explicit` when the record's `seq` lies
inside a `phase.opened` … `phase.closed` range of the same chain (none exist today; §7), else
`derived_from_attempt_scope` when the record carries an `attempt_number`, else `derived_from_role` when the `role` is in
the reader's copy of `_ROLE_PHASES`, else `role_name_fallback`. The UI badges everything but `explicit` as DERIVED.

### 4.4 `evidence.blob` — capped, lazy content (M-rows 31, 41, 45)

`kriya evidence blob <run_id> <sha256-ref> --json --kup-version 1 [--offset O] [--length L]`

```
data: { run_id, ref, bytes_total, encoding: "utf-8" | "base64", content_class, offset, length, content, digest_ok: true }
```
Rules: the ref must match `^sha256:[0-9a-f]{64}$` (`INVALID_REQUEST` before any lookup); refuses when the ref is not
referenced by any record of this run (`EVIDENCE_BLOB_NOT_REFERENCED`); whole blob only up to `evidence_blob_max_bytes`
(1 MiB), otherwise `EVIDENCE_BLOB_TOO_LARGE` with `bytes_total` and the UI uses ranges (≤ 256 KiB each); the digest is
recomputed over the full blob before any bytes are returned (`EVIDENCE_VERIFY_FAILED` with `status: BLOB_CORRUPT`
otherwise). Content is never logged, never written anywhere, never included in any other operation.

`content_class` is derived from the blob key in the referencing record. The class table must cover **every key the
writers emit** (tripwire test: the set of blob keys found in production `content={...}` calls and dynamic `pytest_<name>`
/ mirror keys ⊆ the table), and an unlisted key fails closed:

| Blob key(s) (writer) | Class | Gated by `evidence_content` |
|---|---|---|
| `messages`, `tools`, `wire_body` (model.request/response), `analysis_model_claimed` (model.result) | `model_facing` | yes |
| `proposed`, `attempted_edits` (developer.parse / tool.execution), `before`, `after`, `diff` (candidate.change) | `candidate_content` | yes |
| `goal` (run.opened via run_trace) | `goal` | yes |
| `output`, `tool_output`, `test_execution`, `pytest_<name>` raw streams (gate.result, tool.execution) | `gate_output` | no (needed for Validation) |
| `environment_failure` (recovery.decision) | `gate_output` | no |
| `baseline_cases`, `post_cases` (regression.decision) | `gate_output` | no |
| `event`, `decision`, `gate_outcome`, `gate_outcomes_restored` (mirror.*) | `mirror` | no |
| `evidence` (mirror.evidence, an `EvidenceRecord` with `sensitivity`) | `mirror_evidence` | yes unless `sensitivity == "public"` |
| any other key | `unclassified` | **always refused** (`EVIDENCE_CONTENT_POLICY`, `content_class: "unclassified"`) |

### 4.5 `evidence.candidate` — per-attempt candidate set (M-rows 40, 41)

`kriya evidence candidate <run_id> --json --kup-version 1 [--attempt N]`

A projection of `candidate.change` records (the existing `candidate_export.staged_changes` logic, without writing):
```
data: { run_id, attempt, mode: "content" | "digest_only" | "none", files: [ { path, kind, proposal_kind, decision,
        created, deleted, before_digest, after_digest, blobs: { before?, after?, diff? }, seq } ],
        mode_reason: "capture full" | "capture digest_only" | "no staged candidate in this run" }
```
`mode` drives Changes Mode A/B directly; the UI never infers it from blob presence alone.

### 4.6 `evidence.explain` — the nine questions (M-row 60)

`kriya evidence explain <run_id> --json --kup-version 1` (existing command gains the envelope)
```
data: { run_id, answers: { <question_id>: { status: RECORDED|NOT_APPLICABLE|NOT_RECORDED, reason, items: [ { seq, kind, payload-subset, blobs } ] } } }
```

### 4.7 `evidence.compare` — retry delta between two attempts (M-row 58)

`kriya evidence compare <run_id> --json --kup-version 1 --left A --right B`
Returns the `retry.delta` records whose attempt_number is B (what changed from A to B as the recorder wrote it) plus
both attempts' `attempt.opened/closed/concluded`, `gate.result` and `candidate.change` summaries, each under its own
source key. No diffing is computed by Kriya beyond what `retry.delta` recorded; the UI compares the two lists.

### 4.8 `observe.qualification` — model qualification state (M-row 19)

`kriya model qualification-status --json --kup-version 1 --runtime-digest D --settings-digest S` (one call per role)
```
data: { runtime_digest, settings_digest, record: { status: QUALIFIED|STALE|…, policy_version, recorded_at, … } | null,
        reason: "no record for this identity" | null }
consistency.kind: live_observation
```
Inputs come from the run's `model.role_metrics` event (M-row 17); the UI never guesses a digest. Implementation note:
`model_qualification.load_record(runtime_digest, settings)` takes an `InferenceSettings` object and the record key is
sha256({runtime, inference_settings digest}); the operation needs a small digest-keyed lookup helper beside it, not a
new key. This is the **current** state; the qualification **recorded at run start** is the S1 `model.runtime` event
(M-row 19a) and the two are shown as different facts (02 §7).

### 4.9 `observe.authority` — SEC-009 approval state (M-row 21)

`kriya authority inspect --json --kup-version 1` (existing command gains the envelope)
```
data: { configuration_directory, artifact_present, integrity_ok, set_digest, approved_at, field_records_count,
        current_vs_approved: "current" | "drifted" | "unapproved" }   consistency.kind: live_observation
```
Redaction rules of `authority_approval._redact_env_values` apply; no field values are returned.

### 4.10 `cockpit.run` — composed view (convenience, not a new source)

`kriya traces --json --kup-version 1 --snapshot-id ID --run-id R --cockpit [--evidence-verify]`
```
data: { history: <history.detail envelope>, evidence_manifest: <4.1 envelope>, evidence_verify: <4.2 envelope> | null,
        qualification: { <role>: <4.8 envelope> }, authority: <4.9 envelope> | null }
```
Each member is a complete envelope with its own `source`, `consistency` and `error`; a failing member is an error
envelope inside the map, never a missing key and never a merged partial. `evidence_verify` is included only when the
host passes `--evidence-verify` (cost; `traces --verify` already means snapshot digest verification and is untouched). This is the only composition Kriya performs.

## 5. Strict read rules for S2 (D-4 analogue)

0. `consistency.kind = sealed_store` is returned only by an operation that, in the same invocation, verified the seal
   and the chain for the `head_digest` it reports (records, blob, candidate, compare after their rule-6 check), or that
   names the `evidence.verify` response (by head_digest) it relies on. `evidence.manifest` and `evidence.explain` are
   always `live_observation`.
1. Open the record file read-only; read whole lines only. On an **unsealed** store a trailing partial line (writer
   mid-append) is not a record and makes the result `live_observation` with `truncated_tail: true`; never wait for the
   writer. On a **sealed** store a partial tail is corruption (`TRUNCATED_TAIL`, rule 6), never served as live.
2. Never take or probe the writer's lock; never create, rename or touch any file under the store root.
3. Directories whose name starts with `.` (retention's rename-before-delete) are invisible: `NOT_FOUND`.
4. A record whose `schema` is not the reader's supported schema → `EVIDENCE_SCHEMA_UNSUPPORTED` (reader's
   `UnsupportedEvidenceSchema`); nothing is reinterpreted.
5. `UNSEALED` stores are readable (the run may be active or crashed) but are never cached, always carry
   `consistency.kind = live_observation`, and every UI element shows the Unsealed badge.
6. `CHAIN_BROKEN`, `SEAL_MISMATCH`, `BLOB_CORRUPT`, and `TRUNCATED_TAIL` on a sealed store: `evidence.records`,
   `evidence.blob`, `evidence.candidate` and `evidence.compare` refuse with the verification status in the error; only `manifest`, `verify` and `explain`
   (which reports what it could read, marked) are allowed.
7. Blob bytes are compared to their digest before being returned, every time.
8. Retention and capture mode are reported, never changed; no operation accepts a store path.

## 6. Typed errors (closed vocabulary, added to `policy.ERROR_CODES`)

| Code | Meaning | `database_state`-style extra |
|---|---|---|
| EVIDENCE_STORE_NOT_FOUND | no store directory for the run | `absence_reason` |
| EVIDENCE_STORE_UNREADABLE | permission or I/O failure on the store | `path_kind` |
| EVIDENCE_SCHEMA_UNSUPPORTED | record schema newer/older than the reader | `schema_seen` |
| EVIDENCE_VERIFY_FAILED | status ≠ VERIFIED for an operation that requires it, incl. a blob whose recomputed digest mismatches | `status` (one of the reader statuses), `broken_seq`, `ref` |
| EVIDENCE_HEAD_CHANGED | cursor's head_digest no longer matches | `head_then`, `head_now` |
| EVIDENCE_BLOB_NOT_REFERENCED | ref not used by any record of the run | `ref` |
| EVIDENCE_BLOB_TOO_LARGE | whole-blob request above the cap | `bytes_total` |
| EVIDENCE_CONTENT_POLICY | model-facing content refused by visibility policy | `content_class` |
| OBSERVE_QUALIFICATION_UNAVAILABLE | qualification home unreadable | — |
| OBSERVE_AUTHORITY_UNAVAILABLE | configuration directory or artifact unreadable | — |
| INVALID_REQUEST, RESPONSE_TOO_LARGE, CONFIG_AUTHORITY_REFUSED, CONFIG_LOAD_FAILED | unchanged v1 semantics | — |

## 7. Future phase observability (design only; not a prerequisite)

Records `phase.opened` / `phase.closed` already exist in `model.KINDS` with zero producers. Proposed contract:

```
payload: { phase_id: "knowledge_guard" | "planning" | "architecture" | "development" | "review" | "commit"
                     | "milestone_planning" | "localization" | "requirement_verification",
           display_label (from one table in kriya), ordinal, outcome (closed only): "completed" | "stopped" | "failed",
           reason_code (closed only, optional) }
envelope.phase = phase_id (explicit, provenance OBSERVED) — replaces the role-derived value while a phase is open
```
Producer points (one `with phase_scope(id)` each, in `kriya/workflow/workflow.py` around the existing banners):
KnowledgeGuard stage 0 check; `_log_phase_banner("PLANNING")`; `("ARCHITECTURE")`; `("DEVELOPMENT & QUALITY GATES")`;
`("REVIEW")`; the commit path in the terminal gate / workspace commit service. Schema: no envelope field changes, so
`model.SCHEMA` need not bump; `WRITER_VERSION` bumps. Until implemented, the UI shows DERIVED groups only
(02 §8) and never a boundary. This is Phase 6 in `05` and needs the owner to unfreeze `workflow.py`.

## 8. Host-side behaviour (Electron main)

- Argv builder gains one entry per operation; the allowlist test (`kriya_argv.test.ts`) enumerates them.
- Cache key: `(operation, canonical params, snapshot_id | head_digest)`; only `sealed_store` and `snapshot_copy`
  responses are cached; `live_observation` never; blobs never persisted to disk; cache cleared on settings change.
- Request budget: the host issues at most one `evidence.verify` per run per head_digest and never calls `evidence.blob`
  without a user click.
- Settings gain `evidenceContentVisible` (default false). It only narrows: when false the host never requests
  model-facing or candidate content; when true the host requests it and Kriya still refuses unless its own
  `content_visibility` field is `on`. No argv flag carries the policy.
- `evidence prune` (exists in the CLI) is **excluded** from the host argv allowlist; the allowlist test enumerates the
  read operations only.

## 9. Tests the contract requires (Phase 1A/1B acceptance)

Kriya side: per operation the envelope schema; strict doubles for config; `NOT_FOUND` for absent and dot-prefixed
stores; `UNSEALED` on a store with no seal and on a trailing partial line; `CHAIN_BROKEN` / `SEAL_MISMATCH` /
`BLOB_CORRUPT` refusals for records/blob/candidate/compare; cursor `EVIDENCE_HEAD_CHANGED`; blob cap and range; digest
mismatch; `EVIDENCE_CONTENT_POLICY` for each model-facing class with the policy off; `RESPONSE_TOO_LARGE` on a large
page; `evidence_content` capability false ⇔ policy off (mutation: flipping the policy must flip both); `kriya evidence verify --json`
without `--kup-version` stays byte-identical to today (D-3: no KUP flag given; the existing D-3 golden test is
`tests/test_kup_cli.py::test_text_mode_is_byte_identical_to_the_baseline_golden`); the closed-vocabulary tripwires:
`tests/test_kup_cli.py` requires every `policy.ERROR_CODES` entry to be produced in-test and `set(operations) ==
set(policy.OPERATIONS)`, and `ui/kup/test/contract.test.ts` requires an `errors/<code>.json` fixture per code, so each
new code and kind is added in lock-step at three hand-maintained sites (`kriya/kup/policy.py`, `ui/kup/src/index.ts`,
`ui/kup/schema/common.schema.json`) plus a new Python↔schema parity test; the network inventory and platform guard tests see no
new client or subprocess; `kriya/workflow` imports nothing from `kriya/kup`.
UI side: schema generation drift check; Java round trip over new golden fixtures (a sealed store produced by the test
suite's recorder, not a live run); availability rules for every flag combination in the test host; the evidence
checker gains rules for S2 envelopes (EVC-EVD-*).

## 9. As implemented (Phase 1A, slices 1A-1 and 1A-2; code is ground truth)

Additions and clarifications proven by the code and its tests; none alters a frozen owner decision.

1. **Source block.** Evidence operations return `source.state_directory`, `source.trace_database` (kept so the M1 `Source` schema is unchanged) and `source.evidence_directory`; with a snapshot, `snapshot_directory` and `snapshot_id` as in M1.
2. **`sealed_store` is returned only for `VERIFIED`.** `evidence.verify` and `evidence.records` verify the chain, every referenced blob and the seal in the same invocation, and the consistency block carries `head_digest`. Every other status, including `UNSEALED` and an unsealed `TRUNCATED_TAIL`, is `live_observation`. `evidence.records` therefore re-verifies on every page (one pass that also collects the records); the host caches pages by `(operation, params, head_digest)` for sealed stores only.
3. **`evidence.verify`** reports every reader status as data (`NOT_FOUND` included, no error); `head_digest` is the digest of the last complete record verified (null for `NOT_FOUND` and `CHAIN_BROKEN`).
4. **`evidence.manifest` absence block** also carries `snapshot_id` and `recorded_event` (the `evidence.attempt_store` details verbatim, or null). Only `OPEN`/`CLOSED` events that name a `path` derive `pruned`; any other status is `unknown` and says which. A trace row missing from the snapshot is `unknown`, never `pre_recorder`.
5. **`evidence.records`.** `truncated` means the page stopped at the response byte budget (`limit.evidence_page_max_bytes`, half of the 8 MiB response cap) before `limit`; `truncated_tail` is the separate partial-tail flag of an unsealed store. The first matching record is always served. The cursor is bound to run, filters and `head_digest` (changing the first two is `INVALID_REQUEST`, a moved head is `EVIDENCE_HEAD_CHANGED`). `filters_applied` lists only the filters that were supplied. `phase_provenance` is `null` when no phase was recorded, and `role_name_fallback` only for an unmapped raw role name; the reader's `ROLE_PHASES` is a tested copy of the recorder's map.
6. **Errors added so far:** `EVIDENCE_STORE_UNREADABLE`, `EVIDENCE_SCHEMA_UNSUPPORTED`, `EVIDENCE_STORE_NOT_FOUND` (records only), `EVIDENCE_VERIFY_FAILED` (status, broken_seq, detail, blob_problems), `EVIDENCE_HEAD_CHANGED`. The remaining 04 §6 codes arrive with their operations.
7. **Capabilities advertise only what exists:** `evidence: true`; `evidence_content`, `qualification`, `authority`, `knowledge`, `tools`, `experiments`, `live`, `actions` are `false`. `limits` gains `evidence_page_default`, `evidence_page_max`, `evidence_page_max_bytes`; `evidence_blob_*` arrive with 1A-3 and `evidence_verify_max_bytes` is not advertised because the reader has no such bound.
8. **Reader.** `Verification.head_digest`, `EvidenceRun.measure()`, `EvidenceRun.verify(records_out)`, `UnsupportedEvidenceSchema.schema_seen`, and a non-object record, manifest or seal is corruption (`CHAIN_BROKEN` / `ValueError`), never an `AttributeError` (commit 14f2438).
