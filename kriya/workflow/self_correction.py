"""Bounded, tool-using recovery loop for a compile-gate failure - the one
place in the Developer + Quality Gates cycle (kriya/workflow/attempt.py)
where the model gets a chance to diagnose and fix its own mistake using live
tool feedback (a real compile error, real file content) before the attempt
gives up and falls back to a full regeneration on the next attempt.

Deliberately narrow, built on a specific, live-tested boundary (see
spikes/tool_call_developer/README.md): native tool-calling on local models
is reliable for SMALL tool-call arguments, unreliable for large ones (a
whole file's content). Every tool here is small-argument by construction -
no tool ever carries full file content as an argument. Full-file
materialization stays exactly where it already lived: apply_anchored_edits()
(kriya/workflow/edit_safety.py), reused here unmodified, the same mechanical
step attempt.py's own normal edit path already goes through.

Opt-in via autonomy.self_correction_loop_enabled (default False) - this
module is only ever imported when the flag is on (see the call site in
attempt.py), so it costs nothing when disabled."""
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from kriya.core.llm import LLMClient
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.edit_safety import apply_anchored_edits, atomic_write_file

logger = logging.getLogger(__name__)

SELF_CORRECTION_SYSTEM_PROMPT = (
    "You are helping fix a compile failure in a sandboxed project workspace. "
    "You have a SMALL set of tools - use them to diagnose and fix the failure, "
    "then verify your fix actually worked.\n"
    "Rules:\n"
    "- apply_patch edits must be SMALL, targeted search/replace pairs - never "
    "paste a whole file's content into 'search' or 'replace'. Include just "
    "enough surrounding text in 'search' to match exactly once.\n"
    "- You can only read/patch files already listed as being in the sandbox - "
    "you cannot create new files here.\n"
    "- After applying a patch, call recompile to check whether it actually "
    "fixed the failure. Do not assume a patch worked without recompiling.\n"
    "- If recompile succeeds, stop calling tools and reply with a short plain-"
    "text confirmation."
)

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
                            "search": {
                                "type": "string",
                                "description": "Exact existing text to find (small, a few lines).",
                            },
                            "replace": {
                                "type": "string",
                                "description": "Text to replace it with.",
                            },
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

_TOOLS = [READ_FILE_TOOL, LIST_FILES_TOOL, APPLY_PATCH_TOOL, RECOMPILE_TOOL]


@dataclass
class SelfCorrectionResult:
    """What run_self_correction_loop() hands back to attempt.py. attempt.py
    remains the sole owner of GenerationState mutation - this module never
    touches state.* directly, matching the rest of the retry-loop's module
    split (edit_safety.py, retry_strategy.py, etc. are all pure functions
    over their own inputs)."""

    resolved: bool
    turns_used: int
    final_compile_output: str
    # filepath -> new content, for every file actually modified this loop -
    # already written to disk by the time this is returned (recompile() has
    # to see them), kept here too so a caller can record exactly what changed.
    modified_files: Dict[str, str] = field(default_factory=dict)
    # every tool call + its result this loop made, for gate_outcomes/traces.db
    transcript: List[Dict[str, Any]] = field(default_factory=list)


def _to_openai_tool_call_dicts(tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Reconstructs the OpenAI wire-format tool_calls list (id/type/function
    with a JSON-string arguments field) for the assistant message we append to
    the running conversation - LLMClient.complete_with_tools() already decoded
    arguments into a dict for the caller's convenience, but the message history
    sent back on the NEXT turn needs the original wire shape."""
    import json

    return [
        {
            "id": tc["id"],
            "type": "function",
            "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])},
        }
        for tc in tool_calls
    ]


def _dispatch_tool_call(
    call: Dict[str, Any],
    worktree_path: str,
    validator: PolymorphicValidator,
    files_in_scope: List[str],
    active_code_context: str,
    modified_files: Dict[str, str],
) -> str:
    """Executes one tool call and returns the string fed back to the model as
    the tool result. Every branch is small-argument-in, small-string-out -
    read_file/apply_patch never return/accept more than one file's worth of
    content, and every filepath is checked against files_in_scope/
    modified_files (a strict allowlist, not a sanitized arbitrary path) before
    touching disk."""
    name = call["name"]
    args = call["arguments"] if isinstance(call["arguments"], dict) else {}
    known_files = set(files_in_scope) | set(modified_files.keys())

    if name == "read_file":
        filepath = args.get("filepath")
        if not filepath or filepath not in known_files:
            return f"ERROR: '{filepath}' is not a file in this attempt's sandbox. Known files: {sorted(known_files)}"
        if filepath in modified_files:
            return modified_files[filepath]
        full_path = os.path.join(worktree_path, filepath)
        if not os.path.exists(full_path):
            return f"ERROR: '{filepath}' is listed as written but not found on disk."
        with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()

    if name == "list_files":
        substring = args.get("filter") or ""
        matches = sorted(f for f in known_files if substring in f)
        return "\n".join(matches) if matches else "(no files match)"

    if name == "apply_patch":
        filepath = args.get("filepath")
        edits = args.get("edits")
        if not filepath or filepath not in known_files:
            return f"ERROR: '{filepath}' is not a file in this attempt's sandbox - cannot patch a file that doesn't exist here. Known files: {sorted(known_files)}"
        if not isinstance(edits, list) or not edits:
            return "ERROR: apply_patch requires a non-empty 'edits' list, each with 'search' and 'replace'."

        if filepath in modified_files:
            orig_text = modified_files[filepath]
        else:
            full_path = os.path.join(worktree_path, filepath)
            if not os.path.exists(full_path):
                return f"ERROR: '{filepath}' is listed as written but not found on disk."
            with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                orig_text = fh.read()

        try:
            new_content = apply_anchored_edits(orig_text, edits, active_code_context)
        except ValueError as anchor_ex:
            # Fed back to the model as this tool call's result, NOT raised - the
            # whole point of this loop is letting the model retry within its
            # turn budget before attempt.py's own terminal QualityGateFailure.
            return f"ERROR: patch did not apply to '{filepath}': {anchor_ex}"

        full_path = os.path.join(worktree_path, filepath)
        atomic_write_file(full_path, new_content)
        modified_files[filepath] = new_content
        return f"Patch applied to '{filepath}'. Call recompile to verify it fixed the failure."

    if name == "recompile":
        result = validator.run_compile_check(list(files_in_scope))
        output = result.get("output", "")
        return f"SUCCESS: {output}" if result.get("success") else f"FAILURE: {output}"

    return f"ERROR: unknown tool '{name}'."


async def run_self_correction_loop(
    llm: LLMClient,
    worktree_path: str,
    validator: PolymorphicValidator,
    files_in_scope: List[str],
    compile_error_output: str,
    active_code_context: str,
    max_turns: int = 4,
    model_override: Optional[str] = None,
    base_url_override: Optional[str] = None,
    api_key_override: Optional[str] = None,
) -> SelfCorrectionResult:
    """Runs up to max_turns of native tool-calling against the given llm,
    trying to fix a real compile failure using the 4 small-argument tools
    above. Returns resolved=True the moment a recompile() call reports
    SUCCESS; returns resolved=False if the budget runs out or the model stops
    calling tools without ever getting a passing recompile - either way, the
    caller (kriya/workflow/attempt.py) treats this exactly the same as if the
    loop had never run, falling through to the existing QualityGateFailure
    path."""
    modified_files: Dict[str, str] = {}
    transcript: List[Dict[str, Any]] = []
    last_compile_output = compile_error_output

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SELF_CORRECTION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"The following files failed to compile:\n{compile_error_output}\n\n"
                f"Files currently in the sandbox: {', '.join(sorted(files_in_scope))}\n\n"
                "Use the available tools to diagnose and fix the failure. Call "
                "recompile after applying a fix to check whether it worked. If it "
                "now succeeds, stop calling tools - just reply with a short "
                "confirmation in plain text."
            ),
        },
    ]

    turns_used = 0
    for turn in range(max_turns):
        turns_used = turn + 1
        result = await llm.complete_with_tools(
            messages,
            _TOOLS,
            model_override=model_override,
            base_url_override=base_url_override,
            api_key_override=api_key_override,
        )
        tool_calls = result["tool_calls"]
        if not tool_calls:
            # Model gave up (or thinks it's done) without a passing recompile -
            # budget effectively exhausted early, not a success.
            logger.info("Self-correction loop: model stopped calling tools without a passing recompile.")
            break

        messages.append(
            {
                "role": "assistant",
                "content": result["content"],
                "tool_calls": _to_openai_tool_call_dicts(tool_calls),
            }
        )

        for call in tool_calls:
            tool_result_text = _dispatch_tool_call(
                call, worktree_path, validator, files_in_scope, active_code_context, modified_files,
            )
            transcript.append(
                {"turn": turn, "tool": call["name"], "arguments": call["arguments"], "result": tool_result_text}
            )
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": tool_result_text})

            if call["name"] == "recompile":
                last_compile_output = tool_result_text
                if tool_result_text.startswith("SUCCESS"):
                    return SelfCorrectionResult(
                        resolved=True,
                        turns_used=turns_used,
                        final_compile_output=tool_result_text,
                        modified_files=modified_files,
                        transcript=transcript,
                    )

    return SelfCorrectionResult(
        resolved=False,
        turns_used=turns_used,
        final_compile_output=last_compile_output,
        modified_files=modified_files,
        transcript=transcript,
    )
