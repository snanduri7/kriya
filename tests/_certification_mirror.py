"""PRD-034: the pure rewrite the certification harness applies to a registry-scoped acquisition profile (tests/conftest.py::_certification_maven_mirror).

For a PACKAGE-MANAGER command (mvn, pip, gradle) the profile is pointed at the certification-owned frozen content and the proxy ACL is reduced to the certification host, so a fallback to
any public registry is denied (and logged by the product) instead of being silently carried:

* ``registry_mirror``  - the product's operator-owned Maven mirror (kriya/tools/containment_oci.py renders it into the trusted settings);
* ``env_allowlist``    - with a pip index, the standard ``PIP_INDEX_URL`` / ``PIP_TRUSTED_HOST`` / ``PIP_DISABLE_PIP_VERSION_CHECK`` names, carried into the acquisition container by the
  product's own env-allowlist mechanism (``build_restricted_env``).

Direct ``CONNECT`` probes (the tests that prove the exact-host ACL against the real registry host) address the real host on purpose and are returned unchanged, as is a profile that already
carries its own mirror and any command that is not a package manager.
"""
from __future__ import annotations

import dataclasses
import os
from typing import Any, Optional, Sequence

PACKAGE_MANAGERS = ("mvn", "pip", "gradle")
PIP_ENV = ("PIP_INDEX_URL", "PIP_TRUSTED_HOST", "PIP_DISABLE_PIP_VERSION_CHECK")


def is_package_manager_run(command: Sequence[Any]) -> bool:
    probes_proxy = any("CONNECT " in str(part) for part in command)  # `mvn --version ; CONNECT probe` is a proxy probe, not a package-manager run
    return any(tool in str(part) for part in command for tool in PACKAGE_MANAGERS) and not probes_proxy


def rewrite_profile(profile, command: Sequence[Any], *, mirror_url: str, mirror_host: str, pip_index: Optional[str] = None):
    if not is_package_manager_run(command) or profile.registry_mirror is not None:
        return profile
    changes: dict = {"network_destinations": (mirror_host,), "registry_mirror": mirror_url}
    if pip_index:
        changes["env_allowlist"] = [*profile.env_allowlist, *(name for name in PIP_ENV if name not in profile.env_allowlist)]
    return dataclasses.replace(profile, **changes)


def gradle_registry() -> str:
    """The ``repositories { ... }`` entry a Gradle fixture declares: the certification-owned frozen Maven-layout repository (Gradle's standard ``maven { url }``) when certifying, else
    ``mavenCentral()``. What those fixtures certify is the contained acquisition (and the cache/venv isolation around it), not the availability of Maven Central."""
    mirror = os.environ.get("KRIYA_CERT_MAVEN_MIRROR")
    return f"maven {{ url = uri('{mirror}'); allowInsecureProtocol = true }}" if mirror else "mavenCentral()"
