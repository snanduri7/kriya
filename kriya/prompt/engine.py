import logging
import os
from typing import Any, Dict, Optional

from jinja2 import Environment, meta

logger = logging.getLogger(__name__)

DEFAULT_TEMPLATES = {
    "system_instructions": (
        "You are Kriya, a production-grade AI Engineering Platform. "
        "Your task is to help the user build, understand, modify, and review software systems. "
        "Provide clear, modular, maintainable, and enterprise-grade code. "
        "Do not output simple placeholder code or toy examples."
    ),
    "code_review": (
        "Please review the following code changes:\n\n"
        "=== Files Checked ===\n"
        "{{ files_list }}\n\n"
        "=== Diff Content ===\n"
        "{{ diff_content }}\n\n"
        "Identify potential bugs, architectural misalignment, security issues, performance bottlenecks, and style inconsistencies."
    ),
    "refactor": (
        "Refactor the following code in path '{{ filepath }}':\n\n"
        "=== Code ===\n"
        "{{ code_content }}\n\n"
        "=== Refactoring Guidelines ===\n"
        "{{ guidelines }}\n"
    ),
    "generate_code": (
        "Generate a complete, production-ready, modular, and fully tested Python component based on these requirements:\n\n"
        "=== Requirements ===\n"
        "{{ requirements }}\n\n"
        "=== Target Architecture ===\n"
        "{{ architecture }}\n"
    )
}

class PromptEngineError(Exception):
    """Exception raised for errors in PromptEngine."""
    pass

class PromptEngine:
    """Manages loading, rendering, validating, and tracing of prompt templates."""

    def __init__(self, template_dir: Optional[str] = None) -> None:
        self.template_dir = template_dir
        self.env = Environment()

    def get_template_source(self, name: str) -> str:
        """Retrieve the source string of a template by name, checking custom directory first, then defaults."""
        if self.template_dir:
            file_path = os.path.join(self.template_dir, f"{name}.jinja")
            if os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        return f.read()
                except Exception as e:
                    raise PromptEngineError(f"Failed to read custom template '{name}' from {file_path}: {e}") from e
                    
        if name in DEFAULT_TEMPLATES:
            return DEFAULT_TEMPLATES[name]
            
        raise PromptEngineError(f"Template '{name}' not found.")

    def get_template_variables(self, name: str) -> set:
        """Find all variables required by the given template."""
        source = self.get_template_source(name)
        ast = self.env.parse(source)
        return meta.find_undeclared_variables(ast)

    def render(self, name: str, variables: Dict[str, Any]) -> str:
        """Render a template with variables, validating that all required variables are provided."""
        source = self.get_template_source(name)
        required_vars = self.get_template_variables(name)
        
        missing_vars = [var for var in required_vars if var not in variables]
        if missing_vars:
            raise PromptEngineError(f"Missing required variables for template '{name}': {', '.join(missing_vars)}")
            
        try:
            template = self.env.from_string(source)
            rendered = template.render(**variables)
            logger.debug(f"Rendered template '{name}' successfully.")
            return rendered
        except Exception as e:
            raise PromptEngineError(f"Failed to render template '{name}': {e}") from e
