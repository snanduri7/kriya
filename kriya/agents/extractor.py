import json
import logging
from typing import Dict, Any, List
from kriya.agents.agent import BaseAgent

logger = logging.getLogger(__name__)

class ConventionsExtractorAgent(BaseAgent):
    """Specialized agent to analyze codebase files and extract structural style conventions and rules."""

    @property
    def system_prompt(self) -> str:
        return (
            "You are the Kriya Conventions Extractor Agent.\n"
            "Your task is to analyze a repository structure and sample source files, and extract coding standards, architecture rules, and patterns.\n"
            "Outline your findings strictly in Markdown instructions and a raw list of short, concrete coding rules.\n"
            "You must output EXACTLY a JSON object formatted as follows. Return ONLY the raw JSON block, no extra markdown wrappers (no ```json code blocks):\n"
            "{\n"
            "  \"description\": \"observed patterns and frameworks description\",\n"
            "  \"instructions\": \"detailed markdown guide covering naming conventions, packaging, imports, and component layouts\",\n"
            "  \"rules\": [\n"
            "    \"Rule 1: e.g. Use spaces for indentation\",\n"
            "    \"Rule 2: e.g. All service classes must have Service suffix\"\n"
            "  ]\n"
            "}"
        )

    async def extract_conventions(self, repo_structure: str, sample_contents: str, stream_callback = None) -> Dict[str, Any]:
        """Extract conventions and rules from structure and code snippets."""
        prompt = (
            f"=== Repository Structure ===\n{repo_structure}\n\n"
            f"=== Sample File Snippets ===\n{sample_contents}\n\n"
            "Please analyze the structure and files, and return the Kriya Skill rules and instructions JSON."
        )
        
        response_str = await self.run(prompt, stream_callback=stream_callback)
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
            logger.error(f"ConventionsExtractorAgent returned invalid JSON: {response_str}")
            # Attempt fallback parsing by finding JSON bounds
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start != -1 and end != -1:
                try:
                    return json.loads(cleaned[start:end+1])
                except Exception:
                    pass
            # Graceful default values
            return {
                "description": "Auto-extracted conventions.",
                "instructions": "# Auto-Generated conventions\nStandard repository layouts apply.\n",
                "rules": ["Follow project coding styles and conventions."]
            }
