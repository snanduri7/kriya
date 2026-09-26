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
