"""POC: isolates ONLY the "send a byte[] as a JMS BytesMessage" decision from
the real ignite_qpid_protocol goal - one small method, no protocol/wire
format, no Ignite. Grades with a static regex check for the correct API shape.

Found live once this session (attribution-fix-validation-2):
`session.createBytesMessage(data)` (passing the bytes as if it were a
constructor argument - it isn't one) instead of the correct two-step API,
`session.createBytesMessage()` (no arguments) then `message.writeBytes(data)`.

Unlike the Ignite-resource and cache-typing POCs, the real goal never gave any
explicit warning about this specific API shape at all (it just said "send the
encoded bytes as a JMS BytesMessage to a queue") - so this prompt intentionally
matches that same level of ambiguity, rather than over-specifying the correct
API and testing something easier than what actually happened live. This POC is
measuring whether this is a genuine, repeatable JMS-API knowledge gap (which
would make it a good candidate for a `skills/qpid` rule addition - the
mechanism Kriya already has for exactly this kind of "known API gotcha", not a
new static check) or a one-off.

Run: .venv/bin/python spikes/protocol_bug_pocs/03_jms_bytes_message_api/run_poc.py [--trials N]
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

USER_PROMPT = """Write a single Java class `MessageSender` in package com.example with this exact method:

    public static void sendBytes(javax.jms.Session session, javax.jms.MessageProducer producer, byte[] data) throws javax.jms.JMSException

This method must send `data` as a JMS BytesMessage using the given producer - create the BytesMessage from the session, put `data`'s bytes into it, and send it.

Respond with ONLY the Java file's complete content (including the class declaration and necessary imports) in a single fenced code block. Do not include any explanation or commentary outside the code block.
"""

CODE_BLOCK_RE = re.compile(r"```(?:java)?\n(.*?)```", re.DOTALL)


def extract_code(response: str) -> str:
    m = CODE_BLOCK_RE.search(response)
    return m.group(1) if m else response


def grade(code: str) -> str:
    # Wrong: createBytesMessage called WITH an argument - it takes none.
    if re.search(r"createBytesMessage\s*\(\s*[^)\s]", code):
        return "FAIL (createBytesMessage called with an argument - it takes none)"
    if not re.search(r"createBytesMessage\s*\(\s*\)", code):
        return "FAIL (no correct-shaped createBytesMessage() call found)"
    if ".writeBytes(" not in code:
        return "FAIL (createBytesMessage() called correctly, but never wrote the bytes via writeBytes())"
    return "PASS"


async def run_trial(llm: LLMClient) -> dict:
    start = time.monotonic()
    try:
        response = await llm.complete(
            SYSTEM_PROMPT, USER_PROMPT,
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
    args = parser.parse_args()

    config = load_config()
    llm = LLMClient(config)

    results = []
    for i in range(args.trials):
        r = await run_trial(llm)
        results.append(r)
        print(f"[{i + 1}/{args.trials}] {r['outcome']:<70} {r['elapsed']:6.1f}s")

    pass_count = sum(1 for r in results if r["outcome"] == "PASS")
    print(f"\nSummary: {pass_count}/{args.trials} used the correct JMS BytesMessage API on the first try.")


if __name__ == "__main__":
    asyncio.run(main())
