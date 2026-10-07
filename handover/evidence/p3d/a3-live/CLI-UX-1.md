# CLI-UX-1 (recorded, deferred by owner 2026-10-06; no CLI change)

`-f` relative goal path resolution causes a pre-run refusal; fail-closed.
Observed (MEASURED, A3 post-P3-D attempt 0): `run-p3d.sh` cd's into the workspace before `kriya generate -f
goals/...`; Click's `Path(exists=True)` resolves the relative path against the process CWD (the workspace), finds
nothing, exits 2 before any Kriya code runs: no run id, 0 model calls, no candidate, no M1 store, no workspace change.
TRACED: Kriya resolves `-f` against the CWD (standard CLI behaviour); the immediate trigger was the harness changing
CWD. Owner classification: not a live run; the absolute-path invocation 20261006T171117-866c46d9 is the A3 run.
Mitigation in use: the live runner is always given an absolute goal path.
