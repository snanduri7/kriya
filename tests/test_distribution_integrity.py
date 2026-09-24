"""Release checks exercise artifact contents, not an editable installation."""
from pathlib import Path
from zipfile import ZipFile

import pytest

from kriya.distribution import REQUIRED_RUNTIME_FILES, check_distribution


def _wheel(path, missing=None):
    with ZipFile(path, 'w') as archive:
        for name in (*REQUIRED_RUNTIME_FILES, 'kriya-0.1.0.dist-info/METADATA',
                     'kriya-0.1.0.dist-info/entry_points.txt', 'kriya-0.1.0.dist-info/RECORD'):
            if name != missing:
                archive.writestr(name, 'fixture')


@pytest.mark.parametrize('missing', REQUIRED_RUNTIME_FILES)
def test_incomplete_wheel_fails(tmp_path, missing):
    wheel = tmp_path / 'kriya.whl'
    _wheel(wheel, missing)
    assert missing in check_distribution(wheel)


def test_complete_wheel_passes(tmp_path):
    wheel = tmp_path / 'kriya.whl'
    _wheel(wheel)
    assert check_distribution(wheel) == []


def test_wheel_requires_install_metadata(tmp_path):
    wheel = tmp_path / 'kriya.whl'
    _wheel(wheel, 'kriya-0.1.0.dist-info/METADATA')
    assert check_distribution(wheel)


@pytest.mark.parametrize('missing', ['pyproject.toml', 'requirements.txt',
                                    '.github/workflows/ci.yml', 'plugins/core_tools/__init__.py'])
def test_incomplete_source_export_fails(tmp_path, missing):
    from kriya.distribution import REQUIRED_SOURCE_FILES
    for name in REQUIRED_SOURCE_FILES:
        if name != missing:
            target = tmp_path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('fixture')
    assert missing in check_distribution(tmp_path)


def test_canonical_source_is_complete():
    assert check_distribution(Path(__file__).resolve().parents[1]) == []


@pytest.mark.parametrize('missing', [None, 'plugins/core_tools/__init__.py', 'pyproject.toml'])
def test_sdist_checks_contents_beneath_release_root(tmp_path, missing):
    import io
    import tarfile

    from kriya.distribution import REQUIRED_SOURCE_FILES
    archive_path = tmp_path / 'kriya.tar.gz'
    with tarfile.open(archive_path, 'w:gz') as archive:
        for name in REQUIRED_SOURCE_FILES:
            if name != missing:
                info = tarfile.TarInfo(f'kriya-0.1.0/{name}')
                info.size = 1
                archive.addfile(info, io.BytesIO(b'x'))
    assert check_distribution(archive_path) == ([missing] if missing else [])


def test_corrupt_wheel_cli_fails_with_json(tmp_path):
    import json
    import subprocess
    import sys
    wheel = tmp_path / 'broken.whl'
    wheel.write_bytes(b'not a zip')
    result = subprocess.run([sys.executable, '-m', 'kriya.distribution', str(wheel)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert json.loads(result.stdout)['passed'] is False


def _git_checkout(root, tracked):
    import subprocess
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    for name in tracked:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('fixture')
    subprocess.run(['git', 'add', '--', *tracked], cwd=root, check=True)


def _sdist_with(path, names):
    import io
    import tarfile

    from kriya.distribution import REQUIRED_SOURCE_FILES
    with tarfile.open(path, 'w:gz') as archive:
        for name in (*REQUIRED_SOURCE_FILES, *names):
            info = tarfile.TarInfo(f'kriya-0.1.0/{name}')
            info.size = 1
            archive.addfile(info, io.BytesIO(b'x'))


# Tracked non-.py release content the old per-extension MANIFEST dropped.
_TRACKED_DATA = ['tests/incidents/fixtures/case.json', 'skills/demo/rules.txt',
                 'skills/demo/Example.java']


@pytest.mark.parametrize('missing', _TRACKED_DATA)
def test_sdist_missing_tracked_release_file_fails(tmp_path, missing):
    repo = tmp_path / 'repo'
    _git_checkout(repo, _TRACKED_DATA)
    sdist = tmp_path / 'kriya.tar.gz'
    _sdist_with(sdist, [name for name in _TRACKED_DATA if name != missing])
    assert check_distribution(sdist, repo) == [missing]


def test_sdist_with_every_tracked_release_file_passes(tmp_path):
    repo = tmp_path / 'repo'
    _git_checkout(repo, [*_TRACKED_DATA, 'spikes/untracked_tree_is_ignored.txt'])
    sdist = tmp_path / 'kriya.tar.gz'
    _sdist_with(sdist, _TRACKED_DATA)
    assert check_distribution(sdist, repo) == []


def test_source_root_that_is_not_a_git_checkout_fails_closed(tmp_path):
    import json
    import subprocess
    import sys
    sdist = tmp_path / 'kriya.tar.gz'
    _sdist_with(sdist, [])
    not_git = tmp_path / 'plain'
    not_git.mkdir()
    result = subprocess.run([sys.executable, '-m', 'kriya.distribution', str(sdist),
                             '--source-root', str(not_git)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report['passed'] is False and 'git ls-files failed' in report['error']


def test_source_root_rejected_for_a_source_directory(tmp_path):
    with pytest.raises(ValueError, match='built .whl or .tar.gz'):
        check_distribution(tmp_path, tmp_path)


def test_sdist_with_an_untracked_file_in_a_release_tree_fails(tmp_path):
    # e.g. a runtime-generated auto-<repo> skill or OS clutter in the checkout.
    repo = tmp_path / 'repo'
    _git_checkout(repo, _TRACKED_DATA)
    sdist = tmp_path / 'kriya.tar.gz'
    _sdist_with(sdist, [*_TRACKED_DATA, 'skills/auto-scratch/skill.yaml'])
    assert check_distribution(sdist, repo) == ['untracked: skills/auto-scratch/skill.yaml']


_PLUGIN_FILES = [name for name in REQUIRED_RUNTIME_FILES if name.startswith('plugins/core_tools/')]
_SKILL_FILES = ['skills/demo/skill.yaml', 'skills/demo/rules.txt', 'skills/demo/examples/pom.xml']


def _wheel_with(path, extra):
    _wheel(path)
    with ZipFile(path, 'a') as archive:
        for name in extra:
            archive.writestr(name, 'fixture')


@pytest.mark.parametrize('missing', _SKILL_FILES)
def test_wheel_missing_a_tracked_skill_file_fails(tmp_path, missing):
    repo = tmp_path / 'repo'
    _git_checkout(repo, [*_PLUGIN_FILES, *_SKILL_FILES])
    wheel = tmp_path / 'kriya.whl'
    _wheel_with(wheel, [name for name in _SKILL_FILES if name != missing])
    assert check_distribution(wheel, repo) == [missing]


def test_wheel_with_exactly_the_tracked_skills_passes(tmp_path):
    repo = tmp_path / 'repo'
    _git_checkout(repo, [*_PLUGIN_FILES, *_SKILL_FILES, 'tests/not_in_wheel.py'])
    wheel = tmp_path / 'kriya.whl'
    _wheel_with(wheel, _SKILL_FILES)
    assert check_distribution(wheel, repo) == []


def test_wheel_with_untracked_skill_content_fails(tmp_path):
    repo = tmp_path / 'repo'
    _git_checkout(repo, [*_PLUGIN_FILES, *_SKILL_FILES])
    wheel = tmp_path / 'kriya.whl'
    _wheel_with(wheel, [*_SKILL_FILES, 'skills/demo/staged_rules.txt'])
    assert check_distribution(wheel, repo) == ['untracked: skills/demo/staged_rules.txt']


def test_wheel_package_data_patterns_cover_every_tracked_skill_file():
    """Without building: every git-tracked skills/ file matches one of the
    pyproject package-data shapes, so the wheel cannot silently drop one."""
    import re
    import subprocess
    from pathlib import PurePosixPath

    root = Path(__file__).resolve().parents[1]
    pyproject = (root / 'pyproject.toml').read_text()
    block = re.search(r'^"skills" = \[(.*?)^\]', pyproject, re.S | re.M).group(1)
    patterns = re.findall(r'"([^"]+)"', block)
    tracked = subprocess.run(['git', 'ls-files', '-z', '--', 'skills'], cwd=root,
                             capture_output=True, check=True).stdout.decode().split('\0')
    tracked = [name for name in tracked if name]
    assert tracked, 'no tracked skills found'
    uncovered = [
        name for name in tracked
        if not any(PurePosixPath(name).relative_to('skills').match(pattern)
                   and len(PurePosixPath(name).relative_to('skills').parts) == len(PurePosixPath(pattern).parts)
                   for pattern in patterns)
    ]
    assert uncovered == []


def test_manifest_grafts_every_release_tree():
    from kriya.distribution import RELEASE_TREES
    manifest = (Path(__file__).resolve().parents[1] / 'MANIFEST.in').read_text().splitlines()
    grafted = {line.split(None, 1)[1].strip() for line in manifest if line.startswith('graft ')}
    assert set(RELEASE_TREES) <= grafted
