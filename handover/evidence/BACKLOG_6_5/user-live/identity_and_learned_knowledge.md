# User-run live evidence (2026-09-27, demo-03 workspace/repo, generate-production.yaml)

## model fingerprint (primary)
qwen3-coder:30b  digest ea90552d45f9c181a06512eb628adf89e138f25ce58b38ff54c0789e58264276
exact: True, provider ollama 0.34.2, configured/effective window 32768, probe_errors [], missing_components []
(unchanged from the pre-INF-001 identity; doctor context_window_overrides: [])

## model status
developer qwen3-coder:30b QUALIFIED; developer qwen3.6:35b-a3b-q4_K_M QUALIFIED;
planner/architect/reviewer/run_verifier/skill_gap/spec_compliance qwen3-coder:30b QUALIFIED.
Doctor model.qualification: fallback runtime 64e12eef74d5... (settings 0f1e6b5c...) QUALIFIED -
the same runtime as qualification record fc063b9e (handover/evidence/BATCH6/final-gate-3).

## learn -> ask (KNOWLEDGE-READPATH-001)
learn -t "Team convention ... v2 path prefix ... never v1 ... owning team is Fleet Platform."
-> "Successfully indexed: Manual Entry (2582b5115c)" (no dummy-embedding warning)
ask "Under which path prefix should a new REST endpoint for cars be added, and which team owns this service?"
-> answers v2 (/v2/cars) and Fleet Platform, attributing both to "the untrusted reference context",
   and separately cites the repository's v1 controllers as repository context.
   The code alone says v1 and names no owner, so the answer came from the learned store,
   labelled as untrusted reference by the fencing.
[Usage: 18878 input tokens, 232 output tokens | Finish: stop]

## Live tier
First run: 25 passed, 5 failed (stale fixtures: SEC-009 approval missing in test_live_smoke.py,
PRD-010-removed keys in test_live_generate_json.py; fixed in 5cac93e / 1023082).
Rerun of those 5: 5 passed (pytest_live_rerun.log). All 8 PRD-025..029 evidence files LIVE_EXERCISED.

## context certify / doctor --production
CERTIFIED, precision 0.5814 >= 0.5, every recall class 1.0 (context_certify.json).
("certify exit:" printed empty because the shell is zsh: PIPESTATUS is bash-only; certified=true in the record.)
PRODUCTION_READY=true; model.qualification PASS; context.recall_certification PASS (doctor_production.txt).
WARNs (non-blocking, pre-existing/advisory): persistence.traces (legacy traces.db at <kriya>/logs),
models.role_independence (not required by policy), semantic.precision_boundary, runtime.fixed_guarantees (follows persistence.traces).
