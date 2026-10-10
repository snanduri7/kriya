# M6 - Minimum execution session (Start Run)

Branch `gui/run-cockpit-v1_1`, on top of M5 `c5d567b`. Scope: Open Folder -> inspect -> goal -> Start Run -> observe -> terminal outcome -> inspect, through the REAL Kriya workflow path. Not built: Stop/Pause/Resume/Retry/Rerun, approval UI, terminal, streaming, arbitrary command execution.

## M6-A trace of the existing execution entrypoint (TRACED at c5d567b)
| Question | Answer (code) |
|---|---|
| goal | `kriya generate GOAL` / `--file` / stdin (`_generate_impl`, kriya/cli.py) |
| workspace | `os.getcwd()` - the process working directory |
| configuration | `load_config()` in the group callback: `kriya.yaml` in the cwd, else the installation, else packaged defaults; SEC-009 classifies; operator-only fields need a durable approval |
| state directory | `KRIYA_STATE_DIR` > `paths.state` > `~/.kriya/state` (independent of the workspace) |
| model/security | `cfg.llm`, `llm_chain`, `agent_llms`; authority resolved at config load, before Kernel/model |
| durable run id | `begin_mutating_run` takes the workspace lock and creates the RunRecord (`<ws>/.kriya/control/runs/<id>.json`) with an id it can be GIVEN; the attempt-evidence store is filed under the same id; the trace row (own 8-hex id) is written only at the END |
| output | `--json`: final result JSON on stdout, narrative on stderr (`GenerateOutput`) |
| enough for progress | yes while running: the evidence store is chained and readable open; not the trace row |
| failure before evidence | config refusal exits before the command (no record possible); anything after is covered by the launch record added here |
| cancellation | only the terminal's SIGINT (KeyboardInterrupt -> RunRecord FAILURE, exit 130) |

## What was added
Python: `kriya/control/launch.py` (+ persistence helpers), `kriya/kup/execution.py`, `generate --launch-id` (hidden), `traces --execution-preflight/--launch-status`, `kup-stdio` arguments, `LAUNCH_NOT_FOUND`, a two-line claim/notice hook in `begin_mutating_run`, `ConfigResolutionState.config_path/config_source`, `evidence_ops.store_summary`.
Electron: `execution_host.ts`, `execution_process.ts`, `ui_mode.ts`, registry run-lock and M1 compatibility, two IPC messages, quit handling.
UI: `core/execution.tsx` (state machine, polling, outcome derivation), `RunPanel`, `ActiveRunBanner`, switcher lock, `FixtureExecution` (test only).
Tests: `tests/test_kup_execution.py`; standalone `execution_host`, `execution_real` (real Kriya, controlled body), hardening, workspace_host, ui_mode, contract; shared `execution.test.tsx`.

## Controlled executor
`ui/standalone/test/fixtures/controlled_kriya.py` is a test-only wrapper: the real CLI, preflight, launch record, run coordinator, evidence recorder and trace logger; only the model-calling pipeline is scripted. Never shipped; the production-bundle check forbids the browser-side fixture too.

## Owner decisions surfaced (not taken)
1. Auto-approval: a GUI run has no interactive approval and passes no `-y`; an escalation or knowledge-gap stops the run. Add an explicit approval action later, or allow a narrowly scoped auto-approve?
2. Interrupt-on-quit uses SIGINT; a Stop button would use the same mechanism but its partial-commit safety needs evidence first.
3. Toolchains outside the fixed child PATH (sdkman Java etc.) are not visible to a GUI run.
