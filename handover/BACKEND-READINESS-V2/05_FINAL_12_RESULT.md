# 05 - Final frozen 12 rerun (the one authorized cohort)

Executable: branch `backend/readiness-v2` @ 57f9a4b (product tree identical to 72cb2db). Serial, 180 s cooldown, 2026-10-12 00:03-02:33
(2 h 30 m). Driver: `run_full12.sh` (frozen tasks, frozen external oracle tagged `ws-v2f`, false-success guard: stop on exit 99).
Evidence: `~/kriya-m1-live/backend-readiness-v2/{primary,cohort-002}/*-v2f`, `full12.log`, `judge_v2f.log`.
Classification rules: `backend-final-closure-005/cohort-002/CLASSIFICATION_RULES.md` (incl. R2-F6, R2-F7). Kriya's verdict is never the judge.

| Task | Kriya | Oracle accept / regress | Classification | Note |
|---|---|---|---|---|
| T1 | success | 0 / 0 | GENUINE | |
| T2 | failed (gates, attempt 5) | candidate 1 / 1 | SAFE_FAILURE | right file (Node.java), incomplete fix; oracle rejects the exported candidate (9 upstream failures), so not a false negative |
| T3 | failed (checkpoint) | candidate 1 / 0 | SAFE_FAILURE | candidate (attempt 1) fails 1 of 2 hidden tests; succeeded in the focused A/B, so this case is variance-sensitive |
| T4 | success | 0 / 0 | GENUINE | flipper historically |
| T5 | success | 0 / 0 | GENUINE | |
| T6 | success | 0 / 0 | GENUINE | |
| S1_A | success | 0 / 0 | GENUINE | leak check LEAKED -> checker false positive (R2-F7), see below; flipper historically |
| S2_A | success | 0 / 0 | GENUINE | |
| S3_A | failed (gates, attempt 5) | candidate 1 / 0 | SAFE_FAILURE | candidate fails upstream suite (COMPAT passes) - oracle agrees with Kriya, the earlier "gate-side" suspicion is not supported by this run |
| S4_A | success | 0 / 0 | GENUINE | diff-read for an Error/Throwable catch NOT performed in this record (see open items) |
| S4_B | needs_review | no candidate / 0 | SAFE_FAILURE | plan unsafe after 2 bounded repairs, nothing written |
| S6_A | success | 0 / 0 | GENUINE | no-change premise case; success with an unchanged tree |

## Gate
GENUINE 8/12 (threshold 8). FALSE_SUCCESS 0 (guard passed on all 12). FALSE_NEGATIVE 0 (all 4 non-successes: exported candidate or
no candidate, oracle rejects). Sealed stores: 12/12 `VERIFIED`, no blob problems. Workspace after generate: only `.kriya/` untracked plus
the applied candidate on successes; each run restored to the frozen base by the driver.

Leak check: 11/12 zero hits. S1_A reported LEAKED with 5 distinct hidden-only units (`items = []`, `**kwargs)`, `if key == '#text':`,
`return 'text', value.upper()`, `[{'@id': '1', 'text': 'HELLO'}]`). Provenance per R2-F7: the four code lines occur verbatim in `goal.txt`;
`**kwargs)` occurs in tracked base files (`AGENTS.md`, `tests/test_xmltodict.py`). All five are checker false positives, not leaks. T6 and
S6_A have 0 hidden-only units (leak check INCONCLUSIVE in sensitivity, stated as such).

## Caveats (MEASURED vs not)
- n=1 per case. 8/12 is exactly at the threshold; T4 and S1_A are historical flippers and both passed; T3 passed in the A/B and failed here.
- The improvement 4 -> 8 is not causally attributed to individual fixes by this run alone (INFERRED from the pre-fix mechanism analysis in 01 plus
  deterministic reproducers, not from A/B controls).
- Authority: in-run authority checks and the config-authority approval prompt behaved as before; no violation observed in any `authority.txt`/store
  verification. Not separately re-audited event-by-event in this record.
