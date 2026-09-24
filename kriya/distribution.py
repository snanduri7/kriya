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
# Trees MANIFEST.in grafts whole; an sdist must carry every tracked file in them.
RELEASE_TREES = ("plugins/core_tools", "scripts", "skills", "tests")


def tracked_release_files(source_root: Path) -> list[str]:
    """Git-tracked files under RELEASE_TREES; raises when git cannot answer."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", *RELEASE_TREES],
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


def check_distribution(path: Path, source_root: Path | None = None) -> list[str]:
    """Return missing required files; corrupt/unsupported artifacts raise errors.

    A source export/sdist includes build and CI inputs. A wheel includes runtime
    files and installation metadata; Git history is deliberately not required.
    With ``source_root`` (a git checkout), an sdist must also contain every
    tracked file under RELEASE_TREES, so a narrow MANIFEST cannot silently drop
    fixtures or skill content. This checks completeness, not cryptographic
    authenticity or dependency health.
    """
    if source_root is not None and not path.name.endswith(".tar.gz"):
        raise ValueError("--source-root applies to a .tar.gz source distribution only")
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
        return missing
    if path.name.endswith(".tar.gz"):
        names = _sdist_names(path)
        missing = [name for name in REQUIRED_SOURCE_FILES if name not in names]
        if source_root is not None:
            missing.extend(name for name in tracked_release_files(source_root)
                           if name not in names and name not in missing)
        return missing
    raise ValueError("Expected source directory, .whl, or .tar.gz")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--source-root", type=Path, default=None,
                        help="git checkout the sdist was built from; require every tracked "
                             "file under the release trees")
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
