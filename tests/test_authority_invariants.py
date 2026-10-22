"""AUTHORITY invariant characterization (full modularization, slice C1; plan §11; SLICE-PLAN C1).

One file asserting the authority invariants on the REAL functions, each as a NEGATIVE test: the forbidden path is attempted and refused
with its reason code. Later slices that move authority code (C3, E4, E9, G4, M1) rerun this file with their mutants; the test names
``test_stale_candidate_is_refused`` and ``test_hitl_requires_source_review`` are named by C3/E4 and must not be renamed.

| plan §11 invariant | test here | the code path it drives | deeper existing coverage (unchanged) |
|---|---|---|---|
| exact source review before executable candidate behaviour (human-in-the-loop) | ``test_hitl_requires_source_review`` | the real engine (``run_direct``): ``defer_execution_until_source_review`` + the approval gate | ``test_greenfield_direct_plan_runtime.py::test_gap_b_strong_*`` |
| stale candidate detection | ``test_stale_candidate_is_refused`` | ``launch_approval.decide`` (``STALE_REVIEW``) and the engine's post-approval guard (``CANDIDATE_CHANGED_AFTER_SOURCE_REVIEW``) | ``test_launch_approval.py::test_a_stale_digest_or_review_id_fails_closed_and_the_run_keeps_waiting``, ``test_greenfield_direct_plan_runtime.py::test_gap_b_a_candidate_changed_*`` |
| byte binding reviewed == built == tested == runtime == applied | ``test_reviewed_built_tested_runtime_and_applied_bytes_are_bound`` | ``commit_terminal_candidate`` + ``bind_candidate`` (``VERIFIED_CANDIDATE_EVIDENCE_STALE``/``_MISSING``) and the engine's build-time guard | ``test_candidate_verification_binding.py``, ``test_greenfield_direct_plan_runtime.py::test_gap_b_strong_reviewed_built_tested_executed_and_applied_bytes_are_identical`` |
| a required runtime reject cannot succeed and cannot apply | ``test_required_runtime_reject_cannot_succeed_or_apply`` | the real engine with the runtime-command review rejected (``REQUIRED_RUNTIME_VERIFICATION_DECLINED``) | ``test_greenfield_direct_plan_runtime.py::test_gap_a_*`` |
| a tool requiring review never executes silently | ``test_a_tool_requiring_review_never_executes_silently`` | a REAL MCP server process through ``MCPTool`` (``MCP_TOOL_REQUIRES_APPROVAL``, zero tools/call) | ``test_tool002_mcp_invocation_authority.py`` |
| egress is fail-closed (unlisted destination refused) | ``test_egress_is_fail_closed``, ``test_tool_calling_egress_is_fail_closed`` (C3) | ``LLMClient.complete`` under ``local_only`` (``EgressViolationError`` before any request), ``ExecutionPolicy`` (``NETWORK_TARGET_DENIED``), ``capability_for`` | ``test_llm_egress_policy_integration.py``, ``test_prd012_egress.py`` |
| MCP identity binding | ``test_mcp_invocation_approval_is_bound_to_the_exact_identity`` | the durable approval store + ``ExecutionPolicy`` resolver (``MCP_TOOL_DURABLE_APPROVAL_VALID`` only for the exact binding) | ``test_tool002_p2_*``, ``test_tool003_*`` |
| no write to the owner workspace before apply | ``test_nothing_reaches_the_owner_workspace_before_apply`` | the real engine: owner folder observed at every executing gate, and after a rejected source review | ``test_greenfield_direct_plan_runtime.py::test_gap_b_the_source_review_precedes_the_runtime_review_and_nothing_runs_before_it`` |

Stubbed exactly as in the reused harness: the model transport, the toolchain gates and process execution (no Maven, no network).
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from _chaos_harness import snapshot_tree
from _fake_static_analysis import DISABLED_STATIC_ANALYSIS
from test_greenfield_direct_plan_runtime import FILES, MAIN_FILE, _applied, _succeeded, run_direct
from test_greenfield_runtime_end_to_end import _fast  # noqa: F401 - autouse fixture (approval polling)
from test_launch_approval import _decide, _refusal, _run
from test_prd031a_static_analysis import BASE_A, _workspace, _write
from test_tool002_mcp_invocation_authority import (
    FIXTURE,
    isolated_mcp_approval_home,  # noqa: F401 - autouse fixture
)

from kriya.application.history import application_outcome
from kriya.config import AppConfig
from kriya.core.llm import EgressViolationError, LLMClient
from kriya.mcp import invocation_approval
from kriya.mcp.mcp import MCPClient, MCPTool
from kriya.policy.approval import ApprovalOutcome
from kriya.policy.egress import capability_for
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import (
    ActionRequest,
    ActionType,
    MCPCapabilityProfileIdentity,
    MCPToolIdentity,
    PolicyDecision,
    compute_mcp_schema_digest,
)
from kriya.tools.tool import ToolExecutionError
from kriya.workflow import workflow as workflow_module
from kriya.workflow.runtime_command_approval import RuntimeCommandDecision, approval_matches, build_subject
from kriya.workflow.terminal_commit import commit_terminal_candidate
from kriya.workflow.verification_binding import (
    VERIFIED_CANDIDATE_EVIDENCE_MISSING,
    VERIFIED_CANDIDATE_EVIDENCE_STALE,
    bind_candidate,
)

NON_LOCAL = "http://8.8.8.8:11434/v1"   # a numeric public address: the locality check never resolves a name (TEST-NET ranges count as is_private in Python)


def _owner_files(workspace: Path) -> dict:
    """The owner folder's files (git/Kriya bookkeeping excluded): relpath -> (sha256, mode)."""
    return dict(snapshot_tree(workspace))


def _gate_text(run, gate_type: str) -> str:
    return " ".join(str(g.get("output")) for g in run.gate_outcomes if g.get("type") == gate_type)


# ----------------------------------------------------------------------------- exact source review before execution (HITL)

def test_hitl_requires_source_review(tmp_path):
    """Human-in-the-loop: no model-authored candidate is built, tested or run before the person approved its EXACT source. Attempted:
    the engine reaches the change gate with a three-file candidate. Refused: the person rejects (and, separately, there is no approver):
    nothing executed, nothing applied, and the run ends at the approval gate."""
    rejected = run_direct(tmp_path / "rejected", review_plan={"change_approval": ("reject", 0.0)})
    assert [p["kind"] for p in rejected.person.seen] == ["change_approval"]
    assert rejected.compile_calls == 0 and rejected.test_calls == 0 and rejected.log_at_pom == [] and rejected.executed == []
    assert not _succeeded(rejected.result) and _applied(rejected) == set()
    assert "Rejected by user" in str(rejected.result["review"])

    unreviewable = run_direct(tmp_path / "no_approver", with_approver=False)
    assert unreviewable.compile_calls == 0 and unreviewable.test_calls == 0 and unreviewable.executed == []
    assert not _succeeded(unreviewable.result) and _applied(unreviewable) == set()
    assert "human approval was required" in str(unreviewable.result["review"])


# ----------------------------------------------------------------------------- stale candidate detection

def test_stale_candidate_is_refused(tmp_path):
    """A decision or an execution for a candidate that is not the reviewed one is refused. (a) The typed decision action with a digest or
    review id other than the pending one: ``STALE_REVIEW``, nothing recorded, the run keeps waiting. (b) Bytes changed after the source
    approval: the guard before execution stops the run with ``CANDIDATE_CHANGED_AFTER_SOURCE_REVIEW``; nothing runs, nothing is applied."""
    def scenario(canonical, pending):
        codes = [_refusal(canonical, pending, candidate_digest="sha256:" + "0" * 64),
                 _refusal(canonical, pending, review_id="ra-" + "0" * 24)]
        return codes, _decide(canonical, pending, "reject")

    (tmp_path / "decide").mkdir()
    _canonical, outcome = _run(tmp_path / "decide", scenario)
    assert outcome["scenario"][0] == ["STALE_REVIEW", "STALE_REVIEW"]
    assert outcome["approved"] == ApprovalOutcome.decline() and outcome["record_at_resume"]["status"] == "REJECTED"   # only the bound decision counted

    real, calls = workflow_module.assert_candidate_unchanged_since_source_review, []

    def tamper_first(state, ctx):
        if not calls:
            (Path(ctx.worktree_path) / MAIN_FILE).write_text(FILES[MAIN_FILE] + "// changed after the source approval\n")
        calls.append(1)
        return real(state, ctx)

    with patch.object(workflow_module, "assert_candidate_unchanged_since_source_review", new=tamper_first):
        run = run_direct(tmp_path / "engine")
    assert calls, "the post-approval guard was never consulted"
    assert [p["kind"] for p in run.person.seen] == ["change_approval"]       # the runtime review for the old candidate is never raised
    assert run.executed == [] and not _succeeded(run.result) and _applied(run) == set()
    assert "CANDIDATE_CHANGED_AFTER_SOURCE_REVIEW" in _gate_text(run, "verification_infrastructure_failure")


# ----------------------------------------------------------------------------- byte binding

def test_reviewed_built_tested_runtime_and_applied_bytes_are_bound(tmp_path):
    """Attempted: commit a batch that is not the verified one (bytes changed / no binding at all) and, in the engine, change the bytes
    while they are being built. Refused: ``VERIFIED_CANDIDATE_EVIDENCE_STALE`` / ``_MISSING`` with nothing written; the changed build
    candidate is never tested, run or applied (``CANDIDATE_CHANGED_AFTER_SOURCE_REVIEW``)."""
    (tmp_path / "commit").mkdir()
    workspace = _workspace(tmp_path / "commit", {"A.java": BASE_A})
    verified = bind_candidate([_write(workspace, "A.java", "class A { }\n", base=BASE_A)], workspace)
    before = snapshot_tree(workspace)
    for binding, batch, code in (
        (verified, [_write(workspace, "A.java", "class A { evil(); }\n", base=BASE_A)], VERIFIED_CANDIDATE_EVIDENCE_STALE),
        (None, [_write(workspace, "A.java", "class A { }\n", base=BASE_A)], VERIFIED_CANDIDATE_EVIDENCE_MISSING),
    ):
        outcome = commit_terminal_candidate(batch, workspace_path=workspace, transaction_id="tx",
                                            static_analysis=DISABLED_STATIC_ANALYSIS, verified_candidate=binding)
        assert not outcome.committed and outcome.workspace_state == "UNCHANGED" and outcome.reason_code == code
        assert snapshot_tree(workspace) == before

    edit = lambda root: (Path(root) / MAIN_FILE).write_text(FILES[MAIN_FILE] + "// changed by the build\n")  # noqa: E731 - one-line test hook
    run = run_direct(tmp_path / "engine", tamper_during_build=edit)
    assert run.compile_calls >= 1 and run.bytes_at_build[0] == FILES      # the build saw exactly the reviewed bytes...
    assert run.test_calls == 0 and run.executed == []                     # ...and a change made during it is never tested or run
    assert not _succeeded(run.result) and _applied(run) == set()
    assert "CANDIDATE_CHANGED_AFTER_SOURCE_REVIEW" in _gate_text(run, "verification_infrastructure_failure")


# ----------------------------------------------------------------------------- required runtime reject

def test_required_runtime_reject_cannot_succeed_or_apply(tmp_path):
    """The goal requires observable runtime behaviour. Attempted: the person approves the source and rejects the runtime-command review.
    Refused: nothing executes, the run is not a success, nothing is applied, the typed stop is REQUIRED_RUNTIME_VERIFICATION_DECLINED.
    A rejected typed decision is never an approval of any command set."""
    run = run_direct(tmp_path, review_plan={"runtime_command": ("reject", 0.0)})
    assert [p["kind"] for p in run.person.seen] == ["change_approval", "runtime_command"]
    assert run.executed == [] and not _succeeded(run.result) and _applied(run) == set()
    assert not any(g.get("type") == "run_verification" for g in run.gate_outcomes)
    assert "REQUIRED_RUNTIME_VERIFICATION_DECLINED" in _gate_text(run, "verification_infrastructure_failure")
    assert application_outcome(run.gate_outcomes)["blocked_reason_code"] == "REQUIRED_RUNTIME_VERIFICATION_DECLINED"

    subject = build_subject(unit_id=None, criterion="prints", working_directory="/wt", commands=[["mvn", "-q", "test"]],
                            input_channel="none", stdin_payload=None, reason="r")
    declined = RuntimeCommandDecision(None)
    assert declined.approved is False and approval_matches(declined, subject) is False


# ----------------------------------------------------------------------------- tools requiring review

@pytest.mark.asyncio
async def test_a_tool_requiring_review_never_executes_silently():
    """Attempted: a REAL MCP server's tool is called with nothing approved. Refused: ``MCP_TOOL_REQUIRES_APPROVAL`` (fail closed, never
    a bare ALLOW) and the server receives ZERO tools/call requests."""
    with tempfile.TemporaryDirectory() as directory:
        call_log = os.path.join(directory, "calls.log")
        client = MCPClient(name="fixture", command=sys.executable, args=[FIXTURE], env={"CALL_LOG_FILE": call_log})
        await client.start()
        try:
            echo_meta = next(t for t in await client.list_tools() if t["name"] == "echo")
            identity = MCPToolIdentity("fixture", "echo", compute_mcp_schema_digest(echo_meta["inputSchema"]))
            decision = ExecutionPolicy().evaluate(ActionRequest(action_type=ActionType.MCP_TOOL_CALL, metadata={"mcp_tool_identity": identity}))
            assert decision.decision is PolicyDecision.REQUIRE_APPROVAL and decision.reason_code == "MCP_TOOL_REQUIRES_APPROVAL"
            with pytest.raises(ToolExecutionError) as caught:
                await MCPTool(client, echo_meta).execute(message="must never reach the server")
            assert isinstance(caught.value.__cause__, PolicyDeniedError)
            assert "MCP_TOOL_REQUIRES_APPROVAL" in str(caught.value.__cause__)
        finally:
            await client.stop()
        calls = Path(call_log).read_text().split() if os.path.exists(call_log) else []
        assert calls == [], "the server must have received ZERO tools/call requests"


# ----------------------------------------------------------------------------- egress

def test_egress_is_fail_closed(monkeypatch):
    """Attempted: a model request and a network action to a destination no trusted setting lists. Refused before any byte leaves:
    ``EgressViolationError`` (no request is even attempted), ``NETWORK_TARGET_DENIED`` for both network action types, a host-less
    URL is never local, and an unknown network-authority value has no capability class (raises, never a default)."""
    cfg = AppConfig()
    assert cfg.autonomy.egress_policy == "local_only"          # the packaged default
    cfg.llm.base_url = NON_LOCAL
    llm = LLMClient(cfg)
    sent = AsyncMock(side_effect=AssertionError("a request was attempted to an unlisted destination"))
    monkeypatch.setattr(LLMClient, "_request_once", sent)
    with pytest.raises(EgressViolationError):
        asyncio.run(llm.complete("system", "user"))
    assert sent.await_count == 0

    policy = ExecutionPolicy()
    for action in (ActionType.NETWORK_ACCESS, ActionType.LLM_NETWORK_ACCESS):
        result = policy.evaluate(ActionRequest(action_type=action, network_target=NON_LOCAL))
        assert result.decision is PolicyDecision.DENY and result.reason_code == "NETWORK_TARGET_DENIED", action
    hostless = policy.evaluate(ActionRequest(action_type=ActionType.LLM_NETWORK_ACCESS, network_target="localhost:11434"))
    assert hostless.decision is PolicyDecision.DENY                   # no scheme -> no hostname -> not local (fail closed)
    with pytest.raises(ValueError):
        capability_for("some_new_authority")


def test_tool_calling_egress_is_fail_closed(monkeypatch):
    """The tool-calling path (``LLMClient.complete_with_tools``) has its OWN ``local_only`` check, separate from ``complete``'s (a gap
    noted at C1: only test_llm_egress_policy_integration.py covered it). Attempted: a tool-calling request to an unlisted destination,
    configured AND as a per-call override. Refused: ``EgressViolationError`` before any transport is touched."""
    cfg = AppConfig()
    assert cfg.autonomy.egress_policy == "local_only"
    cfg.llm.base_url = NON_LOCAL
    llm = LLMClient(cfg)
    sent = AsyncMock(side_effect=AssertionError("a request was attempted to an unlisted destination"))
    monkeypatch.setattr(LLMClient, "_request_once", sent)
    monkeypatch.setattr(llm.client.chat.completions, "create", sent)
    with pytest.raises(EgressViolationError):
        asyncio.run(llm.complete_with_tools(messages=[{"role": "user", "content": "u"}], tools=[]))
    local = AppConfig()
    local.llm.base_url = "http://127.0.0.1:11434/v1"
    local_llm = LLMClient(local)
    monkeypatch.setattr(local_llm.client.chat.completions, "create", sent)
    with pytest.raises(EgressViolationError):
        asyncio.run(local_llm.complete_with_tools(messages=[], tools=[], base_url_override=NON_LOCAL))
    assert sent.await_count == 0


# ----------------------------------------------------------------------------- MCP identity binding

def test_mcp_invocation_approval_is_bound_to_the_exact_identity(tmp_path):
    """A durable invocation approval covers EXACTLY (workspace, server, tool, schema digest, capability-profile digest). Attempted: the
    same tool with a changed schema, a changed capability profile, no capability identity, another tool name, another workspace.
    Refused: ``MCP_TOOL_REQUIRES_APPROVAL`` for every one; ``MCP_TOOL_DURABLE_APPROVAL_VALID`` only for the exact binding."""
    workspace, other = tmp_path / "ws", tmp_path / "other"
    workspace.mkdir()
    other.mkdir()
    identity = MCPToolIdentity("fixture", "echo", compute_mcp_schema_digest({"type": "object", "properties": {"m": {"type": "string"}}}))
    profile = MCPCapabilityProfileIdentity("fixture", "sha256:" + "a" * 64)
    path = invocation_approval.default_local_approval_path(str(workspace))
    from kriya.context.workspace_identity import workspace_identity  # noqa: PLC0415

    artifact = invocation_approval.add_approval(invocation_approval.empty_artifact(workspace_identity(str(workspace))), identity, profile.profile_digest)
    invocation_approval.save_approval_artifact(path, artifact)

    def decide(root, tool, capability):
        policy = ExecutionPolicy(mcp_invocation_approval_resolver=invocation_approval.resolve_mcp_invocation_approval(str(root)))
        metadata = {"mcp_tool_identity": tool} if capability is None else {"mcp_tool_identity": tool, "mcp_capability_profile_identity": capability}
        return policy.evaluate(ActionRequest(action_type=ActionType.MCP_TOOL_CALL, metadata=metadata)).reason_code

    assert decide(workspace, identity, profile) == "MCP_TOOL_DURABLE_APPROVAL_VALID"
    drifted = {
        "schema": (workspace, MCPToolIdentity("fixture", "echo", compute_mcp_schema_digest({"type": "object"})), profile),
        "profile": (workspace, identity, MCPCapabilityProfileIdentity("fixture", "sha256:" + "b" * 64)),
        "no profile": (workspace, identity, None),
        "tool name": (workspace, MCPToolIdentity("fixture", "echo2", identity.schema_digest), profile),
        "server": (workspace, MCPToolIdentity("other", "echo", identity.schema_digest), profile),
        "workspace": (other, identity, profile),
    }
    assert {name: decide(*case) for name, case in drifted.items()} == {name: "MCP_TOOL_REQUIRES_APPROVAL" for name in drifted}
    missing = ExecutionPolicy().evaluate(ActionRequest(action_type=ActionType.MCP_TOOL_CALL, metadata={"mcp_tool_identity": "fixture.echo"}))
    assert missing.decision is PolicyDecision.DENY and missing.reason_code == "MCP_IDENTITY_MISSING"   # a name is not an identity


# ----------------------------------------------------------------------------- owner workspace untouched before apply

def test_nothing_reaches_the_owner_workspace_before_apply(tmp_path):
    """Every executing gate runs in Kriya's isolated worktree; the owner's folder holds only its base until the terminal apply.
    Attempted: a full approved run (observed at POM validation, build, tests and execution) and a run whose source review is rejected.
    Refused: at every gate the owner folder is exactly its base; after the rejection it stays exactly its base."""
    owner = tmp_path / "approved" / "ws"
    base = {"README.md"}
    observed = []

    def observe(stage):
        def hook(root):
            assert os.path.realpath(root) != os.path.realpath(owner), f"{stage} ran in the owner folder"
            observed.append((stage, set(_owner_files(owner))))
        return hook

    run = run_direct(tmp_path / "approved", tamper_during_pom=observe("pom"), tamper_during_build=observe("build"),
                     tamper_during_tests=observe("tests"))
    assert {stage for stage, _ in observed} == {"pom", "build", "tests"}
    assert all(files == base for _stage, files in observed), observed
    assert run.applied_at_execution == [{path: False for path in FILES}]
    assert _succeeded(run.result) and _applied(run) == set(FILES)            # the apply happens, after every gate

    rejected = run_direct(tmp_path / "rejected", review_plan={"change_approval": ("reject", 0.0)})
    assert set(_owner_files(rejected.workspace)) == base
    assert (rejected.workspace / "README.md").read_text() == "base\n"
    status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=rejected.workspace,
                            capture_output=True, text=True, check=True).stdout
    assert [line for line in status.splitlines() if ".kriya" not in line] == []
