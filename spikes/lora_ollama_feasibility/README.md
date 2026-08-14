# Spike: can a Soup-trained LoRA adapter actually reach Ollama?

## The one question this tests

If Kriya someday fine-tunes a local model on its own accumulated skill/rule
knowledge (the idea discussed this session: whatever model Kriya is
configured against gets incrementally specialized as it fills knowledge gaps
via skills), the single riskiest unknown is whether the *output* of that
training - a LoRA adapter produced by
[Soup](https://github.com/MakazhanAlpamys/Soup) - can actually be loaded and
served by **Ollama**, Kriya's actual inference backend. Soup outputs
PEFT/safetensors-format adapters; Ollama serves GGUF. This spike tests that
one conversion/loading step directly, before any bigger design commitment.

## What this does NOT test

- **Fine-tuning quality or effectiveness.** 5 training examples and a tiny
  (~0.5B parameter) base model is nowhere near enough to show whether
  fine-tuning actually improves skill-rule compliance better than prompting
  does (increment #13, shipped earlier this session) - that's a real,
  separate, much bigger question for later, using the actual eval harness
  (`spikes/eval_harness/`), a real-sized model, and a much larger accumulated
  dataset.
- **Whether this is a good idea for Kriya at all.** Purely mechanical: does
  the pipeline (train → convert → load → serve) work, at all, on this
  machine, with today's tool versions.

## Why a tiny model

Qwen2.5-0.5B-Instruct (or similar) is suggested throughout, not because it's
what Kriya would ever actually fine-tune for real use, but because a small
model makes every step of this pipeline (training, conversion, disk usage)
fast enough to debug and re-run - the point is proving the mechanics work,
not demonstrating capability. This machine (M1 Max, 64GB, per
`spikes/mlx_benchmark/README.md`) could handle much larger models if this
spike's answer is positive and a follow-up wants to test with something
closer to what Kriya actually uses.

## Steps (run these yourself - see below for why)

1. `.venv/bin/python 01_prepare_dataset.py` - builds `dataset.jsonl` from the
   REAL `skills/binary-wire-protocol/rules.txt` content (Kriya's own venv is
   fine for this - pure stdlib, no heavy deps). Already run once while
   building this spike; regenerate any time.
2. `./02_train.sh` - installs Soup's training extras into a spike-local venv
   (`.venv` here, never Kriya's own), scaffolds a config via `soup init`, asks
   you to point it at the small model + `dataset.jsonl`, then trains.
3. `./03_convert_and_load.sh <path-to-trained-adapter>` - the actual question
   this spike exists to answer. Tries Plan A (adapter-only GGUF conversion +
   Ollama's `ADAPTER` Modelfile directive - the shape that actually matches
   the target design, a small adapter layered onto an existing base model).
   Falls back to Plan B (merge the adapter into the base model's weights,
   convert the whole merged model to GGUF) if Plan A doesn't work cleanly -
   which result you get IS the real finding, not just a hurdle to clear.
4. `ollama create <name> -f Modelfile.adapter` (or `.merged`) - load the
   result into Ollama for real.
5. `.venv/bin/python 04_compare_before_after.py --base <tag> --tuned <tag>`
   (back to Kriya's OWN venv - this part just calls `kriya.core.llm.LLMClient`
   like every other spike) - queries both models with held-out prompts related
   to but not copied from the training data, prints them side by side. First
   qualitative signal only, per the caveat above.

## Why you run this, not me

Step 2 installs a real ML training stack (torch/transformers/peft/trl/
datasets) and downloads real model weights; step 3 clones llama.cpp and runs
real conversion/quantization work. Consistent with how every live-model step
this session has been handled (the eval harness, the protocol-bug POCs) -
built and ready to run, but the actual heavy execution happens in your
terminal, not backgrounded by me.

## Interpreting the result

- **Plan A works cleanly** → the target design (small, swappable adapters on
  top of Kriya's existing configured model) is directly feasible. Strongest
  possible outcome.
- **Plan A fails, Plan B works** → the pipeline is real but the "small
  adapter" shape isn't available yet with today's Ollama - full-model
  merge-and-reload would be the fallback design (heavier: whole new model
  file per training update, not a small diff).
- **Both fail** → the idea needs to wait on tooling maturity, or a different
  serving backend, before it's buildable at all. Also a real, useful answer -
  exactly what this spike was for.

Report back whatever actually happens, including partial/failed steps - the
failure mode matters as much as success here.
