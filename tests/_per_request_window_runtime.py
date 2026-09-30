"""PROVIDER-CONTRACT-001 test double: an OpenAI-compatible runtime that
applies a per-request context window (``options.num_ctx``), identified like
Ollama. Ollama's own /v1 ignores that field (measured), so PRD-016 context
tiers are never offered through it; tests of the tier mechanism run on this
double instead (Ollama's native API is a real runtime of this kind)."""
from contextlib import contextmanager
from dataclasses import replace

from kriya.core import model_runtime
from kriya.core.inference_runtime import RuntimeCapabilities, register_runtime_adapter, unregister_runtime_adapter

PER_REQUEST_WINDOW_RUNTIME = "per-request-window-test"


class PerRequestWindowRuntime(model_runtime.OllamaRuntimeAdapter):
    name = PER_REQUEST_WINDOW_RUNTIME
    capabilities = RuntimeCapabilities(per_request_context_window=True, native_identity_probe=True,
                                       stream_usage=True)

    def request_plan(self, extra_body, *, temperature, reasoning_flag, requested_context_window, fingerprint=None):
        plan = super().request_plan(extra_body, temperature=temperature, reasoning_flag=reasoning_flag,
                                    requested_context_window=requested_context_window, fingerprint=fingerprint)
        if requested_context_window is None:
            return plan
        return replace(plan, wire_body=self.with_context_window(plan.wire_body, requested_context_window))


@contextmanager
def per_request_window_runtime():
    register_runtime_adapter(PerRequestWindowRuntime())
    model_runtime.clear_model_runtime_cache()
    try:
        yield PER_REQUEST_WINDOW_RUNTIME
    finally:
        unregister_runtime_adapter(PER_REQUEST_WINDOW_RUNTIME)
        model_runtime.clear_model_runtime_cache()
