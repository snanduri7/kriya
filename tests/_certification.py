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
import sys
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


# --- certification-platform matrix -----------------------------------------------------------------------------------------------
# ``@pytest.mark.requires_platform("linux")`` declares that a test is MANDATORY evidence of a platform the run may not be on (the Linux-HOST
# procfs measurement of tests/test_d2_m2_fd_inheritance.py). This is NOT a skip allowlist entry: on the required platform the test must run and PASS
# (a skip there is an unexpected skip, a failure); on another platform it cannot be satisfied, so it is recorded as PENDING and the certification can
# never be CERTIFIED while one is pending (INCOMPLETE_PLATFORM_EVIDENCE) - a macOS skip is never a pass. The hosted Linux cell supplies it.
PLATFORM_MARKER = "requires_platform"


def required_platform(markers: Sequence[Any]) -> Optional[str]:
    """The platform a test is mandatory on (``sys.platform`` prefix, e.g. ``linux``), from its markers; None for an ordinary test."""
    for marker in markers:
        if getattr(marker, "name", None) == PLATFORM_MARKER and marker.args:
            return str(marker.args[0])
    return None


# ``@pytest.mark.not_applicable_on("linux", equivalent="tests/x.py::test_y")`` declares that a test's MECHANISM exists only off ``linux`` (e.g. macOS sandbox-exec) and that
# ON linux the same INVARIANT is proved by the test ``equivalent`` (NOT_APPLICABLE_ON_LINUX). It is not an allowlist: it names its proof, only applies on that platform,
# and the execution audit (tests/_certification_collection.py::executed) requires the equivalent to exist in the census and to have PASSED.
NOT_APPLICABLE_MARKER = "not_applicable_on"


def declared_not_applicable(markers: Sequence[Any]) -> Optional[Dict[str, str]]:
    """``{"platform": ..., "equivalent": ...}`` from a ``not_applicable_on`` marker, else None. A marker without a named equivalent is malformed and ignored (so it can
    never excuse a skip)."""
    for marker in markers:
        if getattr(marker, "name", None) == NOT_APPLICABLE_MARKER and marker.args and marker.kwargs.get("equivalent"):
            return {"platform": str(marker.args[0]), "equivalent": str(marker.kwargs["equivalent"])}
    return None


def not_applicable_equivalent(markers: Sequence[Any], platform: Optional[str] = None) -> Optional[str]:
    """The equivalent test's node id when this test is NOT APPLICABLE on ``platform`` (default: this one), else None."""
    declared = declared_not_applicable(markers)
    return declared["equivalent"] if declared is not None and (platform or sys.platform).startswith(declared["platform"]) else None


# ``@pytest.mark.external_private_fixture("PRD034-F9B-SCENARIO01")`` declares that a test needs a fixture that may NOT be redistributed to a hosted runner (PRD-034 F9b: a third-party project
# snapshot with no licence). On a host without the fixture the test is skipped and the skip is recorded as EXTERNAL_PRIVATE_FIXTURE_REQUIRED for that gate - not an unexpected skip and not
# an allowlist entry. It never counts as a pass: the certification summary cannot read CERTIFIED while one is pending, and the only way to discharge it is a private-gate evidence record
# (scripts/certification_private_gate.py) that binds to the same Kriya revision/config and whose gate and node ids match tests/certification/private_fixtures.json.
EXTERNAL_PRIVATE_FIXTURE_MARKER = "external_private_fixture"


def declared_private_fixture(markers: Sequence[Any]) -> Optional[str]:
    """The private-fixture gate id from an ``external_private_fixture`` marker, else None. A marker without a gate id is malformed and ignored (it can never excuse a skip)."""
    for marker in markers:
        if getattr(marker, "name", None) == EXTERNAL_PRIVATE_FIXTURE_MARKER and marker.args and str(marker.args[0]):
            return str(marker.args[0])
    return None


def platform_satisfied(required: Optional[str], platform: Optional[str] = None) -> bool:
    """Whether a run on ``platform`` (default: this one) can supply the evidence a test requires of ``required``."""
    return required is None or (platform or sys.platform).startswith(required)


def write_skips(directory: str, skips: List[Mapping[str, Any]], certification: bool) -> str:
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "skips.json")
    unexpected = [s for s in skips if not s.get("allowlisted") and not s.get("platform_pending") and not s.get("not_applicable") and not s.get("external_private_fixture")]
    private = [{"nodeid": s["nodeid"], "gate": s["external_private_fixture"], "reason": s["reason"]} for s in skips if s.get("external_private_fixture")]
    not_applicable = [{"nodeid": s["nodeid"], "equivalent": s["not_applicable"], "reason": s["reason"]} for s in skips if s.get("not_applicable")]
    pending = [{"nodeid": s["nodeid"], "required_platform": s["platform_pending"], "reason": s["reason"]} for s in skips if s.get("platform_pending")]
    payload: Dict[str, Any] = {"certification_mode": certification, "skipped": len(skips), "platform": sys.platform,
                               "unexpected": len(unexpected), "platform_pending": sorted(pending, key=lambda s: s["nodeid"]),
                               "not_applicable": sorted(not_applicable, key=lambda s: s["nodeid"]),
                               "external_private_fixture": sorted(private, key=lambda s: s["nodeid"]),
                               "skips": sorted(skips, key=lambda s: s["nodeid"])}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return path
