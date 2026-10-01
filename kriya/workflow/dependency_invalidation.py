"""Deterministic dependency-closure and validated-revision invalidation."""

from typing import Dict, Iterable, List, Set


def dependent_closure(
    roots: Iterable[str], dependencies: Dict[str, List[str]],
) -> List[str]:
    """Return roots plus every manifest entry that transitively depends on them."""
    affected: Set[str] = set(roots)
    changed = True
    while changed:
        changed = False
        for path, required_paths in dependencies.items():
            if path in affected:
                continue
            if affected.intersection(required_paths):
                affected.add(path)
                changed = True
    manifest_order = list(dependencies)
    return [path for path in manifest_order if path in affected] + sorted(
        affected.difference(manifest_order)
    )


def invalidate_validated_revisions(
    validated_revisions: Dict[str, str],
    changed_files: Iterable[str],
    dependencies: Dict[str, List[str]],
) -> List[str]:
    invalidated = dependent_closure(changed_files, dependencies)
    removed = []
    for path in invalidated:
        if path in validated_revisions:
            validated_revisions.pop(path)
            removed.append(path)
    return removed
