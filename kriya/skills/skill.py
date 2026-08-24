import hashlib
import json
import logging
import os
import re
import subprocess
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import yaml
from pydantic import BaseModel, Field

from kriya.analyzer.analyzer import RepositoryModel

logger = logging.getLogger(__name__)

def get_global_skills_dir() -> str:
    """Kriya's own shared, global skill library directory (the install's own
    skills/ folder) - not any project-local skills override."""
    kriya_install_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    return os.path.join(kriya_install_dir, "skills")

def is_accidental_shared_skills_write(skills_dir: str, workspace_path: str) -> bool:
    """True when a write to skills_dir is about to land in Kriya's own shared/global
    install directory for a workspace that ISN'T the Kriya install itself - the
    signature of a project's own kriya.yaml not overriding paths.skills, silently
    falling back to the packaged default (which resolves relative paths against the
    Kriya install directory, not the project's own directory - see
    config.py::load_config's default-config resolution). Confirmed live, repeatedly
    (the same incident independently hit at least 4 times across this project's
    history, including once during this very validation pass, by someone who had
    the exact warning in memory) - unrelated scratch/test project content silently
    written into the shared skill library every other real project inherits from.
    Deliberately conservative: only flags the EXACT shared install path, and only
    when workspace_path is clearly a different directory (not Kriya's own repo,
    where writing to its own skills/ is completely normal)."""
    try:
        resolved_skills_dir = os.path.abspath(skills_dir)
        global_skills_dir = get_global_skills_dir()
        kriya_install_dir = os.path.dirname(global_skills_dir)
        resolved_workspace = os.path.abspath(workspace_path)
        return (
            resolved_skills_dir == global_skills_dir
            and resolved_workspace != kriya_install_dir
            and not resolved_workspace.startswith(kriya_install_dir + os.sep)
        )
    except Exception:
        return False

def is_accidental_shared_skill_write(skill_source_path: str, workspace_path: str) -> bool:
    """MA7.4 - the actual root-cause predicate behind [[kriya_backlog_and_lessons]]'s
    durable lesson #4 (recurred 4 times: 67e34b6, a2eb184, fc1c603, and this
    session's b8a56ab, all independently). is_accidental_shared_skills_write()
    above catches a DIFFERENT, narrower case (a project's OWN new-skill/
    auto-bootstrap writes landing in the shared dir because paths.skills was
    never overridden) - every real incident was something else: a project
    with perfectly correctly configured paths.skills still loaded a GLOBAL
    skill (skills.load_global/load_cwd not set to false) that happened to
    match an unrelated goal, and SkillEngine.mark_verified() wrote real
    verification data back to that skill's OWN source_path - which, for a
    genuinely global skill, correctly IS the shared install directory. The
    bug was never "where does this project write its own skills," it's
    "should THIS run be allowed to overwrite a skill's real, shared,
    cross-project verification record at all." True whenever skill_source_path
    resolves inside Kriya's own shared install skills/ directory (not just
    equal to it - a skill's source_path is a SUBdirectory, e.g. skills/auto-core)
    and workspace_path isn't the Kriya install itself - the one workspace
    where writing to its own skills/ is completely normal."""
    try:
        resolved_source = os.path.abspath(skill_source_path)
        global_skills_dir = get_global_skills_dir()
        kriya_install_dir = os.path.dirname(global_skills_dir)
        resolved_workspace = os.path.abspath(workspace_path)
        inside_global = resolved_source == global_skills_dir or resolved_source.startswith(global_skills_dir + os.sep)
        return (
            inside_global
            and resolved_workspace != kriya_install_dir
            and not resolved_workspace.startswith(kriya_install_dir + os.sep)
        )
    except Exception:
        return False


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
    """Helper to parse a version string into (major, minor, patch) integer tuple.

    Each dot-separated part's own LEADING digit run is used, ignoring
    whatever comes after it - a pre-release/build qualifier attached
    directly to a numeric part (e.g. the "3-SNAPSHOT" in "1.2.3-SNAPSHOT",
    or "0-rc1" in "1.0.0-rc1") must not zero out that whole component.
    Previously a whole-string trailing-non-digit strip only worked when the
    qualifier was on the LAST part with nothing but non-digits after it -
    "1.2.3-SNAPSHOT" ends in a letter, so that strip left "3-SNAPSHOT"
    attached, int() raised, and the entire patch number silently coerced to
    0 (2026-08-12 SME review) - not just a precision loss, a genuinely wrong
    major/minor/patch component."""
    parts = []
    cleaned = re.sub(r'^[^\d]+', '', v_str)
    for part in cleaned.split('.'):
        match = re.match(r'^(\d+)', part)
        parts.append(int(match.group(1)) if match else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])

def is_version_supported(ver_str: str, range_str: str) -> bool:
    """
    Checks if ver_str satisfies the range_str.
    Supports operators: >=, <=, >, <, ==, !=, *
    Example ranges: ">=2.15.0 <3.0.0", ">=2.15.0,<3.0.0", "==2.18.0"
    """
    if not range_str or range_str.strip() == "*":
        return True

    version = parse_version_parts(ver_str)
    # Split on whitespace OR commas - a comma-separated range with no space
    # (">=2.15.0,<3.0.0", a natural way to write it) used to become ONE
    # token; re.match below only ever consumes the FIRST bound from that
    # token and silently ignores everything after the comma via re.match's
    # partial-match semantics, so the upper bound never applied at all
    # (2026-08-12 SME review).
    conditions = [c for c in re.split(r"[\s,]+", range_str.strip()) if c]
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


def fact_match(skill: "Skill", repo_model: RepositoryModel) -> bool:
    """Does any of this skill's tags substring-match a dependency or framework the
    repo analyzer actually found in the target repo? Extracted from the inline check
    that used to live only in kriya/workflow/workflow.py's skill-activation loop, so
    other consumers (e.g. the repo-manifest knowledge channel) share one implementation
    instead of a second copy that could silently drift from it."""
    for tag in skill.tags:
        tag_lower = tag.lower()
        if any(tag_lower in dep.lower() for dep in repo_model.dependencies):
            return True
        if any(tag_lower in f.lower() for f in repo_model.frameworks):
            return True
    return False

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

    def __init__(
        self, skills_dir: str, load_global: bool = True,
        load_cwd: Optional[bool] = None, workspace_path: Optional[str] = None,
    ) -> None:
        # MA7.4 - "no flag required for safety": defaults to os.getcwd() when
        # a caller doesn't supply one, rather than skipping the write-safety
        # check entirely - every documented incident of durable lesson #4
        # ([[kriya_backlog_and_lessons]]) happened precisely because SOME
        # caller (a live-validation run, a standalone test script, an ad hoc
        # sweep) never threaded real workspace context through, and "the
        # check silently doesn't run without an explicit opt-in" would just
        # reproduce that exact failure mode one level down. See
        # is_accidental_shared_skill_write's own docstring for what this
        # actually protects against.
        #
        # One deliberate exception: if the CALLER explicitly points
        # `skills_dir` itself AT (or inside) the shared install directory -
        # `kriya skills promote`/`approve`'s own real construction,
        # SkillEngine(global_skills_dir, ...) - that's a strong, explicit
        # signal of intent to author a global skill, not an accidental
        # cross-project write, regardless of the human's actual cwd when
        # they ran the command. Treat that case as if workspace_path IS the
        # Kriya install, so is_accidental_shared_skill_write correctly
        # recognizes it as intentional rather than hard-blocking a
        # legitimate, already-confirmed CLI action.
        if workspace_path:
            self.workspace_path = os.path.abspath(workspace_path)
        else:
            _global_skills_dir = get_global_skills_dir()
            _kriya_install_dir = os.path.dirname(_global_skills_dir)
            _supplied = os.path.abspath(skills_dir)
            if _supplied == _global_skills_dir or _supplied.startswith(_global_skills_dir + os.sep):
                self.workspace_path = _kriya_install_dir
            else:
                self.workspace_path = os.getcwd()
        self.skills_dirs = []
        # Backward compatibility: the historic load_global=False flag disabled
        # both implicit sources. New config callers pass load_cwd explicitly.
        if load_cwd is None:
            load_cwd = load_global

        # 1. Determine Kriya Installation Directory
        KRIYA_INSTALL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        global_skills = os.path.abspath(os.path.join(KRIYA_INSTALL_DIR, "skills"))

        # 2. Add global skills first (lowest precedence) if load_global is True
        if load_global and os.path.exists(global_skills):
            self.skills_dirs.append(global_skills)

        # 3. Add the CWD-based skills directory (an IMPLICIT guess, used when
        # a project's config doesn't set paths.skills explicitly) before the
        # explicitly supplied directory, not after - found live, 2026-08-12
        # (SME architecture review): discover_and_load() below documents
        # "later paths override earlier ones" as deliberate precedence, but
        # this method previously added supplied_dir BEFORE local_cwd_skills,
        # so a same-named skill present in both silently let the implicit
        # CWD guess win over an explicitly configured paths.skills - the
        # opposite of expected precedence whenever supplied_dir differs from
        # CWD/skills (e.g. an absolute path elsewhere).
        local_cwd_skills = os.path.abspath(os.path.join(os.getcwd(), "skills"))
        if load_cwd and os.path.exists(local_cwd_skills) and local_cwd_skills not in self.skills_dirs:
            self.skills_dirs.append(local_cwd_skills)

        # 4. Add the explicitly supplied skills directory LAST (highest
        # precedence) - an explicit configuration should always win over an
        # implicit guess.
        supplied_dir = os.path.abspath(skills_dir)
        if supplied_dir not in self.skills_dirs:
            self.skills_dirs.append(supplied_dir)

        self.skills_dir = supplied_dir
        self._skills: Dict[str, Skill] = {}

    @classmethod
    def from_config(cls, config: Any, workspace_path: Optional[str] = None) -> "SkillEngine":
        return cls(
            config.paths.skills,
            load_global=config.skills.load_global,
            load_cwd=config.skills.load_cwd,
            workspace_path=workspace_path,
        )

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

    def manifest_for(self, names: List[str]) -> List[Dict[str, Any]]:
        """Return a stable local provenance manifest for active prompt inputs."""
        manifest = []
        for name in sorted(set(names), key=str.lower):
            skill = self.get_skill(name)
            canonical = json.dumps({
                "name": skill.name,
                "description": skill.description,
                "tags": skill.tags,
                "instructions": skill.instructions,
                "rules": skill.rules,
                "examples": skill.examples,
                "supported_versions": skill.supported_versions,
                "verified": skill.verified,
            }, sort_keys=True, separators=(",", ":"))
            manifest.append({
                "name": skill.name,
                "source_path": skill.source_path,
                "content_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                "verified": skill.verified,
                "supported_versions": skill.supported_versions,
            })
        return manifest

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

        # MA7.4 - HARD refusal, not a warning: durable lesson #4's real
        # incidents (see is_accidental_shared_skill_write's own docstring)
        # all happened at exactly this write - a run for one workspace
        # overwriting a DIFFERENT, shared skill file's real verification
        # data. self.workspace_path always has a real value (defaults to
        # os.getcwd() in __init__, never None) - this check cannot be
        # silently skipped by a caller forgetting to pass one.
        if is_accidental_shared_skill_write(skill.source_path, self.workspace_path):
            logger.error(
                f"Refusing to write skill.yaml for '{skill_name}': its source ({skill.source_path}) "
                f"lives inside Kriya's own shared install skills directory, but this run's workspace "
                f"({self.workspace_path}) is a different project. Writing here would overwrite this "
                f"skill's real, shared verification record with data from an unrelated run - see "
                f"durable lesson #4 in project memory. Set skills.load_global/load_cwd: false in this "
                f"workspace's kriya.yaml if it shouldn't be touching global skills at all."
            )
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
