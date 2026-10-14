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
