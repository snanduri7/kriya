"""Phase B probe (model-free, read-only against the baseline code; no network, nothing written outside a temporary directory).

Run: PYTHONPATH=<worktree>:<worktree>/tests PYTHONDONTWRITEBYTECODE=1 KRIYA_STATE_DIR=<tmp>/state KRIYA_LOG_DIR=<tmp>/logs
     KRIYA_MODEL_RUNTIME_PROBE=0 <venv python> docs/architecture/evidence/b/b_probe.py

P1  two bindings that name the same model with different settings: which binding does a call execute with, and which
    binding do qualification and the doctor assess? (root issue R-09, child GAP-A6-02)
"""
from __future__ import annotations

import os
import tempfile
import traceback

SCRATCH = tempfile.mkdtemp(prefix="kriya-b-probe-")
for _key, _leaf in (("HOME", "home"), ("KRIYA_AUTHORITY_HOME", "authority"), ("KRIYA_MCP_APPROVAL_HOME", "mcp")):
    os.environ[_key] = os.path.join(SCRATCH, _leaf)
    os.makedirs(os.environ[_key], exist_ok=True)


def p1() -> None:
    from _strict_doubles import strict_config

    from kriya.config.config import routing_alias_conflicts
    from kriya.core.inference_settings import role_binding_for_model, role_inference_settings
    from kriya.core.llm import LLMClient
    from kriya.core.model_runtime import binding_object

    cfg = strict_config(
        llm={"model": "shared-model:1", "context_window": 32768, "temperature": 0.2},
        agent_llms={"reviewer": {"llm": {"model": "shared-model:1", "context_window": 65536, "temperature": 0.7,
                                         "inference_runtime": "ollama_native", "extra_body": {"top_p": 0.5}}}},
    )
    print("configuration accepted with two bindings of one model name and different settings: True")
    print("routing_alias_conflicts (the only same-alias check; it covers routing candidates):", routing_alias_conflicts(cfg))
    client = LLMClient(cfg)
    executed = client._binding("shared-model:1")
    assessed = role_binding_for_model(cfg, "reviewer", "shared-model:1")
    print("EXECUTION  LLMClient._binding: context_window", executed["context_window"], "| inference_runtime", executed["inference_runtime"],
          "| extra_body", executed["extra_body"], "| temperature", executed["temperature"])
    print("ASSESSMENT role_binding_for_model(reviewer): context_window", assessed.context_window, "| inference_runtime", assessed.inference_runtime,
          "| extra_body", assessed.extra_body, "| temperature", assessed.temperature)
    print("model_runtime.binding_object (runtime fingerprint lookup) returns the primary:", binding_object(cfg, "shared-model:1") is cfg.llm)
    print("adapter the Reviewer's call would use:", client._runtime("shared-model:1").name, "| adapter its binding names:", assessed.inference_runtime)
    settings = role_inference_settings(cfg, "reviewer", "shared-model:1")
    print("the reviewer identity qualification would assess has settings digest:", settings.digest[:16],
          "| the primary's:", role_inference_settings(cfg, "developer", "shared-model:1").digest[:16])
    print("fields a role call passes explicitly (agents/agent.py _call_with_escalation): model, base_url, api_key, temperature, max_tokens, reasoning, extra_body")
    print("fields it does not pass, taken from LLMClient._binding by model name: context_window, context_policy, inference_runtime")


if __name__ == "__main__":
    print("=== P1 two bindings of one model name")
    try:
        p1()
    except Exception as error:
        print(f"PROBE_SETUP_FAILED {type(error).__name__}: {error}")
        traceback.print_exc(limit=5)
    print("\nscratch:", SCRATCH)
