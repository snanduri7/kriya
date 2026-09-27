# Batch 6 raw evidence index

| directory | what | used for closure |
|---|---|---|
| `user-live/` | First live run. The fixture was forced to 8K with no qualification record (my bug, 83a80fd, fixed in 3e6f273): 2 FAILED, PRD-029 NOT_LIVE_EXERCISED. | no (triage evidence) |
| `user-live-2/` | Live run before the 001C fix. Its files predate 8600e2d. | no |
| `user-live-3/` | Live run at e34e0ee. All cases LIVE_EXERCISED, but PRD-027 recorded `certified: false` (0.4808), which that test did not yet check. | no (it exposed PRD027-PRECISION-001) |
| `prd027-precision/` | The 0.4808 certification record before the fix, the gate outputs, the retrieval diagnosis for both embedders, and the rule simulation. | diagnosis |
| `final-gate-2/` | The CERTIFIED record 9d2a3e44 (0.5814), and the qwen3.6 NOT_QUALIFIED record 317afb9a under the old fallback settings. | certification |
| `final-gate-3/` | The fallback config change (before, after, diff), and the qwen3.6 QUALIFIED record fc063b9e. | qualification and doctor |
| `user-live-4/` | **Closure live run.** 32K QUALIFIED qwen3-coder preflight (runtime ea90552d…). Every case LIVE_EXERCISED. PRD-027: `live_status` LIVE_EXERCISED and `certification_status` CERTIFIED at 0.5814. PRD-029 targeted: COMMITTED, DIRECT authorization, shape `total(items, tax_rate=0.0)`, consumers invalidated and re-verified by `terminal_full_regression`. | yes |
