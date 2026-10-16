"""AUTHORITY public facade (full modularization, slice C1; MODULE-CONTRACTS.md §4).

``AuthorityPort`` is a thin, typed facade over TODAY's authority functions: every method calls exactly one existing
function and returns its answer unchanged (the same ``PolicyResult``, the same ``ApprovalRefused`` code, the same binding).
It adds no decision, no default and no translation, so nothing a caller observes changes when it goes through the port
instead of the owner function. Each owner function is looked up on its module at call time, so a test that patches the
owner name still reaches the code the port runs.

The module-level names below are AUTHORITY's public surface for modules in other logical modules (DEPENDENCY-RULES §1:
cross-module imports go through a public contract module): each is the owner's object, re-exported as itself.

The heavier owners (``control.launch_approval`` imports the edit engine, ``workflow.verification_binding``,
``mcp.invocation_approval``, ``policy.filesystem``) are imported inside the methods: the KUP read path that imports
``scrub_potential_secrets`` from here loads nothing it did not load before C1.

Not on the port yet (``ARRIVES_LATER``): what needs either a body move or a new type, both outside C1's no-behaviour scope.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from kriya.config.authority_approval import ApprovalArtifactError as ApprovalArtifactError
from kriya.config.authority_approval import TrustPathInsideWorkspaceError as TrustPathInsideWorkspaceError
from kriya.config.authority_approval import default_local_approval_path as default_local_approval_path
from kriya.config.authority_approval import is_approval_current_for as is_approval_current_for
from kriya.config.authority_approval import load_approval_artifact as load_approval_artifact
from kriya.config.authority_approval import resolve_with_approval as resolve_with_approval
from kriya.config.authority_approval import review_fingerprint as review_fingerprint
from kriya.config.authority_approval import (
    validate_trust_path_outside_workspace as validate_trust_path_outside_workspace,
)
from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import ActionRequest, MCPCapabilityProfileIdentity, MCPToolIdentity, PolicyResult
from kriya.policy.telemetry import scrub_potential_secrets as scrub_potential_secrets

# MODULE-CONTRACTS §4 members that are NOT on the C1 port, and the slice that brings each (closed; tests/test_authority_port.py
# asserts every slice exists in the plan and is not done yet).
ARRIVES_LATER: Mapping[str, str] = {
    # the body lives in kriya/workflow/attempt.py (KERNEL); AUTHORITY may not import it, C3 moves it to policy/approval.py
    "stale": "C3",
    # typed SourceApprovalRequest / RuntimeCommandApprovalRequest / CandidateIdentity, AuthorityResult/AuthorityDecision: new types
    "typed_approval_requests": "C3",
    # the elapsed human wait returned with the decision, consumed by BudgetLedger.record_human_wait
    "waited_seconds": "H4",
    # ConfigAuthorityReport is part of ResolvedConfiguration, built by ConfigService
    "config_authority": "D3",
}


class AuthorityPort:
    """The one AUTHORITY facade. Construct it with the ``ExecutionPolicy`` the caller would otherwise build (default: the
    same ``ExecutionPolicy()`` every unparameterised call site builds today)."""

    def __init__(self, execution_policy: Optional[ExecutionPolicy] = None) -> None:
        self._execution_policy = execution_policy if execution_policy is not None else ExecutionPolicy()

    # ------------------------------------------------------------------ actions (policy.execution)

    def evaluate_action(self, request: ActionRequest) -> PolicyResult:
        """``ExecutionPolicy.evaluate``: filesystem, git, egress, package, MCP invocation, command and approval stages."""
        return self._execution_policy.evaluate(request)

    def file_writer(self, workspace_root: str, **scope: Any) -> Any:
        """``policy.filesystem.AuthorizedFileWriter(workspace_root, ...)``: the one authorized content-write entry point."""
        from kriya.policy import filesystem

        return filesystem.AuthorizedFileWriter(workspace_root, **scope)

    def file_reader(self, workspace_root: str, extra_readable_roots: Sequence[str] = ()) -> Any:
        """``policy.filesystem.AuthorizedFileReader``: the read-side sibling."""
        from kriya.policy import filesystem

        return filesystem.AuthorizedFileReader(workspace_root, extra_readable_roots)

    # ------------------------------------------------------------------ source and runtime-command review (control.launch_approval)

    def candidate_digest(self, files: List[Dict[str, str]]) -> str:
        """``launch_approval.candidate_digest``: the identity of the candidate as the change gate presented it."""
        from kriya.control import launch_approval

        return launch_approval.candidate_digest(files)

    async def await_source_decision(self, session: Any, review_id: str, digest: str) -> bool:
        """``launch_approval.await_decision``: True ONLY for a durable approval of exactly ``digest``."""
        from kriya.control import launch_approval

        return await launch_approval.await_decision(session, review_id, digest)

    async def await_runtime_decision(self, session: Any, review_id: str, fingerprint: str) -> Any:
        """``launch_approval.await_runtime_decision``: a ``RuntimeCommandDecision`` bound to ``fingerprint`` (or to nothing)."""
        from kriya.control import launch_approval

        return await launch_approval.await_runtime_decision(session, review_id, fingerprint)

    def decide_review(self, workspace_path: str, launch_id: str, review_id: str, candidate_digest_value: str, decision: str,
                      kind: str = "change_approval") -> Dict[str, Any]:
        """``launch_approval.decide``: accept ONE decision for the pending review of ``kind`` or raise ``ApprovalRefused``."""
        from kriya.control import launch_approval

        return launch_approval.decide(workspace_path, launch_id, review_id, candidate_digest_value, decision, kind)

    # ------------------------------------------------------------------ verified-candidate byte binding (workflow.verification_binding)

    def bind_verified_candidate(self, writes: Iterable[Any], workspace_path: str) -> Any:
        """``verification_binding.bind_candidate``: the binding of an exact commit batch."""
        from kriya.workflow import verification_binding

        return verification_binding.bind_candidate(writes, workspace_path)

    def verified_candidate_refusal(self, verified: Any, writes: Iterable[Any], workspace_path: str) -> Optional[Tuple[str, str]]:
        """``verification_binding.binding_refusal``: (reason code, detail) when ``writes`` is not the verified batch."""
        from kriya.workflow import verification_binding

        return verification_binding.binding_refusal(verified, writes, workspace_path)

    # ------------------------------------------------------------------ MCP invocation approval (mcp.invocation_approval)

    def mcp_invocation_resolver(self, workspace_root: str) -> Callable[[MCPToolIdentity, Optional[MCPCapabilityProfileIdentity]], bool]:
        """``invocation_approval.resolve_mcp_invocation_approval``: the durable-approval resolver ``ExecutionPolicy`` consults."""
        from kriya.mcp import invocation_approval

        return invocation_approval.resolve_mcp_invocation_approval(workspace_root)

    # ------------------------------------------------------------------ configuration approval (config.authority_approval)

    def grant_config_approval(self, violations: Any, config_dict: Dict[str, Any], workspace_root: str,
                              out_path: Optional[str] = None) -> Tuple[Any, str]:
        """``authority_approval.grant_local_approval``: the one write path of a SEC-009 approval (CLI and desktop)."""
        from kriya.config import authority_approval

        return authority_approval.grant_local_approval(violations, config_dict, workspace_root, out_path)


__all__ = [
    "ARRIVES_LATER", "ApprovalArtifactError", "AuthorityPort", "TrustPathInsideWorkspaceError", "default_local_approval_path",
    "is_approval_current_for", "load_approval_artifact", "resolve_with_approval", "review_fingerprint", "scrub_potential_secrets",
    "validate_trust_path_outside_workspace",
]
