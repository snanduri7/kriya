"""INF-001: overhead of the runtime port, measured without inference.

Compares the fake adapter called directly with the same call made through
LLMClient's port dispatch (_request_once: binding -> registry lookup ->
ChatRequest -> adapter -> to_raw). Run: .venv/bin/python handover/evidence/BACKLOG_6_5/inf001_overhead.py
"""
import asyncio
import os
import sys
import time

os.environ.setdefault("KRIYA_MODEL_RUNTIME_PROBE", "0")
sys.path.insert(0, "tests")
from _fake_inference_runtime import FakeRuntimeAdapter  # noqa: E402

from kriya.config import AppConfig  # noqa: E402
from kriya.core.inference_runtime import ChatRequest, register_runtime_adapter, runtime_adapter  # noqa: E402
from kriya.core.llm import LLMClient  # noqa: E402

N = 20000
fake = FakeRuntimeAdapter("fake-bench")
register_runtime_adapter(fake)
config = AppConfig()
config.llm.inference_runtime = "fake-bench"
llm = LLMClient(config)
messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]


async def direct():
    for _ in range(N):
        await fake.complete(None, ChatRequest(model="m", messages=messages, temperature=0.2, max_tokens=16))


async def through_port():
    for _ in range(N):
        await llm._request_once(None, config.llm.model, "s", "u", 0.2, 16, None, None, None)


def timed(fn):
    fake.requests.clear()
    start = time.perf_counter()
    asyncio.run(fn())
    return (time.perf_counter() - start) / N * 1e6


direct_us, port_us = timed(direct), timed(through_port)
start = time.perf_counter()
for _ in range(N):
    runtime_adapter("fake-bench")
lookup_us = (time.perf_counter() - start) / N * 1e6
print(f"calls={N}  direct={direct_us:.2f}us/call  through_port={port_us:.2f}us/call  "
      f"port_overhead={port_us - direct_us:.2f}us/call  registry_lookup={lookup_us:.3f}us")
