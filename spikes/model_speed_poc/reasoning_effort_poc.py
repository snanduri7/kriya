"""Quick POC: can qwen3.8:27b's reasoning_effort be controlled via the API,
through both Ollama's native /api/chat and its OpenAI-compatible
/v1/chat/completions surface?

Motivation (see kriya_backlog_and_lessons.md): qwen3.8:27b's chat template
resolves reasoning_effort with a Jinja default of 'xhigh' unless a caller
passes an override, and only accepts ('xhigh', 'medium', 'low') - anything
else trips the template's own raise_exception(). `ollama show qwen3.8:27b`
reports RENDERER qwen3.8 / PARSER qwen3.5 - Ollama 0.32.14 renders this
model through a built-in Go renderer, not the raw Jinja template directly,
so it's unconfirmed whether/how that renderer exposes the reasoning_effort
knob. This script finds out empirically rather than guessing, by firing a
small reasoning-flavored prompt at every plausible request shape and
recording what actually comes back - including outright errors, which is a
valid and informative result (proves that shape is rejected).

Deliberately non-streaming (stream: false) - simplicity over the perf
rigor bench.py needs, since this is a "does the knob exist" probe, not a
speed benchmark. num_predict/max_tokens is capped small so every variant
finishes quickly even under the heaviest (xhigh) effort - a capped budget
still lets a qualitative effort difference show (thinking text content and
length), it just may truncate mid-thought for the heaviest settings, which
is fine for this question.

Run: .venv/bin/python3 spikes/model_speed_poc/reasoning_effort_poc.py
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

MODEL = "qwen3.8:27b"
NATIVE_URL = "http://localhost:11434/api/chat"
OPENAI_URL = "http://localhost:11434/v1/chat/completions"
MAX_TOKENS = 200
TIMEOUT_S = 240

# Small enough to be fast, but a genuine reasoning task (not pure recall) -
# gives reasoning effort something real to scale with if the knob works.
PROMPT = (
    "A train travels 60 miles in 45 minutes. What is its average speed in "
    "miles per hour? Show your reasoning, then give the final number on its "
    "own line as 'ANSWER: <number>'."
)


def post_json(url: str, body: dict) -> tuple[int | None, dict | None, str | None]:
    """Returns (http_status, parsed_json, error_text). Never raises -
    a rejected/malformed request is itself a result worth recording."""
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(raw), None
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw), None
        except json.JSONDecodeError:
            return e.code, None, raw
    except Exception as e:  # noqa: BLE001 - genuinely want any failure shape here
        return None, None, f"{type(e).__name__}: {e}"


def run_native(label: str, think_value) -> dict:
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": False,
        "options": {"num_predict": MAX_TOKENS},
    }
    if think_value is not None:
        body["think"] = think_value
    return _run("native", label, NATIVE_URL, body)


def run_openai(label: str, extra_fields: dict) -> dict:
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": False,
        "max_tokens": MAX_TOKENS,
        **extra_fields,
    }
    return _run("openai_compat", label, OPENAI_URL, body)


def _run(surface: str, label: str, url: str, body: dict) -> dict:
    print(f"--- {surface}: {label} ---  request extra fields: "
          f"{ {k: v for k, v in body.items() if k not in ('model', 'messages', 'stream')} }")
    start = time.perf_counter()
    status, parsed, err_text = post_json(url, body)
    elapsed = round(time.perf_counter() - start, 2)

    result = {
        "surface": surface,
        "label": label,
        "request_extra_fields": {k: v for k, v in body.items() if k not in ("model", "messages", "stream")},
        "http_status": status,
        "elapsed_s": elapsed,
        "raw_error_text": err_text,
    }

    if parsed is None:
        print(f"    -> no parsed JSON. status={status} error={err_text!r}")
        result["thinking_text"] = None
        result["output_text"] = None
        return result

    # Native /api/chat shape: {"message": {"thinking": ..., "content": ...}, ...}
    # OpenAI-compat shape: {"choices": [{"message": {...}}], ...} - Ollama's
    # OpenAI surface may or may not surface a 'reasoning'/'thinking' field on
    # the message; capture whatever is actually there rather than assuming.
    if surface == "native":
        message = parsed.get("message", {})
        thinking = message.get("thinking")
        content = message.get("content")
    else:
        choices = parsed.get("choices") or [{}]
        message = choices[0].get("message", {})
        thinking = message.get("thinking") or message.get("reasoning") or message.get("reasoning_content")
        content = message.get("content")

    api_error = parsed.get("error")
    result["api_error"] = api_error
    result["thinking_text"] = thinking
    result["thinking_chars"] = len(thinking) if thinking else 0
    result["output_text"] = content
    result["full_response_keys"] = list(parsed.keys())

    if api_error:
        print(f"    -> API ERROR: {api_error}")
    else:
        print(f"    -> {elapsed}s, thinking_chars={result['thinking_chars']}, "
              f"output_preview={(content or '')[:120]!r}")
        if thinking:
            print(f"       thinking_preview={thinking[:200]!r}")

    return result


def main() -> None:
    results = []

    # --- Native /api/chat: sweep every plausible `think` shape ---
    results.append(run_native("no_think_field_baseline", None))
    results.append(run_native("think_true", True))
    results.append(run_native("think_false", False))
    for level in ("low", "medium", "high", "xhigh"):
        results.append(run_native(f"think_str_{level}", level))

    # --- OpenAI-compat /v1/chat/completions: sweep plausible field names ---
    results.append(run_openai("baseline_no_extra_fields", {}))
    results.append(run_openai("reasoning_effort_low", {"reasoning_effort": "low"}))
    results.append(run_openai("reasoning_effort_medium", {"reasoning_effort": "medium"}))
    results.append(run_openai("reasoning_effort_xhigh", {"reasoning_effort": "xhigh"}))
    results.append(run_openai("reasoning_effort_INVALID_should_error_if_validated", {"reasoning_effort": "bogus"}))
    results.append(run_openai("think_true", {"think": True}))
    results.append(run_openai("think_false", {"think": False}))
    results.append(run_openai("think_str_medium", {"think": "medium"}))

    out_dir = Path(__file__).parent / "results"
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"reasoning_effort_poc_{ts}.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {len(results)} results to {out_path}")

    print("\n=== Summary: thinking_chars per variant (proxy for effort actually applied) ===")
    for r in results:
        marker = "ERROR" if r.get("api_error") or r.get("raw_error_text") else "ok"
        print(f"  [{marker:5s}] {r['surface']:13s} {r['label']:45s} "
              f"status={r['http_status']!s:5s} elapsed={r['elapsed_s']:6.2f}s "
              f"thinking_chars={r.get('thinking_chars')}")


if __name__ == "__main__":
    main()
