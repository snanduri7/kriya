# PRD-035 Coding Agent Handover — Live Local-Model Certification Matrix

## Status

Tracker: `READY_FOR_PYTEST_VERIFICATION`. The certification outcome for the target identity is in **Results** below. It is reported exactly as measured and never rounded up.

## Source identity

- **Base:** `325eb4f` (after the prompt-fit closure).
- **Commits:**
  - `e987789` (implementation);
  - the C7 case fix commit;
  - the closure commit.

## Scope implemented

**Instruction:** `tasks/PRD-035_Live_Local-Model_Certification_Matrix.md`, Wave 7 directive §7, and the design in `handover/PRD-035_DESIGN_ANALYSIS.md`.

1. **Existing smoke kept unchanged.** `test_live_smoke.py`; CI runs `live_model and not live_target`. Production-certification cases:

   | Case | Shape |
   |---|---|
   | C1 | simple bug fix |
   | C2 | multi-file feature |
   | C3 | brownfield extension |
   | C4 | exact-requirement compliance |
   | C5 | targeted retry (one injected compile failure) |
   | C6 | configured fallback transition (the demo-03 qualified qwen3.6 identity; the primary's candidates fail until the attempt reaches the fallback) |
   | C7 | contained compile/test (OCI, declared pinned test runner, registry-scoped acquisition) |
   | C8 | PRE/POST full regression (baseline policy `required`) |
   | C9 | resume safety (an injected-failure run, then a resumed run) |
   | C10 | malicious repository instruction |
   | C11 | static analysis enabled and required (Semgrep 1.178.0) |

2. **Tagging.**
   - Marker `live_certification` (plus `live_model` and `live_target`); never CI.
   - Report tier `target_production` only for a single QUALIFIED identity on an exact, observable execution environment. Otherwise it is `wiring`, which never certifies.
   - The environment digest and properties (Apple M1 Max, 64 GiB) are in the report.
3. **Exact `ModelRuntimeFingerprint`** in every case record, per role, with inference-settings digests.
4. **Assertions on deterministic evidence only.** Each case checks:
   - a hidden acceptance test the model never saw, run by the harness after the run;
   - RunRecord and commit results;
   - gate evidence, events and resume decision;
   - static-analysis evidence bound to the commit (`static_analysis:` in `verification_evidence_ids`);
   - no secret in any written file.

   It never checks prose. **Every case must succeed**: a typed failure is FAILED.
5. **Captured per case:**
   - first-pass compile;
   - final result;
   - retries;
   - fallback transitions;
   - wall time;
   - tokens in and out (PRD-033 deriver over the case's own trace rows);
   - context window and output settings;
   - the static-analysis outcome (UNKNOWN/UNAVAILABLE included);
   - the commit outcome.
6. **One command:** `scripts/certify_model.sh [OUT_DIR]`. It writes:
   - `junit.xml` and `pytest.txt` (raw);
   - `model-certification.json` (content-digested);
   - `model-certification.md`.

   On the target tier it also stores the certification record.
7. **Identity-keyed, staling evidence.**
   - `~/.kriya/certifications/` (`KRIYA_CERTIFICATION_HOME`) holds digest-sealed records keyed by model, exact runtime digest, inference-settings digest, environment digest and case-set version.
   - `kriya model certification [--json]` reports CURRENT, FAILED, STALE (naming what changed), INVALID (tampered) or MISSING, and exits 1 unless CURRENT.

**Backlog:**
- The prompt-fit precondition closed first (`325eb4f`).
- INF-001-ENV-EVIDENCE stays INF-002 per the registry.
- No vLLM.

## Files changed

- `kriya/core/model_certification.py` (new).
- `kriya/cli.py` (`model certification`).
- `tests/_model_certification.py`, `tests/test_live_prd035_certification.py`, `tests/test_prd035_certification_records.py` (10 deterministic tests).
- `tests/conftest.py` (`--model-certification`, case collection).
- `scripts/certify_model.sh`.
- `pyproject.toml` (markers).

## Results (target machine, Apple M1 Max 64 GiB, `qwen3-coder:30b`)

Identity:
- runtime `ea90552d45f9c181a06512eb628adf89e138f25ce58b38ff54c0789e58264276` (exact, QUALIFIED);
- inference settings `sha256:ed7bfc09…`;
- environment `sha256:7b3ce83b…`;
- fallback for C6: `qwen3.6:35b-a3b-q4_K_M` at the demo-03 qualified settings.

| Run | Revision | Result | Evidence |
|---|---|---|---|
| 1 | `e987789` | **FAILED, 9/11** | `evidence/PRD-035/matrix-run1/` |
| 2 | C7 case fix | **CERTIFIED, 11/11** (9:07) | `evidence/PRD-035/matrix-run2/` (content digest `26c3c0aa…`) |

**Run 1 failures:**
- **C7: case-design error, fixed.** The contained Python image has no `pytest`, and the case declared no dependencies. Kriya correctly stopped (`environment_failure`: "pytest not available … no dependency manifest"; no host fallback). The case now declares `pytest==9.1.1` and passes contained, with registry-scoped acquisition.
- **C8: a genuine model/repair failure.** The model added its own test calling `add_item("date", 0)`, contradicting the existing `ValueError` contract, and could not repair it in 3 attempts (`quality_gates_exhausted`). Nothing was committed. This is kept as evidence.

**Run 2:** all 11 passed. Certification record `evidence/PRD-035/certification-record.json`; `kriya model certification` → **CURRENT**.

**Honest reading.**
- Over the two runs, 10 of 11 cases passed both times, and C8 passed 1 of 2.
- The certification rule is currently one full passing matrix per identity. A single run cannot measure flakiness like C8's, so certification should require repeated trials (k-of-n per case). Registry **LIVE-CERTIFICATION-REPEATED-TRIALS-001** (P2) targets PRD-036, which owns the release gate.
- The CURRENT record means "the latest full matrix passed", not "reliable".

**Per-case evidence** (tokens, retries, first pass, fallbacks, wall time, commit and gate evidence) is in each run's Markdown and JSON.

## Tests run by the coding agent

| Command | Result |
|---|---|
| `tests/test_prd035_certification_records.py` | 10 passed |
| `scripts/certify_model.sh` (run 1 / run 2) | 9/11 FAILED, then 11/11 CERTIFIED |
| C7 alone after the case fix | passed (52s) |
| ruff / pylint | 0 findings |

## Known limitations

- The single-run certification rule (above).
- The live results depend on the model: they are real evidence, never asserted prose.
- CI never runs this tier (by design).
