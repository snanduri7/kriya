"""Pinned-image preflight: CI pulls every image certification requires by
digest (scripts/pinned_images.py --pull) and scripts/certify.sh verifies each
is present with exactly that digest (--verify): a missing or re-pointed
image fails certification, never skips it. The manifest cannot drift from
the tiers that use the images.
"""
import importlib.util
import re
import stat
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PINNED = re.compile(r"[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}")


def _module():
    spec = importlib.util.spec_from_file_location("pinned_images", ROOT / "scripts" / "pinned_images.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_manifest_names_every_pinned_image_a_certification_tier_uses():
    manifest = set(_module().pinned_images())
    used = set(PINNED.findall((ROOT / "tests" / "test_prd031a_semgrep_live.py").read_text()))
    assert used and used <= manifest
    assert "pinned_images.py --pull" in (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "pinned_images.py --verify" in (ROOT / "scripts" / "certify.sh").read_text()


def test_an_unpinned_manifest_entry_is_refused(tmp_path):
    manifest = tmp_path / "images.txt"
    manifest.write_text("# comment\nsemgrep/semgrep:latest\n")
    with pytest.raises(ValueError, match="not pinned by digest"):
        _module().pinned_images(str(manifest))


IMAGE = "example/tool@sha256:" + "a" * 64


def _fake_docker(tmp_path, monkeypatch, *, local_digests):
    """A docker CLI whose `image inspect` reports ``local_digests`` and whose
    `pull` records the call and then makes the image present."""
    state = tmp_path / "state"
    state.write_text(local_digests)
    log = tmp_path / "pulls"
    docker = tmp_path / "bin" / "docker"
    docker.parent.mkdir()
    docker.write_text(f"""#!/bin/sh
if [ "$1" = "pull" ]; then echo "$2" >> {log}; printf '["%s"]' "$2" > {state}; exit 0; fi
if [ "$1" = "image" ]; then cat {state}; [ -s {state} ] && [ "$(cat {state})" != "[]" ]; exit $?; fi
exit 1
""")
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{docker.parent}:/usr/bin:/bin")
    manifest = tmp_path / "images.txt"
    manifest.write_text(IMAGE + "\n")
    return str(manifest), log


def test_verify_passes_only_for_the_exact_digest_and_never_pulls(tmp_path, monkeypatch, capsys):
    module = _module()
    manifest, log = _fake_docker(tmp_path, monkeypatch, local_digests=f'["{IMAGE}"]')
    assert module.main(["--verify"], manifest) == 0
    repointed = "example/tool@sha256:" + "b" * 64
    (tmp_path / "state").write_text(f'["{repointed}"]')
    assert module.main(["--verify"], manifest) == 1
    assert f"MISSING: {IMAGE}" in capsys.readouterr().err
    assert not log.exists()


def test_pull_fetches_a_missing_image_by_digest_then_verifies(tmp_path, monkeypatch):
    module = _module()
    manifest, log = _fake_docker(tmp_path, monkeypatch, local_digests="[]")
    assert module.main(["--pull"], manifest) == 0
    assert log.read_text().split() == [IMAGE]


def test_no_docker_is_unavailable_and_bad_arguments_are_refused(tmp_path, monkeypatch):
    module = _module()
    monkeypatch.setenv("PATH", str(tmp_path))
    assert module.main(["--verify"]) == 2
    assert module.main(["--push"]) == 2
