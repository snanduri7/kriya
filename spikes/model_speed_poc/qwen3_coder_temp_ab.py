"""Same temperature question as reasoning_effort_ab_v2.py's Arm A/C, mirrored
for qwen3-coder:30b - the current Kriya default, and the other half of the
temperature confound found in the qwen3.8:27b comparisons (every qwen3-coder
harness run so far used the eval harness's hardcoded temperature=0.2, while
qwen3.8:27b was tested at 0.7, its own HF card's instruct-mode recommendation).

qwen3-coder:30b's OWN official recommendation (confirmed earlier this session
via huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct) is temperature=0.7,
top_p=0.8, top_k=20, repetition_penalty=1.05 - already Kriya's packaged
default_config.yaml values AND this model's own Ollama Modelfile-baked
defaults. No reasoning_effort arm here - this model has no 'thinking'
capability (confirmed via `ollama show`'s capabilities list: completion/tools
only), so that axis doesn't apply.

Arm A: temperature=0.7 (vendor-recommended, matches default_config.yaml).
Arm C: temperature=0.2 (matches every actual harness batch run so far,
e.g. b-10q's kriya.yaml, which hardcodes this for eval-batch determinism,
not model-specific tuning).
top_p=0.8 held constant across both arms (already the shared default) so
temperature is the only variable, same discipline as the qwen3.8:27b version.

Run: .venv/bin/python3 spikes/model_speed_poc/qwen3_coder_temp_ab.py
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

MODEL = "qwen3-coder:30b"
OPENAI_URL = "http://localhost:11434/v1/chat/completions"
TIMEOUT_S = 600

SAMPLING_TEMP07 = {"temperature": 0.7, "top_p": 0.8}
SAMPLING_TEMP02 = {"temperature": 0.2, "top_p": 0.8}


def call_openai(prompt: str, max_tokens: int, sampling: dict) -> dict:
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "max_tokens": max_tokens,
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
    content = message.get("content") or ""
    usage = raw.get("usage", {})

    return {
        "elapsed_s": elapsed,
        "finish_reason": choice.get("finish_reason"),
        "output_text": content,
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


def run_arm(arm_label: str, sampling: dict) -> list[dict]:
    results = []
    for prompt_spec in PROMPTS:
        max_tokens = prompt_spec["max_tokens"]
        label = f"{arm_label} / {prompt_spec['id']} (budget={max_tokens})"
        print(f"--- {label} ---", flush=True)
        r = call_openai(prompt_spec["prompt"], max_tokens, sampling)
        r = evaluate(r, prompt_spec)
        r.update(arm=arm_label, prompt_id=prompt_spec["id"], sampling=sampling, max_tokens=max_tokens)
        if "error" in r:
            print(f"    -> ERROR ({r['elapsed_s']}s): {r['error'][:300]}", flush=True)
        else:
            print(
                f"    -> {r['elapsed_s']}s finish_reason={r['finish_reason']} "
                f"completion_tokens={r['completion_tokens']} status={r['status']}",
                flush=True,
            )
        results.append(r)
    return results


def main() -> None:
    all_results = []

    print("\n===== Arm A: temperature=0.7 (vendor-recommended, matches default_config.yaml) =====\n")
    all_results += run_arm("A_temp07", SAMPLING_TEMP07)

    print("\n===== Arm C: temperature=0.2 (matches actual eval harness batch runs) =====\n")
    all_results += run_arm("C_temp02", SAMPLING_TEMP02)

    out_dir = Path(__file__).parent / "results"
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"qwen3_coder_temp_ab_{ts}.json"
    out_path.write_text(json.dumps(all_results, indent=2))
    print(f"\nWrote {len(all_results)} results to {out_path}")

    print("\n=== Summary ===")
    print(f"{'arm':10s} {'prompt':16s} {'budget':>8s} {'elapsed_s':>10s} {'compl_tok':>10s} {'status':>28s}")
    for r in all_results:
        if "error" in r:
            print(f"{r['arm']:10s} {r['prompt_id']:16s} {r['max_tokens']:8d} {r['elapsed_s']:10.2f} {'-':>10s} {'API_ERROR':>28s}")
            continue
        print(
            f"{r['arm']:10s} {r['prompt_id']:16s} {r['max_tokens']:8d} {r['elapsed_s']:10.2f} "
            f"{str(r['completion_tokens']):>10s} {r['status']:>28s}"
        )


if __name__ == "__main__":
    main()
