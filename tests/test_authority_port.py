"""AuthorityPort facade (full modularization, slice C1; MODULE-CONTRACTS.md §4).

The port is a pass-through: each method gives exactly the owner function's answer, on the allowing AND the refusing path, and reaches
the owner function by its module name at call time (a patch of the owner name is what the port runs). The public names re-exported
from ``kriya.policy.authority`` are the owners' objects. ``ARRIVES_LATER`` is closed and names real, not-yet-done slices.
"""
from __future__ import annotations

import asyncio
import importlib

import pytest
from test_launch_approval import FILES, LID, _run
from test_prd031a_static_analysis import BASE_A, _workspace, _write

import kriya.policy.authority as authority
from kriya.config import authority_approval
from kriya.control import launch_approval
from kriya.mcp import invocation_approval
from kriya.policy import filesystem
from kriya.policy.authority import ARRIVES_LATER, AuthorityPort
from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import ActionRequest, ActionType, MCPCapabilityProfileIdentity, MCPToolIdentity
from kriya.workflow import verification_binding


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(launch_approval, "POLL_SECONDS", 0.01)


@pytest.fixture(autouse=True)
def _mcp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_MCP_APPROVAL_HOME", str(tmp_path / "_mcp_home"))


def test_the_public_names_are_the_owners_objects():
    owners = {"scrub_potential_secrets": "kriya.policy.telemetry", **{name: "kriya.config.authority_approval" for name in (
        "ApprovalArtifactError", "TrustPathInsideWorkspaceError", "default_local_approval_path", "is_approval_current_for",
        "load_approval_artifact", "resolve_with_approval", "review_fingerprint", "validate_trust_path_outside_workspace")}}
    for name, owner in owners.items():
        assert getattr(authority, name) is getattr(importlib.import_module(owner), name), name
    assert set(owners) | {"ARRIVES_LATER", "AuthorityPort"} == set(authority.__all__)


def test_the_members_still_to_come_name_real_open_slices():
    from tests.architecture.test_import_direction import DONE_SLICES, SLICES  # noqa: PLC0415

    assert set(ARRIVES_LATER) == {"stale", "typed_approval_requests", "waited_seconds", "config_authority"}
    assert all(slice_id in SLICES and slice_id not in DONE_SLICES for slice_id in ARRIVES_LATER.values())
    assert not any(hasattr(AuthorityPort, name) for name in ARRIVES_LATER), "a member listed as arriving later must not exist as a stub"


@pytest.mark.parametrize("request_", [
    ActionRequest(action_type=ActionType.READ_FILE, target="src/a.py"),                                      # ALLOW
    ActionRequest(action_type=ActionType.WRITE_FILE, target="/elsewhere/a.py", workspace_path="/ws"),        # DENY
    ActionRequest(action_type=ActionType.RUN_COMMAND, command=("bash", "-c", "ls")),                        # REQUIRE_APPROVAL
    ActionRequest(action_type=ActionType.RUN_COMMAND, command=("pytest",)),                                 # ALLOW_SANDBOXED
    ActionRequest(action_type=ActionType.NETWORK_ACCESS, network_target="http://8.8.8.8/"),                # DENY (egress)
], ids=["allow", "deny", "require_approval", "allow_sandboxed", "egress_deny"])
def test_evaluate_action_is_the_execution_policy_answer(request_):
    assert AuthorityPort().evaluate_action(request_) == ExecutionPolicy().evaluate(request_)
    identity = MCPToolIdentity("s", "t", "d")
    policy = ExecutionPolicy(approved_mcp_tool_identities=frozenset({identity}))
    mcp = ActionRequest(action_type=ActionType.MCP_TOOL_CALL, metadata={"mcp_tool_identity": identity})
    assert AuthorityPort(policy).evaluate_action(mcp) == policy.evaluate(mcp)                                # the injected policy is the one used


def test_file_writer_and_reader_are_the_policy_classes_with_the_same_verdicts(tmp_path):
    inside, outside = str(tmp_path / "a.py"), str(tmp_path.parent / "elsewhere.py")
    writer, reader = AuthorityPort().file_writer(str(tmp_path)), AuthorityPort().file_reader(str(tmp_path))
    assert type(writer) is filesystem.AuthorizedFileWriter and type(reader) is filesystem.AuthorizedFileReader
    direct_writer, direct_reader = filesystem.AuthorizedFileWriter(str(tmp_path)), filesystem.AuthorizedFileReader(str(tmp_path))
    for target in (inside, outside):
        assert writer.authorize(target) == direct_writer.authorize(target)
        assert reader.authorize(target) == direct_reader.authorize(target)
    assert writer.authorize(outside).decision.value == "deny" and writer.authorize(inside).decision.value != "deny"


def test_candidate_digest_and_decide_review_are_launch_approvals(tmp_path):
    assert AuthorityPort().candidate_digest(FILES) == launch_approval.candidate_digest(FILES)
    port = AuthorityPort()

    def scenario(canonical, pending):
        with pytest.raises(launch_approval.ApprovalRefused) as stale:
            port.decide_review(canonical, LID, pending["review_id"], "sha256:" + "0" * 64, "approve")
        return stale.value.code, port.decide_review(canonical, LID, pending["review_id"], pending["candidate_digest"], "approve")

    (tmp_path / "proj").mkdir()
    _canonical, outcome = _run(tmp_path / "proj", scenario)
    code, accepted = outcome["scenario"]
    assert code == "STALE_REVIEW" and accepted["accepted"] is True and accepted["code"] == "ACCEPTED" and outcome["approved"] is True
    with pytest.raises(launch_approval.ApprovalRefused) as invalid:
        port.decide_review(str(tmp_path), "bad", "ra-" + "0" * 24, "sha256:" + "0" * 64, "approve")
    assert invalid.value.code == "INVALID_REQUEST"


@pytest.mark.parametrize("answer", [True, False])
def test_the_waits_forward_to_the_owner_functions_by_name(monkeypatch, answer):
    seen = []

    async def await_decision(session, review_id, digest):
        seen.append(("source", session, review_id, digest))
        return answer

    async def await_runtime_decision(session, review_id, fingerprint):
        seen.append(("runtime", session, review_id, fingerprint))
        return ("decision", answer)

    monkeypatch.setattr(launch_approval, "await_decision", await_decision)
    monkeypatch.setattr(launch_approval, "await_runtime_decision", await_runtime_decision)
    port = AuthorityPort()
    assert asyncio.run(port.await_source_decision("S", "ra-1", "sha256:d")) is answer
    assert asyncio.run(port.await_runtime_decision("S", "ra-2", "sha256:f")) == ("decision", answer)
    assert seen == [("source", "S", "ra-1", "sha256:d"), ("runtime", "S", "ra-2", "sha256:f")]


def test_the_byte_binding_and_its_refusal_are_verification_bindings(tmp_path):
    (tmp_path / "b").mkdir()
    workspace = _workspace(tmp_path / "b", {"A.java": BASE_A})
    batch = [_write(workspace, "A.java", "class A { }\n", base=BASE_A)]
    changed = [_write(workspace, "A.java", "class A { evil(); }\n", base=BASE_A)]
    port = AuthorityPort()
    verified = port.bind_verified_candidate(batch, workspace)
    assert verified == verification_binding.bind_candidate(batch, workspace)
    assert port.verified_candidate_refusal(verified, batch, workspace) is None
    for binding, writes in ((verified, changed), (None, batch)):
        assert port.verified_candidate_refusal(binding, writes, workspace) == verification_binding.binding_refusal(binding, writes, workspace)
        assert port.verified_candidate_refusal(binding, writes, workspace)[0] in (
            verification_binding.VERIFIED_CANDIDATE_EVIDENCE_STALE, verification_binding.VERIFIED_CANDIDATE_EVIDENCE_MISSING)


def test_the_mcp_resolver_is_the_durable_approval_resolver(tmp_path):
    from kriya.context.workspace_identity import workspace_identity  # noqa: PLC0415

    workspace = tmp_path / "ws"            # the approval home is a sibling: a store inside the workspace is refused
    workspace.mkdir()
    identity, profile = MCPToolIdentity("s", "t", "d"), MCPCapabilityProfileIdentity("s", "sha256:" + "a" * 64)
    resolver, direct = AuthorityPort().mcp_invocation_resolver(str(workspace)), invocation_approval.resolve_mcp_invocation_approval(str(workspace))
    assert resolver(identity, profile) is False and direct(identity, profile) is False
    artifact = invocation_approval.add_approval(invocation_approval.empty_artifact(workspace_identity(str(workspace))), identity, profile.profile_digest)
    invocation_approval.save_approval_artifact(invocation_approval.default_local_approval_path(str(workspace)), artifact)
    assert resolver(identity, profile) is True and direct(identity, profile) is True
    assert resolver(identity, None) is False and direct(identity, None) is False


def test_grant_config_approval_is_the_one_write_path(monkeypatch):
    calls = []

    def grant(violations, config_dict, workspace_root, out_path=None):
        calls.append((violations, config_dict, workspace_root, out_path))
        return "artifact", "/store/path"

    monkeypatch.setattr(authority_approval, "grant_local_approval", grant)
    assert AuthorityPort().grant_config_approval(["v"], {"k": 1}, "/ws") == ("artifact", "/store/path")
    assert calls == [(["v"], {"k": 1}, "/ws", None)]

    def refuse(*_args, **_kwargs):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(authority_approval, "grant_local_approval", refuse)
    with pytest.raises(OSError):
        AuthorityPort().grant_config_approval(["v"], {}, "/ws")
