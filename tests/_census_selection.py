"""S7: exact-ID test selection for qualification runs. The old plain-text id file (one id per line) silently dropped parametrized ids that contain a line break, so a
migration-sensitive test could escape a qualification run unnoticed. The id list is JSON (a line break is just an escaped character), selection is by EXACT node id, and an
id that is requested but not collected is a usage error - never a silent omission."""
from __future__ import annotations

import json
from typing import Iterable, List, Sequence


def write_ids(path: str, ids: Iterable[str]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(sorted(set(ids)), handle)


def read_ids(path: str) -> List[str]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
        raise ValueError(f"{path}: a census id file is a JSON list of node-id strings")
    return data


def select(items: Sequence, requested: Sequence[str], key=lambda item: item.nodeid):
    """-> (kept items, requested ids that were not collected). ``key`` maps an item to the id compared (the base node id, without xdist's group suffix)."""
    wanted = set(requested)
    kept = [item for item in items if key(item) in wanted]
    return kept, sorted(wanted - {key(item) for item in kept})
