"""ResourceLimitPort contract (ARCH-PLATFORM-001, PLAT-RESOURCE-LIMITS-001).

The resource strategy stays Kriya policy (sandbox.resource_plan); the
provider applies limits and reports how strongly each is enforced. Evidence
never claims an enforced address-space limit where the host treats it as
advisory (PLAT-006), and a host without a provider refuses a requested
limit instead of running unbounded (PLAT-005).
"""
import sys
from unittest.mock import MagicMock, patch

import pytest

from kriya.platform import services
from kriya.platform.capabilities import CapabilityStatus, PlatformCapability, PlatformCapabilityUnavailable
from kriya.platform.posix_resource_limits import PosixRlimit
from kriya.tools.sandbox import ADDRESS_SPACE, JVM_HEAP, resource_plan


@pytest.mark.parametrize("platform_name,expected", [("darwin", CapabilityStatus.ADVISORY),
                                                     ("linux", CapabilityStatus.ENFORCED)])
def test_posix_provider_reports_cpu_enforced_and_address_space_per_host(platform_name, expected):
    with patch.object(sys, "platform", platform_name):
        reports = {r.capability: r.status for r in PosixRlimit().capabilities()}
        assert PosixRlimit().address_space_enforcement() is expected
    assert reports == {PlatformCapability.POSIX_RLIMIT_CPU: CapabilityStatus.ENFORCED,
                       PlatformCapability.POSIX_RLIMIT_AS: expected}


@pytest.mark.parametrize("platform_name,expected", [("darwin", "advisory"), ("linux", "enforced")])
def test_address_space_evidence_states_how_the_host_enforces_it(platform_name, expected):
    with services.override(services.compose(services.POSIX)), patch.object(sys, "platform", platform_name):
        plan = resource_plan(["python3", "-c", "pass"], 60, 512)
        assert plan.strategy == ADDRESS_SPACE
        evidence = plan.evidence()
    assert evidence["address_space_limit_mb"] == 512
    assert evidence["address_space_enforcement"] == expected


def test_a_jvm_plan_has_no_address_space_enforcement_claim():
    evidence = resource_plan(["mvn", "test"], 60, 1024).evidence()
    assert evidence["strategy"] == JVM_HEAP
    assert evidence["address_space_limit_mb"] is None and evidence["address_space_enforcement"] is None


def test_posix_provider_applies_exactly_the_requested_limits():
    fake = MagicMock(RLIMIT_CPU="RLIMIT_CPU", RLIMIT_AS="RLIMIT_AS")
    with patch.dict(sys.modules, {"resource": fake}):
        PosixRlimit().preexec_fn(30, None)()
    fake.setrlimit.assert_called_once_with("RLIMIT_CPU", (30, 30))


def test_a_host_without_a_provider_refuses_requested_limits_and_reports_unavailable():
    provider = services.compose(services.WINDOWS).resource_limits
    assert provider.address_space_enforcement() is CapabilityStatus.UNAVAILABLE
    assert {r.status for r in provider.capabilities()} == {CapabilityStatus.UNAVAILABLE}
    for cpu, memory in ((1, None), (None, 64)):
        with pytest.raises(PlatformCapabilityUnavailable):
            provider.preexec_fn(cpu, memory)
    assert provider.preexec_fn(None, None) is None
    with services.override(services.compose(services.WINDOWS)):
        assert resource_plan(["python3"], 60, 512).evidence()["address_space_enforcement"] == "unavailable"
