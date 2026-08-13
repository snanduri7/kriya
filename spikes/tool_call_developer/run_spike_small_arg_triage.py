"""Re-test (2026-08-13): does small-argument native tool-calling actually work,
reliably, on the models Kriya is choosing between RIGHT NOW - for the specific
new use case that motivated this re-test?

Context: this session diagnosed a live failure in kriya/workflow/attribution.py's
`triage` tier - a json_mode call asking the model to pick which candidate file is
responsible for a compile/test failure. Against gpt-oss:20b, this call returned
EMPTY content (confirmed live: "Attribution triage call failed or returned
unparseable output: Expecting value: line 1 column 1 (char 0)") because gpt-oss
routes its actual output through a separate "reasoning" field under Ollama's
JSON mode, which Kriya's LLMClient never reads. The proposed fix: route this
class of SMALL, bounded decision (pick N files from a short candidate list, not
write file content) through native tool-calling instead of json_mode - the same
boundary the original run_spike.py / run_spike_large_args.py runs in this folder
already found reliable for small arguments and broken for large ones.

This script tests that proposed fix directly, head-to-head against json_mode, on
the models actually in play today: qwen3-coder:30b (positive control - Kriya's
current Developer default, and the model kriya/workflow/self_correction.py's
already-shipped complete_with_tools() mechanism targets), gpt-oss:20b (the model
whose json_mode is confirmed broken - does tool-calling rescue it?), and
glm-4.7-flash (untested candidate raised as a possible fallback-model
replacement). Runs N trials per model per mechanism (not one-shot) since
tool-calling reliability on local models has already been shown in this same
folder to vary run-to-run, not just model-to-model.

The task is a real-shaped triage decision, not a toy: a compile error naming two
call sites (App.java line 43, line 99) is actually caused by an untyped `var`
declaration one file over (CacheConfig.java) - deliberately the same "the
compiler's line isn't the fix site" shape as the real ignite_qpid_protocol bug
this session's diagnosis-vs-diff check (kriya/workflow/edit_safety.py) was built
to catch, so a correct answer requires real reasoning about the error, not
keyword matching against the error text.

Run: .venv/bin/python spikes/tool_call_developer/run_spike_small_arg_triage.py
"""
import json
import time

import httpx

OLLAMA_URL = "http://localhost:11434/v1/chat/completions"
MODELS = ["qwen3-coder:30b", "gpt-oss:20b", "glm-4.7-flash:latest"]
TRIALS_PER_MODEL = 5

CANDIDATE_FILES = [
    "src/main/java/com/example/App.java",
    "src/main/java/com/example/CacheConfig.java",
    "src/main/java/com/example/Protocol.java",
    "pom.xml",
]

# Same error shape as the real ignite_qpid_protocol failure this session traced:
# the compiler names two USE sites in App.java, but the real fix is the raw-typed
# declaration in CacheConfig.java one file over - a triage tool that just greps
# for filenames in the error text would wrongly pick App.java.
ERROR_TEXT = """[ERROR] /src/main/java/com/example/App.java:[43,29] incompatible types: java.lang.Object cannot be converted to com.example.Protocol
[ERROR] /src/main/java/com/example/App.java:[99,34] incompatible types: java.lang.Object cannot be converted to com.example.Protocol
[ERROR] -> [Help 1]"""

CACHE_CONFIG_SNIPPET = """// CacheConfig.java, line 41
var cache = ignite.getOrCreateCache("protocolCache");  // untyped - infers IgniteCache<Object, Object>"""

SYSTEM_PROMPT = (
    "You are diagnosing a Java compile failure in a Maven project. You are given the "
    "compiler's error output and a list of candidate files that were part of this build. "
    "Identify which file(s) actually need to be FIXED to resolve the error - this may not "
    "be the file(s) the compiler's line numbers point at, if the real defect is a "
    "declaration elsewhere that the reported lines merely use."
)

USER_PROMPT = f"""=== Compile error ===
{ERROR_TEXT}

=== Relevant snippet from CacheConfig.java ===
{CACHE_CONFIG_SNIPPET}

=== Candidate files in this project ===
{chr(10).join(CANDIDATE_FILES)}

Which file(s) need to be fixed to resolve this error?"""

TRIAGE_TOOL = {
    "type": "function",
    "function": {
        "name": "attribute_failure",
        "description": (
            "Report which candidate file(s) are actually responsible for the compile "
            "failure and need to be fixed - not necessarily the file(s) named in the "
            "compiler's error output."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filepaths from the candidate list that need to be fixed.",
                },
                "reasoning": {
                    "type": "string",
                    "description": "One or two sentences on why these file(s), not the ones the compiler line-numbers point at.",
                },
            },
            "required": ["files", "reasoning"],
        },
    },
}

CORRECT_FILE = "src/main/java/com/example/CacheConfig.java"
WRONG_ANCHOR_FILE = "src/main/java/com/example/App.java"


def _post(payload: dict, timeout: float = 180) -> tuple[dict, float]:
    start = time.monotonic()
    resp = httpx.post(OLLAMA_URL, json=payload, timeout=timeout)
    elapsed = time.monotonic() - start
    resp.raise_for_status()
    return resp.json(), elapsed


def _grade(files: list) -> str:
    if not files:
        return "EMPTY (no file named)"
    if CORRECT_FILE in files and WRONG_ANCHOR_FILE not in files:
        return "CORRECT (CacheConfig.java only)"
    if CORRECT_FILE in files and WRONG_ANCHOR_FILE in files:
        return "PARTIAL (right file present, but also named the wrong-anchor file)"
    if WRONG_ANCHOR_FILE in files:
        return "WRONG (picked the compiler's reported line, not the real fix site)"
    return f"WRONG (named unrelated file(s): {files})"


def run_tool_call(model: str) -> dict:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT},
        ],
        "tools": [TRIAGE_TOOL],
        "tool_choice": "auto",
    }
    try:
        data, elapsed = _post(payload)
    except Exception as e:
        return {"mechanism": "tool_call", "outcome": f"REQUEST ERROR: {e}", "elapsed": None, "grade": "N/A"}

    message = data["choices"][0]["message"]
    tool_calls = message.get("tool_calls") or []
    if not tool_calls:
        raw = (message.get("content") or "")[:200]
        reasoning_field = message.get("reasoning") or message.get("reasoning_content")
        note = f"empty tool_calls; content={raw!r}"
        if reasoning_field:
            note += f"; NOTE reasoning field present ({len(reasoning_field)} chars) - would need reading too"
        return {"mechanism": "tool_call", "outcome": "NO TOOL CALL", "elapsed": elapsed, "grade": note}

    try:
        args = json.loads(tool_calls[0]["function"]["arguments"])
        files = args.get("files", [])
        return {"mechanism": "tool_call", "outcome": "tool_calls populated", "elapsed": elapsed, "grade": _grade(files), "files": files}
    except Exception as e:
        raw_args = tool_calls[0].get("function", {}).get("arguments", "")
        return {"mechanism": "tool_call", "outcome": f"tool_calls present but unparseable: {e}", "elapsed": elapsed, "grade": f"raw args: {raw_args[:200]!r}"}


def run_json_mode(model: str) -> dict:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT
                + ' Respond with ONLY a JSON object: {"files": ["..."], "reasoning": "..."}. No markdown fences, no commentary.',
            },
            {"role": "user", "content": USER_PROMPT},
        ],
        "response_format": {"type": "json_object"},
    }
    try:
        data, elapsed = _post(payload)
    except Exception as e:
        return {"mechanism": "json_mode", "outcome": f"REQUEST ERROR: {e}", "elapsed": None, "grade": "N/A"}

    message = data["choices"][0]["message"]
    content_text = message.get("content") or ""
    if not content_text.strip():
        reasoning_field = message.get("reasoning") or message.get("reasoning_content")
        note = "EMPTY content"
        if reasoning_field:
            note += f" - NOTE reasoning field has {len(reasoning_field)} chars Kriya's LLMClient never reads"
        return {"mechanism": "json_mode", "outcome": "EMPTY", "elapsed": elapsed, "grade": note}

    try:
        parsed = json.loads(content_text)
        files = parsed.get("files", [])
        return {"mechanism": "json_mode", "outcome": "parsed OK", "elapsed": elapsed, "grade": _grade(files), "files": files}
    except Exception as e:
        return {"mechanism": "json_mode", "outcome": f"unparseable: {e}", "elapsed": elapsed, "grade": f"raw: {content_text[:200]!r}"}


def summarize(results: list, model: str, mechanism: str):
    rows = [r for r in results if r["model"] == model and r["mechanism"] == mechanism]
    correct = sum(1 for r in rows if r["grade"].startswith("CORRECT"))
    times = [r["elapsed"] for r in rows if r["elapsed"] is not None]
    avg_time = sum(times) / len(times) if times else 0.0
    return f"{correct}/{len(rows)} correct, avg {avg_time:.1f}s"


def main():
    results = []
    for model in MODELS:
        print(f"\n{'=' * 78}\nMODEL: {model}\n{'=' * 78}")

        print(f"\n-- tool_call approach ({TRIALS_PER_MODEL} trials) --")
        for i in range(TRIALS_PER_MODEL):
            r = run_tool_call(model)
            r["model"] = model
            results.append(r)
            elapsed_str = f"{r['elapsed']:.1f}s" if r["elapsed"] is not None else "n/a"
            print(f"  [{i+1}] {r['outcome']:<40} {elapsed_str:>8}  grade: {r['grade']}")

        print(f"\n-- json_mode approach ({TRIALS_PER_MODEL} trials, Kriya's current mechanism) --")
        for i in range(TRIALS_PER_MODEL):
            r = run_json_mode(model)
            r["model"] = model
            results.append(r)
            elapsed_str = f"{r['elapsed']:.1f}s" if r["elapsed"] is not None else "n/a"
            print(f"  [{i+1}] {r['outcome']:<40} {elapsed_str:>8}  grade: {r['grade']}")

    print(f"\n{'=' * 78}\nSUMMARY\n{'=' * 78}")
    print(f"{'model':<22}{'tool_call':<28}{'json_mode':<28}")
    for model in MODELS:
        tc = summarize(results, model, "tool_call")
        jm = summarize(results, model, "json_mode")
        print(f"{model:<22}{tc:<28}{jm:<28}")


if __name__ == "__main__":
    main()
