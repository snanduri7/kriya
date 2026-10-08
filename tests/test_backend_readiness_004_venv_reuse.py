"""VENV-ADDITIVE-REUSE-001 (BACKEND-READINESS-004): CONFIRMED by a real-pip measurement
(~/kriya-m1-live/backend-readiness-004/defects/venv-additive-reuse/reproduce_real_partial_removal.json): the
project venv under .kriya/venv persists across attempts and pip never uninstalls, so a candidate that REMOVES a
declared dependency its code still imports passed the test gate while a fresh install failed.

Deterministic regression of the mechanism: a REAL venv is created by the real command; the pip install step is the
one faithful stand-in (it adds a module per specifier and never removes one - exactly pip's additive behaviour, which
the measurement confirmed) so the suite needs no network. The gate must rebuild the venv from nothing when the
declared set drops a specifier (pyproject list or requirements.txt line), and must NOT rebuild when the set only grows.
"""
import json
import os
import subprocess
import sys
from unittest.mock import patch

from kriya.config.config import AutonomyConfig
from kriya.tools import validate as v
from kriya.tools.validate import PolymorphicValidator


def _site_packages(venv_dir):
    lib = os.path.join(venv_dir, "lib")
    [pyver] = [d for d in os.listdir(lib) if d.startswith("python")]
    return os.path.join(lib, pyver, "site-packages")


def _faithful_runner(installs):
    """``python -m venv`` runs for real; ``pip install`` adds one module per specifier and removes nothing."""
    def run(self, cmd, cwd=None, timeout=None, **kwargs):
        if cmd[1:3] == ["-m", "venv"]:
            target = cmd[3] if os.path.isabs(cmd[3]) else os.path.join(cwd, cmd[3])
            subprocess.run([sys.executable, "-m", "venv", "--without-pip", target], check=True)
            return {"returncode": 0, "stdout": "", "stderr": ""}
        if cmd[1:4] == ["-m", "pip", "install"]:
            venv_dir = os.path.dirname(os.path.dirname(cmd[0]))
            venv_dir = venv_dir if os.path.isabs(venv_dir) else os.path.join(cwd, venv_dir)
            specifiers = [a for a in cmd[5:] if a not in ("-q", "pytest") and not a.startswith("-")]
            if "-r" in cmd:
                with open(os.path.join(cwd, cmd[cmd.index("-r") + 1])) as handle:
                    specifiers = [line.strip() for line in handle if line.strip() and not line.startswith("#")]
            for specifier in specifiers:
                open(os.path.join(_site_packages(venv_dir), f"{specifier.split('>=')[0]}.py"), "w").write("")
            installs.append(list(specifiers))
            return {"returncode": 0, "stdout": "", "stderr": ""}
        raise AssertionError(f"unexpected command {cmd}")
    return run


def _validator(root):
    return PolymorphicValidator(str(root), autonomy_cfg=AutonomyConfig(contained_execution_required=False))


def test_01_dropping_a_declared_dependency_rebuilds_the_venv_so_the_removed_module_is_gone(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname = "pkg"\nversion = "0"\ndependencies = ["six", "attrs"]\n')
    validator = _validator(root)
    installs = []
    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=_faithful_runner(installs)):
        python1, error = validator._resolve_python_interpreter()
        assert error is None and python1.startswith(str(root / ".kriya" / "venv"))
        site = _site_packages(str(root / ".kriya" / "venv"))
        assert os.path.exists(os.path.join(site, "six.py")) and os.path.exists(os.path.join(site, "attrs.py"))
        marker = json.load(open(os.path.join(root, ".kriya", "venv", v._VENV_INSTALLED_MARKER)))
        assert marker == {"specifiers": ["attrs", "six"]}
        first_inode = os.stat(root / ".kriya" / "venv").st_ino
        # the candidate drops `six`: the venv is rebuilt from nothing, six is gone, attrs reinstalled
        (root / "pyproject.toml").write_text('[project]\nname = "pkg"\nversion = "0"\ndependencies = ["attrs"]\n')
        python2, error = validator._resolve_python_interpreter()
        assert error is None and validator.venv_recreations == 1 and installs == [["six", "attrs"], ["attrs"]]
        assert not os.path.exists(os.path.join(site, "six.py")) and os.path.exists(os.path.join(site, "attrs.py"))
        assert os.stat(root / ".kriya" / "venv").st_ino != first_inode
        assert json.load(open(os.path.join(root, ".kriya", "venv", v._VENV_INSTALLED_MARKER))) == {"specifiers": ["attrs"]}
        # growing the set reuses the venv (no rebuild): additive is what pip does well
        (root / "pyproject.toml").write_text('[project]\nname = "pkg"\nversion = "0"\ndependencies = ["attrs", "cachetools"]\n')
        validator._resolve_python_interpreter()
        assert validator.venv_recreations == 1 and installs[-1] == ["attrs", "cachetools"]
        assert os.path.exists(os.path.join(site, "attrs.py")) and os.path.exists(os.path.join(site, "cachetools.py"))


def test_02_a_requirements_line_removal_is_a_removed_specifier_too(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "requirements.txt").write_text("six\nattrs  # pinned later\n")
    validator = _validator(root)
    installs = []
    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=_faithful_runner(installs)):
        validator._resolve_python_interpreter()
        site = _site_packages(str(root / ".kriya" / "venv"))
        assert os.path.exists(os.path.join(site, "six.py"))
        assert v._installed_specifiers(str(root / ".kriya" / "venv")) == ["attrs", "six"]
        (root / "requirements.txt").write_text("attrs\n")
        validator._resolve_python_interpreter()
        assert validator.venv_recreations == 1 and not os.path.exists(os.path.join(site, "six.py"))
    # Review F5: a venv without the marker (created before this mechanism, or whose marker could not be written)
    # has an UNKNOWN installed set and is rebuilt once - never trusted as-is.
    assert v._installed_specifiers(str(tmp_path / "nowhere")) is None
    os.remove(os.path.join(str(root / ".kriya" / "venv"), v._VENV_INSTALLED_MARKER))
    next_attempt = _validator(root)  # a new attempt gets a new validator (the per-validator install cache is gone)
    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=_faithful_runner(installs)):
        next_attempt._resolve_python_interpreter()
    assert next_attempt.venv_recreations == 1 and v._installed_specifiers(str(root / ".kriya" / "venv")) == ["attrs"]
    assert v._declared_specifiers(str(root), ["-r", "missing.txt"]) == ["-r:missing.txt:unreadable"]
