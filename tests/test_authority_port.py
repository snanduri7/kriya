"""AuthorityPort facade (full modularization, slices C1 and C3; MODULE-CONTRACTS.md §4).

The port is a pass-through: each method gives exactly the owner function's answer, on the allowing AND the refusing path, and reaches
the owner function by its module name at call time (a patch of the owner name is what the port runs). The public names re-exported
from ``kriya.policy.authority`` are the owners' objects. ``ARRIVES_LATER`` is closed and names real, not-yet-done slices.

C3: ``stale`` and the runtime-command review port (``RuntimeCommandReviewPort``, the parameter ``control.launch_approval`` takes)
are AUTHORITY's ``kriya.policy.approval``; the old import paths (``workflow.runtime_command_approval``, ``control.launch_approval``,
``core.llm.is_local_url``) hand out the owner's objects.
"""
from __future__ import annotations

import asyncio
import importlib

import pytest
from test_launch_approval import FILES, LID, _run
from test_prd031a_static_analysis import BASE_A, _workspace, _write

import kriya.policy.authority as authority
import kriya.workflow.runtime_command_approval as rca
from kriya.config import authority_approval
from kriya.control import launch_approval
from kriya.core import llm
from kriya.mcp import invocation_approval
from kriya.policy import approval, egress, filesystem
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
    # S2: redaction is not on the AUTHORITY surface (its one owner is kriya.foundation.redaction, importable by every layer).
    owners = {**{name: "kriya.config.authority_approval" for name in (
        "ApprovalArtifactError", "TrustPathInsideWorkspaceError", "default_local_approval_path", "is_approval_current_for",
        "load_approval_artifact", "resolve_with_approval", "review_fingerprint", "validate_trust_path_outside_workspace")}}
    for name, owner in owners.items():
        assert getattr(authority, name) is getattr(importlib.import_module(owner), name), name
    assert set(owners) | {"ARRIVES_LATER", "AuthorityPort"} == set(authority.__all__)


def test_the_members_still_to_come_name_real_open_slices():
    from tests.architecture.test_import_direction import DONE_SLICES, SLICES  # noqa: PLC0415

    assert set(ARRIVES_LATER) == {"typed_approval_requests", "waited_seconds"}     # "stale" landed in C3, "config_authority" in D3
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
    assert code == "STALE_REVIEW" and accepted["accepted"] is True and accepted["code"] == "ACCEPTED" and outcome["approved"] == approval.ApprovalOutcome.approve()
    with pytest.raises(launch_approval.ApprovalRefused) as invalid:
        port.decide_review(str(tmp_path), "bad", "ra-" + "0" * 24, "sha256:" + "0" * 64, "approve")
    assert invalid.value.code == "INVALID_REQUEST"


@pytest.mark.parametrize("answer", [approval.ApprovalOutcome.approve(), approval.ApprovalOutcome.decline(), approval.ApprovalOutcome.unavailable_because(approval.APPROVAL_REVIEW_EXPIRED)],
                         ids=["approved", "declined", "unavailable"])
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
    assert asyncio.run(port.await_source_decision("S", "ra-1", "sha256:d")) is answer, "the typed outcome is forwarded unchanged"
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


# ----------------------------------------------------------------------------- C3: approval helpers owned by policy.approval

def test_the_old_approval_paths_are_the_policy_owned_objects():
    for name in ("MAX_ARGV_CHARS", "MAX_COMMANDS", "MAX_REVIEW_ROUNDS", "REVIEW_KIND", "RuntimeCommandDecision", "RuntimeCommandSubject",
                 "TypedApprovalCallback", "approval_matches", "ask", "build_subject", "commands_authored_in_goal", "fingerprint_of",
                 "reviewable", "stdin_digest"):
        assert getattr(rca, name) is getattr(approval, name), name
    for name in ("KIND_CHANGE", "KIND_RUNTIME", "KINDS", "REFUSAL_CODES", "REVIEW_SECTION_HEADER", "WORKING_DIRECTORY_KIND", "ApprovalRefused",
                 "_sha", "candidate_digest", "split_reason"):
        assert getattr(launch_approval, name) is getattr(approval, name), name
    assert approval.RuntimeCommandSubject.__module__ == approval.ApprovalRefused.__module__ == "kriya.policy.approval"
    assert llm.is_local_url is egress.is_local_url and egress.is_local_url.__module__ == "kriya.policy.egress"
    # ask's isinstance dispatch sees ONE class whichever path built the callback
    typed = rca.TypedApprovalCallback(lambda _f, _r: True, lambda _s: approval.RuntimeCommandDecision("sha256:x"))
    subject = rca.build_subject(unit_id=None, criterion="c", working_directory="/wt", commands=[["a"]], input_channel="none",
                                stdin_payload=None, reason="r")
    assert asyncio.run(approval.ask(typed, subject)) == approval.RuntimeCommandDecision("sha256:x")


@pytest.mark.parametrize("guard,expected", [(None, False), (lambda: True, False), (lambda: False, True)],
                         ids=["outside_the_post_approval_phase", "unchanged", "changed"])
def test_stale_is_the_policy_decision_and_consults_the_guard_once(guard, expected):
    calls = []

    def counted():
        calls.append(1)
        return guard()

    probe = None if guard is None else counted
    assert AuthorityPort().stale(probe) is expected
    assert approval.stale(guard) is expected
    assert calls == ([] if guard is None else [1])


def test_stale_forwards_to_the_owner_by_name(monkeypatch):
    seen = []
    monkeypatch.setattr(approval, "stale", lambda guard: seen.append(guard) or True)
    assert AuthorityPort().stale(None) is True and seen == [None]


class _RecordingReview:
    """A RuntimeCommandReviewPort that answers like the default and records what the review record and wait asked it."""

    def __init__(self, reviewable=True):
        self.calls, self._reviewable = [], reviewable

    def fingerprint_of(self, working_directory, commands, input_channel, stdin_sha256):
        self.calls.append("fingerprint_of")
        return "sha256:" + "f" * 64

    def reviewable(self, subject):
        self.calls.append("reviewable")
        return self._reviewable

    def decision(self, approved_fingerprint):
        self.calls.append(("decision", approved_fingerprint))
        return ("typed", approved_fingerprint)


def _runtime_subject():
    return approval.build_subject(unit_id="u", criterion="prints", working_directory="/wt", commands=[["mvn", "-q", "exec:java"]],
                                  input_channel="none", stdin_payload=None, reason="run it")


def test_runtime_command_review_through_the_port_is_the_parameter_launch_approval_takes():
    port = AuthorityPort().runtime_command_review()
    assert port is approval.RUNTIME_COMMAND_REVIEW
    subject = _runtime_subject()
    assert port.fingerprint_of("/wt", subject.commands, "none", None) == approval.fingerprint_of("/wt", subject.commands, "none", None)
    assert port.reviewable(subject) is True and port.reviewable(approval.build_subject(
        unit_id=None, criterion="", working_directory="/wt", commands=[], input_channel="none", stdin_payload=None, reason="")) is False
    assert port.decision("sha256:a") == approval.RuntimeCommandDecision("sha256:a") and port.decision(None).approved is False
    # the default record is the one the port's functions give; an injected port is the one the record uses (no lazy import decides it)
    default = launch_approval.build_pending_runtime(LID, "wsid", 1, subject)
    assert default["candidate_digest"] == default["command_fingerprint"] == subject.fingerprint
    injected = _RecordingReview()
    record = launch_approval.build_pending_runtime(LID, "wsid", 1, subject, review=injected)
    assert record["candidate_digest"] == "sha256:" + "f" * 64 and injected.calls == ["reviewable", "fingerprint_of"]
    refusing = _RecordingReview(reviewable=False)
    assert launch_approval.build_pending_runtime(LID, "wsid", 1, subject, review=refusing) is None and refusing.calls == ["reviewable"]


@pytest.mark.parametrize("answer", [approval.ApprovalOutcome.approve(), approval.ApprovalOutcome.decline(), approval.ApprovalOutcome.unavailable_because(approval.APPROVAL_REVIEW_EXPIRED)],
                         ids=["approved", "declined", "unavailable"])
def test_the_runtime_wait_answers_with_the_ports_typed_decision(monkeypatch, answer):
    approved = answer.approved

    async def await_decision(session, review_id, digest):
        return answer

    monkeypatch.setattr(launch_approval, "await_decision", await_decision)
    expected = "sha256:" + "e" * 64 if approved else None
    assert asyncio.run(AuthorityPort().await_runtime_decision("S", "ra-1", "sha256:" + "e" * 64)) == approval.RuntimeCommandDecision(expected)
    injected = _RecordingReview()
    answer = asyncio.run(launch_approval.await_runtime_decision("S", "ra-1", "sha256:" + "e" * 64, review=injected))
    assert answer == ("typed", expected) and injected.calls == [("decision", expected)]
