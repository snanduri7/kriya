"""Corrected re-run of reasoning_effort_ab.py, qwen3.8:27b ONLY, fixing two
methodological gaps the first pass had (per direct user feedback after
reading the model's real HF card, https://huggingface.co/Qwen/Qwen3.8-27B):

1. The first A/B ran EVERY arm (including reasoning_effort=none) under
   Ollama's pulled Modelfile sampling defaults - temperature=1, top_p=0.95,
   top_k=20 - which the HF card confirms is the THINKING-mode recommended
   profile, not the instruct/non-thinking one. The card's actual instruct-
   mode recommendation is temperature=0.7, top_p=0.80, presence_penalty=1.5.
   Arm A here uses the correct instruct-mode sampling for reasoning_effort=
   none, so a "does none actually work" verdict isn't confounded by running
   the wrong sampling profile for that mode.

2. The first A/B used Kriya's real (tight, unpadded) per-task token
   budgets for every effort level, which starved every non-"none" arm
   before reaching content - informative for "does this fit Kriya's budgets
   today," but NOT a fair test of the model's actual claimed capability
   (SWE-bench Pro 61.7%, LiveCodeBench 90.3%, etc.), which the card says is
   measured with thinking ON (its own stated default is reasoning_effort=
   xhigh) and, implicitly, enough budget to actually finish. Arm B here
   runs thinking-mode xhigh - the card's own default - with the SAME
   REASONING_HEADROOM_TOKENS=4096 pad bench.py already established for a
   fair qwen3.6 think:true comparison, using the card's thinking-mode
   sampling (temperature=1, top_p=0.95, top_k=20 - already Ollama's pulled
   default, left unset here to use exactly that).

Only qwen3.8:27b - explicit user scope, no other model.

Run: .venv/bin/python3 spikes/model_speed_poc/reasoning_effort_ab_v2.py
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
TIMEOUT_S = 1800
REASONING_HEADROOM_TOKENS = 4096  # same pad bench.py uses for think:true arms

# Card's own recommended instruct/non-thinking sampling profile.
INSTRUCT_SAMPLING = {"temperature": 0.7, "top_p": 0.80, "presence_penalty": 1.5}

# Same profile but temperature=0.2 - matches the eval harness's own hardcoded
# primary-llm temperature (spikes/eval_harness/run_harness.py::_write_config,
# deliberately used for every model for batch-determinism, not model-specific
# tuning). Every qwen3-coder:30b harness comparison so far ran at 0.2 while
# qwen3.8:27b ran at 0.7 (the card's actual recommendation) - a real
# temperature confound in those comparisons. This arm isolates temperature as
# the only variable (top_p/presence_penalty unchanged) to see whether it
# matters, independent of "what does qwen3.8:27b's own card recommend."
INSTRUCT_SAMPLING_TEMP02 = {"temperature": 0.2, "top_p": 0.80, "presence_penalty": 1.5}


def call_openai(prompt: str, max_tokens: int, reasoning_effort: str, sampling: dict) -> dict:
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "max_tokens": max_tokens,
        "reasoning_effort": reasoning_effort,
        **sampling,
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
        "output_text": content,
        "output_chars": len(content),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
    }


def evaluate(r: dict, prompt_spec: dict) -> dict:
    if "error" in r:
        r["status"] = "API_ERROR"
        return r
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
    r["status"] = (
        "NO_CONTENT_BUDGET_EXHAUSTED" if not got_content
        else "FAIL_SYNTAX" if not syntax_ok
        else "FAIL_FUNCTIONAL" if functional_passed is False
        else "PASS"
    )
    return r


def run_arm(arm_label: str, reasoning_effort: str, sampling: dict, budget_fn) -> list[dict]:
    results = []
    for prompt_spec in PROMPTS:
        max_tokens = budget_fn(prompt_spec["max_tokens"])
        label = f"{arm_label} / {prompt_spec['id']} (budget={max_tokens})"
        print(f"--- {label} ---", flush=True)
        r = call_openai(prompt_spec["prompt"], max_tokens, reasoning_effort, sampling)
        r = evaluate(r, prompt_spec)
        r.update(arm=arm_label, prompt_id=prompt_spec["id"], reasoning_effort=reasoning_effort,
                  sampling=sampling, max_tokens=max_tokens)
        if "error" in r:
            print(f"    -> ERROR ({r['elapsed_s']}s): {r['error'][:300]}", flush=True)
        else:
            print(
                f"    -> {r['elapsed_s']}s finish_reason={r['finish_reason']} "
                f"thinking_chars={r['thinking_chars']} completion_tokens={r['completion_tokens']} "
                f"status={r['status']}",
                flush=True,
            )
        results.append(r)
    return results


def main() -> None:
    all_results = []

    print("\n===== Arm A: reasoning_effort=none, CORRECTED instruct-mode sampling (temp=0.7, card's own recommendation), normal budgets =====\n")
    all_results += run_arm("A_instruct_none_temp07", "none", INSTRUCT_SAMPLING, lambda b: b)

    print("\n===== Arm C: reasoning_effort=none, temp=0.2 (matches eval harness's hardcoded primary-llm temperature), normal budgets =====\n")
    all_results += run_arm("C_instruct_none_temp02", "none", INSTRUCT_SAMPLING_TEMP02, lambda b: b)

    # Arm B (reasoning_effort=xhigh, padded budget) deliberately skipped this
    # run - already have that data from the prior pass (docs/kriya_backlog_
    # and_lessons.md, 2026-08-17), re-running it adds nothing new and costs
    # 15-25+ minutes for zero new information. This run is scoped to the
    # temperature question only.

    out_dir = Path(__file__).parent / "results"
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"reasoning_effort_ab_v2_{ts}.json"
    out_path.write_text(json.dumps(all_results, indent=2))
    print(f"\nWrote {len(all_results)} results to {out_path}")

    print("\n=== Summary ===")
    print(f"{'arm':32s} {'prompt':16s} {'budget':>8s} {'elapsed_s':>10s} {'think_chars':>12s} {'compl_tok':>10s} {'status':>28s}")
    for r in all_results:
        if "error" in r:
            print(f"{r['arm']:32s} {r['prompt_id']:16s} {r['max_tokens']:8d} {r['elapsed_s']:10.2f} {'-':>12s} {'-':>10s} {'API_ERROR':>28s}")
            continue
        print(
            f"{r['arm']:32s} {r['prompt_id']:16s} {r['max_tokens']:8d} {r['elapsed_s']:10.2f} "
            f"{r['thinking_chars']:12d} {str(r['completion_tokens']):>10s} {r['status']:>28s}"
        )


if __name__ == "__main__":
    main()
