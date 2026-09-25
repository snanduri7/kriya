"""PRD-011 reopen: Kriya declares requires-python >=3.10, so nothing it
imports may need a 3.11+-only module or syntax. The concrete defect was an
unconditional ``import tomllib`` in kriya/tools/toolchain_identity.py, which
made ``import kriya.cli`` fail on 3.10."""
import ast
import os
import subprocess
import sys
import textwrap

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE_ROOT = os.path.join(REPO_ROOT, "kriya")
TOMLCOMPAT = os.path.join(PACKAGE_ROOT, "core", "tomlcompat.py")


def _python_sources():
    for dirpath, _dirnames, filenames in os.walk(PACKAGE_ROOT):
        for filename in filenames:
            if filename.endswith(".py"):
                yield os.path.join(dirpath, filename)


def test_cli_imports_and_parses_pyproject_without_stdlib_tomllib(tmp_path):
    """Simulates a 3.10 interpreter: stdlib tomllib is unavailable and only the
    API-identical ``tomli`` backport is importable (stood in for by the real
    module under that name). ``import kriya.cli`` must succeed and a Python
    project's requires-python must still be honoured, not ignored."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "p"\nrequires-python = ">=3.11,<3.12"\n')
    script = textwrap.dedent(f"""
        import sys
        import tomllib as _stdlib
        sys.modules["tomli"] = _stdlib
        sys.modules["tomllib"] = None
        import kriya.cli
        from kriya.core.tomlcompat import tomllib
        from kriya.tools.toolchain_identity import resolve_toolchain_identity
        assert tomllib is _stdlib
        identity = resolve_toolchain_identity({str(tmp_path)!r}, "python")
        print(identity.runtime_version, identity.requirement_source)
    """)
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["3.11", "pyproject.toml:project.requires-python"]


def test_tomllib_is_imported_only_through_tomlcompat():
    offenders = []
    for path in _python_sources():
        if os.path.realpath(path) == os.path.realpath(TOMLCOMPAT):
            continue
        tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(name.split(".")[0] in {"tomllib", "tomli"} for name in names):
                offenders.append(f"{os.path.relpath(path, REPO_ROOT)}:{node.lineno}")
    assert offenders == [], f"import tomllib via kriya.core.tomlcompat: {offenders}"


def test_tomli_backport_is_declared_for_python_310():
    from kriya.core.tomlcompat import tomllib

    with open(os.path.join(REPO_ROOT, "pyproject.toml"), "rb") as stream:
        project = tomllib.load(stream)["project"]
    assert project["requires-python"] == ">=3.10"
    assert "tomli>=1.1.0; python_version < '3.11'" in project["dependencies"]


# 3.11+-only standard-library names; any of these in kriya/ breaks 3.10.
_PY311_ONLY_ATTRIBUTES = {
    ("asyncio", "TaskGroup"), ("asyncio", "timeout"), ("enum", "StrEnum"),
    ("typing", "Self"), ("datetime", "UTC"),
}


def test_no_python_311_only_syntax_or_stdlib_names():
    offenders = []
    for path in _python_sources():
        tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
        rel = os.path.relpath(path, REPO_ROOT)
        for node in ast.walk(tree):
            if type(node).__name__ == "TryStar":
                offenders.append(f"{rel}:{node.lineno} except*")
            elif isinstance(node, ast.Name) and node.id in {"ExceptionGroup", "BaseExceptionGroup"}:
                offenders.append(f"{rel}:{node.lineno} {node.id}")
            elif (
                isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and (node.value.id, node.attr) in _PY311_ONLY_ATTRIBUTES
            ):
                offenders.append(f"{rel}:{node.lineno} {node.value.id}.{node.attr}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    if (node.module, alias.name) in _PY311_ONLY_ATTRIBUTES:
                        offenders.append(f"{rel}:{node.lineno} from {node.module} import {alias.name}")
    assert offenders == [], offenders
