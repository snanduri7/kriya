# MLX / oMLX vs Ollama benchmark spike

Question: on this machine (M1 Max, 64GB), how does local inference performance
compare between:

1. **Raw MLX** (`mlx-lm`, direct in-process `mlx_lm.generate`)
2. **oMLX** (`omlx` — local inference server exposing an OpenAI-compatible
   `/v1/chat/completions` endpoint, https://omlx.ai, repo `jundot/omlx`)
3. **Ollama** running `qwen3-coder:30b` (the baseline Kriya already targets)

Same underlying model on both MLX paths: `mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit`,
which shares the same base model (`Qwen/Qwen3-Coder-30B-A3B-Instruct`, `qwen3_moe`
architecture, 30.5B params) and roughly the same quantization level as Ollama's
`qwen3-coder:30b` tag (18GB) — chosen for a comparable rather than identical setup.
**These are two independently-produced quantized artifacts, not the same file**: Ollama
uses GGUF Q4_K_M (llama.cpp block quantization) run through llama.cpp; the MLX build uses
MLX's own affine 4-bit quantization (group_size=64, with `mlp.gate`/router layers kept at
8-bit — visible in its `config.json`) run through Apple's MLX kernels. Same lineage and
architecture, different quantization algorithm and different runtime, so outputs will
differ even at temperature 0 — that's why `speed_bench.py` records `output_text` for both
sides instead of assuming numerical equivalence. Nothing in this spike cross-checks weight
equivalence against the Ollama blob; the model directory is identified purely by its own
`config.json` (`model_type: qwen3_moe`, matched against `mlx_lm`'s model registry).

This is a standalone spike, not wired into `kriya/`. It uses its own venv
(`spikes/mlx_benchmark/.venv`) so it doesn't touch Kriya's own dependencies.

## oMLX due-diligence notes (2026-08-13)

Checked before installing, since it's third-party software that runs as a local server:

- Legit, active project: `jundot/omlx`, Apache-2.0, 18.6k stars / 1.6k forks, created
  2026-02-13, latest release v0.5.7 (2026-08-04), still committing daily. Canonical
  source is `jundot/omlx` / omlx.ai — there's an unrelated `qfpr/omlx--` repo floating
  around search results, not that one.
- Binds to `localhost` by default, no telemetry, vendored frontend deps (works offline).
- One open, unresolved security issue ([#1440](https://github.com/jundot/omlx/issues/1440)):
  localhost-first threat model — API keys land in browser localStorage/query strings, no
  `Secure` cookie flag. Acceptable for local-only use; do **not** expose the port to a
  network or enable its multi-Mac SSH/distributed-inference feature without re-reviewing this.
- Real output-corruption bug reports exist, but scoped to specific architectures in what
  I found (hybrid linear-attention models: Qwen3.5/GatedDeltaNet, Kimi-Linear/KDA,
  DeepSeek V4 mxfp4, Mistral3's tokenizer). Qwen3-Coder-30B-A3B-Instruct is a standard MoE,
  not one of those named-affected families — but not a guarantee.
- General crash reports exist at high concurrency / large context in earlier versions.
  Treat as fast-moving beta-quality software, not production-hardened.
- Mitigation: `speed_bench.py` records `output_text` for every backend rather than
  trusting any of them blindly.
- Homebrew's `jundot/omlx` tap is **untrusted** (`brew install` refuses to load it without
  `brew trust jundot/omlx`, which runs third-party install-script code with Homebrew's
  privileges). Installed from source instead — see Setup below — to avoid that trust
  escalation; the tap was removed again (`brew untap`) after finding this out.

## Steps taken

1. `speed_bench.py` — fixed set of code-gen prompts, measures time-to-first-token and
   tokens/sec for (a) raw `mlx_lm.generate`, (b) Ollama `qwen3-coder:30b`, (c) oMLX's
   OpenAI-compatible API.
2. `speed_bench_omlx_only.py` — isolated oMLX-only rerun. The combined 3-backend run in
   step 1 was confounded: `mlx-lm` loading its own 17GB copy in-process, while Ollama
   (24GB resident) and oMLX (16.8GB resident) were *also* both holding the model in memory
   simultaneously — ~58GB of model weights alone on a 64GB machine, causing GPU/memory
   contention that tanked prefill throughput across the board. Freed Ollama's resident
   model (`ollama stop qwen3-coder:30b`) and reran oMLX alone for a clean number.
3. Quality comparison (running one real Kriya `generate` goal through each backend) was
   scoped but not done — same underlying model weights on all three backends means output
   quality differences would mostly be quantization noise, not a meaningful signal, for a
   decent chunk of extra wiring work. Judged not worth it; stopped after the speed result.

## Setup (to reproduce — model/venvs were deleted after this exercise to reclaim ~18GB)

```bash
cd spikes/mlx_benchmark
python3 -m venv .venv
.venv/bin/pip install mlx-lm huggingface_hub
.venv/bin/hf download mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit \
    --local-dir ./models/Qwen3-Coder-30B-A3B-Instruct-4bit
# (huggingface_hub deprecated `huggingface-cli download` in favor of `hf download`
# partway through this exercise — use `hf`, not `huggingface-cli`.)

# oMLX, from source (avoids trusting the untrusted Homebrew tap, see above):
git clone --depth 1 https://github.com/jundot/omlx.git omlx-src
cd omlx-src && python3.13 -m venv .venv && .venv/bin/pip install -e . && cd ..
./omlx-src/.venv/bin/omlx serve --model-dir ./models --port 8000
```

Requires `ollama serve` running with `qwen3-coder:30b` already pulled (`ollama list`).
Only run one backend's model resident at a time (see step 2 above) or numbers will be
confounded by memory/GPU contention.

## Results

Raw JSON per run in `results/`. M1 Max, 64GB RAM.

**Generation throughput (tokens/sec)** — close across all three, Ollama marginally ahead:

| prompt | mlx-lm | ollama | omlx |
|---|---|---|---|
| short_fn | 69.9 | 73.9 | 72.7 |
| medium_refactor | 69.1 | 73.3 | 69.3 |
| longer_task | 67.1 | 71.9 | 66.1 |

**Prompt/prefill throughput (tokens/sec)** — the real finding, isolated/clean numbers:

| prompt | tokens | mlx-lm | ollama | omlx |
|---|---|---|---|---|
| short_fn | 37 | 24.0 | 182.5 | 54.3 |
| medium_refactor | 55 | 97.8 | 270.2 | 117.9 |
| longer_task | 91 | 110.3 | 398.2 | 155.9 |

Ollama/llama.cpp's prefill is 2.5-3.4x faster than oMLX and 4-8x faster than raw
`mlx-lm`, consistently across prompt sizes. Generation speed is a wash. Since Kriya's
prompts are RAG-context-heavy (`build_code_context`) relative to output size, this
prefill gap likely dominates real end-to-end latency more than the close generation-tps
numbers suggest — **Ollama is the better backend for Kriya's workload shape on this
hardware, as currently configured.**

Untested caveat: every oMLX call in this benchmark had `cached_tokens=0` — each prompt
was a fresh, non-overlapping single-turn request, so oMLX's paged-SSD prefix-caching
(its main differentiator) was never exercised. Kriya's retry loop resends the same large
context across fallback-model escalations, which is exactly that scenario — untested here,
and would need a repeated-prefix benchmark to evaluate. Not pursued (see Steps taken §3).
