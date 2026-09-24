"""Deterministic release-content preflight; never imports or extracts an artifact."""

import argparse
import json
import subprocess
import tarfile
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile

REQUIRED_RUNTIME_FILES = (
    "kriya/__init__.py",
    "kriya/cli.py",
    "kriya/config/default_config.yaml",
    "plugins/core_tools/__init__.py",
    "plugins/core_tools/validation_tool.py",
)
REQUIRED_SOURCE_FILES = (*REQUIRED_RUNTIME_FILES, "pyproject.toml", "requirements.txt",
                         "README.md", ".github/workflows/ci.yml", "MANIFEST.in",
                         "scripts/release_smoke.py", "scripts/verify_release.sh")
# Trees MANIFEST.in grafts whole; an sdist must carry exactly the tracked files in them.
RELEASE_TREES = ("plugins/core_tools", "scripts", "skills", "tests")
# Trees installed into site-packages by the wheel (runtime plugin + bundled skills).
WHEEL_TREES = ("plugins/core_tools", "skills")
UNTRACKED_PREFIX = "untracked: "


def tracked_release_files(source_root: Path, trees: tuple[str, ...] = RELEASE_TREES) -> list[str]:
    """Git-tracked files under ``trees``; raises when git cannot answer."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", *trees],
        cwd=source_root, capture_output=True, timeout=60,
    )
    if result.returncode != 0:
        raise ValueError(f"git ls-files failed in {source_root}: "
                         f"{result.stderr.decode(errors='replace').strip()}")
    return sorted(name for name in result.stdout.decode().split("\0") if name)


def _sdist_names(path: Path) -> set[str]:
    with tarfile.open(path, "r:gz") as archive:
        members = [PurePosixPath(item.name) for item in archive.getmembers() if item.isfile()]
    roots = {item.parts[0] for item in members}
    if len(roots) != 1:
        raise ValueError("Source distribution must have exactly one root directory")
    return {str(PurePosixPath(*item.parts[1:])) for item in members}


def _tree_mismatch(names: set[str], tracked: list[str], trees: tuple[str, ...]) -> list[str]:
    """Tracked files the artifact lacks, then artifact files git does not track."""
    missing = [name for name in tracked if name not in names]
    tracked_set = set(tracked)
    unexpected = sorted(
        name for name in names
        if any(name.startswith(tree + "/") for tree in trees) and name not in tracked_set
    )
    return missing + [UNTRACKED_PREFIX + name for name in unexpected]


def check_distribution(path: Path, source_root: Path | None = None) -> list[str]:
    """Return release problems; corrupt/unsupported artifacts raise errors.

    A source export/sdist includes build and CI inputs. A wheel includes runtime
    files, the bundled skill library and installation metadata; Git history is
    deliberately not required. With ``source_root`` (a git checkout), the
    artifact's release trees (RELEASE_TREES for an sdist, WHEEL_TREES for a
    wheel) must hold exactly the git-tracked files: a narrow MANIFEST or
    package-data pattern cannot silently drop fixtures or skill content, and
    untracked working-tree files (a runtime-generated skill, OS clutter) cannot
    ship. A missing file is reported by path; an unexpected one as
    ``untracked: <path>``. This checks completeness, not cryptographic
    authenticity or dependency health.
    """
    if source_root is not None and path.is_dir():
        raise ValueError("--source-root applies to a built .whl or .tar.gz artifact only")
    if path.is_dir():
        return [name for name in REQUIRED_SOURCE_FILES if not (path / name).is_file()]
    if path.suffix == ".whl":
        with ZipFile(path) as archive:
            names = {item.filename for item in archive.infolist() if not item.is_dir()}
        missing = [name for name in REQUIRED_RUNTIME_FILES if name not in names]
        metadata_roots = {str(PurePosixPath(name).parent) for name in names
                          if name.endswith(".dist-info/METADATA")
                          and PurePosixPath(name).parent.name.startswith("kriya-")}
        if len(metadata_roots) != 1:
            missing.append("one kriya-*.dist-info/METADATA")
        else:
            root = metadata_roots.pop()
            missing.extend(f"{root}/{name}" for name in ("entry_points.txt", "RECORD")
                           if f"{root}/{name}" not in names)
        if source_root is not None:
            missing.extend(name for name in _tree_mismatch(
                names, tracked_release_files(source_root, WHEEL_TREES), WHEEL_TREES,
            ) if name not in missing)
        return missing
    if path.name.endswith(".tar.gz"):
        names = _sdist_names(path)
        missing = [name for name in REQUIRED_SOURCE_FILES if name not in names]
        if source_root is not None:
            missing.extend(name for name in _tree_mismatch(
                names, tracked_release_files(source_root), RELEASE_TREES,
            ) if name not in missing)
        return missing
    raise ValueError("Expected source directory, .whl, or .tar.gz")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--source-root", type=Path, default=None,
                        help="git checkout the artifact was built from; its release trees "
                             "must hold exactly the git-tracked files")
    args = parser.parse_args()
    try:
        missing = check_distribution(args.artifact, args.source_root)
        report = {"artifact": str(args.artifact), "passed": not missing, "missing": missing}
    except (OSError, ValueError, tarfile.TarError, BadZipFile, subprocess.SubprocessError) as error:
        report = {"artifact": str(args.artifact), "passed": False, "error": str(error)}
    print(json.dumps(report, sort_keys=True))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
