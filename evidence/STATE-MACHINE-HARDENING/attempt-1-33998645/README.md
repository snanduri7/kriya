# Attempt 1 (FAILED live matrix) - candidate 33998645…, revision e509fbb, Ollama 0.34.4

- Candidate identity (after requalification on Ollama 0.34.4): `release-candidate.json`.
- Production canary: PASS (both runs) - `canary/`.
- Live 11-case matrix: **9/11, FAILED** - `../matrix/trial-20260929T025849Z/` (kept in place: the sealed streak log records its path).
  C4 exact_requirement and C11 static_analysis_enabled ended quality_gates_exhausted.
- Provisional classification: model/runtime behavior, not a Kriya defect (failures begin on first-generation output; no fallback configured for either case; no failure-family cycle; every candidate correctly rejected; nothing committed; no false success). Ollama 0.34.4 is a candidate cause, NOT proven.
- Classifying it exposed STATE-ATTEMPT-METRICS-001 (retry counts reported from budget counters), fixed in 4240d69; the next candidate is that revision.

Preserved permanently as failed evidence; not reinterpreted or replaced.
