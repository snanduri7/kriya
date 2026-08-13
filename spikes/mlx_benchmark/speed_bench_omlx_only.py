"""Isolated oMLX-only pass: run this with Ollama's model unloaded (`ollama stop
qwen3-coder:30b`) and nothing else holding the model resident, so results aren't
confounded by memory/GPU contention from other backends running concurrently.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from prompts import PROMPTS
from speed_bench import run_omlx

RESULTS_DIR = Path(__file__).parent / "results"


def main():
    RESULTS_DIR.mkdir(exist_ok=True)
    results = []
    for prompt_spec in PROMPTS:
        print(f"[omlx]    {prompt_spec['id']} ...")
        r = run_omlx(prompt_spec)
        results.append(r)
        print(
            f"  ttft={r['ttft_s']}s  gen_tps={r['generation_tps']}  "
            f"prompt_tps={r['prompt_tps']}  tokens={r['generation_tokens']}  "
            f"total={r['total_time_s']}s  cached_tokens={r.get('cached_tokens')}"
        )

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = RESULTS_DIR / f"speed_bench_omlx_only_{ts}.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
