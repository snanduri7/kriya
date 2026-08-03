import json
import logging
import re
from abc import ABC
from typing import Any, Callable, Dict, List, Optional, Tuple

from kriya.config.config import FallbackModelConfig, LLMConfig
from kriya.core.llm import LLMClient

logger = logging.getLogger(__name__)


async def call_with_escalation(
    llm: LLMClient,
    system_prompt: str,
    prompt: str,
    candidates: List[Optional[Any]],
    json_mode: bool = False,
    stream_callback: Optional[Callable[[str], None]] = None,
    is_failure: Optional[Callable[[str], bool]] = None,
) -> str:
    """Tries each candidate in order - a role's own configured model, then its own
    escalation chain (kriya/config/config.py::AgentModelConfig) - via llm.complete(),
    returning the first response that doesn't raise and, if is_failure is given,
    doesn't satisfy is_failure(response). A None candidate (the common case: a role
    with no dedicated config) calls complete() with no overrides at all, preserving
    today's exact call shape/behavior - including LLMClient's internal client-reuse
    fast path - for any project that never touches agent_llms.

    If every candidate is exhausted, re-raises the last exception (or returns the
    last response if only is_failure judged every attempt inadequate, never a raw
    exception) - so a role with an empty/unset chain behaves exactly as if escalation
    didn't exist at all."""
    last_exc: Optional[Exception] = None
    last_response: Optional[str] = None
    for i, cand in enumerate(candidates):
        try:
            if cand is None:
                response = await llm.complete(
                    system_prompt, prompt, stream_callback=stream_callback, json_mode=json_mode,
                )
            else:
                response = await llm.complete(
                    system_prompt, prompt, stream_callback=stream_callback, json_mode=json_mode,
                    model_override=cand.model,
                    base_url_override=cand.base_url,
                    api_key_override=cand.api_key,
                    temperature_override=cand.temperature,
                    max_tokens_override=cand.max_tokens,
                    reasoning_override=cand.reasoning,
                )
        except Exception as ex:
            last_exc = ex
            logger.debug(f"Escalation attempt {i + 1}/{len(candidates)} raised: {ex}")
            continue
        if is_failure and is_failure(response):
            last_response = response
            last_exc = None
            logger.debug(f"Escalation attempt {i + 1}/{len(candidates)} produced an unusable response, trying next.")
            continue
        return response
    if last_exc:
        raise last_exc
    return last_response


def _is_unparseable_json(response: str) -> bool:
    """Failure signal for JSON-mode roles: escalate to the next candidate if the
    response doesn't even parse into a JSON object, rather than trusting the first
    model's output no matter what."""
    try:
        parsed = json.loads(DeveloperAgent._strip_markdown_fences(response))
    except Exception:
        return True
    return not isinstance(parsed, dict)


# =====================================================================
# 1. Base Agent
# =====================================================================

class BaseAgent(ABC):
    """Abstract Base Class for Kriya specialized agents."""

    def __init__(
        self,
        name: str,
        llm_client: LLMClient,
        role_llm: Optional[LLMConfig] = None,
        role_chain: Optional[List[FallbackModelConfig]] = None,
    ) -> None:
        self.name = name
        self.llm = llm_client
        # role_llm=None means "use LLMClient's own default model" - the common case
        # for a project that never configures agent_llms for this role. role_chain is
        # this role's OWN escalation list, tried in order if role_llm (or the
        # default) fails - independent of Developer's quality-gate-driven retry loop.
        self.role_llm = role_llm
        self.role_chain = role_chain or []

    @property
    def system_prompt(self) -> str:
        raise NotImplementedError

    def _candidates(self) -> List[Optional[Any]]:
        return [self.role_llm] + list(self.role_chain)

    async def run(self, prompt: str, stream_callback: Optional[Callable[[str], None]] = None) -> str:
        """Execute a text completion request, escalating through this role's chain
        only on a hard call failure (connection/timeout/HTTP/egress error) - a
        legitimately short-but-correct response is never wrongly retried just for
        being brief."""
        return await call_with_escalation(
            self.llm, self.system_prompt, prompt, self._candidates(), stream_callback=stream_callback,
        )


# =====================================================================
# 2. Specialized Agent Implementations
# =====================================================================

class PlannerAgent(BaseAgent):
    @property
    def system_prompt(self) -> str:
        return (
            "You are the Kriya Planner Agent.\n"
            "Your task is to decompose a software engineering request into logical step-by-step implementation tasks.\n"
            "Outline what files need to be created, modified, or verified.\n"
            "Format your plan clearly in Markdown."
        )


class ArchitectAgent(BaseAgent):
    @property
    def system_prompt(self) -> str:
        return (
            "You are the Kriya Architect Agent.\n"
            "Your task is to define the technical design, packages, interfaces, and architecture structure.\n"
            "Ensure the design aligns with the existing codebase structure provided in the context.\n"
            "Outline your interface design clearly in Markdown. DO NOT write or output the full implementation code of the source files. "
            "Only specify file paths, directory layouts, class/interface/method signatures, and bean configuration outlines. "
            "The implementation details and actual code inside files must be left entirely to the Developer Agent.\n"
            "\n"
            "MINIMALISM: Design the smallest set of files that cleanly satisfies the goal. Do not split a small "
            "amount of logic into extra helper classes, config classes, or wrapper files unless there is a concrete "
            "reuse, testability, or separation-of-concerns reason for it - every additional file you design is a "
            "file the Developer Agent must remember to actually create, and unnecessary splitting directly causes "
            "incomplete-generation failures downstream.\n"
            "\n"
            "REQUIRED FILES LIST: Always begin your design with a section titled exactly '## Files to Create or Modify' "
            "followed by one bullet per file you are designing, each bullet containing that file's exact relative "
            "path (e.g. '- src/main/java/com/example/BrokerServer.java'). When extending an existing project, this "
            "list MUST also include already-existing files that need real changes, not just brand-new ones - e.g. "
            "adding a new dependency to an already-existing pom.xml/build.gradle, or adding a bean to an "
            "already-existing Spring XML context. Check the Workspace Context above for what already exists before "
            "assuming a file only needs to be created. This list is the authoritative, complete set of files the "
            "Developer Agent must produce - do not mention any additional file path later in the design that is not "
            "already in this list, and do not list a file here that you don't actually design."
        )


class DeveloperAgent(BaseAgent):
    @property
    def system_prompt(self) -> str:
        return (
            "You are the Kriya Developer Agent.\n"
            "Your task is to implement the requested code changes.\n"
            "CRITICAL RULES FOR COMPILER CORRECTNESS:\n"
            "1. You must include all necessary import statements for all classes, annotations, and functions used in the files you generate or edit. For example, in Java, import all required Apache Ignite, Spring, and JMS types (e.g., org.apache.ignite.Ignite, org.apache.ignite.IgniteCache, org.apache.ignite.Ignition, javax.jms.*, etc.). Do not assume they are imported implicitly.\n"
            "2. Ensure the code compiles cleanly and matches standard structural definitions.\n"
            "3. If a previous compilation error is provided, fix the exact error and do not repeat the mistake.\n"
            "4. You must implement ALL files defined in the Architect Design Guidelines. Do not omit any files, leave placeholders, or defer their creation to a future step.\n"
            "\n"
            "Return a clean JSON block list containing the code modifications. Do NOT wrap your JSON in any extra markdown text (no ```json code blocks), just return the raw JSON array. "
            "Format your output EXACTLY as a JSON array of file objects, like this:\n"
            "[\n"
            "  {\n"
            "    \"filepath\": \"path/to/file.py\",\n"
            "    \"content\": \"raw file contents here\"\n"
            "  }\n"
            "]"
        )

    @staticmethod
    def _strip_markdown_fences(text: str) -> str:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            return "\n".join(lines).strip()

        # Reasoning models sometimes wrap the actual content in a fenced block but
        # surround it with conversational preamble/postamble instead of returning
        # only the fence. Prefer the largest fenced block over the raw text in that
        # case (largest, since a short illustrative aside could also be fenced).
        fences = re.findall(r"```[a-zA-Z0-9_+-]*\n(.*?)\n```", cleaned, re.DOTALL)
        if fences:
            return max(fences, key=len).strip()

        return cleaned

    @staticmethod
    def _extract_json_value(text: str) -> Any:
        """Recovers a JSON value from a response that's supposed to be JSON but might
        have prose preamble/postamble around it - observed live: a reasoning model
        sometimes explains its reasoning in plain text before finally emitting the
        JSON, even under response_format=json_object (not every backend enforces that
        as a hard token-level constraint). Tries, in order: direct parse, parse after
        stripping markdown fences, and parsing the first '['..last ']' or first
        '{'..last '}' span found in the text (array preferred when both are present
        and the array starts first). Raises the direct-parse JSONDecodeError if
        nothing works, so callers see the original diagnostic."""
        cleaned = DeveloperAgent._strip_markdown_fences(text)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            start_a, end_a = cleaned.find("["), cleaned.rfind("]")
            start_d, end_d = cleaned.find("{"), cleaned.rfind("}")

            if start_a != -1 and end_a != -1 and (start_d == -1 or start_a < start_d):
                try:
                    return json.loads(cleaned[start_a:end_a + 1])
                except Exception:
                    pass

            if start_d != -1 and end_d != -1:
                try:
                    return json.loads(cleaned[start_d:end_d + 1])
                except Exception:
                    pass

            logger.warning(f"Could not recover a JSON value from response text: {text[:200]}...")
            raise e

    @staticmethod
    def _split_fix_analysis(text: str) -> Tuple[Optional[str], str]:
        """Splits a per-file retry completion into (fix_analysis, file_content) when
        the model complied with the MANDATORY FIX ANALYSIS instruction added to the
        prompt whenever a real prior error exists (see _fill_missing_content) - a
        case-insensitive search for a literal "FILE CONTENT:" marker line, everything
        before it is the analysis, everything after is the actual file content.

        Found live as a real, generalizable root cause during golden-use-case
        validation, not guessed: single-shot, non-reasoning completion (this repo's
        default local model config) regenerated byte-for-byte identical broken code
        across all 7 retry attempts of a real failing run, despite the exact compile
        error being present in every prompt - confirmed directly by diffing the
        model's own output across attempts. The model was never actually engaging
        with the stated error before writing code; it was just re-emitting its
        strongest prior completion regardless of what the error said. Forcing an
        explicit, structurally-required "identify the error, then fix it" step before
        code generation is a standard chain-of-thought prompting technique that works
        independent of whether the underlying model is a "reasoning" model - it does
        NOT touch kriya.config.llm.reasoning, which is a different thing entirely
        (that flag only accommodates a model that already emits <think> tags on its
        own; qwen3-coder:30b, the model this was diagnosed against, isn't one, so
        toggling that flag would have done nothing here).

        Falls back to (None, text unchanged) if the model didn't include the marker,
        so a non-compliant response degrades to the pre-existing plain-content
        behavior rather than corrupting it - this is a prompt-level nudge, not a hard
        parsing requirement."""
        match = re.search(r"file content:", text, re.IGNORECASE)
        if not match:
            return None, text
        analysis = text[:match.start()].strip()
        content = text[match.end():].strip()
        analysis = re.sub(r"^\s*fix analysis:\s*", "", analysis, flags=re.IGNORECASE).strip()
        return (analysis or None), content

    @staticmethod
    def _normalize_file_entries(parsed: Any) -> Optional[List[Dict[str, Any]]]:
        """Normalizes whatever shape the file-list completion parsed into - a list of
        path strings, a list of dicts with filepath/path (+ optional content/edits), or
        a dict wrapping either - into a uniform list of {"filepath", "content", "edits"}
        dicts. content/edits are None when that file still needs its content generated.
        Returns None if nothing usable was found."""
        candidates = None
        if isinstance(parsed, list):
            candidates = parsed
        elif isinstance(parsed, dict):
            for val in parsed.values():
                if isinstance(val, list) and val:
                    candidates = val
                    break

        if not candidates:
            return None

        if all(isinstance(x, str) for x in candidates):
            return [{"filepath": p, "content": None, "edits": None} for p in candidates]

        if all(isinstance(x, dict) for x in candidates):
            entries = []
            for item in candidates:
                filepath = item.get("filepath") or item.get("path")
                if not filepath:
                    continue
                entries.append({
                    "filepath": filepath,
                    "content": item.get("content") or None,
                    "edits": item.get("edits") or None,
                })
            return entries or None

        return None

    async def _fill_missing_content(
        self,
        file_entries: List[Dict[str, Any]],
        task_description: str,
        design_context: str,
        existing_code_context: str,
        stream_callback: Optional[Callable[[str], None]],
        model_override: Optional[str],
        base_url_override: Optional[str],
        api_key_override: Optional[str],
        prior_error_context: Optional[str] = None,
        implicated_files: Optional[List[str]] = None,
        error_source_context: Optional[Dict[str, str]] = None,
        retry_temperature: Optional[float] = None,
    ) -> List[Dict[str, str]]:
        """Passes through any entry that already has real content/edits unchanged (no
        extra call), and individually generates content for any entry that doesn't -
        so a model that only fills in some files in its file-list response doesn't
        silently end up with empty or missing files.

        retry_temperature, when set (config-driven, see LLMConfig.retry_temperature),
        overrides the completion temperature ONLY for a file this call is actually
        applying the fix-analysis instruction to (same scope as implicated_files) -
        a real, cited finding (not this project's own speculation) found code-gen
        success rate drops as temperature rises even within the low end of the
        range Kriya already defaults to, the opposite of "add randomness to shake a
        stuck retry loose." Left unset (None) by default - opt-in, not a silent
        behavior change.

        implicated_files scopes prior_error_context's fix-analysis instruction to
        only the file(s) the error actually names - found live as a real bug: a
        full-set retry regenerates every file in the batch, and without this scope
        EVERY file's per-file prompt got the same "explain the fix" instruction
        even though the error only ever implicated one of them, producing
        confused/wrong analyses for unrelated files (e.g. blaming a fine Person.java
        for a bug that was actually in a different file's raw-type cache access)
        and diluting the one analysis call that actually mattered. None means
        "apply to every file" (the pre-existing behavior, and correct for a
        targeted retry, where every file in the batch already IS implicated by
        construction via known_target_files)."""
        all_paths = [e["filepath"] for e in file_entries]
        files_out = []
        for entry in file_entries:
            filepath = entry["filepath"]
            if entry.get("content") or entry.get("edits"):
                files_out.append({"filepath": filepath, "content": entry.get("content"), "edits": entry.get("edits") or []})
                continue

            if stream_callback:
                stream_callback(f"\n[Implementing file: {filepath}]")

            file_sys_prompt = (
                "You are the Kriya Developer Agent.\n"
                "Your task is to write the complete, production-grade source code for the requested file path, "
                "and ONLY that one file. Return ONLY the raw file content for that single file. Do not include "
                "markdown code block wrappers (like ```), conversational explanation, or the content of any other "
                "file - even one you're told is also part of this batch. If you believe another file also needs "
                "a change, that is out of scope for this response and will be handled separately; do not act on it "
                "here, and do not prepend or append its content."
            )

            sibling_paths = [p for p in all_paths if p != filepath]
            sibling_section = (
                f"=== Other Files In This Batch (context only - do NOT output their content here) ===\n"
                f"{', '.join(sibling_paths)}\n\n"
            ) if sibling_paths else ""

            # Only present on a retry that's directly responding to a real prior
            # Quality Gate failure (targeted retries, and full-set retries after
            # attempt 1) AND only for a file the error actually implicates - never
            # on a clean first attempt, where there's no error yet to analyze, and
            # never on an unrelated file just along for the ride in a full-set
            # regeneration (implicated_files is None for a targeted retry, where
            # every file in the batch already IS implicated by construction).
            # Forces an explicit "identify the error, then fix it" step before
            # code generation - confirmed live as a real, generalizable gap:
            # single-shot completion with no forcing function regenerated
            # byte-for-byte identical broken code across 7 straight retries despite
            # the exact error being present in every prompt, because nothing required
            # the model to actually engage with it before writing code. See
            # _split_fix_analysis for the full live-testing rationale.
            file_is_implicated = implicated_files is None or filepath in implicated_files
            apply_fix_analysis = bool(prior_error_context) and file_is_implicated
            fix_analysis_instruction = (
                "\nThis is a RETRY: the previous attempt at this file failed the error described "
                "in the Task section above. Before writing any code, you MUST first write a line "
                "\"FIX ANALYSIS:\" followed by 1-3 sentences identifying the SPECIFIC cause of that "
                "error and exactly what you are changing to address it. Only after that analysis, "
                "write the line \"FILE CONTENT:\" on its own line, followed by the complete file "
                "content and nothing else after it.\n"
            ) if apply_fix_analysis else ""

            # The exact broken source line(s), read fresh from the worktree by
            # extract_error_source_locations()/_build_error_source_context()
            # (kriya/workflow/workflow.py) - generic across any compile error
            # shape, since it keys off javac's universal file:[line,col] locator
            # rather than any specific error message. Only shown alongside the
            # fix-analysis instruction, same scoping (this file, this retry).
            source_context_block = (
                (error_source_context or {}).get(filepath, "") if apply_fix_analysis else ""
            )

            # Stable, large blocks first (existing code context, then architecture design) so
            # same-model retries can reuse the inference server's KV-cache prefix; the task
            # description grows with each retry's error context, so it - along with the
            # per-file sibling list and instruction, which already vary per call - goes last.
            # The "only this file" instruction is repeated at the very end, right before
            # generation starts, not just in the system prompt - confirmed live as necessary:
            # a reasoning model that had it only once (system prompt) still concatenated a
            # sibling file's full content into this file's response.
            file_prompt = (
                f"=== Existing Code Base Context ===\n{existing_code_context}\n\n"
                f"=== Architecture Design ===\n{design_context}\n\n"
                f"=== Task ===\n{task_description}\n\n"
                f"{sibling_section}"
                f"Please generate the complete, correct, and production-grade file content for: '{filepath}'\n"
                f"Return ONLY the content of '{filepath}' - nothing before it, nothing after it, no other file."
                f"{source_context_block}"
                f"{fix_analysis_instruction}"
            )

            content = await self.llm.complete(
                file_sys_prompt,
                file_prompt,
                stream_callback=stream_callback,
                json_mode=False,
                model_override=model_override,
                base_url_override=base_url_override,
                api_key_override=api_key_override,
                temperature_override=retry_temperature if apply_fix_analysis else None,
            )

            if apply_fix_analysis:
                analysis, content = self._split_fix_analysis(content)
                if analysis:
                    logger.info(f"Developer fix analysis for '{filepath}': {analysis}")

            files_out.append({"filepath": filepath, "content": self._strip_markdown_fences(content)})
        return files_out

    async def run_generation(
        self,
        task_description: str,
        design_context: str,
        existing_code_context: str,
        stream_callback: Optional[Callable[[str], None]] = None,
        model_override: Optional[str] = None,
        base_url_override: Optional[str] = None,
        api_key_override: Optional[str] = None,
        known_target_files: Optional[List[str]] = None,
        prior_error_context: Optional[str] = None,
        implicated_files: Optional[List[str]] = None,
        error_source_context: Optional[Dict[str, str]] = None,
        retry_temperature: Optional[float] = None,
    ) -> List[Dict[str, str]]:
        """Generates code files based on planner task and architect design. Prefers
        per-file generation for reliability (filling in only what's missing), falling
        back to single-stage generation only if a usable file list can't be determined
        at all.

        known_target_files: set by a caller that already knows exactly which files
        need (re)writing - a targeted retry or missing-file recovery, where
        extract_implicated_files()/the Architect's own required-files list already
        determined this deterministically. When set, skips Step 1 entirely (asking
        the model to independently re-derive a file list) and generates content
        directly for exactly this set. Without this, the target set only ever
        reached the model as prose inside task_description, with no enforcement
        that its own file-list answer actually matched it - confirmed live as the
        cause of a targeted retry silently dropping the one file that actually
        needed fixing from its own file-list response, then never revisiting it,
        burning its entire retry budget without making any progress.

        prior_error_context: the raw Quality Gate failure text this call is retrying
        in response to, if any - passed straight through to _fill_missing_content to
        trigger the mandatory fix-analysis step (see _split_fix_analysis). None on a
        clean first attempt, where there's no error yet to analyze.

        implicated_files/error_source_context: see _fill_missing_content - scopes
        the fix-analysis instruction and source-line context to only the file(s)
        the error actually names, mattering specifically for a full-set retry
        (known_target_files unset, every written file passes through this same
        per-file loop) where without this scope every file in the batch got the
        same "explain the fix" instruction regardless of relevance."""
        if known_target_files:
            file_entries = [{"filepath": p, "content": None, "edits": None} for p in known_target_files]
            return await self._fill_missing_content(
                file_entries, task_description, design_context, existing_code_context,
                stream_callback, model_override, base_url_override, api_key_override,
                prior_error_context, implicated_files, error_source_context, retry_temperature,
            )

        # Step 1: Query the model for the list of files to generate (or full implementation if mocked in tests)
        system_list_prompt = (
            "You are the Kriya File List Planner.\n"
            "Your task is to identify and return a list of file paths that need to be created or modified based on the design.\n"
            "Return ONLY a JSON list of strings (e.g. [\"pom.xml\", \"src/main/java/App.java\"]). Do not include markdown wraps."
        )

        list_prompt = (
            f"=== Design ===\n{design_context}\n\n"
            f"=== Task ===\n{task_description}\n\n"
            "Please return the JSON list of files to create/modify."
        )

        try:
            response_str = await self.llm.complete(
                system_list_prompt,
                list_prompt,
                json_mode=True,
                model_override=model_override,
                base_url_override=base_url_override,
                api_key_override=api_key_override
            )

            parsed = self._extract_json_value(response_str)
            file_entries = self._normalize_file_entries(parsed)

            if file_entries:
                return await self._fill_missing_content(
                    file_entries, task_description, design_context, existing_code_context,
                    stream_callback, model_override, base_url_override, api_key_override,
                    prior_error_context, implicated_files, error_source_context, retry_temperature,
                )

        except Exception as e:
            logger.warning(f"Failed to resolve file list from Developer Agent: {e}. Falling back to single-stage generation.")

        # Fallback to single-stage generation (original implementation)
        # Same stable-first/volatile-last ordering as _fill_missing_content, for KV-cache reuse across retries.
        prompt = (
            f"=== Existing Code Base Context ===\n{existing_code_context}\n\n"
            f"=== Architect Design Guidelines ===\n{design_context}\n\n"
            f"=== User Request & Task ===\n{task_description}\n\n"
            "Please generate the complete, production-grade files. Return ONLY the JSON list of files."
        )
        
        response_str = await self.llm.complete(
            self.system_prompt, 
            prompt, 
            stream_callback=stream_callback,
            json_mode=True,
            model_override=model_override,
            base_url_override=base_url_override,
            api_key_override=api_key_override
        )
        
        try:
            res = self._extract_json_value(response_str)
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse Developer Agent response as JSON: {e}. Raw response: {response_str}") from e

        if isinstance(res, dict):
            for _key, val in res.items():
                if isinstance(val, list) and len(val) > 0 and all(isinstance(x, dict) and ("filepath" in x or "path" in x) for x in val):
                    for item in val:
                        if "path" in item and "filepath" not in item:
                            item["filepath"] = item["path"]
                    return val
            return [res]
        if isinstance(res, list):
            return [item for item in res if isinstance(item, dict)]
        return res


class RunVerifierAgent(BaseAgent):
    """Drives the Runtime Verification Gate: decides whether a goal describes behavior
    that compiling/testing alone can't verify, and (if so) grades captured output from
    actually running the generated app against that goal. Compile and test checks only
    prove the code is valid - they say nothing about whether it does what was asked."""

    @property
    def system_prompt(self) -> str:
        return (
            "You are the Kriya Run Verification Judge.\n"
            "You decide whether a goal describes observable RUNTIME BEHAVIOR (e.g. \"send a "
            "message and print the result\", \"start a server and respond to a request\") that "
            "compiling and passing the existing test suite would NOT actually verify.\n"
            "Only self-terminating/batch entrypoints can be verified this way - a script or app "
            "that runs, does its work, and exits on its own. Do not propose running a long-lived "
            "server/daemon that never exits by itself.\n"
            "Return ONLY a JSON object, no markdown fences, no extra commentary, with exactly "
            "these fields:\n"
            "{\n"
            '  "should_run": true or false,\n'
            '  "run_commands": [["executable", "arg1", "arg2"], ...] or null,\n'
            '  "command_source": "goal_explicit" or "inferred",\n'
            '  "success_criteria": "one or two sentences describing what observable output would prove success"\n'
            "}\n"
            "run_commands is an ORDERED LIST of commands to run in sequence, in the same working "
            "directory (so files/state one command creates persist for the next). Most goals need "
            "only ONE command - return a single-element list. But if the goal's correctness can "
            "only be observed across MULTIPLE invocations (e.g. \"add an item, then list items\" - "
            "a single no-argument invocation can only ever show a help/usage message, never "
            "demonstrate the add-then-list behavior; \"write a value, then read it back\"; "
            "\"start a server, then curl an endpoint\" only if the server itself exits after "
            "handling one request), return each invocation as its own element, in the order they "
            "must run. For example, if the entrypoint file listed under \"Files Generated\" below is "
            "at path src/main/python/cli.py, and the goal needs an add then a list, return exactly "
            "[[\"python\", \"src/main/python/cli.py\", \"add\", \"Task 1\"], [\"python\", "
            "\"src/main/python/cli.py\", \"list\"]] - ALWAYS the entrypoint's real path exactly as it "
            "appears under \"Files Generated\", never a bare filename guessed from habit, even if an "
            "example here or elsewhere used a bare filename; a multi-word argument value (like "
            "\"Task 1\") is still ONE argv element, never split across two. Never invoke a CLI's own "
            "entrypoint with zero arguments as your only command just because the goal didn't spell "
            "out exact CLI flags - infer the concrete arguments needed to actually exercise the "
            "described behavior from the design/goal.\n"
            "If the goal explicitly states how to run the app (e.g. names a specific command "
            "like \"mvn exec:exec\" or \"run with python app.py\"), extract that exact command "
            "and set command_source to \"goal_explicit\". Otherwise, if you can reasonably infer "
            "run command(s) from the generated files, set command_source to \"inferred\": match "
            "whichever exec:GOAL the pom.xml's exec-maven-plugin configuration is actually shaped "
            "for, don't guess - a configuration with an <executable> element and an <arguments> "
            "list (with a -classpath argument and a bare <classpath/> placeholder) implies "
            "[\"mvn\", \"exec:exec\"]; a bare <mainClass> element with no <executable>/<arguments> "
            "implies [\"mvn\", \"exec:java\"]. Note exec-maven-plugin's exec:java goal has NO way "
            "to pass JVM startup flags (no \"jvmArguments\" parameter exists, and exec:java runs "
            "inside Maven's own already-started JVM, which can't retroactively gain --add-opens "
            "etc.) - any app needing JVM flags MUST use exec:exec, so a project depending on "
            "libraries that typically need --add-opens (embedded brokers, in-memory data grids, "
            "and similar reflection-heavy JVM 17+ libraries) is far more likely to have (or need) "
            "the exec:exec shape than the simpler exec:java one. A Python file with a __main__ "
            "guard implies [\"python\", \"that_file.py\"]. If there is no runnable, self-terminating "
            "entrypoint at all (a library, a config file, a long-running service, or the goal doesn't "
            "describe observable behavior), set should_run to false, run_commands to null, and "
            "success_criteria to an empty string."
        )

    async def judge(
        self,
        goal: str,
        design: str,
        files_written: List[str],
        build_file_content: Optional[str] = None,
    ) -> Dict[str, Any]:
        prompt = (
            f"=== Architecture Design ===\n{design}\n\n"
            f"=== Files Generated (already compiled and passed any existing tests) ===\n"
            f"{chr(10).join(files_written)}\n\n"
        )
        # The system prompt above already tells you precisely how to read a
        # pom.xml's exec-maven-plugin shape (exec:exec vs exec:java) - this is
        # the actual data to apply that rule against. Without it you're guessing
        # blind: design is the Architect's own deliberately minimized output
        # (often just a bare file list, by convention), so the richer detail a
        # Planner's plan may have correctly stated ("use exec:exec") frequently
        # doesn't survive to reach you - confirmed live, twice, as a real bug:
        # an exec:exec-shaped pom (bare <classpath/> in <arguments>, needed for
        # --add-opens JVM flags) was still judged as exec:java both times.
        if build_file_content:
            prompt += f"=== Actual pom.xml content (ground truth for how to invoke this app) ===\n{build_file_content}\n\n"
        prompt += (
            f"=== Goal ===\n{goal}\n\n"
            "Decide whether this goal warrants runtime verification, per the rules above."
        )
        response_str = await call_with_escalation(
            self.llm, self.system_prompt, prompt, self._candidates(),
            json_mode=True, is_failure=_is_unparseable_json,
        )
        try:
            parsed = json.loads(DeveloperAgent._strip_markdown_fences(response_str))
        except Exception as e:
            logger.warning(f"Run Verifier judge() returned unparseable JSON, skipping run verification: {e}")
            return {"should_run": False, "run_commands": None, "command_source": "inferred", "success_criteria": ""}

        if not isinstance(parsed, dict):
            return {"should_run": False, "run_commands": None, "command_source": "inferred", "success_criteria": ""}

        raw_commands = parsed.get("run_commands")
        # Tolerate a model still returning the old single-command shape
        # (["executable", "arg1"]) instead of a list of commands.
        if isinstance(raw_commands, list) and raw_commands and all(isinstance(x, str) for x in raw_commands):
            raw_commands = [raw_commands]
        run_commands = None
        if isinstance(raw_commands, list) and raw_commands:
            candidate = [
                cmd for cmd in raw_commands
                if isinstance(cmd, list) and cmd and all(isinstance(x, str) for x in cmd)
            ]
            if len(candidate) == len(raw_commands):
                run_commands = candidate

        return {
            "should_run": bool(parsed.get("should_run")) and run_commands is not None,
            "run_commands": run_commands,
            "command_source": parsed.get("command_source") if parsed.get("command_source") in ("goal_explicit", "inferred") else "inferred",
            "success_criteria": parsed.get("success_criteria") or "",
        }

    async def grade(
        self,
        goal: str,
        success_criteria: str,
        output: str,
        returncode: Optional[int],
    ) -> Dict[str, Any]:
        grader_system_prompt = (
            "You are the Kriya Run Verification Grader.\n"
            "You will be given the original goal, a description of what a successful run's "
            "output should show, and the ACTUAL captured stdout/stderr and exit code from "
            "actually running the generated application.\n"
            "Decide whether the captured output demonstrates the goal was genuinely achieved - "
            "not merely that the process didn't crash. Be strict: exit code 0 alone is not "
            "sufficient evidence if the described behavior isn't actually visible in the output.\n"
            "Return ONLY a JSON object, no markdown fences, no extra commentary:\n"
            '{"passed": true or false, "reasoning": "one or two sentences citing specific evidence from the output"}'
        )
        prompt = (
            f"=== Goal ===\n{goal}\n\n"
            f"=== Expected Success Criteria ===\n{success_criteria}\n\n"
            f"=== Actual Exit Code ===\n{returncode}\n\n"
            f"=== Actual Captured Output ===\n{output}\n\n"
            "Did this run actually succeed per the criteria above?"
        )
        response_str = await call_with_escalation(
            self.llm, grader_system_prompt, prompt, self._candidates(),
            json_mode=True, is_failure=_is_unparseable_json,
        )
        try:
            parsed = json.loads(DeveloperAgent._strip_markdown_fences(response_str))
        except Exception as e:
            logger.warning(f"Run Verifier grade() returned unparseable JSON, treating as failure: {e}")
            return {"passed": False, "reasoning": f"Grader response could not be parsed: {e}"}

        if not isinstance(parsed, dict):
            return {"passed": False, "reasoning": "Grader response was not a JSON object."}

        return {"passed": bool(parsed.get("passed")), "reasoning": parsed.get("reasoning") or ""}


class SkillGapAgent(BaseAgent):
    """Turns user-supplied reference material (a fetched URL, a local file, or pasted
    text) into concrete skill rules/examples when Kriya doesn't have verified
    information for an active skill. Also checks new candidate rules against the
    skill's existing ones so a skill's rules.txt doesn't silently accumulate
    contradictions as it grows from multiple ingestions over time."""

    @property
    def system_prompt(self) -> str:
        return (
            "You are the Kriya Skill Gap Agent.\n"
            "You are given reference material (documentation, source code, or similar) "
            "and a description of what specific information is missing. Extract concrete, "
            "concise engineering rules and/or complete example file content from the "
            "reference material that fill that gap.\n"
            "Rules must be single sentences, specific and actionable (e.g. exact field "
            "names, version numbers, API signatures) - not vague summaries. Only extract "
            "what the reference material actually supports; do not invent or guess "
            "anything it doesn't state.\n"
            "You will also be given the skill's EXISTING rules. Check every candidate rule "
            "against them: if a candidate contradicts an existing rule (e.g. a different "
            "version number, a different field value), do NOT add it as a plain rule - put "
            "it in \"conflicts\" instead so a human can decide, rather than letting "
            "contradictory rules silently coexist.\n"
            "Return ONLY a JSON object, no markdown fences, no extra commentary:\n"
            "{\n"
            '  "rules": ["new rule 1", "new rule 2"],\n'
            '  "examples": {"filename.ext": "full file content"},\n'
            '  "conflicts": [{"candidate_rule": "...", "conflicts_with": "...", "reason": "..."}]\n'
            "}\n"
            "If the reference material doesn't actually address the described gap, return "
            "empty lists/objects for all three fields - do not force something irrelevant."
        )

    async def extract_skill_update(
        self,
        reference_text: str,
        gap_description: str,
        existing_rules: List[str],
    ) -> Dict[str, Any]:
        # Reference material (web pages, source files) can be long - the LLM client's
        # own token budget will truncate server-side if needed, but keep this bounded
        # up front so a huge fetch doesn't dominate the prompt.
        truncated_reference = reference_text[:20000]
        prompt = (
            f"=== Reference Material ===\n{truncated_reference}\n\n"
            f"=== Existing Rules For This Skill ===\n"
            f"{chr(10).join(existing_rules) if existing_rules else '(none yet)'}\n\n"
            f"=== What's Missing ===\n{gap_description}\n\n"
            "Extract rules/examples per the instructions above."
        )
        response_str = await call_with_escalation(
            self.llm, self.system_prompt, prompt, self._candidates(),
            json_mode=True, is_failure=_is_unparseable_json,
        )
        try:
            parsed = json.loads(DeveloperAgent._strip_markdown_fences(response_str))
        except Exception as e:
            logger.warning(f"Skill Gap Agent returned unparseable JSON: {e}")
            return {"rules": [], "examples": {}, "conflicts": []}

        if not isinstance(parsed, dict):
            return {"rules": [], "examples": {}, "conflicts": []}

        rules = parsed.get("rules")
        rules = [r for r in rules if isinstance(r, str) and r.strip()] if isinstance(rules, list) else []

        examples = parsed.get("examples")
        examples = examples if isinstance(examples, dict) and all(isinstance(v, str) for v in examples.values()) else {}

        conflicts = parsed.get("conflicts")
        conflicts = conflicts if isinstance(conflicts, list) else []

        # The prompt tells the model not to put a conflicting candidate in both "rules"
        # and "conflicts", but that's an instruction, not a guarantee - a real run
        # showed a non-reasoning model doing exactly that (flagging a rule as
        # conflicting AND separately listing it as a plain rule in the same response).
        # Enforce mutual exclusivity in code rather than trusting prompt adherence:
        # anything already flagged as conflicting must not also be silently added.
        conflicting_texts = {
            c.get("candidate_rule", "").strip()
            for c in conflicts if isinstance(c, dict) and c.get("candidate_rule")
        }
        if conflicting_texts:
            rules = [r for r in rules if r.strip() not in conflicting_texts]

        return {"rules": rules, "examples": examples, "conflicts": conflicts}

    async def check_skill_conflicts(
        self,
        skill_a_name: str,
        skill_a_rules: List[str],
        skill_b_name: str,
        skill_b_rules: List[str],
    ) -> List[Dict[str, str]]:
        """Compares two skills' rule sets for genuine contradictions when both are
        active for the same generation run (e.g. two broker skills each pinning a
        different value for what must be a single shared setting). Two skills merely
        sharing a topic isn't a conflict - only flag it if following both rules at
        once is actually impossible."""
        if not skill_a_rules or not skill_b_rules:
            return []

        system_prompt = (
            "You are the Kriya Skill Conflict Checker.\n"
            "You are given the rule sets of two engineering skills that are both active "
            "for the same code generation run. Identify pairs of rules that GENUINELY "
            "contradict each other if both were followed at once (e.g. two different "
            "version pins for what should be the same dependency, two different values "
            "for what must be a single shared config setting like a port or protocol). "
            "Do NOT flag rules that merely share a topic but don't actually conflict "
            "(e.g. two different brokers each defining their own, independent config "
            "key is not a conflict).\n"
            "Return ONLY a JSON object, no markdown fences, no extra commentary:\n"
            "{\n"
            '  "conflicts": [{"rule_a": "<verbatim from Skill A>", "rule_b": "<verbatim from Skill B>", "explanation": "..."}]\n'
            "}\n"
            "rule_a and rule_b MUST be copied verbatim, character-for-character, from "
            "the provided rule lists - do not paraphrase or summarize them.\n"
            'If nothing genuinely conflicts, return {"conflicts": []}.'
        )
        prompt = (
            f"=== Skill A: {skill_a_name} ===\n" + "\n".join(skill_a_rules) + "\n\n"
            f"=== Skill B: {skill_b_name} ===\n" + "\n".join(skill_b_rules) + "\n\n"
            "Identify any genuine contradictions per the instructions above."
        )
        response_str = await call_with_escalation(
            self.llm, system_prompt, prompt, self._candidates(),
            json_mode=True, is_failure=_is_unparseable_json,
        )
        try:
            parsed = json.loads(DeveloperAgent._strip_markdown_fences(response_str))
        except Exception as e:
            logger.warning(f"Skill Conflict Checker returned unparseable JSON: {e}")
            return []
        if not isinstance(parsed, dict):
            return []
        conflicts = parsed.get("conflicts")
        if not isinstance(conflicts, list):
            return []

        # Defensive, same reasoning as extract_skill_update's mutual-exclusivity fix:
        # only trust a "conflict" whose rule text is an exact match against the real
        # rule sets, so a hallucinated or paraphrased conflict can never silently
        # exclude real rule content from generation context.
        valid = []
        for c in conflicts:
            if not isinstance(c, dict):
                continue
            rule_a = c.get("rule_a", "")
            rule_b = c.get("rule_b", "")
            if rule_a in skill_a_rules and rule_b in skill_b_rules:
                valid.append({"rule_a": rule_a, "rule_b": rule_b, "explanation": c.get("explanation", "")})
            else:
                logger.warning(
                    f"Skill Conflict Checker returned a conflict whose rule text didn't "
                    f"exactly match '{skill_a_name}'/'{skill_b_name}' rules - discarding."
                )
        return valid


class ReviewerAgent(BaseAgent):
    @property
    def system_prompt(self) -> str:
        return (
            "You are the Kriya Reviewer Agent.\n"
            "Your task is to review proposed code changes for bugs, style consistency, testing coverage, and architectural alignment.\n"
            "State whether the code is approved or rejected, and list details of any issues.\n"
            "Please adhere to these guidelines:\n"
            "1. Be pragmatic: If the user goal does not explicitly request unit tests, test files, or documentation (like a README), do not reject the submission solely for their absence. Instead, list them as optional recommendations.\n"
            "2. Avoid hallucinations: When checking long configuration files (like pom.xml or build files), double-check your analysis. Do not claim parameters, arguments, or dependencies are missing unless you are absolutely certain they are absent from the generated content.\n"
            "3. Run Instructions: At the end of your review report, always include a section '## How to Run the Application' detailing exactly how to compile, start, and verify the generated application (e.g. specifying 'mvn clean compile', 'python main.py', etc.).\n"
            "4. Truncation awareness: if any file's content is marked TRUNCATED (content omitted because it exceeded the review size budget), you MUST explicitly say so at the top of your report and make clear your review only covers the portion you were actually shown - never silently review a partial file as if it were complete."
        )
