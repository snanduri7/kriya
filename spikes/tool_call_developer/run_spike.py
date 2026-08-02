"""Prototype: does moving Kriya's Developer stage to a tool-call-per-file loop
actually work against Kriya's own local models?

This is deliberately narrow, per the plan: one file, one tool call, verify
tool-calling is even reliable on the local Ollama backend before considering any
real architecture change. Not wired into kriya's core - standalone, throwaway.

Compares, for a single one-file coding task, against each candidate model:
  1. "tool_call" approach: expose a write_file(filepath, content) tool, let the
     model call it, read the file back from message.tool_calls.
  2. "tool_call_fallback" approach: same request, but if tool_calls is empty,
     defensively regex-parse message.content for a write_file-shaped attempt
     (mirrors Kriya's existing DeveloperAgent._extract_json_value defensive
     parsing philosophy - "the model tried to do the right thing but the
     backend didn't structure it").
  3. "json_mode" approach: Kriya's actual current mechanism - one completion,
     json_mode requested, asking for {"filepath": ..., "content": ...} in prose.

Reports timing and whether each approach actually recovered valid, correct file
content, per model.
"""
import ast
import json
import re
import time

import httpx

OLLAMA_URL = "http://localhost:11434/v1/chat/completions"
MODELS = ["qwen3-coder:30b", "qwen3.6:35b"]

TASK = (
    "Create a Python file stack.py implementing a Stack class with push, pop, peek, "
    "and is_empty methods, using a list internally. pop and peek must raise "
    "IndexError with a clear message when called on an empty stack."
)

WRITE_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "Write content to a file",
        "parameters": {
            "type": "object",
            "properties": {
                "filepath": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["filepath", "content"],
        },
    },
}

FALLBACK_PATTERN = re.compile(
    r"<function=write_file>\s*"
    r"<parameter=filepath>\s*(?P<filepath>.*?)\s*</parameter>\s*"
    r"<parameter=content>\s*(?P<content>.*?)\s*</parameter>\s*"
    r"</function>",
    re.DOTALL,
)


def _post(payload: dict) -> tuple[dict, float]:
    start = time.monotonic()
    resp = httpx.post(OLLAMA_URL, json=payload, timeout=180)
    elapsed = time.monotonic() - start
    resp.raise_for_status()
    return resp.json(), elapsed


def _validate(filepath: str, content: str) -> str:
    if not filepath or not content:
        return "empty filepath/content"
    try:
        ast.parse(content)
    except SyntaxError as e:
        return f"invalid Python syntax: {e}"
    required = ["def push", "def pop", "def peek", "def is_empty", "IndexError"]
    missing = [r for r in required if r not in content]
    if missing:
        return f"missing required elements: {missing}"
    return "OK"


def run_tool_call(model: str) -> dict:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a coding assistant. Use the write_file tool to create the requested file."},
            {"role": "user", "content": TASK},
        ],
        "tools": [WRITE_FILE_TOOL],
        "tool_choice": "auto",
    }
    data, elapsed = _post(payload)
    message = data["choices"][0]["message"]
    tool_calls = message.get("tool_calls") or []

    if tool_calls:
        try:
            args = json.loads(tool_calls[0]["function"]["arguments"])
            filepath, content = args.get("filepath", ""), args.get("content", "")
            return {
                "path_used": "tool_calls (proper)",
                "elapsed": elapsed,
                "filepath": filepath,
                "validation": _validate(filepath, content),
            }
        except Exception as e:
            return {"path_used": "tool_calls (proper, but unparseable)", "elapsed": elapsed, "filepath": None, "validation": f"error: {e}"}

    # No proper tool_calls - see if a defensive fallback parse of raw content
    # could rescue it anyway (mirrors Kriya's existing JSON-extraction philosophy).
    content_text = message.get("content") or ""
    m = FALLBACK_PATTERN.search(content_text)
    if m:
        filepath, content = m.group("filepath"), m.group("content")
        return {
            "path_used": "content fallback-parsed (tool_calls empty)",
            "elapsed": elapsed,
            "filepath": filepath,
            "validation": _validate(filepath, content),
        }

    return {
        "path_used": "NEITHER - tool_calls empty and content didn't match fallback pattern",
        "elapsed": elapsed,
        "filepath": None,
        "validation": f"raw content: {content_text[:200]!r}",
    }


def run_json_mode(model: str) -> dict:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    'You are the Kriya Developer Agent. Return ONLY a JSON object: '
                    '{"filepath": "...", "content": "..."}. No markdown fences, no commentary.'
                ),
            },
            {"role": "user", "content": TASK},
        ],
        "response_format": {"type": "json_object"},
    }
    data, elapsed = _post(payload)
    content_text = data["choices"][0]["message"].get("content") or ""
    try:
        parsed = json.loads(content_text)
        filepath, content = parsed.get("filepath", ""), parsed.get("content", "")
        return {"path_used": "json_mode", "elapsed": elapsed, "filepath": filepath, "validation": _validate(filepath, content)}
    except Exception as e:
        return {"path_used": "json_mode (unparseable)", "elapsed": elapsed, "filepath": None, "validation": f"error: {e}. raw: {content_text[:200]!r}"}


def main():
    for model in MODELS:
        print(f"\n{'=' * 70}\nMODEL: {model}\n{'=' * 70}")

        print("\n-- tool_call approach --")
        r = run_tool_call(model)
        print(f"  path used : {r['path_used']}")
        print(f"  elapsed   : {r['elapsed']:.2f}s")
        print(f"  filepath  : {r['filepath']}")
        print(f"  validation: {r['validation']}")

        print("\n-- json_mode approach (Kriya's current mechanism) --")
        r = run_json_mode(model)
        print(f"  path used : {r['path_used']}")
        print(f"  elapsed   : {r['elapsed']:.2f}s")
        print(f"  filepath  : {r['filepath']}")
        print(f"  validation: {r['validation']}")


if __name__ == "__main__":
    main()
