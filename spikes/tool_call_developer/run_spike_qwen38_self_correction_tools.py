"""Standalone (no Kriya import) test of qwen3.8:27b's native tool-calling
reliability against a REALISTIC scenario shaped like Kriya's own
self-correction micro-loop (kriya/workflow/self_correction.py): a small,
in-memory sandbox with one file carrying a real Java compile error, and the
4 small-argument tools from that module's ORIGINAL tool set
(read_file/list_files/apply_patch/recompile) - copied inline below, not
imported, per this being a standalone test kept outside Kriya's own loop.

Motivation (2026-08-22): earlier the same day, `RunVerifierAgent.judge()`
hallucinated a nonexistent Java entrypoint class for a pure-library
milestone (fixed, commit af835f9) - a case where a deterministic tool call
(checking the real file list) would have caught the mistake immediately.
self_correction.py's tool-calling loop is exactly that kind of grounding
mechanism, currently only exercised against qwen3-coder:30b (primary) and
whatever `llm_chain` fallback is configured. Separately, external review
(Simon Willison, simonwillison.net/2026/Aug/16/qwen-38-27b/) reported solid
real-world tool-calling reliability for qwen3.8:27b once reasoning is tuned
down - this checks that claim directly, with Kriya-shaped tools, before
considering the model as a self_correction.py fallback candidate.

Settings under test, per user request:
  - MTP (Ollama's speculative-decoding draft head): confirmed ALREADY ON by
    default for this pulled model - `ollama show qwen3.8:27b --modelfile`
    shows `PARAMETER draft_num_predict 4`. Not overridden here; there's
    nothing to turn on.
  - reasoning_effort: tested at both "none" and "low" (two arms) - user
    recalled "low or none" but wasn't sure which one mattered.
  - num_ctx: explicit override via the same `options` shape Kriya's own
    default_config.yaml uses for Ollama's OpenAI-compatible endpoint
    (extra_body -> options -> num_ctx). Default 32768 here - generous
    headroom for a short multi-turn tool-calling loop without paying for
    this model's full 262144 native window on every request.
  - Sampling: the HF card's instruct-mode profile (temperature=0.7,
    top_p=0.80, presence_penalty=1.5), NOT Ollama's pulled thinking-mode
    default (temp=1/top_p=0.95/top_k=20) - see spikes/model_speed_poc/
    reasoning_effort_ab_v2.py for why this distinction matters; running the
    wrong sampling profile confounded an earlier A/B on this same model.

The fake `recompile` tool does a cheap heuristic check (is the seeded bug's
missing semicolon now present?), not a real javac call - deliberately: the
point of this spike is tool-calling MECHANICS (does the model call the
right tools, in the right order, with valid small-argument JSON), not
whether qwen3.8:27b can write correct Java, which is a separate question.

Reports, per arm: turn-by-turn tool calls made (name + parsed args),
whether tool_calls[].function.arguments actually parses as JSON, whether
apply_patch's edit was genuinely small/targeted (not a whole-file dump -
the exact failure mode self_correction.py's own docstring warns is
unreliable for large tool-call arguments on local models), whether the
loop converges (model calls recompile, it reports fixed, model stops
calling tools) within max_turns, and total wall time.

Run: .venv/bin/python spikes/tool_call_developer/run_spike_qwen38_self_correction_tools.py
"""
import json
import time

import httpx

OLLAMA_URL = "http://localhost:11434/v1/chat/completions"
MODEL = "qwen3.8:27b"
MAX_TURNS = 4  # matches self_correction.py's own run_self_correction_loop default
NUM_CTX = 32768
TIMEOUT_S = 300

INSTRUCT_SAMPLING = {"temperature": 0.7, "top_p": 0.80, "presence_penalty": 1.5}

# --- Copied inline from kriya/workflow/self_correction.py's original 4-tool
# set (read-only, small-argument by construction) - not imported. ---
READ_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "Read the current full content of a file already written in this "
            "attempt's sandbox, to see its real, current state before proposing "
            "a fix."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": "Path relative to the project root, e.g. 'src/main/java/App.java'.",
                }
            },
            "required": ["filepath"],
        },
    },
}

LIST_FILES_TOOL = {
    "type": "function",
    "function": {
        "name": "list_files",
        "description": (
            "List files currently written in this attempt's sandbox (optionally "
            "filtered by a substring), to check real directory layout before "
            "diagnosing a compile error."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "description": 'Optional substring to filter paths by. Omit or pass "" to list everything.',
                }
            },
            "required": [],
        },
    },
}

APPLY_PATCH_TOOL = {
    "type": "function",
    "function": {
        "name": "apply_patch",
        "description": (
            "Apply one or more small search/replace edits to a single file "
            "already in the sandbox. Each edit's 'search' text must match "
            "EXACTLY ONCE in the file's current content - include enough "
            "surrounding context to make it unique. Do not pass full file "
            "content; pass only the minimal changed region(s)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filepath": {"type": "string"},
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "search": {"type": "string", "description": "Exact existing text to find (small, a few lines)."},
                            "replace": {"type": "string", "description": "Text to replace it with."},
                        },
                        "required": ["search", "replace"],
                    },
                },
            },
            "required": ["filepath", "edits"],
        },
    },
}

RECOMPILE_TOOL = {
    "type": "function",
    "function": {
        "name": "recompile",
        "description": (
            "Re-run the project's compile check against the current sandbox "
            "state. Call this after applying a patch to see if the fix worked. "
            "Takes no arguments."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

TOOLS = [READ_FILE_TOOL, LIST_FILES_TOOL, APPLY_PATCH_TOOL, RECOMPILE_TOOL]

SYSTEM_PROMPT = (
    "You are helping fix a compile error in a sandboxed project workspace. "
    "You have a SMALL set of tools - use them to diagnose and fix the failure, "
    "then verify your fix actually worked.\n"
    "Rules:\n"
    "- apply_patch edits must be SMALL, targeted search/replace pairs - never "
    "paste a whole file's content into 'search' or 'replace'. Include just "
    "enough surrounding text in 'search' to match exactly once.\n"
    "- You can only read/patch files already listed as being in the sandbox - "
    "you cannot create new files here.\n"
    "- After applying a patch, call recompile to check whether it actually "
    "fixed the failure. Do not assume a patch worked without recompile.\n"
    "- If recompile succeeds, stop calling tools and reply with a short plain-"
    "text confirmation."
)

USER_PROMPT = (
    "The project failed to compile with this error:\n\n"
    "Calculator.java:3: error: ';' expected\n"
    "        return a + b\n"
    "                    ^\n"
    "1 error\n\n"
    "Files in the sandbox: Calculator.java\n\n"
    "Diagnose and fix this."
)

# --- Fake in-memory sandbox: one file, one real, seeded compile error. ---
_BROKEN_LINE = "        return a + b\n"
_FIXED_LINE = "        return a + b;\n"
SANDBOX = {
    "Calculator.java": (
        "public class Calculator {\n"
        "    public int add(int a, int b) {\n"
        f"{_BROKEN_LINE}"
        "    }\n"
        "}\n"
    )
}


def fake_read_file(args: dict) -> str:
    filepath = args.get("filepath", "")
    if filepath not in SANDBOX:
        return f"ERROR: no such file in sandbox: {filepath!r}. Files present: {list(SANDBOX.keys())}"
    return SANDBOX[filepath]


def fake_list_files(args: dict) -> str:
    filt = args.get("filter") or ""
    return "\n".join(f for f in SANDBOX if filt in f) or "(no matching files)"


def fake_apply_patch(args: dict) -> str:
    filepath = args.get("filepath", "")
    edits = args.get("edits") or []
    if filepath not in SANDBOX:
        return f"ERROR: no such file in sandbox: {filepath!r}"
    content = SANDBOX[filepath]
    whole_file_dump = any(len(e.get("search", "")) > len(content) * 0.8 for e in edits)
    applied = []
    for i, edit in enumerate(edits):
        search, replace = edit.get("search", ""), edit.get("replace", "")
        count = content.count(search)
        if count != 1:
            return f"ERROR: edit #{i + 1}'s search text matched {count} times (must match exactly once). search={search!r}"
        content = content.replace(search, replace, 1)
        applied.append((search, replace))
    SANDBOX[filepath] = content
    note = " [WARNING: this edit's search text was suspiciously close to the whole file - looks like a full-file dump, not a small targeted edit]" if whole_file_dump else ""
    return f"Applied {len(applied)} edit(s) to {filepath}.{note}"


def fake_recompile(args: dict) -> str:
    content = SANDBOX["Calculator.java"]
    if _FIXED_LINE in content:
        return "SUCCESS: compiled with no errors."
    if _BROKEN_LINE in content:
        return "FAILURE: Calculator.java:3: error: ';' expected"
    # Model changed the line to something else entirely - a plausible fix
    # attempt this heuristic can't verify either way; report as still broken
    # so the model doesn't falsely believe it succeeded on an unverified change.
    return "FAILURE: Calculator.java:3: error: ';' expected (heuristic check: line no longer matches the original seeded bug or the expected fix)"


DISPATCH = {
    "read_file": fake_read_file,
    "list_files": fake_list_files,
    "apply_patch": fake_apply_patch,
    "recompile": fake_recompile,
}


def _post(messages: list, reasoning_effort: str) -> dict:
    payload = {
        "model": MODEL,
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",
        "reasoning_effort": reasoning_effort,
        "options": {"num_ctx": NUM_CTX},
        **INSTRUCT_SAMPLING,
    }
    resp = httpx.post(OLLAMA_URL, json=payload, timeout=TIMEOUT_S)
    resp.raise_for_status()
    return resp.json()


def warm_up() -> None:
    """Untimed, minimal-payload call to load the model into VRAM before any
    timed arm runs - matches spikes/model_speed_poc/bench.py's own
    convention. Without this, the first arm's first turn confounds real
    reasoning-effort latency with one-time model-load time (confirmed live:
    the "none" arm's turn 1 took 134.6s with thinking_chars=0 on the first
    run of this script, vs. 5-15s for every other turn once the model was
    already warm from that arm)."""
    print("warming up (untimed, loading model into VRAM)...")
    start = time.monotonic()
    _post(
        [{"role": "system", "content": "Reply with one word."}, {"role": "user", "content": "Say hi."}],
        reasoning_effort="none",
    )
    print(f"warm-up done in {time.monotonic() - start:.1f}s\n")


def run_arm(reasoning_effort: str) -> None:
    global SANDBOX
    SANDBOX = {
        "Calculator.java": (
            "public class Calculator {\n"
            "    public int add(int a, int b) {\n"
            f"{_BROKEN_LINE}"
            "    }\n"
            "}\n"
        )
    }
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_PROMPT},
    ]

    print(f"\n{'=' * 70}\nARM: reasoning_effort={reasoning_effort}\n{'=' * 70}")
    start = time.monotonic()
    recompile_succeeded = False
    malformed_tool_call = False
    whole_file_dump_seen = False

    for turn in range(1, MAX_TURNS + 1):
        turn_start = time.monotonic()
        try:
            data = _post(messages, reasoning_effort)
        except Exception as e:
            print(f"  turn {turn}: REQUEST ERROR: {e}")
            break
        turn_elapsed = time.monotonic() - turn_start
        message = data["choices"][0]["message"]
        thinking = message.get("reasoning") or message.get("reasoning_content") or ""
        tool_calls = message.get("tool_calls") or []

        print(f"  turn {turn} ({turn_elapsed:.1f}s, thinking_chars={len(thinking)}):")

        if not tool_calls:
            content = (message.get("content") or "").strip()
            print(f"    no tool call - final reply: {content[:200]!r}")
            messages.append({"role": "assistant", "content": content})
            break

        messages.append({"role": "assistant", "content": message.get("content"), "tool_calls": tool_calls})
        for tc in tool_calls:
            name = tc["function"]["name"]
            raw_args = tc["function"]["arguments"]
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except Exception as e:
                print(f"    -> {name}: MALFORMED ARGUMENTS (not valid JSON): {e} - raw: {raw_args!r}")
                malformed_tool_call = True
                messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": f"ERROR: arguments were not valid JSON: {e}"})
                continue

            fn = DISPATCH.get(name)
            if fn is None:
                print(f"    -> UNKNOWN TOOL CALLED: {name!r} args={args}")
                messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": f"ERROR: no such tool: {name}"})
                continue

            result = fn(args)
            if name == "apply_patch" and "WARNING" in result:
                whole_file_dump_seen = True
            if name == "recompile" and result.startswith("SUCCESS"):
                recompile_succeeded = True
            print(f"    -> {name}({args}) => {result[:150]!r}")
            messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": result})

    total_elapsed = time.monotonic() - start
    print(f"\n  RESULT: recompile_succeeded={recompile_succeeded} "
          f"malformed_tool_call={malformed_tool_call} whole_file_dump_seen={whole_file_dump_seen} "
          f"total_elapsed={total_elapsed:.1f}s turns_used<={MAX_TURNS}")


def main():
    print(f"Model: {MODEL} | num_ctx={NUM_CTX} | sampling={INSTRUCT_SAMPLING} | MTP: on by default (draft_num_predict=4, not overridden)")
    warm_up()
    run_arm("none")
    run_arm("low")


if __name__ == "__main__":
    main()
