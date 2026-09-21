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
