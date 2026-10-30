"""W3-2 Family C (CHANGE TYPE D, a contract correction): ``NullContainmentBackend`` honours ``ContainmentProfile.resolved_env``.

``resolved_env`` is documented as the caller's COMPLETE, already resolved child environment. Only the OCI backend honoured it; the Null
backend silently dropped it and the child inherited the whole parent environment. GitTool needs a per-call environment (the canonical Git
policy) through its existing trusted-infrastructure profile, so the contract is corrected at its owner - the profile, backend and
classification are unchanged.

The census that this changes no existing caller (TRACED, tests/test_w32_host_git_authority.py covers GitTool end to end):
- MCP + Null never uses the environment: it refuses to start when the backend prepares no launch prefix and spawns with ``env=None``;
- semgrep is UNTRUSTED_EXECUTION with network DENIED, so the Null backend raises on its network gate before any environment logic.
"""
import pytest

from kriya.tools.containment import (
    BackendUnavailableError,
    ContainmentProfile,
    NetworkAuthority,
    NullContainmentBackend,
    TrustClass,
    require_single_environment_source,
)
from kriya.tools.containment_oci import OCIContainmentBackend


def _profile(**fields) -> ContainmentProfile:
    return ContainmentProfile(trust_class=TrustClass.TRUSTED_KRIYA_INFRASTRUCTURE, workspace_path="/tmp/ws", **fields)


def test_resolved_env_is_honoured_exactly():
    provided = {"ONLY": "this", "PATH": "/custom"}
    prepared = NullContainmentBackend().prepare(_profile(resolved_env=provided), ["git", "status"])
    assert prepared.env == provided
    assert prepared.env is not provided, "the caller's mapping must be copied, never aliased"
    provided["LATER"] = "mutation"
    assert "LATER" not in prepared.env


def test_an_empty_resolved_env_is_an_empty_environment_not_the_inherited_one():
    """``{}`` is a value, not 'unset' (``is not None``): a caller that resolved nothing gets nothing, not the parent's secrets."""
    assert NullContainmentBackend().prepare(_profile(resolved_env={}), ["git"]).env == {}


def test_env_allowlist_behaviour_is_preserved(monkeypatch):
    monkeypatch.setenv("KRIYA_W32_ALLOWED", "yes")
    monkeypatch.setenv("KRIYA_W32_SECRET", "no")
    env = NullContainmentBackend().prepare(_profile(env_allowlist=["KRIYA_W32_ALLOWED"]), ["git"]).env
    assert env is not None and env["KRIYA_W32_ALLOWED"] == "yes" and "KRIYA_W32_SECRET" not in env and "PATH" in env


def test_neither_source_preserves_full_inheritance():
    assert NullContainmentBackend().prepare(_profile(), ["git"]).env is None


def test_both_sources_are_refused_with_the_oci_contract():
    both = _profile(resolved_env={"A": "1"}, env_allowlist=["HOME"])
    with pytest.raises(ValueError, match="mutually exclusive"):
        NullContainmentBackend().prepare(both, ["git"])
    with pytest.raises(ValueError, match="mutually exclusive"):
        require_single_environment_source(both)


def test_the_oci_backend_applies_the_same_guard_before_any_docker_resource(monkeypatch):
    backend = OCIContainmentBackend()
    backend._docker_path = "/nonexistent/docker"                      # never executed: the guard fires first
    monkeypatch.setattr(OCIContainmentBackend, "_probe_daemon", lambda self, docker_path: None)
    both = _profile(resolved_env={"A": "1"}, env_allowlist=["HOME"], network=NetworkAuthority.UNRESTRICTED)
    with pytest.raises(ValueError, match="mutually exclusive"):
        backend.prepare(both, ["git"])


def test_resource_limits_still_compose_with_a_resolved_env():
    """cpu/memory limits wrap the environment (``plan.apply_env``): a resolved env keeps its keys under limits."""
    prepared = NullContainmentBackend().prepare(_profile(resolved_env={"KEEP": "me"}, cpu_seconds=30, memory_mb=512), ["git"])
    assert prepared.env is not None and prepared.env["KEEP"] == "me"
    assert prepared.preexec_fn is not None or prepared.resources is not None


def test_the_null_backend_still_refuses_an_isolation_profile_before_the_environment_matters():
    """Census row: semgrep-shaped profiles (UNTRUSTED, DENIED) reach the Null backend's network gate, not the environment."""
    untrusted = ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path="/tmp/ws",
                                   network=NetworkAuthority.DENIED, resolved_env={"HOME": "/home"})
    with pytest.raises(BackendUnavailableError):
        NullContainmentBackend().prepare(untrusted, ["semgrep"])
