"""Attempt evidence recorder (LR-R1-M1).

An append-only, hash-chained, per-run store of what each model attempt was
asked, shown, returned and judged. Observational only: no Kriya decision
reads it, and a recorder failure never changes a run (design invariant I-2).

Production code imports ``scope`` (the emit facade, M1.2+). Consumers read
through ``reader`` only (invariant I-1).
"""
