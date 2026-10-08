"""SEC-006: AutonomyConfig.acquisition_registry_hosts is the ONLY source
of destination authority for NetworkAuthority.DEPENDENCY_REGISTRY_ONLY -
deterministic coverage for its own normalization/validation, no Docker
required."""
import pytest
from pydantic import ValidationError

from kriya.config.config import AutonomyConfig


def test_default_hosts_are_empirically_required_only_no_wildcards():
    cfg = AutonomyConfig()
    # GRADLE-WRAPPER-CONTAINMENT-001: services.gradle.org (wrapper distributions) and plugins.gradle.org (the Plugin
    # Portal) were MEASURED as required by the first cohort's Gradle project (T3: UnknownHostException
    # services.gradle.org in the denied container; settings.gradle plugins block) and approved by the owner for the
    # acquisition phase only. GRADLE-PLUGIN-PORTAL-ACQUISITION-001 (owner decision 2026-10-08): plugins-artifacts.
    # gradle.org, the Portal's artifact host plugins.gradle.org/m2 redirects to - MEASURED 403 under the allowlist
    # (T3 foojay-resolver-0.8.0.pom); one exact official host, acquisition phase only.
    assert cfg.acquisition_registry_hosts == [
        "files.pythonhosted.org", "plugins-artifacts.gradle.org", "plugins.gradle.org", "pypi.org",
        "repo.maven.apache.org", "services.gradle.org",
    ]


def test_hosts_are_normalized_lowercase_deduped_sorted():
    cfg = AutonomyConfig(acquisition_registry_hosts=["PyPI.org", "pypi.org", "Repo.Maven.Apache.Org."])
    assert cfg.acquisition_registry_hosts == ["pypi.org", "repo.maven.apache.org"]


@pytest.mark.parametrize("bad_host", [".apache.org", "*.pypi.org", ".python.org"])
def test_wildcard_and_leading_dot_entries_are_rejected(bad_host):
    with pytest.raises(ValidationError, match="wildcard"):
        AutonomyConfig(acquisition_registry_hosts=[bad_host])


@pytest.mark.parametrize("bad_host", ["https://pypi.org", "pypi.org/simple", "http://pypi.org/"])
def test_url_shaped_entries_are_rejected(bad_host):
    with pytest.raises(ValidationError, match="URL"):
        AutonomyConfig(acquisition_registry_hosts=[bad_host])


def test_entries_with_a_port_are_rejected():
    with pytest.raises(ValidationError, match="port"):
        AutonomyConfig(acquisition_registry_hosts=["pypi.org:443"])


def test_empty_entries_are_rejected():
    with pytest.raises(ValidationError, match="empty"):
        AutonomyConfig(acquisition_registry_hosts=["pypi.org", "  "])


def test_explicit_private_registry_host_is_accepted_as_an_addition():
    """A private/internal registry needs an explicit additional entry -
    never inferred from repository content, but a legitimate config
    addition is not rejected."""
    cfg = AutonomyConfig(acquisition_registry_hosts=["pypi.org", "artifacts.internal.example.com"])
    assert "artifacts.internal.example.com" in cfg.acquisition_registry_hosts
