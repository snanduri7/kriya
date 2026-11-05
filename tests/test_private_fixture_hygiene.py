"""Private-fixture hygiene (owner ruling 2026-11-05): the canary baseline is the PRIVATE scenario-01 fixture (third-party source, no redistribution right).

Raw run artifacts of a fixture run (diffs, stdout, run records, anything carrying fixture SOURCE text) must never be tracked: the historical ones were removed from the active tree
(evidence/PRD-036/HISTORICAL_FIXTURE_EVIDENCE_MANIFEST.csv records each by path and Git blob; history is untouched and handled separately). This is the permanent tripwire.
"""
import csv
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "evidence" / "PRD-036" / "HISTORICAL_FIXTURE_EVIDENCE_MANIFEST.csv"
EVIDENCE_ROOTS = ("evidence/", "handover/evidence/")
# Fixture SOURCE text, not a path mention: a statement, a declaration or a diff header of the fixture's Java sources.
STRONG = re.compile(r"driverDO\.setDeleted|driverRepository\.(save|findById|delete)|class DefaultDriverService|public (DriverDO|DefaultDriverService)|diff --git a/src/main/java/com/myapp")
PAYLOAD_NAMES = {"workspace-diff.txt", "stdout.json", "git-diff.txt"}


def _tracked():
    out = subprocess.run(["git", "ls-files", "-s", "-z", "--", *EVIDENCE_ROOTS], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [tuple(entry.split("\t", 1)[::-1]) for entry in out.split("\0") if entry]  # (path, "mode blob stage")


def _payload(path):
    return os.path.basename(path) in PAYLOAD_NAMES or "/run-records/" in path


def _rundir(path):
    return os.path.dirname(os.path.dirname(path)) if "/run-records/" in path else os.path.dirname(path)


def fixture_artifacts(entries, read):
    """The tracked files that are raw fixture-run artifacts: carrying fixture source text, or a payload-class file in a run directory where one does."""
    strong = {path for path, _ in entries if STRONG.search(read(path))}
    runs = {_rundir(path) for path in strong if _payload(path)} | {_rundir(path) for path, _ in entries if _payload(path) and path in strong}
    return sorted(strong | {path for path, _ in entries if _payload(path) and _rundir(path) in runs})


def _read(path):
    try:
        return (ROOT / path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def test_no_tracked_evidence_carries_raw_private_fixture_material():
    assert fixture_artifacts(_tracked(), _read) == []


def test_every_historical_artifact_is_recorded_in_the_manifest_and_is_no_longer_tracked():
    rows = list(csv.DictReader(MANIFEST.open()))
    assert len(rows) == 70 and all(re.fullmatch(r"[0-9a-f]{40}", row["git_blob_sha1"]) for row in rows)
    tracked_paths = {path for path, _ in _tracked()}
    tracked_blobs = {meta.split()[1] for _, meta in _tracked()}
    assert not tracked_paths & {row["path"] for row in rows}
    # an EMPTY file (the shared empty blob) carries no material and is legitimately tracked elsewhere
    assert not tracked_blobs & {row["git_blob_sha1"] for row in rows if int(row["bytes"]) > 0}


def test_the_manifest_holds_metadata_only():
    header = MANIFEST.read_text().splitlines()[0]
    assert header == "path,git_blob_sha1,bytes,class,strong_source_markers"
    assert not STRONG.search(MANIFEST.read_text()) and not STRONG.search((MANIFEST.with_suffix(".md")).read_text())


@pytest.mark.parametrize("name,files,expected", [
    ("a fixture diff", {"evidence/x/run-1/workspace-diff.txt": "diff --git a/src/main/java/com/myapp/Foo.java b/x"}, ["evidence/x/run-1/workspace-diff.txt"]),
    ("source text in a json payload", {"evidence/x/stdout.json": '{"o": "driverDO.setDeleted(true);"}'}, ["evidence/x/stdout.json"]),
    ("a run record next to a leaking payload", {"evidence/x/run-1/stdout.json": "class DefaultDriverService {}", "evidence/x/run-1/run-records/r.json": "{}"},
     ["evidence/x/run-1/run-records/r.json", "evidence/x/run-1/stdout.json"]),
    ("a path-only mention is metadata", {"evidence/x/canary.json": '{"changed": ["src/main/java/com/myapp/service/driver/DefaultDriverService.java"]}'}, []),
    ("an unrelated run directory is untouched", {"evidence/y/stdout.json": "{}", "evidence/y/run-records/r.json": "{}"}, []),
])
def test_the_detector_finds_the_artifact_classes_and_ignores_metadata(name, files, expected):
    entries = [(path, "100644 abc 0") for path in files]
    assert fixture_artifacts(entries, files.get) == expected, name
