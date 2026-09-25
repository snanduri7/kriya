# PRD-011 Live Verification

## Verdict
LIVE_VERIFIED @ 99c2340.

## Evidence
- Command: `KRIYA_PRD011_EVIDENCE_DIR=handover/evidence/PRD-011/user-live .venv/bin/pytest -m live_model -ra -s tests/test_live_prd011_toolchain_parity.py` - passed.
- Record: `handover/evidence/PRD-011/user-live/toolchain-parity.json`.
- Java 17 repository: compile and run gates both ran in `maven:3.9-eclipse-temurin-17`, pinned by image digest
  `sha256:f0be3f74...`; observed runtime `17`, observed Maven `3.9.16`; selection basis `repository_declaration`.
- Python 3.12 repository: compile and run gates both ran in `python:3.12-slim`; observed runtime `3.12.14`
  (checked against the declared constraint); selection basis `repository_declaration`.
- Baseline/target migration semantics (authorized Java 17 -> 21, unauthorized conflict, Python equivalent,
  migration resume) are proven deterministically by `tests/test_prd011_toolchain_migration.py` (pytest above);
  the live test proves the image/attestation path against real Docker.

## Superseded record (pre-reopen revision)

### Verdict (superseded)
LIVE_VERIFIED

## Real containment result
- Command: `.newvenv/bin/python -m pytest -m live_model -ra -s tests/test_live_prd011_toolchain_parity.py`
- Result: **1 passed** in **20.66s**.
- Environment: Docker Desktop on Darwin; Python 3.14.6 test runner.
- Evidence: `handover/evidence/PRD-011/user-live/toolchain-parity.json` and `toolchain-parity.log`.

## Java 17 evidence
- Declared/runtime identity: JDK 17; observed JDK 17.
- Build tool: Maven 3.9; observed Maven 3.9.
- Image: `maven:3.9-eclipse-temurin-17`.
- Content digest: `sha256:f0be3f7442b426f4bc8238fcb0fc5545be96d3266511aed07f767fd51b78a09e`.
- Compile and runtime digests matched.
- Runtime output: `java17-ok`.

## Python evidence
- Declared/runtime identity: CPython 3.12; observed CPython 3.12.
- Image: `python:3.12-slim`.
- Content digest: `sha256:4f8d1afed6d58037c680221ca6dd9fb4737b7ecfa7d4809ca809fdc0c7d9b786`.
- Compile and runtime digests matched.
- Runtime output: `python312-ok`.

## Acceptance conclusion
PRD-011 acceptance criteria are met: contained compile/runtime execution used policy-compatible versioned toolchains, exact image content identities were persisted, and no required-containment host fallback occurred.
