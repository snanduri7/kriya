# PROVIDER-CONTRACT-001 closure record

**Status: CLOSED (2026-09-30).**

## Product revision

| Item | Value |
|---|---|
| Product HEAD (certified) | `1200824c078eae38fdff5b9ebe56f45be4db700f` |
| Remote `origin/milestone-decomposition` after push | `1200824c078eae38fdff5b9ebe56f45be4db700f` (fast-forward from `853aa43`, 11 commits, no rebase, no squash) |
| Release tag | none |

This record is in a later, evidence-only commit. The executable revision the gates ran on is `1200824`.

## Gates run on 1200824

| Gate | Result | Evidence |
|---|---|---|
| Full pytest (`ulimit -n 256`, the operator terminal's limit) | 7555 passed, 0 failed, 0 skipped, 72 deselected (live tiers); 162 warnings (previous baseline about 179); 0 ResourceWarnings | `full_pytest_1200824_ulimit256_tail.txt`; per-test descriptor counts in `fd_1200824_256.tsv` (start 13, end 16, peak 99) |
| `doctor --production` with the successor config | `production_ready: true`; `model.connectivity`, `model.runtime_fingerprint`, `model.provider_contract` and `model.qualification` PASS | `doctor_production_v3.json`, `doctor_production_v3.stderr.txt` |

Doctor also has five WARN rows, all about the environment:
- `persistence.traces`: a legacy traces.db notice.
- `toolchain.required`: the validation workspace is empty.
- `models.role_independence`: not required.
- `semantic.precision_boundary`: an informational notice.
- `runtime.fixed_guarantees`.

The earlier full run at `e10935b` (no descriptor limit) had 7554 passed and 1 failed (`test_lsp.py::test_initialize_timeout_releases_data_dir_process_and_reader`). The test itself had a pid-file race; it was fixed in `1200824` (test-only). See `full_pytest_e10935b_tail.txt`.

## Production operator configuration

| Item | Value |
|---|---|
| Successor config | `~/.kriya/operator/provider-contract-v3-production.yaml` |
| SHA-256 | `667831f068eecdc1532868f530225dbdac8160129997f1949a58d2dd3f75e0d8` |
| SEC-009 set digest | `4e0069863a5f2a7508fe193218a34f2d1de855368958705b312e81c800289fde` (artifact digest `f878b7b1…85e5d1`, workspace `d99ffc6f…1ba3e`, approved 2026-09-30T12:36:33Z) |
| Previous config `~/.kriya/operator/prd036-production.yaml` | unchanged, SHA-256 `a5be2e833e123e89e71631e5754a263c7f9b861c6b103ee811d5ba1d5dc61b77` |
| Role output budgets | the current resolved behaviour is preserved, not tuned: planner 8192; developer, developer fallback, architect, reviewer, run_verifier, skill_gap and spec_compliance 16384 |

## Model qualification identities (policy `kriya-qualification/4`, policy digest `sha256:d9ba8162…8ad5e09`)

The inference settings digest for both is `sha256:9e929b038e293a2c9a7caee18d9e0de3f14ff5fcf398d19cf2de977625315e5e`. The settings are version 3: temperature 0.7, `top_p` 0.8, `reasoning_effort: "none"`, server `top_k` 20. The execution environment is Ollama 0.34.4 on Apple M1 Max (metal), environment digest `sha256:14c376d9…63e63d0d`.

| Model | Roles | Runtime fingerprint | Qualification identity | Result |
|---|---|---|---|---|
| `qwen3-coder:30b-kriya-e52213655394` | all seven roles | `4ef4192f5c82c9e344a0cee1335a5423bc3e9755807d967a37451266cfeadacd` | `4bcf44baaf4be45a489ba8d307369533156aa51808e102aaa9086a7b7ef66a10` | QUALIFIED: 18 PASS / 0 FAIL / 1 UNAVAILABLE |
| `qwen3.6:35b-a3b-q4_K_M-kriya-620d4d5ce36a` | developer fallback | `5b32ac7818b221da22f60c8b8e9b4968b0398a3b50bd1332531298afeef3f1ad` | `b3618138d01493dd274ee45ee7d5f7fadb53bc87b7d0598cd394ad7bb01aa1f7` | QUALIFIED: 15 PASS / 0 FAIL / 4 UNAVAILABLE |

UNAVAILABLE cases:
- **qwen3-coder:** `endpoint_restart_semantics`, which would need a live server restart.
- **qwen3.6:** the same case, plus `native_tool_calls`, `multiple_tool_calls` and `tool_argument_integrity`. That binding does not enable tool calling, so those protocols are not required for it.

UNAVAILABLE is never counted as PASS.

## Provider adapter versions

- `/v1` (packaged default): `kriya-openai-compat/3`
- Native `/api/chat` (`inference_runtime: ollama_native`, not the default, not qualified): `kriya-ollama-native/1`

## Scope boundaries

- Graphify canonical run #3: **NOT RUN.** The run #1 and #2 configs and evidence are untouched.
- "Too many open files" in one operator terminal run: **UNKNOWN / NOT REPRODUCED.**
  - The same suite under the same 256 limit on `1200824` passed.
  - Descriptor use is flat (13 → 16).
  - The failing run's revision and log were not recovered. No defect was filed.
- Next work: FILE-INTEGRITY-CONTRACT-001, baseline `1200824`.
