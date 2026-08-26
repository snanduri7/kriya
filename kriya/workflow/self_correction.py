"""Bounded, tool-using recovery loop for a compile-gate (or, since
2026-08-22, run-verification) failure - the one place in the Developer +
Quality Gates cycle (kriya/workflow/attempt.py) where the model gets a
chance to diagnose and fix its own mistake using live tool feedback (a real
compile error, real file content, real dependency/classpath ground truth)
before the attempt gives up and falls back to a full regeneration.

Deliberately narrow, built on a specific, live-tested boundary (see
spikes/tool_call_developer/README.md): native tool-calling on local models
is reliable for SMALL tool-call arguments, unreliable for large ones (a
whole file's content). Every tool here is small-argument by construction -
no tool ever carries full file content as an argument. Full-file
materialization stays exactly where it already lived: apply_anchored_edits()
(kriya/workflow/edit_safety.py), reused here unmodified, the same mechanical
step attempt.py's own normal edit path already goes through.

Widened 2026-08-22 from 4 source-editing tools to 8: the original 4
(read_file/list_files/apply_patch/recompile) can only ever ground a fix in
files physically inside the workspace - structurally unable to help with a
mismatch against an EXTERNAL dependency's real API shape, or a build-layout
question that isn't really about any one file's content at all. Rather than
hand-writing another one-off deterministic Python function per newly
discovered ground-truth gap (unbounded - the whole reason this widening
exists), the new tools (list_dependencies, inspect_class,
resolve_missing_dependency, list_compiled_output) give the model a way to
check ANY such gap against reality itself, generalizing rather than
enumerating.

Opt-in via autonomy.self_correction_loop_enabled (default False) - this
module is only ever imported when the flag is on (see the call sites in
attempt.py), so it costs nothing when disabled."""
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from kriya.core.llm import LLMClient
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.filesystem import AuthorizedFileWriter
from kriya.tools.resolver import resolve_maven_class
from kriya.tools.validate import PolymorphicValidator, get_pom_dependencies
from kriya.workflow.edit_safety import (
    FileRevisionConflict, apply_anchored_edits,
    content_revision,
)

logger = logging.getLogger(__name__)

def _repair_system_prompt(failure_label: str, validation_tool_name: str) -> str:
    return (
    f"You are helping fix a {failure_label} in a sandboxed project workspace. "
    "You have a SMALL set of tools - use them to diagnose and fix the failure, "
    "then verify your fix actually worked.\n"
    "Rules:\n"
    "- apply_patch edits must be SMALL, targeted search/replace pairs - never "
    "paste a whole file's content into 'search' or 'replace'. Include just "
    "enough surrounding text in 'search' to match exactly once.\n"
    "- You can only read/patch files already listed as being in the sandbox - "
    "you cannot create new files here.\n"
    "- Never guess a class's package, constructor, or method signature, and "
    "never invent a dependency coordinate from memory - use inspect_class, "
    "list_dependencies, and resolve_missing_dependency to check the REAL "
    "ground truth first.\n"
    f"- After applying a patch, call {validation_tool_name} to check whether it actually "
    f"fixed the failure. Do not assume a patch worked without {validation_tool_name}.\n"
    f"- If {validation_tool_name} succeeds, stop calling tools and reply with a short plain-"
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

LIST_DEPENDENCIES_TOOL = {
    "type": "function",
    "function": {
        "name": "list_dependencies",
        "description": (
            "List this project's directly declared dependencies (from pom.xml), "
            "as 'groupId:artifactId' pairs. Use this to check whether something "
            "is already a declared dependency before assuming it needs to be "
            "added, or before guessing at an external class's real package."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

INSPECT_CLASS_TOOL = {
    "type": "function",
    "function": {
        "name": "inspect_class",
        "description": (
            "Look up the REAL, ground-truth shape of a class by its fully-"
            "qualified name (e.g. 'com.example.Protocol' or "
            "'org.apache.ignite.Ignition') - not a guess from memory. If it's "
            "a class already written in this sandbox, returns its real "
            "source. If it's an external dependency, returns its real public "
            "method/constructor signatures (via the actually-resolved Maven "
            "classpath). Use this BEFORE assuming a class's package, "
            "constructor signature, or method names."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fully_qualified_name": {
                    "type": "string",
                    "description": "e.g. 'com.example.Protocol' or 'org.apache.ignite.Ignition'.",
                }
            },
            "required": ["fully_qualified_name"],
        },
    },
}

RESOLVE_MISSING_DEPENDENCY_TOOL = {
    "type": "function",
    "function": {
        "name": "resolve_missing_dependency",
        "description": (
            "Search Maven Central for the real groupId/artifactId/version "
            "coordinate of a fully-qualified class or package that appears to "
            "be genuinely missing (not already declared - check "
            "list_dependencies first - and not found by inspect_class). Use "
            "this before inventing a coordinate from memory."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "A fully-qualified class or package name, e.g. 'org.apache.qpid.jms.JmsConnectionFactory'.",
                }
            },
            "required": ["symbol"],
        },
    },
}

LIST_COMPILED_OUTPUT_TOOL = {
    "type": "function",
    "function": {
        "name": "list_compiled_output",
        "description": (
            "List the real .class files actually produced by the last "
            "compile, under target/classes. An empty result after a "
            "'successful' compile means nothing was actually compiled - "
            "usually a build-layout problem (e.g. source files outside the "
            "configured sourceDirectory), not a code defect."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

def _validation_tool(name: str, description: str) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }


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
    # Optional-loop failures are returned as secondary incidents. They are
    # never raised over the authoritative compile failure.
    incidents: List[Dict[str, str]] = field(default_factory=list)
    # Readable-but-not-writable files the model attempted to patch. The
    # caller converts this deterministic ownership conflict into plan-level
    # recovery instead of allowing a micro-loop to bypass subtask scope.
    scope_conflict_files: List[str] = field(default_factory=list)


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
    writable_files: Set[str],
    active_code_context: str,
    modified_files: Dict[str, str],
    read_files: Set[str],
    observed_revisions: Dict[str, str],
    scope_conflict_files: Set[str],
    validation_tool_name: str = "recompile",
    target_test: Optional[str] = None,
) -> str:
    """Executes one tool call and returns the string fed back to the model as
    the tool result. Every branch is small-argument-in, small-string-out -
    read_file/apply_patch never return/accept more than one file's worth of
    content. Reads are checked against files_in_scope/modified_files; writes
    are separately checked against writable_files (strict allowlists, not
    sanitized arbitrary paths) before touching disk.

    read_files (mutated in place, owned by the caller's per-loop scope) tracks
    which files this conversation has genuinely seen the real content of via
    read_file - see apply_patch's own use of it below for why this exists."""
    if call.get("argument_error"):
        return f"ERROR: incompatible tool arguments: {call['argument_error']}"
    name = call["name"]
    args = call["arguments"] if isinstance(call["arguments"], dict) else {}
    known_files = set(files_in_scope) | set(modified_files.keys())

    if name == "read_file":
        filepath = args.get("filepath")
        if not filepath or filepath not in known_files:
            return f"ERROR: '{filepath}' is not a file in this attempt's sandbox. Known files: {sorted(known_files)}"
        read_files.add(filepath)
        if filepath in modified_files:
            observed_revisions[filepath] = content_revision(modified_files[filepath])
            return modified_files[filepath]
        full_path = os.path.join(worktree_path, filepath)
        if not os.path.exists(full_path):
            return f"ERROR: '{filepath}' is listed as written but not found on disk."
        with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
        observed_revisions[filepath] = content_revision(content)
        return content

    if name == "list_files":
        substring = args.get("filter") or ""
        matches = sorted(f for f in known_files if substring in f)
        return "\n".join(matches) if matches else "(no files match)"

    if name == "list_dependencies":
        deps = get_pom_dependencies(os.path.join(worktree_path, "pom.xml"))
        return "\n".join(deps) if deps else "(no pom.xml, or no dependencies declared)"

    if name == "inspect_class":
        fqcn = args.get("fully_qualified_name")
        if not fqcn:
            return "ERROR: inspect_class requires 'fully_qualified_name'."
        simple_name = fqcn.rsplit(".", 1)[-1]
        workspace_matches = sorted(
            f for f in known_files
            if f.endswith(".java") and f.rsplit("/", 1)[-1] == f"{simple_name}.java"
        )
        if len(workspace_matches) == 1:
            return _dispatch_tool_call(
                {"name": "read_file", "arguments": {"filepath": workspace_matches[0]}},
                worktree_path, validator, files_in_scope, writable_files, active_code_context,
                modified_files, read_files, observed_revisions, scope_conflict_files,
                validation_tool_name, target_test,
            )
        if len(workspace_matches) > 1:
            return (
                f"'{simple_name}' matches multiple files already in this sandbox: "
                f"{', '.join(workspace_matches)} - use read_file with the exact path you mean."
            )
        external_api = validator.inspect_external_class(fqcn)
        if external_api:
            return external_api
        return (
            f"'{fqcn}' is not a file in this sandbox, and its real shape could not be resolved "
            "from the project's dependencies (is it declared and does the project's classpath "
            "actually resolve? try list_dependencies or resolve_missing_dependency)."
        )

    if name == "resolve_missing_dependency":
        symbol = args.get("symbol")
        if not symbol or "." not in symbol:
            return (
                "ERROR: resolve_missing_dependency requires a fully-qualified class or package "
                "name (containing a '.'), not a bare simple name - a bare name matches too many "
                "unrelated libraries to be useful."
            )
        last_segment = symbol.rsplit(".", 1)[-1]
        query_type = "fc" if last_segment[:1].isupper() else "g"
        autonomy_cfg = validator.autonomy_cfg
        allow_external_lookup = bool(
            autonomy_cfg.egress_policy != "local_only"
            and autonomy_cfg.web_lookup_enabled
        )
        if not allow_external_lookup:
            return (
                "External dependency lookup is disabled by local-only or web-lookup policy; "
                "use project-local dependencies and classpath evidence instead."
            )
        coord = resolve_maven_class(
            symbol, query_type, allow_external_lookup=allow_external_lookup,
        )
        if not coord and query_type == "fc":
            coord = resolve_maven_class(
                symbol, "g", allow_external_lookup=allow_external_lookup,
            )
        if not coord:
            return f"No Maven Central match found for '{symbol}'."
        return (
            f"'{symbol}' -> {coord['groupId']}:{coord['artifactId']}:{coord['version']}\n"
            f"<dependency>\n    <groupId>{coord['groupId']}</groupId>\n"
            f"    <artifactId>{coord['artifactId']}</artifactId>\n"
            f"    <version>{coord['version']}</version>\n</dependency>"
        )

    if name == "list_compiled_output":
        classes_dir = os.path.join(worktree_path, "target", "classes")
        compiled = []
        if os.path.isdir(classes_dir):
            for dirpath, _dirnames, filenames in os.walk(classes_dir):
                for fn in filenames:
                    if fn.endswith(".class"):
                        compiled.append(os.path.relpath(os.path.join(dirpath, fn), classes_dir))
        return "\n".join(sorted(compiled)) if compiled else "(target/classes is empty or doesn't exist - nothing was actually compiled)"

    if name == "apply_patch":
        filepath = args.get("filepath")
        edits = args.get("edits")
        if not filepath or filepath not in known_files:
            return f"ERROR: '{filepath}' is not a file in this attempt's sandbox - cannot patch a file that doesn't exist here. Known files: {sorted(known_files)}"
        if filepath not in writable_files:
            scope_conflict_files.add(filepath)
            return (
                f"PLAN_SCOPE_INSUFFICIENT: '{filepath}' is readable for diagnosis but is not "
                f"inside this subtask's approved writable scope: {sorted(writable_files)}"
            )
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

        expected_revision = observed_revisions.get(filepath, content_revision(orig_text))
        if content_revision(orig_text) != expected_revision:
            return (
                f"ERROR: '{filepath}' changed since it was read. Re-read the file before "
                "submitting another patch."
            )

        # Found live, 2026-08-17 (ignite_qpid_person, run b-10m): apply_patch's
        # OWN edit-application already re-reads the real, current, full file
        # content as orig_text (from disk or modified_files, identical to
        # what read_file just returned) - fully grounded. But it then passed
        # `active_code_context` (a snapshot fixed at the START of this
        # conversation, possibly skeletonized for token budget, never updated
        # as read_file gets called mid-loop) as apply_anchored_edits'
        # shown_context check, which independently re-validates the search
        # block against THAT stale snapshot. A model that called read_file,
        # was shown the real current content, and copied its search block
        # exactly from what it just read could still get rejected with
        # "elided in the skeletonized context and not shown to the model" -
        # false, it WAS shown, just through a different, more current channel
        # than the one this check was comparing against. Confirmed live: the
        # self-correction model read PersonDemoApp.java, correctly diagnosed
        # the cast issue, and proposed an exact, grounded fix that got
        # rejected by this stale-context check before ever reaching
        # recompile. Once a file has been read (or already modified) THIS
        # conversation, its own real content is strictly more current and
        # trustworthy evidence than the original prompt-time snapshot, so the
        # skeletonized-context check is skipped entirely for it - the strict
        # exact/unique anchor-matching against the REAL current content
        # (below) still fully protects against a stale or ambiguous edit.
        # For a file NEVER read or modified this conversation, the original
        # check stays exactly as-is - a model guessing at unseen content is
        # still exactly the failure mode this check exists to catch.
        effective_shown_context = "" if (filepath in read_files or filepath in modified_files) else active_code_context
        try:
            new_content = apply_anchored_edits(orig_text, edits, effective_shown_context)
        except ValueError as anchor_ex:
            # Fed back to the model as this tool call's result, NOT raised - the
            # whole point of this loop is letting the model retry within its
            # turn budget before attempt.py's own terminal QualityGateFailure.
            return f"ERROR: patch did not apply to '{filepath}': {anchor_ex}"

        full_path = os.path.join(worktree_path, filepath)
        try:
            # MA4.16 - AuthorizedFileWriter really enforces (raises
            # PolicyDeniedError, not audit-only) workspace containment + a
            # narrow sensitive-path check before this micro-loop's own
            # tool-driven patch reaches disk, using the real worktree_path
            # this call site has always had in scope.
            new_revision = AuthorizedFileWriter(worktree_path).commit_file(
                full_path, new_content, expected_revision=expected_revision,
            )
        except FileRevisionConflict as revision_ex:
            return f"ERROR: {revision_ex}"
        except PolicyDeniedError as denial:
            # Fed back to the model as this tool call's result, same as a
            # revision conflict - a real containment/sensitive-path denial
            # here is a signal the model should stop and reconsider, not a
            # terminal crash of the whole self-correction loop.
            return f"ERROR: {denial}"
        modified_files[filepath] = new_content
        observed_revisions[filepath] = new_revision
        return (
            f"Patch applied to '{filepath}'. Call {validation_tool_name} to verify "
            "it fixed the failure."
        )

    if name == validation_tool_name:
        result = (
            validator.run_tests(target_test=target_test)
            if target_test else validator.run_compile_check(list(files_in_scope))
        )
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
    writable_files: Optional[List[str]] = None,
    max_turns: int = 4,
    model_override: Optional[str] = None,
    base_url_override: Optional[str] = None,
    api_key_override: Optional[str] = None,
    extra_body_override: Optional[Dict[str, Any]] = None,
    failure_type: str = "compile",
    target_test: Optional[str] = None,
) -> SelfCorrectionResult:
    """Runs up to max_turns of native tool-calling against the given llm,
    trying to fix a real failure using small-argument tools: the original 4
    (read_file/list_files/apply_patch/recompile-or-retest) plus, since
    2026-08-22, 4 read-only "ground truth" tools (list_dependencies,
    inspect_class, resolve_missing_dependency, list_compiled_output) that
    close a structural gap none of the original 4 could reach: an external
    dependency's real API shape, or a real build-layout question (did
    anything actually get compiled, and where) - see each tool's own
    description for why. Returns resolved=True the moment a
    recompile()/retest() call reports SUCCESS; returns resolved=False if the
    budget runs out or the model stops calling tools without ever getting a
    passing check - either way, the caller (kriya/workflow/attempt.py) treats
    this exactly the same as if the loop had never run, falling through to
    the existing QualityGateFailure path.

    failure_type widens what "the failure" means beyond a plain compile
    error: "run_verification" is for the class of bug found live 2026-08-22
    (ignite_qpid_protocol) - a compile gate that reported success while
    build-layout was actually broken (Maven's sourceDirectory not covering
    real source files), only surfacing downstream as a confusing runtime
    "Could not find or load main class". For this failure_type, the
    validation tool is still `recompile` (unchanged - PolymorphicValidator's
    own false-positive safety net, added the same day, means a real compile
    now honestly reflects whether anything actually got built), NOT a full
    re-run of the generated application - actual runtime BEHAVIOR
    correctness stays owned by attempt.py's own run-verification cycle on
    the next attempt, this loop only fixes the INFRASTRUCTURE-shaped cause
    (wrong classpath, missing class, wrong sourceDirectory) that made the
    run fail for a reason that was never really about application logic."""
    validation_tool_name = "retest" if target_test else "recompile"
    writable_file_set = set(files_in_scope if writable_files is None else writable_files)
    if target_test:
        failure_label = "targeted test failure"
    elif failure_type == "run_verification":
        failure_label = "runtime verification failure (likely a build-layout or classpath problem, not application logic)"
    else:
        failure_label = "compile failure"
    tools = [
        READ_FILE_TOOL, LIST_FILES_TOOL, APPLY_PATCH_TOOL,
        LIST_DEPENDENCIES_TOOL, INSPECT_CLASS_TOOL, RESOLVE_MISSING_DEPENDENCY_TOOL,
        LIST_COMPILED_OUTPUT_TOOL,
        _validation_tool(
            validation_tool_name,
            "Re-run only the failing targeted test after a patch."
            if target_test else "Re-run the project's compile check after a patch.",
        ),
    ]
    modified_files: Dict[str, str] = {}
    read_files: Set[str] = set()
    observed_revisions: Dict[str, str] = {}
    scope_conflict_files: Set[str] = set()
    for filepath in files_in_scope:
        full_path = os.path.join(worktree_path, filepath)
        if os.path.exists(full_path):
            with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                observed_revisions[filepath] = content_revision(fh.read())
    transcript: List[Dict[str, Any]] = []
    last_compile_output = compile_error_output

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": _repair_system_prompt(failure_label, validation_tool_name)},
        {
            "role": "user",
            "content": (
                f"The project has this {failure_label}:\n{compile_error_output}\n\n"
                f"Files currently in the sandbox: {', '.join(sorted(files_in_scope))}\n\n"
                "Use the available tools to diagnose and fix the failure. Call "
                f"{validation_tool_name} after applying a fix to check whether it worked. If it "
                "now succeeds, stop calling tools - just reply with a short "
                "confirmation in plain text."
            ),
        },
    ]

    turns_used = 0
    for turn in range(max_turns):
        turns_used = turn + 1
        try:
            result = await llm.complete_with_tools(
                messages,
                tools,
                model_override=model_override,
                base_url_override=base_url_override,
                api_key_override=api_key_override,
                extra_body_override=extra_body_override,
            )
        except Exception as exc:
            # Found live, 2026-08-17 (ignite_qpid_person, run b-10l): this call
            # had NO exception handling, despite this function's own docstring
            # explicitly promising "either way, the caller treats this exactly
            # the same as if the loop had never run" - false for an exception,
            # which propagated all the way up past attempt.py's own unguarded
            # call site to workflow.py's outer `except Exception as e:`, where
            # retry_strategy.py's handle_attempt_failure() does
            # `raw_error_context = str(e)` and - since a bare HTTP/SDK
            # exception has no `.failure` attribute - falls through to a
            # generic Failure(type="general_error", message=raw_error_context).
            # Confirmed live: three consecutive HTTP 500s from Ollama while
            # parsing the model's own native tool call ("XML syntax error on
            # line 15: element <parameter> closed by </function>") completely
            # REPLACED the real, already-captured Maven compile error
            # (compile_error_output, sitting unused in attempt.py's own local
            # scope) as the attempt's failure text - every subsequent retry
            # then implicated applicationContext.xml (the only file-like token
            # in the HTTP error text) instead of the actually-broken
            # App.java, burning the whole retry budget on a file that was
            # never broken. Degrading to resolved=False here restores the
            # documented contract: the caller (attempt.py) falls through to
            # its own _build_quality_gate_failure() call using compile_res
            # ['output'] - the real Maven error - exactly as if this optional
            # loop had never run at all.
            logger.warning(
                "Self-correction tool loop failed (optional micro-loop, not the "
                f"original compile failure) - preserving original compile error: {exc}"
            )
            return SelfCorrectionResult(
                resolved=False,
                turns_used=turns_used,
                final_compile_output=last_compile_output,
                modified_files=modified_files,
                transcript=transcript,
                incidents=[{
                    "source": "self_correction",
                    "type": "model_or_tool_error",
                    "message": str(exc),
                }],
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
                call, worktree_path, validator, files_in_scope, writable_file_set,
                active_code_context, modified_files, read_files, observed_revisions,
                scope_conflict_files,
                validation_tool_name=validation_tool_name, target_test=target_test,
            )
            transcript.append(
                {"turn": turn, "tool": call["name"], "arguments": call["arguments"], "result": tool_result_text}
            )
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": tool_result_text})

            if scope_conflict_files:
                return SelfCorrectionResult(
                    resolved=False,
                    turns_used=turns_used,
                    final_compile_output=last_compile_output,
                    modified_files=modified_files,
                    transcript=transcript,
                    scope_conflict_files=sorted(scope_conflict_files),
                )

            if call["name"] == validation_tool_name:
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


# Public generic name for new callers. The compatibility name above remains
# because compile-repair call sites and external tests already import it.
run_repair_loop = run_self_correction_loop
