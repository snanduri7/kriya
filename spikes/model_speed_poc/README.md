# Spike: which pulled model is actually fastest for Kriya-shaped code-gen -
and does qwen3.6's reasoning mode earn its cost?

## The question this tests

Three models are already pulled locally: `qwen3.6:27b` (Kriya's default,
verified live to be a real dense 27B model, competitive with much larger
MoE models on coding benchmarks per its own Hugging Face model card),
`qwen3-coder:30b`, and `qwen3.6:35b-a3b-q4_K_M`. Given they're all sitting
on disk already, which one is actually fastest for a code-gen-shaped prompt
on this machine, right now, under the current Ollama server settings
(`OLLAMA_KV_CACHE_TYPE=q8_0`, `OLLAMA_FLASH_ATTENTION=1` - already active on
the running `ollama serve` process, confirmed via its process environment)?

`qwen3.6:27b` and `qwen3.6:35b-a3b-q4_K_M` are also native "thinking"
models - confirmed live via raw `curl` traces against both Ollama's native
`/api/chat` (`message.thinking` field) and its OpenAI-compatible
`/v1/chat/completions` (`delta.reasoning` field). Kriya de-prioritized
"enabling reasoning" once before (see `kriya_backlog_and_lessons.md`
memory): a direct A/B on a *fact-recall-class retry error* showed zero
correctness benefit at 13x the latency. That result doesn't necessarily
generalize to every task shape, and today's models signal reasoning
differently (a real structured field, not inline noise) - so this spike
also runs a proper reasoning-on vs reasoning-off A/B per prompt for the two
thinking-capable models, to get real evidence before deciding whether to
revisit that decision for any Kriya phase. See `bench.py`'s module
docstring for why the *first* pass at this (if you're looking at old
results/ output or git history) doesn't count as that A/B - it accidentally
only ever tested "thinking on, starved for token budget," never
thinking-off, and never gave reasoning a fair budget to finish.

## What this does NOT test

- **A/B of q8_0 vs f16 KV cache.** This only measures the *current* active
  setting (q8_0) across models, not a before/after on the setting itself -
  that would need restarting the Ollama server between runs, out of scope
  for this pass.
- **Deep code-quality/correctness across the board.** Only `short_fn`
  (`is_palindrome`) gets a real functional-correctness check via an
  executed assertion snippet - its spec is fully unambiguous. The other two
  prompts (`LRUCache`, `TaskQueue`) only get an `ast.parse()` syntax check,
  because their prompts don't pin down edge-case behavior precisely enough
  (e.g. what should `LRUCache.get()` return on a missing key?) for a strict
  assertion to mean "model got it wrong" rather than "model chose a
  different reasonable interpretation." See `prompts.py` for the exact
  reasoning.
- **Multi-turn/agentic behavior.** Single-shot prompts only, not
  representative of the full Developer-agent retry/context-budget loop in
  `kriya/workflow/workflow.py`.
- **Planner/Architect-shaped reasoning benefit.** These prompts are all
  Developer-loop-shaped (write a function/class/module), the phase where
  reasoning is *least* likely to help per the prior negative result. This
  spike answers "does thinking cost latency/produce different output here,"
  not "would reasoning help Kriya's Planner or Architect phase" - that's
  the actual open question for the next backlog item, deliberately not
  answered here.

## Two more models wired in, not yet pulled or run (2026-08-15)

- **`qwen3.8:27b` / `qwen3.8:27b-mlx`** - Qwen3.8-27B released 2026-08-14,
  ~24h before this was added. Both tags are real, official entries in
  Ollama's own library (confirmed via web search, not run locally - nothing
  about this model has been independently verified yet, only vendor/launch-
  day claims exist). Added to `THINKING_MODELS` on the strength of its own
  description ("flexible thinking control"); unlike qwen3.6, this hasn't
  been confirmed via a real `curl` trace to actually expose
  `message.thinking`/`delta.reasoning` the way qwen3.6 does - if it
  doesn't, the `think:true` arm just won't differ from `think:false`,
  it won't error.
- **`qwen3-coder:30b-a3b-q8_0`** - the Q8_0 build of the exact same model
  already in this spike (`qwen3-coder:30b` is confirmed Q4_K_M via `ollama
  show`, both are the `qwen3moe` architecture, 30.5B total/~3.3B active
  params). Added for a direct, same-harness Q4-vs-Q8 accuracy/speed
  comparison - MoE models are documented as more quantization-sensitive
  than dense models specifically because low-bit quantization distorts the
  router's expert-selection logic, so unlike a dense model this comparison
  has real reason to show something, not just theoretical bit-depth noise.
  32GB on disk vs 18GB for the Q4_K_M build.

`ollama pull <tag>` any of the three before running, or remove them from
`MODELS`/`THINKING_MODELS`/`NON_THINKING_MODELS` in `bench.py` to skip.

## Run it

```bash
cd spikes/model_speed_poc
python3 bench.py                              # every model in MODELS
python3 bench.py qwen3-coder:30b-a3b-q8_0      # only this one
```

Standard library only - no venv/deps needed. Requires `ollama serve`
running with the models in `MODELS` (`bench.py`) already pulled (`ollama
list` to check - see the two-more-models section above for what's new and
unpulled as of this version). Each model is loaded into VRAM fresh on first
use; expect this to take a while for the 27B/30B/35B models (a warm-up call
is issued per model, untimed, before the measured runs specifically so
first-prompt numbers aren't skewed by model-load time on top of generation
time). Every thinking-capable model runs every prompt twice (`think:false`
at the prompt's normal token budget, `think:true` at that budget plus
`REASONING_HEADROOM_TOKENS` extra so reasoning has room to actually
finish) - with 4 thinking-capable models now instead of 2, expect this run
to take considerably longer than either prior pass.

Writes a timestamped JSON file to `results/` (full output text + thinking
text + all metrics per model/mode/prompt) and prints a summary table:
`model | mode(no_think/think/n/a) | prompt | ttft_content | total_s | gen_tps | think_tok~ | cont_tok~ | syntax | func`.

**For comparing speed across models/quant levels, use `gen_tps`/`total_s`,
not `ttft_content` or `cont_tok~`.** `ttft_content` is prefill+first-token
latency - noisy for a short prompt, and NOT governed by the same memory-
bandwidth economics as sustained decode, so a bigger/higher-precision model
can legitimately show a *lower* ttft than a smaller one without anything
being wrong (confirmed live: Q8_0 showed lower ttft than Q4_K_M for the
same qwen3-coder:30b-a3b prompts, while its `gen_tps` was correctly lower
as expected - 49-51 tok/s vs 50-64 tok/s). `think_tok~`/`cont_tok~` are
this script's own chunk-count approximation of the reasoning/content token
split, confirmed to undercount Ollama's real `eval_count` by ~15% in every
run so far (both quant levels) - a real gap in the approximation itself,
not something specific to any one model. `gen_tps`/`total_s` come straight
from Ollama's own `eval_count`/`eval_duration` and aren't affected by
either issue.

## Safety note

`short_fn`'s functional test executes the model's generated code via
`subprocess` (10s timeout, isolated from this script's own process) to
check real correctness - not `exec()` in-process. This is locally
generated code from a local model on your own machine, the same trust
boundary as running any other script in your own dev loop; not something
that would be safe to do with untrusted/remote model output.

## MTP (multi-token prediction) tag comparison - result and verdict (2026-09-04)

**Question:** `qwen3.6:35b-a3b-mtp-q4_K_M` is a separately-tagged Ollama
build of the same qwen3.6-35b-a3b model. Does its MTP/speculative-decoding
support actually deliver a real speedup on this machine (M1 Max, Ollama
0.33.2), and does it match the "~2x faster" claims circulating for MTP on
Apple Silicon?

**Setup:** `qwen3.6:35b-a3b-mtp-q4_K_M` added to `THINKING_MODELS` in
`bench.py` (see that file's own comment block for the Modelfile evidence
that motivated it - `PARAMETER draft_num_predict 2`, absent from the plain
tag). Run via:
```bash
python3 bench.py qwen3.6:35b-a3b-q4_K_M qwen3.6:35b-a3b-mtp-q4_K_M
```
Result: `results/bench_20260904T051757Z.json`.

**Blob structure (confirmed via `ollama show <tag> --modelfile` + `ls -lh`
on the underlying blob files under `~/OllamaModels/blobs/`):** the plain
tag is a single 22G blob (`FROM` line only). The `-mtp-` tag is a 20G main-
model blob **plus a separate 861M blob**, passed to `llama-server` via
`--mmproj` - not classic two-model speculative decoding with an
independent draft LLM, but Ollama loading the model's own embedded MTP
head as a draft context against the same target model (confirmed in
`~/.ollama/logs/server.log`: `common_speculative_init_result: creating MTP
draft context against the target model ...`, launch flags `--spec-type
draft-mtp --spec-draft-n-max 2 --spec-draft-backend-sampling`).

**MTP genuinely engaged** - this is not a case of the parameter being
silently ignored. `~/.ollama/logs/server.log` (macOS Ollama.app) carries
per-request telemetry:
```
grep -i "draft\|speculativ\|mtp" ~/.ollama/logs/server.log | tail -60
```
showed real draft-acceptance rates of 70-93% across the three prompts,
mean accepted draft length ~2.4-3.0 tokens per cycle (max possible = 2
drafted + 1 verified = 3, given `spec-draft-n-max=2`) - a genuinely good
acceptance rate, not a misconfiguration.

**Result: net SLOWER despite good acceptance**, matched pair by pair
(`generation_tps`, straight from Ollama's own `eval_count`/`eval_duration`):

| prompt | mode | plain tps | mtp tps | delta |
|---|---|---|---|---|
| short_fn | no_think | 62.3 | 72.1 | +15.7% |
| short_fn | think | 60.9 | 49.9 | -18.1% |
| medium_refactor | no_think | 61.8 | 56.2 | -9.1% |
| medium_refactor | think | 60.5 | 46.1 | -23.8% |
| longer_task | no_think | 61.7 | 49.3 | -20.1% |
| longer_task | think | 57.5 | 44.6 | -22.4% |

5 of 6 matched pairs slower under MTP; the one win is the shortest
possible response (37 tokens), the least reliable data point. `ttft`
showed no consistent MTP advantage either. One secondary, non-conclusive
data point: `medium_refactor`/`no_think` was the only syntax failure among
the no_think rows, on the MTP model - noted, not treated as proven (one
sample, could be ordinary model variance or the `-mtp-` tag's own higher
`presence_penalty` of 1.5 vs the plain tag's default).

**Why this doesn't match the "MTP is ~2x faster on Apple Silicon" claims
circulating online:** every one of those claims traces to
[MTPLX](https://github.com/dbuck/mtplx) (and its predecessor
[MTPLX](https://github.com/youssofal/MTPLX)) - an **MLX-native**
implementation, not llama.cpp/GGUF (what Ollama runs). MTPLX's own README
states the mechanism directly: "Small-M qmv retuning cuts the verify-MLP
region by enough to be the difference between 'MTP loses to AR' and 'MTP
at ~2.24x'" - i.e. without a custom Metal kernel specifically retuned for
speculative decoding's small-batch (2-3 token) verify step, MTP loses to
plain autoregressive decoding **even in MTPLX's own stack**. MTPLX adds
that retuned kernel plus a separately-quantized 3/4-bit draft-only LM head
(~29% faster draft step) and a compiled-graph cache to remove per-cycle
Python dispatch overhead. Ollama's bundled `llama-server` uses generic
llama.cpp Metal kernels, not kernels retuned for this specific shape - the
mechanism (MTP draft+verify) is the same, the acceptance rate is
genuinely good, but the backend implementation quality gap is large enough
to erase the win and turn it slightly negative. Same model family
(qwen3.6), same MTP mechanism, two very different runtimes.

**Do any Ollama parameters fix this? No identified path.** The bottleneck
is the kernel implementation itself, not configuration:
- `draft_num_predict` (Modelfile PARAMETER -> `--spec-draft-n-max`) is the
  only user-tunable knob Ollama exposes for this. Since the loss is driven
  by per-cycle verify *cost*, not draft-depth/acceptance, increasing it
  would very likely make things worse, not better. `draft_num_predict=1`
  (shallowest draft) would be a cheap way to test whether the loss scales
  down with draft depth as that theory predicts, but there's no reason to
  expect it to flip net-positive.
- `--spec-draft-backend-sampling` and the batch-size flags (`-b`/`-ub`)
  are hardcoded by Ollama when it launches `llama-server` for this tag,
  not exposed as Modelfile-tunable parameters.

**Real path to the claimed 2x, if wanted:** run MTPLX (or the underlying
converted MLX weights) directly on MLX instead of through Ollama - a
genuinely different serving stack (separate install, separate OpenAI/
Anthropic-compatible API layer, MLX-format converted weights), not a
config change to the current Ollama setup. Out of scope for this pass;
flagged as the actual next step if MTP speed is still wanted. See also
`spikes/mlx_benchmark/` (this repo's own prior raw-MLX-runtime comparison,
different question, same underlying hardware) for related prior art before
starting that investigation from scratch.

**Verdict for Kriya:** stay on `qwen3.6:35b-a3b-q4_K_M` (plain tag). The
`-mtp-` tag is not a drop-in speed win on this Ollama/Apple-Silicon
combination as tested.
