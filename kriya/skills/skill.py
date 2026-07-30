import json
import logging
import os
import re
import subprocess
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

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


def _conflict_registry_path(skills_dir: str) -> str:
    return os.path.join(os.path.abspath(skills_dir), ".skill_conflicts.json")


def _same_conflict_pair(record: Dict[str, str], skill_a: str, rule_a: str, skill_b: str, rule_b: str) -> bool:
    """Order-independent match: a resolution recorded as (A, ruleA, B, ruleB) also
    covers a later lookup for (B, ruleB, A, ruleA)."""
    stored = (record.get("skill_a"), record.get("rule_a"), record.get("skill_b"), record.get("rule_b"))
    return stored == (skill_a, rule_a, skill_b, rule_b) or stored == (skill_b, rule_b, skill_a, rule_a)


def load_conflict_resolutions(skills_dir: str) -> List[Dict[str, str]]:
    """Loads all remembered skill-pair conflict resolutions. Never raises - a missing
    or corrupt registry is treated as "nothing resolved yet", not a hard error."""
    path = _conflict_registry_path(skills_dir)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"Failed to read skill conflict registry at '{path}' (treating as empty): {e}")
        return []


def find_conflict_resolution(skills_dir: str, skill_a: str, rule_a: str, skill_b: str, rule_b: str) -> Optional[str]:
    """Returns a previously-recorded resolution ('prefer_a'/'prefer_b'/'both_ok') for
    this exact skill-and-rule pair, oriented to match the caller's (skill_a, skill_b)
    order, or None if this specific pair has never been resolved before."""
    for record in load_conflict_resolutions(skills_dir):
        if _same_conflict_pair(record, skill_a, rule_a, skill_b, rule_b):
            resolution = record.get("resolution")
            if record.get("skill_a") == skill_a and record.get("rule_a") == rule_a:
                return resolution
            return {"prefer_a": "prefer_b", "prefer_b": "prefer_a"}.get(resolution, resolution)
    return None


def record_conflict_resolution(
    skills_dir: str, skill_a: str, rule_a: str, skill_b: str, rule_b: str, resolution: str, note: str = ""
) -> None:
    """Persists a human's resolution of a specific skill-pair rule conflict so future
    runs that co-activate the same two skills with the same rule text don't re-ask.
    Best-effort, like every other skill-file write - failing to persist shouldn't fail
    the generation run that triggered it."""
    path = _conflict_registry_path(skills_dir)
    records = [r for r in load_conflict_resolutions(skills_dir) if not _same_conflict_pair(r, skill_a, rule_a, skill_b, rule_b)]
    records.append({
        "skill_a": skill_a,
        "rule_a": rule_a,
        "skill_b": skill_b,
        "rule_b": rule_b,
        "resolution": resolution,
        "note": note,
        "resolved_at": date.today().isoformat(),
    })
    try:
        os.makedirs(os.path.abspath(skills_dir), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2)
        git_commit_if_tracked(path, f"Kriya: record skill-conflict resolution ({skill_a} vs {skill_b}): {resolution}")
    except Exception as e:
        logger.warning(f"Failed to persist skill conflict resolution (non-fatal): {e}")


def _rule_provenance_path(skill_source_path: str) -> str:
    return os.path.join(skill_source_path, "rule_provenance.json")


def load_rule_provenance(skill_source_path: str) -> List[Dict[str, Any]]:
    """Loads a skill's per-rule provenance records ({text, verified, source,
    added_at}) - a parallel tracking file, not a rules.txt format change, so every
    existing skill (including ones written long before this tracking existed) keeps
    working completely unmodified. A rule with no record here is pre-existing/
    untracked content and is treated as already-trusted, not retroactively flagged
    unverified - only rules extracted since this tracking was added ever get a
    record."""
    path = _rule_provenance_path(skill_source_path)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"Failed to read rule provenance at '{path}' (treating as empty): {e}")
        return []


def record_rule_provenance(skill_source_path: str, rule_text: str, source: str) -> None:
    """Records a freshly-extracted rule as unverified with where it came from, so
    generation prompts can flag it distinctly (kriya/workflow/workflow.py) until a
    passing Runtime Verification run proves it. Best-effort, like every other
    skill-file write in this subsystem - failing to persist shouldn't fail the
    generation run that triggered it."""
    path = _rule_provenance_path(skill_source_path)
    records = [r for r in load_rule_provenance(skill_source_path) if r.get("text") != rule_text]
    records.append({
        "text": rule_text,
        "verified": False,
        "source": source,
        "added_at": date.today().isoformat(),
    })
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2)
        git_commit_if_tracked(path, f"Kriya: record provenance for new rule in skill '{os.path.basename(skill_source_path)}'")
    except Exception as e:
        logger.warning(f"Failed to persist rule provenance (non-fatal): {e}")


def mark_rules_verified(skill_source_path: str, rule_texts: List[str]) -> None:
    """Flips verified=True for exactly the given rule texts that already have a
    provenance record - never creates a new record, since a rule with no record is
    pre-existing/untracked content that was never flagged unverified in the first
    place. Called after a passing Runtime Verification run, scoped to only the rules
    that were part of the skill when that run's context was actually built (a
    snapshot taken before the retry loop starts), not whatever the skill's rules.txt
    happens to contain by the time verification finishes."""
    records = load_rule_provenance(skill_source_path)
    if not records:
        return
    texts = set(rule_texts)
    changed = False
    for r in records:
        if r.get("text") in texts and not r.get("verified", False):
            r["verified"] = True
            r["verified_at"] = date.today().isoformat()
            changed = True
    if not changed:
        return
    path = _rule_provenance_path(skill_source_path)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2)
        git_commit_if_tracked(path, f"Kriya: mark rule(s) verified in skill '{os.path.basename(skill_source_path)}'")
    except Exception as e:
        logger.warning(f"Failed to persist rule verification (non-fatal): {e}")


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
    verified_at: Optional[str] = Field(default=None, description="Date (YYYY-MM-DD) the skill was last marked verified - advisory provenance for a human to judge staleness, not used to automatically re-trigger anything.")
    verified_context: Optional[str] = Field(default=None, description="Best-effort description of what was actually verified (e.g. a version mentioned in the goal, or 'promoted from X') - advisory provenance only, same as verified_at.")
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
                        verified_at=meta.get("verified_at"),
                        verified_context=meta.get("verified_context"),
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

    def _set_skill_yaml_fields(self, skill_name: str, fields: Dict[str, Any], commit_message: str) -> bool:
        """Writes one or more fields back to a skill's skill.yaml in a single read-
        modify-write (and a single git commit, if tracked) and keeps the in-memory
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
            meta.update(fields)
            with open(yaml_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(meta, f, default_flow_style=False)
            for field, value in fields.items():
                setattr(skill, field, value)
            git_commit_if_tracked(yaml_path, commit_message)
            return True
        except Exception as e:
            logger.warning(f"Failed to update skill '{skill_name}': {e}")
            return False

    def _set_skill_yaml_field(self, skill_name: str, field: str, value: Any) -> bool:
        return self._set_skill_yaml_fields(skill_name, {field: value}, f"Kriya: set {field}={value} on skill '{skill_name}'")

    def mark_verified(self, skill_name: str, context: str = "") -> bool:
        """Marks a skill verified - called after a Runtime Verification Gate run that
        used this skill passes, or when a human explicitly promotes a rule into it via
        `kriya skills promote`. Also clears any prior gap-acknowledgment, since a newly
        verified skill has nothing left to ask about, and records `context` (e.g. the
        version actually verified, or "promoted from X") plus today's date as advisory
        provenance - visible via `kriya skills list`/`show` for a human to judge
        staleness themselves later (e.g. a pinned version gets yanked, or a new major
        version changes the config shape) - nothing here automatically re-triggers
        anything; that's a deliberate choice over guessing at staleness automatically."""
        fields = {
            "verified": True,
            "verification_gap_acknowledged": False,
            "verified_at": date.today().isoformat(),
        }
        if context:
            fields["verified_context"] = context
        suffix = f" ({context})" if context else ""
        return self._set_skill_yaml_fields(skill_name, fields, f"Kriya: mark skill '{skill_name}' verified{suffix}")

    def mark_gap_acknowledged(self, skill_name: str) -> bool:
        """Marks that the user was asked to strengthen this unverified skill and
        declined, so future generation runs don't keep re-asking about the same skill."""
        return self._set_skill_yaml_field(skill_name, "verification_gap_acknowledged", True)

    def mark_unverified(self, skill_name: str) -> bool:
        """Explicit human action resetting a skill's verified status (e.g. they know
        it's stale - a version bump, a deprecated approach) - deliberately manual, not
        triggered automatically by a failing Runtime Verification run, since failure
        attribution to one specific skill among several active ones is unreliable and
        auto-demoting risks skills flip-flopping for reasons unrelated to the skill
        itself. Also clears verification_gap_acknowledged so future runs ask about it
        again rather than silently staying unverified forever."""
        return self._set_skill_yaml_fields(
            skill_name,
            {"verified": False, "verification_gap_acknowledged": False},
            f"Kriya: reset skill '{skill_name}' to unverified"
        )
