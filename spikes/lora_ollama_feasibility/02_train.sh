#!/usr/bin/env bash
# Trains a tiny LoRA adapter on dataset.jsonl (built by 01_prepare_dataset.py)
# using Soup (github.com/MakazhanAlpamys/Soup). Deliberately targets the
# SMALLEST reasonable base model, not something Kriya would actually use for
# real generation - this spike tests PIPELINE MECHANICS (train -> convert ->
# load into Ollama), not fine-tuning quality/effectiveness. A tiny model keeps
# each iteration of this spike fast to re-run while debugging the pipeline.
#
# This script does NOT hand you a fully pre-written soup.yaml - Soup's exact
# current config schema wasn't independently verified against its live docs
# (only its top-level README was read when this spike was built), and
# guessing wrong field names would just produce a confusing config-parse
# error instead of a real answer. `soup init` scaffolds a verified-correct
# starting config instead - safer than a hand-typed one that might be stale.
#
# Run this yourself, not something to background - it installs a real ML
# training stack (torch/transformers/peft/trl/datasets) and downloads real
# model weights. Uses its own venv (.venv here), matching
# spikes/mlx_benchmark's own convention - never installed into Kriya's own
# .venv, which has no reason to carry a training stack.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "Creating spike-local venv..."
    python3 -m venv .venv
fi

echo "Activating spike-local venv (spikes/lora_ollama_feasibility/.venv)..."
source .venv/bin/activate

echo
echo "=== Step 1: install Soup with the training extras ==="
echo "(torch/transformers/peft/trl/datasets - a real, sizable download)"
pip install --upgrade pip
pip install "soup-cli[train]"
# On Apple Silicon (this machine: M1 Max, per spikes/mlx_benchmark), also check
# whether Soup's `mlx` extra applies to your chosen model/backend - see
# docs/models.md in the Soup repo for current Apple Silicon guidance. Not
# installed by default here since CPU/MPS-backed torch is the more portable
# fallback and this spike's model is tiny enough for that to be fine.

echo
echo "=== Step 2: scaffold a verified-correct base config ==="
soup init --template chat
echo
echo ">>> ACTION NEEDED: open the generated soup.yaml and edit it by hand:"
echo "    - model: point it at a SMALL base model, e.g. Qwen/Qwen2.5-0.5B-Instruct"
echo "      (small on purpose - this spike tests the pipeline, not fine-tune quality)"
echo "    - data/dataset path: point it at $(pwd)/dataset.jsonl"
echo "    - lora.r: a small rank (e.g. 8) is enough for this feasibility test"
echo "    - training.stream_layers / quantization: leave OFF for a 0.5B model -"
echo "      those exist for models much too big to fit in memory otherwise, not needed here"
echo "    - training.seed: pin it (e.g. 1234) for reproducibility"
echo "Press Enter once soup.yaml is edited and ready, or Ctrl+C to stop here."
read -r

echo
echo "=== Step 3: train ==="
soup train --config soup.yaml

echo
echo "Done. The trained adapter should now be under whatever output dir soup.yaml specified"
echo "(commonly ./output or similar - check soup.yaml's own output/save_dir field)."
echo "Next: 03_convert_and_load.sh"
