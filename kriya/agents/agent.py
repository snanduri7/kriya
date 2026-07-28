import json
import logging
from abc import ABC
from typing import Dict, Any, List, Optional, Callable

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
            "Outline your interface design clearly in Markdown."
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
        """Generates code files based on planner task and architect design."""
        prompt = (
            f"=== User Request & Task ===\n{task_description}\n\n"
            f"=== Architect Design Guidelines ===\n{design_context}\n\n"
            f"=== Existing Code Base Context ===\n{existing_code_context}\n\n"
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
        
        # Clean any accidental markdown codeblock wrappers (e.g. ```json ... ```)
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
                # Check if this dict wraps a list of files (e.g. {"files": [...]})
                for key, val in res.items():
                    if isinstance(val, list) and len(val) > 0 and all(isinstance(x, dict) and ("filepath" in x or "path" in x) for x in val):
                        # Standardize "path" to "filepath"
                        for item in val:
                            if "path" in item and "filepath" not in item:
                                item["filepath"] = item["path"]
                        return val
                return [res]
            if isinstance(res, list):
                # Ensure all elements in the list are dicts, skip strings
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
                        for key, val in res.items():
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
                        for key, val in res.items():
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


class ReviewerAgent(BaseAgent):
    @property
    def system_prompt(self) -> str:
        return (
            "You are the Kriya Reviewer Agent.\n"
            "Your task is to review proposed code changes for bugs, style consistency, testing coverage, and architectural alignment.\n"
            "State whether the code is approved or rejected, and list details of any issues.\n"
            "Please adhere to these guidelines:\n"
            "1. Be pragmatic: If the user goal does not explicitly request unit tests, test files, or documentation (like a README), do not reject the submission solely for their absence. Instead, list them as optional recommendations.\n"
            "2. Avoid hallucinations: When checking long configuration files (like pom.xml or build files), double-check your analysis. Do not claim parameters, arguments, or dependencies are missing unless you are absolutely certain they are absent from the generated content."
        )
