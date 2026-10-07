# Incident: milestone replay first produced an empty decision result (verification tooling defect, corrected)

Owner of the defect: the milestone replay script written for this verification (milestone_replay.py), not Kriya.

## Observation (MEASURED)

The first replay run on be3cbb2 completed with exit 0 and printed the candidate score (acceptance 5/5, external 5/5,
regression 76/76) but an **empty** `regression_decisions` list — no sealed regression decision was re-decided.
That run's JSON output was overwritten by the corrected rerun and is not preserved; the empty list was observed in the
console summary.

## Root cause (CONFIRMED by reading the code)

`regression_replays()` assigned `records = run.records()`, a one-shot iterator over the sealed M1 records. The first
comprehension (collecting the `gate.result` test runs) consumed it; the second comprehension (finding the
`regression.decision` records) then iterated an exhausted iterator and found nothing. The loop body never ran, so the
function returned `[]` — which the script reported as a normal result. An empty result could have been read as "no
blocking decisions", i.e. a vacuous PASS.

## Correction

1. `records = list(run.records())` — the records are materialised once and read twice.
2. Guard: `assert results, 'no regression.decision record found - refusing a vacuous pass'` — an empty decision list
   now fails the replay instead of passing.

## Re-measurement

The corrected script re-decided both sealed decisions (seq 72, seq 138), each asserted faithful to the sealed
observations, and reproduced the live REG-R2 verdicts exactly (see REG-R2-REPLAY.md). The candidate score was
unchanged (5/5, 5/5, 76/76).

## Why it is recorded

The certification summary would otherwise hide a defect in the verification tooling that was discovered and
corrected during milestone verification. The committed milestone_replay.py is the corrected version.
