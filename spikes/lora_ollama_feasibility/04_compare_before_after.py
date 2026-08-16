"""Queries the BASE model and the newly-loaded fine-tuned model (whichever of
Plan A/B from 03_convert_and_load.sh actually worked) with held-out prompts -
related to the training content but not copied from it - and prints both
responses side by side.

This is a FIRST, qualitative signal only, not a rigorous eval - 5 training
examples and a 0.5B model is nowhere near enough data/capacity to draw a real
effectiveness conclusion from. The question this script actually answers is
narrower and more mechanical: did the fine-tuned model load and respond AT
ALL via Ollama, and does its answer at least LOOK different from the base
model's - confirming the round trip (train -> convert -> load -> serve)
produced something, not just confirming it produced something GOOD. See
README.md's "what this does NOT test" section - a real effectiveness
comparison needs the full eval harness this session already built
(spikes/eval_harness/), not this script.

Run: (Kriya's own venv is fine - this uses kriya.core.llm.LLMClient like every
other spike this session, not the spike's own heavy training venv)
.venv/bin/python spikes/lora_ollama_feasibility/04_compare_before_after.py \
    --base <base-model-tag-in-ollama> --tuned <tuned-model-tag-in-ollama>
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kriya.config import load_config  # noqa: E402
from kriya.core.llm import LLMClient  # noqa: E402

BASE_URL = "http://localhost:11434/v1"
TEMPERATURE = 0.2

# Held out - related to the trained skill content (skills/binary-wire-protocol)
# but not a copy of any of dataset.jsonl's 5 examples, so this isn't just
# testing verbatim memorization of the exact training text.
HELD_OUT_PROMPTS = [
    "In Java, I need to write a 5-byte big-endian sequence number into a byte array. What's the correct way to do this?",
    "Is it OK to use ByteBuffer.putShort(4, value) partway through a method that's otherwise writing fields sequentially with put()?",
]

SYSTEM_PROMPT = "You are an expert Java developer. Answer concisely and directly."


async def ask(llm: LLMClient, model: str, prompt: str) -> str:
    return await llm.complete(
        SYSTEM_PROMPT, prompt,
        model_override=model, base_url_override=BASE_URL, api_key_override="local-key",
        temperature_override=TEMPERATURE,
    )


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, help="Ollama tag for the UN-tuned base model")
    parser.add_argument("--tuned", required=True, help="Ollama tag for the fine-tuned model (Plan A or B)")
    args = parser.parse_args()

    config = load_config()
    llm = LLMClient(config)

    for prompt in HELD_OUT_PROMPTS:
        print(f"\n{'=' * 78}\nPrompt: {prompt}\n{'=' * 78}")
        base_answer = await ask(llm, args.base, prompt)
        tuned_answer = await ask(llm, args.tuned, prompt)
        print(f"\n--- BASE ({args.base}) ---\n{base_answer}")
        print(f"\n--- TUNED ({args.tuned}) ---\n{tuned_answer}")


if __name__ == "__main__":
    asyncio.run(main())
