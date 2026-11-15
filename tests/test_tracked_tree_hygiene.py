"""WAVE8-TRACKED-BYTECODE-001: no compiled bytecode is tracked.

MEASURED (2026-11-06, and again on ef89dcd): three ``oracle_preflight.cpython-31x.pyc`` files were tracked under
``handover/BACKEND-READINESS-V2/gate-certification/__pycache__/`` although ``.gitignore`` lists ``__pycache__/``. A run that imported
``oracle_preflight`` rewrote one of them, so a test run made the TRACKED tree dirty: a certification run on a pinned commit must
never change tracked state. An ignore rule does not untrack a file, so the rule alone proved nothing.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_no_compiled_bytecode_is_tracked():
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, timeout=60, check=False)
    if listed.returncode != 0:
        pytest.skip("not a git checkout (for example a source archive): there is no tracked set to check")
    tracked = [name for name in listed.stdout.split("\0") if name]
    assert tracked, "git listed no file: the check would pass vacuously"
    bytecode = [name for name in tracked if name.endswith((".pyc", ".pyo")) or "__pycache__/" in name]
    assert bytecode == [], f"tracked bytecode dirties the tree whenever its module is imported: {bytecode}"
