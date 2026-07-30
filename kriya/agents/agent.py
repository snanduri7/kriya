import json
import logging
import re
from abc import ABC
from typing import Any, Callable, Dict, List, Optional

from kriya.core.llm import LLMClient

logger = logging.getLogger(__name__)

# =====================================================================
# 1. Base Agent
# =====================================================================

class BaseAgent(ABC):
    """Abstract Base Class for Kriya specialized agents."""

    def __init__(self, name: str, llm_client: LLMClient) -> None:
        self.name = name
        self.llm = llm_client

    @property
    def system_prompt(self) -> str:
        raise NotImplementedError

    async def run(self, prompt: str, stream_callback: Optional[Callable[[str], None]] = None) -> str:
        """Execute a text completion request using the LLM Client."""
        return await self.llm.complete(self.system_prompt, prompt, stream_callback=stream_callback)


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
            "The implementation details and actual code inside files must be left entirely to the Developer Agent."
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
    ) -> List[Dict[str, str]]:
        """Passes through any entry that already has real content/edits unchanged (no
        extra call), and individually generates content for any entry that doesn't -
        so a model that only fills in some files in its file-list response doesn't
        silently end up with empty or missing files."""
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
                "Your task is to write the complete, production-grade source code for the requested file path. "
                "Return ONLY the raw file content. Do not include markdown code block wrappers (like ```) or conversational explanation."
            )

            sibling_paths = [p for p in all_paths if p != filepath]
            sibling_section = f"=== Other Files In This Batch ===\n{', '.join(sibling_paths)}\n\n" if sibling_paths else ""

            # Stable, large blocks first (existing code context, then architecture design) so
            # same-model retries can reuse the inference server's KV-cache prefix; the task
            # description grows with each retry's error context, so it - along with the
            # per-file sibling list and instruction, which already vary per call - goes last.
            file_prompt = (
                f"=== Existing Code Base Context ===\n{existing_code_context}\n\n"
                f"=== Architecture Design ===\n{design_context}\n\n"
                f"=== Task ===\n{task_description}\n\n"
                f"{sibling_section}"
                f"Please generate the complete, correct, and production-grade file content for: '{filepath}'"
            )

            content = await self.llm.complete(
                file_sys_prompt,
                file_prompt,
                stream_callback=stream_callback,
                json_mode=False,
                model_override=model_override,
                base_url_override=base_url_override,
                api_key_override=api_key_override
            )

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
        api_key_override: Optional[str] = None
    ) -> List[Dict[str, str]]:
        """Generates code files based on planner task and architect design. Prefers
        per-file generation for reliability (filling in only what's missing), falling
        back to single-stage generation only if a usable file list can't be determined
        at all."""

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

            parsed = json.loads(self._strip_markdown_fences(response_str))
            file_entries = self._normalize_file_entries(parsed)

            if file_entries:
                return await self._fill_missing_content(
                    file_entries, task_description, design_context, existing_code_context,
                    stream_callback, model_override, base_url_override, api_key_override
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
        
        cleaned = response_str.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
            
        try:
            res = json.loads(cleaned)
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
        except json.JSONDecodeError as e:
            logger.error(f"Developer Agent returned invalid JSON: {response_str}")
            start_d = cleaned.find("{")
            end_d = cleaned.rfind("}")
            start_a = cleaned.find("[")
            end_a = cleaned.rfind("]")
            
            if start_a != -1 and end_a != -1 and (start_d == -1 or start_a < start_d):
                try:
                    res = json.loads(cleaned[start_a:end_a+1])
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
                except Exception:
                    pass
            
            if start_d != -1 and end_d != -1:
                try:
                    res = json.loads(cleaned[start_d:end_d+1])
                    if isinstance(res, dict):
                        for _key, val in res.items():
                            if isinstance(val, list) and len(val) > 0 and all(isinstance(x, dict) and ("filepath" in x or "path" in x) for x in val):
                                for item in val:
                                    if "path" in item and "filepath" not in item:
                                        item["filepath"] = item["path"]
                                return val
                        return [res]
                    return [res]
                except Exception:
                    pass
                    
            raise ValueError(f"Failed to parse Developer Agent response as JSON: {e}. Raw response: {response_str}") from e


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
            '  "run_command": ["executable", "arg1", "arg2"] or null,\n'
            '  "command_source": "goal_explicit" or "inferred",\n'
            '  "success_criteria": "one or two sentences describing what observable output would prove success"\n'
            "}\n"
            "If the goal explicitly states how to run the app (e.g. names a specific command "
            "like \"mvn exec:exec\" or \"run with python app.py\"), extract that exact command "
            "and set command_source to \"goal_explicit\". Otherwise, if you can reasonably infer "
            "a run command from the generated files (e.g. a pom.xml with an exec-maven-plugin "
            "block implies [\"mvn\", \"exec:exec\"]; a Python file with a __main__ guard implies "
            "[\"python\", \"that_file.py\"]), set command_source to \"inferred\". If there is no "
            "runnable, self-terminating entrypoint at all (a library, a config file, a long-running "
            "service, or the goal doesn't describe observable behavior), set should_run to false, "
            "run_command to null, and success_criteria to an empty string."
        )

    async def judge(
        self,
        goal: str,
        design: str,
        files_written: List[str],
        model_override: Optional[str] = None,
        base_url_override: Optional[str] = None,
        api_key_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        prompt = (
            f"=== Architecture Design ===\n{design}\n\n"
            f"=== Files Generated (already compiled and passed any existing tests) ===\n"
            f"{chr(10).join(files_written)}\n\n"
            f"=== Goal ===\n{goal}\n\n"
            "Decide whether this goal warrants runtime verification, per the rules above."
        )
        response_str = await self.llm.complete(
            self.system_prompt,
            prompt,
            json_mode=True,
            model_override=model_override,
            base_url_override=base_url_override,
            api_key_override=api_key_override,
        )
        try:
            parsed = json.loads(DeveloperAgent._strip_markdown_fences(response_str))
        except Exception as e:
            logger.warning(f"Run Verifier judge() returned unparseable JSON, skipping run verification: {e}")
            return {"should_run": False, "run_command": None, "command_source": "inferred", "success_criteria": ""}

        if not isinstance(parsed, dict):
            return {"should_run": False, "run_command": None, "command_source": "inferred", "success_criteria": ""}

        run_command = parsed.get("run_command")
        if not isinstance(run_command, list) or not all(isinstance(x, str) for x in run_command):
            run_command = None

        return {
            "should_run": bool(parsed.get("should_run")) and run_command is not None,
            "run_command": run_command,
            "command_source": parsed.get("command_source") if parsed.get("command_source") in ("goal_explicit", "inferred") else "inferred",
            "success_criteria": parsed.get("success_criteria") or "",
        }

    async def grade(
        self,
        goal: str,
        success_criteria: str,
        output: str,
        returncode: Optional[int],
        model_override: Optional[str] = None,
        base_url_override: Optional[str] = None,
        api_key_override: Optional[str] = None,
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
        response_str = await self.llm.complete(
            grader_system_prompt,
            prompt,
            json_mode=True,
            model_override=model_override,
            base_url_override=base_url_override,
            api_key_override=api_key_override,
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
        model_override: Optional[str] = None,
        base_url_override: Optional[str] = None,
        api_key_override: Optional[str] = None,
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
        response_str = await self.llm.complete(
            self.system_prompt,
            prompt,
            json_mode=True,
            model_override=model_override,
            base_url_override=base_url_override,
            api_key_override=api_key_override,
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
            "3. Run Instructions: At the end of your review report, always include a section '## How to Run the Application' detailing exactly how to compile, start, and verify the generated application (e.g. specifying 'mvn clean compile', 'python main.py', etc.)."
        )
