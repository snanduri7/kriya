#!/usr/bin/env python3
"""MODEL-001 capability probe - evidence script, not production Kriya code.

Empirically measures the three ModelCapabilities fields
(kriya/config/config.py) that the class docstring says must never be
inferred from API shape: native_tool_calls, json_mode,
reliable_multiline_json. Three cheap live completions per model against the
local Ollama OpenAI-compatible endpoint. This is a bounded measurement, not
a capability research project - it answers exactly the question Kriya's own
ModelCapabilities schema requires an answer to, nothing more.

Frozen-before-pilot: run once for all three campaign models, output written
to capability_profiles.json with a timestamp, never revised after seeing
any campaign run's outcome (MODEL-001 spec: "No capability flag may be
changed after seeing campaign results merely to make a model pass").
"""
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone

BASE_URL = "http://localhost:11434/v1"

# kriya/core/llm.py's own documented floor for a model that emits hidden
# <think>...</think> reasoning regardless of the `reasoning` config flag
# (qwen3.6:35b-a3b is named explicitly in that file's comments as exactly
# this case) - mirrored here so the probe measures the same thing Kriya's
# production client actually observes, not an artifact of too-small a
# max_tokens budget getting consumed entirely by hidden reasoning.
REASONING_FLOOR_TOKENS = 12288


def _strip_think(content):
    return re.sub(r"<think>.*?</think>", "", content or "", flags=re.DOTALL).strip()


def _post(payload, timeout=180):
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _completion_content(payload, timeout=180):
    """Runs payload at a generous max_tokens; if content comes back empty after
    stripping a possible hidden <think> block (a real, cited failure mode, not
    speculative), retries once at Kriya's own REASONING_FLOOR_TOKENS - the same
    two-step mitigation kriya/core/llm.py applies in production."""
    resp = _post(payload, timeout=timeout)
    raw = resp["choices"][0]["message"].get("content", "")
    content = _strip_think(raw)
    if content:
        return content, resp
    retry_payload = dict(payload, max_tokens=REASONING_FLOOR_TOKENS)
    resp2 = _post(retry_payload, timeout=timeout)
    raw2 = resp2["choices"][0]["message"].get("content", "")
    return _strip_think(raw2), resp2


def probe_native_tool_calls(model):
    tools = [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a location",
            "parameters": {
                "type": "object",
                "properties": {"location": {"type": "string"}},
                "required": ["location"],
            },
        },
    }]
    try:
        resp = _post({
            "model": model,
            "messages": [{"role": "user", "content": "What's the weather in Boston? Use the get_weather tool."}],
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.2,
            "max_tokens": 512,
        })
        msg = resp["choices"][0]["message"]
        tool_calls = msg.get("tool_calls") or []
        ok = len(tool_calls) > 0 and tool_calls[0].get("function", {}).get("name") == "get_weather"
        return {"supported": bool(ok), "raw_tool_calls": tool_calls, "error": None}
    except Exception as e:  # noqa: BLE001 - probe must record failure, not crash the run
        return {"supported": None, "raw_tool_calls": None, "error": f"{type(e).__name__}: {e}"}


def probe_json_mode(model):
    try:
        content, _resp = _completion_content({
            "model": model,
            "messages": [{"role": "user", "content": 'Return a JSON object with exactly one key "ok" set to true.'}],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "max_tokens": 2048,
        })
        parsed = json.loads(content)
        ok = isinstance(parsed, dict) and parsed.get("ok") is True
        return {"supported": bool(ok), "raw_content": content, "error": None}
    except Exception as e:  # noqa: BLE001
        return {"supported": None, "raw_content": None, "error": f"{type(e).__name__}: {e}"}


def probe_reliable_multiline_json(model):
    prompt = (
        'Return a JSON object with exactly one key "note" whose value is the '
        'literal three-line string "line one\\nline two\\nline three" '
        '(a single JSON string containing two embedded newline characters, '
        "properly escaped as \\n - not three separate keys, not an array)."
    )
    try:
        content, _resp = _completion_content({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "max_tokens": 2048,
        })
        parsed = json.loads(content)
        note = parsed.get("note", "") if isinstance(parsed, dict) else ""
        ok = note.count("\n") == 2 and "line one" in note and "line three" in note
        return {"supported": bool(ok), "raw_content": content, "error": None}
    except Exception as e:  # noqa: BLE001
        return {"supported": None, "raw_content": None, "error": f"{type(e).__name__}: {e}"}


def main():
    models = sys.argv[1:]
    if not models:
        print("usage: probe_capabilities.py <model1> [model2] [model3]", file=sys.stderr)
        sys.exit(2)

    results = {"probed_at": datetime.now(timezone.utc).isoformat(), "base_url": BASE_URL, "models": {}}
    for model in models:
        print(f"=== probing {model} ===", file=sys.stderr)
        tool_result = probe_native_tool_calls(model)
        json_result = probe_json_mode(model)
        multiline_result = probe_reliable_multiline_json(model)
        results["models"][model] = {
            "native_tool_calls": tool_result,
            "json_mode": json_result,
            "reliable_multiline_json": multiline_result,
        }
        print(json.dumps(results["models"][model], indent=2), file=sys.stderr)

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
