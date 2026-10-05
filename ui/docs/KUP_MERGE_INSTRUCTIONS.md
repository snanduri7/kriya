# KUP hand-off: merge instructions for the main-repository session (prepared 2026-10-05; NOT executed)

Source checkout: `/Users/sriramnanduri/WorkingDirectory/AI/AntiGravity/Kriya-By-Antigraviry/tmp/kriya-demo1-attribution-fix-2`,
branch `codex/fix-demo1-attribution`. Target: `/Users/sriramnanduri/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode`
(read-only observations below; nothing was written there).

## 1. The six commits, in order (each re-verified: only `kriya/`, `tests/`, `handover/` paths; no `ui/` path)

| # | commit | parent in source | content | files |
|---|---|---|---|---|
| 1 | `db96b12` | `db16acb` (= gate approval revision) | KUP snapshot read adapter | `kriya/kup/{__init__,acquire,cli_ops,inspect,policy,store}.py`, `tests/_kup_fixtures.py`, `tests/test_kup_acquisition.py`, `tests/test_file_integrity_contract_001.py` (+1 audit row), `handover/FILE_INTEGRITY_CONTRACT_001.md` |
| 2 | `f4bd13f` | `db96b12` | `traces --json` / `runs status --json --kup-version 1` grammar, goldens, sandbox write-boundary test | `kriya/cli.py`, `tests/test_kup_cli.py`, `tests/test_kup_write_boundary.py`, `tests/golden/kup/*.golden`, `handover/evidence/KUP/write_boundary_evidence_2026-10-04.json` |
| 3 | `f4688ea` | `b85d333` (GUI-C commits in between; cherry-pick ignores them) | `snapshot.verify` | `kriya/cli.py`, `kriya/kup/cli_ops.py`, `kriya/kup/policy.py`, `tests/test_kup_cli.py` |
| 4 | `8065005` | `f4688ea` | event contract pinned to `RunEvent.to_dict` on the adapter path | `tests/_kup_fixtures.py`, `tests/test_kup_cli.py` |
| 5 | `f876f49` | `8065005` | write boundary under the production launch environment, bytecode negative control, GUI/CLI parity | `tests/_kup_fixtures.py`, `tests/test_kup_host_environment.py`, `tests/test_kup_write_boundary.py`, `handover/evidence/KUP/*2026-10-04*.json` |
| 6 | `b531404` | `8d6fe45` | child cwd makes resolution independent of the host's launch directory | `tests/test_kup_host_environment.py` |

Baseline of the series: `61a867f` (also matrix arm A). All six are ancestors of the source HEAD.

Seventh Kriya-side candidate (independent of the KUP series, test infrastructure only): `3669717` PRD-034 - the certification test
asserts outcome counts instead of a summary substring and `pyproject.toml` registers the `xdist_group` marker (files:
`pyproject.toml`, `tests/test_prd034_certification.py`). Cherry-pick it after the six; with pytest-xdist installed in the target the
marker registration is redundant but harmless.

## 2. Target state observed (read-only, 2026-10-05)

- Checked-out branch `feature/cagc-r1` at `0e12535` ("CAGC-0 matrix-40 corrections (evidence only) ..."), 0 modified tracked
  files, 27 untracked files (not inspected).
- `61a867f` exists in the target but is NOT an ancestor of `feature/cagc-r1`; merge-base is `0bea22f` (2026-10-03, "Merge
  feature/python-adapters-r1"). Since that merge-base: 13 commits on the `61a867f` side, 10 on the `feature/cagc-r1` side.
- Neither side touched any KUP-touched path (`kriya/cli.py`, `kriya/kup/`, `tests/_kup_fixtures.py`,
  `tests/test_file_integrity_contract_001.py`, `handover/FILE_INTEGRITY_CONTRACT_001.md`, `tests/golden/kup/`) since the
  merge-base, so cherry-picks are expected to apply without conflict onto `feature/cagc-r1`. The old `main` branch tip is
  `3bf89ba` (2026-08-18) and is not a sensible target.
- `kriya/kup/` does not exist in the target yet.

The target branch is the main-repository session's decision; the observations only say that `feature/cagc-r1` is conflict-free
for these six commits as of `0e12535`.

## 3. Steps for the main-repository session (to run there; this session runs none of them)

```
# in /Users/sriramnanduri/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode, on the chosen target branch, clean tree
git fetch /Users/sriramnanduri/WorkingDirectory/AI/AntiGravity/Kriya-By-Antigraviry/tmp/kriya-demo1-attribution-fix-2 codex/fix-demo1-attribution:refs/kup/from-antigravity
git cherry-pick db96b12 f4bd13f f4688ea 8065005 f876f49 b531404     # in this order, one series
```

After the series applies:

1. Static gates (must exit 0): `.venv/bin/pylint kriya plugins/core_tools tests` and `.venv/bin/ruff check .`
2. KUP tests: `.venv/bin/pytest -q tests/test_kup_acquisition.py tests/test_kup_cli.py tests/test_kup_write_boundary.py tests/test_kup_host_environment.py tests/test_traces_command.py`
   (the sandbox test needs macOS `sandbox-exec`; the goldens under `tests/golden/kup/` are byte-identical text outputs).
3. Full suite: `ulimit -n 256; .venv/bin/pytest -q -n 2 --dist loadgroup` (the target's `.venv` has pytest-xdist 3.8.0; without
   pytest-xdist use the sequential `ulimit -n 256; .venv/bin/pytest -q`)
4. Keep `tests/_kup_fixtures.py::host_child_env` identical to `ui/standalone/src/main/child_env.ts` (fixed PATH, fixed
   `PYTHONDONTWRITEBYTECODE=1`, HOME, absolute `KRIYA_STATE_DIR` only); a change to one must change the other.
5. `handover/BACKLOG_REGISTRY.csv`: the series adds no registry rows; the write-site audit row is in
   `handover/FILE_INTEGRITY_CONTRACT_001.md` (commit 1).
6. Do not push (D-10). The UI (`ui/`) stays in the source checkout.

## 4. What this does not do

No merge, fetch, checkout or write in the target was performed from here. Real-store use, model runs and GitHub pushes are not
authorized by these instructions.
