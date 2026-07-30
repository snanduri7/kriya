import logging
import os
import re
import subprocess
from typing import Dict, List, Optional, Tuple

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

def git_commit_if_tracked(path: str, message: str) -> None:
    """Best-effort commit of a skill-file change, scoped to `path`, if it lives inside a
    git work tree. Gives skill content a structured undo/audit trail (git log/revert)
    instead of relying on the user to notice and manually fix a bad extraction or
    promotion. Never raises - this is a nice-to-have, not a correctness requirement,
    and most skill directories won't be their own git repo."""
    try:
        directory = path if os.path.isdir(path) else os.path.dirname(path)
        check = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=directory, capture_output=True, text=True
        )
        if check.returncode != 0 or check.stdout.strip() != "true":
            return
        subprocess.run(["git", "add", path], cwd=directory, capture_output=True)
        res = subprocess.run(
            ["git", "commit", "-m", message], cwd=directory, capture_output=True, text=True
        )
        if res.returncode == 0:
            logger.info(f"Committed skill change: {message}")
        else:
            # Commonly just "nothing to commit" (e.g. value unchanged) - not an error.
            logger.debug(f"git commit for skill change did not create a commit: {res.stdout.strip()} {res.stderr.strip()}")
    except Exception as e:
        logger.debug(f"Failed to git-commit skill change at '{path}' (non-fatal): {e}")


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
    verified: bool = Field(default=False, description="True once a Runtime Verification Gate run using this skill has passed, or a human has explicitly promoted a rule into it.")
    verification_gap_acknowledged: bool = Field(default=False, description="True once the user has been asked to strengthen this unverified skill and declined - suppresses re-asking until it actually becomes verified.")
    source_path: Optional[str] = Field(default=None, description="Filesystem path to this skill's folder, set by discover_and_load - not part of skill.yaml itself.")


class SkillEngine:
    """Discovers, validates, and manages engineering skills."""

    def __init__(self, skills_dir: str, load_global: bool = True) -> None:
        self.skills_dirs = []
        
        # 1. Determine Kriya Installation Directory
        KRIYA_INSTALL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        global_skills = os.path.abspath(os.path.join(KRIYA_INSTALL_DIR, "skills"))
        
        # 2. Add global skills first (lowest precedence) if load_global is True
        if load_global and os.path.exists(global_skills):
            self.skills_dirs.append(global_skills)
            
        # 3. Add local/supplied skills directory
        supplied_dir = os.path.abspath(skills_dir)
        if supplied_dir not in self.skills_dirs:
            self.skills_dirs.append(supplied_dir)
            
        # 4. Add CWD-based skills directory if present and different (only if load_global is True)
        local_cwd_skills = os.path.abspath(os.path.join(os.getcwd(), "skills"))
        if load_global and os.path.exists(local_cwd_skills) and local_cwd_skills not in self.skills_dirs:
            self.skills_dirs.append(local_cwd_skills)
            
        self.skills_dir = supplied_dir
        self._skills: Dict[str, Skill] = {}

    def discover_and_load(self) -> None:
        """Walks the skills directories to discover and parse skills (later paths override earlier ones)."""
        self._skills = {}
        for directory in self.skills_dirs:
            if not os.path.exists(directory):
                continue
            
            for folder in os.listdir(directory):
                folder_path = os.path.join(directory, folder)
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
                        supported_versions=meta.get("supported_versions", "*"),
                        verified=bool(meta.get("verified", False)),
                        verification_gap_acknowledged=bool(meta.get("verification_gap_acknowledged", False)),
                        source_path=folder_path
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
        skill = self._resolve_skill(name)
        if skill is None:
            raise KeyError(f"Skill '{name}' not found.")
        return skill

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
        git_commit_if_tracked(skill_path, f"Kriya: create skill skeleton '{name}'")
        return skill_path

    def _resolve_skill(self, skill_name: str) -> Optional[Skill]:
        safe_lookup = re.sub(r'[^a-z0-9-_]+', '-', skill_name.lower()).strip('-')
        if safe_lookup in self._skills:
            return self._skills[safe_lookup]
        for skill in self._skills.values():
            if skill.name.lower() == skill_name.lower():
                return skill
        return None

    def _set_skill_yaml_field(self, skill_name: str, field: str, value: bool) -> bool:
        """Writes a boolean field back to a skill's skill.yaml and keeps the in-memory
        Skill object consistent. Returns False (logged, non-fatal) if the skill isn't
        known or has no resolvable source_path rather than raising - callers treat this
        as best-effort bookkeeping, not something that should block generation."""
        skill = self._resolve_skill(skill_name)
        if not skill or not skill.source_path:
            logger.warning(f"Cannot update skill '{skill_name}': not found or has no known source path.")
            return False

        yaml_path = os.path.join(skill.source_path, "skill.yaml")
        try:
            meta = {}
            if os.path.exists(yaml_path):
                with open(yaml_path, "r", encoding="utf-8") as f:
                    meta = yaml.safe_load(f) or {}
            meta[field] = value
            with open(yaml_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(meta, f, default_flow_style=False)
            setattr(skill, field, value)
            git_commit_if_tracked(yaml_path, f"Kriya: set {field}={value} on skill '{skill.name}'")
            return True
        except Exception as e:
            logger.warning(f"Failed to update '{field}' on skill '{skill_name}': {e}")
            return False

    def mark_verified(self, skill_name: str) -> bool:
        """Marks a skill verified - called after a Runtime Verification Gate run that
        used this skill passes, or when a human explicitly promotes a rule into it via
        `kriya skills promote`. Also clears any prior gap-acknowledgment, since a newly
        verified skill has nothing left to ask about."""
        ok = self._set_skill_yaml_field(skill_name, "verified", True)
        if ok:
            self._set_skill_yaml_field(skill_name, "verification_gap_acknowledged", False)
        return ok

    def mark_gap_acknowledged(self, skill_name: str) -> bool:
        """Marks that the user was asked to strengthen this unverified skill and
        declined, so future generation runs don't keep re-asking about the same skill."""
        return self._set_skill_yaml_field(skill_name, "verification_gap_acknowledged", True)
