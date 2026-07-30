import sys
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


def test_posix_resource_limits_preexec_fn_tolerates_setrlimit_failure():
    fn = posix_resource_limits_preexec_fn(cpu_seconds=60, memory_mb=512)

    mock_resource = MagicMock()
    mock_resource.RLIMIT_CPU = "RLIMIT_CPU"
    mock_resource.RLIMIT_AS = "RLIMIT_AS"
    mock_resource.setrlimit.side_effect = OSError("not permitted")

    with patch.dict(sys.modules, {"resource": mock_resource}):
        fn()  # should not raise
