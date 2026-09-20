"""PRD-001: production imports and configuration remain bootstrap-safe."""

import subprocess
import sys
from pathlib import Path
from typing import get_type_hints

import pytest
from pydantic import ValidationError

from kriya.config.authority import get_field_value
from kriya.config.config import ExecutionPolicyConfig

ROOT = Path(__file__).resolve().parents[1]


def test_authority_annotations_resolve_on_lazy_annotation_python():
    # Python 3.14 defers annotations, masking missing imports at bootstrap.
    assert get_type_hints(get_field_value)


def test_all_production_modules_import_in_fresh_process():
    script = """
import importlib
from pathlib import Path
for path in sorted(Path('kriya').rglob('*.py')):
    parts = list(path.with_suffix('').parts)
    if parts[-1] == '__init__':
        parts.pop()
    importlib.import_module('.'.join(parts))
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT,
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_cli_bootstrap_in_fresh_process():
    result = subprocess.run(
        [sys.executable, "-m", "kriya.cli", "--help"], cwd=ROOT,
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Usage:" in result.stdout


def test_config_has_no_undefined_names():
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "F821",
         "kriya/config/authority.py", "kriya/config/config.py"],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("mode", ["audit", "enforce"])
def test_execution_policy_accepts_supported_modes(mode):
    assert ExecutionPolicyConfig(mode=mode).mode == mode


@pytest.mark.parametrize("mode", ["unknown", "ENFORCE", "", "disabled"])
def test_execution_policy_rejects_unknown_modes(mode):
    with pytest.raises(ValidationError, match="must be 'audit' or 'enforce'"):
        ExecutionPolicyConfig(mode=mode)


def test_execution_policy_default_remains_audit():
    assert ExecutionPolicyConfig().mode == "audit"
