# PRD-031A Coding Agent Handover

## Status
READY_FOR_PYTEST_VERIFICATION (2026-09-27). Not self-certified.
- Live test: REQUIRED. This is the real pinned-scanner tier `-m live_static_analysis` (Semgrep 1.178.0; Docker plus the pinned image for the contained cases).
- No live-model test.

## Source identity
- Base revision: 991ec45, the approved spec (`handover/PRD-031A_TASK.md`, including §5.5 V1-V16).
- Branch: milestone-decomposition (local, unpushed).
- Pinned scanner:
  - host: Semgrep **1.178.0** via pipx;
  - OCI: `semgrep/semgrep@sha256:32e459968daabe7ab86968184a29109b9564aa00392401156f9788452b42786b` (1.178.0, arm64, pulled and recorded 2026-09-27; `latest` is never used).

## How the work was split
- The lead built:
  - the provider-neutral core (`kriya/static_analysis/` except `adapters/semgrep.py`);
  - both commit boundaries, the commit guard, config and authority, the doctor rows, the CLI and the docs;
  - the deterministic core suite.
- A helper agent built, against the lead's contracts and file boundary:
  - the Semgrep adapter (`kriya/static_analysis/adapters/semgrep.py`);
  - its recorded 1.178.0 fixtures;
  - the adapter deterministic suite;
  - the real-tier suite, adapter-level and service-level, host and contained.
- The lead reviewed the adapter code: result interpretation, error classification, engine location, fingerprint, probe, invocation, and containment with no host fallback.

## Scope implemented
The spec's order, 1-13:
1. **Normalized models and contracts** (`model.py`).
   - Covers capability, coverage, findings, the PRE/POST diff, outcomes (closed), reason codes (closed, tripwire), and evidence identity.
2. **`StaticAnalysisPort`, the registry and factory, and the fake adapter.**
   - Files: `port.py`, `registry.py`, `adapters/__init__.py`, `tests/_fake_static_analysis.py`.
   - The fake is test-only, and a tripwire checks that kriya/ never imports it.
3. **Scope planner, coverage and prerequisites** (`scope.py`, `coverage.py`).
   - **Scopes.** CHANGED_FILES, MODULE, REPOSITORY and BUILD_GRAPH are supported. The scope is the provider minimum (`auto`) or broader, never narrower.
   - **Snapshots.** Equivalent PRE and POST snapshots are built in one read per file:
     - an unchanged file is byte-identical in both;
     - a modified file is PRE old, POST candidate;
     - an added file is POST only;
     - a deleted file is PRE only;
     - a nonexistent path is never passed.
   - **Kriya-enforced limits.**
     - **Target size.** Enforced before submission. An oversized target is recorded with path, side, size, limit and reason, and never silently omitted. The provider's size flag is defense in depth only.
     - **Exclusions.** Operator exclusions are applied by Kriya.
     - **Control files.** Scanner-control files never enter a snapshot.
   - **Coverage.** Intended targets are compared with scanner-confirmed ones: scanned minus skipped minus errored.
   - **Scope-wide findings.** Findings in files the candidate did not change are diffed like any other.
4. **Policy** (`policy.py`, pure).
   - The requirement × outcome table follows §7.2.
   - Precedence is UNKNOWN > UNAVAILABLE > BLOCKED > ACCEPTED_RISK > PASS_WITH_WARNINGS > PASS.
   - UNKNOWN always blocks when analysis is enabled.
5. **Waiver registry and operator CLI** (`waivers.py`; `kriya static-analysis status|scan|waive|revoke|waivers`).
   - **Store.** Outside the workspace, read fresh on every use, atomic writes, and each record sealed by a digest.
   - **Binding.** A waiver binds the rule, the path or fingerprint scope, the classification, the max severity, the expiry, the rule-pack digest and the workspace, with provenance.
   - **Authority.** Only the CLI writes or revokes, enforced by single-caller tests. Coverage gaps and scanner failures are never waivable.
6. **SemgrepAdapter** (helper agent). The details are below.
7. **StaticAnalysisService** (`service.py`).
   - The order is: probe, egress admission, scope, change set (with the base verified), snapshots, coverage, scans (POST, then PRE), confirmation, comparability, diff, waivers, policy, evidence.
   - The service fails closed: an internal error is UNKNOWN.
   - Evidence goes under `<state>/static_analysis/<run>/<unit>/`, with the raw outputs stored beside it.
8. **Both commit paths.**
   - Enforce: `TerminalGateService` gate 4 of 7, injected as `TerminalGateValidators.evaluate_static_analysis` from the controller's own `StaticAnalysisService` name.
   - Direct and milestone: `workflow._run_static_analysis_gate`, at the pre-apply boundary before approval, with deterministic stop wiring:
     - retry_strategy's stop set;
     - `failure_category` `static_analysis_{blocked,unknown,unavailable}`;
     - the CLI message.
   - No scanner branching in WorkflowEngine or the controller.
9. **Mandatory evidence in `commit_terminal_candidate`.**
   - The REQUIRED `static_analysis=` guard (`commit_guard(cfg, result)`, reading the current config) refuses, before any intent:
     - missing evidence;
     - non-permitting evidence;
     - stale evidence: the batch, the base scope, scope membership, the provider runtime or rule packs (`runtime_fingerprint()`, recomputed without running the tool), or the effective settings changed.
   - The evidence id goes into the commit's `verification_evidence_ids`, so the RunRecord schema is unchanged.
10. **CLI and JSON reporting.**
    - The result keys are `static_analysis`, `accepted_risk` and `accepted_risks`, on both paths.
    - Banners are printed after generate and fix.
    - ACCEPTED_RISK exits 0 and prints `ACCEPTED RISK — NOT A CLEAN PASS`.
    - The approval prompt shows the outcome.
11. **Doctor.**
    - Seven `static_analysis.*` rows from one probe.
    - NOT_APPLICABLE uses the PRD-027 representation (PASS, `required=False`, `evidence.status`).
    - `schema_version` stays 1.
12. **Deterministic tests.** Listed below.
13. **The pinned Semgrep real tier.** Listed below.

**Production seal (conditional).** It is not forced on. When enabled, the seal requires:
- requirement=required;
- analysis_errors=block;
- prerequisites_missing=block.

UNKNOWN and UNAVAILABLE both block.

## Semgrep adapter (helper agent), as verified
- **Argv:**
  - `scan --metrics=off --disable-version-check --disable-nosem --no-git-ignore --oss-only --json --verbose --timeout <n> --timeout-threshold <n> --max-target-bytes <kriya limit> --config <local pack>... -- <explicit existing targets>`;
  - `--` is accepted by 1.178.0 (host and OCI);
  - `semgrep scan --help` is never relied on (V1).
- **Environment.** Exactly `PATH`, `HOME=<scratch>/home`, `TMPDIR=<scratch>/tmp`, `SEMGREP_SEND_METRICS=off` and `SEMGREP_ENABLE_VERSION_CHECK=0`. Nothing else is inherited: `SEMGREP_RULES`, `SEMGREP_BASELINE_COMMIT` and similar are proven absent.
- **Result mapping.**
  - Findings are parsed whatever the exit code; exit 0 is not clean.
  - `errors[].type` may be a string or a list (V10).
  - Config and rule errors are classified from structured evidence (an invalid pattern exits 2 in 1.178.0, V14) and give CONFIG_ERROR, which the service turns into UNAVAILABLE.
  - Target errors make the target `not_analyzed`. Any other error, or a nonzero exit, gives FAILED, which the service turns into UNKNOWN.
  - A missing `paths.skipped` is MALFORMED_OUTPUT, since `--verbose` is always passed.
- **Identity.**
  - Host: the entry point AND the `semgrep-core` engine, located deterministically from the entry point's absolute shebang interpreter, with no PATH search and no fallback.
  - Contained: the pinned image digest.
  - The severity-map version and effective options are part of the identity digest.
- **Contained execution.**
  - The profile is `UNTRUSTED_EXECUTION`, with a read-only snapshot, read-only rule mounts, `--network none`, uid 65534 and `--pull never`.
  - A missing image or backend gives CONTAINMENT_UNAVAILABLE or PROVIDER_PROBE_FAILED. There is never a host fallback.
- **Declared limitations.**
  - Semgrep CE may recover silently from syntax errors (V11), so parse completeness is not claimed. This does not make results UNKNOWN; Kriya's own compile and build gates stay authoritative.
  - Analysis is file-local.
  - In host mode the network is not OS-enforced.

## Corrections to the spec made during implementation (all toward fail-closed)
1. **An operator coverage condition set to `block` yields BLOCKED, not UNAVAILABLE.**
   - The cause: an optional requirement would have let UNAVAILABLE commit, so `prerequisites_missing: block` would not have blocked.
   - It is found and killed by the mutation check.
2. **Exit 4, 5, 7 or 13 with no config kind in `errors[]` is FAILED/UNKNOWN**, not the §5.3 table's RULE_PACK_INVALID or PROVIDER_PROBE_FAILED. Classification follows the structured evidence (user directive), and both results block.
3. **No `ObligationKind.STATIC_ANALYSIS` ledger record** (§10.2).
   - That enum's own rule forbids speculative kinds.
   - Lineage is already carried by the `static_analysis.result` run event, the evidence file and the commit's evidence id.
   - The gate's gap stays the single blocking authority.
4. **No doctor schema bump** (approved correction). The existing NOT_APPLICABLE representation is reused.
5. **`ContainmentProfile.image_reference`** (new, additive, default None) plus **`--pull never`** in the OCI backend. A mutable tag is refused by the backend.
6. **Relative rule-pack and waiver-store paths are anchored to `config_dir`** in `resolve_config_state`, with the same idiom as other path fields.

## Own bugs found and fixed before any commit
Each has a regression test proven to fail without its fix.
- **A config object without a `static_analysis` section crashed the gate,** which then failed closed as a false gate failure. It was found by the existing enforce characterization suite. It now reports DISABLED/NOT_CONFIGURED. Test: `test_a_configuration_without_the_section_is_not_configured_not_an_error`.
- **A workspace reached through a symlink (macOS `/var` → `/private/var`) made every changed path look like it escaped the workspace.** It was found by the operator-scan test; pytest's `tmp_path` is already real, which had hidden it. Paths now compare as real paths. Tests: `test_a_workspace_reached_through_a_symlink_is_the_same_workspace` and `test_operator_scan_...` (both fail without the fix; shown).
- **A static-analysis validator that raised emitted `not_evaluated` instead of `failed`.** Test: the PRD-030 matrix case `static_analysis`.

## Characterization tests changed on purpose
- `tests/test_prd030_terminal_services.py`:
  - GATES includes `static_analysis` at position 4;
  - the default validators wire a disabled service;
  - the all-pass sequence expects the `disabled` event;
  - a new matrix case covers a raising static-analysis gate;
  - the commit request carries the disabled guard.
- `tests/test_workflow_controller_enforce.py`:
  - there are 7 `terminal_gate_outcome` events, with `static_analysis` 4th and status `disabled`;
  - the result carries `static_analysis`/`accepted_risk`;
  - a new gate-matrix case covers a raising service.
- `tests/test_production_doctor.py`: the pinned check-id tuple gains the seven rows.
- Commit-seam tests pass `DISABLED_STATIC_ANALYSIS` (or `commit_guard(None, None)` inside subprocess scripts), because the new keyword argument is required:
  - `tests/_milestone_proof_harness.py`
  - `tests/test_prd007_run_lifecycle.py`
  - `tests/test_prd008_recovery.py`
  - `tests/test_prd008_commit_state_gate.py`
  - `tests/test_prd029_contract_lifecycle.py`

No other characterization test changed.

## Files changed
- **New:**
  - `kriya/static_analysis/{__init__,model,port,registry,coverage,scope,baseline,policy,waivers,service,doctor,operator_scan}.py`
  - `kriya/static_analysis/adapters/{__init__,semgrep}.py`
  - `tests/_fake_static_analysis.py`
  - `tests/test_prd031a_static_analysis.py`
  - `tests/test_prd031a_semgrep_adapter.py`
  - `tests/test_prd031a_semgrep_live.py`
  - `tests/fixtures/static_analysis/semgrep/1.178.0/**`
- **Modified production files:**
  - `kriya/config/config.py`: the config schema, the production seal, and path anchoring.
  - `kriya/config/authority.py`: `static_analysis.*` is SECURITY_AUTHORITY.
  - `kriya/workflow/terminal_gate_service.py` and `commit_service.py`: the gate and `plan_terminal_writes`.
  - `kriya/workflow/terminal_commit.py`: the required guard.
  - `kriya/workflow/workflow_controller.py` and `workflow.py`: both boundaries and the result fields.
  - `kriya/workflow/retry_strategy.py`: the stop set.
  - `kriya/workflow/state.py`: `static_analysis_result`.
  - `kriya/tools/containment.py` and `containment_oci.py`: `image_reference` and `--pull never`.
  - `kriya/production_doctor.py`: the seven rows.
  - `kriya/cli.py`: the command group, the banners and the stop message.
- **Other:**
  - `pyproject.toml`: the `live_static_analysis` marker, excluded by default.
  - Docs: `docs/design.md` §2.9d, `docs/user_guide.md` §2.1g, `CLAUDE.md`.
  - `handover/BACKLOG_REGISTRY.csv`: three items added.

## Tests run by the coding agents
Targeted runs only. The full suite is for the user.

| Command | Passed | Failed |
|---|---:|---:|
| `pytest tests/test_prd031a_static_analysis.py` (core, fake provider) | 92 | 0 |
| `pytest tests/test_prd031a_semgrep_adapter.py` (recorded 1.178.0 fixtures) | 136 | 0 |
| `pytest -m live_static_analysis tests/test_prd031a_semgrep_live.py` (helper agent; host and OCI, pinned image) | 26 | 0 |
| `pytest tests/test_prd030_terminal_services.py` | 27 | 0 |
| enforce gate matrix and event-order tests (`-k` on the two PRD-004 tests) | 8 | 0 |
| `pytest tests/test_production_doctor.py` | 63 | 0 |
| `pytest tests/test_sec009_config_authority.py tests/test_sec009_p2_authority_approval.py tests/test_authority_cli_refusal.py` | 95 | 0 |
| `pytest tests/test_strict_doubles.py tests/test_prd012_network_inventory.py tests/test_config.py` (with the doctor file) | 119 total | 0 after the pinned-id update |
| commit-seam files, `-k "commit or crash or transition or recover"` | 76 | 0 |
| `pytest tests/test_containment_oci.py -k "not docker and not real"` | 26 | 0 |

**Mutation check.**
- Core: 36/36 killed. They cover:
  - the policy table and precedence (the waiver-masking survivor was killed by the new `test_a_waiver_never_masks_another_blocking_finding`);
  - every waiver conjunct;
  - diff and fingerprint;
  - every guard component;
  - `analysis_enabled`, comparability, egress, scope minimum, scan failure, oversize, control files, exclusions, base mismatch, confirmation;
  - `commit_eligible`, the raising-gate status, the commit refusal, and the direct-stop raise.
- Adapter (helper agent): 45/45 killed.

## Static/lint
`.venv/bin/ruff check .` passed. `.venv/bin/pylint kriya plugins/core_tools tests` exited 0.

## Registry (canonical: `handover/BACKLOG_REGISTRY.csv`)
New, each with a target scope:
- **OCI-NONROOT-TMPFS-WORKDIR-001** (P3 OPEN): a pre-existing OCI backend behaviour that affects no-workspace, non-root TOOL-003 profiles. It fails closed.
- **STATIC-ANALYSIS-SEVERITY-MAP-V2-001** (P3 DEFERRED): newer Semgrep severity values currently resolve to unknown and are decided as high, which is fail-safe.
- **STATIC-ANALYSIS-CI-LIVE-JOB-001** (P3 DEFERRED → PRD-034).

Already registered by the spec: STATIC-ANALYSIS-REMEDIATION-LOOP-001 and STATIC-ANALYSIS-MULTI-PROVIDER-001 (both P3, DEFERRED).

No new P0 or P1.

## Verification handoff
- **Focused:**
  ```
  .venv/bin/pytest tests/test_prd031a_static_analysis.py tests/test_prd031a_semgrep_adapter.py tests/test_prd030_terminal_services.py tests/test_workflow_controller_enforce.py tests/test_workflow_controller.py tests/test_workflow.py tests/test_production_doctor.py tests/test_config.py tests/test_sec009_config_authority.py tests/test_sec009_p2_authority_approval.py tests/test_prd007_run_lifecycle.py tests/test_prd008_recovery.py tests/test_prd008_commit_state_gate.py tests/test_prd029_contract_lifecycle.py tests/test_prd004_commit_failure.py tests/test_prd005_commit_transactions.py tests/test_strict_doubles.py tests/test_prd012_network_inventory.py tests/test_containment_oci.py tests/test_backlog_registry.py
  ```
- **Full:** `.venv/bin/pytest`
- **Real pinned-scanner tier.** This needs Semgrep 1.178.0 on PATH, and for the contained cases Docker plus the pinned image above:
  ```
  .venv/bin/pytest -m live_static_analysis tests/test_prd031a_semgrep_live.py -v
  ```
