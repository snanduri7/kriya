# Backlog 6.6 final gate: user-run evidence (2026-09-27, HEAD f378c40)

## Pytest
- Focused (handover/BACKLOG_6_6_SUMMARY.md "Final-gate commands"): passed.
- Full `.venv/bin/pytest`: `6419 passed, 30 deselected, 176 warnings in 1642.34s (0:27:22)`, 0 failed.

## model status (demo-03 production config)
```
  developer        qwen3-coder:30b                          QUALIFIED
  developer        qwen3.6:35b-a3b-q4_K_M                   QUALIFIED
  planner          qwen3-coder:30b                          QUALIFIED
  architect        qwen3-coder:30b                          QUALIFIED
  reviewer         qwen3-coder:30b                          QUALIFIED
  run_verifier     qwen3-coder:30b                          QUALIFIED
  skill_gap        qwen3-coder:30b                          QUALIFIED
  spec_compliance  qwen3-coder:30b                          QUALIFIED
```
Runtime digests unchanged: primary ea90552d45f9c181…, fallback 64e12eef74d56021… (doctor model.qualification evidence).

## context certify
```
[PASS] same_class_member: 4/4 recall 1.0 (target 1.0)
[PASS] sibling_implementation: 2/2 recall 1.0 (target 0.5)
[PASS] interface_contract: 2/2 recall 1.0 (target 1.0)
[PASS] test_precedent: 2/2 recall 1.0 (target 0.5)
[PASS] build_metadata: 2/2 recall 1.0 (target 0.5)
[PASS] configuration: 2/2 recall 1.0 (target 0.5)
[PASS] direct_caller: 2/2 recall 1.0 (target 1.0)
[PASS] one_hop_dependency: 2/2 recall 1.0 (target 1.0)
[PASS] two_hop_dependency: 2/2 recall 1.0 (target 0.5)
precision 0.5814 (target 0.5)
CERTIFIED=true  record: .../run-production/logs/context_certification/1a241633b725bd64b4be99503a47ebddf57b98ec2453404a740c86798b75feb7.json
```

## doctor --production
- PASS: profile.production, plugins.core_tools, workspace.identity_lock, persistence.checkpoints, persistence.logs, capacity.workspace, capacity.temp, git.worktree, isolation.candidate_worktree, toolchain.required, containment.oci_smoke, containment.no_host_fallback, egress.policy, model.connectivity, model.runtime_fingerprint (ea90552d…, context_window_overrides []), **model.qualification** (all roles QUALIFIED), embedding.connectivity, **context.recall_certification** (CERTIFIED 2026-09-27T07:49:36Z), lsp.java, release.integrity.
- WARN (non-blocking, unchanged from 6.5): persistence.traces (legacy traces.db; `kriya traces --migrate-legacy` out of 6.6 scope), models.role_independence (not required by policy), semantic.precision_boundary, runtime.fixed_guarantees (follows persistence.traces).
- `PRODUCTION_READY=true`

## authority inspect with KRIYA_AUTHORITY_HOME inside the workspace (live)
Lists the 20 pending security-authority fields, then:
```
Error: [TRUST_PATH_INSIDE_WORKSPACE] trust-artifact path '.../workspace/repo/.authority-test/x.json' resolves inside the workspace root '.../workspace/repo' - a trust artifact must live outside the workspace ...
Remediation: Point KRIYA_AUTHORITY_HOME (or --trust-file/KRIYA_TRUST_FILE/--out) at a directory outside .../workspace/repo, or unset KRIYA_AUTHORITY_HOME to use the default ~/.kriya/authority.
exit=1
```
No traceback; the refusal is kept; nothing written.

## Live verdicts
- AUTHORITY-INSPECT-TRACEBACK-001: LIVE_EXERCISED (above).
- PROMPT-FIT-ROLE-CHAIN-001, DEVELOPER-PROMPT-FIT-001: NOT_LIVE_EXERCISED by design (the qualified 32K profile triggers neither fit); verified by pytest, as the user accepted in the final-gate directive.
