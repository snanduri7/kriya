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
        stream_callback: Optional[Callable[[str], None]] = None
    ) -> List[Dict[str, str]]:
        """Generates code files based on planner task and architect design."""
        prompt = (
            f"=== User Request & Task ===\n{task_description}\n\n"
            f"=== Architect Design Guidelines ===\n{design_context}\n\n"
            f"=== Existing Code Base Context ===\n{existing_code_context}\n\n"
            "Please generate the complete, production-grade files. Return ONLY the JSON list of files."
        )
        
        response_str = await self.run(prompt, stream_callback=stream_callback)
        
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
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.error(f"Developer Agent returned invalid JSON: {response_str}")
            # Attempt to find JSON array brackets as fallback
            start = cleaned.find("[")
            end = cleaned.rfind("]")
            if start != -1 and end != -1:
                try:
                    return json.loads(cleaned[start:end+1])
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
