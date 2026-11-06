"""STATE-DIR-RESOLUTION-DIVERGES-001 / OI-02: ONE authority chooses the state location; callers transport/use it and never recompute a fallback.

Measured defect (2026-11-06 reconciliation): ``kriya/core/state_paths.py`` refused a relative ``KRIYA_STATE_DIR`` and expanded ``~``, but
``tools/validate.py`` (gate view root + git-metadata export) and ``workflow/acceptance_jvm.py`` resolved the same variable against the process
working directory, and ``core/model_runtime.py`` did not expand ``~`` - so one run could place state in several directories.
"""
import ast
import os
from pathlib import Path

import pytest

from kriya.config import AppConfig
from kriya.core import model_runtime
from kriya.core import state_paths as sp
from kriya.core.state_paths import StateDirectoryError, ambient_state_directory, resolve_state_directory
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow import acceptance_jvm

PACKAGE = Path(sp.__file__).resolve().parents[1]
OWNERS = {"kriya/core/state_paths.py", "kriya/config/environment.py"}   # the authority and the module that reads the raw environment
FORBIDDEN_NAMES = {"environment_state_dir", "default_state_directory", "default_state_dir"}


def _callers():
    """Every state-location consumer that has no AppConfig, as (name, callable returning the directory it uses)."""
    return {
        "validate._state_root": lambda: PolymorphicValidator._state_root(),  # noqa: SLF001
        "acceptance_jvm._state_root": lambda: acceptance_jvm._state_root(),  # noqa: SLF001
        "model_runtime.fingerprint_store_dir": lambda: os.path.dirname(model_runtime.fingerprint_store_dir()),
        "ambient": lambda: ambient_state_directory(),
    }


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("KRIYA_STATE_DIR", raising=False)
    return tmp_path / "home"


@pytest.mark.parametrize("name", list(_callers()))
def test_a_relative_state_directory_is_refused_by_every_caller_never_resolved_against_the_cwd(home, monkeypatch, tmp_path, name):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KRIYA_STATE_DIR", "relative/state")
    with pytest.raises(StateDirectoryError):
        _callers()[name]()
    assert not (tmp_path / "relative").exists()


@pytest.mark.parametrize("name", list(_callers()))
def test_a_home_relative_state_directory_expands_the_same_way_everywhere(home, monkeypatch, name):
    monkeypatch.setenv("KRIYA_STATE_DIR", "~/kriya-state")
    assert _callers()[name]() == os.path.realpath(home / "kriya-state")


@pytest.mark.parametrize("env", [None, "abs"])
def test_every_caller_agrees_with_the_configured_resolver(home, monkeypatch, tmp_path, env):
    if env:
        monkeypatch.setenv("KRIYA_STATE_DIR", str(tmp_path / "state-abs"))
    expected = resolve_state_directory(AppConfig())[0]
    assert {name: call() for name, call in _callers().items()} == {name: expected for name in _callers()}


def test_the_default_location_is_the_same_for_everyone(home):
    assert {call() for call in _callers().values()} == {os.path.realpath(home / ".kriya" / "state")}


def test_a_configured_paths_state_is_still_honoured_where_a_configuration_is_in_scope(home, tmp_path):
    cfg = AppConfig()
    cfg.paths.state = str(tmp_path / "configured")
    assert resolve_state_directory(cfg) == (os.path.realpath(tmp_path / "configured"), "config")
    assert model_runtime.fingerprint_store_dir(cfg) == os.path.join(os.path.realpath(tmp_path / "configured"), "model_runtimes")


def test_tripwire_no_module_other_than_the_authority_recomputes_the_environment_or_default_location():
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        relative = path.relative_to(PACKAGE.parent).as_posix()
        if relative in OWNERS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom):
                names = [(alias.name, node.lineno) for alias in node.names]
            elif isinstance(node, ast.Name):
                names = [(node.id, node.lineno)]
            elif isinstance(node, ast.Attribute):
                names = [(node.attr, node.lineno)]
            offenders += [f"{relative}:{line} {name}" for name, line in names if name in FORBIDDEN_NAMES]
            if isinstance(node, ast.Constant) and node.value == "KRIYA_STATE_DIR" and relative != "kriya/core/state_paths.py":
                # the NAME of the variable may be shown in text; reading it is what is forbidden - only an os.environ read counts
                pass
    assert offenders == [], offenders


def test_mutation_a_caller_that_recomputes_the_location_is_detected(tmp_path):
    source = "from kriya.config.environment import state_dir as environment_state_dir\nroot = environment_state_dir() or '/x'\n"
    tree = ast.parse(source)
    hits = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and any(a.name in FORBIDDEN_NAMES or a.name == "state_dir" for a in n.names)]
    assert hits, "the tripwire would not see a re-introduced independent computation"


def test_mutation_restoring_the_independent_computations_brings_the_divergence_back(home, monkeypatch, tmp_path):
    """The OLD validator/jvm/model_runtime formulas, evaluated for a relative and a ~ value, disagree with the authority."""
    from kriya.config.environment import expand_user, state_dir

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KRIYA_STATE_DIR", "relative/state")
    old_validate = os.path.realpath(expand_user(state_dir() or sp.default_state_directory()))
    assert old_validate == os.path.realpath(tmp_path / "relative" / "state"), "the old formula resolved against the cwd"
    with pytest.raises(StateDirectoryError):
        ambient_state_directory()
    monkeypatch.setenv("KRIYA_STATE_DIR", "~/s")
    assert os.path.realpath(state_dir() or "") != os.path.realpath(home / "s"), "the old model_runtime formula did not expand ~"
