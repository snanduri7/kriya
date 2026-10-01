"""Installed build identity (KRIYA-VERSION-001).

What an installed Kriya reports about itself: the package version (from the
installed distribution's metadata, never a second hand-kept constant) and the
exact source it was built from (git commit, tree and dirty flag). The source
identity is EMBEDDED at build time by setup.py into ``kriya/_build_info.json``
inside the wheel; this module only reads that file. It never runs git: an
installed wheel has no checkout, and the checkout it came from may have moved
on since.

A Kriya without embedded provenance (an editable/source checkout, or any
other unsupported install path) reports ``UNKNOWN`` with
``build_provenance: unavailable`` - never a guessed revision. A present but
malformed file reports ``build_provenance: invalid``.
"""
from __future__ import annotations

import json
import os
import platform
import re
from importlib import metadata
from typing import Any, Dict, Optional

PRODUCT = "kriya"
UNKNOWN = "UNKNOWN"
EMBEDDED = "embedded"
UNAVAILABLE = "unavailable"
INVALID = "invalid"
BUILD_INFO_SCHEMA = 1
BUILD_INFO_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_build_info.json")
_OBJECT_ID = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")  # SHA-1 or SHA-256 git object ids


def package_version() -> str:
    """The installed distribution's version (package metadata), or UNKNOWN."""
    try:
        return metadata.version(PRODUCT)
    except metadata.PackageNotFoundError:
        return UNKNOWN


def embedded_build_info(path: Optional[str] = None) -> Dict[str, Any]:
    """The source identity embedded at build time: ``commit``, ``tree``,
    ``dirty`` and ``build_provenance``. Never raises and never invents data."""
    unknown = {"commit": UNKNOWN, "tree": UNKNOWN, "dirty": None}
    try:
        with open(path or BUILD_INFO_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {**unknown, "build_provenance": UNAVAILABLE}
    except (OSError, ValueError):
        return {**unknown, "build_provenance": INVALID}
    if (not isinstance(data, dict) or data.get("schema") != BUILD_INFO_SCHEMA
            or not all(isinstance(data.get(key), str) and _OBJECT_ID.match(data[key])
                       for key in ("git_commit", "git_tree"))
            or not isinstance(data.get("dirty"), bool)):
        return {**unknown, "build_provenance": INVALID}
    return {"commit": data["git_commit"], "tree": data["git_tree"], "dirty": data["dirty"],
            "build_provenance": EMBEDDED}


def version_report(path: Optional[str] = None) -> Dict[str, Any]:
    """The machine-readable identity (`kriya version --json`)."""
    return {"product": PRODUCT, "version": package_version(), **embedded_build_info(path),
            "python": platform.python_version(),
            "install_path": os.path.dirname(os.path.abspath(__file__))}


def version_line(report: Optional[Dict[str, Any]] = None) -> str:
    """The concise form (`kriya --version`), e.g.
    ``Kriya 0.1.0 (commit 28fa21f, tree bc6ffc113f0d)``."""
    report = report or version_report()
    if report["build_provenance"] != EMBEDDED:
        return (f"Kriya {report['version']} (commit {UNKNOWN}, tree {UNKNOWN}; "
                f"build provenance {report['build_provenance']})")
    dirty = ", dirty" if report["dirty"] else ""
    return f"Kriya {report['version']} (commit {report['commit'][:7]}, tree {report['tree'][:12]}{dirty})"
