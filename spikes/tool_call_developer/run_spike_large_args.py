"""Re-test of tool-calling reliability (2026-08-03), extending the original
run_spike.py in one specific direction: a LARGE file-content tool-call
argument, closer to Kriya's real Developer workload (a multi-hundred-line
Java file) than the original spike's small Stack class. This directly probes
the failure mode the original spike's research flagged as most relevant but
never actually tested: ollama/ollama#14570, which reports the qwen3-coder
parser returning HTTP 500 when a tool call's arguments get large.

Also re-runs the original small-task comparison for a like-for-like check
against the recorded 2026-08-01/02 result, in case Ollama version drift
(0.32.5 as of this run) changed anything.

Run: .venv/bin/python spikes/tool_call_developer/run_spike_large_args.py
"""
import ast
import json
import re
import time

import httpx

OLLAMA_URL = "http://localhost:11434/v1/chat/completions"
MODELS = ["qwen3-coder:30b", "qwen3.6:35b", "devstral-small-2:24b"]

SMALL_TASK = (
    "Create a Python file stack.py implementing a Stack class with push, pop, peek, "
    "and is_empty methods, using a list internally. pop and peek must raise "
    "IndexError with a clear message when called on an empty stack."
)

LARGE_TASK = (
    "Create a Java file IntegrationApp.java (package com.example) that: "
    "(1) defines a Person class with String name and int age fields, a constructor, "
    "getters, and a toString() method; "
    "(2) starts an embedded Apache Ignite cache node using Ignition.start(); "
    "(3) starts an embedded Apache Qpid broker using SystemLauncher; "
    "(4) sends a Person object as a JMS TextMessage (JSON-serialized) to a queue "
    "named 'orders.queue' via a JMS client; "
    "(5) consumes the message back synchronously, deserializes it into a Person; "
    "(6) puts the Person into the Ignite cache under key 'Order-2002'; "
    "(7) reads it back from the cache and logs it using an slf4j Logger "
    "(not System.out.println); "
    "(8) has a main() method that runs this whole flow end to end, with proper "
    "try/finally cleanup of both the broker and the Ignite node. "
    "Write the COMPLETE, real, compilable file - do not abbreviate or omit any part."
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


def _post(payload: dict) -> tuple[dict | None, float, str | None]:
    start = time.monotonic()
    try:
        resp = httpx.post(OLLAMA_URL, json=payload, timeout=300)
        elapsed = time.monotonic() - start
        if resp.status_code != 200:
            return None, elapsed, f"HTTP {resp.status_code}: {resp.text[:300]}"
        return resp.json(), elapsed, None
    except Exception as e:
        elapsed = time.monotonic() - start
        return None, elapsed, f"request failed: {e}"


def _validate_python(filepath: str, content: str) -> str:
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


def _validate_java(filepath: str, content: str) -> str:
    if not filepath or not content:
        return "empty filepath/content"
    required = [
        "class Person", "Ignition.start", "SystemLauncher", "orders.queue",
        "Order-2002", "org.slf4j.Logger", "public static void main",
    ]
    missing = [r for r in required if r not in content]
    if missing:
        return f"missing required elements: {missing} (content len={len(content)})"
    return f"OK (content len={len(content)})"


def run_tool_call(model: str, task: str, validator) -> dict:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a coding assistant. Use the write_file tool to create the requested file."},
            {"role": "user", "content": task},
        ],
        "tools": [WRITE_FILE_TOOL],
        "tool_choice": "auto",
    }
    data, elapsed, err = _post(payload)
    if err:
        return {"path_used": "REQUEST ERROR", "elapsed": elapsed, "filepath": None, "validation": err}

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
                "validation": validator(filepath, content),
            }
        except Exception as e:
            return {"path_used": "tool_calls (proper, but unparseable)", "elapsed": elapsed, "filepath": None, "validation": f"error: {e}"}

    content_text = message.get("content") or ""
    m = FALLBACK_PATTERN.search(content_text)
    if m:
        filepath, content = m.group("filepath"), m.group("content")
        return {
            "path_used": "content fallback-parsed (tool_calls empty)",
            "elapsed": elapsed,
            "filepath": filepath,
            "validation": validator(filepath, content),
        }

    return {
        "path_used": "NEITHER - tool_calls empty and content didn't match fallback pattern",
        "elapsed": elapsed,
        "filepath": None,
        "validation": f"raw content ({len(content_text)} chars): {content_text[:200]!r}",
    }


def run_json_mode(model: str, task: str, validator) -> dict:
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
            {"role": "user", "content": task},
        ],
        "response_format": {"type": "json_object"},
    }
    data, elapsed, err = _post(payload)
    if err:
        return {"path_used": "REQUEST ERROR", "elapsed": elapsed, "filepath": None, "validation": err}

    content_text = data["choices"][0]["message"].get("content") or ""
    try:
        parsed = json.loads(content_text)
        filepath, content = parsed.get("filepath", ""), parsed.get("content", "")
        return {"path_used": "json_mode", "elapsed": elapsed, "filepath": filepath, "validation": validator(filepath, content)}
    except Exception as e:
        return {"path_used": "json_mode (unparseable)", "elapsed": elapsed, "filepath": None, "validation": f"error: {e}. raw len={len(content_text)}"}


def report(label: str, r: dict):
    print(f"\n-- {label} --")
    print(f"  path used : {r['path_used']}")
    print(f"  elapsed   : {r['elapsed']:.2f}s")
    print(f"  filepath  : {r['filepath']}")
    print(f"  validation: {r['validation']}")


def main():
    for model in MODELS:
        print(f"\n{'=' * 70}\nMODEL: {model}\n{'=' * 70}")

        print("\n### SMALL TASK (like-for-like re-check of the original spike) ###")
        report("tool_call, small", run_tool_call(model, SMALL_TASK, _validate_python))
        report("json_mode, small", run_json_mode(model, SMALL_TASK, _validate_python))

        print("\n### LARGE TASK (multi-hundred-line Java file - the real Kriya workload shape) ###")
        report("tool_call, LARGE", run_tool_call(model, LARGE_TASK, _validate_java))
        report("json_mode, LARGE", run_json_mode(model, LARGE_TASK, _validate_java))


if __name__ == "__main__":
    main()
