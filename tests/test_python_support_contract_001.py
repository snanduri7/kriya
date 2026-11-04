"""PRD-034: Kriya's declared Python support is the Python it is measured to work on.

Measured (run 41785193323): ``kriya/control/history/acquire.py`` calls ``sqlite3.Connection.setconfig`` (Python 3.12+), and on 3.10/3.11 the plain jobs failed 108 and 96 tests.
The metadata that claimed ``>=3.10`` was false, so it is corrected to ``>=3.12`` rather than a fallback added for a range nothing requires. This file is the static control:
metadata rejects 3.10/3.11 (also through a real pip), every Python in CI is inside the claim, every supported minor is exercised, release-gating jobs install the exact lock, and
no document still claims the old range.
"""
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import yaml
from packaging.specifiers import SpecifierSet
from packaging.version import Version

ROOT = Path(__file__).resolve().parent.parent
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
SUPPORTED = ("3.12", "3.13", "3.14")


def test_the_declared_python_range_rejects_310_and_311_and_accepts_312_to_314():
    spec = SpecifierSet(PROJECT["requires-python"])
    assert PROJECT["requires-python"] == ">=3.12"
    for rejected in ("3.9.18", "3.10.21", "3.11.14"):
        assert Version(rejected) not in spec, rejected
    for accepted in ("3.12.0", "3.12.15", "3.13.1", "3.14.6"):
        assert Version(accepted) in spec, accepted


def test_the_tomli_backport_for_python_310_is_gone_and_no_dependency_is_conditional_on_an_unsupported_python():
    assert not [d for d in PROJECT["dependencies"] if d.startswith("tomli")]
    for dependency in PROJECT["dependencies"]:
        match = re.search(r"python_version\s*<\s*'?\"?(3\.\d+)", dependency)
        assert match is None or Version(match.group(1)) <= Version("3.12"), dependency   # a marker below the floor can never be true


def test_a_real_pip_refuses_to_install_kriya_on_311_and_accepts_312(tmp_path):
    """pip's own verdict on the metadata (``--python-version`` makes it evaluate Requires-Python for that interpreter); the project is built from a copy of pyproject.toml only."""
    project = tmp_path / "project"
    (project / "kriya").mkdir(parents=True)
    (project / "kriya" / "__init__.py").write_text("")
    (project / "README.md").write_text("x\n")
    (project / "pyproject.toml").write_text((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    def pip(version):
        return subprocess.run([sys.executable, "-m", "pip", "install", "--dry-run", "--no-deps", "--no-build-isolation", "--no-index", "--disable-pip-version-check",
                               "--python-version", version, "--target", str(tmp_path / f"t{version}"), str(project)], capture_output=True, text=True, check=False, timeout=300)
    for version in ("3.10", "3.11"):
        refused = pip(version)
        assert refused.returncode != 0 and "requires a different Python" in refused.stderr and "'>=3.12'" in refused.stderr, (version, refused.stderr[-400:])
    accepted = pip("3.12")
    assert accepted.returncode == 0, accepted.stderr[-600:]


def _matrix(job):
    return [str(v) for v in WORKFLOW["jobs"][job]["strategy"]["matrix"]["python-version"]]


def test_the_test_matrix_is_exactly_the_supported_pythons_and_every_ci_python_is_inside_the_claim():
    assert _matrix("test") == list(SUPPORTED)
    spec = SpecifierSet(PROJECT["requires-python"])
    seen = set()
    for job in WORKFLOW["jobs"].values():
        for step in job.get("steps", []):
            version = (step.get("with") or {}).get("python-version")
            if version and "matrix" not in str(version):
                seen.add(str(version))
    assert seen and all(Version(f"{version}.0") in spec for version in seen), seen


def test_release_gating_jobs_install_the_exact_lock_not_the_declared_ranges():
    for name in ("test", "lock-file", "static-check", "release-integrity"):
        script = "\n".join(step.get("run", "") for step in WORKFLOW["jobs"][name]["steps"])
        assert "pip install -r requirements.txt" in script, name
        assert ".[dev]" not in script, f"{name} floats to the declared ranges"
    assert "pip check" in "\n".join(step.get("run", "") for step in WORKFLOW["jobs"]["test"]["steps"])
    certification = "\n".join(step.get("run", "") for step in WORKFLOW["jobs"]["production-certification"]["steps"])
    assert "pip install -r requirements.txt" in certification and ".[dev]" not in certification
    windows = "\n".join(step.get("run", "") for step in WORKFLOW["jobs"]["platform-windows"]["steps"])
    assert ".[dev]" in windows, "the Windows import guard is the one declared-range job (the lock is POSIX-resolved, PLAT-036)"


def test_no_current_document_still_claims_python_310_support():
    for name in ("README.md", "CLAUDE.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert not re.search(r"Python 3\.10( or higher|\+)", text), name
        assert "3.12" in text, name


def test_the_lock_file_was_resolved_for_a_python_inside_the_claim():
    header = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()[1]
    match = re.search(r"with Python (\d+\.\d+)", header)
    assert match and Version(f"{match.group(1)}.0") in SpecifierSet(PROJECT["requires-python"]), header
