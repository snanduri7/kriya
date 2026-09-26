# Batch 6 gate outputs before the PRD-027 precision fix (2026-09-27 02:00-02:01 IST)

These outputs come from the user's terminal, as pasted into the session. Kriya was at HEAD e34e0ee. The config was demo-03 `config/generate-production.yaml`, run from `demo-03-brownfield/workspace/repo`.
- The certification lines are verbatim.
- The doctor lines are verbatim status lines. The evidence body is kept in full for the two checks that matter here. The bodies of the passing checks are left out.

## `kriya -c ../../config/generate-production.yaml context certify`
```
=== Context-recall certification (context-recall/1) ===
[PASS] same_class_member: 4/4 recall 1.0 (target 1.0)
[PASS] sibling_implementation: 2/2 recall 1.0 (target 0.5)
[PASS] interface_contract: 2/2 recall 1.0 (target 1.0)
[PASS] test_precedent: 2/2 recall 1.0 (target 0.5)
[PASS] build_metadata: 2/2 recall 1.0 (target 0.5)
[PASS] configuration: 2/2 recall 1.0 (target 0.5)
[PASS] direct_caller: 2/2 recall 1.0 (target 1.0)
[PASS] one_hop_dependency: 2/2 recall 1.0 (target 1.0)
[PASS] two_hop_dependency: 2/2 recall 1.0 (target 0.5)
precision 0.4808 (target 0.5)
CERTIFIED=false  record: /Users/sriramnanduri/kriya-live-demo/demo-03-brownfield/run-production/logs/context_certification/7c8bf9aa6298ec538cb8bdecd157e995a8318752e09728f9a75246c50cbe87e3.json
```
A copy of that record is in `prefix_certification_record_7c8bf9aa.json`.

## `kriya -c ../../config/generate-production.yaml doctor --production`
```
[PASS] profile.production
[PASS] plugins.core_tools
[PASS] workspace.identity_lock
[PASS] persistence.checkpoints
[WARN] persistence.traces            (legacy trace db at <kriya repo>/logs/traces.db)
[PASS] persistence.logs
[PASS] capacity.workspace
[PASS] capacity.temp
[PASS] git.worktree
[PASS] isolation.candidate_worktree
[PASS] toolchain.required
[PASS] containment.oci_smoke
[PASS] containment.no_host_fallback
[PASS] egress.policy
[PASS] model.connectivity
[PASS] model.runtime_fingerprint     (qwen3-coder:30b ea90552d..., exact, effective_context_window 32768)
[FAIL] model.qualification
[PASS] embedding.connectivity
[PASS] context.recall_certification
[PASS] lsp.java
[WARN] models.role_independence
[WARN] semantic.precision_boundary
[PASS] release.integrity
[WARN] runtime.fixed_guarantees      (trace_persistence WARN, inherited from persistence.traces)
PRODUCTION_READY=false
```
**model.qualification.** `reason_code: QUALIFICATION_STALE`.
- qwen3-coder:30b (fingerprint ea90552d..., settings sha256:ed7bfc09...) is QUALIFIED for architect, developer, planner, reviewer, run_verifier, skill_gap and spec_compliance.
- The developer fallback qwen3.6:35b-a3b-q4_K_M (fingerprint 48e3ddd2..., settings sha256:65e5b10c...) is **STALE**. Every required case is missing. The reasons given:
  - "the record predates inference-settings identity (re-qualify)";
  - "the qualification policy changed (kriya-qualification/2 -> kriya-qualification/3)";
  - "the qualification record schema changed".

**context.recall_certification.** Evidence: `{"detail": "no code index at paths.memory - Graph RAG retrieval is not used", "status": "NOT_APPLICABLE"}`. The doctor never read the stored certification record, so this PASS is not integration evidence.
