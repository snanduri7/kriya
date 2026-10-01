# PRD027-SCORE-NORMALIZATION-001: direct query hits are ranked below graph-expanded files

**Status: OPEN, P2.** Found during the PRD027-PRECISION-001 diagnosis (2026-09-27). Not fixed. The user decided to keep it separate from the precision fix.

## Problem
`graph_retrieval.retrieve_graph_context` builds one `file_scores` map from two score domains that can't be compared:
- files retrieved directly carry their best hybrid RRF score, at most 2/61 ≈ 0.033;
- graph-expanded files carry relation weight divided by hop, 0.5–1.0.

So every graph-expanded file outranks every file the query actually matched. In junit-upgrade, the golden `pom.xml` scores 0.0328 while PriceCalculator scores 1.0 (`evidence/BATCH6/prd027-precision/diag_real_embedder.json`).

`context_budget._build_file_tiers` degrades the **lowest** score first, and `_omit_over_budget` drops the lowest score first. When the budget binds, the query's own direct evidence is therefore shrunk or omitted before secondary graph evidence. That breaks the intended evidence priority.

It did not affect the Batch 6 certification, because the 10644-token reference budget does not bind there (nothing was omitted, every file at full tier).

## Severity
P2. It becomes P1 if a deterministic budget-constrained regression shows a directly relevant query hit omitted or shrunk because a graph-expanded file outranks it only through the incompatible scales.

## Requirements for the future fix
- **Invariant:** direct query evidence must not lose priority merely because graph scores are numerically larger.
- **No arbitrary constants.** Evaluate a common ranking model: normalized per-source rank, reciprocal-rank fusion across sources, explicitly tiered evidence classes, or another deterministic comparable scoring.
- **Keep what works:** useful graph expansion and the existing PRD-027 recall.

## Fix (Backlog 6.5, 2026-09-27): FIXED, awaiting the user's pytest run and `context certify`
**Strategy: explicit evidence tiers** (`kriya/workflow/graph_retrieval.py::evidence_scores`).
- Each evidence class (direct query hits and graph-expanded files) is normalized by its own best score into (0, 1].
- Direct hits are then lifted into (1, 2] by `DIRECT_EVIDENCE_TIER = 1.0`. That is the upper bound of the lower tier: the tier boundary, not a tuned weight.

**Result:**
- Any direct hit outranks any graph-expanded file, whatever the raw scales (tested at graph scales 1, 10 and 1000).
- Order within a class is exactly its raw order.
- A file in both classes is ranked as direct.
- A missing class (embedding-only or keyword-only, or no graph walk) is simply absent.
- The budget builders (`_build_file_tiers`, `_omit_over_budget`) are unchanged: they degrade and omit lowest-first, so graph expansion now gives way first and is kept in full whenever it fits.
- The PRD027-PRECISION-001 seed rule is untouched.

**Certification identity:**
- `graph_retrieval` is digested as a whole, so this change invalidates the stored certification.
- While testing that requirement I found my own gap from 3c1822d: the digest did not cover the tier degradation, omission, skeletonizers or token estimate. Fixed in its own commit, f324f91.
- The user must re-run `kriya context certify`.
- CI (deterministic) certification after the fix: precision **0.5435**, CERTIFIED, all 9 classes pass. That is unchanged, because the reference budget does not bind.

**Tests:** `tests/test_prd027_score_normalization_001.py`, 16 tests.
- **Ranking:** direct over expanded; any graph scale; order within a class; explicit bounded tiers; both classes; a missing class.
- **Binding budget:**
  - the graph file gives way first;
  - a control with the raw pre-fix scores degrades the direct hit first;
  - graph evidence is kept whenever it fits.
- **Real retrieval** on the hashing-embedder index of the PRD-027 Java fixture:
  - matched files rank above related files;
  - under a binding budget (junit-upgrade) the golden `pom.xml` stays full, and no graph file keeps a better tier than a direct hit.
- **Identity:** changing the ranking changes the digest.

**Mutations: 5 run, all killed** after one change. The within-class normalization first survived, because the fixture's graph weights happen to be ≤ 1. The any-scale test was added, and it kills that mutation.
