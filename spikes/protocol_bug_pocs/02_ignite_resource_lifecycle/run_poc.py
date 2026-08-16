"""POC: isolates ONLY the "start an embedded Ignite node and close it" decision
from the real ignite_qpid_protocol goal - one small class, no protocol/wire
format, no Qpid. Grades each trial with Kriya's own real, already-shipped
`IgniteUnclosedResourceCheck` (kriya/workflow/static_checks.py) - the exact
deterministic check the real pipeline runs, so this POC's grading is provably
identical to what the real retry loop would catch, not an approximation.

Found live, 3 separate times this session (a-1, a-4, attribution-fix-
validation-6): the model forgets to close the Ignite instance on attempt 1 at
roughly the same rate every time, DESPITE the real goal already stating this
requirement about as explicitly as English allows ("you MUST explicitly close
it... this is a real defect... not a false alarm").

A/B tests goal wording directly: "explicit" mirrors the real goal's own strong
warning almost verbatim; "implicit" is the identical task with that warning
removed entirely, asking only for the base functionality. If the pass rate is
similar between the two, the explicit warning isn't actually the lever - this
is a model execution/attention habit, not a goal-clarity gap (see
docs/kriya_backlog_and_lessons.md's 2026-08-14 entries for the reasoning this
POC exists to test empirically instead of by argument).

Run: .venv/bin/python spikes/protocol_bug_pocs/02_ignite_resource_lifecycle/run_poc.py [--trials N] [--variants explicit implicit]
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
from kriya.workflow.static_checks import IgniteUnclosedResourceCheck  # noqa: E402

MODEL = "qwen3-coder:30b"
BASE_URL = "http://localhost:11434/v1"
TEMPERATURE = 0.2

SYSTEM_PROMPT = (
    "You are an expert Java developer. Follow the requirements exactly and "
    "respond in exactly the format requested - no extra commentary."
)

_BASE_TASK = """Write a single Java class `IgniteApp` in package com.example with a `public static void main(String[] args) throws Exception` method that does the following:

1. Starts an embedded Apache Ignite node by calling `Ignition.start("ignite-config.xml")` (a config file that already exists in the working directory - you do not need to create it).
2. Uses the started Ignite instance to get or create a cache named "myCache" and puts one entry into it (any key/value you like).
3. Prints "Done" to standard output.
"""

_EXPLICIT_WARNING = """
IMPORTANT: An Ignite node that is started but never explicitly closed leaves background discovery/communication threads running that keep the JVM alive indefinitely, even after all the code above has already run successfully - this is a real defect, not a false alarm. You MUST ensure the Ignite instance is explicitly closed before the program exits, no matter which control-flow path is taken (including if an exception occurs).
"""

_RESPONSE_FORMAT = """
Respond with ONLY the Java file's complete content in a single fenced code block. Do not include any explanation or commentary outside the code block.
"""

PROMPTS = {
    "explicit": _BASE_TASK + _EXPLICIT_WARNING + _RESPONSE_FORMAT,
    "implicit": _BASE_TASK + _RESPONSE_FORMAT,
}

CODE_BLOCK_RE = re.compile(r"```(?:java)?\n(.*?)```", re.DOTALL)


def extract_code(response: str) -> str:
    m = CODE_BLOCK_RE.search(response)
    return m.group(1) if m else response


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
    violation = IgniteUnclosedResourceCheck().check({"IgniteApp.java": code})
    if violation:
        return {"outcome": "FAIL (unclosed)", "elapsed": elapsed, "detail": ""}
    if "Ignition.start(" not in code:
        return {"outcome": "MISSING_CALL", "elapsed": elapsed, "detail": "no Ignition.start( found at all"}
    return {"outcome": "PASS", "elapsed": elapsed, "detail": ""}


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
            print(f"  [{i + 1}/{args.trials}] {r['outcome']:<20} {r['elapsed']:6.1f}s   {r['detail']}")
        results[variant] = trial_results

    print(f"\n{'=' * 78}\nSummary\n{'=' * 78}")
    for variant, trial_results in results.items():
        outcomes = [r["outcome"] for r in trial_results]
        pass_count = outcomes.count("PASS")
        print(f"  {variant:<10}: {pass_count}/{len(outcomes)} PASS   {outcomes}")


if __name__ == "__main__":
    asyncio.run(main())
