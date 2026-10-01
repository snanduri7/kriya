# MODEL-EVAL-001: Qwen3.8 qualification and Kriya A/B, preparation

**Status:** PREPARED, awaiting user-run live execution (2026-09-26). No live command was run by Claude, and packaged defaults are unchanged.

## User decisions (2026-09-26)

- **Baseline:** three-way. `qwen3-coder:30b` (the packaged default for every role), `qwen3.6:35b-a3b-q4_K_M` (the current fallback) and the candidate `qwen3.8:27b`.
- **Reasoning:** `reasoning_effort: none` on both thinking models.
- **Runs:** 3 timed demo-03 runs per arm, all at 32K.
- **Execution:** the user runs every live command.

## Corrections to the task as written

- **Model tag.** There is no `qwen3.8:27b-q4_K_M` in Ollama. `qwen3.8:27b` is Q4_K_M 27.3B and has the same image ID (`22130167c4c2`) as `qwen3.8:27b-mtp-q4_K_M`. `qwen3.8:27b` is used.
- **Baseline.** Qwen3.6 is not a default for any role; it is the fallback. `qwen3-coder:30b` is the default, so it is a third arm.
- **qwen3-coder and reasoning.** It serves no `thinking` capability, so its arm does not send `reasoning_effort`.
- **Architect.** demo-03's production profile runs enforce mode, which has no Architect call. The Architect comparison therefore comes only from qualification cases.

## Bundle (outside the repo)

`~/kriya-live-demo/demo-03-brownfield/model-eval-001/`: `RUNBOOK.md`, `make-configs.py`, `configs/`, `setup.sh` (approve / qualify / qualify-64k), `run-arm.sh`, `run-all.sh`, `extract-run.py`, `compare.py`.

**Design:**
- **Arm configs** are generated from `config/generate-production.yaml`. They differ only in the model, the memory directory and `reasoning_effort`. `llm_chain: []` stops an arm from escalating to another model.
- **Approvals:** each arm gets its own trust file via `authority approve --out`, outside the workspace. The workspace identity is the path, so approvals survive `reset.sh`.
- **Qualification** runs with its own `KRIYA_STATE_DIR`, so it never reaches the A/B `traces.db`.
- **Every timed run** starts clean: workspace reset, arm index wiped, all Ollama models unloaded (the same cold load for every arm). The runner refuses while pytest is running, and the arm order rotates each round.
- **SUCCESS** requires all three: Kriya's success, a correct diff (the save call inside `delete()`, only the target file), and an independent `mvnw -o` compile and test pass.
- **Promotion signals** in `compare.py` are withheld until 3/3 clean runs per arm, and even then they are inputs to review, not a decision.

**Offline checks done:**
- The configs resolve through `resolve_config_state`.
- `extract-run.py` reproduces today's real qwen3-coder production run (SUCCESS, CORRECT_MINIMAL, 0 retries, 53/53, 81 s of model calls, 228 s of workflow).
- The diff grader marks a fix in the wrong file INCORRECT and a correct fix plus an extra line CORRECT_WITH_EXTRA_CHANGES.
- `compare.py` withholds signals at 2/3 runs and emits them at 3/3.

## Kriya finding, not fixed here: PRD-013 identity gap

`model_runtime` builds the fingerprint using only `num_ctx` from `extra_body`. `reasoning_effort` and the sampling options (`top_p`/`top_k`/temperature) are not part of the runtime identity, and qualification records are keyed by that digest. So:
- a model qualified with `reasoning_effort: none` shares its record with the same model and `num_ctx` without it;
- a config that drops the setting (and so reasons at the template default, e.g. `xhigh` for qwen3.8) would still show QUALIFIED.

The A/B is unaffected, because every thinking arm uses the same setting. Proposed separate fix: include the normalized behaviour-affecting request parameters in the Kriya-side fingerprint inputs, and bump the adapter version.
