import os
import yaml
import logging
from typing import List, Dict, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

import re
from typing import Tuple

def parse_version_parts(v_str: str) -> Tuple[int, int, int]:
    """Helper to parse a version string into (major, minor, patch) integer tuple."""
    parts = []
    cleaned = re.sub(r'^[^\d]+', '', v_str)
    cleaned = re.sub(r'[^\d]+$', '', cleaned)
    for part in cleaned.split('.'):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])

def is_version_supported(ver_str: str, range_str: str) -> bool:
    """
    Checks if ver_str satisfies the range_str.
    Supports operators: >=, <=, >, <, ==, !=, *
    Example ranges: ">=2.15.0 <3.0.0", "==2.18.0"
    """
    if not range_str or range_str.strip() == "*":
        return True
    
    version = parse_version_parts(ver_str)
    conditions = range_str.strip().split()
    for cond in conditions:
        match = re.match(r"^([>=<!\s]*)([0-9\.]+)", cond.strip())
        if not match:
            continue
        op, cond_ver_str = match.groups()
        op = op.strip() if op else "=="
        cond_ver = parse_version_parts(cond_ver_str)
        
        if op == "==":
            if version != cond_ver:
                return False
        elif op == "!=":
            if version == cond_ver:
                return False
        elif op == ">=":
            if version < cond_ver:
                return False
        elif op == "<=":
            if version > cond_ver:
                return False
        elif op == ">":
            if version <= cond_ver:
                return False
        elif op == "<":
            if version >= cond_ver:
                return False
    return True

class Skill(BaseModel):
    name: str = Field(description="Name of the skill.")
    description: str = Field(description="Short summary of the skill's purpose.")
    category: str = Field(default="General", description="Category classification (e.g. Database, Framework).")
    tags: List[str] = Field(default_factory=list, description="Associated keywords/tags.")
    instructions: str = Field(default="", description="Detailed instructions in markdown format.")
    rules: List[str] = Field(default_factory=list, description="Strict coding rules/standards for this skill.")
    examples: Dict[str, str] = Field(
        default_factory=dict, 
        description="Dictionary mapping example file basenames to their contents."
    )
    supported_versions: str = Field(default="*", description="Supported version range (e.g. >=2.15.0 <3.0.0).")


class SkillEngine:
    """Discovers, validates, and manages engineering skills."""

    def __init__(self, skills_dir: str) -> None:
        self.skills_dir = os.path.abspath(skills_dir)
        self._skills: Dict[str, Skill] = {}

    def discover_and_load(self) -> None:
        """Walks the skills directory to discover and parse skills."""
        if not os.path.exists(self.skills_dir):
            logger.warning(f"Skills directory '{self.skills_dir}' does not exist.")
            return

        for folder in os.listdir(self.skills_dir):
            folder_path = os.path.join(self.skills_dir, folder)
            if not os.path.isdir(folder_path) or folder.startswith(".") or folder.startswith("_"):
                continue

            yaml_path = os.path.join(folder_path, "skill.yaml")
            if not os.path.exists(yaml_path):
                logger.debug(f"Directory '{folder}' is missing 'skill.yaml'. Skipping.")
                continue

            try:
                # Load metadata
                with open(yaml_path, "r", encoding="utf-8") as f:
                    meta = yaml.safe_load(f) or {}

                name = meta.get("name", folder).lower()
                
                # Load instructions
                instructions = ""
                instructions_path = os.path.join(folder_path, "instructions.md")
                if os.path.exists(instructions_path):
                    with open(instructions_path, "r", encoding="utf-8") as f:
                        instructions = f.read()

                # Load rules
                rules = []
                rules_path = os.path.join(folder_path, "rules.txt")
                if os.path.exists(rules_path):
                    with open(rules_path, "r", encoding="utf-8") as f:
                        rules = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]

                # Load examples
                examples = {}
                examples_dir = os.path.join(folder_path, "examples")
                if os.path.exists(examples_dir) and os.path.isdir(examples_dir):
                    for file in os.listdir(examples_dir):
                        file_path = os.path.join(examples_dir, file)
                        if os.path.isfile(file_path) and not file.startswith("."):
                            try:
                                with open(file_path, "r", encoding="utf-8") as f:
                                    examples[file] = f.read()
                            except Exception as ex:
                                logger.warning(f"Could not read example file '{file}' in skill '{name}': {ex}")

                # Build Skill object
                skill = Skill(
                    name=meta.get("name", folder),
                    description=meta.get("description", "No description provided."),
                    category=meta.get("category", "General"),
                    tags=meta.get("tags", []),
                    instructions=instructions,
                    rules=rules,
                    examples=examples,
                    supported_versions=meta.get("supported_versions", "*")
                )

                self._skills[folder.lower()] = skill
                logger.info(f"Loaded skill '{skill.name}' from {folder_path}")

            except Exception as e:
                logger.error(f"Failed to load skill from '{folder}': {e}", exc_info=True)

    def list_skills(self) -> List[Skill]:
        """Returns all discovered skills."""
        return list(self._skills.values())

    def get_skill(self, name: str) -> Skill:
        """Retrieves a skill by name."""
        import re
        safe_lookup = re.sub(r'[^a-z0-9-_]+', '-', name.lower()).strip('-')
        
        # Check folder slug first
        if safe_lookup in self._skills:
            return self._skills[safe_lookup]
            
        # Fallback to direct name matching
        for skill in self._skills.values():
            if skill.name.lower() == name.lower():
                return skill
                
        raise KeyError(f"Skill '{name}' not found.")

    def find_skills_by_tag(self, tag: str) -> List[Skill]:
        """Finds all skills tagged with the specified keyword."""
        tag_lower = tag.lower()
        return [s for s in self._skills.values() if any(t.lower() == tag_lower for t in s.tags)]

    def create_skill_skeleton(self, name: str) -> str:
        """Creates folder structure and default files for a new skill."""
        import re
        safe_name = re.sub(r'[^a-z0-9-_]+', '-', name.lower()).strip('-')
        if not safe_name:
            raise ValueError(f"Invalid skill name '{name}'.")

        skill_path = os.path.join(self.skills_dir, safe_name)
        if os.path.exists(skill_path):
            raise FileExistsError(f"Skill '{safe_name}' already exists at {skill_path}")

        os.makedirs(skill_path, exist_ok=True)
        os.makedirs(os.path.join(skill_path, "examples"), exist_ok=True)

        # Write skill.yaml
        yaml_content = {
            "name": name,
            "description": f"Custom template skill for {name}.",
            "category": "General",
            "tags": [safe_name]
        }
        with open(os.path.join(skill_path, "skill.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(yaml_content, f, default_flow_style=False)

        # Write instructions.md
        instructions_content = f"# instructions for {name}\n\nAdd guidelines here.\n"
        with open(os.path.join(skill_path, "instructions.md"), "w", encoding="utf-8") as f:
            f.write(instructions_content)

        # Write rules.txt
        rules_content = "# Add architectural rules here (one per line)\n"
        with open(os.path.join(skill_path, "rules.txt"), "w", encoding="utf-8") as f:
            f.write(rules_content)

        logger.info(f"Created skill skeleton for '{name}' at {skill_path}")
        return skill_path
