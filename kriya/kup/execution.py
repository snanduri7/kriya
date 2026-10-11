"""KUP execution operations (GUI M6): ``execution.preflight`` and ``launch.status``.

``execution.preflight`` answers, before Start Run, what a run in this folder would be: which configuration Kriya would load, where it would
record its state, which models it would route to, what security authority applies, and whether the cockpit could observe the result. It is
computed by the SAME resolution a terminal ``kriya generate`` performs when run from that folder (the process working directory decides
configuration discovery - kriya/config/config.py), by temporarily entering the folder in this process. Nothing is cached: every call resolves
again, so an approval granted or revoked a moment ago is seen. The host displays the answer and decides nothing from it; the launched
process re-runs the same function and refuses on any blocker, so the preflight cannot be forged by the host.

``launch.status`` reads one launch back from durable records only: the launch record, the RunRecord, the attempt-evidence store and the
workspace run lock. It never infers an outcome from a process exit; a launch that is not ENDED and whose lock is free is reported as
``interrupted`` (the run did not seal), which is neither success nor a failure Kriya recorded.
"""
from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional

from kriya.control.launch import PHASE_ENDED, PHASE_STARTING, valid_launch_id
from kriya.control.persistence import UnreadableRunRecordError, load_launch_record, load_run_record
from kriya.control.recovery import STATUS_CLEAN, STATUS_RUN_ACTIVE, assess_recovery
from kriya.control.run_ownership import probe_run_lock
from kriya.control.workspace_identity import workspace_identity
from kriya.core.state_paths import StateDirectoryError, resolve_state_directory
from kriya.kup import evidence_ops, policy
from kriya.kup.inspect import envelope, live_consistency, source_block
from kriya.kup.store import SnapshotError, trace_store_path
from kriya.kup.workspace_ops import validated_workspace
from kriya.platform.toolchain_locator import tool_visibility

KNOWLEDGE_POLICY = "strict"
INTERACTION_REVIEW = ("A change Kriya proposes is held for your review in this window and applied only if you approve it. Any other step that needs a human "
                      "decision (a knowledge-gap acknowledgement, a skill choice, a command approval) stops the run instead of being approved for you.")
INTERACTION_NO_REVIEW = ("This folder's configuration sets the autonomy mode to {mode!r}: a routine change is not held for review by this configuration (a change that "
                         "touches a sensitive path or exceeds the risk threshold still is). Any other step that needs a human decision (a knowledge-gap "
                         "acknowledgement, a skill choice, a command approval) stops the run instead of being approved for you.")
INTERACTION_UNKNOWN = ("The configuration could not be resolved, so how changes are approved is unknown. Any step that needs a human decision stops the run "
                       "instead of being approved for you.")


def _interaction(cfg: Any) -> Dict[str, Any]:
    """How a change is approved in a run here, from the RESOLVED configuration (shown, never changed): the autonomy mode is the operator's, the app has no selector."""
    if cfg is None:
        return {"approvals": "unknown", "autonomy_mode": None, "knowledge_policy": KNOWLEDGE_POLICY, "note": INTERACTION_UNKNOWN}
    mode = str(cfg.autonomy.mode)
    if mode == "human-in-the-loop":
        return {"approvals": "change_review", "autonomy_mode": mode, "knowledge_policy": KNOWLEDGE_POLICY, "note": INTERACTION_REVIEW}
    return {"approvals": "configured_mode", "autonomy_mode": mode, "knowledge_policy": KNOWLEDGE_POLICY, "note": INTERACTION_NO_REVIEW.format(mode=mode)}
DISCOVERY = ("Configuration is discovered from the process working directory, which is this folder - the same as running `kriya generate` "
             "here. A kriya.yaml in the folder (else the Kriya installation) is read; fields only the operator may set need a durable approval.")

TOOLCHAIN_NOTE = ("Visibility under this process's own environment (a fixed PATH; no login shell). Which tools a goal needs is only known while the workflow runs; "
                  "a missing tool then ends the run with Kriya's own typed failure.")

_CWD_LOCK = threading.Lock()


def _blocker(code: str, message: str) -> Dict[str, str]:
    return {"code": code, "message": message}


def _model_identity(cfg) -> Dict[str, Any]:
    llm = cfg.llm
    roles = {}
    for role in ("planner", "architect", "reviewer", "run_verifier", "skill_gap", "spec_compliance"):
        override = getattr(getattr(cfg.agent_llms, role, None), "llm", None)
        if override is not None:
            roles[role] = override.model
    return {"provider": llm.provider, "model": llm.model, "endpoint": llm.base_url, "context_window": llm.context_window,
            "fallback_chain": [entry.model for entry in cfg.llm_chain], "role_overrides": roles}


def _load_in(workspace: str):
    """(state, cfg, authority, failure): configuration resolved with ``workspace`` as the working directory, exactly as a terminal run there."""
    from kriya.config.authority import ConfigAuthorityError
    from kriya.config.config import load_config, resolve_config_state

    with _CWD_LOCK:
        previous = os.getcwd()
        os.chdir(workspace)
        try:
            state = resolve_config_state()
            try:
                cfg = load_config()
            except ConfigAuthorityError as error:
                fields = [{"field": v.field_path, "source": v.source.value, "classification": v.classification.value, "reason": v.reason}
                          for v in sorted(error.violations, key=lambda x: x.field_path)]
                return state, None, {"status": "refused", "fields": fields}, _blocker(
                    "CONFIG_AUTHORITY_REFUSED",
                    f"{len(fields)} field(s) in this folder's configuration need an operator approval (kriya authority approve): "
                    + ", ".join(f["field"] for f in fields[:6]))
            except Exception as error:  # a malformed or unreadable configuration: typed, never a traceback
                return state, None, {"status": "failed", "fields": []}, _blocker("CONFIG_LOAD_FAILED", f"{type(error).__name__}: {str(error)[:300]}")
            status = "approved" if state.violations else "no_findings"
            return state, cfg, {"status": status, "fields": [v.field_path for v in state.violations]}, None
        finally:
            os.chdir(previous)


def build_preflight(workspace: str, cockpit_state_directory: Optional[str] = None) -> Dict[str, Any]:
    """The preflight document for a canonical ``workspace``. ``cockpit_state_directory`` is where the reading side looks (None when the caller
    is the launched process itself, which cannot know)."""
    blockers: List[Dict[str, str]] = []
    state, cfg, authority, failure = _load_in(workspace)
    if failure is not None:
        blockers.append(failure)
    assessment = assess_recovery(workspace)
    holder = probe_run_lock(workspace)
    if holder is not None:
        blockers.append(_blocker("RUN_ACTIVE", f"another Kriya run already owns this folder ({holder})"))
    elif assessment.status != STATUS_CLEAN:
        blockers.append(_blocker("RECOVERY_REQUIRED", f"the folder's last run left it in state {assessment.status}; recover it (kriya runs recover) before starting another"))
    tools = tool_visibility()
    git_entry = next((t for t in tools if t["tool"] == "git"), None)
    if git_entry is not None and not git_entry["available"]:
        # traced from a real run: every run creates an isolated generation sandbox with git, so a git that does not run fails the run at its first step
        why = git_entry.get("reason") or "is not on this process's PATH"
        later = f"; a working copy exists later on PATH ({git_entry['alternatives'][0]}) but a run executes the first" if git_entry.get("alternatives") else ""
        blockers.append(_blocker("TOOL_UNAVAILABLE", f"git is required for every run and {why}{later}"))
    configuration: Dict[str, Any] = {
        "status": "loaded" if cfg is not None else ("refused" if authority["status"] == "refused" else "failed"),
        "source": state.config_source or "packaged_default", "file": state.config_path, "directory": workspace, "discovery": DISCOVERY,
        "authority": authority, "fingerprint": None,
    }
    runtime_state: Dict[str, Any] = {"directory": None, "source": None, "cockpit_directory": cockpit_state_directory, "observable_by_cockpit": None}
    model: Optional[Dict[str, Any]] = None
    if cfg is not None:
        from kriya.workflow.checkpoint import compute_config_fingerprint

        configuration["fingerprint"] = compute_config_fingerprint(cfg.model_dump())
        model = _model_identity(cfg)
        try:
            directory, source = resolve_state_directory(cfg)
        except StateDirectoryError as error:
            blockers.append(_blocker("STATE_DIRECTORY_INVALID", str(error)))
        else:
            runtime_state.update(directory=directory, source=source)
            if cockpit_state_directory is not None:
                same = os.path.realpath(directory) == os.path.realpath(cockpit_state_directory)
                runtime_state["observable_by_cockpit"] = same
                if not same:
                    blockers.append(_blocker(
                        "STATE_DIRECTORY_NOT_OBSERVABLE",
                        f"a run here would record into {directory}, but this cockpit reads {cockpit_state_directory}; its progress and result would not appear"))
    return {
        "ready": not blockers, "blockers": blockers,
        "workspace": {"path": workspace, "identity": workspace_identity(workspace), "name": os.path.basename(workspace.rstrip(os.sep)) or workspace},
        "configuration": configuration, "state": runtime_state, "model": model,
        "recovery": {"status": assessment.status, "run_active": assessment.status == STATUS_RUN_ACTIVE},
        "interaction": _interaction(cfg),
        "toolchains": {"tools": tools, "note": TOOLCHAIN_NOTE},
    }


def preflight(cfg, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    _requested, workspace = validated_workspace(request)
    try:
        cockpit_state, _source = resolve_state_directory(cfg)
    except StateDirectoryError as error:
        raise SnapshotError(policy.CONFIG_LOAD_FAILED, str(error)) from error
    data = build_preflight(workspace, cockpit_state)
    return envelope("execution.preflight", request_id, data=data, source=source_block(cockpit_state, trace_store_path(cfg), None), consistency=live_consistency())


def _launch_view(record: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("launch_id", "workspace", "requested_at", "goal", "options", "phase", "run", "ended", "history", "approval_required", "declined_decisions", "pending_approval", "approval_history")
    view = {key: record.get(key) for key in keys}
    process = record.get("process")
    view["process"] = {"pid": process.get("pid")} if isinstance(process, dict) else None
    return view


def _run_record_view(workspace: str, run_id: str) -> Dict[str, Any]:
    try:
        record = load_run_record(workspace, run_id)
    except UnreadableRunRecordError as error:
        return {"found": False, "unreadable": error.reason}
    if record is None:
        return {"found": False, "unreadable": None}
    return {"found": True, "unreadable": None, "lifecycle_state": record.lifecycle_state.value, "terminal_status": record.terminal_status,
            "commit_result": record.commit_result, "revision": record.revision}


def _liveness(phase: str, lock_held: bool) -> str:
    """ENDED is the launching process's own seal. Otherwise the kernel lock - the workspace's only ownership authority - says whether a run
    is live; a RUNNING launch whose lock is free did not seal."""
    if phase == PHASE_ENDED:
        return "ended"
    if lock_held:
        return "live"
    return "starting" if phase == PHASE_STARTING else "interrupted"


def launch_status(cfg, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    launch_id = request.get("launch_id")
    if not valid_launch_id(launch_id):
        raise SnapshotError(policy.INVALID_REQUEST, "launch_id must have the shape YYYYMMDDTHHMMSS-xxxxxxxx")
    _requested, workspace = validated_workspace(request)
    try:
        record = load_launch_record(workspace, str(launch_id))
    except UnreadableRunRecordError as error:
        raise SnapshotError(policy.EVIDENCE_STORE_UNREADABLE, f"the launch record cannot be trusted: {error.reason}", path_kind="launch") from error
    if record is None:
        raise SnapshotError(policy.LAUNCH_NOT_FOUND, f"no launch {launch_id} is recorded for this folder", reason="not_recorded")
    phase = str(record.get("phase"))
    holder = probe_run_lock(workspace)
    lock_held = holder is not None and f"run_id={launch_id}," in holder  # the lock's own diagnostic text; ownership itself is the kernel lock
    run = record.get("run")
    run_id = run.get("run_id") if isinstance(run, dict) else None
    data = {
        "launch": _launch_view(record),
        "run_record": _run_record_view(workspace, str(run_id)) if run_id else {"found": False, "unreadable": None},
        "evidence": evidence_ops.store_summary(cfg, str(run_id)) if run_id else {"state": "absent", "line_count": None, "sealed": False},
        "workspace_lock": {"held": holder is not None, "held_by_this_launch": lock_held},
        "liveness": _liveness(phase, lock_held),
        "liveness_note": "DERIVED from the launch phase and the workspace run lock; a process exit alone is never the outcome",
    }
    state_dir = resolve_state_directory(cfg)[0]
    return envelope("launch.status", request_id, data=data, source=source_block(state_dir, trace_store_path(cfg), None), consistency=live_consistency())
