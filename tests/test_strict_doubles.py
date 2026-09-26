"""Tripwire for CLAUDE.md "Mandatory quality bar" rule 3 (strict test doubles),
plus the contract of the helpers in ``tests/_strict_doubles.py``.

A bare ``MagicMock()`` config, kernel or workflow engine makes every flag the
code under test reads truthy. In Batch 5 that silently switched PRD-020's
terminal requirement verifier on in two suites. The static check cannot see
this, so the repository guard below rejects the pattern at its source."""
import ast
import re
from pathlib import Path

import pytest
from _strict_doubles import strict_config, strict_engine, strict_kernel

from kriya.config.config import AppConfig

TESTS_DIR = Path(__file__).resolve().parent
_MOCK_FACTORIES = {"Mock", "MagicMock", "AsyncMock", "NonCallableMock", "NonCallableMagicMock"}
# A name that holds a config, kernel or policy double.
_GUARDED_NAME = re.compile(r"(^|_)(kernel|config|cfg|policy)$")
# A name that holds a WorkflowEngine double: allowed bare only when the same
# function then sets its ``kernel`` explicitly (None, or a real config).
_ENGINE_NAME = re.compile(r"^(we|engine|workflow_engine|mock_engine|fake_engine)$")
_GUARDED_ATTR = {"kernel", "config", "cfg"}


def _is_bare_mock(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call) or node.args:
        return False
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name not in _MOCK_FACTORIES:
        return False
    return not any(kw.arg in ("spec", "spec_set", "wraps") for kw in node.keywords)


def _target_name(target: ast.AST):
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _guarded_target(target: ast.AST) -> bool:
    name = _target_name(target)
    if name is None:
        return False
    if isinstance(target, ast.Attribute) and name in _GUARDED_ATTR:
        return True
    return bool(_GUARDED_NAME.search(name))


def _sets_kernel_explicitly(scope: ast.AST, engine: str) -> bool:
    for node in ast.walk(scope):
        if not isinstance(node, ast.Assign) or _is_bare_mock(node.value):
            continue
        for target in node.targets:
            if (isinstance(target, ast.Attribute) and target.attr == "kernel"
                    and isinstance(target.value, ast.Name) and target.value.id == engine):
                return True
    return False


def bare_double_sites(source: str, filename: str = "<src>"):
    tree = ast.parse(source, filename)
    scope_of = {}
    for scope in ast.walk(tree):
        if isinstance(scope, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for child in ast.walk(scope):
                scope_of[child] = scope  # innermost scope wins: walk visits outer first
    sites = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if not _is_bare_mock(value):
            continue
        for target in targets:
            name = _target_name(target)
            if _guarded_target(target) or (
                isinstance(target, ast.Name) and _ENGINE_NAME.search(name)
                and not _sets_kernel_explicitly(scope_of[node], name)
            ):
                sites.append(f"{filename}:{node.lineno}")
                break
    return sites


def test_no_bare_mock_config_kernel_or_engine_in_tests():
    offenders = []
    for path in sorted(TESTS_DIR.rglob("*.py")):
        if path.name == "_strict_doubles.py":
            continue
        offenders += bare_double_sites(path.read_text(encoding="utf-8"), str(path.relative_to(TESTS_DIR)))
    assert offenders == [], (
        "bare MagicMock config/kernel/engine doubles make every flag truthy; use "
        "tests/_strict_doubles.py (strict_config/strict_kernel/strict_engine) or spec=:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("snippet", [
    "kernel = MagicMock()",
    "mock_kernel = MagicMock()",
    "cfg = MagicMock()",
    "app_config = mock.MagicMock()",
    "we = MagicMock()",
    "engine = AsyncMock()",
    "self.kernel = MagicMock()",
    "we.kernel = MagicMock()",
    "obj.config = Mock()",
    "execution_policy = MagicMock()",
    "kernel: object = MagicMock()",
    "def f():\n    we = MagicMock()\n    we.kernel = MagicMock()",
])
def test_guard_flags_bare_doubles(snippet):
    assert bare_double_sites(snippet)


@pytest.mark.parametrize("snippet", [
    "kernel = MagicMock(spec=Kernel)",
    "cfg = MagicMock(spec_set=AppConfig)",
    "kernel = strict_kernel()",
    "run_state = MagicMock()",
    "engineering_triage = MagicMock()",
    "config_path = MagicMock()",
    "we.run_verifier = MagicMock()",
    "shared_engine = MagicMock()",
    "def f():\n    we = MagicMock()\n    we.kernel = None",
    "def f():\n    engine = MagicMock()\n    engine.kernel = SimpleNamespace(config=cfg)",
    "def f():\n    we = MagicMock()\n    we.kernel = strict_kernel()",
])
def test_guard_allows_strict_or_unrelated_doubles(snippet):
    assert bare_double_sites(snippet) == []


def test_strict_config_is_real_validated_and_rejects_unknown_fields():
    cfg = strict_config(autonomy={"spec_compliance_enabled": True})
    assert isinstance(cfg, AppConfig)
    assert cfg.autonomy.spec_compliance_enabled is True
    # Every other flag is the packaged default, not a truthy MagicMock.
    assert cfg.autonomy.self_correction_loop_enabled is False
    with pytest.raises(KeyError):
        strict_config(autonomy={"spec_complianc_enabled": True})
    with pytest.raises(KeyError):
        strict_config(autonomi={})
    with pytest.raises(ValueError):
        strict_config(workflow_controller={"mode": "not-a-mode"})


def test_strict_config_paths_never_resolve_into_the_cwd(tmp_path):
    cfg = strict_config()
    assert Path(cfg.paths.memory).is_absolute() and Path(cfg.paths.skills).is_absolute()
    assert strict_config(paths={"memory": str(tmp_path), "skills": str(tmp_path)}).paths.memory == str(tmp_path)


def test_strict_kernel_and_engine_carry_real_config_and_reject_unknown_attributes():
    cfg = strict_config()
    kernel = strict_kernel(cfg)
    assert kernel.config is cfg
    with pytest.raises(AttributeError):
        kernel.llm  # noqa: B018 - Kernel has no llm attribute
    engine = strict_engine(cfg)
    assert engine.kernel.config is cfg
    assert isinstance(strict_engine().kernel.config, AppConfig)
