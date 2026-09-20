import sys

import pytest
from unittest.mock import MagicMock, patch

from kriya.tools.sandbox import build_restricted_env, posix_resource_limits_preexec_fn


def test_build_restricted_env_strips_non_allowlisted_vars(monkeypatch):
    monkeypatch.setenv("KRIYA_TEST_SECRET", "super-secret-value")
    monkeypatch.setenv("HOME", "/home/tester")

    env = build_restricted_env(allowlist=["HOME"])

    assert "KRIYA_TEST_SECRET" not in env
    assert env["HOME"] == "/home/tester"
    assert "PATH" in env


def test_build_restricted_env_omits_missing_allowlist_entries(monkeypatch):
    monkeypatch.delenv("JAVA_HOME", raising=False)

    env = build_restricted_env(allowlist=["JAVA_HOME"])

    assert "JAVA_HOME" not in env


def test_posix_resource_limits_preexec_fn_none_on_windows():
    with patch.object(sys, "platform", "win32"):
        fn = posix_resource_limits_preexec_fn(cpu_seconds=60, memory_mb=512)
    assert fn is None


def test_posix_resource_limits_preexec_fn_sets_expected_limits():
    fn = posix_resource_limits_preexec_fn(cpu_seconds=60, memory_mb=512)
    assert fn is not None

    mock_resource = MagicMock()
    mock_resource.RLIMIT_CPU = "RLIMIT_CPU"
    mock_resource.RLIMIT_AS = "RLIMIT_AS"

    with patch.dict(sys.modules, {"resource": mock_resource}):
        fn()

    mock_resource.setrlimit.assert_any_call("RLIMIT_CPU", (60, 60))
    mock_resource.setrlimit.assert_any_call("RLIMIT_AS", (512 * 1024 * 1024, 512 * 1024 * 1024))


def test_posix_resource_limits_preexec_fn_fails_closed_on_cpu_limit_failure():
    """SEC-001 fail-closed correction (2026-09-11): the prior version of
    this test asserted "should not raise" for ANY setrlimit failure - that
    encoded the exact vulnerability this fix closes (a subprocess starting
    completely unbounded whenever limit application silently failed).
    RLIMIT_CPU is reliably settable on both Linux and macOS, so a failure
    here is a genuine, actionable setup failure and must propagate."""
    fn = posix_resource_limits_preexec_fn(cpu_seconds=60, memory_mb=512)

    mock_resource = MagicMock()
    mock_resource.RLIMIT_CPU = "RLIMIT_CPU"
    mock_resource.RLIMIT_AS = "RLIMIT_AS"
    mock_resource.setrlimit.side_effect = OSError("not permitted")

    with patch.dict(sys.modules, {"resource": mock_resource}):
        with pytest.raises(OSError, match="not permitted"):
            fn()


def test_posix_resource_limits_preexec_fn_fails_closed_on_linux_memory_limit_failure():
    """On Linux, RLIMIT_AS is reliably settable - a failure there is just
    as actionable as an RLIMIT_CPU failure and must also fail closed."""
    fn = posix_resource_limits_preexec_fn(cpu_seconds=60, memory_mb=512)

    def setrlimit_side_effect(which, _limits):
        if which == "RLIMIT_AS":
            raise OSError("not permitted")

    mock_resource = MagicMock()
    mock_resource.RLIMIT_CPU = "RLIMIT_CPU"
    mock_resource.RLIMIT_AS = "RLIMIT_AS"
    mock_resource.setrlimit.side_effect = setrlimit_side_effect

    with patch.dict(sys.modules, {"resource": mock_resource}):
        with patch.object(sys, "platform", "linux"):
            with pytest.raises(OSError, match="not permitted"):
                fn()


def test_posix_resource_limits_preexec_fn_tolerates_macos_memory_limit_failure():
    """Real, empirically-confirmed macOS/XNU platform limitation (2026-09-11,
    macOS 26.6.2/arm64): `setrlimit(RLIMIT_AS, ...)` itself can fail with
    "current limit exceeds maximum limit" even for an ordinary, well-formed
    value - not merely be weakly enforced once set. Failing closed on this
    specific case would break every real macOS caller out of the box, the
    opposite of this fix's own intent - matching the design's own explicit
    "memory containment stays advisory-only on macOS" conclusion. RLIMIT_CPU
    failing on macOS is NOT given this exemption - only RLIMIT_AS is."""
    fn = posix_resource_limits_preexec_fn(cpu_seconds=60, memory_mb=512)

    def setrlimit_side_effect(which, _limits):
        if which == "RLIMIT_AS":
            raise ValueError("current limit exceeds maximum limit")

    mock_resource = MagicMock()
    mock_resource.RLIMIT_CPU = "RLIMIT_CPU"
    mock_resource.RLIMIT_AS = "RLIMIT_AS"
    mock_resource.setrlimit.side_effect = setrlimit_side_effect

    with patch.dict(sys.modules, {"resource": mock_resource}):
        with patch.object(sys, "platform", "darwin"):
            fn()  # should not raise - macOS-specific, RLIMIT_AS-specific tolerance


def test_posix_resource_limits_preexec_fn_still_fails_closed_on_macos_cpu_limit_failure():
    """The macOS exemption above is scoped to RLIMIT_AS only - an RLIMIT_CPU
    failure on macOS is just as actionable as anywhere else and must still
    fail closed."""
    fn = posix_resource_limits_preexec_fn(cpu_seconds=60, memory_mb=512)

    mock_resource = MagicMock()
    mock_resource.RLIMIT_CPU = "RLIMIT_CPU"
    mock_resource.RLIMIT_AS = "RLIMIT_AS"
    mock_resource.setrlimit.side_effect = OSError("not permitted")

    with patch.dict(sys.modules, {"resource": mock_resource}):
        with patch.object(sys, "platform", "darwin"):
            with pytest.raises(OSError, match="not permitted"):
                fn()


def test_posix_resource_limits_preexec_fn_cpu_only_never_touches_rlimit_as():
    """A caller that wants only a CPU bound must pass memory_mb=None, not 0 -
    0 would literally mean setrlimit(RLIMIT_AS, (0, 0)), which makes the
    process unable to allocate memory at all rather than 'unlimited'. This
    was a live trap in NullContainmentBackend.prepare before this fix (it
    coalesced an unset field to 0 instead of leaving it untouched)."""
    fn = posix_resource_limits_preexec_fn(cpu_seconds=60, memory_mb=None)

    mock_resource = MagicMock()
    mock_resource.RLIMIT_CPU = "RLIMIT_CPU"
    mock_resource.RLIMIT_AS = "RLIMIT_AS"

    with patch.dict(sys.modules, {"resource": mock_resource}):
        fn()

    mock_resource.setrlimit.assert_called_once_with("RLIMIT_CPU", (60, 60))


def test_posix_resource_limits_preexec_fn_memory_only_never_touches_rlimit_cpu():
    fn = posix_resource_limits_preexec_fn(cpu_seconds=None, memory_mb=256)

    mock_resource = MagicMock()
    mock_resource.RLIMIT_CPU = "RLIMIT_CPU"
    mock_resource.RLIMIT_AS = "RLIMIT_AS"

    with patch.dict(sys.modules, {"resource": mock_resource}):
        fn()

    mock_resource.setrlimit.assert_called_once_with("RLIMIT_AS", (256 * 1024 * 1024, 256 * 1024 * 1024))
