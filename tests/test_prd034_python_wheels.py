"""PRD-034 (PRD034-EXTERNAL-ACQUISITION-PYPI): the frozen Python wheelhouse - the release stage's clean install must not depend on live PyPI.

The release stage certifies PACKAGING. Its content authority is the hashed lock ``tests/certification/requirements.release.hashes.txt``; the install runs
``pip install --no-index --find-links WHEELHOUSE --require-hashes --no-deps -r HASHED`` in a fresh virtualenv (scripts/verify_release.sh). Negative controls with a real pip:
PyPI unreachable -> still passes; a required wheel removed -> deterministic failure; wheel bytes altered -> hash refusal.
"""
import hashlib
import importlib.util
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("certification_python_wheels", ROOT / "scripts" / "certification_python_wheels.py")
wheels = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wheels)


def _wheel(directory: Path, name: str = "kriyatest", version: str = "1.0", body: str = "VALUE = 1\n") -> Path:
    """A real, minimal pure-Python wheel (PEP 427) built without network or build tools."""
    dist = f"{name}-{version}"
    path = directory / f"{dist}-py3-none-any.whl"
    directory.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{name}.py", body)
        archive.writestr(f"{dist}.dist-info/METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
        archive.writestr(f"{dist}.dist-info/WHEEL", "Wheel-Version: 1.0\nGenerator: kriya-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr(f"{dist}.dist-info/RECORD", "")
    return path


def _hashed(tmp_path: Path, wheel: Path, name: str = "kriyatest", version: str = "1.0") -> Path:
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    path = tmp_path / "hashed.txt"
    path.write_text(f"{name}=={version} \\\n    --hash=sha256:{digest}\n")
    return path


# --- the hashed lock and wheelhouse verification ------------------------------------------------------------------------------------------

def test_the_shipped_hashed_lock_covers_exactly_the_locked_requirements_with_hashes():
    pins = wheels.parse_lock((ROOT / "requirements.txt").read_text())
    hashed = wheels.parse_hashed((ROOT / "tests" / "certification" / "requirements.release.hashes.txt").read_text())
    expected = {wheels.normalize(name): version for name, version in pins}
    assert expected == {name: version for name, (version, _hashes) in hashed.items() if name != "six"}
    assert all(hashes for _version, hashes in hashed.values()), "every pin carries the SHA-256 of the files PyPI published for it"
    assert "six" in hashed and hashed["six"][0] == "1.16.0", "the pin the contained pip acquisition test installs"


def test_verify_accepts_the_exact_wheelhouse_and_refuses_unknown_missing_or_altered_files(tmp_path):
    house = tmp_path / "house"
    wheel = _wheel(house)
    hashed = _hashed(tmp_path, wheel)
    assert wheels.verify(str(hashed), str(house)) == []
    unknown = _wheel(house, name="intruder")
    assert any("NOT IN THE HASHED LOCK" in p and "intruder" in p for p in wheels.verify(str(hashed), str(house)))
    unknown.unlink()
    wheel.write_bytes(wheel.read_bytes() + b"altered")
    assert any("NOT IN THE HASHED LOCK (or bytes differ)" in p for p in wheels.verify(str(hashed), str(house)))
    wheel.unlink()
    assert any(p == "no file for kriyatest==1.0" for p in wheels.verify(str(hashed), str(house)))


def test_the_wheelhouse_digest_is_derived_from_the_lock_and_the_files_only(tmp_path):
    house = tmp_path / "house"
    wheel = _wheel(house)
    hashed = _hashed(tmp_path, wheel)
    first = wheels.digest(str(hashed), str(house))
    assert first["wheelhouse_files"] == "1" and first == wheels.digest(str(hashed), str(house))
    wheel.write_bytes(wheel.read_bytes() + b"x")
    assert wheels.digest(str(hashed), str(house))["wheelhouse_sha256"] != first["wheelhouse_sha256"]


# --- the real install with the release stage's exact flags --------------------------------------------------------------------------------

def _install(venv: Path, house: Path, hashed: Path, env_extra=None):
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run([str(venv / "bin" / "python"), "-m", "pip", "install", "--no-index", "--find-links", str(house), "--require-hashes", "--no-deps", "--force-reinstall",
                           "--disable-pip-version-check", "-r", str(hashed)], capture_output=True, text=True, timeout=300, env=env, check=False)


@pytest.fixture(scope="module")
def fresh_venv(tmp_path_factory):
    venv = tmp_path_factory.mktemp("venv") / "v"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, capture_output=True, timeout=300)
    return venv


def test_the_install_passes_with_pypi_unreachable_fails_without_the_wheel_and_refuses_altered_bytes(tmp_path, fresh_venv):
    house = tmp_path / "house"
    wheel = _wheel(house)
    hashed = _hashed(tmp_path, wheel)
    original = wheel.read_bytes()
    dead_pypi = {"PIP_INDEX_URL": "http://127.0.0.1:9/simple", "HTTPS_PROXY": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9", "PIP_RETRIES": "0", "PIP_TIMEOUT": "2"}
    ok = _install(fresh_venv, house, hashed, dead_pypi)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert subprocess.run([str(fresh_venv / "bin" / "python"), "-c", "import kriyatest; print(kriyatest.VALUE)"], capture_output=True, text=True, check=False).stdout.strip() == "1"
    wheel.write_bytes(original + b"ALTERED")
    altered = _install(fresh_venv, house, hashed, dead_pypi)
    assert altered.returncode != 0 and "hash" in (altered.stdout + altered.stderr).lower() and "kriyatest" in (altered.stdout + altered.stderr), altered.stderr
    wheel.unlink()
    missing = _install(fresh_venv, house, hashed, dead_pypi)
    assert missing.returncode != 0 and ("No matching distribution" in missing.stderr or "Could not find a version" in missing.stderr), missing.stderr


# --- wiring ---------------------------------------------------------------------------------------------------------------------------------

def test_the_release_stage_uses_the_frozen_wheelhouse_with_no_index_and_hashes_only_when_certifying():
    release = (ROOT / "scripts" / "verify_release.sh").read_text()
    frozen = release.split('if [ -n "${KRIYA_RELEASE_WHEELHOUSE:-}" ]; then', 1)[1].split("else", 1)[0]
    assert "--no-index" in frozen and "--find-links" in frozen and "--require-hashes" in frozen and "--extra-index-url" not in release
    assert release.count("pip install -r requirements.txt") == 1, "the ordinary online install is unchanged when the variable is unset"
    certify = (ROOT / "scripts" / "certify.sh").read_text()
    assert certify.index("stage python_wheelhouse") < certify.index("stage collection")
    assert 'elif [ "$PY_WHEELS_OK" != "1" ]; then' in certify and 'stage release UNAVAILABLE "no frozen Python wheelhouse' in certify
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "certification_python_wheels.py provision tests/certification/requirements.release.hashes.txt" in ci


# --- the SAME wheelhouse as the PEP 503 index of the contained pip tests --------------------------------------------------------------------

import http.server  # noqa: E402
import threading  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402


class _Index:
    def __init__(self, tmp_path, house, hashed):
        self.log = tmp_path / "index.jsonl"
        parsed = wheels.parse_hashed(Path(hashed).read_text())
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), wheels._make_handler(parsed, str(house), wheels._Log(str(self.log), None)))  # pylint: disable=protected-access
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, b""

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def test_the_index_lists_only_hash_listed_files_with_their_sha256_and_refuses_anything_else(tmp_path):
    import json

    house = tmp_path / "house"
    wheel = _wheel(house)
    _wheel(house, name="intruder")                       # on disk, NOT in the hashed lock
    hashed = _hashed(tmp_path, wheel)
    index = _Index(tmp_path, house, hashed)
    try:
        status, page = index.get("/simple/kriyatest/")
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        assert status == 200 and f"/packages/{wheel.name}#sha256={digest}" in page.decode(), "pip verifies this fragment itself"
        assert index.get("/simple/intruder/")[0] == 404 and index.get("/packages/intruder-1.0-py3-none-any.whl")[0] == 404
        assert index.get("/simple/")[0] == 200 and b"intruder" not in index.get("/simple/")[1]
        assert index.get(f"/packages/{wheel.name}") == (200, wheel.read_bytes())
        wheel.write_bytes(wheel.read_bytes() + b"swapped after the start")
        assert index.get(f"/packages/{wheel.name}")[0] == 409, "bytes re-hashed at every request"
    finally:
        index.close()
    rows = [json.loads(line) for line in index.log.read_text().splitlines()]
    assert any(r["status"] == 409 and "BYTES DIFFER" in r["why"] for r in rows)


def test_pip_installs_from_the_frozen_index_and_never_from_pypi(tmp_path, fresh_venv):
    house = tmp_path / "house"
    wheel = _wheel(house, name="kriyaindexed")
    hashed = _hashed(tmp_path, wheel, name="kriyaindexed")
    index = _Index(tmp_path, house, hashed)
    try:
        env = {**os.environ, "PIP_INDEX_URL": index.base + "/simple/", "PIP_TRUSTED_HOST": "127.0.0.1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
        done = subprocess.run([str(fresh_venv / "bin" / "python"), "-m", "pip", "install", "--force-reinstall", "kriyaindexed"], capture_output=True, text=True, timeout=300, env=env, check=False)
        assert done.returncode == 0, done.stdout + done.stderr
        absent = subprocess.run([str(fresh_venv / "bin" / "python"), "-m", "pip", "install", "this-package-is-not-in-the-frozen-lock"], capture_output=True, text=True, timeout=300, env=env, check=False)
        assert absent.returncode != 0 and "No matching distribution" in absent.stderr, "an unlisted package is a deterministic failure, with no fallback to a public index"
    finally:
        index.close()
