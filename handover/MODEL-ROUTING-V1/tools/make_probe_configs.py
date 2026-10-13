"""Experiment-only: derive single-axis probe configs from the frozen V2 baseline operator config.

Each variant changes exactly ONE binding of the baseline (planner / developer primary / developer retry) and nothing else.
Bindings are copies of a binding that production already runs for that model (no new sampling values), except the
DeepSeek planner probe, which reuses the baseline Planner sampling verbatim.
Usage: make_probe_configs.py <baseline config.yaml> <out dir> [probe home dir]
"""
import copy
import sys
from pathlib import Path

import yaml

QWEN38, CODER, QWEN36 = "qwen3.8:27b", "qwen3-coder:30b-kriya-e52213655394", "qwen3.6:35b-a3b-q4_K_M-kriya-620d4d5ce36a"
DEEPSEEK = "deepseek-r1:32b"


def bindings(cfg):
    q38 = copy.deepcopy(cfg["llm"])
    coder = copy.deepcopy(cfg["agent_llms"]["architect"]["llm"])
    q36 = copy.deepcopy(cfg["agent_llms"]["planner"]["llm"])
    ds = copy.deepcopy(cfg["agent_llms"]["planner"]["llm"])
    ds["model"] = DEEPSEEK
    ds.pop("extra_body", None)
    ds["extra_body"] = copy.deepcopy(cfg["agent_llms"]["planner"]["llm"]["extra_body"])
    ds["extra_body"].pop("reasoning_effort", None)
    # Developer-position bindings: the Developer primary is the top-level ``llm`` block, so coder is its own production
    # binding (the architect/reviewer block, sampling identical to the V2 baseline's other bindings) in that position. qwen3.6 keeps the binding it runs with in the baseline chain (no explicit
    # ``capabilities`` block: its profile is qualification-derived, never declared on its behalf).
    coder_dev = copy.deepcopy(coder)  # the coder qualification identity has NO repeat_penalty (its Modelfile default applies server-side)
    q36_dev = {k: v for k, v in q38.items() if k != "capabilities"}
    q36_dev["model"] = QWEN36
    q36_dev["context_window"] = cfg["llm_chain"][0]["context_window"]
    q36_dev["extra_body"] = copy.deepcopy(cfg["llm_chain"][0]["extra_body"])
    return {QWEN38: q38, CODER: coder, QWEN36: q36, DEEPSEEK: ds, "dev:" + CODER: coder_dev, "dev:" + QWEN36: q36_dev}


def chain_entry(binding):
    keep = ("model", "inference_runtime", "base_url", "api_key", "context_window", "temperature", "extra_body", "capabilities")
    entry = {k: binding[k] for k in keep if k in binding}
    entry.setdefault("context_window", binding.get("extra_body", {}).get("options", {}).get("num_ctx", 32768))
    return entry


def variants(cfg, home=None):
    """``home``: probe-only state/skills/memory directories (paths are not model settings; they keep probe runs out of
    the V2 evidence stores)."""
    if home:
        cfg = copy.deepcopy(cfg)
        cfg["paths"] = {"skills": f"{home}/skills", "state": f"{home}/state", "memory": f"{home}/memory"}
    b = bindings(cfg)
    out = {"baseline": cfg}
    for name, model in (("planner_qwen38", QWEN38), ("planner_coder", CODER), ("planner_deepseek", DEEPSEEK)):
        v = copy.deepcopy(cfg)
        v["agent_llms"]["planner"]["llm"] = copy.deepcopy(b[model])
        out[name] = v
    # DeepSeek-R1 always reasons: its honest binding declares ``reasoning: true`` (existing precedent: qwen38-qualification.yaml)
    v = copy.deepcopy(cfg)
    v["agent_llms"]["planner"]["llm"] = {**copy.deepcopy(b[DEEPSEEK]), "reasoning": True}
    out["planner_deepseek_reasoning"] = v
    for name, model in (("devprimary_coder", "dev:" + CODER), ("devprimary_qwen36", "dev:" + QWEN36)):
        v = copy.deepcopy(cfg)
        v["llm"] = copy.deepcopy(b[model])
        out[name] = v
    for name, model in (("retry_coder", "dev:" + CODER), ("retry_qwen38", QWEN38)):
        v = copy.deepcopy(cfg)
        v["llm_chain"] = [chain_entry(b[model])]
        out[name] = v
    return out


if __name__ == "__main__":
    base = yaml.safe_load(Path(sys.argv[1]).read_text())
    outdir = Path(sys.argv[2])
    outdir.mkdir(parents=True, exist_ok=True)
    for name, cfg in variants(base, home=sys.argv[3] if len(sys.argv) > 3 else None).items():
        (outdir / f"{name}.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
        print(name)
