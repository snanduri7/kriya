# GUI configuration approval bridge (Demo V2)

Branch `demo/v2`, starting HEAD `0a86fd8`. Scope: the GUI becomes one more **client** of the existing SEC-009 configuration-authority subsystem. It adds no authority, no new approval record format, and no new trust model. All claims below are labelled **TRACED** (read in code) or **MEASURED** (observed by running something).

## 1. The existing path, traced

| Question | Answer (TRACED) |
|---|---|
| CLI entry | `kriya authority inspect | approve | revoke` — `kriya/cli.py` `authority_group` (`main()` skips `load_config()` for the `authority` group, so it is reachable while the config is denied). |
| What it calls | `resolve_config_state()` (`kriya/config/config.py`) → `describe_pending()`; `approve` then `build_approval_artifact()` + `save_approval_artifact()` (`kriya/config/authority_approval.py`). |
| How protected fields are discovered | `resolve_config_state()` merges default + discovered `kriya.yaml` (**cwd decides discovery and `workspace_root = realpath(cwd)`**), records per-field provenance, and `compute_violations()` (`kriya/config/authority.py`) returns every repository-equivalent, non-`REPOSITORY_SAFE` (or unclassified) field. Deny by default. |
| Approval subject | The **complete sorted set** of violation records `{field_path, classification, source, value_digest, env_keys}` (Option A: all-or-nothing, not per-field grants). |
| Digest | `value_digest = sha256(salt ‖ field ‖ canonical_json(value))`, `mcp.*.env` values pre-digested per key with the salt; `set_digest = sha256(canonical_json(records))`; `artifact_digest` over the whole artifact. **The salt is a fresh `secrets.token_hex(16)` per artifact**, so `set_digest` differs on every approval of identical config. |
| Persistence | `~/.kriya/authority/<workspace_identity>.json` (override `KRIYA_AUTHORITY_HOME`), atomic tmp+`os.replace`, **outside the workspace** (a path resolving inside is refused: `TrustPathInsideWorkspaceError`). |
| Scope | Per workspace identity (realpath-based). Not per user, not per repo URL, not a blanket flag. |
| Staleness | No clock expiry. `is_approval_current_for()` recomputes the current records with the artifact's own salt and compares `set_digest`; also workspace id, schema versions, artifact integrity. Any added/removed/changed protected field invalidates the whole approval. |
| How readiness detects it | `load_config()` → `resolve_with_approval()`; uncovered violations raise `ConfigAuthorityError`. KUP `execution.preflight` (`kriya/kup/execution.py::_load_in`) re-resolves on **every** call (nothing cached) in the folder's cwd and turns it into the blocker `CONFIG_AUTHORITY_REFUSED`. The launched run runs the same function again and refuses on any blocker, so the preflight cannot be forged by the host. |
| Revoke / read / status | `revoke_local_approval()` (CLI `authority revoke`); `authority inspect` = read-only status. |
| Audit / evidence | **None beyond the artifact itself** (`approved_at`, `artifact_digest`). The CLI writes no log or evidence record. The GUI path therefore produces exactly the same class of record: the same artifact. |
| Secrets | Only `mcp.*.env` feeds the digest, pre-digested with the salt. `describe_pending()` redacts `env` values and `api_key` only (see §3). |
| Existing tests | `tests/test_sec009_config_authority.py`, `test_sec009_p2_authority_approval.py`, `test_authority_cli_refusal.py`; KUP: `test_kup_execution.py`, `test_kup_serve.py`, `test_kup_cli.py`. |

### What the host already guarantees (TRACED)
* The Electron child environment is an allowlist (`PATH`, `PYTHONDONTWRITEBYTECODE`, `HOME`, optional `KRIYA_STATE_DIR`): **no `KRIYA_TRUST_FILE`, no `KRIYA_AUTHORITY_HOME`**. The persistent KUP child (`kriya kup-stdio`) and the launched run therefore resolve the same `~/.kriya/authority` store.
* Mutating KUP operations never ride the generic renderer query channel (`workspace.create` is rejected there); they have a dedicated typed IPC channel and a host module that checks the folder against the one the host has open (`approval_host.ts`, `project_host.ts`).

## 2. Design (no second authority implementation)

```
Renderer ──typed IPC {workspace, fingerprint}──▶ Electron main (ConfigApprovalHost: open-workspace check, in-flight guard, closed grammar)
        ──▶ KUP `configuration.approve` (kup-stdio)
        ──▶ kriya/kup/authority_ops.py: re-resolve in the folder (same cwd discipline as the preflight)
        ──▶ compare to the reviewed fingerprint ──▶ authority_approval.grant_local_approval()  ◀── the SAME function `kriya authority approve` now calls
        ──▶ the unchanged digest-bound artifact in ~/.kriya/authority
Renderer ──▶ `execution.preflight` (existing, uncached) refreshes readiness
```

* **Read:** `configuration.approval_status` (KUP, read-only, `workspace` only) → protected field **names**, classification, source, a review fingerprint, `can_approve`, `blocking_reason`.
* **Write:** `configuration.approve` (KUP; **not** reachable from the generic query channel) → arguments `{workspace, fingerprint}` only. The renderer cannot submit fields, values, a path, a digest to trust, or a store location.
* **Review fingerprint (answer to stop condition 4).** The artifact's own `set_digest` cannot serve as "the digest the person reviewed" (random salt). The fingerprint is `compute_set_digest(build_security_field_records(violations, config, salt=<process-local random review salt>))`: the *same* record builder and *same* set digest, with a salt that is fixed for the life of the Kriya service process instead of per artifact. It is computed only to be compared by the backend; the artifact format, `set_digest` calculation, SEC-009 decisions, approval lifetime and trust model are unchanged. A process-local salt means the renderer cannot use the fingerprint as an offline guessing oracle for an MCP `env` secret; if the service restarts between Review and Approve the approval is refused as stale and the person reviews again (safe direction).
* **TOCTOU:** `approve` re-resolves, recomputes the fingerprint and compares to the reviewed one. Different → `APPROVAL_STALE`, nothing written. Equal → the unmodified CLI primitives write the artifact for exactly the state that was compared (the same `state` object feeds both).
* **No values to the renderer.** Existing redaction covers `env`/`api_key` only; it does not cover credentials embedded in a URL or headers. There is no authoritative per-field "safe to display" metadata, so no value is sent at all: field names, classification and source only. Stop condition 5 does not arise.
* **`can_approve=false`** (with a reason) when `KRIYA_TRUST_FILE` is set in the Kriya process (a trust file is authoritative and the local store is ignored) or the store would resolve inside the workspace.
* **Idempotence:** approving an already-covered, unchanged config reports `already_approved` and writes nothing (the CLI would rewrite an equivalent artifact; the effect is identical).

## 3. Semantics held fixed (item 23 of the spec)

Unchanged: which fields need approval, digest calculation (`compute_value_digest`, `compute_set_digest`, artifact digest), SEC-009 decision logic, artifact format and location, approval lifetime, trust model. New: two KUP operations, one typed IPC channel, a review fingerprint used only for comparison, one extracted function shared by the CLI and KUP.

(Results, tests, review record and screenshots are appended below as the work completes.)

## 4. What was built

* Backend: `kriya/kup/authority_ops.py` (`configuration.approval_status`, `configuration.approve`); `authority_approval.grant_local_approval()` (one write path, now also used by `kriya authority approve`) and `review_fingerprint()`; `serve.py` lists the two operations in `NO_CONFIGURATION_OPERATIONS` (they load no service configuration, as `kriya authority` is reachable while a configuration is denied). New closed error codes `APPROVAL_STALE`, `APPROVAL_UNAVAILABLE`.
* Contract: `ui/kup/schema` (payloads, `KupRequest` entries, `ApproveConfigurationRequest/Result`), regenerated TS/Ajv/Java (`check:generated` current, `java:check` OK). The `protected_fields` items are `additionalProperties: false`, so **the contract itself refuses an answer that carries a value** (tested: the renderer shows a typed error, never the value).
* Host: IPC channel `kriya:host:config:approve`, `config_approval_host.ts` (closed grammar `{workspace, fingerprint}`, open-workspace check, in-flight guard, passes Kriya's answer through). `configuration.approve` is refused on the generic query channel (as `workspace.create`); `kriya_argv` has no one-shot form for it.
* Renderer: `ConfigApproval.tsx` (ACTION REQUIRED card, review dialog), `GoalComposer.tsx` (other blockers stay visible under "Also needs attention"), `ExecutionApi.approveConfiguration` (always re-reads the preflight; `preflightStale` while it is in flight). Wording: the terminal instruction is gone from the Project page; it remains only (a) as a fallback for a host with no approval port and (b) in Kriya's own blocker message shown in diagnostics.

## 5. Verification (this session, HEAD after commit 0b92510 + review fixes)

| Check | Result |
|---|---|
| Backend `tests/test_kup_config_approval.py` (equivalence with the CLI in both directions, TOCTOU, stale-after-change, restart/durability, redaction, grammar, unavailable conditions, store faults, service path) | 36 pass; 9 mutants (no fingerprint compare, constant fingerprint, coverage always true, grammar admits values, no trust-file refusal, value leak, CLI no longer shares the write, exemption removed, idempotence) all killed |
| Existing authority/KUP: `test_sec009*`, `test_authority_cli_refusal`, `test_kup_*` (incl. error-code and vocabulary tripwires, updated for the two new codes/operations) | pass (790 in the wide targeted run; the one failure is the owner's vim swap file, see below) |
| Real KUP + host + real CLI + launched run (`ui/standalone/test/config_approval_real.test.ts`) | 11 pass: GUI approval is CURRENT for `kriya authority inspect`, honoured by a launched run, stored in the operator HOME outside the folder with no secret; reverse direction; race refused with nothing written; drift; restart; idempotence; typed errors |
| Host unit `config_approval_host.test.ts` | 8 pass |
| Renderer `config_approval.test.tsx` (A-M of the spec + extras) | 23 pass; 11 UI + 7 host mutants all killed (3 survivors on first pass were real test gaps; tests added) |
| Full suites | ui/shared 591 pass (568 before), ui/standalone 367 pass 1 skipped (348 before), ui/kup 66 pass, test-host 48 pass |
| typecheck, eslint, `check:shared-deps`, `check:generated`, `java:check`, ruff, pylint | all clean |
| Electron smoke (`demo-v2/scripts/config_approval_smoke.sh`, controlled body, scratch HOME, no model) | journey Open Folder -> ACTION REQUIRED -> Review -> Approve -> Ready (goal kept, Start Run on) -> Start Run -> COMPLETED; Start Run bottom edge 396 px at 1280x800, 1400x900, 1100x700; no secret in the page; after the app exited the real `kriya authority inspect` reported the approval CURRENT, the artifact held no secret, nothing was written into the project |

## 6. Observations (not changed here)

* **Pre-existing:** `kriya authority inspect|approve` print `llm.base_url` in clear including URL credentials (`describe_pending` redacts only `env` and `api_key`). The GUI sends no values at all, so it is unaffected. Recorded for the owner; a display-redaction fix is a CLI change outside this task.
* The review fingerprint is process-local: restarting the Kriya service between Review and Approve refuses the approval as stale and the person reviews again (safe direction, tested).
* No Revoke UI (the CLI `kriya authority revoke` remains; `reset_demo.sh` revokes).

## 7. Independent review (fresh reviewer, read-only, commits 89ca45b + 0b92510)

Verdicts as reported: **Security PASS (conditional)**, **UX FAIL (not yet)**; no BLOCKING finding. The reviewer verified: one shared write path (`grant_local_approval`), request shape enforced three times (host grammar, service allowlist, `serve.ARGUMENTS`), query channel refusal, double workspace gate, resolve+compare+write under one lock hold, no value/path/digest to the renderer, child environment without `KRIYA_TRUST_FILE`/`KRIYA_AUTHORITY_HOME`, Electron hardening, CLI refactor behaviour-neutral, no approve-on-Enter.

| # | Sev | Finding | Disposition |
|---|---|---|---|
| 1 | IMPORTANT | The human confirmation is a renderer click; there is no main-process (native dialog) confirmation, so a compromised renderer could call status -> approve -> start. The fingerprint binds to configuration drift, not to a human review. | **OWNER DECISION, not implemented.** This is the same trust model as the existing change-approval path (renderer click -> typed IPC -> Kriya), and a hostile renderer already controls Start Run. A native `dialog.showMessageBox` inside `ConfigApprovalHost` would add a second confirmation to every approval; it is a small, separable hardening if the owner wants it. The earlier sentence "renderer cannot approve what it did not review" is corrected: it holds against configuration drift (TOCTOU), not against a compromised renderer. |
| 2 | IMPORTANT | Names-only review: the person approves without seeing values; the CLI shows redacted values. | **Partly fixed.** Each setting now shows where it came from ("set by this project's kriya.yaml") in the main view, and the dialog says that a name does not describe behaviour and to read the kriya.yaml first. A safe value projection needs authoritative per-field display metadata that does not exist (stop condition 5): deferred. |
| 3 | IMPORTANT | Dead end if the configuration was approved elsewhere after the card appeared: "Close" did not re-read readiness. | **Fixed + test** (closing the review re-reads the preflight; mutation-checked: removing it fails the test). |
| 4 | IMPORTANT | Jargon; no statement of what approving permits or who the operator is. | **Fixed (wording):** the dialog now says the settings come from configuration files, that approving lets Kriya use them (model server address, tools/programs they name), that the person is the operator and should approve only if they trust it. |
| 5 | COSMETIC | Evidence 03 still showed the stale "Approve the configuration above to start." | Fixed in 0b92510; screenshots re-captured on the final build. |
| 6 | COSMETIC | For an `unavailable` status the card still says Review & Approve / "Needs your approval". | Recorded, not changed (the dialog explains why it cannot be done here). |
| 7 | COSMETIC | A mid-review restart of the Kriya service shows "Configuration changed...". | Recorded; safe direction, wording follows the spec. |
| 8 | COSMETIC | Doc uncommitted at review time. | Committed with this update. |
