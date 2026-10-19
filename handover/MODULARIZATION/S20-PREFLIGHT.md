# S20 PHASE 1: measured preflight (TYPE D, raw credential retention in stored evidence; OI-134, OI-152)

**VERDICT.** Raw credential retention in stored run evidence is NOT allowed by the contract. OBS-003 (REQUIRED, "Secret
redaction in logs/traces/evidence") and KRP-025 say so, and no contract text grants an exemption beyond the two OI-146
approval surfaces. It is MEASURED on 14 persisted surfaces (table below).
- **Fixable now (S20 corrects them; no golden or stored digest moves, predicted).** These surfaces are not bound by any
  digest and have no consumer that needs the raw bytes:
  - I-1: the traces.db `runs` row;
  - I-3: the attempt-evidence record envelope (`payload`);
  - I-9: the launch record's free text (`approval_required`, `declined_decisions`, `ended.error`, a pending change
    review's `reason`);
  - I-11: the decision ledger;
  - I-12: the enforce planning diagnostics.
- **Redaction at write would move a golden or a stored digest, or would break a consumer that needs the exact bytes.**
  These go to the OWNER QUESTION at the top of the report, and S20 leaves them byte-identical: the fail-closed choice
  for evidence integrity, stated, not silent.
  - I-4: the attempt-evidence content blobs at the default capture `full` (content-addressed; `candidate_export`,
    `leak_check` and content_visibility need the exact bytes);
  - I-5: the verification-contract and authority-request stores (content-addressed by the contract digest);
  - I-6: checkpoints (resume rewrites `final_files` into the workspace);
  - I-7: the RunRecord execution plan (the plan fingerprint binds every unit goal);
  - I-8: the milestone run state;
  - I-13: the static-analysis raw scanner output (bound by the evidence digest).
- **Raw by design (OI-146, INTENTIONAL_CONTRACT):** I-10, the review-diff sidecar (the approval surface's own bound
  copy, re-verified by digest before it is served). Recommendation: OBS-003 does not govern it; it falls under the
  approval-surface retention rule (OI-104/S7).
- **Already redacted (MEASURED):** logs (the log formatter), policy telemetry summaries, every KUP/CLI/history projection
  (S2/S2b/S2c), doctor evidence.
- **A positional secret (OI-153, open owner question) is stored raw on every surface.** The redactor does not recognise
  it, and S20 builds no command-grammar redaction.
- **OI-152** (endpoint display brackets/port 0/None) is display-only. No golden carries an IPv6 endpoint (MEASURED:
  `grep -rl "::1\|\[::" tests/golden` is empty), so it is fixed at the redaction owner with the deliberate pin change
  OI-152 asks for.

**First wrong owner.** `kriya/core/attempt_evidence/writer.py:138-141`, the manifest header. It stamps
`redaction_policy_version: "1"` (`model.py:18`) on every store, yet the writer never calls any redactor, on records or
on blobs. TRACED: `grep redact kriya/core/attempt_evidence` finds nothing.
- The stamp has no consumer (MEASURED: `grep -rn redaction_policy_version kriya tests` finds only the definition and the
  stamp). So it asserts a policy nobody applies and nobody checks.
- The S2 owner docstring (`kriya/foundation/redaction.py:5-6`) then wrote the opposite rule into the redaction owner:
  "stored authoritative evidence keeps its raw bytes (digests bind exact bytes)". That holds only for the
  content-addressed stores (I-4..I-8, I-13). It does not hold for the unbound rows (I-1, I-3, I-9, I-11, I-12). S2b
  carried that docstring into a pin (`test_the_stored_run_row_stays_raw_after_a_history_detail_read`) that encodes the
  defect.

Base: `arch/full-modularization-v1` @ 43954e6, clean. Host: macOS Darwin 27.0.0, `.venv` Python. Scratch (every
reproducer and its raw JSON):
`/private/tmp/claude-501/-Users-sriramnanduri-WorkingDirectory-AI-ClaudeCode-Kriya-By-ClaudeCode/bc971a42-dc25-4273-ba41-f653fa8c96f5/scratchpad/s20/`
- `walk.py`: a byte-level grep of every file under the roots, covering sqlite main/-wal/-shm read as bytes (never
  through SELECT), gunzipped blobs, and the raw and percent-encoded forms. It then attributes each hit to a sqlite
  table.column or a JSON key path, and to a record kind and content name for blobs.
- `test_s20_repro_e2e.py` (part A): the REAL direct pipeline over a real LLMClient (`tests/_chaos_harness.py`, only the
  runtime port scripted). It runs at capture `full` and `digest_only`, with one marker per channel:
  - goal `api_key=S20GOALk1`;
  - candidate file `API_TOKEN = "S20CANDk2"`;
  - gate output: the failing candidate prints `password=S20GATEk3` (pytest output; the first attempt fails, the retry
    succeeds);
  - Planner `token: S20PLANk4`;
  - Reviewer `secret=S20REVk5`;
  - Architect `Authorization: Bearer S20ARCHk6`.

  The walk covers the state dir, the log dir, `<ws>/.kriya` and the qualification and certification homes.
- `test_s20_repro_ingress.py` (part B): the ingresses a direct run does not reach, driven through their REAL producers.
  - A desktop `LaunchSession`:
    - a held change review (reason `password=…k16`, diff `+API_KEY=…k17`);
    - a runtime review refused for `run.sh --token …k18` (the CLI callback `_runtime_command_decision`, with the reason
      `token: …k11`);
    - a positional `sshpass -p …k19`;
    - `decline_decision` (reason `secret=…k13`, details `api_key=…k14`);
    - `finish` with `error: Authorization: Bearer …k15`.
  - `AttemptEvidenceWriter.append("tool.execution")` (error `Authorization: Token …k20`, content `DB_PASSWORD=…k21`).
  - An unserializable record, which produces `recorder.gap` (k22).
  - `DecisionLedger.record_and_persist(note="token=…k23")`.

  Run with `PYTHONPATH=tests:$S20 .venv/bin/pytest -p conftest --rootdir=. -c pyproject.toml $S20/test_s20_repro_*.py`.
  Outputs go to `out/e2e_{full,digest_only}_before.json` and `out/ingress_before.json`.

## Ingress table (MEASURED against the unfixed bytes at 43954e6 unless marked TRACED)

The table has eight columns:
- **Ingress / where:** the producing file:line.
- **What is persisted:** with the planted markers found there.
- **Redactor today.**
- **Stamped version.**
- **Digest/binding consequence if redacted at write.**
- **Consumers needing raw.**
- **Reproducer.**
- **Verdict.**

| # | ingress / where | what is persisted (markers found) | redactor today | stamped version | digest / binding if redacted at write | consumers needing raw | reproducer | verdict |
|---|---|---|---|---|---|---|---|---|
| I-1 | traces.db `runs` row: `TraceLogger.log_run` `kriya/control/trace.py:109-160` (single writer; every caller through it: `workflow/run_lifecycle.py:196,227`, `workflow/run_trace.py:72`, `application/run.py:277`, `shadow_mode` via `run_trace`) | `goal` (k1), `prompt_rendered` (k1), `gate_outcomes[].output` (k3), `evidence_records[].payload.message/raw_output` (k3), `run_events[].message` (k3), `run_events[].details.requirements[].text` (k1); gate argv `--token x` (S2b MEASURED, OI-134); positional argv raw (OI-153) | none (display only: history, `kriya traces`) | none | NONE: no digest is computed over a stored column (TRACED: readers are `control/history`, `cli.py:4750`, `metrics/evidence.py:197`, `core/role_metrics.py:321`, `production_doctor.py:652`; none hashes or compares a stored text with a live one); the `history_detail` golden is ALREADY the redacted projection (idempotent); `checkpoint_and_trace_forms` golden has no secret-shaped input | none (all display/metrics; resume reads checkpoints, not traces.db) | part A (full + digest_only) | RAW_RETAINED_VIOLATION - fixable |
| I-2 | traces.db `milestone_plans`: `log_milestone_plan` trace.py:164 | reason codes + topology summary (TRACED: no free text) | none | none | none | none | TRACED | no secret-bearing field (redact for uniformity) |
| I-3 | attempt-evidence record envelope `records.jsonl` `payload`: `AttemptEvidenceWriter.append` writer.py:192-239 (every producer through `scope.emit`) | free-text payload fields: `tool.execution.error` (k20); `recorder.gap.error` = `str(error)[:500]` (MEASURED k22: the serialization error names the TYPE, not the value - no hit; any other exception text would be stored as written) | none (served redacted: `application/evidence.py:232`, S2c) | `redaction_policy_version "1"` stamped, NOT applied | chain `prev`/`head_digest` are computed over the stored line, so they stay self-consistent (re-hashed on read: consistent); `content_digests` and `blobs` are NOT touched (they bind content, not the envelope); `scope._observe` reads the caller's payload object, not the stored copy, so retry-delta digests are unchanged | none (TRACED: `decisions.py`, `explain.py`, `candidate_export.py`, `retention.py` read kinds, reason codes, paths, digests, decisions) | part B k20 | RAW_RETAINED_VIOLATION - fixable (TYPE D: the stamp's meaning changes, version "2") |
| I-4 | attempt-evidence content blobs (default capture `full`): writer.py:206-217 `_blob` (gzip, content-addressed `sha256(raw)`) from `scope.py` `content=` | goal (k1), candidate before/after/diff (k2, k3), gate output (k3), model messages/response/wire body (k1..k6, k5 only here), mirrored events/evidence/gate outcomes/decisions | none (content_visibility gates SERVING only, S2c) | "1" (not applied) | YES: `content_digests` and the blob ref are the digest of the exact bytes; `candidate.change` digests must equal the candidate the gates and approval saw (verification binding) | YES: `candidate_export.py` (exact rejected-candidate reconstruction for an external oracle), `leak_check.py` (redacting a hidden-oracle line could turn LEAKED into a false CLEAN: a safety regression), content_visibility on = "the recorded bytes" (OI-146 pin) | part A full; S2c `store_probe.py` | RAW_RETAINED_VIOLATION of OBS-003 text, NOT fixable at write without an owner decision -> OWNER QUESTION Q1 (digest_only: digests only, no bytes - MEASURED part A digest_only has no blob) |
| I-5 | `verification-contracts/<contract.digest>.json` `workflow/contract_compilation.py:848-859`; `authority-requests/<contract.digest>.json` `workflow/authority_request.py:169-178` | requirement text = goal text (k1) | none | none | YES: content-addressed by `contract.digest`, which binds the requirement text (requirement lineage, PRD-020) | YES: the contract digest is evidence identity | part A | violation of OBS-003 text; OWNER QUESTION Q1 |
| I-6 | checkpoints `<ws>/.kriya/checkpoints/<run>.json` `control/checkpoint.py:55-78` | plan (k4), design (k6), obligation ledger history (k1), `final_files` (candidate bytes k2, k3) | none | none | the resume fingerprints (goal_hash, config, registry) bind goal/config, and resume writes `final_files` back (`workflow/workflow.py:2593-2615, 2898`) | YES: resume | part A | OWNER QUESTION Q1 (resume needs exact bytes) |
| I-7 | RunRecord `<ws>/.kriya/control/runs/<run>.json` `control/persistence.py:279-307` | `execution_plan.work_units[].goal` (k1) | none | none | YES: `ExecutionPlan.fingerprint` covers every unit goal (`workflow/execution_plan.py:360-370`); resume convergence compares it | YES: resume/plan identity | part A | OWNER QUESTION Q1 |
| I-8 | milestone run state `workflow/milestones.py:398-409` | milestone goals / plan (TRACED) | none | none | YES (milestone proofs / plan) | YES: milestone resume | TRACED | OWNER QUESTION Q1 |
| I-9 | launch record `<ws>/.kriya/control/launches/<launch>.json`: `LaunchSession` `control/launch.py:120-147, 247-270`; pending change review `control/launch_approval.py:89-100`; one writer `persistence.save_launch_record:105` | `approval_required.reason` (k11) and `.details.commands[]` (k18: the `--token` argv refused for review); `declined_decisions[].reason` (k13) / `.details.terms[]` (k14); `ended.error` (k15); `pending_approval/approval_history[].reason` (k16); `pending_approval.commands` holds only argv the redactor leaves unchanged (`build_pending_runtime` refuses any other) - so only a positional form (k19, OI-153) | none (served redacted: `application/run.py:156`, `application/review.py:177`) | none | NONE for the free text: the review's `reason_sha256` and `review_id` bind the RAW reason and are computed before storage (unchanged); nothing re-hashes the stored `reason` (TRACED: `grep reason_sha256` = the two producers only); the decision binds `candidate_digest` + workspace identity, not the reason; the approval DIFF is not in this record (I-10) | none (display only) | part B | RAW_RETAINED_VIOLATION - fixable (k19 stays: OI-153) |
| I-10 | review-diff sidecar `<launch>.review-diff-<review>.json` `control/launch.py:202-216` -> `persistence.py:160` | the exact diff (k17) | none, by design | none | the approval binding itself: re-verified against the per-file `diff_sha256` + `candidate_digest` before serving (`application/review.py:144-166`); redacting = approving bytes nobody saw | YES: `change_approval.review` | part B | BY_DESIGN_RAW (OI-146); retention OI-104 (S7); OWNER QUESTION Q2 (does OBS-003 govern it; recommendation: no) |
| I-11 | decision ledger `<ws>/.kriya/control/decisions.jsonl` `control/decisions.py:107-142` (`append_to_file`) | every field as given (k23); the module docstring promises "never secrets" but only truncates | none | none | NONE (no digest over a decision line; ownership stamp is the workspace id) | none in kriya (`load_decision_ledger` has no production caller; TRACED) | part B | RAW_RETAINED_VIOLATION - fixable at the persistence ingress (the in-memory ledger and its mirror blob stay as recorded: I-4) |
| I-12 | enforce planning diagnostics `workflow/planning_diagnostics.py:136-178` | planner request / system prompt / raw plan response / repair prompt (model + goal text) | none (bounded only) | none | NONE (a diagnostic jsonl, no digest) | none (no reader in kriya; TRACED) | TRACED (enforce path) | RAW_RETAINED_VIOLATION - fixable |
| I-13 | static-analysis evidence `static_analysis/service.py:369-392` (`<scan>.raw.json`, `evidence.json`) | scanner raw output (a secret-detector finding quotes the matched code; TRACED) | none | none | YES: "the evidence digest and the result stay authoritative" (the raw scan is the digest's input) | YES: evidence digest | TRACED | OWNER QUESTION Q1 |
| I-14 | logs (console/file) `core/logging_setup.py:55` | - (no marker in the log dir, part A) | `redact_text` at format | - | - | - | part A + S2 MEASURED | REDACTED |
| I-15 | policy telemetry summary `policy/telemetry.py:75` | - | `redact_text` before the cut | - | - | - | S2 | REDACTED |
| I-16 | KUP/history/CLI projections, doctor evidence (`application/history.py`, `application/run.py:156`, `application/evidence.py:232`, `application/review.py:177`, `production_doctor.py:71`, `cli.py:4789`) | display, not storage | `redact_value`/`redact_text` | - | - | - | S2/S2b/S2c | REDACTED (projection) |
| I-17 | exports: `kriya history acquire` (`control/history/acquire.py`, a copy of traces.db), `candidate_export` (operator-chosen directory) | whatever the source holds | none | - | acquire: copies rows (after I-1 the new rows are redacted at source; existing stores are not migrated); candidate export: exact by purpose | candidate export: yes (I-4) | TRACED | follows its source (I-1 / I-4) |
| I-18 | approval records: SEC-009 config approval (`config/authority_approval.py`), MCP invocation approval (`mcp/invocation_approval.py:246-256`), launch decision document (`persistence.py:118`) | salted digests / identities / decision only (S2c MEASURED: no diff byte; SEC-009: no plaintext secret) | by design | - | - | - | S2c + TRACED | REDACTED / identity-only by design |
| I-19 | repository-content stores (vector index, code_intel, analyzer graph, knowledge staging, skills, memory) | the user's own repository and knowledge, not run evidence | - | - | - | - | TRACED | out of OBS-003 run-evidence scope (classified, not changed) |

The four S2c-traced items:
- **(a) Launch `approval_required`/`declined_decisions` raw.** CONFIRMED by part B (I-9, markers k11, k13, k14, k18).
- **(b) Default capture `full` blobs raw.** CONFIRMED by part A (I-4: every channel marker is in gzip blobs).
- **(c) Review-diff sidecar raw by design.** CONFIRMED (I-10, k17). The decision is Q2.
- **(d) Positional-secret argv stored raw.** CONFIRMED in the launch record `pending_approval.commands` (k19). Through the
  same path, a positional argv also reaches `runs.gate_outcomes` and the evidence blobs (TRACED: the redactor leaves it
  unchanged, so redaction at write cannot remove it). No grammar-aware redaction is built (OI-153).

File modes (MEASURED, part A/B, process umask 022): attempt-evidence 0600 files in 0700 directories (writer.py:43-44).
traces.db, the verification contracts, checkpoints, RunRecords, launch records and the decision ledger are 0644 in 0755
directories, so they are readable by other local users on a shared host. This is relevant to Q1 option (b).

## How `redaction_policy_version "1"` is stamped

`writer.py:138-143` builds the manifest header from constants: `schema`, `writer_version`,
`redaction_policy_version: model.REDACTION_POLICY_VERSION` ("1"), `capture`, `run_id`, plus the caller's manifest. It
is a constant, not a record of anything applied. No line in `kriya/core/attempt_evidence/` calls a redactor (TRACED),
and no reader branches on it (MEASURED grep).

What each stored digest binds:
- `content_digests[name]` and `blobs[name]`: the exact content bytes (`model.as_bytes`), re-hashed on read
  (`reader.BlobCorrupt`).
- `prev` / seal `head_digest`: the canonical bytes of the previous record line, as stored.
- The manifest is not digested.

So the envelope (I-3) can be redacted before `canonical_bytes` without breaking any binding. The content (I-4) cannot be
redacted without changing what `content_digests` means.

## OWNER QUESTIONS (S20 cannot answer them from the existing authority model)

**Q1. Content-addressed and resume-bound stores** (I-4 blobs at capture `full`, I-5, I-6, I-7, I-8, I-13). OBS-003
requires redaction in evidence. These stores bind the exact bytes (a content digest, a contract or plan fingerprint), or
a consumer needs them exactly (resume, candidate export, the hidden-oracle leak check). Redacting them at write changes
stored digests and evidence identity, which is a STOP for S20.

The options:
- **(a) Redaction at write.** Every digest is re-based on the redacted bytes. This breaks resume (`final_files`),
  candidate export exactness and the verification binding, and the leak check could miss a leaked hidden line.
- **(b) Retention plus access control.** Keep the bytes. Make every such store owner-only (0600/0700 like the
  attempt-evidence store; today they are 0644/0755). Serve them only through the redacting projections or the
  operator-opt-in content_visibility. Bound their lifetime by the retention policy. Amend OBS-003 to say: redacted on
  every log, trace-row, display and export channel; raw only in owner-only, digest-bound stores needed for resume,
  replay and verification.
- **(c) Encrypt-at-rest for the raw stores** (new key management: out of scope).
- **(d) Make `digest_only` the default capture** for the attempt-evidence blobs. This loses candidate export and the leak
  check at the default.

Recommendation: **(b)**, plus a follow-up unit that sets 0600/0700 on the five 0644 stores through their single writers
(no digest moves; Type C).

Fail-closed choice made here: these stores are left BYTE-IDENTICAL. Integrity and identity are not traded for a partial
redaction, and the violation is stated, not hidden.

**Q2. The review-diff sidecar** (I-10): does OBS-003 govern it? Recommendation: no. It is the OI-146 approval surface's
own bound copy, raw by design and re-verified by digest; its lifetime belongs to OI-104 (S7).

## Predicted effect of the S20 corrections (to be re-measured with the same reproducers)

- **I-1:** part A shows no k1 or k3 in `traces.db` (main/-wal); the other stores are unchanged; no golden moves
  (`history_detail.json` is already the redacted projection; `checkpoint_and_trace_forms.json` has no secret-shaped
  input).
- **I-3:** part B shows no k20 in `records.jsonl`; k21 stays in its blob (I-4); `content_digests` are unchanged; the
  equivalence test (I-2) stays green.
- **I-9:** part B shows no k11, k13, k14, k15, k16 or k18 in the launch record; k17 stays in the sidecar (by design);
  k19 stays (OI-153); `policy_approval/serialized_forms.json` is unchanged (no secret-shaped reason).
- **I-11:** no k23 in `decisions.jsonl`.
- **I-12:** a planted secret in the planner response is absent from the diagnostics file.
- **Pins that encode the defect (to flip, listed in the report):**
  - `tests/test_s2b_redaction_completeness.py::test_the_stored_run_row_stays_raw_after_a_history_detail_read`;
  - `tests/test_s2c_redaction_residuals.py::test_the_launch_status_the_gui_receives_hides_a_recorded_secret` (the
    `stored raw (S20 owns that)` line).

## Stale contract text found (READ-ONLY for S20, reported)

- `kriya/foundation/redaction.py:5-6` (owner docstring): "stored authoritative evidence keeps its raw bytes (digests bind
  exact bytes)". It is too broad: it is true only for I-4..I-8 and I-13. Corrected in S20, since the owner module's own
  docstring is S20's.
- `Kriya-By-ClaudeCode/handover/claude-context/config-resolution-and-authority.md:18` ("Stored authoritative evidence
  stays RAW (digests bind exact bytes); redaction happens at the projection") contradicts its own next sentence ("That
  exemption does not extend to durable evidence, history, telemetry, logs or exports (S20 owns persisted-evidence
  redaction)").
- `verification-platform-and-assurance.md` §"Attempt evidence recorder" never states the redaction policy or what
  `redaction_policy_version` means.
- `storage-cli-repl-routing.md` (traces.db) says nothing on redaction at write.
- `kriya/control/decisions.py` module docstring: "never secrets or full payloads" with truncation only.
- OBS-003 and KRP-025 are NOT rows of `handover/BACKLOG_REGISTRY.csv` (MEASURED grep). They live in
  `docs/assurance/KRIYA_PRODUCTION_RISK_REGISTER.md:1309` and `docs/assurance/KRIYA_KRP_RECONCILIATION.md:39`.
