"""Builds a tiny instruction-tuning dataset from a REAL Kriya skill
(skills/binary-wire-protocol/rules.txt) - not synthetic toy data. This is the
raw material for the LoRA training step: does fine-tuning a small local model
on Kriya's own accumulated skill knowledge, then loading the result back into
Ollama, actually work end to end. Quality/effectiveness of the result is
explicitly NOT what this spike measures - see README.md.

Output: dataset.jsonl, one {"instruction": ..., "input": "", "output": ...}
object per line (Alpaca-style - the most common/portable instruction-tuning
schema, likely but not confirmed to be what `soup train` expects directly;
verify against the real Soup repo's dataset-format docs or `soup init
--template chat` output before running step 02 - this schema was written
without live access to Soup's own docs beyond its top-level README).

Run: .venv/bin/python 01_prepare_dataset.py
(Kriya's own venv is fine for this step - pure stdlib, no heavy deps.)
"""
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_PATH = Path(__file__).resolve().parent / "dataset.jsonl"

# Hand-derived from the real skill text (skills/binary-wire-protocol/rules.txt,
# read directly, not paraphrased from memory) - one instruction/response pair
# per distinct rule in that file, so the fine-tuned model is being taught
# exactly what the skill's own rules.txt already teaches Kriya's prompted
# generation. Small on purpose (this is a pipeline-feasibility spike, not a
# quality benchmark - see README.md's "what this does NOT test" section).
EXAMPLES = [
    {
        "instruction": "In Java, how do I write a 3-byte big-endian length field to a byte array or ByteBuffer, given an int value?",
        "input": "",
        "output": (
            "Do NOT use ByteBuffer.putInt() or DataOutputStream.writeInt() - they always write "
            "the full 4-byte native width, corrupting a 3-byte field. Instead, manually extract "
            "and write each byte via bit-shifting and masking: "
            "header[o] = (byte) ((value >> 16) & 0xFF); "
            "header[o+1] = (byte) ((value >> 8) & 0xFF); "
            "header[o+2] = (byte) (value & 0xFF);"
        ),
    },
    {
        "instruction": "When is it safe to use ByteBuffer.putInt()/putShort()/putLong() directly for a binary wire-format field?",
        "input": "",
        "output": (
            "Only when the wire field's declared width EXACTLY matches that primitive's native "
            "Java width (4 bytes for int, 2 for short, 8 for long). Narrower-than-native fields "
            "require manual byte-by-byte packing instead - never trust the native-width method "
            "to write fewer bytes than it always writes."
        ),
    },
    {
        "instruction": (
            "I'm writing a Java method that encodes a binary header sequentially, field by "
            "field, one pass top to bottom. Should I use ByteBuffer's absolute-indexed writes "
            "like buffer.putInt(index, value)?"
        ),
        "input": "",
        "output": (
            "No - for sequential encoding, use only relative (cursor-based) writes: put(byte), "
            "putInt(value), putLong(value), with no index argument. An absolute-indexed write at "
            "a hard-coded offset silently overwrites already-written earlier fields and misaligns "
            "everything written after it, with no compile error and often no runtime exception "
            "either. Absolute-indexed writes are only correct for a backpatch: write a placeholder "
            "sequentially, then correct it with exactly one absolute write after everything else "
            "in the buffer is already written."
        ),
    },
    {
        "instruction": (
            "How should I size the destination byte array or ByteBuffer when encoding a "
            "wire-format binary header that mixes standard-width and non-standard-width fields?"
        ),
        "input": "",
        "output": (
            "Size it to the wire format's EXACT total declared byte length - the sum of every "
            "field's own declared width. Do not derive the size from each field's native Java "
            "type width, since that overcounts for any narrower-than-native field (e.g. a 3-byte "
            "length field sized as if it were a full 4-byte int)."
        ),
    },
    {
        "instruction": "How do I decode a 3-byte big-endian length field back into an int in Java?",
        "input": "",
        "output": (
            "Reconstruct it from 3 raw bytes, masking each with & 0xFF first since Java bytes are "
            "signed: int value = ((data[o] & 0xFF) << 16) | ((data[o+1] & 0xFF) << 8) | "
            "(data[o+2] & 0xFF); Never use ByteBuffer.getInt() for this - it reads 4 bytes, not 3."
        ),
    },
]


def main():
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        for ex in EXAMPLES:
            f.write(json.dumps(ex) + "\n")
    print(f"Wrote {len(EXAMPLES)} examples to {OUT_PATH}")
    print("Verify this schema against Soup's actual expected dataset format "
          "(check the Soup repo's docs/ or `soup init --template chat`) before running 02_train.sh.")


if __name__ == "__main__":
    main()
