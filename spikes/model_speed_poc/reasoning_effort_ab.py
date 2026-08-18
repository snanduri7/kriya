"""Real A/B: does qwen3.8:27b's `reasoning_effort` knob (confirmed reachable
via Ollama's OpenAI-compat surface in reasoning_effort_poc.py) actually buy
back speed within Kriya's REAL per-prompt token budgets, without losing
correctness? qwen3.8:27b ONLY - no other model, per explicit scope.

Uses /v1/chat/completions (not native /api/chat) deliberately: Kriya's own
llm.base_url (default_config.yaml) points at the OpenAI-compat surface, so
this measures the exact mechanism config.llm.extra_body would actually
exercise, not a native-API approximation of it.

Sweeps reasoning_effort in ("none", "low", "medium", "high", "xhigh") across
the SAME 3 fixed prompts bench.py already uses (short_fn/medium_refactor/
longer_task), at THEIR existing max_tokens (256/512/1024) - deliberately NOT
padded with extra reasoning headroom the way the earlier qwen3.6 A/B was.
That's the point: Kriya's real retry loop uses these real budgets today, so
"does a heavy-reasoning run even finish within budget" is itself the
production-relevant question, not an artifact to correct for.

Run: .venv/bin/python3 spikes/model_speed_poc/reasoning_effort_ab.py
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from bench import check_syntax, extract_code, run_functional_test  # noqa: E402
from prompts import PROMPTS  # noqa: E402

MODEL = "qwen3.8:27b"
OPENAI_URL = "http://localhost:11434/v1/chat/completions"
LEVELS = ["none", "low", "medium", "high", "xhigh"]
TIMEOUT_S = 900


def call_openai(prompt: str, max_tokens: int, reasoning_effort: str) -> dict:
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "max_tokens": max_tokens,
        "reasoning_effort": reasoning_effort,
    }
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        OPENAI_URL, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": e.read().decode("utf-8", errors="replace"), "elapsed_s": round(time.perf_counter() - start, 2)}
    elapsed = round(time.perf_counter() - start, 2)

    choice = (raw.get("choices") or [{}])[0]
    message = choice.get("message", {})
    thinking = message.get("reasoning") or message.get("reasoning_content") or message.get("thinking") or ""
    content = message.get("content") or ""
    usage = raw.get("usage", {})

    return {
        "elapsed_s": elapsed,
        "finish_reason": choice.get("finish_reason"),
        "thinking_chars": len(thinking),
        "thinking_preview": thinking[:200],
        "output_text": content,
        "output_chars": len(content),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
    }


def main() -> None:
    results = []
    for prompt_spec in PROMPTS:
        for level in LEVELS:
            label = f"{prompt_spec['id']} / reasoning_effort={level}"
            print(f"--- {label} ---", flush=True)
            r = call_openai(prompt_spec["prompt"], prompt_spec["max_tokens"], level)
            r["prompt_id"] = prompt_spec["id"]
            r["reasoning_effort"] = level
            r["max_tokens"] = prompt_spec["max_tokens"]

            if "error" in r:
                print(f"    -> ERROR ({r['elapsed_s']}s): {r['error'][:300]}", flush=True)
                results.append(r)
                continue

            code = extract_code(r["output_text"])
            syntax_ok, syntax_err = check_syntax(code)
            r["syntax_ok"] = syntax_ok
            r["syntax_err"] = syntax_err

            functional_passed = None
            if prompt_spec["functional_test"] and syntax_ok:
                functional_passed, detail = run_functional_test(code, prompt_spec["functional_test"])
                r["functional_detail"] = detail
            r["functional_passed"] = functional_passed

            got_content = bool(r["output_text"].strip())
            status = (
                "NO_CONTENT_BUDGET_EXHAUSTED" if not got_content
                else "FAIL_SYNTAX" if not syntax_ok
                else "FAIL_FUNCTIONAL" if functional_passed is False
                else "PASS"
            )
            r["status"] = status
            print(
                f"    -> {r['elapsed_s']}s finish_reason={r['finish_reason']} "
                f"thinking_chars={r['thinking_chars']} completion_tokens={r['completion_tokens']} "
                f"status={status}",
                flush=True,
            )
            results.append(r)

    out_dir = Path(__file__).parent / "results"
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"reasoning_effort_ab_{ts}.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {len(results)} results to {out_path}")

    print("\n=== Summary ===")
    header = f"{'prompt':16s} {'effort':8s} {'elapsed_s':>10s} {'think_chars':>12s} {'compl_tok':>10s} {'status':>28s}"
    print(header)
    for r in results:
        if "error" in r:
            print(f"{r['prompt_id']:16s} {r['reasoning_effort']:8s} {r['elapsed_s']:10.2f} {'-':>12s} {'-':>10s} {'API_ERROR':>28s}")
            continue
        print(
            f"{r['prompt_id']:16s} {r['reasoning_effort']:8s} {r['elapsed_s']:10.2f} "
            f"{r['thinking_chars']:12d} {str(r['completion_tokens']):>10s} {r['status']:>28s}"
        )


if __name__ == "__main__":
    main()
