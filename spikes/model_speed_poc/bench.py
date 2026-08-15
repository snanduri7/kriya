"""Speed + basic-accuracy comparison across locally pulled Ollama models -
including a real reasoning-on vs reasoning-off A/B for the thinking-capable
qwen3.6 models.

Question: which of these already-pulled models is actually fastest/most
correct for real Kriya-shaped code-gen work, and specifically: does
qwen3.6's native "thinking" mode earn its latency cost on a code-gen task,
or not?

The first pass at this (see results/ for the original run, or git history)
accidentally only ever tested "thinking on, starved for token budget" -
Ollama enables thinking by default for a thinking-capable model unless you
explicitly pass think:false, and the original prompt budgets (256-1024
tokens) turned out to be nowhere near enough for qwen3.6 to finish
reasoning AND produce content, so every qwen3.6 result came back with zero
real output. That was a benchmark bug, not a finding about the model. This
version fixes it two ways: (1) explicit think:true/think:false per run
instead of relying on Ollama's default, (2) a separate, generous token
budget for the think:true arm (REASONING_HEADROOM_TOKENS on top of the
prompt's normal content budget) so reasoning actually gets a fair chance to
finish. Thinking and content tokens are now also tracked separately (Ollama
streams them as distinct message fields - `thinking` vs `content` on the
native /api/chat endpoint used here), instead of conflating them into one
"generation_tokens" number like the first pass did.

Usage:
    python3 bench.py

Requires:
    - `ollama serve` running locally (this machine's setup: Ollama.app)
    - Models already pulled - confirmed present before writing this:
        qwen3.6:27b, qwen3-coder:30b, qwen3.6:35b-a3b-q4_K_M
      Models added later, NOT YET PULLED as of the version of this file that
      added them (`ollama pull <tag>` first, or trim MODELS/THINKING_MODELS/
      NON_THINKING_MODELS below to skip them):
        qwen3.8:27b, qwen3.8:27b-mlx      (released 2026-08-14, unverified)
        qwen3-coder:30b-a3b-q8_0          (32GB - Q8_0 of the existing qwen3-coder:30b,
                                            for a direct Q4-vs-Q8 comparison)

Standard library only - no venv needed, unlike spikes/mlx_benchmark (which
needs mlx-lm). Each model is loaded fresh into VRAM the first time it's
called; a warm-up call (untimed) is issued per model before the timed runs
so first-prompt numbers aren't skewed by model-load time.

Safety note on the functional-test step: `short_fn`'s generated code is
executed via subprocess (isolated from this script's own process, 10s
timeout, no network access implied by the task itself) purely to check
correctness of LOCALLY GENERATED code from a LOCAL model on YOUR OWN
machine - the same trust boundary as running any other local dev-loop
script. See prompts.py for why only that one prompt gets a functional
test rather than all three.
"""

import ast
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from prompts import PROMPTS

OLLAMA_URL = "http://localhost:11434/api/chat"
# Models that support native "thinking" mode - these get run twice per
# prompt (think:false and think:true). Everything else in MODELS runs once,
# with no think param sent at all (not a thinking-capable model, so the
# param is meaningless for it).
#
# qwen3.8:27b / qwen3.8:27b-mlx: NOT YET PULLED OR TESTED (as of 2026-08-15,
# ~24h after Qwen3.8-27B's release) - added here per direct request to wire
# the model in, not to run it. Placed in THINKING_MODELS on the strength of
# its own model description ("flexible thinking control"), unverified by any
# curl trace the way qwen3.6's thinking field was confirmed earlier this
# session - if it turns out NOT to expose message.thinking/delta.reasoning
# the same way, the think:true arm will just look identical to think:false
# rather than error, so this is a safe default guess either way. The -mlx
# tag is Ollama's own official library tag (served through Ollama's normal
# API either way; distinct from the raw-MLX-runtime comparison spikes/mlx_
# benchmark/ does), so no separate MLX-specific plumbing was needed here.
#
# qwen3-coder:30b-a3b-q8_0: same model family/architecture as the existing
# qwen3-coder:30b (Q4_K_M, confirmed via `ollama show`), just the Q8_0
# quantization - added specifically to get a real, same-harness Q4-vs-Q8
# accuracy/speed comparison instead of trusting generic quantization
# research. 32GB vs 18GB on disk - expect a real VRAM/speed cost per the
# same memory-bandwidth-bound reasoning noted in mlx_benchmark's README.
THINKING_MODELS = ["qwen3.6:27b", "qwen3.6:35b-a3b-q4_K_M", "qwen3.8:27b", "qwen3.8:27b-mlx"]
NON_THINKING_MODELS = ["qwen3-coder:30b", "qwen3-coder:30b-a3b-q8_0"]
MODELS = THINKING_MODELS + NON_THINKING_MODELS
# Extra token budget given to the think:true arm on top of a prompt's normal
# max_tokens, so reasoning has real room to finish before content starts -
# the original run showed qwen3.6 can burn 1000+ tokens thinking about even
# a trivial prompt. Generous on purpose; num_ctx is 32768 so there's room.
REASONING_HEADROOM_TOKENS = 4096
RESULTS_DIR = Path(__file__).parent / "results"
FUNCTIONAL_TEST_TIMEOUT_S = 10


def extract_code(text: str) -> str:
    """Strip a ```python ... ``` or bare ``` ... ``` fence if present, else
    return the text as-is (some models return raw code with no fence)."""
    match = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    return match.group(1).strip() if match else text.strip()


def check_syntax(code: str) -> tuple[bool, str | None]:
    try:
        ast.parse(code)
        return True, None
    except SyntaxError as e:
        return False, str(e)


def run_functional_test(code: str, test_snippet: str) -> tuple[bool, str]:
    full_source = code + "\n\n" + test_snippet
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(full_source)
        temp_path = f.name
    try:
        proc = subprocess.run(
            [sys.executable, temp_path],
            capture_output=True,
            text=True,
            timeout=FUNCTIONAL_TEST_TIMEOUT_S,
        )
        passed = proc.returncode == 0 and "FUNCTIONAL_TEST_PASS" in proc.stdout
        detail = proc.stdout if passed else (proc.stderr or proc.stdout or "no output")
        return passed, detail.strip()[-500:]
    except subprocess.TimeoutExpired:
        return False, f"timed out after {FUNCTIONAL_TEST_TIMEOUT_S}s"
    finally:
        Path(temp_path).unlink(missing_ok=True)


def call_ollama(model: str, prompt: str, max_tokens: int, think: bool | None) -> dict:
    """think=True/False sends an explicit think param (only meaningful for a
    thinking-capable model); think=None omits it entirely (use for
    non-thinking models - sending think:false to a model that doesn't
    support thinking is presumably a no-op, but omitting it is the honest
    "this axis doesn't apply here" representation)."""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "options": {"num_predict": max_tokens},
    }
    if think is not None:
        body["think"] = think
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"}
    )

    start = time.perf_counter()
    ttft_thinking = None
    ttft_content = None
    thinking_text = ""
    thinking_chunks = 0
    content_text = ""
    content_chunks = 0
    final = None
    with urllib.request.urlopen(req, timeout=300) as resp:
        for line in resp:
            if not line.strip():
                continue
            chunk = json.loads(line)
            message = chunk.get("message", {})
            if message.get("thinking"):
                if ttft_thinking is None:
                    ttft_thinking = time.perf_counter() - start
                thinking_text += message["thinking"]
                thinking_chunks += 1
            if message.get("content"):
                if ttft_content is None:
                    ttft_content = time.perf_counter() - start
                content_text += message["content"]
                content_chunks += 1
            if chunk.get("done"):
                final = chunk
    total_time = time.perf_counter() - start

    eval_count = final.get("eval_count") if final else None
    eval_duration_s = (final.get("eval_duration", 0) / 1e9) if final else None
    prompt_eval_count = final.get("prompt_eval_count") if final else None
    prompt_eval_duration_s = (final.get("prompt_eval_duration", 0) / 1e9) if final else None

    return {
        "ttft_thinking_s": round(ttft_thinking, 3) if ttft_thinking is not None else None,
        "ttft_content_s": round(ttft_content, 3) if ttft_content is not None else None,
        "total_time_s": round(total_time, 3),
        "prompt_tokens": prompt_eval_count,
        "prompt_tps": round(prompt_eval_count / prompt_eval_duration_s, 1)
        if prompt_eval_count and prompt_eval_duration_s
        else None,
        # eval_count is thinking+content combined (Ollama doesn't split it) -
        # thinking_chunks/content_chunks are an approximation of the split,
        # accurate whenever the backend streams ~1 token per chunk (true for
        # Ollama's native /api/chat as observed here), not an exact token count.
        "generation_tokens_total": eval_count,
        "generation_tps": round(eval_count / eval_duration_s, 1)
        if eval_count and eval_duration_s
        else None,
        "thinking_tokens_approx": thinking_chunks or None,
        "content_tokens_approx": content_chunks or None,
        "thinking_text": thinking_text,
        "output_text": content_text,
    }


def run_one(model: str, prompt_spec: dict, max_tokens: int, think: bool | None, mode_label: str) -> dict:
    r = call_ollama(model, prompt_spec["prompt"], max_tokens, think)
    code = extract_code(r["output_text"])
    syntax_ok, syntax_err = check_syntax(code)

    functional_result = None
    if prompt_spec["functional_test"] and syntax_ok:
        passed, detail = run_functional_test(code, prompt_spec["functional_test"])
        functional_result = {"passed": passed, "detail": detail}
    elif prompt_spec["functional_test"] and not syntax_ok:
        functional_result = {"passed": False, "detail": "skipped: syntax check failed first"}

    row = {
        "model": model,
        "mode": mode_label,
        "prompt_id": prompt_spec["id"],
        "max_tokens_budget": max_tokens,
        "ttft_thinking_s": r["ttft_thinking_s"],
        "ttft_content_s": r["ttft_content_s"],
        "total_time_s": r["total_time_s"],
        "prompt_tokens": r["prompt_tokens"],
        "prompt_tps": r["prompt_tps"],
        "generation_tokens_total": r["generation_tokens_total"],
        "generation_tps": r["generation_tps"],
        "thinking_tokens_approx": r["thinking_tokens_approx"],
        "content_tokens_approx": r["content_tokens_approx"],
        "syntax_ok": syntax_ok,
        "syntax_error": syntax_err,
        "functional_test": functional_result,
        "thinking_text": r["thinking_text"],
        "output_text": r["output_text"],
    }
    func = "n/a" if functional_result is None else ("PASS" if functional_result["passed"] else "FAIL")
    print(
        f"  [{mode_label:9s}] {prompt_spec['id']:16s} "
        f"ttft_content={row['ttft_content_s']}s think_tok~{row['thinking_tokens_approx']} "
        f"content_tok~{row['content_tokens_approx']} syntax_ok={syntax_ok} functional={func}"
    )
    return row


def main():
    # Optional: `python3 bench.py <model> [<model> ...]` runs only the named
    # model(s) instead of all of MODELS - e.g. `python3 bench.py
    # qwen3-coder:30b-a3b-q8_0` to test just the new Q8 pull without paying
    # for a full run across every model. A name not in THINKING_MODELS is
    # just treated as non-thinking (no think param sent), same as any other
    # NON_THINKING_MODELS entry - no need to register it anywhere first.
    models_to_run = sys.argv[1:] if len(sys.argv) > 1 else MODELS

    RESULTS_DIR.mkdir(exist_ok=True)
    results = []

    for model in models_to_run:
        print(f"=== {model} ===")
        print("  warming up (load into VRAM, untimed)...")
        try:
            call_ollama(model, "Say hi in one word.", 8, think=None)
        except Exception as e:
            print(f"  [SKIP] warm-up failed for {model}: {e}")
            continue

        is_thinking_model = model in THINKING_MODELS
        for prompt_spec in PROMPTS:
            if is_thinking_model:
                results.append(
                    run_one(model, prompt_spec, prompt_spec["max_tokens"], think=False, mode_label="no_think")
                )
                results.append(
                    run_one(
                        model,
                        prompt_spec,
                        prompt_spec["max_tokens"] + REASONING_HEADROOM_TOKENS,
                        think=True,
                        mode_label="think",
                    )
                )
            else:
                results.append(
                    run_one(model, prompt_spec, prompt_spec["max_tokens"], think=None, mode_label="n/a")
                )
        print()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = RESULTS_DIR / f"bench_{ts}.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"Wrote {out_path}\n")

    print("--- Summary ---")
    # gen_tps/total_time_s are the metrics that actually reflect a quant/model
    # speed comparison (both straight from Ollama's own eval_count/eval_duration) -
    # ttft is prefill+first-token latency, noisy and not bandwidth-bound the same
    # way, and content_tokens_approx is this script's OWN chunk-count approximation,
    # confirmed to undercount Ollama's real eval_count by ~15% in both quant levels
    # tested so far (not a Q8-specific artifact) - print gen_tps/total_time_s too so
    # a quant/model speed comparison doesn't have to be reconstructed from the JSON.
    header = (
        f"{'model':26s} {'mode':9s} {'prompt':16s} {'ttft_c':>7s} {'total_s':>8s} "
        f"{'gen_tps':>8s} {'think_tok':>9s} {'cont_tok~':>9s} {'syntax':>7s} {'func':>5s}"
    )
    print(header)
    for row in results:
        func = "n/a" if row["functional_test"] is None else ("PASS" if row["functional_test"]["passed"] else "FAIL")
        print(
            f"{row['model']:26s} {row['mode']:9s} {row['prompt_id']:16s} "
            f"{str(row['ttft_content_s']):>7s} {str(row['total_time_s']):>8s} "
            f"{str(row['generation_tps']):>8s} {str(row['thinking_tokens_approx']):>9s} "
            f"{str(row['content_tokens_approx']):>9s} {str(row['syntax_ok']):>7s} {func:>5s}"
        )


if __name__ == "__main__":
    main()
