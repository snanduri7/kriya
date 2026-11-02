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


# --- PRD-034 hosted parity: tagged pins ---------------------------------------------------------------------------------------------------

TAGGED = "example/tool:1.2@sha256:" + "a" * 64


def _scripted_docker(monkeypatch, module, *, repo_digests, ids):
    """``docker image inspect <ref> --format <template>`` answered from ``repo_digests`` (digest reference -> list) and ``ids`` (reference -> Id)."""
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        reference, template = command[3], command[5]
        import subprocess

        if template == "{{json .RepoDigests}}":
            payload = repo_digests.get(reference)
        else:
            payload = ids.get(reference)
        if payload is None:
            return subprocess.CompletedProcess(command, 1, "", "No such image")
        return subprocess.CompletedProcess(command, 0, __import__("json").dumps(payload) if template != "{{.Id}}" else payload, "")
    monkeypatch.setattr(module.subprocess, "run", run)
    return calls


def test_a_tagged_pin_splits_into_the_digest_reference_and_the_tag():
    module = _module()
    assert module.split(TAGGED) == ("example/tool@sha256:" + "a" * 64, "1.2")
    assert module.split(IMAGE) == (IMAGE, None)
    assert module.split("registry.example:5000/team/tool:9@sha256:" + "c" * 64) == ("registry.example:5000/team/tool@sha256:" + "c" * 64, "9")
    assert module.split("registry.example:5000/team/tool@sha256:" + "c" * 64)[1] is None   # a registry port is not a tag


def test_a_tag_that_resolves_to_other_bytes_is_not_present(monkeypatch):
    module = _module()
    digest_reference = module.split(TAGGED)[0]
    _scripted_docker(monkeypatch, module, repo_digests={digest_reference: [digest_reference]},
                     ids={digest_reference: "sha256:one", "example/tool:1.2": "sha256:one"})
    assert module.present("docker", TAGGED) is True
    _scripted_docker(monkeypatch, module, repo_digests={digest_reference: [digest_reference]},
                     ids={digest_reference: "sha256:one", "example/tool:1.2": "sha256:ANOTHER"})
    assert module.present("docker", TAGGED) is False          # re-pointed tag: different bytes under the name the tests use
    _scripted_docker(monkeypatch, module, repo_digests={digest_reference: [digest_reference]}, ids={digest_reference: "sha256:one"})
    assert module.present("docker", TAGGED) is False          # the tag is absent


def test_every_image_a_docker_tier_skip_or_provisions_is_in_the_manifest_with_its_tag():
    """Drift guard: a test's skipif image, and every base image the Docker tiers run on, must be pinned (a cold runner has none of them)."""
    import sys

    sys.path.insert(0, str(ROOT / "tests"))
    try:
        import _real_oci
    finally:
        sys.path.remove(str(ROOT / "tests"))
    module = _module()
    tags = {tag: line for line in module.pinned_images() for _ref, tag in [module.split(line)] if tag}
    for image in (_real_oci.MAVEN_IMAGE, _real_oci.MCP_IMAGE, "maven:3.9-eclipse-temurin-8", "maven:3.9-eclipse-temurin-21", "gradle:8-jdk8",
                  "gradle:8-jdk17", "gradle:8-jdk21", "python:3.12-slim", "debian:bookworm-slim"):
        repository, _, tag = image.partition(":")
        assert any(line.startswith(f"{repository}:{tag}@sha256:") for line in module.pinned_images()), image
    assert tags and all(line in module.pinned_images() for line in tags.values())
