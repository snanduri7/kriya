"""PRD-034 certification mode: an unexpected skip is a failure.

In certification mode (``--certification`` or ``KRIYA_CERTIFICATION=1``)
every skipped or xfailed test must match an entry in
``tests/certification/skip_allowlist.yaml``. Each entry carries a reason, an
owner and an expiry date; an expired entry no longer allows anything.
Outside certification mode nothing changes: skips are only recorded. The
tests/conftest.py plugin applies this and writes ``skips.json`` with
``--certification-report DIR``.
"""
from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import yaml

ALLOWLIST_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "certification", "skip_allowlist.yaml")
ENV_ALLOWLIST = "KRIYA_SKIP_ALLOWLIST"  # another allowlist (the plugin's own tests)
ENV_CERTIFICATION = "KRIYA_CERTIFICATION"
_REQUIRED_FIELDS = ("id", "nodeid", "reason", "owner", "expires")


class AllowlistError(ValueError):
    """The allowlist is malformed: certification cannot rely on it."""


@dataclass(frozen=True)
class AllowedSkip:
    id: str
    nodeid: str  # an fnmatch pattern over the pytest node id
    reason: str
    owner: str
    expires: date
    reason_contains: Optional[str] = None  # the skip reason must contain this text

    def matches(self, nodeid: str, reason: str) -> bool:
        return fnmatch.fnmatchcase(nodeid, self.nodeid) and (
            self.reason_contains is None or self.reason_contains in reason)


def load_allowlist(path: Optional[str] = None) -> Tuple[AllowedSkip, ...]:
    path = path or os.environ.get(ENV_ALLOWLIST) or ALLOWLIST_PATH
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError) as error:
        raise AllowlistError(f"cannot read {path}: {error}") from error
    entries = payload.get("allowed_skips")
    if payload.get("schema_version") != 1 or not isinstance(entries, list):
        raise AllowlistError(f"{path}: schema_version 1 with an allowed_skips list is required")
    allowed = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or any(not entry.get(name) for name in _REQUIRED_FIELDS):
            raise AllowlistError(f"{path}: entry {index} needs {', '.join(_REQUIRED_FIELDS)}")
        expires = entry["expires"]
        if not isinstance(expires, date):
            raise AllowlistError(f"{path}: entry {entry['id']} expires must be a YYYY-MM-DD date")
        allowed.append(AllowedSkip(str(entry["id"]), str(entry["nodeid"]), str(entry["reason"]),
                                   str(entry["owner"]), expires, entry.get("reason_contains")))
    ids = [a.id for a in allowed]
    if len(ids) != len(set(ids)):
        raise AllowlistError(f"{path}: duplicate ids")
    return tuple(allowed)


def match_skip(allowlist: Sequence[AllowedSkip], nodeid: str, reason: str,
               today: Optional[date] = None) -> Tuple[Optional[AllowedSkip], bool]:
    """(matching entry or None, expired). An expired entry allows nothing."""
    today = today or date.today()
    for entry in allowlist:
        if entry.matches(nodeid, reason):
            return entry, entry.expires < today
    return None, False


def skip_reason(longrepr: Any) -> str:
    """The human reason of a skip report (pytest's (file, line, reason) tuple)."""
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        return str(longrepr[2]).removeprefix("Skipped: ")
    return str(longrepr)


def write_skips(directory: str, skips: List[Mapping[str, Any]], certification: bool) -> str:
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "skips.json")
    unexpected = [s for s in skips if not s.get("allowlisted")]
    payload: Dict[str, Any] = {"certification_mode": certification, "skipped": len(skips),
                               "unexpected": len(unexpected), "skips": sorted(skips, key=lambda s: s["nodeid"])}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return path
