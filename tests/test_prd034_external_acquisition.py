"""PRD-034 (PRD034-EXTERNAL-ACQUISITION-PYPI-GRADLE-APT-001): the canonical run consumes FROZEN inputs - no live Maven/PyPI/Gradle registry and no apt-get inside it."""
import importlib.util
from pathlib import Path

import pytest
from _certification_mirror import PIP_ENV, is_package_manager_run, rewrite_profile

from kriya.tools.containment import ContainmentProfile, NetworkAuthority, TrustClass

ROOT = Path(__file__).resolve().parent.parent
MIRROR, HOST, INDEX = "http://host.docker.internal:18080/maven2", "host.docker.internal", "http://host.docker.internal:18090/simple"


def _profile(hosts=("repo.maven.apache.org", "pypi.org", "plugins.gradle.org"), **kwargs):
    return ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path="/w", network=NetworkAuthority.DEPENDENCY_REGISTRY_ONLY,
                              network_destinations=tuple(hosts), env_allowlist=["PATH"], **kwargs)


@pytest.mark.parametrize("command", [["mvn", "-B", "dependency:go-offline"], ["python3", "-m", "pip", "download", "-r", "requirements.txt"], ["gradle", "--no-daemon", "compileJava"],
                                     ["/bin/sh", "-c", "cd /w && mvn -B test"]])
def test_a_package_manager_acquisition_is_pointed_at_the_certification_content_and_every_public_registry_is_denied(command):
    rewritten = rewrite_profile(_profile(), command, mirror_url=MIRROR, mirror_host=HOST, pip_index=INDEX)
    assert rewritten.network_destinations == (HOST,), "the ACL is reduced to the certification host: a fallback to Central/PyPI/the Gradle hosts is DENIED"
    assert rewritten.registry_mirror == MIRROR and all(name in rewritten.env_allowlist for name in PIP_ENV) and rewritten.env_allowlist[0] == "PATH"


def test_without_a_pip_index_the_environment_is_not_touched():
    assert rewrite_profile(_profile(), ["mvn", "x"], mirror_url=MIRROR, mirror_host=HOST).env_allowlist == ["PATH"]


@pytest.mark.parametrize("command", [
    ["/bin/sh", "-c", 'printf "CONNECT repo.maven.apache.org:443 HTTP/1.1\\r\\n\\r\\n" | nc proxy 3128'],
    ["/bin/sh", "-c", "mvn --version >/dev/null 2>&1 ; printf 'CONNECT pypi.org:443 HTTP/1.1' | nc proxy 3128"],   # a package-manager trigger plus a proxy probe: still a probe
    ["/bin/sh", "-c", "curl -s https://example.com"],
])
def test_direct_proxy_probes_and_non_package_commands_are_left_alone(command):
    profile = _profile()
    assert rewrite_profile(profile, command, mirror_url=MIRROR, mirror_host=HOST, pip_index=INDEX) is profile
    assert not is_package_manager_run(command)


def test_a_profile_that_already_carries_its_own_mirror_is_left_alone():
    own = _profile(registry_mirror="https://repo.corp.example/maven2")
    assert rewrite_profile(own, ["mvn", "x"], mirror_url=MIRROR, mirror_host=HOST) is own


# --- apt: the product-built images are provisioned before the run and must not change during it ----------------------------------------------

def _images_module():
    spec = importlib.util.spec_from_file_location("provision_containment_images", ROOT / "scripts" / "provision_containment_images.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_check_fails_when_an_image_was_built_rebuilt_or_removed_during_the_run(tmp_path, monkeypatch, capsys):
    images = _images_module()
    record = tmp_path / "record.json"
    state = {"kriya-acq-proxy-base:1": "sha256:aaa", "kriya-acq-tools--maven-3.9-eclipse-temurin-17:1": "sha256:bbb"}
    monkeypatch.setattr(images, "_image_ids", lambda: dict(state))
    assert images.main(["x", "record", str(record)]) == 0 and images.main(["x", "check", str(record)]) == 0
    state["kriya-acq-tools--gradle-8-jdk21:1"] = "sha256:ccc"          # a live apt build inside the run
    assert images.main(["x", "check", str(record)]) == 1 and "BUILT/ADDED during the run" in capsys.readouterr().out
    del state["kriya-acq-tools--gradle-8-jdk21:1"]
    state["kriya-acq-proxy-base:1"] = "sha256:zzz"                      # rebuilt
    assert images.main(["x", "check", str(record)]) == 1 and "REBUILT during the run" in capsys.readouterr().out
    del state["kriya-acq-proxy-base:1"]
    assert images.main(["x", "check", str(record)]) == 1 and "disappeared" in capsys.readouterr().out


def test_the_provisioned_image_set_is_exactly_what_the_product_builds_for_those_bases():
    images = _images_module()
    from kriya.tools import containment_oci as oci

    tags = {oci._acquisition_image_tag(base) for base in images.BASES}  # pylint: disable=protected-access
    assert "kriya-acq-tools--maven-3.9-eclipse-temurin-17:1" in tags and len(tags) == len(images.BASES)


def test_certify_gates_the_run_on_frozen_inputs_and_audits_that_nothing_was_built_during_it():
    script = (ROOT / "scripts" / "certify.sh").read_text()
    for stage in ("maven_repository", "python_wheelhouse", "product_images"):
        assert script.index(f"stage {stage}") < script.index("stage collection")
    assert 'elif [ "$IMAGES_OK" != "1" ]; then' in script and "stage images_unchanged" in script
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert ci.index("provision_containment_images.py build") < ci.index("scripts/certify.sh certification-out/ci")
    assert not any(line.lstrip().startswith(("apt-get", "sudo apt", "apt ")) for line in script.splitlines()), "the canonical run itself performs no apt-get"
