"""Bounded, revision-grounded evidence packages for generation retries."""

import os
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

from kriya.workflow.context_projection import FileProjection, project_implementation_source
from kriya.workflow.failure import Failure


_OMISSION = "\n... [middle omitted from retry evidence; canonical evidence remains local] ...\n"


def _bounded_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= len(_OMISSION):
        return text[:max_chars]
    remaining = max_chars - len(_OMISSION)
    head = remaining // 2
    return text[:head] + _OMISSION + text[-(remaining - head):]


@dataclass(frozen=True)
class RetryPackage:
    """Canonical local evidence projected into a bounded model-facing view."""

    failure_type: str
    authoritative_error: str
    advisory_context: str
    target_files: tuple[str, ...]
    target_projections: tuple[FileProjection, ...]
    reference_projections: tuple[FileProjection, ...]
    source_context: Dict[str, str]
    omitted_files: tuple[str, ...]

    def render_error(self) -> str:
        rendered = (
            f"Failure type: {self.failure_type}\n"
            f"Authoritative validator evidence:\n{self.authoritative_error}"
        )
        if self.advisory_context:
            rendered += (
                "\n\nAdvisory reference evidence (verify against local validators):\n"
                + self.advisory_context
            )
        return rendered

    def render_context(self) -> str:
        blocks: List[str] = []
        for projection in self.reference_projections:
            blocks.append(
                "=== Existing dependency/reference file (read-only unless the fix "
                f"requires it): {projection.path} ===\n{projection.render()}"
            )
        for projection in self.target_projections:
            blocks.append(
                f"=== Authoritative current implementation to repair: {projection.path} ===\n"
                f"{projection.render()}"
            )
            source = self.source_context.get(projection.path)
            if source:
                blocks.append(
                    f"=== Validator source location for {projection.path} ===\n{source}"
                )
        if self.omitted_files:
            blocks.append(
                "=== Additional files omitted from retry evidence budget ===\n"
                + ", ".join(self.omitted_files)
            )
        return "\n\n".join(blocks)


def build_retry_package(
    *,
    failure: Failure,
    worktree_path: str,
    all_files: Iterable[str],
    target_files: Optional[Sequence[str]],
    source_context: Optional[Dict[str, str]],
    max_chars: int,
    max_error_chars: int = 6000,
    advisory_context: str = "",
) -> RetryPackage:
    """Build a deterministic package without sending or resolving data remotely.

    Targets receive 70% of the source budget and are always considered first.
    Reference files share the remainder.  Every included projection carries the
    SHA-256 revision of its complete canonical source, even when its middle is
    omitted from the model-facing excerpt.
    """
    ordered_files = sorted(set(all_files))
    requested_targets = tuple(dict.fromkeys(target_files or failure.likely_files))
    target_set = set(requested_targets)
    targets = [path for path in ordered_files if path in target_set]
    references = [path for path in ordered_files if path not in target_set]

    target_budget = int(max_chars * 0.7) if targets else 0
    reference_budget = max_chars - target_budget
    target_per_file = max(1000, target_budget // max(1, len(targets)))
    reference_per_file = max(600, reference_budget // max(1, len(references)))

    target_projections: List[FileProjection] = []
    reference_projections: List[FileProjection] = []
    omitted: List[str] = []
    consumed = 0

    def add(paths: List[str], per_file: int, destination: List[FileProjection], reason: str) -> None:
        nonlocal consumed
        for path in paths:
            try:
                with open(
                    os.path.join(worktree_path, path), "r",
                    encoding="utf-8", errors="replace",
                ) as fh:
                    content = fh.read()
            except OSError:
                omitted.append(path)
                continue
            remaining = max_chars - consumed
            if remaining <= 0:
                omitted.append(path)
                continue
            projection = project_implementation_source(
                content,
                path,
                min(per_file, remaining),
                reason=reason,
            )
            destination.append(projection)
            consumed += len(projection.content)

    add(targets, target_per_file, target_projections, "implicated_by_authoritative_failure")
    add(references, reference_per_file, reference_projections, "dependency_reference")

    error = failure.raw_output or failure.message
    return RetryPackage(
        failure_type=failure.type,
        authoritative_error=_bounded_text(error, max_error_chars),
        advisory_context=_bounded_text(advisory_context, 3000),
        target_files=requested_targets,
        target_projections=tuple(target_projections),
        reference_projections=tuple(reference_projections),
        source_context={
            path: _bounded_text(text, 2000)
            for path, text in (source_context or {}).items()
            if path in target_set
        },
        omitted_files=tuple(omitted),
    )
