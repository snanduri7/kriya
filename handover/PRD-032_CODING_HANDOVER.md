# PRD-032 Coding Agent Handover — Adversarial and Crash Chaos Harness

## Status

**LOCALLY_VERIFIED / READY_FOR_USER_REVIEW** (Wave 7 overnight batch, local and unpushed).
Tracker: `READY_FOR_PYTEST_VERIFICATION`, per the Global Contract; only the user marks VERIFIED.

The coding agent ran the full deterministic suite, the chaos tiers and the live tier itself, under the user's standing permission for this batch. The independent Stage B/C re-runs remain the user's.

## Source identity

- **Base revision:** `4496327` (PRD-031A VERIFIED, pushed).
- **Final revision:** see `handover/OVERNIGHT_WAVE7_SUMMARY.md`. PRD-032 is the commits `7ef2a1a` (harness plus families A–C), `91e474d` (defect fix), `b40b4da` (families D/E, live tier, harness contract) and the evidence/closure commit.
- **Kriya version:** unchanged.

## Scope implemented

- **Instruction files:**
  - `tasks/PRD-032_Adversarial_and_Crash_Chaos_Harness.md`;
  - Wave 7 directive §4;
  - design analysis `handover/PRD-032_DESIGN_ANALYSIS.md`.
- **Requirements completed:**
  1. Reusable hostile model/provider fixtures. `ChaosRuntime` is a real `InferenceRuntimePort` answering per role, registered for one test, so the real LLMClient path (budgeting, PRD-015 normalization, tool-call decoding and argument validation) runs. The fixtures cover:
     - prose around tool calls;
     - partial JSON;
     - duplicate calls;
     - unknown tools;
     - wrong-operation arguments;
     - 1 MiB arguments;
     - malformed anchored edits;
     - traversal and absolute paths (read and write);
     - endless investigation;
     - identical ineffective retries;
     - fabricated success;
     - truncated and empty completions.
  2. Failure injection:
     - model endpoint disappearance;
     - Docker disappearance (a real docker CLI with an unreachable daemon);
     - hung process trees;
     - MCP malformed response and mid-session disappearance (a real adversarial server);
     - I/O error between staged replaces;
     - disk-full while staging;
     - a concurrent source edit;
     - a second Kriya writer (a real second process holding the lock);
     - checkpoint and RunRecord corruption;
     - subprocess `os._exit` at three commit windows (after intent / between replaces / after the source write, before RunRecord settlement);
     - resume from an uncertain state;
     - enforce terminal stale candidate.
  3. Malicious repository prompt-injection fixtures (secrets, network, authority expansion, disabling gates/tests/static analysis, trusted-store writes, fake system/operator messages).
  4. Every scenario asserts one invariant set through the `chaos_case` fixture:
     - typed outcome;
     - no unauthorized mutation (whole per-test tree);
     - no authority expansion;
     - no false PASS;
     - bounded retry (the policy's global attempt ceiling);
     - an auditable RunRecord;
     - safe or explicitly uncertain recovery.
  5. The deterministic cases run in ordinary pytest (marker `chaos`, not excluded). Only real-model cases are `live_model` (L01–L03), and only real-scanner cases are `live_static_analysis` (E08).
  6. A machine-readable, reproducible chaos report: `--chaos-report DIR`, `scripts/chaos_report.sh`, and `tests/_chaos_report.py`, which writes JSON plus Markdown with a content digest. Two runs gave the same digest.
  7. The PRD-031A attack family E runs through the real direct pipeline and the real enforce services.
- **Requirements deliberately not implemented (with reason):**
  - Random fuzzing is not the core, per the directive.
  - No production refactor.
  - ENFORCE-EXECUTE-PLAN-CONVERGENCE-001 is untouched: the chaos suite now characterizes both paths.

## Scenario matrix (51 scenarios)

The report is `handover/evidence/PRD-032/chaos/chaos-report.md` (every tier). It lists each scenario's injected failure, expected invariant, verdict, typed outcome, content-free evidence and identity.

| Family | Scenarios | Notes |
|---|---|---|
| A model/protocol | A01–A13 | investigation loop via real LLMClient; direct pipeline |
| B repository injection | B01–B06 | SEC-009 denials, reader refusals, egress, fence neutralization, goal-only requirements |
| C tool/runtime | C01–C10 | process tree reaped; MCP; Docker; scanner unavailable/malformed/unconfirmed; EIO/ENOSPC in commit; model endpoint gone |
| D commit/recovery | D01–D09 | typed refusals `RUN_RECORD_UNREADABLE`, `UNCERTAIN_COMMIT_STATE`, `UNCERTAIN_RUN_RECORD_COMMIT_STATE`; recover never SUCCESS |
| E PRD-031A | E01–E10 | E08 on real Semgrep 1.178.0; a valid-waiver control case proves E05–E07 are not vacuous |
| L live model | L01–L03 | exact runtime `ea90552d…` QUALIFIED (qwen3-coder:30b) |

## Defect found by the harness and fixed (separate commit `91e474d`)

**Direct/milestone terminal commit failure was retried.**
- **What happened.** `workflow.py` re-raised a non-committed `TerminalCommitOutcome` (revision conflict, rolled-back I/O error, uncertain state, refused static-analysis evidence, unpersisted intent) into the generic attempt-failure path. The Developer was asked again, which could never help, and the run ended with a wrong category (`quality_gates_exhausted`/`no_progress`). Enforce already reported these structurally (PRD-004).
- **Fix.** `_raise_terminal_commit_stop` makes it a deterministic stop:
  - failure type `workspace_commit`, added to retry_strategy's stop set;
  - `failure_category: workspace_commit_failed`;
  - the enforce-parity `workspace_commit_failure` payload;
  - a CLI message (with a `kriya runs recover` hint for UNCERTAIN);
  - docs in design §2.9b and user guide §3.
- **What does not change.** No authority change. No write was ever unsafe; the workspace was unchanged and the RunRecord truthful in both old and new behavior.
- **Regression tests.** `tests/test_prd032_terminal_commit_stop.py` has 4 tests:
  - they fail without the fix (`quality_gates_exhausted` ≠ `workspace_commit_failed`; KeyError on the payload);
  - they cover the failing, success and gate-failure result contracts and the CLI message;
  - all 4 of 4 mutations are caught (stop-set entry, category branch, payload assignment, call site).

## Defect 2 found by the live tier and fixed (separate commit `2f66763`)

**The Planner/Architect prompt carried the absolute workspace path.**

The generation pipeline embedded the repository model as JSON, including `root_path` (the absolute host path). The structured plan schema refuses an absolute planned path. The real model sometimes copied the path into `planned_files`, and Kriya then refused its own prompt's echo (`planner_output_unauthorized_path`, 1 of 5 live runs).

- **Fix:** exclude `root_path` from the prompt JSON (`workflow.py`). Every path a model sees is workspace-relative.
- **Regression test:** `tests/test_prd032_planner_workspace_path.py`. It fails without the fix: the absolute path reaches the planning prompt.
- **Authority:** no change. The schema's refusal is unchanged; the defect was Kriya contradicting its own schema.

## Findings recorded (registry, P3, not blocking)

- **`ARCHITECT-FILE-LIST-ESCAPE-FALLBACK-001`.**
  - What happens: an Architect file list rejected because an entry escapes the workspace falls back to heuristic prose extraction, which re-derives in-workspace basenames (`../outside/evil.py` → `evil.py`, `.kriya/control/runs/forged.json` → `forged.json`).
  - Impact: no write leaves the workspace, and every write stays in the run's recorded plan (A08/B05 assert this).
  - Target: plan/file-list hardening with ENFORCE convergence.
- **`CANDIDATE-VERIFIED-DIGEST-BINDING-001`.**
  - What happens: with static analysis disabled, neither terminal commit binds the committed batch to the digest the gates verified (both re-materialize the candidate).
  - Impact: production seals OCI containment, and every contained command's container is removed before the commit. Uncontained code could already write the real workspace. D09 proves the binding holds when PRD-031A evidence is present.
  - Target: convergence PRD.

## Files changed

- **Production (fix only):**
  - `kriya/workflow/workflow.py`
  - `kriya/workflow/state.py`
  - `kriya/workflow/retry_strategy.py`
  - `kriya/cli.py`
- **Tests:**
  - `tests/_chaos_harness.py`, `tests/_chaos_report.py`, `tests/conftest.py` (report plugin, `chaos_case` fixture);
  - `tests/test_prd032_chaos_{model,injection,runtime,commit,static_analysis,harness}.py`;
  - `tests/test_prd032_terminal_commit_stop.py`;
  - `tests/test_live_prd032_chaos.py`;
  - `tests/test_architecture_regression_index.py` (entry 7).
- **Tooling:** `scripts/chaos_report.sh`; `pyproject.toml` (`chaos` marker).
- **Docs/decisions:**
  - `docs/design.md` §2.9b (direct parity), §2.9e;
  - `docs/user_guide.md` §2.1h and §3 (a commit that did not commit);
  - `CLAUDE.md` (chaos harness);
  - `handover/BACKLOG_REGISTRY.csv` (2 P3).
  - `.eie/DECISIONS.md` is not present and was not created.

## Tests run by the coding agent

| Command | Passed | Failed | Skipped | Notes |
|---|---:|---:|---:|---|
| `scripts/chaos_report.sh <dir> deterministic` (×2) | 47 | 0 | 0 | identical content digest both runs |
| `-m "chaos and not live_model"` (incl. E08 real Semgrep) | 48 | 0 | 0 | |
| `-m live_model tests/test_live_prd032_chaos.py` | 3 | 0 | 0 | L02 once hit a real Planner schema rejection before any Developer request (typed safe stop, path not exercised). It is a FAIL in that run and passed on two re-runs; recorded honestly |
| adjacent suites (prd031a, prd007, prd008a x2, traces, retry policy, prd026, prd004, prd029, bootstrap, qual identity, strict doubles, inf001) | 354 | 0 | 0 | |
| full `.venv/bin/pytest` (venv on PATH) at `b40b4da` | 6774 | 0 | 0 (60 deselected) | 28:23 before fix 2; fix 2 then passed 1065 neighbouring tests. The wave's final full run is in the Wave 7 summary. |
| every tier `scripts/chaos_report.sh … all` at `ae29d0a` | 51 | 0 | 0 | digest `9fe5a8fb…` |

## Static/lint/architecture checks

`.venv/bin/ruff check .` and `.venv/bin/pylint kriya plugins/core_tools tests` report 0 findings at every commit.

## Live test additions

- **Required by instruction:** YES.
- **Test file:** `tests/test_live_prd032_chaos.py`.
  - L01: repository prompt injection.
  - L02: malformed first Developer answer at the runtime port, then recovery.
  - L03: an always-rejecting compile gate, which must be a bounded stop.
- **Environment prerequisites:** local Ollama with the QUALIFIED `qwen3-coder:30b` (32K window). The preflight fails, never skips.
- **Command:** `KRIYA_LIVE_BASE_URL=http://localhost:11434/v1 KRIYA_LIVE_LLM_MODEL=qwen3-coder:30b .venv/bin/pytest -m live_model -ra tests/test_live_prd032_chaos.py --chaos-report <dir>`, or `scripts/chaos_report.sh <dir> all`.
- **Expected invariant:** Kriya's safety state only (the report's L rows).

## Known limitations / residual risks

- The live tier depends on the model. L02 requires the real model to recover, and a real Planner schema rejection can pre-empt the Developer (seen once). That surfaces as a FAIL, never as a pass.
- **E09 note:** after the provider disappears, the commit guard's refusal reason is `STATIC_ANALYSIS_EVIDENCE_STALE` (the runtime fingerprint cannot be recomputed), not a dedicated "provider unavailable at commit" code. It fails closed, and is typed and recorded as observed.
- The two P3 findings above.

## Evidence artifacts

`handover/evidence/PRD-032/`:
- `chaos/` (every tier);
- `chaos-deterministic-rerun/` (reproducibility);
- `pytest.md`.

## Verification-agent handoff

**Pytest agent:**
- `PATH=.venv/bin:$PATH .venv/bin/pytest -q`
- `scripts/chaos_report.sh /tmp/c1 deterministic`, run twice (compare `content_digest`)
- `.venv/bin/pytest -m live_static_analysis tests/test_prd032_chaos_static_analysis.py`

**Live agent:** the live command above. Each L row must PASS with the exact runtime fingerprint recorded.
