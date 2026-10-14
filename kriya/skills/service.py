"""SkillService: the read-only façade the GUI/KUP reads Skills through (Demo V2).

A thin layer over the authoritative skill implementation (``kriya.skills.skill``): discovery, parsing, the content digest and the provenance
file are all the engine's; nothing is parsed twice here. V1 has two methods, ``list`` and ``describe``, and no mutation: enabling, installing,
removing, approving or unverifying a skill are deliberately absent (see demo-v2/docs/EXTENSION_CONTRACT.md).

A skill's identity is its name (lower-cased), never a filesystem path. A value the backend cannot know is ``None`` and named in
``unknown_fields``; it is never invented. Rule TEXT is not returned: it is retrieved/acquired content, and the cockpit's execution.context
already returns metadata only. What is returned about provenance is where rules came from and whether they were verified.

Discovery reads the process working directory (``skills/`` beside the configuration); a caller in a long-lived process must hold the
directory it wants for the duration of the call (``kriya.kup.catalog_ops`` does, under the same lock ``execution.context`` uses).
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from kriya.platform.filesystem_semantics import PathRelation, path_relation
from kriya.skills.skill import Skill, SkillEngine, get_global_skills_dir, load_rule_provenance

MAX_SKILLS = 500
MAX_SOURCES = 50
MAX_TEXT = 500  # descriptions, sources and contexts are bounded: they are display text from files this process does not own

PROVENANCE_NONE_RECORDED = "NONE_RECORDED"  # no provenance file: rules are pre-existing/untracked content
PROVENANCE_RECORDED = "RECORDED"
PROVENANCE_UNREADABLE_OR_EMPTY = "UNREADABLE_OR_EMPTY"  # a file exists but yields no usable record (the engine logs and treats it as empty)

ORIGIN_KRIYA_INSTALL = "kriya_install"
ORIGIN_WORKSPACE_OR_CONFIGURED = "workspace_or_configured"
SCOPE_WORKSPACE = "workspace"
SCOPE_INSTALL = "install"
SCOPE_CONFIGURED = "configured"
SOURCE_LOCAL_DIRECTORY = "local_directory"


def _clip(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    return value if len(value) <= MAX_TEXT else value[:MAX_TEXT]


@dataclass(frozen=True)
class SkillSourceSummary:
    source: str
    rule_count: int
    verified_count: int
    first_added: Optional[str]
    last_added: Optional[str]


@dataclass(frozen=True)
class ProvenanceSummary:
    state: str
    tracked_rule_count: int
    untracked_rule_count: int
    verified_tracked_count: int
    sources: Tuple[SkillSourceSummary, ...] = ()
    sources_truncated: bool = False


@dataclass(frozen=True)
class SkillDescriptor:
    id: str
    name: str
    description: str
    category: str
    tags: Tuple[str, ...]
    supported_versions: str
    skill_version: Optional[str]  # a skill has no version of its own, only the technology range it supports
    digest: Optional[str]
    source: str
    origin: str
    scope: str
    verified: bool
    verified_at: Optional[str]
    verified_context: Optional[str]
    verification_gap_acknowledged: bool
    rule_count: int
    staged_rule_count: int
    example_count: int
    has_instructions: bool
    provenance: ProvenanceSummary
    unknown_fields: Tuple[str, ...] = field(default=("skill_version",))

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["tags"] = list(self.tags)
        data["unknown_fields"] = list(self.unknown_fields)
        data["provenance"]["sources"] = [asdict(s) for s in self.provenance.sources]
        return data


def staged_rule_count(skill_dir: Optional[str]) -> int:
    """Non-blank lines of the skill's ``staged_rules.txt`` (rules awaiting human approval); 0 when absent or unreadable."""
    if not skill_dir:
        return 0
    try:
        with open(os.path.join(skill_dir, "staged_rules.txt"), "r", encoding="utf-8", errors="replace") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def skill_id(skill: Skill) -> str:
    return skill.name.lower()


def _provenance(skill: Skill) -> ProvenanceSummary:
    source_path = skill.source_path
    if not source_path:
        return ProvenanceSummary(PROVENANCE_NONE_RECORDED, 0, len(skill.rules), 0)
    path_exists = os.path.exists(os.path.join(source_path, "rule_provenance.json"))
    rules = set(skill.rules)
    by_source: Dict[str, List[Dict[str, Any]]] = {}
    seen: set = set()
    for record in load_rule_provenance(source_path):
        # The file is not authoritative: ignore anything that is not a record for a rule this skill currently has.
        if not isinstance(record, dict) or record.get("text") not in rules or record["text"] in seen:
            continue
        seen.add(record["text"])
        label = _clip(record.get("source")) or "unspecified"
        by_source.setdefault(label, []).append(record)
    if not seen:
        state = PROVENANCE_UNREADABLE_OR_EMPTY if path_exists else PROVENANCE_NONE_RECORDED
        return ProvenanceSummary(state, 0, len(rules), 0)
    summaries = []
    for label in sorted(by_source):
        records = by_source[label]
        dates = sorted(d for d in (_clip(r.get("added_at")) for r in records) if d)
        summaries.append(SkillSourceSummary(
            source=label, rule_count=len(records), verified_count=sum(1 for r in records if r.get("verified") is True),
            first_added=dates[0] if dates else None, last_added=dates[-1] if dates else None))
    verified = sum(s.verified_count for s in summaries)
    return ProvenanceSummary(PROVENANCE_RECORDED, len(seen), len(rules) - len(seen), verified,
                             tuple(summaries[:MAX_SOURCES]), len(summaries) > MAX_SOURCES)


def _scope(skill: Skill, workspace: str, install_dir: str) -> str:
    path = skill.source_path
    if not path:
        return SCOPE_CONFIGURED
    if path_relation(install_dir, path) is PathRelation.WITHIN:
        return SCOPE_INSTALL
    if path_relation(workspace, path) is PathRelation.WITHIN:
        return SCOPE_WORKSPACE
    return SCOPE_CONFIGURED


class SkillService:
    """``SkillService(cfg, workspace).list()`` / ``.describe(skill_id)``. Every call discovers afresh: no cache, so what is shown is what a run would load now."""

    def __init__(self, cfg: Any, workspace: str) -> None:
        self._cfg = cfg
        self._workspace = os.path.realpath(workspace)

    def _engine(self) -> SkillEngine:
        engine = SkillEngine.from_config(self._cfg, workspace_path=self._workspace)
        engine.discover_and_load()
        return engine

    def _describe(self, engine: SkillEngine, skill: Skill, install_dir: str) -> SkillDescriptor:
        try:
            digest: Optional[str] = "sha256:" + engine.manifest_for([skill.name])[0]["content_hash"]
        except Exception:  # noqa: BLE001 - an unreadable skill is shown with an explicit unknown digest, not hidden
            digest = None
        unknown = ["skill_version"] + (["digest"] if digest is None else [])
        origin = ORIGIN_KRIYA_INSTALL if skill.source_path and path_relation(install_dir, skill.source_path) is PathRelation.WITHIN else ORIGIN_WORKSPACE_OR_CONFIGURED
        return SkillDescriptor(
            id=skill_id(skill), name=skill.name, description=_clip(skill.description) or "", category=skill.category, tags=tuple(skill.tags)[:20],
            supported_versions=skill.supported_versions, skill_version=None, digest=digest, source=SOURCE_LOCAL_DIRECTORY, origin=origin,
            scope=_scope(skill, self._workspace, install_dir), verified=skill.verified, verified_at=skill.verified_at,
            verified_context=_clip(skill.verified_context), verification_gap_acknowledged=skill.verification_gap_acknowledged,
            rule_count=len(skill.rules), staged_rule_count=staged_rule_count(skill.source_path), example_count=len(skill.examples),
            has_instructions=bool(skill.instructions.strip()), provenance=_provenance(skill), unknown_fields=tuple(unknown))

    def list(self) -> Tuple[List[SkillDescriptor], int]:
        """(descriptors in id order, capped at MAX_SKILLS; the true total). Ids are unique: of two skills sharing a name, the one ``describe`` resolves wins."""
        engine = self._engine()
        install_dir = os.path.realpath(get_global_skills_dir())
        unique: Dict[str, Skill] = {}
        for skill in engine.list_skills():
            unique.setdefault(skill_id(skill), skill)
        ordered = [unique[key] for key in sorted(unique)]
        return [self._describe(engine, s, install_dir) for s in ordered[:MAX_SKILLS]], len(ordered)

    def describe(self, requested_id: str) -> Optional[SkillDescriptor]:
        """The descriptor of the skill with this id, or None when no skill has it (including a skill whose files failed to load: the engine skips those)."""
        engine = self._engine()
        install_dir = os.path.realpath(get_global_skills_dir())
        skill = next((s for s in engine.list_skills() if skill_id(s) == requested_id.lower()), None)
        return self._describe(engine, skill, install_dir) if skill is not None else None
