"""VER-004 closure evidence (2026-09-13, VER/STATE/RECV/REPO reconciliation
package): the risk register's own gap statement is narrow and precise -
"pyproject.toml dependency extraction and installation is a real, wired
mechanism... though no dedicated unit test isolates the extraction->install
path in isolation." This file closes exactly that gap.

`_pyproject_dependencies()` is a pure function (no subprocess, no venv) -
tested directly. `_resolve_python_interpreter()`'s own wiring is tested by
mocking `_ensure_project_venv` (the one real, expensive I/O boundary) and
asserting the EXACT extracted dependency list reaches it unmodified - the
"extraction -> install" path this risk names, isolated from the real
venv-creation/pip-install cost.
"""
import os
from unittest.mock import patch

from kriya.tools.validate import PolymorphicValidator, _pyproject_dependencies


def _write(tmp_path, relpath, content):
    p = tmp_path / relpath
    p.write_text(content)
    return str(p)


# ---------------------------------------------------------------------------
# _pyproject_dependencies() - pure extraction
# ---------------------------------------------------------------------------

def test_extracts_real_pep621_dependencies(tmp_path):
    path = _write(tmp_path, "pyproject.toml", (
        "[project]\nname = \"x\"\ndependencies = [\"django>=4.0\", \"requests\"]\n"
    ))
    assert _pyproject_dependencies(path) == ["django>=4.0", "requests"]


def test_missing_project_dependencies_key_returns_empty(tmp_path):
    path = _write(tmp_path, "pyproject.toml", "[project]\nname = \"x\"\n")
    assert _pyproject_dependencies(path) == []


def test_no_project_table_at_all_returns_empty(tmp_path):
    path = _write(tmp_path, "pyproject.toml", "[tool.black]\nline-length = 100\n")
    assert _pyproject_dependencies(path) == []


def test_malformed_toml_degrades_to_empty_never_raises(tmp_path):
    path = _write(tmp_path, "pyproject.toml", "not valid toml [[[")
    assert _pyproject_dependencies(path) == []


def test_missing_file_degrades_to_empty_never_raises(tmp_path):
    assert _pyproject_dependencies(str(tmp_path / "does_not_exist.toml")) == []


def test_non_string_or_blank_entries_filtered(tmp_path):
    path = _write(tmp_path, "pyproject.toml", (
        "[project]\nname = \"x\"\ndependencies = [\"django\", \"\", \"   \"]\n"
    ))
    assert _pyproject_dependencies(path) == ["django"]


def test_dependencies_not_a_list_returns_empty(tmp_path):
    # A malformed [project.dependencies] that is a table, not an array -
    # PEP 621 requires an array; this must degrade safely, not raise.
    path = _write(tmp_path, "pyproject.toml", (
        "[project]\nname = \"x\"\n[project.dependencies]\nfoo = \"bar\"\n"
    ))
    assert _pyproject_dependencies(path) == []


# ---------------------------------------------------------------------------
# _resolve_python_interpreter() - extraction -> install wiring
# ---------------------------------------------------------------------------

def test_pyproject_dependencies_reach_ensure_project_venv_unmodified(tmp_path):
    """The exact extraction->install path VER-004's own evidence gap names:
    a pyproject.toml-only Python project (no requirements.txt) must have
    its REAL extracted dependency list passed, verbatim, into
    _ensure_project_venv - never a re-derived, re-filtered, or partial
    list."""
    _write(tmp_path, "pyproject.toml", (
        "[project]\nname = \"x\"\ndependencies = [\"django>=4.0\", \"psycopg2-binary\"]\n"
    ))
    v = PolymorphicValidator(str(tmp_path))
    assert v.stack == "python"

    with patch.object(v, "_ensure_project_venv", return_value=("/fake/venv/bin/python", None)) as mock_ensure:
        interpreter, error = v._resolve_python_interpreter()

    assert error is None
    assert interpreter == "/fake/venv/bin/python"
    mock_ensure.assert_called_once_with(["django>=4.0", "psycopg2-binary"])


def test_pyproject_with_no_dependencies_never_calls_ensure_project_venv(tmp_path):
    """An empty/absent [project.dependencies] array must not trigger a
    pointless venv-creation attempt - falls through to the bare default
    interpreter, matching _has_real_requirements' own 'not worth the cost'
    posture for the requirements.txt sibling case."""
    _write(tmp_path, "pyproject.toml", "[project]\nname = \"x\"\n")
    v = PolymorphicValidator(str(tmp_path))

    with patch.object(v, "_ensure_project_venv") as mock_ensure:
        interpreter, error = v._resolve_python_interpreter()

    mock_ensure.assert_not_called()
    assert error is None
    assert interpreter in (os.sys.executable, "python3")


def test_requirements_txt_takes_priority_over_pyproject_toml(tmp_path):
    """Documented, pre-existing priority order (unchanged) - a project with
    BOTH files installs from requirements.txt, never pyproject.toml,
    proving the wiring doesn't accidentally double-install or prefer the
    wrong source."""
    _write(tmp_path, "requirements.txt", "flask\n")
    _write(tmp_path, "pyproject.toml", (
        "[project]\nname = \"x\"\ndependencies = [\"django>=4.0\"]\n"
    ))
    v = PolymorphicValidator(str(tmp_path))

    with patch.object(v, "_ensure_project_venv", return_value=("/fake/venv/bin/python", None)) as mock_ensure:
        v._resolve_python_interpreter()

    called_args = mock_ensure.call_args[0][0]
    assert "-r" in called_args
    assert not any("django" in a for a in called_args)


def test_pyproject_install_failure_surfaces_as_real_error_not_silently_swallowed(tmp_path):
    """A genuinely failed pip install (e.g. a bad package pin) must be
    reported as a hard failure to the caller, never silently degraded to
    'no dependencies installed, proceed anyway' - matching this function's
    own documented contract ('install_error is set ONLY when pip install
    of THIS project's own declared dependencies genuinely failed... the
    caller should treat that as a hard failure')."""
    _write(tmp_path, "pyproject.toml", (
        "[project]\nname = \"x\"\ndependencies = [\"this-package-does-not-exist-xyz\"]\n"
    ))
    v = PolymorphicValidator(str(tmp_path))

    with patch.object(v, "_ensure_project_venv", return_value=(None, "pip install failed: no matching distribution")):
        interpreter, error = v._resolve_python_interpreter()

    assert error is not None and "pip install failed" in error
