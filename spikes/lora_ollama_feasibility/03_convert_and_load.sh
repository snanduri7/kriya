#!/usr/bin/env bash
# THE key step this whole spike exists to test: can the LoRA adapter 02_train.sh
# just produced actually reach Ollama. Soup outputs a PEFT/safetensors-format
# adapter; Ollama loads GGUF. Two paths, tried in order - which one (if either)
# actually works cleanly is itself the real answer this spike is after, not
# just "did it eventually load somehow":
#
#   Plan A (adapter-only, via llama.cpp's convert_lora_to_gguf.py + Ollama's
#   own `ADAPTER` Modelfile directive): keeps the adapter small and separate
#   from the base model - the shape that actually matches the target design
#   (a small adapter layered onto whatever base model Kriya is already
#   configured to use, not a whole new model file per update). Depends on
#   Ollama's adapter support being solid for your installed version - that's
#   exactly the open question.
#
#   Plan B (merge-and-convert, fallback if A fails): merge the adapter into
#   the base model's weights first (`peft`'s merge_and_unload()), convert the
#   FULL merged model to GGUF, load that directly with a plain `FROM`. More
#   robust (no separate adapter-loading code path to depend on) but produces
#   a whole new multi-GB model file per training update, not a small adapter -
#   works for proving the pipeline CAN reach Ollama at all, but isn't the
#   shape you'd actually want for repeated incremental updates.
#
# Run yourself - clones llama.cpp and runs real conversion/quantization work.
set -euo pipefail
cd "$(dirname "$0")"
source .venv/bin/activate

ADAPTER_DIR="${1:-}"
if [ -z "$ADAPTER_DIR" ]; then
    echo "Usage: $0 <path-to-trained-adapter-dir>"
    echo "(whatever soup.yaml's output/save_dir produced in step 02)"
    exit 1
fi

if [ ! -d llama.cpp ]; then
    echo "=== Cloning llama.cpp (shallow) for its GGUF conversion scripts ==="
    git clone --depth 1 https://github.com/ggml-org/llama.cpp.git
fi
pip install -r llama.cpp/requirements.txt

echo
echo "=== Plan A: adapter-only conversion ==="
set +e
python3 llama.cpp/convert_lora_to_gguf.py "$ADAPTER_DIR" --outfile adapter.gguf
PLAN_A_STATUS=$?
set -e

if [ "$PLAN_A_STATUS" -eq 0 ] && [ -f adapter.gguf ]; then
    echo "Plan A conversion succeeded: adapter.gguf"
    cat > Modelfile.adapter <<EOF
FROM $(python3 -c "import json; print(json.load(open('$ADAPTER_DIR/adapter_config.json'))['base_model_name_or_path'])" 2>/dev/null || echo "REPLACE_WITH_BASE_MODEL_NAME")
ADAPTER ./adapter.gguf
EOF
    echo "Wrote Modelfile.adapter - check the FROM line names a model Ollama already has"
    echo "(pull it first with 'ollama pull <name>' if not, or point FROM at a local GGUF path)."
    echo "Then: ollama create kriya-lora-spike-adapter -f Modelfile.adapter"
else
    echo "Plan A failed (conversion script exited $PLAN_A_STATUS, or Ollama's ADAPTER"
    echo "directive doesn't accept this yet) - that result IS the answer this spike is"
    echo "testing for, not just a bug to route around. Falling back to Plan B to at least"
    echo "confirm the adapter's TRAINED CONTENT can reach Ollama some way, even if not"
    echo "in the small-separate-adapter shape."
    echo
    echo "=== Plan B: merge adapter into base, convert the full model ==="
    python3 - "$ADAPTER_DIR" <<'PYEOF'
import sys, json
from pathlib import Path
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

adapter_dir = sys.argv[1]
base_model_name = json.load(open(Path(adapter_dir) / "adapter_config.json"))["base_model_name_or_path"]
print(f"Merging adapter from {adapter_dir} into base {base_model_name}...")

base = AutoModelForCausalLM.from_pretrained(base_model_name)
tok = AutoTokenizer.from_pretrained(base_model_name)
merged = PeftModel.from_pretrained(base, adapter_dir).merge_and_unload()

out_dir = "merged-model"
merged.save_pretrained(out_dir)
tok.save_pretrained(out_dir)
print(f"Merged model saved to {out_dir}")
PYEOF
    python3 llama.cpp/convert_hf_to_gguf.py merged-model --outfile merged.gguf
    cat > Modelfile.merged <<EOF
FROM ./merged.gguf
EOF
    echo "Wrote Modelfile.merged (no ADAPTER directive - the fine-tuning is baked into merged.gguf)."
    echo "Then: ollama create kriya-lora-spike-merged -f Modelfile.merged"
fi
