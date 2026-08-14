"""POC: isolates ONLY the "declare an Ignite cache reference" decision from the
real ignite_qpid_protocol goal - one small method, no protocol/wire format, no
Qpid. Grades with a static regex check for `var` vs. an explicit generic type
on the cache declaration.

Found live once this session (attribution-fix-validation-3): `var cache =
ignite.getOrCreateCache(...)` infers as the raw `IgniteCache<Object, Object>`
type, which then fails to compile at every later use site that expects a typed
value - DESPITE the real goal already explicitly forbidding this ("using an
explicitly-typed IgniteCache<Integer, Protocol> reference (never a raw or
var-inferred cache handle)").

A/B tests goal wording directly, same design as ../02_ignite_resource_lifecycle:
"explicit" mirrors the real goal's own explicit prohibition; "implicit" is the
identical task with that prohibition removed entirely, asking only for a
working cache reference. If the pass rate is similar between the two, explicit
wording isn't the lever here either.

Run: .venv/bin/python spikes/protocol_bug_pocs/04_cache_generics_typing/run_poc.py [--trials N] [--variants explicit implicit]
"""
import argparse
import asyncio
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from kriya.config import load_config  # noqa: E402
from kriya.core.llm import LLMClient  # noqa: E402

MODEL = "qwen3-coder:30b"
BASE_URL = "http://localhost:11434/v1"
TEMPERATURE = 0.2

SYSTEM_PROMPT = (
    "You are an expert Java developer. Follow the requirements exactly and "
    "respond in exactly the format requested - no extra commentary."
)

_BASE_TASK = """Write a single Java method inside a class `CacheAccess` in package com.example:

    public static void storeAndRead(org.apache.ignite.Ignite ignite)

This method must get or create an Ignite cache named "protocolCache" that stores Integer keys and String values, put the entry (1, "hello") into it, then read key 1 back out and print it.
"""

_EXPLICIT_REQUIREMENT = """
The cache reference MUST be declared using an explicitly-typed `IgniteCache<Integer, String>` reference - never a raw or `var`-inferred cache handle.
"""

_RESPONSE_FORMAT = """
Respond with ONLY the Java file's complete content (including the class declaration and necessary imports) in a single fenced code block. Do not include any explanation or commentary outside the code block.
"""

PROMPTS = {
    "explicit": _BASE_TASK + _EXPLICIT_REQUIREMENT + _RESPONSE_FORMAT,
    "implicit": _BASE_TASK + _RESPONSE_FORMAT,
}

CODE_BLOCK_RE = re.compile(r"```(?:java)?\n(.*?)```", re.DOTALL)


def extract_code(response: str) -> str:
    m = CODE_BLOCK_RE.search(response)
    return m.group(1) if m else response


def grade(code: str) -> str:
    if re.search(r"\bvar\s+\w*[Cc]ache\w*\s*=", code):
        return "FAIL (used var for the cache handle)"
    if re.search(r"IgniteCache\s*<[^>]+>\s+\w*[Cc]ache\w*\s*=", code):
        return "PASS"
    if "getOrCreateCache(" not in code and "cache(" not in code:
        return "MISSING_CALL (no cache retrieval found at all)"
    return "UNKNOWN (couldn't find a recognizable typed/untyped cache declaration - check manually)"


async def run_trial(llm: LLMClient, variant: str) -> dict:
    start = time.monotonic()
    try:
        response = await llm.complete(
            SYSTEM_PROMPT, PROMPTS[variant],
            model_override=MODEL, base_url_override=BASE_URL, api_key_override="local-key",
            temperature_override=TEMPERATURE,
        )
    except Exception as e:
        return {"outcome": "LLM_ERROR", "elapsed": time.monotonic() - start, "detail": str(e)[:200]}
    elapsed = time.monotonic() - start
    code = extract_code(response)
    return {"outcome": grade(code), "elapsed": elapsed, "detail": ""}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=8)
    parser.add_argument("--variants", nargs="+", default=["explicit", "implicit"], choices=list(PROMPTS.keys()))
    args = parser.parse_args()

    config = load_config()
    llm = LLMClient(config)

    results: dict = {}
    for variant in args.variants:
        print(f"\n{'=' * 78}\nvariant = {variant}\n{'=' * 78}")
        trial_results = []
        for i in range(args.trials):
            r = await run_trial(llm, variant)
            trial_results.append(r)
            print(f"  [{i + 1}/{args.trials}] {r['outcome']:<60} {r['elapsed']:6.1f}s")
        results[variant] = trial_results

    print(f"\n{'=' * 78}\nSummary\n{'=' * 78}")
    for variant, trial_results in results.items():
        outcomes = [r["outcome"] for r in trial_results]
        pass_count = outcomes.count("PASS")
        print(f"  {variant:<10}: {pass_count}/{len(outcomes)} PASS   {outcomes}")


if __name__ == "__main__":
    asyncio.run(main())
