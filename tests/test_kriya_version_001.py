"""KRIYA-VERSION-001: an installed Kriya reports its package version and the
exact source it was built from, from identity embedded at build time - never
from git at run time, never invented when absent."""
import json
import os
import shutil
import subprocess
import sys
import sysconfig
from importlib import metadata

import pytest
from click.testing import CliRunner

from kriya import build_info
from kriya.cli import main

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMIT, TREE = "28fa21f4f0d42b7a979ff4cc1ba81cdd75d16a09", "bc6ffc113f0dac7e2fcf93111111280e4ea0aafc"


@pytest.fixture
def embedded(tmp_path, monkeypatch):
    """A build-info file as setup.py writes it, and git made unusable: any
    subprocess at all fails the test."""
    path = tmp_path / "_build_info.json"
    path.write_text(json.dumps({"schema": 1, "git_commit": COMMIT, "git_tree": TREE, "dirty": False}))
    monkeypatch.setattr(build_info, "BUILD_INFO_FILE", str(path))

    def no_subprocess(*_args, **_kwargs):
        raise AssertionError("KRIYA-VERSION-001: the version must never run a subprocess (git) at run time")
    for name in ("run", "Popen", "check_output", "call"):
        monkeypatch.setattr(subprocess, name, no_subprocess)
    return path


def _invoke(*args):
    result = CliRunner().invoke(main, list(args), catch_exceptions=False)
    return result.exit_code, result.stdout


def test_dash_dash_version_reports_the_embedded_identity_without_git(embedded):
    code, out = _invoke("--version")
    assert code == 0
    assert out == f"Kriya {metadata.version('kriya')} (commit 28fa21f, tree bc6ffc113f0d)\n"


def test_version_command_reports_the_full_identity(embedded):
    code, out = _invoke("version")
    assert code == 0
    assert out.splitlines()[0] == f"Kriya {metadata.version('kriya')} (commit 28fa21f, tree bc6ffc113f0d)"
    assert f"commit:           {COMMIT}" in out and f"tree:             {TREE}" in out


def test_version_json_is_exactly_the_identity_and_nothing_else(embedded):
    code, out = _invoke("version", "--json")
    assert code == 0
    report = json.loads(out)  # the whole of stdout parses: no prose or log lines mixed in
    assert report == {"product": "kriya", "version": metadata.version("kriya"), "commit": COMMIT, "tree": TREE,
                      "dirty": False, "build_provenance": "embedded", "python": report["python"],
                      "install_path": os.path.dirname(os.path.abspath(build_info.__file__))}
    assert report["python"] == sys.version.split()[0]


def test_the_package_version_is_the_installed_distribution_metadata():
    import kriya
    assert build_info.package_version() == metadata.version("kriya") == kriya.__version__


def test_a_dirty_build_is_reported_as_dirty(embedded):
    embedded.write_text(json.dumps({"schema": 1, "git_commit": COMMIT, "git_tree": TREE, "dirty": True}))
    assert _invoke("--version")[1] == f"Kriya {metadata.version('kriya')} (commit 28fa21f, tree bc6ffc113f0d, dirty)\n"
    assert json.loads(_invoke("version", "--json")[1])["dirty"] is True


@pytest.mark.parametrize(("content", "provenance"), [
    (None, "unavailable"),  # an editable checkout or unsupported install: no file at all
    ("not json", "invalid"),
    (json.dumps({"schema": 1, "git_commit": "HEAD", "git_tree": TREE, "dirty": False}), "invalid"),
    (json.dumps({"schema": 2, "git_commit": COMMIT, "git_tree": TREE, "dirty": False}), "invalid"),
    (json.dumps({"schema": 1, "git_commit": COMMIT, "git_tree": TREE, "dirty": "no"}), "invalid"),
])
def test_missing_or_malformed_provenance_is_unknown_never_invented(embedded, content, provenance):
    if content is None:
        embedded.unlink()
    else:
        embedded.write_text(content)
    code, out = _invoke("version", "--json")
    report = json.loads(out)
    assert code == 0
    assert (report["commit"], report["tree"], report["dirty"], report["build_provenance"]) == (
        "UNKNOWN", "UNKNOWN", None, provenance)
    assert _invoke("--version") == (0, f"Kriya {metadata.version('kriya')} (commit UNKNOWN, tree UNKNOWN; "
                                       f"build provenance {provenance})\n")


def test_version_needs_no_configuration(tmp_path, embedded, monkeypatch):
    """Any directory, even one whose kriya.yaml the config loader would refuse."""
    (tmp_path / "kriya.yaml").write_text("paths: {logs: ./logs}\n")  # a removed field: load_config() refuses it
    monkeypatch.chdir(tmp_path)
    assert _invoke("version", "--json")[0] == 0
    assert _invoke("--version")[0] == 0


def test_the_source_tree_never_carries_a_build_info_file():
    """Generated into build output only; a stale one in the checkout would
    make an editable install claim a revision it is not."""
    assert not os.path.exists(os.path.join(REPO, "kriya", "_build_info.json"))
    ignored = subprocess.run(["git", "check-ignore", "-q", "kriya/_build_info.json"], cwd=REPO, check=False)
    assert ignored.returncode == 0


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def test_an_installed_wheel_reports_the_exact_source_it_was_built_from(tmp_path):
    """The demo deployment model end to end: a clean git source -> sdist ->
    wheel built from that sdist (scripts/verify_release.sh's path) ->
    non-editable install -> the installed `kriya` run from a directory that
    is not a git repository reports that source's commit and tree."""
    source = tmp_path / "source"
    tracked = _git(REPO, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--",
                   "kriya", "plugins/core_tools", "skills").split("\0")
    for rel in [*filter(None, tracked), "pyproject.toml", "setup.py", "MANIFEST.in", "README.md", "requirements.txt"]:
        if os.path.isfile(os.path.join(REPO, rel)):
            os.makedirs(source / os.path.dirname(rel), exist_ok=True)
            shutil.copy2(os.path.join(REPO, rel), source / rel)
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.email=t@x", "-c", "user.name=t", "commit", "-qm", "source"]):
        _git(source, *args)
    commit, tree = _git(source, "rev-parse", "HEAD"), _git(source, "rev-parse", "HEAD^{tree}")
    dist, target, elsewhere = tmp_path / "dist", tmp_path / "installed", tmp_path / "not-a-repo"
    elsewhere.mkdir()
    env = {**os.environ, "KRIYA_BUILD_REQUIRE_CLEAN": "1"}
    subprocess.run([sys.executable, "-m", "build", "--no-isolation", "--outdir", str(dist), str(source)],
                   capture_output=True, check=True, env=env, timeout=600)
    wheel = next(dist.glob("*.whl"))
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--no-deps", "--no-index",
                    "--target", str(target), str(wheel)], capture_output=True, check=True, timeout=300)
    # -S: no site processing, so the dev checkout's editable-install finder is
    # not loaded; Kriya comes from the installed wheel, its dependencies from
    # this interpreter's site-packages.
    run_env = {"PATH": os.environ["PATH"], "PYTHONPATH": os.pathsep.join([str(target), sysconfig.get_paths()["purelib"]])}
    script = str(target / "bin" / "kriya")
    out = subprocess.run([sys.executable, "-S", script, "version", "--json"], cwd=elsewhere, env=run_env,
                         capture_output=True, text=True, check=True, timeout=120)
    report = json.loads(out.stdout)
    assert (report["commit"], report["tree"], report["dirty"], report["build_provenance"]) == (commit, tree, False,
                                                                                             "embedded")
    assert report["version"] == metadata.version("kriya")
    assert os.path.realpath(report["install_path"]) == os.path.realpath(target / "kriya")
    line = subprocess.run([sys.executable, "-S", script, "--version"], cwd=elsewhere, env=run_env,
                          capture_output=True, text=True, check=True, timeout=120).stdout
    assert line == f"Kriya {report['version']} (commit {commit[:7]}, tree {tree[:12]})\n"


def test_a_required_clean_build_refuses_a_dirty_source(tmp_path, monkeypatch):
    """KRIYA_BUILD_REQUIRE_CLEAN=1 (official/demo builds): a dirty or
    unknown source identity is a build failure, never a wheel."""
    import importlib.util

    import setuptools
    source = tmp_path / "source"
    source.mkdir()
    shutil.copy2(os.path.join(REPO, "setup.py"), source / "setup.py")
    (source / "kriya").mkdir()
    (source / "kriya" / "__init__.py").write_text("")
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.email=t@x", "-c", "user.name=t", "commit", "-qm", "source"]):
        _git(source, *args)
    spec = importlib.util.spec_from_file_location("kriya_setup_under_test", source / "setup.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(setuptools, "setup", lambda **_kwargs: None)  # load the hooks without running a build
    spec.loader.exec_module(module)
    clean = module._source_identity()
    assert clean["dirty"] is False and clean["git_commit"] == _git(source, "rev-parse", "HEAD")
    (source / "kriya" / "new_module.py").write_text("")  # untracked inside a packaged directory: packaged, so dirty
    assert module._source_identity()["dirty"] is True
    (source / "kriya" / "new_module.py").unlink()
    (source / "kriya" / "__init__.py").write_text("# changed\n")  # a tracked change
    assert module._source_identity()["dirty"] is True
    monkeypatch.setenv("KRIYA_BUILD_REQUIRE_CLEAN", "1")
    with pytest.raises(SystemExit, match="dirty"):
        module._build_info()
    monkeypatch.delenv("KRIYA_BUILD_REQUIRE_CLEAN")
    assert module._build_info()["dirty"] is True  # without the requirement a dirty build is labelled, not refused


def test_a_source_inside_another_repository_never_takes_that_repositorys_identity(tmp_path, monkeypatch):
    """An unpacked sdist may sit anywhere, including inside some unrelated
    git checkout: only a source that IS a checkout's top level has a git
    identity; otherwise the sdist's carried identity (or none) is used."""
    import importlib.util

    import setuptools
    outer = tmp_path / "unrelated-repo"
    nested = outer / "unpacked-sdist"
    nested.mkdir(parents=True)
    (outer / "README").write_text("x\n")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@x", "-c", "user.name=t", "commit", "-qm", "x"]):
        _git(outer, *args)
    shutil.copy2(os.path.join(REPO, "setup.py"), nested / "setup.py")
    spec = importlib.util.spec_from_file_location("kriya_setup_nested", nested / "setup.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(setuptools, "setup", lambda **_kwargs: None)
    spec.loader.exec_module(module)
    assert module._source_identity() is None
    assert module._build_info() is None  # no carried identity either: the installed Kriya will say UNKNOWN
    (nested / "kriya").mkdir()
    carried = {"schema": 1, "git_commit": COMMIT, "git_tree": TREE, "dirty": False}
    (nested / "kriya" / "_build_info.json").write_text(json.dumps(carried))
    assert module._build_info() == carried
