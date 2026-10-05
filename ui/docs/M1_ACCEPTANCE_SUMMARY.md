# Kriya GUI M1: acceptance summary and merge readiness (2026-10-05)

Branch `codex/fix-demo1-attribution` in the Antigravity checkout. Three kinds of evidence are kept apart below: automated
(this session, fixtures or the real exports), owner's terminal (the owner ran and reported), and owner-performed manual checks.

## 1. Acceptance status

| item | evidence | result |
|---|---|---|
| Full Kriya suite | owner's terminal, two workers with `--dist loadgroup` | PASS: 8,482 tests, 14 warnings, 0 failures (owner-reported) |
| Full UI check (`npm run check`: fixtures, generated-output drift, typecheck, lint, shared-deps, every workspace's tests, Java round trip) | automated, fixtures | PASS: 209 tests at the time of the readiness run; later batches re-ran the affected workspaces one worker at a time (shared 66, kup 66, test-host 36 + 2 skipped without exports) |
| First real-store acceptance, steps 1-9 (`POST_MATRIX_READINESS.md` §7) | automated against the owner's store: capabilities, two explicit acquisitions, digest verification, pinned browsing, freshness fields, snapshot switch, panels rendered in jsdom from the real exports, offline tools on the real exports | PASS, all criteria; source database byte-identical throughout; details in `REAL_STORE_ACCEPTANCE_RESULTS.md` §1 |
| Manual walkthrough on the real store (pinning and switching, Refresh without acquisition, panels for two real runs, keyboard, VoiceOver spot checks) | owner-performed, `MANUAL_WALKTHROUGH_SESSION_2026-10-05.md` | PASS (owner's record and confirmation; no observations beyond the confirmation were supplied) |
| Step 10, workspace assessment | - | NOT PERFORMED: no recovery workspace has been authorized. Neither passed nor failed; it does not block the completed history-inspector checks |
| Accessibility checklist (`A11Y_MANUAL_CHECKLIST.md`) | owner-performed in part (K2 partial, K3, K5, K6, K7, K9, V4, V10 via the session sheet) | partially performed; the remaining items and the narrow-window section stay pending |

Open fixture-fidelity items, documented and not blocking: `retrieved_chunks`/`active_skills` exposure (`RETRIEVED_CHUNKS_AND_FIELDS.md`,
a separate contract topic); the first acquisition of a quiet store reports a metadata difference (wording fixed to say so).

## 2. Kriya-side hand-off: seven commits, in order

| # | commit | content |
|---|---|---|
| 1 | `db96b12` | KUP snapshot read adapter (`kriya/kup/`), acquisition tests, write-site audit row |
| 2 | `f4bd13f` | `traces --json` / `runs status --json --kup-version 1` grammar, goldens, sandbox write-boundary test, evidence |
| 3 | `f4688ea` | `snapshot.verify` |
| 4 | `8065005` | event contract pinned to `RunEvent.to_dict` on the adapter path |
| 5 | `f876f49` | write boundary under the production launch environment, bytecode negative control, GUI/CLI parity |
| 6 | `b531404` | explicit child cwd makes configuration/store resolution independent of the host's launch directory |
| 7 | `3669717` | PRD-034 certification test asserts outcome counts; `xdist_group` marker registered (independent of 1-6) |

Each touches only `kriya/`, `tests/`, `handover/` or `pyproject.toml`; no `ui/` path. Series baseline `61a867f` (= matrix arm A).

## 3. Main repository (inspected read-only, 2026-10-05)

- Checkout `/Users/sriramnanduri/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode`, branch `feature/cagc-r1`, HEAD `0e12535`
  (2026-10-03), 0 modified tracked files, 27 untracked (not inspected). `kriya/kup/` absent.
- Merge-base with the series baseline `61a867f`: `0bea22f`. No change on the `feature/cagc-r1` side touches any path the seven
  commits touch (`kriya/cli.py`, `kriya/kup/`, `tests/_kup_fixtures.py`, `tests/test_file_integrity_contract_001.py`,
  `handover/FILE_INTEGRITY_CONTRACT_001.md`, `tests/golden/kup/`, `pyproject.toml`, `tests/test_prd034_certification.py`).
- **Dry run (MEASURED):** main HEAD `0e12535` was fetched into THIS checkout and the seven commits were cherry-picked in order onto
  it in a throwaway worktree: all seven applied cleanly (no conflict), 23 files, +3,436 / -9 lines; `ruff check` and
  `pylint kriya plugins/core_tools tests` both exit 0 on the merged tree. The KUP/certification test result on the merged tree is
  recorded in `KUP_MERGE_INSTRUCTIONS.md` §2. Nothing was written in the main repository.

## 4. Exact post-merge verification (main-repository session; `.venv` there has pytest-xdist 3.8.0)

1. `git cherry-pick db96b12 f4bd13f f4688ea 8065005 f876f49 b531404 3669717` on the chosen target branch, clean tree (after
   `git fetch <this checkout> codex/fix-demo1-attribution`).
2. `.venv/bin/pylint kriya plugins/core_tools tests` and `.venv/bin/ruff check .` must exit 0.
3. `.venv/bin/pytest -q tests/test_kup_acquisition.py tests/test_kup_cli.py tests/test_kup_write_boundary.py tests/test_kup_host_environment.py tests/test_traces_command.py tests/test_prd034_certification.py`
   (the sandbox test needs macOS `sandbox-exec`; the goldens under `tests/golden/kup/` are byte-identical text outputs).
4. `ulimit -n 256; .venv/bin/pytest -q -n 2 --dist loadgroup` (full suite).
5. Keep `tests/_kup_fixtures.py::host_child_env` identical to `ui/standalone/src/main/child_env.ts`.
6. Do not push (D-10). The UI stays in this checkout; the Electron host is pointed at the merged install only after the owner
   switches the executable setting.

## 5. Still held / not authorized

Workspace assessment (step 10) until the owner names a workspace or chooses to leave it unset; recovery execution; model runs;
the merge itself (owner-coordinated with the main-repository session); GitHub push (never).
