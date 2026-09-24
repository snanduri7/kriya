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


def _production_python_files():
    # Tracked files only, so local scratch copies never decide the result;
    # fall back to the package directories outside a git checkout (sdist).
    listed = subprocess.run(
        ["git", "ls-files", "kriya/*.py", "plugins/*.py"],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    files = listed.stdout.split() if listed.returncode == 0 else []
    return files or ["kriya", "plugins"]


def test_production_code_has_no_undefined_names():
    # F821 also covers string/deferred annotations, which neither a fresh
    # import nor Python 3.14's lazy annotations would ever surface.
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "F821",
         *_production_python_files()],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_every_production_annotation_resolves_at_runtime():
    # F821 accepts a name imported only under TYPE_CHECKING, but
    # typing.get_type_hints() cannot see it and raises NameError. Every
    # class, method and function defined in kriya/ must resolve.
    script = """
import importlib, inspect, typing
from pathlib import Path
unresolved = []
for path in sorted(Path('kriya').rglob('*.py')):
    parts = list(path.with_suffix('').parts)
    if parts[-1] == '__init__':
        parts.pop()
    module = importlib.import_module('.'.join(parts))
    for name, obj in vars(module).items():
        if getattr(obj, '__module__', None) != module.__name__:
            continue
        targets = [obj]
        if inspect.isclass(obj):
            targets += [v for v in vars(obj).values() if inspect.isfunction(v)]
        elif not inspect.isfunction(obj):
            continue
        for target in targets:
            try:
                typing.get_type_hints(target)
            except NameError as error:
                unresolved.append(f'{module.__name__}.{name}: {error}')
print('\\n'.join(unresolved))
raise SystemExit(1 if unresolved else 0)
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT,
        capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# Pairs that import each other; annotations cross via a module alias bound at
# the end of the module, which must work whichever side loads first.
@pytest.mark.parametrize("module", [
    "kriya.tools.containment", "kriya.tools.toolchain_identity",
    "kriya.workflow.proposal_binding", "kriya.workflow.proposal_promotion",
    "kriya.workflow.review_context", "kriya.workflow.workflow",
])
def test_mutually_importing_modules_load_first_in_fresh_process(module):
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"], cwd=ROOT,
        capture_output=True, text=True, timeout=60,
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
