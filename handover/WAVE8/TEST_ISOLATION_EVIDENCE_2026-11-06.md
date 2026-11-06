# Test-isolation evidence: diagnostic probes that touched real operator state (2026-11-06)

Process evidence only. NOT classified a product defect: no reproducer shows product ownership (owner ruling 2026-11-06).

1. **Chunk-5 reconciliation agent (OI-156 probe).** A diagnostic `LaunchSession.open()` wrote one launch record into the REAL `~/.kriya/authority/launches/<hash>/` (the authority home ignores `KRIYA_STATE_DIR`). The agent deleted the file and the two empty directories it created (`~/.kriya/authority/launches` did not exist before). Later probes used a scratch HOME.
2. **Chunk-2 agent.** The pytest run for OI-42/59/63 ended with a conftest teardown error ("`~/.kriya/authority` changed during the session", mtime 07:12 inside its run window). Attribution was left UNKNOWN by that agent; probable cause: item 1 (INFERRED, not confirmed).
3. **Real-backend UI session (this session, 07:17-07:22 on 2026-11-06).** `kriya generate -y` ran with `KRIYA_STATE_DIR` pointing at a disposable directory but the REAL HOME, so Kriya wrote logs under `~/.kriya/logs` and may have written to `~/.kriya/authority` (authority home) and read `~/.kriya/operator`. The disposable workspace and state directory held everything else. Entries from that run in the real homes are listed in the "Measured at write time" section below.
4. **Rule going forward.** Every diagnostic probe, agent brief and real-backend session uses a scratch `HOME` plus explicit state, log and authority homes unless the test explicitly requires real operator state. Environment traps found while planning the Wave-8 correction campaign: the `kriya` console script of the shared venv is an editable install of the OTHER worktree (reproducers must run as `python -m` with `PYTHONPATH` set to the worktree); `plugins`/`skills` directories also resolve per install.

## Measured at write time (2026-11-06)
- `~/.kriya/authority` directory mtime 07:12 (before my 07:17 run, inside the chunk-2/chunk-5 agents' window): consistent with item 1, not with my run (MEASURED mtime; attribution INFERRED).
- My run left `~/.kriya/logs/kriya.log` appended (mtime 07:22) and a per-run directory under `~/.kriya/logs/runs/` stamped 07:17 (the run id 20261106T071707-bfac5023). Both are ordinary Kriya log output of an operator run; nothing else of that run was written outside the disposable directories that was checked.
