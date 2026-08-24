"""Auditable, local-only projections from canonical source evidence."""
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from kriya.control.artifacts import ArtifactRegistry
from kriya.workflow.context_package import ContextPackage
from kriya.workflow.edit_safety import content_revision


class ProjectionLevel(str, Enum):
    FULL = "full"
    IMPLEMENTATION_EXCERPT = "implementation_excerpt"
    SIGNATURES = "signatures"
    SUMMARY = "summary"


@dataclass(frozen=True)
class FileProjection:
    path: str
    revision: str
    level: ProjectionLevel
    reason: str
    content: str
    omitted_regions: bool
    original_chars: int

    def metadata(self) -> Dict[str, Any]:
        data = asdict(self)
        data.pop("content")
        data["level"] = self.level.value
        return data

    def render(self) -> str:
        omitted = "yes" if self.omitted_regions else "no"
        return (
            f"--- {self.path} [revision={self.revision[:12]} "
            f"projection={self.level.value} omitted={omitted} "
            f"reason={self.reason}] ---\n{self.content}"
        )


def project_implementation_source(
    content: str, path: str, max_chars: int, *, reason: str,
    known_revision: Optional[str] = None,
) -> FileProjection:
    """Keep bounded implementation evidence with explicit head/tail omission.

    known_revision lets a caller that already computed this exact content's
    SHA-256 moments earlier (e.g. the Quality Gates loop's own post-compile
    state.validated_file_revisions snapshot) pass it straight through instead
    of this function re-hashing the full content a second time - safe only
    when the caller can guarantee content hasn't changed on disk since that
    revision was computed, which callers must verify themselves."""
    marker = "\n... [middle omitted from working context; canonical source remains local] ...\n"
    if len(content) <= max_chars:
        projected = content
        omitted = False
        level = ProjectionLevel.FULL
    elif max_chars <= len(marker):
        projected = content[:max_chars]
        omitted = True
        level = ProjectionLevel.IMPLEMENTATION_EXCERPT
    else:
        available = max_chars - len(marker)
        head_chars = available // 2
        tail_chars = available - head_chars
        projected = content[:head_chars] + marker + content[-tail_chars:]
        omitted = True
        level = ProjectionLevel.IMPLEMENTATION_EXCERPT
    return FileProjection(
        path=path,
        revision=known_revision if known_revision is not None else content_revision(content),
        level=level,
        reason=reason,
        content=projected,
        omitted_regions=omitted,
        original_chars=len(content),
    )


def render_established_file_context(established_file_context: Dict[str, str]) -> str:
    """Renders established_file_context into run_generation_workflow()'s
    `supplementary_context` - see that parameter's own docstring for why this
    goes there rather than into the goal text passed to
    build_milestone_goal_text() (kriya/workflow/milestones.py): goal text only
    reliably reaches Planner, not Architect or Developer's initial generation.

    Moved here from kriya/workflow/milestones.py at MA5.8 (control-plane
    implementation plan) so kriya/workflow/context_projection.py's own new
    project_established_file_context() (MA5.8's ContextPackage compatibility
    bridge) can call this SAME rendering logic instead of a second,
    driftable copy of it - milestones.py itself couldn't be the shared home,
    since it already imports project_implementation_source FROM this module
    and importing back the other way would be circular. milestones.py
    re-exports this name unchanged so its own existing callers and
    tests/test_milestones.py's import are unaffected."""
    if not established_file_context:
        return ""
    blocks = [
        f"=== {path} (already built by an earlier milestone in this sequence "
        "- this is its REAL current content, not a guess. Match its actual "
        "constructor/method signatures exactly, AND match its build layout: "
        "the exact package it declares (or, if it declares none, the default/"
        "unnamed package) and its directory location relative to the "
        "workspace root. A class in one named package can never reference a "
        "class in a different or default package, in any language version - "
        "if new code must interoperate with this file, adapt the NEW file's "
        "package/directory to match this established one, never the "
        f"reverse.) ===\n{content}"
        for path, content in sorted(established_file_context.items())
    ]
    return "\n\n" + "\n\n".join(blocks)


def project_established_file_context(context_package: ContextPackage) -> str:
    """MA5.8 compatibility bridge: renders a ContextPackage's established-
    milestone-output items into the EXACT same string shape
    render_established_file_context() already produces for
    MilestoneRunState.established_file_context - so run_generation_workflow()
    can consume either source through the same supplementary_context
    parameter without caring which one produced it. Only items tagged with
    context_orchestrator.py's SOURCE_TYPE_ESTABLISHED_MILESTONE_OUTPUT are
    included - a semantic_hit or contract_provider item was never
    established file content and would misrepresent the "already built by
    an earlier milestone" framing render_established_file_context's own
    wording makes."""
    from kriya.workflow.context_orchestrator import SOURCE_TYPE_ESTABLISHED_MILESTONE_OUTPUT

    established = {
        item.path: item.content
        for item in context_package.relevant_files
        if item.source_type == SOURCE_TYPE_ESTABLISHED_MILESTONE_OUTPUT
    }
    return render_established_file_context(established)


def project_established_dependencies(artifacts: ArtifactRegistry) -> List[str]:
    """MA5.8 compatibility bridge: produces the SAME 'groupId:artifactId'
    (or bare package name for non-Maven ecosystems) string shape
    MilestoneRunState.established_dependencies already carries (populated
    today via kriya/tools/validate.py's get_pom_dependencies) - a real
    caller can swap an ArtifactRegistry in as the source of this list
    without changing anything downstream that already expects a flat
    List[str] of dependency coordinates. Sorted for a stable, diffable
    result - ArtifactRegistry.all_records() has no guaranteed order."""
    coordinates = []
    for record in artifacts.all_records():
        group_id = record.coordinates.get("groupId")
        artifact_id = record.coordinates.get("artifactId")
        if artifact_id:
            coordinates.append(f"{group_id}:{artifact_id}" if group_id else artifact_id)
            continue
        name = record.coordinates.get("name")
        if name:
            coordinates.append(name)
    return sorted(set(coordinates))
