"""Auditable, local-only projections from canonical source evidence."""
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, Optional

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
