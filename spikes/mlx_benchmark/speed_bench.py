"""Step 1: raw mlx-lm vs Ollama qwen3-coder:30b, inference speed only.

Usage:
    .venv/bin/python speed_bench.py

Requires:
    - `ollama serve` running locally with `qwen3-coder:30b` pulled
    - models/Qwen3-Coder-30B-A3B-Instruct-4bit/ downloaded (see README.md)
"""

import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from prompts import PROMPTS

MODEL_DIR = Path(__file__).parent / "models" / "Qwen3-Coder-30B-A3B-Instruct-4bit"
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen3-coder:30b"
OMLX_URL = "http://localhost:8000/v1/chat/completions"
OMLX_MODEL = "Qwen3-Coder-30B-A3B-Instruct-4bit"
RESULTS_DIR = Path(__file__).parent / "results"


def run_mlx(prompt_spec: dict, model, tokenizer) -> dict:
    from mlx_lm.generate import stream_generate

    messages = [{"role": "user", "content": prompt_spec["prompt"]}]
    prompt_text = tokenizer.apply_chat_template(messages, add_generation_prompt=True)

    start = time.perf_counter()
    ttft = None
    text = ""
    last_response = None
    for response in stream_generate(
        model, tokenizer, prompt_text, max_tokens=prompt_spec["max_tokens"]
    ):
        if ttft is None:
            ttft = time.perf_counter() - start
        text += response.text
        last_response = response
    total_time = time.perf_counter() - start

    return {
        "backend": "mlx-lm (direct)",
        "prompt_id": prompt_spec["id"],
        "ttft_s": round(ttft, 3) if ttft is not None else None,
        "total_time_s": round(total_time, 3),
        "prompt_tokens": last_response.prompt_tokens if last_response else None,
        "prompt_tps": round(last_response.prompt_tps, 1) if last_response else None,
        "generation_tokens": last_response.generation_tokens if last_response else None,
        "generation_tps": round(last_response.generation_tps, 1) if last_response else None,
        "peak_memory_gb": round(last_response.peak_memory, 2) if last_response else None,
        "output_text": text,
    }


def run_ollama(prompt_spec: dict) -> dict:
    payload = json.dumps(
        {
            "model": OLLAMA_MODEL,
            "messages": [{"role": "user", "content": prompt_spec["prompt"]}],
            "stream": True,
            "options": {"num_predict": prompt_spec["max_tokens"]},
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"}
    )

    start = time.perf_counter()
    ttft = None
    text = ""
    final = None
    with urllib.request.urlopen(req) as resp:
        for line in resp:
            if not line.strip():
                continue
            chunk = json.loads(line)
            if ttft is None and chunk.get("message", {}).get("content"):
                ttft = time.perf_counter() - start
            text += chunk.get("message", {}).get("content", "")
            if chunk.get("done"):
                final = chunk
    total_time = time.perf_counter() - start

    eval_count = final.get("eval_count") if final else None
    eval_duration_s = (final.get("eval_duration", 0) / 1e9) if final else None
    prompt_eval_count = final.get("prompt_eval_count") if final else None
    prompt_eval_duration_s = (final.get("prompt_eval_duration", 0) / 1e9) if final else None

    return {
        "backend": "ollama qwen3-coder:30b",
        "prompt_id": prompt_spec["id"],
        "ttft_s": round(ttft, 3) if ttft is not None else None,
        "total_time_s": round(total_time, 3),
        "prompt_tokens": prompt_eval_count,
        "prompt_tps": round(prompt_eval_count / prompt_eval_duration_s, 1)
        if prompt_eval_count and prompt_eval_duration_s
        else None,
        "generation_tokens": eval_count,
        "generation_tps": round(eval_count / eval_duration_s, 1)
        if eval_count and eval_duration_s
        else None,
        "peak_memory_gb": None,
        "output_text": text,
    }


def run_omlx(prompt_spec: dict) -> dict:
    payload = json.dumps(
        {
            "model": OMLX_MODEL,
            "messages": [{"role": "user", "content": prompt_spec["prompt"]}],
            "stream": True,
            "max_tokens": prompt_spec["max_tokens"],
            "stream_options": {"include_usage": True},
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        OMLX_URL, data=payload, headers={"Content-Type": "application/json"}
    )

    start = time.perf_counter()
    ttft = None
    text = ""
    usage = None
    with urllib.request.urlopen(req) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8").strip()
            if not line or not line.startswith("data: "):
                continue
            data = line[len("data: ") :]
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            if chunk.get("model") == "keepalive":
                continue
            choices = chunk.get("choices") or []
            if choices:
                delta_content = choices[0].get("delta", {}).get("content")
                if delta_content:
                    if ttft is None:
                        ttft = time.perf_counter() - start
                    text += delta_content
            if chunk.get("usage"):
                usage = chunk["usage"]
    total_time = time.perf_counter() - start

    return {
        "backend": "omlx (OpenAI-compatible API)",
        "prompt_id": prompt_spec["id"],
        "ttft_s": round(usage["time_to_first_token"], 3)
        if usage and usage.get("time_to_first_token") is not None
        else (round(ttft, 3) if ttft is not None else None),
        "total_time_s": round(total_time, 3),
        "prompt_tokens": usage.get("prompt_tokens") if usage else None,
        "prompt_tps": round(usage["prompt_tokens_per_second"], 1)
        if usage and usage.get("prompt_tokens_per_second") is not None
        else None,
        "generation_tokens": usage.get("completion_tokens") if usage else None,
        "generation_tps": round(usage["generation_tokens_per_second"], 1)
        if usage and usage.get("generation_tokens_per_second") is not None
        else None,
        "cached_tokens": usage.get("prompt_tokens_details", {}).get("cached_tokens")
        if usage
        else None,
        "peak_memory_gb": None,
        "output_text": text,
    }


def main():
    RESULTS_DIR.mkdir(exist_ok=True)

    if not MODEL_DIR.exists():
        raise SystemExit(f"Model not found at {MODEL_DIR} — see README.md setup step.")

    print(f"Loading MLX model from {MODEL_DIR} ...")
    from mlx_lm import load

    model, tokenizer = load(str(MODEL_DIR))
    print("Loaded.\n")

    results = []
    for prompt_spec in PROMPTS:
        print(f"[mlx-lm]  {prompt_spec['id']} ...")
        r = run_mlx(prompt_spec, model, tokenizer)
        results.append(r)
        print(
            f"  ttft={r['ttft_s']}s  gen_tps={r['generation_tps']}  "
            f"tokens={r['generation_tokens']}  total={r['total_time_s']}s"
        )

        print(f"[ollama]  {prompt_spec['id']} ...")
        r = run_ollama(prompt_spec)
        results.append(r)
        print(
            f"  ttft={r['ttft_s']}s  gen_tps={r['generation_tps']}  "
            f"tokens={r['generation_tokens']}  total={r['total_time_s']}s"
        )

        print(f"[omlx]    {prompt_spec['id']} ...")
        r = run_omlx(prompt_spec)
        results.append(r)
        print(
            f"  ttft={r['ttft_s']}s  gen_tps={r['generation_tps']}  "
            f"tokens={r['generation_tokens']}  total={r['total_time_s']}s  "
            f"cached_tokens={r.get('cached_tokens')}"
        )
        print()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = RESULTS_DIR / f"speed_bench_{ts}.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"Wrote {out_path}")

    print("\n--- Summary (generation tokens/sec | prompt tokens/sec) ---")
    for prompt_spec in PROMPTS:
        pid = prompt_spec["id"]
        row = {r["backend"]: r for r in results if r["prompt_id"] == pid}
        for backend in ["mlx-lm (direct)", "ollama qwen3-coder:30b", "omlx (OpenAI-compatible API)"]:
            r = row.get(backend, {})
            print(
                f"{pid:16s} {backend:30s}  gen_tps={r.get('generation_tps')!s:>8}  "
                f"prompt_tps={r.get('prompt_tps')!s:>8}"
            )


if __name__ == "__main__":
    main()
