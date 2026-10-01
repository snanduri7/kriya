"""Build hook (KRIYA-VERSION-001): embed the source identity into the package.

All project metadata stays in pyproject.toml. This only adds
``kriya/_build_info.json`` (git commit, git tree, dirty) to the built
artifacts, so an installed Kriya can report exactly which source produced it
without running git (kriya/build_info.py reads it):

- building from a git checkout (this directory is the checkout's top level):
  the identity is computed here, at build time;
- building a wheel from an sdist (no checkout): the identity the sdist
  carries is copied through unchanged;
- anything else: no file, and the installed Kriya reports UNKNOWN.

The file is generated only into the build output and the sdist tree, never
into the source checkout (it is git-ignored). Dirty means a tracked change
anywhere, or an untracked file inside a packaged directory.
KRIYA_BUILD_REQUIRE_CLEAN=1 (official and demo builds) refuses to build
unless the identity is known and clean.
"""
import json
import os
import subprocess

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist

ROOT = os.path.dirname(os.path.abspath(__file__))
BUILD_INFO = os.path.join("kriya", "_build_info.json")
PACKAGED_PATHS = ("kriya", "plugins/core_tools", "skills", "pyproject.toml", "setup.py", "MANIFEST.in")


def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def _source_identity():
    """The checkout's identity, or None when ROOT is not its own git top level
    (an sdist unpacked anywhere, even inside another repository)."""
    try:
        if os.path.realpath(_git("rev-parse", "--show-toplevel")) != os.path.realpath(ROOT):
            return None
        tracked = _git("status", "--porcelain=v1", "--untracked-files=no")
        untracked = _git("status", "--porcelain=v1", "--untracked-files=all", "--", *PACKAGED_PATHS)
        return {"schema": 1, "git_commit": _git("rev-parse", "HEAD"), "git_tree": _git("rev-parse", "HEAD^{tree}"),
                "dirty": bool(tracked) or any(line.startswith("??") for line in untracked.splitlines())}
    except (OSError, subprocess.CalledProcessError):
        return None


def _build_info():
    identity = _source_identity()
    if identity is None and os.path.isfile(os.path.join(ROOT, BUILD_INFO)):
        with open(os.path.join(ROOT, BUILD_INFO), encoding="utf-8") as handle:
            identity = json.load(handle)  # carried by the sdist this build started from
    if os.environ.get("KRIYA_BUILD_REQUIRE_CLEAN") == "1" and (identity is None or identity.get("dirty") is not False):
        raise SystemExit("KRIYA_BUILD_REQUIRE_CLEAN=1: refusing to build - the source identity is "
                         + ("unknown" if identity is None else "dirty") + " (build from a clean git checkout).")
    return identity


def _write(base_dir, identity):
    if identity is not None:
        with open(os.path.join(base_dir, BUILD_INFO), "w", encoding="utf-8") as handle:
            json.dump(identity, handle, sort_keys=True)
            handle.write("\n")


class BuildPyWithBuildInfo(build_py):
    def run(self):
        super().run()
        if not self.editable_mode:  # an editable install reads the source tree: it reports UNKNOWN
            _write(self.build_lib, _build_info())


class SdistWithBuildInfo(sdist):
    def make_release_tree(self, base_dir, files):
        super().make_release_tree(base_dir, files)
        _write(base_dir, _build_info())


setup(cmdclass={"build_py": BuildPyWithBuildInfo, "sdist": SdistWithBuildInfo})
