"""PRD-034 (D-type authority/contract correction, owner 2026-11-02): ``autonomy.acquisition_registry_mirror``, the default-off operator-owned Maven mirror.

A. UNSET: nothing about acquisition changes - the trusted setup script is BYTE-identical to the pre-change one (digest MEASURED against the commit before the seam);
B. AUTHORITY: the mirror's host must also be an authorized registry host (config AND the OCI backend, fail closed); the field is SEC-009 security authority, so a repository
   cannot introduce it and an approval is digest-bound to the exact value;
   and the value reaches a ROOT shell heredoc and XML, so its grammar is closed (refused, never escaped).
The real-container acquisition proof (C/D/E) is tests/test_prd034_registry_mirror_real.py.
"""
import contextlib
import hashlib
import os
import subprocess

import pytest
import yaml

from kriya.config import authority_approval as aa
from kriya.config.authority import _SECURITY_AUTHORITY_FIELDS, ConfigAuthorityError
from kriya.config.config import AppConfig, AutonomyConfig, load_config, resolve_config_state
from kriya.tools import containment_oci as oci
from kriya.tools.containment import BackendUnavailableError, ContainmentProfile, NetworkAuthority, TrustClass
from kriya.tools.validate import PolymorphicValidator

MIRROR = "http://host.docker.internal:18080/maven2"
HOSTS = ["host.docker.internal", "repo.maven.apache.org"]

# MEASURED 2026-11-02 against the commit BEFORE the seam existed (git show 97df908's containment_oci.py, same inputs).
PRE_CHANGE_SCRIPT_SHA256 = "29168f2a16d154e02a1e57d3f0f42960ba872264932ce5de0f0230ce7350346c"
PRE_CHANGE_AUTHORITY_ID = "c03193c18d29"


def _settings(script):
    """The settings.xml heredoc of a rendered setup script."""
    return script.split("cat > /usr/share/maven/conf/settings.xml <<EOF\n", 1)[1].split("\nEOF", 1)[0]


def _script(mirror=None):
    return oci._render_setup_script("172.20.0.2", 1000, 1000, mirror)  # pylint: disable=protected-access


# --- A. unset behaviour ----------------------------------------------------------------------------------------------------------------

def test_unset_the_trusted_setup_script_and_authority_identity_are_byte_identical_to_before_the_seam():
    assert hashlib.sha256(_script().encode()).hexdigest() == PRE_CHANGE_SCRIPT_SHA256
    assert hashlib.sha256(_script(None).encode()).hexdigest() == PRE_CHANGE_SCRIPT_SHA256
    assert oci.compute_authority_id(("pypi.org", "repo.maven.apache.org")) == PRE_CHANGE_AUTHORITY_ID
    assert oci._mirror_host_gateway_args(None) == []  # pylint: disable=protected-access
    assert "<mirror>" not in _script() and "<id>central</id>" not in _script() and "host-gateway" not in _script()


def test_unset_is_the_packaged_default_everywhere_the_value_travels(tmp_path):
    assert AppConfig().autonomy.acquisition_registry_mirror is None
    assert ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(tmp_path)).registry_mirror is None
    cache = tmp_path / "cache"
    cache.mkdir()
    profile, _ = PolymorphicValidator(str(tmp_path), autonomy_cfg=AppConfig().autonomy).build_containment_profile_and_backend(
        network=NetworkAuthority.DEPENDENCY_REGISTRY_ONLY, acquisition=True, dependency_cache_path=str(cache), dependency_cache_writable=True)
    assert profile.registry_mirror is None


def test_a_configured_mirror_reaches_only_a_registry_scoped_acquisition_profile(tmp_path):
    autonomy = AutonomyConfig(acquisition_registry_hosts=HOSTS, acquisition_registry_mirror=MIRROR)
    cache = tmp_path / "cache"
    cache.mkdir()
    acquisition, _ = PolymorphicValidator(str(tmp_path), autonomy_cfg=autonomy).build_containment_profile_and_backend(
        network=NetworkAuthority.DEPENDENCY_REGISTRY_ONLY, acquisition=True, dependency_cache_path=str(cache), dependency_cache_writable=True)
    assert acquisition.registry_mirror == MIRROR and "host.docker.internal" in acquisition.network_destinations
    execution, _ = PolymorphicValidator(str(tmp_path), autonomy_cfg=autonomy).build_containment_profile_and_backend(
        network=NetworkAuthority.DENIED, dependency_cache_path=str(cache))
    assert execution.registry_mirror is None, "the offline execution phase has no network and no mirror"


# --- the rendered mirror ---------------------------------------------------------------------------------------------------------------

def test_a_configured_mirror_renders_one_standard_maven_mirror_for_central_and_changes_nothing_else():
    script = _script(MIRROR)
    settings = _settings(script)
    assert settings.count("<mirror>") == 1 and settings.count("<mirrors>") == 1 and "<proxies>" in settings
    assert "<id>central</id>" in settings and "<mirrorOf>central</mirrorOf>" in settings and f"<url>{MIRROR}</url>" in settings
    assert "<mirrorOf>*</mirrorOf>" not in settings, "a pom's own <repository> must still resolve to the host it names (SEC-006)"
    assert "<mirror" not in _settings(_script()), "unset: the generated settings are the proxies block only"
    block = oci._render_mirrors_block(MIRROR)  # pylint: disable=protected-access
    assert script.replace(block, "") == _script(), "the mirror adds exactly one block to the generated settings"
    assert subprocess.run(["sh", "-n"], input=script, text=True, capture_output=True, check=False).returncode == 0, "still valid shell"


def test_the_mirror_id_is_central_because_the_offline_execution_phase_only_accepts_artifacts_cached_from_an_available_repository_id():
    """MEASURED 2026-11-02: with another id, every acquired artifact is 'cached from a remote repository ID that is unavailable in current build context' and the
    offline run fails MAVEN_ACQUISITION_INCOMPLETE. The real-container proof is test_prd034_registry_mirror_real.py::test_c_*."""
    assert "<id>central</id>" in _settings(_script(MIRROR)) and "kriya-registry-mirror" not in _script(MIRROR)


@pytest.mark.parametrize("mirror", [
    "http://host.docker.internal:18080/maven2/$(id)", "http://host.docker.internal/`id`", "http://host.docker.internal/a;b", "http://host.docker.internal/a b",
    "http://host.docker.internal/a\"b", "http://host.docker.internal/<x>", "http://host.docker.internal/a?x=1", "http://host.docker.internal/a#f",
    "http://user:pass@host.docker.internal/a", "ftp://host.docker.internal/a", "file:///tmp/repo", "host.docker.internal:18080", "",
    "http://host.docker.internal:99999/a", "http://host.docker.internal/../etc", "http://host.docker.internal/a\\b", "http://host.docker.internal/a\nb",
])
def test_the_mirror_grammar_is_closed_it_reaches_a_root_shell_and_xml_so_it_is_refused_never_escaped(mirror):
    with pytest.raises(ValueError):
        AutonomyConfig(acquisition_registry_hosts=HOSTS, acquisition_registry_mirror=mirror)
    if mirror:
        with pytest.raises(BackendUnavailableError):
            oci._render_mirrors_block(mirror)  # pylint: disable=protected-access


def test_the_mirror_value_is_normalised_and_the_gateway_flag_is_only_for_the_docker_host_name():
    assert AutonomyConfig(acquisition_registry_hosts=HOSTS, acquisition_registry_mirror="HTTP://Host.Docker.Internal:18080/maven2/").acquisition_registry_mirror == MIRROR
    assert oci._mirror_host_gateway_args(MIRROR) == ["--add-host", "host.docker.internal:host-gateway"]  # pylint: disable=protected-access
    assert oci._mirror_host_gateway_args("https://repo.corp.example/maven2") == []  # pylint: disable=protected-access
    assert oci.compute_authority_id(("a.example",), "https://a.example/m") != oci.compute_authority_id(("a.example",)), "the per-run authority identity covers the mirror"


# --- B. authority ----------------------------------------------------------------------------------------------------------------------

def test_b_the_mirror_host_must_also_be_an_authorized_registry_host_in_config():
    with pytest.raises(ValueError, match="not in autonomy.acquisition_registry_hosts"):
        AutonomyConfig(acquisition_registry_hosts=["repo.maven.apache.org"], acquisition_registry_mirror=MIRROR)
    assert AutonomyConfig(acquisition_registry_hosts=HOSTS, acquisition_registry_mirror=MIRROR).acquisition_registry_mirror == MIRROR


def test_b_the_oci_backend_refuses_a_profile_whose_mirror_host_is_not_authorized_before_it_creates_anything(tmp_path):
    profile = ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(tmp_path), network=NetworkAuthority.DEPENDENCY_REGISTRY_ONLY,
                                 network_destinations=("repo.maven.apache.org",), registry_mirror=MIRROR)
    with pytest.raises(BackendUnavailableError, match="not in network_destinations"):
        oci.OCIContainmentBackend()._prepare_registry_scoped("docker-that-must-not-run", profile, ["mvn"])  # pylint: disable=protected-access


def test_b_the_field_is_security_authority_and_every_surface_that_lists_registry_authority_lists_it():
    assert ("autonomy", "acquisition_registry_mirror") in _SECURITY_AUTHORITY_FIELDS
    from kriya.workflow.resume_fingerprints import CONFIG_FIELD_OWNERS

    assert CONFIG_FIELD_OWNERS[("autonomy", "acquisition_registry_mirror")] == "containment"


@contextlib.contextmanager
def _cwd(path):
    old = os.getcwd()
    os.makedirs(path, exist_ok=True)
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def _write_yaml(path, data):
    with open(path, "w", encoding="utf-8") as handle:
        yaml.dump(data, handle)


def _approve():
    """What ``kriya authority approve --confirm`` does (tests/test_sec009_p2_authority_approval.py): resolve fresh, build, save."""
    state = resolve_config_state()
    artifact = aa.build_approval_artifact(state.violations, state.config_dict, state.workspace_root)
    aa.save_approval_artifact(aa.default_local_approval_path(state.workspace_root), artifact)


def _config(mirror):
    return {"autonomy": {"acquisition_registry_hosts": HOSTS, "acquisition_registry_mirror": mirror}}


def test_b_a_repository_cannot_introduce_a_mirror_an_exact_approval_accepts_it_and_a_changed_value_needs_a_new_approval(tmp_path):
    with _cwd(tmp_path / "ws"):
        _write_yaml("kriya.yaml", _config(MIRROR))
        with pytest.raises(ConfigAuthorityError, match="acquisition_registry_mirror"):
            load_config()
        _approve()
        assert load_config().autonomy.acquisition_registry_mirror == MIRROR
        _write_yaml("kriya.yaml", _config("http://host.docker.internal:18081/maven2"))  # same host, other port: a different artifact source
        with pytest.raises(ConfigAuthorityError, match="acquisition_registry_mirror"):
            load_config()


def test_b_an_approval_does_not_make_an_unauthorized_host_valid(tmp_path):
    with _cwd(tmp_path / "ws"):
        _write_yaml("kriya.yaml", {"autonomy": {"acquisition_registry_hosts": ["repo.maven.apache.org"], "acquisition_registry_mirror": MIRROR}})
        with pytest.raises(Exception, match="not in autonomy.acquisition_registry_hosts|acquisition_registry"):
            _approve()
            load_config()
