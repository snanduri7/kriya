# PRD-010 Live Verification

## Verdict
LIVE_VERIFIED

## Local-model runtime evidence
- Model: `qwen3.5:9B`.
- Endpoint: local Ollama-compatible endpoint at the test default.
- Test: `tests/test_live_production_doctor.py::test_production_doctor_recognizes_real_runtime_fingerprint_and_qualification`.
- Result: **1 passed, 0 failed** in **0.50s**.
- Proven invariant: the live endpoint exposed the exact model through its OpenAI-compatible listing, Ollama native metadata supplied an artifact digest, the doctor produced a 64-character runtime fingerprint, and the identity resolved to `known_production_profile`.

## OCI runtime evidence
- Test: `tests/test_production_doctor.py::test_real_oci_containment_smoke_has_network_and_root_filesystem_closed`.
- Result: **1 passed, 0 failed** in **1.27s**.
- Proven invariant: a real Docker container completed the production smoke command with networking disabled, a read-only root filesystem, all capabilities dropped, `no-new-privileges`, and only bounded writable tmpfs scratch.

## Independent pytest evidence
- Result: **4749 passed, 0 failed, 7 deselected, 146 warnings**.
- Duration: **911.59s (0:15:11)**.

## Evidence paths
- `handover/evidence/PRD-010/user-live/model-fingerprint.log`
- `handover/evidence/PRD-010/user-live/oci-smoke.log`
- `handover/evidence/PRD-010/user-full.xml`

## Conclusion
PRD-010 acceptance criteria are met and the task is VERIFIED.
