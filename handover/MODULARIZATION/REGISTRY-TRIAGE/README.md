# Registry triage evidence (owner decisions 2026-10-19: zero-known-gap completion, Option C)

PROVISIONAL SIZING, not final closure (owner). Source of truth for every classifier: the frozen snapshot of commit 8e0c90a (git archive); the orchestrator's accepted-graph checks use 42d0e70 (E14 baseline).

- `SIZING_REPORT.md` - counts, every Class A row with its closing unit, conditional E rows (regenerate with `make_report.py` after `consolidate.py`).
- `FINAL_CLASSIFICATION.json` - one object per row (76 registry rows + derived rows), final_class, verification depth, note, closing unit.
- `raw/out_*.json` - the four first-pass classifiers (Opus, read-only); `raw/adv_out_*.json` - THREE BLIND adversarial reviews of the 43 proposed B / C / E rows (given the row and the defect definition only, not the conclusion; they overturned three: SUREFIRE-RENDERER C -> A, PLAN-FORM B -> A, HITL-RESUME-DEFERRED-GATES B -> A, each reproduced by the orchestrator); `raw/allow_out_*.json` - the three allowlist ownership classifiers (76 target modules / 225 P2 rows).
- `orch_review_*.md`, `orchestrator_findings_2026-10-19.md`, `NEW_FINDINGS_FROM_ADVERSARIAL.md` - the orchestrator's independent review (what was reproduced by probe, read in code, or taken from evidence).
- `OWNER_COHESION.md` - why R6 / R7 / R8 were split into owner-coherent units S1-S17.
- `ALLOWLIST_FINAL_TABLE.md/.json`, `ALLOWLIST_BATCH*_NOTES.md`, `combined_sim.py` - the per-target allowlist plan and the combined simulation (on the accepted E14 graph: import violations 370 -> 234 by the 36 public-contract declarations alone, 0 new, 0 reason changes). The allowlist and baseline are NOT mutated until the combined plan is reviewed (owner).
- REMAINING RULE: B / C / E rows are re-verified by a blind adversarial reviewer before their final closure; Class A units reproduce or trace first.
